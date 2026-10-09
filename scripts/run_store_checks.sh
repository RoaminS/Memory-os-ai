#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
mkdir -p .local-qualification
export MEMORY_WORKSPACE="${MEMORY_WORKSPACE:-$task_root}"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH="$task_root/v4/src:$task_root/v4"
task_python="${MEMORY_STORE_TEST_PYTHON:-/Users/romain/Memory-os-ai/.venv/bin/python}"
"$task_python" -m pytest -q -s -p no:cacheprovider --basetemp=.local-qualification/store-checks \
  v4/tests/test_sqlite_store.py v4/tests/test_sqlite_engine.py v4/tests/test_sqlite_cache.py \
  v4/tests/test_sqlite_retention.py v4/tests/test_sqlite_migration.py v4/tests/test_sqlite_lifecycle.py \
  v4/tests/test_ops.py v4/tests/test_writer_idempotency.py v4/tests/test_readiness.py \
  v4/tests/test_core_blocks.py v4/tests/test_core_memory.py v4/tests/test_scope_and_fact_updates.py \
  v4/tests/test_core_migrate.py v4/tests/test_server_handlers.py \
  v4/tests/test_incident_002_dedup.py v4/tests/test_server_project_scope.py
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
ruff check --no-cache --config pyproject.toml src tests scripts
