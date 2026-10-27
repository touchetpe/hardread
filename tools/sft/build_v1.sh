#!/usr/bin/env bash
# v1 SFT data: ALL C1t1 label-farm chunks, prompt v1.2, argmax targets with choice-token fields.
# Run after the farm ends (08:00 UTC Oct 27). Target: 120k train action + 12k reads rows, val 2% of games.
# Usage: bash tools/sft/build_v1.sh [out_dir]      (default scratch_ds/sft/v1; ~10-15 min, ~3 GB RAM)
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:-scratch_ds/sft/v1}"
SRC=scratch_ds/sft/v1-src
for n in 1 2 3 4 5 6; do bash azure/fetch.sh chunks "hrlab-20261026-n$n-01d4" scratch_ds/lab; done
mkdir -p "$SRC"
ls scratch_ds/lab/lab-n*-c*.tar.gz | wc -l | xargs echo "chunks:"
python3 -I tools/dataset/extract.py scratch_ds/lab/lab-n*-c*.tar.gz --out "$SRC/decisions.jsonl.gz"
N=$(gzip -dc "$SRC/decisions.jsonl.gz" | wc -l | tr -d ' ')
# keep-frac so that ~120k train action rows survive the min-legal drop (~1.6%) and the 2% val split (+3% margin);
# the caps below then trim to exactly 120,000 / 12,000.
KEEP=$(python3 -c "print(min(1.0, round(120000 * 1.03 / ($N * 0.984 * 0.98), 4)))")
echo "decisions: $N  keep-frac: $KEEP"
python3 tools/sft/build_sft.py "$SRC/decisions.jsonl.gz" --out-dir "$OUT" \
  --task both --target argmax --val-frac 0.02 --cfg C1t1 --keep-frac "$KEEP" --reads-every 10 \
  --max-train-action 120000 --max-train-reads 12000 --n-examples 2
python3 -c "import json;m=json.load(open('$OUT/manifest.json'));print(json.dumps({k:m['counts'][k] for k in ('decisions_in','train_by_task','val_by_task','train_games','val_games')}))"
