# HardRead pre-registration (v1, 2026-10-26)

This file fixes the evaluation design before any sealed game is played. The annotated git tag `prereg-v1` on the commit that adds this version is the timestamp. Any later change is listed in a dated amendment section; nothing above it is edited.

The sample size was approved by the team on 2026-10-26 ("you can even add more games").

## 1. Primary hypothesis (Ending A)
**H1:** the HardRead engine beats vanilla Foul Play at the stated configuration.
- **HardRead engine:** Foul Play plus the levers frozen on D6 (Nov 1) 12:00 Paris.
- **Vanilla Foul Play:** upstream commit `8f541f80`, with the same data snapshot (`DATA_SNAPSHOT.json`) and the same Showdown commit `c0845f33`. The only patches are local login, which is protocol-only.
- **Wording if H1 holds:** "the strongest open-source Gen9 OU agent at this configuration." Never "SOTA Pokémon AI".

| Item | Value |
|---|---|
| Configuration | C1t1: 4 sampled worlds × 2,000 ms × 1 thread per world, for both sides. Student inference counts inside HardRead's time. |
| Teams | `data/sealed-v1.json`: 1,000 teams, one per roster, from the 2,556 reserved rosters never used in development. Content SHA-256 `d60e036d…6311`. |
| Pairing | Each game draws two different sealed teams (fixed seed). Each pairing is played twice with the teams swapped. The challenger seat alternates the HardRead side every pairing. |
| Sample size | **n = 10,000 games (5,000 pairings)** at C1t1 on ≤ 10 nodes, at 4–5 games per node in parallel (≈17 h on 10 nodes). Expected 95% half-width ≈ ±0.9 points (from the F1 A/A: ±2.04 at 998 pairings), so power ≈ 90% for a +1.5-point effect. |
| Primary metric | HardRead's win rate. Estimate: mean over pairings of the pairing's HardRead score, with a 95% interval clustered by pairing. |
| Test | Two-sided, α = 0.05 (normal approximation on pairing means). No interim looks; n is never cut. |
| Failures | A bot crash or hang is a loss for that bot (vanilla Foul Play has known crash bugs: Magic Room terrain, fifth move index). Infrastructure interruptions are re-run with the same pairing and seed. |

## 2. Harness checks (must pass before the freeze)
- **A/A 1:** vanilla vs vanilla.
- **A/A 2:** the HardRead harness with all levers off vs vanilla.
- **Design:** C1t1, n = 2,000 each (1,000 side-swapped pairings), on development teams.
- **Pass:** both within 50% ± 2.2 points (95% interval of the paired estimate inside the band).
- **If either fails:** fix the harness and rerun, or report the failure and use Ending B.
- **Done so far (F1, not C1t1):** side-swapped A/A `hraa-20261026-310d28` gave 49.35% ± 2.04 (RESULTS.md).

## 3. Confirmatory and secondary tests (fixed order; each runs only if the previous one is significant)
1. **Ladder-like configuration:** C2 (8 worlds × 3,500 ms), n = 2,000 games on the same sealed design.
2. **Reveals-only:** the belief uses only revealed information (no learned prior beyond public usage statistics), vs vanilla, C1t1, n = 2,000.

## 4. Ending B (if H1 fails or the harness checks fail)
- **Belief:** the learned reader vs Foul Play's own belief on the hidden-set benchmark (§5).
- **Value of information:** the curve from §6.
- **Reporting:** the head-to-head result is reported as measured, with its interval, whatever its sign.

## 5. Hidden-set benchmark (S-A)
- **Test set:** decisions from games on the `hr_val` rosters (`data/pools-v1.json`; 596 rosters never used for training).
- **Target:** each visible opponent Pokémon's attributes that are still hidden at decision time: item, ability, tera type, unrevealed moves, nature/EV spread.
- **Primary metric:** item negative log-likelihood (ε floor 1e-4) on hidden items. Secondary metrics: item top-1 accuracy, plus ability, tera and move recall.
- **Baselines:** Smogon usage prior with the public constraints; Foul Play's sampler (Monte Carlo); the as-played sampled worlds.
- **Comparison:** the learned reader vs Foul Play's sampler, paired by example, bootstrap by game.

