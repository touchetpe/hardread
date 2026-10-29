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

<a id="src-student-v2-model"></a>

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

<a id="src-luck-v2-self-play"></a>

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

<a id="src-voi-full-revelation"></a>

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

<a id="src-bridge-draft-b"></a>

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

## 2026-10-27 16:07:18 UTC — VT1 offered population ACCEPTED (C7 / S1.21; not verdict accuracy)

Fixed184analyzable/unique decisions on136game-sides, from300completeerror-free sides of150seed7label-farmgames. Exactregisteredabsolute_human/WP[.20,.80]/cost>=.05/top3rule:11,883decisions,8,824balanced,185precap,1dropped,184retained;rank1/2/3=136/37/11.0truthmismatch/unknown/missinglog/team/error;chosenlabeltruth andeveryselectedfieldindependentlyreconciled. Stage1exit0end15:43:40UTC;Stage2exit0at15:52. Source/input/forkhashesunchanged. NoC7Azurejob.

Rootcheck154917Z/root-population-check.json andfreshSol sol-population-review.md ACCEPT S1.21. CandidateSHA256`dc8455b5d1fd96444acbcddab9a3c56489196b092369fe51ce8b586737df5c2f`. Stage1maincodeee2dfdd/forkc04b38e; selectionstartmain50930a8,exactreview/calibrator sourceSHA pinned. C9unrelatedsourceimplementation ongoing is not partofthispopulation. No false-decisive count/rate/CI/p orVT1PASS computed; selectioncountsnotanaccuracybenchmark.13casepilotexcluded.


## 2026-10-27 17:05 UTC — VT1 initial 20k gate: FAIL (C7 / S2.3; fixed offered population)

The registered initial batch completed all **184 decisions from 136 game-sides**, with zero errors and exact candidate/output case-ID reconciliation. Settings: stated none, reference engine, seeds 11/12/13, four worlds, 20,000 iterations, margin .05, twelve Mac workers. Worker source was clean pinned HardRead `4bf7aeb0a6102c20e9d3121860092d9f4c7fb476`, fork `c04b38e0193fe922badc61a913bd4d343ee68b34`; independent anchors preserve source through the pending scrub. Candidate SHA-256 remains `dc8455b5d1fd96444acbcddab9a3c56489196b092369fe51ce8b586737df5c2f`. Full VT3 passed **11/11**.

- Misplay 138; Misread 0; Defensible 3; Within noise 43. Registered false decisive: **138/184 = 75.00%**, Wilson 95% CI **[68.275%, 80.702%]**. The gate **FAILS** the rate ≤10% and upper bound ≤18% criteria, with n≥100 satisfied. No p-value is part of this registered gate. This is the protocol's self-play false-decisive measure, not a measured human-player error rate.
- Runtime: **239.46 s**, October 27 16:51:51.867–16:55:51.320 UTC, child and supervisor exit 0. The 48 repeated game-side decision rows are legitimate distinct offered decisions under the cap of three; no case was dropped, replaced or retried. The 13-case pilot is excluded from n.
- Root independently rederived the rule/counts/interval from all 184 rows; fresh Sol **ACCEPTED the valid FAIL**. No decisive product or demo claim is supported. The next step is only the registered .08 rescore of this saved run, followed by the same-case 60k search if that also fails; neither fallback has run at this record.

Evidence: `scratch_ds/codex/c7/vt1-batch-plan/20261027T161652Z/real-run-pinned-20261027T164636735110Z/` (structural gate, registered readout and root check), plus `scratch_ds/codex/c7/sol-initial-gate-review.md`. Sidecar SHA-256 `18032daa6f220079c2ea381701a4380a416ef3f5cbf8708a01166d84fb2c3d35`; raw summary SHA-256 `9cda33857649155c45e072f34bdbebfa11ed999c8c69b575b98a24e348c48d3e`. No fallback/by-margin readout was used for this record.


## 2026-10-27 17:19 UTC — VT1 .08 rescore serialization defect (invalid execution, no gate result)

The registered `rescore --margin 0.08` command exited 0 but returned `n=0, errors=184`. This is **invalid**, not a quantitative PASS or FAIL: the original 184-row sidecar has zero errors. The compact batch projection stores `error:null` on successful cases, while `summarize` rejected every record containing the key. All original source/input/result hashes were unchanged; no search was repeated and no case dropped. Invalid output SHA-256: `375ce94241486287213baae5708ff6aed40a7853e48bc66f491a583f739b50da` (preserved).

The minimal reviewed fix accepts absent or null `error` and continues to reject every non-null value, including empty strings. Two pure regression tests pass, including 184 synthetic cases round-tripped through the real rescore function; full VT3 remains 11/11. Root independently checked both suites and fresh Sol accepted the one-line analysis fix before a corrected real readout. Rules, thresholds, cases, margins and MCTS are unchanged. Evidence: `scratch_ds/codex/c7/rescore-repair/` and the original run's `rescore-m008-20261027T170643795885Z/`. The valid .08 readout and any 60k fallback remain pending at this commit.


## 2026-10-27 17:40 UTC — VT1 registered margin .08: valid FAIL

The corrected rescore of the unchanged initial 184-case, 20,000-iteration batch exited 0 using scorer commit `083bde7`. No new search or model call occurred. At margin .08, the complete population has **39 Misplay, 0 Misread, 3 Defensible and 142 Within noise**, with **n=184, errors=0**. False decisive is **39/184 = 21.1957%**, Wilson 95% **[15.9106%, 27.6589%]**. This **FAILS** both registered bounds (rate ≤10%, upper bound ≤18%). No p-value applies.

Root independently rederived the result from all 184 raw rows and verified unchanged input identities; fresh Sol accepted the complete FAIL. Output SHA-256: `36d7bd958a54398fbf4f189107ecec3e3b2a3df0679b402163eaddf00d4ec5d9`. The earlier zero-case serialization artifact remains preserved and excluded. Source/input provenance and the initial .05 FAIL remain as recorded above; the 13-case pilot contributes nothing to n. Evidence: `scratch_ds/codex/c7/rescore-repair/20261027T172025Z-corrected-m008/` and `scratch_ds/codex/c7/sol-m008-gate-review.md`.

The next permitted stage is a separately checked 60,000-iteration run of the same 184 cases, seeds 11/12/13, four worlds, margin .08 and 12 workers from the pinned worktree. It has not started at this record. No decisive product or demo claim is supported.


<a id="src-vt1-60k-08"></a>

## 2026-10-27 18:13 UTC — VT1 registered 60k/.08 fallback: PASS

The same fixed 184 offered decisions completed at 60,000 iterations, margin .08, four worlds and seeds 11/12/13 (`stated=none`, `reference=engine`, `absolute_human`, 12 Mac workers). Pinned HardRead `4bf7aeb` and fork `c04b38e` were clean at launch; independent Git anchors let main remain editable. The run started 17:49:57 UTC and ended 17:54:44.419 UTC, exit 0, **286.55 seconds**. No case was dropped or replaced, and there were **zero errors**.

Counts: **18 Misplay, 0 Misread, 27 Defensible, 139 Within noise**. False decisive is **18/184 = 9.7826%**, Wilson 95% **[6.2778%,14.9324%]**. This **PASSES** the registered rate ≤10%, Wilson upper ≤18%, n≥100 rule. No p-value applies. VT3 remains 11/11. The earlier .05 and .08 failures were recorded before this permitted fallback; the 13-case pilot is excluded. No wording fallback is triggered.

Root independently applied the exact pinned pure rule to all 184 rows; fresh Sol ACCEPT is `scratch_ds/codex/c7/sol-60k-gate-review.md`. Summary SHA `a0908c2298ab8ac4209490412bfd1a43c83abc8d1c4d561c3cd2b303645c552b`; sidecar SHA `494822b8751b855c28a4fd2deed94808bbe5578baf56dce5b6262330f3e4c12d`. Evidence: `scratch_ds/codex/c7/vt1-60k-pending/20261027T174226Z/`, including `root-exact-rule-check.private.json`. A stale redundant manifest argv hash is preserved as a provenance warning: the exact argv array, launch preflight and both durable process records agree. E9 began after VT1 exited; there is no overlap.

This validates this registered configuration and offered population. The app must use 60k/.08 before applying this result to product verdicts. It does not establish perfect accuracy, the complete app flow, or explainer quality.

## 2026-10-27 18:13 UTC — External GPU training wave: execution verified, P5 protocol deviations

A separate coordinator submitted `hrsft-20261027-v1e1-bfef` and `hrsft-20261027-v1e2-bfef`, six nodes each, plus `hrstu-20261027-v2-bfef` on one node. At 18:05 UTC all three jobs and thirteen nodes were Running. The two Glimmer jobs passed preflight, detected H100 NVL GPUs and began training stages at 17:53:25 and 17:53:39 UTC. This is execution evidence only; no partial accuracy metric was inspected.

**P5 acceptance is not met as submitted.** Training stages began before the audited data-manifest commit `244f772` at 17:56:16 UTC, and no pre-submission Sol wave check occurred. Both use the correct 132,000 training / 2,666 validation rows, but their evaluation file is the v1 default, not the fixed v0 400-row population. Any eventual comparison with .395/.470 requires the exact fixed v0 rows, separately checked. Do not interpret the earlier log's fixed-evaluation wording as actual snapshot provenance. No duplicate SFT was submitted or running job canceled by this root team.

The posthoc exact-name deadline monitor for these three jobs is armed and verified; it mitigates deadline risk but cannot repair protocol chronology. Private evidence: `scratch_ds/codex/p5-20261027/preflight-trainstart-audit.private.json`, `external-sft-audit-sol.private.md` and `scratch_ds/codex/urgent-20261027/deadline-watch/`. Final stage results, actual node-hours and all scientific gate readouts remain pending.

## 2026-10-27 18:52 UTC — R1 prospective model selection and comparison
Before any new adapter accuracy read, freeze candidate set e1/e2/XL. Require all own pipeline stages ok, served legal ≥98% and p95 ≤5 s. Evaluate every eligible adapter on the same complete v1 validation action set (2,428 rows; val SHA aed05c91f3140fbffb4b37b429baa4d456a8ba75e0da85888c4c0c3081cb384e). Select maximum validation top-1, then minimum CE, then e1/e2/XL deterministic order. Record selection before fixed-v0 scores. Separately evaluate all three on fixed-v0 400 rows (eval SHA 3b0ba8fa2e2db60b9149d4ac03f1ae5bcbcb152745c4fa8fe1bf54fcc5e06276); original default-v1 evaluation is not that comparison. Only the selected adapter gets seed-2000, 200-game arena; failures count as losses. ≥45% triggers registered G1 seeds 4000/5000. Science only; development teams partly seen. Full rule in P5XL card committed 53e57b0 before launch; XL manifest cca93de precedes training. No new accuracy measured here.

Initial P6 ten-job farm wave failed at setup: code snapshot omitted required foul-play directory level, and output URI incorrectly reused an older job path. That shared artifact set is quarantined and excluded from new training-data counts. No valid new chunk acceptance. Root and review staging checks missed these faults; retry requires explicit layout and unique-output assertions.

## 2026-10-27 19:36 UTC — XL ten-node startup failure, no model result
The audited286k-row XL job met Running and preflight timing, but rank2 hit output-mount EIO while saving the tokenizer before optimizer step1; its wrapper retried plain loss while nine peers remained weighted. Root preserved logs and cancelled the exact job; terminal Canceled verified19:13UTC. No accuracy read or adapter accepted. Separate e1/e2 audit confirms allsixranks/job weighted, microbatch4, no failed ranks/fallbacks, steps648/649 at19:14UTC. One owner-authorized six-node fallback is being staged from reviewed repaird9d3a10; no outcome claimed.

The setup-only farm probe finished setup but endedFailed: its leader exit0 was followed by an immediate owned-descendant TERM/guard125, consistent with log-pipe drainage. Six simulated guard tests pass; three Linux behavior tests pending. No new farm chunks count.


## 2026-10-27 20:38 UTC — Student v2 R2: dev-selected LR model and complete offline report

The fixed candidates are `hrstu-20261027-v2-bfef`, `hrstu-20261027-v2lr-5019`, and `hrstu-20261027-v2ep-5019`; all completed. Selection rule was committed in `d9d3a10` before any result read. Independent input CHECK found byte-identical per-job inputs/extraction/count metadata, the same 264 lab archives and encoder/split code, and 48,117 dev decisions. Three identical missing lab logs were counted, not silently repaired. All 264 lab bytes were cross-checked against remote content MD5 and a local SHA manifest; the 39 non-dev val/vol MD5 gaps did not decide selection.

Main dev loss (`pol + 0.5*vsearch`) was bfef 1.840206051959429, lr 1.828945203736395, ep 1.8415082566234005. The global-minimum tie tolerance is 1e-4 with fixed bfef/lr/ep priority; no tie occurred. **Selected: lr, `main_lr0.0008`, LR 0.0008, logged minimum epoch 5** (not a claim about the saved checkpoint epoch). Dev lock preceded held-out inspection. Selected `main/best.pt`: 102,461,844 bytes, SHA-256 `4ddf39ca4edb0e8fe4e218875b07b5e6838812e4cf4cc3eed8aafdac0a4adea6`; before/after remote identity matched. Immutable checkpoint lock was written 20:15:10 UTC, before the held-out report at 20:16:39.

| Candidate | Arm | Split | Decisions | Games | Engine-choice top-1 | KL | Inference-value Brier |
|---|---|---|---:|---:|---:|---:|---:|
| bfef | main | lab_test | 93,417 | 1,157 | 57.3482% | 0.377454 | 0.174009 |
| bfef | main | val_crosspool | 124,611 | 1,579 | 57.7606% | 0.376739 | 0.170820 |
| bfef | control | lab_test | 93,417 | 1,157 | 57.8931% | 0.372555 | 0.175726 |
| bfef | control | val_crosspool | 124,611 | 1,579 | 58.2589% | 0.373390 | 0.171806 |
| lr | main | lab_test | 93,417 | 1,157 | 58.1243% | 0.367678 | 0.173620 |
| lr | main | val_crosspool | 124,611 | 1,579 | 58.4748% | 0.365429 | 0.170606 |
| lr | control | lab_test | 93,417 | 1,157 | 57.9359% | 0.370101 | 0.175106 |
| lr | control | val_crosspool | 124,611 | 1,579 | 58.2846% | 0.368904 | 0.169687 |
| ep | main | lab_test | 93,417 | 1,157 | 57.1352% | 0.380008 | 0.175090 |
| ep | main | val_crosspool | 124,611 | 1,579 | 57.4524% | 0.378561 | 0.168527 |
| ep | control | lab_test | 93,417 | 1,157 | 57.7272% | 0.373968 | 0.178267 |
| ep | control | val_crosspool | 124,611 | 1,579 | 57.9676% | 0.376029 | 0.174411 |

V1 descriptive reference: main roster test 54.5% / KL .411 / Brier .176; separate-pool 55.3% / .415 / .171; control roster top-1 53.3%. V1 used 2,699 label games, about 37,600 roster-test decisions and a 440-game separate pool; v2 uses larger cohorts shown above. The gap is descriptive, not paired or a significance claim. The selected v2 main exceeds its same-run control by only about 0.19 top-1 points; no significant main-control advantage is established. These percentages measure offline engine-choice agreement, not battle wins.

Fresh Sol accepted comparability, selection chronology, checkpoint bytes and complete reporting. Filtered report SHA-256 `c55132620a29e90cddb1eab7bd45e3c28db781e08dcf8cf915d1dc318f473760`; checkpoint-lock SHA `5d7de931026c26147eb4da705ee4c5213bd5ef5293591432cd1796572dfee01c`. Raw artifacts and reviews remain private under `scratch_ds/codex/training-roadmap/r2-input-audit/`. No offline CI/p-value was computed from these aggregate reports. The selected-model 200-game arena, legality and latency measurement remain pending; the old 32/40 heuristic arena is a small earlier pilot, not a v2 result. No new arena or T3 job was submitted by this readout.

<a id="src-s22-calibration-fail"></a>

## 2026-10-27 20:53 UTC — C8 Draft-B calibration: overall FAIL; current calibrator retained

Prospective v4 source and merge/CI rules committed as `bfb8d47b754470a2be54fbe94188e5b9995e0a4d` at 20:35:48 UTC, before preparation at 20:40:57 and fitting at 20:41:58. Independent Sol accepted provenance and the negative readout. Execution record SHA-256 `65057ab71b7aacda90bcbd892937530ec28cd2524600de1cd0b5c71d281a1f29`; private review `scratch_ds/codex/c8/sol-real-readout-review.private.md`. No Azure job was used for this local readout.

S1.22 cohorts have both-side success 198/200, 195/200, 200/200, 200/200, **182/182**, and 513/513 in ascending broad bands followed by top50. The strict 190-game high-band quota is unmet; all 182 eligible games qualify only for the previously registered ≥100 fallback. There are 1,495 cohort incidences but 1,474 unique games, with 21 overlapping games. Unique pooled both-side success is 1,467/1,474 = 99.53%, Wilson 95% [99.023%, 99.770%]. Retain all errors and wider high-band uncertainty.

A2 excludes **412 whole games** when either roster matches: 39 hr_val, 380 reserved, seven overlapping. Remaining: 1,062 games / 2,124 sides, including eight error sides. Fit/test halves are disjoint by registered game-hash parity: 491 fit games / 978 successful sides and 570 test games / 1,138 successful sides; one remaining game has no successful side. No test game contributes to fitting. Pooled test has 31,865 unique decision rows; cohort membership overlaps without duplicating pooled rows.

| Test scope | Games | Successful sides | Decision rows | ECE | Brier | Registered ECE verdict |
|---|---:|---:|---:|---:|---:|---|
| Pooled unique | 570 | 1,138 | 31,865 | 0.0200087 | 0.1912 | PASS ≤0.04 |
| <1300 | 95 | 190 | 5,164 | 0.0629091 | 0.2163 | FAIL >0.06 |
| 1300–1499 | 97 | 192 | 4,653 | 0.0238984 | 0.1896 | PASS |
| 1500–1699 | 73 | 146 | 4,294 | 0.0468981 | 0.1790 | PASS |
| 1700–1899 | 76 | 152 | 4,132 | 0.0215000 | 0.1995 | PASS |
| 1900+ | 66 | 132 | 3,922 | 0.0729196 | 0.1709 | FAIL >0.06 |
| Top50 | 168 | 336 | 9,942 | 0.0323707 | 0.1916 | PASS |

Candidate coefficients a=0.6244, b=-0.0084. Gate decisions use unrounded values. The prospective 1,000-replicate game bootstrap (seed 7) gives non-gating pooled ECE 95% CI [0.00983, 0.04322] and Brier [0.18102, 0.20247]; no p-value is part of this threshold gate. Preparation exited 0; calibration exited 1 because two bands failed. An initial incomplete preparation argv exited 2 without output and is preserved in the execution record.

E6 separately passes **11/11 gated rows** on the full unfiltered merge (2,940 usable sides, eight error rows skipped), with top-level PASS. Its separate ungated run520 reference has one diagnostic failure. Bridge PASS does not override the failed calibration gate. **S2.2 FAIL: no candidate write, force, app switch or stronger population-calibration claim.** Keep `absolute_human` and the registered top-ladder wording. No threshold change or reroll is authorized by this result.

## 2026-10-27 21:05 UTC — Local runtime checks: T3 architecture risk and partial W1 coverage

T3's untrained, seed-11 large model was exported through the existing NumPy path using the pinned encoder and source55b6506. On an Apple M4 Max, end-to-end `Student.policy_value` (including encoding), batch1 and thread limits1, used20 warmups then500 timed calls across3 repeats per fixed synthetic scenario. Four actions/no events: p50 16.083/16.130/16.504ms, p95 17.510/17.298/18.040ms. Thirteen actions/16 events: p50 16.531/16.438/16.511ms, p95 18.019/17.275/17.761ms. The untrained91,018,246-parameter NumPy export is364,547,637bytes. Concurrent Mac load was uncontrolled. This flags latency risk; it is not the trained-checkpoint≤15ms acceptance measurement and changes no seed, LR or recipe. Sol accepted the architectural method. Result SHA-256 `b3a8768a053a8d8b1d5a4aac1b6a36736bf90686953e5e02ac15217fc97df11d`, private `scratch_ds/codex/training-roadmap/t3-mac-architecture-latency-v2/`. A first synthetic history-count failure and its partial timing are preserved separately; the corrected full run keeps the20/500/3 protocol. No training or Azure job was used.

W1's frozen ten public raw-log cases produced10/10 successful preflights,5 completed general reviews and5 HTTP429 submissions under the default five-new-reviews/client/hour limit. Completed-only n=5 latency p50=46.47s and nearest-rank p95=60.62s. Quick429s are excluded from that timing denominator, retained as failures in the original ten attempts. Cases had empty team exports; none called verdict or explanation endpoints. This is partial technical coverage, not full-demo acceptance or explanation quality. Sol checked request/response/source/receipt hashes. Result SHA-256 `b459a397280548b4dd57490d3cd2871d0ef8dae98846a577df7b907af703583a`, private `scratch_ds/codex/win-odds/w2-proposal/evidence/w1-public-review-smoke-v2-20261027T205454Z/`. A prospective bounded retry of the same five blocked requests is allowed only after22:00UTC, preserving all15 attempt records and the original ten-case denominator, with no limiter reset or bypass.


## 2026-10-27 22:30 UTC — N5 farm probe failure and prospective final corrective reservation

The first N5 one-chunk probe `hrlab-20261027-r2-probe-a6fe856f` is terminal Completed, but its infrastructure gate **FAILS**:20 distinct decided rows yet38/40 required side decision files. Both missing B bots exited1 at startup while parsing the shared sets cache; no gameplay decision was logged for either. All1,412 present decision rows were parseablev3/sample64. No A/B win-rate readout is used. Full archive SHA`0c3748e9d1894116d667a47664aef9887e31651f26e90f9b8d8e7bc0ae376558`; independent diagnosis and corrective-source review are private. Follow-on farms remain held.

**Prospective reservation, before creation:** `hrlab-20261027-r2-n5cw-6581c705fb` reserves the ENTIRE closed200-seed block **[10260000,10260199]**, not just the ten scheduled pairing seeds. This block will never be reused, including after failure. Fresh exhaustive audit SHA`394577dbcaeaf5f6defdc92d2ac0c1778610f4450c3987c221e6033f0aed16f8`:148attempt directories/296files exact,118parsed farm ranges,26input digests current, zero outside-candidate intersections/unclassified cases. Candidate YAML SHA`0a4e49f30ff142c78bd4a06a958981d89e5fe7fc5031a7083860971511617807`; Sol conditional source/cost review SHA`9446ce1a85dac444843123686560c8f780d27e687460e20cfa638f9ce85bd1e6`.

One node, max3node-hours; fixedC1t1/hr_train/v3/sample64; ten side-swapped pairings=20scheduledgames. Only a private wrapper is added to the unchanged captured source: serial existing-loader cache warmup, strict nonempty/stable JSON validation before concurrency. Offline rehearsal reproduced cold-cache JSON failure and then40clean warmed readers. Captured source markers remain historical5215676/c04b38e; relevant runtime files match the post-scrub checkout, whose fork HEAD isf061e63. No source marker is relabelled as a new capture.

**Final gate fixed before data:** exactly20unique game rows, at least19decided, both bot return codes0 for every row, and **all40** expected A/B side decision files nonempty and wholly parseablev3/sample64. The embedded wrapper validates only decided rows, so its `COUNTER_GATE_PASS`/AML Completed status is insufficient: a separate terminal-archive all40 audit is mandatory before any expansion. Zero missing/invalid counters required. Preserve every failure; if this last corrective probe fails, no farm expansion and T4 moves after sealed work. Exact-name watcher cancel08:15UTC and terminal08:30UTC Oct28; fresh default25node admission and exact-name/output absence required before create. No new quality threshold or model selection is introduced.

## 2026-10-27 22:30 UTC — Historical infrastructure provenance and partial public-review smoke

Independent audit of all250completed A/A1,A/A2,G2archives/5,000game rows found all10,000bot log references present and no JSON cache decode errors. Nineteen rcflags map to12unique games: six already-attributed A timeouts, one already-recorded A/A1 unknown, and five decided B forfeits (A/A1four,G2one) counted under the existing A3a rule. Published denominators and A/A PASS/G2 FAIL are unchanged; no new outcome sensitivity or reclassification. Final census SHA`90d929acef31f58d29ba49994945817dce5a254a6f72cde8b9bcb30ffe8a1649`; exceptions SHA`2f2fe9dbd4d28d2517dd3390ed6dd9226229b7267cf791fd2eae12556625c7bb`; final independent review SHA`73d7331616336ef67f5a7a68b542e8bdf8d19165d37ddc99fed388b797b5186a`.

The ten fixed public replay cases now all completed **general draft review** after15submissions: five original completions/five original429responses retained, then one permitted post22UTC retry for each remaining case, allfivecompleted. Completed-only n10 p5045.9786s/p95nearest-rank77.7973s; retry-onlyn5 p5039.4163s/p9577.7973s. No teams supplied, no verdict/explanation calls. This is not fullW1/P3/C10/A1 or model-quality acceptance. Original results immutable; retry result SHA`b7a3484545877004001d30ecee484208df9d1fdd65c245b32fbd45588b20a2f0`; independent review SHA`2d59afc4235e68cca3f090d8627d9b3277bdf9211c16c8fa600487de2d8d45a9`.

