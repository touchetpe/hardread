# Built during the hackathon: what was made when

This ledger lists every commit of the two HardRead repositories by date, so judges can see what was built during the Meta Global AI Developer Hackathon. It is generated from git history; the commands under *Verify* reproduce it.

- Repositories: `hardread` (this one, branch `main`, HEAD `ca8d3830b702`) and `hardread-foulplay` (our fork of Foul Play, branch `hardread`, HEAD `c04b38e0193f`; our commits are those after upstream `8f541f80f91f`).
- Generated: 2026-10-27 04:46 UTC. Commit times are git committer times in UTC (author and committer times are equal in both repositories).

## Time bands

| Band | From (UTC) | To (UTC) | Why this boundary | HardRead commits | Fork commits |
|---|---|---|---|---|---|
| 1. Before kickoff | — | 2026-10-26 16:00 | Kickoff Mon Oct 26, 12:00 PM ET (EDT, UTC−4) | 11 | 5 |
| 2. Kickoff → building period | 2026-10-26 16:00 | 2026-10-27 04:00 | The participant guide's building period starts Oct 27 (00:00 ET); the official rules' build window starts Oct 26 | 45 | 5 |
| 3. Building period | 2026-10-27 04:00 | 2026-11-05 02:00 | Submissions due Wed Nov 4, 9:00 PM ET (EST, UTC−5) | 12 | 0 |

**Before kickoff:** 16 commits between 2026-10-26 12:21:44 and 2026-10-26 14:30:26 UTC (about 2.1 h of work, ending 1.5 h before kickoff). They set up a self-play harness around the open-source Foul Play engine: a match runner, cloud job scripts, a team validator, an oracle hook for one science experiment, and decision logging. **None of them is a demo feature.** The product's verdict does not use the oracle. The review engine (band 2) reuses the decision log's public-view serializer (`fp/hardread_decision_log.py`), which was first written before kickoff and extended after it.

## Demo and science features: first commit

| Feature | Kind | File(s) | First commit | UTC | Band |
|---|---|---|---|---|---|
| Replay reconstruction (what each player knew, turn by turn) | product | `tools/review/reconstruct.py` | `3cc3859` (hardread) | 2026-10-26 18:46:46 | 2 |
| Search evaluation and win probability | product | `tools/review/evaluate.py`, `tools/review/value.py` | `3cc3859` (hardread) | 2026-10-26 18:46:46 | 2 |
| Win-probability calibration | product | `tools/review/calibrate.py` | `3cc3859` (hardread) | 2026-10-26 18:46:46 | 2 |
| Luck meter | product | `tools/review/luck.py` | `645311d` (hardread) | 2026-10-26 20:13:47 | 2 |
| Review JSON / CLI | product | `tools/review/review.py` | `3cc3859` (hardread) | 2026-10-26 18:46:46 | 2 |
| Review on human games (Showdown replays) | product | `tools/review/human_eval.py` | `9749eb0` (hardread) | 2026-10-26 22:48:36 | 2 |
| Bridge: Start -> Luck -> Choices -> Result | product | `tools/review/bridge.py` | `4373557` (hardread) | 2026-10-27 04:44:48 | 3 |
| Bridge validation | product | `tools/review/validate_bridge.py`, `tools/review/proposed/validate_bridge.py` | `b212084` (hardread) | 2026-10-27 04:11:32 | 3 |
| Verdict (Misplay / Misread / Defensible / Within noise) | product | `tools/review/verdict.py` | not committed yet | — | (3) |
| Glimmer explainer with verifier | product | `tools/agent/explain.py`, `tools/agent/verifier.py` | not committed yet | — | (3) |
| Note parser (what you were thinking -> stated read) | product | `tools/agent/parse_note.py` | not committed yet | — | (3) |
| Web app | product | `app/server.py` | not committed yet | — | (3) |
| Match runner (self-play harness) | science | `tools/run_match.py` | `c18ac3e` (hardread) | 2026-10-26 12:35:18 | 1 |
| Oracle injection (value-of-information experiment) | science | `fp/hardread_oracle.py` | `934ef59` (fork) | 2026-10-26 12:40:41 | 1 |
| Decision log | science | `fp/hardread_decision_log.py` | `3de9f0c` (fork) | 2026-10-26 14:30:26 | 1 |
| Pre-registration of the sealed test | science | `PREREGISTRATION.md` | `18e21c6` (hardread) | 2026-10-26 16:27:46 | 2 |
| Engine levers (value, double-half, student-blend, belief) | science | `fp/hardread_levers.py` | `df65fd8` (fork) | 2026-10-26 19:27:28 | 2 |
| Value-of-information analysis | science | `tools/analysis/voi.py` | `18e21c6` (hardread) | 2026-10-26 16:27:46 | 2 |
| 25M student policy/value model | science | `tools/student/model.py` | `36f6858` (hardread) | 2026-10-26 19:33:27 | 2 |
| Belief model | science | `tools/belief/model.py` | `4c6a2b3` (hardread) | 2026-10-26 19:15:04 | 2 |
| Glimmer fine-tuning pipeline | science | `tools/sft/serialize.py`, `azure/sft/run_sft.py` | `fe214be` (hardread) | 2026-10-26 19:39:59 | 2 |
| Arena vs PokeChamp's abyssal bot | science | `tools/arena/run_arena.py` | `f4583d6` (hardread) | 2026-10-26 19:32:03 | 2 |

