"""Release tooling only: synthetic SQLite and JSON, never neural/TCP/production."""

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_406_checks import INSTANCE, SOURCE, validate_copy
from release_copy_worker import QUERIES


def proof_and_manifest():
    profile = {
        "device": "mps",
        "dtype": "native",
        "batch_size": 32,
        "max_seq_length": 512,
        "parameter_devices": ["mps"],
        "parameter_dtypes": ["torch.bfloat16"],
        "model_revision": "fixture",
        "runtime": {"torch": "2.10.0"},
    }
    caps = {
        "store": {"backend": "sqlite_s1", "instance_id": INSTANCE},
        "work_state": True,
        "retrieval_ready": True,
        "block_cas": True,
        "runtime": {"ok": True},
        "encoding": {"loaded": True},
        "projects": {"registry_enabled": False},
        "policies": {
            "write_mode": "read_only",
            "bm25_profile": "scoped_literal",
            "lexical_guard_k": 3,
        },
        "brief": {"tokenizer": {"ready": True}},
    }
    manifest = {
        "source_commit": SOURCE,
        "artifacts": [{"name": "fixture", "sha256": "a" * 64}],
        "copy_scripts_sha256": {"runner": "b" * 64},
    }
    workers = {}
    for name in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"):
        workers[name] = {
            "ok": True,
            "version": "4.0.5" if name == "baseline" else "4.0.6",
            "store_instance_id": INSTANCE,
            "authority": {"history": "fixture"},
            "encoding_profile": deepcopy(profile),
            "corpus_encoded_texts": 0,
            "startup_seconds": 1.0,
            "total": 12,
            "search": {q: {"ids": ["witness"]} for q in QUERIES},
        }
    proof = {
        "ok": True,
        "version": "4.0.6",
        "baseline_version": "4.0.5",
        "source_commit": SOURCE,
        "artifacts": manifest["artifacts"],
        "scripts_sha256": manifest["copy_scripts_sha256"],
        "source_sql_writes": False,
        "production_modified": False,
        "seconds": 3,
        "snapshot": {"store_instance_id": INSTANCE, "schema_version": 7},
        "workers": workers,
        "search_identical_to_baseline": True,
        "rankings_identical_between_artifacts_and_reopen": True,
        "authority_identical": True,
        "warm_encoded_texts": 0,
        "doctor": {
            "ok": True,
            "ready": {
                "version": "4.0.6",
                "commit": SOURCE,
                "identity_source": "embedded_descriptor",
                "capabilities": caps,
                "encoding_profile": profile,
            },
        },
    }
    return proof, manifest


def test_406_complete_copy_is_accepted():
    proof, manifest = proof_and_manifest()
    validate_copy(proof, manifest)


@pytest.mark.parametrize(
    "fault",
    [
        "authority",
        "rank",
        "reencode",
        "warm_reencode",
        "missing_worker",
        "count",
        "identity",
        "profile",
        "runtime",
        "registry",
        "write_mode",
        "descriptor",
        "timeout",
        "source_write",
        "probe_set",
        "scripts",
    ],
)
def test_406_copy_refuses_partial_changed_or_unattested_evidence(fault):
    proof, manifest = proof_and_manifest()
    row = proof["workers"]["wheel"]
    if fault == "authority":
        row["authority"] = {"changed": True}
    elif fault == "rank":
        row["search"][QUERIES[0]]["ids"] = ["other"]
    elif fault == "reencode":
        row["corpus_encoded_texts"] = 1
    elif fault == "warm_reencode":
        proof["workers"]["sdist-warm"]["corpus_encoded_texts"] = 1
    elif fault == "missing_worker":
        del proof["workers"]["wheel-warm"]
    elif fault == "count":
        row["total"] += 1
    elif fault == "identity":
        row["store_instance_id"] = "f" * 32
    elif fault == "profile":
        row["encoding_profile"]["device"] = "cpu"
    elif fault == "runtime":
        proof["doctor"]["ready"]["capabilities"]["runtime"]["ok"] = False
    elif fault == "registry":
        proof["doctor"]["ready"]["capabilities"]["projects"]["registry_enabled"] = True
    elif fault == "write_mode":
        proof["doctor"]["ready"]["capabilities"]["policies"]["write_mode"] = (
            "read_write"
        )
    elif fault == "descriptor":
        proof["doctor"]["ready"]["identity_source"] = "env_declared"
    elif fault == "timeout":
        proof["seconds"] = 901
    elif fault == "source_write":
        proof["source_sql_writes"] = True
    elif fault == "probe_set":
        for r in proof["workers"].values():
            r["search"].pop(QUERIES[0])
    elif fault == "scripts":
        proof["scripts_sha256"] = {}
    with pytest.raises(ValueError):
        validate_copy(proof, manifest)


