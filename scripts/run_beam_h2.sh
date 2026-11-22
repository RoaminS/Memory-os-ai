#!/bin/bash
# H2 lexical/semantic parity on BEAM 100K (manifest h2-lexical-parity-v1); see v4/docs/H2_RUN_v1.md.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/.memory-os-runtime/venv-4.0.8/bin/python}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONNOUSERSITE=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
exec nice -n 15 "$task_python" -B -m bench.e2e.beam_h2 "$@"
