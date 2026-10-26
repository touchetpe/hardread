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
