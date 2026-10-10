#!/bin/bash
set -euo pipefail
campaign_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$campaign_root"
: "${MEMORY_EVAL_PYTHON:?Set the attested 4.0.3 runtime Python path explicitly}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export PYTHONPATH="$campaign_root/v4/src:$campaign_root/v4"
exec nice -n 15 "$MEMORY_EVAL_PYTHON" -m bench.e2e.answer_campaign "$@"
