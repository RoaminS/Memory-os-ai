"""Offline S3 on synthetic copies only; byte equality, no models or TCP."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys

import pytest

from memory_os_v4 import Config, MemoryItem, MemoryV4
from memory_os_v4.idempotency import RequestIncompleteError
from memory_os_v4.sqlite_engine import fingerprint
from memory_os_v4.sqlite_migrate import _files, _publish, export, main, migrate
from memory_os_v4.sqlite_store import MIN_SQLITE, SQLiteStore

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason="SQLite runtime too old")


def write_log(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def source_fixture(directory):
    directory.mkdir()
    (directory / "writer.lock").write_bytes(b"")
    old = asdict(
        MemoryItem(
            id="old",
            kind="fact",
            text="Synthetic user lives in Paris",
            project="A",
            source="client:synthetic",
            created_at=100,
            event_time=100,
            parent_ids=["episode"],
        )
    )
    old["future_field"] = {"provenance": ["keep", "unchanged"]}
    corrected = {**old, "valid_until": 200, "invalidated_by": "new"}
    new = asdict(
        MemoryItem(
            id="new",
            kind="fact",
            text="Synthetic user lives in Lyon",
            project="A",
            created_at=200,
            event_time=200,
            replaces=["old"],
        )
    )
    episode = asdict(
        MemoryItem(
            id="episode",
            kind="episode",
            text="Synthetic provenance episode",
            project="A",
            created_at=90,
            event_time=90,
        )
    )
    write_log(directory / "items.jsonl", [episode, old, corrected, new])
    write_log(
        directory / "blocks.jsonl",
        [
            {
                "name": "working_rules",
                "text": "Legacy block",
                "updated_at": 100,
                "unknown_block": {"keep": True},
            },
            {
                "name": "project_state",
                "text": "State A",
                "updated_at": 200,
                "scope": "project",
                "project": "A",
            },
        ],
    )
    fp = fingerprint("legacy", {"text": "original"})
    write_log(
        directory / "requests.jsonl",
        [
            {
                "scope": "A",
                "request_id": "completed",
                "fingerprint": fp,
                "state": "pending",
                "unknown_receipt": [1],
            },
            {
                "scope": "A",
                "request_id": "completed",
                "fingerprint": fp,
                "state": "complete",
                "result": {"ok": True},
            },
            {
                "scope": "A",
                "request_id": "uncertain",
                "fingerprint": fp,
                "state": "pending",
                "unknown_receipt": [2],
            },
        ],
    )
    (directory / "meta.json").write_text(json.dumps({"version": "4.0.1", "source_extra": {"keep": True}}))
    write_log(
        directory / "repair-audit.jsonl",
        [{"incident": "synthetic", "actor": "test", "ids": ["old"], "trace": "preserved"}],
    )
    return directory


def sql_engine(directory):
    return MemoryV4(
        config=Config(data_dir=str(directory), embed_model="hash", hash_dim=16, store_backend="sqlite_s1")
    )


def test_dry_run_exhaustive_import_and_byte_exact_original_restore(tmp_path):
    source = source_fixture(tmp_path / "source")
    target = tmp_path / "target"
    before = _files(source)
    plan = migrate(source, target)
    assert plan["dry_run"] is True and not target.exists() and _files(source) == before
    report = migrate(source, target, apply=True)
    assert report["comparison"]["item_versions"] == 4
    assert report["comparison"]["legacy_pending"] == 1
    assert report["comparison"]["legacy_request_versions"] == 3
    assert report["normalizations"] == {"block.scope_defaults": 1}
    assert _files(source) == before
    target_before = _files(target)
    assert migrate(source, target, apply=True)["already_migrated"] is True
    assert _files(target) == target_before  # Read-only resume creates no WAL/SHM files.
    restored = tmp_path / "restored"
    assert export(target, restored, original=True)["original"] is True
    assert _files(restored) == before and _files(target) == target_before


def test_unknown_fields_pending_and_completed_replay_survive_updates(tmp_path):
    source = source_fixture(tmp_path / "source")
    target = tmp_path / "target"
    before = _files(source)
    migrate(source, target, apply=True)
    with sql_engine(target) as engine:
        assert engine.execute_request(
            "legacy",
            "A",
            {"text": "original"},
            "completed",
            lambda: pytest.fail("must replay legacy completion"),
        ) == {"ok": True}
        with pytest.raises(RequestIncompleteError, match="uncertain"):
            engine.execute_request(
                "legacy", "A", {"text": "original"}, "uncertain", lambda: pytest.fail("must not replay")
            )
        assert engine.store.raw_history("old")[0]["future_field"]["provenance"] == ["keep", "unchanged"]
        engine.forget("old", project="A")
        assert engine.store.raw_history("old")[-1]["future_field"] == {"provenance": ["keep", "unchanged"]}
        engine.blocks.append("working_rules", "new")
        raw = json.loads(
            engine.store._connection.execute(
                "SELECT record_json FROM block_versions ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
        )
        assert raw["unknown_block"] == {"keep": True}
        assert engine.status()["receipts"]["legacy_pending"] == 1
    assert _files(source) == before
    with pytest.raises(ValueError, match="lose data"):
        export(target, tmp_path / "unsafe-restore", original=True)
    with pytest.raises(ValueError, match="new writes"):
        migrate(source, target, apply=True)
    out = tmp_path / "semantic"
    export(target, out)
    assert _files(out / "legacy-original") == before
    records = [json.loads(line) for line in (out / "items.jsonl").read_text().splitlines()]
    assert records[-1]["future_field"] == {"provenance": ["keep", "unchanged"]}
    requests = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
    assert next(r for r in requests if r["request_id"] == "uncertain")["state"] == "pending"
    assert json.loads((out / "export.json").read_text())["automatic_downgrade"] is False


def test_hash_search_brief_timeline_unchanged_on_comparison_copy(tmp_path, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 1000.0)
    source = source_fixture(tmp_path / "source")
    target, reference = tmp_path / "target", tmp_path / "reference"
    before = _files(source)
    migrate(source, target, apply=True)
    shutil.copytree(source, reference)
    with (
        MemoryV4(config=Config(data_dir=str(reference), embed_model="hash", hash_dim=16)) as jsonl,
        sql_engine(target) as sql,
    ):
        for at_time in (None, 150, 250):
            assert [
                hit.item.id for hit in jsonl.search("Synthetic user lives", project="A", at_time=at_time)
            ] == [hit.item.id for hit in sql.search("Synthetic user lives", project="A", at_time=at_time)]
        from memory_os_v4.models import TimelineIn
        from memory_os_v4.server import _h_timeline

        args = TimelineIn(project="A", query="Synthetic user lives")
        assert _h_timeline(jsonl, args) == _h_timeline(sql, args)
        before_brief, after_brief = jsonl.session_brief(project="A"), sql.session_brief(project="A")
        # Deployment path and SQLite-only operational counters aren't user memory.
        for brief in (before_brief, after_brief):
            brief["stats"].pop("data_dir")
            brief["stats"].pop("receipts", None)
        assert before_brief == after_brief
    assert _files(source) == before


@pytest.mark.parametrize(
    "damage", ["truncated", "malformed", "duplicate", "nan", "pending_state", "kind_change", "linked"]
)
def test_invalid_source_refused_without_source_or_target_change(tmp_path, damage):
    source = source_fixture(tmp_path / "source")
    if damage == "truncated":
        path = source / "items.jsonl"
        path.write_bytes(path.read_bytes()[:-1])
    elif damage == "malformed":
        (source / "repair-audit.jsonl").write_text("broken\n")
    elif damage == "duplicate":
        (source / "meta.json").write_text('{"version":1,"version":2}')
    elif damage == "nan":
        (source / "meta.json").write_text('{"value":NaN}')
    elif damage == "pending_state":
        write_log(
            source / "requests.jsonl",
            [{"scope": "A", "request_id": "x", "fingerprint": "f", "state": "success"}],
        )
    elif damage == "kind_change":
        with (source / "items.jsonl").open("a") as fh:
            fh.write(
                json.dumps({"id": "old", "kind": "episode", "text": "Wrong identity", "project": "A"}) + "\n"
            )
    else:
        (source / "bad-link").symlink_to(source / "items.jsonl")
    before = {p.name: p.read_bytes() for p in source.iterdir() if not p.is_symlink()}
    with pytest.raises((ValueError, RuntimeError)):
        migrate(source, tmp_path / "target", apply=True)
    assert not (tmp_path / "target").exists()
    assert {p.name: p.read_bytes() for p in source.iterdir() if not p.is_symlink()} == before


def test_running_writer_missing_lease_and_nonempty_wal_refused(tmp_path):
    source = tmp_path / "source"
    with MemoryV4(config=Config(data_dir=str(source), embed_model="hash")) as engine:
        engine.add_episode("Synthetic offline guard")
        engine.flush()
        with pytest.raises(RuntimeError, match="active writer"):
            migrate(source, tmp_path / "target", apply=True)
    (source / "writer.lock").unlink()
    with pytest.raises(ValueError, match="writer.lock"):
        migrate(source, tmp_path / "target", apply=True)
    (source / "writer.lock").touch()
    migrate(source, tmp_path / "target", apply=True)
    (tmp_path / "target" / "store.sqlite3-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(ValueError, match="nonempty WAL"):
        export(tmp_path / "target", tmp_path / "out", original=True)


def test_failure_at_every_import_write_boundary_preserves_source(tmp_path):
    source = source_fixture(tmp_path / "source")
    before = _files(source)
    boundaries = []
    migrate(source, tmp_path / "trace", apply=True, fault_hook=boundaries.append)
    assert boundaries[-2:] == ["before_publish", "after_publish"]
    for ordinal, label in enumerate(boundaries):
        seen = []

        def fail(point):
            seen.append(point)
            if len(seen) == ordinal + 1:
                raise OSError(f"injected {ordinal}:{label}")

        target = tmp_path / f"failure-{ordinal}"
        with pytest.raises(OSError, match="injected"):
            migrate(source, target, apply=True, fault_hook=fail)
        assert target.exists() == (label == "after_publish")
        assert _files(source) == before
        if target.exists():
            assert migrate(source, target, apply=True)["already_migrated"] is True
    print(
        f"S3 exceptions: {len(boundaries)} import boundaries, source unchanged, no partial target published"
    )


@pytest.mark.parametrize("point", ["before_commit", "after_commit", "before_publish", "after_publish"])
def test_sigkill_staging_is_not_published_and_fresh_retry_has_no_duplicates(tmp_path, point):
    source = source_fixture(tmp_path / "source")
    before = _files(source)
    target = tmp_path / "target"
    code = """
