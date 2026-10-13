"""``memory-os-admin doctor``: one read-only check of a whole local installation.

Each check yields PASS / WARN / FAIL with an actionable hint. FAIL means the installation does not work as a secured
ALPHA (exit 1); WARN means degraded or incomplete (e.g. a client not connected yet). Strictly read-only; never
prints a secret.
"""

from __future__ import annotations

import json
import os
import re
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
    info = {}
    try:
        info = json.loads(body or b"{}")
        if not isinstance(info, dict):
            info = {}
    except (ValueError, UnicodeError):
        pass
    if status == 200:
        version, commit = info.get("version"), info.get("commit")
        attested = (info.get("ok") is True and info.get("ready") is True
                    and (info.get("commit_source") in {"deployment_manifest", "embedded_descriptor"}
                         or (info.get("commit_source") == "checkout" and info.get("source_dirty") is False))
                    and info.get("source_dirty") is not True and isinstance(version, str)
                    and re.fullmatch(r"\d+\.\d+\.\d+", version)
                    and isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit))
        checks.append(Check("ready", "PASS" if attested else "FAIL",
                            f"/ready → ready, version {version}, commit {commit[:7]}" if attested
                            else "/ready → 200 with missing/invalid ready or build identity",
                            ("identity_source: " + info.get("identity_source", "legacy") +
                             "; MEMORY_V4_BUILD_COMMIT is no longer needed with a valid embedded descriptor")
                            if attested and info.get("identity_source") == "embedded_descriptor"
                            else ("" if attested else "check the qualified server build and /ready contract")))
    elif status == 404:
        checks.append(Check("ready", "WARN", "/ready not available (server < 4.0.1)", "upgrade the server"))
    else:
        checks.append(Check("ready", "FAIL", f"/ready → {status}",
                            "build_unqualified: reinstall a qualified artifact; an installed descriptor cannot be "
                            "replaced by MEMORY_V4_BUILD_COMMIT. Source checkouts may use a declared release commit; indexes_not_ready: see RB-02"))
    if status == 200:
        checks += _capabilities(info)
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
    try:
        rc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{settings.service_label}"],
                            capture_output=True, check=False, timeout=5).returncode
    except (OSError, subprocess.TimeoutExpired):
        return Check("service", "WARN", "launchd status unavailable (platform or timeout)")
    return (Check("service", "PASS", f"launchd job {settings.service_label} loaded") if rc == 0 else
            Check("service", "FAIL", f"launchd job {settings.service_label} not loaded",
                  "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"))


def _store(settings: OpsSettings) -> Check:
    from .storecheck import StoreCheckError, check_dir

    store = settings.store_dir
    if (store / "store.sqlite3").exists():
        try:
            from ..sqlite_stats import scan_sqlite
            report = scan_sqlite(store / "store.sqlite3")
            counts = report["receipts"]
            return Check("store", "PASS", f"SQLite quick_check valid; {report['live_total']} live items; "
                         f"receipts explicit={counts['explicit']} implicit={counts['implicit']} "
                         f"json_bytes={counts['json_bytes']} db_bytes={counts['db_bytes']} wal_bytes={counts['wal_bytes']}")
        except Exception as exc:
            return Check("store", "FAIL", f"SQLite read-only check failed: {type(exc).__name__}",
                         "see ADR-001; doctor never repairs or upgrades SQLite")
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


def _storage(settings: OpsSettings) -> Check:
    from .storage import margin
    try:
        space = margin(settings)
        return Check("storage", "PASS" if space["ok"] else "WARN",
                     f"DB/source {space['source_bytes']} bytes; working estimate {space['estimated_working_bytes']} bytes; "
                     f"margins { {k: v['margin_bytes'] for k, v in space['volumes'].items()} }",
                     "free operator-owned space or choose qualified storage; never bypass the backup guard")
    except (OSError, ValueError) as exc:
        return Check("storage", "WARN", f"storage margin unknown: {type(exc).__name__}")


