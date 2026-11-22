"""Installed-only 4.1.0 worker; engine opens private copies, never the live SQLite store.

Kinds: ``snapshot`` and the five semantic workers (G9, as in 4.0.9), ``lexical-refusal``
(G9 addition: the lexical profile is refused on the semantic copy), ``lexical-store`` and
``semantic-refusal`` (G11: brand-new lexical store on FIXED data and queries).
Heavy imports stay inside ``main`` so the fixed literals can be hashed without the engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

PLAN_FORMAT = "sqlite-release-410-v1"
SEMANTIC_WORKERS = ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm")
# G11: FIXED data and queries, versioned here as literals. Never generated, never sampled.
LEXICAL_PROJECT = "qualify-410-lexical"
LEXICAL_EVENT_TIME = 1795000000.0
LEXICAL_AT_TIME = 1795000600.0
LEXICAL_FACTS = (
    "The harbour ferry Mistral leaves the north quay at 07:40 on weekdays.",
    "Camille Rousseau repairs accordion bellows in the workshop on Rue des Tanneurs.",
    "The 2026 lighthouse survey measured the Phare du Cap lantern at 42 metres.",
    "Invoice QL-410-0007 covers twelve oak barrels delivered to the Verdier cellar.",
    "The orchard irrigation valve V3 was replaced with a brass coupling in March.",
    "Lucie Ferrand teaches celestial navigation with a sextant every Thursday evening.",
    "The archive room keeps the parish registers of Saint-Aubin in drawer C14.",
    "A red kite nested above the limestone quarry near the old tram depot.",
    "The bakery on Place Carnot sells buckwheat sourdough only on Saturdays.",
    "Backup tape LTO-0410 holds the cartography scans of the estuary dredging plan.",
    "The glassblower Henri Marchand signs each carafe with a cobalt blue thread.",
    "Tide table revision R9 corrects the spring tide height at the Mistral north quay.",
)
LEXICAL_QUERIES = (
    "ferry Mistral north quay departure",
    "accordion bellows repair workshop",
    "lighthouse lantern height survey",
    "oak barrels invoice Verdier cellar",
    "irrigation valve brass coupling",
    "celestial navigation sextant Thursday",
    "parish registers Saint-Aubin drawer",
    "buckwheat sourdough Saturday bakery",
    "backup tape cartography estuary dredging",
    "spring tide height revision",
)


def fixed_data_sha256():
    value = {"project": LEXICAL_PROJECT, "event_time": LEXICAL_EVENT_TIME, "at_time": LEXICAL_AT_TIME,
             "facts": list(LEXICAL_FACTS), "queries": list(LEXICAL_QUERIES)}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


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


def installed_candidate(plan, profile):
    """This interpreter runs the installed 4.1.0 payload of the sealed commit, never the checkout."""
    import importlib.metadata as md
    import memory_os_v4
    from memory_os_v4.ops.qualified_runtime import inspect_current
    from memory_os_v4.readiness import build_identity

    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError("Checkout/foreign package forbidden")
    if memory_os_v4.__version__ != md.version("memory-os-ai") or md.version("memory-os-ai") != plan["version"]:
        raise ValueError("Wrong installed version")
    if not inspect_current(extras=("ops", "brief"), profile=profile)["ok"]:
        raise ValueError("Installed runtime drift")
    identity = build_identity()
    if identity["identity_source"] != "embedded_descriptor" or identity["commit"] != plan["source_commit"]:
        raise ValueError("Installed payload/descriptor mismatch")
    return str(Path(memory_os_v4.__file__).resolve())


def refusal(root, name, store, requested):
    """Open ``store`` in the opposite profile: it must be refused by ``store_profile_mismatch``."""
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.config import ProfileError

    if requested == "lexical":
        cfg = Config.lexical(data_dir=str(store))
    else:
        cfg = Config.from_env()
        cfg.data_dir, cfg.store_backend = str(store), "sqlite_s1"
        if cfg.retrieval_profile != "semantic":
            raise ValueError("Semantic profile expected for the opposite open")
    report = {"requested": requested, "refused": False, "code": None}
    try:
        with MemoryV4(config=cfg):
            report["error"] = "store opened in the opposite profile"
    except ProfileError as exc:
        report.update(refused=True, code=exc.code)
    (root / (name + ".json")).write_text(json.dumps(report, indent=2) + "\n")


def lexical_store(plan, root, guard):
    """G11: write, search, close, reopen, back up, restore; identical rankings at the three moments."""
    import importlib.util as util
    import os
    from contextlib import closing
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.ops import sqlite_backup as s4
    from memory_os_v4.ops.backup import load_or_create_key
    from memory_os_v4.ops.settings import OpsSettings
    from memory_os_v4.sqlite_store import SQLiteStore

    if plan["lexical"]["fixed_data_sha256"] != fixed_data_sha256():
        raise ValueError("Fixed data differ from the sealed literals")
    package = installed_candidate(plan, "lexical")
    spec = {name: (None if util.find_spec(name) is None else "present")
            for name in ("torch", "sentence_transformers", "transformers", "faiss")}
    if any(spec.values()):
        raise ValueError("ML package present in the lexical environment")
    store = root / "lexical-store"
    if store.exists():
        raise ValueError("Brand-new lexical store required")

    def rankings(engine):
        rows = {}
        for query in LEXICAL_QUERIES:
            guard(root)
            hits = engine.search(query, top_k=10, project=LEXICAL_PROJECT, at_time=LEXICAL_AT_TIME, rerank=False)
            rows[query] = [hit.item.text for hit in hits]
        return rows

    report = {"package_path": package, "find_spec": spec, "rankings": {}}
    with MemoryV4(config=Config.lexical(data_dir=str(store))) as engine:
        added = engine.add_facts([{"text": text} for text in LEXICAL_FACTS], project=LEXICAL_PROJECT,
                                 event_time=LEXICAL_EVENT_TIME)
        report["added"] = len(added["added"])
        report["rankings"]["after_write"] = rankings(engine)
    with MemoryV4(config=Config.lexical(data_dir=str(store))) as engine:
        report["rankings"]["after_reopen"] = rankings(engine)
        report["marker"] = engine.store.meta.get("retrieval_profile")
        report["store_instance_id"] = engine.store.meta["store_instance_id"]
        report["capabilities_marker"] = engine.status().get("retrieval_profile")
    if os.environ.get("MEMORY_V4_DIR") != str(store):
        raise ValueError("Backup settings must target the private lexical store")
    settings = OpsSettings.from_env()
    load_or_create_key(settings.backup_key)
    # Online backup, writer open, as S4 does in production.
    with closing(SQLiteStore(store)):
        backup = s4.backup(settings, apply=True)
        if not backup.get("ok"):
            raise RuntimeError(backup)
        drill = s4.restore_drill(settings, archive=settings.backup_dir / backup["archive"], apply=True)
    report["backup"], report["restore"] = backup, drill
    if not drill.get("ok"):
        raise RuntimeError(drill)
    restored = root / "lexical-restored"
    s4.verify_archive(settings.backup_dir / backup["archive"], s4.load_key(settings.backup_key), restored,
                      lambda: guard(root))
    with MemoryV4(config=Config.lexical(data_dir=str(restored), write_mode="read_only")) as engine:
        report["rankings"]["after_restore"] = rankings(engine)
        report["restored_marker"] = engine.store.meta.get("retrieval_profile")
    report["ok"] = True
    (root / "lexical-store.json").write_text(json.dumps(report, indent=2) + "\n")


def main():
    import importlib.metadata as md
    import socket

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--snapshot-source", type=Path)
    args = p.parse_args()
    plan = json.loads(args.plan.read_text())
    root = args.plan.parent.resolve()
    if plan["format"] != PLAN_FORMAT or not (root / "copy-owner.json").is_file():
        raise ValueError("Owned private plan required")
    # Use the sealed guard as standalone tooling; never change the baseline package.
    from qualification_guard import guard

    guard(root)
    name = args.kind

    # No download in any worker: any IP request is a qualification failure.
    def denied(*a, **kw):
        raise RuntimeError("IP network forbidden in qualification worker")

    if name == "lexical-store":
        socket.create_connection = denied
        socket.socket.connect = denied
        return lexical_store(plan, root, guard)
    if name == "semantic-refusal":
        socket.create_connection = denied
        socket.socket.connect = denied
        installed_candidate(plan, "lexical")
        return refusal(root, name, root / "lexical-store", "semantic")
    if name == "lexical-refusal":
        socket.create_connection = denied
        socket.socket.connect = denied
        installed_candidate(plan, "semantic")
        store = root / "lexical-refusal-store"
        if not store.is_relative_to(root) or store.is_symlink():
            raise ValueError("Private store only")
        return refusal(root, name, store, "lexical")

    from schema8_inventory import inventory
    from schema8_unchanged import verify_unchanged
    from contextlib import closing
    import memory_os_v4

    if Path(sys.executable).parent.parent not in Path(memory_os_v4.__file__).parents:
        raise ValueError("Checkout/foreign package forbidden")
    from memory_os_v4.ops import sqlite_backup as s4
    from schema8_inventory import readonly
    from qualification_limits import configured

    if args.snapshot_source:
        if name != "snapshot" or md.version("memory-os-ai") != plan["version"]:
            raise ValueError("Snapshot requires installed candidate with qualified capacity support")
        from memory_os_v4.readiness import build_identity

        if build_identity()["commit"] != plan["source_commit"]:
            raise ValueError("Snapshot candidate build mismatch")
        # Cut through the candidate's backup API; the source is only read.
        logical = s4.snapshot(args.snapshot_source / s4.DB, root / "snapshot.sqlite3", lambda: guard(root))
        (root / "snapshot.json").write_text(json.dumps(logical, indent=2) + "\n")
        return
    if name not in SEMANTIC_WORKERS:
        raise ValueError("Unknown private worker")
    store = root / (name.removesuffix("-warm") + "-store")
    if not store.is_relative_to(root) or store.is_symlink():
        raise ValueError("Private store only")

    configured().check((store / "store.sqlite3").stat().st_size, "database")
    socket.create_connection = denied
    socket.socket.connect = denied
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.embed import Embedder, _resolve_device

    version = "4.0.7" if name == "baseline" else plan["version"]
    if memory_os_v4.__version__ != md.version("memory-os-ai") or md.version("memory-os-ai") != version:
        raise ValueError("Wrong installed version")
    if name != "baseline":
        installed_candidate(plan, "semantic")
    cfg = Config.from_env()
    cfg.data_dir, cfg.store_backend, cfg.work_state, cfg.write_mode = (str(store), "sqlite_s1", True, "read_only")
    if cfg.project_registry or cfg.bm25_profile != "scoped_literal" or cfg.lexical_guard_k != 3:
        raise ValueError("Production C4/registry policy mismatch")
    if cfg.embed_model == "hash" or _resolve_device(cfg.device) != "mps":
        raise ValueError("Real production model/MPS required")
    # The 4.0.7 baseline has no profile field: its absence means semantic.
    retrieval_profile = getattr(cfg, "retrieval_profile", None)
    if name != "baseline" and retrieval_profile != "semantic":
        raise ValueError("Semantic profile required on the production copy")
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
            hits = engine.search(query, top_k=10, project=None, at_time=plan["at_time"], rerank=False)
            rows[query] = {"ids": [h.item.id for h in hits], "scores": [h.score for h in hits],
                           "seconds": time.monotonic() - t}
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
            "journal": {"generation": journal["generation"], "coverage_start": journal["coverage_start"],
                        "events": len(inv["tables"]["change_events"])} if journal else None,
            "ok": True,
            "version": version,
            "retrieval_profile": retrieval_profile,
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