## Band 1: before kickoff (16 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-26 12:21:44 | fork | `136244c` | Local guest login for private Showdown servers | Fork: log in as a guest on a local server (no account needed). |
| 2026-10-26 12:35:18 | hardread | `c18ac3e` | Engine lane: private Showdown runtime (18010), match runner, setup notes | Local Showdown server config and a script that plays Foul Play against itself (science harness). |
| 2026-10-26 12:35:29 | fork | `3a73c06` | Add a Gen9 OU-valid version of the example team | Fork: Foul Play's example team made Gen9 OU-legal. |
| 2026-10-26 12:35:41 | hardread | `fe9875d` | Pin Foul Play data snapshot (hashes) | SHA-256 hashes of Foul Play's cached Smogon data (reproducibility). |
| 2026-10-26 12:40:41 | fork | `934ef59` | Oracle injection (env-controlled) for value-of-information experiments | Fork: env-controlled oracle (true opponent sets) for the value-of-information experiment. Science only; the product's verdict does not use it. |
| 2026-10-26 12:42:56 | hardread | `e835560` | Runner: per-run server ports, oracle options; README results | Runner options (ports, oracle flags); notes. |
| 2026-10-26 13:49:53 | hardread | `591701d` | Team-pool pairing in runner; batch team validator (Showdown TeamValidator) | Team pairing in the runner; a batch wrapper around Showdown's TeamValidator. |
| 2026-10-26 13:49:54 | fork | `d37606b` | Validated Gen9 OU pools: 2,000 dev teams (metamon hl_05_26, non-reserved rosters) + 11 competitive | Fork: team files for local tests (removed from the public fork history; see DATA.md). |
| 2026-10-26 13:51:51 | hardread | `c697b5c` | Azure ML farm + Glimmer speed test jobs; README: team pool and Azure notes | Cloud job scripts for self-play farms and a Glimmer serving speed test. |
| 2026-10-26 14:09:24 | hardread | `1822513` | Engineering results ledger (local engine + H100 Glimmer speed) | Start of RESULTS.md (speed measurements). |
| 2026-10-26 14:16:04 | hardread | `a20d2f9` | Farm throughput results (Azure node) | RESULTS.md entry (throughput). |
| 2026-10-26 14:28:19 | hardread | `a99d8c6` | Runner --side-swap; generic Azure run_job.sh | Side-swapped pairings in the runner; generic cloud job wrapper. |
| 2026-10-26 14:28:52 | hardread | `a0a563f` | Record diverse-pool A/A warning (59% challenger) | RESULTS.md entry (A/A sanity check). |
| 2026-10-26 14:30:26 | fork | `3de9f0c` | Per-decision JSONL logging (public view, sampled worlds, per-world MCTS stats, choice) | Fork: per-decision JSONL log (`fp/hardread_decision_log.py`, 96 lines). The review engine (band 2) reuses its public-view serializer; it was extended after kickoff (v2, v3). |
| 2026-10-26 14:30:26 | hardread | `30c53a4` | Runner: --log-decisions/--log-states; oracle env never leaks to side B | Runner flags for decision logging; oracle isolation fix. |
| 2026-10-26 14:30:26 | hardread | `a45354a` | Results: decision logging | RESULTS.md entry. |

