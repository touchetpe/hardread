# HardRead: engine lane

Working repo for the plan in [research/22-final-comparison.md](../research/22-final-comparison.md): the N1-lite route, with the v5.2-lean SOTA spine plus an asynchronous "Hax or You" replay review.

## What's installed (2026-10-26)

| Component | Where | Pin |
|---|---|---|
| Rust toolchain | `../environments/rust/` (`RUSTUP_HOME`, `CARGO_HOME`; no shell profile changes) | 1.89.0, minimal profile |
| poke-engine (Python bindings) | built into `../environments/foulplay` with `--features poke-engine/terastallization --no-default-features` | v0.0.48 (`bcf13823`) |
| Foul Play (upstream, pristine) | `../sources/foul-play` | `8f541f80` (Oct 3, 2026 data sync) |
| poke-engine source | `../sources/poke-engine` | tag v0.0.48 |
| Foul Play fork | `foul-play/` (branch `hardread`) | upstream + our patches |
| Python environment | `../environments/foulplay` | CPython 3.13.12, pip 24.2 |
| Private Showdown server | `showdown/runtime` → symlinks to `../sources/pokemon-showdown` (`c0845f33`, Oct 23) | loopback port **18010**, 4 simulator workers, guest login without the login server |

**Tests:** Foul Play's suite passes, 737 tests.

**Ports.** 18000 (battle harness) and 18002 (llama.cpp) belong to other lanes; never stop or reuse them.

## Patches on the `hardread` branch
1. **Local guest login:** on a `localhost`/`127.0.0.1` server, Foul Play sends `/trn name,0,` directly instead of calling the public login API.

## Teams
- `foul-play/fp/teams/teams/gen9/ou/example` is **rejected** by current Gen9 OU rules: Spore (Sleep Moves Clause) and Tera Blast (banned).
  - Foul Play doesn't detect the rejection and waits forever. **Always validate teams before using them:**
    ```sh
    cd showdown/runtime && node --preserve-symlinks --preserve-symlinks-main ./pokemon-showdown validate-team gen9ou < TEAMFILE
    ```
- `foul-play/fp/teams/teams/gen9/ou_hr/example_fixed`: the example with Spore → Clear Smog and Tera Blast → Phantom Force. It validates.

## Running games
```sh
/opt/homebrew/bin/python3.13 tools/run_match.py --games 4 --concurrency 2 \
  --cfg-a F1 --cfg-b F1 \
  --team-a gen9/ou_hr/example_fixed --team-b gen9/ou_hr/example_fixed --run-id my-run
```

**Search configurations** (`tools/run_match.py`):

| Name | Worlds | ms per world | Threads | Note |
|---|---|---|---|---|
| F1 | 2 | 100 | 1 | fast |
| C1 | 4 | 2000 | 2 | ≈2 s per move |
| C2 | 8 | 3500 | 1 | ≈3.5 s per move, ≈7 s early |

Foul Play doubles the number of worlds while the opponent has revealed fewer than 3 moves.

**Output.** Results go to `runs/<run_id>/games.jsonl` and `summary.json`, with per-bot logs in `runs/<run_id>/`.

## Data that Foul Play downloads on first use (cached; to be pinned and hashed)
- Smogon chaos stats for the previous month (`smogon.com/stats`);
- data.foulplay.cc team datasets;
- Showdown sets.

**Caches live under** `foul-play/fp/data/`.
