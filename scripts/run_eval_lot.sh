#!/bin/sh
# One guarded campaign; NEVER downloads, binds TCP, submits or touches live stores.
set -eu
if [ "$#" -lt 2 ]; then
  echo "Usage: $0 SOURCE_COMMIT OUTPUT_BASENAME [bench.e2e.run options]" >&2
  exit 2
fi
task_source_commit=$1
task_output_name=$2
shift 2
case "$task_output_name" in
  ''|*[!a-zA-Z0-9_-]*) echo 'OUTPUT_BASENAME must contain only letters, digits, _ or -' >&2; exit 2 ;;
esac
task_clone=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
task_python=${EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}
task_model=${EVAL_MODEL:-/Users/romain/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41}
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_clone/v4:$task_clone/v4/src"
export PYTHONOPTIMIZE=0 PYTHONNOUSERSITE=1 TOKENIZERS_PARALLELISM=false
# The Python guard checks nice effectiveness too (nice itself can warn and
# still launch an unreniced child when sandbox setpriority is denied).
exec nice -n 15 "$task_python" -m bench.e2e.run \
  --data /Users/romain/Memory-os-ai/v4/bench/data/longmemeval_oracle.json \
  --variant oracle --model "$task_model" --limit 500 \
  --source-commit "$task_source_commit" \
  --output "$task_clone/.local-qualification/$task_output_name" "$@"
