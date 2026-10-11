"""C01/C02/C07-C12, process/file faults and transport doubles, offline."""
import json
import os
import subprocess
import sys

import anyio
import pytest

from memory_os_v4.idempotency import raw_args_sha256
from memory_os_v4.ops import request_journal as ledger
from memory_os_v4.ops.durable_relay import DurableRelay, response
from memory_os_v4.ops.settings import OpsSettings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_V4_RELAY_JOURNAL_DIR", str(tmp_path / "ops/relay"))
    return OpsSettings(url="http://127.0.0.1:9/mcp/", token_file=tmp_path / "http.token", store_dir=tmp_path / "store",
                       backup_dir=tmp_path / "backup", backup_key=tmp_path / "key", ops_dir=tmp_path / "ops", service_label=None)


def row(i="logical"):
    return {"request_id": i, "scope": '["project", "p"]', "operation": "memory_block_set",
            "raw_args_sha256": "a" * 64, "store_instance_id": "store"}


class Session:
    def __init__(self, fault=None):
        self.fault, self.effects, self.receipts = fault, 0, {}
        self.identity = "store"
        self.legacy = False

    async def call_tool(self, name, args):
        base = {"ok": True, "contract": "l3-v2", "store_instance_id": self.identity, "replay_permitted": False}
        if name == "memory_request_status":
            if self.legacy:
                return response({"ok": False, "error": "unknown tool"})
            if "arguments" in args:
                return response({**base, "scope": '["project", "p"]', "operation": args["operation"],
                                 "raw_args_sha256": raw_args_sha256(args["arguments"])})
            return response({**base, "request_id": args["request_id"], **self.receipts.get(args["request_id"], {"status": "not_found"})})
        if name == "memory_session_brief":
            return response({"text": "Synthetic brief"})
        if name == "memory_status":
            return response({"alive": True})
        if self.fault == "before":
            raise ConnectionError("before accept")
        self.effects += 1
        if self.fault == "during":
            raise ConnectionError("partial mutation")
        self.receipts[args["request_id"]] = {"status": "completed", "result": {"effect": self.effects}}
        if self.fault == "after":
            raise ConnectionError("after commit")
        return response({"effect": self.effects})


class Transport:
    def __init__(self, session):
        self.session = session

    async def submit(self, op, *, replay):
        return await op(self.session)


def call(relay, name="memory_block_set", args=None):
    return anyio.run(relay.call, name, {"name": "working_rules", "text": "SENSITIVE PAYLOAD", "request_id": "id"} if args is None else args)


@pytest.mark.parametrize("fault,effects", [("before", 0), ("during", 1), ("after", 1)])
def test_C02_C08_orphan_adoption_and_status_never_replay(settings, fault, effects):
    session = Session(fault)
    first = DurableRelay(Transport(session), settings)
    with pytest.raises(RuntimeError, match="request_id=id"):
        call(first)
    assert session.effects == effects
    path = first.journal.path
    original = path.read_bytes()
    assert b"SENSITIVE PAYLOAD" not in original
    assert b"sending" in original
    first.close()
    second = DurableRelay(Transport(session), settings)
    try:
        report = call(second, "memory_request_status", {}).structuredContent
        assert report["unresolved_count"] == 1
        assert path.read_bytes() == original
        brief = call(second, "memory_session_brief", {}).structuredContent
        assert "1 uncertain" in brief["text"]
        found = call(second, "memory_request_status", {"request_id": "id"}).structuredContent
        assert found["replay_permitted"] is False
        if fault == "after":
            assert found["status"] == "completed"
            assert call(second).structuredContent == {"effect": 1}
            assert second.journal.status()["unresolved_count"] == 0
        else:
            assert found["status"] == "not_found"
            with pytest.raises(RuntimeError):
                call(second)
        assert session.effects == effects
        assert path.read_bytes() == original  # adopter NEVER edits orphan.
    finally:
        second.close()


@pytest.mark.parametrize("phase", ["prepared", "sending", "completed"])
def test_C01_C10_fsync_failure_before_send_or_after_effect(settings, monkeypatch, phase):
    session = Session()
    relay = DurableRelay(Transport(session), settings)
    journal = relay.ledger()
    original_append = journal.append
    real_fsync = os.fsync
    def append(record, state):
        if state == phase:
            monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected disk full")))
        try:
            return original_append(record, state)
        finally:
            monkeypatch.setattr(os, "fsync", real_fsync)
    monkeypatch.setattr(journal, "append", append)
    try:
        with pytest.raises(RuntimeError):
            call(relay)
        assert session.effects == int(phase == "completed")
        assert journal.poisoned
        assert journal.status()["unresolved_count"] == 1
        with pytest.raises(RuntimeError):
            call(relay, args={"request_id": "fresh", "name": "working_rules", "text": "x"})
        assert session.effects == int(phase == "completed")
    finally:
        relay.close()


