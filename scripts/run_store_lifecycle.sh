#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
task_python="${MEMORY_STORE_TEST_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
exec nice -n 15 "$task_python" -m bench.sqlite_lifecycle "$@"
