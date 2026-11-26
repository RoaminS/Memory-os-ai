"""``memory-os-admin doctor``: one read-only check of a whole local installation.

Each check yields PASS / WARN / FAIL with an actionable hint. FAIL means the installation does not work as a secured
ALPHA (exit 1); WARN means degraded or incomplete (e.g. a client not connected yet). Strictly read-only; never
prints a secret.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version
from pathlib import Path

from .auth import SecretFileError, http_get, read_token
from .clients import (
    CLIENTS,
    ClientPathError,
    classify,
    gemini_system_settings_notice,
    grok_claude_duplicate_notice,
    read_entry,
)
from .diagnostics import exception_label
from .settings import OpsSettings

PROFILES = ("lexical", "semantic")


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
        return Check(
            label,
            "FAIL" if required else "WARN",
            str(exc),
            "memory-os-admin backup creates it"
            if (key and missing)
            else "fix permissions to 0600 or recreate",
        )
    if token is None:
        return Check(label, "FAIL" if required else "WARN", f"{path} missing", "memory-os-admin token create")
    return Check(label, "PASS", f"{path} (0600, content not shown)")


def _pep440_version(value) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        Version(value)
        return True
    except InvalidVersion:
        return False


def _server(settings: OpsSettings) -> list[Check]:
    checks: list[Check] = []
    try:
        with_token, _ = http_get(settings.health_url, settings)
    except SecretFileError:
        with_token = -1
    without_token = _get_without_token(settings.health_url)
    if with_token != 200:
        checks.append(
            Check(
                "server",
                "FAIL",
                f"/health with token → {with_token}",
                "start the service (launchctl bootstrap …) and check server.log",
            )
        )
        return checks
    checks.append(Check("server", "PASS", f"/health with token → 200 ({settings.base_url})"))
    if without_token == 401:
        checks.append(Check("security", "PASS", "/health without token → 401 (token enforced)"))
    else:
        checks.append(
            Check(
                "security",
                "FAIL",
                f"/health without token → {without_token}: token NOT enforced",
                "run a SEC-1 server (≥ 4.0.1) with MEMORY_V4_TOKEN_FILE",
            )
        )
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
        attested = (
            info.get("ok") is True
            and info.get("ready") is True
            and (
                info.get("commit_source") in {"deployment_manifest", "embedded_descriptor"}
                or (info.get("commit_source") == "checkout" and info.get("source_dirty") is False)
            )
            and info.get("source_dirty") is not True
            and isinstance(version, str)
            and _pep440_version(version)
            and isinstance(commit, str)
            and re.fullmatch(r"[0-9a-f]{40}", commit)
        )
        checks.append(
            Check(
                "ready",
                "PASS" if attested else "FAIL",
                f"/ready → ready, version {version}, commit {commit[:7]}"
                if attested
                else "/ready → 200 with missing/invalid ready or build identity",
                (
                    "identity_source: "
                    + info.get("identity_source", "legacy")
                    + "; MEMORY_V4_BUILD_COMMIT is no longer needed with a valid embedded descriptor"
                )
                if attested and info.get("identity_source") == "embedded_descriptor"
                else ("" if attested else "check the qualified server build and /ready contract"),
            )
        )
    elif status == 404:
        checks.append(Check("ready", "WARN", "/ready not available (server < 4.0.1)", "upgrade the server"))
    else:
        checks.append(
            Check(
                "ready",
                "FAIL",
                f"/ready → {status}",
                "build_unqualified: reinstall a qualified artifact; an installed descriptor cannot be "
                "replaced by MEMORY_V4_BUILD_COMMIT. Source checkouts may use a declared release commit; indexes_not_ready: see RB-02",
            )
        )
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
    """State of the service through the service interface (launchd or systemd --user); read-only."""
    from . import service as managers

    manager = managers.host_manager()
    name = "systemd" if manager is not None and manager.kind == "systemd-user" else "launchd"
    if not settings.service_label:
        return Check(
            "service",
            "WARN",
            f"MEMORY_V4_SERVICE_LABEL not set: {name} state not checked",
            "export MEMORY_V4_SERVICE_LABEL=<label> (needed for qualified backups)",
        )
    if manager is None:
        manager = managers.Launchd()  # as before on a platform without a manager: the command is absent, WARN
    state, word = manager.status_detail(settings.service_label, run=subprocess.run, timeout=5)
    if state not in {"running", "stopped"} or not word:
        # Anything that is not a complete, positive answer of the manager: never "loaded", never "not loaded".
        return Check("service", "WARN", f"{name} status unavailable (platform or timeout)")
    what = "unit" if name == "systemd" else "job"
    # The word is the manager's own fact: "not loaded" only when the manager knows no such job or
    # unit; a systemd unit that is loaded and inactive (or failed) is said "loaded but inactive".
    if state == "running" and word == "loaded but not running":
        # Starting, stopping or waiting for a restart, with no main process: never a healthy service.
        return Check("service", "WARN", f"{name} {what} {settings.service_label} {word}",
                     "no main process (starting, stopping or restarting): check the server log, then run doctor again")
    return (
        Check("service", "PASS", f"{name} {what} {settings.service_label} {word}")
        if state == "running"
        else Check(
            "service",
            "FAIL",
            f"{name} {what} {settings.service_label} {word}",
            manager.manual_start(settings.service_label),
        )
    )


def _store(settings: OpsSettings) -> Check:
    from .storecheck import StoreCheckError, check_dir

    store = settings.store_dir
    if (store / "store.sqlite3").exists():
        try:
            from ..sqlite_stats import scan_sqlite

            report = scan_sqlite(store / "store.sqlite3")
            counts = report["receipts"]
            return Check(
                "store",
                "PASS",
                f"SQLite quick_check valid; {report['live_total']} live items; "
                f"receipts explicit={counts['explicit']} implicit={counts['implicit']} "
                f"json_bytes={counts['json_bytes']} db_bytes={counts['db_bytes']} wal_bytes={counts['wal_bytes']}",
            )
        except Exception as exc:
            from ..sqlite_store import ReadOnlyStoreError

            if isinstance(exc, ReadOnlyStoreError):
                # 4.1.3: a controlled refusal of the read-only open, named by its code; a writer starting or
                # closing during the read is transient (WARN), the other refusals need the operator (FAIL).
                transient = exc.code in {"store_busy_retry", "store_side_files_recreated"}
                return Check(
                    "store",
                    "WARN" if transient else "FAIL",
                    f"SQLite read-only check refused: {exc.code}",
                    "retry doctor" if transient
                    else "doctor never creates writer.lock and never deletes SQLite side files; see the 4.1.3 notes",
                )
            return Check(
                "store",
                "FAIL",
                f"SQLite read-only check failed: {exception_label(exc)}",
                "see ADR-001; doctor never repairs or upgrades SQLite",
            )
    if not (store / "items.jsonl").is_file():
        return Check("store", "WARN", f"{store}/items.jsonl not found (empty installation?)")
    names = [
        p.name
        for p in store.iterdir()
        if p.is_file() and p.name in {"items.jsonl", "blocks.jsonl", "requests.jsonl", "meta.json"}
    ]
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
        return Check("backup", "WARN", f"backup log unreadable: {exception_label(exc)}")
    if age is None:
        return Check("backup", "WARN", "no verified qualified backup yet", "memory-os-admin backup")
    status = "PASS" if age <= BACKUP_MAX_AGE_H else "WARN"
    return Check("backup", status, f"last verified qualified backup {age} h ago")


def _storage(settings: OpsSettings) -> Check:
    from .storage import margin

    try:
        space = margin(settings)
        return Check(
            "storage",
            "PASS" if space["ok"] else "WARN",
            f"policy={space['policy']} reserve={space['reserve_bytes']} bytes; DB/source {space['source_bytes']} bytes; working estimate {space['estimated_working_bytes']} bytes; "
            f"margins { {k: v['margin_bytes'] for k, v in space['volumes'].items()} }",
            "free operator-owned space or choose qualified storage; never bypass the backup guard",
        )
    except (OSError, ValueError) as exc:
        return Check("storage", "WARN", f"storage margin unknown: {exception_label(exc)}")


def _backup_capacity(settings):
    from .sqlite_limits import capacity

    try:
        value = capacity(settings.store_dir)
        state = "FAIL" if not value["ok"] else "WARN" if value["warning"] else "PASS"
        return Check(
            "backup_capacity",
            state,
            f"DB/WAL/SHM limit={value['limits']['database_bytes']} bytes; margin={value['margin_bytes']} bytes",
            "Explicit MEMORY_V4_SQLITE_BACKUP_MAX_BYTES; never purge history to bypass a cap",
        )
    except (OSError, ValueError) as exc:
        return Check("backup_capacity", "FAIL", f"backup capacity unavailable: {exception_label(exc)}")


def _sqlite_runtime() -> Check:
    """SQLite loaded by the interpreter of THIS command; it does not certify a process already started."""
    from . import sqlite_runtime

    report = sqlite_runtime.inspect()
    if report.get("ok"):
        return Check("sqlite-runtime", "PASS",
                     f"SQLite {report.get('version')} loaded by this interpreter (minimum {report.get('minimum')})")
    return Check(
        "sqlite-runtime",
        "FAIL",
        f"{report.get('code')}: SQLite {report.get('version')} loaded by this interpreter, "
        f"minimum {report.get('minimum')}",
        "use a Python 3.11 linked with SQLite >= " + str(report.get("minimum")) + " (JSON functions, WAL)",
    )


def _server_sqlite(caps: dict) -> Check:
    """SQLite attested by the server process itself (``capabilities.store.sqlite_version``)."""
    from .sqlite_runtime import MIN_VERSION

    minimum = ".".join(str(part) for part in MIN_VERSION)
    store = caps.get("store")
    value = store.get("sqlite_version") if isinstance(store, dict) else None
    version = _sqlite_triplet(value)
    if version is None:
        return Check("server-sqlite-runtime", "WARN", "SQLite version not attested by the running server",
                     "server older than 4.1.0: restart it on a qualified build")
    if version < tuple(MIN_VERSION):
        return Check("server-sqlite-runtime", "FAIL",
                     f"sqlite_runtime_too_old: running server loaded SQLite {value}, minimum {minimum}",
                     "restart the service with an interpreter linked with a fit SQLite")
    return Check("server-sqlite-runtime", "PASS", f"SQLite {value} loaded by the running server (minimum {minimum})")


def _sqlite_triplet(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        return None
    return tuple(int(part) for part in value.split("."))


def _clients(home: Path) -> list[Check]:
    hints = {
        "direct-http": "HTTP authentication not checked: prefer the token-file relay (memory-os-admin setup)",
        "legacy-v3": "old v3 server = separate memory: switch to the relay (memory-os-admin setup)",
        "other": "unrecognised entry: switch to the relay (memory-os-admin setup)",
        "absent": "not connected: add the relay entry (memory-os-admin setup)",
    }
    checks = []
    active_entries: set[str] = set()
    gemini_seen = False
    for client in CLIENTS:
        try:
            config_path = client.config_path(home)
        except ClientPathError as exc:
            detail = (
                f"{client.name}: not supported on this OS in 4.1.2 (no path guessed)"
                if exc.code == "client_unsupported_on_os"
                else f"{client.name}: {exc}"
            )
            checks.append(Check(f"client:{client.key}", "WARN", detail))
            continue
        state, entry = read_entry(client, home)
        if state == "missing-config":
            checks.append(
                Check(
                    f"client:{client.key}", "WARN", f"{client.name}: no config found (installation unknown)"
                )
            )
            continue
        if client.key == "gemini-cli":
            gemini_seen = True
        if state == "unreadable":
            checks.append(
                Check(
                    f"client:{client.key}",
                    "WARN",
                    f"{client.name}: config unreadable",
                    str(config_path),
                )
            )
            continue
        if entry is not None:
            active_entries.add(client.key)
        kind = classify(entry)
        checks.append(
            Check(
                f"client:{client.key}",
                "PASS" if kind == "relay" else "WARN",
                f"{client.name}: {kind}"
                + (" (configuration only; handshake not checked)" if kind == "relay" else ""),
                "" if kind == "relay" else hints[kind],
            )
        )
    if gemini_seen:
        warning = gemini_system_settings_notice()
        if warning:
            checks.append(Check("client:gemini-cli-system-settings", "WARN", warning))
    if {"claude-code", "grok"} <= active_entries:
        checks.append(Check("client:grok-claude-duplicate", "WARN", grok_claude_duplicate_notice()))
    return checks


def run(settings: OpsSettings | None = None, home: Path | None = None) -> list[Check]:
    from .qualified_runtime import inspect_current

    settings = settings or OpsSettings.from_env()
    home = home or Path.home()
    deployed_info = None
    try:
        code, raw = http_get(settings.ready_url, settings)
        deployed_info = json.loads(raw) if code == 200 else None
    except (SecretFileError, ValueError, TypeError):
        pass
    # This environment is inspected with the catalog of the INSTALLATION profile, whose source is shown.
    profile, source = installation_profile(deployed_info, os.environ)
    local_runtime = inspect_current(profile=profile, extras=("ops", "brief"))
    checks = [
        _secret(settings.token_file, "token", required=True),
        _secret(settings.backup_key, "backup-key", required=False, key=True),
    ]
    checks.append(
        Check(
            "local-runtime",
            "PASS" if local_runtime["ok"] else "FAIL",
            f"doctor/relay environment runtime versions (not the server), profile {profile} ({source}): "
            + (
                local_runtime["profile_id"]
                if local_runtime["ok"]
                else ", ".join(sorted(local_runtime["mismatches"]))
            ),
        )
    )
    checks.append(_sqlite_runtime())
    checks += _server(settings)
    checks += [_service(settings), _store(settings), _backup(settings), _storage(settings)]
    if (settings.store_dir / "store.sqlite3").exists():
        checks.append(_backup_capacity(settings))
    from .request_journal import directory, scan, key, RESOLVED

    journal = scan(directory(settings))
    resolved = {key(r) for r in journal["entries"] if r["state"] in RESOLVED}
    uncertain = {key(r) for r in journal["entries"] if key(r) not in resolved}
    checks.append(
        Check(
            "relay-journals",
            "WARN" if uncertain or journal["errors"] or journal["active_journals"] else "PASS",
            f"{len(uncertain)} orphan uncertain operations; {len(journal['errors'])} journal errors; "
            f"{journal['active_journals']} active (unresolved count not inspected); "
            f"{journal['bytes']} bytes; oldest {journal['oldest_age_s']:.0f} s",
            "memory_request_status without ID via each relay; never repeat an unresolved mutation",
        )
    )
    if (settings.store_dir / "store.sqlite3").exists():
        try:
            from ..sqlite_stats import scan_sqlite

            journal = scan_sqlite(settings.store_dir / "store.sqlite3")["change_journal"]
            checks.append(
                Check(
                    "journal_size",
                    "WARN" if journal.get("alert") else "PASS",
                    f"change journal {journal['json_bytes']} bytes / alert {journal['alert_bytes']}; no automatic purge",
                )
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            code = getattr(exc, "code", None)            # ReadOnlyStoreError (4.1.3) names its refusal
            checks.append(Check("journal_size", "WARN", "change journal size unavailable"
                                + (f" (read-only open refused: {code})" if isinstance(code, str) else "")))
    checks += _clients(home)
    from .connection_proof import inspect_proofs

    current_identity = None
    try:
        deployed = deployed_info
        if isinstance(deployed, dict) and deployed.get("ready") is True:
            current_identity = {
                "version": deployed["version"],
                "commit": deployed["commit"],
                "store_instance_id": deployed["capabilities"]["store"]["instance_id"],
            }
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


def installation_profile(info, environ) -> tuple[str, str]:
    """(profile, source) used to inspect THIS environment: what the reachable service says, else
    ``MEMORY_V4_RETRIEVAL_PROFILE``, else the engine default, always shown with its source."""
    marker = None
    if isinstance(info, dict) and isinstance(info.get("capabilities"), dict):
        marker = info["capabilities"].get("retrieval_profile")
    if isinstance(marker, dict) and marker.get("name") in PROFILES:
        return marker["name"], "deployed"
    if environ.get("MEMORY_V4_RETRIEVAL_PROFILE") in PROFILES:
        return environ["MEMORY_V4_RETRIEVAL_PROFILE"], "environment"
    return "semantic", "engine-default"


def _store_profile(caps: dict) -> Check | None:
    from ..config import LEXICAL_MARKER

    marker = caps.get("retrieval_profile")
    if marker is None:
        return None  # server older than the profile marker: nothing to compare
    if marker == LEXICAL_MARKER:
        return Check("store-profile", "PASS", f"retrieval profile lexical, policy {marker['policy']}")
    if marker == {"name": "semantic", "policy": None}:
        return Check("store-profile", "PASS", "retrieval profile semantic")
    return Check("store-profile", "FAIL", "malformed retrieval profile marker",
                 "the deployed profile marker is not one this release knows; do not convert a store in place")


def _capabilities(info: dict) -> list[Check]:
    """Compare explicit deployment expectations, not this host's default Config."""
    from .. import __version__

    caps = info.get("capabilities")
    if not isinstance(caps, dict) or caps.get("schema_version") != 1:
        return [Check("capabilities", "WARN", "deployed capabilities unknown (older server)")]
    profile, _ = installation_profile(info, {})
    lexical = profile == "lexical"
    try:
        encoder = caps["encoding"]["profile"]
        if encoder is None and not lexical:
            raise TypeError("encoder profile missing on a semantic deployment")
        values = {
            "SERVER_VERSION": caps["engine"]["version"],
            "BACKEND": caps["store"]["backend"],
            "DEVICE": encoder["device"] if encoder is not None else None,  # lexical: no encoder, no device
            "REGISTRY_SHA256": caps["projects"]["registry_sha256"],
        }
        consistent = (
            values["SERVER_VERSION"] == info["version"]
            and caps["engine"]["commit"] == info["commit"]
            and encoder == info["encoding_profile"]
            and values["BACKEND"] in {"jsonl", "sqlite_s1"}
        )
        mismatches = [
            key
            for key, value in values.items()
            if os.environ.get("MEMORY_V4_EXPECT_" + key) is not None
            and os.environ["MEMORY_V4_EXPECT_" + key] != value
        ]
        status = "PASS" if consistent and not mismatches else "FAIL"
        checks = [
            Check(
                "capabilities",
                status,
                "server capabilities consistent"
                if status == "PASS"
                else "deployed contract mismatch: " + ",".join(mismatches or ["response_identity"]),
            ),
            Check(
                "relay-artifact",
                "PASS" if __version__ == values["SERVER_VERSION"] else "WARN",
                f"doctor environment package={__version__}; client relay artifacts are separate",
            ),
        ]
    except (KeyError, TypeError):
        return [Check("capabilities", "FAIL", "malformed deployed capability contract")]
    tokenizer = caps.get("brief", {}).get("tokenizer", {})
    from .qualified_runtime import REFERENCE_CELL, compare

    deployed_runtime = caps.get("runtime")
    if isinstance(deployed_runtime, dict) and isinstance(deployed_runtime.get("actual"), dict):
        deployed_cell = deployed_runtime.get("cell_id")
        if deployed_cell is None and profile == "semantic":
            deployed_cell = REFERENCE_CELL
        try:
            proof = compare(deployed_runtime["actual"], extras=("ops", "brief"), profile=profile, cell_id=deployed_cell)
        except ValueError as exc:
            checks.append(Check("server-runtime", "FAIL", f"deployed runtime cell/lock invalid ({profile}): {exc}"))
        else:
            checks.append(
                Check(
                    "server-runtime",
                    "PASS" if proof["ok"] else "FAIL",
                    f"deployed core/ops/brief runtime versions ({profile} catalog)"
                    if proof["ok"]
                    else f"deployed runtime drift ({profile} catalog): " + ", ".join(sorted(proof["mismatches"])),
                )
            )
    else:
        checks.append(Check("server-runtime", "WARN", "deployed core runtime attestation unavailable"))
    runtime = encoder.get("runtime") if isinstance(encoder, dict) else None
    if lexical:
        checks.append(Check("encoder-runtime", "PASS", "encoder absent by profile (lexical): nothing to attest"))
    elif isinstance(runtime, dict):
        proof = compare(runtime, encoding_only=True, cell_id=REFERENCE_CELL)
        names = ", ".join(sorted(proof["mismatches"]))
        if proof["ok"]:
            checks.append(Check("encoder-runtime", "PASS", "qualified encoder package versions"))
        elif proof["state"] == "missing_dependency":
            checks.append(
                Check(
                    "encoder-runtime",
                    "FAIL",
                    "deployed encoder missing dependency: " + names,
                    f'pip install -c qualified_runtime_constraints_v1.txt "memory-os-ai[ops,brief,semantic]=={__version__}"',
                )
            )
        else:
            checks.append(
                Check(
                    "encoder-runtime",
                    "FAIL",
                    "deployed encoder version drift: " + names,
                    "review the qualified runtime; do not re-encode/mix vectors implicitly",
                )
            )
    else:
        checks.append(Check("encoder-runtime", "WARN", "deployed package versions unavailable"))
    marker = _store_profile(caps)
    if marker is not None:
        checks.append(marker)
    checks.append(_server_sqlite(caps))
    if caps.get("brief", {}).get("compact"):
        checks.append(
            Check(
                "brief-tokenizer",
                "PASS" if tokenizer.get("ready") else "FAIL",
                "exact offline cl100k_base available"
                if tokenizer.get("ready")
                else "compact unavailable: provision brief extra and attested local table",
            )
        )
    mutation = caps.get("policies", {}).get("mutations")
    if mutation is not None:
        from ..fact_updates import mutation_policy

        checks.append(
            Check(
                "mutation-policy",
                "PASS" if mutation == mutation_policy() else "WARN",
                "deployed explicit-only replacement/exact assertion dedup"
                if mutation == mutation_policy()
                else "deployed mutation policy differs or is unknown; no COS confinement guarantee",
            )
        )
    inert = caps.get("policies", {}).get("deprecated_inert_settings", [])
    if inert:
        checks.append(
            Check(
                "deprecated-config",
                "WARN",
                "deployed *_COS settings are ignored",
                "remove old settings during an explicitly approved service update; not a confinement mechanism",
            )
        )
    return checks
