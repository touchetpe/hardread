#!/usr/bin/env bash
# Fetch finished label-farm chunks (read-only blob download) and extract each new chunk into
# its own decisions file. Usage: bash tools/student/sync_data.sh [loops] [sleep_s] [job_suffix] [prefix]
# Writes scratch_ds/student/<prefix>/*.tar.gz and scratch_ds/student/ext/<chunk>.jsonl.gz.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOOPS="${1:-1}"; SLEEP="${2:-900}"; SUF="${3:-01d4}"; PFX="${4:-lab}"
TAR="$ROOT/scratch_ds/student/$PFX"; EXT="$ROOT/scratch_ds/student/ext"
mkdir -p "$TAR" "$EXT"
for ((i = 0; i < LOOPS; i++)); do
  if [ "$PFX" = lab ]; then jobs="hrlab-20261026-n1-$SUF hrlab-20261026-n2-$SUF hrlab-20261026-n3-$SUF hrlab-20261026-n4-$SUF hrlab-20261026-n5-$SUF hrlab-20261026-n6-$SUF"
  else jobs="$SUF"; fi
  for j in $jobs; do bash "$ROOT/azure/fetch.sh" chunks "$j" "$TAR" || true; done
  for t in "$TAR"/*.tar.gz; do
    [ -e "$t" ] || continue
    b="$(basename "$t" .tar.gz)"
    [ -s "$EXT/$b.jsonl.gz" ] && continue
    mkdir -p "$EXT/tmp"
    python3 "$ROOT/tools/dataset/extract.py" "$t" --out "$EXT/tmp/$b.jsonl.gz" 2>>"$EXT/extract.log" \
      && mv "$EXT/tmp/$b.games.jsonl.gz" "$EXT/$b.games.jsonl.gz" \
      && mv "$EXT/tmp/$b.jsonl.gz" "$EXT/$b.jsonl.gz" && echo "[$(date -u +%T)] extracted $b"
  done
  echo "[$(date -u +%T)] sync $i: $(ls "$EXT"/*.jsonl.gz 2>/dev/null | grep -vc games) decision files"
  [ $((i + 1)) -lt "$LOOPS" ] && sleep "$SLEEP"
done
