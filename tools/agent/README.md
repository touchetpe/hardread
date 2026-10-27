# Explainer agent and note parser (base Muse Glimmer-30B over engine tools)

Lane Ag-LLM, plan of record `research/28-plan-decision.md` (S1.6, S2.4, S2.5, S3.4, S4.2, E10, E11); design `research/27-plan-proposal-r2.md` §4.6. **Base** Glimmer only: nothing here is fine-tuned (never call it "fine-tuned"; research/28 §B).

The claim: every number shown is copied from a cited engine result. The model never writes a number: it writes sentences with `{placeholders}` whose slots point at tool fields (`"#id.field"`); code renders the values, and a deterministic verifier rejects anything else.

## Files
| File | What |
|---|---|
| `tools.py` | Read-only tools over one `review.py` JSON: `get_summary`, `list_key_moments(kind, k)`, `get_turn`, `get_luck_events`, `get_verdict` (cache only). Every returned dict with an `id` is a citable record. Bridge integers (start/luck/choices) come from the review's `bridge` block or `tools/review/bridge.py:from_review` (Ag-Sci). Disagreements use the research/27 §4.3 filter (known action, WP 0.20–0.80, gap ≥ 5 points, never summed). |
| `render.py` | Fills slots by key type: `*_pts` points (signed, or magnitude when the words give the sign; gaps unsigned), `*_wp_pct`/`wp_*_pct` win chance %, `p`/`prob`/`*_prob` probability %, `turn`, names. |
| `verifier.py`, `lexicon.py` | The 7 rules (below) + structure. `VERIFIER_VERSION`, `LEXICON_VERSION`. |
| `explain.py` | The loop: ≤ 4 data-tool calls → `submit_explanation` → verifier → one repair turn → template fallback. Modes `loop` (product), `prefetch` (single-call fallback: tool outputs in the prompt), `template`. |
| `templates.py` | Deterministic sentences over the same records (they pass the verifier); phrase the moments the model selected when it fetched any ("Glimmer selects; templates phrase"). |
| `llm.py` | Client for the local `llama-server` (OpenAI-compatible, `--jinja`, tool calls parsed by llama.cpp's Muse/ATEM parser). |
| `parse_note.py` | Note → belief chips: single-turn `submit_belief` (species enum = the 12 in the game), dex normalisation, **grounding** (a value is kept only if the note names it, directly or by alias), keyword baseline, belief schema for `verdict.py`, and the data-only read check from `decisions[].opponent_policy_top`. |
| `names.py` | Display names and the species/move lexicon from `tools/sft/dex.json`. |
| `smoke_toolloop.py` | S1.6/E10 smoke. |
| `eval_a1.py` | A1 runner (pilot and record). |
| `test_verifier.py` | 41 unit tests: verifier (adversarial), renderer, tools, templates. |
| `test_parse_note.py` | 13 unit tests: grounding, aliases, own/unknown species, no numbers, chips → belief, read check. |
| `../../EVAL_PROTOCOL_AGENT.md` | E11 protocol (committed before any eval record). |

## Run
```bash
# server (local, Metal); record the PID, stop only that PID
M=<PROJECT_ROOT>
$M/build/llama.cpp/bin/llama-server --model $M/models/muse-glimmer-gguf/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf \
  --alias muse-glimmer --host 127.0.0.1 --port 18131 --gpu-layers 99 --temp 0 --top-k 40 --top-p 1 --seed 42 \
  --ctx-size 32768 --parallel 1 --jinja --reasoning-format deepseek --chat-template-kwargs '{"reasoning_strength":"low"}' \
  --no-webui --swa-full > scratch_ds/agent/llama_server.log 2>&1 & echo $! > scratch_ds/agent/llama_pid

python3 tools/agent/test_verifier.py --json scratch_ds/agent/verifier_tests.json
python3 tools/agent/explain.py REVIEW.json "Was this game decided by luck or by my choices?" --out result.json
python3 tools/agent/explain.py REVIEW.json "..." --mode template          # no model needed
python3 tools/agent/smoke_toolloop.py --runs 5 --out scratch_ds/agent/smoke_toolloop.json
python3 tools/agent/parse_note.py --log REPLAY.json --side p1 --note "Dragapult was Specs" --review REVIEW.json --turn 12
python3 tools/agent/parse_note.py --smoke --out scratch_ds/agent/parser_smoke.json
python3 tools/agent/eval_a1.py --pilot --reviews R.json --questions 1,2,3 --out scratch_ds/agent/a1_dryrun.json
```
System `python3` (3.9) is enough: nothing imports Foul Play.

## Output contract (what the app consumes)
`explain.run(...)` returns `final` ∈ {`glimmer_raw`, `glimmer_repaired`, `template`}, `rendered.text`, `rendered.sentences[].citations` (slot → ref → tool → field → value → shown: the receipts drawer), `trace` (every tool call and its exact output), `steps` (per request: tokens, latency), `attempts` (payloads + verifier reports). Label template answers as "template" in the UI.

`parse_note.parse(...)` returns `chips` (editable; `species`, `field`, `value`, `id`, `evidence`, `source`), `dropped` (with reasons), `belief` (research/27 §4.4 schema). `belief_from_chips(edited_chips)` rebuilds the belief after user edits. `read_check(review, turn, k, belief)` returns the data-only sentence.

## Verifier rules and the choices made
1. Every slot resolves to a scalar field of a tool record from this session.
2. Phrase class ↔ key type: luck words and chance-event words (crit, miss, flinch, damage roll, speed tie) → luck or swing points; "chance/probability/likely" → `p`/`prob`/`*_prob`; "win chance"/"start" → win-chance %; "prefer/disagree/gap/margin" → `gap_pts`; "choices/decisions" → `choices_pts`; "turn {t}" ↔ a turn. The nearest trigger in the sentence wins (ties: the preceding one). A numeric slot with no trigger fails (an unlabelled number). `choices_pts` is never "your choices" (it is both players').
3. No digits or number words in free text, **except digits inside a species/move name present in a cited output** (Porygon2; research/28 §0.1 fix). "one" counts only in counting phrases ("one turn").
4. Polarity: strong direction phrases ("toward you", "against you", "in your favour", "cost you", "lucky"…) need a signed points slot in the sentence and must match the sign of the nearest one; weak words ("helped", "cost", "hurt", "dropped") are checked only when a signed slot is present. `gap_pts` counts as a cost.
5. Species and move names in free text must appear in a tool output that contains a cited record (form variants accepted: "Ogerpon" for "Ogerpon-Wellspring"). Single common English words ("Protect", "Rest") count only when capitalised mid-sentence.
6. Verdict words (misplay, misread, defensible, within noise, mistake, error, wrong, correct, optimal…) and so their negations never appear in free text; a `verdict` field must come from `get_verdict`.
7. With a probability below one half (or a win chance below 50%) in the sentence: no certainty words ("certain", "always", "definitely"…), and "was/had/were/will/would" only alongside a hedge ("chance", "likely", "only", "might"…).

## Known limits
- `get_luck_events` reports the turn's realised-outcome probability and swing, not per-event probabilities (`review.py` does not store them).
- No opponent set estimate in the tools (`review.py` JSON does not carry it); the read check uses only the engine's top-3 opponent policy, so an unlisted action is reported as "below the third option's probability".
- Disagreements cannot yet exclude Draft "assumed" options (the review JSON has no such flag); add when `review.py` emits it.
- The Mac decodes Glimmer at ≈ 8–9 tok/s and a loop answer takes ≈ 2–3 min; this is async in the product.

## Changelog
- v1 (2026-10-27 ≈05:00 UTC): tools, renderer, verifier (agent-verifier-v1, agent-lex-v1), explain loop (explain-prompt-v1), templates, parser (parse-note-v1), smoke, A1 runner.
- v2 (2026-10-27 ≈05:20 UTC, after the S1.6 smoke, before any eval record): **renderer sign fix** (a polarity phrase drops the sign only of the slot it refers to, by the same nearest-slot association as rule 4; smoke run 1 had shown `choices_pts` −61 as "61 points"); no unit doubling ("{x} luck points"); rule 3 treats "percent" right after a placeholder as a unit; rule 2 luck triggers are luck words and chance-event words (crit, miss, flinch…), not the neutral verb "swung" (smoke run 4's correct "choices swung {c} against you" had failed). agent-verifier-v2, agent-lex-v2, agent-render-v2. The 5 smoke payloads re-verify 5/5 under v2.
