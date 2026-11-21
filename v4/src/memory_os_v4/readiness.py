"""Read-only readiness: strict source checks, loaded model and index coverage."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess


def build_identity(*, cache_for=0.0) -> dict:
    from . import __version__

    result = {"version": __version__, "commit": None, "commit_source": "unknown", "source_dirty": None,
              "identity_source": "unqualified"}
    configured = os.environ.get("MEMORY_V4_BUILD_COMMIT")
    if configured and re.fullmatch(r"[0-9a-f]{40}", configured) is None:
        raise ValueError("MEMORY_V4_BUILD_COMMIT must be a complete lowercase Git SHA")
    root = Path(__file__).resolve().parents[3]
    source_layout = (Path(__file__).resolve() == root / "v4/src/memory_os_v4/readiness.py"
                     and (root / "pyproject.toml").is_file())
    if not source_layout:
        from .build_descriptor import verify
        checked = verify(Path(__file__).resolve().parent.parent, __version__, max_age=cache_for)
        if not checked["ok"]:
            return {**result, "identity_error": checked["error"]}
        if configured and configured != checked["commit"]:
            raise ValueError("MEMORY_V4_BUILD_COMMIT contradicts the embedded descriptor; remove the old declaration")
        return {**result, "commit": checked["commit"], "source_dirty": False,
                "commit_source": "embedded_descriptor", "identity_source": "embedded_descriptor",
                "descriptor_sha256": checked["descriptor_sha256"], "payload_sha256": checked["payload_sha256"]}
    # Explicit source/archive operator mode remains declarative, never embedded.
    if configured:
        return {**result, "commit": configured, "commit_source": "deployment_manifest", "identity_source": "env_declared"}
    if not (root / ".git").exists():
        return result
    try:
        commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                         stderr=subprocess.DEVNULL, text=True, timeout=2).strip()
        dirty = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--",
                                "v4/src", "src", "pyproject.toml"], timeout=2,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if re.fullmatch(r"[0-9a-f]{40}", commit) and dirty in {0, 1}:
            return {**result, "commit": commit, "commit_source": "checkout", "source_dirty": bool(dirty),
                    "identity_source": "git_checkout" if not dirty else "unqualified"}
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def _jsonl(path: Path) -> list[dict]:
    data = path.read_bytes() if path.exists() else b""
    if data and not data.endswith(b"\n"):
        raise ValueError("Truncated source")
    return [json.loads(line) for line in data.splitlines() if line.strip()]


def engine_readiness(engine) -> tuple[bool, str]:
    if engine is None:
        return False, "engine_not_loaded"
    if not engine._lock.acquire(blocking=False):
        return False, "engine_busy"
    try:
        engine._ensure_open()
        if engine._writer_lock._file is None:
            return False, "writer_lease_missing"
        # Lexical profile: no encoder by choice, never a missing model; retrieval is ready at open.
        lexical = engine.config.retrieval_profile == "lexical"
        if (not lexical and engine._retrieval_ready and engine.config.embed_model != "hash"
                and getattr(engine.embedder, "_model", None) is None):
            return False, "model_not_loaded"
        if engine.config.store_backend == "sqlite_s1":
            # The engine lock serializes semantic writers. Never serve a cached
            # success from an open transaction or a poisoned/closed authority.
            if engine.store._connection.in_transaction:
                return False, "store_transaction_active"
            from .read_audit import ReadAudit, file_stamp
            audit = getattr(engine, '_ready_integrity_audit', None)
            if audit is None:
                audit = engine._ready_integrity_audit = ReadAudit()
            def stamp():
                store = engine.store
                return (store._readiness_generation, store._connection.total_changes,
                        store._connection.execute('PRAGMA data_version').fetchone()[0],
                        *(file_stamp(Path(str(store.db_path)+suffix)) for suffix in ('', '-wal', '-shm')))
            def check():
                if not engine.store.check_integrity():
                    return False, "store_integrity"
                if not engine.blocks.check_integrity():
                    return False, "blocks_integrity"
                if not engine.requests.check_integrity():
                    return False, "requests_integrity"
                return True, "ready"
            valid, reason = audit.run(stamp, check, lambda r: r[0] is True)
            if not valid:
                return False, reason
        else:
            if not engine.store.check_integrity():
                return False, "store_integrity"
            sources_ok, sources_reason = _jsonl_sources_ready(engine)
            if not sources_ok:
                return False, sources_reason
        if not engine._retrieval_ready:
            if engine.config.store_backend == 'sqlite_s1' and engine.config.work_state:
                return True, 'ready_work_only'
            return False, 'model_not_loaded'
        live_ids = {item.id for item in engine.store.all(False)}
        # No vector coverage in the lexical profile; BM25/entity/facet coverage is checked alike.
        indexed = live_ids if lexical else {
            engine.store.chunk_owner.get(key, key) for key in engine.vector_index.ids()}
        if (indexed != live_ids or set(engine.bm25.doc_len) != live_ids
                or set(engine.entities.doc_entities) != live_ids or engine.facet_index.ids() != live_ids):
            return False, "indexes_not_ready"
        return True, "ready"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError, sqlite3.Error):
        return False, "integrity_check_failed"
    finally:
        engine._lock.release()


def _jsonl_sources_ready(engine):
    blocks = {}
    for record in _jsonl(engine.blocks.path):
        scope = record.get("scope", "project")
        project = None if scope == "global" else record.get("project", "default")
        if (scope not in {"global", "project"} or not isinstance(record["name"], str)
                or not isinstance(record["text"], str)
                or (scope == "project" and (not isinstance(project, str) or not project or project == "*"))):
            return False, "blocks_integrity"
        blocks[(project, record["name"])] = record
    if set(blocks) != set(engine.blocks._blocks):
        return False, "blocks_integrity"
    for key, record in blocks.items():
        block = engine.blocks._blocks[key]
        if block.text != record["text"] or block.updated_at != record["updated_at"]:
            return False, "blocks_integrity"
    receipts = {}
    for record in _jsonl(engine.requests.path):
        receipts[(record["scope"], record["request_id"])] = record
    if engine.requests._poisoned or receipts != engine.requests.records:
        return False, "requests_integrity"
    return True, "ready"
