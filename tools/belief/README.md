# S-A: hidden-set inference benchmark and learned belief (v0, v1)

L2 "Reading the Opponent": how well can the opponent's hidden set (item, ability, tera type, moves, nature/EV
spread) be inferred from public battle evidence, and how does a learned belief compare with the champion bot's own
(Foul Play's set sampler)?

## Files

| File | What |
|---|---|
| `targets.py` | extractor output → one example per (decision, alive opponent Pokémon in the view): public view + context, true attributes (labels), which ones are hidden, the decision's sampled worlds for that Pokémon, data-quality flags, roster split. Options: `--max-per-mon N` (training files: at most N decisions per game, side and opponent Pokémon), `--force-split test` (the hr_val farm). It also holds `excluded_rosters()` (hr_val + reserved rosters) and `capped()` |
| `human_prep.py` | human partial-label examples (`tools/metamon/partial_targets.py`) → capped train/dev/test, **dropping hr_val and reserved rosters** |
| `baselines.py` | beliefs per example: `usage` (Smogon prior + view constraints), `revealed` (revealed-set prior from 2.02M human games), `fpbelief` (**Foul Play's own belief**, decision log v3), `fp` (Foul Play's sampler rebuilt offline, no damage pruning), `played` (the logged worlds) |
| `model.py`, `train.py` | learned belief: v0 (farm full labels) and v1 (plus human partial labels: masked losses, reveal-propensity correction, `--init` fine-tuning, `--worlds` hybrid) |
| `predict.py` | predictions from a saved checkpoint (e.g. v0 on a new test set) |
| `benchmark.py` | S-A metrics, streaming, with a paired bootstrap over games → JSON + markdown |
| `run_v1.sh` | the v1 pipeline behind the Oct 27 results; `run_all.sh` is the v0 pipeline (Oct 26) |
| `export_npz.py` | checkpoint → `.npz` for the belief lever (numpy inference in `foul-play/fp/hardread_belief.py`), with a torch-vs-numpy parity check |
| `lever_offline.py` | offline check of the lever on belief examples: pool vs resampled worlds vs the belief, as mean p(true) |

## Benchmark spec (S-A v1)

**Test sets:**
1. **hr_val farm (the pre-registered S-A test, PREREGISTRATION §5).** Vanilla Foul Play self-play at C1t1 on the 596 `hr_val` rosters (`hrval-20261026-n{1,2}-9231`), with decision log v3. All rows: 146,225 rows from 33,253 decisions in 440 games, covering 290 hr_val rosters. These rosters are never trained on.
2. **Held-out human games.** The metamon 2026H1 subset (rating ≥ 1500). Rows come from the roster-hash test split, with hr_val and reserved rosters removed, at one decision per (game, side, opponent Pokémon): 88,087 rows from 15,674 games and 3,494 rosters. **Labels are only what the opponent revealed by the end of the game.**
3. (v0, Oct 26: hr_train games held out by roster hash, `results/`.)

**Rows.** One row per (decision, alive opponent Pokémon in the view). Every decision counts once per Pokémon.

**Scored only when hidden in the view:**
- **item:** Foul Play's item is `unknownitem` and `removed_item` is null.
- **ability:** the view ability is null. `ability (>1)` restricts to species with more than one legal ability.
- **tera:** not terastallized.
- **moves:** the true moves not yet revealed. For human games: the hidden moves revealed later.
- **nature, EV class (the two stats with the most EVs), spread = nature|EV class:** farm only.

Human rows are scored only where the attribute is hidden *and* was revealed later, excluding forced labels.

**Metrics.**
- **Primary (pre-registered):** item NLL = −ln max(p_true, ε) with **ε = 10⁻⁴**.
- **Secondary:** top-1, mean p(true) (the expected accuracy of one draw, unbiased for sampled beliefs), Brier, zero-mass rate; for moves, recall@k (k = the number of hidden labelled moves), recall@4 and mean P(move).
- **95% CIs** come from a bootstrap over games (2,000 resamples). The **paired difference** to a reference method is computed on the same rows.

**Comparison as pre-registered:** learned reader vs Foul Play's sampler (`fp_belief`), paired by example, bootstrap by game.

## Foul Play's item encoding

Checked in `fp/battle/state.py`, `fp/battle/protocol.py` (`set_item`, `remove_item`), `fp/battle/inference.py` and `fp/search/poke_engine_helpers.py`.

| View `item` | `removed_item` | Meaning | `item_state` |
|---|---|---|---|
| `"unknownitem"` | null | never revealed (initial value; Illusion ending resets it) | `unknown`: the **only** scored case |
| item id | null | revealed by a protocol message **or inferred** (`choicescarf` from speed order, `heavydutyboots` from hazards; `item_inferred` isn't logged) | `known` |
| null | item id | original item revealed, then knocked off / consumed / stolen | `removed` |
| item id 2 | item id | original swapped away (Trick, Thief), now holds id 2 | `removed` |
| null | null | holds nothing, original unknown (Z-move path only) | `none_unexplained`: never seen |

In the logged worlds (`worlds[].opp_sets`) "no item" is the **string `"None"`**: `battle_to_poke_engine_state` writes it on the battle copy before logging. In `fp_belief` histograms, values are `str()` of the sampled field, so a null is `"None"`.

## Baselines

- **usage:** Smogon chaos stats (`fp/data/smogon_stats_cache/gen9ou-0.json`, the farm's copy) renormalised after the view's hard constraints:
  - impossible items, and no choice item when Foul Play rules it out;
  - impossible abilities;
  - revealed moves (excluded);
  - the speed range on spreads.
- **revealed_prior:** `scratch_ds/metamon/priors/alt_usage_prior.json`, slice `2026H1|ge1500` (fallback `all|ge1500`, `all|all`).
  - **Item / ability / tera** follow the distribution of **revealed** values.
  - **Moves** use the per-game reveal rate. That ranks moves correctly but under-states P(move in set).
  - **Spread** comes from Smogon, since spreads are never revealed.
  - The constraints are the same as for **usage**.
- **fp_belief:** Foul Play's **own** belief. Decision log v3 records 64 draws of `sample_pokemon` per visible opponent Pokémon, on the live `battle.mode`, **after** its in-place reverse-damage pruning.
  - This is the faithful champion baseline. It was read from the raw chunk tarballs (the extractor doesn't carry it).
  - The snapshot costs 332 ms per decision in play.
- **played:** the decision's 4–8 logged worlds (the same sampler, few draws).
- **fp:** Foul Play's sampler rebuilt offline from the view. It has no damage pruning, so from turn 6 on it is worse than `played` on spreads (v0 report).

## Learned belief

**v0 (Oct 26):** an MLP (2 × 512) over the public view of one opponent Pokémon.
- **Inputs:**
  - species;
  - revealed moves;
  - view item, ability and tera;
  - Foul Play's flags (choice possible, impossible items and abilities);
  - speed-range bounds;
  - HP, status, turn.
- **Team-context inputs** are zeroed by default: they let the net memorise rosters.
- **Heads:** item, ability, tera, moves, nature, EV class, spread.
- **Training:** a residual on the Smogon usage prior (the untrained net *is* the prior), the same hard constraints as the baselines, and early stopping on the farm dev split.

**v1 (Oct 27)** (same net; `model.py`/`train.py`):
- **Farm data:** C1t1 label farm (2,774 games) plus F1 volume farm (1,796 games), at most 4 decisions per (game, side, opponent Pokémon), train split only. That is 146,521 rows from 1,946 rosters.
- **Human data:** metamon 2026H1, rating ≥ 1500, 57,310 games, both observers, with partial labels.
  - Rows whose opponent roster is an hr_val or reserved roster are dropped (`targets.excluded_rosters()`): **4.44M of 15.39M rows (29%)**. The ladder replays contain the sealed rosters, so this filter is required.
  - At most 3 decisions per (game, side, Pokémon), train split only. That is 870,906 rows from 42,109 games and 11,819 rosters; 1,816 of these rosters also occur in the farm.
- **Masked losses:**
  - item/ability/tera cross-entropy only on rows where the label is known;
  - spread heads only on farm rows;
  - the 4-move BCE only where all 4 moves are known;
  - otherwise a positive-only listwise term on the revealed hidden moves (weight 0.25).
- **Reveal-propensity correction (`--reveal`, on with `--human`).** Human labels are values that *got revealed*, so P(label = v) ∝ P(v | view)·r(v).
  - A learned per-class vector log r(v) (item, ability, tera, moves) is added to the logits of human rows only. Farm rows, which have true labels, anchor P(v | view).
  - True-set predictions omit the vector. The "revealed head" adds it.
  - What it learned for items: Choice Band −1.6, Specs −1.3, Scarf −1.2, Loaded Dice −1.3, Heavy-Duty Boots −1.1, Assault Vest −0.8, against Leftovers +0.5, Life Orb +0.6, Sitrus +0.7, Air Balloon +0.7, Booster Energy +0.6.
  - For abilities: Sand Stream +2.1, Intimidate +1.2, Electric and Psychic Surge +3.7 to +4.3, against Regenerator −3.4 and Unaware −3.4.
- **`--init` fine-tuning:** pre-train on farm + human, then fine-tune on farm rows only (lr 3e-4).
- **`--worlds 1`:** also reads the decision's logged worlds (only farm rows have them).
- **Cost:** every run takes 1–3 min on the Mac (CPU, 5–6 threads; 1.0M rows), so no Azure job was needed.

## Results (Oct 26, 2026, about 23:50 UTC = Oct 27 00:50 Paris; `scratch_ds/belief/results_v1/`)

### 1. hr_val farm (pre-registered S-A test)

The main table shows 95% CIs over games, with each method's paired difference to `fp_belief`. Full tables, strata and per-species values are in `results_v1/sa_hrval.md`.

| Method | **item NLL (ε 1e-4)** | Δ vs fp_belief | item top-1 | item p(true) | ability (>1) top-1 | tera top-1 | moves R@k | EV class top-1 | spread top-1 |
|---|---|---|---|---|---|---|---|---|---|
| **fp_belief** (Foul Play, 64 draws, pruned) | 1.800 [1.720, 1.885] | (ref) | 0.562 | 0.443 | 0.885 | 0.375 | 0.576 | 0.791 | 0.675 |
| usage prior | 1.301 [1.261, 1.340] | −0.500 [−0.563, −0.439] | 0.576 | 0.404 | 0.886 | 0.409 | 0.601 | 0.745 | 0.654 |
| revealed-set prior | 1.457 [1.399, 1.512] | −0.344 [−0.415, −0.275] | 0.515 | 0.438 | 0.758 | 0.443 | 0.599 | 0.745 | 0.654 |
| as played (4–8 worlds) | 2.603 [2.506, 2.701] | +0.803 [+0.766, +0.840] | 0.519 | 0.443 | 0.878 | 0.329 | 0.553 | 0.751 | 0.621 |
| v0 (1,166 games) | 1.214 [1.172, 1.254] | −0.586 [−0.651, −0.524] | 0.596 | 0.458 | 0.888 | 0.437 | 0.595 | 0.755 | 0.656 |
| v1 farm-only | 1.197 [1.152, 1.238] | −0.604 [−0.665, −0.544] | 0.604 | 0.469 | 0.893 | 0.440 | 0.601 | 0.760 | 0.659 |
| v1 farm+human (joint) | 1.208 [1.167, 1.247] | −0.592 [−0.656, −0.531] | 0.593 | 0.461 | 0.890 | 0.446 | 0.601 | 0.758 | 0.654 |
| v1 farm+human, no reveal correction | 1.279 [1.235, 1.319] | −0.521 [−0.590, −0.458] | 0.568 | 0.460 | 0.883 | 0.448 | 0.600 | 0.755 | 0.654 |
| **v1 human pre-train → farm fine-tune** | **1.182** [1.141, 1.223] | **−0.618** [−0.682, −0.557] | 0.601 | 0.471 | 0.893 | 0.445 | **0.605** | 0.759 | 0.659 |
| v0 + worlds | 1.207 [1.165, 1.249] | −0.593 [−0.655, −0.532] | 0.598 | 0.466 | 0.891 | 0.431 | 0.595 | 0.800 | 0.695 |
| v1 farm-only + worlds | 1.190 [1.147, 1.231] | −0.610 [−0.672, −0.550] | 0.598 | 0.471 | 0.893 | 0.444 | 0.600 | 0.806 | 0.706 |
| v1 farm+human + worlds | 1.199 [1.157, 1.239] | −0.602 [−0.663, −0.542] | 0.596 | 0.471 | 0.892 | 0.444 | 0.603 | 0.805 | 0.702 |
| v1 pre-train → fine-tune + worlds | 1.175 [1.134, 1.215] | −0.626 [−0.689, −0.566] | 0.602 | 0.475 | 0.896 | 0.443 | 0.604 | 0.808 | 0.708 |
| **same model, fp_belief as the worlds input at test time** | **1.172** [1.131, 1.212] | **−0.629** [−0.691, −0.569] | **0.605** | **0.475** | **0.897** | 0.442 | **0.605** | **0.814** | **0.716** |

**Pre-registered comparison, learned reader vs Foul Play's sampler.** Item NLL is **−0.618 nats [−0.682, −0.557]** for the view-only v1, and −0.626 [−0.689, −0.566] with the worlds.

The last row needs no retraining (`predict.py --worlds-from-fpbelief`). It is trained on 4–8 logged worlds; at test time it reads Foul Play's 64 pruned draws instead. It is the best belief on every attribute except tera.

- **Not only calibration.** Paired with `fp_belief`, the learned reader is also ahead on:
  - item top-1: +0.039 [+0.026, +0.052];
  - item p(true): +0.028 [+0.021, +0.035];
  - tera top-1: +0.070;
  - moves: +0.029.
- **Zero mass.** `fp_belief` puts zero mass on the true item in 9% of rows, and on the true tera in 16%.
- **Smoothing doesn't close the gap.** Mixing `fp_belief` with 20% usage prior (`sa_hrval_smoothfp.md`) only brings it to 1.370.
- **Where Foul Play still wins.** Its pruned belief is better on EV class (0.791 vs 0.759) and spread (0.675 vs 0.659), because the damage information isn't in the view. Adding the logged worlds recovers and passes it (0.808 / 0.708).
- **By turn:** the learned reader's item p(true) goes from 0.434 at preview to 0.520 at turn 21+; `fp_belief` goes from 0.378 to 0.512. Its NLL is 1.25 → 1.08, against 1.80 → 1.84.

**Ablation: farm-only vs farm+human, 3 seeds each** (item NLL, paired Δ to farm-only seed 0; `results_v1/sa_hrval_seeds.md`):

| | seed 0 | seed 1 | seed 2 |
|---|---|---|---|
| farm-only | 1.197 (ref) | +0.004 [−0.002, +0.011] | −0.004 [−0.014, +0.004] |
| farm+human, joint | +0.012 [−0.000, +0.023] | +0.001 [−0.012, +0.013] | +0.013 [+0.001, +0.025] |
| human pre-train → farm fine-tune | **−0.014 [−0.022, −0.007]** | **−0.016 [−0.024, −0.008]** | **−0.012 [−0.021, −0.003]** |

**Human data amount (joint training):** with 10% / 30% / 100% of the human games, the change is +0.001 / −0.001 / +0.012 on hr_val, so there is no gain.

Dev loss on the farm dev split shows the same ordering. Farm-only scores 6.32 / 6.32 / 6.33. Joint farm+human scores 6.37 / 6.37 / 6.39, and 6.47 without the reveal correction. Pre-train then fine-tune scores 6.26 / 6.27 / 6.27.

### 2. Held-out human games (revealed labels only)

`results_v1/sa_human.md`: 88,087 rows from 15,674 games. Paired Δ is to the usage prior.

| Method | item NLL | Δ vs usage | item top-1 | item p(true) | ability (>1) top-1 | tera top-1 | moves R@4 |
|---|---|---|---|---|---|---|---|
| usage prior | 1.390 [1.370, 1.409] | (ref) | 0.560 | 0.396 | 0.894 | 0.479 | 0.759 |
| **revealed-set prior** | **1.006** [0.987, 1.024] | −0.384 [−0.396, −0.371] | **0.672** | **0.548** | 0.922 | **0.557** | **0.789** |
| v0 | 1.220 [1.200, 1.239] | −0.170 | 0.633 | 0.470 | 0.902 | 0.512 | 0.756 |
| v1 farm-only | 1.208 [1.187, 1.227] | −0.182 | 0.637 | 0.478 | 0.932 | 0.524 | 0.764 |
| v1 farm+human (true-set head) | 1.164 [1.145, 1.182] | −0.227 | 0.640 | 0.484 | 0.934 | 0.543 | 0.770 |
| v1 farm+human, **revealed head** (+ log r) | 1.080 [1.060, 1.099] | −0.310 | 0.664 | 0.536 | **0.953** | 0.538 | 0.784 |
| v1 farm+human, no reveal correction | 1.078 [1.058, 1.096] | −0.312 | 0.662 | 0.527 | 0.949 | 0.541 | 0.778 |
| v1 pre-train → fine-tune | 1.168 [1.148, 1.186] | −0.222 | 0.647 | 0.484 | 0.934 | 0.539 | 0.770 |

- **Human labels help on human games.** Item NLL against farm-only is −0.044 [−0.050, −0.039], with seeds at −0.036 and −0.041. It improves with the amount of human data: −0.021 at 10%, −0.031 at 30%, −0.044 at 100%.
- **The reveal correction behaves as designed.** With it, the true-set head is better on hr_val's true sets: 1.208 vs 1.279 without. Adding log r back reproduces the revealed-label fit (1.080 vs 1.078).
- **Reveal-bias caveat.** These labels are the values that got revealed, a selection that depends on the value: Leftovers, Life Orb, berries and announcing abilities show; Choice items, Boots, Assault Vest and passive abilities rarely do. So this test measures P(revealed value | view). The revealed-set prior is fit to exactly that selection, on 2.02M games, so it wins here by construction. It is a *worse* true-set predictor on hr_val (item NLL 1.457 vs 1.301 for the Smogon prior; ability top-1 0.758). Claims about true sets come only from the farm test.

### 3. Reading

1. **On the pre-registered test, every learned reader beats Foul Play's own belief by about 0.6 nats of item NLL,** with CIs far from 0. It is also ahead on item and tera top-1, item p(true) and moves. Foul Play keeps an edge on EV class and spread from damage pruning, and the logged-worlds hybrid takes that edge back.
2. **Human partial labels: about +0.01 nats worse on hr_val when mixed in jointly, and about 0.014 nats better (in all 3 seeds) as pre-training before farm fine-tuning.** Mixed in jointly, they don't help (or slightly hurt), even with the reveal correction. On human games they help clearly and in proportion to the amount.
   - The likely reason is a **domain gap between the two "truths"**: hr_val sets are metamon's *predicted* hl_05_26 teams (hidden details filled in from statistics), while human labels are real revealed sets from 2026 play.
   - Distinct human teams therefore don't buy what the farm learning curve suggested, at least not on this test. A truly-held-out set with real hidden sets would settle it.
3. **The reveal-propensity correction is necessary** for using human labels at all: without it, hr_val item NLL rises by +0.07.

## Belief lever (Foul Play's world sampling guided by belief v1)

Code: `foul-play/fp/hardread_belief.py` plus a 13-line hook in `fp/search/standard_battles.py: prepare_battles`. Tests: `foul-play/tests/test_hardread_belief.py`.

**Off by default.** Without `HARDREAD_LEVER_BELIEF`, the hook only calls `hardread_belief.enabled()` (False), so Foul Play is upstream:
- numpy isn't imported;
- the random stream is the same (unit test `test_off_matches_upstream_random_stream`);
- 745 tests pass: 737 upstream + 8 new.

**Lever A, `HARDREAD_LEVER_BELIEF=resample` (importance resampling).**
- **Pool.** For each opponent Pokémon that Foul Play samples (the active one and the alive reserves), draw a pool of up to M = 16 sets with Foul Play's own `sample_pokemon`, on deep copies, with its damage-pruned datasets and legality checks.
  - The first draws are the one-per-world that vanilla makes anyway.
  - Extra draws go round-robin within a time budget (`HARDREAD_BELIEF_BUDGET_MS`, default 60).
  - While one Pokémon's pool is drawn, its dataset filters are computed once (`_same_pokemon_cache`; the draws are identical, see the unit test).
- **Score.** Each set gets p_belief(set) = p(item) p(ability) p(tera) p(spread class) Π_{hidden moves} p/(1−p), using only the attributes still hidden.
- **Resample.** Each world draws its set from the pool with weight (p_belief / q̂)^λ, where q̂ is the smoothed frequency of that exact set in the pool. This keeps every set Foul Play-legal and moves the mass toward the belief.
- **Failures.** Any failure falls back to Foul Play's own sampling, for the decision or for that Pokémon, and is counted and logged.

**Lever B, `HARDREAD_LEVER_BELIEF=direct`.**
- Item, ability, tera and spread class are drawn from the belief's marginals. Moves are drawn ∝ P(move) among the moves in the pool.
- Each set is checked with Foul Play's `PokemonSet.set_makes_sense` and `PredictedPokemonSet.set_makes_logical_sense`. A pool set is used when 8 tries fail.

**Foul Play's own draws as model input:** `HARDREAD_BELIEF_FP_INPUT=1` with the worlds checkpoint. The pool histogram is fed as the worlds input, like the S-A variant "fp_belief as the worlds input".

**Environment variables:**

| Variable | Meaning |
|---|---|
| `HARDREAD_BELIEF_CKPT` | the `.npz` |
| `HARDREAD_BELIEF_WORLDS_CKPT` | used instead when `FP_INPUT=1` |
| `HARDREAD_BELIEF_M` | pool size (16) |
| `HARDREAD_BELIEF_LAMBDA` | tempering λ (1.0) |
| `HARDREAD_BELIEF_BUDGET_MS` | time budget for extra draws (60) |
| `HARDREAD_BELIEF_LOG` | JSONL per decision: timings, pool sizes, effective sample sizes, counters |

**Model export.** `tools/belief/export_npz.py` turns a `train.py` checkpoint into numpy weights, vocabularies, the legal-ability matrix and the config. Lever inference is a numpy port of `model.py`.
- **Parity with torch:** at most 1.6e-5 difference in log-probabilities on 2,000 hr_val rows, for both exports.
- **Checkpoints exported** (trained on hr_train farm plus filtered human rows; no hr_val or sealed roster):
  - `scratch_ds/belief/export/belief_v1.npz`: human pre-train → farm fine-tune, view-only. SHA-256 `b66252ba…`.
  - `scratch_ds/belief/export/belief_v1_worlds.npz`: the same recipe with worlds. SHA-256 `1080a8af…`.
- **numpy.** Locally, the Foul Play venv borrows numpy from the user site-packages for Python 3.13. Azure nodes install it.
- **Shipping to Azure:**
  - `azure/submit.sh` copies `BELIEF_NPZ` / `BELIEF_WORLDS_NPZ` into the job (`code/belief/belief.npz`, `belief_worlds.npz`);
  - `azure/setup_node.sh` exports `HARDREAD_BELIEF_CKPT` / `HARDREAD_BELIEF_WORLDS_CKPT`;
  - `run_match.py --treat` then only needs the lever variables.

**Local smoke** (F1, `gen9/hr_hl_dev` pool, lever on the alternating seat): 30 games over 8 runs, **0 failures, 0 decision- or Pokémon-level fallbacks.**

| Run | Lever | Games | Failures | Decisions logged |
|---|---|---|---|---|
| 1–5 | resample (successive speed fixes) | 20 | 0 | 779 |
| direct | direct | 4 + 2 | 0 | 112 + 125 |
| fp-input | resample + FP input | 4 | 0 | 129 |

In direct mode, 4–8% of the belief-drawn sets fail Foul Play's checks 8 times and use a pool set instead.

**Cost per decision** (final code, run 5, 203 decisions, Mac under other load). "Added" = extra draws + inference + weights. The draws vanilla makes anyway are excluded.

| | median | mean | p90 | p99 | max |
|---|---|---|---|---|---|
| added (ms) | 21.6 | 23.5 | 37.3 | 52.4 | 83.3 |
| inference (ms) | 2.7 | 2.8 | 3.5 | 4.5 | 4.9 |

That is about 1.1% of a 2 s search at the median and 2.6% at p99. 99% of Pokémon get the full pool of 16, and the median effective sample size is 4.3.

Earlier runs, before the budget rule and the cache, had a p99 of 230–2,300 ms. Foul Play's Smogon moveset loop sometimes spins for 50–300 ms on a single draw (Iron Treads, Kyurem with one move revealed). The budget rule only draws a Pokémon again while twice its slowest draw so far fits in the remaining budget.

Measured with 2 other jobs running on the Mac:
- direct mode adds 73 ms at the median (it builds sets in Python);
- FP input adds 40 ms at the median.

**Offline effect** (`tools/belief/lever_offline.py`; hr_val examples, 40 game-sides, 4,662 hidden-item rows; pools from the offline sampler, so without damage pruning). Values are the mean probability of the true value in a world:

| | item | tera | spread class | hidden moves | ESS |
|---|---|---|---|---|---|
| Foul Play pool (vanilla-like) | 0.418 | 0.250 | 0.464 | 0.502 | 16 |
| lever λ = 1 | **0.534** | **0.339** | **0.622** | **0.578** | 5.0 |
| lever λ = 0.5 | 0.514 | 0.316 | 0.595 | 0.562 | 7.6 |
| lever λ = 1, FP input | 0.535 | 0.342 | 0.614 | 0.575 | 5.2 |
| belief marginal alone | 0.491 | 0.318 | 0.570 | 0.543 | — |

The resampled worlds put more mass on the truth than the belief's own marginals. The product over attributes favours sets that are consistent on all of them, within Foul Play's support. The price is diversity: the effective sample size is about 5 of 16, and λ = 0.5 keeps more of it.

**Screens** (`tools/run_match.py --treat ... --treat-seat alternate`; submit with `BELIEF_NPZ=<repo>/scratch_ds/belief/export/belief_v1.npz BELIEF_WORLDS_NPZ=<repo>/scratch_ds/belief/export/belief_v1_worlds.npz bash azure/submit.sh ...`):

| Arm | `--treat` |
|---|---|
| A | `HARDREAD_LEVER_BELIEF=resample` |
| A, λ = 0.5 | `HARDREAD_LEVER_BELIEF=resample,HARDREAD_BELIEF_LAMBDA=0.5` |
| A with Foul Play's draws as input | `HARDREAD_LEVER_BELIEF=resample,HARDREAD_BELIEF_FP_INPUT=1` |
| B | `HARDREAD_LEVER_BELIEF=direct` |

## How to run

```sh
bash tools/belief/run_v1.sh          # v1: fetch, examples, human prep, baselines, all runs, both benchmarks
bash tools/belief/run_all.sh         # v0 (Oct 26)
# single steps (python3 = /usr/bin/python3 with torch)
python3 tools/belief/baselines.py --examples val.examples.jsonl.gz --method fpbelief --raw scratch_ds/chunks/val/*.tar.gz --out F.jsonl.gz
python3 tools/belief/train.py --examples lab.examples.jsonl.gz --extra-train vol.examples.jsonl.gz --human human.train.jsonl.gz \
    --out-dir D --fractions 1.0 --tag _farmhuman --predict hrval=val.examples.jsonl.gz --predict-revealed human=human.test1.jsonl.gz
python3 tools/belief/train.py ... --init D/model_f1.0_farmhuman.pt --lr 3e-4 --tag _ft     # (farm-only fine-tune)
python3 tools/belief/benchmark.py --examples val.examples.jsonl.gz --eps 1e-4 --bootstrap 2000 --ref fp_belief --pred fp_belief=F.jsonl.gz --pred v1=...
```

Data lives in `scratch_ds/` (gitignored):
- `belief/v1data/`: farm examples and hr_val baselines;
- `belief/human/`: human splits;
- `belief/v1/`: runs and predictions;
- `belief/results_v1/`: benchmark tables.

Prediction files must follow the example order (every tool here writes them that way). The benchmark streams them in lockstep.

## Data notes and caveats

- **hr_val farm:** 440 games, 33,253 decisions, decision log v3 everywhere (no row without `fp_belief`). View vs truth:
  - 151 ability mismatches (Gardevoir's Trace: the view shows the traced ability);
  - 28 wrong view items: Illusion, where a Zoroark's Life Orb is attributed to Indeedee; an Air Balloon on Ogerpon-Wellspring; a speed-inferred Scarf on a Boots Talonflame;
  - 15 revealed moves not in the true set (the same Illusion case);
  - 244 Ogerpon-Tera forme abilities (expected).
- **Hard constraints:** Foul Play's own constraints exclude the true hidden item in about 0.4% of rows (v0 report), which costs those rows the ε floor.
- **Sealed rosters appear in the human data.** 29% of human rows had an hr_val or reserved opponent roster. They were removed before any split. Anything else trained on these replays (student, Glimmer SFT) should apply the same filter.
- **One decision per Pokémon counts once,** so rows are correlated within a game. The CIs resample games, and the training seeds vary by about 0.005–0.01 nats.

## Next steps

1. Log `fp_belief` (v3) in the label farm too, and train the hybrid on `fp_belief` histograms (64 pruned draws) instead of 4–8 worlds. This is the cleanest "learned prior + Foul Play's damage pruning" combination. Feeding them at test time to a model trained on worlds already helps (row "fp_belief as the worlds input": EV class 0.814, spread 0.716).
2. Add damage evidence from the public log (a sequence encoder over `history_end`) to close the EV-class gap without Foul Play's calculator.
3. Human data:
   - per-species or exposure-aware reveal propensities (the `yturn`/`final` fields), or a "revealed within k turns" target;
   - a real-set check (own-side full teams of human players are not available; ladder team dumps could serve);
   - weight human rows down instead of fine-tuning.
4. Pilot in play: sample worlds from v1 (pre-train → fine-tune + worlds) instead of `sample_pokemon`, then run A/A at C1t1 on hr_val rosters (development set B).

## v0 history (Oct 26; `results/`, test = hr_train held out by roster)

- **Data:** 725 C1t1 games plus 598 F1 games; 60,154 test rows.
- **Mean p(true), item / EV class / spread:**

  | Method | item | EV class | spread |
  |---|---|---|---|
  | usage | 0.421 | 0.578 | 0.438 |
  | offline `fp` | 0.425 | 0.614 | 0.464 |
  | played | 0.471 | 0.720 | 0.560 |
  | v0 | 0.468 | 0.678 | 0.557 |
  | v0 + worlds | 0.484 | 0.733 | 0.600 |

- **Learning curve:** dev loss went 7.49 → 7.13 → 6.70 for 57 → 200 → 647 C1t1 games, and 6.58 with F1 games added.
- **Reconstruction check:** offline `fp` matches `played` at turns 0–5 and diverges later because of the damage pruning that v3 now logs.
