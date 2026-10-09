"""MCP SDK servers use their own sessions; clean only nonce-verified private jobs."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from .guard import ResourceStop


def verified_pid(pid: int, owner: str) -> bool:
    if pid <= 1 or pid == os.getpid():
        return False
    try:
        if os.getpgid(pid) != pid:
            return False
        out = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=2)
        return out.returncode == 0 and "bench.e2e.offline_server" in out.stdout and \
            f"--eval-owner={owner}" in out.stdout.split()
    except (ProcessLookupError, PermissionError):
        return False


def cleanup(registry: Path, owner: str) -> None:
    for path in registry.glob("*.json"):
        record = json.loads(path.read_text())
        if record.get("owner") != owner:
            continue
        pid = record["pid"]
        if type(pid) is not int or not verified_pid(pid, owner):
            continue
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while verified_pid(pid, owner) and time.monotonic() < deadline:
                time.sleep(.1)
            if verified_pid(pid, owner):
                os.killpg(pid, signal.SIGKILL)
                time.sleep(.1)
            if verified_pid(pid, owner):
                raise ResourceStop("Private MCP server survived termination; no next worker allowed")
        except ProcessLookupError:
            pass
        except PermissionError as e:
            raise ResourceStop("Cannot terminate owned MCP server") from e
