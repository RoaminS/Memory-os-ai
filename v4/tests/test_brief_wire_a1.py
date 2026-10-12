"""A1 compatibility and negotiation through the pinned MCP SDK, no TCP/models."""
import json

import anyio
from mcp import ClientSession
from mcp.types import Tool
import pytest

from memory_os_v4 import Config, MemoryV4, brief, server
from memory_os_v4.models import SessionBriefIn
from memory_os_v4.ops.durable_relay import DurableRelay
from memory_os_v4.ops.relay import SessionToolCatalog


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(brief, "counter", lambda: lambda s: (len(s.encode()) + 2) // 3)
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model="hash", hash_dim=16)) as e:
        e.add_facts([{"text": "UNIQUE_BODY_MARKER: explicit fact for the current project."}], project="P")
        monkeypatch.setattr(server, "get_engine", lambda: e)
        monkeypatch.setattr(server, "_engine", e)
        yield e


def data(result):
    return result.structuredContent if result.structuredContent is not None else json.loads(result.content[0].text)


def test_direct_single_body_and_legacy_json_parsers(engine):
    legacy = server.dispatch("memory_session_brief", {"project": "P"})
    v2 = server.dispatch("memory_session_brief", {"project": "P", "wire_format": "compact_v2"})
    assert brief.envelope(legacy).structuredContent == legacy
    assert brief.envelope(v2).structuredContent is None
    assert brief.serialized(legacy).count("UNIQUE_BODY_MARKER") == 2
    assert brief.serialized(v2).count("UNIQUE_BODY_MARKER") == 1
    assert data(brief.envelope(v2)) == v2
    assert legacy["text"] == v2["text"]


def test_sdk_initialize_list_validate_and_parse_both_formats(engine):
    async def run():
        to_server, server_read = anyio.create_memory_object_stream(10)
        server_write, from_server = anyio.create_memory_object_stream(10)
        async with anyio.create_task_group() as group:
            group.start_soon(server.server.run, server_read, server_write,
                             server.server.create_initialization_options())
            async with ClientSession(from_server, to_server) as session:
                await session.initialize()
                catalog = await session.list_tools()
                descriptor = next(t for t in catalog.tools if t.name == "memory_session_brief")
                assert descriptor.outputSchema is None
                for wire in ("legacy", "compact_v2"):
                    result = await session.call_tool("memory_session_brief", {"project": "P", "wire_format": wire})
                    assert not result.isError and "UNIQUE_BODY_MARKER" in data(result)["text"]
                    assert (result.structuredContent is None) == (wire == "compact_v2")
            group.cancel_scope.cancel()
    anyio.run(run)


class Ledger:
    def status(self):
        return {"unresolved_count": 1, "errors": [], "journal_poisoned": False}


class Transport:
    def __init__(self, *, down=False):
        self.arguments, self.down = [], down
    async def submit(self, operation, *, replay):
        if self.down:
            raise ConnectionError("synthetic outage")
        owner = self
        class Session:
            async def call_tool(self, name, args):
                owner.arguments.append(args)
                return brief.envelope(server.dispatch(name, args))
        return await operation(Session())


@pytest.mark.parametrize("supports_v2", [False, True])
@pytest.mark.parametrize("down", [False, True])
def test_relay_negotiates_only_advertised_format_and_budgets_final_notices(engine, supports_v2, down):
    transport = Transport(down=down)
    relay = DurableRelay(transport, None)
    relay.journal = Ledger()
    relay.brief_wire_formats = {"legacy", "compact_v2"} if supports_v2 else {"legacy"}
    relay.brief_budget_supported = supports_v2
    result = anyio.run(relay.call, "memory_session_brief", {"project": "P", "budget_tokens": 600})
    parsed = data(result)
    size, tokens = brief.measures(parsed)
    assert size <= 6000 and tokens <= 600
    assert (result.structuredContent is None) == supports_v2
    if not down:
        assert "unresolved_requests" in parsed["notices"]
        assert ("wire_format" in transport.arguments[0]) == supports_v2
        assert ("budget_tokens" in transport.arguments[0]) == supports_v2
    else:
        assert parsed["code"] == "upstream_unavailable"
        assert "unresolved_requests" in parsed["notices"]


def test_explicit_v2_on_unsupported_server_refuses_without_sending(engine):
    transport = Transport()
    relay = DurableRelay(transport, None)
    relay.journal = Ledger()
    result = anyio.run(relay.call, "memory_session_brief", {"wire_format": "compact_v2"})
    assert data(result)["code"] == "wire_format_unavailable"
    assert not transport.arguments


def test_old_relay_default_keeps_legacy_format(engine):
    # Frozen 4.0.6 behavior: no wire_format sent, and duplicated envelope rebuilt.
    old_upstream = server.dispatch("memory_session_brief", {"project": "P"})
    rebuilt = brief.envelope(brief.rebudget(old_upstream, ["unresolved_requests"]), structured=True)
    assert rebuilt.structuredContent is not None
    assert json.loads(rebuilt.content[0].text) == rebuilt.structuredContent


def test_catalog_never_advertises_single_body_with_required_output_schema():
    catalog = SessionToolCatalog()
    assert catalog.brief_contract() == ({"legacy"}, False)
    descriptor = Tool(name="memory_session_brief", inputSchema=SessionBriefIn.model_json_schema())
    catalog._tools = [descriptor]
    assert catalog.brief_contract() == ({"legacy", "compact_v2"}, True)
    descriptor.outputSchema = {"type": "object"}
    assert catalog.brief_contract() == ({"legacy"}, False)


@pytest.mark.parametrize("wire", ["legacy", "compact_v2"])
@pytest.mark.parametrize("budget", [200, 600, 1500])
def test_large_and_error_envelopes_respect_requested_whole_budget(engine, wire, budget):
    value = server.dispatch("memory_session_brief", {"project": "P", "wire_format": wire, "budget_tokens": budget})
    size, tokens = brief.measures(value)
    assert size <= 6000 and tokens <= budget
    value = server.dispatch("memory_session_brief", {"project": "x" * 10000, "wire_format": wire, "budget_tokens": budget})
    assert brief.measures(value)[1] <= budget
    error = server.dispatch("memory_session_brief", {"wire_format": wire, "budget_tokens": budget, "invalid": True})
    assert error["ok"] is False and brief.measures(error)[1] <= budget


def test_detailed_rejects_compact_only_options():
    with pytest.raises(ValueError, match="require compact mode"):
        SessionBriefIn(mode="detailed", budget_tokens=600)
    with pytest.raises(ValueError, match="require compact mode"):
        SessionBriefIn(mode="detailed", wire_format="compact_v2")


@pytest.mark.parametrize("wire", ["legacy", "compact_v2"])
def test_error_and_oversized_project_never_hide_unresolved_operations(engine, wire):
    transport = Transport()
    relay = DurableRelay(transport, None)
    relay.journal = Ledger()
    relay.brief_wire_formats = {"legacy", "compact_v2"}
    relay.brief_budget_supported = True
    result = anyio.run(relay.call, "memory_session_brief", {"project": "huge" * 3000, "wire_format": wire, "budget_tokens": 200})
    assert "unresolved_requests" in data(result)["notices"]
    assert brief.measures(data(result))[1] <= 200
    result = anyio.run(relay.call, "memory_session_brief", {"invalid": True, "wire_format": wire, "budget_tokens": 200})
    assert "unresolved_requests" in data(result)["notices"]
    assert brief.measures(data(result))[1] <= 200
