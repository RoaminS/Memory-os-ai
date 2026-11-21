"""Installed-only worker; engine opens private copies, never the live SQLite store."""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
from pathlib import Path
import socket
import sys
import time


def authority(con):
    from schema8_inventory import inventory
    from qualify_schema8_copy import engine_authority

    value = inventory(con)
    meta = json.loads(dict(value["tables"]["store_metadata"])["metadata"])
    return {
        "schema_version": value["schema"],
        "sha256": engine_authority(value),
        "store_instance_id": meta["store_instance_id"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--snapshot-source", type=Path)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    root = args.plan.parent.resolve()
    if (
        plan["format"] != "sqlite-release-409-v1"
        or not (root / "copy-owner.json").is_file()
    ):
        raise ValueError("Owned private plan required")
    # Use the sealed guard as standalone tooling; never change the baseline package.
    from qualification_guard import guard
    from schema8_inventory import inventory
    from schema8_unchanged import verify_unchanged
    from contextlib import closing

    guard(root)
    import memory_os_v4

    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError("Checkout/foreign package forbidden")
    from memory_os_v4.ops import sqlite_backup as s4
    from schema8_inventory import readonly
    from qualification_limits import configured

    if args.snapshot_source:
        if args.kind != "snapshot" or md.version("memory-os-ai") != plan["version"]:
            raise ValueError(
                "Snapshot requires installed candidate with qualified capacity support"
            )
        from memory_os_v4.readiness import build_identity

        if build_identity()["commit"] != plan["source_commit"]:
            raise ValueError("Snapshot candidate build mismatch")
        logical = s4.snapshot(
            args.snapshot_source / s4.DB, root / "snapshot.sqlite3", lambda: guard(root)
        )
        (root / "snapshot.json").write_text(json.dumps(logical, indent=2) + "\n")
        return
    name = args.kind
    if name not in {"baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"}:
        raise ValueError("Unknown private worker")
    store = root / (name.removesuffix("-warm") + "-store")
    if not store.is_relative_to(root) or store.is_symlink():
        raise ValueError("Private store only")

    configured().check((store / "store.sqlite3").stat().st_size, "database")

    # Weights already cached. Any IP request is a qualification failure.
    def denied(*a, **kw):
        raise RuntimeError("IP network forbidden in copy worker")

    socket.create_connection = denied
    socket.socket.connect = denied
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.embed import Embedder, _resolve_device
    from memory_os_v4.readiness import build_identity

    version = "4.0.7" if name == "baseline" else plan["version"]
    if (
        memory_os_v4.__version__ != md.version("memory-os-ai")
        or md.version("memory-os-ai") != version
    ):
        raise ValueError("Wrong installed version")
    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError("Checkout/foreign package forbidden")
    if name != "baseline":
        from memory_os_v4.ops.qualified_runtime import inspect_current

        if not inspect_current(extras=("ops", "brief"))["ok"]:
            raise ValueError("Installed runtime drift")
        identity = build_identity()
        if (
            identity["identity_source"] != "embedded_descriptor"
            or identity["commit"] != plan["source_commit"]
        ):
            raise ValueError("Installed payload/descriptor mismatch")
    cfg = Config.from_env()
    cfg.data_dir, cfg.store_backend, cfg.work_state, cfg.write_mode = (
        str(store),
        "sqlite_s1",
        True,
        "read_only",
    )
    if (
        cfg.project_registry
        or cfg.bm25_profile != "scoped_literal"
        or cfg.lexical_guard_k != 3
    ):
        raise ValueError("Production C4/registry policy mismatch")
    if cfg.embed_model == "hash" or _resolve_device(cfg.device) != "mps":
        raise ValueError("Real production model/MPS required")
    con = readonly(store / s4.DB)
    try:
        before = authority(con)
    finally:
        con.close()
    with closing(readonly(store / s4.DB)) as con:
        before_inventory = inventory(con)
    # Explicit same-schema open, not an upgrade; compare EVERY SQL row/object.
    from memory_os_v4.sqlite_store import SQLiteStore

    opened = time.monotonic()
    with closing(SQLiteStore(store)) as checked:
        verify_unchanged(before_inventory, inventory(checked._connection))
    open_seconds = time.monotonic() - opened
    encoded = []
    original = Embedder.encode

    def counted(self, texts):
        guard(root)
        encoded.extend(texts)
        value = original(self, texts)
        guard(root)
        return value

    Embedder.encode = counted
    started = time.monotonic()
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()  # Same path as the installed --warm HTTP server.
        startup_seconds, corpus_encoded_texts = time.monotonic() - started, len(encoded)
        engine.store.touch = lambda *a, **kw: None  # Disposable diagnostic only.
        rows = {}
        for query in plan["queries"]:
            guard(root)
            t = time.monotonic()
            hits = engine.search(
                query, top_k=10, project=None, at_time=plan["at_time"], rerank=False
            )
            rows[query] = {
                "ids": [h.item.id for h in hits],
                "scores": [h.score for h in hits],
                "seconds": time.monotonic() - t,
            }
        after = authority(engine.store._connection)
        if before != after:
            raise ValueError("Authoritative history/receipts/tasks/identity changed")
        inv = inventory(engine.store._connection)
        schema_check = verify_unchanged(before_inventory, inv)
        journal = dict(inv["tables"]["store_metadata"]).get("change_journal")
        journal = json.loads(journal) if journal else None
        report = {
            "migration": None,
            "schema_open_seconds": open_seconds,
            "schema_check": schema_check,
            "journal": {
                "generation": journal["generation"],
                "coverage_start": journal["coverage_start"],
                "events": len(inv["tables"]["change_events"]),
            }
            if journal
            else None,
            "ok": True,
            "version": version,
            "authority": after,
            "search": rows,
            "search_hit_counts": {q: len(h["ids"]) for q, h in rows.items()},
            "store_instance_id": engine.store.meta["store_instance_id"],
            "encoding_profile": engine.status()["encoding_profile"],
            "startup_seconds": startup_seconds,
            "corpus_encoded_texts": corpus_encoded_texts,
            "cache_hits": engine._embedding_cache.hits,
            "cache_misses": engine._embedding_cache.misses,
            "total": engine.status()["total"],
            "access_tracking_disabled_on_copy_only": True,
        }
    (root / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
