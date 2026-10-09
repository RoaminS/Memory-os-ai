#!/bin/sh
# Fixed, offline, sequential amendment and one candidate confirmation.
set -eu
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 SOURCE_COMMIT prepare-dev|run-dev|prepare-s|run-s" >&2
  exit 2
fi
task_revision=$1
task_stage=$2
task_clone=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
task_python=${EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_clone/v4:$task_clone/v4/src"
export PYTHONOPTIMIZE=0 PYTHONNOUSERSITE=1 TOKENIZERS_PARALLELISM=false
case "$task_stage" in
  prepare-dev)
    exec nice -n 15 "$task_python" -m bench.e2e.amendment prepare-dev --source-commit "$task_revision" \
      --previous "$task_clone/.local-qualification/eval-dev-fusion-v1" \
      --output "$task_clone/.local-qualification/eval-resident-dev-v2" ;;
  run-dev)
    exec nice -n 15 "$task_python" -m bench.e2e.amendment run-dev --source-commit "$task_revision" \
      --plan-dir "$task_clone/.local-qualification/eval-resident-dev-v2" ;;
  prepare-s)
    exec nice -n 15 "$task_python" -m bench.e2e.amendment prepare-s --source-commit "$task_revision" \
      --plan-dir "$task_clone/.local-qualification/eval-resident-dev-v2" \
      --output "$task_clone/.local-qualification/eval-confirmation-s-v1" ;;
  run-s)
    exec nice -n 15 "$task_python" -m bench.e2e.amendment run-s --source-commit "$task_revision" \
      --plan-dir "$task_clone/.local-qualification/eval-confirmation-s-v1" ;;
  *) echo 'Unknown stage' >&2; exit 2 ;;
esac
