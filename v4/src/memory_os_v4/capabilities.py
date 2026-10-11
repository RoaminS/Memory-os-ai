"""Deployed server facts, never claims about remote client installations."""

from __future__ import annotations

from . import __version__


def deployed(engine, build: dict) -> dict:
    from .brief_tokens import status

    cfg = engine.config
    return {
        "schema_version": 1,
        "engine": {"name": "memory-os-v4", "version": __version__, **build},
        "store": {
            "backend": cfg.store_backend,
            "instance_id": engine.store.meta.get("store_instance_id"),
            "atomic_mutations": cfg.store_backend == "sqlite_s1",
        },
        "encoding": {
            "model": cfg.embed_model,
            "requested_device": cfg.device,
            "profile": engine._compute_profile(),
        },
        "policies": {
            "maintenance": engine.maintenance_status(),
            "writes": engine.write_status(),
            "bm25_profile": cfg.bm25_profile,
            "lexical_guard_k": cfg.lexical_guard_k,
            "fusion": cfg.fusion,
            "request_receipts": "l3-v2",
            "automatic_mutation_replay": False,
        },
        "projects": {
            "registry_enabled": engine.projects.document is not None,
            "registry_sha256": engine.projects.sha256,
            "unknown_labels": "refused" if engine.projects.document is not None else "literal",
            "automatic_merge": False,
        },
        "brief": {
            "default_mode": "compact",
            "compact": True,
            "max_envelope_bytes": 6000,
            "max_envelope_tokens": 1500,
            "tokenizer": status(),
            "detailed": True,
            "expansion": True,
        },
        "client_relays": "unknown_to_server",
        "work_state": False,
        "block_cas": False,
    }


def readiness_snapshot(engine, build):
    from .readiness import engine_readiness

    if engine is None:
        return False, "engine_not_loaded", None
    if not engine._lock.acquire(blocking=False):
        return False, "engine_busy", None
    try:
        ok, reason = engine_readiness(engine)
        return ok, reason, deployed(engine, build) if ok else None
    finally:
        engine._lock.release()
