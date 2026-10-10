#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
exec nice -n 15 "$task_python" -m bench.e2e.mps_reuse_campaign "$@"
