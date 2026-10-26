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
| `hax.py` | Chance-only luck per turn: chance branches from `generate_instructions` in sampled worlds; the realised branch is matched from the log; luck = WP(realised) − E[WP]. `sanity` is the farm self-play check. |
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

## Luck ("hax meter"): sanity check NOT passed yet

**Method.** At each turn start:
1. Sample worlds for the opponent's hidden sets with Foul Play's sampler, so the luck is **belief-averaged**, not computed in the true world.
2. In each world, enumerate every chance branch of the actual action pair with `generate_instructions`: crits, accuracy, secondary effects, speed ties, random targets, consecutive Protect, and KO-deciding damage-roll splits. poke-engine averages the other rolls.
3. Value each branch's end state with a short MCTS and calibrate it.
4. Pick the realised branch: the discrete outcome first (faints, statuses, boosts), then the log's `-crit` flags, then nearest HP.

Cost: about 12 s CPU per game at 3 worlds × 10k iterations, and about 30 s at the review default of 4 × 20k.

**Farm self-play check** (C1t1, identical bots; 100 games scored from each seat; `hax.py sanity`):

| Check | Result | Verdict |
|---|---|---|
| Total luck vs outcome, seat A | r = 0.016 (R² ≈ 0; SE ≈ 0.10) | **fails** |
| Total luck vs outcome, seat B | r = 0.09 | **fails** |
| Same 40 games, 6× deeper valuation (60k iterations) | r = −0.02; luck correlates 0.90 with the 10k version | not a noise problem |
| Seat consistency L_A vs L_B (ideal −1) | per game −0.55; per turn −0.36; on crit/miss turns −0.41 | partly consistent |
| Per-turn luck vs the bot's own logged ΔWP | r = 0.18, slope 0.63 | signal per turn |
| Mean turn luck | −0.0006 (|luck| 0.010); total luck sd 0.22 per game | close to zero-mean |
| **Engine-free baseline:** net chance events for − against (crits, misses, full para/sleep/flinch) vs outcome | **r = 0.18** (250 games) | chance does matter |

**Reading.** Chance clearly matters in these games, yet the engine-valued luck does not track the outcome. It agrees only weakly with the naive count (r = 0.15). It is consistent across seats because both seats use the same evaluator; it does not track the result.

**Suspects, in order:**
1. poke-engine's short-horizon value of post-chance states. Example: a crit KO is valued *below* the no-crit branch that leaves the target at 6% HP, a "free switch" effect.
2. Pseudo-luck from KO-roll splits in sampled worlds whose bulk or attack is wrong.
3. Chance after a mid-turn pivot decision is not counted.

**Do not ship total luck as a validated "hax" number yet.** Per-turn events and branch probabilities are shown in the review JSON; until fixed, the product should present them as events ("you were crit at 4%"), not as a luck/skill split.

## Review cost per game (M4 Max, while other jobs shared the CPU)

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
- **Luck is not validated** (see above). It is belief-averaged and covers only turn-start chance up to the next decision point. Damage rolls count only at KO thresholds.
- **Calibration source.** The calibrator was fitted on live C1t1 root values from bot self-play. It is applied to the review's 4 × 1 s values (corr 0.988 with live values; mean difference 0.018) and to short-MCTS branch values, and to human games. Early turns carry almost no signal.
- **Public replays.** Own HP is estimated from percent. The in-game team order is taken from the export; it affects only internal indices. Nicknames are resolved from switch lines.
- **Untested or unsupported:**
  - user-side Illusion/Zoroark, Transform, Revival Blessing (none in the data), Imprison, Skill Swap / Role Play on the user;
  - Shadow Tag / Arena Trap (banned in OU; not modelled);
  - formats other than Gen 9 singles with team preview.
- **One game per process.** Foul Play's set datasets are process-global, so review one game per process at a time; `review.py` does.

## Next
1. **Luck.** In this order:
   - condition sampled worlds on observed damage before enumerating;
   - value KO branches after the forced replacement, at a matched ply;
   - score event classes separately;
   - then test a learned value function: an AIVAT correction is only as good as its value function.
2. **Calibration.** Refit on the growing label farm (≈450 games/h), on review-engine values instead of live values, and on human games once available.
3. **Human gate.** Collect public replays with posted teams (tournament threads) for ground-truth-free checks of crash rate and hidden-action rate on human play.