@pytest.mark.parametrize("state", ["prepared", "sending"])
def test_C01_process_exit_at_durable_boundary(tmp_path, state):
    target = tmp_path / "journals"
    script = ("import os,sys,json; from pathlib import Path; "
              "from memory_os_v4.ops.request_journal import RelayJournal; "
              "j=RelayJournal(Path(sys.argv[1]),'http://127.0.0.1:9/mcp/'); "
              "j.append(json.loads(sys.argv[2]),'prepared'); "
              + ("j.append(json.loads(sys.argv[2]),'sending'); " if state == "sending" else "") + "os._exit(71)")
    done = subprocess.run([sys.executable, "-c", script, str(target), json.dumps(row())], check=False)
    assert done.returncode == 71
    adopted = ledger.scan(target)
    assert adopted["active_journals"] == 0 and adopted["errors"] == []
    assert adopted["entries"][0]["state"] == state


def test_C05_C10_corruption_and_permissions(settings):
    session = Session()
    j = ledger.RelayJournal(settings.ops_dir / "relay", settings.url)
    j.append(row(), "sending")
    j.close()
    with j.path.open("ab") as f:
        f.write(b'{"truncated"')
    assert ledger.scan(j.path.parent)["errors"]
    relay = DurableRelay(Transport(session), settings)
    try:
        with pytest.raises(RuntimeError):
            call(relay)
        assert session.effects == 0
    finally:
        relay.close()
    os.chmod(j.path, 0o644)
    assert ledger.scan(j.path.parent)["errors"]


def test_C09_legacy_or_changed_store_refuses_before_mutation(settings):
    session = Session()
    relay = DurableRelay(Transport(session), settings)
    try:
        session.legacy = True
        with pytest.raises(RuntimeError):
            call(relay)
        assert session.effects == 0
        session.legacy = False
        call(relay)
        session.identity = "replacement"
        with pytest.raises(RuntimeError):
            call(relay, args={"name": "working_rules", "text": "new", "request_id": "new"})
        assert session.effects == 1
    finally:
        relay.close()


