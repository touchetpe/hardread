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
