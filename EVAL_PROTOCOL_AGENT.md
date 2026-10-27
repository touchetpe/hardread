# EVAL_PROTOCOL_AGENT: evaluation protocol for the HardRead explainer and note parser (E11)

*Version 1, written 2026-10-27 (D1) by lane Ag-LLM, before any eval record. Plan of record: `research/28-plan-decision.md` (S2.4, S2.5, S3.4, S4.2, E11); design: `research/27-plan-proposal-r2.md` §4.6–4.7. This protocol is **exploratory**: it is not part of the pre-registration in `PREREGISTRATION.md` and makes no confirmatory claim. It is committed before any A1, A0 or parser record; it is OpenTimestamped only if the owner approves OD-5 (default: not stamped).*

## 1. What is evaluated

**Claim under test:** an open model (base Muse Glimmer-30B), given read-only tools over an engine review, explains a hidden-information decision **without inventing numbers**: every number shown is copied from a cited engine result.

| Component | Version under test | File |
|---|---|---|
| Model | base **Muse Glimmer-30B**, GGUF `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` (SHA-256 recorded in each output's `provenance`) | `../models/muse-glimmer-gguf/` |
| Server | llama.cpp `f8def7f` `llama-server`: `--ctx-size 32768 --jinja --reasoning-format deepseek --swa-full --gpu-layers 99 --parallel 1` | runbook §Meta models |
| Request | temperature 0, top_k 40, top_p 1, seed 42, `reasoning_strength` low, `max_tokens` 2048, `tool_choice` required | `tools/agent/llm.py` |
| Explainer loop | ≤ 4 data-tool calls, then `submit_explanation`; one repair turn; template fallback | `tools/agent/explain.py` (`PROMPT_VERSION`) |
| Tools | `get_summary`, `list_key_moments`, `get_turn`, `get_luck_events`, `get_verdict` (read-only over `review.py` JSON; bridge integers from `tools/review/bridge.py`) | `tools/agent/tools.py` |
| Renderer | fills `{slots}` from cited fields by key type | `tools/agent/render.py` |
| Verifier | 7 rules of research/27 §4.6 + structure, with the research/28 §0.1 fix (digits allowed inside a cited species/move name) | `tools/agent/verifier.py` (`VERIFIER_VERSION`, lexicon `LEXICON_VERSION`) |
| Note parser | single-turn `submit_belief`, species enum = the 12 in the game, grounding of every value in the note | `tools/agent/parse_note.py` (`PARSER_VERSION`) |

**Freeze rule.** The versions above may change during development and the pilot (§7). At the start of A1, the commit hash of `tools/agent/` and the three version strings are written into the output file; they do not change until A1, A0 and the parser eval are complete. Any later change produces a new output version (`*_v2.json`), reported next to v1, never in place of it.

### Implementation clarification, v1a — committed before first A1/A0 record

The §3 source pools, eligibility, deterministic SHA ordering, 20 frozen reviews, 60 A1 and 30 A0 questions, §4 audit sampling/rubric and §5 ship thresholds are unchanged. Before a recorded call, the selection manifest fixes each source UID/side, replay/review/context SHA-256 and the exact ordered raw offered-decision list returned by the shipped app's `build_view_with_offered`; an empty list remains empty. A1 passes that list and only its exact-key cached verdicts into the existing explainer. The registered raw-pass, after-repair, template and misleading-sentence measures continue to use the direct verifier result and the §4 Glimmer-sentence audit population. A separate post-`wording_guard` count reports model answers the shipped app would replace with templates; it does not alter those registered measures or the §5 point-estimate gate. A0's regex candidates only aid annotation: every numeric claim in all 30 complete answers is classified by the stated checkable/±5-point rubric, with ten owner answer spot-checks. A recorded runner must have actual base-model/build/flags provenance and an exclusive quiescent `EVAL_LOCK` window. Missing gold notes remain `not run (no gold notes)`. Code/model changes after A1 begins require a separately reported v2 record, not an overwrite.

This addendum binds implementation to the already shipped capability; it is not a new source, model arm, exclusion, denominator or threshold. If reconciling the exact farm source or product guard requires changing §3–5, **stop and seek a separate scientific decision** rather than smuggling a new rule into code.

**Hardware.** The owner's Mac (local llama-server on port 18131) or, only if the owner approves OD-9, the Modal deployment with the same GGUF, llama.cpp commit and flags. No external API is called (no Meta, Modal or Baseten API without OD-6/OD-9/OD-14). Latency is reported with the host and whether other jobs shared the machine.

**Exclusive server window (added 2026-10-27 ≈05:50 UTC, before any record; Coord decision).** The local llama-server is shared with the app (Ag-App). Every recorded run (A1, A0, parser eval) holds `scratch_ds/agent/EVAL_LOCK` (one line: `<UTC start> <expected end> Ag-LLM <what>`) for its whole duration; the app does not call the server while the file exists. The runners create and delete it themselves (`tools/agent/evallock.py`) and record the line in the output. Pilot latencies measured without the lock are labelled "shared server, upper bound".

## 2. Arms

| Arm | System | Runs by default |
|---|---|---|
| **A1** Glimmer + tools (the product) | `explain.py --mode loop` | yes |
| **A0** Glimmer, no engine | the same base model, the replay text only (Showdown protocol log, team preview included), the same question, free text, no tools; `max_tokens` 2048 | yes |
| A2 Muse Spark 1.3 + tools | same loop through the Meta API | **no**: only if OD-14 is approved (P1) |

## 3. Sets

**A1 reviews (20):** 10 crawl games reviewed in Draft-B mode + 10 `hr_val` farm games (evaluation-only pool; never used for training).
- Crawl candidates: game-sides of `scratch_ds/human_broad/games.jsonl.gz` in the held-out (test) half of the Draft-B recalibration split (S2.2, split by game-id hash), with ≥ 10 turns and a win/loss result. Farm candidates: decided `hr_val` games (`scratch_ds/val/`), both seats eligible, ≥ 10 turns.
- Selection: sort candidates by `sha256("a1/" + uid + "/" + side)` and take the first 10 of each pool that `review.py` reviews without an exception (exceptions are counted and reported). One side per game.
- Each review JSON is produced by the current `review.py` (Draft B: the user's team drafted by `human_eval.draft_team_smogon`; farm: the seat's real team export) and **frozen before any Glimmer call**: its SHA-256 goes into the output file.
- Crawl raw files never leave the Mac (`handoff/06_DATA.md`); only aggregate metrics and the rendered sentences are reported.

**Questions (fixed, 3 per review → 60 items):**
1. Q1 "Was this game decided by luck or by my choices?"
2. Q2 "What was the biggest piece of luck in this game, and how much did it matter?"
3. Q3 "Where did the engine disagree most with my choices, and by how much?"

**A0 (30 items):** Q1–Q3 on the first 10 reviews of the A1 order (5 crawl + 5 farm).

**Parser (40 gold notes):** the owner's notes on the listed disagreements of the owner's games (`../data/owner/notes.jsonl`) and gold labels in the belief schema, written **before seeing any parser output** (`../data/owner/notes_gold.jsonl`, S2.8). Fallback: ≥ 20 gold notes, with wider CIs. If the owner provides none (OD-7 default), the parser eval is reported as "not run".

## 4. Metrics

### A1
| Metric | Definition |
|---|---|
| **Raw pass rate** | items whose first `submit_explanation` passes the verifier (all 7 rules + structure) |
| **After-repair rate** | items that pass after at most one repair turn (raw passes included) |
| **Template share** | 1 − after-repair rate: items answered by the deterministic template (incl. server errors, which are also counted separately) |
| Tool calls per answer | data-tool calls (mean, max) |
| Latency | end-to-end seconds per item, p50 / p95 (and per request) |
| **Misleading-sentence rate** | blinded audit of 60 rendered sentences (below) |
| Verifier failures by rule | count of failing rules on raw attempts (diagnostic) |

All rates are reported with Wilson 95% intervals over items (n = 60).

**Misleading-sentence audit.** Population: every sentence of the answers that passed with Glimmer text (raw or repaired; templates excluded because they are fixed code). Draw 60 sentences uniformly with `random.Random(20261027)` (all of them if fewer). The auditor (an agent rubric, then the owner spot-checks 15 drawn with seed 15) sees the rendered sentence, the question and the JSON of the tool outputs it cites, not the arm, raw/repaired status or verifier report. A sentence is **misleading** if a reader would come away with a false belief about the game given that JSON:
- (a) a quantity attributed to the wrong thing (luck vs choices vs engine preference; event chance vs win chance);
- (b) a direction or sign that contradicts the data;
- (c) a causal or decisive claim the data does not support (e.g. "the crit decided the game" when its swing is small next to the totals);
- (d) a judgement of the player's decision (mistake, correct, misplay) without a verdict;
- (e) certainty about something the data gives a probability below one half;
- (f) a wrong association of a move, Pokémon or turn.
Rate = misleading / audited, Wilson 95% CI; owner–agent agreement on the 15 is reported (count of disagreements).

### A0 (no engine)
- Extract every numeric claim of the free-text answer (agent rubric; the owner spot-checks 10 answers).
- **Checkable share** = claims that map to an engine quantity in the review (start win chance, luck points, a turn's win chance, a gap, an event's chance).
- **Within ±5 points** = checkable claims within 5 percentage points of the review value.
- Never reported as "% verified" or "% correct"; reported next to A1's by-construction 100% copied numbers.

### Parser
- Unit: (species, field, value) triples for item, ability, tera, moves_has, moves_not; plus expects_action (kind, species, move) and confidence as single fields.
- Per-field and micro F1 for Glimmer and the keyword baseline, bootstrap 95% CI by note (2,000 resamples, seed 0); the paired difference Glimmer − keyword with its CI.
- Invented-value count (values not grounded in the note; 0 by construction, checked), schema-valid rate, latency p50/p95.
- VT4 cross-check (research/28 S3.3): parse F1 ≥ 0.80 against gold, or the chips are corrected by the user.

## 5. Ship gate (decided now)

- **A1 after-repair ≥ 80% and misleading ≤ 5%** (point estimates; CIs reported alongside) → the product shows live Glimmer text and the submission may flag the Agentic prize.
- Otherwise → templates phrase, Glimmer selects the moments ("Glimmer selects; templates phrase"); the Agentic prize is not flagged.
- Early read (research/28 S3.6): after-repair on the first 20 A1 items is reported at the D3 triage; it does not change the gate.

## 6. Wording rules for reporting (research/28 §B)

- Allowed: "every number is copied from a cited engine result"; "Glimmer's first draft passed X%; after one repair Y%; Z% fell back to templates. Without the engine, W% of its numbers were within 5 points." Only with measured X, Y, Z, W.
- Never: "verified agent", "verifiable reasoning", "fine-tuned" for the explainer or parser (both are **base** Glimmer), "coach", "analyzer".

## 7. Pilot (development, not part of the record)

Before A1, a pilot of ≤ 10 items runs on reviews outside the A1 set (the local farm and public reviews in `scratch_ds/review/`) to measure the raw / after-repair / template rates and latency and to debug. Its outputs are `scratch_ds/agent/a1_dryrun*.json`, labelled `"pilot": true`. Prompt, verifier or tool changes after the pilot are allowed until A1 starts (§1 freeze rule) and are listed in `tools/agent/README.md`.

## 8. Outputs

| File | Content |
|---|---|
| `scratch_ds/evals/faithfulness_v1.json` (+ `.items.jsonl`) | A1: provenance (GGUF SHA-256, llama.cpp commit, flags, versions, `tools/agent` commit, review SHA-256s), per-item results with full traces, metrics, audit |
| `scratch_ds/evals/noengine_v1.json` | A0 |
| `scratch_ds/evals/parser_eval.json` | parser F1 (Glimmer, keyword), CIs |
| `scratch_ds/agent/smoke_toolloop.json`, `parser_smoke.json`, `verifier_tests.json`, `a1_dryrun.json` | S1.6, S2.4, S2.5 and pilot signals |

Each run is logged in `handoff/PROGRESS.md` (Ag-LLM); headline numbers that reach the video go to `ops/video/numbers.json` with their source line (S5.3).
