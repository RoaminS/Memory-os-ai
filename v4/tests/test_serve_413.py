"""4.1.3 Lot 3: `memory-os-admin serve`, the service in the foreground (no launchd/systemd)."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from memory_os_v4.ops import serve
from memory_os_v4.ops.auth import create_token, read_token
from memory_os_v4.ops.settings import OpsSettings

UNRETAINED = {"cell_id": None, "retained": False, "label": "debian-12-x86_64-py311",
              "refusal": "debian-12-x86_64-py311 is not one of the retained cells", "sqlite_ok": True,
              "sqlite_version": "3.53.4", "implementation": "cpython"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    port = free_port()
    for key, value in {"HOME": str(home), "MEMORY_V4_URL": f"http://127.0.0.1:{port}/mcp/",
                       "MEMORY_V4_TOKEN_FILE": str(home / "token"), "MEMORY_V4_DIR": str(home / "store"),
                       "MEMORY_V4_OPS_DIR": str(home / "ops"),
                       "MEMORY_V4_SERVICE_LABEL": "local.memory-os-serve-test"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("MEMORY_V4_CL100K_PATH", raising=False)
    return home, port


def settings():
    return OpsSettings.from_env()


def refused(code, **kwargs):
    with pytest.raises(serve.ServeRefused) as caught:
        serve.plan(settings(), **kwargs)
    assert caught.value.code == code, caught.value
    return caught.value


def test_refusals_before_anything_starts(installation, monkeypatch):
    home, _ = installation
    refused("setup_profile_required", home=home, detected=UNRETAINED)
    refused("runtime_unqualified", profile="lexical", home=home, detected=UNRETAINED)
    refused("platform_unqualified_lexical_only", profile="semantic", allow_unqualified=True, home=home,
            detected=UNRETAINED)
    refused("sqlite_too_old", profile="lexical", allow_unqualified=True, home=home,
            detected={**UNRETAINED, "sqlite_ok": False, "sqlite_version": "3.45.1"})
    refused("python_not_cpython", profile="lexical", allow_unqualified=True, home=home,
            detected={**UNRETAINED, "implementation": "pypy"})
    refused("token_missing", profile="lexical", allow_unqualified=True, home=home, detected=UNRETAINED)
    assert not (home / "token").exists()                      # serve never creates the token
    (home / "token").write_text("x" * 43 + "\n")
    os.chmod(home / "token", 0o644)
    refused("token_unsafe", profile="lexical", allow_unqualified=True, home=home, detected=UNRETAINED)
    monkeypatch.setattr(serve.sys, "platform", "win32")
    refused("platform_windows_unsupported", profile="lexical", allow_unqualified=True, home=home,
            detected=UNRETAINED)


def test_unqualified_plan_is_the_installation_environment(installation, monkeypatch):
    home, port = installation
    create_token(home / "token")
    monkeypatch.setenv("MEMORY_V4_HOST", "0.0.0.0")             # inherited MEMORY_V4_* never reach the server
    monkeypatch.setenv("MEMORY_V4_WRITE_MODE", "read_only")
    result = serve.plan(settings(), profile="lexical", allow_unqualified=True, home=home, detected=UNRETAINED)
    assert result["argv"][1:] == ["-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
                                  "--port", str(port), "--warm"]
    env = result["env"]
    assert env["MEMORY_V4_PLATFORM_UNQUALIFIED"] == "1" and env["MEMORY_V4_RETRIEVAL_PROFILE"] == "lexical"
    assert env["MEMORY_V4_DIR"] == str(home / "store") and env["MEMORY_V4_TOKEN_FILE"] == str(home / "token")
    assert "MEMORY_V4_HOST" not in env and "MEMORY_V4_WRITE_MODE" not in env
    assert "MEMORY_V4_CL100K_PATH" not in env                    # the embedded table needs no variable
    assert result["notices"] and "unqualified platform" in result["notices"][0]
    assert read_token(home / "token") not in json.dumps(result)  # the token value is never in the plan


def test_retained_cell_plan_needs_a_qualified_build_and_runtime(installation, monkeypatch):
    home, _ = installation
    create_token(home / "token")
    from memory_os_v4 import readiness
    from memory_os_v4.ops import cell, qualified_runtime
    retained = {**UNRETAINED, "cell_id": "macos-arm64-py311", "retained": True, "label": "macos-arm64-py311",
                "refusal": None}
    monkeypatch.setattr(cell, "acceptance", lambda detected, profile: {"ok": True, "code": None, "message": None})
    monkeypatch.setattr(qualified_runtime, "inspect_current", lambda **kw: {"ok": True, "mismatches": {}})
    monkeypatch.setattr(readiness, "build_identity", lambda: {"identity_source": "env", "commit": "a" * 40})
    refused("build_unqualified", profile="lexical", home=home, detected=retained)
    monkeypatch.setattr(readiness, "build_identity",
                        lambda: {"identity_source": "embedded_descriptor", "commit": "a" * 40, "source_dirty": False})
    result = serve.plan(settings(), profile="lexical", home=home, detected=retained)
    assert not result["unqualified"] and "MEMORY_V4_PLATFORM_UNQUALIFIED" not in result["env"]
    assert result["notices"] == []
    monkeypatch.setattr(qualified_runtime, "inspect_current", lambda **kw: {"ok": False, "mismatches": {"numpy": 1}})
    refused("runtime_unqualified", profile="lexical", home=home, detected=retained)


def test_dry_run_prints_the_command_and_starts_nothing(installation, capsys, monkeypatch):
    home, port = installation
    create_token(home / "token")
    from memory_os_v4.ops import cell, cli
    monkeypatch.setattr(cell, "detect", lambda: UNRETAINED)
    assert cli.main(["serve", "--profile", "lexical", "--allow-unqualified-platform", "--dry-run"]) == 0
    captured = capsys.readouterr()
    printed = json.loads(captured.out)
    assert printed["unqualified"] and printed["argv"][-3:] == ["--port", str(port), "--warm"]
    assert "WARNING: unqualified platform" in captured.err
    assert not (home / "store").exists()                        # nothing started, nothing written
    assert cli.main(["serve", "--profile", "lexical", "--dry-run"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "runtime_unqualified"


LAUNCHER = """
import sys
from memory_os_v4.ops import cell, cli
cell.detect = lambda: {"cell_id": None, "retained": False, "label": "debian-12-x86_64-py311",
                       "refusal": "debian-12-x86_64-py311 is not one of the retained cells",
                       "sqlite_ok": True, "sqlite_version": "3.53.4", "implementation": "cpython"}
