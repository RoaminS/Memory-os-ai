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
if fault == "warming":
    original = server.dispatch
    def dispatch(name, args):
        from pathlib import Path
        if name == "memory_request_status" and "arguments" in args and Path(os.environ["MEMORY_V4_DIR"]).parent.joinpath("warming").exists():
            return {"ok": False, "code": "engine_not_ready"}
        return original(name, args)
    server.dispatch = dispatch
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


@pytest.mark.parametrize("fault,code", [("warming", "upstream_not_ready"),
                                        ("outage", "preflight_unavailable")])
def test_405_preflight_refusal_through_sdk_then_manual_retry(harness, fault, code):
    h = harness
    marker = h.root / "warming"
    if fault == "warming":
        marker.write_text("synthetic preflight refusal")
    h.start("warming" if fault == "warming" else "none")
    args = {"request_id": "manual-after-ready", "name": "working_rules", "text": "ONCE",
            "project": "synthetic"}

    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            if fault == "outage":
                h.stop()
            with anyio.fail_after(10):
                refusal = await session.call_tool("memory_block_set", args)
            assert refusal.isError
            details = refusal.structuredContent
            assert details == json.loads(refusal.content[0].text)
            assert details["code"] == code and details["retryable"] is True
            assert details["attempt_outcome"] == "not_sent" and details["mutation_sent"] is False
            assert details["automatic_retry"] is False and details["logical_status_required"] is False
            local = payload(await session.call_tool("memory_request_status", {}))
            assert local["unresolved_count"] == 0
            assert local["connection"]["preflight_failure"]["code"] == code
            assert not (h.root / "store/requests.jsonl").exists()
            if fault == "warming":
                marker.unlink()
            else:
                h.start()
            with anyio.fail_after(5):
                assert not (await session.call_tool("memory_block_set", args)).isError
            completed = payload(await session.call_tool("memory_request_status", {"request_id": args["request_id"]}))
            assert completed["status"] == "completed" and completed["replay_permitted"] is False
            local = payload(await session.call_tool("memory_request_status", {}))
            assert local["connection"]["preflight_failure"] is None

    anyio.run(run)
    versions = [json.loads(line) for line in (h.root / "store/blocks.jsonl").read_text().splitlines()]
    assert len(versions) == 1 and versions[0]["text"] == "ONCE"


def test_S4_B09_read_only_window_keeps_reads_and_never_sends_mutation(harness):
    h = harness
    h.env['MEMORY_V4_WRITE_MODE'] = 'read_only'
    h.start()

    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            assert payload(await session.call_tool('memory_status', {}))['total'] == 0
            result = await session.call_tool('memory_block_set', {'name': 'working_rules', 'text': 'No write',
                                                               'request_id': 'window', 'project': 'synthetic'})
            assert result.isError and result.structuredContent['code'] == 'maintenance_window'
            assert result.structuredContent['outcome'] == 'not_started'
            assert result.structuredContent['retryable'] is True
            local = payload(await session.call_tool('memory_request_status', {}))
            assert local['unresolved_count'] == 0
            assert not (h.root / 'store/requests.jsonl').exists()
    anyio.run(run)


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
            assert json.loads(result.content[0].text)["code"] == "project_unknown"
            status = payload(await session.call_tool("memory_request_status", {"request_id": "canonical"}))
            assert status["status"] == "completed"
            inventory = payload(await session.call_tool("memory_request_status", {}))
            assert json.loads(inventory["recent_completed"][0]["scope"]) == ["project", "prj_test"]
    anyio.run(run)


def test_L4_rejected_token_is_explicit_then_recovers(harness):
    h = harness
    h.start()
    async def run():
        async with stdio_client(h.params()) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            original = h.token.read_bytes()
            h.rotate()  # Server still accepts original: deliberate 401, not a server outage.
            with anyio.fail_after(5):
                result = await session.call_tool("memory_status", {})
                assert result.isError and "authentication_rejected" in result.content[0].text
                local = payload(await session.call_tool("memory_request_status", {}))
                assert local["connection"]["last_error"]["code"] == "authentication_rejected"
            replacement = h.root / "restored-token"
            replacement.write_bytes(original)
            replacement.chmod(0o600)
            replacement.replace(h.token)
            with anyio.fail_after(5):
                assert payload(await session.call_tool("memory_status", {}))["embed_model"] == "hash"
    anyio.run(run)


def test_L5_capabilities_roundtrip_and_client_observation(harness):
    h = harness
    h.env['MEMORY_V4_CLIENT_ID'] = 'codex'
    h.start()
    async def run():
        async with stdio_client(h.params()) as (r,w), ClientSession(r,w) as session:
            await session.initialize()
            result=payload(await session.call_tool('memory_status',{}))
            caps=result['capabilities']
            assert caps['store']['backend']=='jsonl'
            assert caps['encoding']['profile']['device']=='hash'
            assert caps['brief']['default_mode']=='compact'
            local=payload(await session.call_tool('memory_request_status',{}))
            proof=local['connection']['observation']
            assert proof['client_id']=='codex' and proof['successful_control']
            assert proof['server']['commit']=='a'*40
            assert proof['server']['store_instance_id']==caps['store']['instance_id']
            assert len(proof['relay']['source_sha256'])==64
    anyio.run(run)


def test_L1a_real_tcp_compact_and_offline_counter_failure(harness):
    h=harness
    h.start()
    async def run():
        async with stdio_client(h.params()) as (r,w), ClientSession(r,w) as session:
            await session.initialize()
            payload(await session.call_tool('memory_add_facts',{'project':'brief-test','facts':[{'text':'Synthetic current task: verify compact brief.'}]}))
            compact=await session.call_tool('memory_session_brief',{'project':'brief-test'})
            data=payload(compact)
            from memory_os_v4.brief_tokens import status, counter
            proof={'counter_ready':status()['ready'],'measured':False}
            if proof['counter_ready']:
                wire=json.dumps(compact.model_dump(mode='json',by_alias=True,exclude_none=True),ensure_ascii=False,separators=(',',':'))
                proof.update(measured=True,bytes=len(wire.encode()),tokens=counter()(wire))
                assert proof['bytes']<=6000 and proof['tokens']<=1500
                assert data['mode']=='compact' and data['references']
                detailed=payload(await session.call_tool('memory_session_brief',{'project':'brief-test','mode':'detailed','expand':[data['references'][0]['id']]}))
                assert detailed['items'][0]['text']=='Synthetic current task: verify compact brief.'
            else:
                assert data['code']=='tokenizer_unavailable'
            (h.root/'brief-envelope.json').write_text(json.dumps(proof))
            huge=await session.call_tool('memory_session_brief',{'expand':['x'*10000]})
            assert len(json.dumps(huge.model_dump(mode='json',exclude_none=True)).encode())<1000
    anyio.run(run)