## 2026-10-27 22:51 UTC — N3d read-only BF16 prerequisite: Completed, infrastructure scope only

Owned job `hrxpl-20261027-bf16hash-8f136d11dd` completed22:26:56–22:29:08UTC (132seconds), one node, reviewed ceiling0.5node-hours. Source9a2881cb5db26f2612845fd32366437aa46690b3; script SHAed7e7f03a78b3b9fbf759b8cb99725fd3a31d69dac72eeec4ffe9ffa6a3ed0e5. The4,522-byte manifest reports PASS_PREFLIGHT_ONLY and binds12mounted files/59,581,817,299bytes, including2weight files/59,553,435,272bytes and2tokenizer assets/28,209,833bytes. Mounted model-tree SHAd011cd8d3b69d4d1005a0a50d34857a4a55dc8a9d490e2ef895efa86df33eb86.

The actual readonly mount checks pass; CUDA sees one NVIDIA H100 NVL(cap9.0). Runtime is Python3.12.15, Torch2.12.1+cu130/CUDA13.0 and Transformers5.16.1. No model initialization, forward call, inference, install or network action was performed. No target-mount lock test or committed client-template binding exists in this receipt. Accordingly this is NOT full N3d readiness, a50-item verifier result, tokens/s, semantic quality or A1 success; those measures are unmeasured and noCI/p-value applies. Private raw manifest SHA4f87d1489a78c35ec35b0521a2e703bdc6e65595d132696ab83d3225d2be6d5a, under scratch_ds/codex/n3d-bf16-preflight/owned-readout-20261027T2240Z. Separate driver recording remains disabled under the two-reject fallback.

## 2026-10-27 22:54 UTC — T3 large student: three-seed held-out readout and trained Mac timing

The three exact large-model jobs completed under the prospective rule recorded in the T3 card before the local held-out read. This is an offline decision-agreement/value readout, not a battle win-rate measurement. All main and control rows use the same cohorts: `lab_test` n=93,417 decisions / 1,157 games and `val_crosspool` n=124,611 / 1,579 games.

| Seed | Arm | Lab top-1 | Lab KL | Lab value Brier | Cross-pool top-1 | Cross-pool KL | Cross-pool value Brier |
|---:|---|---:|---:|---:|---:|---:|---:|
| 11 | main | 58.505411% | 0.365708 | 0.174851 | 58.817440% | 0.365570 | 0.170457 |
| 12 | main | 58.293458% | 0.366754 | 0.175457 | 58.583913% | 0.366434 | 0.171244 |
| 13 | main | 58.447606% | 0.364555 | 0.175165 | 58.910530% | 0.363697 | 0.171328 |
| 11 | control | 57.348234% | 0.375486 | 0.172614 | 57.889753% | 0.372460 | 0.165705 |
| 12 | control | 57.818170% | 0.375443 | 0.176221 | 58.169824% | 0.374576 | 0.171046 |
| 13 | control | 57.486325% | 0.374220 | 0.171365 | 57.597644% | 0.373845 | 0.168708 |

The registered main roster top-1 arithmetic mean is **58.41549182696939%**, with sample standard deviation **0.1095649963661747 percentage points**. The threshold is **59.1243%**, so the roster gate **FAILS by 0.70880817303061 points**. The main cross-pool mean is **58.77062752619485%**, for a cross-pool minus roster mean gap of **+0.35513569922546 points**, within the registered 1-point limit: the cross-pool gap check **PASSES**. Per-seed gaps (cross-pool minus lab) are +0.312029, +0.290455, and +0.462924 points. The control rows are descriptive and do not select the main model. The earlier v2 values (58.1243% roster and 58.4748% cross-pool) are references from different recorded cohorts/label sources; no unpaired significance claim follows. Offline top-1 does not imply battle wins. Seed 11 remains fixed for downstream work; there is no test-based reselection.

The accepted T3 report records matched source/input identity across all three jobs: guarded manifest SHA-256 `289c417e6d4ab9aab2f201fec9a7182bfe99789d94732615aca9f276c3f1924f`, encoder SHA-256 `f944e033f1a970227f8d1c2f9d82b1c579fdaa1cec3e154c431660e1e6e795d4`, 264 label / 12 volume / 79 evaluation-only archives, identical input/extraction/count and split files. The exact `results.json` identities were checked against the v1 lock: s11 `a19ed3bc70cb982dc4d61a3f738dbbb564cdf6301a4bceedebb6e3b82fae8ca3`, s12 `b342f3a56df3613ed6f5885793ffa7c0f51c69dc7f1e3c0ec1e391ce2dd8ec63`, s13 `7065ce9fb60e3fbc556ebd527d0993aae5c2747aeba14ce7ea187b30a5add593`. Lock SHA-256 `6ad4495481a72939af3429d40ec4bc7ba0fd97771eee848560c3e25a3fc81cd2`; held-out projection SHA-256 `c2395980727dd8d89d69355d0e25d68d3ad002ef0bf86e4f53656151e2cebe6a`; fresh independent Sol review SHA-256 `d1ec7876e9d9bc7684ff6cb4f4554a6edf4e8847a3e3e75663a70ba9ca016490`. Sol accepted the measurement/provenance within the written evidence limits, confirmed roster FAIL and cross-pool PASS, and did not accept full T3 completion.

All seven registered T3 source files still match the v1 lock:

| Registered source path | SHA-256 |
|---|---|
| `azure/student/run_student.py` | `45bc10575de0bc2fda0f4f704a21e2cb1cd998492e332567877e76ec9d9ec24b` |
| `foul-play/fp/hardread_history.py` | `02e1cbf5115dc76f9d476d462e0288a239a8d21f5fd82617b8beae723352046f` |
| `tools/dataset/extract.py` | `938cb32b6943a5364e47aafba37391fe05c6317602731a83b025455dc0fc8192` |
| `tools/student/encode.py` | `dd2097b14c9a102dc2fc1576cd7fc8a4e010344a14a56df172a9d74110493c0c` |
| `tools/student/infer.py` | `8f9de922ed4b267a2d0f2398b5fbefc1ca4797970eca7adab9aa60fb53e10764` |
| `tools/student/model.py` | `f06950d5fa830481764a4413f573a090d8e966e58387cf6736ced026a2289ac4` |
| `tools/student/train.py` | `b53b7ae33cb8484d3bca094eae353bd5efee00c32785d21d752053d350b8db1a` |

The trained Mac timing used the locked seed-11 `main/best.npz` (SHA-256 `c5bf0cb7f99eca92457869ed56de5bedfd031ba9bc5d557b28b496c10a5f7031`, 364,547,668 bytes; pre/post hashes equal) on an Apple M4 Max. It used NumPy batch-one `Student.policy_value` end to end, including encoding, with all five thread-limit environment variables set to 1 before numerical imports. The frozen synthetic input SHA-256 was `14a0c8feab990fade94e6760321af4102929341ea9ca8e19e9ce83dd894b439f` (13 legal actions, 16 encoded events). After 20 warmups, each of three repeats made exactly 500 timed calls. Repeat p50/p95 were 21.266521/22.114121 ms, 21.266729/21.861465 ms, and 21.245105/21.833315 ms. The predeclared pooled p50 over 1,500 calls was **21.258375 ms**, above 15 ms: the trained latency gate **FAILS**. The report records background load; one-minute load average ranged about 3.4–4.0 during timing, so this is not described as an unloaded measurement. Timing runner SHA-256 `5a2c71db0c7878e4bcffa4a66acd76f179c6999855aa7f0e4e50f2bfcadcf134`; result SHA-256 `30926447426955c1c2cc14fe91ff1a0a91484511e793ae8301ab3b1287de3083`.

The historical HardRead snapshot commit `55b65062d7ab6cab052a714a67590550e64f8187` maps to retained main commit `a4d967106ad0161922279c2b856cfd9c9aafebfd` in `handoff/scrub/commit-map-main.txt` (SHA-256 `5324be0edd3731433bacce5429fff1afb1eba3f69d83499e7c269fab68781b3b`). Fresh Sol additionally verified the seven historical blobs and their current byte hashes. A first HEAD-only timing preflight in the later local checkout (`7e429e0a11b79d2b566f49e33a49e5951cb9b62a`) rejected its difference from the historical lock commit and stopped before checkpoint load or inference. That failed preflight is preserved at `scratch_ds/codex/training-roadmap/t3-readout-private/stage2/timing-runner-preflight1.private.log` (SHA-256 `cfd5b0598c9d1aaae5f705c98f217a76bb0f9a4e72f81bd304e0a2331683f51d`). The subsequent fixed runner required and verified all seven registered source hashes before measuring.

ONNX, ONNX Runtime, and ExecuTorch were absent in the existing runtime. No dependency was installed and no Android-format export was attempted; that gate remains **PENDING/UNTESTED**. The 200-game side-swapped arena remains **PENDING**. Full T3 acceptance is not complete; the registered roster and trained-latency gates have failed, while arena outcome is still unmeasured.

Evidence: `scratch_ds/codex/training-roadmap/t3-readout-private/t3-dev-checkpoint-lock-v1-20261027T223852Z.json`; `scratch_ds/codex/training-roadmap/t3-readout-private/stage2/t3-heldout-readout-20261027T224339Z.private.json`; `scratch_ds/codex/training-roadmap/t3-readout-private/stage2/t3-trained-mac-timing-20261027T224703Z.private.json`; `scratch_ds/codex/training-roadmap/t3-readout-private/stage2/sol-t3-readout-review-v1.private.md`.

<a id="src-student-v2-arena"></a>

## 2026-10-27 23:16 UTC — Selected student v2 battle arena: 86.50% on development teams

The dev-selected v2 LR student (`main_lr0.0008`, PT SHA-256 `4ddf39ca4edb0e8fe4e218875b07b5e6838812e4cf4cc3eed8aafdac0a4adea6`, staged NPZ SHA-256 `a3f1b4b3c1cc15a4f812a0a39d9c2e4e99246dfef9ae7f556287237bea758d09`) completed its registered100side-swapped pairings on partly-seen `gen9/hr_hl_dev` teams (200scheduledgames,seed2000). Exactjob `hrarena-20261027-n3a-v2lr-r1-741c` Completed22:51:59UTC. It won173, lost26 and had1timeout counted as a loss: **173/200=86.50%, Wilson95%CI[81.07%,90.55%]**. Completed-only:173/199=86.93%, Wilson95%CI[81.54%,90.93%]. These distinct denominators and intervals are not interchangeable.

The199policylogs contain5,329observed student decisions, zeroillegalactions/fallbacks/serverrejections, and nearest-rank p95decisionlatency **3.591s**. The timeout has no decisions/policylog; zeroillegal applies to observed decisions. All199completed-game rc pairs are zero. Final807-object inventories match; selectedNPZ/PT136-arrayparity and actual policy=student are verified. The final201requested result objects were fetched with preserved hashes; no decision/state/battle logs were required for this readout.

Earlier v1student32/40=80% is a descriptive reference from an unequal, unpaired smaller run, not evidence of significant improvement or unseen-team generalization. No champion-strength or G1PASSclaim follows. TheGlimmerP5>=45%trigger does not apply to this student; no follow-on arena is required by itsR2card. Fresh independentSol **ACCEPTS the arena measurement**; there is no new statistical threshold gate.

RawgamesSHA4aa6f1e418bb66571727fea522a557a4e2c3a38a600f2c8e92df5ed7ecc410ec; summarySHA73b6b4b3940aeb8e1c59e577e5f933d134f54d11ad196a4dd367649537b29da2; acceptedprivatev1metricreportSHAc0a2ca0d15e90bae4cc8cf52776e098270814942b178ec79be1d11753d9181a9. V2reportSHAfe54f87670dd270ebb2e0890338826b3302a25cf99fa9346862ef8b1634c8e53 corrects fetched-object accounting without changing metrics. Independent reviewSHA27de6ef913be425c6d3e0b8449e006d418047ec34c9b1783afcb1c9dff2c112f at scratch_ds/codex/training-roadmap/n3a/Sol-v2-arena-final-review.private.md.

## 2026-10-27 23:19 UTC — Final N5 cache-warmup probe: external gate PASS

Exactjob `hrlab-20261027-r2-n5cw-6581c705fb` is Completed; authoritative Azure properties give StartTimeUtc2026-10-27 22:38:58 and EndTimeUtc2026-10-27 23:01:45 (1367executionseconds on1node, approximately0.380execution-node-hours, not a billing measurement). Singlechunkarchive SHA`3b8a729a8b494be6804a545bbd9a957b5be6fd4136e785d4b35b4811019b4c60`. Independent external counter-only audit finds20unique game rows,20decided, bothbotreturncodes0forALL20 and0wrong-poolprefixes. ALL40expected A/Bsidefiles are present,nonempty,whollyparseablev3/sample64, withzero missing/extra/emptyfiles,parseerrors,non-v3,missing-belief orwrong-sample records. No A/Bwinrate or outcomes were read. Thus the fixed external20row/>=19decided/ALL40/zero-rc gate **PASSES**.

Candidate YAML/command/uniqueoutput/source identities and all2236codeinventoryhashes match. This clears only the probeGO, not the six-slotlaunch or later teacher-data acceptance. Futurewave still requires reviewedcard/source, freshwhole-blockseedaudit/reservations, completecapacity/name/outputchecks andarmedexactwatchers. Failed38/40probe remains recorded. Wholeblock[10260000,10260199]staysconsumed permanently.

V1externalreport mistakenlydatedtheterminaltimestampOct28; itis preserved unchanged. Versionedv2SHA`bb37019005a9e9470838298347ee8a6210815591efeefab2c2980d1a8815edd6` corrects ONLYtheUTCdate, boundtoauthoritativeexactjobmetadataSHA`4ccd4620a4d0918099dcaa648d675a31024b028eecfc4ad320a03a98d98543fa`. Archive/countersunchanged. FreshindependentSolreviewSHA`759711ab9888e7cf19771781ca5b7e790e236caf1d6ec79c193bfc51c5919b05` accepts bothrawstructuralgateandcorrectedprovenance. No statisticalCI/p-value is applicable tothiscomplete mechanicalgate.

## 2026-10-27 23:25 UTC — Prospective N5 six-slot seed reservations, before any wave create

Final corrective probe independently PASSES its all40gate (reviewSHA759711ab9888e7cf19771781ca5b7e790e236caf1d6ec79c193bfc51c5919b05). Fresh exhaustive seed-union auditV2SHAdaedf67b5ffe6e2fab06358132e0ebeee67e43e650bbf319f1e8a68e9edbb92c verifies149attemptdirectories/298jobfiles unchanged and all51currentinputhashes. RESULTS changes are exact append-only selected-v2seed2000nonfarm reporting and already-reserved10260000probe reporting. Allsixproposedclosedblocks have0actual-reservation/nonfarm-point/pairwiseintersections and0unknowns. Supersededfour-slotproposaloverlaps are explicitlywithdrawn/unexecuted/unreserved, not hidden.

The following ENTIREclosed200-seed blocks are now RESERVED PERMANENTLY for their exactjobs, includingunused/failed/partialattempts; neverreuse them. The correctiveblock[10260000,10260199]remains separatelyconsumed andexcluded. This commits reservations only: no jobcreate, watcherarm orcloudcapacityclaim.

