# Glimmer SFT: distil Foul Play into Muse Glimmer-30B

Decision logs (Foul Play self-play) → public-observation prompts → QLoRA SFT on one H100 → LoRA served by llama.cpp.

## Files
| File | What |
|---|---|
| `serialize.py` | **Shared with the live player.** `build_example(view, history, legal, player, turn, k, team_preview, task)` and `target_message(choice, id_map, task, reads)`; `reads_label(species, opp_team)` builds the reads label. `SERIALIZER_VERSION` = `hr-sft-v1.2`. |
| `glimmer_chat.py` | Native chat template rendering (Jinja, byte-identical to transformers' `apply_chat_template`) and tokenization with the pinned `tokenizer.json`; `encode()` makes ids + labels; `parse_tool_call()` is a port of llama.cpp f8def7fe's Muse parser (tool_choice=required). |
| `build_sft.py` | Decisions (`tools/dataset/extract.py` output + `.games` file) → `train/val/eval.jsonl.gz` + `manifest.json` + `examples.txt`. |
| `dex.json`, `mkdex.js` | Display names, types, base power, base stats from Showdown `c0845f3` (`node mkdex.js <showdown dir> > dex.json`). |
| `../../azure/sft/` | Job package: `qlora_sft.yaml`, `sft_plugin.py` (axolotl plugin), `run_sft.py` (job entry), `eval_sft.py`, `submit_sft.sh`. |

## Prompt format
- **System:** short fixed instructions ending in `Reasoning strength: low.`, followed by the template's tool block. Because the text contains the reasoning line, rendering does not depend on the `reasoning_strength` kwarg. There is no date line, so it is deterministic.
- **User:** compact text, not JSON.
  - Our team: full sets, HP (exact for the active), status, boosts, and the Speed stat in place of the EV spread.
  - Opponent, public only: HP as a **percentage**, status, revealed or lost item, Foul Play's impossible items and "no Choice item" flag, revealed ability, moves, Tera, and speed range.
  - Field and side conditions.
  - Recent events: the last 2–3 turns plus the current partial turn. Opponent HP in battle-log lines is converted to Showdown's public percentage (`ceil`, 99 cap).
  - Frozen legal menu: `act_01: Close Combat (Fighting, phys 120)`, `act_05: Terastallize (Ghost) + Knock Off`, `act_09: Switch to Kyurem (40%)`. Order: moves, then Tera moves, then switches.
- **Tool:** `submit_action(choice_id enum)`, the same as the battle harness. All keys are sorted so llama.cpp's re-serialised tool JSON matches.
- **Tokens** (real Glimmer tokenizer, C1t1): prompt p50 1,178, p90 1,309, max 1,463. The fixed system and tool block is about 465 of those tokens and is cached by llama.cpp's prefix cache.
- **Never rendered:** the opponent's true sets, and Foul Play's default opponent nature and EVs.

## Target ("act directly")
`target_message` sets `reasoning_content = ACT_REASONING` ("Acting directly."). The native template then renders:

`<|start|>assistant to=self<|message|>Acting directly.<|eom|><|start|>assistant to=submit_action<|message|><atem:function_calls>…<atem:parameter name="choice_id">act_06</atem:parameter>…</atem:function_calls><|eot|>`

That is 57 target tokens.
- **Labels:** every token after the generation prompt `<|start|>assistant` is trained. This includes the channel headers and `<|eom|>`, which separates reasoning from the answer and is never EOS. The canary trained only the payload, and an 18-update profile later emitted a malformed channel prefix.
- **Asserted in `encode`:**
  - exactly one BOS, at position 0;
  - no EOS (200001/200008) before the final `<|eot|>`;
  - the prompt ids are a prefix of the full ids.
- **llama.cpp parity, checked on a local server:**
  - `/apply-template` renders the same text, minus the leading BOS text;
  - llama.cpp adds that BOS when tokenizing, so the token ids are identical.
- **Parsing:** llama.cpp's PEG parser accepts the layout (`(start analysis)* start tool`). Setting `ACT_REASONING = None` gives a bare tool call, which is also valid.

## Reads task
- **Tool:** `submit_reads(reads={species: {ability, item, moves, spread, tera}})`, for every non-fainted opponent Pokémon.
- **Labels:** taken from `opp_team` and used only as labels.
- **Order:** keys in schema order.
- **Size:** the target is about 350 tokens.
- **Sampling:** `--reads-every N` keeps about 1 in N decisions.

## Build
```sh
python3 tools/dataset/extract.py scratch_ds/c1t1/chunks/*.tar.gz --out scratch_ds/c1t1/decisions.jsonl.gz
python3 tools/sft/build_sft.py scratch_ds/c1t1/decisions.jsonl.gz --out-dir scratch_ds/sft/v0 \
    --task action --val-frac 0.05 --cfg C1t1 [--drop-team-preview] [--min-visits N] [--min-conf X] \
    [--weight conf --conf-ref 0.5] [--teampreview-weight 0.5] [--max-train N]
```
- **Split:** by game, using `sha256("<run_id>/g<game>")`, so both sides of a game land in the same split. Teams can recur across splits because they come from the 2,000-team dev pool.
- **Default filter:** drops decisions with only one legal action.
- **Weights:** applied by deterministic resampling, so the trainer stays plain SFT.

## Train / evaluate on Azure
```sh
bash azure/sft/submit_sft.sh hrsft-<date>-<tag> scratch_ds/sft/v0 10800 "description" \
    --max-steps N --micro-batch 4 --grad-accum 4 --save-steps 200 --eval-n 400 --serve-n 400
```
- **Job:** one H100. The BF16 snapshot is mounted read-only with a 2 GB block cache and the file cache off; `shm_size` is 128g.
- **Stages, in order:**
  1. Preflight hash checks.
  2. In the background, llama.cpp is built in `/tmp` and the base Q4_K_M is downloaded to `/dev/shm`.
  3. Axolotl QLoRA training, with checkpoints written to the job output so a killed job resumes.
  4. A fresh-process HF reload and teacher-forced eval: agreement, top-k, and policy mass, with the adapter on and off.
  5. `convert_lora_to_gguf.py`, with a one-line `LoraTorchTensor.ndim` shim. Without it, Muse adapters fail at f8def7fe.
  6. `llama-server --lora` with the comparison-worker flags (`--swa-full`, temp 0, top-k 40). The eval runs on chat+tools and on a raw no-grammar path.
- **Gate:** legal rate ≥ 98% on the chat path. Malformed or truncated outputs count as failures.
- **Multi-node DDP (not run yet):** `NODES=N bash azure/sft/submit_sft.sh …` adds `distribution: pytorch, process_count_per_instance: 1` with `instance_count: N`.
  - AML sets RANK, WORLD_SIZE and MASTER_*, and Accelerate initialises DDP.
  - Rank 0 alone saves, evaluates and serves.
  - NCCL runs over TCP because these nodes have no InfiniBand. LoRA gradients are about 0.4 GB per step, which is small next to a step of about 10 s.
  - Each rank sees `micro_batch × grad_accum` rows per step. Divide `--grad-accum` by N to keep the effective batch.

## Serving
Base Q4_K_M GGUF + `--lora adapter-bf16.gguf` (about 0.4 GB). The flags are those of `azure/sft/run_sft.py`, with the request using `tool_choice: required`. The alternative is to merge the adapter into the BF16 base, convert, and quantize to Q4_K_M.
- **Merge advantages:** no runtime LoRA matmuls, and a single file.
- **Merge costs:** about 60 GB of BF16 plus a 60 GB intermediate GGUF; a QLoRA→BF16 merge mismatch; and requantization error.

Use the adapter first. Merge only if the measured LoRA latency overhead matters.
