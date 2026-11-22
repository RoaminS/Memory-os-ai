#!/bin/bash
# H2 lexical/semantic parity on BEAM 100K (manifest h2-lexical-parity-v1); see v4/docs/H2_RUN_v3.md.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
task_python="${MEMORY_EVAL_PYTHON:-/Users/romain/.memory-os-runtime/venv-4.0.8/bin/python}"
# Nothing inherited may redirect imports or libraries of the SUPERVISOR either.
for name in $(compgen -e); do
  case "$name" in PYTHON*|DYLD_*|LD_*) unset "$name" ;; esac
done
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONNOUSERSITE=1 PYTHONSAFEPATH=1 PYTHONOPTIMIZE=0
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
# Before the first import: a fresh, private, EMPTY bytecode cache prefix. With it CPython never reads a
# __pycache__ next to a source file, and -B never writes: every module is compiled from its attested
# source. The supervisor verifies this isolation at start and before scoring, and refuses otherwise.
pycache="$(mktemp -d "${TMPDIR:-/tmp}/h2-pycache.XXXXXXXX")"
export PYTHONPYCACHEPREFIX="$pycache"
status=0
nice -n 15 "$task_python" -P -B -m bench.e2e.beam_h2 "$@" || status=$?
rmdir "$pycache" 2>/dev/null || true  # only succeeds when still empty
exit "$status"