def _clients(home: Path) -> list[Check]:
    hints = {
        "direct-http": "HTTP authentication not checked: prefer the token-file relay (memory-os-admin setup)",
        "legacy-v3": "old v3 server = separate memory: switch to the relay (memory-os-admin setup)",
        "other": "unrecognised entry: switch to the relay (memory-os-admin setup)",
        "absent": "not connected: add the relay entry (memory-os-admin setup)",
    }
    checks = []
    for client in CLIENTS:
        state, entry = read_entry(client, home)
        if state == "missing-config":
            checks.append(Check(f"client:{client.key}", "WARN", f"{client.name}: no config found (installation unknown)"))
            continue
        if state == "unreadable":
            checks.append(Check(f"client:{client.key}", "WARN", f"{client.name}: config unreadable",
                                str(client.config_path(home))))
            continue
        kind = classify(entry)
        checks.append(Check(f"client:{client.key}", "PASS" if kind == "relay" else "WARN",
                            f"{client.name}: {kind}" + (" (configuration only; handshake not checked)" if kind == "relay" else ""),
                            "" if kind == "relay" else hints[kind]))
    return checks


def run(settings: OpsSettings | None = None, home: Path | None = None) -> list[Check]:
    from .qualified_runtime import inspect_current
    local_runtime = inspect_current(extras=('ops', 'brief'))
    settings = settings or OpsSettings.from_env()
    home = home or Path.home()
    checks = [_secret(settings.token_file, "token", required=True),
              _secret(settings.backup_key, "backup-key", required=False, key=True)]
    checks.append(Check('local-runtime', 'PASS' if local_runtime['ok'] else 'FAIL',
                        'doctor/relay environment runtime versions (not the server): ' +
                        (local_runtime['profile_id'] if local_runtime['ok'] else
                         ', '.join(sorted(local_runtime['mismatches'])))))
    checks += _server(settings)
    checks += [_service(settings), _store(settings), _backup(settings), _storage(settings)]
    from .request_journal import directory, scan, key, RESOLVED
    journal = scan(directory(settings))
    resolved = {key(r) for r in journal["entries"] if r["state"] in RESOLVED}
    uncertain = {key(r) for r in journal["entries"] if key(r) not in resolved}
    checks.append(Check("relay-journals", "WARN" if uncertain or journal["errors"] or journal["active_journals"] else "PASS",
                        f"{len(uncertain)} orphan uncertain operations; {len(journal['errors'])} journal errors; "
                        f"{journal['active_journals']} active (unresolved count not inspected); "
                        f"{journal['bytes']} bytes; oldest {journal['oldest_age_s']:.0f} s",
                        "memory_request_status without ID via each relay; never repeat an unresolved mutation"))
    if (settings.store_dir / 'store.sqlite3').exists():
        try:
            from ..sqlite_stats import scan_sqlite
            journal = scan_sqlite(settings.store_dir / 'store.sqlite3')['change_journal']
            checks.append(Check('journal_size', 'WARN' if journal.get('alert') else 'PASS',
                                f"change journal {journal['json_bytes']} bytes / alert {journal['alert_bytes']}; no automatic purge"))
        except (OSError, ValueError):
            checks.append(Check('journal_size','WARN','change journal size unavailable'))
    checks += _clients(home)
    from .connection_proof import inspect_proofs
    current_identity = None
    try:
        code, raw = http_get(settings.ready_url, settings)
        deployed = json.loads(raw)
        if code == 200 and deployed.get("ready") is True:
            current_identity = {"version": deployed["version"], "commit": deployed["commit"],
                                "store_instance_id": deployed["capabilities"]["store"]["instance_id"]}
    except (SecretFileError, ValueError, KeyError, TypeError):
        pass
    checks += [Check(**row) for row in inspect_proofs(settings, current_identity)]
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


