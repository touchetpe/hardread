# HardRead engineering results ledger

These are tool and infrastructure measurements only, not scientific claims. Every run is listed, failures included.

## 2026-10-26: local engine (Mac M4 Max)

| Run | What | Result |
|---|---|---|
| tests | Foul Play test suite (fork, poke-engine 0.0.48 Gen9 + Tera) | 737 passed (also after the oracle patch) |
| smoke-F1-1, debug-1 | First self-play | **FAILED:** Foul Play's example team is rejected by current Gen9 OU rules (Spore, Tera Blast), and the bot hangs. Fixed by `ou_hr/example_fixed` plus a validation step. |
| smoke-F1-2 | 1 game at F1 | Completed naturally, 36 s |
| tput-F1-8x4 | 8 games at F1, 4 in parallel | 8/8 completed, ≈29 s per game, ≈460 games/hour |
| smoke-C1-1 | 1 game at C1 (2 threads per world) | **FAILED:** search worker crash (`BrokenProcessPool`); the 2-thread mode is unstable |
| smoke-C1t1-1 | 1 game at C1t1 (4 worlds × 2,000 ms × 1 thread) | Completed, 179 s; ≤0.4 GB per search process |
| measure-C2-1 | 1 game at C2 (8 worlds × 3,500 ms) | Completed, 323 s (≈1.4 core-hours per game on the Mac) |
| oracle-test-p1 | Oracle at p = 1 | **FAILED (runner bug):** a concurrent runner shut down the shared server. Fixed with per-run `--port`. |
| oracle-test-p1b | Oracle at p = 1 | Completed; 500/500 substitutions equal the true team |
| oracle-test-p05 | Oracle at p = 0.5 | Completed; ≈half of the worlds substituted |
| aa-F1-100 | A/A at F1, mirror team | 52–48 (n = 100, CI ±9.8 points), 0 failures |
| pool-F1-6 | Diverse team pool | 6/6 completed, 27–92 s |
| aa-pool-F1-200 | A/A at F1, diverse pool, **no side swap** | **A (challenger) won 118/200 = 59%**, outside the 95% band (±6.9 points; p ≈ 0.01). 0 failures; median game 38 s. **Warning: possible seat bias, mixed up with team strength because sides weren't swapped.** Follow-up: `hraa-20261026-310d28` (1,000 side-swapped pairs on Azure). **Resolved below: no seat bias.** |

## 2026-10-26: Azure H100 Glimmer serving (`hrglimmer-20261026-9925f5`)

**Setup:** llama.cpp `f8def7fe` built with CUDA for sm_90 (88 s); pinned Q4_K_M GGUF, hash verified (87 s download). The workload is 31 real battle requests from the Oct 25 Mac game: public observation plus the `submit_action` tool.

| Variant | Legal actions | p50 | p95 | Decisions/min | Note |
|---|---|---|---|---|---|
| A: reasoning low, 1 slot | **31/31** | **5.6 s** | **7.3 s** | 10.8 | ≈361 completion tokens; decode ≈72 tok/s; prompt ≈2,600 tok/s |
| B: `--reasoning-budget 0`, 1 slot | 0/31 | 4.4 s | 4.5 s | — | **INVALID:** the budget flag does not disable Glimmer's reasoning (≈1,000 reasoning characters remain); every request is cut off at 256 tokens |
| C: `--reasoning-budget 0`, 4 slots | 0/31 | 9.8 s | 10.6 s | — | Same failure |
| D: reasoning low, 4 slots, 4 parallel | 31/31 | 11.6 s | 17.4 s | 19.5 | Higher throughput, higher latency |

**Takeaways:**
- Base Glimmer is usable for live play and asynchronous review at p95 ≈7 s.
- Getting under 2 s needs the fine-tuned "act directly" model (planned SFT); flags won't do it.

## 2026-10-26: Azure CPU farm test (`hrfarm-20261026-60eb6e`, one H100 node used as CPU only)

**Node:** AMD EPYC 9V84, **40 physical cores (1 thread per core)**, 314 GB RAM. Setup took 96 s (Node, Rust, poke-engine build, Showdown at `c0845f33`).

| Batch | Games | Failures | Median game | Throughput per node |
|---|---|---|---|---|
| F1, 10 in parallel | 160 | 0 | 32 s | **1,080 games/h** |
| C1t1, 4 in parallel | 8 | 0 | 208 s | **60 games/h** |
| C2, 2 in parallel | 4 | 0 | 313 s | **22.7 games/h** |

Concurrency was conservative, so C1t1 and C2 may gain ≈20–40% when tuned (not yet measured).

**Capacity implications** (4 always-on nodes; up to 10):

| Use | 4 nodes | 10 nodes |
|---|---|---|
| Self-play data at F1 | ≈4,300 games/h ≈ **100k games/day** | ≈250k/day |
| Self-play labels at C1t1 | ≈240 games/h ≈ 5,800/day | ≈14k/day |
| Sealed test, 12,000 games at **C2** | ≈130 h | ≈53 h |
| Sealed test, 12,000 games at **C1t1** | ≈50 h | ≈20 h |

**Conclusion:** the pre-registered n = 12,000 at C2 cannot fit in the D6–D7 window on this cluster. The sealed design must be resized (see the status note to the user).

## 2026-10-26: decision logging (`declog-test`)
- One F1 game on the diverse pool with `--log-decisions`: 40 decision records (22 A + 18 B), ≈11.7 KB each without world states.
- **Fields:**
  - public view, including Foul Play's inferences (impossible items, speed range);
  - every sampled world's opponent sets and weight;
  - both sides' per-world MCTS visits and average scores;
  - the choice.
- Foul Play tests are still at 737 passed.
- **Storage:** ≈0.5 MB per game uncompressed, so 100k games ≈ 47 GB raw. Gzip per game before upload; node disks are 128 GB.
- **To check before belief training:** opponent item encodings (`None` vs unknown vs removed) in the public view.

