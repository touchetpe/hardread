# S-A: hidden-set inference benchmark and learned belief v0

L2 "Reading the Opponent": how well can the opponent's hidden set (item, ability, tera type,
moves, nature/EV spread) be inferred from public battle evidence, and how does a learned belief
compare with the champion bot's own (Foul Play's set sampler)?

## Files

| File | What |
|---|---|
| `targets.py` | extractor output → one example per (decision, alive opponent Pokémon in the view): public view + context, true attributes (labels), which ones are hidden, the decision's sampled worlds for that Pokémon, data-quality flags, roster split |
| `baselines.py` | three beliefs per example: `usage` (Smogon prior + view constraints), `fp` (Foul Play's sampler rebuilt offline, N = 64), `played` (the logged worlds) |
| `benchmark.py` | S-A metrics → JSON + markdown |
| `model.py`, `train.py` | learned belief v0 (PyTorch MLP, residual on the usage prior), learning curve |
| `run_all.sh` | fetch → extract → examples → baselines → train → benchmark → learning curve |

## Benchmark spec (S-A v0)

**Rows.** One row per (bot decision, alive opponent Pokémon in the bot's view); in Gen 9 OU team preview shows all six, so every alive opponent Pokémon is in the view from turn 0. Fainted Pokémon are dropped (Foul Play doesn't sample them, and they don't matter for play). Every decision counts once per Pokémon, so long games and Pokémon that stay unrevealed weigh more.

**Labels.** The true set from the Showdown battle log (`opp_team`). All names are Showdown ids (lowercase alphanumerics). The view species is matched to the true set by National Dex number; the species clause makes it unique, so `ogerponwellspringtera` ↔ Ogerpon-Wellspring and `landorustherian` ↔ Landorus-Therian work. Battle-only formes map to their base forme (`skey`) for the priors and the model. True sets are labels only, never model inputs.

**Scored only when hidden in the view:**
- **item:** only when Foul Play's item is `unknownitem` and `removed_item` is null (encoding below).
- **ability:** only when the view's ability is null. `ability (>1 legal)` restricts to species with more than one legal ability; the others are trivial.
- **tera:** only when the Pokémon hasn't terastallized.
- **moves:** the true moves not yet revealed.
- **spread:** always hidden. Scored as nature (neutral natures merged into `serious`), EV class (the two stats with the most EVs, e.g. `atk+spe`), and spread = `nature|EV class`.

**Metrics:**
- top-1 accuracy;
- **mean p(true)**: the probability the belief puts on the truth, i.e. the expected accuracy of one draw. It is unbiased for sampled beliefs at any sample count, so it is the fair way to compare the 4–8 logged worlds with N = 64 or exact distributions;
- NLL = −ln max(p_true, ε) with **ε = 10⁻³** (a miss costs at most 6.9 nats);
- multi-class Brier;
- zero-mass rate (p_true = 0).

**Moves** are ranked by P(move in set), with revealed moves excluded. With k = the number of hidden true moves, the metrics are recall@k, `set` (top-k equals the hidden moves exactly), recall@4, and mean P(move) over the hidden moves.

**Strata and averages.** Results are stratified by turn bucket (0 = preview, 1–5, 6–10, 11–20, 21+) and by the number of revealed moves. The per-species macro average covers species with ≥30 scored rows.

**Held out by roster.** `split` = SHA-1 of the opponent roster (sorted Showdown species joined by `|`, the `data/pools-v1.json` format), giving 20% test, 10% dev (early stopping) and 70% train. Every decision log so far comes from the **hr_train** pool, so tonight's test set is held out *by roster within hr_train*, not hr_val. Re-point to hr_val decision logs once they exist (`targets.py` already counts any hr_val-roster rows). Reserved/sealed rosters are never read.

## Foul Play's item encoding (the open TODO in RESULTS.md)

Checked in `fp/battle/state.py`, `fp/battle/protocol.py` (`set_item`, `remove_item`), `fp/battle/inference.py` and `fp/search/poke_engine_helpers.py`:

| View `item` | `removed_item` | Meaning | `item_state` |
|---|---|---|---|
| `"unknownitem"` | null | never revealed (initial value; Illusion ending resets it) | `unknown`: the **only** scored case |
| item id | null | revealed by a protocol message **or inferred**: `choicescarf` from speed order, `heavydutyboots` from hazards. `item_inferred` isn't logged | `known` |
| null | item id | original item revealed, then knocked off, consumed or stolen (`-enditem`) | `removed` |
| item id 2 | item id | original swapped away (Trick: when the bot is tricked, the opponent's `removed_item` is set), and it now holds id 2 | `removed` |
| null | null | holds nothing, original unknown (Z-move path only) | `none_unexplained`: never seen |

In the **logged worlds** (`worlds[].opp_sets`), "no item" is the **string `"None"`**: `battle_to_poke_engine_state` writes `pkmn.item = "None"` on the battle copy (also when `knocked_off`) before the sets are logged. An unsampled fainted Pokémon keeps `"unknownitem"`. The sampler (`populate_pkmn_from_set`) only replaces `unknownitem`, so null items stay null. In `PokemonSet.item_check`, `removed_item` counts as the original item.

## Baselines

- **usage:** the Smogon chaos stats Foul Play uses (`fp/data/smogon_stats_cache/gen9ou-0.json`, byte-identical to the Azure farm's copy), full lists rather than Foul Play's top-10/20/6 truncation. Renormalised after:
  - impossible items, plus choice items when `can_have_choice_item` is false;
  - impossible abilities;
  - revealed moves (excluded);
  - the speed range: a spread is kept if its speed, or ×1.5 when a Choice Scarf is still possible, is in `speed_range`. This is Foul Play's `speed_check`.
- **fp:** an `fp` `Pokemon` is rebuilt from the v2 view fields, and `fp.search.standard_battles.sample_pokemon` runs 64 times on deep copies. It uses a `StandardBattleMode` whose `TeamDatasets` and `SmogonSets` are initialised as in a real battle: `initialize_team_preview_datasets("gen9ou", <the game's 12 team-preview species>)`, from `fp/modes/standard_battle.py`. Identical views within a game share one draw of 64.
- **played:** the decision's logged worlds, chance-weighted. That is 4 worlds at C1t1, or 8 while the opponent's active has revealed fewer than 3 moves. **This is Foul Play's real in-game belief.**

**Reconstruction fidelity, and a hidden state.** At team preview and turns 1–5, `fp` and `played` agree on mean p(true) for every attribute, within noise. Later they diverge on spreads and items: `played` is better (EV class 0.59 → 0.85 at turn 21+). The cause is that Foul Play **prunes its dataset lists in place by reverse damage calculation** (`fp/battle/inference.py: update_dataset_possibilities → _do_check → possibilites.pop(i)` on `mode.team_datasets`/`mode.smogon_sets`). That state isn't in the v2 log, so `fp` is "Foul Play's sampler on the public view, without damage pruning". To make the real belief reconstructible offline, log v3 should record the surviving dataset entries per opponent species (indices or a hash).

## Learned belief v0

`model.py` is an MLP (2 × 512, GELU, dropout 0.5) over:
- species, with battle-only formes mapped plus a forme flag;
- opponent teammates seen, own active species, own team: implemented but **zeroed by default** (`--drop-inputs`). At 811 games they let the net memorise training rosters: dropping them lowered dev loss from 6.93 to 6.85, and test mean p(true) rose slightly on every attribute. Re-test with more data;
- revealed moves;
- view item / removed item / ability / tera;
- the terastallized flag;
- Foul Play's flags (choice possible, impossible items, impossible abilities);
- speed-range bounds, absolute and relative to the species' range;
- HP fraction, status, active flag, turn, preview flag, fainted counts.

**Heads.** item, ability, tera, moves (multi-label), nature, EV class, spread.

**Residual on the usage prior.** Each head adds `scale × log prior[species]` (Smogon) to a zero-initialised head, so the untrained net *is* the usage prior and training learns corrections. Training from scratch overfits within one epoch on a few hundred games.

**Training setup:**
- The same hard constraints as the baselines are applied (masking).
- Losses use only hidden attributes.
- Each row is weighted by 1/(rows of the same Pokémon in the same game and side).
- AdamW (lr 1e-3, wd 0.1), early stopping on dev loss.
- `--extra-train` adds the F1 volume farm's **train-split** rows; test and dev rosters are excluded by construction.
- `--worlds 1` (hybrid "v0+worlds") also feeds the decision's logged worlds as additive log-probabilities.

## How to run

```sh
# everything, incremental downloads (read-only blob access via azure/fetch.sh)
bash tools/belief/run_all.sh
# or step by step (python3 = /usr/bin/python3 with torch; FP env for --method fp)
python3 tools/dataset/extract.py data/samples/sample-F1-12.tar.gz --out scratch_ds/sample.jsonl.gz
python3 tools/belief/targets.py scratch_ds/sample.jsonl.gz --out scratch_ds/belief/sample.examples.jsonl.gz
python3 tools/belief/baselines.py --examples scratch_ds/belief/sample.examples.jsonl.gz --method usage --out U.jsonl.gz
../environments/foulplay/bin/python tools/belief/baselines.py --examples ... --method fp --workers 12 --out F.jsonl.gz
python3 tools/belief/train.py --examples ... [--extra-train vol.examples.jsonl.gz] --out-dir scratch_ds/belief/v0
python3 tools/belief/benchmark.py --examples ... --pred usage=U.jsonl.gz --pred fp=F.jsonl.gz ... --out-md out.md
```

Outputs go to `scratch_ds/belief/` (gitignored), and results to `scratch_ds/belief/results/`.

## Current results

Run of Oct 26, 2026, about 18:45 UTC (`scratch_ds/belief/results/`). The setup:

- **Data:** 29 C1t1 chunks (fetched 18:10 UTC): 725 games, 61,150 decisions, 274,891 rows. That is 187k train / 27k dev / 60k test rows over 611 / 92 / 162 rosters.
- **Test set:** 162 held-out rosters (within hr_train), 272 games, 13,074 decisions, 60,154 rows.
- **Extra training data:** for the `+vol` models, the train split of 4 F1 volume chunks (598 games, 137k rows).

36 C1t1 chunks were on disk at 18:43. `bash tools/belief/run_all.sh` refreshes everything; it takes about 30 min on an idle Mac.

**Mean p(true)** (the belief's probability on the truth; moves: mean P(move in set) over the hidden moves):

| Method | item | ability (>1 legal) | tera | moves | nature | EV class | spread |
|---|---|---|---|---|---|---|---|
| usage prior | 0.421 | 0.827 | 0.293 | 0.525 | 0.581 | 0.578 | 0.438 |
| fp (offline sampler, N=64, no damage pruning) | 0.425 | 0.843 | 0.291 | 0.502 | 0.614 | 0.614 | 0.464 |
| played (Foul Play's in-game worlds) | 0.471 | 0.852 | 0.292 | 0.507 | 0.676 | **0.720** | 0.560 |
| v0, C1t1 only (647 games) | 0.464 | 0.854 | **0.324** | 0.545 | 0.674 | 0.673 | 0.554 |
| v0, C1t1 + F1 volume (1,166 games) | 0.468 | **0.861** | 0.321 | **0.548** | 0.675 | 0.678 | 0.557 |
| v0 + worlds (hybrid), C1t1 + F1 volume | **0.484** | **0.861** | 0.321 | 0.536 | **0.707** | **0.733** | **0.600** |

**Top-1 accuracy** (moves: recall@k of the hidden moves) and **NLL** (ε = 10⁻³):

| Method | item | ability (>1) | tera | moves R@k | moves set | nature | EV class | spread | NLL item / tera / spread |
|---|---|---|---|---|---|---|---|---|---|
| usage prior | 0.566 | 0.873 | 0.449 | 0.604 | 0.195 | 0.779 | 0.758 | 0.668 | 1.25 / 1.63 / 1.07 |
| fp | 0.527 | 0.859 | 0.409 | 0.581 | 0.159 | 0.734 | 0.717 | 0.615 | 1.56 / 2.25 / 1.37 |
| played | 0.536 | 0.869 | 0.354 | 0.557 | 0.143 | 0.753 | 0.779 | 0.645 | 2.03 / 3.08 / 1.44 |
| v0, C1t1 only | 0.578 | 0.880 | 0.466 | 0.606 | 0.191 | 0.775 | 0.768 | 0.673 | 1.19 / 1.57 / 0.88 |
| v0, C1t1 + vol | 0.577 | 0.879 | 0.460 | 0.606 | 0.187 | 0.764 | 0.778 | 0.680 | 1.17 / 1.56 / 0.86 |
| v0 + worlds | **0.603** | 0.881 | 0.455 | **0.608** | 0.189 | **0.802** | **0.820** | **0.721** | **1.15** / 1.57 / **0.78** |

**Scored rows:**
- item 39,549 (66% of rows);
- ability 47,609, of which 27,035 for species with >1 legal ability;
- tera 56,839;
- moves 56,605;
- spread 60,154.

Strata (turn bucket, revealed moves) and per-species macro averages are in `results/sa_c1t1.md`.

**Learning curve** (dev loss of the summed heads; test mean p(true)). Fractions are of training *games*, nested:

| Training data | games | rows | best dev loss | item | tera | moves | EV class | spread |
|---|---|---|---|---|---|---|---|---|
| usage prior (untrained net) | 0 | 0 | 7.59–7.63 | 0.421 | 0.293 | 0.525 | 0.578 | 0.438 |
| C1t1 10% | 57 | 17k | 7.49 | 0.418 | 0.289 | 0.525 | 0.580 | 0.462 |
| C1t1 30% | 200 | 61k | 7.13 | 0.457 | 0.312 | 0.529 | 0.644 | 0.516 |
| C1t1 100% | 647 | 187k | 6.70 | 0.464 | 0.324 | 0.545 | 0.673 | 0.554 |
| C1t1 + vol, 10% | 113 | 33k | 7.23 | 0.439 | 0.292 | 0.521 | 0.623 | 0.506 |
| C1t1 + vol, 30% | 355 | 104k | 6.99 | 0.447 | 0.309 | 0.528 | 0.652 | 0.533 |
| C1t1 + vol, 100% | 1,166 | 325k | 6.58 | 0.468 | 0.321 | 0.548 | 0.678 | 0.557 |

**Reading the learning curve:**
- Dev loss falls roughly linearly in log(games) and hasn't flattened at about 1.2k games.
- All runs stop early (0.5–1 epoch), because consecutive decisions repeat the same Pokémon. **The number of distinct teams is the binding resource**, not decisions.
- F1 volume games help: adding about 520 F1 games to all 647 C1t1 games moves dev loss from 6.70 to 6.58. Per game they are worth somewhat less than C1t1 games on this C1t1 test (the views come from different play), but they are about 15× cheaper to produce.

**Takeaways:**
1. The champion's belief has little edge over a usage prior except where it uses damage. Its **offline sampler (`fp`) is no better than the Smogon prior overall**: it is worse in top-1 and NLL everywhere. In mean p(true) it is +0.03 on nature and EV class but −0.02 on moves. A likely cause is that TeamDatasets sets are drawn uniformly, not by count. Its **in-game belief (`played`) is clearly better on EV class, nature and item**, thanks to reverse-damage pruning, which the v2 log doesn't capture.
2. **Learned v0 (public view only) matches the in-game belief** on item, ability, nature and spread, and beats it on tera (+0.03) and moves (+0.04). It trails on EV class (0.673 vs 0.720) because it doesn't see damage. As a full calibrated distribution, its NLL is far better than 4–8 sampled worlds.
3. **The hybrid (v0 + the logged worlds) is the best belief on 5 of 7 attributes.** The learned prior and Foul Play's damage-pruned worlds are complementary.
4. **Hard-constraint errors.** In 0.41% of hidden-item rows (752 / 184,198), Foul Play's own constraints exclude the true item. Examples: Choice Scarf ruled out on Gholdengo; Leftovers marked impossible on Zamazenta, Corviknight and Dondozo; Life Orb on Golduck and Kingambit; Booster Energy on Raging Bolt. Every belief that applies these constraints gives such rows zero mass. A soft constraint is a cheap v1 fix.

**Sampler cost.** One `sample_pokemon` call costs 1.2 ms on a single core with the Mac idle, and 2.4–4.3 ms under load. With N = 64, a view costs 75–150 ms on one core. Dataset initialisation costs 0.7–1.8 s per game. In play, Foul Play calls the sampler once per alive opponent Pokémon per world, about 20–40 calls or 25–100 ms per decision, which is small next to a 2 s search.

## Caveats

- **The "true" sets are metamon's *predicted* teams** (hl_05_26, hidden details predicted from replays), probably filled with usage-style statistics. So the usage prior may be artificially strong here. Real ladder opponents could favour data-driven beliefs differently.
- Every decision counts once per alive opponent Pokémon. Rows within a game are highly correlated, so the effective sample is about 160 rosters and 270 games. Differences under about 0.01 are noise: one seed, no confidence intervals yet.
- The test set is held out by roster *within hr_train*. hr_val decision logs will replace it.
- The F1 volume farm (`hrvol-20261026-n1-a612`) packed chunks c000–c003 and then **started c004 at 17:02 UTC, which was still not packed at 18:43** (normally about 8 min). It may be hung; check the job. Nothing was touched.

## Next steps

1. **Log v3:** per opponent species, the surviving TeamDatasets/SmogonSets entries after damage pruning (or their indices). Without them, Foul Play's real belief can't be reconstructed offline beyond the 4–8 logged worlds.
2. **v1 model:** add damage evidence from the public log (`history_end` cuts: damage %, move, crit, attacker/defender, field) through a small sequence encoder. This is what closes the EV-class gap without Foul Play's calculator. Also use soft constraints and team context once there are thousands of games.
3. **Scale data:** the curve is still rising. F1 volume games are cheap and nearly as useful per game, so train on the full overnight volume farm and keep C1t1 for evaluation.
4. Add bootstrap confidence intervals over rosters to `benchmark.py`, and 3 seeds per learning-curve point.
5. **Pilot in play:** replace `sample_pokemon` with draws from the hybrid belief (the oracle hook shows where), then run A/A versus vanilla at C1t1.