## 6. Value of information (exploratory, D1 gate)
- **Design:** an oracle gives one side the opponent's true sets with probability p per Pokémon per world (all fields). p ∈ {1, 0.5, 0.25}, C1t1, 300 side-swapped pairings on `hr_val` rosters, oracle seat alternating every pairing (jobs `hrvoi-20261026-*`).
- **Gate:** p = 1 at ≥ 58%, and the p = 0.5 lower bound above 50%.
- **If the gate fails:** HardRead keeps only engine levers (no belief lever).
- **Status:** exploratory; it decides effort, not the claim.

## 7. Data and code identity
Every reported run lists:
- the HardRead commit;
- the Foul Play fork commit;
- the team-pool hashes (`data/pools-v1.json`, `data/sealed-v1.json`);
- the Azure job names.

`RESULTS.md` lists every run, including failures.

## 8. Schedule
- **D5–D6 (Oct 31 → Nov 1 10:00 Paris):** A/A checks at C1t1.
- **D6 12:00 Paris:** freeze.
- **D6 → D7:** the sealed primary (≈17 h), then the confirmatory C2 run (≈9 h) if the primary is significant. Then reveals-only, if time allows before the D7 analysis.

## Amendments

### A1 (2026-10-26, before any lever or sealed data): development sets
- **Development set A** (lever tuning): the `hr_train` pool.
- **Development set B** (G2 gate on D4, n ≥ 1,000 at C1t1): the `hr_val` pool. Its rosters are never used to train the student, the belief model or Glimmer.
- Lever screens and gates use the same design as the primary: side-swapped pairings, with the treatment seat alternating every pairing (`run_match.py --treat ... --treat-seat alternate`).
- Nothing in §1–§3 changes.

### A2 (2026-10-27, before any sealed game): contamination filter for external replays
- **Rule:** any model trained on external human replays (Metamon raw, crawled public replays) must first drop every game in which either team's 6-species roster equals an `hr_val` roster or a reserved/sealed roster (split v1).
- **Covers:** the belief model, the student and Glimmer SFT.
- **Reason:** the sealed and `hr_val` teams come from real ladder teams, so those teams' revealed sets appear in public replays (29% of human belief rows matched).
- **How the filter is built:** it reads only the reserved teams' species lists. No sets, items or spreads of sealed teams are read.
- Nothing else changes.

### A3 (2026-10-27 ≈02:00 UTC, before any pre-registered A/A game and before any sealed game): A/A decision rule; "vanilla" and A/A 2 as run; failure attribution; sealed layout
**Data status when this was written.** No §2 A/A game has been played (they are scheduled for D5–D6) and no sealed game exists. The only A/A data are exploratory: `hraa-20261026-310d28` (F1, 998 pairings, `hr_hl_dev` teams) and `hraa-20261026-c1t1-n{1,2,3}-d106` (C1t1, `hr_train` teams, running until 08:00 UTC Oct 27). Their spread, not their outcome, motivates item 1. Recorded at the team's request after an internal review of the handoff (finding F1).

1. **A/A decision rule (replaces the parenthesis of §2 "Pass").** Each A/A keeps n = 1,000 side-swapped pairings (2,000 games), C1t1, development teams. It passes if both:
   - (i) its point estimate lies within 50 ± 2.2 points; and
   - (ii) the two-sided test of its pairing means against 50% is not significant at α = 0.05 (|z| < 1.96; the §1 estimator and interval).

   **Reason.** The measured spread is ≈32 points per pairing (32.2 on the C1t1 A/A at 210 pairings, 32.9 on the F1 A/A at 998), so at 1,000 pairings the 95% half-width is ≈±2.0 points. The original wording ("95% interval of the paired estimate inside the band") then requires |estimate − 50| ≤ 0.2: a correct harness passes it ≈16% of the time per A/A (≈2.5% for both). The new rule passes a correct harness ≈95% of the time per A/A (≈90% for both), and fails a harness biased by 2.2 points ≈58% of the time, by 3 points ≈84%, by 4 points ≈97%. This restores the intent of plan v5.2 (`research/16`: "land within 50% ± 2.2"). Rejected alternative: keeping the strict rule needs ≈2,260 pairings per A/A for an 80% pass rate.

   Applied to the exploratory F1 A/A: 49.35 ± 2.04 [47.31, 51.39] fails the original literal rule and passes this one (|−0.65| ≤ 2.2; z = −0.62, p = 0.53).