## 2026-10-26: side-swapped A/A on Azure (`hraa-20261026-310d28`)
- **Setup:** vanilla vs vanilla Foul Play, F1, pool `gen9/hr_hl_dev`, 1,000 team pairings, each played twice with the teams swapped (2,000 games, A always challenges). One node, 14 games in parallel, 1,262 games/h.
- **Result:** challenger A won **987 / 1,998 = 49.4%**. Paired by pairing (998 complete pairs): **49.35% ± 2.04** (95%). That is inside the pre-registered 50 ± 2.2 band, so **no seat bias** at F1.
- **Team strength:** in 56.8% of pairings the same team won both games (50% if teams didn't matter). That explains the earlier unswapped 59%: team strength plus chance, not seat. All head-to-head runs keep `--side-swap`.
- **Failures: 2 / 2,000 (0.1%), both Foul Play bugs, symmetric between sides:**
  - g0160: both bots crashed with `ValueError: Unknown terrain magicroom` (`poke_engine_helpers.get_terrain_string`);
  - g1731: A crashed with `PanicException: Invalid PokemonMoveIndex: 4` (a fifth move on one Pokémon, likely Illusion-related), then B hung.
- **For the sealed test:** count bot crashes as losses for the crashing side, as pre-registered. Any HardRead fix to these bugs is reported separately from the levers.
- The A/A is still to be repeated at C1t1 and with "our harness, levers off" before the freeze (D5–D6).

## 2026-10-26 evening: overnight farm (10 nodes, until 08:00 UTC Oct 27)
- **Jobs:**
  - value-of-information gate: `hrvoi-20261026-p{100,050,025}-a5fb`. Oracle p = 1 / 0.5 / 0.25 vs vanilla, C1t1, `hr_val` pool, 300 side-swapped pairings each, oracle seat alternating every pairing.
  - label farm: `hrlab-20261026-n{1..6}-01d4`. C1t1 self-play on `hr_train`, decision log v2 plus Showdown battle logs, 25-game chunks.
  - volume farm: `hrvol-20261026-n1-a612`. F1 self-play on `hr_train`, 150-game chunks.
- **Earlier label submissions cancelled after minutes:**
  - `*-fbb5`: wrong log level, which hides the win/loss line.
  - `*-1621`: decision log v1, missing sampler fields.
- **First chunks (16:45 UTC):**
  - label farm: 125 games, 0 failures, 11,125 decisions (89 per game), all v2. 70% of decisions searched 8 worlds, because Foul Play doubles worlds while the opponent's active has fewer than 3 revealed moves.
  - volume farm: 299 games, 23,211 decisions.
  - The extractor found a history cut for every decision.
- **Throughput:**
  - label farm: ≈60 games/h per node at 5 in parallel;
  - value-of-information jobs: ≈65 games/h per node at 4 in parallel (40 games in 37 min);
  - volume farm: ≈1,200 games/h.
- **Value-of-information gate, provisional after 40 games per level (do not cite):** oracle side wins 67.5% (p = 1), 67.5% (p = 0.5), 47.5% (p = 0.25), with intervals of ±11 to ±18. The full 600 games per level are expected around 01:30 UTC.

## 2026-10-26 evening: review engine (`tools/review/`, commit 3cc3859; local, 250 C1t1 label-farm games)
- **Reconstruction:** 21,351 decisions (500 sides) rebuilt offline by driving Foul Play's own battle code.
  - **Farm format (exact HP):** 92.8% strict-correct. State plus legal set match 99.98%; the legal set matches 100%; the action matches 100% whenever the log shows it.
  - **Public-replay format (percent HP):** 92.4% strict.
  - **All strict misses are actions the replay never shows (7.2%):** the Pokémon fainted, slept, was paralysed or flinched before moving, or Encore replaced the move. The review gives a cost range for these.
  - One real 53-turn ladder replay, with its team drafted from what was revealed, reconstructs cleanly (58 decisions).
  - **The D2 gate on the owner's replays is still to be measured.**
- **Calibration (5 folds by game):**
  - raw root value: Brier 0.2062, ECE 0.018;
  - Platt: Brier 0.2060, ECE 0.0075 (Brier gain within noise; root values are already close to calibrated);
  - turns 0–5 carry no signal; turns 16–30 reach Brier 0.181.
- **Mirror check** (9,310 matched seat pairs): |P_A + P_B − 1| has mean 0.061, median 0.038, p90 0.14, and grows with turn. Calibration doesn't close it: the gap comes from each seat's private information and search noise.
- **Decision cost** (4 worlds × 1 s per decision):
  - the bot's own choices cost 0.0015 win probability on average (median 0);
  - a random alternative costs 0.089;
  - the review's top move equals the bot's 76% of the time;
  - noise floor ≈ 0.02;
  - the human replay averaged 0.032 per decision (illustrative; team drafted).
- **Hax meter: FAILED its sanity check:**
  - summed luck vs winner: correlation 0.02 / 0.09 (100 games per seat, SE ≈ 0.10); deeper valuation doesn't change it;
  - per-turn signal exists (0.18 vs the bot's value changes), and the seats are mirrored at −0.55 per game;
  - a plain event count (crits, misses, full para, flinches) does better: 0.18 over 250 games.
  - **Suspects:** short-horizon valuation of post-chance states (forced-replacement KOs), and fake luck from wrong sampled bulk.
  - **Fix in progress.** Until it passes, the product shows chance as events, labelled experimental.
- **Review cost:** 25–200 s per game, ≈1.1 s per decision.

## 2026-10-26 ≈20:30 UTC: second wave (cluster raised to 20 nodes by the owner)
**Code** (Foul Play `df65fd8`; HardRead `4cadb43`; 737 Foul Play tests pass with everything off):
- Engine levers are hooked in (`HARDREAD_LEVER_SELECT` = fp / argmax / value; `HARDREAD_LEVER_WORLDS` = fp / double-half / half-double).
- `run_match.py --treat/--treat-seat` gives the levers to one side only, alternating its seat every pairing. Local smoke: 3 levers × 2 games, 0 failures; the lever was applied only on the treatment side.
- **Decision log v3** (`HARDREAD_LOG_BELIEF_SAMPLES=N`) stores N draws of Foul Play's own set sampler for every visible opponent Pokémon. That is its true belief after its in-place damage pruning, which v2 could not rebuild offline. The random state is saved and restored, so play is unchanged. Cost ≈0.3 s per decision on the loaded Mac at N = 64.

**Jobs** (all end 08:00 UTC Oct 27):
- `hrval-20261026-n{1,2}-9231`: C1t1 self-play on `hr_val` with log v3. This is the held-out S-A test set and the student validation set.
- `hrvol-20261026-n{2,3}-9231`: F1 volume farm on `hr_train` for belief training. Distinct teams are the binding resource, per the S-A learning curve.
- `hrlev-20261026-{argmax,value,dbl}-n{1,2}-9231`: lever screens vs vanilla, C1t1, dev set A = `hr_train`. Same seeds across levers (common random numbers).

**Earlier tonight:**
- `hrvol-20261026-n1-a612` was cancelled at 17:05 UTC to free a node for the arena test. Its chunks c000–c003 are kept; c004 was lost.
- Agent tests: `hrarena-20261026-00cc` completed (report pending), and `hrsft-20261026-smoke01-dbe8` is running.

**Belief benchmark S-A** (`tools/belief/`, commit 4c6a2b3; 29 C1t1 chunks; held out by roster within `hr_train`):

| Method | Item top-1 | Spread top-1 | Item mean p(true) |
|---|---|---|---|
| Usage prior | 0.566 | 0.668 | 0.421 |
| Foul Play as played (logged worlds) | 0.536 | 0.645 | 0.471 |
| Learned v0 | 0.577 | 0.680 | 0.468 |
| v0 + logged worlds | 0.603 | 0.721 | 0.484 |

- Item NLL: v0 1.19, as played 2.03.
- Learning curve: dev loss falls linearly in log(games) and hasn't flattened. Distinct teams are the binding resource.
- Foul Play's own constraints exclude the true item in 0.41% of hidden-item rows.

**Value of information, provisional at 160 games per level:** 71.9 ± 6.0 (p = 1), 66.3 ± 7.1 (p = 0.5), 47.5 ± 7.6 (p = 0.25).

## 2026-10-26 evening: arena, student, Glimmer SFT smoke
### Arena (`tools/arena/`, commit f4583d6)
- **"abyssal":** PokéChamp's heuristic bot `AbyssalPlayer`, pinned at commit `0f84c46` and run unmodified.
- **Azure `hrarena-20261026-00cc`:**
  - Foul Play F1 vs abyssal: **93/100** (95% CI 86–97%), 0 failures.
  - Base Glimmer Q4_K_M through the HardRead policy hook: **9–20 (31%)**, **31% illegal** (268/863). Every illegal move is the 1,024-token cap hit while reasoning. p50 / p95 latency 14.7 / 17.8 s.
- **Prompt parity:** the play-time prompt equals the training prompt on 863/863 decisions.

### Student (`tools/student/`, commit 36f6858; lever in Foul Play `fb80f6f`)
- **Model:** a 24.9M-parameter transformer: 12 Pokémon tokens, field, 16 recent public events, one token per legal action. Heads: policy, search value, win probability, opponent next action.
- **Training data:** 1,100 C1t1 games (92k decisions), held out by roster hash.

| Held-out metric | Student | Reference |
|---|---|---|
| Top-1 vs search argmax | **49.9%** | — |
| Top-1 vs played choice | 49.2% | max-damage heuristic 34.7%, uniform 16.4% |
| KL to search policy | 0.477 | uniform 0.861 |
| Value Brier | **0.176** | search root value 0.208 |
| CPU latency (numpy) | ≈8 ms | — |

- **Learning curve:** +2–5 points per doubling of data.
- **Ablations:**
  - recent events: +0.95 points top-1;
  - opponent-action head: improves value Brier, but not the policy at this scale.
- **Standalone** (no search) vs Foul Play F1: 7 wins in 40 games.
- **Lever `student-blend`** (q = 0.7·search share + 0.3·student): local smoke with the sidecar, 0 failures.

### Glimmer SFT smoke (`hrsft-20261026-smoke01-dbe8`; pipeline commit fe214be)
- **Run:** 160 steps × 16 = 2,560 action examples (C1t1, prompt v1.1). 12.6 s/step, about 0.79 s/example of about 1,225 tokens, 27 GiB peak.
- **Format:** learned by step 10; greedy generation 24/24 legal, 0 malformed. The LoRA → GGUF conversion works with a one-line converter fix, and llama-server loads the adapter. The serving evaluation is pending.
- **Decision quality (200 held-out decisions):**
  - fine-tuned: top-1 34.0%, top-3 67%;
  - base: 30.5% / 58%;
  - always the first menu item: 26%.
- **Diagnosis:** only 1–2 of about 57 target tokens carry the decision.
- **Next (v0):** target = search argmax, choice-token loss weighting, soft-target distillation on the choice token, 50k examples.

## 2026-10-26 ≈22:00 UTC: luck meter v2 passes (`tools/review/`, commit above)
- **Root cause of v1's failure:** poke-engine's MCTS scores a position as sigmoid(eval(leaf) − root eval). Branch values from separate searches therefore had no common reference, and a KO's gain was subtracted out.
- **Fix:** an absolute scale, A = eval + logit(MCTS value) / 0.0125. The Python port of poke-engine's evaluation matches its Rust evaluation to 1e-4 on 300 states.
- **Checks, over 500 self-play games (999 game-sides, 95% bootstrap intervals):**

| Check | Result | Verdict |
|---|---|---|
| Var(outcome − Σluck) / Var(outcome), seat-symmetrised | **0.77 [0.71, 0.84]** | pass |
| corr(Σluck, outcome) | 0.45 | — |
| Mirror corr(L_A, L_B) per game | **−0.81** (v1: −0.55) | pass |
| Mean luck per decision | −0.0005 [−0.0010, −0.0001] | close; residual ≈ −0.02 win probability per game |
| Event-count baseline | corr 0.19; luck correlates 2.4× better | pass |

- **Replication:** each 250-game half passes, including the games the student never trained on.
- **Estimator ablations:**
  - an events-only estimator mirrors best (−0.90) but removes almost no variance (0.96–0.98);
  - the student as branch evaluator is worse on every check (0.87 / 0.90).
- **Absolute value as win probability:** Brier **0.154** vs 0.206 for the raw root value; seat mirror gap 0.061 → 0.030. The review graph and decision costs now use it.
- **Cost:** luck ≈ 8.6 s CPU per game-side; a full review takes ≈27 s for a 14-turn game and ≈92 s for a 53-turn game.
- **Gaps:**
  - chance after a mid-turn pivot switch is not counted;
  - damage rolls count only where they decide a KO;
  - validated on bot self-play only; human replays come next.

## 2026-10-26 ≈21:40–22:00 UTC: cluster raised to 27 nodes; jobs added
- `hrsft-20261026-v0b-2205` (3-node DDP): a Glimmer v0 hedge on the same 55.6k rows. LoRA alpha 32 (scale 1.0 vs 0.5), choice weight 20, soft alpha 0.9.
- `hraa-20261026-c1t1-n{1,2,3}-d106`: early A/A at the sealed configuration (C1t1, vanilla vs vanilla, side-swapped, `hr_train` teams). The pre-registered A/As still run on D5–D6.
- `hrlev-20261026-dbl-n1-r9231`: double-half lever screen restarted on an idle node.
- **Student v1 GPU retrain:** to be submitted by the student lane (`hrstu-*`).
- **Glimmer v0 progress at 21:27 UTC** (`steps.jsonl`; weighted loss confirmed on GPU):
  - **v0-ddp:** 3 nodes at 6.7 s/step (≈3.1× the single node). Training-batch choice accuracy rose from 0.26 to ≈0.40; choice cross-entropy 1.94 → 1.61; format cross-entropy 0.14 → 0.05.
  - **v0-safe:** 13.9 s/step.
- **Provisional at 21:27 UTC:**
  - value of information (320 games per level): 70.9 ± 4.4 (p = 1), 65.0 ± 5.0 (p = 0.5), 50.9 ± 5.1 (p = 0.25);
  - lever screens (220 games each): value 54.5 ± 5.9, argmax 48.6 ± 6.0.

## 2026-10-26 22:42 UTC: G-24h rehearsal (`hrarena-20261026-g24hpre-b71e`)
- **Setup:** the smoke adapter (160 steps, 2,560 examples, prompt v1.1, served with prompt v1.2) through llama.cpp with LoRA, playing abyssal on 100 side-swapped pairings from `gen9/hr_hl_dev`, 4 games in parallel.
- **Result:** 199/200 games completed (1 timeout).
  - Illegal moves: **0 / 5,857**.
  - Latency p50 / p95: **3.1 / 3.5 s** (4 concurrent).
  - **Win rate 14.6% [10.3, 20.2]: G-24h FAILS on win rate** (needs ≥ 35%). It passes on legality and latency.
- **Reading:** the act-directly model trained on 2.5k examples plays worse than base Glimmer with reasoning (31%, despite 31% illegal moves). Legality and speed are solved; strength is not.
- **Official G-24h:** run with the v0 adapters (50k examples, weighted loss). Fallback per plan: the 25M student (or Foul Play) pilots Spar, and Glimmer reads and explains.
- The student-alone vs abyssal measurement is running locally (`arena-student-vs-abyssal-40`).

## 2026-10-26 ≈23:00 UTC: review engine on human games (513 random top-ladder games, 1,019 game-sides)
- **Reconstruction fixes** (nicknames from switch lines, Revival Blessing, Healing Wish on the opponent): 99.98% clean without nicknames in the export (was 77.8%), no crashes. Farm reconstruction unchanged.
- **Win-probability calibration:**
  - raw absolute value on humans: Brier 0.203, ECE 0.052 (self-play: 0.154 / 0.021);
  - humans are less extreme than the engine (predicted 5% → 12% actual, 95% → 92%);
  - a human calibrator (slope 0.60), fitted on half the games, gives ECE 0.042 → 0.027 on the other half. The review uses it.
- **Luck:**
  - corr(Σluck, outcome) 0.41 [0.34, 0.48] vs 0.14 for the net chance-event count;
  - variance ratio 0.80 symmetrised (on humans this is an upper bound on the luck share: luck and skill covary);
  - mirror −0.49.
  - **Drafting matters:** a realistic draft (most common Smogon set consistent with the reveals) removes the bias (+0.0004 per decision) and improves the mirror to −0.60.
- **Decision cost** (4 worlds × 20k iterations):
  - top-50 players and their opponents: mean 0.023, median 0.005;
  - the bot's noise floor on the absolute scale: 0.0077;
  - **it does not separate ratings** within 1588–1843 (corr 0.03).
  - Next: full search, and the broad crawl's wider rating range.

## 2026-10-26 ≈23:10 UTC: student alone vs abyssal (local, `arena-student-vs-abyssal-40`)
- **Setup:** the 25M student (checkpoint final/frac1, 68k decisions; through the sidecar) as the HardRead player via `HARDREAD_POLICY=student`, legal menu from F1 worlds, against abyssal on 20 side-swapped pairings from `gen9/hr_hl_dev` (seed 2000, the same teams as the G-24h rehearsal).
- **Result: 32/40 = 80% [65, 90]**, 0 failures, 0 illegal in 1,071 decisions, latency p50 / p95 35 / 66 ms.
- **Reading:** the student alone already clears G1's bar (≥ 60% vs abyssal). It is the fallback pilot for Spar. Same teams: Glimmer smoke 14.6%, base Glimmer (reasoning) 31%, Foul Play F1 93%.

## 2026-10-26 ≈23:30 UTC: Metamon raw processing (`tools/metamon/`, commit 12e84e2; local)
- **Index:** 2,038,375 gen9ou games (rating bands as earlier).
- **Belief subset:** 2026-01..05, rating ≥ 1500, full 6v6 preview = **57,310 games** (only 20% of 2026 games are rated ≥ 1500).
  - Both observer seats reconstructed: **3.70M decisions**, 114,620 sides. 0.03% failed (Transform fifth move, Magic Room, Eiscue).
  - Human action vs reconstructed choice: 99.994% agree.
- **Fast reconstruction** (Foul Play's pruning and dataset init off; views byte-identical on the games checked): 0.14–0.26 CPU-s per game.
- **Partial labels** (revealed by game end):
  - reveal rates ≈ 46% items, 43% abilities, 13% tera;
  - 20k-game sample → 5.5M belief examples over 6,939 training rosters;
  - a masked-loss smoke run worked (reveal-biased targets).
- **Revealed-set priors (2026H1, ≥ 1500, 177 species) vs Smogon:** top-1 agreement items 0.54, abilities 0.85, tera 0.70.
- **Human action table:** 3.70M rows with rating band.
- **Ops note:** the agent ran a broad `pkill -f multiprocessing.spawn`, which briefly killed other lanes' pool workers (respawned). Agents are now told to stop only their own PIDs.
- **Next:** belief v1 (farm full labels + human partial labels) on the S-A test set (`hr_val`, log v3 with Foul Play's own 64-draw belief as the baseline).

## 2026-10-26 23:05 UTC: student v1 (`hrstu-20261026-v1-8187`, one H100, ≈10 min of node time)
- **Data:**
  - C1t1 label farm: 2,699 games, 226k decisions (train 168k / dev 21k / roster-hash test 37.6k);
  - F1 volume pretraining: 109k decisions;
  - `hr_val` cross-pool test: 440 games, 33k decisions.
- **Results** (top-1 vs search argmax; KL to the search policy; value Brier):

| Model | Roster-hash test | `hr_val` |
|---|---|---|
| v0 | 49.7% / 0.475 / 0.185 | 49.9% / 0.480 / 0.176 |
| Control (C1t1 only, paper same-input control) | 53.3% / 0.431 / 0.178 | 53.6% / 0.433 / 0.173 |
| **Main** (F1 pretraining + C1t1) | **54.5% / 0.411 / 0.176** | **55.3% / 0.415 / 0.171** |

- The search's root-value Brier is 0.214 / 0.200.
- No pool overfitting: the unseen `hr_val` rosters score as well as the test split.
- **Learning curve:** the control gains ≈3 points per doubling and is not flattening; F1 pretraining is worth ≈8× the C1t1 data at 21k decisions.
- **Latency:** 6.6 ms on the Mac (numpy); 44 ms numpy / 33 ms torch on the Azure node CPU.
- **Lever screens submitted:** `hrlev-20261027-sb{015,03,05}-9231` (student-blend λ = 0.15 / 0.3 / 0.5 vs vanilla, C1t1, dev set A).

## 2026-10-27 ≈00:00 UTC: S-A benchmark on the pre-registered test set (`hr_val`, log v3) — belief v1 (commit e8465e1)
- **Test set:** 440 games, 33,253 decisions, 146,225 rows, 290 `hr_val` rosters.
- **Metric:** item NLL (ε 1e-4); bootstrap CIs by game; paired Δ vs Foul Play's own belief (`fp_belief`, 64 draws after damage pruning).

| Method | Item NLL | Δ vs fp_belief | Item top-1 |
|---|---|---|---|
| Foul Play's own belief (`fp_belief`) | 1.800 [1.720, 1.885] | ref | 0.562 |
| Usage prior (Smogon) | 1.301 | −0.500 | 0.576 |
| As-played worlds (4–8) | 2.603 | +0.803 | 0.519 |
| v0 | 1.214 | −0.586 | 0.596 |
| **v1 human pre-train → farm fine-tune** | **1.182** | **−0.618 [−0.682, −0.557]** | 0.601 |
| v1 + `fp_belief` draws as input | **1.172** | **−0.629** | **0.605** |

- `fp_belief` gives zero probability to the true item in 9% of rows.
- Foul Play stays ahead on EV class and spread (its damage pruning). The worlds-input model wins those back (EV class 0.814, spread 0.716).
- **Human data:**
  - helps as pre-training (−0.014 item NLL, 3 seeds); joint training does not help on `hr_val`;
  - on held-out human games (revealed labels), more human data helps steadily;
  - reveal-bias correction is essential: Choice items and Boots are under-revealed, Leftovers and Life Orb over-revealed.
- **Contamination filter** (pre-registration A2): 29% of human rows involved an `hr_val` or reserved roster and were dropped.
- **Reading:** the L2 "reader beats the champion's belief" result (Ending B) holds on the pre-registered test. The engine lever (belief-weighted world sampling) is next.

## 2026-10-27 ≈00:25 UTC: Glimmer v0 outcome
- **Both v0 jobs ended Failed:** `run_sft.py`'s internal train timeout (default 10,800 s) killed axolotl at 3 h, despite the 6 h job timeout. My submission error: `--train-timeout` was not passed.
- **v0-safe** (`hrsft-20261026-v0safe-2205`): stopped at checkpoint-700 (of 1,250; ≈11.2k rows seen), then evaluated, converted and served.

| v0-safe evaluation | Result |
|---|---|
| Teacher-forced top-1 vs Foul Play (400 held out) | **0.395** (base 0.2525; first menu item 0.18; smoke 0.34) |
| Top-3 | 0.72 |
| CE vs search policy | 1.79 (base 3.36) |
| llama.cpp + LoRA | **400/400 legal**, 0 malformed, p50 / p95 1.41 / 1.49 s |

- **v0-ddp** (`hrsft-20261026-v0ddp-2205`): stopped at checkpoint-1400 (of 2,315; ≈33.6k rows seen). The DDP job died during rank 0's evaluation. To salvage: eval-only from checkpoint-1400, then resume.
- **v0b** (3-node DDP) will hit the same cap at ≈00:55 UTC.
- **Official G-24h arena:** `hrarena-20261027-g24h-v0safe-07d3`, v0-safe adapter vs abyssal, 200 games, seed 2000 (same teams as the rehearsal).
- **For comparison, the 25M student** (same public input): 54.5% top-1 held out, 80% vs abyssal.

## 2026-10-27 ≈00:50 UTC: provisional and final numbers (analysis: `tools/analysis/voi.py`, `tools/analysis/h2h.py`)
- **Value of information (≥ 500 games per level, so the gate status is "final"; the jobs still run to 600):**
  - oracle p = 1: **70.96 ± 3.61**;
  - p = 0.5: **63.46 ± 3.84**;
  - p = 0.25: 53.46 ± 4.05 (519 / 519 / 518 games).
  - **Gate PASS:** p = 1 ≥ 58%, and the p = 0.5 lower bound (59.6) > 50%.
  - Knowing the opponent's sets is worth about +21 points at C1t1. Partial knowledge is roughly linear in p above 0.25.
- **Lever screens** (dev set A, C1t1, treatment win rate, pairing-clustered 95% CI):

| Lever | Win rate | Games |
|---|---|---|
| argmax | 49.56 ± 3.45 | 679 |
| value | 51.91 ± 3.35 | 679 |
| double-half | 49.5 ± 6.31 | 199 |
| student-blend λ 0.15 | 48.75 ± 9.6 | 80 |
| student-blend λ 0.3 | 46.67 ± 11.45 | 60 |
| student-blend λ 0.5 | 61.67 ± 10.17 | 60 |

  - Belief-lever screens (`hrlev-20261027-bel*`) started or are queued.
  - So far no lever is clearly above +2 points; the student-blend and belief screens are too young to read.
- **Early A/A at C1t1** (`hraa-20261026-c1t1-n{1,2,3}-d106`): seat A **51.19 ± 4.36** over 418 games (2 timeouts). Consistent with no seat bias so far; it runs to 08:00 UTC.

## 2026-10-27 ≈02:00 UTC: handoff review fixes (clarifications, corrections, failure attribution; no new experiment)
- **Pre-registration amendment A3** (before any pre-registered A/A or sealed game):
  - A/A decision rule: point estimate within 50 ± 2.2 AND two-sided p ≥ 0.05, n unchanged. The old wording passed a correct harness only ≈16% of the time per A/A.
  - "Vanilla" and A/A 2 as actually run.
  - Failure attribution.
  - Sealed layout, seeds and rerun rule.
  - Under the old literal rule the exploratory F1 A/A (`hraa-20261026-310d28`, 49.35 ± 2.04 = [47.31, 51.39]) **fails**: its interval is not inside [47.8, 52.2]. Under A3 it passes. The 2026-10-26 entry above checked only the point estimate.
- **S-A headline, clarified** (same numbers as the ≈00:00 UTC entry; no new run). Learned v1 has item NLL 1.182 against Foul Play's raw 64-draw belief at 1.800. That Δ of −0.618 [−0.682, −0.557] is the pre-registered §5 comparison, but it partly comes from `fp_belief` putting zero mass on the true item in 9% of rows. The strongest non-learned baselines are much closer:
  - the usage prior alone: 1.301 (Δ −0.119 for v1);
  - `fp_belief` mixed with 20% usage prior: 1.370 (Δ −0.188).
  - Item top-1: 0.601 (v1) vs 0.576 (usage) vs 0.562 (`fp_belief`).
  - No paired CI of v1 vs the usage prior has been computed yet. That is the next S-A item.
- **Smoke-adapter serving eval, now logged** (`hrsft-20261026-smoke01-dbe8`, `eval_serve_lora.json`): 200/200 legal, p50/p95 1.413/1.466 s, prompt tokens p50 1,147. It was marked "pending" above.
- **Glimmer v0 training-set sizes:**
  - v0-safe trained on a **20,000-row** file (18,001 action + 1,999 reads; sha `a43d83ee…`) and stopped at checkpoint-700.
  - v0-ddp and v0b trained on 55,556 rows (50,000 + 5,556; sha `9c3a6427…`).
  - All three evaluate on 400 rows from 23 validation games.
- **v0b** (`hrsft-20261026-v0b-2205`) ended Failed at its 3 h train cap: started 22:46 UTC, cap 01:46 UTC, checkpoints 1000/1200/1400. The SFT agent submitted the eval-only `hrsft-20261027-evv0b1400-af65` on checkpoint-1400. `hrsft-20261027-evddp1400-c5c9` is running.
- **Failure attribution** (`tools/analysis/attribute_failures.py`, new) on every chunk on the Mac at 01:54 UTC:
  - 14 undecided games among 8,015 game records (A/A, lever screens, VoI, label and hr_val farms).
  - All 14 are crashes of the challenging bot (side A): 12 × Foul Play `ValueError: Could not find keldeoresolute in team_dict` at turn 1, 2 × `ZeroDivisionError` in `select_move_from_mcts_results`.
  - A crash of the accepting bot (side B) already ends as a decided game: Showdown forfeits it (e.g. `lev-value-n1-c006` g0007). So both cases count as a loss for the crashing bot, as pre-registered.
  - No infrastructure failure was found.
- **Corrections to the 00:50 UTC entry:**
  - The double-half screen pooled the cancelled `hrlev-20261026-dbl-n1-9231` (2 chunks) with its replacement `…-dbl-n1-r9231`. Both use seed 810000, so the same 40 pairings were counted twice. Without them, and with the pre-registered failure rule: 48.75 ± 5.62 on 240 games at 01:54 UTC.
  - Label-farm volume: at 01:49 UTC the blob held 157 chunks (≈3,925 games). The Mac had 111 chunks = 2,775 games (2,774 extracted, 233,129 decisions ≈ 84 per game). The handoff's "276 chunks" counted files, not chunks.
- **Provisional at 01:54 UTC** (pre-registered failure rule; still running; not for decisions):

| Run | Estimate | Games |
|---|---|---|
| VoI p = 1 / 0.5 / 0.25 (oracle side) | 69.29 ± 3.58 / 62.68 ± 3.69 / 54.0 ± 3.76 (gate still passes) | 559 / 559 / 598 |
| Lever argmax / value | 49.64 ± 3.11 / 52.38 ± 3.02 | 840 / 840 |
| Lever student-blend λ 0.15 / 0.3 / 0.5 | 49.29 ± 7.33 / 44.00 ± 9.55 / 51.67 ± 8.71 | 140 / 100 / 120 |
| Lever belief resample λ 1 | 50.00 ± 9.28 | 80 |
| Early C1t1 A/A, seat A | 49.71 ± 3.46 (A3 rule: pass) | 680 |

## 2026-10-27 ≈02:00 UTC: VoI final (600 games per level) and official G-24h (fails on win rate)
- **Value of information, final.** `hrvoi-20261026-p{100,050,025}-a5fb` all Completed: 15 chunks × 40 games = 600 games per level, 300 side-swapped pairings on `hr_val`, C1t1, oracle seat alternating. Analysis: `tools/analysis/voi.py` (undecided games excluded) and `tools/analysis/attribute_failures.py --mode oracle` (pre-registered failure rule).

| Oracle p | voi.py (excl. undecided) | Failure rule applied |
|---|---|---|
| 1 | **68.67 ± 3.43** (598 games) | 68.33 ± 3.43 (600) |
| 0.5 | **62.50 ± 3.59** (598), lower bound 58.91 | 62.33 ± 3.58 (599; 1 game with both bots crashed, bounds 62.17–62.33) |
| 0.25 | 54.00 ± 3.76 (598) | 53.83 ± 3.75 (600) |

  - Paired differences (same 300 pairings): p 1 − 0.5 = 6.17 ± 4.54; 1 − 0.25 = 14.67 ± 4.96; 0.5 − 0.25 = 8.50 ± 5.20.
  - **Gate PASS (final):** p = 1 ≥ 58%; the p = 0.5 lower bound is 58.91 > 50.
  - Knowing the opponent's sets is worth ≈+19 points at C1t1. The provisional +21 at 519 games per level (00:50 entry) regressed toward this.
  - Crashes: 5 challenger-side crashes (Keldeo-Resolute ×3, ZeroDivisionError ×1, `Unknown terrain magicroom` ×1). One game had both bots crash on `Unknown terrain magicroom`.
- **Official G-24h** (`hrarena-20261027-g24h-v0safe-07d3`, Completed). v0-safe adapter (checkpoint-700, 20k-row training file) through llama.cpp + LoRA vs abyssal, 100 side-swapped pairings from `gen9/hr_hl_dev`, seed 2000, 4 games in parallel.
  - **Win 42/200 = 21.0%** (failures as losses; 42/199 = 21.1% of completed, Wilson [16.0, 27.3]).
  - Illegal 0 / 6,122 decisions; latency p50 / p95 3.12 / 3.49 s; 1 timeout.
  - **Gate FAILS on win rate** (needs ≥ 35%). It passes on games, legality and latency.
  - Better than the smoke adapter on the same teams (14.6%), still below base Glimmer with reasoning (31%, with 31% illegal moves).
  - **Per the plan:** run the arena on the better eval-only adapters before D1 22:00 Paris. `hrsft-20261027-evddp1400-c5c9` has teacher top-1 0.44 and `hrsft-20261027-evv0b1400-af65` 0.43, against 0.395 for v0-safe; both were still in serve_eval at 01:59 UTC. If none reaches 35%, the pre-decided fallback applies: the student pilots Spar, and Glimmer reads, explains and parses notes.

## 2026-10-27 ≈02:30 UTC: Glimmer v0 checkpoint-1400 evaluations (eval-only) and the v0-ddp resume
Jobs were submitted by the SFT agent from its uncommitted `azure/sft/*` fixes (train budget from `--job-timeout`, DDP rank exit, `--resume-from`, `--eval-only`; 11/11 CPU tests). Numbers are from each job's `eval_teacher.json` / `eval_serve_lora.json`, on the same 400 held-out rows (23 validation games) as v0-safe.

| Adapter | Teacher top-1 | Top-3 | CE vs search policy | Served legal (chat) | Agreement via llama.cpp | p50 / p95 |
|---|---|---|---|---|---|---|
| v0-ddp checkpoint-1400 (`hrsft-20261027-evddp1400-c5c9`) | **0.440** | 0.76 | 1.729 | 400/400 | 0.4325 | 1.415 / 1.485 s |
| v0b checkpoint-1400 (`hrsft-20261027-evv0b1400-af65`) | 0.430 | 0.76 | 1.722 | 400/400 | 0.435 | 1.417 / 1.486 s |
| v0-safe checkpoint-700 (for reference) | 0.395 | 0.72 | 1.79 | 400/400 | 0.395 | 1.41 / 1.49 s |

- v0-ddp vs v0b is a statistical tie at n = 400 (±≈2.5 points); both beat v0-safe.
- Adapter GGUFs: `pokemon-rl-research/hardread/<job>/adapter-bf16.gguf`.
- **Resume:** `hrsft-20261027-v0ddpres-fd53` (3 nodes; v0-ddp from checkpoint-1400 with the same 55,556 rows and loss; job timeout 14,400 s) started 02:13 UTC. Train budget to 05:23 UTC; the epoch should end ≈04:05–04:15 UTC, then eval.
- The G-24h retry (one run before 21:00 UTC) should use the best of these adapters.

## 2026-10-27 ≈05:10 UTC: broad human replay crawl (`tools/human/crawl_broad.py`)
- **Coverage:** 17,779 public gen9ou replays (Jul 1 – Oct 26). 15,612 new: 7,806 from ladder users 51–500 and 7,806 from the global search. Plus the 2,167 top-50 replays.
- **Parsed:** 17,676 games, 9,426 distinct players (pseudonymised; same salt as the top-50 set).
- **Games per band** (`rating_band`): <1300 3,738; 1300–1499 3,775; 1500–1699 6,074; 1700–1899 3,766; 1900+ 183; unrated 140.
- **Stopped early:** the replay host went down at 04:01 UTC (Cloudflare 522 for over an hour). The crawl is checkpointed; resume with `crawl_broad.py run --interval 2`.
- **Outputs:** `scratch_ds/human_broad/` (tables plus `summary_by_band.*`), `data/replays/gen9ou-broad/raw/` (manifest with per-shard SHA-256).

## 2026-10-27 ≈04:40 UTC: Glimmer v0-ddp resume, full epoch (`hrsft-20261027-v0ddpres-fd53`, 3-node DDP; S1.2)
- **Run:** resumed from checkpoint-1400; finished the epoch (2,315 steps, 55,556 rows: 50k action + 5.6k reads, weighted loss). All stages ok: train, eval_teacher, convert_lora_gguf, serve_eval. The job did not fail this time; the DDP-exit fix worked.
- **Teacher-forced, 400 held-out decisions:**
  - top-1 vs Foul Play **0.470**; top-2 0.66; top-3 0.8075;
  - top-1 vs search argmax 0.4725;
  - CE vs search policy 1.688.
  - For comparison: v0-ddp ckpt-1400 0.440, v0b ckpt-1400 0.430, v0-safe ckpt-700 0.395, base 0.2525.
- **Served (llama.cpp + LoRA, Q4_K_M base):** 400/400 legal (raw path 0 malformed); agreement 0.4775; p50 / p95 1.41 / 1.49 s.
- **Adapter:** `azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/hardread/hrsft-20261027-v0ddpres-fd53/adapter-bf16.gguf`, sha256 `4c0989e9191ebd75d37b6526d2e2eb2e63cfbdde995ad15c055a9a4fb6af5435`.
- **Use:** the adapter for the single G-24h retry (research/28 E1).

## 2026-10-27 ≈04:50 UTC: finalist and G2 rules (written BEFORE the 08:00 UTC lever-screen readout; research/28 S1.8 and §0.3 P3)
These rules are fixed now. The dev-A screens end at 08:00 UTC (argmax, value, double-half, student-blend) and 14:00 UTC (belief).
1. **F1 (finalist replication on dev A, fresh seeds 831000–846004):** `value` (HARDREAD_LEVER_SELECT=value). Information, not a gate.
2. **F2 (combo):** `value` + `double-half` **only if** double-half's final dev-A estimate is ≥ 51.0 with ≥ 400 games at its 08:00 UTC end; otherwise no combo.
3. **Excluded from finalists and G2:**
   - belief levers (their model is pre-trained on Metamon human replays with no stated data licence);
   - student-blend (a select lever, exclusive with `value`).
4. **G2 order on dev set B (`hr_val`):** the combo first only if its dev-A estimate ≥ F1's; otherwise `value` first. At most 2 candidates on dev B.
5. **G2 rules:** ≥ 1,000 games; markers valid; pre-registered win rate ≥ 52.0 → PASS. A G2 FAIL is not a blocker: the better candidate is frozen anyway, and the sealed run always runs.
6. **Timing:** G2 runs on D1 only if amendment A3a (draft: `handoff/drafts/A3a.md`, owner decision OD-2) is approved. Otherwise G2 runs on D4 as registered.

## 2026-10-27 ≈04:45 UTC: bridge module and validation (research/28 S1.5, E6) — PASS
- **Files:** `tools/review/bridge.py` (new; pure Python, one implementation for the product and its validation), `tools/review/validate_bridge.py` (moved from `tools/review/proposed/`, rewritten to the 27 §4.2 CLI: inputs, `--cal`, `--gate-file`, `--json`; exit 0 iff every acceptance row passes on the gate file), `tools/review/test_bridge.py` (new). `review.py` now adds `meta.end_reason`/`meta.end_turn` (from the log) and a `bridge` block (`bridge.from_review`) to its output; the UI reads `bridge.s/l/c` and `bridge.labels` from there.
- **Definitions:** S = cal(root A at team preview); L = Σ luck v2 `branch_cond`; R = 1/0 (ties: no bridge); C = R − S − L. Displayed s = round(100S), l = round(100L), c = 100R − s − l, with half-up rounding (= JavaScript `Math.round`; Python's banker's rounding would show 50 instead of 51 for S = 0.505). Diagnostic split C = Σact + drift + end. Forfeit/timeout tag "Ended by forfeit on turn N (your win chance then: X%)".
- **Test:** `python3 tools/review/test_bridge.py` 4/4 (identity to 1e-9 on 50 random sides of `paired_smogon` + `run520`, also cross-checked against `luck.turn_luck` under the Foul Play venv; integer sums on 40k synthetic cases; the `bridge` block of a fresh `review.py` output on farm game `lab-n4-c000_g0019` equals `bridge.from_review`; end tags).
- **E6** (`--cal absolute_human --gate-file paired_smogon`, bootstrap 1,000 by game, seed 7) → `scratch_ds/review/bridge_validation.json` `"pass": true`, exit 0:

| Row (gate: Draft B `paired_smogon`, 292 sides / 146 paired games) | Value | Rule | Draft A `run520` (1,028 / 512) | Draft A `paired_default` (291 / 145) |
|---|---|---|---|---|
| V1 max \|S + L + C − R\| | 0.0 | ≤ 1e-9 ✔ | 1.1e-16 | 1.1e-16 |
| V1 integers add up; max \|c − round(100C)\| | yes; 1 | exact; ≤ 1 ✔ | yes; 1 | yes; 1 |
| V2 share \|C\| > 1 | 0.68% | ≤ 2% ✔ | 1.17% | 1.72% |
| V3 mean L winners − losers [95% CI] | 0.173 [0.121, 0.229] | low > 0 ✔ | [0.144, 0.205] | [0.094, 0.191] |
| V3 P(L > 0 \| win) / P(L > 0 \| loss) | 0.733 / 0.336 | ≥ 0.65 / ≤ 0.50 ✔ | 0.825 / 0.454 | 0.795 / 0.407 |
| V4 mean L (bias) [95% CI] | +0.0136 [−0.002, 0.034] | \|·\| ≤ 0.03 ✔ | **+0.080 ✘** | **+0.089 ✘** |
| V5 corr(L_p1, L_p2) | −0.526 | ≤ −0.40 ✔ | −0.442 | **−0.172 ✘** |
| V5 corr(C_p1, C_p2) | −0.888 | ≤ −0.80 ✔ | −0.864 | −0.820 |
| V6 corr(Σact, C) | 0.717 | ≥ 0.50 ✔ | 0.605 | 0.588 |

- **Informational (not gated):** S + L outside [0, 1] on 4.1% of Draft-B sides (so the UI uses signed bars, never a running total); "swingy" flag (|L| > 0.5 or |C| > 1) on 3.8%; variance shares of C: act 0.63, drift 0.37, end 0.11; mean \|end\| KO 0.023 / forfeit 0.123 / timeout 0.261. **Mean \|S_p1 + S_p2 − 1\| is 0.066 on Draft B** (0.046 on Draft A `run520`): research/27 §4.2 listed ≤ 0.06 measured on Draft A only; research/28 S1.5 does not gate it. Reported, not hidden.
- **Reading:** the bridge passes on the shipped Draft-B path; Draft A fails the bias row (as expected) and is never shipped. These numbers reproduce the proposer's prototype exactly. Rerun on D2 with `run_b.jsonl` and `absolute_human_b` (S2.2).

## 2026-10-27 ≈05:10 UTC: verdict v1 code, VT3, and a 13-case depth pilot (NOT VT1)
- **Code** (commit `6f7eee8`): `tools/review/verdict.py` per research/28 §0.2: signed paired contrast Δ = WP(X) − WP(chosen) with X = the review's engine-best; seeds 11/12/13, each one base sample of 4 worlds shared by U and R (common random numbers on worlds only; poke-engine's MCTS is OS-seeded, so runs are independent); 20k iterations per world; K BAD = mean Δ ≥ 0.05 and all 3 > 0; K OK = mean ≤ 0.02; Misplay / Misread / Defensible / Within noise; a contradicted read is reported ("Read contradicted on turn N") and dropped; reference = revealed later (from Foul Play's end-of-log state) else the engine's estimate; results cached. `tools/review/select_offered.py` = the product's disagreement rule (WP ∈ [0.20, 0.80], cost ≥ 0.05, known action, ≤ 3 per side) + VT1 population builder. `human_eval.py run` gained `--games/--band` (E9).
- **VT3** (`tools/review/test_verdict.py`, Foul Play venv): **10/10 pass** (rule table incl. sign agreement and missing options; Wilson bounds; partial belief keeps sampled fields; a revealed move is never removed; moves_has replaces an unrevealed move; moves_not removes and refills; item contradiction dated "turn 10" from the Life Orb line; tera after Terastallizing; consumed item not set; revealed-later item = the item held at the decision even when tricked away later).
- **Selection rate check** (`select_offered.py` on `scratch_ds/review/human/farm_noise.jsonl`, 20 bot game-sides): 13 offered of 725 balanced decisions = **1.79%** (research/28 §0.2 V3 had 1.8%).
- **Depth pilot on those 13 offered bot decisions** (U = R = the engine's estimate, chosen = the bot's C1t1 move; n = 13, data the judge already used to set the thresholds; **not VT1**, information for the D2 fallback order):

| Verdict search | Misplay + Misread at m = 0.05 | at m = 0.08 | mean Δ_U | median s per verdict (3 workers) |
|---|---|---|---|---|
| 4 × 20k (plan default) | **11 / 13** | 6 / 13 | +0.069 | 2.1 |
| 4 × 60k | 4 / 13 | 0 / 13 | +0.040 | 9.4 |
| 4 × 200k | 3 / 13 | 0 / 13 | +0.023 | 19.1 |

- **Reading (provisional, n = 13):** the 3 fresh seeds reproduce the 20k review search's own preference almost exactly (Δ per seed within ≈ ±0.01 of the selection cost), so at 20k the false-decisive rate on the offered population is likely far above VT1's 10%. This is the "correlated disagreement" risk of §0.2 V2: the bias is the search depth (C1t1 ≈ 60× more iterations per world than 20k), not seed noise. Deeper verdict searches shrink it. VT1 proper (≥ 100 fresh label-farm decisions, S1.21 + E7 step 2) runs tonight with the registered default (20k, m = 0.05) and the fallback chain (m = 0.08 → 60k → "leans" wording) as written. Search cost: 4 × 20k = 0.13 s and 4 × 60k = 0.47 s per condition on the M4 Max, so 60k–200k verdicts fit the 45 s p95 target with parallel conditions.

## 2026-10-27 ≈05:45 UTC: check of the app's test verdict "Misplay" on a Crunch that KO'd (replay gen9ou-2703487331, p1, turn 17) — cause: the drafted user set
- **Case:** Ag-App ran `verdict.py one` with a Draft-B team standing in for an export (test shortcut; the app now requires an export). Zamazenta (88% HP, −1 Speed from Sticky Web) vs Ceruledge (+2 Atk, 66% = 230/349 HP); stated read: Ceruledge Heavy-Duty Boots + Bitter Blade; X = switch Dragonite. In the replay, Crunch KO'd. The app got Misplay (Δ_U +0.157/+0.111/+0.137).
- **The KO is in the search:** poke-engine's damage for Crunch against the sampled Ceruledge (same worlds, `calculate_damage`) is **192–288** with the drafted Zamazenta (Atk stat 276) vs 230 HP left: a KO on part of the rolls only, so the search values Crunch below switching to Dragonite.
- **The drafted team is the cause:** Draft B filled Zamazenta's unrevealed fields with the most common Smogon set that fits Close Combat + Crunch, the bulky Body Press set (**252 HP / 0 Atk / 88 Def / 168 Spe**, Jolly, Iron Defense, Body Press). The same decision with only the spread changed to 0 HP / 252 Atk / 4 Def / 252 Spe (Atk 339): Crunch does **234–354** (a KO on every roll) and the verdict becomes **Defensible**, Δ_U = **−0.016 / −0.011 / −0.014** (crunch is the engine's top option in all 3 runs). With the draft, the same diagnostic run (other worlds) gave Misplay, Δ_U +0.087 / +0.085 / +0.056.
- **Conclusion:** no verdict bug; the verdict is only as good as the user's stats. This confirms the plan's rule that verdicts require the export (now enforced by the app). Note for the review in Draft mode: the "assumed option" filter (27 §4.3) covers unrevealed moves/items, not spreads; a drafted spread can move KO thresholds, so a Draft-mode disagreement involving a KO-range attack can be an artefact. Suggested copy in Draft mode: "your sets are assumed (Smogon's most common set)". Possible later fix (not planned): choose the draft spread consistent with the damage the user's own attacks dealt in the log.

## 2026-10-27 ≈08:25 UTC: lever screens final (08:00 deadline), early C1t1 A/A final, finalist rule applied, F1 submitted (research/28 S1.10–S1.11)
- **Harvest:** every 08:00-deadline `hr*` job is terminal (label farm ×6, hr_val ×2, argmax/value ×2 each, dbl-n1r, sb015/03/05, early A/A ×3; the last, `hrlab-…-n3`, ended ≈08:20). Every blob chunk of the screens and the A/A is local (blob = local: argmax 44 + 42, value 44 + 41, dbl-n1r 33 (+ dbl-n2 1, a valid seed-820000 chunk of the cancelled job), sb 22 each, A/A 28 + 32 + 32). Belief screens (`bel*`) run to 14:00 UTC (S1.18).
- **Final lever table** (dev set A = `hr_train`, C1t1 both sides, treatment seat alternating, pre-registered failure rule via `tools/analysis/attribute_failures.py --mode treat --group-by-prefix`; `lev-dbl-n1-c*` excluded as duplicate seeds; JSON `scratch_ds/lev_final/screens_0800_final.json`, failure map `screens_0800_failures.json`; 0 infra reruns):

| Lever (fork commit) | Win rate ± 95% CI [lo, hi] | p | Games / pairings | Undecided → label | Screen rule |
|---|---|---|---|---|---|
| **value** (`df65fd8`) | **54.18 ± 2.17 [52.01, 56.34]** | **0.0002** | 1,699 / 850 | 4 vanilla crashes, 1 treatment crash, 1 unknown (hang; bounds 54.12–54.18) | **CANDIDATE** |
| argmax (`df65fd8`) | 50.17 ± 2.12 [48.06, 52.29] | 0.87 | 1,720 / 860 | 3 vanilla crashes | undecided |
| double-half (`fb80f6f`) | 48.09 ± 3.32 [44.77, 51.41] | 0.26 | 680 / 340 | 1 vanilla crash | DROP |
| student-blend λ 0.15 (`363684f`) | 53.18 ± 4.21 [48.97, 57.40] | 0.14 | 440 / 220 | 1 vanilla crash | candidate by the screen rule; **excluded** (pre-registered rule) |
| student-blend λ 0.3 | 50.68 ± 4.30 [46.38, 54.99] | 0.76 | 440 / 220 | 1 vanilla crash | undecided; excluded |
| student-blend λ 0.5 | 46.59 ± 4.33 [42.26, 50.92] | 0.12 | 440 / 220 | 1 vanilla crash | DROP; excluded |

  - Crashes: vanilla = Foul Play's `keldeoresolute` team_dict bug (shared pairings, so it hits every lever at the same games) and one `ZeroDivisionError`; the one treatment crash is new: `AttributeError: 'NoneType' object has no attribute 'can_z'` (`lev-value-n2-c031` g0008). Counted as losses for the crashing side, as pre-registered.
  - **Reading:** `value` ended at +4.2 points (p = 0.0002, 1,699 games) after being +3.0 at 1,139 games. About 9 levers were screened, so expect regression to the mean; F1 (fresh seeds) measures it.
- **Early C1t1 A/A (exploratory, `hraa-20261026-c1t1-n{1,2,3}-d106`, `--mode seatA`, `scratch_ds/lev_final/early_aa_final.json`):** seat A **50.16 ± 2.05 [48.11, 52.22]**, p = 0.88, 1,838 games / 920 pairings. Labels: 4 seat-A crashes (Keldeo-Resolute) and 2 unknown hangs (bounds 50.05–50.16). **Passes the A3 rule** (|0.16| ≤ 2.2 and p ≥ 0.05). No seat bias seen.
- **Finalist rules (fixed at ≈04:37 UTC, before this readout) applied:**
  - F1 = `value`.
  - **F2: none.** Double-half's final is 48.09 with 680 games, below the 51.0 threshold.
  - Belief and student-blend levers are excluded.
  - Under track REG (A3a not approved), G2 on dev set B tests `value` alone on D4.
- **F1 submitted (E5, track REG):** `hrlev-20261027-f1value-n1…n15-c9d0`, submitted 08:22–08:24 UTC. 15 jobs × 5 chunks × 10 side-swapped pairings = 1,500 games. Seed bases 830000 + 1000·j, j = 1–15 (chunks 831000–845004; research/28's "846004" is a typo for the formula's 845004). `--treat HARDREAD_LEVER_SELECT=value --treat-seat alternate`, `hr_train`, C1t1, timeout 1,800 s per game. Fork `c04b38e` (adds the belief lever, off by default) and HardRead `5f9a02f`. At submission, hr* Running + Queued = 18 ≤ 25; the cluster had 10 idle nodes of 13.

## 2026-10-27 ≈08:30 UTC: G-24h retry, the one allowed (`hrarena-20261027-g24h-v0ddpres-9599`; S1.4): **PASS**
- **Setup:** Glimmer v0-ddp full-epoch adapter (teacher top-1 0.470) through llama.cpp LoRA (Q4_K_M base) vs PokéChamp abyssal; 100 side-swapped pairings, seed 2000 (the same pairings as the failed first run); 4 games in parallel.
- **Result:** 199/200 completed (1 failure, counted as a loss).
  - **Win rate 88/199 = 44.2%, 95% CI [37.5, 51.2]; 44.0% with failures as losses.**
  - Illegal 0 / 6,494 decisions. Latency p50 / p95 3.13 / 3.52 s.
- **Gate g24h** (games ≥ 200, win ≥ 35%, illegal ≤ 2%, p95 ≤ 5 s): **pass = true**.
- **Comparison on the same pairings:** v0-safe ckpt-700 21.0%; smoke adapter 14.6%; base Glimmer with reasoning 31% (31% illegal); the 25M student alone 80% (40 games, v0).
- **G1** (≥ 60% over 400 games) is **not attempted**. The pre-registered rule runs G1 only if this retry reaches ≥ 45%, and 44.0% is below that.
- **Fine-tune sentence for the video (research/28 §4.7):** "our full-epoch fine-tune raised Glimmer's win rate against a heuristic bot from 21% to 44%, with no illegal moves, but it does not play as strongly as the small student network or the search engine".

## 2026-10-27 ≈09:15 UTC: F1 first-chunk acceptance (research/28 S1.11) — PASS
- All 15 `hrlev-20261027-f1value-*-c9d0` jobs are Running; each has its first chunk in blob (all c000 fetched to `scratch_ds/lev/`).
- First chunks: **299 / 300 games decided (99.7%)**. The undecided game is a vanilla crash: a poke-engine Rust panic surfacing as `PicklingError` (`lev-f1value-n10-c000` g0006).
- `attribute_failures.py --check-treat-markers` on the 15 first chunks: **valid: true**. Treatment 299/299 bot logs have lever markers (12,408 lines); vanilla 0 marker lines; 0 fallback warnings on either side.
- No win rate is reported before completion (information run; it is read at the end, ≥ 1,500 games).
- The `azure/fetch.sh` refactored at `5e37173` (identifiers from `handoff/env.sh`) listed and downloaded these chunks without error.

## 2026-10-27 ≈10:06 UTC: track A3a — G2 and A/A 1 wave 1 submitted (research/28 S1.11 [A3a], E2–E3)
- **Authority:** OD-2 (A3a, both parts) approved by the owner and committed to `PREREGISTRATION.md` (commit `0f03c12`, 09:59:50 UTC), as confirmed by Coord. The first G2/A/A job was created at 10:03:49 UTC, after that commit. No G2, pre-registered A/A or sealed game existed before it.
- **G2 (E2):** `hrg2-20261027-value-j00…j09-d241` (10 jobs), created 10:03–10:05 UTC.
  - `--team-pool gen9/hr_val` (dev set B), C1t1 both sides, `--treat HARDREAD_LEVER_SELECT=value --treat-seat alternate`.
  - 5 chunks × 10 side-swapped pairings per job = 1,000 games; seed bases 4,000,000 + 10,000·j.
  - Only candidate: `value` (A3a item 3).
  - Read only when all 50 chunks are in. Pass = ≥ 1,000 games, markers valid, `pre_registered` win rate ≥ 52.0.
- **A/A 1 (E3):** `hraa1-20261027-j00…j08-c0d6` (9 of 20 jobs), created 10:05–10:06 UTC.
  - Vanilla vs vanilla, no `--treat`, `hr_train`, seed bases 2,000,000 + 10,000·j.
  - Jobs j09–j19 follow in waves (the same hex `c0d6`, the same layout).
- **A/A 2 (E4):** follows in waves: `--treat HARDREAD_LEVER_SELECT=fp` (A3a item 2), seed bases 3,000,000 + 10,000·j.
- **Capacity:** hr* Running + Queued = 25 after this wave (F1 3 + belief 3 + 19 new). The cluster had 12 idle nodes of 18 at 10:02.
- **Infrastructure:** these are the first submissions through the refactored `azure/submit.sh` (`5e37173`, identifiers from `handoff/env.sh`). All 19 job creates returned "Starting", and the generated `job.yml` resolves compute and environment correctly.

## 2026-10-27 ≈10:15 UTC: F1 final — `value` does NOT replicate on fresh dev-A seeds (information run, research/28 E5)
- **Run:** `hrlev-20261027-f1value-n1…n15-c9d0`, all 15 Completed at 10:07 UTC; 75 chunks / 1,500 games / 750 pairings, all fetched; `rerun_chunks` empty, no chunk gaps. JSON: `scratch_ds/lev_final/f1_value_final.json`; failure map: `f1_value_failures.json`.
- **Markers:** valid. Treatment 1,494/1,496 logs carry lever lines (the 2 without are turn-1 crashes, within the 1% allowance); vanilla 0; fallbacks 0.
- **Result (pre-registered failure rule):** treatment (`value`) win rate **48.73 ± 2.17 [46.57, 50.90], p = 0.25**, 1,500 games. Undecided: 4 vanilla crashes (2 Keldeo-Resolute, 1 ZeroDivisionError, 1 Rust panic), counted as treatment wins.
- **Against the screen** (54.18 ± 2.17 on 1,699 games, seeds 810000/820000): difference −5.45 ± 3.07 points (z ≈ −3.5).
  - Selection among ≈ 9 screened levers predicts some shrinkage, but probably not this much [J].
  - No sign of within-run heterogeneity: screen nodes 52.7% / 55.6%; F1 jobs 41–56% around 48.6%, chunk SD at the binomial level.
- **Code check:** the screen ran fork `df65fd8`, F1 `c04b38e`. The diff only adds the student-blend and belief levers (both off unless their env vars are set) and passes `battle` to `select_move`. The `value` branch is unchanged, the team-pool tarball is byte-identical (md5 `ca3f536e…`), and `run_match.py` / `farm_chunks.sh` are identical. The node setup adds a numpy install to the bot venv, which applies to both sides. No mechanical cause found.
- **Reading:** the screen's +4 points was most likely a lucky draw plus selection. On the 3,199 dev-A games (screen + F1) the evidence for `value` is weak, and F1 alone is consistent with no effect. G2 (dev set B, running) remains the pre-registered gate; `value` is the only candidate and is frozen whatever G2 gives (the sealed run always runs). P(Ending A) should be revised down [J; Coord/judge]. Optional diagnostic (not planned, needs a decision): an argmax or vanilla-vs-vanilla run on the F1 seeds, to check whether those pairings differ.

## 2026-10-27 ≈10:25 UTC: addendum to "F1 final" — screen vs F1 gap, load diagnosis (no mechanical cause found); Ending-A estimate [J]
Diagnosis only, at Coord's request. No candidate or registered rule changes. Script: an inline parse of every bot log in the chunks; the mean is taken over game-sides.

| Run (chunks) | Treatment MCTS iterations per world per decision | Vanilla iterations per world | Treatment win % (decided games) |
|---|---|---|---|
| Screen `value`, all (85) | 1,183,763 | 1,190,616 | 54.1 (1,694) |
| Screen `value`, chunks c < 20 / c ≥ 20 | 1,183,258 / 1,184,213 | 1,193,565 / 1,187,992 | 52.6 (799) / 55.4 (895) |
| Screen argmax, all (86) | 1,179,480 | 1,180,912 | 50.1 (1,717) |
| Screen argmax, c < 20 / c ≥ 20 | 1,185,557 / 1,174,191 | 1,191,972 / 1,171,286 | 49.2 / 50.9 |
| Screen double-half, all | 647,197 (2× worlds at half time, as designed) | 1,171,642 | 47.8 (659) |
| Screen student-blend λ 0.15, all | 921,068 | 882,721 | 53.1 (439) |
| **F1 `value`, all (75)** | **1,185,574** | **1,195,214** | 48.6 (1,496) |
| F1 `value`, c < 3 / c ≥ 3 | 1,195,218 / 1,171,060 | 1,209,634 / 1,173,512 | 49.9 (899) / 46.6 (597) |

- **Node load / search depth:** under the 2,000 ms C1t1 budget, the screen and F1 searched the same depth on both sides (≈1.18–1.20 M iterations per world; treatment vs vanilla within 1%). There is no sign of CPU contention in either run.
  - The student-blend jobs ran ≈25% fewer iterations on both sides, and their win rates are excluded anyway.
  - Double-half's treatment shows 0.65 M per world: twice the worlds at half the time, as designed.
- **Early vs late chunks:**
  - Screen `value`: 52.6 → 55.4.
  - Argmax (same pairings): 49.2 → 50.9.
  - F1: 49.9 → 46.6.
  - No common time trend, and nothing that would point to a drift in the harness.
- **Crash rates** (vanilla crashes counted as treatment wins):
  - screen `value`: 4 vanilla + 1 treatment per 1,699 games;
  - screen argmax: 3 vanilla per 1,720;
  - F1: 4 vanilla per 1,500.
  - Rates are similar; the effect on the estimates is ≤ 0.3 points.
- **Code and data:** as in the F1 entry. The `value` code path is unchanged, the team pools are byte-identical, and `run_match.py`/`farm_chunks.sh` are identical.
- **Conclusion:** no mechanical cause found. The z ≈ −3.5 gap is best read as a lucky screen (selected as the best of ≈ 9 levers) plus noise. That is a 1-in-several-hundred event under pure noise [J], so it stays flagged. The A/As are the harness check.
- **Ending-A estimate [J]: ≈ 15% (range 5–25%).**
  - Shrinkage model: the screen is discounted by the expected maximum of ≈ 9 noise draws (≈ 1.7 points); F1 is taken at face value (−1.27 ± 1.11 SE). Prior on true lever effects N(0, τ²) with τ 0.7–1.0. Posterior for `value` ≈ +0.3 ± 0.6 points.
  - Sealed SE ≈ 0.45, so significance needs an estimate ≥ ≈ +0.9: P ≈ 0.19–0.25.
  - F1 alone (no credit for the screen) gives ≈ 5%.
  - G2 (1,000 games on dev B, running) will move this a lot.

## 2026-10-27 ≈10:50 UTC: held-out leak check — `hr_hl_dev` ∩ `hr_val` confirmed (228 teams), no impact on any held-out claim; sealed set clean
Diagnosis at Coord's request (Ag-Play's report). Method: overlap by file name, by packed-team SHA-256 and by roster (sorted 6-species set). The inputs were only names, species lists and hashes: `data/teams/validated/hl_05_26-gen9ou.jsonl`, the split v1 file, the pools tarball's member names, and the sealed manifest's names. No sealed or reserved team file was opened.

| Pair | Same file | Same packed team | Same roster |
|---|---|---|---|
| `hr_hl_dev` (2,000 teams / 1,154 rosters) ∩ `hr_val` (2,778 / 596) | **228** | 228 | **131** |
| `hr_hl_dev` ∩ `hr_train` (26,850 / 5,369) | 1,772 | 1,772 | 1,023 |
| `hr_hl_dev` ∩ reserved (14,136 / 2,556) | 0 | 0 | 0 |
| `hr_hl_dev` ∩ `hr_sealed` (1,000 / 1,000) | 0 | 0 | 0 |
| `hr_train` ∩ `hr_val` | 0 | 0 | 0 |
| `hr_train` or `hr_val` ∩ reserved / `hr_sealed` | 0 | 0 | 0 |

- **Cause:** `hr_hl_dev` was drawn before kickoff (fork `d37606b`, Oct 26) from the non-reserved (dev) rosters. `hr_val` was split later from the same dev rosters (`pools-v1.json`, seed 20261027), so `hr_hl_dev` spans both `hr_train` and `hr_val`. It never touched the reserved rosters: the split v1 held them out first.
- **Runs that used `hr_hl_dev`** (from every `azure/attempts/*/job.yml`):
  - the exploratory F1 A/A `hraa-20261026-310d28`;
  - the arena runs (rehearsal `g24hpre`, official G-24h, the G-24h retry, the student-pilot arena, `hrarena-20261026-00cc`).
  - None is a training run.
- **Training sets, checked:** no training set contains a `hr_hl_dev` game.
  - Label farm `hrlab-*` and volume farm `hrvol-*`: `--team-pool gen9/hr_train`.
  - Glimmer v0 SFT: 1,350 label-farm games, all `hr_train`, checked per game.
  - Student v1 (`hrstu-20261026-v1-8187`): lab + vol inputs (both `hr_train`); `hr_val` is cross-pool test only.
  - Belief v0/v1: vol/lab chunks (`hr_train`) plus Metamon human data under the A2 roster filter. `hr_val` (`hrval-*`) is used only as a test set.
  - The only `hr_hl_dev`-derived dataset found is `scratch_ds/sample*` (12 games), which fed only early belief smoke models (`sample_v0`), not v0/v1.
- **Impact:**
  - **S-A** (pre-registered §5, "held-out rosters"): **no leak**. The models never saw an `hr_val` roster (`hr_train` ∩ `hr_val` = 0 by roster), and the claim stands.
  - **Student 55.3% on unseen rosters:** **no leak**, same reason.
  - **G2:** **no impact**. `value` has no learned part, and no lever parameter was tuned on `hr_hl_dev`: the levers were added post-kickoff (fork `df65fd8`) and screened on `hr_train` only.
  - **Sealed set:** **clean**, disjoint from every pool by file, hash and roster.
  - **Caveat for the arena numbers** (G-24h 21.0% / retry 44.0%, student 80%): they use 100 pairings = 195 distinct `hr_hl_dev` teams.
    - 171 of those teams are `hr_train` teams and 24 are `hr_val` teams.
    - 18 of the 195 appear in Glimmer v0's SFT training games, and 71 somewhere in the label farm.
    - Say "dev teams (partly seen in training)", never "unseen teams". The claims are about strength vs abyssal, not team generalisation.
- **Housekeeping (no rerun needed):** `hr_hl_dev` should not be used for any future held-out claim. The 228 overlapping teams could be dropped from `hr_hl_dev`, or the pool documented as "dev, overlaps hr_train/hr_val". That is Coord's call.

## 2026-10-27 ≈10:50 UTC: track A3a first-chunk acceptance (research/28 S1.11) — G2 PASS, A/A 1 PASS (no win rates computed)
- **G2** `hrg2-20261027-value-j00…j09-d241`:
  - 10/10 first chunks; **200/200 decided**; `treat_side` recorded for all 200.
  - `--check-treat-markers` **valid: true**: treatment 200/200 logs with markers (7,739 lines), vanilla 0, fallbacks 0.
- **A/A 1** `hraa1-20261027-j00…j08-c0d6`:
  - 9/9 first chunks; **179/180 decided (99.4%)**; no `--treat` (no `treat_side`), as specified.
- No estimate was computed for G2 or the A/As. The checking script printed only counters.

## 2026-10-27 ≈11:45 UTC: G2 on dev set B — `value` FAILS the G2 rule (research/28 S1.25, E2; pre-registered A1/A3a)
- **Run:** `hrg2-20261027-value-j00…j09-d241`, all 10 Completed. 50/50 chunks fetched to `scratch_ds/g2/`; `rerun_chunks` empty, no chunk gaps.
  - `hr_val` teams, C1t1 both sides, `--treat HARDREAD_LEVER_SELECT=value --treat-seat alternate`, seed bases 4,000,000 + 10,000·j.
  - Fork `c04b38e`.
  - Output: `scratch_ds/g2/result_value.json`, `scratch_ds/g2/failures_value.json`.
- **Markers:** `--check-treat-markers` **valid: true**. Treatment 999/999 logs with markers (38,105 lines), vanilla 0, fallbacks 0.
- **Result** (`pre_registered`, crash = loss for the crashing bot): **48.40 ± 2.71 [45.69, 51.11], z = −1.16, p = 0.25**, 1,000 games / 500 pairings. One undecided game: a treatment crash, counted as a loss.
- **Verdict:** **G2 FAIL** (needs ≥ 52.0 with ≥ 1,000 games and valid markers). There is no second candidate: double-half did not qualify (A3a item 3).
- **Per the pre-registered rules:** `value` is still the freeze configuration (a G2 FAIL is not a blocker), and the sealed run always runs.
- **Reading:** dev B agrees with F1 (48.73 on fresh dev-A seeds). The screen's 54.18 was very likely a selection plus noise artefact. Ending A now looks unlikely: [J] ≈ 5–8%, down from ≈ 15%. Ending B (S-A and VoI) carries the science beat.

## 2026-10-27 ≈11:45 UTC: S-A refresh (research/28 S1.10) — primary = the 440-game run; secondary = all 1,579 `hr_val` games, retrained models
- **Primary (pre-registered comparison, first run).** Frozen models and data (`scratch_ds/belief/{v1,v1data,results_v1}_frozen_20261027`, npz sha `b66252ba…` / `1080a8af…`); 440 games, 33,253 decisions, 146,225 rows.
  - v1 (human pre-train → farm fine-tune), item NLL 1.182 [1.141, 1.223]:
    - vs Foul Play's belief: Δ −0.618 [−0.682, −0.557] (logged at ≈00:00 UTC);
    - **vs the usage prior: Δ −0.118 [−0.136, −0.102]** (new; `results_v1_frozen_20261027/sa_hrval440_vs_usage.{json,md}`).
  - v1 + fp_belief input: Δ −0.129 [−0.145, −0.113] vs usage.
  - Item top-1 vs usage: v1 +0.024 [+0.011, +0.038].
- **Secondary (enlarged test set, chosen after seeing the first result: a forking path, so secondary only).** `FETCH=1 bash tools/belief/run_v1.sh` at 08:25–11:45 UTC on all fetched chunks (label farm 264 chunks = 6,597 games; `hr_val` 79 chunks = 1,579 games; 124,611 decisions; 555,025 rows; 494 rosters seen). Output: `scratch_ds/belief/results_v1/sa_hrval.{json,md}`.
  - Foul Play's belief 1.809; usage 1.303; v1 (human pre-train → farm fine-tune) **1.190 [1.165, 1.214]**, Δ vs fp_belief **−0.619 [−0.656, −0.583]**; v1 + fp_belief input 1.176, Δ −0.633.
  - **The ordering and effect sizes match the primary.**
  - A paired CI vs usage on the enlarged set is not computed yet (one benchmark call with `--ref usage`, as for the primary).
- **Leak check** (entry ≈10:50 UTC): the `hr_val` test rosters never appear in any training set.
- The `sa_human` benchmark (held-out human games) was still running at the handover; see PROGRESS.

## 2026-10-27 ≈11:45 UTC: S-A held-out human benchmark (secondary)

- **Status:** Secondary held-out human evidence only; it is not the primary or pre-registered S-A test and was not used for finalist selection.
- **Population and denominator:** 2026 human games, rating ≥1500, revealed labels only: 15,674 games, 81,008 decisions, 88,087 decision × alive-opponent-Pokémon examples, 3,494 rosters. Item NLL is scored on 22,026 item-labeled rows (ε = 1e-4); 95% CIs use 2,000 game bootstrap resamples.
- **Usage prior:** item NLL 1.390 [1.370, 1.409] on 22,026 rows.
- **v1 human pre-train → farm fine-tune:** item NLL 1.175 [1.155, 1.193] on the same rows; paired Δ vs usage −0.216 [−0.224, −0.207].
- **p-value:** Not computed or reported; the benchmark artifact reports bootstrap CIs.
- This is separate from the primary 440-game `hr_val` result and the enlarged 1,579-game `hr_val` secondary above; it does not replace either result or alter the frozen `value` finalist.
- **Provenance:** The local refresh completed at 11:45 UTC (`FETCH=1 bash tools/belief/run_v1.sh`, exit 0; `scratch_ds/belief/logs/run_v1_refresh_20261027.log`). The benchmark uses `scratch_ds/belief/human/human.test1.jsonl.gz`, `scratch_ds/belief/human/h.usage.jsonl.gz`, and `scratch_ds/belief/v1/pred_f1.0_farmhuman_ft_human.jsonl.gz`; the model checkpoint is `scratch_ds/belief/v1/model_f1.0_farmhuman_ft.pt`. The tracked training, prep, and benchmark scripts were last changed in commit `e8465e1c0b997f0559d98fccc1d11655719cb309`. The run log does not record the full Git HEAD at invocation; local scratch data, checkpoint, and prediction artifacts have no Git commit. Their SHA-256 values and the source comparison are saved under `scratch_ds/codex/c2-human/`. No Azure benchmark job was used.
- **Result sources:** `scratch_ds/belief/results_v1/sa_human.md` and `scratch_ds/belief/results_v1/sa_human.json`. The Markdown and JSON headline values and denominators were mechanically reconciled; raw source excerpt and comparison output are in `scratch_ds/codex/c2-human/`.

## 2026-10-27 13:14:51 UTC: A/A 2 first-chunk integrity check (partial, counters only)

- **Scope:** first `c000` archives for `hraa2-20261027-j00-7073` through `hraa2-20261027-j06-7073`; seven chunks, **140 game records / 140 decided**. This is an infrastructure/validity check, not a result readout. No win rate, CI or p-value was computed.
- **Registered A3a item 2 counters:** every chunk has 20/20 decided records, 20/20 decided `treat_side` values, zero missing decided bot logs, and treatment/vanilla `marker_lines = 0` and `fallback_warnings = 0`. Generic positive-marker `valid:false` is expected for neutral `fp` and is not the A/A 2 gate. **Technical PASS for these seven first chunks only.**
- **Completeness and timing:** 13:05:50–13:07:17 UTC inventory listed 19/20 exact expected jobs (4 Completed, 15 Running; j19 not listed). At 13:07:39–13:08:06, j07–j18 had no `c000` blob. Actual Azure start times were null, so each start-to-first-blob timing verdict is **UNKNOWN**. Creation-to-blob intervals of 17.01–21.95 minutes are informational bounds, not credited as timing PASS. No missing chunk was excluded or resubmitted.
- **Provenance / checks:** reviewed helper commit `773a5da`; raw per-chunk JSON, archive SHA-256/byte verification, sanitized blob/job timestamps and timing evidence are under `scratch_ds/codex/c1/first-chunks/`. Root independently reran the counter path with `h2h.analyse` patched to raise: **0 estimator calls**, identical seven technical passes (`orchestrator-first-chunk-check.json`). Fresh Sol evidence review: `scratch_ds/codex/c1/sol-review-first-chunks.md`, ACCEPT seven technical checks and existing G2 record only. No Azure jobs submitted/canceled/paused.
- **Limit:** whole C1 is **PARTIAL** (13 unchecked first chunks, timing unknown). Separate non-secret-identifier setup output exceptions are recorded in PROGRESS/audit; strict operational DoD-4 is not claimed met. Both A/A result readouts remain behind their complete-family inventory gates.

## 2026-10-27 13:47:24 UTC: pre-registered A/A 1 — PASS (A3; complete-family readout)

- **Jobs/design:** `hraa1-20261027-j00-c0d6` through `hraa1-20261027-j19-c0d6`, C1t1 vanilla on both sides, **no --treat** in all 20 actual job commands. Before any estimate, root independently verified all 20 exact jobs Completed, all 100 exact archives (5/job), 20 source records/chunk, indexes 0–19, exact registered seeds, and **1,000 designed pairings / 2,000 source records**. The two missing-list/status cases j13/j17 were reconciled by exact-name queries; both final chunks arrived before analysis. Archive SHA-256 and command SHA-256 manifests are saved under `scratch_ds/codex/c5/`.
- **Primary A3 result:** **49.65% ± 2.03 percentage points**, 95% CI **[47.62, 51.68]**, two-sided **p = 0.7349**, z = −0.34; **1,000 analyzed pairings / 1,999 analyzed games**. PASS: point estimate is within [47.8,52.2] and p ≥ .05. Under A3, the point estimate, rather than the entire CI, must lie inside the band. This is a harness check of identical configurations, not evidence that HardRead outperforms or is equivalent to Foul Play.
- **Denominator/failures:** 1,996 games had decided outcomes; 3 seat-A crashes are attributed as seat-A losses under the registered rule. One both-crash `unknown` is excluded from the primary; its paired partner is decided, so all 1,000 pairing IDs remain represented. No infrastructure label, rerun chunk or chunk-number gap. Primary analyzed-undecided count is 0 after registered attribution/exclusion.
- **Registered sensitivities:** unknown-as-loss: 49.65%, 95% CI [47.62,51.68], p .7349; unknown-as-win: 49.70%, 95% CI [47.68,51.72], p .7714. Each uses 2,000 games / 1,000 pairings and also meets A3. No outcome-driven exclusion, replacement or rerun.
- **Provenance:** per-family analysis gate opened 13:32:49 UTC and committed as `f9be302`; registered analyzer completed 13:33:52 UTC, exit 0. Invocation HEAD `f9be302b46e61cae6c770d3a8862c9608941685b`; attribution source last-touch `0ec609684d58da56275d41923726e4c06945be47`. Exact command, times, source/input hashes and raw stdout are in `scratch_ds/codex/c5/aa1-readout/20261027T133333Z/`; result/failure JSON in `scratch_ds/aa1/`. A private diagnostic initially compared percentage to fractional bounds, corrected before a verdict; raw analyzer outputs were unchanged.
- **Review:** independent Sol **ACCEPT/PASS**, `scratch_ds/codex/c5/sol-review-aa1.md`; all 100 archive hashes independently matched, chronology/denominator/attribution/sensitivities checked. A/A 2 is still incomplete and has not been analyzed; whole C5/S2.1 remains partial. `value` remains the sole freeze candidate regardless of G2 FAIL; the early freeze still requires A/A 2 to pass as well.

## 2026-10-27 13:59:51 UTC: A/A 2 all first chunks — technical PASS (counters only)

- **Scope:** all 20 exact original jobs `hraa2-20261027-j00-7073` through `j19-7073`, one `c000` each; **400/400 decided records**, complete decided A/B treat-side coverage and bot logs. Every first chunk has 20 records, no missing decided log, treatment/vanilla marker_lines=0, and treatment/vanilla fallback_warnings=0. Generic positive-marker valid=false is expected for neutral fp. This completes the first-chunk **technical** check; no A/A 2 win rate, CI or p was computed.
- **CHECK/REVIEW:** helper source remains accepted commit 773a5da. Root checked all 20 archives together for duplicate internal identities and individually for caller gates with h2h.analyse patched to raise; zero estimator calls. All archive bytes/SHA-256s match saved metadata. Fresh Sol ACCEPT: `scratch_ds/codex/c1/sol-review-first-chunks-all20.md`. Evidence: `scratch_ds/codex/c1/first-chunks/orchestrator-all20-check.json` and `20261027T134258Z-remaining/`.
- **Limits:** actual Azure start fields remain null, so all start-to-blob timing verdicts remain UNKNOWN; creation-to-blob bounds are informational. Whole C1 remains partial due to timing and its recorded non-secret-identifier output-handling DoD exception. Complete-family A/A 2 analysis remains behind C5 exact terminal/chunk/seed/pairing gates.

## 2026-10-27 14:24:39 UTC — Belief screens final (C2 / S1.18)

**Record only, not finalists (research/28 §0.3 P3).** No candidate or threshold change; `value` remains the sole freeze candidate. All three exact jobs Completed after their registered deadline-capped run (`2026-10-27T14:00:00Z`); these are not fixed 200-chunk experiments. Runtime: 10 pairings/chunk with side swap, max 200 chunks, seed base 810000 plus chunk index. All uploaded chunks are contiguous, fetched and independently checked: 131 archives, 2,620 records, 1,310 pairings; every chunk has 20 distinct game indexes 0–19 and the expected seed/side-swap. All 131 SHA-256 values reconcile between Luna and root. Exact job shows supersede the initial incomplete list query; actual Azure start/end times are unavailable (null), not inferred. Complete-family gate: **14:14:49 UTC**, before the first analysis.

| Family / exact job | Chunks | Primary game records / pairings | Pre-registered treatment win rate, 95% CI | Nominal two-sided p | Marker validity |
|---|---:|---:|---|---:|---|
| `lev-belrs` / `hrlev-20261027-belrs-9231` | 47 | 940 / 470 | 50.11% [47.28, 52.93] | 0.9411 | valid: true |
| `lev-belrs05` / `hrlev-20261027-belrs05-9231` | 42 | 840 / 420 | 47.14% [44.31, 49.97] | 0.0477 | valid: true |
| `lev-belrsfp` / `hrlev-20261027-belrsfp-9231` | 42 | 840 / 420 | 50.48% [47.47, 53.49] | 0.7565 | valid: true |

Failure attribution: 8 timeout records, all labelled `A crashed` and attributed to vanilla crashes (3 / 3 / 2 by the table order). The pre-registered estimator includes those as treatment wins; there are **zero unknown exclusions**, no rerun chunks and no chunk-number gaps. Direct E2 marker counters and the safe `first_chunk_check.py` yes-mode agree for all three: valid true, no vanilla marker lines and zero fallback warnings on either role. These exploratory nominal p-values do not establish a confirmatory benefit or change finalist selection.

For transparency, the non-primary default estimator excluding the 8 undecided records is:

| Family | Games / pairings | Win rate, 95% CI | Two-sided p |
|---|---:|---|---:|
| `lev-belrs` | 937 / 470 | 49.79% [46.94, 52.63] | 0.8835 |
| `lev-belrs05` | 837 / 420 | 46.90% [44.06, 49.75] | 0.0330 |
| `lev-belrsfp` | 838 / 420 | 50.24% [47.21, 53.27] | 0.8775 |

No additional sensitivity field was emitted or invented; zero unknown games require no unknown-game bounds. Execution deviation: the first per-family legacy `--check-treat-markers` calls also ran completed-family analyses. Every family was already complete at that time, so no interim look occurred. All raw outputs/timestamps/hashes are preserved, and root verified those reports exactly match the subsequent grouped output. The marker-only step was repeated using the approved safe helper; no analysis code or rule was changed.

Registered grouped invocation (with runtime-derived explicit 20-record validation):
```sh
python3 tools/analysis/attribute_failures.py --mode treat --group-by-prefix --expected-chunk-games 20 scratch_ds/lev/lev-belrs-c*.tar.gz scratch_ds/lev/lev-belrs05-c*.tar.gz scratch_ds/lev/lev-belrsfp-c*.tar.gz --out scratch_ds/lev/belief_failures.json --json scratch_ds/lev/belief_result.json
```
Executed 14:19:46–14:19:50 UTC, exit 0. Invocation checkout **fb4ef434f7c22349650d238138fe6b1e64d5f123**; attribution source last changed in **0ec609684d58da56275d41923726e4c06945be47**. Source/config hashes, exact argv hash, terminal/blob inventory, gate chronology, first-call deviation/reconciliation and root checks: `scratch_ds/codex/c2/screens/20261027T140100Z/`. Raw grouped outputs: `scratch_ds/lev/belief_result.json`, `belief_failures.json`. Human secondary remains separately recorded above (f261cf0); no rerun of that benchmark. C2 mechanical technical CHECK ACCEPT; current-source full no-fetch signal remains to be checked.

## 2026-10-27 15:30:30 UTC — C10 partial integration measurements (local, not scientific gate)

Repair1 eight-source-file snapshot independently accepted by Sol (`scratch_ds/codex/c10/sol-review-repair1.md`; exact SHA values in repair1/root-check.json). Separate-process parser24/24,verifier53/53,integration6/6 tests PASS. Fixed five-real-review audit,258 hidden decision-key probes:0 hidden-tool exposures,unauthorized receipts,hidden citations or mismatches. Five intercepted UI explanation requests did not reach the model; rendered-string corpus0 prohibited hits. These deterministic checks have no CI/p and establish implementation boundaries, not explanation truth, latency or VT1 performance. No Azure jobs. Input source full snapshot is recorded by the associated commit and SHA manifest; prior invocation main was ab9fdf5.

Original pinned export flow measurement remains PARTIAL: one parse HTTP200,keyword parser4chips/1drop,25.106seconds,exact resulting belief lacks cached verdict. No matching verdict/accuracy result,latency distribution,CI or p is asserted; no rescue workload or changed note. Four readable captures accepted; absent-panel criterion NOTMET after two reviews, conservative showcase/template demonstration fallback, whole C10PARTIAL. C7/VT1 and C9 live-text gates remain pending; pre-repair tab-started explanation excluded.

## 2026-10-27 15:36:44 UTC — A/A 2 official PASS (C5 / S2.1 / A3/A3a)

**PASS**, fresh independent Sol ACCEPT (`scratch_ds/codex/c5/sol-review-aa2.md`). Exact20 original jobs `hraa2-20261027-j00..j19-7073` Completed;100 canonical archives,2000 records/1000 designed pairings, exact3,000,000+10,000*j+k seeds and20 registered side-swap/neutral-fp runtime commands. All20preexisting c000 preserved. Root recomputed neutral helper with estimator patched to raise:0estimatorcalls before gate,1998decided/side-covered,0missinglogs, both roles marker_lines=0/fallback_warnings=0. Genericvalid:false expected for fp under A3a. Gate committed **18b5d28 at15:28:49UTC**, BEFORE first analyzer15:29:50–15:29:52UTC; no interim look.

Primary pre-registered result: **50.85% ±1.97points,95%CI[48.88,52.82],two-sided p=.3972,z=.85,n=1000pairings/2000games**. A3 requires point47.8–52.2 AND p>=.05, not containment of the entire CI: PASS.1998decided+2timeout records labelledAcrashed (1vanilla,1treatment); each counted as loss for crashing bot. No unknown/infra label, no exclusions in primary, no replacements/reruns/chunk gaps. Default excluding-undecided sensitivity:50.80%[48.83,52.77],p=.4261,n1998games/1000pairings,also PASS; it is not the primary estimator.

Invocation: `python3 tools/analysis/attribute_failures.py --mode treat --expected-chunk-games 20 scratch_ds/aa2/aa2-j*.tar.gz --out scratch_ds/aa2/failures.json --json scratch_ds/aa2/result.json`,exit0. Invocation HEAD18b5d28ada7e57bafbc3cce0a84860d9f00323c6; main C10commit advanced checkout toee2dfdd during run, analysis/helper source SHA values unchanged. Attribution source last changed0ec6096. Exact SHA/argv/source/gate chronology and raw output under `scratch_ds/codex/c5/aa2-readout/20261027T152926Z/`; full inventory/rootCHECK separately in c5folder. No claim full HEAD stayed unchanged.

**Two-family S2.1 scientific condition met:** AA1officialPASS49.65%[47.62,51.68],p=.7349,n1000pairings/1999analyzedgames (2000source); AA2PASS above; F1alreadylogged1500games. The registered **Oct29 12:00Paris early-freeze date condition** is therefore satisfied, without authorizing A4/tag/build/sealed read/submission ahead of its separate gates. `value` remains the sole candidate despite failedF1/G2. These are harness-calibration checks, not evidence of bot strength, champion equivalence or a positive sealed result. No statistical rerun.

## 2026-10-27 15:37:34 UTC — VT1 population Stage1 started (C7 / S1.21), no verdict result

Local detached launch15:34:09UTC, supervisor21279/farm21280, exactly12reviewworkers (plus1resource tracker), n150inputgameIDs/seed7/worlds4/20000iterations. Main ee2dfdd9d38ca5bb6999e2e93e37dc2309556928,forkc04b38e0193fe922badc61a913bd4d343ee68b34; runner/input/script/calibratorSHA and argv captured in `scratch_ds/codex/c7/vt1-start/20261027T153204Z/`. Preflight noE9/otherreview/unexplainedoutput; root hash/PIDsurvival checked15:36:58UTC. VT3preflight11/11PASS. No Azure job.

StatusRUNNING, end/exit not yet available. Selected/analyzable n, false-decisive count/rate/CI/p and VT1PASS are not computed; selection and verdict files absent. After complete successful search, selection and independent >=100analyzable population gate remain, then a separately authorized registered batch.13casepilot excluded. Private supervisor final-status metadata suffix issue is preserved/corrected separately, not a change to scientific output. E9/source mutation held.


Correction at 2026-10-27 15:39:21 UTC: Luna retracted the provisional final-status newline-defect report: raw start records are standard-valid JSON, independently parsed by root; no farm/source/scientific output defect is established. The original records remain unchanged. Metadata observer22022 had already started and will exit naturally after writing a separate SHA-linked status on supervisor completion; it changes no scientific output. Use actual exit/raw metadata, never the earlier mistaken note, to assess Stage1.
