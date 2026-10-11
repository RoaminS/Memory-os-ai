"""Claude-only real TCP: hash engines/stores/tokens/journals entirely disposable."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from memory_os_v4.ops.auth import create_token, read_token


class Harness:
    def __init__(self, root):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.root, self.process = root, None
        self.token = root / "token"
        create_token(self.token)
        self.env = {**os.environ, "MEMORY_V4_DIR": str(root / "store"), "MEMORY_V4_EMBED_MODEL": "hash",
                    "MEMORY_V4_OPS_DIR": str(root / "ops"), "MEMORY_V4_RELAY_JOURNAL_DIR": str(root / "ops/relay"),
                    "MEMORY_V4_TOKEN_FILE": str(self.token), "MEMORY_V4_URL": f"http://127.0.0.1:{self.port}/mcp/",
                    "MEMORY_V4_BUILD_COMMIT": "a" * 40, "MEMORY_V4_STORE_BACKEND": "jsonl",
                    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "MEMORY_V4_QUALIFICATION_METRICS": "0",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    def stop(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None

    def rotate(self):
        temporary = self.root / "token-new"
        temporary.write_text(secrets.token_urlsafe(32) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.token)

    def start(self, fault="none"):
        self.stop()
        # Only this isolated child is fault-injected. Never import/configure the live server.
        code = '''
import os,sys
from memory_os_v4 import server
fault = sys.argv[1]
if fault == "identity":
    server.server.version = "synthetic-changed"
if fault == "partial":
    handler = server.TOOL_HANDLERS["memory_block_set"]
    def partial(e, args):
        result = handler(e, args)
        if args.request_id == "cut": os._exit(78)
        return result
    server.TOOL_HANDLERS["memory_block_set"] = partial
elif fault == "committed":
    original = server.dispatch
    def dispatch(name, args):
        result = original(name, args)
        if name == "memory_block_set" and args.get("request_id") == "cut": os._exit(79)
        return result
    server.dispatch = dispatch
sys.argv = [sys.argv[0], "--transport", "http", "--port", sys.argv[2], "--warm"]
server.run()
'''
        log = (self.root / "server.log").open("ab")
        self.process = subprocess.Popen([sys.executable, "-c", code, fault, str(self.port)], env=self.env,
                                        stdout=log, stderr=log)
        log.close()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}/ready",
                                             headers={"Authorization": "Bearer " + read_token(self.token)})
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        return
            except Exception:
                pass
            if self.process.poll() is not None:
                raise AssertionError((self.root / "server.log").read_text()[-2000:])
            time.sleep(.05)
        raise AssertionError("Hash server not ready after 30 s")

    def params(self):
        return StdioServerParameters(command=sys.executable, args=["-m", "memory_os_v4.ops.relay"], env=self.env)


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.stop()


def payload(result):
    assert not result.isError, result
    return json.loads(result.content[0].text)


def test_C07_thirty_restart_rotation_cycles(harness):
    h = harness
    h.start()
    measurements = []
    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            for i in range(30):
                h.stop()
                h.rotate()
                h.start()
                start = time.monotonic()
                with anyio.fail_after(5):
                    result = await session.call_tool("memory_status", {})
                assert payload(result)["embed_model"] == "hash"
                measurements.append(time.monotonic() - start)
            # Missing/unsafe tokens MUST fail without persisting a mutation.
            for mode in (0o644, None):
                if mode is None:
                    h.token.unlink()
                else:
                    h.token.chmod(mode)
                with anyio.fail_after(5):
                    bad = await session.call_tool("memory_block_set", {"name": "working_rules", "text": "NEVER", "request_id": "invalid-token"})
                assert bad.isError
            assert not (h.root / "store/requests.jsonl").exists()
    anyio.run(run)
    (h.root / "cycles.json").write_text(json.dumps({"cycles": 30, "seconds_after_ready": measurements,
                                                    "max_s": max(measurements)}))


@pytest.mark.parametrize("fault,expected", [("partial", "pending"), ("committed", "completed")])
def test_C02_C04_C08_disconnect_and_new_relay_adopts_journal(harness, fault, expected):
    h = harness
    h.start(fault)
    args = {"request_id": "cut", "name": "working_rules", "text": "ONCE", "project": "synthetic"}
    async def first():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            with anyio.fail_after(10):
                result = await session.call_tool("memory_block_set", args)
            assert result.isError
            report = payload(await session.call_tool("memory_request_status", {}))
            assert report["unresolved_count"] == 1
    anyio.run(first)
    h.start()
    async def second():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            assert payload(await session.call_tool("memory_request_status", {}))["unresolved_count"] == 1
            found = payload(await session.call_tool("memory_request_status", {"request_id": "cut"}))
            assert found["status"] == expected and found["replay_permitted"] is False
            again = await session.call_tool("memory_block_set", args)
            assert again.isError == (expected == "pending")
    anyio.run(second)
    versions = [json.loads(line) for line in (h.root / "store/blocks.jsonl").read_text().splitlines()]
    assert len(versions) == 1 and versions[0]["text"] == "ONCE"


def test_L4_identity_changed_survives_outage_catalog(harness):
    h = harness
    h.start()
    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            h.stop()
            h.rotate()
            h.start("identity")
            for _ in range(2):
                with anyio.fail_after(5):
                    result = await session.call_tool("memory_status", {})
                    assert result.isError and "identity_changed" in result.content[0].text
                    status = payload(await session.call_tool("memory_request_status", {}))
                    assert status["connection"]["last_error"]["code"] == "identity_changed"
                    assert status["connection"]["live_probe"] is False
            assert not (h.root / "store/requests.jsonl").exists()
    anyio.run(run)


def test_L7a_registry_scopes_through_packaged_relay(harness):
    h = harness
    registry = h.root / "projects.json"
    registry.write_text(json.dumps({"schema_version": 1, "projects": [
        {"id": "prj_test", "aliases": ["legacy"]}, {"id": "prj_other", "aliases": []}]}))
    registry.chmod(0o600)
    h.env["MEMORY_V4_PROJECT_REGISTRY"] = str(registry)
    h.start()
    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            args = {"name": "project_state", "text": "SCOPED", "project": "legacy", "request_id": "canonical"}
            payload(await session.call_tool("memory_block_set", args))
            assert payload(await session.call_tool("memory_block_get", {"project": "prj_test"}))["blocks"]["project_state"] == "SCOPED"
            assert payload(await session.call_tool("memory_block_get", {"project": "prj_other"}))["blocks"]["project_state"] == ""
            result = await session.call_tool("memory_block_set", {**args, "project": "unknown", "request_id": "rejected"})
            assert result.isError
            status = payload(await session.call_tool("memory_request_status", {"request_id": "canonical"}))
            assert status["status"] == "completed"
            inventory = payload(await session.call_tool("memory_request_status", {}))
            assert json.loads(inventory["recent_completed"][0]["scope"]) == ["project", "prj_test"]
    anyio.run(run)
