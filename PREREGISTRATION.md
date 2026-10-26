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
