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
