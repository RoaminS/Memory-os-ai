#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
mkdir -p .local-qualification
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
exec nice -n 15 "$task_python" -m bench.e2e.lexical_s_v2 "${1:?prepare or run required}" \
  --source-commit "${2:?full source commit required}" \
  --dev-dir "${3:-.local-qualification/lexical-c4-dev-v1}" \
  --plan-dir "${4:-.local-qualification/lexical-c4-s-v2}"