| Exact job | Closed seed block | State |
|---|---|---|
| `hrlab-20261027-r2-wfill01-a72c51e4` | [10220000,10220199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill02-a72c51e4` | [10230000,10230199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill03-a72c51e4` | [10240000,10240199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill04-a72c51e4` | [10250000,10250199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill05-a72c51e4` | [10270000,10270199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill06-a72c51e4` | [10280000,10280199] | RESERVED; not created |

Wave source/card acceptance remains conditional on freshcompletecapacity/exactname-outputabsence/armedwatcher/deadline and per-jobsourceidentity checks. Atmost6one-nodejobs, max36node-hours; eachuses10side-swappedpairings/20games perchunk,max200chunks,C1t1/hr_train/v3/sample64/concurrency5/timeout1800. No startafter02UTC; terminaldeadline08:30UTC/cancel08:15UTC. Onlyavailable reviewedslotswillbecreated; reservedunusedslotsarenotclaimedasrunning. Beforeeachcreate refresh currentattempt/seedledger andchargeanysuccessfulcreateimmediately; nointerimwinreadout.

## 2026-10-28 01:02 UTC — Seven reviewed night jobs created; no performance readout yet

Root submitted the fixed seed-11 T3 arena and six permanently reserved N5 farm slots in the committed order. All seven create responses were confirmed as Starting; the serial runner exited 0. The complete pre-wave All-view inventory counted 16 occupied nodes, zero unknown owned capacity and zero aggregate foreign exceptions. The entire wave was pre-reserved at **23 nodes under the default25 cap**; no27-node exception was used. The last receipt was written 107.458s after that inventory, inside the120s window. This is admission evidence, not a claim of23 Running workers or measured GPU utilization. The original2134-entry arena and2240-file farm stages remained unchanged. Configured upper exposure is39node-hours; all new jobs retain08:15 cancellation/08:30 terminal bounds.

Exact names and six permanently consumed seed blocks are recorded above and in the seven-target ledger. The arena seed remains2000,200scheduled games including failed attempts as losses. T3 accuracy/latency failures remain failures. Each complete farm chunk requires20/20decided and all40v3/sample64side logs before ingestion; no protected interim win read occurred. Source/attempt/reservation/name/output/watcher checks passed before each create. First Running/first-chunk/terminal checks remain pending. Private ledger SHA-256 `6afe94c126f1c8acb8833a03073dd02762fc068efc683b8bbe97aea18cf7158b`; capacity proof `2fd0b3b2b0ec6fa33fa34c679d9d24c1fcdcf865ddd43a91145367bca84eb99b`.

## 2026-10-28 01:02 UTC — Three technical review fixtures and separate Q1 receipt checks

All three fixed independent technical reviews completed with HTTP200 views/receipts and no product/API/job crash. Every direct current `disagreements` field was explicitly an empty list. Consequently **0/3 complete note/verdict/explanation flows**; parser and verdict were not attempted, and the three were not resampled. Fresh saved-artifact schema review confirmed the response contract. The original browser-blocked P3 inputs remain untouched; these technical fixtures do not replace its acceptance cohort.

A separate precommitted Q1 card made one ordinary explanation request on each same review with empty offered keys. Final modes: repaired Glimmer, template fallback, raw Glimmer. Across the three answers, **17/17 checked citations resolve**, exact receipt replay and re-render match, and there are zero hidden decision/disagreement references or never-say hits. Worker latencies were47.0/55.8/42.8seconds; polling wall times use a different clock. The template fallback cause is not exposed. This demonstrates narrow numeric provenance and successful job completion, not semantic accuracy, A1/A0 quality or an end-to-end verdict flow. Fresh Sol saved-output ACCEPT SHA-256 `9d9f72cb1eaa96bb4785fa4f17baa92779ab9a16ad93a6f734393e65dd2b9ec0`; all160manifest entries independently rehashed by root. Frozen manifest SHA-256 `89b4afbd5221be364589bc375784b032ed7cfdb5179e8b2e092d2f78f3a19c3d`.

C9 source/protocolv1b integrated at main d887982 after fresh repair ACCEPT. Root's13constructor/manifest/source tests pass, alongside the independently run synthetic suites. The real20-review/60A1/30A0population is not frozen, parser has no gold notes, and explanation quality remains unmeasured.


## 2026-10-28 01:54 UTC — Optional farm slots 07–09 permanently reserved before any creation

The fresh exhaustive seed audit (SHA-256 `b4d93c88ab646f5cfd6a938db0c6411d78ef523bded5a60027fcbbc92d35fe96`) and independent Sol source/cost and reservation review ACCEPT the three prospective blocks. All prior reservations, failed attempts, and nonfarm seed points remain included. No overlap or unknown reservation was found. Each entire closed 200-seed block below is now permanently consumed, including unused or failed attempts; never reuse it.

| Exact job | Closed seed block | State at reservation |
|---|---|---|
| `hrlab-20261027-r2-wfill07-a72c51e4` | [10300000,10300199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill08-a72c51e4` | [10310000,10310199] | RESERVED; not created |
| `hrlab-20261027-r2-wfill09-a72c51e4` | [10320000,10320199] | RESERVED; not created |

This records reservations only. N4 e1 has priority. Source/cost ACCEPT SHA-256 `fdb3d242b5903aad2cde72ede4ee5a3ed7e45eb7a758b3e2baae54c5385ffa26`; seed/card ACCEPT `44d2bfda89cf4b4b402043022c8c25a2d5fdc2d2d74900c436810f5d62dd3efd`. Each job uses one node, at most six hours, C1t1/hr_train, v3/sample64 logs, 20 games per chunk. No create or rank start at or after 02:00 UTC; cancellation 08:15 and hard end 08:30.

Owner-invoked addendum 6 replaces the expensive admission workflow with its two-command, two-minute check and explicit last-good-count fallback after one retry. Root remains the sole creator and charges every subsequent attempted job once. The cap is 25, or 27 only with no other active jobs. Source, exact name/output absence, armed deadlines, and science rules remain in force.


## 2026-10-28 02:04 UTC — e1 evaluation created; optional farms not created; product attempts preserved

Owner-invoked addendum6 replaces the prior expensive All-view/120-second admission process with two CLI commands and the stated last-good-count fallback. The successful current job list found10 HardRead Running and zero other active/unknown rows; the last complete01:47:46 inventory counted23 nodes. Root is the sole creator. The reviewed one-node `hrsft-20261028-e1-fixed400-91eaa7d3` was confirmed Starting, subsequently Queued; source/YAML command, one instance and18,000-second timeout are bound. Conservative reservation becomes24. This is evaluation-only, with scores quarantined pending the original R1 validation-selection commit; no new accuracy or battle result is read. Private receipts: `scratch_ds/codex/add6-manual-admission-20261028/`.

Optional farm slots07–09 were never submitted: watcher receipt schema/start-time formats caused pre-create stops, then the02:00 guard stopped execution. No attempted-create receipt or cloud create exists for them. Their whole blocks remain permanently reserved and unused. The isolated watcher was stopped after exact PID/start/argv verification; existing seven-target and N4 watchers remain. These failed preflight versions are retained; no retry after cutoff.

All seven earlier jobs were Running by01:04:57. First normal wfill01 c000 independently PASSES20 unique/decided rows, all40 nonempty v3/sample64 side files, zero return-code/pool/JSON/belief/summary errors. Archive SHA`e02d5231689ba8cfe3095ffef33348cbbebf569ca784b246cef397f9042c4fac`; independent review`c28f319218beb857257da28a55b597fc6b6ecd7747df09c1a1fe822c2a4737fb`. This is validity evidence only; no protected interim win read or terminal teacher-data acceptance.

C9 full79-archive derivation verified1,580 games/3,160 sides; the subsequent pinned83e099c CPU constructor stopped after1114.9s on a113-turn input exceeding the product limit100. Its64 partial files and raw stderr are preserved; no final selection manifest or model call. A prospective source repair follows the existing first-ten-successful ranked exception rule, with source-reproduced overlength witnesses.

The separately generated125-turn bot demo was sent once to the local API after explicit owner approval and returned HTTP422 for the same100-turn product limit. No review ID, parser, verdict or explanation call followed. This remains a failed showcase input, separate from the earlier0/3 empty-offer cases. Addendum6 D1 now has a prospective known-offered three-case VT1 cohort; it is not yet a product success, P3 rescue or semantic benchmark.

## 2026-10-28 03:05 UTC — Frozen explanation population and one real demo API flow

C9 source population is independently ACCEPTED at source `fb1015301859058b274e6ccc7f792daf19a7c9a4`: 20 distinct reviewed games, ten Draft-B crawl and ten hr_val farm, selected by the committed first-successful ranked rule. A source-reproduced 113-turn farm input exceeded the product100-turn limit and is retained as the one registered exception before selection continued. All20 review/replay/team/context closures bind; eleven offered-disagreement lists remain empty. A1 has60 items (Q1/Q2/Q3:20 each,30 per pool); A0 has30 (10 each,15 per pool). Independent complete source validation passed. Frozen file digests: selection `dc56c64a3f20af6bdf6424dc39b51cc7a41cd9af7aa583f0b2fc3289e2511e2a`, canonical manifest `e556265cef303c25c7bfa2ed47c3945be1ac5ad4f4087fe9122172344516c707`, A1 `a021d472b42368a3286f4a1e60ae38b0c261d0644ea7c1918bb19459035f6a88`, A0 `c232ca22765dad387b37360a6fb0d7f8eda3f1358ba455125b7cf602e4b37f43`; independent review `db8c48c53eafb1414759b0663e59e34c42b20e52bc9904617ff0afb808e960ae`. This accepts source population only: no model-quality score, A1/A0 activation, training-overlap clearance or training result. Safe manifest must be committed before recorded calls.

The prospectively bounded D1 discovery completed four ordinary current review requests and found one offered review (1/4). Exactly that one qualified case completed a normal partial API flow: one hypothetical pre-turn note parse, canonicalized chips, one new identity-bound verdict, and one Q3 explanation. Note parsing used the keyword backend, not Glimmer. Verdict settings are three independent seeds11/12/13, four worlds,60,000 iterations, margin0.08, engine reference; result Misplay, mean delta-U0.1585 and delta-R0.1592. Q3 completed as raw Glimmer in80.9 seconds with four of four citations resolving and zero non-offered/never-say references. This latency is one observation, not a benchmark distribution. All71 frozen evidence files independently rehashed; fresh Sol ACCEPT is one bounded technical API flow only. Original audit summary falsely marked its four references missing; direct trace checks resolve them, and an additive erratum preserves/corrects that derived field. Evidence manifest `e6148104c3cf181c1ee404e5f46c34df470e63c6ad715f433d3e3385fd02fe13`; actual flow summary `6a45f8197e0afe1b50542902ddb5b8bdd28087960862b6e1d673c85aa72411d5`.

This is **1/1 partial API flow**, not the required three-case D1/UI acceptance, semantic explanation accuracy, human verdict accuracy or a player’s own reasoning. Desktop/375px capture and age attestation remain pending. The earlier0/3 empty-offer cohort, failed125-turn input and D1v1 failure remain separate preserved attempts; no fifth discovery case or replacement under this card.

## 2026-10-28 03:05 UTC — e1 eligibility and evaluation infrastructure status

The full e1 producer is Completed with2,750 training steps/one epoch and all five rank0 stages successful. Serving measured400/400 legal responses, zero length finishes/errors, nearest-method p951.476s. This is serving eligibility, not battle win rate or teacher accuracy. Independent projection SHA `8f82e45547ceec9e425b93b42882ec71afa031656a3e06c9a1cfa03b5626f3fc`.

The separate fixed400 evaluation `hrsft-20261028-e1-fixed400-91eaa7d3` is Completed; scores remain unread under the original R1 validation-selection embargo. The first full-validation job `hrsft-20261028-e1-fullval-2d344a02` Failed in preflight with OSError; its named output was empty and teacher scoring did not start. Exact cause is unconfirmed because the handler hid errno. A mount-incompatible hard-link write is plausible. Source commit `122c005e01c7707c81670d12d3f51d43d0e1133c` replaces publication with exclusive no-overwrite creation and improves sanitized diagnostics; six tests and real CPU input preflight pass. One prospectively registered versioned retry retains identical data/model/scoring, one H100/at most18,000s,08:30 hard end. Final independent source/cost review `d61d544b3df263f07224698a07e491284ccefb3df05f3cb5540d16c56b67a541`; no new quality result is claimed.

## 2026-10-28 03:13 UTC — Reviewed source integrations and single e1 retry created

Root created the one authorized versioned validation retry `hrsft-20261028-e1-fullval-r2-f796ce5f`; Azure confirmed Starting on one instance. The failed original remains Failed and is not a measurement. Runtime source/YAML, committed card, original terminal state, exact absence of name/output, three watcher PID/start/argv identities and pending-creation audit were checked before create. P6 simple admission found10 active HardRead jobs and zero other active jobs; node-row states were not interpretable, so the authorized last-good23 plus conservatively held completedN4 one plus retry one yielded25 under the default cap. This is reservation/admission, not measured GPU utilization. Deadline08:30, cancellation08:15, max18,000s. First Running/preflight evidence remains pending; no score read.

Main `040b234` saves the independently reviewed W2 hosting prototype:25 exact source/test files plus reviewed README, deployment disabled, explicit private input/output/selector parameters. Root's relocated35-test CHECK passes. Source promotion review `8eb8da4166f84e52c07bd4485f3ce89a34b6b4cb40792b04bb0512aae1d43a51`. The5/10 local-process smoke remains5/10, with the other five rate-limited; no container smoke, hosted workflow, platform budget control or deployment is claimed.

Main `07d5441` integrates fullT1v4 after independent SOURCE ACCEPT `bb354e37c2789ef2e7b44e9e42e8b498623d39d73a7f3fd9568576fc4d3c0a53`: full BF16 tree, replay-derived target/dataset bytes, independently bound post-training checkpoint inventory. Root CHECK passes77 T1-agent tests plus16 SFT/Azure tests, including synthetic2,000-decision/16,000-slot lineage with1,500 verified targets and500 templates, and substituted weights/rehashed fake dataset rejections. These are synthetic source tests, not collected explanations, training samples, verifier rates or a trained adapter. All execution switches remain disabled. Real source/privacy/overlap/model/resource gates remain; T1 sampling/training follows sealed/C2 priority.

The safe C9 cohort digest manifest was committed privately at `ab96fa8` before all recorded model calls; recording card `7ea48fe` binds60A1/30A0, actual Mac provenance, serial EVAL_LOCK and the original manual owner-check requirements. Parser remains not run without gold notes.

## 2026-10-28 03:17 UTC — e1 retry preflight passed; A1 runtime mismatch preserved

Exact `hrsft-20261028-e1-fullval-r2-f796ce5f` was Running at03:13:35UTC on one instance. Its668-byte preflight receipt exists with2,666 paired records:2,428 action rows and238 read rows. This confirms the versioned writer passes the previously failed stage; it does not establish the original OSError cause or a completed scoring result. No quality score was read.

The A1 live preflight stopped before runner/model call, output reservation or EVAL_LOCK: handed-over server9794 is healthy and has all registered core flags, but its actual argv also contains alias/no-webui and generation defaults matching the protocol request row. The source validator permits only the core list and rejects these extras. Sol is reviewing a narrow pre-record implementation correction; no server restart, method waiver, provenance fabrication or A1 result is claimed.

## 2026-10-28 04:07 UTC — e1 full validation independently accepted; no selection yet

The completed one-retry job `hrsft-20261028-e1-fullval-r2-f796ce5f` yields **1,294/2,428 = 53.29489291598023% engine-target teacher-forced agreement**, mean policy cross-entropy **1.6187080494656885**, across128 distinct source-game prefixes. An independent fresh Sol rejoined all2,666 staged val/companion identities (2,428 action and238 read), checked the exact action-ID digest, unique teacher rows and legal predictions, and recomputed engine-policy argmax agreement and math.fsum CE. Played choices differ from engine targets on264 rows. These are fixed-split offline metrics, not battle win rate and not directly comparable with the played-choice fixed400/default evaluation; no row-independent confidence interval is claimed.

Source `122c005e01c7707c81670d12d3f51d43d0e1133c`; exact staged YAML `abe30691b362d13d48916a9fa10edaba2c4b51266ba401d5b9cd00b836bec013`. Independent review `c82900aa6ba1e6478e1b7fbd174d243e59c21ab5fd750e9c0bf3d3d1fdec7c54`, readout validation `a1af3f159f8c6e811498f731c7e8fd3ea6dcd1386a25e20def1764dc06a008e0`, terminal receipt `7db754373819cabbd1d8fe4a60f4a99b58f2a3320a06328bc15d13478e31fab6`. The failed first attempt remains a failed preflight. No R1 winner has been selected; e2/XL6 validation remains pending. Fixed400/default producer quality remains unread.

## 2026-10-28 04:07 UTC — C9 constructor identity repair; explanation scores still unmeasured

Both A1 launch attempts stopped before model calls, locks, outputs or traces. A capture assertion stopped the first; the second found that a reviewed server-provenance-only change to the evaluator file no longer matched its historical constructor-file pin. No frozen game/review/question was replaced. Main `f46cc82` separates actual constructorHEAD `fb1015301859058b274e6ccc7f792daf19a7c9a4`, review-code commit `a2328c90d7f1a6abed44b4ce031fbe52baf11566`, and current evaluator. For the exact existing v1 manifest only, all16 historical Git blobs are hash-verified, the other15 live source files remain checked, and current evaluator content/index/worktree must match cleanHEAD. Generic data-file verification is unchanged. Independent fresh review `6ab1615693e40fc72e18fe5941ca8a752df02499fe700c711286e485b4c65202`; root postcommit38tests and actual16/15/current-file checks pass. §1 implementation clarificationv1e is committed before any record; §3–5 populations, questions and metrics are unchanged.

The old corrected provenance bundle is preserved; a fresh absent r3 path is required for the next pre-call launch. A1/A0 semantic-quality, misleading-rate and Agentic eligibility remain unmeasured/pending.

OpenWeights artifact identity now has primary-source support: the captured disk file hash/size matches Meta's pinned official GGUF revision. Public-safe metadata is `ops/provenance/glimmer-official-gguf.json` (main `a8db207`), and independently reviewed PITCH§8 is updated. This proves captured disk-artifact identity, not resident-memory state, organizer verification or a prize. W2 Linux-engine/SDK dependency research did not find a cached install-free route; no new job, download or install was made. Hosting remains PARTIAL.

## 2026-10-28 04:17 UTC — A1 v1 entered recording; no quality readout yet

The third pre-call launch passed full source and live-provenance validation, acquired its own EVAL_LOCK at 04:12:55 UTC and reserved the registered aggregate/trace outputs exclusively. Its first of 60 frozen items completed at about 04:16 UTC. This is execution progress only; the full denominator, raw/after-repair/template rates, latency and misleading audit remain unmeasured. No interim quality gate is applied. The two earlier launches remain preserved failures before any model call.

Frozen manifest canonical digest e556265cef303c25c7bfa2ed47c3945be1ac5ad4f4087fe9122172344516c707; A1 items SHA a021d472b42368a3286f4a1e60ae38b0c261d0644ea7c1918bb19459035f6a88; fresh R3 provenance SHA 98344e8fa9f644db52682399f1f5ae1b17f5777f639fba1d078346b729811106. Scoped evaluator commit f46cc82; source review-code commit a2328c90d7f1a6abed44b4ce031fbe52baf11566 and constructor fb1015301859058b274e6ccc7f792daf19a7c9a4 remain distinct. Current Mac model/app identities and the complete observed argv/build/hash evidence are privately bound. No scoped source/model/server change during recording; no A0 start before the fresh root check and provenance bundle.

## 2026-10-28 04:19 UTC — T3 large student arena: incomplete, failure-inclusive readout

Exact job `hrarena-20261027-t3-s11-r1-e82f6c41` is terminal Completed, but the requested 200-game measurement is **INCOMPLETE / REJECTED**: 176 games completed, one timed out, and 23 scheduled games were skipped after the run's wall-time cap. The output contains all 200 unique scheduled indices and 100 reversed team pairs, of which only 87 pairs completed both games. Against Abyssal on partly seen `gen9/hr_hl_dev` development teams, the locked seed-11 large student won 156 and lost 20 completed games.

Under the prospectively registered 200-slot failure-inclusive rule, **156/200 = 78.00%**, Wilson 95% CI **71.7612–83.1835%**; every timeout and skip counts as a loss. Completed-only is 156/176 = 88.6364%, Wilson 83.0998–92.5223%, conditional on completion. The producer's 156/177 denominator excludes skips and is not the registered all-scheduled rate. These are not 200 played games, 100 completed pairs, an unseen-team claim, or a new win-rate gate. No inferential comparison with student v2 is made.

Indexed rows report 4,724 decisions, zero fallbacks/server rejections; the source summary reports zero illegal moves and p95 decision latency 7.977s. Raw policy logs were not fetched for this readout, so exact per-decision legality and p95 were not independently recomputed. The original three-seed offline accuracy FAIL (58.4155% versus 59.1243% required) and trained Mac latency FAIL (p50 21.2584ms versus 15ms) persist. Large stays shelved; selected student v2 remains unchanged.

Root checked receipt SHA a6bcc6d1e3945906054f5a3088fb43d0983941e28bf4089edf67200a3d60e94b and fresh independent gate review SHA b7e53d1c1c6db4edbf01eedffbbf7b1ad43d2d8092c021a1708941ab44627552. Review rederived outcomes, Wilson intervals, completion/pair structure and parity for all 2,134 staged entries, YAML, and locked checkpoint. Stage manifest SHA 34ea35323ce9ba19a5f60003400a86f74581ed72165d5c65f4ec9c3d8d28a8bc; checkpoint c5bf0cb7f99eca92457869ed56de5bedfd031ba9bc5d557b28b496c10a5f7031. No reroll or replacement run is authorized by this negative readout.

## 2026-10-28 04:52 UTC — Video science charts: four source-bound stills accepted for editing

`ops/video/science-charts-v1/` contains four editable SVG/PNG pairs, the locked seven-row input table, renderer CLI, public README and render manifest. These are video-production assets, not a rough cut or new AI results. The exact accepted RESULTS snapshot is f0b990f3b25eed8bae972db9240088849f363574, SHA ee063ca34cd54e59d56cffc20dc657b6653803a1faa8668e5554c6b3e342ac48. VoI, luck, selected-student and historical-Glimmer measurements remain separate; there is no v1 score or cross-population comparison.

Fresh Sol accepted source/statistics/geometry/IP in v3 but rejected implementation-first headlines for short muted shots (review c746fffe9a5cc81317873f9597445f9bbd62deac3fecfe39d693c00eabef4e1c). V4 implements the exact result-first copy and the explicit unplotted VoI-summary caveat; root inspected the actual quarter-frame contact. New public README/CLI received conditional ACCEPT 3a90e2f0faea950d42c4afeb81bb0c87bbd0557981c84622b89dbe1a42377850; root applied all four exact README fixes. Root's promoted CLI rebuild verified the source snapshot and produced all four PNGs byte-identical to the reviewed candidate. Rebuild manifest SHA79a7f02c6ab8364b6911e96ddc7c6e6bb9df197e3e88e8919e67686816643dd0; root promotion check cfd23464c47d531ba0b5bad90f8bfa6362af5deb9c709b505a0a9ebd6f74c21c. Final video captures, editing, captions and two-non-player comprehension test remain pending.


## 2026-10-28 05:22 UTC — Full T1 population timing repair: source-only acceptance

The existing full-T1 population loop would review every eligible game before choosing its2,000 offered decisions. An isolated repair now fixes game and within-game ordering before execution, stops on the deterministic required prefix, bounds concurrent reviews and CPU/deadline use, and preserves an immutable restart ledger. Source review rejected orphaned child processes, crash-publication recovery and duplicate-identity handling in v1; v2 repairs those three issues. Independent review `2e5fd5d6bf22c887e10781574ac8f6fd00e8b7b7edd66d7ab017219290857c48` conditionally accepted v2 with an exact conservative CPU-headroom correction. Root applied that correction: at least `3W+1` headroom and `7W+1` total CPU slots for W concurrent reviews. Forty focused synthetic tests plus one test covering six boundary cases pass. Frozen corrected patch SHA `50b38b0f89279972eb76069af2da76413b084bb186edd49281ef2b4da8a4559d`.

This is **source acceptance only**: the patch is not integrated or activated, no actual training population has been processed, and no GPU explanations or adapter exist. The owner-permitted mixed crawl/farm source extension is still being completed; its source review remains pending. A protected-input metadata identity remains unresolved, so actual source processing is held. All five execution switches stay false. No change to frozen A1/A0 inputs, the active Mac model or the running recording.


## 2026-10-28 05:37 UTC — Student v2.1 train-only source repair integrated; no training yet

The dormant student pipeline now preserves the original v2 development/test/evaluation cohorts while supporting a separately admitted extra-training population. Independent v6 SOURCE ACCEPT `6797698ba2c83ddaccfaf5d11173da9652088960717c841a25462eb3e8843e2d` closes the three v5 defects: the actual producer field is `fp_belief`; the exact historical identity index retains seven incomplete train and one incomplete validation records with `index_completeness_claim=false`; and archive admission enforces source-bound summary/seed/configuration/pool, ordinals0..19, ten swapped pairs and forty nonempty decision files. A future bounded eight-line historical metadata proof and conservative collision quarantine remain required; missing replay identities are not fabricated. The reviewed seed audit is reused.

Root applied the exact four-file patch `664044ca86ef0f7f85eccb0dc3aebb7847318e58f3a4e6e1953a5471a83ac7ca` and independently passed43 synthetic tests, shell syntax and whitespace checks. Existing A1 recording sources are unchanged. No actual farm archive,100,000-row admission, model training, new benchmark, cost authorization or job is established. The extra-data CLI/submission barriers and all runtime authorization fields remain disabled. The original gates remain at least100,000 new eligible deduplicated decisions, fixed-cohort top1 at least v2+1point, and200-game arena at least v2−3points. This repairs comparability; it does not improve a measured score.


## 2026-10-28 06:28 UTC — A1 base explanation record complete; semantic gate pending

The fixed, pre-call-frozen population completed all 60 questions on 20 reviews (10 Draft-B crawl and 10 hr_val farm), with exit code 0 at 06:19:21 UTC. Base Muse Glimmer-30B Q4_K_M passed the numeric/tool verifier directly on 36/60 answers (60.0%; Wilson 95% CI 47.37–71.43%) and after its permitted repair on 49/60 (81.67%; CI 70.08–89.44%). Eleven answers used templates (18.33%); there were zero server errors and zero additional product-wording replacements. These are verifier pass rates, not battle wins or semantic accuracy. Mean data-tool calls per answer 2.72, maximum 4. Under the exclusive-server lock, answer latency p50 129.002 s and p95 213.204 s; the maximum was 302.258 s.

Root recomputed the counts and ordered trace. Fresh independent Sol accepted the record for A0 preparation: all 60 frozen links, 80 selected review/context/replay/offered artifacts, five server-evidence refs and request/source/model settings match. Exactly 221 server slot launches and releases match the 221 A1 model requests; app logs gained zero bytes during recording. Private review SHA-256 `96c0724f9bcb26f25127b728fd9664cc609a1867cfa3616a1695fcec80ab3bde`; output SHA `7bf4845d08ff7f2a1c5bdffedb9399f15250d8598192e3b1ee6bc3367a991ea5`; trace SHA `71dcf7be3c405326f64f321d5aee02fb8ddea4dbd48bcc8a84eeb5412f77e0bd`. Evaluator scope remains `f46cc82`; frozen constructor `fb10153`; review source `a2328c9`.

The after-repair point estimate exceeds 80%, but the full ship gate is **PENDING**: the seeded blinded 60-sentence audit from 88 eligible sentences and the separate owner 15 checks have not been scored. A0 and its full numeric-claim/owner audit remain pending. No misleading-rate, semantic accuracy, Agentic qualification or complete quality PASS is claimed. Parser remains not run (no gold notes).


## 2026-10-28 06:40 UTC — A0 preflight failure preserved; corrected capture accepted

The first A0 wrapper ran 06:27:46–06:32:23 UTC and exited 1 during provenance validation, before acquiring a lock, reserving answer/receipt outputs or making any chat call. Its private capture omitted the required inline `evidence.model.model_path` reference; the saved model JSON path already matched the live argv, so an initial symlink diagnosis was incomplete. The failed bundle (SHA `65a7c85e7a33649327a652989dc54bc886e652066e1e4da2614b1978d7b2b954`) and all original evidence remain preserved. An unrun r2 proposal was rejected because it did not add that reference.

Fresh independent review accepted r3's minimal reference repair and versioned absent provenance path; the fixed 30 questions, source/model/request settings and exclusive no-overwrite answer paths remain unchanged. Capture source SHA `8d38287af63b1fe74f8eb459ad92e64ce32c77cf5fd9b7b6b8d9dc3c4c441c78`, wrapper SHA `9c8489d2596e2bbe01fc679de400c4e7dc13a5f271a539d08bf211d8d7b08f4c`. Root rechecked source, live identity and empty app queues, then started the reviewed wrapper at06:37:10. Its one fresh capture passed the existing provenance validator (verified06:39:24; bundle SHA `5872cf635eaacfe8eac5617d97da3c17ddbbe8c419df3e26fd62de34a43af4d3`). No A0 outcome or numeric-claim result is claimed until the complete run and registered annotations/owner checks.


## 2026-10-28 07:41 UTC — T1 bounded new-farm source and complete-receipt judge integrated (source only)

After the fixed A0 runner ended successfully at07:24:44UTC and released its lock, root integrated the five exact reviewed source patches: bounded deterministic population, the mixed-source guards, authenticated new-P6-only routing, and the complete scoped-receipt semantic judge. The mixed historical route remains held for its unresolved source identity; the independently accepted new-P6 route does not recover those payloads. The frozen combined rehearsal reproduced all22 changed-file hashes. Root’s59 focused synthetic bounded/mixed/new-P6/judge/preflight tests pass; whitespace and Python syntax checks pass. The earlier unexplained Mac process-group failure remains unresolved and prevents runtime inference from these synthetic checks.

All execution switches remain disabled. No real source census, T1 engine review, model sampling, judging, training, source-quality claim or new GPU job is established. The formal T1 method is unapproved; the reviewed draft’s two chronology rejections are preserved, with a corrected private draft offered to the owner. A separate final-target gate requiring at least1,500 distinct selected model candidates passing both raw verification and semantic judging is staged for independent source review. The current raw-only feasibility count does not satisfy that future training gate. Linux process/runtime proof, source admission, complete exclusion and selected-ID bindings, BF16/model/judge provenance, owner method approval, and fresh priority/cost/admission are still required.


## 2026-10-28 07:43 UTC — A0 v1 recording complete; numeric-claim audit pending

The fixed no-engine base-Glimmer A0 completed30/30 at07:24:44UTC with exit0, no server errors, and all answers nonempty with finish_reason=stop. Root verified all21 recorded integrity checks, ordered question/replay/review/side identities, exact immutable answer/receipt and provenance hashes, and unchanged frozen source/input bytes. A separate terminal continuity check verified the five evidence files, unchanged server/app identities, zero app-log growth, idle app health and exactly30 paired server task launches/releases. Answer SHA256 `748dd032fa6c46652ba5bbc4e1b49e50970c71687c3e3e984054d50cf1374238`; runner receipt SHA256 `c99e6959eeeb6bf70812d342d55bc8dc2328d41a96949b10d95d0ada70111f8c`.

This accepts recording integrity only. Full-answer annotation has found written-number and range expressions that the current scalar/digit-only scorer cannot represent; these stay explicitly unresolved. Ten whole-answer owner spot-checks remain required. Their seed10 selection was saved before any answer inspection. No A0 checkable-share or within-five-point percentage is reported. No answer is regenerated, dropped, silently relabelled, or assigned a midpoint. Parser evaluation remains not run (no gold notes).

## 2026-10-28 07:43 UTC — e2 terminal training failure; R1 selection remains pending

Producer `hrsft-20261027-v1e2-bfef` is Azure Completed, but its own training stage is `failed` after47,392.3seconds with a timeout. Preflight, teacher evaluation, conversion and serving finished successfully. The permitted serving projection shows400/400 legal outputs, zero finish-length failures and p951.473seconds. Fresh Sol eligibility review applies the original18:52UTC all-stages-ok rule: e2 is INELIGIBLE for R1, and a full-validation job is not required for that exclusion. Checkpoints remain preserved; no conclusion about their accuracy follows. Review SHA256 `5371629a57304a9f14dcdab9d5e3d8ec1fcdf4051ba95470695efb159b09f1a3`.

XL6 training reports `ok` after5,668 of5,959 nominal steps; this is a budget-stopped partial epoch, never a completed full epoch. The original rule permits assessing a transparently disclosed partial adapter after all remaining own stages and serving gates pass. Its eligibility and complete validation remain pending. No R1 winner has been selected and no producer-default or fixed-v0 comparison accuracy/top3/CE has been read. The original full-validation-before-test embargo remains in force; missing time is reported rather than waiving it.


## 2026-10-28 07:47 UTC — T1 final-target BOTH gate accepted and integrated (disabled source)

Terra implemented the prospective final-target admission gate under Sol’s plan; fresh independent source review ACCEPTED the exact incremental patch `f2c06558b05e6cdc94e30b35e5632f300cdaf9bdb10cacb3b6c62322a8691849`. It replays complete candidate/judge evidence and requires at least1,500 distinct selected model targets passing both zero-repair verification and semantic judging; templates count zero. Raw-only sampling feasibility remains a separate diagnostic. The prospective rule/code pins are checked before sampling; the actual completed target count is checked before dataset promotion/training, without requiring future targets before sampling.

The independent disposable-tree check passed21 focused tests and a full synthetic2,000×8 lineage replay: raw1,500 with only1,499 BOTH targets correctly blocks promotion; exactly1,500 BOTH plus500 templates passes. Forged/rehashed counts and alternative-slot substitutions fail rederivation. Root verified16 baseline and10 resulting source hashes, integrated those exact bytes after A0 ended, and passed19 focused combined tests. Review SHA256 `78046747bce10c4c98c06be6d17959960529c9461a075d110def5a88daf8174d`. All seven execution switches and training remain disabled; owner method approval, source admission, actual counts, Linux process/model/judge proofs, prior process-group incident, and fresh resource/cost/priority gates remain unresolved. No real data/model/GPU call or improved score follows from this source acceptance.


## 2026-10-28 07:55 UTC — Six night farms terminal Failed; valid-chunk census is incomplete

Exact job checks show all six night farms Failed. Metadata-only artifact/census checks contain106 complete archives:100 nonquarantined and6 quarantined, zero partial archives. Farms2/4 retain FAIL_INVALID; structural totals are5 missing return codes,4 nonzero return codes and6 missing decision files. Every census is finalized=false with incomplete coverage, so no complete eligible C1t1 action count or training admission is established. Each farm configured21,600seconds; permitted job metadata exposes no error, exitcode or start/end time, so timeout versus application/postpack failure remains UNKNOWN. The jobs were Running at06:24:47 and Failed by07:50:17. Source inspection confirms finalization follows active-marker removal; metadata does not prove why finalization was missed. No raw log/archive was opened for diagnosis. Failure receipt SHA256 `ca9ced6a99396654ea5e5de8f51deb7565d5339ae53ef3f80dfdb28dd45ef1f3`; terminal snapshot SHA256 `6c5bb78e31f9d98eb92a13ef59247377b20fc50c659de7939fe0656f9e65df01`. No late replacement farm is launched; source admission and any post-sealed repair remain separate work.

## 2026-10-28 07:55 UTC — Synthetic demo source batch2/3; separate regression fixtures fixed

The preselected three-slot source batch ran07:41:06–07:50:38UTC within its75-minute cap. Slot1 failed before a battle because its sandboxed Showdown process could not bind the local port; it is preserved without retry. With narrow local-bind escalation, fixed slots2/3 exited0, completed13/16turns and passed their original10–100-turn Gen9OU6+6/public-log/own-p2-export checks. Root verified all73 frozen artifact hashes/sizes and the byte-identical copies. This source cohort is2/3, not3/3. Summary SHA256 `91570cf8b287f94fa9b28098b590a05ad4bbf37a7de4d544ba5de357fc40c11d`.

A distinct prospective regression/video fixture plan fixes the earlier qualified case01 plus these two new source games before any new app/model call. It requires a direct current offered disagreement and first-ordered-key/prefix-only note per new review; no replacement if ineligible. The earlier completed case is reused, not a new discovery success. Root granted conditional ordinary local API-flow GO only after source checks and normal health/rate/lock preflight. The original failed source cohort and previous D1-v1/v3/P3/A1 denominators remain unchanged. API results and UI desktop/375px acceptance are pending; no age attestation or scientific/human-efficacy claim follows. Plan SHA256 `6c172ac57060b77cae1bf99569336cf9f02755c327915793428739402d67e7e7`.


## 2026-10-28 08:03 UTC — XL6 and bold terminal; quality scores remain unopened

Safe terminal projections show XL6 and bold Azure Completed with all five own stages ok. XL6 reports 5,668 of 5,959 nominal steps: disclose a budget-stopped partial epoch. Its serving projection has 400/400 legal outputs, zero finish-length failures and p95 1.476seconds. Bold reports 3,517 steps and one epoch requested; completed-epoch evidence is absent, so no full-epoch claim is made. Its serving projection has 400/400 legal outputs, zero finish-length failures and p95 1.49seconds. These are pipeline/serving counters only.

XL6 adapter persistence binding and complete v1 validation remain pending; observed full-validation duration has no safe demonstrated fit before the night evaluation’s08:15 cancellation. The original e1/e2/XL candidate set and val-before-test rule are unchanged. No R1 selection or protected/default/fixed400 quality result is read or claimed. Bold remains separately scoped, with its conditional continuation decision pending the authorized comparison. All known night-wave jobs are now terminal; no late farm replacement is submitted. XL stage projection SHA256 `307aaf4558e980856bd5b5084513c044fb35a43e4a1d4f53b476b8aeb704f570`; serving SHA256 `1cc4d4a5a9e9bae9adecef31c0b0956bd42c559314c54b7e0c243cd175e9455a`. Bold stage SHA256 `f6d94a37bbff0a53831d7526a5c75c94cb99974c4e06939457e48dc61116d89d`; serving SHA256 `1714b14bc54c92e2229e188785f7eedb6ebcbe98533b8ad9199fae7a000dbf0b`.


## 2026-10-28 11:01:27 UTC — A4 ratified freeze and sealed pool provenance (C11; pre-submission)

The owner ratified the exact offered A4 record. At 2026-10-28T11:00:12.543131+00:00, the unchanged reviewed wiring was committed as `088d04bf5068cd8d3421c6b4ff60503dfdea7e72` (tree `0caa909465249ae358540cb648c99310e44dd72a`), then A4 was appended without changing prior bytes and committed as `3ee88fde16d06869591f3f7b8668c7800efbdde6`. Main annotated `freeze-v1` object `85b4b588cbfcc91fe6c96e15611c5a010c76f6e9` targets that A4 commit; fork tag object `cf4b23672fd1938386d109561ce328e2f2d47d86` targets frozen code `f061e63d3d588f30e72af2f4a7d12d3101896d02`. Both tags were verified before the first sealed-byte build. This meets the Wednesday noon Paris time barrier and records actual owner ratification, not an unanswered default.

The one post-A4 pool build completed at 2026-10-28T11:00:19.260161+00:00: **1,000 teams**, stored content SHA-256 verified as `d60e036d2a4c608b7ce3fded7522ebc83456a63fc4592bc66ad3c554c7fb6311`; actual tarball SHA-256 **`7304a879fd69501d2afd4aa0e8905c9d296d97cae67e4443362a2a595c896874`**. No local extraction or set inspection occurred. All 25 separate code snapshots/YAMLs were rendered and hash-bound while HEAD remained the exact A4 commit, before subsequent source changes. Every snapshot binds that A4 commit, frozen fork, this tarball and no student/belief checkpoint. Private freeze/build/render receipts are under `scratch_ds/codex/c11/root-freeze-20261028/`.

The reviewed primary is 25 one-node jobs ×20 chunks ×10 side-swapped pairings =10,000 games, maximum300 H100 node-hours. At this record no real sealed job has been created, so S3.1 is not complete. Admission, exact-name/output absence and a live identity-bound watcher's complete initial pending poll remain required. Only chunk/marker validity counters may be read until all500 chunks and replacements finish; no protected outcome has been read.


## 2026-10-28 11:02:46 UTC — F1 complete validation and selection: XL6 (owner addendum7)

**Prospective method:** the owner-directed e1/XL6/bold candidate set and rule were committed in ops at09:47UTC (`fced085`), before either new validation call or quality read. Card SHA-256 `2590bbd9798382d109ecdd5b4660f0623b5fb7208214dc5d118683b553e47674`: maximum unrounded engine-target top-1 on the same2,428 action identities, then minimum math.fsum mean cross-entropy, then e1→XL6→bold. The original08:30 R1 selection deadline was **missed**; this is the later addendum7 F1 selection. E2's saved partial-second-epoch checkpoint remains eligible for separately disclosed validation but unresolved/unvalidated in this immediate three-candidate set.

| Candidate | Engine-target agreement | Mean policy cross-entropy | Status |
|---|---:|---:|---|
| e1 |1,294/2,428 =53.29489291598023%|1.6187080494656885|Comparable accepted baseline|
| XL6 |1,379/2,428 =56.79571663920923%|1.5677069632737148|**SELECTED**, highest unrounded agreement|
| bold |568/2,428 =23.393739703459637%|1.9314002983196616|Not selected|

Each new job Completed, with identical2,666 paired identities (2,428 action,238 read), exact action-ID digest `95b5abff823805f5082ac1a325c944deeb8b7cdb92e3a4293d1915375c7cba72`, legal predictions, finite CE and independent argmax/CE recomputation. There are128 game prefixes; played choices differ from engine targets on264 rows. These are correlated offline engine-target metrics, **not battle win percentages**; no row-independent CI or p is claimed. XL6 stopped after5,668/5,959 planned steps (partial epoch). Bold's3,517 steps do not prove a full epoch.

Selected XL6 adapter weights SHA-256 `b2d55ffde079ef484c349d6fa2201e211f603b9604716f3623a6f5cddf4e6d28`, config `8ea2aaffe2c309092767f57cbaa39dbd704e092f733999c4e2e25c5f2dc965af`. Actual jobs: `hrsft-20261028-xl6-fullval-f1-70a4c2e1`, `hrsft-20261028-bold-fullval-f1-9b6e0f34`. Independent Sol **ACCEPT** SHA-256 `af2b3b80d8989cec801fded1d2e845ed1cfd9383b8058c84682a7e389689128b`. XL result/rows digests `13e02fba3465ebc743b38152838da945eb36671cb6c2d7ee7cbca39eede89cf9` / `72fef924ab56cbc5a53e920e9987d70f5a96c1356e4b86bebebe65de9d2b6c5d`; bold `47b75d68b6de2fc44ae0d9867a301434e9284619b0bbf053a205ade92edba522` / `de209d9fcabfeee78b3ba16182739a81aa9386bd231a4998ff1d714b258b58aa`.

**Selection barrier:** no fixed-400 or default-producer quality output has been opened. This selection is committed before those reads; later test results cannot change this winner. Full25 sealed source packages were rendered before this main-documentation change.


## 2026-10-28 11:30:01 UTC — S1 fixed-v0 400 readout after committed F1 selection

Selection main`0f8904c` preceded every new fixed-v0/producer quality read. Independent Sol **ACCEPT** SHA-256 `94f8cd744729d6eb0cec2fac5cefb80022676ff5489d6a047ab0ab6394544446` verifies identical400unique action identities/order and legal teacher predictions for exact e1N4 and bold producer snapshots. Both use fixed-v0 evalSHA `3b0ba8fa…e06276`, evaluatorSHA `60abc879…3137` and selected400-ID LF-join digest `dcf05ed8…fde9` (full hashes in private source receipt).

| Model/version | Fixed-v0 top-1 | Top-3 | Mean CE |
|---|---:|---:|---:|
| e1, exact N4 job |200/400=50.00%|331/400=82.75%|1.6327556288156122|
| bold, producer evaluation |73/400=18.25%|238/400=59.50%|1.9369344775662443|

These are descriptive same-fixed-row teacher-forced results, separate from the2,428-row F1 engine-target agreement. Historical same-fixed-v0 references remain v0-safe39.5%, v0-ddp checkpoint1400 44.0%, v0b checkpoint1400 43.0%, v0ddpres full47.0%. No test-driven reselection: XL6 remains the validation-selected model.

**XL6 fixed-v0 remains PENDING.** Its producer default uses different v1 evalSHA `7ea29968…abb10`; recorded rounded default400 top1=.5425, top3=.8800, CE=1.6117 must be labelled separate and cannot fill the fixed-v0 comparison. E2's last-checkpoint fixed-v0 result is also missing. A separately reviewed one-node selected-XL fixed-v0 job is being staged, with no new score inferred.

Exact e1N4 job `hrsft-20261028-e1-fixed400-91eaa7d3`: teacher resultSHA `eebc73769e6a2d0140bff3f98bccd2986d104140950670008d6d876efee78336`, rowsSHA `f3bf2908571dddee033aa1ab0f1f4ed7aaa5347d15695dcd396bd06ab9f29d3b`. Bold `hrsft-20261027-v1bold-d3cb`: resultSHA `310f653627a697014728a6bf78571b91aab56428f2e24633c36be5215904a15f`, rowsSHA `e7a72665090b632d84c43fb2d08cf7c3a1afa5978ea4f963bd7c77bbc3bddd9c`. Artifact bytes were acquired privately under standing own-job approval; their identities matched before/after retrieval.


## 2026-10-28 12:12 UTC — Sealed technical launch acceptance; primary incomplete (C11/S3.1)

All25 exact primary jobs were observed Running after their11:05–11:09 creates. First c000 direct-counter checks are valid for25/25 archives, with positive treatment markers, zero fallback warnings and20 records per chunk:500 records,498 decided. Independent technical/provenance review ACCEPT is `scratch_ds/codex/c11/first-chunk-provenance-review.private.md`; counter summary SHA `f3b2d4976fbce648d2cac37feaf2547d9889612917157dd3a02b220b9d4f7de3`. A4/tag/build/package/create chronology remains verified against the earlier freeze record.

The final mechanical S3.1 signal is now saved: `scratch_ds/codex/c11/root-freeze-20261028/sealed-followup-counts.private.json` at11:58:58UTC reports **25 Running,59/500 chunks** and explicitly computes no estimate before completion. Root accepts the technical freeze/launch step; full primary analysis is still gated on all500 required chunks and replacements. Counts are not wins or a strength result. No protected interim outcome analysis was performed. OTS calendar confirmation remains pending.

## 2026-10-28 12:12 UTC — Linux synthetic probe failed before runtime proof

Own one-node job `hrt1probe-20261028-linux-7846756a` failed at import/startup: `ModuleNotFoundError: poke_engine`, line11. Exact1021-byte stdlog SHA `9c7c975433f73c530bba1f2d09bf82ca0393d1b8122e6dbe3ce4ddccb0c0824a` is retained under `scratch_ds/codex/training-roadmap/t1-linux-probe/terminal-diagnostics-20261028T115905Z/`. No model/data was mounted, no real review occurred and no valid runtime proof resulted. The source acceptance remains distinct from this runtime failure. No automatic retry or install; a finite plan using an already approved runtime is being checked.


<a id="s1-fixed-v0-final-20261028"></a>

## 2026-10-28 12:36:53 UTC — Selected XL6 fixed-v0 final readout (S1)

The validation-selected XL6 adapter was evaluated after selection commit0f8904c on the same400 fixed-v0 action identities used by e1 and bold. Exact job `hrsft-20261028-s1-xl6-fixed400-dcf05ed8` Completed. Root recomputed400 unique ordered IDs, **223/400 top-1(55.75%)**, **336/400 top-3(84.00%)**, mean CE **1.593645290891526** using math.fsum. Selected-ID LF-join SHA `dcf05ed82eaf7b487df1741f27c091c581aa65aa1af6869fb1153bd44225c545` matches the registered fixed-v0 cohort. Eval/evaluator source pins match the earlier S1 record. Independent readout ACCEPT SHA `e7fc41d14a6c4705cb33a3dbc96d1272c470e0945e85d7f7dde35adc53ed5bd6`.

| Same fixed-v0 cohort | Top-1 | Top-3 | Mean CE |
|---|---:|---:|---:|
| e1 |200/400(50.00%)|331/400(82.75%)|1.6327556288156122|
| XL6, selected |223/400(55.75%)|336/400(84.00%)|1.593645290891526|
| bold |73/400(18.25%)|238/400(59.50%)|1.9369344775662443|

These are teacher-forced fixed-test metrics; they are separate from battle wins and from the2428-position engine-target validation used for selection. Historical fixed-v0 v0ddpres remains47.0%; XL6's different producer-default-v1 result remains separately labelled. E2 checkpoint fixed-v0 is pending. No test-driven model reselection. XL6 output JSON SHA `69e6b01ba6deda535737d2cff4f6629e8e47379f7701349768c8706be3714085`; rows SHA `93b4408cf83fad5c9ec9625dee614315878f1d72a4eea84762851a34cab45e99`.

<a id="student-v2-local-foul-play-matched40-20261028"></a>

## 2026-10-28 12:36:53 UTC — Student v2 against Foul Play: terminal local40-game arm

The prospectively fixed local benchmark ran40 scheduled identities,20 side-swapped team pairings, team seed6200, with20 games per treatment seat. Opponent is untreated Foul Play F1 at100ms/search,2parallel searches,1thread. Team pool is partly seen; Showdown event RNG is uncontrolled. Student source NPZ SHA `a3f1b4b3c1cc15a4f812a0a39d9c2e4e99246dfef9ae7f556287237bea758d09`. This is a separate protocol from the earlier200-game Abyssal arena and early-student7/40 Foul Play pilot.

All40 identities launched;39 completed. Raw outcomes: **13wins,0draws,26losses,1unknown**. Fixture0 failed after model load because the harness used a relative policy-log path; original artifacts were preserved and no fixture was replayed. Under the prospective launched-failure-as-loss rule, primary **13/40=32.5%**, Wilson95% **[20.0843%,47.9825%]**. The completed-only decisive secondary is **13/39=33.3333%**. The unknown is a harness failure, not an observed battle defeat.

Across1660 observed Student decisions, zero fallback, server rejection, illegal action or invalid policy rows; nearest-rank policy latency p50 **0.024s**, p95 **0.082s**. These timings belong to this local protocol. Root reproduced counts from the immutable continuation ledger SHA `73e68d3372490cc47bbea35906bf1f02878278094b998df95b57846931a196a4`. Independent terminal readout ACCEPT SHA `619540657cbe8fe593c581dacc48d9dca3f57c7e9a131b82ed179d2eb0fef351`; counts SHA `4386cee26353da2c9d55f4a217310fef96f79a14e397683984149c39c4ab8def`. No XL6 comparison result or paired difference is available yet.


## 2026-10-28 13:24:53 UTC — Addendum8 semantic source acceptance and DATA_URI preparation

Integrated semantic source commit `b07b0d5` distinguishes attributed `luck_pts` from counterfactual `swing_pts`, preserves exact cited field/value/sign, and rejects the three reconstructed misleading claims. Root checks passed:53 verifier/template tests,10 receipt tests,3 semantic methods covering3 original cases plus10 prespecified synthetics and a bare-swing case. Independent source ACCEPT SHA `8b4d371bfe0740efd880c04556c1f5bba5289e65a181886fe404cbc08b1a3896`. These are source regressions, not a new A1 quality score; v1 labels12/60 remain unchanged. Templates/noAgentic remain the product policy.

Integrated DATA_URI commit `b59f533` provides optional read-only mounted SFT data, with four exact file hashes verified on each node before the unchanged trainer. Root5 unittest methods passed; the independent source/cost review records19 synthetic scenarios, SHA `70a26879737e96e0bf69f68a560d36ec5bdf7487dd51ef8aa2033a7f4cee4c10`. No real dataset has been admitted by this source change. All-data identity/filter/overlap checks, frozen v1val/eval, remote readback and mandatory2k-row one-step smoke remain required before full training.

Both standalone Linux probes Failed. The first lacked poke_engine; the second rejected its private ledger at the directory/0700 check (exact stdlog SHA `99d086d965b523b47d1185d6f00239fffa1609acfa28b289e625e999da49cda9`). Artifact-mount mode behavior is a plausible explanation, not proven by this exception. No automatic third probe. Any conditional realT1 uses localPOSIX0700journals plus actual same-job proof.


## 2026-10-28 13:24:53 UTC — F2 farm termination triage; raw counts are not eligible training rows

Independent original analysis ACCEPT SHA `f16fb64d2ee50f6635a20c9bac55f997e57c88cb83edbea5620edd5f9b322a73`. The six outer guards propagated parent SIGTERM(rc143); exact signal times follow.

| Owned job | AML Start UTC | Parent-signal TERM sent UTC | Start + 21,600 s UTC | Difference | Direct cause evidence |
|---|---|---|---|---:|---|
| `hrlab-20261027-r2-wfill01-a72c51e4` | 00:59:48 | 06:59:33 | 06:59:48 | −15 s | `parent_signal_term_sent`; owned group stopped 06:59:34, rc 143 |
| `hrlab-20261027-r2-wfill02-a72c51e4` | 00:59:55 | 06:59:42 | 06:59:55 | −13 s | parent signal; stopped 06:59:42, rc 143 |
| `hrlab-20261027-r2-wfill03-a72c51e4` | 01:00:14 | 07:00:02 | 07:00:14 | −12 s | parent signal; stopped 07:00:02, rc 143 |
| `hrlab-20261027-r2-wfill04-a72c51e4` | 01:00:19 | 07:00:07 | 07:00:19 | −12 s | parent signal; stopped 07:00:07, rc 143 |
| `hrlab-20261027-r2-wfill05-a72c51e4` | 01:00:27 | 07:00:14 | 07:00:27 | −13 s | parent signal; stopped 07:00:14, rc 143 |
| `hrlab-20261027-r2-wfill06-a72c51e4` | 01:04:20 | 07:04:07 | 07:04:20 | −13 s | parent signal; stopped 07:04:07, rc 143 |

The guard's scheduled absolute TERM was08:25, the chunk stop-before-next deadline08:10, and watcher cancellation08:15. The watcher recorded zero cancel requests and all targets terminal07:05:24. None explains these06:59–07:04 parent signals. AML end timestamps31–99seconds after nominal timeout are compatible with platform timeout; the12–15second early signals and absent platform reason do not establish the upstream sender. Cause remains **UNKNOWN**, not confirmed planned termination. Slot01 signal06:59:33 differs from owned-group stop06:59:34.

Source census:100 independently complete valid archives,2000 decided game rows,six invalid archives quarantined,zero partial archives,no finalized in-job POSTPACK_FINAL. Hash-bound fetch covers the100 expected archives. Raw172815 decision rows remain **eligible teacher count UNKNOWN** until the prospective source/holdout/policy/serialization admission audit. No >=100k student-data readiness claim. No protected outcomes were read for this triage.


<a id="s1-e2-last-fixed400-20261028"></a>

## 2026-10-28 13:35:46 UTC — E2 partial-second-epoch checkpoint on the same fixed-v0 test400

Exact job `hrsft-20261028-s1-e2last-fixed400-dcf05ed8` Completed; watcher observed13:15:42 and all-terminal13:15:44. The highest persisted `checkpoint-5500` is a **partial second epoch**, not a final/completed-epoch result; its original training timeout remains recorded. The comparison does not change validation selection of XL6.

| Same fixed-v0 cohort | Teacher-forced hard played-choice top1 | Top3 | Policy-argmax agreement | Mean CE vs policy |
|---|---:|---:|---:|---:|
|E2 checkpoint5500|212/400=53.00%|340/400=85.00%|218/400=54.50%|1.599177601729603|
|Base same400 prefix|101/400=25.25%|228/400=57.00%|109/400=27.25%|3.35584456830693|

Independent analysis ACCEPT SHA `e441f7490574fb91b01b2163d0d500b6bde159bc8952482e754e22c91fe13abe`:400 unique ordered action IDs, registered LF-join digest `dcf05ed82eaf7b487df1741f27c091c581aa65aa1af6869fb1153bd44225c545`, fixed eval SHA `3b0ba8fa2e2db60b9149d4ac03f1ae5bcbcb152745c4fa8fe1bf54fcc5e06276`, evaluator SHA `60abc879…3137`, all14 staged source hashes and4 checkpoint/tokenizer hashes match. Both preflight/eval_teacher stages pass; raw rows independently reproduce summary. Mechanical receipt SHA `f6841f3e0d04a70e8059c66d242020f6575a075b8360e34718e17738dec1a345`; only3 small allowlisted result artifacts(227723bytes) acquired under standing own-job approval.

Teacher-forced top1 and policy-argmax agreement are distinct. Mean legal token-probability mass0.999933358431 is not serving-action legality: generation was disabled(n=0), no serving/arena result follows. Same fixed-v0 hard-token references remain e150.00%,XL655.75%,bold18.25%,historical fullv047.0%. These hard-label numbers are not battle wins or the2428-row validation engine-target agreement. No test-driven reselection.


<a id="d1-template-ui-20261028"></a>

## 2026-10-28 15:23:23 UTC — D1 accepted for three selected template UI flows

Independent original D1 §3 readout **ACCEPT**, review SHA256 `9cfbd4b56a46cd8d21a5f0d3510de69cc88602d7efdcda609df0ec097e568b21`. Scope: three selected engineering demonstration flows under CODEX_PROMPT_6 §3 and addendum8 template fallback; not a blind quality rate, unseen-game claim, A1/Agentic ship PASS or repair of the old fixed cohort's 2/3 result. Prospective execution CARD main `c3bb0ac`; reviewed source `7dcfeaa` active in owned local app69204.

All three exact public-log/P2-team/frozen-note paths reach paste → bridge → offered disagreement → note → chips → three-run verdict → Q3 template and clickable source list, with zero selected app crashes. Frozen targets11:0,4:0,5:1 remain unchanged. Verdict settings: seeds11–13,4 worlds,60000iterations,margin0.08. Case01/03 generic expected-switch copy reports only top-three membership and explicitly says aggregate any-switch probability is unavailable; no derived aggregate probability or upper bound. Case05 is confidence-only with no testable opponent read. No counterfactual replay claim.

Evidence manifest SHA256 `039a9549d855eb0f8d88fedf190faefa40bc9756b66c9b6031334445534d2b1c` binds42PNG +42paired metadata at actual375/1366px, zero horizontal overflow. Three exact UI-linked verdict GETs are200 with matching review/target/identity. Independent pure-input normalization reproduces all cached pseudonymized replay.JSON and trimmed-team bytes and receipt hashes; raw public-log and JSON hashes are different domains. All24 clicked Q3 citations use the first offered key,12 numeric citations match direct receipts,0 non-offered citations and0 Q1/Q3 never-say matches.

| Selected case | Offered turn:key | Q3 direct numeric facts (turn / gap points / best WP% / chosen WP%) |
|---|---|---|
|case01|11:0|11 /9 /50 /41|
|case03|4:0|4 /13 /50 /37|
|case05|5:1|5 /8 /38 /30|

Original case03 step03 full-page screenshots obscure the note first line under fixed navigation; these remain unchanged and cannot alone prove the paste. The accepted camera-only supplement SHA256 `797caa1d9d105226d458463ae101e9874acf073c380be139c6e57809e4991dc6` supplies two readable375/1366frames with exact frozen input, contemporaneous AX/DOM value and note fully below the header. Only scroll/focus/camera followed this literal paste, no parse/model/engine/verdict rerun. The earlier mutable-helper mixed/overwritten image attempts are excluded, with a retained deviation receipt. Old empty-offer failures, fixed synthetic2/3, selected API parser recovery and pre-fix semantic failure remain historical. This is a scoped product demonstration success using templates; original A1 flags/owner checks and new A1v2 are separate.


<a id="student-v2-xl6-foul-play-matched40-20261028"></a>

## 2026-10-28 — Student v2 and selected XL6 versus Foul Play F1: accepted matched local readout

The accepted failure-inclusive comparison covers the same 40 prospectively scheduled fixtures in each arm, arranged as 20 side-swapped matched team blocks with team seed 6200. The opponent was untreated Foul Play F1 at 100 ms, two parallel searches, one thread. Teams were partly seen; battle-event RNG was uncontrolled. The Student arm here is the same 40-game outcome set already recorded in the standalone terminal Student v2 section above, not a second Student sample.

| Arm | Launched | Raw W / D / L / unknown | Registered primary compliant W/40 | Wilson 95% |
|---|---:|---:|---:|---:|
| Student v2 | 40/40 | 13 / 0 / 26 / 1 | **13/40 = 32.5%** | 20.08%–47.98% |
| Selected Glimmer XL6 | 40/40 | 10 / 0 / 28 / 2 | **8/40 = 20.0%** | 10.50%–34.76% |

The registered paired Student-minus-XL6 difference is **+12.5 percentage points**. Resampling the 20 matched team blocks while keeping each block's two swapped-seat games together, with 10,000 percentile-bootstrap samples and PRNG seed 6200, gives **95% interval [0, 25] percentage points**. The interval includes zero. Event RNG was uncontrolled across arms; the sequential local runs also had differing Mac resource load, including overlapping local model/CPU work and I/O during XL6. This is a failure-inclusive matched local result, not proof of model superiority or a controlled speed comparison.

Student launched all 40 fixtures and completed 39: 13 wins, 0 draws, 26 losses, and one unknown after the original harness failed at its policy-log path. That fixture is a primary nonwin; it was not replayed. Student's completed-only 13/39 = 33.3% is secondary. Its 39 completed policy logs contain 1,660 decisions, zero fallback, rejection, illegal-action, or invalid-policy rows; nearest-rank p50/p95 latency is 0.024/0.082 s. The registered run had one original harness failure.

XL6 launched all 40 fixtures and completed 38: 10 raw wins, 0 draws, 28 losses, and two unknown. Its failure-inclusive primary result is 8/40 after 15 primary nonwins were designated noncompliant: nine initial-harness fixtures and six later fallback/timeout-contaminated fixtures. XL6's 10/38 raw wins include noncompliant games; its 25 compliant completed games contain 8 wins (32.0%). The compliant-only 8/25 and Student's 13/39 are exploratory subsets with different failure filters; they are nonrandom and do not support a strength ranking.

Across all 40 preserved XL6 policy logs, the complete attempted-decision audit is 1,371 decisions and 223 fallbacks (208 serializer errors and 15 request timeouts), with zero observed illegal actions or server rejections; nearest-rank p50/p95 is 8.010/10.994 s. The ledger-only view remains explicitly separate at 1,357 decisions and 209 fallbacks, with p50/p95 8.023/11.133 s. The all-log audit includes 14 additional decisions from the interrupted original index-8 policy log, all serializer fallbacks. No ledger figure is silently overwritten by the all-log count.

Independent matched readout ACCEPT SHA-256 `59bdc5752a5be0477ab6a24eb9431214f18852020c545a018052ae4283e45a4f`; root independent final-check SHA-256 `44a11022775978d1cdc5537989d81d1bb3ad736584b7079162d7ee13bc05b710`. Student continuation ledger SHA-256 `73e68d3372490cc47bbea35906bf1f02878278094b998df95b57846931a196a4`; XL6 continuation ledger SHA-256 `1785d3d3233737ca5cc51e2a65ab7ad28808ee8a4e9bb6e8fb636e5289be613c`; common schedule SHA-256 `09afc31f5262b6aa4c737711510eaee748113c387d2d3fcc9543cf6c62d6b1d0`. No outcome-driven rerun, reseeding, selection, or retuning occurred.

<a id="selected-xl6-abyssal-arena-failure-20261028"></a>

## 2026-10-28 15:49:35 UTC — Selected XL6/Abyssal arena attempt: incomplete technical failure, no score

The planned run contained 200 game records; technical progress reached 199/200 before the attempt ended in Failed status. This is a technical completeness observation only. No partial win rate, outcome quality metric, or 199/200 score is recorded here.

The accepted diagnosis classifies the failure as post-run process-group teardown / Azure execution-wrapper finalization, not a model-quality cause. The absolute-deadline guard recorded `orphan_descendant_term_sent` at 13:51 UTC and returned code 125 after the leader exited while an owned descendant remained. The available evidence does not establish the surviving descendant's identity. Setup completed in 124 seconds and server readiness was recorded after 5 seconds; setup warnings are not established as causal. The attempt remains failed/incomplete, and automatic retry is forbidden.

**Benchmarklane handoff:** In a new reviewed candidate, explicitly terminate and wait for every background child before the arena shell returns, including the llama-server process after kill; track and reap a process-substitution `tee` child if used. Preserve the deadline guard. Root owns admission of any future run, which would determine whether the exit-125 teardown failure is cleared. Do not infer which process caused the orphan event.

Technical diagnosis source SHA-256 `a0795c892303d4b8bff9df408b9902504a4c7188bf73ddd0181b8b2b15b8d1ce`; execution-wrapper log SHA-256 `9dfc2812f525cb1f38b18116cfe5b1f33ed04c0ecc9a22bff02f591adaceb012`; job log SHA-256 `0c7363d8a1ce5b239a2832bd00bb87138f5fdf5eb8690d5edf1ce3a85249f731`. No retry or new arena run was launched for this note.


<a id="a1-base-v2-completed-20261028"></a>

<a id="src-a1-v2-owner-pending"></a>

## 2026-10-28 16:19:20 UTC — Base A1 v2 completed; blind audit point thresholds met, owner verification pending

The prospective base-first A1 v2 recording completed60/60 frozen Q1–Q3 items on20reviews, exit0 at16:01:46.939826UTC. All v2 method/source/config commits preceded the15:09:08 recorder launch; the original v1 outputs and labels remain unchanged. Source/config pins55c0d4a/34fa6cb/6dbea13/788c9e1/70ab801 bind the registered base Q4 server58330 and complete model-visible tool receipts. This is a separate protocol version, with fixed semantic source repairs and complete audit evidence; no isolated causal improvement claim follows.

| A1 v2 recorder measure | Count / denominator | Fraction | Wilson95% |
|---|---:|---:|---:|
|Direct numeric/tool verifier pass|41/60 answers|68.33%|55.77–78.69%|
|After at most one repair|59/60 answers|98.33%|91.14–99.71%|
|Template fallback|1/60 answers|1.67%|0.29–8.86%|
|Post-wording-guard replacements|0/60 answers|0%|Not separately estimated|

Server errors0. Recorded answer latency p50=43.905s,p95=86.871s on the shared Mac; this is not serving-action latency or a controlled hardware comparison. The early local battle arm, later data-index CPU work and one29.151s video encode overlapped portions of recording. The encode MP4 mtime is15:56:22.845907UTC, completion observed by15:59:18; encoder PID was not captured. No inference replay/reset or source/model change occurred.

Summary SHA25632b5f4dc0fccfa5f89641a500af0f68b19e8288f05369f287231748e4339932f; full60-row trace SHA2562f002dd19c2678bacac1cdd8d37e25146b1d67505d65156f122b54705b0839bc; config a81f27ad11491e9376853c471e3aeb81ace91ef6b6d699d47dc23b0365895b53; source closure61ae0c47094b42ff89c21d907bf30715436bb5b09ef8c0ee9d2723b3faf5fee2. Physical frozen items and complete scoped returns were verified by bound_derive before the blinded packet was written. Sentence population120; seeded sample60; templates excluded.

Fresh blinded Sol audit labels cover60unique packet IDs in packet order with no structural issue: **3/60 sentences flagged =5.00%**, Wilson95%1.71–13.70%, all categoryc(unsupported causal/decisive claim). Packet SHAa3580072c2e52592c42d353270dd4cda281fd27576feead9f51840571ab67045; labels SHA9c7d3f48a13c8ac41f8ec5bc461110781e0cac7875ff91d2b76eec6cbeb2471b. This is a blinded-label diagnostic while owner checks are pending, not a completed bound score or semantic-accuracy claim. Original v1 remains49/60afterrepair and12/60audit flags, unchanged.

Both registered point thresholds are met (after-repair>=80%, flagged<=5%). **Full release/Agentic decision PENDING:**15new seeded v2 owner labels missing, bound_score not run. The15 readable cards v2-01…v2-15 and private mapping are under scratch_ds/evals/owner15-v2-readable; owner asked once with a ten-minute estimate and example. Original v1 owner request remains separate/pending. Product stays on templates; conditional T1 training does not start on a pending base-v2 gate.

<a id="data-admission-index-attempts-20261028"></a>

## 2026-10-28 16:19:20 UTC — All-data source-index attempts and census-definition repair

All attempts below are identity/source admission only; **no dataset build, eligible-teacher count, model training, upload or cloud create** follows from them. Actual owner approvals for whole-game typed-nonzero P5 exclusion and nine timeout/null-RC quarantines are retained. Three missing-log source identities remain tombstones/roster barriers; P6 and fixed tests stay strict.

| Version / root authorization | Result | Diagnosis / disposition |
|---|---|---|
|v5 /65cb57c|Stopped; no index receipt|Typed-nonzero source games required owner policy; failed attempt retained.|
|v6 /2212283|Stopped14:34:42; no receipt|Incomplete/wrong-config generic source check; original exclusion approval did not cover nine null-return cases.|
|v7.1 /8f97354|Exit2 at15:23; no receipt|Side decision log missing, before approved whole-game exclusion could retain identity.|
|Unchanged v7.1 passive context /8ff2ad9|Exit2 at15:46:22; exact context saved|P5lab-n4-c008/g6 is winner-decided, rcA0/rcB1, missingB; it was already excluded under approved nonzero policy.|
|v7.2 /180778a|Exit2 at16:04:17.604955; no receipt|Missing-side guard fixed; full scan stopped on source-health census-definition mismatch.|
|v7.3 /94ff997|Running since16:16:57|Named counters aligned to pinned census definition, eligibility unchanged; await actual receipt and independent admission review.|

The old metadata census defined its `decided` flag as statusok AND both RCs typed zero AND winner non-null; its13nondecided and4status-ok/nonzero counters were composite health counts. Runtime had used raw A/B winner membership for those assertions. The earlier side-census prose therefore incorrectly described all missing-side games as nondecided. Original evidence remains intact with superseding correction SHA0aab1e632139d3ab3268790892bf3cf3cde4c92014f87e1f19b532408e3d7e94.

Reviewed v7.3 source main00ebb42 separates composite health failure13, status-ok/nonzero process4, malformed RC9, nonzero RC12 and status-not-ok9 from the independently reported raw absent-winner-marker count. Invalid present markers fail closed. It preserves raw completion checks, the v7.2 missing-side guard, all exclusions/holdouts and second-pass checks. Original Sol source ACCEPT;35independent and root synthetic tests passed. Spec SHA622f171c7e80212b8200d1e4df240678a736ec4ee8802bee23b2a31b473c88fd; CARD02552d2654dd737d63b676ec0947d1d4a2cc64dbf4871f582f3ec82e6402f33b. This fixes counter definitions without changing scientific eligibility. Until an actual index receipt is accepted, training data remain **NOT ADMITTED**.

<a id="all-data-identity-admission-20261028"></a>

## 2026-10-28 16:51:58 UTC — All-source identity admission accepted; actual dataset build running

The v7.3 index authorized at94ff997 exited0 at16:26:45.460904UTC. Its receipt reconciles364 archives,8,600 raw game lines,8,597 joined games,three missing-log tombstones and724,308 linked raw decisions. The source candidates surviving the fixed identity, roster, team-name and approved health exclusions are3,710 games /311,185 linked raw decisions. These are **source candidates, not serialized eligible teacher rows**.

| Source | Raw joined games / decision links | Candidate games / decision links |
|---|---:|---:|
|P5|6,597 /551,493|2,884 /240,274|
|P6,100 valid archives|2,000 /172,815|826 /70,911|
|Total|8,597 /724,308|3,710 /311,185|

The ordered exclusions account for4,887 games /413,123 decision links. All three tombstone identities and their roster/name barriers remain. The independent actual-index review found no admitted intersection with the registered holdout components, names or rosters. Fixed tests and validation identities are unchanged. Receipt SHA2567b598b22b129c4a6cfcd11edcd23e3aeaa0f34e697388fa12b39a2a5ecab5438; source00ebb42; spec622f171c7e80212b8200d1e4df240678a736ec4ee8802bee23b2a31b473c88fd. Review JSON SHA87bf44feccefaeb2019ee382069f3b96e188ea49a304752d6b57d19b17cc842e committed at e24a952. Earlier failed attempts above remain historical and are not relabeled successful.

Fresh build authorization430f941 permits one guarded offline SFT dataset build from this accepted index. Root launched it at16:35:05UTC, OS PID1881, exec session17337, with a7,200-second cap ending18:35UTC. It recomputes the accepted index, applies the unchanged teacher/serialization/token gates, verifies fixed-file readback and zero overlap, and publishes the output atomically. As of this entry it is still running: **final teacher/action/read row counts and the final manifest are unknown**. No upload, model training or cloud job follows from this build authorization. The mounted four-file proof and mandatory2k-row/one-step smoke remain next gates after sealed/conditional-C2 priority.

P6 contributes at most70,911 newly eligible decisions before row-level losses. It therefore cannot alone meet studentv2.1's100,000-new-decision requirement; the deficit is at least29,089. The prospective top-up farm card retains the existing reviewed producer contract, fresh disjoint seeds/source catalog and all admission barriers. No top-up farm or studentv2.1 training has been launched.


<a id="student-v21-topup-seed-reservation-20261028"></a>

## 2026-10-28T17:40:59.057991+00:00 — Prospective student v2.1 top-up seeds permanently reserved

Seeds 10,330,000–10,330,199; seeds 10,340,000–10,340,199; seeds 10,350,000–10,350,199; seeds 10,360,000–10,360,199 are permanently reserved for four prospective C1t1 / hr_train top-up farm jobs. Each can use at most offsets0–24; the entire200-seed block stays reserved even if no job runs. Fresh read-only union SHA2567e04ed0d64d1a633e4992ec3962b938bafecbb424e44f321254f0581885d6675 found no intersection or unclassified attempt. All historical reservations remain unchanged.

Source/cost review v2 ACCEPT SHA256dbd4d59459e75b07b616d9b84d2105e11a7fe9cc7aaa48dbeabb5b8ff7b0fdf9 covers at most four one-node six-hour jobs /24node-hours. The rejected first packet and repaired cutoff/watcher evidence are retained. These are unsubmitted reservations, not data or training results. Root still must bind fresh per-job absolute times, exact-name watcher identities, current capacity and higher-priority work before serial creation. New producer IDs will need an authenticated new source-catalog version and actual eligible-count checks before indexing/training.


<a id="glimmer-v2-dataset-built-20261028"></a>

## 2026-10-28 17:47:57 UTC — Glimmer v2 dataset built and independently admitted; GPU smoke staged

The guarded local build ran16:35:05–17:13:13.374699UTC and exited0 under auth430f941. The final dataset has **305,281 action examples +30,347 opponent-read examples =335,628 serialized rows**, from3,710 admitted games /2,640 distinct rosters. It processed311,185 admitted raw decision links;5,904 rows failed the fixed minimum-legal-action gate, and there were no task/tokenization drops. All seven registered overlap domains are zero. These are dataset counts, not model agreement or battle scores.

| Source | Admitted raw decisions | Action rows | Read rows |
|---|---:|---:|---:|
|P5|240,274|235,721|23,519|
|P6|70,911|69,560|6,828|
|Total|311,185|305,281|30,347|

Independent Sol actual-data admission ACCEPT, committed060fc4b; review SHA2560aa03c8dec43c38b851c52b01a4410309bbfb54fca50b5335155e18a0540291b, private evidencee52b927adce4a8594791ca00d5990cc88d587f14e46b72b7b5f8c6657eb66816. It independently read all335,628 rows and checked IDs, duplicates, action token targets, deterministic reads selection, source reconciliations and holdout-ID intersections without another source-archive scan or quality inference. Full manifest0822d13ec13eda05a3f188abaaead9b70dd67e74428a4d3273b72ddf45acfd60; receipt4cfd09e0425825f3c9469241b11a92a1b8bc270e88d6ae254cf00e1c1ac09aa1; train285,790,119bytes, SHAfc94428772b0fc67e893347099513ba968b7c9aff175167c0012f1cf5eac1979. Frozen v1 val/eval bytes remain unchanged. P6 counts here describe SFT eligibility; student-specific feature eligibility remains a separate check and its100k-new requirement is still unmet.

The consumer-schema repair a4d924c passed29 root synthetic checks; it preserves legacy manifests and validates proof-bound full-v2 and distinct derived-smoke schemas. The first actual derivation stopped on the proposed output parent's permissions before writing a subset. A dedicated0700 sibling parent and fresh auth2dae6b3 repaired that environment issue without source/data-selection changes. The successful unchanged prefix contains2,000 unique rows:1,809 action/191 reads. Its manifest11d61e08bb5d3d0d379b93c1c6c09a4e3315660ec38331cc4350492d365bf87f and ordered-ID digest2e157de6f3a487abd226b745b607cea0cd9f6e56f676bea0db0593e737371ce2 match the parent byte-for-byte selection rule.

Original actual-smoke source/data/cost ACCEPT SHA f19225ec4eac8485486fbe1ba21a5ec7fd6fa0eabc3f51d8a717f508da7c2e26. Prospective job hrsft-20261028-v2mount-smoke-6f8428c2 is **staged, not created**: one H100 /7200seconds /2node-hour ceiling, one optimizer step, exact weighted XL6 recipe, no fallback or quality/serving evaluation. YAML1cfa8df97eb6e4a983d436ec0ff50e0107572a64aa878594f3cc36f26353b5e4; code manifest5723d99d92924e38062e90323501f23c8a41c2335e012cbc8758ef73379e589f. Four data hashes are checked on each read-only mount before model loading; dataset bytes are absent from the code snapshot. Eight exact full/smoke objects were uploaded to fresh private own-workspace paths and independently downloaded:291,528,538bytes in each direction, everySHA/byte/ETag matched. Transport receipt43c5206db7e5d1a83c0c27e331531c303a894ac3d319121d98f2c370e41c0e88. No public access or new resource was created.

Fresh capacity, exact-name/output absence, sealed/conditional-C2 priority, a newly armed identity-bound watcher and root's create decision remain. No new v2 model, GPU fit, win rate, agreement or full-wave pass is claimed.


<a id="dpo-pairs-admitted-20261028"></a>

## 2026-10-28 18:01:35 UTC — DPO pair dataset completed and independently admitted

The authorized T2 offline build ran16:54:48–17:43:11.317146UTC and exited0. From311,185 admitted raw decision links it reported5,904 minimum-legal drops,62,760 decisions without a qualifying pair, and242,521 qualified pairs. The deterministic registered cap retains **100,000 pairs:77,160 P5 and22,840 P6**, covering3,710 admitted games. All seven overlap domains are zero. These are data counts, not model scores.

Fresh Sol admission ACCEPT committed75ac845 independently streamed all100,000 retained rows: exact file bindings, canonical unique IDs and rank order, admitted game/source membership, holdout-ID exclusion, prompt masks/digests and one-token response differences. Every retained gap is at least0.05 and agreement at least3 positive worlds; maximum observed length1,581tokens. Exact source-decision membership, legal-action mapping and cap selection across all242,521 candidates rely on the already accepted producer/build proof; this review did not rescan archives or run a tokenizer.

Manifest SHA256b76c5a0c27d8d5f058fa22deb2d4ebc46d6896fd83b424f6d8c51fe34513539b; pair file2,524,616,187bytes, SHAe4a5164d6559b568a459c0c3e5fa2fe69828fd382849726188f752fb268aa563. Structured review863886b4a45a2534ad879b0c890ea7f8562c1eff0c8c2636196a4ac962f04e7f; private evidence12158afa5ef4c85f31a292efa71bcb59f584e658ba1a0aa7d6f32a71b94e5f7d. Source activation50cbaf8 and distinct build authorization589850b preceded execution.

No GPU fit, six-rank startup, training or model-quality result is implied. Exact own-private pair/manifest transport and one-node selected-XL6 memory/timing qualification are next. Full six-node DPO remains behind sealed/conditional-C2 priority and runtime admission.


## 2026-10-28 18:08:59 UTC — Mandatory mounted-data smoke submitted

Root created exactly one reviewed job, `hrsft-20261028-v2mount-smoke-6f8428c2`, one H100 node,7,200-second AML limit, one optimizer step, no quality/teacher/serving/model-selection read. It was Starting on create; no runtime PASS or checkpoint is yet proven. Source/data/cost review f19225ec4eac8485486fbe1ba21a5ec7fd6fa0eabc3f51d8a717f508da7c2e26 and exact YAML1cfa8df97eb6e4a983d436ec0ff50e0107572a64aa878594f3cc36f26353b5e4 preceded the create; root authorization7c5ccf9.

The live exact-name watcher first polled pending_creation before submission; original launcher26733 exec-transitioned to the supervisor with the same PID/start time, verified before create. Cancel19:48:13UTC/hard end20:03:13UTC, earlier than the job's own latest possible timeout. Fresh two-command admission found0owned nonterminal nodes and0other-team active jobs; reserve25forconditionalC2 +1smoke =26of27. Output/name absence and13snapshot file hashes passed. Primary25jobs were already Completed and exact500sealedchunks present, with full-source fetch underway; the spare-node qualification cannot delay C2.

Two pre-create checks stopped without any create: a process argv changed through the expected caffeinate exec transition, then the exact output-absence query timed out. Original evidence remains private; identity and fresh admission were rechecked, with a single actual create at18:08:59.217213UTC. Final mounted hashes, real global_step1, persisted adapter/training state, terminal success and coherent ranks remain required before full SFT.


## 2026-10-28 — Mandatory mounted-data GPU smoke independently accepted

The exact one-node job `hrsft-20261028-v2mount-smoke-6f8428c2` completed successfully. Independent actual-runtime review SHA2f0db2975f8bdb2f466c8a3e0ae29a4b6a34947ca2cd67111f66db71510a8f14 accepted H100 preflight/train, the frozen2,000rows(1,809action/191reads), exact mounted four-file pins, one real optimizer-step event and trainer_state.global_step1, persisted identical final/checkpoint adapters plus optimizer/scheduler, and26downloaded file hashes over1,980,540,018bytes. The exact watcher recorded Running→Finalizing→Completed, zero errors.

The wrapper is silent when each file hash matches; its pinned control flow checks all four mounted files before Python/model loading. Successful model preflight and training prove passage through that gate; no separate printed hash receipt is claimed. It is a single-process infrastructure smoke with no DDP-marker or model-quality claim. No teacher, serving, fixed-test or selection score was read.

Storage CLI metadata attempts timed out, so the standard AML named-output private download was used as the30-minute manual fallback;1.98GB stayed below the6GB/3-minute bounds. Full12-node source/cost review separately ACCEPT55fc2b4b…ab66, committed3155947. Actual full training still needs sealed/C2 priority, current admission, live exact-name watcher and root create.


<a id="sealed-full-s41-20261028"></a>

## 2026-10-28T18:47:11.484481+00:00 — Sealed 10,000-game test: registered Ending B, no demonstrated improvement

The single registered full readout on500accepted logical chunks gives **50.05% treatment wins**,95% pairing-clustered interval **49.16–50.94%**, two-sided **p=.912**,5,000side-swapped pairings and9,997analyzed games from10,000raw game records. The registered improvement criterion (>50% and p<.05, with both A/As passed) is **not met**. Root selects **Ending B** after independent ACCEPT; **no confirmatory C2 run is authorized or required**. The sealed capacity reservation is released. This does not establish equivalence or a model-strength ranking.

All25primary jobs Completed before the final name inventory/fetch. Exactly500expected nonempty archives,total393,625,216compressed bytes,are hash-bound in the accepted selection;492initial downloads and8exact-object retries are preserved. All original bytes/logs remain. No scientific reruns,replacements,missing seeds or chunk gaps. The reviewed counters-only preflight passed500chunks/10,000records,valid treatment-only markers,zero fallback/infra warnings andzero metadata failures before any estimate. Three unknown outcomes were permitted by the registered sensitivity analysis; no protected interim outcome was read.

There are44raw games without a direct winner:41A-crash forfeits resolved by the registered attribution rule(20treatment/21vanilla), plus3unknown excluded from the primary9,997. Unknown-as-loss sensitivity:50.04%,CI49.15–50.93,p=.9295; unknown-as-win:50.07%,CI49.18–50.96,p=.8769,each10,000games. Both A/A gates remain PASS: AA1 49.65%,p=.7349,n1,000pairings/1,999games;AA2 50.85%,p=.3972,n1,000/2,000.

Frozen A4 tag3ee88fde16d06869591f3f7b8668c7800efbdde6;engine088d04bf5068cd8d3421c6b4ff60503dfdea7e72;forkf061e63d3d588f30e72af2f4a7d12d3101896d02;sealed tar7304a879fd69501d2afd4aa0e8905c9d296d97cae67e4443362a2a595c896874. Exact25job bindings are in scratch_ds/codex/c11/root-freeze-20261028/all25-rendered.private.json; each usesj00–24/k000–019 and registeredseeds1,000,000+10,000j+k. Analysis tools byte-match frozen A4.

Full-readout authorization3c30e1c preceded the sole call. ResultSHAe1286b7a365c1bba4c4164a22cb5932a3504c55df4bc0ac0a83903684ddcbd85;failuresSHAf3c03ffdf6ddfe7f1d59e2c4830d8c22216d593bd9794acd63573ef2a24c8607;preflight84b075152dd266e5da76256686f3777f998d723dbf29366d824288804be8a39a. FreshSolreadoutACCEPTe94ca56b84d3b3166c6f4bb26dd8ff4aa2c49a1ab1608e43c496c9ffb789e74c independently verifiedall500archivehashes/identities/seeds/source/failure arithmetic and both A/As without a second estimator call. Readout/EndingBdecision is a separate scientific result from the Studentv2/XL6local battle comparison.


## 2026-10-28T18:48:43.512809+00:00 — All-data Glimmer v2 SFT running on twelve nodes

Root submitted `hrsft-20261028-v2all-31317803` after the reviewed sealed Ending B released C2 capacity (ff88b21). The exact watcher observed Running, with zero verification errors. This is one epoch on the admitted335,628rows using the accepted weighted XL6 recipe and full read-only DATA_URI. Mandatory actual2kGPU smoke passed1b5a17a; actual12-node source/cost review3155947 and root authorization3b5fdde preceded creation.

Fresh admission found0owned nonterminal nodes and0other-team active jobs; after this sole create12nodes are reserved. Name/output absence,13snapshot hashes and live pending_creation watcher passed. CancelOct29 08:30:26UTC,hard end08:45:26UTC,AMLtimeout50,400seconds,maximum168H100node-hours. Supervisor38901 andcaffeinate38902 are root-owned. Actual GPU utilization,12rankstartup,epochcompletion,allfive rank0stages,11workerterminals,persistence andmodelquality remain unproved. Full2,428identityvalidation requires at least1,416correct before fixed-v0 test reads; the benchmark lane owns the arena.


<a id="student-v21-topup4-created-20261028"></a>
## 2026-10-28 19:23:36 UTC — Four student-data top-up farms submitted

Root created the four prospectively reviewed one-node farms at 19:20:42–19:22:04 UTC after source-byte, permanent seed, exact-name/output absence, live watcher and fresh aggregate capacity checks. The source/cost review is `dbd4d594…fdf9`; authorization `fa41237`. Each has a six-hour AML timeout; total ceiling 24 node-hours. All nonterminal reservations count: twelve Glimmer v2 nodes plus four farms = sixteen. Other-team aggregate was zero at every admission.

These are data-generation jobs, with no model score or eligible yield yet. First valid chunks, prospective source catalog and independent data admission remain required. Student v2.1 still requires at least 100,000 eligible NEW decisions. Exact observed create times, YAML/source hashes, watcher PIDs and individual deadlines are in `experiments/cards/STUDENT-V21-TOPUP4-CREATE-RECEIPT.json`. The existing `20261030` name labels are preserved; actual creation was October28.


## 2026-10-28 19:38 UTC — Glimmer v2 twelve-rank startup and training progress

Bounded own-job runtime inspection found exactly ranks0–11:12/12 successful preflights reporting H100,12train stages running, and positive CUDA/step markers. The shared log reached globalstep346 (692numeric step-bearing records, not692distinct optimizer steps). No preflight/train failure, OOM, traceback or NCCL error markers were detected in the bounded read. This is progress evidence, not a completed model or quality gate. GPU utilization, rank0termination and checkpoint persistence remain unverified. The source-bound typed receipt and unchanged12-node/168node-hour controls are recorded in `experiments/cards/GLIMMER-V2-FULL12-STARTUP-PROGRESS.json`. No validation, fixed400 or arena score was read.


<a id="xl6-incomplete-arena-20261028"></a>
## Selected XL6 arena — incomplete technical run (199/200), 2026-10-28

Selected XL6 versus **Abyssal**, seed2000, 200scheduled games/100side-swapped team pairs: **163/200 wins =81.5%**, Wilson95%CI**75.54–86.27%**, counting the one timeout as a loss. The other199 records contain163wins and36losses; completed-only163/199=81.91% is descriptive and does not replace the200denominator. This is the owner-authorized conservative lower-bound record of the original run.

**Incomplete technical run (199/200); job terminal Failed during teardown, wrapper return125. No full arena PASS or model-strength claim.** The runner summary's internal gate counted200ledger rows, not200completed battles. Failure diagnosis established post-run process-group/wrapper lifecycle failure; it did not identify a specific surviving child or a model-quality cause. Runner-reported diagnostics are0/5,525illegal events and policy latency p95=3.588seconds; individual policy logs were not independently available locally.

XL6 was selected using the original2,428validation identities atcommit0f8904c before test/arena reads. Exact selected GGUF/base/source/roster/seed bindings and independent original claim review are recorded in `experiments/cards/XL6-ARENA-199-OF-200-READOUT.json` (private record, not bundled in this source tree). The clean200game rerun with explicit model-server/tee termination and reaping remains with the separate Benchmark & Ladder lane; this conservative record does not substitute for that rerun. Historical Glimmer v0ddpres44.0% and base9/29~31% are separate runs with different denominators and runtime settings, not a matched causal comparison. F1 here names HardRead's search configuration, not the opponent.


<a id="dpo-qualifier-add11-first-failed-20261028"></a>
## DPO one-H100 qualification — pre-model input failure, 2026-10-28

`hrdpo-qual-20261028-n1-4f9c1a72` was created22:48:18UTC, reachedRunning, then was observedFailed22:51:40. The exact-file preflight raised `DPOError: tree inventory differs` before model loading or measurement. No runtime inventory, reference cache, fit/memory/gradient observation or timing projection was produced; six-node DPO was not launched. This is an infrastructure/input-binding failure, not a model-quality result.

Read-only storage metadata has the12expected base files and the4expected selectedXL6 adapter files plus42additional objects in its prefix. The adapter prefix is the likely mismatch; the failed job's mounted tree was not directly inspected. Repair uses a clean four-file byte-identical mount while preserving strict inventory/source/selection checks. Two startup GPU samples were0% utilization/0MiB; these do not measure training. The original metadata upload/readback passed and no owner transfer approval is pending. Source/readout and independent runtimeHOLD are bound in `experiments/cards/DPO-QUALIFICATION-ADD11-FAILED.json` (private record, not bundled in this source tree). The earlier successful launch observation remains historical; it is not ongoing Running status.


<a id="dpo-r2-fit-timing-20261028"></a>
## 2026-10-28T23:29:39.459910Z — DPO R2 fits, full-run timing fails

The repaired one-H100 qualifier `hrdpo-qual-20261028-n1r2-6c8d70f1` was created at 23:13:08 UTC and observed **Completed at 23:21:29**. Its 48 preselected pairs passed the original longest/quartile coverage, finite reference-cache, optimizer-update and memory gates. Peak reserved memory was 83,233,865,728 bytes, leaving 16,715,939,840 bytes of device headroom. This is qualification evidence, not a trained DPO checkpoint or a benchmark gain. The first failed input-inventory attempt remains recorded separately.

The unchanged pinned projection over all 100,000 pairs on six ranks is **83,839.45 seconds (23.29 hours) even with zero persistence reserve**, or **84,589.45 seconds (23.50 hours)** with a 600-second reserve. Both exceed the owner-applied **43,200-second / 12-hour** cap. Verdict: **single-H100 fit ACCEPT; six-node timing REJECT; full training not launched**. The requested one-hour launch target was missed. No quality scores were read.

Eight one-minute native GPU samples, including startup idle, averaged **37% utilization, p10 0%**, with peak sampled memory **71,239 MiB**. These samples do not establish steady-state utilization. Source `b9be2cd` uses six ranks and accumulation eight (global 48 pairs per update); an earlier coordinator description of global batch 12 was wrong and did not change the source. The next repair will preserve the original objective, full data and source batch, and must pass a fresh actual runtime qualification.

Evidence: `experiments/cards/DPO-QUALIFIER-R2-ACTUAL-READOUT.json`; independent actual review SHA-256 `0c9fe0c2e780edfa0b33dd605ae2a5771cd33ae88f729f4668bfdfe6d1e19ac3`, separate fit proof `263ca187…c8b06` and timing rejection `69618f7e…b1eb`. No statistical CI or p-value applies to this engineering qualification.


<a id="dpo-response-preflight-failed-20261028"></a>
## 2026-10-29T00:01:42.471347Z — Response-only DPO qualifier fails before model load

`hrdpo-qual-20261029-response-2d5ca35c` was created23:51:47 UTC, reached Running, then was observed Failed23:55:46. Input preflight deliberately rejected `pair source review differs`: the coordinator's new config placed the independent code-review hash in the field for the original admitted-pair source review. The frozen pair manifest and data did not change. Independent final-render acceptance was withdrawn for this configuration; the separately accepted response-only source remains unchanged.

No model load, native API/parity, memory-fit, reference cache or throughput observation was obtained. Two startup GPU samples were0%/0MiB. This is an engineering failure, with no AI quality conclusion. The prospective repair restores the manifest's original admitted-pair review and regenerates the config/approval/YAML/resource pins in a new stage and exact-name job. All100k pairs,6ranks/8accumulation/global48 and12h full-training cap remain. Full six-node training has not started. Evidence: `experiments/cards/DPO-RESPONSE-QUALIFIER-FAILED-PREFLIGHT.json`; independent actual failure gate SHA `0133934570fc76e60f65c9881ac86f6c56097eab4401aa747238207cdaa16189`.

<a id="w2-offline-build-failed-20261028"></a>
## 2026-10-29T00:01:42.471347Z — Hosting image identity and resource proof pass; offline build fails

The corrected offline import is cryptographically bound to the two approved image configurations and ordered layers. Docker29's inspect ID is a target manifest digest, rather than the expected configuration digest; exact local manifest payloads close that mapping. The resource-only probe passed the unchanged2CPU/4GiB/no-swap cgroup check using the approved script over stdin. An earlier host-bind probe failed before container creation and remains preserved.

The single approved offline legacy build ran23:56:32–23:56:37 UTC and failed with `COPY failed: NotFound: parent snapshot <digest> does not exist`. No target image was created. No retry or BuildKit fallback ran, and the ten-replay container smoke was not run. No native ABI/install or deployment readiness is claimed. Working local app remains the fallback; missing a public link weakens the Demo and CommunityVote route. Final evidence index SHA `54dcd1b2ec2e4c9a86f701d8344841e1ffdc4b03cc89fc65fc8455616bb1fe69`.


<a id="dpo-response-parity-r4-r5-20261029"></a>
## 2026-10-29T00:30:08.396541Z — DPO optimized calculation fails the optimizer-update check

The configuration-corrected R4 qualifier reached the actual model/cache stage and failed `dense/selected parity differs`. Runtime inventory and cache bindings passed, but no scalar metric was recorded; independent actual review declared NO-GO. R5 added only scalar diagnostics before the same guard and was created once at00:22:33UTC on one H100. Its actual receipt observed Finalizing at00:28:48, with the same parity exception and no fit/timing observation. This is engineering qualification, not an AI benchmark score.

R5 measured identical reported log-probabilities and loss (absolute differences0), gradient cosine0.99998819 and relative L2 error0.00486513, all within their original limits. **AdamW update relative L2 error0.05494545 exceeds the unchanged0.01 limit. Full six-node DPO remains unlaunched.** The numerical cause remains under investigation; no tolerance or objective was changed. R5 six startup-inclusive minute samples averaged16.67% utilization,p10=0%,peak22281MiB. The one-hour launch target was missed. All own-storage transfers were authorized and verified; no upload approval is pending. `experiments/cards/DPO-RESPONSE-PARITY-R4-R5-ACTUAL.json` (private record, not bundled in this source tree).

<a id="reads-n3-incomplete-20261029"></a>
## 2026-10-29T00:30:08.396541Z — Glimmer reads evaluation incomplete and unscored

The first XL6 reads job stopped after20/440decisions: `20-decision timing projection exceeds deadline`; terminalFailed at23:58:39UTC. No top-1, log-score or paired-bootstrap result was computed, and the partial model-output file was left unread. The runner's projection included one-time loading; actual load/decode durations were not logged, so this does not establish whether a corrected full evaluation fits the three-hour cap. A prospective timing-only repair preserves the same440identities, model and decoding and must refresh source-bound metadata before a new call.

Telemetry erratum: the native five-column CSV contains12samples, mean35.5833%,linear p10=38%,peak21459MiB including startup. The earlier no-samples report was a local parsing error and remains preserved. `experiments/cards/READS-N3-XL6-INCOMPLETE-ACTUAL.json` (private record, not bundled in this source tree).

<a id="student-v21-topup4-count-20261029"></a>
## 2026-10-29T00:30:08.396541Z — Student v2.1 new-source count below the training gate

The reviewed v5 derivation published a pending-review count of90,362selected/deduplicated/encodeddecisions, including **70,689eligibleNEWtrainingdecisions**:54,018legacy-P6 plus16,671four-farmtopup. The required100,000NEWtrainingdecisions is not met: **29,311short**. Raw226,655decisions are not eligibility. No Student training ran. Actual independent source/count review is pending; farms05/06 require a prospectively versioned producer/catalog admission before inclusion. `experiments/cards/STUDENT-V21-TOPUP4-ACTUAL-COUNT.json` (private record, not bundled in this source tree).


<a id="reads-n3-xl6-timing-retry-20261029"></a>
## 2026-10-29T01:00:13Z — Glimmer reads timing retry remains incomplete and unscored

The N3 timing check completed **20 of 440 decisions**. Loading took **17.904 s** and the first 20 decisions took **687.424 s**; the projected remaining work was **14,435.902 s**, above the **9,879.260 s** available at the checkpoint, so the run ended incomplete on its timing gate. Quality scoring was not performed, no model outputs or labels were read, and this attempt did not produce a completed quality run. GPU telemetry contained 12 samples: mean utilization **35%**, linear p10 **38%**, and peak memory **21,459 MiB**. This is a timing-feasibility result, not an accuracy result; the earlier incomplete attempt remains preserved separately. `experiments/cards/READS-N3-XL6-TIMING-RETRY-ACTUAL.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `scratch_ds/codex/training-roadmap/glimmer-reads-n3/monitor-xl6-20261029T0045Z/poll-9/metadata-snapshot/timing-gate-readout.private.json` SHA-256 `5810f0dc2b81d87e8f55199557dc2f26d50f8cef0a3b9d5c90b0f5b428958a38`; `timing.json` SHA-256 `6276fb8813353460e88e308cae9c954ceb4f800d2a69fbf9d081621d172128f0`; `terminal.json` SHA-256 `743573867abb7d3e95e3a37e7e4fc4c58228440e8e55a82276234bfddae11db5`; `gpu-samples.log` SHA-256 `6d656ddc8fdc20aa25d4b22cd42edb552d1cafd06bb4ab806e5b010382bceea3`.

<a id="dpo-stable-r6-actual-nogo-20261029"></a>
## 2026-10-29T01:05:04Z — DPO stable-backend R6 qualification ends NO-GO

The one-H100 stable-backend qualification recorded **48 scalar samples across 12 comparisons** in two six-comparison groups: one group passed and one failed. The failed group's maximum AdamW update relative-L2 error was **0.0665657285**, above the unchanged **0.01** limit. Arm log-probability and loss absolute errors were zero; the worst-case gradient cosine was **0.9999877880** and gradient relative-L2 error **0.0049461067**. The independent actual review classified the qualification **NO-GO**. Fit/memory/end-to-end timing were not proven, GPU utilization was unmeasured, and the full six-node run was not authorized. This is a qualification diagnostic, not a trained-model benchmark or quality result; the prior R4/R5 negatives remain in place. `experiments/cards/DPO-STABLE-R6-ACTUAL-NOGO.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `scratch_ds/codex/training-roadmap/dpo-t2/qualification-stable-r6-final-20261029T0055Z/actual-runtime-20261029T010504Z.private/typed-receipt.private.json` SHA-256 `78670a41cbd04df8695d8c5c50c5f0b2f518801a57626317e6e0895b103b299c`; `scratch_ds/codex/training-roadmap/dpo-t2/qualification-stable-r6-final-20261029T0055Z/sol-actual-review.private/SOL-R6-ACTUAL-NOGO.private.json` SHA-256 `09b98b7c4dbbebdad2f0ad0c6f0d555da788e6cc9a2499832000cd6860b8e0e3`. The review binds both six-comparison parity summaries.

<a id="student-v21-topup6-count-pending-20261029"></a>
## 2026-10-29T01:20:45Z — Student v2.1 count review accepted; new six-farm count remains in progress

The independent review accepted the v5 actual source count **for count reporting only**: **70,689 eligible new training decisions** (54,018 from legacy P6 plus 16,671 from the four-farm top-up), **29,311 below** the 100,000 training gate. No training was authorized or run. This resolves the earlier pending-review status for that v5 count only.

A separate root-authorized, source-count-only derivation for the prospective six-farm source set (the original four top-ups plus farms05/06) started at 01:20:45 UTC and was still active at the 01:29:44 UTC process check. It has no final count or reviewed eligible yield yet; it does not authorize or imply training. Do not add the six farms to the accepted 70,689 count unless a completed derivation is reviewed and admitted. `experiments/cards/STUDENT-V21-TOPUP6-SOURCE-COUNT-PENDING.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `experiments/cards/STUDENT-V21-TOPUP4-ACTUAL-COUNT.json` SHA-256 `4283b8e5681192b174bc81280ab36078a5126cbd26730df0927ecdc8b6eacb91`; independent v5 source-count review `scratch_ds/codex/training-roadmap/student-v21-topup4-data-readiness-20261028T1952Z/source-selection-actual-review-v1/independent-actual-source-count-review.private.json` SHA-256 `29de16f474048d8084eb4f6e072c420a861a8c50dbe625a7f4a46983e74e596a`; new six-farm count authorization `experiments/cards/STUDENT-V21-TOPUP6-COUNT-AUTH.json` SHA-256 `112c28a4cd664a37e6466ca2cd202c26944be1564ba828bfba7b60d981634e09`; root launch record `scratch_ds/codex/training-roadmap/student-v21-topup6-candidate-v3/root-launch.private.json` SHA-256 `9153dbb92ce7d19edfc67bb88fedc65f30c13e39cfb4e1f328ffcefdf40c7b2c`.


<a id="reads-n3-native-bf16-timing-failed-20261029"></a>
## 2026-10-29T02:08:52Z — Native-BF16 N3 evaluation stops on the first-20 timing gate

The native-BF16 N3 run completed **20 of 440 decisions**. Model loading took **16.637 s** and the first 20 decisions took **537.106 s**. At the timing checkpoint, projected remaining work was **11,279.218 s**, which exceeded the **10,246.081 s** available by **1,033.137 s**. The run ended incomplete at 20/440; no full-cohort quality result was produced and the actual receipt records no score or partial-quality access. This is a timing-feasibility failure, not an accuracy result. `experiments/cards/READS-N3-NATIVE-BF16-TIMING-FAILED.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `scratch_ds/codex/training-roadmap/glimmer-reads-n3/actual-xl6-nativebf16-fde53726-stage-v1/runtime-watch-20261029T020852Z/native-runtime-failure-readout.private.json` SHA-256 `64dc31894071de03a6736db4353aa9463277ff9c3f2ae76a854dc6dff96d931c`; `timing.private.json` SHA-256 `23d4abb40da723ad4e7bd2f34b91313a8c2609e0071386d9ceabd5852c892a36`; `terminal.private.json` SHA-256 `057f5a7e73ce6e406e290121391862004ce7174d9440b156f24f2cb561292c37`; `status.private.json` SHA-256 `c9458fb093e32fb5e9388a5936c6916de35520ebc1e36772a419449febc83b76`.

<a id="dpo-r7-full6-startup-update-20261029"></a>
## 2026-10-29T02:14:02Z — R7 fit/parity pass; first full-six startup fails before model load

The independent R7 actual review passed the one-H100 fit check and all **12/12 parity comparisons**. Its full-six timing projection was **90,951.295 s (25.264 h)**, above the **12 h** limit applied in that review; the review therefore recorded full-six timing **NO-GO** and did not authorize a full-six run. A later owner-approved cap card records **27 h across six nodes (162 node-hours)** under commit `059bfd3`. The larger cap is a resource limit; it does not revise the R7 review or clear separate launch gates.

The subsequent first full-six startup failed during pre-model tree-inventory validation. The terminal classification records six `tree inventory differs` errors and identifies the wrong XL6 mount. The model did not start; no full-six training or quality result was produced. This is an input/startup failure, not a model-quality result. `experiments/cards/DPO-R7-FITPASS-FULL6-STARTUP-UPDATE.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `scratch_ds/codex/training-roadmap/dpo-t2/qualification-fullhead-r7-20261029T012547Z/sol-actual-review.private/SOL-R7-ACTUAL-FIT-PASS-TIMING-NOGO.private.json` SHA-256 `61eebd09471165d4c22a38a1ac53ab02702e05d4ff8221b61ef9b398125b9e29`; `experiments/cards/DPO-27H-CAP-SOURCE.json` SHA-256 `d34ea7746563202d2b4d7fa073ac1a7a360120b7c0a41ac803fba6e2a8c04a95`; `scratch_ds/codex/training-roadmap/dpo-t2/full6-27h-root-final-20261029T0155Z/actual-startup-20261029T021337Z.private/typed-receipt.private.json` SHA-256 `d2cc6401aae6d820d2825955e252e75f07734edd856ffbd03c1e6bc9b381d416`; terminal failure classification SHA-256 `005df2e6301b7bf4cf50492adc9a6631abaec95cd1e97ec531a525f858ba605e`.

<a id="student-v21-topup6-count-accepted-20261029"></a>
## 2026-10-29 — Six-farm Student v2.1 source count accepted below the training threshold

The independent review accepted the completed six-farm source-count report for **count reporting only**: **74,033 eligible new-only training decisions**, from **250,212 raw** and **95,867 selected/deduplicated/encoded** decisions. This remains **25,967 below** the 100,000 training threshold. Training was not admitted or executed. The earlier pending six-farm status is superseded by this count-only review; this report does not authorize training. `experiments/cards/STUDENT-V21-TOPUP6-ACTUAL-COUNT.json` (private record, not bundled in this source tree).

Evidence (repository-relative paths): `scratch_ds/codex/training-roadmap/student-v21-topup6-candidate-v3/count-helper/execution-v1/source-count.private.json` SHA-256 `6eb5e9243f0fcfe30750610aefd580e4c958d8ef8885a4cdcd62aac2af004b1a`; independent count-only review `scratch_ds/codex/training-roadmap/student-v21-topup6-candidate-v3/independent-actual-count-review.private.json` SHA-256 `eedbc2ea8dd96e7183054190a0b5d1cb6241980a8be38de96e8c632ee0ba54ca`.


<a id="glimmer-v2-original-validation-20261029"></a>
## 2026-10-29T03:53:57Z — Glimmer v2 original validation complete; XL6 retained

The all-data Glimmer v2 candidate completed its original full validation in `hrsft-20261029-v2all-fullval-946a548b`. Independent original readout and root reprojection accept **1,386/2,428 = 57.0840% engine-policy agreement**, policy CE **1.5677826965**, top-2 **1,886/2,428**, top-3 **2,123/2,428**, and zero generated rows. All 2,428 action identities, original validation/companion bytes and final v2 adapter hashes match. The companion's 238 reads rows are not included in the action denominator.

| Original validation | Correct / n | Engine agreement | Policy CE |
|---|---:|---:|---:|
| Selected XL6 | 1,379 / 2,428 | 56.7957% | 1.5677069633 |
| Glimmer v2 candidate | 1,386 / 2,428 | 57.0840% | 1.5677826965 |

V2 gains seven cases (**+0.2883 percentage points**) and misses the prespecified **at least 1,416/2,428** selection gate by 30. **V2 is not selected; XL6 remains selected. DPO round 2 is not triggered.** The descriptive v2 Wilson 95% interval is55.106–59.040%; the aggregate difference establishes neither paired significance nor a causal effect of more training data. This agreement metric is not a battle win rate.

This decision is recorded and committed before any registered fixed-v0 test-quality read for v2. A subsequent owner-12 candidate fixed-400 comparison is descriptive and cannot change selection. The producer's built-in400 rows are not that registered test. Training/five-stage/persistence completion and earlier failures remain separate records.

`experiments/cards/GLIMMER-V2-ORIGINAL-VAL-NOT-SELECTED-20261029.json` (private record, not bundled in this source tree). Independent original review SHA-256`c02f5483c441f1edace4edaf32854986b675e255535c11342ac2731070d43c30`; teacher rows SHA-256`716b56ad028a12d774b1a4b0ce619ae080bf65ce6cc8b1df55e3f15867ba5bce`; v2 adapter SHA-256`946a548b635db87ed304eb66d5a104c35d7c4787a29c9a3a87574e04dcbe57f3`. No fixed-v0 score, new arena outcome or DPO quality result was opened for this decision.

<a id="engineering-operations-update-20261029T0354Z"></a>
## 2026-10-29T03:54:11Z — Engineering and runtime updates; no new quality result

The Glimmer v2 full-12 training run completed **3,497 steps** across five successful stages. Its persistence receipt lists **42 objects**, and the final five-file artifact readback passed at **795,101,549 bytes**. The separate original R1 validation and selection decision are recorded above; this engineering entry reports no validation metric.

DPO full-six R2 failed before model execution because the YAML code directory did not match the intended configuration; all six source errors were job config bytes differ. R3 was still Running in its 03:02 UTC receipt. Its six ranks each had 21 utilization samples (mean range **80.67–80.86%**, latest **98–100%**, in the reference phase); optimizer updates and terminal completion were not proven.

The three N3 native-BF16 starts failed before evaluation (quoted launch command, R1 cutoff guard, and R2 source-review binding). The three failed starts were **conservatively charged 300 seconds each**; that accounting is not measured compute consumption. No full-cohort score or quality result was produced.

The hosting wheel-stage build missed its 90-minute hard deadline. The later Cargo emulation diagnostic stalled for 196 seconds at 0.02–0.05% CPU, with no rustc start and no output. It received a PermissionError reading /proc/<cargo>/syscall, leaving the precise cause unknown; no actual nativekit job, wheel, image, or smoke result is claimed.

The farm 07/08 census is health-only: **15 archives**, **14 healthy**, **one whole chunk held**, and **22,990 raw decision rows**. Eligibility was not evaluated, so the prior **74,033** count-only new-source result remains unchanged and this union remains pending. No N1/B2 outcome is added here; those remain with their Benchmark-owned record.

`experiments/cards/ENGINEERING-OPS-UPDATE-20261029T0231-0354Z.json` (private record, not bundled in this source tree) records the source paths, hashes and scope limits.


<a id="glimmer-v2-fixed-v0-400-20261029"></a>

## 2026-10-29T04:46:20.954083+00:00 — Glimmer v2 on the registered fixed-v0 400: descriptive comparison; XL6 retained

`hrsft-20261029-v2-fixed400-owner12-946a548b` Completed. The selection decision **v2 NOT SELECTED / XL6 retained** was committed in `6a9b7d6` before this job was created at 04:20:09 UTC. The full 400 ordered identities exactly match the prior fixed-v0 cohort (LF-join digest `dcf05ed82eaf7b487df1741f27c091c581aa65aa1af6869fb1153bd44225c545`). Both preflight and teacher evaluation passed; generation count is zero.

| Model / version | Recorded-choice top-1 | Top-2 | Top-3 | Mean policy cross-entropy |
|---|---:|---:|---:|---:|
| Glimmer v2 final, adapter 946a548b | **227/400 = 56.75%** | 303/400 = 75.75% | 336/400 = 84.00% | 1.582312375779234 |
| Selected XL6, same fixed-v0 cohort | 223/400 = 55.75% | See original XL6 record | 336/400 = 84.00% | 1.593645290891526 |
| Base Glimmer, same-prefix scoring in this run | 101/400 = 25.25% | 168/400 = 42.00% | 228/400 = 57.00% | 3.35584456830693 |

V2 has four additional correct choices (+1.00 percentage point) in this descriptive test. This is **teacher-forced agreement with the recorded choice**, separate from the 2,428-position engine-policy validation used for selection and from battle win rates. Base engine-policy argmax agreement is separately 109/400, not its 101/400 recorded-choice score. No significance, independent-position interval, battle-strength improvement or causal data-scaling effect is claimed. No test-driven reselection: v2 missed the original validation threshold, XL6 remains selected, and DPO round 2 is not triggered. The teacher-forced legal probability mass is not a generated-action legality rate.

Root independently reprojected all 400 rows with `math.fsum`; original Sol result review **ACCEPT, descriptive only**, SHA `5ea500daae1053b2cde15529c96a3b4f680908a20e725f7e6a2536c578cf3ca1`. Raw result `ffba598bfb83ae42af002e43ef7c131d48253827c440474cd13f303f5a06f1eb`, rows `105f319dc6bfe63c7023ca5cb415e551f1b59e562b30c76705e8e1082cfae14b`, summary `56e988a2dd12ad76e7048a664c3c5f118fdaed02119b28caa4cfbaa5255318f9`. Exact adapter, source/dataset, original source/cost review, row digest and corrected preflight projection are in `experiments/cards/GLIMMER-V2-FIXED400-DESCRIPTIVE-RESULT-20261029.json`. The initial preflight extraction receipt remains preserved alongside its correction; the raw artifacts were unchanged. No further evaluator or model call was made for this readout.


## 2026-10-29T04:47:28.129977+00:00 — XL6 reads R3 first-20 runtime only; quality remains unread

`hrreads-xl6-20261029-nativebf16-r3-ae267cdf` is Running. Its exact first-20 operational artifact confirms native BF16 base / FP32 adapter on CUDA, 728 adapter parameter tensors matching the frozen config. Twenty decisions took **539.841 seconds**; projected remaining **11,336.669 seconds** versus **12,943.301 available** (`within_budget=true`). Root rehashed the raw artifact and pinned runtime config; original source/data/cost admission remains unchanged. This is a runtime projection, not completion or accuracy. No output rows or partial quality were read.

The monitor task's earlier root expectation of 528 tensors was stale; actual frozen config and observed runtime both have 728. The earlier false literal check and correction remain preserved. Raw timing SHA `642fcd083cd5ac7f428b11381671d7f98ea505a09ba905bb9c7d76318b6ff347`; reconciled receipt `407d11ec9002bd069cae55e2454b6aac20b7f39e9557c2f4ea5f5f70c5753600`. Main source record: `experiments/cards/READS-N3-R3-FIRST20-RUNTIME-20261029.json`. Original 440 prompts / 1,954 targets and four-node-hour completion ceiling remain; cancel 08:25 / hard 08:40 UTC. At the bounded local check, DPO watcher 43046 and reads watcher 70456 were alive with their recorded birth times; no signals or restarts were used. All earlier reads failures remain unscored and recorded.


<a id="xl6-clean-n1-pooled-g1-20261029"></a>

## 2026-10-29T04:55:17.091528+00:00 — XL6 clean rerun and pooled G1: final, failure-inclusive G1 PASS

The existing Benchmark-owned jobs `hrarena-20261029-xl6-{clean,g1a,g1b}-n1r1` all **Completed**, with the selected XL6 converted-adapter hash `0f907059…e7fb94` verified and server/monitor cleanup reaped. These are against the **Abyssal heuristic bot**, on **partly seen `gen9/hr_hl_dev` development teams**, with 100 side-swapped pairings per arm. They are not battles against Foul Play or human-ladder results.

| Run / seed | Wins / all scheduled records | Wilson 95% CI | Completed battles | Decision p95 |
|---|---:|---:|---:|---:|
| Clean rerun / 2000 | **161/200 = 80.50%** | 74.46–85.39% | 199; one timeout | 3.577 s |
| G1 arm A / 4000 | 163/200 = 81.50% | 75.54–86.27% | 199; one timeout | 3.523 s |
| G1 arm B / 5000 | 170/200 = 85.00% | 79.39–89.29% | 200 | 3.541 s |
| **G1 pooled** | **333/400 = 83.25%** | **79.28–86.59%** | **399; one timeout** | Both arms below 5 s; no pooled p95 claimed |

All 600 scheduled records are present and unique by arm/index. Timeouts count as non-wins. The pooled result **passes the predefined ≥60% G1 rule**, and the recorded legal/latency checks pass: zero fallbacks or server rejections across **16,675 decisions**, with p95 below five seconds in every arm. Each individual 200-game G1 summary retains its `gate.pass=false` because the implementation checks 400 records per invocation; the prospectively prescribed pooled 400-record rule governs the final G1 verdict. The clean job has all 200 scheduled records, rather than 200 completed battles.

The earlier incomplete historical run remains **163/200 = 81.5%**, with failed teardown and 199 completed. The clean rate differs by −1.0 percentage point and lies inside that earlier descriptive interval; no formal paired or causal improvement claim is made. Root reprojected every game record, native decision latency record and fallback/rejection counter; Sol independently accepted the original analysis (review `060ce0a6…75488`). Exact raw summary/game hashes, terminal projection, adapter identity, cleanup and registered thresholds are in `experiments/cards/XL6-CLEAN-N1-POOLED-G1-FINAL-20261029.json`. Benchmark owns subsequent battle work; no additional run was launched for this readout. These results are eligible for the source-keyed video draft before the October31 10:00 UTC model-number freeze; later numbers remain separate.


## 2026-10-29T05:16:33.757399+00:00 — Private v6 video engineering check, not a finished submission

The updated silent preview incorporates the accepted XL6 arena chart at120–130 seconds, then retains the tested-search VoI still at130–135. It remains180seconds/5400frames/1920×1080, with14soft-caption cues. Root independently decoded both versions and matched all4950frames outside the changed120–135 science interval; the accepted450-frame comparison is preserved. The other11caption cues are unchanged. Original source/claim/timing plan273643de and chart claim review28cb5091 are accepted, and root inspected the contact sheet/full arena frame. Exact video/manifest/source hashes and private artifact path are in `experiments/cards/VIDEO-PRIVATE-ROUGH-CUT-V6-OWNER12.json`. Actual voice, two non-player human viewers and pacing/caption-density acceptance remain pending; this is not a publication or finished video. Final model-number freeze remains October31 10:00UTC.


## 2026-10-29 — Student v2.1 data count v3: 90,133 NEW, training gate not met

The single accepted 18-producer count completed under its original 90-minute / sampled 12-GiB guard. Independent review accepts **90,133 eligible NEW train decisions**, **9,867 short** of 100,000; Student v2.1 training remains disabled. This is a training-data count, not a model accuracy or win rate. Historical six-source 74,033 remains unchanged as its own run. The expanded union gives 314,303 raw decisions, 117,356 selected/encoded/deduplicated, and 90,133 eligible NEW. Whole-game selection precedes encoding; original cohorts, collisions, C9 and heldout barriers remain. The union removes 2,492 previously eligible prior-class rows and adds 18,592 new-class rows: direct addition to 74,033 would be wrong. Exact report/provenance/cohort hashes and original independent admission are in `experiments/cards/STUDENT-V21-TOPUP12-COUNT-RESULT-20261029.json`. Private work was removed after the successful zero exit; final sampled work was 7,854,231,326 bytes, with transient peaks unverified. No training or count rerun occurred. A new useful farm wave requires prospective source/cost/usefulness checks and current capacity admission.


<a id="glimmer-reads-n3-xl6-complete-20261029"></a>

## 2026-10-29 — N3 XL6 native BF16 R3: complete exploratory reads

On a prospectively frozen exploratory subset of 440 held-out S-A hr_val games (one predetermined decision per game; 1,954 Pokémon rows), XL6 Glimmer completed all 440 reads. Eligible-row top-1 accuracy was 907/1,473 (61.58%) for hidden items, 800/1,855 (43.13%) for tera type, 1,509/1,620 (93.15%) for ability, and 848/958 (88.52%) when restricting abilities to species with more than one legal ability. Exact hidden-move-set match was 309/1,911 (16.17%). No output was excluded for parse or field failure.

Compared on identical rows, item and tera rates exceeded the usage reference by 3.53 and 3.13 percentage points (exploratory 95% paired game-bootstrap intervals: 1.55 to 5.61 and 1.62 to 4.72). The intervals did not demonstrate gains over belief v1 for item, tera or ability; hidden-move-set exact match was lower than usage and belief v1 by 3.04 and 3.66 points (intervals: −4.68 to −1.47 and −5.28 to −2.11). These are exploratory subset results, with 2,000 paired game resamples and seed 0. They do not replace the full S-A benchmark or establish overall model or playing strength. Glimmer categorical NLL and all moves NLL remain undefined; this readout does not trigger a product or shipping decision.

| Field | XL6 correct / eligible rows | XL6 | Usage | Belief v1 |
|---|---:|---:|---:|---:|
| Hidden item | 907/1473 | 61.58% | 58.04% | 60.96% |
| Hidden tera type | 800/1855 | 43.13% | 40.00% | 43.83% |
| Hidden ability | 1509/1620 | 93.15% | 93.58% | 93.58% |
| Ability, multi-ability species | 848/958 | 88.52% | 89.14% | 89.14% |
| Exact hidden-move set | 309/1911 | 16.17% | 19.20% | 19.83% |

Exact version, source/input/model/output hashes, all field intervals and baseline support counts: `experiments/cards/READS-N3-XL6-COMPLETE-EXPLORATORY-20261029.json`. Original independent result review6abe25af reproduced every count and paired interval exactly; maximum arithmetic error0. Earlier timing and pre-model failures remain separate and unscored.


## 2026-10-29 — Student topup16 four-farm wave Running, no new eligibility result

Four unchanged-recipe training-data farms13–16 were created once each at08:07:06–08:08:33UTC and are all **Running** in the08:16 snapshot. The prospective source/cost decision, permanent full200-seed blocks, exact rendered clocks, source rechecks, current node admission and identity-bound watchers passed before each create. Maximum exposure is24node-hours (four one-node jobs, six-hour AML timeout each); no dollar rate is invented. New chunks stop11:06–11:08UTC, workload guards are13:51–13:53 and watcher hard ends14:11–14:13. This is CPU engine data generation on existing H100 nodes, not Student training. Actual accepted NEW remains90,133,9,867 below100k. First-chunk health and the eventual whole-union eligible count remain pending. Exact jobs, guards, authority commits, source hashes and observed states are in `experiments/cards/STUDENT-V21-TOPUP16-FOUR-RUNNING-20261029.json`. No create retry, source-method amendment or public action occurred.


<a id="student-v21-topup16-first-chunk-health-20261029"></a>
## 2026-10-29 — Student v2.1 farms13–16: first-chunk startup health only

Under prospective root authorizationcff4bfa, acquired the exact first c000 archive from each of the four new farms: **4/4 passed** the unchanged strict whole-chunk inspector, **20 games each /80 total**, **34,729,258 bytes**. ETag-conditioned no-overwrite transfer, pinned source, local byte/hash and seed checks passed. This confirms valid initial engine output from all four producers; it is not full-wave terminal acceptance. Two other metadata-visible archives were not acquired or assessed.

The inspector found6,963 structural decision rows; that is **not an eligible-train count** and cannot be added to the accepted90,133. Full terminal coverage, whole-chunk health/quarantine, frozen source/catalog admission and the single full-union count remain before training. No roster/decision encoding, new eligibility measurement, training, battle ranking or source modification occurred. Counts have no CI/p-value; the health check is mechanical. Per-slot filenames, hashes, seeds and receipts are versioned in `experiments/cards/STUDENT-V21-TOPUP16-FIRST-CHUNKS-HEALTH-RESULT-20261029.json`.


## 2026-10-29 — One private relocated-checkout review succeeds; clean-machine test pending

Under prospective authorization93a8517, local detached source/fork clones completed the single preselected public, pseudonymized W1-01 replay in Draft-B mode: worker exit0, terminal `done`, **155.44 seconds**,25 turns and28 decision entries. The isolated app's `/api/health` GET passed with the engine present, template mode and verdicts disabled; Glimmer was deliberately unavailable on an unused probe port. Owned-process cleanup receipts confirm both isolated ports free. Root verified29 mechanical checks covering artifact hashes/modes, source commits, clean tracked clones, terminal state, schema/array lengths and health/cleanup receipts. No outcome or quality score was projected.

This borrowed the owner's installed dependencies and pinned cache, with no acquisition or install. It is one local path-relocation rehearsal, **not S6.4 clean-machine acceptance**, a hosting ten-replay smoke, a latency percentile or a model-quality result. Initial setup/import-probe and root metadata-schema errors are preserved; the worker ran once. CI/p-value do not apply to this single engineering check. Full provenance and limitations: `experiments/cards/S64-W1-PRIVATE-OFFLINE-REHEARSAL-RESULT-20261029.json`.


## 2026-10-29 — DPO R3 operational refresh through09:43UTC; no benchmark score

The existing six-H100 DPO job remainedRunning in the09:35status read. Six exact rank logs contain2,532native utilization samples (422perrank), independently reprojected mean95.8148%, linearp1098%, latest100% on allsix through09:43:12UTC.219,890bytes were downloaded against219,512bytes of earlier metadata;378bytes of livegrowth are disclosed. Hash/size checks apply to the saved bodies, without claiming post-read ETag stability. No optimizer/cache-completion/update marker or model-quality score was observed. Original04:56UTC Oct30 hardend/162totalnode-hour cap is unchanged. No new resource, model/evaluation call, process signal or watcher restart. Exact operational proof: `experiments/cards/DPO-R3-OPERATIONAL-REFRESH-20261029T0943Z.json`. This is GPU activity, not accuracy or a battle result.


## 2026-10-29 — DPO R3 operational refresh through10:56UTC; phase still unverified

The existing six-H100 job was Running at10:55:53UTC. Root independently reprojected2,970 native utilization samples (495perrank), mean96.3428%, linear p1098%, latest100%all6 through10:56:12UTC. Saved bodies total238,253bytes,168more than earlier live metadata. Exact original watcher identity was rechecked; no restart/signal. One bounded exact metadata probe for the six post-reference model-dtype receipts returned not_found for allsix, with no retries/body reads. Reference completion, trainable-policy load and optimizer updates remain unverified; missing receipts do not establish a new failure. The pinned runner has no stdout progress prints, so absence of stdout markers alone does not identify phase. No benchmark score or model-quality read. Original Oct30 04:56UTC hard end and162totalnode-hour cap remain. Source/hash/operational proof: `experiments/cards/DPO-R3-OPERATIONAL-REFRESH-20261029T1056Z.json`.


## 2026-10-29T14:35:19Z — owner-requested DPO and second-epoch operational checks

This is operational progress, not a new benchmark or quality score. Evidence: `experiments/cards/TRAINING-OPERATIONAL-CHECKS-20261029.json`. No model call, held-out read, new job, restart, active-source mutation or deadline extension.

- **DPO R3:** corrected the artifact route using the actual `workspaceblobstore` mapping. The earlier log-container empty listing is rejected. The mapped output contains reference-cache metadata dated05:43:45UTC and six-rank collective evidence dated05:45:04. Root read the2,757-byte collective receipt: all six ranks agree on gradient fingerprints, while `optimizer_step=false` and weights unchanged. This verifies the gradient startup path, not any completed optimizer update or learning improvement. Current optimizer count/loss remain unobservable; no final/checkpoint/optimizer artifact was listed. Source saves only after the complete epoch and has no safe live periodic-save hook. The run was preserved unchanged. Phase-rebased qualification estimate:05:45:04 +1.25×(60,019.13248 policy seconds+600 reserve)=**Oct30 02:47:58UTC**, about2h08 before hard04:56 (cancel04:41). This is not a measured live training-rate ETA; original whole-run qualification estimate03:57–03:58 remains historical.
- **Glimmer v2 second epoch:**39 operational bodies were independently size/hash checked. At14:23:12 acquisition, global step**3,795/6,994**, with298 new updates after restored3,497. Recent≈20-minute rate**8.06steps/min** (10-minute≈8.11). Recent120 GPU samples, ten/rank, give pooled mean**96.97%**, per-rank means**88.0–99.5%**. These exclude bootstrap. Conditional training ETA≈**21:00UTC**, or≈**21:15** with an explicit15-minute postflight allowance. The actual earlier graceful training barrier is22:10:06, outer cancellation22:45:18 and hard23:00:18. Current pace fits; no under60% or late-projection fix is indicated. The source requests local checkpoints every200steps, with final persistence still pending.

Original quality/validation/selection/arena rules and budgets remain unchanged. DPO update evidence is missing; Glimmer's progress does not establish improved validation. All model-number ledger entries remain unchanged and unfrozen.


## 2026-10-29T14:56:01.026856+00:00 — local product health component, not a new benchmark

Two existing loopback GET endpoints, `/api/health` and `/api/provenance`, returned HTTP200 and `ok=true`; the configured explanation mode remains `template`. Root checked the saved response hashes and transport records. No new engine review, model generation, quality evaluation, process or deployment. This establishes local component health only, not hosted10/10, clean-machine operation, semantic quality or OpenWeights eligibility. Private public-form discovery, fixed-ref build-window metadata and unverified quickstart preparation are recorded in `experiments/cards/PRODUCT-LOCAL-READINESS-PREP-20261029T1455Z.json`. Exact submission labels/limits and all aggregate release gates remain pending; score/selection/ledger unchanged.


## 2026-10-29T15:13:56.386203+00:00 — Deploy dependency attribution (documentation component)

Added Modal SDK1.2.4, httpx0.28.1 and Starlette1.7.0 to THIRD_PARTY.md from the already approved local wheels. Root checked metadata and embedded notice members for five packages, source bindings and the exact diff; historical Mac versions remain, while installed client and prospective Linux runtime versions are labelled separately. The original public-copy review rejected an ambiguous introductory hash-provenance sentence; the focused correction was accepted. The first root table assertion was overly broad and is preserved; the scoped check passes.

Evidence: `experiments/cards/P3-DEPLOY-ATTRIBUTION-CORRECTION-20261029.json`. This is a completed attribution component only, with no licence-compatibility, clean-install, hosting, final privacy/history or publication acceptance. No install/download/network/model/job/paid action or active-training source change; science scores and selection are unchanged.


## 2026-10-29T15:42:19.638453+00:00 — G1 second-epoch operational refresh (no quality read)

Existing corrected job `hrsft-20261029-g1ep2r2-405667bc`, sourceace4851: all25 operational bodies rehashed. Acquisition finished15:34:58UTC. Global step4,367/6,994,870updates beyond restored3,497; no held-out score or selection. Root's fixed closest-point windows measure8.1004/min over≈10minutes and8.1119/min over≈20minutes. Latest10GPU samples perrank (120total) average96.425%; minimumrank mean93.8%. Conditional20-minute projection gives training≈20:58:49UTC plus15-minute saving/wrap-up≈21:13:49, before22:10:06graceful/22:45:18cancel/23:00:18hard. Anchor is acquisition completion, not an unavailable per-step UTC. Current GPU/time checks indicate no run change.

Evidence: `experiments/cards/G1-OPERATIONAL-REFRESH-20261029T1535Z.json`. Earlier failure/debit, acquisition/interpretation/checker corrections and private non-secret identifier-output incident remain recorded. No new job, source/model mutation, restart or deadline extension; DPO update/loss evidence remains unobserved and its qualification-only ETA unchanged. Model scores/selection unchanged.


## 2026-10-29T15:55:31.893818+00:00 — Private v8 burned-caption component

The existing three-minute v7 had optional mov_text subtitles; it did not satisfy the burned-caption component. Private v8 now burns all14 substantive cues in a240px band below the complete1920×1080 source picture. Actual binary:1920×1320,180seconds/5,400frames/30fps, video only. Root checked exact text/times and14band hashes/bounds, full binary frame count and the450comparison frames at82–97s. Sol and root inspected the1fps180sample audit and readable caption/context/credits; no official art observed in those samples.

The initial strict preservation check failed on1,289later picture frames, first3,601; one range-tag repair made identical output and stopped. Original strictREJECT remains. A separate manual fallback accepted the caption/readability component with that encoding difference explicit; no all-picture pixel-equality claim. Screen 'replay flows' versus caption 'engineering flows' was reviewed as the same flow unit, not three unique games.

Evidence: `experiments/cards/VIDEO-PRIVATE-BURNED-CAPTIONS-V8.json`. Voice, actual two-viewer feedback, model-number freeze and overall video acceptance remain pending; this is private preparation, no publication or hosted-playback guarantee. Sourcev7 and all39ledger entries/scores/selection remain unchanged; no new model, app capture, dependency install, paid action or experiment.


## 2026-10-29T16:13:50.823161+00:00 — Private 40-second teaser and copy component

The reviewed private S8.3 cut uses four existing v8 spans (0–5,82–97,97–105,168–180seconds). Actual binary is40.000seconds/1,200frames,1920×1320,30fps,H.264/yuv420p,one video stream/no audio or subtitle stream. The whole15-second comparison remains adjacent to the8-second template/source caveat and12-second credits/fan-made/18+card. Root independently counted decoded frames, verified source/output hashes and inspected the contact sheet plus four full-size frames. Sol accepted one original actual visual/public-copy review. Gallery and277-character X drafts remain private, without a destination link.

Evidence: `experiments/cards/VIDEO-PRIVATE-TEASER-S83-20261029.json`. Ordinary recoding differences are not an equality gate; private rough-cut labels and original source-card times remain. Sampled inspection is not a full-motion art scan. S8.3 owner/final-copy OD18 acceptance, publication, and the full180-second video's actual viewer/freeze gates remain pending. No new quality metric, model/app capture, install, paid action, public post or source-video change; all39ledger entries remain unchanged/unfrozen.


## 2026-10-29T16:19:13.073826+00:00 — Observed submission fields aligned (preparation component)

A read-only existing browser session exposed the official submission modal:12fields/controls,8required,3optional, plus an unmarked Team Members control. No text limit was exposed; application/server limits remain unknown. Open Weights/Agentic controls were not observed in this modal; later steps remain unknown. No field values or account identity were retained, no fields/consent/submission were changed.

Handoffcommitd38aed1 aligns the private draft with that exact order and adds distinct Meta-use and Meta-importance answers after one original public-copy review. Root rechecked source/patch/draft hashes, all labels/markers and unchanged v5 short/long descriptions. Evidence: `experiments/cards/SUBMISSION-OBSERVED-FORM-ALIGNMENT-20261029.json`. This closes field-discovery preparation only: actual owner details, public URLs, final video/release and permission/submission remain pending. No model call, new science score or change to39ledger entries/selection.


## 2026-10-29T16:30:28.935062+00:00 — README local setup disclosure

Integrated one reviewed paragraph distinguishing the dated development-Mac installation table from a fresh-machine recipe. Root checked exact source/patch/candidate bytes and the existing offline CLI documentation. Sol accepted one original changed-copy review. Fresh clone/isolated venv/local review acceptance remains false; the app quickstart remains in preparation. Evidence: `experiments/cards/README-LOCAL-SETUP-DISCLOSURE-20261029.json`. No dependency acquisition, install, build, model/engine call, source/A1 mutation, paid action or publication. All39model ledger entries unchanged/unfrozen.


## 2026-10-29T16:37:01.627220+00:00 — Nonfinal build-history disclosure extension

Added29 reachable main commits after the original9f69f1a snapshot through exact2db38a9, with actual committer UTCs and shortened hashes. Root independently matched all29 rows against Git metadata and preserved historical counts, the first-feature table and16-versus-14 pre-kickoff disclosure verbatim. V1's Markdown table split is retained; the formatting-only v2 adds a header, with no data change. One original public-copy/provenance review accepted v2. Evidence: `experiments/cards/BUILD-LEDGER-EXTENSION-20261029.json`. This is a bounded nonfinal capture, not final release-history/privacy or publication acceptance. No timestamp/history rewriting, experiment, protected data read, install or public action.


## 2026-10-29T16:40:05.093934+00:00 — Local Open Weights evidence page

Added the reviewed, dated `OPEN_WEIGHTS.md` and its README link. It joins the captured official-file/provenance match and accepted base-Glimmer integration with the separate selected partial-epoch XL6 gameplay study, retaining all original counts, intervals, timeout/development-team limits and pending product/partner gates. Root checked the four accepted source records, existing local links and copied disclosure. One original public-copy review plus a focused standard-notice/link check accepted the actual bytes. Evidence: `experiments/cards/OPEN-WEIGHTS-PAGE-PREPARATION-20261029.json`. No current live-serving or prose PASS, partner verification, prize eligibility, public URL or publication claim. No model/API call, fresh model hash, install, paid action or new benchmark; all39ledger entries remain unchanged/unfrozen.


## 2026-10-29T16:47:49.327252+00:00 — G1 operational refresh from saved non-atomic snapshot

At acquisition completion16:40:49UTC, corrected G1 reached4,907/6,994 steps:1,410 contiguous new updates beyond restored3,497. Root rehashed13 saved operational bodies (905,510bytes), checked complete JSONL/monotone elapsed time and recomputed10/20-minute rates8.0434/8.0454steps/min. Latest ten samples per rank (120total) average97.8167%GPU, rank means95.7–99.6%; one individual59%sample, no sustained rank-window mean below60%. Conditional20-minute forecast: training21:00:14UTC, saving/wrap-up21:15:14 with15-minute allowance, before22:10:06graceful/22:45:18cancel/23:00:18hard. No run change indicated; no quality/selection/persistence claim.

Evidence: `experiments/cards/G1-OPERATIONAL-REFRESH-20261029T1640Z.json`. A literal-env invocation read no bodies; one same-route correction followed. Earlier metadata listed711,218stepbytes; complete acquired body712,736 (+1,518) stopped the strict guard. Root locally validated saved bodies with actual hashes; no further fetch, stable ETag/atomic claim or inferred cause. A local NameError and root10-minute startpoint assertion/absent-path commit failure were corrected and preserved; both window arithmetic paths match their actual points, with20-minute agreement. These live operational snapshots do not relax immutable adapter/evaluation gates. Original source/budget/deadlines unchanged; DPO updates/loss remain unobserved, and all39score entries unchanged/unfrozen.


## 2026-10-29T17:05:11.458183+00:00 — README reserved-team history correction

The Teams section now identifies the reserve counts as the Oct26 split record and cites the completed Oct28 sealed study. Counts are unchanged; the page no longer implies that the reserve is currently untouched. PREREGISTRATION, the final sealed RESULTS anchor and DATA support the qualifier. Root exact-delta/source/link checks and one original Sol public-copy review passed. No raw teams, estimator, engine/model run, new outcome or release acceptance. Evidence: `experiments/cards/README-TEAMS-HISTORY-CORRECTION-20261029.json`.


## 2026-10-29T17:06:58.630084+00:00 — Open Weights citation fits planned public source selection

One citation in `OPEN_WEIGHTS.md` now targets the existing clean/pooled G1 RESULTS anchor instead of an operational card excluded from the source proposal. Root verified the exact one-link delta and current/frozen468 anchor support; original narrow public-copy review ACCEPT. The next public selection must still add the page and carry the README link. No export was regenerated and no scan/publication, new measurement or overall release PASS occurred. Evidence: `experiments/cards/OPEN-WEIGHTS-EXPORT-LINK-20261029.json`.


## 2026-10-29T17:25:42.482492+00:00 — Prospective portable clean CLI setup source

The Mac arm64/CPython3.13 README procedure and `tools/setup/` now accept explicit local source/tool/input paths, fresh clones and isolated dependency/cache directories. The bounded installer records new product-input hashes, supports chunked transfers within caps, vendors Cargo offline and cleans up its own failed child stages. Root verified exact64artifact projections and ran9synthetic tests successfully; one original source/scope/public-step review ACCEPT. No dependency acquisition/install/build or actual fresh review occurred. S6.4 remains pending specific acquisition/install approval and the literal new review. Python socket denial is not OS-level native isolation. Existing engine science, source locks and39model scores unchanged. Evidence: `experiments/cards/CLEAN-CLI-PORTABLE-SOURCE-20261029.json`.


## 2026-10-29T17:38:03.465411+00:00 — Clean-machine attempt stopped at set-data HTTP403; S6.4 not met

The owner-approved single local attempt used committed main `872bc9624ff1e16fb31b0f25d4723176bb660fa7` and the fixed fork. All64pinned package/crate files downloaded and passed exact size/SHA checks (32,411,866bytes). The first configured set-data feed, `https://data.foulplay.cc/gen9ou/pokemon_full_sets.json`, returned HTTP403 Forbidden. All four new cache destinations and the data receipt are absent. The driver stopped, terminalexit1, before any fresh venv/install/native build or new review. No retry or alternate source was used; the refusal cause is unknown. Root verified raw/log/source/file evidence; one original actual-result review ACCEPTS the truthful FAIL. S6.4 remains unmet and the working local app remains the fallback. All outputs stay private; model benchmarks and selection are unchanged. Evidence: `experiments/cards/CLEAN-CLI-ACTUAL-ATTEMPT-20261029.json`.


## 2026-10-29T17:57:59.431070+00:00 — Clean CLI source-only HTTP failure diagnostic

The existing allowlisted data opener now records only the configured URL, stage, HTTP status and feed index in an exclusive private failure receipt, then stops. It does not read the error body/headers or call the next feed. Sol accepted the original narrowed source review and root passed10 synthetic tests. No real request, retry, new install/build or product review occurred. The owner-approved872 one-shot HTTP403 failure is unchanged; S6.4 remains unmet. Sources, request headers, transfer caps and success/retry policy remain unchanged. Evidence: `experiments/cards/CLEAN-CLI-HTTP-DIAGNOSTIC-SOURCE-20261029.json`. Model results/selection/ledger unchanged.


## 2026-10-29T17:58:18.512215+00:00 — G1 operational refresh, original guards retained

At acquisition completion17:46:15UTC, corrected G1 reached5,436/6,994steps,1,939new updates after restored3,497. Root independently checked all13 saved operational body hashes (1,244,839bytes), completeJSONL/contiguous steps and monotone elapsed time. Closest-point10/20-minute rates are8.0414/8.0605steps/min. The latest ten GPU samples perrank (120total) average97.6917%; rank means94.0–99.2%, latest99–100%. No sustained under60% window or run change is indicated. Conditional conservative training21:00:00UTC plus15-minute saving/wrap-up allowance gives21:15:00, before original22:10:06graceful/22:45:18cancel/23:00:18hard. ETA anchors acquisition completion, not unavailable per-stepUTC.

The live step body grew2,552bytes beyond its listing; actual saved bytes/hashes were checked without an atomic/stable-ETag claim. Local schema/window corrections and one noncredential identifier-output mistake are retained privately without repeating the identifier. Evidence: `experiments/cards/G1-OPERATIONAL-REFRESH-20261029T1746Z.json`. This is operational progress only; full additional epoch/final persistence/validation/selection remain pending. No quality read, new job, restart, active-source mutation or deadline extension. All39model-number entries unchanged/unfrozen.


## 2026-10-29T18:00:16.871174+00:00 — DPO six-rank GPU activity refresh only

Six bounded reads of the existing native GPU telemetry logs totaled344,236bytes; root rehashed every body and independently recomputed the latest ten valid samples perrank. At observed samples through17:58:13UTC, pooled utilization is99.7833% over60samples; six rank means99.2–100%, latest100% each. Live-range limits remain explicit: no atomic or latest-blob-end claim. No job-status, checkpoint/artifact metadata, quality or model call.

This is activity only: completed optimizer updates, current loss and checkpoint/final evidence remain unobserved. The qualification-phase-onlyETA remains Oct30 02:47:58UTC versus cancel04:41/hard04:56; source saves only at full epoch and has no safe live-save hook. No restart, source injection or extension. Evidence: `experiments/cards/DPO-GPU-ACTIVITY-20261029T1759Z.json`. All39model-number entries unchanged/unfrozen.


## 2026-10-29T18:22:07.108326+00:00 — Private selected-source privacy component refreshed

Created one private source snapshot pinned to committed main `8e39462`:478files,6,617,192bytes. It retains461accepted base files unchanged, overlays seven selected committed files and adds Open Weights evidence plus nine setup files. The exact-member tar matches every staged path/mode/byte; accepted export-only privacy repairs and proposed GPL text are preserved. The registered22identifier+22personal+1secret patterns produce zero content/path hits; relative-tree Gitleaks reports zero findings. All22local inline Markdown links resolve; five remote links were not fetched. Root actual-byte checks and one independent original actual-source/privacy review accepted this bounded component.

Evidence: `experiments/cards/P3-PRIVATE-SELECTED-SOURCE-20261029T1809Z.json`. Two current SFTtests, unselected operational cards and the working video ledger remain outside selection. This is a nonfinal private snapshot, with no all-current-main, complete SFTsuite, legal/licence adoption, clean-machine, hosted smoke or publication PASS. No main/A1/Git/tag/Benchmark-source mutation or dirty-file copy; no network/model/job/build/install/public action. All39model results unchanged/unfrozen.


## 2026-10-29T18:26:25.797606+00:00 — Prospective clean-CLI package attribution metadata

Rehashed the64already acquired archives (9wheels,55Cargo.lock crates;32,411,866bytes) against the frozen manifest and failed-attempt record. Root independently checked wheelMETADATA/crateCargo.toml declarations and actual archive member names:all64declare licence metadata;62contain notice filenames, while r-efi and wasip2 do not. `THIRD_PARTY.md` now links a64-row `THIRD_PARTY_SETUP.md` with exact declared metadata and distinct notice basenames, including platform-specific/transitive crates. Root checked every row and local link; one original changed-copy/provenance review accepted it. This is metadata attribution only, not licence compatibility/redistribution/adoption or a completed installation/build. No licence bodies, new downloads, installs, builds or product review.

The original patch omitted the new-file hunk marker and `git apply` created an empty file despite exit0; root final-byte checking caught it before commit and wrote the exact already-reviewed candidate bytes. Original patch/correction retained. Evidence: `experiments/cards/P3-CLEAN-CLI-PACKAGE-NOTICES-20261029.json`. The478private source snapshot remains immutable at its8e pin; a later final selected-source refresh must include this notice file and updated attribution. No repeat scan/export now. Original cleanHTTP403failure/S6.4notmet and all39model results remain unchanged.


## 2026-10-29T18:44:51.120293+00:00 — G1 operational progress for the scheduled owner report

At acquisition completion18:40:34UTC, corrected G1 reached5,872/6,994steps,2,375new updates after restored3,497. Root checked all13operational body hashes and1,525,121actual bytes, complete JSONL/contiguous updates/monotone elapsed time. Closest20-minute rate8.0802steps/min; latest ten GPU samples perrank (120total) average99.1083%, rank means96.8–99.5%. Conditional conservative saving/wrap-up ETA21:14:26UTC includes15minutes after training and fits original22:10:06graceful/22:45:18cancel/23:00:18hard. No underuse/time fix indicated.

Body/listed sizes matched in this bounded capture; this is not an atomic/stable-ETag or per-stepUTC claim. Final epoch/persistence, original2428validation, committed selection and fixed400/Benchmarkarena remain pending. No quality read, newjob, source/model mutation, restart or extension. All39model numbers unchanged/unfrozen. Evidence: `experiments/cards/G1-OPERATIONAL-REFRESH-20261029T1840Z.json`.


<a id="dpo-r3-full-epoch-20261029"></a>

## 2026-10-29T19:06:39.803763+00:00 — DPO R3 full epoch and saved adapter accepted; quality pending

**Training and persistence accepted; quality is a separate test.** R3 completed one epoch on 100,000 preference pairs, with 2,084 optimizer updates and 16,672 slots per rank. All six rank reports agree. The terminal records 57,234.132 seconds; cloud completion was observed at 18:36:54 UTC, before the original Friday 04:56 hard end. The final trainable fingerprint differs from startup. Rank 0 saved the final PEFT adapter, config, README and optimizer after all six full-epoch reports arrived.

Root and the original independent review streamed the four final files plus the opaque reference cache: 2,314,873,511 bytes. Every SHA and size matches the terminal and recorded stable metadata. Root checked 94 frozen files and 19 source pins. Header-only inspection found 728 F32 LoRA tensors, r32/alpha16; no model, optimizer or cache rows were loaded. Final model SHA `f8388d8a1841865929cd2793d46724ad4b9bac14f815fc063d590d88f88c4630`; config SHA `8ea2aaffe2c309092767f57cbaa39dbd704e092f733999c4e2e25c5f2dc965af`.

The original review is `d9b5129a8630540ec687af0b84c4f13fe56e44e5fa19b4374c725ef4c41099e2`; acquisition manifest `8130f1dae757a1fe760f10c36ea1d36c150f5ecb2e0a1b847dc2bad6349d7418`. The startup collective still says `optimizer_step=false`: it was a gradient/startup check, separate from the full-epoch reports. Earlier unobservable counters and loss remain dated history. The original final-only save completed; no periodic checkpoint, live injection, restart or extension was added. Preserve both charged failures, the original 12-hour NO-GO and the owner's 27-hour/162-node-hour cap. No training-completion claim establishes improved quality, selection, or release. The unchanged fixed-400 test and Benchmark-owned arena rules remain separate. See `experiments/cards/DPO-R3-FULL-EPOCH-PERSISTENCE-20261029.json`.

## 2026-10-29 19:18 UTC — DPO R3 fixed-v0 400 evaluation created

Root created `hrdpo-fixed400-20261029-r3-f8388d8a` once at 19:15:00.076 UTC, using one H100 with a 3,600-second platform limit: at most one node-hour. Actual source/cost/binding review `08a79f86` accepted the unchanged 15-file evaluator, fixed-400 data and final DPO adapter URI. Purpose authorization `ffffb59` was committed before creation. Root verified the physical code directory and all source/config/data hashes before and after the fresh, 18.74-second two-command admission. Twelve existing owned nodes plus one new node made 13 under the 25-node limit; observed physical state was 12 running, eight idle, with zero other-team nonterminal jobs in the permitted aggregate.

The exact name and output were absent, using the correct workspace artifact route. Watcher 10719 had a clean pending poll and matched identity before the sole API call. It observed Queued at 19:15:19 and Running at 19:16:21. Runner 20:15, cancellation 20:30 and hard end 20:45 UTC were unchanged. No partial score was read. Original selected IDs `dcf05ed8`, compressed data `3b0ba8fa` and the threshold of at least 231/400 recorded-choice matches were preserved. No G1/R1 gate, reselection, new training or battle create occurred here.

Training terminal to evaluation create took 38m06s, exceeding the 30-minute utilization target by 8m06s. Terminal discovery, full artifact proof and actual source/cost checks preceded creation; preserve that interval and those gates. Main `9c5f44f` and `experiments/cards/DPO-R3-FIXED400-CREATED-20261029.json` record the operation.

<a id="dpo-r3-fixed400-negative-20261029"></a>

## 2026-10-29T19:31:23.905283+00:00 — DPO R3 fixed-v0400 readout valid; registered gate failed

**DPO scored 214/400 = 53.50% teacher-forced recorded-choice top-1 agreement.** Selected XL6 scored **223/400 = 55.75%** on the exact same ordered cases. The observed difference is **−9 cases, or −2.25 percentage points**. The registered requirement of **at least 231/400 = 57.75%** was not met. **XL6 remains selected; DPO round 2 is not triggered.**

The root's descriptive Wilson 95% interval is 48.60–58.33%; it is not a paired significance test or a win-rate interval. The paired descriptive table is 205 both correct, nine DPO-only, 18 XL6-only and 168 neither. Secondary DPO scores are top-2 293/400 = 73.25%, top-3 337/400 = 84.25%, and policy-argmax agreement 215/400 = 53.75%. Policy-argmax is a different target from the primary recorded choice. Mean search-policy cross-entropy was 8.3112964991, versus XL6's 1.5936452909 on the same cases; this negative observation does not establish its cause. Zero generation was requested. Teacher-forced legal-menu renormalization does not establish arena action legality or p95 action latency.

The job completed at 19:21:30 UTC. Exactly three approved result bodies, 226,776 bytes, were acquired; five-object output metadata was stable before and after acquisition. Logs were excluded. Root and the original independent review verified the physical source and data, all 400 unique ordered IDs, choice/legal fields, boolean flags, zero-generation arguments, parent/teacher/raw summaries and actual job binding. Source `60abc879`, data `3b0ba8fa` and selected IDs `dcf05ed8` are unchanged; final adapter SHA `f8388d8a` binds the accepted R3 model. Review `95ed25a33131bfc0d64802c841eac38065112008f211bc988acbde1c44380045` **accepts the valid readout and rejects the fixed-400 quality gate**.

Training/persistence acceptance in `a50184e` remains true and separate from this negative quality result. A local projection initially guessed `summary.base`; the actual field is `base_same_prefix`. The KeyError and correction are preserved; no raw output, model or source changed. No checkpoint selection or model rerun occurred. Arenas and all battle creates/readouts remain with Benchmark under the original comparison, conversion and resource rules. Arena, legality and p95 results are unknown here. This is neither an overall playing-strength nor a release claim. `experiments/cards/DPO-R3-FIXED400-RESULT-20261029.json` binds the raw, manifest, root and review hashes.


<a id="g1-epoch2-full-epoch-20261029"></a>

## 2026-10-29T21:20:56.187768+00:00 — G1 second epoch completed and saved; validation pending

The corrected job completed at21:00:32UTC, before original22:10:06graceful/22:45:18cancel/23:00:18hard. All12ranks completed3,497additional optimizerupdates after restoring3,497, reaching6,994global steps and2totalepochs. Each restored728optimizerentries/counters and scheduler3497; rank0 terminal reports success. Bitwise replay is false: rank0 RNG was restored while the other11rank RNG states were new. Preserve the original failed firstattempt, its4node-hour debit and the108+4<=120training ceiling. No restart or extension.

Root verified42frozen evidencefiles and independently streamed the final5adapter/package bodies,795,101,549bytes; all SHA/size/private-mode and recorded pre/postproperties matched. Original independent actual review bdacb72ef52921278dab1b30b98fa425d21d0c87eb4cc28cd0dc822dc2cdc910 accepts the additional epoch and durable adapter. FinalmodelSHA f48a310273789bda35c85266196e0b15459d9447b351787d3b9cf3fca87357cd; configSHA 0cd02e23b0dc47359148dac1340a156ca0afa7ad0218f572e1f8be1b97b51d66. This establishes training and persistence, not improved quality or selection. Original2428validation and>=1416decision-before-fixed400 remain the next gate. XL6 remains selected. All39qualityledgerentries and currentvideo remain unchanged/unfrozen. Evidence: experiments/cards/G1-EPOCH2-FULL-EPOCH-PERSISTENCE-20261029.json.


## 2026-10-29T21:23:15.103354+00:00 — Original G1 full validation created once

Root created `hrval-20261029-g1e2-full2428-405667bc` at21:21:58.209UTC,21m26s after producer completion. OneH100,7,200-second platform timeout (<=2node-hours), within the originally accepted5node-hour method ceiling. Main1fdaf7e committed the actual terminal acceptance and source/cost/purpose beforecreate. The exact10accepted evaluator/data bytes and original2428/pairedcompanion closure are unchanged; current final G1 adapterpair f48a3102/0cd02e23 and sameofflinebase bind the job. No new method or model training.

Fresh two-command admission took12.97seconds:0owned nonterminal nodes plus1newunder25;2other-team nonterminal jobs in the permittedaggregate. Physical node states were4running/16idle, not GPU utilization. Exactname/outputabsent, identity-bound watcher25239/cleanfirstpending and source/config/dataURI checks before/afteradmission and aftercreate passed. WorkloadOct30 00:00/cancel00:15/hard00:30UTC. Conditional planning resultETA22:00UTC; this is not current measured throughput or a guaranteed completiontime. No output/quality read. Selection still requires>=1416/2428 and a committed decision beforefixed400; XL6 remainsselected. Battle/arena work staysBenchmark-owned and its separatepause is preserved. Evidence: experiments/cards/G1-EPOCH2-FULLVAL-CREATED-20261029.json.


<a id="g1-epoch2-original-validation-20261029"></a>

## 2026-10-29T22:05:12.306528+00:00 — G1 second epoch passes original validation; selected before fixed-400

**SELECTED under the original R1 rule:** final G1 second-epoch adapter matched the engine-policy argmax on **1,451/2,428 = 59.7611%**, 35 cases above the registered minimum1,416. Original XL6 scored1,379/2,428 (56.7957%); the observed increase is72cases/+2.9654percentagepoints on the same heldout action identities. First-epoch v2 remains the separate historical1,386/2,428 (57.0840%), NOTSELECTED in6a9b7d6; that decision is unchanged. Mean policy cross-entropy is1.5455330891021022 using math.fsum over2428. These correlated offline engine-target metrics are not battle winrates, paired significance or causal playing-strength evidence.

Root and the original independent review21679cd7 verified the exact source/config/data/adapter/job binding, all four completed result bodies (1,360,013bytes), original input population2,666 in each pairedsource (2,428actions+238reads), and exactly2,428unique teacher action rows. The238readsource records are not another scored readarm. Action-ID digest95b5abff, original dataaed05c91/7ea29968, template/tokenizer and zero-generation settings are unchanged. Final model f48a3102/config0cd02e23 match the independently verified training artifact. Completed was observed21:49:16UTC; an exactplatformend is not exposed in the savedshow and is not invented.

This record is committed before any current G1fixed400 job or quality read. Fixed400 remains a descriptive next test and cannot reverse validation selection; only the later200-game seed2000arena request is selection-conditional and Benchmark-owned. Benchmark's separateidentityownerreview/PAUSED remains in force. No DPO2 or further training is authorized by this selection. The frozenA4engine and templateexplainer remain unchanged; there is no serving/strength/ship claim. Currentvideo and existing39workingledgerentries are unchanged at this decision commit; any new workingentry follows this committed decision and remains unfrozen.

Exact request/route/list/body evidence and mechanical failures are retained privately. Two pre-listing local assertions compared a receiptfile pointer to URI data and mishandled a trailing slash; both stopped before listing/body requests. The final successful collector then caught its ownSystemExit0 and reported an erroneous exit10 after complete acquisition. Raw CLI/body success and complete hashes were verified; no cloud request/model evaluation was repeated. Card: experiments/cards/G1-EPOCH2-ORIGINAL-VAL-DECISION-20261029.json.


## 2026-10-29T22:09:31.999470+00:00 — Original G1 fixed-v0 400 descriptive evaluation created

After SELECTED decision06c276a and source/cost/purposefb5e3f5, root created `hrval-20261029-g1e2-fixed400-405667bc` once at22:07:09.580UTC. OneH100,3600s<=1node-hour. Current status Running; no currentfixed400 score/body was read. The original15-file code/data and fixed-v0400IDs/data remain unchanged; finalG1 model f48a3102/config0cd02e23 bind the readonlyadapter. This descriptive readout cannot reverse originalvalidationselection or authorizeDPO2/newtraining.

Fresh two-commandadmission took16.52s:0existingownednodes+1under25,0othernonterminalaggregate, physical20idle. Exactname/output absent, liveidentitywatcher28790/cleanpending and source/config/dataURI checks beforeafteradmission/aftercreate passed. WorkloadOct30 00:00/cancel00:15/hard00:30UTC. Create followed the firstobserved fullval Completed by17m54; exact fullval platformend was not exposed and is not invented. ConditionalplanningresultETA22:25UTC, not currentmeasuredthroughput. Actualsource-only postterminal reference identifies summary.json/eval_teacher.json/eval_teacher.rows.jsonl; no artifact/logbody read beforeterminal. Benchmarkoriginalidentityownerreview/PAUSED remains; no arena request yet. Workingledger has40entries, old39 unchanged, allunfrozen/currentvideo unchanged. Card: experiments/cards/G1-EPOCH2-FIXED400-CREATED-20261029.json.
