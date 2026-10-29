#!/usr/bin/env bash
# Submit a HardRead Glimmer SFT job (one H100 node, or N single-GPU nodes with NODES=N).
# Usage: bash azure/sft/submit_sft.sh <job_name hrsft-...> <data_dir> <timeout_s> "<description>" [run_sft.py args...]
#   data_dir: output of tools/sft/build_sft.py (train/val/eval .jsonl.gz + manifest.json).
#             It is copied into the code snapshot unless DATA_URI is set, when it supplies hash pins.
#   NODES=N (env, default 1): multi-node DDP over N single-H100 nodes (see README; not yet run).
#   DRY_RUN=1: stage the attempt dir and job.yml, do not submit.
#   DATA_URI=azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/.../
#   mounts the data read-only and verifies its four local pinned hashes on every node.
#   CKPT_URI=azureml://.../checkpoints/checkpoint-N/ with CKPT_ARG=--adapter (use with --eval-only) or
#   CKPT_ARG=--resume-from: mounts that checkpoint read-only as inputs.ckpt and passes it to run_sft.py.
#   run_sft.py always receives --job-timeout <timeout_s> so its training budget fits inside the job.
set -euo pipefail
NAME="$1"; DATA="$2"; TIMEOUT="$3"; DESC="$4"; shift 4
RESUME_MODE=0
for arg in "$@"; do
  [[ "$arg" != --resume-marker && "$arg" != --resume-marker=* ]] || { echo "use RESUME_MARKER_FILE" >&2; exit 2; }
  [[ "$arg" != --resume-v2-second-epoch ]] || RESUME_MODE=1
  [[ "$arg" != --proof-binding && "$arg" != --proof-binding=* && "$arg" != --proof-binding-sha256 && "$arg" != --proof-binding-sha256=* ]] || { echo "use V2_PROOF_FILE to stage the reviewed proof" >&2; exit 2; }
