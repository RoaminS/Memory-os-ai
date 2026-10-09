#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
mkdir -p .local-qualification
task_operation="${1:?prepare, diagnose or run required}"
task_revision="${2:?full 40-character source commit required}"
task_plan="${3:-.local-qualification/lexical-dev-v1}"
task_previous="${4:-.local-qualification/eval-resident-dev-v2}"
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
case "$task_operation" in
  prepare)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_dev prepare \
      --previous "$task_previous" --output "$task_plan" --source-commit "$task_revision" ;;
  diagnose|run)
    exec nice -n 15 "$task_python" -m bench.e2e.lexical_dev "$task_operation" \
      --plan-dir "$task_plan" --source-commit "$task_revision" ;;
  *) echo "Unknown operation: $task_operation" >&2; exit 2 ;;
esac
