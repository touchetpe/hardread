#!/usr/bin/env bash
# S-A v1 end to end (the commands behind the Oct 27 results in README.md):
#   farm chunks (C1t1 label farm, F1 volume farm, hr_val farm with decision log v3) -> examples
#   human partial labels (tools/metamon outputs) -> capped, roster-excluded train/dev/test
#   baselines on the hr_val test set (usage, revealed-set prior, Foul Play's fp_belief, as played, v0)
#   belief v1 runs (farm-only, farm+human, ablations, +worlds, human pre-training + farm fine-tuning)
#   benchmarks: hr_val (pre-registered S-A test, item NLL eps 1e-4, bootstrap by game) and held-out human games.
# Incremental downloads; everything downstream is rebuilt. FETCH=0 to skip downloads.
#   bash tools/belief/run_v1.sh
set -euo pipefail
HR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HR"
PY="${PY:-/usr/bin/python3}"                     # torch + numpy
FETCH="${FETCH:-1}"; T="${THREADS:-5}"
S=scratch_ds; B=$S/belief; D=$B/v1data; H=$B/human; V=$B/v1; R=$B/results_v1
mkdir -p $S/chunks/lab $S/chunks/vol $S/chunks/val $S/lab $S/vol $S/val $D $H $V $R

# 1. farm chunks -> extractor records
if [ "$FETCH" = 1 ]; then
  for j in hrval-20261026-n1-9231 hrval-20261026-n2-9231; do bash azure/fetch.sh chunks $j $S/chunks/val || true; done
  for n in 1 2 3 4 5 6; do bash azure/fetch.sh chunks hrlab-20261026-n$n-01d4 $S/chunks/lab || true; done
  for j in hrvol-20261026-n1-a612 hrvol-20261026-n2-9231 hrvol-20261026-n3-9231; do bash azure/fetch.sh chunks $j $S/chunks/vol || true; done
