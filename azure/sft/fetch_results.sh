#!/usr/bin/env bash
# Download the small result files of an hrsft job (summary, eval JSON, step/train logs), read-only.
# Usage: bash azure/sft/fetch_results.sh <job_name> [dest_dir]   (default dest: azure/attempts/<job>/results)
# Checkpoints and adapter GGUF stay on the blob store; list them with: bash azure/fetch.sh list <job_name>
set -euo pipefail
ACC=${HR_AZ_STORAGE_ACCOUNT}
CON=${HR_AZ_CONTAINER}
JOB="$1"; HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${2:-$HERE/../attempts/$JOB/results}"; mkdir -p "$DEST"
az storage blob list --account-name $ACC -c $CON --auth-mode key --prefix "pokemon-rl-research/hardread/$JOB/" \
  --query "[].name" -o tsv 2>/dev/null | grep -v "/checkpoints/" | grep -E "\.(json|jsonl|log|yaml)$" | while read -r b; do
    az storage blob download --account-name $ACC -c $CON --auth-mode key -n "$b" -f "$DEST/$(basename "$b")" >/dev/null 2>&1 \
      && echo "$DEST/$(basename "$b")"
  done
python3 - "$DEST" <<'EOF'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]) / "summary.json"
if d.exists():
    s = json.loads(d.read_text())
    for k, v in s.get("stages", {}).items():
        print(k, v.get("status"), {x: v[x] for x in ("seconds", "s_per_step", "tokens_per_s", "s_per_10k_rows", "error") if x in v})
EOF
