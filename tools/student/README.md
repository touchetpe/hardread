# HardRead student (tools/student)

A ≈25M-parameter policy/value network distilled from Foul Play's C1t1 self-play searches: 24.9M at inference, plus a 0.66M training-only opponent head.

It has three uses:
- an engine lever: blend the student policy into Foul Play's final move choice;
- a fast standalone player;
- the same-input control for "Students of the Champion".

**Input:** only public information, the same information Glimmer's serializer gets:
- the bot's decision view (decision log v2);
- the legal root menu;
- turn, k and team_preview;
- the **recent public events**, taken from the same canonical public history (`fp/hardread_history.canonical_history`, with the opponent's HP as Showdown's public percentage).

The opponent's true sets (`opp_team`) are never an input; they are read only to define rosters for the split. The other seat's search policy is used only as an auxiliary *training target* (the opponent head); it is never an input.

## Files
| File | What |
|---|---|
| `encode.py` | Deterministic encoder `(view, legal, turn, k, team_preview, history, player)` → arrays. Numpy only; `history` is the canonical public history visible at the decision. |
| `model.py` | `StudentNet` (PyTorch; MPS, CUDA or CPU). |
| `train.py` | Training, splits, held-out metrics, learning curve (`--fracs`), warm start (`--init`), `--eval-only`. |
| `infer.py` | `load(path)`, `policy_value(view, legal, turn, k, team_preview, history=None, player=None) -> ({action: prob}, value)`, `choose(...)`. Pass `history` and `player`: the model was trained with the event tokens. The numpy backend needs no torch and matches torch to 1e-7; a torch backend is also available. CLI: `export` (.pt → .npz), `bench`, `serve` (HTTP sidecar). |
| `fp_student.py` | Foul Play side: decision-log view, k counter, canonical history (from `hardread_policy`'s capture), the student-blend rule, `student_choose` for `HARDREAD_POLICY=student`, and an optional input log (`HARDREAD_STUDENT_LOG`) for parity audits. Nothing in `foul-play/` imports it. |
| `lever_student_blend.patch` | Proposed diff to `fp/hardread_levers.py`, `fp/search/main.py` and `fp/websocket_client.py` (which also captures protocol history when the lever is on): `HARDREAD_LEVER_SELECT=student-blend`. **Not applied.** |
| `policy_student.patch` | Proposed diff to the other lane's `fp/hardread_policy.py`: `HARDREAD_POLICY=student`, plus `HARDREAD_POLICY_ONLY_PREFIX` to put the policy on one seat only. **Not applied.** |
| `sync_data.sh` | Fetch finished farm chunks (read-only) and extract each one to `scratch_ds/student/ext/<chunk>.jsonl.gz`. |

Data, caches, checkpoints and run logs are under `scratch_ds/student/` (gitignored).

## Encoder (`hr-student-enc-v3`)
The input is a set of tokens; there is no positional encoding, so reserve order and menu order don't matter.

**Field token (48 numbers + weather and terrain ids):**
- trick room, turn, k, team preview;
- 15 side conditions per side;
- alive counts and HP totals;
- menu composition;
- speed comparison of the actives (boosts, paralysis, scarf, tailwind, trick room);
- each active's best-damage ratio.

**12 entity tokens** (own 6, then opponent 6):
- Embeddings: species, item (`unknownitem` → `<unk>`, none → `<none>`), ability, tera type, current types (tera-aware), status, removed item.
- Bags (mean of embeddings): known moves, Foul Play's impossible items, impossible abilities.
- 46 numbers:
  - HP fraction, boosts, tera flags, choice-item flag;
  - Foul Play's speed-range inference (opponents);
  - base stats;
  - our own computed stats (own side only; the opponent's nature/EVs in the view are Foul Play defaults and are ignored);
  - revealed flags;
  - calc_lite damage estimates;
  - entry-hazard damage.

**One action token per legal action:**
- kind (move / move-tera / switch), move id and type;
- power, accuracy, priority, category;
- STAB, type effectiveness, move-family flags;
- calc_lite expected damage vs the opponent's active, as a fraction of its current HP, with a KO flag;
- for a switch: the target's entity embedding (gathered), its defensive matchup, the threat from the opponent's active, and hazard damage.

**16 recent-event tokens.** These are the last 16 public events, parsed deterministically from the canonical public history (`encode.parse_events`, last 200 lines):
- **Events:** move, switch/drag/replace, residual damage/heal, status/cure, faint, boost/unboost, weather, terrain, side conditions, volatile start/end, Terastallize, item and ability reveals, cant/activate/fail.
- **A move absorbs its outcome lines:** the target's HP after the move, the damage dealt, crit, miss, super-effective/resisted/immune, fail.
- **Ids** go through the shared move, species, item and ability tables, plus a small `misc` vocabulary (statuses, weather, terrains, side conditions, volatiles) with 32 OOV buckets.
- **Numbers:** side (ours/theirs), HP after, damage dealt, `[from]` source, turns ago, recency.

**Event parity, checked live:** one F1 game on a scratch copy of `foul-play/` with both patches, both seats using the lever, and `--log-decisions`.
- **History:** the student's live input history (the bot's own protocol stream, captured by `hardread_policy.record` and canonicalised) equals the training-path history (`log[:history_end]` from the extracted battle log, canonicalised) at **89 of 89** decisions.
- **Event tokens:** identical at **89 of 89** decisions.
- **Menus:** identical at 89 of 89 decisions.
- **Outputs:** the student's live outputs equal its offline outputs on the extracted record (max |Δp| = 0).

**calc_lite** is the standard damage formula on public information:
- **Inputs:** our exact stats; the opponent's stats from base stats with one fixed generic spread; boosts; STAB, including Tera; type chart; burn; Band/Specs/Life Orb/Expert Belt; sun/rain; screens; Knock Off; multi-hit; fixed-damage moves; known immunity abilities and Air Balloon.
- **Opponent attackers** with fewer than 4 revealed moves also get a 90-power STAB proxy per type.

**Vocabularies:**
- **Built from Foul Play's data:** `fp/data/pokedex.json` (1,469 species), `moves.json` (886 moves), items and abilities from the Smogon stats and sets caches, and the type chart and natures parsed from `fp/battle/helpers.py`.
- **Unseen names** go to 8 crc32 OOV buckets per vocabulary.
- **Checkpoints** store the full encoder state (vocabularies + tables), so inference doesn't depend on the data files changing.

## Model
Pre-LayerNorm transformer over `[CLS, FIELD, 12 entities, 16 events, ≤16 actions]`: d = 512, 7 layers, 8 heads, FFN 2048, GELU-tanh, dropout 0.1, key-padding mask.

| Part | Parameters |
|---|---|
| Transformer blocks | 22.07M |
| Embeddings | 2.32M |
| Input projections, policy and value heads | 0.47M |
| **Total at inference** | **24.86M** |
| Opponent-action head (training-only) | 0.66M |
| **Total while training** | **25.52M** |

**Heads:**
- **Policy:** one logit per action token, masked to the legal menu.
- **Search-value head (CLS):** logit of `root_value`.
- **Outcome head:** logit of P(win), computed on *stop-gradient* CLS features.
- **Opponent-next-action head** (auxiliary; an output only):
  - **Output space:** read from the opponent's active entity token, over `[move id | tera move id | switch to opponent slot 0–5]`. The move logits are tied to the move-embedding table, so the space is fixed and no opponent-private input is needed.
  - **Target:** the *other seat's* own search policy at the same decision point. The A and B records of a game are joined by (turn, k = 0), i.e. simultaneous decisions: turn starts and team preview, 87% of decisions.
  - **Mapping:** the other seat's actions are mapped by move id and by opponent species → slot. Unmappable mass is dropped; if less than half the mass maps, the target falls back to the other seat's played choice.
  - **Masks:** special ids; switches to missing slots, and (outside team preview) to the active or fainted slots.

## Training (`train.py`)
**Loss per decision:**
```
L = KL(pi_search || p)                         # chance-weighted visit shares (the search policy)
  + 0.1 * CE(played choice)
  + 0.5 * BCE(sigmoid(v_search), root_value)    # soft target in [0, 1]
  + 0.5 * BCE(sigmoid(v_outcome), outcome)      # outcome head on stop-gradient features
  + 0.5 * CE(opponent head, other seat's search policy)   # k = 0 decisions only (--opp-weight)
```
Without the stop-gradient (first pilot), the outcome loss let the trunk memorise games: every decision in a game shares one outcome label. Train outcome log-loss fell to 0.03 while dev rose to 1.85, and the policy suffered. With the stop-gradient, the trunk learns only from the per-decision targets.

**Early stopping and model selection:**
- Early stopping uses dev `KL + 0.5 * search-value loss`.
- `infer.policy_value` returns P(win) from whichever value head has the lower dev log-loss against the game outcome (stored as `value_head` in the checkpoint).
- That value is then Platt-scaled with 2 parameters fitted on dev (`value_calibration` in the checkpoint; `train.py --calibrate-only <ckpt>` adds it to an existing checkpoint).

**Optimisation:** AdamW (lr 3e-4, betas 0.9/0.98, weight decay 0.05 on matrices only), 300-step warmup then cosine to 10%, batch 256, gradient clip 1.0. Batches are trimmed to their longest legal menu.

**Seeds:** fixed (20261026) for Python, numpy and torch, and the splits are hash-based. MPS kernels aren't bit-deterministic, so reruns match to noise, not bits.

**Splits** (deterministic; both sides of a game always fall in the same split):
- **test:** every game in which either team's roster (sorted species ids) has `crc32(roster) % 1000 < 70`. Each test game contains at least one roster that never appears in training. Metrics are also given for the subset where both rosters are unseen.
- **dev:** 10% of the remaining games, by `crc32(game uid)`.
- **train:** the rest. Learning-curve subsets are nested, by `crc32("lc" + game uid)`.
- **hr_val:** the `hr_val` rosters (`data/pools-v1.json`) are untouched by tonight's farm, so they remain fully clean for later evaluation.

## Results (Mon Oct 26, data cutoff 19:10 UTC)
### Data used
- **Main data:** 44 C1t1 label-farm chunks from `hrlab-20261026-n{1..6}-01d4` (fetched every ≈16 min, 16:58–19:10 UTC). That is 1,100 self-play games, **92,198 decisions**, all with history, 87% with an opponent-action target.
- **Pool and holdout:** all games are on the `hr_train` pool. The held-out sets are defined by the rules in the Splits section.

| Split | Decisions | Games |
|---|---|---|
| Train | 68,354 | 819 |
| Dev | 9,737 | — |
| **Test** | **14,107** | **178** |
| Test, both rosters unseen | 4,991 | 67 |

- **F1 volume farm:** 4 chunks (49k decisions) were used only in a side experiment (see "F1 pretraining").

### Main model (24.9M at inference; the frac = 1 row below)
Checkpoint: `scratch_ds/student/runs/final/frac1/best.pt`, numpy export `best.npz`.

**Training:** 4 epochs (cosine schedule), 7.8 min on MPS. Dev loss was still decreasing slowly at epoch 4.

| Test metric (14,107 decisions, 178 games) | Student | Reference |
|---|---|---|
| Top-1 vs the played choice | **49.2%** | uniform 16.4%; max-damage heuristic 34.7%; search argmax = played 88.8% |
| Top-1 vs the search argmax | **49.9%** | two half-budget searches agree on 80% (see Limitations) |
| KL(search policy ‖ student) | **0.477** | uniform 0.861 |
| NLL of the played choice | 1.384 | — |
| Value (what `infer` returns: outcome head + dev Platt): Brier / log-loss / ECE | **0.1755 / 0.517 / 0.015** | search root value as a predictor: 0.2076 / 0.592 / 0.009; constant 0.25 / 0.693 |
| Value, raw outcome head before Platt: Brier / ECE | 0.1814 / 0.073 | Biased low: mean prediction 0.41 vs win rate 0.48, on dev and test alike; the dev Platt fit (a = 0.96, b = +0.41) fixes this. |
| Opponent head top-1 vs the opponent's search argmax | 38.1% | 12,288 decisions |

**By phase:**

| Phase | Decisions | top-1 vs search argmax | KL |
|---|---|---|---|
| Team preview | 356 | 39.6% | 0.32 |
| Turn start | 11,932 | 49.5% | 0.48 |
| Extra switches (k > 0) | 1,819 | 54.7% | 0.49 |

**Subsets:**
- **Both rosters unseen** (4,991 decisions): top-1 50.6%, KL 0.475, Brier 0.199. This is in line with the full test set, so there's no sign of roster memorisation.
- **Multi-action decisions only:** top-1 48.9%.

**Calibration of the returned value** (test, 10 bins: mean prediction → win rate): 0.04→0.06, 0.15→0.16, 0.25→0.27, 0.35→0.37, 0.45→0.44, 0.55→0.54, 0.65→0.62, 0.74→0.75, 0.85→0.88, 0.96→0.97.

### Learning curve
Same 24.9M model and recipe. Nested subsets of train games, all evaluated on the same test set; Platt scaling fitted on dev in each run.

| Train games | Train decisions | top-1 vs played | top-1 vs search argmax | KL | Value Brier | Value ECE | Opponent-head top-1 |
|---|---|---|---|---|---|---|---|
| 103 | 9,189 | 40.0% | 40.5% | 0.589 | 0.196 | 0.039 | 24.0% |
| 205 | 18,581 | 44.5% | 45.0% | 0.553 | 0.191 | 0.029 | 29.9% |
| 410 | 35,715 | 47.3% | 47.9% | 0.509 | 0.177 | 0.017 | 33.3% |
| **819** | **68,354** | **49.2%** | **49.9%** | **0.477** | **0.176** | **0.015** | **38.1%** |

**Per doubling of data:** top-1 gains +4.5, +2.9, +1.9 points; KL falls by 0.036, 0.044, 0.032. The KL drop per doubling isn't shrinking yet.
- **Extrapolation [rough]:** ≈450k decisions is ~2.7 more doublings, i.e. top-1 ≈55% and KL ≈0.40 *with this recipe*. A longer schedule and F1 pretraining should add to that.

### F1 pretraining (side experiment, small model, encoder v1, 20k C1t1 decisions)
| Training data | top-1 vs search argmax (C1t1 test) | KL |
|---|---|---|
| C1t1 only (20k) | 39.5% | 0.612 |
| F1 only (38k decisions from the fast farm) | 41.5% | 0.581 |
| F1 then C1t1 fine-tune | **43.7%** | **0.574** |

At this scale, volume beats label quality. The F1 farm (≈1,200 games/h) is a strong pretraining source for the *engine* student.

The *same-input control* for the paper should stay C1t1-only: the same data the LLM is fine-tuned on.

### CPU inference latency (per decision, batch 1)
Final model, real test decisions with their history. Encode includes calc_lite and event parsing (≈1.3–1.5 ms). Forward is 24.9M parameters over ≈44 tokens. Use `VECLIB_MAXIMUM_THREADS=1` for numpy.

| Backend | Conditions | p50 | p95 |
|---|---|---|---|
| **numpy, 1 BLAS thread** | M4 Max, shared (load average ≈60) | **7.6–8.0 ms** | **9.1–9.9 ms** |
| torch CPU, 1 thread | same | 8.6 ms | 21.9 ms |
| numpy, 1 thread | same Mac during my own 4-game run (load ≈85) | 18.2 ms | 27.5 ms |
| HTTP sidecar, student call inside live games | 40-game standalone run, load 60–100 | 19.6 ms | 39 ms |

Either way it is <2% of a C1t1 decision (≥ 4 worlds × 2 s, run in parallel).

### Lever activity offline (test decisions with ≥2 actions, 5,779 decisions, final model)
| lam | argmax changed | Foul Play candidate set (≥75% of top) changed | TV distance of the sampling distribution |
|---|---|---|---|
| 0.15 | 3.8% | 8.0% | 0.036 |
| **0.3** | **7.9%** | **16.6%** | **0.080** |
| 0.5 | 16.5% | 30.4% | 0.163 |

### Compute used
- **MPS (M4 Max, shared):** ≈50 min in total. That covers pilots, profiling, the F1 side experiment, the 4-run ablation, the 4-point learning curve and calibration.
- **CPU:** encoding (≈4 ms per decision per process, 6 processes), smoke and parity games on a private server (port 18071), and the student sidecar (port 18061). All were stopped afterwards.

### Ablation: recent events and the opponent head (equal data)
**Setup:**
- Small model (4.5–4.6M), same recipe: lr 4e-4, 6 epochs, best dev epoch.
- Data: 26 C1t1 chunks (650 games, 54,252 decisions). Train 42,635 decisions / 502 games.
- Test: 7,509 decisions / 100 games under the roster rule.

| Config | top-1 vs played | top-1 vs search argmax | KL to search | Outcome Brier | Outcome log-loss | ECE |
|---|---|---|---|---|---|---|
| A: no events, no opponent head | 46.1% | 46.9% | 0.514 | 0.190 | 0.553 | 0.053 |
| B: + events | 47.0% | 47.8% | 0.503 | 0.198 | 0.569 | 0.030 |
| **C: + events + opponent head** | **47.7%** | **48.3%** | 0.505 | **0.183** | **0.536** | **0.015** |
| D: opponent head, no events | 44.3% | 45.0% | 0.531 | 0.192 | 0.558 | 0.038 |

**Paired differences** on the same test decisions, with 95% intervals from a game-clustered bootstrap. These cover test sampling only (one training seed), so read them as indicative:

| Comparison | top-1 vs search argmax | KL | Outcome Brier |
|---|---|---|---|
| Events (B − A) | +0.95 pt [−0.3, +2.3] | −0.011 [−0.020, −0.003] | +0.008 [−0.007, +0.023] |
| Opponent head, with events (C − B) | +0.45 pt [−0.6, +1.4] | +0.002 [−0.004, +0.007] | −0.015 [−0.026, −0.003] |
| Opponent head, without events (D − A) | −1.9 pt [−2.8, −1.0] | +0.017 [+0.011, +0.023] | +0.002 [−0.008, +0.012] |
| Both (C − A) | +1.4 pt [+0.2, +2.6] | −0.009 [−0.018, −0.001] | −0.006 [−0.022, +0.009] |

**Reading:**
- **Events:** the recent-event tokens give a small, consistent policy gain (KL interval excludes 0).
- **Opponent head:** it improves value calibration when it has the events, but does not reproduce Jaxcalibur's reported "≈3× efficiency" at this scale; without events it *hurts* the policy. The head itself reaches 35% top-1 against the opponent seat's search argmax (6,488 test decisions).
- **Choice:** the main model uses C (events + opponent head, weight 0.5). The events also make the student's input match Glimmer's.

## Proposed lever: `HARDREAD_LEVER_SELECT=student-blend` (for the next head-to-head)
**Exact rule, applied to each decision after the normal search:**
1. **Search share:** `s(a)` is the chance-weighted visit share of root action `a` over the sampled worlds (`hardread_levers.aggregate`), normalised so `sum s = 1`. The menu is `A = keys(s)`.
2. **Student policy:** `p(a) = infer.policy_value(view, sorted(A), turn, k, team_preview, history, player)`.
   - `view` is built exactly like the decision log, on the same deep-copied battle after team-preview handling.
   - `k` comes from a per-battle, per-turn counter.
   - `history` is the bot's captured protocol stream, cut like `extract.history_cuts` and canonicalised.
3. **Blend:** `q(a) = (1 - lam) * s(a) + lam * p(a)`, with `lam = 0.3` (`HARDREAD_STUDENT_LAMBDA`).
4. **Choice:** Foul Play's own final rule applied to `q`. With `C = {a : q(a) >= 0.75 * max q}`, sample `a ~ q` restricted to `C` (`HARDREAD_STUDENT_SELECT=sample`, the default). `argmax` is the deterministic variant.
5. **Failure:** on any student failure (no checkpoint, sidecar down, empty menu) the lever returns `None`, so Foul Play's own selection is played, and the failure is logged.

**Why it can help:** the student is an amortised average of many C1t1 searches over similar states. Blending it in lowers the variance of a single 4–8-world, 2-second search, which shifts the decision when the search is close. The student moves at most `lam = 0.3` of the probability mass. When the search puts ≥ 82% on one action, the blend always plays that action: no other action can reach 75% of its `q`.

**Compute:** the student adds ≈8 ms (numpy, 1 thread, p50; ≈20 ms under heavy load or through the sidecar) after the search. The pre-registration counts this inside HardRead's time, so it is ≈0.4–1% of a C1t1 decision (2 s of search per world, worlds in parallel); for strict equality, shorten the per-world search time by that amount.

**Suggested test** (pre-registration style):
1. Tune `lam` ∈ {0.15, 0.3, 0.5} on development set A, at C1t1, with 500 side-swapped pairings each.
2. Gate the best `lam` on set B against `fp`.
3. Report `argmax` and `sample` separately.

**How to run:**
```sh
cd foul-play && git apply ../tools/student/lever_student_blend.patch     # proposal: not applied
python3 tools/student/infer.py export --ckpt <run>/best.pt --out <run>/best.npz
# in process (needs `pip install numpy` in the Foul Play venv):
HARDREAD_LEVER_SELECT=student-blend HARDREAD_STUDENT_CKPT=<run>/best.npz ...
# or with the sidecar (no numpy in the venv):
python3 tools/student/infer.py serve --ckpt <run>/best.npz --port 18061 &
HARDREAD_LEVER_SELECT=student-blend HARDREAD_STUDENT_URL=http://127.0.0.1:18061/policy_value ...
```
**Smoke tests:** on a scratch copy of `foul-play/` with both patches applied, F1 self-play with the lever on both seats, using the sidecar.
- **Pilot model:** 27 + 28 lever decisions, 0 failures.
- **Event model:** 28 + 25 lever decisions, history captured (27 lines at team preview, growing to ≈170 by turn 16), 0 failures.
- **Parity game:** the 89/89 audit above.

## Standalone player: `HARDREAD_POLICY=student`
`policy_student.patch` adds a `student` branch to the other lane's `fp/hardread_policy.py`. It reuses that module's `legal_actions`, `build_view` and k counter, so the inputs match the decision log. It calls `fp_student.student_choose`, which plays the argmax.

`HARDREAD_POLICY_ONLY_PREFIX=hra` puts the student on seat A only (`run_match.py` names A bots `hra…`).

**Standalone strength (final model, no search):** student (A) vs plain Foul Play F1 (B), 20 side-swapped pairings on `gen9/hr_hl_dev`, sidecar.
- **Result: 7 / 40 = 17.5%** (95% CI ≈ ±12 points).
- 1,540 student decisions: 0 fallbacks, 0 server rejections.
- Latency p50: student call 19.6 ms, whole policy step 44 ms.
- As a standalone player it is far below even F1 search, which is expected at 68k training decisions and ≈50% search-argmax agreement. Its near-term value is as a lever and as the control.

**Smoke test** (pilot model, sidecar, scratch copy): 4 F1 games, student (A) vs plain Foul Play F1 (B), side-swapped.
- 200 student decisions: 0 fallbacks, 0 server rejections.
- Latency p50: student call 9 ms, whole policy step 31 ms (including 21 ms for the legal-menu probe).

## Limitations
- **Volatile state isn't in the decision view:** Substitute, Taunt, Encore, confusion, Leech Seed, choice lock, PP. The student sees volatiles only through the event tokens, and only for the last 16 events. Glimmer's serializer has the same restriction (3 recent turns).
- **Only half the search's information:** the search also has its sampled opponent sets and a full simulator; the student has only the public view plus calc_lite.
  - Two halves of the same search's worlds agree on the argmax 80% of the time (KL 0.155 between them; 16k decisions).
  - The student is far below that, so there is large headroom from more data.
- **Labels are noisy:** C1t1 labels are 4–8 worlds × 2 s, and the played choice equals the search argmax 89% of the time.
- **Model selection:** single training seed. The ablation intervals cover test sampling only.
- **Outcome head:** tonight's data is ~1k games, so every game outcome label is shared by ~89 decisions. The outcome head is therefore on stop-gradient features; `infer` picks the value head (`value_head`) on dev and Platt-scales it.
- **Extractor (not modified, reported):** `tools/dataset/extract.py` names the games file with `name.replace(".jsonl", ".games.jsonl")`, which rewrites *every* `.jsonl` in the name. For example, `x.jsonl.gz.tmp.jsonl.gz` becomes `x.games.jsonl.gz.tmp.games.jsonl.gz`. Harmless for normal names. Suggested fix: replace only the last occurrence.
- **Platform workarounds:**
  - MPS (torch 2.8) returned NaN for a `clamp(min=1e-8)` denominator in the opponent loss; `train.py` uses `torch.where`.
  - numpy 2.0 + Accelerate prints spurious matmul FP warnings; `infer.py` silences them, and its results match torch to 1e-7.

## Next: ≈450k C1t1 decisions on an H100
1. **Data.**
   - **Main source:** all overnight C1t1 chunks: `bash tools/student/sync_data.sh 1 0`, then `train.py --workers 16`. This is ~5k games, 6.5× tonight's data.
   - **Clean cross-pool test:** a small C1t1 decision-logging run on the `hr_val` rosters (the value-of-information jobs don't log decisions).
2. **Two students, kept separate.**
   - **(a) Same-input control:** C1t1 only, events on. This is the paper's control: the same data and the same public input as Glimmer's SFT.
   - **(b) Engine student:** pretrain on the F1 volume farm (≈1M+ decisions overnight; F1 beat C1t1 at equal small scale), then fine-tune on C1t1.
3. **Recipe.**
   - bf16, batch 1024, 10–20 epochs (the dev loss was still falling at 4), EMA weights.
   - lr sweep {2e-4, 4e-4, 8e-4}; 3 seeds for every ablation.
   - At H100 speed the 25M model takes minutes per epoch. Try 50–100M only if the learning curve keeps its slope.
   - Re-run the events × opponent-head ablation at full scale. At 54k decisions the opponent head helped value (−0.015 Brier) but not the policy.
4. **Auxiliary targets.**
   - An opponent-set belief head (item, ability, tera, moves; labels from `opp_team`, never inputs), shared with `tools/belief` and as Jaxcalibur does.
   - Per-world value.
5. **Lever.**
   - Tune `lam` ∈ {0.15, 0.3, 0.5} at C1t1 on development set A, then gate on set B (pre-registration style).
   - Next levers: student prior for world sampling or move ordering; student value as a tie-breaker in `select`. Leaf value or PUCT priors need poke-engine (Rust) changes.
