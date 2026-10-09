#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
mkdir -p .local-qualification
task_operation="${1:?prepare-dev, run-dev, prepare-s or run-s required}"
task_revision="${2:?full 40-character source commit required}"
task_dev="${3:-.local-qualification/lexical-c4-dev-v1}"
task_s="${4:-.local-qualification/lexical-c4-s-v1}"
task_previous="${5:-.local-qualification/lexical-dev-v1}"
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
case "$task_operation" in
  prepare-dev)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_c4 prepare-dev \
      --previous "$task_previous" --output "$task_dev" --source-commit "$task_revision" ;;
  run-dev)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_c4 run-dev \
      --plan-dir "$task_dev" --source-commit "$task_revision" ;;
  prepare-s)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_c4 prepare-s \
      --plan-dir "$task_dev" --output "$task_s" --source-commit "$task_revision" ;;
  run-s)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_c4 run-s \
      --plan-dir "$task_s" --source-commit "$task_revision" ;;
  *) echo "Unknown operation: $task_operation" >&2; exit 2 ;;
esac
