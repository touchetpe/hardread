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
| aa-pool-F1-200 | A/A at F1, diverse pool, **no side swap** | **A (challenger) won 118/200 = 59%**, outside the 95% band (±6.9 points; p ≈ 0.01). 0 failures; median game 38 s. **Warning: possible seat bias, mixed up with team strength because sides weren't swapped.** Follow-up: `hraa-20261026-310d28` (1,000 side-swapped pairs on Azure). |

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