done
NODES="${NODES:-1}"
[[ "$NAME" == hrsft-* ]] || { echo "job name must start with hrsft-"; exit 2; }
MAX_TIMEOUT="${MAX_TIMEOUT:-10800}"  # raise explicitly (e.g. MAX_TIMEOUT=21600) for v0 runs
(( TIMEOUT <= MAX_TIMEOUT )) || { echo "timeout must be <= $MAX_TIMEOUT s (set MAX_TIMEOUT to raise)"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"; AZ="$(dirname "$HERE")"; HR="$(dirname "$AZ")"
HR_ROOT="$HR"; [ -f "$HR_ROOT/handoff/env.sh" ] && . "$HR_ROOT/handoff/env.sh"
HR_AZ_MSG="Azure settings missing: source handoff/env.sh or export the HR_AZ_* variables (README, Azure ML jobs)"
: "${HR_AZ_SUBSCRIPTION:?$HR_AZ_MSG}" "${HR_AZ_RG:?$HR_AZ_MSG}" "${HR_AZ_WS:?$HR_AZ_MSG}" "${HR_AZ_CLUSTER:?$HR_AZ_MSG}"
RG="${HR_AZ_RG}"; WS="${HR_AZ_WS}"
DATA_INPUT=""; DATA_CMD="--data data"; RUNNER="python azure/sft/run_sft.py"
if [[ ${DATA_URI+x} ]]; then
  prefix='azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/'
  [[ "$DATA_URI" =~ ^azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/[a-zA-Z0-9._~/-]+/$ ]] || { echo "invalid DATA_URI" >&2; exit 2; }
  suffix="${DATA_URI#"$prefix"}"
  [[ "$suffix" != *'//'* && "/$suffix" != *'/../'* && "/$suffix" != *'/./'* ]] || { echo "invalid DATA_URI path" >&2; exit 2; }
  for file in train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json; do
    [[ -f "$DATA/$file" && ! -L "$DATA/$file" ]] || { echo "required local data file missing or symlink: $file" >&2; exit 2; }
  done
  DATA_INPUT=$'  sft_data:\n    type: uri_folder\n    path: '"$DATA_URI"$'\n    mode: ro_mount'
  DATA_CMD='--data "${{inputs.sft_data}}"'
  RUNNER='bash azure/sft/verify_sft_data.sh "${{inputs.sft_data}}"'
fi
ENV_ID=/subscriptions/${HR_AZ_SUBSCRIPTION}/resourceGroups/$RG/providers/Microsoft.MachineLearningServices/workspaces/$WS/environments/pokemon-h100-axolotl-cuda13-training/versions/20261026-9f7aa9a5704c
SNAP=azureml://datastores/workspaceblobstore/paths/pokemon-rl-research/h100/model-snapshots/acquire-20261026-01/
if [[ ${V2_PROOF_FILE+x} ]]; then
  [[ -f "$V2_PROOF_FILE" && ! -L "$V2_PROOF_FILE" ]] || { echo "v2 proof file missing or symlink" >&2; exit 2; }
  V2_PRE_PROOF_SHA="$(shasum -a 256 "$V2_PROOF_FILE" | awk '{print $1}')"
  V2_PRE_RUNNER_SHA="$(shasum -a 256 "$HERE/run_sft.py" | awk '{print $1}')"
  V2_PRE_VALIDATOR_SHA="$(shasum -a 256 "$HERE/validate_v2_data.py" | awk '{print $1}')"
  V2_PRE_SERIALIZER_SHA="$(shasum -a 256 "$HR/tools/sft/serialize.py" | awk '{print $1}')"
  V2_PRE_FILES="$(cd "$DATA" && shasum -a 256 train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json)"
  V2_VALIDATION="$(python3 "$HERE/validate_v2_data.py" --data "$DATA" --proof "$V2_PROOF_FILE" --serializer "$HR/tools/sft/serialize.py")"
  [[ "$(shasum -a 256 "$V2_PROOF_FILE" | awk '{print $1}')" == "$V2_PRE_PROOF_SHA" ]] || { echo "v2 proof changed during validation" >&2; exit 2; }
  [[ "$(cd "$DATA" && shasum -a 256 train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json)" == "$V2_PRE_FILES" ]] || { echo "v2 data changed during validation" >&2; exit 2; }
fi
if [ "$RESUME_MODE" = 1 ]; then
  [[ "$NODES" = 12 && "${CKPT_ARG:-}" = --resume-from && -n "${CKPT_URI:-}" && -n "${DATA_URI:-}" && -n "${V2_PROOF_FILE:-}" ]] || { echo "reviewed DDP resume bindings missing" >&2; exit 2; }
  [[ -f "${RESUME_MARKER_FILE:-}" && ! -L "$RESUME_MARKER_FILE" && "$(shasum -a 256 "$RESUME_MARKER_FILE" | awk '{print $1}')" = ae466cac9617dfd8635835574ec9ce048316946bfebf916693a26abd1341a333 ]] || { echo "resume marker differs" >&2; exit 2; }
fi
A="$AZ/attempts/$NAME"
[ -e "$A" ] && { echo "attempt dir exists: $A"; exit 2; }
mkdir -p "$A/code/azure/sft" "$A/code/tools/sft"
cp "$HERE"/{run_sft.py,eval_sft.py,sft_plugin.py,qlora_sft.yaml} "$A/code/azure/sft/"
cp "$HR/tools/sft"/{serialize.py,glimmer_chat.py,build_sft.py,dex.json} "$A/code/tools/sft/"
PROOF_CMD=""
if [[ ${V2_PROOF_FILE+x} ]]; then
  [[ -f "$V2_PROOF_FILE" && ! -L "$V2_PROOF_FILE" ]] || { echo "v2 proof file missing or symlink" >&2; exit 2; }
  cp "$V2_PROOF_FILE" "$A/code/v2-proof.json"
  cp "$HERE/validate_v2_data.py" "$A/code/azure/sft/validate_v2_data.py"
  PROOF_SHA="$(shasum -a 256 "$A/code/v2-proof.json" | awk '{print $1}')"
  [[ "$PROOF_SHA" == "$V2_PRE_PROOF_SHA" ]] || { echo "staged v2 proof differs" >&2; exit 2; }
  [[ "$(shasum -a 256 "$A/code/azure/sft/run_sft.py" | awk '{print $1}')" == "$V2_PRE_RUNNER_SHA" && "$(shasum -a 256 "$A/code/azure/sft/validate_v2_data.py" | awk '{print $1}')" == "$V2_PRE_VALIDATOR_SHA" && "$(shasum -a 256 "$A/code/tools/sft/serialize.py" | awk '{print $1}')" == "$V2_PRE_SERIALIZER_SHA" ]] || { echo "v2 code changed during staging" >&2; exit 2; }
  printf '%s\n' "$V2_VALIDATION" > "$A/v2-validation.json"
  PROOF_CMD="--proof-binding v2-proof.json --proof-binding-sha256 $PROOF_SHA"
fi
if [[ ${DATA_URI+x} ]]; then
  cp "$HERE/verify_sft_data.sh" "$A/code/azure/sft/"
  (cd "$DATA" && shasum -a 256 train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json) > "$A/code/data.sha256"
else
  mkdir -p "$A/code/data"
  cp "$DATA"/{train.jsonl.gz,val.jsonl.gz,eval.jsonl.gz,manifest.json} "$A/code/data/"
fi
if [[ ${V2_PROOF_FILE+x} ]]; then
  if [[ ${DATA_URI+x} ]]; then
    [[ "$(cat "$A/code/data.sha256")" == "$V2_PRE_FILES" ]] || { echo "v2 staged data pins differ" >&2; exit 2; }
  else
    [[ "$(cd "$A/code/data" && shasum -a 256 train.jsonl.gz val.jsonl.gz eval.jsonl.gz manifest.json)" == "$V2_PRE_FILES" ]] || { echo "v2 copied data differs" >&2; exit 2; }
  fi
fi
RESUME_CMD=""
if [ "$RESUME_MODE" = 1 ]; then
  cp "$RESUME_MARKER_FILE" "$A/code/resume-persistence-marker.json"
  [[ "$(shasum -a 256 "$A/code/resume-persistence-marker.json" | awk '{print $1}')" = ae466cac9617dfd8635835574ec9ce048316946bfebf916693a26abd1341a333 ]] || { echo "staged resume marker differs" >&2; exit 2; }
  RESUME_CMD="--resume-marker resume-persistence-marker.json"
fi
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
command: $RUNNER --snapshot \${{inputs.model_snapshot}} $DATA_CMD --out \${{outputs.artifacts}} --job-timeout $TIMEOUT $CKPT_CMD $PROOF_CMD $RESUME_CMD $*
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
$DATA_INPUT
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
