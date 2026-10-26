# Hax or You: replay review engine

Paste a Showdown replay plus your team export; get, per turn, a calibrated win probability, the luck you got from chance events, and the cost of each of your decisions. Everything runs offline on Foul Play's own code (battle updates, set sampler, poke-engine MCTS); no server.

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
| `review.py` | The CLI that combines the above into a JSON review. |
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

**Human replays:** a 53-turn Gen 9 OU ladder replay (1566 vs 1792) with a team drafted from what the replay reveals reconstructs cleanly: 58 decisions, 4 actions hidden. There is no ground truth for it.

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

**Scale change.** These numbers use the raw-root-value scale (v1). `review.py` now prices options on the absolute scale; the same human review then costs 0.014 WP per decision (turning point unchanged: turn 11, 0.25). The bot noise floor has not yet been re-measured on that scale.

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
- **Calibration source.** The calibrator was fitted on live C1t1 root values from bot self-play. It is applied to the review's 4 × 1 s values (corr 0.988 with live values; mean difference 0.018) and to short-MCTS branch values, and to human games. Early turns carry almost no signal.
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
