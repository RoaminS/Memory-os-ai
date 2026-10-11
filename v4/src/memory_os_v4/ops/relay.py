"""stdio → streamable-HTTP relay to the shared memory-os-v4 server (``memory-os-relay``).

For stdio-only MCP clients (Claude Desktop) and for clients that would otherwise need the bearer token in a
plain-text config (Claude Code ``.mcp.json``, Codex): the relay reads the token file on every (re)connection.

Lifecycle: ONE owner task opens, uses and closes the upstream connection (AnyIO task groups and cancel scopes
must be exited by the task that entered them). Handlers send requests to the owner through a channel; requests
are serialized. On a transport failure the owner closes the connection in its own task and reconnects for the next
request.

Mutations are never replayed: if the connection drops during a tool call, the server may already have applied it,
so the relay reports an *uncertain result* instead of risking a duplicate write. Tool listing (read-only) is
retried once after reconnecting.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import anyio
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .auth import SecretFileError, http_client, read_token
from .durable_relay import DurableRelay
from .settings import OpsSettings

UNCERTAIN = (
    "memory-os relay: uncertain result, the connection was lost during the call. Do not repeat the "
    "mutation until its status is resolved: an empty search does not prove it was not applied."
)
UNAVAILABLE = "memory-os relay: the memory server is unreachable; nothing was sent."

Operation = Callable[[ClientSession], Awaitable[Any]]
CATALOG_PREFETCH_TIMEOUT_S = 5.0


@dataclass
class _Request:
    op: Operation
    reply: MemoryObjectSendStream = field(repr=False)
    rotations: int = 0
    abandoned: anyio.Event = field(default_factory=anyio.Event, repr=False)


class _Failed:
    def __init__(self, exc: BaseException, sent: bool) -> None:
        self.exc, self.sent = exc, sent


async def _port_alive(settings: OpsSettings) -> bool:
    """TCP-level liveness: a busy server still completes the handshake; a dead one refuses it."""
    try:
        with anyio.fail_after(2):
            stream = await anyio.connect_tcp("127.0.0.1" if "::1" not in settings.url else "::1", settings.port)
        await stream.aclose()
        return True
    except (OSError, TimeoutError):
        return False


async def _guarded(op: Operation, session: ClientSession, settings: OpsSettings,
                   abandoned: anyio.Event | None = None) -> Any:
    """Run ``op``; abort it if the server stops accepting connections (the MCP client would otherwise wait
    forever for a response that will never come). Two consecutive refusals, 1 s apart, mean the server is gone."""
    box: dict[str, Any] = {}

    async def run(scope: anyio.CancelScope) -> None:
        box["value"] = await op(session)
        scope.cancel()

    async def watch() -> None:
        refusals = 0
        while True:
            await anyio.sleep(1.0)
            refusals = 0 if await _port_alive(settings) else refusals + 1
            if refusals >= 2:
                raise ConnectionError("memory server stopped accepting connections")

    async def cancelled() -> None:
        if abandoned is None:
            await anyio.sleep_forever()
        else:
            await abandoned.wait()
            raise TimeoutError("caller abandoned operation; outcome may be uncertain")

    async with anyio.create_task_group() as tg:
        tg.start_soon(run, tg.cancel_scope)
        tg.start_soon(watch)
        tg.start_soon(cancelled)
    return box["value"]


async def _open(stack: AsyncExitStack, settings: OpsSettings) -> tuple[ClientSession, types.InitializeResult]:
    if read_token(settings.token_file) is None:
        raise SecretFileError("token_missing: nothing sent")
    client = await stack.enter_async_context(http_client(settings))
    read, write, _ = await stack.enter_async_context(streamable_http_client(settings.url, http_client=client))
    session = await stack.enter_async_context(ClientSession(read, write))
    with anyio.fail_after(3):
        init = await session.initialize()
    return session, init


async def owner(
    settings: OpsSettings,
    requests: MemoryObjectReceiveStream,
    ready: Callable[[types.InitializeResult | BaseException], None],
) -> None:
    """Single owner of the upstream connection. ``ready`` is called once with the first initialize result (or the
    first connection error). After a failure, the next request triggers one reconnection attempt before it is
    served; if that attempt fails the request is answered "unavailable" (nothing was sent)."""
    first = True
    initial_identity = None
    identity_blocked = False
    carry: _Request | None = None  # received, not yet sent upstream
    inflight: _Request | None = None  # sent upstream, no answer yet
    async with requests:
        while True:
            try:
                async with AsyncExitStack() as stack:  # entered AND exited in this task only
                    if identity_blocked:
                        raise RuntimeError("identity_changed: client restart required")
                    token = read_token(settings.token_file)
                    session, init = await _open(stack, settings)
                    identity = (init.serverInfo.name, getattr(init.serverInfo, "version", None))
                    if initial_identity is not None and identity != initial_identity:
                        identity_blocked = True
                        raise RuntimeError("identity_changed: server handshake changed; restart client after review")
                    initial_identity = identity
                    if first:
                        first = False
                        ready(init)
                    while True:
                        req, carry = (carry, None) if carry is not None else (await _next_or_none(requests), None)
                        if req is None:
                            return  # channel closed: relay shutting down
                        if req.abandoned.is_set():
                            await req.reply.aclose()
                            continue
                        carry = req  # not sent; preserve across token validation failure.
                        if read_token(settings.token_file) != token:
                            if req.rotations >= 1:
                                raise ValueError("Token changed repeatedly; nothing sent")
                            req.rotations += 1
                            break  # atomic token rotation: reconnect before the next request, never replay.
                        carry = None
                        inflight = req
                        try:
                            result = await _guarded(req.op, session, settings, req.abandoned)
                        except Exception as exc:  # transport failure while the request was in flight
                            inflight = None
                            await _reply(req, _Failed(exc, sent=True))
                            break  # leave the stack here (same task); reconnect on the next request
                        inflight = None
                        await _reply(req, result)
            except Exception as exc:  # connection lost (transport task group failed), or could not (re)connect
                if first:
                    ready(exc)
                    return
                if inflight is not None:  # the transport died under an in-flight request: outcome uncertain
                    await _reply(inflight, _Failed(exc, sent=True))
                    inflight = None
                if carry is not None:
                    await _reply(carry, _Failed(exc, sent=False))
                    carry = None
            if carry is None:
                carry = await _next_or_none(requests)
            if carry is None:
                return


async def _next_or_none(requests: MemoryObjectReceiveStream) -> _Request | None:
    try:
        return await requests.receive()
    except (anyio.EndOfStream, anyio.ClosedResourceError):
        return None


async def _reply(req: _Request, value: Any) -> None:
    try:
        async with req.reply:
            await req.reply.send(value)
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        pass


def _identity_changed(exc):
    return "identity_changed" in str(exc) or any(_identity_changed(e) for e in getattr(exc, "exceptions", ()))


def error_code(exc):
    """Stable redacted diagnostics, including nested transport exception groups."""
    import httpx
    if _identity_changed(exc):
        return "identity_changed"
    if isinstance(exc, SecretFileError):
        return "token_missing" if str(exc).startswith("token_missing:") else "token_invalid"
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
        return "authentication_rejected"
    if isinstance(exc, TimeoutError):
        return "control_timeout"
    for child in getattr(exc, "exceptions", ()):
        code = error_code(child)
        if code != "upstream_unavailable":
            return code
    return "upstream_unavailable"


class Relay:
    """Handler-side facade: submit an operation to the owner and wait for its result."""

    def __init__(self, send: MemoryObjectSendStream) -> None:
        self._send = send
        self.last_error = None
        self.handshake = None

    async def control(self, op: Operation) -> Any:
        """Bound only control reads, not slow searches or mutations. Cancellation
        wakes the owner even if a silent upstream still accepts TCP connections."""
        try:
            with anyio.fail_after(4):
                return await self.submit(op, replay=True)
        except TimeoutError:
            self.last_error = {"code": "control_timeout", "sent": None, "replay_permitted": False}
            raise RuntimeError("control_timeout: no mutation replay; inspect local request status") from None

    async def submit(self, op: Operation, *, replay: bool) -> Any:
        for attempt in (0, 1):
            reply_send, reply_recv = anyio.create_memory_object_stream(1)
            request = _Request(op, reply_send)
            try:
                await self._send.send(request)
                async with reply_recv:
                    value = await reply_recv.receive()
            finally:
                request.abandoned.set()
            if not isinstance(value, _Failed):
                self.last_error = None
                return value
            code = error_code(value.exc)
            self.last_error = {"code": code, "sent": value.sent,
                               "replay_permitted": False}
            if not value.sent:
                if _identity_changed(value.exc):
                    raise RuntimeError("identity_changed: restart the client after operator review") from value.exc
                raise RuntimeError(f"{code}: {UNAVAILABLE}") from value.exc
            if not replay or attempt == 1:
                raise RuntimeError(UNCERTAIN) from value.exc
        raise AssertionError("unreachable")


class SessionToolCatalog:
    """Session-bound descriptors, not a claim that the upstream is reachable.

    MCP clients (and the server SDK) can list tools while validating a LOCAL
    status result. That path must never queue an upstream request, even when a
    mutation is in flight. New upstream capabilities require a new client session.
    """

    def __init__(self) -> None:
        self._tools: list[types.Tool] = []

    async def prefetch(self, relay: Relay) -> None:
        try:
            with anyio.move_on_after(CATALOG_PREFETCH_TIMEOUT_S):
                result = await relay.submit(lambda session: session.list_tools(), replay=True)
                self._tools = [tool.model_copy(deep=True) for tool in result.tools
                               if tool.name != "memory_request_status"]
        except Exception:
            # Preserve a previous valid snapshot if a refresh ever fails. Never
            # catch cancellation or advertise invented upstream capabilities.
            pass

    def tools(self) -> list[types.Tool]:
        from ..models import RequestStatusIn
        # Always own this descriptor: an upstream outputSchema could reject the
        # relay-local unresolved inventory, which has a different response shape.
        local = types.Tool(name="memory_request_status",
                           description="Local unresolved calls; no automatic replay. Available during upstream outages.",
                           inputSchema=RequestStatusIn.model_json_schema())
        return [tool.model_copy(deep=True) for tool in self._tools] + [local]


async def serve(settings: OpsSettings) -> None:
    send, recv = anyio.create_memory_object_stream(16)
    init_box: dict[str, Any] = {}
    init_event = anyio.Event()

    def ready(value) -> None:
        init_box["value"] = value
        init_event.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(owner, settings, recv, ready)
        await init_event.wait()
        init = init_box["value"]
        if isinstance(init, BaseException):
            print(f"memory-os relay: cannot reach {settings.url}: {error_code(init)}", file=sys.stderr)
            await send.aclose()
            raise SystemExit(1)
        relay = Relay(send)
        relay.handshake = {"name": init.serverInfo.name, "version": init.serverInfo.version}
        durable = DurableRelay(relay, settings)
        server = Server(init.serverInfo.name, version=init.serverInfo.version,
                        instructions=init.instructions)

        catalog = SessionToolCatalog()

        @server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return catalog.tools()

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict):
            res = await durable.call(name, arguments)
            if res.isError:
                text = "\n".join(c.text for c in res.content if isinstance(c, types.TextContent))
                raise RuntimeError(text or f"{name} failed")
            if res.structuredContent is not None:
                return res.content, res.structuredContent
            return res.content

        try:
            await catalog.prefetch(relay)
            async with stdio_server() as (r, w):
                await server.run(r, w, server.create_initialization_options())
        finally:
            durable.close()
            await send.aclose()  # the owner exits its connection in its own task


def main() -> None:
    anyio.run(serve, OpsSettings.from_env())


if __name__ == "__main__":
    main()
