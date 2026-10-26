# HardRead arena

A Foul Play-based player plays against PokéChamp's **abyssal** heuristic bot on a private Showdown server, with side-swapped team pairings. This is the harness for gates G-24h and G1.

## Pieces
| File | What it does |
|---|---|
| `run_arena.py` | The runner. It reuses `tools/run_match.py`'s paths, configs, server start and Foul Play command. The HardRead player is side A and always challenges; abyssal is side B and always accepts. |
| `abyssal_bot.py` | Runs `AbyssalPlayer`, unmodified, from PokéChamp (`sources/pokechamp`, commit `0f84c460`, file `poke_env/player/baselines.py`). It logs in as a local guest and uses a random team preview, as in PokéChamp. LLM backends that PokéChamp imports but abyssal never uses (torch, transformers, google-genai, ollama) are stubbed, and a stub raises if anything calls it. |
| `../../foul-play/fp/hardread_policy.py` | Active only when `HARDREAD_POLICY=llm`. It replaces the MCTS choice with an OpenAI-compatible tool call (see its docstring for the environment variables). |
| `../../foul-play/fp/hardread_history.py` | `canonical_history()`, the history form shared by training and serving (see Parity). |
| `mock_llm.py` | Mock endpoint that returns a random legal `choice_id`. It can inject faults (`--p-no-tool`, `--p-illegal`, `--delay`). |
| `check_history_parity.py` | For a run made with `--log-history`, checks that the history and the rendered prompt at play time equal what `extract.py` plus `build_sft.py` produce from the saved battle log. |
| `replay_requests.py` | Replays logged decisions against a live endpoint through the policy's own request code. |
| `serialize_standin.py` | Temporary stand-in serializer, for smoke tests only. |
| `pokechamp-uvlock-constraints.txt` | PokéChamp's `uv.lock` exported as pins. The arena environment installs only the subset abyssal needs. |

## Environments
- **Arena environment** (`environments/arena`):
  ```sh
  uv venv --python /opt/homebrew/bin/python3.12 environments/arena
  uv pip install --python environments/arena/bin/python -c hardread/tools/arena/pokechamp-uvlock-constraints.txt \
    numpy pandas orjson websockets requests tqdm huggingface-hub openai termcolor rich pyfiglet fade
  ```
- **Runner:** any Python 3 (stdlib only), like `run_match.py`.
- **Foul Play:** the existing `environments/foulplay`.

## Run
**Ports.** Use 18040–18049 only. Each port gets a private Showdown runtime, `showdown/arena-runtime-<port>`: symlinks to the pinned code, plus its own config, `logs/` and battle counter. Servers that share one runtime reuse room IDs and overwrite each other's saved battle logs.

**Vanilla Foul Play vs abyssal** (10 games):
```sh
python3 tools/arena/run_arena.py --player F1 --team-pool gen9/hr_hl_dev --seed 1 --games 5 --side-swap --concurrency 4 --port 18040
```

**LLM policy vs abyssal:**
```sh
python3 tools/arena/run_arena.py --player llm --llm-url http://127.0.0.1:18045/v1/chat/completions \
  --llm-max-tokens 64 --team-pool gen9/hr_hl_dev --seed 2000 --games 100 --side-swap --concurrency 4 \
  --port 18041 --gate g24h [--log-history]
```

**Output.** Everything goes to `runs/<run_id>/`:
- `games.jsonl`: one record per game;
- `summary.json`: wins, Wilson 95% CI, failures, illegal rate, latency p50/p95, and the gate verdict;
- `g####_hr.log` / `g####_ab.log`: the two bots' logs;
- `g####_hr.policy.jsonl`: one record per decision, plus server-rejection events;
- `battlelogs/`: the Showdown battle logs.

## Counting rules
- **Illegal move.** A decision counts as illegal when the policy falls back, for any of these reasons: timeout, HTTP error, no tool call, wrong tool, bad arguments, unknown `choice_id`, serializer error. The fallback plays the first legal action and is clearly logged. A server rejection (`|error|[Invalid choice]`) also counts as illegal.
  - `illegal_rate` = illegal / decisions.
  - Latency is measured per decision, wall-clock: legal-menu sampling (`legal_seconds`) plus the request.