def test_C12_two_processes_and_independent_instances(tmp_path):
    script = ("import sys,json; from pathlib import Path; from memory_os_v4.ops.request_journal import RelayJournal; "
              "j=RelayJournal(Path(sys.argv[1]),'http://127.0.0.1:9/mcp/','codex'); "
              "j.append(json.loads(sys.argv[2]),'sending'); print(j.path.name,flush=True); input(); j.close()")
    path = tmp_path / "ledgers"
    processes = []
    try:
        for i in range(2):
            p = subprocess.Popen([sys.executable, "-c", script, str(path), json.dumps(row(str(i)))],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            processes.append(p)
            assert p.stdout.readline().startswith("codex-")
        report = ledger.scan(path)
        assert report["active_journals"] == 2 and report["entries"] == []
    finally:
        for p in processes:
            p.communicate("\n", timeout=5)
            assert p.returncode == 0
    report = ledger.scan(path)
    assert len(report["entries"]) == 2 and len({r["request_id"] for r in report["entries"]}) == 2


def test_C12_retention_clock_changes_and_fsync_samples(tmp_path, monkeypatch):
    j = ledger.RelayJournal(tmp_path / "ledger", "endpoint", max_bytes=2048, max_days=30)
    try:
        j.append(row("uncertain"), "sending")
        for i in range(30):
            j.append(row(str(i)), "prepared")
            j.append(row(str(i)), "sending")
            j.append(row(str(i)), "completed")
        report = j.status()
        assert report["unresolved_count"] == 1
        assert report["fsync_ms"]["prepared"]["count"] == 30
        assert report["fsync_ms"]["sending"]["p95"] >= 0
        assert j.path.stat().st_size <= 2048
        monkeypatch.setattr(ledger.time, "time", lambda: 10**12)
        j.rotate()
        assert j.status()["unresolved_count"] == 1
        assert all(r["state"] != "completed" for r in ledger._records(j.path)[0].values())
    finally:
        j.close()


def test_C11_wrong_endpoint_does_not_resolve_foreign_call(settings):
    j = ledger.RelayJournal(settings.ops_dir / "relay", "http://127.0.0.1:10/mcp/")
    j.append(row(), "sending")
    j.close()
    relay = DurableRelay(Transport(Session()), settings)
    try:
        assert call(relay, "memory_request_status", {}).structuredContent["unresolved_count"] == 0
        assert call(relay, "memory_request_status", {"request_id": "logical"}).structuredContent["status"] == "unknown"
    finally:
        relay.close()


def test_journal_symlink_directory_rejected(settings, tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    settings.ops_dir.mkdir()
    (settings.ops_dir / "relay").symlink_to(target, target_is_directory=True)
    r = DurableRelay(Transport(Session()), settings)
    with pytest.raises(ValueError, match="private"):
        call(r)


def test_retention_closed_is_separate_from_readonly_adoption(tmp_path, monkeypatch):
    path = tmp_path / "ledger"
    j = ledger.RelayJournal(path, "endpoint")
    j.append(row("pending"), "sending")
    j.append(row("done"), "completed")
    assert ledger.prune_closed(path, apply=True)["active"] == 1
    j.close()
    old = j.path.read_bytes()
    monkeypatch.setattr(ledger.time, "time", lambda: 10**12)
    assert len(ledger.scan(path)["entries"]) == 2 and j.path.read_bytes() == old
    assert ledger.prune_closed(path)["journals"][0]["eligible"]
    assert j.path.read_bytes() == old
    result = ledger.prune_closed(path, apply=True)
    assert result["journals"][0]["unresolved_preserved"] == 1
    assert [r["request_id"] for r in ledger.scan(path)["entries"]] == ["pending"]


def test_C08_cancellation_leaves_durable_uncertainty(settings):
    class Hanging(Session):
        async def call_tool(self, name, args):
            if name == "memory_block_set":
                self.effects += 1
                await anyio.sleep_forever()
            return await super().call_tool(name, args)
    session = Hanging()
    relay = DurableRelay(Transport(session), settings)
    async def cancel():
        with anyio.move_on_after(.02) as scope:
            await relay.call("memory_block_set", {"request_id": "cancel", "name": "x", "text": "x"})
        assert scope.cancel_called
    try:
        anyio.run(cancel)
        assert session.effects == 1
        assert relay.journal.status()["unresolved_count"] == 1
        assert list(relay.journal.records.values())[0]["state"] == "sending"
    finally:
        relay.close()


def test_ledger_v2_schema_and_payload_not_retained(tmp_path):
    from pathlib import Path
    from jsonschema import Draft202012Validator
    schema = json.loads((Path(__file__).parents[1] / "docs/contracts/l3-l4-v2.schema.json").read_text())
    j = ledger.RelayJournal(tmp_path / "ledger", "endpoint")
    try:
        j.append({**row(), "arguments": {"secret": "do not retain"}}, "prepared")
        data = json.loads(j.path.read_text())
        Draft202012Validator({**schema, "$ref": "#/$defs/ledger_record"}).validate(data)
        assert "arguments" not in data
    finally:
        j.close()


def test_preflight_maintenance_refusal_preserved_without_journal_entry(settings):
    class Denied(Session):
        async def call_tool(self, name, args):
            assert name == "memory_request_status"
            return response({"ok": False, "code": "maintenance_denied", "outcome": "not_started"})
    relay = DurableRelay(Transport(Denied()), settings)
    try:
        result = call(relay, "memory_consolidate", {"dry_run": False})
        assert result.structuredContent["code"] == "maintenance_denied"
        assert relay.journal.status()["unresolved_count"] == 0
        assert relay.journal.path.read_bytes() == b""
    finally:
        relay.close()


def test_C02_completed_before_stdio_delivery_remains_discoverable(settings):
    session = Session()
    first = DurableRelay(Transport(session), settings)
    call(first, args={"name": "working_rules", "text": "x"})
    first.close()
    next_relay = DurableRelay(Transport(session), settings)
    try:
        report = call(next_relay, "memory_request_status", {}).structuredContent
        assert report["unresolved_count"] == 0
        entries = report["recent_completed"]
        assert len(entries) == 1
        value = call(next_relay, "memory_request_status", {"request_id": entries[0]["request_id"]}).structuredContent
        assert value["status"] == "completed" and value["result"] == {"effect": 1}
        assert session.effects == 1
    finally:
        next_relay.close()


def test_every_server_mutation_has_durable_relay_policy():
    from memory_os_v4 import models, server
    from memory_os_v4.ops.durable_relay import MUTATIONS, READS
    mutations = {name for name, cls in server.TOOL_MODELS.items() if issubclass(cls, models.MutationIn)}
    assert mutations == MUTATIONS
    assert set(server.TOOL_MODELS) - mutations == READS
