"""S2: real engine transactions, disposable stores, no neural encoders/network."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4 import server
from memory_os_v4.idempotency import RequestConflictError
from memory_os_v4.readiness import engine_readiness
from memory_os_v4.sqlite_store import APPLICATION_ID, MIN_SQLITE, SCHEMA, SCHEMA_VERSION, schema_checksums

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="Unqualified SQLite runtime")

IDENTITY = {"subject_id": "synthetic:user", "predicate": "city", "cardinality": "single"}
PAYLOAD = {"synthetic": "episodes + correction + forget + blocks + counters"}


def open_engine(directory):
    return MemoryV4(
        config=Config(maintenance_mode="apply", data_dir=str(directory), store_backend="sqlite_s1", embed_model="hash", hash_dim=16)
    )


def seed(engine):
    def apply():
        engine.add_episode("An acknowledged episode", project="A", item_id="saved")
        engine.add_episode("Obsolete episode", project="A", item_id="obsolete")
        engine.add_facts(
            [{"item_id": "old", "text": "Synthetic user lives in Paris", "identity": IDENTITY}],
            project="A",
            event_time=100,
        )
        engine.blocks.set("project_state", "Acknowledged block", project="A")
        return {"acknowledged": ["saved", "obsolete", "old"]}

    return engine.execute_request("seed", "A", {}, "acknowledged", apply)


def batch(engine):
    def apply():
        engine.add_episodes_bulk(
            [
                {"text": "First batch episode", "project": "A", "item_id": "ep1"},
                {"text": "Second batch episode", "project": "A", "item_id": "ep2"},
            ]
        )
        engine.add_facts(
            [
                {"item_id": "new", "text": "Synthetic user lives in Lyon", "identity": IDENTITY},
                {"item_id": "independent", "text": "Synthetic project uses SQLite"},
            ],
            project="A",
            event_time=200,
        )
        engine.forget("obsolete", project="A")
        engine.blocks.append("project_state", "Committed batch", project="A")
        engine.blocks.set("working_rules", "Shared synthetic rule", scope="global")
        engine.store.touch("saved", t=300)
        engine.store.meta["batch_marker"] = "all-or-none"
        engine.flush()
        return {"ok": True, "new": ["ep1", "ep2", "new", "independent"]}

    return engine.execute_request("synthetic_batch", "A", PAYLOAD, "batch", apply)


def source_rows(engine):
    # A logical snapshot catches modifications to old history/provenance, not just counts.
    return {
        table: engine.store._connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
        for table in ("items", "item_versions", "block_versions", "request_receipts", "store_metadata", "receipt_counter")
    }


def full_batch_present(engine):
    assert engine.store.get("old").invalidated_by == "new"
    assert engine.store.get("old").valid_until == 200
    assert engine.store.get("obsolete").deleted
    assert engine.store.get("saved").access_count == 1
    assert engine.store.get("saved").last_accessed == 300
    assert all(engine.store.get(item_id) for item_id in ("ep1", "ep2", "new", "independent"))
    assert engine.blocks.get("project_state", project="A") == "Acknowledged block\nCommitted batch"
    assert engine.blocks.get("working_rules", scope="global") == "Shared synthetic rule"
    assert len(engine.blocks.history("project_state", project="A")) == 2
    assert engine.store.meta["batch_marker"] == "all-or-none"
    assert engine.requests.records[("A", "batch")]["result"]["ok"] is True
    assert engine_readiness(engine) == (True, "ready")


@pytest.fixture
def engine(tmp_path):
    value = open_engine(tmp_path)
    try:
        yield value
    finally:
        value.store.fault_hook = None
        try:
            value.close()
        except RuntimeError:
            # An ambiguous commit deliberately prevents close's final flush.
            assert value._closed


def test_composite_batch_one_begin_one_commit_final_receipt_and_replay(engine, tmp_path):
    seed(engine)
    sql, boundaries = [], []
    engine.store._connection.set_trace_callback(sql.append)
    engine.store.fault_hook = boundaries.append
    result = batch(engine)
    engine.store.fault_hook = None
    engine.store._connection.set_trace_callback(None)
    assert sql.count("BEGIN IMMEDIATE") == 1 and sql.count("COMMIT") == 1
    assert boundaries[-4:] == ["before_receipt", "after_receipt", "before_commit", "after_commit"]
    full_batch_present(engine)
    before = source_rows(engine)
    assert batch(engine) == result and source_rows(engine) == before
    with pytest.raises(RequestConflictError):
        engine.execute_request("different", "A", PAYLOAD, "batch", lambda: None)
    assert source_rows(engine) == before
    assert not any(
        (tmp_path / name).exists()
        for name in (
            "items.jsonl",
            "blocks.jsonl",
            "requests.jsonl",
            "meta.json",
            "embeddings.npy",
            "ids.json",
            "bm25.json",
            "entities.json",
        )
    )
    engine.close()
    with open_engine(tmp_path) as reopened:
        full_batch_present(reopened)
        assert batch(reopened) == result


def test_no_id_mutations_have_final_receipts_and_block_scopes(engine):
    engine.add_episode("Direct episode", item_id="direct")
    engine.blocks.set("working_rules", "global", scope="global")
    engine.blocks.set("working_rules", "private", project="A")
    engine.blocks.append("working_rules", "more", project="A")
    assert len(engine.requests.records) == 4
    assert {value["state"] for value in engine.requests.records.values()} == {"complete"}
    assert engine.blocks.get("working_rules", project="A") == "private\nmore"
    assert engine.blocks.get("working_rules", project="B") == "global"


def test_payload_fingerprint_is_frozen_before_action(engine):
    payload = {"nested": {"text": "original"}}

    def apply():
        payload["nested"]["text"] = "changed by action"
        engine.add_episode("Atomic payload", item_id="frozen")
        return {"ok": True}

    assert engine.execute_request("freeze", "A", payload, "frozen", apply) == {"ok": True}
    assert engine.execute_request(
        "freeze",
        "A",
        {"nested": {"text": "original"}},
        "frozen",
        lambda: pytest.fail("Replay must not execute the action"),
    ) == {"ok": True}
    with pytest.raises(RequestConflictError):
        engine.execute_request("freeze", "A", payload, "frozen", lambda: None)


def test_real_chat_save_handler_rolls_back_episodes_on_late_fact_failure(engine, monkeypatch):
    monkeypatch.setattr(server, "_engine", engine)
    trace = []
    engine.store._connection.set_trace_callback(trace.append)
    result = server.dispatch(
        "memory_chat_save",
        {
            "project_label": "A",
            "request_id": "handler-batch",
            "messages": [{"role": "user", "content": "Must not be stored partially"}],
            "facts": [{"text": "First valid fact"}, {"text": "Bad correction", "replaces": ["absent"]}],
        },
    )
    assert result["ok"] is False and "error" in result
    assert "replacement target" in result["error"]
    assert any(statement.startswith("INSERT INTO items ") for statement in trace)
    assert trace.count("ROLLBACK") == 1
    engine.store._connection.set_trace_callback(None)
    assert not engine.store.items and not engine.requests.records
    assert engine_readiness(engine) == (True, "ready")


def test_document_and_v3_import_each_have_one_engine_transaction(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_WORKSPACE", str(tmp_path))
    log = tmp_path / "v3.jsonl"
    log.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-21T17:54:00+00:00",
                "project": "A",
                "summary": "Synthetic summary",
                "messages": [{"role": "user", "content": "Synthetic migrated episode"}],
            }
        )
        + "\n"
    )
    for action in (
        lambda: engine.add_document("synthetic.txt", "Synthetic document " * 100, project="A"),
        lambda: engine.migrate_v3(str(log)),
        lambda: engine.consolidate(dry_run=False, project="A"),
    ):
        trace = []
        engine.store._connection.set_trace_callback(trace.append)
        count = len(engine.requests.records)
        action()
        engine.store._connection.set_trace_callback(None)
        assert trace.count("BEGIN IMMEDIATE") == 1 and trace.count("COMMIT") == 1
        assert len(engine.requests.records) == count + 1
        assert engine_readiness(engine) == (True, "ready")


def test_ambiguous_commit_requires_reopen_then_explicit_replay(engine, tmp_path):
    original = engine.store._connection

    class LostAcknowledgement:
        def __getattr__(self, name):
            return getattr(original, name)

        def execute(self, sql, *args):
            result = original.execute(sql, *args)
            if sql == "COMMIT":
                raise sqlite3.OperationalError("injected acknowledgement lost")
            return result

    engine.store._connection = LostAcknowledgement()
    with pytest.raises(sqlite3.OperationalError, match="acknowledgement lost"):
        seed(engine)
    assert not engine_readiness(engine)[0]
    with pytest.raises(RuntimeError, match="uncertain"):
        seed(engine)
    with pytest.raises(RuntimeError, match="uncertain"):
        engine.close()
    with open_engine(tmp_path) as reopened:
        count = len(reopened.store.items)
        assert seed(reopened) == {"acknowledged": ["saved", "obsolete", "old"]}
        assert len(reopened.store.items) == count == 3
        assert engine_readiness(reopened) == (True, "ready")


def test_failed_rollback_disables_engine_until_reopen(engine, tmp_path):
    seed(engine)
    original = engine.store._connection

    class FailedRollback:
        def __getattr__(self, name):
            return getattr(original, name)

        def execute(self, sql, *args):
            if sql == "ROLLBACK":
                raise sqlite3.OperationalError("injected rollback failure")
            return original.execute(sql, *args)

    engine.store._connection = FailedRollback()

    def broken():
        engine.add_episode("Uncommitted", item_id="partial")
        raise ValueError("abort")

    with pytest.raises(sqlite3.OperationalError, match="rollback failure"):
        engine.execute_request("broken", "A", {}, "broken", broken)
    assert engine.store._poisoned
    assert not engine_readiness(engine)[0]
    with pytest.raises(RuntimeError, match="uncertain"):
        engine.close()
    with open_engine(tmp_path) as reopened:
        assert reopened.store.get("partial") is None
        assert ("A", "broken") not in reopened.requests.records
        assert reopened.requests.records[("A", "acknowledged")]["state"] == "complete"
        assert engine_readiness(reopened) == (True, "ready")


def test_handler_cannot_catch_nested_failure_and_commit_partial_batch(engine):
    def broken():
        engine.add_episode("Must roll back", item_id="partial")
        try:
            engine.add_facts([{"text": "Invalid replacement", "replaces": ["absent"]}])
        except ValueError:
            pass
        return {"ok": True}

    with pytest.raises(RuntimeError, match="whole request"):
        engine.execute_request("broken", "A", {}, "broken", broken)
    assert not engine.store.items and not engine.requests.records
    assert engine_readiness(engine) == (True, "ready")


def test_late_fact_validation_rolls_back_first_fact_and_indexes(engine):
    before = source_rows(engine)
    with pytest.raises(ValueError):
        engine.add_facts([{"text": "First valid fact"}, {"text": "Bad correction", "replaces": ["absent"]}])
    assert source_rows(engine) == before
    assert engine_readiness(engine) == (True, "ready")
    assert not engine.search("First valid fact")


def test_rebuild_ignores_corrupt_disk_caches_and_restores_retriever(engine, tmp_path):
    seed(engine)
    engine.close()
    for name in ("bm25.json", "entities.json", "ids.json", "embeddings.npy", "chunk_map.json"):
        (tmp_path / name).write_bytes(b"not a cache")
    with open_engine(tmp_path) as reopened:
        assert engine_readiness(reopened) == (True, "ready")
        assert reopened.search("acknowledged episode", project="A")


def test_unrebuildable_rollback_disables_engine(engine, monkeypatch):
    seed(engine)

    def fail():
        raise RuntimeError("injected index failure")

    monkeypatch.setattr(engine, "_rebuild_sql_indexes", fail)
    with pytest.raises(ValueError):
        engine.execute_request("bad", "A", {}, "bad", lambda: (_ for _ in ()).throw(ValueError("fail")))
    assert engine_readiness(engine) == (False, "integrity_check_failed")
    with pytest.raises(RuntimeError, match="index rebuild"):
        engine.add_episode("Must not be accepted")


def test_s1_schema_upgrade_preserves_records_and_refuses_nonempty_jsonl(tmp_path):
    directory = tmp_path / "old"
    directory.mkdir()
    conn = sqlite3.connect(directory / "store.sqlite3", isolation_level=None)
    for statement in SCHEMA:
        conn.execute(statement)
    conn.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (*schema_checksums()[0], 1))
    conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO store_metadata VALUES('metadata',?)", ('{"provenance":"S1"}',))
    conn.close()
    (directory / "blocks.jsonl").write_text('{"text":"do not lose"}\n')
    before = (directory / "store.sqlite3").read_bytes()
    with pytest.raises(ValueError, match="offline migration"):
        open_engine(directory)
    assert (directory / "store.sqlite3").read_bytes() == before
    (directory / "blocks.jsonl").unlink()
    with open_engine(directory) as reopened:
        assert reopened.store.meta["provenance"] == "S1"
        assert reopened.store._connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "table,trigger",
    [("block_versions", "block_versions_no_update"), ("request_receipts", "receipts_no_update")],
)
def test_source_hashes_and_immutable_history(engine, table, trigger):
    seed(engine)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        engine.store._connection.execute(f"UPDATE {table} SET record_json='{{}}'")
    engine.store._connection.execute(f"DROP TRIGGER {trigger}")
    engine.store._connection.execute(f"UPDATE {table} SET record_json='{{}}'")
    assert not engine.store.check_integrity()


def make_template(directory):
    with open_engine(directory) as value:
        result = seed(value)
    # An independent fsync'ed acknowledgement witness, never inferred from the DB.
    witness = directory / "acknowledgements.jsonl"
    with witness.open("w") as fh:
        fh.write(json.dumps({"request_id": "acknowledged", "result": result}) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def assert_acknowledgement_preserved(engine, directory):
    witness = json.loads((directory / "acknowledgements.jsonl").read_text())
    assert engine.requests.records[("A", witness["request_id"])]["result"] == witness["result"]
    assert all(engine.store.get(item_id) for item_id in witness["result"]["acknowledged"])
    assert engine.store.history("old")[0].text == "Synthetic user lives in Paris"
    assert engine.blocks.history("project_state", project="A")[0].text == "Acknowledged block"


def boundary_list(template, trace_dir):
    shutil.copytree(template, trace_dir)
    with open_engine(trace_dir) as value:
        boundaries = []
        value.store.fault_hook = boundaries.append
        batch(value)
        value.store.fault_hook = None
    return boundaries


def test_exception_at_every_write_boundary_rolls_back_or_is_uncertain(tmp_path):
    template = tmp_path / "template"
    make_template(template)
    boundaries = boundary_list(template, tmp_path / "trace")
    assert len(boundaries) >= 40
    for ordinal, label in enumerate(boundaries):
        directory = tmp_path / f"exception-{ordinal}"
        shutil.copytree(template, directory)
        value = open_engine(directory)
        before = source_rows(value)
        seen = []

        def inject(point):
            seen.append(point)
            if len(seen) == ordinal + 1:
                raise OSError(f"injected {ordinal}:{label}")

        value.store.fault_hook = inject
        with pytest.raises(OSError, match="injected"):
            batch(value)
        value.store.fault_hook = None
        if label != "after_commit":
            assert source_rows(value) == before, (ordinal, label)
            assert engine_readiness(value) == (True, "ready")
        else:
            assert value.store._poisoned
            assert not engine_readiness(value)[0]
            with pytest.raises(RuntimeError, match="uncertain"):
                batch(value)
        # Simulate reopening without introducing a close/flush receipt.
        value.store.close()
        value._closed = True
        value._writer_finalizer()
        with open_engine(directory) as reopened:
            assert_acknowledgement_preserved(reopened, directory)
            if label == "after_commit":
                full_batch_present(reopened)
            else:
                assert source_rows(reopened) == before
                assert batch(reopened)["ok"] is True  # No effects + no receipt => safe retry after rollback.
    print(f"S2 exceptions: {len(boundaries)} boundaries, zero partial successes")


def test_sigkill_at_every_write_boundary_acknowledged_writes_survive(tmp_path):
    template = tmp_path / "template"
    make_template(template)
    boundaries = boundary_list(template, tmp_path / "trace")
    source = str(Path(__file__).resolve().parents[1] / "src")
    tests = str(Path(__file__).resolve().parent)
    code = """
