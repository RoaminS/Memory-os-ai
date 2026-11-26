#!/usr/bin/env python3
"""4.1.3 Lot 3 (decision D8): smoke test of the minimal image on a disposable CI runner. Never pushes the image.

* The token is a mounted FILE, mode 0600, owned by the image user (uid 10001); never an environment variable.
* Published on the host loopback ONLY, same port on both sides (127.0.0.1:8765:8765).
* /ready answers 200 with the token; /health refuses an unauthenticated request; a foreign Host header is refused
  (Host/Origin protection kept with 0.0.0.0 inside the container); Docker reports the container healthy.
* A fact written through the container's relay is found again after a restart (data persisted in the volume).
* ``docker stop`` ends it with exit code 0 (clean stop, store closed).
* The image as built, WITHOUT the opt-in, listens on the container loopback only: unreachable from the host
  (decision D8: ``--container-listen-all`` is appended at ``docker run``, never the default).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

PORT = 8765
NAME = "memory-os-ci"
VOLUME = "memory-os-ci-data"


def docker(*args, check=True, timeout=120):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=timeout)


def get(path, token=None, host=None, timeout=2):
    headers = {"Authorization": "Bearer " + token} if token else {}
    if host:
        headers["Host"] = host
    request = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, ValueError) as exc:
        return None, {"error": type(exc).__name__}


RELAY_CALL = """
import json, sys, anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
action = sys.argv[1]
async def run():
    params = StdioServerParameters(command="/opt/memory-os/venv/bin/memory-os-relay", args=[])
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        if action == "write":
            result = await session.call_tool("memory_add_facts", {"project": "ci-persistence", "facts": [
                {"text": "Container persistence witness: the volume keeps this fact."}]})
        else:
            result = await session.call_tool("memory_search", {"project": "ci-persistence",
                                                               "query": "container persistence witness volume"})
        print(json.dumps({"error": result.isError, "text": result.content[0].text if result.content else ""}))
anyio.run(run)
"""


def relay(name, action):
    out = docker("exec", name, "/opt/memory-os/venv/bin/python", "-I", "-c", RELAY_CALL, action, timeout=120).stdout
    return json.loads(out.strip().splitlines()[-1])


def wait_ready(token, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status, body = get("/ready", token)
        if status == 200:
            return body
        time.sleep(1)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True)
    report = {"image": args.image, "checks": [], "ok": False}

    def check(name, ok, detail=None):
        report["checks"].append({"check": name, "ok": bool(ok), **({"detail": detail} if detail is not None else {})})
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        if not ok:
            raise RuntimeError(f"{name}: {json.dumps(detail, default=str)[:1500]}")

    workdir = Path(tempfile.mkdtemp(prefix="memory-os-ci-"))
    token = secrets.token_urlsafe(32)
    token_file = workdir / "http.token"
    try:
        user = json.loads(docker("image", "inspect", "--format", "{{json .Config.User}}", args.image).stdout)
        check("the image runs as the unprivileged user 10001", user == "10001:10001", user)
        fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(token + "\n")
        subprocess.run(["sudo", "-n", "chown", "10001:10001", str(token_file)], check=True)
        docker("volume", "create", VOLUME)
        mounts = ["-v", f"{VOLUME}:/data", "-v", f"{token_file}:/run/memory-os/http.token:ro"]
        # 1. The image as built, without the opt-in: loopback of the container only, unreachable from the host.
        docker("run", "-d", "--name", NAME + "-loopback", "-p", f"127.0.0.1:{PORT}:{PORT}", *mounts, args.image)
        time.sleep(20)
        status, _ = get("/ready", token)
        logs = docker("logs", NAME + "-loopback", check=False).stdout + docker("logs", NAME + "-loopback", check=False).stderr
        check("by default (no opt-in) the service is unreachable from the host", status is None
              and "http://127.0.0.1:8765/mcp/" in logs, {"status": status, "log": logs[-600:]})
        docker("stop", "-t", "30", NAME + "-loopback", timeout=60)
        docker("rm", NAME + "-loopback")
        # 2. With the explicit opt-in appended at docker run, published on the host loopback only.
        docker("run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:{PORT}", *mounts, args.image,
               "--container-listen-all")
        ready = wait_ready(token)
        check("/ready answers 200 with the token", ready is not None, docker("logs", NAME, check=False).stderr[-1500:])
        report["ready"] = {"version": ready.get("version"), "commit": ready.get("commit"),
                           "platform": (ready.get("capabilities") or {}).get("platform"),
                           "store": ((ready.get("capabilities") or {}).get("store") or {}).get("backend"),
                           "tokenizer": ((ready.get("capabilities") or {}).get("brief") or {}).get("tokenizer")}
        check("lexical sqlite store, embedded tokenizer table, retained cell", report["ready"]["store"] == "sqlite_s1"
              and (report["ready"]["tokenizer"] or {}).get("table_source") == "embedded"
              and (report["ready"]["platform"] or {}).get("cell") not in (None, "unqualified", "unretained"),
              report["ready"])
        check("/health refuses an unauthenticated request", get("/health")[0] == 401)
        status, _ = get("/ready", token, host=f"evil.example:{PORT}")
        check("a foreign Host header is refused (Host/Origin kept)", status not in (None, 200), status)
        deadline, health = time.monotonic() + 150, None
        while time.monotonic() < deadline:
            health = json.loads(docker("inspect", "--format", "{{json .State.Health.Status}}", NAME).stdout)
            if health == "healthy":
                break
            time.sleep(5)
        check("Docker healthcheck: healthy", health == "healthy", health)
        written = relay(NAME, "write")
        check("a fact is written through the container's relay", not written["error"], written)
        docker("restart", "-t", "30", NAME, timeout=120)
        check("/ready again after a restart", wait_ready(token) is not None)
        found = relay(NAME, "search")
        check("the fact is found again after the restart (data persisted in the volume)", not found["error"]
              and "Container persistence witness" in found["text"], found["text"][:300])
        docker("stop", "-t", "30", NAME, timeout=60)
        code = int(docker("inspect", "--format", "{{.State.ExitCode}}", NAME).stdout.strip())
        check("docker stop: exit code 0 (clean stop)", code == 0, code)
        report["ok"] = True
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        for name in (NAME, NAME + "-loopback"):
            logs = docker("logs", name, check=False)
            (args.output / f"{name}.log").write_text(logs.stdout + logs.stderr)
            docker("rm", "-f", name, check=False)
        docker("volume", "rm", "-f", VOLUME, check=False)
        subprocess.run(["sudo", "-n", "rm", "-rf", str(workdir)], check=False)
        (args.output / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
        print(json.dumps({k: report.get(k) for k in ("ok", "error")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
