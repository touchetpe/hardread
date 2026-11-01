# HardRead: was it luck, or was it you?

HardRead reviews luck and moves with Foul Play and Meta’s Muse Glimmer. Its shared bot account ranked #199 on 30 October 2026.

HardRead reviews finished competitive Pokémon replays. It uses Foul Play search to estimate chance, inspect selected decisions and test a player's stated read against engine alternatives. The current product explains cited results with labeled templates while live model prose awaits its final checks.

Foul Play is pmariglia’s independent GPL-3.0 open-source search bot and winner of the [2025 PokéAgent Gen 9 OU tournament](https://pokeagent.github.io/leaderboard.html) ([upstream project](https://github.com/pmariglia/foul-play)). HardRead builds the retrospective review, luck and stated-read layers around that credited baseline; the tournament result belongs to Foul Play, not HardRead.

**Dated ladder snapshot.** HardRead builds on Foul Play. The earlier frozen Foul Play engine record was 77–34 across 111 official Gen 9 OU ladder games. Separately, the shared registered HardReadBot account ranked #199, with Elo 1,712 and GXE 71.9%, at 23:13 UTC on 29 October 2026 (00:13 CET on 30 October). Other agents also used the account. A later archived engine-only record was 103 wins and 60 losses over 163 games through 05:08 UTC on 30 October. Games against bots do not affect human ratings.

**Own-game showcase (local, 30 October 2026).** The local app displays three of HardReadBot’s own ladder games. Recorded decisions cite the bot’s choices captured before it acted. A separate engine review later estimates luck and a remaining swing from both players’ choices and other effects. Its win-probability calibration was fitted on human games, and own HP was approximated from public percentages; these estimates are not the bot’s original reasoning. These explanations currently use labeled templates; successful Glimmer selection for this showcase has not been demonstrated. Its private game inputs and derived per-game artifacts are not bundled with the source. See the [data policy](../DATA.md).

**Fan-made, not affiliated with Nintendo, The Pokémon Company or Game Freak. 18+.**
[Open-weight evidence (status as of 2026-10-29)](../OPEN_WEIGHTS.md)


## How HardRead differs from a typical AI analyzer

The comparator is the one-prompt wrapper described in the owner-relayed Q10 ruling. This is a product-shape comparison, not a measured benchmark of every AI analyzer. [Q10](#organizer-ruling)

| | One-prompt AI analyzer | HardRead |
|---|---|---|
| Who does the analysis | An LLM responds from a prompt. | Foul Play's independent search engine samples possible hidden sets and continuations. HardRead adds finished-replay review, luck estimation and stated-read testing. [Foul Play](https://github.com/pmariglia/foul-play) |
| Where the numbers come from | The model generates answer text. | The engine computes underlying values. In the accepted D1 flow, application code fills displayed numeric slots from cited tool fields, and a verifier rejects uncited numeric claims. This checks provenance, not whether every estimate or sentence is correct. [D1](../RESULTS.md#d1-template-ui-20261028) |
| What has been measured | No comparison result is asserted here for generic analyzers. | On the registered 60k/.08 offered-decision cohort, false decisive verdicts were **18/184 = 9.78%** (Wilson 95% CI **6.28–14.93%**); separate bot self-play luck mirror correlation was **−0.81**; the shipped bridge passed its Draft-B gate on **292 game-sides**; and the S2.2 calibration upgrade **failed** and was not switched on. [VT1](../RESULTS.md#src-vt1-60k-08) [LUCK-SP](../RESULTS.md#src-luck-v2-self-play) [BRIDGE](../RESULTS.md#src-bridge-draft-b) [S2.2](../RESULTS.md#src-s22-calibration-fail) |
| What the player can test | A free-form question. | In the selected review flow, a player can enter a stated read with a team export and compare a chosen move with engine alternatives using three seeded searches and a within-noise verdict rule. D1 passed three selected template-based engineering flows; this is not human accuracy or a general end-to-end rate. [D1](../RESULTS.md#d1-template-ui-20261028) [VT1](../RESULTS.md#src-vt1-60k-08) |
| Role of Muse Glimmer | The model supplies the analysis. | Base Muse Glimmer selects read-only engine evidence and can propose editable fields from a player's note. Current product explanations use labeled templates while full C10 and live-prose checks remain pending. A1 v2 recorder returned **59/60** answers after at most one repair. Separately, a blinded Sol review flagged **3/60** sentences in a seeded sample of 60 from 120. **15 owner labels** and `bound_score` remain pending; this is not a live-prose or Agentic ship PASS. A separately fine-tuned Glimmer policy is game-playing research, not the review explainer. [A1-V2](../RESULTS.md#src-a1-v2-owner-pending) |

D1 §3 accepted three selected template-based engineering UI flows, with **42 primary screenshots plus two camera-only supplements, 24 clicked citations and 12 matching numeric values**. This is not a blind quality rate, acceptance of the original fixed C10/P3 cohort's **2/3** result, or a live-prose/Agentic PASS. [D1](../RESULTS.md#d1-template-ui-20261028)

Separate game-playing research: a **24.9M-parameter Student v2** scored **173/200 = 86.50%** against PokéChamp’s Abyssal heuristic bot (Wilson 95% CI **81.07–90.55%**) on partly seen development teams, with a timeout counted as a loss. It is not an unseen-team or general-strength claim, and it is not the review explainer. [STUDENT-V2 model](../RESULTS.md#src-student-v2-model) [STUDENT-V2 arena](../RESULTS.md#src-student-v2-arena)

Pokémon is the testbed; the method is decision review under hidden information.

<a id="organizer-ruling"></a>

The owner relayed the Oct 28 organizer ruling: HardRead is fair game as a real engine-backed tool, conditional on actual owner usefulness and clear README/demo differentiation from a one-prompt wrapper. This addresses the sports-analyzer example only; other submission, ownership, privacy, model and publication gates remain. [Q10](#organizer-ruling)

> **Local setup status.** The installation table below records the development Mac as of 2026-10-26; it is not a fresh-machine install recipe. The owner has approved first-party local cache-only runs from held private inputs on this Mac. The one-shot clean-test attempt reached the native-build stage and stopped cooperatively when its local work window closed; the native-engine build did not complete, and native installation/import and fresh-clone review were not reached. The data caches, manifest, and authorization remain outside the repository. See the [cache-only test evidence](../docs/setup-cache-attempt.md).

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
  - **At the 2026-10-26 split, 30% of rosters (2,556 rosters, 14,136 teams) were reserved and untouched** for later held-out or sealed evaluation;
  - 29,628 development teams.

This is the pre-test reservation record. A 1,000-team pool, one team per reserved roster, was subsequently used for the registered sealed 10,000-game test, completed on Oct 28 with no demonstrated improvement. The dated split is not a claim that the reserve remains untouched today. Raw sealed team contents remain private; only recorded metadata and aggregate outcomes are public. [Protocol](../PREREGISTRATION.md) [Sealed result](../RESULTS.md#sealed-full-s41-20261028) [Data](../DATA.md)

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

### Clean local CLI on Mac arm64 / CPython 3.13

These are prospective examples, not evidence of a completed install, native build, or fresh-clone review. The earlier approved live-feed attempt stopped at HTTP 403 before installation or build; it did not exercise the explicit input modes shown here. The owner’s standing local approval covers first-party local cache-only runs on this Mac. Live mode remains a separate prospective path requiring fresh authorization. Choose exactly one `--input-mode`; there is no automatic fallback.

Both modes require a completed replay log and matching own-team export, an existing approved engine sdist, the pinned Rust 1.89 toolchain, and a separate local Foul Play Git checkout containing commit `f061e63d3d588f30e72af2f4a7d12d3101896d02`. Foul Play is not included in this repository. The engine sdist must have SHA-256 `070010686f2aedff11e25137e696e301ccd80fd57c805d255464067fc905ca12`. Use the directly installed Rust/Cargo 1.89 binaries pinned by the setup helper; a rustup installer shim does not satisfy this requirement.

Set these variables to absolute paths before use: `PYTHON`, `RUST_BIN`, `ENGINE_SDIST`, `FOULPLAY_SOURCE`, `REPLAY`, `TEAM`, and `HARDREAD_SOURCE`. `HARDREAD_SOURCE` is the local HardRead checkout containing these setup instructions. Each example creates a fresh source clone, new runtime/build environments, and a private run directory; neither reuses a virtual environment, compiled dependency, app state, nor review output. For package/data acquisition, the owner-local mode uses the 64 previously verified package archives and four exact held cache snapshots; engine source, replay/team inputs, toolchain, and fresh local venv installs remain prerequisites. The pinned engine archive contains two paths that differ only by case; use case-sensitive APFS and preserve both without renaming, dropping, or overwriting either file. Before running these examples, set `TMPDIR` to an existing private directory on a mounted case-sensitive APFS volume, so the created `BASE` and `--run` stay on that filesystem. The owner’s standing local approval covers these first-party local cache-only builds and tests on this Mac; paid or external compute, external accounts, public posting, new downloads, and global software installs remain gated.

```bash
: "${PYTHON:?absolute path to CPython 3.13}" "${RUST_BIN:?absolute path to Rust 1.89 bin directory}" "${ENGINE_SDIST:?absolute path to approved engine sdist}" "${FOULPLAY_SOURCE:?absolute path to Foul Play git checkout}" "${REPLAY:?absolute path to completed replay}" "${TEAM:?absolute path to matching team export}" "${HARDREAD_SOURCE:?absolute path to HardRead git checkout}"
umask 077
BASE="$(mktemp -d "${TMPDIR:-/tmp}/hardread-clean.XXXXXXXX")"
HARDREAD_COMMIT="$(git -C "$HARDREAD_SOURCE" rev-parse HEAD)"
git clone --no-local --no-checkout "$HARDREAD_SOURCE" "$BASE/hardread"
git -C "$BASE/hardread" checkout --detach "$HARDREAD_COMMIT"
cd "$BASE/hardread"
COMMON_ARGS=(--python "$PYTHON" --rust-bin "$RUST_BIN" --engine-sdist "$ENGINE_SDIST" --foulplay-source "$FOULPLAY_SOURCE" --replay "$REPLAY" --team "$TEAM" --side p2 --run "$BASE/run")
```

**Live mode — prospective.** The consumed approved attempt is not an attempt with this mode flag. A future live invocation needs new authorization. It would use the 64 pinned package/crate inputs and fetch the four set-data inputs described below; their current availability is not asserted. Set-data redistribution rights are unverified, so keep any retrieved data private and do not redistribute it.

```bash
"$PYTHON" tools/setup/clean_cli.py --input-mode live "${COMMON_ARGS[@]}"
```

**Owner-local mode — first-party local cache-only route; clean-test attempt incomplete.** The earlier owner-approved feed-based attempt stopped at its first set-data fetch with HTTP 403, before installation or build. Do not retry that feed, change its source, or add a fallback. Four held snapshots, a private hash-bound manifest, and a separate authorization record are now staged outside this repository and are not bundled here. Standing approval covers first-party local cache-only runs on this Mac. The one-shot attempt passed the case-sensitive filesystem probe and held package/data archive checks, including both case-distinct engine files; it stopped cooperatively at `native_build` when the local work-window guard closed. The parent launcher ended after 15.96 seconds; the exact guard field was not retained. The native-engine build did not complete, native installation/import and review were not reached, and no clean-machine PASS is claimed. See the [cache-only test record](../docs/setup-cache-attempt.md).

Supply exactly one file for each cache destination below; do not substitute a different dataset or mix snapshots. If any role is unavailable, stop and report that missing role rather than switching to live mode:

- Full sets: `foul-play/fp/data/pkmn_sets_cache/gen9ou/pokemon_full_sets.json`
- Replay moves: `foul-play/fp/data/pkmn_sets_cache/gen9ou/replay_moves.json`
- Showdown sets: `foul-play/fp/data/pkmn_sets_cache/gen9ou/showdown_sets.json`
- Smogon stats: `foul-play/fp/data/smogon_stats_cache/gen9ou-0.json`

Each file must already be a cache-ready, nonempty UTF-8 JSON object. For the Smogon stats destination, supply the inner `data` object that is written to the cache, not the outer feed-response object; the owner-local stager does not unwrap that response. Keep all four snapshots and both records outside the repository.

The manifest must use schema `hardread-owner-cache-bundle-v1`, set `snapshot_date` and `provenance_record_id`, and contain exactly four rows in `files`. Each row must bind `cache_path`, manifest-relative `relative_file`, `size_bytes`, `sha256`, `source`, `capture_date`, and `permission_reference`. The separate authorization record must set `scope` to `private-local-S6.4-test`, bind the SHA-256 of the exact manifest bytes in `manifest_sha256`, and include a nonempty `decision_reference`. The setup checks the manifest's four destinations, safe relative paths, exact file sizes and SHA-256 values, and that each file is a nonempty UTF-8 JSON object. It checks that provenance and permission fields are present and dates parse, but cannot establish that those statements are true or that the owner has rights to use the snapshots; the owner must verify both.

This route reuses only the 64 previously verified package archives and the four held cache snapshots with their private manifest and authorization record; it still requires the pinned Foul Play checkout, replay/team inputs, Rust toolchain, fresh environments, and local native-engine build described above. The three absolute-path variables below are required for the owner-local invocation. This owner-local route is covered by the owner’s standing local approval; live-feed retries, paid or external compute, external accounts, public posting, new downloads, and global software installs remain gated. Before another run, verify the exact source/cache/manifest pins and a released local work window. The mode choice is explicit and exclusive: missing or mixed inputs fail closed, with no automatic network fallback. The observed attempt stopped during `native_build`; no completed native-engine build, native installation/import, or review is established, and it is not a clean-machine PASS. The recorded one-shot is consumed; a later distinct run needs a fresh private output and source/input/work-window admission.

```bash
: "${PACKAGE_ARCHIVE_ROOT:?absolute path to the verified 64 package/crate archive outputs}" "${OWNER_DATA_MANIFEST:?absolute path to the private four-snapshot manifest}" "${OWNER_AUTHORIZATION_RECORD:?absolute path to the owner authorization record}"
"$PYTHON" tools/setup/clean_cli.py --input-mode owner-local "${COMMON_ARGS[@]}" --package-archive-root "$PACKAGE_ARCHIVE_ROOT" --owner-data-manifest "$OWNER_DATA_MANIFEST" --owner-authorization-record "$OWNER_AUTHORIZATION_RECORD"
```

Select `--side p1` instead of `p2` if your team was player one. These examples do not establish a successful clean CLI review.
