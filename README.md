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
**Pool** (`../data/teams/`):
- **Source:** [metamon-teams v5](https://huggingface.co/datasets/jakegrigsby/metamon-teams) Gen9 OU.
  - `hl_05_26`: 43,764 high-ladder teams (May 2026). Hidden details are *predicted* from replays, so they are plausible teams, not ground truth.
  - `competitive`: 16 expert sample teams.
- **Validated** with `tools/validate_teams.js` (Showdown's own TeamValidator, pinned rules):
  - all 43,764 high-ladder teams are legal;
  - 11 of 16 sample teams are legal (5 use Tera Blast, now banned);
  - sanity check: the validator rejects the known-bad example team.
- **Diversity:** 386 species, 8,521 distinct 6-species rosters.
- **Split** `data/teams/splits/hl_05_26-gen9ou-split-v1.json` (SHA-256 `575b5dc2…`):
  - **30% of rosters (2,556 rosters, 14,136 teams) reserved and untouched** for a future held-out or sealed set;
  - 29,628 development teams.
- **Installed in the fork:**
  - `gen9/hr_hl_dev`: 2,000 development teams;
  - `gen9/hr_comp`: 11 legal expert teams.
- **Pairing:** `--team-pool gen9/hr_hl_dev --seed N` draws two different teams per game. Tested: 6 of 6 games completed, 27–92 s each.

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
| C1 | 4 | 2000 | 2 | ≈2 s per move. **Crashes (`BrokenProcessPool`) with 2 threads per world: use C1t1.** |
| C1t1 | 4 | 2000 | 1 | ≈2 s per move; one game ≈179 s on the Mac |
| C2 | 8 | 3500 | 1 | ≈3.5 s per move, ≈7 s early |

Foul Play doubles the number of worlds while the opponent has revealed fewer than 3 moves.

**Server isolation:** each run starts its own private server on `--port` (default 18010) and refuses a busy port. Run parallel jobs on different ports (18011, 18012, …), or use `--reuse-server` for a server you started yourself.

**Oracle (evaluator-only).** `--oracle-a-p P --oracle-fields all|item|ability|tera|moves|spread` gives side A side B's true sets with probability P, per Pokémon and per sampled world (`fp/hardread_oracle.py`, hooked into `prepare_battles`). Tested: at p = 1, 500 of 500 substitutions matched the true team; at p = 0.5, about half the worlds.

**Output.** Results go to `runs/<run_id>/games.jsonl` and `summary.json`, with per-bot logs in `runs/<run_id>/`.

## Measured on the Mac (M4 Max, 16 cores)
- **F1:** one game ≈25–35 s; 8 of 8 games completed at 4 in parallel → **≈460 games/hour**.
- **C1t1:** one game ≈179 s, ≈0.4 GB per search process.
- **C1 with 2 threads per world:** search worker crash. Avoid.

## Data that Foul Play downloads on first use (cached; to be pinned and hashed)
- Smogon chaos stats for the previous month (`smogon.com/stats`);
- data.foulplay.cc team datasets;
- Showdown sets.

**Caches live under** `foul-play/fp/data/`.

## Azure ML jobs (cluster `${HR_AZ_CLUSTER}`, ≥4 nodes always on, 40 vCPU + 1× H100 NVL each)
Workspace `${HR_AZ_WS}`. Other lanes run jobs there too; never touch their jobs.
The scripts in `azure/` read the workspace settings from environment variables: `HR_AZ_SUBSCRIPTION`, `HR_AZ_RG`, `HR_AZ_WS`, `HR_AZ_CLUSTER`, `HR_AZ_STORAGE_ACCOUNT`, `HR_AZ_CONTAINER`. Export them, or put them in `handoff/env.sh` (private, never committed), which the scripts source when it exists.

**Environment:** reuses `pokemon-h100-axolotl-cuda13-training:20261026-9f7aa9a5704c` (Python 3.12, CUDA 13).

**Outputs:** `azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/hardread/<job>/`.

**Jobs:**
- `azure/farm_job.sh`: CPU farm test.
  - Setup: installs Node v24.13.0 and Rust 1.89.0, builds poke-engine, fetches Showdown at `c0845f33`.
  - Then runs timed F1, C1t1 and C2 batches.
- `azure/glimmer_speed/speed_test.py`: Glimmer serving speed.
  - Setup: llama.cpp `f8def7fe` built with CUDA for sm_90; pinned Q4_K_M GGUF downloaded with its hash checked.
  - Replays 31 real battle requests with reasoning low vs off, 1 vs 4 slots.
- **Attempts** (staged code, `job.yml`, create responses) are kept under `azure/attempts/<job>/`.