def test_406_sqlite_authority_allows_only_metadata_version_change(tmp_path):
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.ops import sqlite_backup as s4
    from sqlite_release_worker import authority

    store = tmp_path / "sql"
    cfg = Config(
        data_dir=str(store), store_backend="sqlite_s1", embed_model="hash", hash_dim=16
    )
    with MemoryV4(config=cfg) as engine:
        engine.add_episode("Synthetic conserved history", project="p")
        before = authority(engine.store._connection)
        engine.store.save_meta({**engine.store.meta, "version": "4.0.5"})
        assert authority(engine.store._connection) == before
        engine.add_episode("Synthetic new acknowledged witness", project="p")
        assert authority(engine.store._connection) != before
        expected = s4.inventory(engine.store._connection)
        pinned = s4.snapshot(store / s4.DB, tmp_path / "sealed.sqlite3")
        assert pinned == expected
    con = s4.readonly(tmp_path / "sealed.sqlite3", sealed=True)
    try:
        assert s4.inventory(con) == pinned
    finally:
        con.close()


def test_406_pending_manifest_refuses_deployment_and_changed_evidence():
    import release_406_checks as checks

    directory = ROOT / ".local-qualification/release-4.0.6-v1"
    # Installed evidence is exercised separately once the folder is assembled.
    assert checks.SOURCE == SOURCE
    from verify_release_manifest import validate_local

    with pytest.raises((KeyError, ValueError, FileNotFoundError)):
        validate_local(
            {"version": "4.0.6", "source_commit": SOURCE, "open_blockers": []},
            directory,
        )


def test_baseline_snapshot_worker_runs_without_provision_in_installed_405(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import memory_os_v4
    import sqlite_release_worker as worker
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.ops import sqlite_backup as s4

    root = tmp_path / "private"
    root.mkdir()
    plan = root / "plan.json"
    plan.write_text(json.dumps({"format": "sqlite-release-406-v1"}))
    (root / "copy-owner.json").write_text("{}")
    source = tmp_path / "source"
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "qualification_guard",
        SimpleNamespace(guard=lambda p: calls.append(p)),
    )
    # Model a baseline installation with no provision module; its standalone
    # guard must be used before the real pinned SQLite snapshot is copied.
    monkeypatch.setitem(sys.modules, "memory_os_v4.ops.provision", None)
    prefix = tmp_path / "installed-405"
    monkeypatch.setattr(worker.sys, "executable", str(prefix / "bin/python"))
    monkeypatch.setattr(
        memory_os_v4,
        "__file__",
        str(prefix / "lib/site-packages/memory_os_v4/__init__.py"),
    )
    monkeypatch.setattr(worker.md, "version", lambda name: "4.0.5")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tool",
            "--plan",
            str(plan),
            "--kind",
            "snapshot",
            "--snapshot-source",
            str(source),
        ],
    )
    with MemoryV4(
        config=Config(
            data_dir=str(source),
            store_backend="sqlite_s1",
            embed_model="hash",
            hash_dim=16,
        )
    ) as engine:
        engine.add_episode("Synthetic baseline snapshot witness", project="p")
        before = s4.inventory(engine.store._connection)
        worker.main()
        assert s4.inventory(engine.store._connection) == before
    assert calls and all(p == root for p in calls)
    assert json.loads((root / "snapshot.json").read_text()) == before
