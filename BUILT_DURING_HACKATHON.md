# Built during the hackathon: what was made when

I designed and directed this project; AI coding agents (Claude Code and Codex) wrote much of the code.

This ledger lists every commit of the two HardRead repositories by date, so judges can see what was built during the Meta Global AI Developer Hackathon. It is generated from git history; the commands under *Verify* reproduce it.

- Repositories: `hardread` (this one, branch `main`, HEAD `49e427f4bbf5`) and `hardread-foulplay` (our fork of Foul Play, branch `roaring-moon-guard`, HEAD `8e43a61ae720`; our commits are those after upstream `3264ac8bf0b4`).
- Generated: 2026-11-05 00:48 UTC. Commit times are git committer times in UTC. Author and committer times differ on 8 of 328 commits.
- These are the public histories, filtered for release with dates unchanged. The private main history has 761 commits; commits that touched only private paths aren't in this repository, so the counts here are lower. Team files were removed from the public fork's history (see DATA.md).
- After the first public release on 2 November, work reached this repository in batches: one public commit can hold many private commits, so a feature first published that way shows the batch's date, not the date it was written.
- Known causes of those differences: six commits were rebased together on 2 November at 02:17:44 UTC, and one was cherry-picked from a side branch on 27 October (its first copy is dated at the author time). One has a 6-second gap whose cause isn't on record. None changes time band.
- 2 subjects are shortened in the tables below; the full ones are in git.
- This file's own commit follows HEAD `49e427f4bbf5`.

## Time bands

| Band | From (UTC) | To (UTC) | Why this boundary | HardRead commits | Fork commits |
|---|---|---|---|---|---|
| 1. Before kickoff | — | 2026-10-26 16:00 | Kickoff Mon Oct 26, 12:00 PM ET (EDT, UTC−4) | 11 | 3 |
| 2. Kickoff → building period | 2026-10-26 16:00 | 2026-10-27 04:00 | The participant guide's building period starts Oct 27 (00:00 ET); the official rules' build window starts Oct 26 | 35 | 5 |
| 3. Building period | 2026-10-27 04:00 | 2026-11-05 02:00 | Submissions due Wed Nov 4, 9:00 PM ET (EST, UTC−5) | 270 | 4 |

**Before kickoff:** 14 commits between 2026-10-26 12:21:44 and 2026-10-26 14:30:26 UTC (about 2.1 h of work, ending 1.5 h before kickoff). They set up a self-play harness around the open-source Foul Play engine: a match runner, cloud job scripts, a team validator, an oracle hook for one science experiment, and decision logging. **None of them is a demo feature.** The product's verdict does not use the oracle. The review engine (band 2) reuses the decision log's public-view serializer (`fp/hardread_decision_log.py`), which was first written before kickoff and extended after it.

## Demo and science features: first commit

| Feature | Kind | File(s) | First commit | UTC | Band |
|---|---|---|---|---|---|
| Replay reconstruction (what each player knew, turn by turn) | product | `tools/review/reconstruct.py` | `d339b33` (hardread) | 2026-10-26 18:46:46 | 2 |
| Search evaluation and win probability | product | `tools/review/evaluate.py`, `tools/review/value.py` | `d339b33` (hardread) | 2026-10-26 18:46:46 | 2 |
| Win-probability calibration | product | `tools/review/calibrate.py` | `d339b33` (hardread) | 2026-10-26 18:46:46 | 2 |
| Luck meter | product | `tools/review/luck.py` | `bdd25a3` (hardread) | 2026-10-26 20:13:47 | 2 |
| Review JSON / CLI | product | `tools/review/review.py` | `d339b33` (hardread) | 2026-10-26 18:46:46 | 2 |
| Review on human games (Showdown replays) | product | `tools/review/human_eval.py` | `2b18bcb` (hardread) | 2026-10-26 22:48:36 | 2 |
| Bridge: Start -> Luck -> Choices -> Result | product | `tools/review/bridge.py` | `a74b665` (hardread) | 2026-10-27 04:44:48 | 3 |
| Bridge validation | product | `tools/review/validate_bridge.py`, `tools/review/proposed/validate_bridge.py` | `a74b665` (hardread) | 2026-10-27 04:44:48 | 3 |
| Verdict (Misplay / Misread / Defensible / Within noise) | product | `tools/review/verdict.py` | `3cb0f25` (hardread) | 2026-10-27 04:54:47 | 3 |
| Glimmer explainer with verifier | product | `tools/agent/explain.py`, `tools/agent/verifier.py` | `e24b6ea` (hardread) | 2026-10-27 05:10:05 | 3 |
| Note parser (what you were thinking -> stated read) | product | `tools/agent/parse_note.py` | `e24b6ea` (hardread) | 2026-10-27 05:10:05 | 3 |
| Read test (saved example card) | product | `app/data/showcases/showcase-6.read.json` | `49e427f` (hardread) | 2026-11-05 00:17:54 | 3 |
| Explanation agent's read-only tools | product | `tools/agent/tools.py` | `e24b6ea` (hardread) | 2026-10-27 05:10:05 | 3 |
| Turning-point question | product | `tools/agent/test_turning_point.py` | `914288c` (hardread) | 2026-11-02 02:17:44 | 3 |
| Public deployment (isolated container, kill switch) | product | `deploy/public/up.sh` | `e2ab0d2` (hardread) | 2026-11-02 00:14:12 | 3 |
| Web app | product | `app/server.py` | `c65c0b5` (hardread) | 2026-10-27 05:00:24 | 3 |
| Match runner (self-play harness) | science | `tools/run_match.py` | `92b7b47` (hardread) | 2026-10-26 12:35:18 | 1 |
| Oracle injection (value-of-information experiment) | science | `fp/hardread_oracle.py` | `aafe821` (fork) | 2026-10-26 12:40:41 | 1 |
| Decision log | science | `fp/hardread_decision_log.py` | `2058dee` (fork) | 2026-10-26 14:30:26 | 1 |
| Pre-registration of the sealed test | science | `PREREGISTRATION.md` | `0d83b4d` (hardread) | 2026-10-26 16:27:46 | 2 |
| Engine levers (value, double-half, student-blend, belief) | science | `fp/hardread_levers.py` | `6aa0a01` (fork) | 2026-10-26 19:27:28 | 2 |
| Value-of-information analysis | science | `tools/analysis/voi.py` | not in this repository | — | — |
| 25M student policy/value model | science | `tools/student/model.py` | `b8a8c78` (hardread) | 2026-10-26 19:33:27 | 2 |
| Belief model | science | `tools/belief/model.py` | `092638e` (hardread) | 2026-10-26 19:15:04 | 2 |
| Glimmer fine-tuning pipeline | science | `tools/sft/serialize.py`, `azure/sft/run_sft.py` | `83af559` (hardread) | 2026-10-26 19:39:59 | 2 |
| Arena vs PokeChamp's abyssal bot | science | `tools/arena/run_arena.py` | `eb8462f` (hardread) | 2026-10-26 19:32:03 | 2 |
| Static site | product | `index.html` in the site repository | `e8a9c4a` (hardread-site) | 2026-11-02 00:33:35 | 3 |

