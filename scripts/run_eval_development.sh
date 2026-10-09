#!/bin/sh
# Three explicit stages; no network, no live store, no test partition.
set -eu
if [ "$#" -ne 3 ]; then
  echo "Usage: $0 SOURCE_COMMIT OUTPUT_BASENAME diagnose|prepare|run" >&2
  exit 2
fi
task_revision=$1
task_name=$2
task_stage=$3
case "$task_name" in
  ''|*[!a-zA-Z0-9_-]*) echo 'Invalid output basename' >&2; exit 2 ;;
esac
task_clone=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
task_python=${EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_clone/v4:$task_clone/v4/src"
export PYTHONOPTIMIZE=0 PYTHONNOUSERSITE=1 TOKENIZERS_PARALLELISM=false
case "$task_stage" in
  diagnose)
    exec nice -n 15 "$task_python" -m bench.e2e.diagnose \
      --source-commit "$task_revision" \
      --campaign "$task_clone/.local-qualification/eval-oracle-500-v1" \
      --output "$task_clone/.local-qualification/$task_name-diagnosis" ;;
  prepare)
    exec nice -n 15 "$task_python" -m bench.e2e.experiments prepare \
      --data /Users/romain/Memory-os-ai/v4/bench/data/longmemeval_oracle.json \
      --source-commit "$task_revision" --output "$task_clone/.local-qualification/$task_name" ;;
  run)
    exec nice -n 15 "$task_python" -m bench.e2e.experiments run \
      --plan-dir "$task_clone/.local-qualification/$task_name" ;;
  *) echo 'Unknown stage' >&2; exit 2 ;;
esac
