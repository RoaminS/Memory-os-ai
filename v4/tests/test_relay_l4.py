"""Remaining L4 failure paths using real owner lifecycle, no network/model."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import httpx
import pytest

from memory_os_v4.ops import relay
from memory_os_v4.ops.auth import SecretFileError


@pytest.mark.parametrize("exc,code", [
    (SecretFileError("token_missing: nothing sent"), "token_missing"),
    (SecretFileError("unsafe"), "token_invalid"),
    (TimeoutError(), "control_timeout"),
    (ConnectionError("SECRET must not escape"), "upstream_unavailable"),
    (RuntimeError("identity_changed: stop"), "identity_changed"),
    (httpx.HTTPStatusError("SECRET", request=httpx.Request("GET", "http://localhost"),
                           response=httpx.Response(401)), "authentication_rejected"),
])
def test_redacted_nested_error_codes(exc, code):
    assert relay.error_code(ExceptionGroup("transport", [exc])) == code


def run_owner(monkeypatch, scenario, versions=("current", "current")):
    opened, closed, calls = [], [], []
    async def fake_open(stack, settings):
        number = len(opened)
        @asynccontextmanager
        async def lifetime():
            opened.append(number)
            try:
                yield number
            finally:
                closed.append(number)
        await stack.enter_async_context(lifetime())
        return number, SimpleNamespace(serverInfo=SimpleNamespace(name="engine", version=versions[number]))
    monkeypatch.setattr(relay, "_open", fake_open)
    monkeypatch.setattr(relay, "read_token", lambda path: "valid")
    async def main():
        send, receive = anyio.create_memory_object_stream(16)
        ready = anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(relay.owner, SimpleNamespace(token_file=None), receive, lambda value: ready.set())
            await ready.wait()
            await scenario(relay.Relay(send), calls)
            await send.aclose()
        assert opened == closed
    anyio.run(main)
    return opened


def test_abandoned_prefetch_frees_owner_even_when_port_alive(monkeypatch):
    monkeypatch.setattr(relay, "CATALOG_PREFETCH_TIMEOUT_S", .03)
    async def scenario(r, calls):
        async def silent(session):
            calls.append("silent")
            await anyio.sleep_forever()
        async def value(session):
            calls.append("control")
            return "ok"
        with anyio.move_on_after(.03) as scope:
            await r.submit(silent, replay=True)
        assert scope.cancel_called
        with anyio.fail_after(1):
            assert await r.control(value) == "ok"
        assert calls == ["silent", "control"]
    assert run_owner(monkeypatch, scenario) == [0, 1]


def test_cancelled_queued_mutation_is_never_sent(monkeypatch):
    async def scenario(r, calls):
        entered, release = anyio.Event(), anyio.Event()
        async def busy(session):
            entered.set()
            await release.wait()
        async def mutation(session):
            calls.append("BAD")
        async def first():
            await r.submit(busy, replay=False)
        async with anyio.create_task_group() as tg:
            tg.start_soon(first)
            await entered.wait()
            with anyio.move_on_after(.02):
                await r.submit(mutation, replay=False)
            release.set()
        async def value(session):
            return "ok"
        assert await r.control(value) == "ok"
        assert calls == []
    assert run_owner(monkeypatch, scenario) == [0]


def test_identity_change_is_latched_until_client_restart(monkeypatch):
    async def scenario(r, calls):
        async def fail(session):
            raise ConnectionError("lost")
        with pytest.raises(RuntimeError, match="uncertain"):
            await r.submit(fail, replay=False)
        for _ in range(2):
            with pytest.raises(RuntimeError, match="identity_changed"):
                await r.control(fail)
            assert r.last_error["code"] == "identity_changed"
    assert run_owner(monkeypatch, scenario, ("current", "changed")) == [0, 1]