## Band 1: before kickoff (14 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-26 12:21:44 | fork | `7d5d641` | Local guest login for private Showdown servers | Fork: log in as a guest on a local server (no account needed). |
| 2026-10-26 12:35:18 | hardread | `92b7b47` | Engine lane: private Showdown runtime (18010), match runner, setup notes | Local Showdown server config and a script that plays Foul Play against itself (science harness). |
| 2026-10-26 12:35:41 | hardread | `683ab89` | Pin Foul Play data snapshot (hashes) | SHA-256 hashes of Foul Play's cached Smogon data (reproducibility). |
| 2026-10-26 12:40:41 | fork | `aafe821` | Oracle injection (env-controlled) for value-of-information experiments | Fork: env-controlled oracle (true opponent sets) for the value-of-information experiment. Science only; the product's verdict does not use it. |
| 2026-10-26 12:42:56 | hardread | `7f2cde2` | Runner: per-run server ports, oracle options; README results | Runner options (ports, oracle flags); notes. |
| 2026-10-26 13:49:53 | hardread | `1a59dcb` | Team-pool pairing in runner; batch team validator (Showdown TeamValidator) | Team pairing in the runner; a batch wrapper around Showdown's TeamValidator. |
| 2026-10-26 13:51:51 | hardread | `cb2d399` | Azure ML farm + Glimmer speed test jobs; README: team pool and Azure notes | Cloud job scripts for self-play farms and a Glimmer serving speed test. |
| 2026-10-26 14:09:24 | hardread | `bca14b4` | Engineering results ledger (local engine + H100 Glimmer speed) | Start of RESULTS.md (speed measurements). |
| 2026-10-26 14:16:04 | hardread | `7e307d9` | Farm throughput results (Azure node) | RESULTS.md entry (throughput). |
| 2026-10-26 14:28:19 | hardread | `a2096eb` | Runner --side-swap; generic Azure run_job.sh | Side-swapped pairings in the runner; generic cloud job wrapper. |
| 2026-10-26 14:28:52 | hardread | `da31655` | Record diverse-pool A/A warning (59% challenger) | RESULTS.md entry (A/A sanity check). |
| 2026-10-26 14:30:26 | fork | `2058dee` | Per-decision JSONL logging (public view, sampled worlds, per-world MCTS stats, choice) | Fork: per-decision JSONL log (`fp/hardread_decision_log.py`, 96 lines). The review engine (band 2) reuses its public-view serializer; it was extended after kickoff (v2, v3). |
| 2026-10-26 14:30:26 | hardread | `a901266` | Runner: --log-decisions/--log-states; oracle env never leaks to side B | Runner flags for decision logging; oracle isolation fix. |
| 2026-10-26 14:30:26 | hardread | `bbfc57b` | Results: decision logging | RESULTS.md entry. |

