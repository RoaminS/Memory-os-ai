"""Deployed server facts, never claims about remote client installations."""

from __future__ import annotations

from . import __version__


def deployed(engine, build: dict) -> dict:
    from .brief_tokens import status
    from .ops.qualified_runtime import inspect_current
    from .config import inert_cos_settings
    from .fact_updates import mutation_policy

    cfg = engine.config
    return {
        "schema_version": 1,
        "runtime": inspect_current(extras=('ops', 'brief')),
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
            "loaded": engine._retrieval_ready,
            "profile_source": "loaded_encoder" if engine._retrieval_ready else "historical_store_metadata",
        },
        "policies": {
            "maintenance": engine.maintenance_status(),
            "writes": engine.write_status(),
            "write_mode": cfg.write_mode,
            "bm25_profile": cfg.bm25_profile,
            "lexical_guard_k": cfg.lexical_guard_k,
            "fusion": cfg.fusion,
            "request_receipts": "l3-v2",
            "automatic_mutation_replay": False,
            "mutations": mutation_policy(),
            "deprecated_inert_settings": inert_cos_settings(),
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
            "wire_formats": ["legacy", "compact_v2"],
            "negotiation": "tool_input_schema",
            "compact_budget_tokens_range": [200, 1500],
            "whole_units": True,
            "value_ranking": "brief-v5-pa-v1",
            "caller_identity": "relay_clientInfo_declarative",
            "coverage": "scoped_candidates_v1",
            "unit_layout": "body_spans_v1",
            "initial_section_shares": {"blocks": .30, "tasks": .20},
            "candidate_limits": {"memory": 40, "blocks": 16, "tasks": 10},
            "max_envelope_bytes": 6000,
            "max_envelope_tokens": 1500,
            "tokenizer": status(),
            "detailed": True,
            "expansion": True,
        },
        "fact_corrections": {
            "contract": "b2-v1", "controlled": cfg.store_backend == 'sqlite_s1',
            "fast_apply": "one_explicit_impact_with_revision_reason_request_id",
            "legacy": "correction_unverified", "legacy_max_impacts": 3,
            "caller_identity": "declarative_not_authenticated",
        },
        "change_journal": {"available": cfg.store_backend == "sqlite_s1", "coverage": "prospective", "automatic_purge": False, "alert_bytes": 10*1024*1024},
        "retrieval_ready": engine._retrieval_ready,
        "client_relays": "unknown_to_server",
        "work_state": cfg.store_backend == 'sqlite_s1' and cfg.work_state,
        "block_cas": cfg.store_backend == 'sqlite_s1' and cfg.work_state,
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