2. **"Vanilla Foul Play" as run (clarifies §1).** Every head-to-head (screens, A/As, G2, sealed) runs both bots from one directory: the HardRead fork of Foul Play (branch `hardread` = upstream `8f541f80` + local guest login + HardRead hooks; `tools/run_match.py` has a single `HARDREAD_FOULPLAY_DIR`). The vanilla side runs with every hook off: no `HARDREAD_LEVER_*`, `HARDREAD_ORACLE_*`, `HARDREAD_POLICY` or decision-log variable takes effect for it (`run_match.py` strips the lever and oracle variables from both bots and gives `--treat` variables to the treatment side only). Each hook is gated on its environment variable and is a no-op when unset (code inspection; the fork's 745 tests pass). The sealed opponent is therefore "Foul Play `8f541f80` plus local login, run from the HardRead fork with all hooks off", not a pristine upstream checkout. This was never compared against a pristine binary, and no A/A below can detect a difference between the two, because both sides run the fork. The fork commit used is recorded at the freeze (A4).
3. **What each A/A checks (clarifies §2).**
   - **A/A 1, vanilla vs vanilla** (no `--treat`; analysed with `--mode seatA`): seat bias, team-assignment asymmetry, harness and analysis bugs.
   - **A/A 2, "harness with levers wired but neutral" vs vanilla** (`--mode treat`). It replaces "harness with all levers off", which with one shared binary is identical to A/A 1. The treatment side gets the frozen lever configuration with every lever that has a neutral setting set to it (student-blend `HARDREAD_STUDENT_LAMBDA=0`; belief lever `HARDREAD_BELIEF_LAMBDA=0`). Levers without a neutral setting (select `argmax`/`value`, worlds `double-half`/`half-double`) stay unset. The jobs ship the frozen checkpoints (`STUDENT_NPZ`, `BELIEF_NPZ`, `BELIEF_WORLDS_NPZ`): without them both levers silently fall back to Foul Play. A/A 2 checks the levers' compute overhead (student and belief code still run and take time from the search), crashes and fallbacks in the lever path, and one-sided wiring errors.
   - **Validity check before reading A/A 2 (and the sealed run):** `tools/analysis/attribute_failures.py --check-treat-markers` must report `valid: true`. That means: lever log lines on the treatment side only, zero fallback warnings, and at most 1% of decided treatment logs without lever lines (bots that crashed at turn 1).
   - **Layout:** each A/A = 20 jobs × 5 chunks × 10 side-swapped pairings = 1,000 pairings, `--team-pool gen9/hr_train`, C1t1 both sides, `--timeout 1800`. Seed bases: A/A 1 `2,000,000 + 10,000·j`; A/A 2 `3,000,000 + 10,000·j`; j = 0–19.
4. **Failure attribution (makes §1 "Failures" operational).** Undecided games are labelled from the bot logs by `tools/analysis/attribute_failures.py`:
   - a traceback, Rust panic or non-zero exit on exactly one side is a crash of that bot, counted as a loss for it;
   - connection/server errors, missing logs or an incomplete chunk with no bot crash is infrastructure, rerun per item 5;
   - both bots crashed, or a hang with no evidence on either side, is "unknown": excluded and listed, with a sensitivity bound (all as losses / all as wins for HardRead) reported next to the estimate.

   The primary estimate is that tool's `pre_registered` output: `h2h.py`'s estimator after this rule. A crash of the accepting bot (side B) already ends as a decided game: Showdown forfeits it, so the loss is counted for the crashing bot. Observed so far: 14 undecided games among 8,015 game records checked (screens, A/A, VoI, label and hr_val farms), all crashes of the challenging bot (side A). Twelve are Foul Play's `ValueError: Could not find keldeoresolute in team_dict` at turn 1; two are a `ZeroDivisionError` in `select_move_from_mcts_results`.
5. **Sealed primary layout, seeds and rerun rule (makes §1 "Pairing"/"Sample size" operational).**
   - **Layout:** 25 jobs, j = 0–24. Job j runs `azure/farm_chunks.sh` with prefix `sealed-j<jj>` (two digits), 20 chunks of 10 side-swapped pairings (20 games), `seed_base_j = 1,000,000 + 10,000·j`; chunk k uses seed `seed_base_j + k`. 25 × 20 × 10 = 5,000 pairings = 10,000 games.
   - **Configuration:** C1t1 both sides, `--team-pool gen9/hr_sealed`, `--treat <frozen configuration> --treat-seat alternate`, `--timeout 1800` per game, job timeout 43,200 s, no deadline.
   - **Rerun rule:** a chunk with an infrastructure failure (the tool's `rerun_chunks`, or a chunk never packed) is rerun whole, with the same seed (`seed_base_j + k`), in a new job, and the rerun replaces the chunk. Every rerun is listed in RESULTS.md. No chunk is rerun or dropped for any other reason.
   - **No interim looks:** no win rate is computed before every chunk is in.
   - **Nodes and wall time are not part of the design** (§1 said "≤ 10 nodes, ≈17 h"). The run uses up to 25 nodes, ≈6–8 h at the measured ≈17–23 min per 20-game chunk.
6. **Numbering.** The freeze record (frozen configuration, checkpoint SHA-256, both commits, sealed pool hash) will be amendment **A4**.

Nothing else changes: H1, n, the configuration, the teams, the primary metric, the test and the confirmatory order stay as in §1–§3.

### A3a (2026-10-27 ≈10:00 UTC, before any G2 game, any pre-registered A/A game and any sealed game): timing; A/A 2 for configurations without neutral levers
**Approval.** The owner approved this amendment, both parts (a) timing and (b) the "full" option, in chat on 2026-10-27 at ≈10:55 Paris (recorded in `handoff/DECISIONS.md`, OD-2).

**Data status when this was written.** Dev-A data only (`hr_train` teams):
- the lever screens (selection/worlds/student-blend screens ended 08:00 UTC Oct 27; the three belief-lever screens are running until 14:00 UTC);
- the exploratory C1t1 A/A;
- the F1 replication of `value` on fresh dev-A seeds (submitted 08:22 UTC, running).

No G2 game (dev B, `hr_val`), no pre-registered A/A game and no sealed game exists.

1. **Timing.** G2 (A1) and both A/As (§2 as amended by A3) may run from Oct 27. The freeze of §1 and §8 moves to **Thu Oct 29, 12:00 Paris if both A/As have passed by then** under A3 item 1; otherwise it stays at Sun Nov 1, 12:00 Paris. **Reason:** the result must exist before the hackathon video is cut, and no lever development is planned after Oct 27.
2. **A/A 2 without neutral levers.**
   - **Setup.** If the frozen-candidate configuration contains no lever with a neutral setting (e.g. `HARDREAD_LEVER_SELECT=value`, `HARDREAD_LEVER_WORLDS=double-half`), the treatment side receives `--treat HARDREAD_LEVER_SELECT=fp`: the upstream default, a no-op that exercises the treatment-only environment path.
   - **Validity check:** zero lever-marker lines on both sides, zero fallback warnings, and a recorded `treat_side` for every decided game.
   - **Pass rule:** A3 item 1's rule is unchanged.
   - **Reason:** with no lever set, no marker line is ever logged, so A3 item 3's marker check cannot be met.
3. **Candidates.** The G2 candidates are fixed by the rule logged in RESULTS.md at 2026-10-27 04:37:22 UTC (commit `9fc5771`), before the screen readout: `value`, plus `value` + `double-half` only if double-half qualifies. Double-half ended at 48.09 and did not qualify, so the only candidate is `value`. Belief levers are not candidates: their model is pre-trained on human replays without a stated data licence.

Nothing else changes: H1, n, the configuration, the teams, the primary metric, the test, the A3 §5 layout and rerun rule, and the confirmatory order.


### A3b (2026-10-27, owner-approved before any sealed game): freeze timing only
**Approval.** The owner approved OD-25 in chat: “Approve conditional Wednesday Oct 28 freeze.” Approval was recorded on 2026-10-27 at 16:42:02 UTC (17:42 Paris), before any sealed game, sealed-content read, or sealed job. Both A/A harness checks have passed under A3. This amendment is prospective for sealed data; it does not redesign any completed experiment.

1. **Timing.** If the reviewed **pre-freeze S2.10 kit** is accepted by **Wed 2026-10-28 10:00 Paris**, the freeze of §1 and §8 moves from Thu 2026-10-29 12:00 Paris to **Wed 2026-10-28 12:00 Paris**. The kit check follows C11's reviewed sequence: the actual sealed tarball SHA remains pending until the permitted post-A4 build; no sealed team bytes are read to satisfy the pre-freeze condition. If the kit condition is unmet, A3a remains in force: Thu 2026-10-29 12:00 Paris if both A/As have passed by then, otherwise Sun 2026-11-01 12:00 Paris.
2. **No other change.** H1, candidate, configuration, teams, sample size, pairing, primary metric, statistical test, no-interim-look rule, A/A pass rules, A3 §5 layout and rerun rule, confirmatory order, and all sealed-access and submission gates remain unchanged.