import os, signal, sys
from memory_os_v4.sqlite_migrate import migrate
def kill(point):
    if point==sys.argv[3]: os.kill(os.getpid(),signal.SIGKILL)
migrate(sys.argv[1],sys.argv[2],apply=True,fault_hook=kill)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source), str(target), point],
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert _files(source) == before and target.exists() == (point == "after_publish")
    abandoned = list(tmp_path.glob(".target.migrating-*"))
    assert len(abandoned) == int(point != "after_publish")
    if abandoned:
        assert (abandoned[0] / "migration-intent.json").exists()
    report = migrate(source, target, apply=True)
    assert report["comparison"]["item_versions"] == 4
    assert report["abandoned_stages"] == [p.name for p in abandoned]
    store = SQLiteStore(target)
    try:
        assert len(store.raw_history("old")) == 2
        assert store._connection.execute("SELECT count(*) FROM item_versions").fetchone()[0] == 4
    finally:
        store.close()


def test_cli_default_simulation_does_not_create_target(tmp_path, capsys):
    source = source_fixture(tmp_path / "source")
    target = tmp_path / "target"
    assert main(["import", str(source), str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not target.exists()


def test_publication_never_replaces_destination_even_after_race(tmp_path, monkeypatch):
    stage, target = tmp_path / "stage", tmp_path / "target"
    stage.mkdir()
    target.mkdir()
    inode = target.stat().st_ino
    original = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: False if self == target else original(self))
    with pytest.raises(OSError):
        _publish(stage, target)
    assert target.stat().st_ino == inode and stage.is_dir()


def test_legacy_defaults_are_deterministic_and_original_fields_reversible(tmp_path):
    source = source_fixture(tmp_path / "source")
    raw = {"id": "minimal", "kind": "episode", "text": "Minimal legacy record", "unknown": [1]}
    write_log(source / "items.jsonl", [raw])
    target = tmp_path / "target"
    before = _files(source)
    report = migrate(source, target, apply=True)
    assert report["normalizations"]["item.event_time"] == 1
    store = SQLiteStore(target)
    try:
        assert store.get("minimal").event_time == 0 and store.get("minimal").created_at == 0
        assert store.get("minimal").project == "default"
        assert store.raw_history("minimal")[0]["unknown"] == [1]
    finally:
        store.close()
    export(target, tmp_path / "restored", original=True)
    assert _files(tmp_path / "restored") == before


def test_corrupt_archive_refuses_restore_without_changing_source(tmp_path):
    source = source_fixture(tmp_path / "source")
    target = tmp_path / "target"
    migrate(source, target, apply=True)
    db = sqlite3.connect(target / "store.sqlite3")
    db.execute("DROP TRIGGER legacy_files_no_update")
    db.execute("UPDATE legacy_files SET contents=x'00' WHERE path='items.jsonl'")
    db.commit()
    db.close()
    before = _files(target)
    with pytest.raises(ValueError, match="checksum"):
        export(target, tmp_path / "out", original=True)
    assert _files(target) == before and not (tmp_path / "out").exists()