def _capabilities(info: dict) -> list[Check]:
    """Compare explicit deployment expectations, not this host's default Config."""
    from .. import __version__
    caps = info.get("capabilities")
    if not isinstance(caps, dict) or caps.get("schema_version") != 1:
        return [Check("capabilities", "WARN", "deployed capabilities unknown (older server)")]
    try:
        values = {"SERVER_VERSION": caps["engine"]["version"], "BACKEND": caps["store"]["backend"],
                  "DEVICE": caps["encoding"]["profile"]["device"],
                  "REGISTRY_SHA256": caps["projects"]["registry_sha256"]}
        consistent = (values["SERVER_VERSION"] == info["version"]
                      and caps["engine"]["commit"] == info["commit"]
                      and caps["encoding"]["profile"] == info["encoding_profile"]
                      and values["BACKEND"] in {"jsonl", "sqlite_s1"})
        mismatches = [key for key, value in values.items()
                      if os.environ.get("MEMORY_V4_EXPECT_" + key) is not None
                      and os.environ["MEMORY_V4_EXPECT_" + key] != value]
        status = "PASS" if consistent and not mismatches else "FAIL"
        checks = [Check("capabilities", status,
                        "server capabilities consistent" if status == "PASS" else
                        "deployed contract mismatch: " + ",".join(mismatches or ["response_identity"])),
                  Check("relay-artifact", "PASS" if __version__ == values["SERVER_VERSION"] else "WARN",
                        f"doctor environment package={__version__}; client relay artifacts are separate")]
    except (KeyError, TypeError):
        return [Check("capabilities", "FAIL", "malformed deployed capability contract")]
    tokenizer = caps.get("brief", {}).get("tokenizer", {})
    from .qualified_runtime import compare
    deployed_runtime = caps.get('runtime')
    if isinstance(deployed_runtime, dict) and isinstance(deployed_runtime.get('actual'), dict):
        proof = compare(deployed_runtime['actual'], extras=('ops', 'brief'))
        checks.append(Check('server-runtime', 'PASS' if proof['ok'] else 'FAIL',
                            'deployed core/ops/brief runtime versions' if proof['ok'] else
                            'deployed runtime drift: ' + ', '.join(sorted(proof['mismatches']))))
    else:
        checks.append(Check('server-runtime', 'WARN', 'deployed core runtime attestation unavailable'))
    runtime = caps.get('encoding', {}).get('profile', {}).get('runtime')
    if isinstance(runtime, dict):
        proof = compare(runtime, encoding_only=True)
        checks.append(Check('encoder-runtime', 'PASS' if proof['ok'] else 'FAIL',
                            'qualified encoder package versions' if proof['ok'] else
                            'deployed encoder version drift: ' + ', '.join(sorted(proof['mismatches'])),
                            '' if proof['ok'] else 'review the qualified runtime; do not re-encode/mix vectors implicitly'))
    else:
        checks.append(Check('encoder-runtime', 'WARN', 'deployed package versions unavailable'))
    if caps.get("brief", {}).get("compact"):
        checks.append(Check("brief-tokenizer", "PASS" if tokenizer.get("ready") else "FAIL",
                            "exact offline cl100k_base available" if tokenizer.get("ready") else
                            "compact unavailable: provision brief extra and attested local table"))
    mutation = caps.get("policies", {}).get("mutations")
    if mutation is not None:
        from ..fact_updates import mutation_policy
        checks.append(Check("mutation-policy", "PASS" if mutation == mutation_policy() else "WARN",
                            "deployed explicit-only replacement/exact assertion dedup" if mutation == mutation_policy() else
                            "deployed mutation policy differs or is unknown; no COS confinement guarantee"))
    inert = caps.get("policies", {}).get("deprecated_inert_settings", [])
    if inert:
        checks.append(Check("deprecated-config", "WARN", "deployed *_COS settings are ignored",
                            "remove old settings during an explicitly approved service update; not a confinement mechanism"))
    return checks
