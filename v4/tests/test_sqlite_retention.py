"""Bounded implicit receipts, permanent explicit replay, read-only counters."""
import json
import sqlite3

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops.cli import main
from memory_os_v4.ops.health import scan_store
from memory_os_v4.sqlite_store import MIN_SQLITE

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="SQLite runtime too old")


def test_implicit_receipts_bounded_without_results_and_explicit_replay_retained(tmp_path, monkeypatch, capsys):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash")) as engine:
        engine.requests.implicit_limit = 3
        action = lambda: {"large": "x" * 10000}
        expected = engine.execute_request("explicit", "A", {}, "keep", action)
        for _ in range(7):
            engine.execute_request("implicit", "A", {}, None, action)
        explicit = [r for r in engine.requests.records.values() if r.get("explicit", True)]
        implicit = [r for r in engine.requests.records.values() if not r.get("explicit", True)]
        assert len(explicit) == 1 and len(implicit) == 3
        assert all("result" not in r for r in implicit)
        assert engine.execute_request("explicit", "A", {}, "keep", lambda: pytest.fail("must replay")) == expected
        with pytest.raises(sqlite3.IntegrityError, match="immutable explicit"):
            engine.store._connection.execute("DELETE FROM request_receipts WHERE request_id='keep'")
        stats = engine.status()["receipts"]
        assert stats["explicit"] == 1 and stats["implicit"] == 3 and stats["json_bytes"] > 10000
        maximum = engine.store._connection.execute("SELECT max(sequence) FROM request_receipts").fetchone()[0]
        assert maximum == 8 and engine.store.check_integrity()
        assert scan_store(tmp_path)["receipts"]["json_bytes"] == stats["json_bytes"]
        monkeypatch.setenv("MEMORY_V4_DIR", str(tmp_path))
        assert main(["doctor"]) == 0
        assert json.loads(capsys.readouterr().out)["receipts"]["explicit"] == 1


def test_retention_is_rolled_back_with_failed_final_receipt(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend="sqlite_s1", embed_model="hash")) as engine:
        engine.requests.implicit_limit = 2
        for _ in range(2):
            engine.flush()
        before = dict(engine.requests.records)
        def fail(point):
            if point == "before_receipt":
                raise OSError("injected final receipt failure")
        engine.store.fault_hook = fail
        with pytest.raises(OSError):
            engine.flush()
        engine.store.fault_hook = None
        assert engine.requests.records == before
        assert engine.requests.check_integrity()
        assert engine.store._connection.execute("SELECT value FROM receipt_counter").fetchone()[0] == 2


def test_doctor_refuses_invalid_database_without_traceback(tmp_path, monkeypatch, capsys):
    (tmp_path / "store.sqlite3").write_bytes(b"bad database")
    monkeypatch.setenv("MEMORY_V4_DIR", str(tmp_path))
    assert main(["doctor"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
def test_metadata_version_tracks_package_version(tmp_path, monkeypatch, backend):
    import memory_os_v4
    monkeypatch.setattr(memory_os_v4, "__version__", "4.9.99-test")
    config = Config(data_dir=str(tmp_path), store_backend=backend, embed_model="hash")
    with MemoryV4(config=config) as engine:
        engine.add_episode("Synthetic versioned episode")
        engine.flush()
        assert engine.store.meta["version"] == memory_os_v4.__version__
    with MemoryV4(config=config) as reopened:
        assert reopened.store.meta["version"] == memory_os_v4.__version__
