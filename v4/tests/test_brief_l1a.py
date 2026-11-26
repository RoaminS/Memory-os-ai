"""Frozen 100 cases, real hash stores/dispatch/relay; structural counter explicitly a test double.
Exact cl100k qualification is a separate refusing offline runner, never inferred here.
"""

import hashlib
import json
from pathlib import Path
from datetime import datetime

import anyio
import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4 import brief, brief_tokens
from memory_os_v4.blocks import Block
from memory_os_v4.store import MemoryItem
from memory_os_v4.ops.durable_relay import DurableRelay

FIXTURES = Path(__file__).parent / "fixtures/brief_l1a"
CASES = [json.loads(line) for line in (FIXTURES / "scenarios_v1.jsonl").read_text().splitlines()]


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def load_case(case, root, backend):
    registry = root / "registry.json"
    registry.write_text(json.dumps(case["registry"]))
    registry.chmod(0o600)
    e = MemoryV4(
        config=Config(
            data_dir=str(root / "store"),
            embed_model="hash",
            hash_dim=16,
            project_registry=str(registry),
            store_backend=backend,
        )
    )
    for source in case["items"]:
        data = dict(source)
        data.setdefault("created_at", data.get("valid_from", case["now"]))
        data.setdefault("event_time", data.get("valid_from", case["now"]))
        for key in ("created_at", "event_time", "valid_from", "valid_until"):
            if data.get(key) is not None:
                data[key] = timestamp(data[key])
        it = MemoryItem(**data)
        e.store.append(it)
        if not it.deleted:
            e.bm25.add(it.id, brief.lexical_tokens(it.text, e.config))
    # Historical source fixtures: do not truncate through today's write API.
    for source in case["blocks"]:
        project = None if source["scope"] == "global" else source["project"]
        e.blocks._blocks[(project, source["name"])] = Block(
            source["name"], source["text"], timestamp(case["now"]), project, source["scope"]
        )
    return e


class Ledger:
    def __init__(self, scenario):
        self.scenario = scenario

    def status(self):
        return {
            "unresolved_count": len(self.scenario["unresolved"]),
            "errors": self.scenario["errors"],
            "journal_poisoned": False,
        }


class Transport:
    async def submit(self, op, *, replay):
        class Session:
            async def call_tool(self, name, args):
                return brief.envelope(server.dispatch(name, args))

        return await op(Session())


def run_path(case, path):
    if path == "server":
        return server.dispatch("memory_session_brief", case["request"])

    class Down:
        async def submit(self, op, *, replay):
            raise ConnectionError("synthetic outage")

    down = path in case["expect"].get("path_errors", {})
    r = DurableRelay(Down() if down else Transport(), None)
    r.journal = Ledger(case["relay"])
    return anyio.run(r.call, "memory_session_brief", case["request"]).structuredContent


def ids_of(data):
    return {r["id"] for r in data.get("references", [])} | {
        r["id"]
        for key in ("items", "key_facts", "pending", "focus", "recent_episodes", "procedures")
        for r in data.get(key, [])
    }


def assert_case(case, data, path):
    expected = case["expect"]
    assert data.get("ok") is not False, data
    ids = ids_of(data)
    assert set(expected["required_ids"]) <= ids
    assert not set(expected["forbidden_ids"]) & ids
    assert (
        set(expected["required_block_names"] if "required_block_names" in expected else [])
        <= data.get("blocks", {}).keys()
    )
    assert not set(expected.get("forbidden_block_names", [])) & data.get("blocks", {}).keys()
    codes = expected["notice_codes"] + (expected.get("relay_notice_codes", []) if path == "relay" else [])
    assert set(codes) <= set(data.get("notices", []))
    text = json.dumps(data, ensure_ascii=False)
    for it in case["items"]:
        if it["id"] in expected["forbidden_ids"]:
            assert it["text"] not in text


def test_seal_and_language_counts():
    manifest = json.loads((FIXTURES / "manifest_v1.json").read_text())
    assert (
        hashlib.sha256((FIXTURES / "scenarios_v1.jsonl").read_bytes()).hexdigest()
        == manifest["scenarios_sha256"]
    )
    assert len(CASES) == 100 and len({c["id"] for c in CASES}) == 100
    assert sum(c["language"] == "fr" for c in CASES) == 50


