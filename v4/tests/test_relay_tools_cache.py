"""Real MCP SDK over in-memory streams: no TCP, server, store or neural model."""
from contextlib import asynccontextmanager
import json

import anyio
from mcp import ClientSession, types
from mcp.shared.message import SessionMessage
import pytest

from memory_os_v4.ops import relay
from memory_os_v4.ops.settings import OpsSettings


def test_catalog_keeps_last_good_snapshot_and_local_descriptor():
    upstream = types.Tool(name="memory_request_status", inputSchema={"type": "object"},
                          outputSchema={"type": "object", "required": ["server_only"]})
    search = types.Tool(name="memory_search", inputSchema={"type": "object"})
    catalog = relay.SessionToolCatalog()
    class Transport:
        fail = False
        async def submit(self, op, *, replay):
            assert replay
            if self.fail:
                raise RuntimeError("upstream unavailable")
            return types.ListToolsResult(tools=[search, upstream])
    transport = Transport()
    anyio.run(catalog.prefetch, transport)
    search.description = "changed externally"
    first = catalog.tools()
    assert [t.name for t in first] == ["memory_search", "memory_request_status"]
    assert first[0].description is None and first[-1].outputSchema is None
    first[0].inputSchema["pollution"] = True
    transport.fail = True
    anyio.run(catalog.prefetch, transport)
    assert "pollution" not in catalog.tools()[0].inputSchema
    assert len(catalog.tools()) == 2


def test_failed_initial_prefetch_advertises_only_local_status():
    catalog = relay.SessionToolCatalog()
    class Down:
        async def submit(self, *a, **kw):
            raise ConnectionError("down before first tools/list")
    anyio.run(catalog.prefetch, Down())
    assert [t.name for t in catalog.tools()] == ["memory_request_status"]


@pytest.mark.parametrize("prefetch_fails", [False, True])
def test_sdk_can_validate_local_status_without_upstream_after_disconnect(tmp_path, monkeypatch, prefetch_fails):
    """Reproduce ClientSession._validate_tool_result -> tools/list during outage.

    The first explicit client call is local status; no prior client tools/list
    fills its schema cache. The relay's real serve/decorators and SDK run intact.
    """
    settings = OpsSettings(url="http://127.0.0.1:9/mcp/", token_file=tmp_path / "token",
                           store_dir=tmp_path / "unused", backup_dir=tmp_path / "backup",
                           backup_key=tmp_path / "key", ops_dir=tmp_path / "ops", service_label=None)
    monkeypatch.setenv("MEMORY_V4_RELAY_JOURNAL_DIR", str(tmp_path / "ops/relay"))
    from memory_os_v4.ops.request_journal import RelayJournal
    orphan = RelayJournal(tmp_path / "ops/relay", settings.url, "previous-client")
    orphan.append({"request_id": "cut", "scope": '["project", "synthetic"]',
                   "operation": "memory_block_set", "raw_args_sha256": "a" * 64,
                   "store_instance_id": "synthetic"}, "sending")
    orphan.close()
    original = orphan.path.read_bytes()
    requests_seen = []
    init = types.InitializeResult(protocolVersion="2025-03-26", capabilities=types.ServerCapabilities(),
                                  serverInfo=types.Implementation(name="memory-os-v4", version="synthetic"))

    async def fake_owner(settings, requests, ready):
        ready(init)
        class Upstream:
            async def list_tools(self):
                requests_seen.append("list_tools")
                if prefetch_fails or len(requests_seen) > 1:
                    raise ConnectionError("down")
                return types.ListToolsResult(tools=[types.Tool(name="memory_request_status", inputSchema={},
                    outputSchema={"type": "object", "required": ["server_only"]})])
        async with requests:
            async for request in requests:
                try:
                    value = await request.op(Upstream())
                except Exception as exc:
                    value = relay._Failed(exc, sent=False)
                await relay._reply(request, value)

    async def scenario():
        to_server, server_read = anyio.create_memory_object_stream[SessionMessage](16)
        to_client, client_read = anyio.create_memory_object_stream[SessionMessage](16)
        @asynccontextmanager
        async def stdio():
            async with server_read, to_client:
                yield server_read, to_client
        monkeypatch.setattr(relay, "stdio_server", stdio)
        async with anyio.create_task_group() as group:
            group.start_soon(relay.serve, settings)
            async with ClientSession(client_read, to_server) as client:
                await client.initialize()
                with anyio.fail_after(2):
                    result = await client.call_tool("memory_request_status", {})
                    assert not result.isError
                    body = json.loads(result.content[0].text)
                    assert body["local_journals"] is True and body["replay_permitted"] is False
                    assert body["unresolved_count"] == 1 and body["unresolved"][0]["request_id"] == "cut"
                    for _ in range(3):
                        tools = (await client.list_tools()).tools
                        assert [t.name for t in tools] == ["memory_request_status"]
                    again = await client.call_tool("memory_request_status", {})
                    assert not again.isError
            await to_server.aclose()
            await client_read.aclose()
            group.cancel_scope.cancel()
    monkeypatch.setattr(relay, "owner", fake_owner)
    anyio.run(scenario)
    assert orphan.path.read_bytes() == original
    assert requests_seen == ["list_tools"]  # Only startup prefetch; zero outage RPCs.


def test_silent_prefetch_is_bounded_and_does_not_hide_parent_cancellation(monkeypatch):
    catalog = relay.SessionToolCatalog()
    class Silent:
        async def submit(self, *a, **kw):
            await anyio.sleep_forever()
    async def scenario():
        monkeypatch.setattr(relay, "CATALOG_PREFETCH_TIMEOUT_S", .01)
        with anyio.fail_after(1):
            await catalog.prefetch(Silent())
        assert [tool.name for tool in catalog.tools()] == ["memory_request_status"]
        monkeypatch.setattr(relay, "CATALOG_PREFETCH_TIMEOUT_S", 5)
        with anyio.move_on_after(.01) as outer:
            await catalog.prefetch(Silent())
            pytest.fail("Parent cancellation was swallowed")
        assert outer.cancel_called
    anyio.run(scenario)
