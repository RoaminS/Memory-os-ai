"""``memory-os-admin doctor``: one read-only check of a whole local installation.

Each check yields PASS / WARN / FAIL with an actionable hint. FAIL means the installation does not work as a secured
ALPHA (exit 1); WARN means degraded or incomplete (e.g. a client not connected yet). Strictly read-only; never
prints a secret.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .auth import SecretFileError, http_get, read_token
from .clients import CLIENTS, classify, read_entry
from .settings import OpsSettings


@dataclass
class Check:
    area: str
    status: str  # PASS | WARN | FAIL
    detail: str
    hint: str = ""


def _secret(path: Path, label: str, *, required: bool, key: bool = False) -> Check:
    try:
        if key:
            from .backup import load_key

            load_key(path)
            return Check(label, "PASS", f"{path} (0600)")
        token = read_token(path)
    except SecretFileError as exc:
        missing = "missing" in str(exc)
        return Check(label, "FAIL" if required else "WARN", str(exc),
                     "memory-os-admin backup creates it" if (key and missing) else "fix permissions to 0600 or recreate")
    if token is None:
        return Check(label, "FAIL" if required else "WARN", f"{path} missing", "memory-os-admin token create")
    return Check(label, "PASS", f"{path} (0600, content not shown)")


def _server(settings: OpsSettings) -> list[Check]:
    checks: list[Check] = []
    try:
        with_token, _ = http_get(settings.health_url, settings)
    except SecretFileError:
        with_token = -1
    without_token = _get_without_token(settings.health_url)
    if with_token != 200:
        checks.append(Check("server", "FAIL", f"/health with token → {with_token}",
                            "start the service (launchctl bootstrap …) and check server.log"))
        return checks
    checks.append(Check("server", "PASS", f"/health with token → 200 ({settings.base_url})"))
    if without_token == 401:
        checks.append(Check("security", "PASS", "/health without token → 401 (token enforced)"))
    else:
        checks.append(Check("security", "FAIL", f"/health without token → {without_token}: token NOT enforced",
                            "run a SEC-1 server (≥ 4.0.1) with MEMORY_V4_TOKEN_FILE"))
    status, body = http_get(settings.ready_url, settings)
    reason, info = "", {}
    try:
        info = json.loads(body or b"{}")
        reason = info.get("reason", "")
    except ValueError:
        pass
    if status == 200:
        checks.append(Check("ready", "PASS", f"/ready → ready, version {info.get('version')}, commit "
                                             f"{str(info.get('commit'))[:7]} ({info.get('commit_source')})"))
    elif status == 404:
        checks.append(Check("ready", "WARN", "/ready not available (server < 4.0.1)", "upgrade the server"))
    else:
        checks.append(Check("ready", "FAIL", f"/ready → {status} {reason}".strip(),
                            "build_unqualified: set MEMORY_V4_BUILD_COMMIT; indexes_not_ready: see RB-02"))
    return checks


def _get_without_token(url: str) -> int:
    import urllib.error
    import urllib.request

    from .auth import _OPENER

    try:
        with _OPENER.open(urllib.request.Request(url), timeout=3) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return -1


def _service(settings: OpsSettings) -> Check:
    if not settings.service_label:
        return Check("service", "WARN", "MEMORY_V4_SERVICE_LABEL not set: launchd state not checked",
                     "export MEMORY_V4_SERVICE_LABEL=<label> (needed for qualified backups)")
    rc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{settings.service_label}"],
                        capture_output=True, check=False).returncode
    return (Check("service", "PASS", f"launchd job {settings.service_label} loaded") if rc == 0 else
            Check("service", "FAIL", f"launchd job {settings.service_label} not loaded",
                  "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"))


def _store(settings: OpsSettings) -> Check:
    from .storecheck import StoreCheckError, check_dir

    store = settings.store_dir
    if not (store / "items.jsonl").is_file():
        return Check("store", "WARN", f"{store}/items.jsonl not found (empty installation?)")
    names = [p.name for p in store.iterdir() if p.is_file() and p.name in
             {"items.jsonl", "blocks.jsonl", "requests.jsonl", "meta.json"}]
    try:
        report = check_dir(store, names)
    except (StoreCheckError, OSError) as exc:
        return Check("store", "FAIL", f"strict check failed: {exc}", "see RB-08; do not write until resolved")
    return Check("store", "PASS", f"{report['items']['live_total']} live items, structure valid")


def _backup(settings: OpsSettings) -> Check:
    from .health import BACKUP_MAX_AGE_H, last_good_backup_age

    try:
        age = last_good_backup_age(settings)
    except Exception as exc:
        return Check("backup", "WARN", f"backup log unreadable: {type(exc).__name__}")
    if age is None:
        return Check("backup", "WARN", "no verified qualified backup yet", "memory-os-admin backup")
    status = "PASS" if age <= BACKUP_MAX_AGE_H else "WARN"
    return Check("backup", status, f"last verified qualified backup {age} h ago")


def _clients(home: Path) -> list[Check]:
    hints = {
        "direct-http": "will get 401 from a secured server: switch to the relay (memory-os-admin setup)",
        "legacy-v3": "old v3 server = separate memory: switch to the relay (memory-os-admin setup)",
        "other": "unrecognised entry: switch to the relay (memory-os-admin setup)",
        "absent": "not connected: add the relay entry (memory-os-admin setup)",
    }
    checks = []
    for client in CLIENTS:
        state, entry = read_entry(client, home)
        if state == "missing-config":
            checks.append(Check(f"client:{client.key}", "PASS", f"{client.name}: not installed / no config"))
            continue
        if state == "unreadable":
            checks.append(Check(f"client:{client.key}", "WARN", f"{client.name}: config unreadable",
                                str(client.config_path(home))))
            continue
        kind = classify(entry)
        checks.append(Check(f"client:{client.key}", "PASS" if kind == "relay" else "WARN",
                            f"{client.name}: {kind}", "" if kind == "relay" else hints[kind]))
    return checks


def run(settings: OpsSettings | None = None, home: Path | None = None) -> list[Check]:
    settings = settings or OpsSettings.from_env()
    home = home or Path.home()
    checks = [_secret(settings.token_file, "token", required=True),
              _secret(settings.backup_key, "backup-key", required=False, key=True)]
    checks += _server(settings)
    checks += [_service(settings), _store(settings), _backup(settings)]
    checks += _clients(home)
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.area) for c in checks)
    lines = []
    for c in checks:
        lines.append(f"{c.status:4}  {c.area:<{width}}  {c.detail}")
        if c.hint and c.status != "PASS":
            lines.append(f"{'':4}  {'':<{width}}  → {c.hint}")
    fails = sum(c.status == "FAIL" for c in checks)
    warns = sum(c.status == "WARN" for c in checks)
    lines.append(f"\n{len(checks)} checks: {len(checks) - fails - warns} pass, {warns} warn, {fails} fail")
    return "\n".join(lines)
