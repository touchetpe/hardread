#!/usr/bin/env bash
# Submit a HardRead Glimmer SFT job (one H100 node, or N single-GPU nodes with NODES=N).
# Usage: bash azure/sft/submit_sft.sh <job_name hrsft-...> <data_dir> <timeout_s> "<description>" [run_sft.py args...]
#   data_dir: output of tools/sft/build_sft.py (train/val/eval .jsonl.gz + manifest.json); it is shipped
#             inside the code snapshot (smoke ~2 MB; 50k examples ~40 MB).
#   NODES=N (env, default 1): multi-node DDP over N single-H100 nodes (see README; not yet run).
#   DRY_RUN=1: stage the attempt dir and job.yml, do not submit.
#   CKPT_URI=azureml://.../checkpoints/checkpoint-N/ with CKPT_ARG=--adapter (use with --eval-only) or
#   CKPT_ARG=--resume-from: mounts that checkpoint read-only as inputs.ckpt and passes it to run_sft.py.
#   run_sft.py always receives --job-timeout <timeout_s> so its training budget fits inside the job.
set -euo pipefail
NAME="$1"; DATA="$2"; TIMEOUT="$3"; DESC="$4"; shift 4
NODES="${NODES:-1}"
[[ "$NAME" == hrsft-* ]] || { echo "job name must start with hrsft-"; exit 2; }
MAX_TIMEOUT="${MAX_TIMEOUT:-10800}"  # raise explicitly (e.g. MAX_TIMEOUT=21600) for v0 runs
(( TIMEOUT <= MAX_TIMEOUT )) || { echo "timeout must be <= $MAX_TIMEOUT s (set MAX_TIMEOUT to raise)"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"; AZ="$(dirname "$HERE")"; HR="$(dirname "$AZ")"
RG=rg-${HR_AZ_WS}; WS=${HR_AZ_WS}
ENV_ID=/subscriptions/${HR_AZ_SUBSCRIPTION}/resourceGroups/$RG/providers/Microsoft.MachineLearningServices/workspaces/$WS/environments/pokemon-h100-axolotl-cuda13-training/versions/20261026-9f7aa9a5704c
SNAP=azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/h100/model-snapshots/acquire-20261026-01/
A="$AZ/attempts/$NAME"
[ -e "$A" ] && { echo "attempt dir exists: $A"; exit 2; }
mkdir -p "$A/code/azure/sft" "$A/code/tools/sft" "$A/code/data"
cp "$HERE"/{run_sft.py,eval_sft.py,sft_plugin.py,qlora_sft.yaml} "$A/code/azure/sft/"
cp "$HR/tools/sft"/{serialize.py,glimmer_chat.py,build_sft.py,dex.json} "$A/code/tools/sft/"
cp "$DATA"/{train.jsonl.gz,val.jsonl.gz,eval.jsonl.gz,manifest.json} "$A/code/data/"
(cd "$HR" && git rev-parse HEAD > "$A/code/HARDREAD_COMMIT" 2>/dev/null || true)
(cd "$A/code" && find . -type f | sort | xargs shasum -a 256 > "$A/code-manifest.sha256")
CKPT_CMD=""; CKPT_INPUT=""
if [ -n "${CKPT_URI:-}" ]; then
  [[ "${CKPT_ARG:-}" == "--adapter" || "${CKPT_ARG:-}" == "--resume-from" ]] || { echo "CKPT_ARG must be --adapter or --resume-from"; exit 2; }
  CKPT_CMD="$CKPT_ARG \${{inputs.ckpt}}"
  CKPT_INPUT=$'  ckpt:\n    type: uri_folder\n    path: '"$CKPT_URI"$'\n    mode: ro_mount'
fi
if [ "$NODES" -gt 1 ]; then
  # AML launches one process per node and sets RANK/WORLD_SIZE/MASTER_ADDR/MASTER_PORT;
  # HF Trainer/Accelerate initialise DDP from those env vars (axolotl --launcher python).
  DIST=$'distribution:\n  type: pytorch\n  process_count_per_instance: 1'
else
  DIST=""
fi
cat > "$A/job.yml" <<YML
\$schema: https://azuremlschemas.azureedge.net/latest/commandJob.schema.json
name: $NAME
display_name: $NAME
experiment_name: hardread-sft
description: "$DESC"
code: "$A/code"
command: python azure/sft/run_sft.py --snapshot \${{inputs.model_snapshot}} --data data --out \${{outputs.artifacts}} --job-timeout $TIMEOUT $CKPT_CMD $*
compute: azureml:${HR_AZ_CLUSTER}
environment: azureml:$ENV_ID
environment_variables:
  DATASET_MOUNT_BLOCK_BASED_CACHE_ENABLED: "true"
  DATASET_MOUNT_BLOCK_FILE_CACHE_ENABLED: "false"
  DATASET_MOUNT_MEMORY_CACHE_SIZE: "2147483648"
inputs:
  model_snapshot:
    type: uri_folder
    path: $SNAP
    mode: ro_mount
$CKPT_INPUT
outputs:
  artifacts:
    type: uri_folder
    path: azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/hardread/$NAME/
    mode: rw_mount
limits:
  timeout: $TIMEOUT
resources:
  instance_count: $NODES
  shm_size: 128g
$DIST
tags:
  project: pokemon-rl-research
  lane: hardread-sft
  stage: glimmer-sft
YML
echo "staged $A"
if [ "${DRY_RUN:-0}" = "1" ]; then cat "$A/job.yml"; exit 0; fi
az ml job create -f "$A/job.yml" -g $RG -w $WS -o json > "$A/job-create.json" 2> "$A/job-create.stderr"
python3 -c "import json; d=json.load(open('$A/job-create.json')); print(d['name'], d['status'])"