- **Crashes and timeouts.** A game the HardRead player does not finish counts as a loss in `win_rate_failures_as_losses`, which is the number the gates use.
- **Gates** (`--gate`):

| Gate | Win rate | Illegal moves | p95 latency | Games |
|---|---|---|---|---|
| G-24h | ≥ 35% | ≤ 2% | ≤ 5 s | ≥ 200 |
| G1 | ≥ 60% | — | — | ≥ 400 |

## Parity: same observation at play time as in SFT data
At play time the policy builds its inputs the same way the training data does:
- **View:** `hardread_decision_log._side()` on the same battle copy the search logs, JSON round-tripped.
- **Legal menu:** the union of MCTS root options over the search's sampled worlds, sorted. This equals `extract.search_targets()["legal"]`.
- **History:** the lines on the bot's own channel, cut like `extract.history_cuts`.

The saved battle log is Showdown's *omniscient* log, with exact HP for both sides. `serialize.py` converts HP to percentages, so the prompts match:
- `check_history_parity.py` gave rendered prompts equal on 52/52 decisions (real serializer) and 83/83 (stand-in run, re-rendered with the real serializer).
- `canonical_history` adds the same guarantee for any consumer.

**Known `extract.history_cuts` bug for later decisions (k ≥ 1).** The cut includes lines written *after* the decision (for example `|-end|...|Quark Drive|[silent]` on our own switch-out). The suggested fix is `chunk_cut()` in `check_history_parity.py`: cut at the `|` / `|t:|` pair that starts the next update. The current serializer doesn't render those lines, so the prompts are still equal.

## Azure (one H100)
The job is `azure/arena/arena_job.sh` and the submit script is `azure/arena/submit_arena.sh`: name prefix `hrarena-`, experiment `hardread-arena`, `shm_size` 128g, timeout ≤ 3 h. Setup:
- `setup_node.sh` for Node, Rust, poke-engine, Showdown and the pools;
- PokéChamp at its pinned commit, plus the arena virtualenv;
- llama.cpp `f8def7fe` built with CUDA in `/tmp`;
- the pinned Q4_K_M GGUF in a private `/dev/shm` directory, SHA-256 checked;
- `llama-server` with `--jinja --temp 0 --top-k 40 --swa-full --reasoning-format deepseek --parallel NP` and an optional `--lora`.

**Fine-tuned Glimmer v0 (LoRA):**
1. Convert the PEFT adapter to GGUF with llama.cpp `f8def7fe` `convert_lora_to_gguf.py` (`--base` = a folder with Glimmer's `config.json`; `--outtype f16`). Upload it to the datastore.
2. Submit:
   ```sh
   # G-24h: 200 games
   ADAPTER=azureml://datastores/workspaceblobstore/paths/<...>/glimmer-v0-lora.gguf \
   bash azure/arena/submit_arena.sh hrarena-g24h-<tag> 10800 "G-24h: Glimmer v0 vs abyssal" \
     F1_PAIRS=0 LLM_PAIRS=100 NP=4 MAX_TOKENS=64 LLM_TIMEOUT=20 LLM_WALL=9600 GATE=g24h
   # G1: 400 games (same, with LLM_PAIRS=200 GATE=g1; split it in two seeds if it can't fit in 3 h: SEED=2000 and SEED=3000, 100 pairs each)
   ```

**Notes:**
- **Output length.** The act-directly target is a bare tool call, about 20 tokens. `MAX_TOKENS=64` leaves no room for reasoning, so a model that reasons is counted as truncated (`no_tool_call`).
- **Precision.** For base Glimmer, keep the Q4_K_M base. LoRA on a BF16 GGUF matches training precision better (coordinator figures: 0.36 s TTFT, 46 tok/s) but needs the BF16 GGUF staged; check the precision the SFT lane trained against.
- **Throughput.** One H100 at ~1 s per decision with 4 slots runs ≈ 100–150 games/h. That is ≈ 1.5–2 h for G-24h; G1 likely needs two jobs.
- **Serializer.** The job snapshots `tools/sft/serialize.py`. The adapter must have been trained with the same `SERIALIZER_VERSION`.
