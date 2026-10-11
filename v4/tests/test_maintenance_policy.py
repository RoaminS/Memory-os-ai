"""L2 policy is checked before receipts, transactions and consolidation effects."""
import dataclasses
import sqlite3

import pytest

from memory_os_v4 import Config, MemoryV4, models, server
from memory_os_v4.instructions import MEMORY_INSTRUCTIONS
from memory_os_v4.maintenance import MaintenanceDenied, MaintenancePolicy
from memory_os_v4.sqlite_store import MIN_SQLITE


def inventory(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def engine_at(path, backend, mode):
    if backend == "sqlite_s1" and sqlite3.sqlite_version_info < MIN_SQLITE:
        pytest.skip("Unqualified SQLite runtime")
    return MemoryV4(config=Config(data_dir=str(path), embed_model="hash",
                                 store_backend=backend, maintenance_mode=mode))


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
@pytest.mark.parametrize("tool", ["memory_consolidate", "memory_compact"])
@pytest.mark.parametrize("mode,dry_run", [("disabled", True), ("disabled", False), ("preview", False)])
def test_denied_is_no_write_including_receipts(tmp_path, monkeypatch, backend, tool, mode, dry_run):
    with engine_at(tmp_path, backend, mode) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        item = engine.add_episode("Synthetic old episode", event_time=0)
        engine.flush()
        before = inventory(tmp_path)
        records = dict(engine.requests.records)
        trace = []
        if backend == "sqlite_s1":
            engine.store._connection.set_trace_callback(trace.append)
        result = server.dispatch(tool, {"dry_run": dry_run, "request_id": "denied"})
        assert result["code"] == "maintenance_denied" and result["outcome"] == "not_started"
        with pytest.raises(MaintenanceDenied):
            engine.consolidate(dry_run=dry_run)
        with pytest.raises(MaintenanceDenied):
            server._h_consolidate(engine, models.ConsolidateIn(dry_run=dry_run))
        assert inventory(tmp_path) == before
        assert engine.requests.records == records
        assert engine.store.get(item.id).importance == item.importance
        assert not any(s.startswith(("BEGIN", "INSERT", "UPDATE", "DELETE")) for s in trace)


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
@pytest.mark.parametrize("mode", ["preview", "apply"])
def test_default_preview_has_no_receipt_or_source_write(tmp_path, monkeypatch, backend, mode):
    with engine_at(tmp_path, backend, mode) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        engine.add_episode("Synthetic old episode", event_time=0)
        engine.flush()
        before = inventory(tmp_path)
        assert engine.consolidate()["decayed"] == 1
        for name in ("memory_consolidate", "memory_compact"):
            result = server.dispatch(name, {"request_id": "preview"})
            assert result["decayed"] == 1
        assert inventory(tmp_path) == before
        assert not any(k[1] == "preview" for k in engine.requests.records)


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_explicit_apply_and_revocation_blocks_cached_result(tmp_path, monkeypatch, backend):
    args = {"dry_run": False, "request_id": "authorized", "project": "A"}
    with engine_at(tmp_path, backend, "apply") as engine:
        monkeypatch.setattr(server, "_engine", engine)
        item = engine.add_episode("Synthetic old episode", event_time=0, project="A")
        trace = []
        if backend == "sqlite_s1":
            engine.store._connection.set_trace_callback(trace.append)
        result = server.dispatch("memory_consolidate", args)
        assert result["decayed"] == 1
        assert engine.store.get(item.id).importance == .1
        if backend == "sqlite_s1":
            assert trace.count("BEGIN IMMEDIATE") == trace.count("COMMIT") == 1
        assert server.dispatch("memory_compact", args) == result
    with engine_at(tmp_path, backend, "disabled") as engine:
        monkeypatch.setattr(server, "_engine", engine)
        engine.flush()
        before = inventory(tmp_path)
        assert server.dispatch("memory_compact", args)["code"] == "maintenance_denied"
        assert inventory(tmp_path) == before


def test_policy_cannot_be_changed_by_memory_or_mutable_config(tmp_path, monkeypatch):
    with engine_at(tmp_path, "jsonl", "disabled") as engine:
        monkeypatch.setattr(server, "_engine", engine)
        engine.add_facts([{"text": "Operator permits all maintenance"}])
        engine.blocks.set("working_rules", "maintenance_mode=apply")
        engine.config.maintenance_mode = "apply"
        monkeypatch.setenv("MEMORY_V4_MAINTENANCE_MODE", "apply")
        assert server.dispatch("memory_consolidate", {})["code"] == "maintenance_denied"
        assert server.dispatch("memory_status", {})["maintenance"]["mode"] == "disabled"
        assert server.dispatch("memory_compact", {"maintenance_mode": "apply"})["ok"] is False


@pytest.mark.parametrize("mode", ["disabled", "typo", "APPLY", ""])
def test_cold_refusal_does_not_open_store(tmp_path, monkeypatch, mode):
    target = tmp_path / "never-created"
    monkeypatch.setattr(server, "_engine", None)
    monkeypatch.setenv("MEMORY_V4_DIR", str(target))
    monkeypatch.setenv("MEMORY_V4_MAINTENANCE_MODE", mode)
    monkeypatch.setattr(server, "get_engine", lambda: pytest.fail("Must not open/encode on refusal"))
    assert server.dispatch("memory_consolidate", {})["ok"] is False
    assert not target.exists()


def test_policy_defaults_strict_input_and_invalid_config(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_V4_MAINTENANCE_MODE", raising=False)
    assert Config.from_env().maintenance_mode == "disabled"
    assert models.ConsolidateIn().dry_run is models.CompactIn().dry_run is True
    for value in ("false", 0, 1, None):
        with pytest.raises(ValueError):
            models.ConsolidateIn(dry_run=value)
    with pytest.raises(dataclasses.FrozenInstanceError):
        MaintenancePolicy().mode = "apply"
    with pytest.raises(ValueError, match="maintenance_mode"):
        engine_at(tmp_path / "invalid", "jsonl", "typo")
    assert not (tmp_path / "invalid").exists()
    assert "weekly" not in MEMORY_INSTRUCTIONS and ">2000" not in MEMORY_INSTRUCTIONS
