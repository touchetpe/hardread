#!/usr/bin/env bash
# S-A end to end: fetch farm chunks (read-only), extract, build belief examples, run the three
# baselines, train learned belief v0 (learning curve), benchmark. Re-run any time: downloads and
# extractions are incremental, everything downstream is rebuilt.
#   bash tools/belief/run_all.sh            # C1t1 label farm + F1 volume farm (extra training data)
#   FETCH=0 bash tools/belief/run_all.sh    # no download, use what is on disk
#   VOL=0 bash tools/belief/run_all.sh      # C1t1 only
set -euo pipefail
HR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$HR"
FPPY="${FPPY:-$HR/../environments/foulplay/bin/python}"   # Foul Play env (Python 3.13, for --method fp)
PY="${PY:-/usr/bin/python3}"                              # torch + numpy (model, training)
LAB_JOBS="${LAB_JOBS:-hrlab-20261026-n1-01d4 hrlab-20261026-n2-01d4 hrlab-20261026-n3-01d4 hrlab-20261026-n4-01d4 hrlab-20261026-n5-01d4 hrlab-20261026-n6-01d4}"
VOL_JOBS="${VOL_JOBS:-hrvol-20261026-n1-a612}"
FETCH="${FETCH:-1}"; VOL="${VOL:-1}"; WORKERS="${WORKERS:-12}"
S=scratch_ds; B=$S/belief; R=$B/results
mkdir -p $S/chunks/lab $S/chunks/vol $S/lab $S/vol $B $R

if [ "$FETCH" = 1 ]; then
  for j in $LAB_JOBS; do bash azure/fetch.sh chunks "$j" $S/chunks/lab || true; done
  [ "$VOL" = 1 ] && for j in $VOL_JOBS; do bash azure/fetch.sh chunks "$j" $S/chunks/vol || true; done
fi
for kind in lab vol; do
  for f in $S/chunks/$kind/*.tar.gz; do
    [ -e "$f" ] || continue
    b=$(basename "$f" .tar.gz)
    [ -s $S/$kind/$b.jsonl.gz ] || $PY tools/dataset/extract.py "$f" --out $S/$kind/$b.jsonl.gz
  done
done

$PY tools/belief/targets.py $S/lab/*-c???.jsonl.gz --out $B/lab.examples.jsonl.gz 2> $R/targets_lab.json
[ "$VOL" = 1 ] && ls $S/vol/*-c???.jsonl.gz >/dev/null 2>&1 && \
  $PY tools/belief/targets.py $S/vol/*-c???.jsonl.gz --out $B/vol.examples.jsonl.gz 2> $R/targets_vol.json

$PY tools/belief/baselines.py --examples $B/lab.examples.jsonl.gz --method usage --out $B/lab.usage.jsonl.gz
$PY tools/belief/baselines.py --examples $B/lab.examples.jsonl.gz --method played --out $B/lab.played.jsonl.gz
$FPPY tools/belief/baselines.py --examples $B/lab.examples.jsonl.gz --method fp --workers "$WORKERS" --out $B/lab.fp.jsonl.gz

$PY tools/belief/train.py --examples $B/lab.examples.jsonl.gz --out-dir $B/v0 --fractions 0.1,0.3,1.0
PREDS=(--pred usage=$B/lab.usage.jsonl.gz --pred fp=$B/lab.fp.jsonl.gz --pred played=$B/lab.played.jsonl.gz
       --pred v0_c1t1_10%=$B/v0/pred_f0.1.jsonl.gz --pred v0_c1t1_30%=$B/v0/pred_f0.3.jsonl.gz
       --pred v0_c1t1_100%=$B/v0/pred_f1.0.jsonl.gz)
if [ "$VOL" = 1 ] && [ -s $B/vol.examples.jsonl.gz ]; then
  $PY tools/belief/train.py --examples $B/lab.examples.jsonl.gz --extra-train $B/vol.examples.jsonl.gz \
      --out-dir $B/v0 --fractions 0.1,0.3,1.0 --tag _vol
  PREDS+=(--pred v0_+vol_10%=$B/v0/pred_f0.1_vol.jsonl.gz --pred v0_+vol_30%=$B/v0/pred_f0.3_vol.jsonl.gz
          --pred v0_+vol_100%=$B/v0/pred_f1.0_vol.jsonl.gz)
  # hybrid: the same net also reads the decision's logged worlds (Foul Play's in-game belief)
  $PY tools/belief/train.py --examples $B/lab.examples.jsonl.gz --extra-train $B/vol.examples.jsonl.gz \
      --out-dir $B/v0 --fractions 1.0 --worlds 1 --tag _vol_worlds
  PREDS+=(--pred v0+worlds_+vol_100%=$B/v0/pred_f1.0_vol_worlds.jsonl.gz)
fi
$PY tools/belief/benchmark.py --examples $B/lab.examples.jsonl.gz "${PREDS[@]}" \
    --title "S-A on held-out rosters (C1t1 label farm)" --out-json $R/sa_c1t1.json --out-md $R/sa_c1t1.md

# learning curve table: train size (from train_summary*.json) vs held-out scores (from the benchmark JSON)
$PY - "$B" "$R" <<'PY'
import json, sys
from pathlib import Path
B, R = Path(sys.argv[1]), Path(sys.argv[2])
res = json.loads((R / "sa_c1t1.json").read_text())["results"]
sizes = {}
for f in sorted((B / "v0").glob("train_summary*.json")):
    for r in json.loads(f.read_text())["runs"]:
        sizes[r["tag"]] = r
names = {"v0_c1t1_10%": "f0.1", "v0_c1t1_30%": "f0.3", "v0_c1t1_100%": "f1.0", "v0_+vol_10%": "f0.1_vol",
         "v0_+vol_30%": "f0.3_vol", "v0_+vol_100%": "f1.0_vol", "v0+worlds_+vol_100%": "f1.0_vol_worlds"}
cols = ("item", "ability_nt", "tera", "moves", "nature", "evclass", "spread")
L = ["| Model | train games | train rows | " + " | ".join(cols) + " |", "|---|---|---|" + "---|" * len(cols)]
for name in ["usage", "fp", "played"] + list(names):
    if name not in res:
        continue
    o = res[name]["overall"]
    s = sizes.get(names.get(name), {})
    L.append("| {} | {} | {} | ".format(name, s.get("train_games", "–"), s.get("train_examples", "–")) +
             " | ".join("{:.3f}".format(o[a]["ptrue"]) for a in cols) + " |")
md = "## Learning curve (held-out rosters, mean p(true); moves: mean P(move) over hidden moves)\n\n" + "\n".join(L) + "\n"
(R / "learning_curve.md").write_text(md)
print(md)
PY
