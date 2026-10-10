#!/bin/bash
set -euo pipefail
pilot_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$pilot_root"
pilot_python="${MEMORY_EVAL_PYTHON:-/Users/romain/.memory-os-runtime/venv-4.0.2/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$pilot_root/v4/src:$pilot_root/v4"
exec nice -n 15 "$pilot_python" -m bench.e2e.answer_pilot "$@"