fi
for kind in val lab vol; do
  for f in $S/chunks/$kind/*.tar.gz; do
    [ -e "$f" ] || continue
    b=$(basename "$f" .tar.gz)
    [ -s $S/$kind/$b.jsonl.gz ] || $PY tools/dataset/extract.py "$f" --out $S/$kind/$b.jsonl.gz
  done
done

# 2. farm examples: training files capped at 4 decisions per (game, side, opponent Pokemon); hr_val: every row, split=test
$PY tools/belief/targets.py $S/lab/*-c???.jsonl.gz --out $D/lab.examples.jsonl.gz --max-per-mon 4 2> $D/lab.report.json
$PY tools/belief/targets.py $S/vol/*-c???.jsonl.gz --out $D/vol.examples.jsonl.gz --max-per-mon 4 2> $D/vol.report.json
$PY tools/belief/targets.py $S/val/*-c???.jsonl.gz --out $D/val.examples.jsonl.gz --force-split test 2> $D/val.report.json

# 3. human partial labels (tools/metamon: sample20k examples + partial_targets.py on the `rest` shards), then cap 3,
#    drop hr_val / reserved rosters, split by roster hash; test subset = 1 decision per (game, side, Pokemon)
REST=$(ls $H/rest*.examples.jsonl.gz 2>/dev/null || true)
$PY tools/belief/human_prep.py $S/metamon/beliefs/sample20k.examples.jsonl.gz $REST --out-dir $H --max-per-mon 3
$PY - <<'PY'
import json, sys
sys.path.insert(0, "tools/belief")
from targets import capped, iter_jsonl, open_text
with open_text("scratch_ds/belief/human/human.test1.jsonl.gz", "wt") as f:
    for e in capped(iter_jsonl(["scratch_ds/belief/human/human.test.jsonl.gz"]), 1, 1):
        f.write(json.dumps(e, separators=(",", ":")) + "\n")
PY

# 4. baselines and v0 on the two test sets
$PY tools/belief/baselines.py --examples $D/val.examples.jsonl.gz --method fpbelief --raw $S/chunks/val/*.tar.gz --out $D/val.fpbelief.jsonl.gz
for m in usage revealed played; do $PY tools/belief/baselines.py --examples $D/val.examples.jsonl.gz --method $m --out $D/val.$m.jsonl.gz; done
$PY tools/belief/predict.py --model $B/v0/model_f1.0_vol.pt --examples $D/val.examples.jsonl.gz --out $D/val.v0.jsonl.gz
$PY tools/belief/predict.py --model $B/v0/model_f1.0_vol_worlds.pt --examples $D/val.examples.jsonl.gz --out $D/val.v0worlds.jsonl.gz
for m in usage revealed; do $PY tools/belief/baselines.py --examples $H/human.test1.jsonl.gz --method $m --out $H/h.$m.jsonl.gz; done
$PY tools/belief/predict.py --model $B/v0/model_f1.0_vol.pt --examples $H/human.test1.jsonl.gz --out $H/h.v0.jsonl.gz

# 5. belief v1 runs
C="--examples $D/lab.examples.jsonl.gz --extra-train $D/vol.examples.jsonl.gz --out-dir $V --fractions 1.0 --threads $T"
P="--predict hrval=$D/val.examples.jsonl.gz human=$H/human.test1.jsonl.gz"
$PY tools/belief/train.py $C $P --tag _farm
$PY tools/belief/train.py $C --human $H/human.train.jsonl.gz --tag _farmhuman --predict hrval=$D/val.examples.jsonl.gz --predict-revealed human=$H/human.test1.jsonl.gz
$PY tools/belief/train.py $C $P --human $H/human.train.jsonl.gz --reveal 0 --tag _farmhuman_noreveal
$PY tools/belief/train.py $C $P --init $V/model_f1.0_farmhuman.pt --lr 3e-4 --tag _farmhuman_ft
$PY tools/belief/train.py $C --predict hrval=$D/val.examples.jsonl.gz --worlds 1 --tag _farm_worlds
$PY tools/belief/train.py $C --predict hrval=$D/val.examples.jsonl.gz --human $H/human.train.jsonl.gz --worlds 1 --tag _farmhuman_worlds
$PY tools/belief/train.py $C --predict hrval=$D/val.examples.jsonl.gz --worlds 1 --init $V/model_f1.0_farmhuman_worlds.pt --lr 3e-4 --tag _farmhuman_worlds_ft
for s in 1 2; do
  $PY tools/belief/train.py $C $P --seed $s --tag _farm_s$s
  $PY tools/belief/train.py $C $P --human $H/human.train.jsonl.gz --seed $s --tag _farmhuman_s$s
done
for f in 0.1 0.3; do $PY tools/belief/train.py $C $P --human $H/human.train.jsonl.gz --human-frac $f --tag _farmhuman_h$f; done

# 5b. the worlds hybrid fed Foul Play's fp_belief (64 pruned draws) instead of the logged worlds, at test time only
$PY tools/belief/predict.py --model $V/model_f1.0_farmhuman_worlds_ft.pt --examples $D/val.examples.jsonl.gz \
    --worlds-from-fpbelief $S/chunks/val/*.tar.gz --out $D/val.v1ftworlds_fpb.jsonl.gz

# 6. benchmarks
$PY tools/belief/benchmark.py --examples $D/val.examples.jsonl.gz --eps 1e-4 --bootstrap 2000 --ref fp_belief \
  --pred fp_belief=$D/val.fpbelief.jsonl.gz --pred usage=$D/val.usage.jsonl.gz --pred revealed_prior=$D/val.revealed.jsonl.gz \
  --pred played=$D/val.played.jsonl.gz --pred v0=$D/val.v0.jsonl.gz --pred v1_farm=$V/pred_f1.0_farm_hrval.jsonl.gz \
  --pred v1_farm+human=$V/pred_f1.0_farmhuman_hrval.jsonl.gz --pred v1_human-pretrain+farm-ft=$V/pred_f1.0_farmhuman_ft_hrval.jsonl.gz \
  --pred v0+worlds=$D/val.v0worlds.jsonl.gz --pred v1_farm+worlds=$V/pred_f1.0_farm_worlds_hrval.jsonl.gz \
  --pred v1_farm+human+worlds=$V/pred_f1.0_farmhuman_worlds_hrval.jsonl.gz \
  --pred v1_human-pretrain+farm-ft+worlds=$V/pred_f1.0_farmhuman_worlds_ft_hrval.jsonl.gz \
  --pred "v1_human-pretrain+farm-ft+fp_belief-input=$D/val.v1ftworlds_fpb.jsonl.gz" \
  --title "S-A on the hr_val farm (pre-registered test set)" --out-json $R/sa_hrval.json --out-md $R/sa_hrval.md
$PY tools/belief/benchmark.py --examples $H/human.test1.jsonl.gz --eps 1e-4 --bootstrap 2000 --ref usage \
  --pred usage=$H/h.usage.jsonl.gz --pred revealed_prior=$H/h.revealed.jsonl.gz --pred v0=$H/h.v0.jsonl.gz \
  --pred v1_farm=$V/pred_f1.0_farm_human.jsonl.gz --pred v1_farm+human=$V/pred_f1.0_farmhuman_human.jsonl.gz \
  --pred v1_farm+human_revealed-head=$V/pred_f1.0_farmhuman_human_revealed.jsonl.gz \
  --pred v1_human-pretrain+farm-ft=$V/pred_f1.0_farmhuman_ft_human.jsonl.gz \
  --title "Held-out human games (2026, rating >= 1500), revealed labels only" --out-json $R/sa_human.json --out-md $R/sa_human.md