from memory_os_v4.ops import serve
real_plan = serve.plan
def plan(*args, **kwargs):
    # source checkout only: the declarative build identity an installed wheel carries in its descriptor
    result = real_plan(*args, **kwargs)
    result["env"]["MEMORY_V4_BUILD_COMMIT"] = "a" * 40
    return result
serve.plan = plan
sys.exit(cli.main(["serve", "--profile", "lexical", "--allow-unqualified-platform"]))
"""


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_serve_runs_in_the_foreground_and_stops_cleanly_on_a_signal(installation, stop):
    """The process is replaced by the server: same PID, loopback only, clean stop, exit code 0, lease released."""
    home, port = installation
    create_token(home / "token")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    log = (home / "serve.log").open("wb")
    process = subprocess.Popen([sys.executable, "-c", LAUNCHER], env=env, stdout=log, stderr=log)
    log.close()
    try:
        deadline = time.monotonic() + 60
        while True:
            request = urllib.request.Request(f"http://127.0.0.1:{port}/ready",
                                             headers={"Authorization": "Bearer " + read_token(home / "token")})
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    ready = json.load(response)
                    break
            except Exception:
                pass
            assert process.poll() is None, (home / "serve.log").read_text()[-3000:]
            assert time.monotonic() < deadline, (home / "serve.log").read_text()[-3000:]
            time.sleep(0.1)
        assert ready.get("capabilities", {}).get("platform", {}).get("cell") == "unqualified", ready
        process.send_signal(stop)
        assert process.wait(timeout=30) == 0, (home / "serve.log").read_text()[-3000:]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert "WARNING: unqualified platform" in (home / "serve.log").read_text()
    store = home / "store"
    assert (store / "store.sqlite3").is_file()
    # a clean close: the last SQLite connection checkpointed and removed its side files (a kill leaves them)
    assert not (store / "store.sqlite3-wal").exists() and not (store / "store.sqlite3-shm").exists()
    from memory_os_v4.writer_lock import WriterLock
    lease = WriterLock(home / "store")                          # the writer lease was released at exit
    lease.close()
