# Hax or You: replay review engine

Paste a Showdown replay plus your team export; get, per turn, a win probability from a fitted formula, the luck you got from chance events, and the cost of each of your decisions. Everything runs offline on Foul Play's own code (battle updates, set sampler, poke-engine MCTS); no server.

```sh
PY=../environments/foulplay/bin/python   # from hardread/
$PY tools/review/review.py REPLAY.json --team TEAM.txt --side p1 --out review.json
```

`REPLAY` can be a Showdown replay `.json` (`{"log": ...}`), a farm battle-log `.json`, or plain protocol lines.

| File | What it does |
|---|---|
| `reconstruct.py` | Replays the log through `fp.battle.protocol` exactly as the bot receives it live: the same message batches, a synthesized `|request|` per batch, and the same copies `find_best_move` makes. Output per decision: turn, k, view (decision-logger v2 format), legal actions, the action read from the log. `validate` compares against farm decision logs. |
| `evaluate.py` | Foul Play's search on a reconstructed decision (`prepare_battles` then `get_result_from_mcts`; default 4 worlds × 1 s). Per option: value and visit share; also the decision cost (best minus chosen) and the root value. `noise` measures the bot's own decision cost. |
| `calibrate.py` | Maps root value to P(win). Isotonic, Platt, or raw, chosen by 5-fold cross-validation over games. `--mirror` runs the seat-consistency check. Writes `calibrator.json`. |
| `value.py` | **Absolute state value** A(s) = eval(s) + logit(V_mcts(s))/0.0125: a Python port of poke-engine's `evaluate` (identical to the Rust CLI on 300 states, max diff 1e-4) plus the search correction. Needed because poke-engine's MCTS scores are *relative to each root's own eval*. |
| `luck.py` | **Luck v2** (the validated hax meter):<br>• chance outcome classes from `generate_instructions`, valued on the absolute scale;<br>• sampled worlds conditioned on the damage the log shows;<br>• hidden actions as a move mixture.<br>Subcommands: `farm` (self-play runs; optional student evaluator) and `analyze` (the four checks with bootstrap CIs). |
| `hax.py` | Luck v1 (superseded; failed the sanity check). Its branch-matching helpers are reused by `luck.py`. |
| `review.py` | The CLI that combines the above into a JSON review (includes the `bridge` block). |
| `bridge.py` | **The bridge** Start → Luck → Choices (both players) → Result (research/27 §4.2): S = start win probability, L = total luck, R = result, C = R − S − L; integer points s/l/c that always add up (half-up rounding = JS `Math.round`); forfeit/timeout tag; diagnostic split C = Σact + drift + end. Pure Python. `review.py` stores `bridge.from_review(review)` under `"bridge"`: the UI reads `bridge.s/l/c` and `bridge.labels` from there and never recomputes or sums disagreements. |
| `validate_bridge.py` | Validation of the bridge on human-eval records (V1–V7; research/28 S1.5/E6). `--gate-file` names the gated input (Draft B / export path); exit 0 iff every acceptance row passes. Writes `scratch_ds/review/bridge_validation.json`. |
| `select_offered.py` | The product's disagreement rule (WP before ∈ [0.20, 0.80], cost ≥ 0.05, known action, ≤ 3 per side by cost; `offered_from_review` for review.py JSON) and the VT1 population builder from `human_eval.py farm` output. |
| `verdict.py` | **Verdict v1** (research/28 §0.2): stated read merged into sampled worlds (U) vs the reference (R: revealed later, else the engine's estimate); seeds 11/12/13, 4 worlds × 20k iterations, CRN on worlds only; signed contrast Δ = WP(X) − WP(chosen); Misplay / Misread / Defensible / Within noise at m = 0.05; contradicted reads reported, not judged; results cached. `batch` (VT1/VT2), `one` (product), `rescore`. |
| `test_verdict.py` | VT3: the rule table, Wilson bounds, merge semantics, contradictions, Knock Off / tera edge cases, revealed-later reference (Foul Play venv). |
| `test_bridge.py` | Identity on 50 random sides, integer rounding, UI JSON = `bridge.py`, end tags (`python3 tools/review/test_bridge.py`). |
| `human_eval.py` | The review pipeline at scale on human replays (`run`), on farm games with the bot's own choices (`farm`, noise floor), and `analyze` (calibration, luck checks, decision cost vs rating). |
| `fpenv.py` | Imports and configures Foul Play in-process. |

**How the request is synthesized.** Live, Foul Play gets the user's exact state from Showdown's `|request|` JSON. Offline, `UserTracker` rebuilds it from the team export and the log, following Showdown's rules for:
- HP, status, items (consumed, knocked off, tricked, Magician/Pickpocket, the silent Sticky Barb transfer);
- abilities (forme changes; Regenerator, which Neutralizing Gas suppresses);
- PP, including Pressure, which counts only real targets (none on `[notarget]`; hazards only through `mustpressure`);
- disabled moves: choice lock, Encore, Taunt, Disable, Torment, Heal Block, Throat Chop, Assault Vest, Gigaton Hammer, Belch;
- locked moves: Outrage (renewed only on a hit), two-turn moves (instant in sun or with Power Herb), recharge, Struggle;
- trapping: moves, partial traps, Magnet Pull "maybe trapped"; Ghost types and Shed Shell are immune;
- tera availability and the displayed forme: Greninja-Bond shows as Greninja; the user's own `|poke|` details are used.

Public replays show percent HP for both sides. The user's exact HP is then estimated from the team's max HP (error ≤ 1% of max HP).

## Reconstruction accuracy (label farm C1t1, 250 games, 500 game-sides, 21,351 decisions)

**Correct** means all of the following match the bot's live decision log:
- same (turn, k) decision point;
- same legal set;
- same action;
- own and opponent public state equal: species, active, HP, max HP, status, item, ability, tera type and state, moves, boosts, side conditions, weather, terrain, Trick Room.

Belief inputs (impossible items and abilities, speed range, choice-item flag, user nature and EVs) are reported separately.

| Log format | Decisions | State + legal | Legal set | Action (when the log shows it) | Action shown by the log | Strict (all of it) |
|---|---|---|---|---|---|---|
| Farm (exact HP for both sides) | 21,351 | **99.98%** | 100% | **100%** (19,817 / 19,817) | 92.8% | **92.8%** |
| Public-replay proxy (same logs, percent HP for both sides, own HP within 1%) | 21,351 | **99.6%** | 100% | 100% | 92.8% | **92.4%** |

- **No crashes,** no missing or extra decision points.
- **Every miss in the strict rate is an action the log does not show** (7.2%):
  - the user's Pokemon fainted, or was asleep, fully paralysed or flinched, before moving;
  - or a faster Encore replaced the chosen move (44 cases, detected and flagged).
  - When only one move was possible (choice lock, Encore, locked move), the action is inferred (77 cases).
  - Otherwise `review.py` reports the decision cost as a range over the possible moves.
- **Remaining state mismatches** (exact logs):
  - 2 opponent item;
  - 2 user ability.
- **Public-format proxy:** 79 extra HP mismatches beyond 1%.
- **Belief inputs equal: 98.6%.** The rest is the opponent's speed range (295 decisions). Foul Play's speed inference reads its own last selected move, which the log does not reveal when the user's Pokemon fell before moving.

**Human replays** (`tools/human/reconstruct_check.py`, 50 top-50 ladder games, both seats, teams drafted from the replay; no ground truth, cross-checked against `parse_replays.py`):

| Run | Clean decisions | Turn-start state clean | Failed sides |
|---|---|---|---|
| Drafted teams without nicknames, before the fixes | 77.8% (4,337) | 74.2% | 1 |
| **Drafted teams without nicknames, after the fixes** | **99.98%** (4,371 / 4,372) | **100%** | **0** |
| With nicknames, before → after | 99.98% → 99.98% (4,371 / 4,372) | 99.97% → 100% | 1 → 0 |

**Fixes:**
- **Nicknames.** The user's percent HP is now scaled through a nickname → species → max-HP map built from the log's own `|switch|`/`|drag|`/`|replace|` lines, so an export without nicknames works.
  - Foul Play's export parser reads the first "(…)" as the species, so it now gets a nickname-free export (a nickname like "Surf's Up (2007)" had broken team setup).
- **Revival Blessing.** The action is built from the revived Pokémon's species, not its nickname.
  - The decision is tagged `revival`, and its menu is Showdown's (the fainted Pokémon), since poke-engine has no revival menu.
  - Foul Play's `last_selected_move` stays `revivalblessing`. A "switch X" there crashed its speed inference (`KeyError: 'switch…'`).
- **Healing Wish / Lunar Dance on the opponent.** Showdown cures status silently; the reconstruction now injects a `-curestatus` before the `-heal`.
  - The live bot's own view keeps the stale status (a Foul Play bug), so farm validation runs with `cure_healing_wish=False` (fidelity to the live view). Reviews use the correct state.
  - With the cure on, farm views differ at 72 of 21,351 decisions, exactly where the live bot's view was wrong.

The single remaining unclean decision in both human runs is a Revival Blessing choice, for which `parse_replays.py` writes no row to compare against.

## Calibration (same 250 games; out-of-fold, 5 folds by game)

| | Brier | ECE | Log loss |
|---|---|---|---|
| Raw root value | 0.2062 | 0.018 | 0.589 |
| **Platt (saved)** | **0.2060** | **0.0075** | 0.589 |
| Isotonic | 0.2064 | 0.0088 | 0.590 |
| Coin flip | 0.2500 | — | 0.693 |

**Brier by turn bucket (Platt):**

| Preview | 1–5 | 6–15 | 16–30 | 31+ |
|---|---|---|---|---|
| 0.248 | 0.251 | 0.225 | 0.181 | 0.189 |

- **Raw values are already close to calibrated.** Platt only lowers ECE; the Brier gain over raw is within noise (game-bootstrap 95% CI [−0.0011, +0.0008]).
- **The early game carries no signal:** turns 0–5 score like a coin flip.
- **Saved calibrator:** `calibrator.json`, Platt fitted on all 250 games, P = σ(1.176·logit(v) + 0.031).

**Raw root values are relative, not absolute.** poke-engine's MCTS scores each leaf as σ(0.0125·(eval(leaf) − eval(root))), so a root value measures "better or worse than now". The review now uses the **absolute value** A = eval + logit(V_mcts)/0.0125 (`value.py`), calibrated as P = σ(a·0.0125·A + b); `calibrator.json` → `absolute`: a = 0.966, b = 0.017.

| Win probability from | Out-of-fold Brier (by game) |
|---|---|
| **Absolute value (now used by `review.py`)** | **0.154** (250 games, 18,120 turn starts); 0.156 over 500 games |
| Raw root value + Platt | 0.206 |
| Student value head (on 250 games it never saw) | 0.169, vs 0.159 for the absolute value on the same games |

**Mirror gap |P_A + P_B − 1| with absolute values** (9,060 matched pairs): mean **0.030** (raw root values: 0.061), median 0.016, p90 0.077; the mean sum is 1.000.
- **Mirror check** (`calibrate.py --mirror`; 9,310 matched (game, turn, k=0) pairs): |P_A + P_B − 1| has mean 0.061, median 0.038, p90 0.139, p99 0.42. Raw sums average 0.968.

| Bucket | Preview | 1–5 | 6–15 | 16–30 | 31+ |
|---|---|---|---|---|---|
| Mean gap (raw) | 0.034 | 0.048 | 0.061 | 0.069 | 0.062 |

- **Calibration does not shrink the mirror gap; it widens it slightly:**
  - pooled Platt 0.0645;
  - seat-A-only Platt 0.0755, isotonic 0.080.

  The gap is two seats with different private information and independent search noise, not a monotone miscalibration a per-seat map can fix.

## Decision cost noise floor (`evaluate.py noise`, 6 C1t1 games, 225 bot decisions, review search 4 worlds × 1 s)

| Measure | Value |
|---|---|
| Bot's own choices: mean cost | **0.0015 WP** |
| Bot's own choices: median / p90 | 0 / 0.003 |
| Bot decisions costing > 0.02 / > 0.05 | 1.4% / 0.5% |
| Random other legal option: mean cost | **0.089 WP** |
| Review best = bot's choice | 76% of decisions |
| Review root value vs logged root value | corr 0.988, mean abs diff 0.018 |

**So costs below about 0.02 WP are noise.** The 1566-rated human in the public replay averaged 0.032 WP per decision, about 20 times the bot's average. That review used a team drafted from the replay, so read it as an illustration.

**Scale change.** These numbers use the raw-root-value scale (v1). `review.py` now prices options on the absolute scale; the same human review then costs 0.014 WP per decision (turning point unchanged: turn 11, 0.25). Re-measured on the absolute scale (4 worlds × 20k iterations, 956 farm bot decisions): mean 0.0077, median 0, p90 0.028, 4.3% above 0.05. This cheaper search is noisier than the 4 × 1 s one; see the human-games section.

## Luck ("hax meter"): v2 passes the self-play checks

### What was wrong with v1
v1 valued each chance branch with an MCTS rooted at that branch. poke-engine scores leaves *relative to the root's own static eval* (`src/mcts.rs`: `sigmoid(eval(leaf) − root_eval)`). Values of different branches were therefore on different scales, and an immediate gain such as a KO was subtracted out.

**Controlled check (a): crit KO vs a 6%-HP survivor (same turn, same world).**

| Branch (probability) | v1 (relative) | v2 absolute A | v2 win probability |
|---|---|---|---|
| Crit KO (3.75%) | 0.625 | **−59** | **0.32** |
| No crit, target at 6% (86.25%) | 0.672 | −73 | 0.29 |
| Miss (10%) | 0.496 | −161 | 0.12 |

v1 ranked the crit KO below the survivor; v2 ranks it highest, as it should for the attacker.

### Estimator (`luck.py`)
At each turn start:
1. Draw opponent worlds with Foul Play's sampler (belief-averaged).
2. **(c) Condition on observed damage.** A world is rejected if no branch reproduces the HP the log shows for the Pokémon involved that turn (sum |ΔHP| > 0.12). Up to 3K draws are made to keep K = 4 worlds.
   - 17% of worlds are rejected.
   - On 5% of turns every draw fails; those turns fall back to the unconditioned worlds.
3. Enumerate the chance branches of the actual action pair with `generate_instructions`.
4. **(b) Outcome classes:**
   - group branches by faints, statuses, boosts, and damage dealt to 3%: 3.1 classes per world on average;
   - value one representative state per class on the absolute scale A, with a 10k-iteration MCTS correction;
   - calibrate A to a win probability.
5. Pick the realised class: discrete outcome first, then the log's crit flags, then HP.
6. luck = WP(realised) − Σ p_c·WP(c), averaged over the accepted worlds.

**Hidden actions** (a Pokémon fainted or was immobilised before moving): treat the hidden action as a uniform mixture over that Pokémon's usable moves, for both seats. Earlier fallbacks (one guessed move for the user, "no move" for the opponent) caused most of the bias; on 60 games the mixture moved the bias from −0.0013 to −0.0006 per decision and improved every other check.

**Variants:**

| Name | Definition |
|---|---|
| `branch` | The same, without damage conditioning. |
| **`branch_cond`** | **Product default.** |
| `event` (d) | `branch_cond` counted only on turns where the log shows a chance event: crit, miss, full paralysis, sleep or freeze skip, flinch, secondary status. On a turn with one event this equals the event-level counterfactual, (1 − p)·(WP(event) − WP(no event)). |

### Results (`luck.py analyze`)
**Data:** C1t1 self-play, identical bots, both seats scored from their own view, 95% game-bootstrap CIs, calibration cross-fitted by game.

| Check | Label farm, 250 games | Post-cutoff, 250 new games | **Pooled, 500 games** |
|---|---|---|---|
| **(1) Var(outcome − Σluck) / Var(outcome)**, seat A | 0.785 [0.69, 0.89] | 0.804 [0.72, 0.91] | **0.794 [0.73, 0.86]** |
| Same, seat B | 0.742 [0.66, 0.83] | 0.854 [0.76, 0.96] | **0.798 [0.73, 0.87]** |
| Same, seat-symmetrised ((L_A − L_B)/2) | 0.742 [0.66, 0.83] | 0.807 [0.72, 0.90] | **0.774 [0.71, 0.84]** |
| corr(Σluck, outcome), A / B | 0.47 / 0.51 | 0.44 / 0.39 | **0.45 / 0.45** |
| **(2) Mirror corr(L_A, L_B)** per game | −0.83 [−0.87, −0.78] | −0.78 [−0.83, −0.73] | **−0.81 [−0.84, −0.77]** |
| **(3) Mean luck per decision** | −0.00056 [−0.0011, 0.0000] | −0.00050 [−0.0011, 0.0000] | **−0.00053 [−0.0010, −0.0001]** |
| Mean \|luck\| per decision | 0.013 | 0.013 | 0.013 |
| **(4) Event-count baseline** (crits, misses, full para/sleep/freeze/flinch; for − against) | 0.18 [0.10, 0.26] | 0.20 [0.11, 0.28] | **0.19 [0.13, 0.24]** |

**3 November 2026:** Measured before the 2 November fix to how reviews track hidden sets. On the same 499 paired games after the fix: mirror −0.82 [−0.85, −0.79], variance removed 22.5% [16.1, 28.5] (test settings, which differ from the page's search).

**Reading:**
- **(1)** Chance-only luck removes about 20–23% of outcome variance (CIs well below 1). After also subtracting the starting value, the ratio stays at 0.79–0.80.
- **(2)** The two seats largely agree on the sign and size of each game's luck. v1 was −0.55.
- **(3)** The residual bias is −0.02 win-probability points per game, about 8% of one standard deviation of a game's total luck. It is small but statistically nonzero over 500 games.
- **(4)** Luck correlates with the outcome 2.4× better than the event-count floor.

**Other estimators:**
- `branch` (no conditioning) is within noise of `branch_cond`.
- The event-only variant mirrors best (−0.90), but explains almost no variance (ratio 0.96–0.98). Most outcome-relevant chance is in quieter branches: KO-deciding rolls, secondary boosts, consecutive Protect, random drag targets.

**Student value head as the branch evaluator** (option b′; 250 post-cutoff games the student never trained on):
- **Branch views:** the decision's public view, with HP, status, active, boosts, tera, consumed own items, side conditions, weather, terrain and Trick Room taken from the branch.
- **Approximated:** this turn's newly revealed items, abilities and moves are not added, and the event history is the pre-turn history for every branch, so all branches share one value function. The next menu is approximated.

| Estimator | Var ratio A | Var ratio B | corr A / B | Mirror | Bias per decision |
|---|---|---|---|---|---|
| Search-valued (`branch_cond`) | **0.80** [0.72, 0.91] | **0.85** [0.76, 0.96] | **0.44 / 0.39** | **−0.78** | −0.0005 [−0.0011, 0.0000] |
| Student-valued | 0.87 [0.78, 0.98] | 0.90 [0.80, 1.03] | 0.36 / 0.32 | −0.65 | −0.0006 [−0.0010, −0.0001] |

The search-valued estimator wins on every check, so **luck v2 keeps search valuation**.
- **Value scale:** the student's branch values vary much less than the absolute search values. With the stale event history, it under-reacts to the branch outcome.
- **Decision points:** the absolute value is also better calibrated than the student (Brier 0.159 vs 0.169 on the same games).
- **What could still help:** the student may do better with per-branch event tokens, which would need synthesized protocol lines per branch.

**Valuation depth:** not re-swept for v2. For v1 (relative values), 10k and 60k iterations gave luck correlated at 0.90. Under the relative scale, depth did not matter; on the absolute scale it is untested.

**Cost:** median **8.6 s CPU per game-side** (p90 21 s) for luck at 4 worlds × 10k iterations. A whole review is about 30 s for a 14-turn game and 92 s for the 53-turn human replay (table below).

**Product rule:** v2 passes (1), (2) and (4), and comes close on (3). `review.py` now reports per-turn luck from `branch_cond`. Each turn also shows:
- its visible chance events;
- the realised branch's probability;
- the counterfactual swing, WP(realised) − WP(other outcomes).

## Human games (top-50 Gen 9 OU ladder, `human_eval.py`)

**Data:** `scratch_ds/human/` (2,154 public replays, Jul 1 – Oct 26 2026, pseudonymised).
- 520 random games with at least 3 turns, both seats; **513 games / 1,019 game-sides** analysed.
- 2 sides failed on Foul Play's known `Invalid PokemonMoveIndex: 4` panic.
- 55% of games end by forfeit or timeout.

**Pipeline per game-side:**
1. Reconstruct from the public (percent-HP) log, with a team export without nicknames.
2. Search every decision with the review search at fixed iterations: 4 worlds × 20k (in place of 4 × 1 s, for budget). This gives per-option absolute values.
3. Compute luck v2 records: 3 worlds × 10k iterations, hidden-action mixture.
4. Calibrate win probability with the farm absolute calibrator (fitted on the same 10k-iteration valuation).

**Teams.** The *user's own* side is drafted from what that player revealed during the whole replay; the opponent always stays belief-based (Foul Play's sampler), as in a real review.
- **Not an information leak:** the player knows their full team, so the draft gives the review nothing a real user lacks.
- **Less than a real export:** unrevealed fields are guesses, never the true set.

**Draft A (default, the 513-game run)** is `tools/human/reconstruct_check.draft_team`:
- Leftovers when the item is unrevealed;
- the first listed ability;
- Serious nature and 85 EVs in every stat;
- moves padded from common sets.

**Draft B (Smogon, the 146-game paired check)** is `draft_team_smogon`:
- the same reveals;
- the rest taken from the most common Smogon set consistent with them (nature, EVs, item, ability, tera, moves).

Draft A's under-invested sets make the user's own Pokémon weaker than the real ones, which biases luck (below).

### Win-probability calibration
Turn-start decisions, farm absolute calibrator.

| Brier (ECE) | n | All | Turns 1–5 | 6–15 | 16–30 | 31+ |
|---|---|---|---|---|---|---|
| **Humans, all games (forfeits counted as results)** | 32,274 (513 games) | **0.203** (0.052) | 0.251 | 0.215 | 0.183 | 0.182 |
| Humans, KO-ended games only | 16,432 (255) | 0.206 (0.060) | 0.253 | 0.224 | 0.188 | 0.172 |
| Humans, forfeit/timeout games only | 15,842 (258) | 0.201 (0.046) | 0.249 | 0.206 | 0.177 | 0.191 |
| Self-play reference (250 farm games; 2-parameter calibrator, in-sample) | 18,120 | 0.154 (0.021) | 0.234 | 0.184 | 0.126 | 0.116 |
| The review's own search values (4 × 20k), humans, all | 32,291 | 0.203 (0.054) | 0.252 | 0.216 | 0.182 | 0.182 |

**Calibration shifts on humans.** Human outcomes are less extreme than the engine predicts.

| Predicted | Human win rate | Self-play win rate |
|---|---|---|
| 0.05 | 0.12 | 0.04 |
| 0.15 | 0.26 | 0.16 |
| 0.85 | 0.79 | 0.82 |
| 0.95 | 0.92 | 0.96 |

These figures test the fit's own valuation: the luck run at 3 worlds × 10k iterations, with a Draft A team, before the 2 November fix. A review runs 4 worlds × 1 s, with a Smogon draft or your export. So don't read .027 as the accuracy of the win chances on the page.

**Human-specific calibrator** (fitted on 255 games, tested on the other 258): P = σ(0.60 · 0.0125·A + 0.13), much flatter than the farm calibrator (slope 0.97).

| On the 258 test games | Brier | ECE | Log loss |
|---|---|---|---|
| Human calibrator | 0.195 | **0.027** | 0.570 |
| Farm calibrator | 0.196 | 0.042 | 0.576 |

**Reading:**
- Resolution (Brier) is the same; reliability is better.
- Forfeits change little: forfeit games are slightly *more* predictable than KO games (forfeiters quit lost positions).
- Saved as `calibrator.json` → `absolute_human`. Use it for human reviews.

### Luck on humans

**Draft A, 513 games:**

| Check | Result |
|---|---|
| corr(Σluck, outcome), p1 | **0.41** [0.34, 0.48] |
| corr, both seats | 0.39 [0.33, 0.44] |
| corr, KO-ended games | 0.41 [0.30, 0.51] |
| Var(outcome − Σluck) / Var(outcome), p1 | 0.86 [0.78, 0.94] |
| Variance ratio, both seats | 0.88 [0.82, 0.94] |
| Variance ratio, KO-ended games | 0.89 [0.76, 1.04] |
| Seat-symmetrised variance ratio | **0.80** [0.74, 0.87] (corr 0.45) |
| Mirror corr(L_p1, L_p2) | −0.49 [−0.59, −0.39] |
| Mean luck per decision | **+0.0030** [0.0024, 0.0036] |
| Event-count baseline on the same games | 0.14 [0.06, 0.23] |

**Draft A vs draft B, paired on the same 146 games:**

| | Bias per decision | Mirror | corr p1 | Variance ratio p1 / symmetrised | WP Brier (ECE) |
|---|---|---|---|---|---|
| Draft A (default) | +0.0032 [0.0021, 0.0043] | −0.31 [−0.56, −0.08] | 0.35 | 0.92 / 0.84 | 0.198 (0.052) |
| **Draft B (Smogon sets)** | **+0.0004** [−0.0006, 0.0014] | **−0.60** [−0.76, −0.45] | **0.41** | **0.85 / 0.79** | **0.192 (0.036)** |

**Reading:**
- **Draft B gets close to the self-play numbers.** The positive bias and the weak mirror on humans came from the under-invested default drafts: survivals and KOs that are routine with the real sets look lucky from the user's seat. With realistic sets, as from a real export, the bias vanishes and the mirror doubles; the 513-game run used draft A, so its luck bias and mirror understate what a real review gets.
- **Interpreting the variance ratio on humans:** it is not the pure chance share it is in identical-bot self-play.
  - Outcome variance here also contains skill differences between players.
  - Luck can correlate with skill: good players take fewer coin flips when ahead, and force the opponent into them.
  - A ratio below 1 still means luck explains part of the outcome. The ~14–20% removed is luck plus any luck–skill covariance, so treat it as an upper bound on the chance share, and the symmetrised number as the best single estimate.
- **Beyond the event-count floor:** luck correlates with the result 2.8× better than the net count of visible chance events.

### Decision cost on humans
Per decision, absolute scale, review search 4 × 20k.

| | Decisions | Mean | Median | p90 | Share > 0.02 | Share > 0.05 | Agrees with the engine's best |
|---|---|---|---|---|---|---|---|
| All humans | 34,593 | 0.023 | 0.005 | 0.070 | 34% | 16.5% | 41% |
| Top-50 players | 17,710 | 0.023 | 0.005 | 0.070 | 34% | 16.4% | 41% |
| Their opponents | 16,883 | 0.023 | 0.005 | 0.069 | 34% | 16.5% | 41% |
| **Noise floor: the farm bot's own choices** (C1t1), same search | 956 | **0.0077** | 0 | 0.028 | 14% | 4.3% | 60% |

**Does cost separate ratings? No, not among these players.** The rating range is narrow: p10 1588, p90 1843.
- corr(mean cost per player-game, rating) = 0.03 [−0.02, 0.09] over 1,011 player-games.
- Within a game, the lower-cost player is the higher-rated one 48.7% of the time (n = 501).
- The lower-cost player wins 43.8% (n = 504). That is a scale confound: in lopsided positions all options sit near WP 0 or 1, so costs are small, and losing positions dominate there.
- Restricted to balanced positions (WP 0.25–0.75): the lower-cost player wins 53.2% (n = 459, within noise), and the top-50 vs opponent mean cost is 0.0285 vs 0.0294.

**What does work:** humans (top-ladder players) average 3× the bot's noise floor, and 16.5% of their decisions cost more than 0.05 WP (bots: 4%). So the review separates human decisions from the engine's.

**Why it doesn't separate players:**
- the cheap search (20k iterations);
- drafted user sets;
- the narrow rating band.

**Next tests:** the full 4 × 1 s search, and a wider rating range (for example Metamon's raw replays).

**Cost:** about 45 s CPU per game-side for search + luck on this budget setting; the full review setting is about 1.5 min per game.

## Review cost per game (M4 Max, while other jobs shared the CPU)

**v2:**
- Farm game g0019 (14 turns): 27 s total: reconstruct 3 s, evaluate 21 s, luck 3 s.
- Human ladder replay (53 turns): 92 s total: 5 s, 67 s, 20 s.

**v1 measurements (luck v1):**

| Game | Turns | Decisions | Reconstruct | Evaluate (4 × 1 s) | Luck (4 worlds × 20k iterations) | Total |
|---|---|---|---|---|---|---|
| Farm g0019 | 14 | 20 | 2 s | 21 s | 2 s | **25 s** |
| Farm g0002, exact HP | 17 | 20 | 12 s | 23 s | 8 s | **42 s** |
| Farm g0002, public format | 17 | 20 | 15 s | 23 s | 8 s | **45 s** |
| Farm g0000 | 77 | 86 | 72 s | 95 s | 34 s | **201 s** |
| Human ladder replay | 53 | 58 | 13 s | 70 s | 25 s | **108 s** |

**The exact-HP and public-format reviews of g0002 agree:** turn win probabilities within 0.01; total luck −0.111 vs −0.113. Evaluation dominates cost at about 1.1 s per decision with 4 processes; on an idle machine a typical game is roughly 1–2 minutes.

## Limitations

- **Hidden actions (7.2% of decisions).** When the user fainted, was immobilised, or was Encored before moving, the chosen move is unknown. The review gives a cost range over the possible moves; Foul Play's `last_selected_move` then uses a priority-0 stand-in. That is the source of the 1.4% speed-range differences.
- **Luck v2: what remains.** It is belief-averaged and covers only turn-start chance up to the next decision point; chance after a mid-turn pivot is not counted. Damage rolls count only where a roll decides a KO, since poke-engine averages the other rolls.
  - **Residual bias:** −0.0005 per decision.
  - **When every sampled world fails the damage check** (5% of turns), the realised branch can be misread. Example: the real attacker's damage is underestimated in all worlds, so a no-crit KO is matched to the 4% crit branch.
  - **Calibration source:** the calibration and all checks are on bot self-play, not human games.
- **Calibration source.** The farm calibrators come from bot self-play; human games need the flatter `absolute_human` calibrator (above). Early turns carry almost no signal.
- **Public replays.** Own HP is estimated from percent. The in-game team order is taken from the export; it affects only internal indices. Nicknames are resolved from switch lines.
- **Untested or unsupported:**
  - user-side Illusion/Zoroark, Transform, Revival Blessing (none in the data), Imprison, Skill Swap / Role Play on the user;
  - Shadow Tag / Arena Trap (banned in OU; not modelled);
  - formats other than Gen 9 singles with team preview.
- **One game per process.** Foul Play's set datasets are process-global, so review one game per process at a time; `review.py` does.

## Next
1. **Luck v2:**
   - **Residual bias:** when every world fails the damage check, sample from the posterior (reweight sets by damage likelihood) instead of falling back.
   - **Matching rule:** let the absence of a `-crit` line veto crit classes even when only a crit reproduces a KO.
   - **Coverage:** count chance after mid-turn pivots.
   - **Valuation depth:** a dose-response sweep on the absolute scale.
2. **Student evaluator:** give branch views their own event tokens and retest on the hr_val chunks once they land.
3. **Calibration:** refit the absolute calibrator on the review's own 4 × 1 s search values and on human games.
4. **Human gate:** collect public replays with posted teams.