import json, os, signal, sys
from test_sqlite_engine import open_engine, batch
value = open_engine(sys.argv[1])
target = int(sys.argv[2])
seen = 0
def kill(point):
    global seen
    if seen == target:
        os.kill(os.getpid(), signal.SIGKILL)
    seen += 1
value.store.fault_hook = kill
result = batch(value)
with open(os.path.join(sys.argv[1], 'batch-acknowledged.json'), 'w') as fh:
    fh.write(json.dumps(result)); fh.flush(); os.fsync(fh.fileno())
os.kill(os.getpid(), signal.SIGKILL)
"""
    for ordinal, label in enumerate([*boundaries, "after_acknowledgement"]):
        directory = tmp_path / f"kill-{ordinal}"
        shutil.copytree(template, directory)
        with open_engine(directory) as value:
            before = source_rows(value)
        # Snapshot was taken before close; drop its extra internal flush receipt
        # by copying the original template back (no active owner now).
        shutil.rmtree(directory)
        shutil.copytree(template, directory)
        result = subprocess.run(
            [sys.executable, "-c", code, str(directory), str(ordinal)],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join((source, tests)),
                "PYTHONDONTWRITEBYTECODE": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "OMP_NUM_THREADS": "1",
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == -signal.SIGKILL, (ordinal, label, result.stderr)
        witness = directory / "batch-acknowledged.json"
        assert witness.exists() == (label == "after_acknowledgement"), (ordinal, label)
        with open_engine(directory) as reopened:
            assert_acknowledgement_preserved(reopened, directory)
            if label in {"after_commit", "after_acknowledgement"}:
                full_batch_present(reopened)
                assert batch(reopened)["ok"] is True  # Replay, never duplicate effects.
                if witness.exists():
                    assert (
                        json.loads(witness.read_text()) == reopened.requests.records[("A", "batch")]["result"]
                    )
            else:
                assert source_rows(reopened) == before, (ordinal, label)
                assert ("A", "batch") not in reopened.requests.records
                assert engine_readiness(reopened) == (True, "ready")
    print(
        f"S2 SIGKILL: {len(boundaries)} write boundaries + post-acknowledgement, zero acknowledged losses, zero partial successes"
    )
