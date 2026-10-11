"""C02-C06/C11: actual receipts, both backends, no neural inference/network."""
import hashlib
import shutil

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.idempotency import raw_args_sha256


@pytest.fixture(params=["jsonl", "sqlite_s1"])
def engine(request, tmp_path, monkeypatch):
    e = MemoryV4(config=Config(data_dir=str(tmp_path / "store"), embed_model="hash", hash_dim=16,
                              store_backend=request.param, maintenance_mode="apply"))
    monkeypatch.setattr(server, "get_engine", lambda: e)
    monkeypatch.setattr(server, "_engine", e)
    yield e
    e.close()


def preflight(args, name="memory_block_set"):
    return server.dispatch("memory_request_status", {"operation": name, "arguments": args})


def lookup(args, before):
    return {"request_id": args["request_id"], **{k: before[k] for k in
            ("scope", "operation", "raw_args_sha256", "store_instance_id")}}


def status(query):
    value = server.dispatch("memory_request_status", query)
    assert value["replay_permitted"] is False
    return value


def test_C03_C11_raw_canonical_scope_and_readonly(engine):
    args = {"request_id": "logical", "name": "working_rules", "text": "Synthetic", "project": "p"}
    before = preflight(args)
    result = server.dispatch("memory_block_set", args)
    q = lookup(args, before)
    value = status(q)
    assert value["status"] == "completed" and value["result"] == result
    record = engine.requests.records[(before["scope"], "logical")]
    assert record["raw_args_sha256"] == raw_args_sha256(args)
    assert record["fingerprint"] != record["raw_args_sha256"]
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in engine.data_dir.iterdir() if p.is_file()}
    for _ in range(3):
        assert status(q)["status"] == "completed"
    assert files == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in engine.data_dir.iterdir() if p.is_file()}
    assert "result" not in status({**q, "raw_args_sha256": "f" * 64})
    assert status({**q, "scope": '["project", "other"]'})["status"] == "not_found"
    assert status({**q, "store_instance_id": "another"})["status"] == "unknown"
    assert server.dispatch("memory_block_set", args) == result
    assert server.dispatch("memory_block_set", {**args, "text": "Different"})["ok"] is False
    assert len(engine.blocks.history("working_rules", project="p")) == 1
    different_scope = {**args, "project": "q"}
    assert "error" not in server.dispatch("memory_block_set", different_scope)
    assert status(lookup(different_scope, preflight(different_scope)))["status"] == "completed"


def test_C03_alias_default_scope_resolution_server_only(engine, monkeypatch):
    monkeypatch.setenv("MEMORY_V4_PROJECT", "first")
    args = {"request_id": "alias", "dry_run": False}
    p = preflight(args, "memory_compact")
    result = server.dispatch("memory_compact", args)
    assert p["operation"] == "memory_consolidate"
    assert status(lookup(args, p))["result"] == result
    assert server.dispatch("memory_consolidate", args) == result
    monkeypatch.setenv("MEMORY_V4_PROJECT", "second")
    assert preflight(args, "memory_compact")["scope"] != p["scope"]


def test_C04_C05_partial_or_rollback_after_restart(engine, monkeypatch):
    args = {"request_id": "cut", "name": "working_rules", "text": "partial", "project": "p"}
    before = preflight(args)
    original = server.TOOL_HANDLERS["memory_block_set"]
    def partial(e, parsed):
        original(e, parsed)
        raise OSError("injected after source write")
    monkeypatch.setitem(server.TOOL_HANDLERS, "memory_block_set", partial)
    assert server.dispatch("memory_block_set", args)["ok"] is False
    expected = "not_found" if engine.config.store_backend == "sqlite_s1" else "pending"
    assert status(lookup(args, before))["status"] == expected
    engine.close()
    reopened = MemoryV4(config=engine.config)
    monkeypatch.setattr(server, "get_engine", lambda: reopened)
    try:
        assert status(lookup(args, before))["status"] == expected
        if expected == "pending":
            monkeypatch.setitem(server.TOOL_HANDLERS, "memory_block_set", original)
            assert server.dispatch("memory_block_set", args)["ok"] is False
            assert len(reopened.blocks.history("working_rules", project="p")) == 1
        else:
            assert reopened.blocks.get("working_rules", project="p") == ""
    finally:
        reopened.close()


def test_C06_old_copy_never_authorizes_replay(engine, tmp_path, monkeypatch):
    args = {"request_id": "after-snapshot", "name": "working_rules", "text": "new", "project": "p"}
    p = preflight(args)
    engine.close()
    snapshot = tmp_path / "old-copy"
    shutil.copytree(engine.data_dir, snapshot)
    live = MemoryV4(config=engine.config)
    monkeypatch.setattr(server, "get_engine", lambda: live)
    server.dispatch("memory_block_set", args)
    assert status(lookup(args, p))["status"] == "completed"
    live.close()
    old = MemoryV4(config=Config(data_dir=str(snapshot), embed_model="hash", hash_dim=16,
                                store_backend=engine.config.store_backend))
    monkeypatch.setattr(server, "get_engine", lambda: old)
    try:
        assert status(lookup(args, p))["status"] == "not_found"
    finally:
        old.close()


def test_C05_legacy_receipt_unknown(engine):
    args = {"request_id": "legacy", "name": "working_rules", "text": "x", "project": "p"}
    p = preflight(args)
    engine.execute_request("legacy", p["scope"], {}, "legacy", lambda: {"old": True})
    assert status(lookup(args, p))["reason"] == "legacy_unattested"
    assert status({})["local_journals"] is False


def test_raw_fingerprint_only_excludes_id():
    assert raw_args_sha256({"a": "é", "request_id": "one"}) == raw_args_sha256({"request_id": "two", "a": "é"})
    assert raw_args_sha256({"a": "é"}) != raw_args_sha256({"a": "é", "default": None})
    with pytest.raises(ValueError):
        raw_args_sha256({"x": float("nan")})


def test_C05_corrupt_jsonl_receipts_fail_closed(tmp_path):
    (tmp_path / "requests.jsonl").write_text('{"truncated"')
    with pytest.raises(RuntimeError, match="damaged"):
        MemoryV4(config=Config(data_dir=str(tmp_path), embed_model="hash"))


def test_runtime_contract_v2_matches_schema(engine):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    from memory_os_v4.models import RequestStatusIn
    schema = json.loads((Path(__file__).parents[1] / "docs/contracts/l3-l4-v2.schema.json").read_text())
    assert schema["$defs"]["request_status_input"] == RequestStatusIn.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validate = Draft202012Validator({**schema, "$ref": "#/$defs/server_status_output"}).validate
    args = {"name": "working_rules", "text": "x", "request_id": "schema"}
    p = preflight(args)
    validate(p)
    q = lookup(args, p)
    validate(status(q))
    server.dispatch("memory_block_set", args)
    validate(status(q))
    validate(status({**q, "raw_args_sha256": "f" * 64}))
    validate(status({**q, "store_instance_id": "different"}))