@pytest.mark.parametrize("backend", ["jsonl", "sqlite_s1"])
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_sealed_case_structural(case, backend, tmp_path, monkeypatch):
    # Conservative byte-count double exercises fitting. NOT cl100k token evidence.
    monkeypatch.setattr(brief, "counter", lambda: lambda text: (len(text.encode()) + 2) // 3)
    monkeypatch.setattr(brief.time, "time", lambda: timestamp(case["now"]))
    e = load_case(case, tmp_path, backend)
    monkeypatch.setattr(server, "get_engine", lambda: e)
    monkeypatch.setattr(server, "_engine", e)
    try:

        def no_mutation(*args, **kwargs):
            pytest.fail("brief attempted a mutation")

        for name in ("add_facts", "add_episode", "forget", "consolidate"):
            monkeypatch.setattr(e, name, no_mutation)
        for path in ("server", "relay"):
            data = run_path(case, path)
            if path in case["expect"].get("path_errors", {}):
                assert data["code"] == case["expect"]["path_errors"][path]
                continue
            if "error_code" in case["expect"]:
                assert data["code"] == case["expect"]["error_code"]
                continue
            assert_case(case, data, path)
            if case["request"]["mode"] == "compact":
                size, tokens = brief.measures(data)
                assert size <= 6000 and tokens <= 1500
            if case["expect"].get("deterministic"):
                assert run_path(case, path) == data
            if case["expect"].get("compare_paths"):
                assert run_path(case, "server") == run_path(case, "relay")
            if case["expect"].get("compare_modes"):
                other = server.dispatch("memory_session_brief", {**case["request"], "mode": "compact"})
                assert set(case["expect"]["required_ids"]) <= ids_of(other)
            if case["expect"]["expand_ids"]:
                detail = server.dispatch(
                    "memory_session_brief",
                    {
                        "project": case["request"]["project"],
                        "mode": "detailed",
                        "expand": case["expect"]["expand_ids"],
                    },
                )
                assert set(case["expect"]["expand_ids"]) <= ids_of(detail)
    finally:
        e.close()


def test_no_table_never_downloads(monkeypatch):
    # 4.1.3: the table ships in the package; "no table" now means an installation without the embedded copy
    monkeypatch.delenv("MEMORY_V4_CL100K_PATH", raising=False)
    monkeypatch.setattr(brief_tokens, "embedded_table", lambda: None)
    brief_tokens._load.cache_clear()
    with pytest.raises(brief_tokens.TokenizerUnavailable):
        brief_tokens.counter()


def test_untrusted_giant_error_is_bounded_without_tokenizer():
    data = brief.compact_error({"ok": False, "error": "x" * 100000, "code": "x" * 100000})
    assert len(brief.serialized(data).encode()) < 1000


def test_expired_deleted_sensitive_reference_cannot_expand(tmp_path, monkeypatch):
    case = CASES[83]
    e = load_case(case, tmp_path, "jsonl")
    monkeypatch.setattr(brief.time, "time", lambda: timestamp(case["now"]))
    try:
        data = brief.expand_detailed(e, e.projects.resolve("prj_a", read=True), ["sec"])
        assert data["items"] == [] and data["notices"] == ["reference_not_found"]
    finally:
        e.close()


def test_block_reference_expands_only_current_project(tmp_path, monkeypatch):
    e = load_case(CASES[50], tmp_path, "jsonl")
    try:
        data = brief.expand_detailed(e, e.projects.resolve("prj_a", read=True), ["block:user_profile"])
        assert data["blocks"]["user_profile"] and not data["notices"]
    finally:
        e.close()


@pytest.mark.asyncio
async def test_real_mcp_serialization_matches_counted_envelope(tmp_path, monkeypatch):
    """SDK round trip, including automatic result validation and structuredContent."""
    from mcp import ClientSession
    from mcp.shared.message import SessionMessage

    monkeypatch.setattr(brief, "counter", lambda: lambda text: len(text.encode()))
    e = load_case(CASES[64], tmp_path, "jsonl")
    monkeypatch.setattr(server, "_engine", e)
    monkeypatch.setattr(brief.time, "time", lambda: timestamp(CASES[64]["now"]))
    try:
        client_send, server_read = anyio.create_memory_object_stream[SessionMessage](16)
        server_send, client_read = anyio.create_memory_object_stream[SessionMessage](16)
        async with anyio.create_task_group() as tg:
            tg.start_soon(
                server.server.run, server_read, server_send, server.server.create_initialization_options()
            )
            async with ClientSession(client_read, client_send) as session:
                await session.initialize()
                result = await session.call_tool("memory_session_brief", CASES[64]["request"])
                assert result.structuredContent["mode"] == "compact"
                encoded = json.dumps(
                    result.model_dump(mode="json", by_alias=True, exclude_none=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                assert encoded == brief.serialized(result.structuredContent)
                assert len(encoded.encode()) <= 1500
            tg.cancel_scope.cancel()
    finally:
        e.close()


@pytest.mark.asyncio
async def test_large_invalid_brief_arguments_get_small_error_before_engine(monkeypatch):
    monkeypatch.setattr(server, "get_engine", lambda: pytest.fail("invalid brief opened engine"))
    result = await server.call_tool("memory_session_brief", {"expand": ["x" * 100000]})
    assert result.structuredContent["ok"] is False
    assert len(json.dumps(result.model_dump(mode="json", exclude_none=True)).encode()) < 1000


def test_default_compact_missing_counter_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_V4_CL100K_PATH", raising=False)
    monkeypatch.setattr(brief_tokens, "embedded_table", lambda: None)   # 4.1.3: installation without the table
    brief_tokens._load.cache_clear()
    e = load_case(CASES[4], tmp_path, "jsonl")
    monkeypatch.setattr(server, "_engine", e)
    try:
        result = server.dispatch("memory_session_brief", {"project": "prj_a"})
        assert result["code"] == "tokenizer_unavailable"
        detailed = server.dispatch("memory_session_brief", {"project": "prj_a", "mode": "detailed"})
        assert detailed["mode"] == "detailed" and detailed["key_facts"]
    finally:
        e.close()