## Band 2: kickoff → building period (40 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-26 16:02:34 | hardread | `271bb28` | Farm at scale: chunked jobs, roster-split pools, oracle seat alternation, battle logs |  |
| 2026-10-26 16:11:20 | hardread | `e4f0ef4` | Results: side-swapped A/A passes (49.35% +/- 2.04), two Foul Play crash bugs |  |
| 2026-10-26 16:13:09 | fork | `e0d9633` | Decision log v2: all fields the set sampler reads (removed item, impossible abilities, choice flag, level) |  |
| 2026-10-26 16:22:09 | hardread | `4ab9a03` | Dataset extractor: one record per decision with search targets, outcome, exact public history cut and true-team labels |  |
| 2026-10-26 16:27:46 | hardread | `0d83b4d` | Pre-registration draft v0.9, sealed team set (1,000 reserved rosters, hashed), VoI analysis, blob fetch helper |  |
| 2026-10-26 16:48:33 | hardread | `6152ca3` | Results: overnight farm launch, first real chunks, provisional VoI numbers |  |
| 2026-10-26 16:53:59 | hardread | `f59d27b` | Pre-registration v1: sealed primary n = 10,000 at C1t1 (approved), schedule |  |
| 2026-10-26 18:46:46 | hardread | `d339b33` | Review engine: replay reconstruction (92.8% strict, 100% on visible actions), search evaluation, Platt calibration, hax meter (experimental: fails outcome sanity check), review CLI |  |
| 2026-10-26 18:47:18 | hardread | `c19fbbe` | Results: review engine (reconstruction, calibration, decision cost; hax meter failed sanity check, fix in progress) |  |
| 2026-10-26 19:15:04 | hardread | `092638e` | Belief benchmark S-A: targets, usage-prior / Foul Play sampler / as-played baselines, learned belief v0, learning curve |  |
| 2026-10-26 19:27:28 | fork | `6aa0a01` | HardRead hooks (all default off): engine levers, external policy hook + history capture, decision log v3 (Foul Play belief snapshot) |  |
| 2026-10-26 19:27:28 | hardread | `18149ca` | Runner: --treat/--treat-seat give an environment (e.g. HardRead levers) to one side, seat alternating per pairing |  |
| 2026-10-26 19:29:46 | hardread | `67ef897` | Pre-registration amendment A1: development sets A = hr_train, B = hr_val |  |
| 2026-10-26 19:30:02 | hardread | `b6e4df3` | Results: second wave on 20 nodes, log v3, levers, S-A benchmark numbers |  |
| 2026-10-26 19:32:03 | hardread | `eb8462f` | Arena: PokéChamp abyssal baseline (pinned 0f84c46), LLM policy runner vs abyssal, mock endpoint, history parity check, Azure arena job |  |
| 2026-10-26 19:33:27 | hardread | `b8a8c78` | Student: 24.9M policy/value transformer (49.9% top-1 vs search argmax held out, value Brier 0.176 < search 0.208, 8 ms CPU), numpy inference, student-blend lever and policy patches; extractor games-file naming fix |  |
| 2026-10-26 19:34:48 | fork | `6ce9acd` | Lever: student-blend (search share blended with the 25M student policy; falls back to Foul Play's rule on any failure) |  |
| 2026-10-26 19:39:59 | hardread | `83af559` | Glimmer SFT pipeline: serializer hr-sft-v1.2, chat rendering with native reasoning/tool-call frame, dataset builder, QLoRA job package with eval and LoRA->GGUF llama.cpp serving |  |
| 2026-10-26 19:40:11 | hardread | `a544ad9` | Results: arena, student, Glimmer SFT smoke |  |
| 2026-10-26 20:08:21 | hardread | `e7fb9a5` | SFT learning signal: argmax target, choice-token weighting (x10) and soft-target distillation on the decisive id token (alpha 0.7), plain-loss fallback, multi-node DDP option; CPU tests 9/9 |  |
| 2026-10-26 20:13:47 | hardread | `bdd25a3` | Review v2: absolute value scale (eval + logit(MCTS)/0.0125; poke-engine MCTS scores are relative to each root's static eval), luck meter v2 passes AIVAT-style checks |  |
| 2026-10-26 20:13:47 | hardread | `f18a312` | Results: luck meter v2 |  |
| 2026-10-26 20:36:59 | hardread | `e6b77dd` | Human replays: crawler for public top-50 Gen9 OU replays (2,167 since Jul 1), parser (games/actions/revealed sets, pseudonymised), reconstruction check (99.98% clean with nicknames) |  |
| 2026-10-26 22:39:11 | hardread | `f65b518` | Results: 27 nodes, v0b hedge, early C1t1 A/A, v0 training progress |  |
| 2026-10-26 22:43:58 | fork | `47ee61e` | Policy hook: HARDREAD_POLICY=student (25M student via sidecar or npz), HARDREAD_POLICY_ONLY_PREFIX |  |
| 2026-10-26 22:43:58 | hardread | `480b8c5` | Arena: student player; results: G-24h rehearsal (0 illegal, p95 3.5 s, 14.6% win) |  |
| 2026-10-26 22:48:36 | hardread | `2b18bcb` | Review on human games: nickname/Revival Blessing/Healing Wish fixes (99.98% without nicknames), human win-probability calibrator, luck and decision cost on 513 top-ladder games |  |
| 2026-10-26 22:48:36 | hardread | `81089be` | Results: review on human games |  |
| 2026-10-26 22:48:54 | hardread | `6411e6b` | Results: student alone beats abyssal 32/40 |  |
| 2026-10-26 22:59:13 | hardread | `6805c45` | Results: Metamon processing |  |
| 2026-10-26 23:07:24 | hardread | `4da7b3c` | Student v1 on one H100: pretrain and fine-tune |  |
| 2026-10-26 23:08:03 | hardread | `3bd8b33` | Results: student v1; student-blend lever screens submitted |  |
| 2026-10-26 23:53:26 | hardread | `53d29c2` | Belief v1: farm + human (Metamon, masked partial labels, reveal-bias correction); S-A on hr_val v3 vs Foul Play's own 64-draw belief |  |
| 2026-10-26 23:53:26 | hardread | `587546a` | Pre-registration amendment A2: contamination filter for external replays |  |
| 2026-10-26 23:53:35 | hardread | `5bf0026` | Results: S-A benchmark, belief v1 beats Foul Play's own belief on hr_val |  |
| 2026-10-27 00:23:22 | fork | `175e591` | Belief lever (default off): HARDREAD_LEVER_BELIEF=resample\|direct reweights Foul Play's own world sampling toward the HardRead belief model (importance resampling over 16 Foul Play draws per Pokemon, 60 ms budget, fallback to Foul Play sampling) | **Evaluated commit:** reported results were measured here (same tree as private `f061e63`). |
| 2026-10-27 00:23:22 | hardread | `bf5f166` | Belief lever support: npz export, offline lever check, BELIEF_NPZ/BELIEF_WORLDS_NPZ shipping in Azure jobs |  |
| 2026-10-27 00:24:58 | hardread | `8fcc725` | Results: Glimmer v0 (train-timeout failure, v0-safe ckpt-700 eval), official G-24h arena launched |  |
| 2026-10-27 00:50:48 | hardread | `22677d5` | Results: VoI gate passes (final status, 519 games/level); lever and C1t1 A/A provisional numbers; h2h analysis tool |  |
| 2026-10-27 02:52:49 | hardread | `ff737e1` | Handoff fixes (review iteration 1): A/A rule amendment A3, failure attribution, follow-up script |  |

## Band 3: building period (274 commits)

| UTC | Repo | Commit | Subject | Note |
|---|---|---|---|---|
| 2026-10-27 04:10:08 | hardread | `70a6bcd` | Human replays: broad crawl across rating bands (17,779 replays Jul 1-Oct 26; 17,676 parsed; bands <1300 3,738 / 1300-1499 3,775 / 1500-1699 6,074 / 1700-1899 3,766 / 1900+ 183), resumable after the replay host outage |  |
| 2026-10-27 04:10:08 | hardread | `13fc24a` | Results: broad human replay crawl |  |
| 2026-10-27 04:10:20 | hardread | `9148d72` | SFT jobs: train budget from --job-timeout with clean stop, DDP ranks exit 0 after rank-0 eval, --resume-from, --eval-only, checkpoint inputs (CKPT_URI/CKPT_ARG); v1 data build script; tests 11/11 |  |
| 2026-10-27 04:37:04 | hardread | `32599ea` | Results: Glimmer v0-ddp resume full epoch (teacher top-1 0.470, 400/400 legal served, p95 1.49 s); add round-1 critic report |  |
| 2026-10-27 04:37:22 | hardread | `35ab14e` | Results: finalist/G2 rules before readout; draft amendment A3a (not in force) |  |
| 2026-10-27 04:44:48 | hardread | `a74b665` | Bridge: bridge.py (Start/Luck/Choices/Result, half-up integers, end tag, split), validate_bridge.py CLI (E6 pass on Draft B), test_bridge.py 4/4; review.py emits the bridge block |  |
| 2026-10-27 04:48:55 | hardread | `7e945ea` | Compliance drafts: build ledger, third-party licences, data policy, statement |  |
| 2026-10-27 04:54:47 | hardread | `3cb0f25` | Verdict v1 (verdict.py: signed paired contrast, seeds 11-13, CRN on worlds, merge semantics, contradictions, revealed-later reference, cache), VT3 tests 10/10, select_offered.py (product disagreement rule + VT1 population), human_eval.py run --games/--band |  |
| 2026-10-27 05:00:24 | hardread | `c65c0b5` | App S1.7: local review app (FastAPI + static SPA) |  |
| 2026-10-27 05:08:37 | hardread | `3645819` | Results: verdict v1 code, VT3 10/10, 13-case depth pilot (not VT1) |  |
| 2026-10-27 05:10:05 | hardread | `e24b6ea` | Agent: Glimmer explainer (tools, pointer renderer, 7-rule verifier with cited-name digits fix, repair, templates), note parser + chips + read check, S1.6 smoke 5/5, A1 runner; EVAL_PROTOCOL_AGENT.md before any eval record |  |
| 2026-10-27 05:10:29 | hardread | `5200944` | calib_b.py: Draft-B human calibrator fit (id-hash halves), ECE by rating band, luck reference quantiles (S2.2 tool; dry run on run520 reproduces absolute_human: a 0.604, b 0.136, test ECE 0.025) |  |
| 2026-10-27 05:17:54 | hardread | `cea2daa` | App D2: decision check flow, Glimmer explanation panel, share card, 6 showcases |  |
| 2026-10-27 05:46:20 | hardread | `d67849b` | App: shared-LLM rules (EVAL_LOCK, HARDREAD_LLM_URL, one call in flight), wording guard, provenance, mobile bridge |  |
| 2026-10-27 05:47:40 | hardread | `c507e51` | App: optional client blocklist for POST endpoints (HARDREAD_BLOCKLIST) |  |
| 2026-10-27 05:50:04 | hardread | `1082ab3` | Verdict: merge never drops another stated move (VT3 11/11); vt2_cases.py (VT2/VT2b planted cases from hr_val log v3); Results: the app's Crunch 'Misplay' is a drafted-spread artefact (offensive spread -> Defensible) |  |
| 2026-10-27 05:50:23 | hardread | `0ad4436` | Results/Progress: correct two Ag-Sci entry timestamps |  |
| 2026-10-27 05:51:37 | hardread | `f9f2ea4` | App: Draft mode says the sets are assumed; EV caveat on damage-dependent disagreements |  |
| 2026-10-27 05:51:52 | hardread | `6a06f37` | App: clear message for decision checks on showcases; progress for the Draft-mode caveats |  |
| 2026-10-27 05:52:26 | hardread | `fda8468` | Agent v3: verifier rule 8 (never-say wording: cost/coach/summed/claims) and both-players wording for choices; parser attribution (own moves never credited to the opponent); EVAL_LOCK for recorded runs; A0 runner; renderer sign fix; prompt v2 |  |
| 2026-10-27 05:54:30 | hardread | `6a4e2e0` | App: note reader gets own_moves (parse-note-v3), shows why details were left out; read check guard at forced replacements |  |
| 2026-10-27 05:56:59 | hardread | `f25f5cf` | Agent v4: clause-aware trigger binding (rule 2); verdict.py cache shape in get_verdict; disagreements from the app list or select_offered; parser keeps items when the opponent is the subject, lists every rejection, read_check guard for forced replacements; blinded audit tool |  |
| 2026-10-27 06:19:56 | hardread | `c3e3349` | Agent v5: rule 8 rejects attributing the result to the player's own choices; parser max_tokens 2048; README with measured smoke, parser and pilot results |  |
| 2026-10-27 08:26:42 | hardread | `9a08dee` | Results: lever screens final (value 54.18 +/- 2.17, p 0.0002, 1,699 games), early A/A final (50.16, A3 pass), F2 none (dbl 48.09), F1 submitted (hrlev-20261027-f1value-*-c9d0) |  |
| 2026-10-27 08:48:31 | hardread | `bc234d7` | Results: G-24h retry PASS (44.0% vs abyssal, 0 illegal, p95 3.5 s); G1 not attempted (< 45%) |  |
| 2026-10-27 08:48:38 | hardread | `546ecfc` | Azure identifiers from environment (HR_AZ_*; sourced from handoff/env.sh when present, clear error otherwise) |  |
| 2026-10-27 09:12:34 | hardread | `9bd6e18` | Results/Progress: F1 first-chunk acceptance pass (299/300 decided, markers valid); Ag-Sci S1.10/S1.11 done |  |
| 2026-10-27 09:59:50 | hardread | `c8354ae` | Pre-registration amendment A3a (owner-approved OD-2, both parts): G2 and A/As from Oct 27; freeze Thu Oct 29 12:00 Paris if both A/As pass; A/A 2 validity without neutral levers |  |
| 2026-10-27 10:06:25 | hardread | `588880b` | Results/Progress/Jobs: track A3a wave 1 (G2 value x10 hrg2-*-d241, A/A 1 x9 hraa1-*-c0d6); refactored submit.sh works |  |
| 2026-10-27 10:12:07 | hardread | `15c3972` | Results: F1 final - value 48.73 +/- 2.17 (1,500 fresh dev-A games) does not replicate the screen's 54.18; code paths checked identical |  |
| 2026-10-27 10:24:23 | hardread | `ba20678` | Results: F1 addendum - screen vs F1 search depth identical (no node-load cause), early/late and crash rates; Ending A ~15% [J] |  |
| 2026-10-27 10:52:02 | hardread | `c69e90b` | Results: held-out leak check (hr_hl_dev overlaps hr_val by 228 teams; no training exposure; S-A/student/G2/sealed unaffected; arena caveat); A3a first-chunk acceptance (G2 markers valid, A/A 1 99.4% decided) |  |
| 2026-10-27 10:52:05 | hardread | `a9a458d` | Results: fix a timestamp |  |
| 2026-10-27 11:45:05 | hardread | `b02f1db` | Results: G2 FAIL (value 48.40 +/- 2.71 on 1,000 dev-B games, markers valid); S-A refresh primary (440 games, v1 vs usage -0.118) and secondary (1,579 games); tools/ops wave manager for the remaining A/A 2 jobs |  |
| 2026-10-27 12:28:58 | hardread | `e0ae3b4` | Results: record secondary held-out human reader benchmark |  |
| 2026-10-27 13:15:17 | hardread | `f249c80` | Record partial A/A2 first-chunk acceptance and remaining gates |  |
| 2026-10-27 13:47:24 | hardread | `e694300` | Record preregistered AA1 PASS and plan sourced demo storyboard |  |
| 2026-10-27 13:59:51 | hardread | `f6edd15` | Record all AA2 first-chunk checks and refresh reviewed project state |  |
| 2026-10-27 14:24:39 | hardread | `96ebdca` | Record final exploratory belief screens and provenance |  |
| 2026-10-27 15:30:30 | hardread | `382a50a` | Constrain app explanations to offered decisions with reviewed partial demo |  |
| 2026-10-27 15:36:44 | hardread | `2de4373` | Record independently accepted AA2 PASS and two-family freeze condition |  |
| 2026-10-27 15:37:35 | hardread | `7eb432c` | Checkpoint VT1 population launch and block mismatched explainer evaluation |  |
| 2026-10-27 15:42:17 | hardread | `69b21fe` | Checkpoint accepted AA checks, partial demo and running VT1 population |  |
| 2026-10-27 16:07:18 | hardread | `8f69e1e` | Accept fixed 184-case verdict-test population before batch |  |
| 2026-10-27 16:42:02 | hardread | `1b8391f` | Approve conditional October 28 freeze timing in A3b |  |
| 2026-10-27 17:05:02 | hardread | `7b8158e` | Record complete initial VT1 failure before registered fallback |  |
| 2026-10-27 17:19:08 | hardread | `81edcbc` | Fix null error handling before registered VT1 rescoring |  |
| 2026-10-27 17:40:53 | hardread | `8620754` | Record complete VT1 margin .08 failure before 60k fallback |  |
| 2026-10-27 17:42:10 | hardread | `4c00c34` | Replace held-out roster lists with strict canonical hashes |  |
| 2026-10-27 18:13:47 | hardread | `a700225` | Record VT1 60k pass and verified GPU execution with deviations |  |
| 2026-10-27 18:52:41 | hardread | `e24d8c7` | Record training takeover, XL submission and farm setup failure |  |
| 2026-10-27 18:58:44 | hardread | `3e566f1` | Bind verdict receipts to offered inputs and fail closed in evaluation |  |
| 2026-10-27 19:35:03 | hardread | `3446278` | Make distributed training and deadline shutdown fail closed |  |
| 2026-10-27 19:36:57 | hardread | `d7a07a4` | Record real training progress, failed XL and next acceptance gates |  |
| 2026-10-27 20:35:48 | hardread | `4bac65c` | Bind human calibration to both-roster filtering and cohort provenance |  |
| 2026-10-27 20:38:35 | hardread | `6e06000` | Record dev-selected student v2 held-out results and limitations |  |
| 2026-10-27 20:52:28 | hardread | `be27470` | Add large student seeds and frozen input validation |  |
| 2026-10-27 20:56:32 | hardread | `57415d6` | Record failed band calibration and retain current app calibrator |  |
| 2026-10-27 21:05:38 | hardread | `158b351` | Record local latency risk and partial public review smoke |  |
| 2026-10-27 21:51:03 | hardread | `d65d424` | Keep private operations and generated proof outside tracked trees |  |
| 2026-10-27 22:11:55 | hardread | `a2d69c4` | Add disabled source checks and isolated BF16 smoke interfaces |  |
| 2026-10-27 22:13:41 | hardread | `2186adf` | Add reviewed read-only BF16 snapshot preflight |  |
| 2026-10-27 22:30:37 | hardread | `90b3589` | Reserve final corrective farm block and retain diagnostic failures |  |
| 2026-10-27 22:51:52 | hardread | `427675b` | Record bounded BF16 mount audit without inference claim |  |
| 2026-10-27 22:54:46 | hardread | `465f24b` | Record large student agreement and latency gate failures |  |
| 2026-10-27 23:16:41 | hardread | `0419761` | Record independently verified student v2 86.5 percent arena |  |
| 2026-10-27 23:19:26 | hardread | `c53a9be` | Record full external all40 corrective farm probe pass |  |
| 2026-10-27 23:25:24 | hardread | `b26c4bf` | Reserve six audited night farm seed blocks before launch |  |
| 2026-10-28 00:55:52 | hardread | `e8d31b5` | Bind C9 evaluation selection to verified farm source bytes |  |
| 2026-10-28 01:02:24 | hardread | `f1731c6` | Record seven-job admission and scoped demo receipt results |  |
| 2026-10-28 01:22:29 | hardread | `775ccc9` | Resolve authenticated producer team paths for C9 source derivation |  |
| 2026-10-28 01:53:16 | hardread | `a16fe17` | Reserve optional night farm seed blocks before creation |  |
| 2026-10-28 02:04:04 | hardread | `d643f4d` | Record e1 evaluation and preserved night product failures |  |
| 2026-10-28 02:06:16 | hardread | `e867766` | Record source-proven overlength review exceptions in evaluation selection |  |
| 2026-10-28 03:06:22 | hardread | `6be29f9` | Record frozen explanation cohort and verified partial demo flow |  |
| 2026-10-28 03:12:19 | hardread | `27f91a1` | Bind explainer training to verified data and checkpoint lineage |  |
| 2026-10-28 03:13:14 | hardread | `994f05a` | Record validation retry and accepted training and hosting source |  |
| 2026-10-28 03:17:05 | hardread | `9976c43` | Record successful validation startup and pre-call provenance block |  |
| 2026-10-28 03:28:14 | hardread | `9eaeb4d` | Verify live server flags without rejecting registered defaults |  |
| 2026-10-28 03:38:44 | hardread | `8ec4451` | Record official Glimmer artifact revision and disk hash proof |  |
| 2026-10-28 04:04:13 | hardread | `ba01422` | Bind frozen reviews to their original constructor snapshot |  |
| 2026-10-28 04:07:12 | hardread | `546fdad` | Record accepted e1 validation metrics and preserved explanation attempts |  |
| 2026-10-28 04:17:44 | hardread | `4463ba0` | Record frozen explanation evaluation entering its first item |  |
| 2026-10-28 04:19:23 | hardread | `c748565` | Record incomplete large student arena with scheduled denominator |  |
| 2026-10-28 04:52:07 | hardread | `caf6c2d` | Add source-bound science charts with readable video headlines |  |
| 2026-10-28 05:22:09 | hardread | `724dfcc` | Record source-only explainer population timing repair |  |
| 2026-10-28 05:37:22 | hardread | `8b3ce19` | Preserve student v2 cohorts for future train only farm updates |  |
| 2026-10-28 06:28:43 | hardread | `4b793a4` | Record complete base explanation verifier measurement with audit pending |  |
| 2026-10-28 06:40:43 | hardread | `4f47b6f` | Preserve A0 preflight failure and reviewed capture correction |  |
| 2026-10-28 07:41:20 | hardread | `0680954` | Integrate reviewed bounded T1 source and scoped receipt judging |  |
| 2026-10-28 07:43:17 | hardread | `15d1f21` | Record complete A0 capture and e2 training-stage failure |  |
| 2026-10-28 07:47:00 | hardread | `1b29674` | Enforce replayed semantic and numeric target count before T1 training |  |
| 2026-10-28 07:55:53 | hardread | `f62fedd` | Record incomplete terminal farms and fixed synthetic demo sources |  |
| 2026-10-28 08:03:57 | hardread | `4fcc91b` | Record XL and bold terminal stage and serving counters |  |
| 2026-10-28 09:10:12 | hardread | `99e3702` | Adopt owner-approved prospective T1 and paired A1 v2 methods |  |
| 2026-10-28 11:00:12 | hardread | `d6712b9` | Record owner-ratified A4 frozen sealed design |  |
| 2026-10-28 11:01:27 | hardread | `8cebd1d` | Record ratified freeze and sealed pool provenance |  |
| 2026-10-28 11:03:13 | hardread | `d60c08e` | Select XL6 using prospectively declared full validation |  |
| 2026-10-28 11:05:40 | hardread | `eab2573` | Default to verified templates and correct unsupported read wording |  |
| 2026-10-28 11:30:01 | hardread | `987e139` | Record same-row fixed400 e1 and bold benchmark results |  |
| 2026-10-28 12:10:57 | hardread | `320e619` | Accept sealed technical launch and record runtime probe failure |  |
| 2026-10-28 12:36:53 | hardread | `ba74e79` | Record Student v2 Foul Play result and selected XL6 fixed400 scores |  |
| 2026-10-28 13:01:18 | hardread | `5cb2af2` | Separate attributed luck from counterfactual swing in explanations |  |
| 2026-10-28 13:07:33 | hardread | `ebbbf21` | Mount pinned SFT datasets outside the Azure code snapshot |  |
| 2026-10-28 13:24:53 | hardread | `ef599d0` | Record explainer source checks and conservative farm termination evidence |  |
| 2026-10-28 13:29:53 | hardread | `1089947` | Register base-first A1v2 with complete scoped receipts and strict preflight |  |
| 2026-10-28 13:32:00 | hardread | `c7db58f` | Pin prospective base A1v2 model settings and semantic source closure |  |
| 2026-10-28 13:35:46 | hardread | `09566d9` | Record E2 partial-epoch fixed400 comparison without model reselection |  |
| 2026-10-28 14:03:46 | hardread | `b8ab6c7` | Avoid treating a listed switch probability as the total for any switch |  |
| 2026-10-28 14:09:50 | hardread | `554009d` | Register bounded-memory all-data identity and training admission rules |  |
| 2026-10-28 14:17:11 | hardread | `b68ecda` | Validate frozen A1v2 data against historical provenance and current offered parity |  |
| 2026-10-28 14:17:11 | hardread | `8bbd18b` | Refresh prospective A1v2 runtime and frozen-data loader pins |  |
| 2026-10-28 14:31:24 | hardread | `0b9e32d` | Exclude failed P5 games prospectively with complete identity accounting |  |
| 2026-10-28 14:35:56 | hardread | `88364b3` | Verify native two-line executable build in A1 v2 |  |
| 2026-10-28 14:35:56 | hardread | `ae607eb` | Pin accepted A1 v2 native live-build verifier |  |
| 2026-10-28 15:18:03 | hardread | `863cd3f` | Retain P5 tombstone identities and quarantine incomplete source games |  |
| 2026-10-28 15:23:23 | hardread | `305f4b0` | Record independently accepted three-case template demo |  |
| 2026-10-28 15:47:18 | hardread | `ad86e24` | Add disabled engine-grounded DPO preparation with corrected score semantics |  |
| 2026-10-28 15:49:35 | hardread | `9ef7a25` | Version matched Foul Play comparison and E2 scores with failure caveats |  |
| 2026-10-28 15:52:13 | hardread | `f7c79fe` | Retain missing-side identities for already excluded P5 error games |  |
| 2026-10-28 16:15:03 | hardread | `ade8d79` | Align source-health census definitions without changing data eligibility |  |
| 2026-10-28 16:19:20 | hardread | `35e9758` | Record completed A1 v2 and pending audit alongside source-index failures |  |
| 2026-10-28 16:52:08 | hardread | `5bbdb0c` | Record accepted all-data identity admission and guarded dataset build |  |
| 2026-10-28 16:53:27 | hardread | `5fb454f` | Activate reviewed local DPO pair build with distinct scoped approvals |  |
| 2026-10-28 17:12:30 | hardread | `8c985d6` | Validate proof-bound v2 SFT manifests and derived DATA_URI smoke data |  |
| 2026-10-28 17:38:46 | hardread | `c45af26` | Add guarded DPO GPU qualification and real six-rank startup proof |  |
| 2026-10-28 17:40:59 | hardread | `edc4245` | Record permanent prospective top-up seed reservations |  |
| 2026-10-28 17:47:57 | hardread | `76fda1a` | Record admitted v2 rows and reviewed smoke with verified private transport |  |
| 2026-10-28 18:01:35 | hardread | `1250ad9` | Record completed and admitted DPO pair dataset |  |
| 2026-10-28 18:10:17 | hardread | `53258d3` | Record actual mounted-data smoke submission and bounded watcher |  |
| 2026-10-28 18:32:19 | hardread | `463c33c` | Capture qualification runtime package inventory before model loading |  |
| 2026-10-28 18:43:01 | hardread | `6fefaf3` | Accept actual one-step GPU mounted-data smoke with persisted checkpoint |  |
| 2026-10-28 18:47:11 | hardread | `c8b2edb` | Record sealed full readout Ending B and release confirmatory capacity |  |
| 2026-10-28 18:56:44 | hardread | `2f811cc` | Record actual twelve-node Glimmer v2 running wave |  |
| 2026-10-28 19:23:36 | hardread | `2384179` | Record four bounded student-data farm creates and exact watchers |  |
| 2026-10-28 19:41:48 | hardread | `252b17d` | Record twelve H100 rank preflights and actual optimizer progress |  |
| 2026-10-28 19:55:10 | hardread | `0c659d3` | Explain engine-backed review with sourced measurements and explicit limits |  |
| 2026-10-28 20:29:54 | hardread | `24d6174` | Bind DPO loader to governed immutable XL6 selection proof |  |
| 2026-10-28 22:43:15 | hardread | `4a68ebc` | Record selected XL6 arena conservatively as incomplete technical run |  |
| 2026-10-28 22:58:13 | hardread | `7e791a1` | Preserve failed DPO input qualification and runtime hold |  |
| 2026-10-28 22:59:53 | hardread | `90e017f` | Commit reviewed prospective Glimmer reads evaluation before data preparation |  |
| 2026-10-28 23:15:23 | hardread | `15470bd` | Handle frozen sparse baselines and prospectively label censored log scores |  |
| 2026-10-28 23:30:43 | hardread | `d3f3f07` | Record actual DPO fit acceptance and twelve-hour timing rejection |  |
| 2026-10-28 23:43:46 | hardread | `92ed2ec` | Compute DPO logits only at response positions with parity-gated qualification |  |
| 2026-10-29 00:01:42 | hardread | `2e780a9` | Preserve DPO preflight and offline hosting build failures honestly |  |
| 2026-10-29 00:19:14 | hardread | `b6b473a` | Persist scalar parity diagnostics before unchanged GPU qualification failure gate |  |
| 2026-10-29 00:30:38 | hardread | `8d45dbb` | Record DPO parity failure, incomplete reads and Student data shortfall |  |
| 2026-10-29 00:30:38 | hardread | `d5aec0c` | Separate one-time loading from reads evaluation timing projection |  |
| 2026-10-29 00:41:21 | hardread | `2f1c13f` | Qualify deterministic DPO backends with fixed self and cross controls |  |
| 2026-10-29 01:23:27 | hardread | `85068c7` | Keep native LM head and qualify response-only softmax for DPO |  |
| 2026-10-29 01:23:57 | hardread | `bba37d4` | Prospectively evaluate reads with native BF16 base and pinned FP32 LoRA |  |
| 2026-10-29 01:31:45 | hardread | `cbdaf53` | Record measured DPO and reads failures and the bounded R7 qualifier start |  |
| 2026-10-29 01:56:15 | hardread | `e242d05` | Apply owner-approved DPO runtime ceiling without changing training recipe |  |
| 2026-10-29 02:36:11 | hardread | `5562412` | Record actual DPO startup, reads timing and Student data outcomes |  |
| 2026-10-29 02:38:08 | hardread | `0bde895` | Bound one prospective full-cohort reads completion attempt to four hours |  |
| 2026-10-29 03:53:58 | hardread | `3910eb0` | Record Glimmer v2 original validation; retain XL6 before test readout |  |
| 2026-10-29 03:56:39 | hardread | `e5032ee` | Preserve startup and hosting failures with actual runtime receipts |  |
| 2026-10-29 04:46:21 | hardread | `f3f6732` | Record v2 fixed400 descriptive result without reselection |  |
| 2026-10-29 04:47:28 | hardread | `adc3bb6` | Record actual reads first20 runtime without partial quality scores |  |
| 2026-10-29 04:55:17 | hardread | `d0ff387` | Record final XL6 clean rerun and pooled G1 pass |  |
| 2026-10-29 05:16:33 | hardread | `d6c4bab` | Record checked 180-second preview with confirmed XL6 chart |  |
| 2026-10-29 07:29:18 | hardread | `16df5e7` | Record accepted 90133 Student new-decision count below training gate |  |
| 2026-10-29 08:13:48 | hardread | `baf3cbd` | Version complete exploratory XL6 reads and paired baseline results |  |
| 2026-10-29 08:17:56 | hardread | `47f6277` | Record four running bounded Student data farms and exact guards |  |
| 2026-10-29 08:58:06 | hardread | `115a2fa` | Record healthy first chunks from all four new Student farms |  |
| 2026-10-29 09:28:11 | hardread | `6aedb7f` | Record successful private checkout replay rehearsal with clean-machine limits |  |
| 2026-10-29 09:53:22 | hardread | `b03214f` | Record bounded DPO utilization refresh without quality claim |  |
| 2026-10-29 10:35:53 | hardread | `e10b499` | Prepare GPL license and truthful source and build provenance |  |
| 2026-10-29 11:15:41 | hardread | `0a48509` | Integrate reviewed CPU hosting source and synthetic checks |  |
| 2026-10-29 11:19:19 | hardread | `4f11090` | Record bounded DPO activity and unobserved phase receipts |  |
| 2026-10-29 11:30:51 | hardread | `d92b7de` | Clarify independent evidence and comparison scopes |  |
| 2026-10-29 12:24:18 | hardread | `5c156d5` | Repair public source links and record private scan evidence |  |
| 2026-10-29 12:39:57 | hardread | `187bb6d` | Support reviewed stateful Glimmer second-epoch resume |  |
| 2026-10-29 13:33:03 | hardread | `c47d196` | Handle saved bnb optimizer state exactly and log training GPU use |  |
| 2026-10-29 14:35:44 | hardread | `b3ce6ad` | Record measured training pace and qualified DPO ETA |  |
| 2026-10-29 14:56:01 | hardread | `e7610a0` | Record bounded local product and submission preparation |  |
| 2026-10-29 15:13:56 | hardread | `d79d6e7` | Document deployment dependency versions and licence provenance |  |
| 2026-10-29 15:42:19 | hardread | `c9729af` | Refresh measured G1 optimizer throughput and GPU activity |  |
| 2026-10-29 15:55:31 | hardread | `99cfe71` | Record reviewed private burned-caption video component |  |
| 2026-10-29 16:14:00 | hardread | `cb43408` | Record reviewed private 40-second teaser component |  |
| 2026-10-29 16:19:32 | hardread | `4aed64f` | Record observed submission form and draft alignment |  |
| 2026-10-29 16:30:29 | hardread | `edbd9b4` | Clarify historical local installation status |  |
| 2026-10-29 16:37:01 | hardread | `2fbe4f9` | Extend dated build history through captured main snapshot |  |
| 2026-10-29 16:40:05 | hardread | `7310e16` | Prepare scoped Open Weights model evidence page |  |
| 2026-10-29 16:47:49 | hardread | `c27ef96` | Record G1 operational pace from saved live snapshot |  |
| 2026-10-29 17:05:16 | hardread | `36e6f7b` | Clarify historical reserved-team statement after sealed test |  |
| 2026-10-29 17:06:58 | hardread | `2373260` | Use included results citation for Open Weights evidence |  |
| 2026-10-29 17:25:42 | hardread | `4f765a6` | Prepare bounded portable Mac clean CLI setup |  |
| 2026-10-29 17:38:03 | hardread | `4c1e346` | Record clean CLI acquisition failure without retry |  |
| 2026-10-29 17:57:59 | hardread | `b07b551` | Record bounded HTTP data failure diagnostics without retrying setup |  |
| 2026-10-29 17:58:18 | hardread | `d49e590` | Record G1 progress and conditional saving ETA at 1746 UTC |  |
| 2026-10-29 18:00:16 | hardread | `1defe26` | Refresh six-rank DPO GPU activity without claiming learning |  |
| 2026-10-29 18:22:07 | hardread | `7f19009` | Record updated private selected-source privacy component |  |
| 2026-10-29 18:26:25 | hardread | `3c04239` | Document authenticated local setup package licence metadata |  |
| 2026-10-29 18:44:51 | hardread | `9cb9d6f` | Refresh G1 operational pace for the scheduled owner report |  |
| 2026-10-29 19:06:39 | hardread | `5b54fe4` | Accept DPO R3 full epoch and final persistence |  |
| 2026-10-29 19:18:33 | hardread | `d6feb17` | Record guarded DPO fixed400 evaluation create |  |
| 2026-10-29 19:31:23 | hardread | `07fb37e` | Record valid negative DPO fixed400 result |  |
| 2026-10-29 19:33:16 | hardread | `dd52fc4` | Clarify DPO completion and negative readout wording |  |
| 2026-10-29 20:08:47 | hardread | `3db9156` | Add guarded explicit local inputs for prospective clean setup |  |
| 2026-10-29 21:20:56 | hardread | `5eb39ef` | Accept G1 second epoch persistence and bind original full validation |  |
| 2026-10-29 21:23:15 | hardread | `798fd36` | Record sole original G1 full-validation create and fresh admission |  |
| 2026-10-29 22:05:12 | hardread | `537b82d` | Select G1 second epoch on original 2428 validation before fixed400 |  |
| 2026-10-29 22:07:53 | hardread | `254087d` | Record selected G1 validation in unfrozen ledger with scope limits |  |
| 2026-10-29 22:09:32 | hardread | `869304d` | Record sole G1 fixed400 create after selection and fresh admission |  |
| 2026-10-29 22:27:56 | hardread | `07a2b84` | Record Glimmer epoch2 descriptive fixed400 result |  |
| 2026-10-29 22:28:24 | hardread | `265f55b` | Source Glimmer epoch2 fixed400 in working numbers ledger |  |
| 2026-10-30 00:13:59 | hardread | `0fb94b8` | Add bounded public replay increment acquisition |  |
| 2026-10-30 01:10:23 | hardread | `8385d47` | Record qualified selected Glimmer epoch-two arena result |  |
| 2026-10-30 03:30:06 | hardread | `33cb481` | Record verified bounded public replay acquisition result |  |
| 2026-10-30 03:30:57 | hardread | `39829ce` | Correct terminal observation elapsed arithmetic |  |
| 2026-10-30 04:29:23 | hardread | `46d8427` | Document the blocked-feed manual cache setup route |  |
| 2026-10-30 04:45:14 | hardread | `84bd80f` | Separate dated account rank from the frozen ladder record |  |
| 2026-10-30 04:50:54 | hardread | `57b4266` | Add cited own-game panels with a recorded-facts fallback |  |
| 2026-10-30 05:04:24 | hardread | `f4cea5b` | Add a strict read-only evidence-reference validator |  |
| 2026-10-30 05:38:14 | hardread | `937e51e` | Add the qualified 89 percent arena chart and owner-selected tagline |  |
| 2026-10-30 06:02:29 | hardread | `77f44fd` | Add reviewed local Glimmer selection for recorded game facts |  |
| 2026-10-30 06:13:02 | hardread | `13a57ca` | Add cited read-only postgame panels for the bot games |  |
| 2026-10-30 06:17:19 | hardread | `823f857` | Accept the existing Tera suffix in cited postgame moves |  |
| 2026-10-30 06:32:55 | hardread | `2771990` | Keep game citations readable with expandable source details |  |
| 2026-10-30 07:23:36 | hardread | `404f02b` | Add bounded private selector failure diagnostics |  |
| 2026-10-30 07:55:19 | hardread | `6396262` | Clarify own-game source and private data boundaries |  |
| 2026-10-30 13:29:41 | hardread | `d6bfd48` | Admit owner-approved fresh local own-game selector stage |  |
| 2026-10-30 14:14:59 | hardread | `bd81d48` | Record owner19 dated opening and local template fallback |  |
| 2026-10-30 15:08:04 | hardread | `5789d8e` | Document guarded cache test and preserve case-sensitive evidence |  |
| 2026-11-01 06:19:09 | hardread | `484abfa` | Replace private setup evidence links with reviewed public-safe note |  |
| 2026-11-01 23:46:55 | hardread | `45b025e` | Turn on live Glimmer explanations and show its work; rewrite README for judges |  |
| 2026-11-01 23:52:24 | hardread | `17a949a` | Tighten public claims and disclose pre-kickoff reuse |  |
| 2026-11-02 00:05:54 | hardread | `a70ab29` | Save Glimmer answers and plain-prompt comparisons for the showcases |  |
| 2026-11-02 00:10:48 | hardread | `af2b28d` | Keep saved Glimmer answers in the static showcase page |  |
| 2026-11-02 00:11:21 | hardread | `7eb4661` | Drop the untested hosting recipe from the README |  |
| 2026-11-02 00:14:12 | hardread | `e2ab0d2` | Add an isolated public copy: public links only, container, model gate, kill switch |  |
| 2026-11-02 00:16:18 | hardread | `603a93b` | Make the UI readable for a first-time judge in 60 seconds |  |
| 2026-11-02 00:20:43 | hardread | `11a2fba` | Merge first-minute and mobile UI work | Internal notes only (no public file changed). |
| 2026-11-02 00:21:27 | hardread | `281e71e` | Say what the checker covers: number sources, not whether the words are true |  |
| 2026-11-02 00:22:06 | hardread | `49987ee` | Static site: link to live review when it's up, and say so when it's not |  |
| 2026-11-02 00:32:32 | hardread | `05841b4` | Publish the checker test, with its weak spots |  |
| 2026-11-02 00:35:36 | hardread | `7392130` | Public copies only describe public replay links |  |
| 2026-11-02 00:39:40 | hardread | `99b8341` | Prepare Modal as the public route: model reachable only by private call, hourly caps |  |
| 2026-11-02 00:39:56 | hardread | `9ec2900` | Don't name a host for live review while it's offline |  |
| 2026-11-02 00:44:00 | hardread | `4f35ef9` | Let the VM accept replies from the public container, and nothing else from it |  |
| 2026-11-02 00:46:26 | hardread | `2acb977` | Keep the gate and tunnel running, and publish a changed tunnel address |  |
| 2026-11-02 00:51:42 | hardread | `7ef1601` | Don't mount the home folder into the public VM |  |
| 2026-11-02 01:10:13 | hardread | `75f2823` | Apply security review fixes to the public copy |  |
| 2026-11-02 01:11:06 | hardread | `813f25e` | Fix review failures found by pushing public replays through the app |  |
| 2026-11-02 01:17:22 | hardread | `53cfaea` | Lead with a showcase whose review doesn't rest on an assumed spread; link the site |  |
| 2026-11-02 01:22:43 | hardread | `476b3ce` | Merge real-replay reliability fixes |  |
| 2026-11-02 01:23:26 | hardread | `0e7f3b1` | Close the tunnel before publishing, count verdicts as jobs, limit IPv6 by /64 |  |
| 2026-11-02 01:43:24 | hardread | `aefff4f` | Add the one decision to look at again |  |
| 2026-11-02 02:17:44 | hardread | `914288c` | Point the turning-point question at the largest win-chance move; steady Q1 wording |  |
| 2026-11-02 02:17:44 | hardread | `5ee29cc` | Correct turning-point and Q1 wording found by reading the first full run |  |
| 2026-11-02 02:17:44 | hardread | `7251b11` | Record the turning-point and luck-or-choices check |  |
| 2026-11-02 02:17:44 | hardread | `5b5c369` | Let Glimmer choose and explain the turning point itself |  |
| 2026-11-02 02:17:44 | hardread | `589c538` | State the checker's choices wording in the turning-point question |  |
| 2026-11-02 02:17:44 | hardread | `ce7daf1` | Say where the what_happened slot reads correctly |  |
| 2026-11-02 02:17:44 | hardread | `0cc6b78` | Record the free-form turning-point results, including the scripted version they replace |  |
| 2026-11-02 02:17:58 | hardread | `a79f7d9` | Point the evidence file at the rebased commit |  |
| 2026-11-02 02:33:32 | hardread | `15ef673` | Say that luck is an estimate, with the information-edge test |  |
| 2026-11-02 02:41:08 | hardread | `ee44705` | Regenerate saved answers with the new questions |  |
| 2026-11-02 02:55:55 | hardread | `4f376a2` | Plainer public text and fuller disclosure after a fresh-reader review |  |
| 2026-11-02 02:56:10 | hardread | `f8fdf14` | Say estimates, and name the base engine at the top |  |
| 2026-11-02 02:56:24 | hardread | `520e23d` | Point the build ledger at public commit ids |  |
| 2026-11-02 02:56:24 | hardread | `3dd8633` | Link the public engine fork |  |
| 2026-11-02 02:59:04 | hardread | `c0f669f` | Make the build ledger's commit ids resolve publicly |  |
| 2026-11-02 03:23:48 | hardread | `a5ee8b6` | Apply the claim audit: narrower claims about tools, the checker and results |  |
| 2026-11-02 04:01:36 | hardread | `2be8712` | Update the luck caveat with the second information-edge test |  |
| 2026-11-02 04:02:00 | hardread | `49a7574` | Describe the second luck test level by its setting |  |
| 2026-11-02 10:56:17 | hardread | `afe2bb5` | Say when assumed sets fall back to a plain default; ship the species profiles with the app |  |
| 2026-11-02 10:57:36 | hardread | `4ac4d35` | Describe the species profiles data |  |
| 2026-11-02 13:08:05 | fork | `be712e8` | Note the likely Roaring Moon crash; code unchanged |  |
| 2026-11-02 13:18:40 | fork | `414c6aa` | Handle species missing from the Smogon usage table |  |
| 2026-11-02 13:45:41 | fork | `14b08f2` | Explain the crash and the guard in the fork note |  |
| 2026-11-02 13:47:35 | fork | `8e43a61` | State the measured crash count |  |
| 2026-11-05 00:17:54 | hardread | `49e427f` | Reviews use only what was known at each decision; label older figures |  |

## Verify

```sh
# HardRead
git log --reverse --before=2026-10-26T16:00:00Z --format='%h %cI %s'
git log --reverse --since=2026-10-26T16:00:00Z --before=2026-10-27T04:00:00Z --format='%h %cI %s'
git log --reverse --since=2026-10-27T04:00:00Z --format='%h %cI %s'
# Foul Play fork (our commits only)
git log --reverse --format='%h %cI %s' 3264ac8bf0b4..roaring-moon-guard
```

Git dates are set by the committer's machine. They are evidence, not proof.
