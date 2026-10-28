#!/usr/bin/env bash
# Verify mounted bytes on this node before importing or loading any model code.
set -euo pipefail
[[ $# -ge 1 ]] || { echo 'mounted data path missing' >&2; exit 2; }
data_dir=$1; shift
count=0; expect_data=0
for arg in "$@"; do
  if (( expect_data )); then
    [[ "$arg" == "$data_dir" ]] || { echo 'runner data path differs from checked mount' >&2; exit 2; }
    expect_data=0
  elif [[ "$arg" == --data ]]; then
    count=$((count+1)); expect_data=1
  elif [[ "$arg" == --data=* ]]; then
    echo 'inline data path is not allowed' >&2; exit 2
  fi
done
(( count == 1 && expect_data == 0 )) || { echo 'runner must receive the checked mount once' >&2; exit 2; }
manifest='data.sha256'
[[ -f "$manifest" && ! -L "$manifest" ]] || { echo 'data pin manifest missing' >&2; exit 2; }
exec 3< "$manifest"
for name in train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json; do
  IFS= read -r line <&3 || { echo 'data pin manifest incomplete' >&2; exit 2; }
  [[ "$line" == *"  $name" ]] || { echo 'data pin manifest order differs' >&2; exit 2; }
  expected=${line%%  *}
  [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || { echo 'data pin hash malformed' >&2; exit 2; }
  [[ -f "$data_dir/$name" && ! -L "$data_dir/$name" ]] || { echo "mounted data missing: $name" >&2; exit 2; }
  actual=$(sha256sum -- "$data_dir/$name")
  [[ "${actual%% *}" == "$expected" ]] || { echo "mounted data hash differs: $name" >&2; exit 2; }
done
if IFS= read -r extra <&3 || [[ -n "${extra:-}" ]]; then echo 'extra data pin manifest entry' >&2; exit 2; fi
exec python azure/sft/run_sft.py "$@"