## Band 2: kickoff → building period (50 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-26 16:02:34 | hardread | `288e975` | Farm at scale: chunked jobs, roster-split pools, oracle seat alternation, battle logs |  |
| 2026-10-26 16:11:20 | hardread | `2b2af97` | Results: side-swapped A/A passes (49.35% +/- 2.04), two Foul Play crash bugs |  |
| 2026-10-26 16:13:09 | fork | `9ab38cc` | Decision log v2: all fields the set sampler reads (removed item, impossible abilities, choice flag, level) |  |
| 2026-10-26 16:22:09 | hardread | `719d479` | Dataset extractor: one record per decision with search targets, outcome, exact public history cut and true-team labels |  |
| 2026-10-26 16:27:46 | hardread | `18e21c6` | Pre-registration draft v0.9, sealed team set (1,000 reserved rosters, hashed), VoI analysis, blob fetch helper |  |
| 2026-10-26 16:48:33 | hardread | `5d2782f` | Results: overnight farm launch, first real chunks, provisional VoI numbers |  |
| 2026-10-26 16:53:59 | hardread | `00f7c6a` | Pre-registration v1: sealed primary n = 10,000 at C1t1 (approved), schedule |  |
| 2026-10-26 18:46:46 | hardread | `3cc3859` | Review engine: replay reconstruction (92.8% strict, 100% on visible actions), search evaluation, Platt calibration, hax meter (experimental: fails outcome sanity check), review CLI |  |
| 2026-10-26 18:47:18 | hardread | `f968453` | Results: review engine (reconstruction, calibration, decision cost; hax meter failed sanity check, fix in progress) |  |
| 2026-10-26 19:15:04 | hardread | `4c6a2b3` | Belief benchmark S-A: targets, usage-prior / Foul Play sampler / as-played baselines, learned belief v0, learning curve |  |
| 2026-10-26 19:27:28 | fork | `df65fd8` | HardRead hooks (all default off): engine levers, external policy hook + history capture, decision log v3 (Foul Play belief snapshot) |  |
| 2026-10-26 19:27:28 | hardread | `4cadb43` | Runner: --treat/--treat-seat give an environment (e.g. HardRead levers) to one side, seat alternating per pairing |  |
| 2026-10-26 19:29:46 | hardread | `b6c4386` | Pre-registration amendment A1: development sets A = hr_train, B = hr_val |  |
| 2026-10-26 19:30:02 | hardread | `dafbeb4` | Results: second wave on 20 nodes, log v3, levers, S-A benchmark numbers |  |
| 2026-10-26 19:31:32 | hardread | `983f385` | Extractor: forced/pivot-switch history cut at the start of the post-decision update chunk (arena parity finding) |  |
| 2026-10-26 19:32:03 | hardread | `f4583d6` | Arena: PokéChamp abyssal baseline (pinned 0f84c46), LLM policy runner vs abyssal, mock endpoint, history parity check, Azure arena job |  |
| 2026-10-26 19:33:27 | hardread | `36f6858` | Student: 24.9M policy/value transformer (49.9% top-1 vs search argmax held out, value Brier 0.176 < search 0.208, 8 ms CPU), numpy inference, student-blend lever and policy patches; extractor games-file naming fix |  |
| 2026-10-26 19:34:48 | fork | `fb80f6f` | Lever: student-blend (search share blended with the 25M student policy; falls back to Foul Play's rule on any failure) |  |
| 2026-10-26 19:34:48 | hardread | `bd36dec` | Node setup: numpy + HARDREAD_STUDENT_DIR for the student-blend lever |  |
| 2026-10-26 19:39:59 | hardread | `fe214be` | Glimmer SFT pipeline: serializer hr-sft-v1.2, chat rendering with native reasoning/tool-call frame, dataset builder, QLoRA job package with eval and LoRA->GGUF llama.cpp serving |  |
| 2026-10-26 19:40:11 | hardread | `0efbda4` | Results: arena, student, Glimmer SFT smoke |  |
| 2026-10-26 20:08:21 | hardread | `f8ed48a` | SFT learning signal: argmax target, choice-token weighting (x10) and soft-target distillation on the decisive id token (alpha 0.7), plain-loss fallback, multi-node DDP option; CPU tests 9/9 |  |
| 2026-10-26 20:13:47 | hardread | `645311d` | Review v2: absolute value scale (eval + logit(MCTS)/0.0125; poke-engine MCTS scores are relative to each root's static eval), luck meter v2 passes AIVAT-style checks |  |
| 2026-10-26 20:13:47 | hardread | `49500ba` | Results: luck meter v2 |  |
| 2026-10-26 20:36:59 | hardread | `ff83773` | Human replays: crawler for public top-50 Gen9 OU replays (2,167 since Jul 1), parser (games/actions/revealed sets, pseudonymised), reconstruction check (99.98% clean with nicknames) |  |
| 2026-10-26 22:39:11 | hardread | `6b33805` | Results: 27 nodes, v0b hedge, early C1t1 A/A, v0 training progress |  |
| 2026-10-26 22:43:58 | fork | `363684f` | Policy hook: HARDREAD_POLICY=student (25M student via sidecar or npz), HARDREAD_POLICY_ONLY_PREFIX |  |
| 2026-10-26 22:43:58 | hardread | `6c8513e` | Arena: student player; results: G-24h rehearsal (0 illegal, p95 3.5 s, 14.6% win) |  |
| 2026-10-26 22:48:36 | hardread | `9749eb0` | Review on human games: nickname/Revival Blessing/Healing Wish fixes (99.98% without nicknames), human win-probability calibrator, luck and decision cost on 513 top-ladder games |  |
| 2026-10-26 22:48:36 | hardread | `57d6112` | Results: review on human games |  |
| 2026-10-26 22:48:54 | hardread | `151116e` | Results: student alone beats abyssal 32/40 |  |
| 2026-10-26 22:58:50 | hardread | `12e84e2` | Metamon raw processing: index of 2.04M gen9ou games, revealed-set priors vs Smogon, belief dataset with partial (revealed-by-end) labels for 57,310 games rated >=1500 (2026H1, 3.70M decisions, both seats), human action table, fast reconstruction mode |  |
| 2026-10-26 22:59:13 | hardread | `f4352ad` | Results: Metamon processing |  |
| 2026-10-26 23:07:24 | hardread | `027ac10` | Student v1 on one H100 (hrstu-20261026-v1-8187, ~10 min node time): F1 pretrain + C1t1 fine-tune; top-1 vs search argmax 54.5% (roster-hash test) / 55.3% (hr_val cross-pool), value Brier 0.176 / 0.171; paper control (C1t1-only) 53.3% |  |
| 2026-10-26 23:07:24 | hardread | `fcdd8a9` | Azure: optional STUDENT_NPZ shipped with the job; HARDREAD_STUDENT_CKPT set on the node |  |
| 2026-10-26 23:08:03 | hardread | `157ced4` | Results: student v1; student-blend lever screens submitted |  |
| 2026-10-26 23:53:26 | hardread | `e8465e1` | Belief v1: farm + human (Metamon, masked partial labels, reveal-bias correction); S-A on hr_val v3 vs Foul Play's own 64-draw belief |  |
| 2026-10-26 23:53:26 | hardread | `75b8399` | Pre-registration amendment A2: contamination filter for external replays |  |
| 2026-10-26 23:53:35 | hardread | `dd2263b` | Results: S-A benchmark, belief v1 beats Foul Play's own belief on hr_val |  |
| 2026-10-27 00:23:22 | fork | `c04b38e` | Belief lever (default off): HARDREAD_LEVER_BELIEF=resample\|direct reweights Foul Play's own world sampling toward the HardRead belief model (importance resampling over 16 Foul Play draws per Pokemon, 60 ms budget, fallback to Foul Play sampling) |  |
| 2026-10-27 00:23:22 | hardread | `f646772` | Belief lever support: npz export, offline lever check, BELIEF_NPZ/BELIEF_WORLDS_NPZ shipping in Azure jobs |  |
| 2026-10-27 00:24:58 | hardread | `e7a2f6f` | Results: Glimmer v0 (train-timeout failure, v0-safe ckpt-700 eval), official G-24h arena launched |  |
| 2026-10-27 00:50:48 | hardread | `11703fa` | Results: VoI gate passes (final status, 519 games/level); lever and C1t1 A/A provisional numbers; h2h analysis tool |  |
| 2026-10-27 00:58:15 | hardread | `2bd270f` | Handoff package for ChatGPT/Codex: prompt, project, state, roadmap D1-D9, gates and definitions of done, runbook, data inventory, history, risks and lessons, live jobs, papers, presentation source | Internal notes only (no public file changed). |
| 2026-10-27 02:52:49 | hardread | `0ec6096` | Handoff fixes (review iteration 1): A/A rule amendment A3, failure attribution, follow-up script |  |
| 2026-10-27 03:42:05 | hardread | `7a0bac6` | Handoff: live progress log and heartbeat for resumable work | Internal notes only (no public file changed). |
| 2026-10-27 03:42:09 | hardread | `4212a52` | Progress log: correct timestamp | Internal notes only (no public file changed). |
| 2026-10-27 03:42:29 | hardread | `581ad28` | Progress: scheduled auto-resume task | Internal notes only (no public file changed). |
| 2026-10-27 03:43:47 | hardread | `3c85ee3` | Progress: hourly resume protocol with busy_until | Internal notes only (no public file changed). |
| 2026-10-27 03:49:20 | hardread | `ff0b791` | Progress: R2 review done, P2 running | Internal notes only (no public file changed). |

## Band 3: building period (12 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-27 04:10:08 | hardread | `50e590c` | Human replays: broad crawl across rating bands (17,779 replays Jul 1-Oct 26; 17,676 parsed; bands <1300 3,738 / 1300-1499 3,775 / 1500-1699 6,074 / 1700-1899 3,766 / 1900+ 183), resumable after the replay host outage |  |
| 2026-10-27 04:10:08 | hardread | `4703235` | Results: broad human replay crawl |  |
| 2026-10-27 04:10:20 | hardread | `86b60fb` | SFT jobs: train budget from --job-timeout with clean stop, DDP ranks exit 0 after rank-0 eval, --resume-from, --eval-only, checkpoint inputs (CKPT_URI/CKPT_ARG); v1 data build script; tests 11/11 |  |
| 2026-10-27 04:10:25 | hardread | `653ba7c` | Progress: background agents' work committed | Internal notes only (no public file changed). |
| 2026-10-27 04:11:32 | hardread | `b212084` | Progress: P2 done, judge running; keep the proposer's bridge validation script |  |
| 2026-10-27 04:35:16 | hardread | `fecd742` | Handoff: plan of record research/28; owner decisions table | Internal notes only (no public file changed). |
| 2026-10-27 04:37:04 | hardread | `76fd4e1` | Results: Glimmer v0-ddp resume full epoch (teacher top-1 0.470, 400/400 legal served, p95 1.49 s); add round-1 critic report |  |
| 2026-10-27 04:37:10 | hardread | `a0f1a91` | farm_job.sh: executable bit |  |
| 2026-10-27 04:37:22 | hardread | `9fc5771` | Results: finalist/G2 rules before readout; draft amendment A3a (not in force) |  |
| 2026-10-27 04:39:52 | hardread | `8c9af8e` | Handoff: START HERE section for the plan of record (read order, acceptance conditions, lanes, coordination, live state) | Internal notes only (no public file changed). |
| 2026-10-27 04:44:48 | hardread | `4373557` | Bridge: bridge.py (Start/Luck/Choices/Result, half-up integers, end tag, split), validate_bridge.py CLI (E6 pass on Draft B), test_bridge.py 4/4; review.py emits the bridge block |  |
| 2026-10-27 04:44:56 | hardread | `ca8d383` | Progress: Ag-Sci S1.5 bridge pass | Internal notes only (no public file changed). |

## Verify

```sh
# HardRead
git log --reverse --before=2026-10-26T16:00:00Z --format='%h %cI %s'
git log --reverse --since=2026-10-26T16:00:00Z --before=2026-10-27T04:00:00Z --format='%h %cI %s'
git log --reverse --since=2026-10-27T04:00:00Z --format='%h %cI %s'
# Foul Play fork (our commits only)
git log --reverse --format='%h %cI %s' 8f541f80f91f..hardread
```

Git dates are set by the committer's machine. They are evidence, not proof.
