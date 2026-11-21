"""``memory-os-admin uninstall``: remove OUR client entries and OUR service; keep every byte of data.

Contract: docs/SETUP_4_1_0.md §7 (and §5.2 for ownership, §6 for the stop sequence).

- ``plan`` is read-only. ``apply`` revalidates everything before the first mutation, removes the
  client entries first, and stops the service last, only when every planned removal succeeded
  and no entry of the WHOLE client registry depends, or may depend, on the service.
- Only an entry proven by a trusted journal of this installation is removed; an unproven relay
  entry needs the client named (``remove_existing``); anything else is kept.
- A removal durably revokes the proof it used, before the entry is touched.
- Store, backups, backup key, token, resources, journals, logs and the Python environment are
  never removed, restored, replaced or opened. There is no option to delete data and no option
  to stop a service that is still used.
- The result never claims to be complete: what cannot be inspected is listed for manual removal.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import quickstart as qs
from .clients import CLIENTS, SERVER, classify, read_entry

JOURNAL_SUBDIR = ".local/state/memory-os/uninstall"  # below the selected HOME
URL_FIELDS = ("url", "serverUrl", "httpUrl")
# Clients where the ``env`` of an entry is known to override the environment the client inherited
# (sources read by the contract reviewer: VS Code, Codex, Gemini CLI). Antigravity is NOT in the list:
# its sources were not read, and sharing the ``.gemini`` directory proves nothing about its launcher.
# Elsewhere an explicit foreign URL does not establish independence from our service.
ENV_PRECEDENCE_ESTABLISHED = frozenset({"vscode", "codex", "gemini-cli"})
V3_ARGS = ["-m", "memory_os_ai.server"]
CONSOLE_SCRIPT = "memory-os-relay"
PACKAGE = re.compile(r"memory-os-ai(\[[a-z]+(,[a-z]+)*\])?(==[0-9][0-9A-Za-z.]*)?")  # index name only: no URL, no path
PYTHON_VERSION = re.compile(r"[0-9]+(\.[0-9]+){0,2}")


class UninstallError(RuntimeError):
    """Refusal with a stable ``code`` (SETUP_4_1_0.md §11); the message never carries foreign text."""

    def __init__(self, message: str, code: str = "uninstall_refused"):
        super().__init__(message)
        self.code = code


# ---------- dependency of an entry on our service (a fourth notion, distinct from ownership) ----------

STRICT_URL = re.compile(r"(https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::([0-9]{1,5}))?(/[A-Za-z0-9._~/-]*)?")
IPV4 = re.compile(r"(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}")


def _destination(url):
    """(host, port, verdict) of a literal URL of a plain shape, else None.

    The verdict is about OUR service on ``port``: decided by ``_verdict``. Only a strictly plain
    ASCII ``http(s)://host[:port][/path]`` is read: userinfo, backslashes, percent-encoding,
    variables, non-ASCII or any other shape that two URL parsers could read differently is not
    a literal destination (None, hence ``indeterminate``).
    """
    if not isinstance(url, str):
        return None
    match = STRICT_URL.fullmatch(url)
    if not match:
        return None
    scheme, host, port = match.group(1), match.group(2).lower(), match.group(3)
    number = int(port) if port else (443 if scheme == "https" else 80)
    if not 0 < number < 65536:
        return None
    return ("loopback" if host in {"127.0.0.1", "localhost", "[::1]"} else host), number


def _verdict(destination, port: int) -> str:
    """``wired`` | ``none`` | ``indeterminate`` for a literal destination and our service port.

    Another PORT is another destination, whatever the host. On OUR port: the three loopback
    names are our service; a canonical dotted IPv4 address outside 127/8 and 0/8 is another
    machine; anything else (a DNS or hosts-file name, ``localhost.``, ``0.0.0.0``, short or
    IPv6-mapped forms) may resolve to this machine and establishes nothing.
    """
    host, number = destination
    if number != port:
        return "none"
    if host == "loopback":
        return "wired"
    if IPV4.fullmatch(host) and all(int(part) < 256 for part in host.split(".")) and host.split(".")[0] not in {"0", "127"}:
        return "none"
    return "indeterminate"


def _ours(destination, port: int) -> bool:
    return _verdict(destination, port) == "wired"


def _plain_keys(entry: dict) -> bool:
    return not set(entry) - {"command", "args", "env", *qs.TRANSPORT_KEYS} and all(
        entry[key] == value for key, value in qs.TRANSPORT_KEYS.items() if key in entry)


def _uvx_relay(args) -> bool:
    """``uvx [--python V] [--no-python-downloads] --from memory-os-ai[…] memory-os-relay``, nothing else."""
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args) or args[-1] != CONSOLE_SCRIPT:
        return False
    options, index, source = args[:-1], 0, None
    while index < len(options):
        if options[index] == "--no-python-downloads":
            index += 1
        elif options[index] == "--python" and index + 1 < len(options) and PYTHON_VERSION.fullmatch(options[index + 1]):
            index += 2  # a version, never a path to an arbitrary interpreter
        elif options[index] == "--from" and index + 1 < len(options) and source is None:
            source = options[index + 1]
            index += 2
        else:
            return False
    return source is not None and PACKAGE.fullmatch(source) is not None


def _relay_launch(entry: dict) -> bool:
    """A launch that runs OUR relay with the entry's environment and nothing in between."""
    if qs.relay_form(entry):
        return True
    if not _plain_keys(entry):
        return False
    env = entry.get("env", {})
    if not isinstance(env, dict) or set(env) - qs.RELAY_ENV:
        return False
    command, args = entry.get("command"), entry.get("args", [])
    if not isinstance(command, str):
        return False
    if os.path.basename(command) == CONSOLE_SCRIPT and command in {CONSOLE_SCRIPT, os.path.abspath(command)}:
        return args == []
    return command == "uvx" and _uvx_relay(args)


def _dependency(entry, port: int, client: str | None) -> str:
    if not isinstance(entry, dict):
        return "indeterminate"
    urls = [entry[field] for field in URL_FIELDS if field in entry]
    if urls:
        if "command" in entry or "args" in entry:
            return "indeterminate"  # a command and a URL: nothing says which one the client uses
        destinations = [_destination(url) for url in urls]
        if None in destinations or len(set(destinations)) != 1:
            return "indeterminate"  # not literal, or several contradictory URL fields
        return _verdict(destinations[0], port)
    command = entry.get("command")
    env = entry.get("env", {})
    if (isinstance(command, str) and os.path.isabs(command) and qs.INTERPRETER.fullmatch(os.path.basename(command))
            and entry.get("args") == V3_ARGS and _plain_keys(entry)
            and isinstance(env, dict) and not any(str(key).upper().startswith("PYTHON") for key in env)):
        return "none"  # exact v3 server: a standalone stdio server with its own store
    if not _relay_launch(entry):
        return "indeterminate"  # wrapper, foreign or opaque command: it decides by itself where it connects
    env = entry.get("env") or {}
    destination = _destination(env.get("MEMORY_V4_URL")) if "MEMORY_V4_URL" in env else None
    if destination is None:
        return "indeterminate"  # inherited, variable or absent: the configuration does not establish it
    verdict = _verdict(destination, port)
    if verdict != "none":
        return verdict
    # Another destination is established only where the entry environment is known to win.
    return "none" if client in ENV_PRECEDENCE_ESTABLISHED else "indeterminate"


def service_dependency(entry, settings, *, home, client=None) -> str:
    """``wired`` | ``indeterminate`` | ``none``: does this entry depend on OUR service?

    ``none`` needs a positive proof: the entry ITSELF establishes another destination. Whatever
    is not established (absent, inherited, variable, opaque command, contradictory fields, a
    client whose environment precedence is not established) is ``indeterminate``, and
    ``indeterminate`` keeps the service. Neither the class of the entry, nor the relay defaults,
    nor the environment of this command proves an independence. ``client`` is the key of the
    client holding the entry; the comparison is on loopback host and port, not on the token file.
    """
    return _dependency(entry, settings.port, client)


def remaining_dependencies(home: Path, port: int, *, removed=()) -> dict:
    """{client: dependency} for every ``memory-os-ai`` entry of the WHOLE registry that keeps the service."""
    found = {}
    for client in CLIENTS:
        state, entry = _inspect(client, home)
        entries = [] if client.key in removed or entry is None else [entry]
        if client.fmt == "claude-user" and state == "ok":
            entries += _claude_project_entries(client.config_path(home))  # never "removed" by us: always counted
        if state in {"unreadable", "unsafe-path", "ambiguous"}:
            found[client.key] = "indeterminate"  # an entry may be there: nothing proves the contrary
            continue
        verdicts = {_dependency(item, port, client.key) for item in entries}
        if "wired" in verdicts:
            found[client.key] = "wired"
        elif "indeterminate" in verdicts:
            found[client.key] = "indeterminate"
    return found


def _inspect(client, home: Path):
    """(state, entry) with ONE path and ambiguity rule for the plan and for the dependency scan.

    ``unsafe-path``: a symbolic link anywhere on the path (dangling parent included) or a special
    file. ``ambiguous``: a TOML file holding BOTH server tables (``mcp_servers`` and
    ``mcp.servers``) with our name in one of them: which table the client reads is not ours to guess.
    """
    path = client.config_path(home)
    try:
        qs._safe_path(path, home)
    except qs.QuickstartError:
        return "unsafe-path", None
    state, entry = read_entry(client, home)
    if state == "ok" and client.fmt.endswith("toml"):
        try:
            import tomllib
            data = tomllib.loads(path.read_bytes().decode("utf-8"))
            first, nested = data.get("mcp_servers"), data.get("mcp")
            second = nested.get("servers") if isinstance(nested, dict) else None
            if isinstance(first, dict) and isinstance(second, dict) and (SERVER in first or SERVER in second):
                return "ambiguous", None
        except (OSError, ValueError):
            return "unreadable", None
    return state, entry


def _claude_project_entries(path: Path) -> list:
    """``memory-os-ai`` entries of the per-project scopes kept in ``~/.claude.json`` (``projects.*.mcpServers``).

    The user-scope entry is the only one we write or remove, but a project-scope entry depends
    on the service just as much. Project ``.mcp.json`` files live in the projects themselves and
    cannot be enumerated from here: a declared limit (next_steps says so).
    """
    try:
        projects = json.loads(path.read_bytes().decode("utf-8")).get("projects")
    except (OSError, ValueError, AttributeError, RecursionError):
        return []
    found = []
    for project in projects.values() if isinstance(projects, dict) else ():
        servers = project.get("mcpServers") if isinstance(project, dict) else None
        if isinstance(servers, dict) and SERVER in servers:
            found.append(servers[SERVER])
    return found


# ---------- plan (read-only) ----------

MANUAL_REMOVAL = (
    ("claude-desktop-extension",
     "If present: check Claude Desktop → Settings → Extensions and remove `memory-os-ai` "
     "(an extension is managed by the application, outside claude_desktop_config.json)."),
    ("claude-code-hooks",
     "If present: check the hooks of ~/.claude/settings.json and remove the `memory-os-admin hook` entries."),
    ("auxiliary-launchd-jobs",
     "If present: check ~/Library/LaunchAgents for the `.backup` and `.health` jobs written by "
     "`memory-os-admin setup --write-launchd`, `launchctl bootout` them and remove their plist."),
    ("python-environment",
     "If wanted: `pip uninstall memory-os-ai`, or remove the virtual environment by hand."),
)


def _manual_removal() -> list:
    return [{"kind": kind, "inspected": False, "present": None, "procedure": procedure}
            for kind, procedure in MANUAL_REMOVAL]


def _identity(settings) -> str:
    return qs.installation_identity({"MEMORY_V4_URL": settings.url, "MEMORY_V4_TOKEN_FILE": str(settings.token_file)})


def _service(settings, home: Path, python: str, *, run) -> dict:
    """The service definition as this installation would write it, and whether the plist on disk is it."""
    from . import provision
    label = settings.service_label or "local.memory-os-v4"
    path = home / "Library/LaunchAgents" / (label + ".plist")
    facts = {"label": label, "path": str(path), "state": "absent", "sha256": None, "loaded_state": "absent"}
    if not path.exists() and not path.is_symlink():
        return facts
    profile = qs._installed_profile(settings, home)
    try:
        resources = provision.plan(resources=("cl100k",)) if profile == "lexical" else provision.plan()
        service = qs._service_plan(settings, home, python, resources["env"], profile=profile or "semantic")
        facts.update(state=service["state"], sha256=service["sha256"], argv=service["argv"], profile=profile,
                     env={key: service["env"][key] for key in ("MEMORY_V4_TOKEN_FILE", "MEMORY_V4_DIR")},
                     resources={key: service["env"][key] for key in ("HF_HUB_CACHE", "MEMORY_V4_CL100K_PATH")
                                if key in service["env"]})
    except (qs.QuickstartError, provision.ProvisionError, OSError, ValueError, KeyError):
        facts.update(state="foreign-or-different", sha256=qs._sha(path))
    if facts["state"] != "owned":
        # Another interpreter than the installing one: the trusted install journal may still attest it.
        for directory, document in qs._install_journals(home / qs.JOURNAL_SUBDIR):
            recorded = document.get("service") if isinstance(document.get("service"), dict) else {}
            step = document.get("steps", {}).get("service", {})
            if (qs.journal_trusted(directory) and step.get("created") is True and recorded.get("path") == str(path)
                    and step.get("plist_sha256") == qs._sha(path) and isinstance(recorded.get("argv"), list)
                    and isinstance(recorded.get("env"), dict)):
                facts.update(state="owned", sha256=step["plist_sha256"], argv=recorded["argv"], env=recorded["env"],
                             resources=document.get("paths", {}).get("resources", {}))
                break
    if facts["state"] == "owned":
        facts["loaded_state"] = qs._job_state(facts, run=run)
    return facts


def plan(settings, *, home, python, run, clients=None, remove_existing=()):
    """Read-only plan: what would be removed, what stays, and why the service would stop or not."""
    known = {client.key for client in CLIENTS}
    selection = None if clients is None else tuple(clients)
    if selection is not None and set(selection) - known:
        raise UninstallError("Unknown clients: " + ", ".join(sorted(set(selection) - known)), code="client_unknown")
    selected = [client for client in CLIENTS if selection is None or client.key in selection]
    stray = sorted(set(remove_existing) - {client.key for client in selected})
    if stray:
        raise UninstallError("Consent names a client that is not selected: " + ", ".join(stray),
                             code="client_consent_unselected")
    identity = _identity(settings)
    journal_root = home / qs.JOURNAL_SUBDIR
    port = settings.port
    blockers, blocker_codes, items = [], [], []

    def block(code, message):
        blocker_codes.append(code)
        blockers.append(message)
    for client in selected:
        path = client.config_path(home)
        state, entry = _inspect(client, home)  # unsafe path or ambiguous file: never written through
        item = {"client": client.key, "name": client.name, "path": str(path),
                "file_sha256": qs._sha(path) if state != "unsafe-path" else None,
                "current_class": classify(entry), "ownership": None, "dependency": None}
        if state == "unsafe-path":
            item.update(action="keep-unsafe-path", dependency="indeterminate")
        elif state == "ambiguous":
            item.update(action="keep-ambiguous", dependency="indeterminate")
        elif state == "missing-config":
            item["action"] = "missing-config"
        elif state == "unreadable":
            item.update(action="keep-unreadable", dependency="indeterminate")
        elif entry is None:
            item["action"] = "absent"
        else:
            item["dependency"] = _dependency(entry, port, client.key)
            item["entry_sha256"] = qs._entry_hash(entry)
            explicit = _destination((entry.get("env") or {}).get("MEMORY_V4_URL")) if isinstance(entry.get("env"), dict) else None
            if qs.proof_of_ownership(journal_root, client.key, entry, identity):
                item.update(action="remove", ownership="journal")
            elif item["current_class"] == "relay" and (explicit is None or _verdict(explicit, port) != "none"):
                # Relay-looking and possibly ours, but nothing proves we wrote it (or the proof was revoked).
                consent = client.key in remove_existing
                item.update(action="remove" if consent else "keep-unproven", ownership="consent" if consent else "unproven")
            else:
                item["action"] = "keep-foreign"
        items.append(item)
    service = _service(settings, home, python, run=run)
    if any(item["client"] == "claude-desktop" and item["action"] == "remove" for item in items):
        running, _, _ = qs._job_run(["pgrep", "-x", "Claude"], run=run, timeout=5)
        if running != 1:
            block("client_app_running", "quit Claude Desktop before apply; live config may be rewritten by the app")
    removing = {item["client"] for item in items if item["action"] == "remove"}
    remaining = remaining_dependencies(home, port, removed=removing)
    if service["state"] == "absent":
        service["action"] = "absent"
    elif remaining:
        service["action"] = "keep-clients-remaining"
    else:
        service["action"] = "stop"
    service.update(remaining_clients=sorted(remaining), remaining_dependencies=dict(sorted(remaining.items())))
    # A definition that is not ours refuses everything. A loaded job that cannot be attested refuses
    # the plan only when the plan would stop it; a kept service is not touched (and a later stop
    # re-attests the job anyway, §6).
    if service["state"] not in {"absent", "owned"} or (
            service["action"] == "stop" and service["loaded_state"] in {"unknown", "foreign-or-unattested"}):
        block("uninstall_service_foreign", "uninstall_service_foreign: the service definition or the loaded job is "
              "not the one this installation owns; nothing is stopped or removed")
    paths = {"store": str(settings.store_dir), "backups": str(settings.backup_dir),
             "backup-key": str(settings.backup_key), "token": str(settings.token_file),
             "resources": service.get("resources") or _recorded_resources(home),
             "journals": str(journal_root), "logs": str(settings.ops_dir / "logs"),
             "python-environment": str(Path(python).parent.parent)}
    retained = qs.retained_items(paths) + [
        {"kind": "python-environment", "path": paths["python-environment"],
         "exists": Path(paths["python-environment"]).exists()}]
    return {"version": 1, "ok": not blockers, "stage": "preview", "writes": "none (preview)",
            "python": python, "selection": None if selection is None else list(selection),
            "remove_existing": sorted(remove_existing), "selective": selection is not None,
            "clients": items, "service": service, "blockers": blockers, "blocker_codes": blocker_codes,
            "nothing_to_do": not removing and service["state"] == "absent",
            "retained": retained, "data_deleted": False, "complete": False,
            "scope": _scope(items, service), "manual_removal_required": _manual_removal(),
            "next_steps": _next_steps(remaining)}


def _recorded_resources(home: Path) -> dict:
    for _, document in qs._install_journals(home / qs.JOURNAL_SUBDIR):
        resources = document.get("paths", {}).get("resources") if isinstance(document.get("paths"), dict) else None
        if isinstance(resources, dict) and resources:
            return resources
    return {}


def _scope(items, service) -> dict:
    return {"clients_inspected": [item["client"] for item in items],
            "service_label": service["label"],
            "data": "never removed, restored or opened",
            "not_inspected": [kind for kind, _ in MANUAL_REMOVAL]}


def _next_steps(remaining: dict) -> list:
    steps = ["not inspected: project-level .mcp.json files (Claude Code and others) live in each project; an entry "
             "there that reaches this service is not seen — check them before the service stops"] if not remaining else []
    for key, dependency in sorted(remaining.items()):
        reason = ("a memory-os-ai entry points to this service" if dependency == "wired" else
                  "a memory-os-ai entry may depend on this service, or its configuration cannot be read")
        steps.append(f"{key}: {reason} ({dependency}); the service is kept. Remove that entry "
                     f"(uninstall --clients {key}, with --remove-existing {key} for an unproven relay entry, "
                     "or by hand), or check where it connects, then run uninstall again")
    return steps


# ---------- apply ----------

def _changed(plan_document: dict, current: dict) -> bool:
    def client_view(document):
        return [(item["client"], item["action"], item["file_sha256"], item.get("entry_sha256"))
                for item in document["clients"]]

    def service_view(document):
        service = document["service"]
        # The announced fate of the service is part of the plan: a service shown as kept is never
        # stopped because a client outside the selection vanished after the preview.
        return (service["state"], service["sha256"], service["loaded_state"], service.get("argv"), service["path"],
                service["action"], service["remaining_clients"], service["remaining_dependencies"])
    return (not current["ok"] or client_view(plan_document) != client_view(current)
            or service_view(plan_document) != service_view(current))


def _appended_toml(journal_root: Path, client_key: str, digest: str):
    """The exact text our install appended for this TOML entry, read from the journal that proves it."""
    for _, document in qs._trusted_journals(journal_root):
        for record in document["records"]:
            if (isinstance(record, dict) and record.get("client") == client_key
                    and qs._record_digest(record) == digest and isinstance(record.get("toml_appended"), str)):
                return record["toml_appended"]
    return None


def apply(plan, settings, *, home, run, stop_timeout=10.0, sleep=time.sleep):
    """Explicit uninstall of what ``plan`` showed: clients first, the service last, the data never."""
    preview = plan
    if not preview.get("ok"):
        raise UninstallError("Preflight refused: " + "; ".join(preview.get("blockers", [])), code="preflight_refused")
    selection = preview.get("selection")
    # 1. Global revalidation before any mutation: every client file, the plist, the loaded job.
    current = globals()["plan"](settings, home=home, python=preview["python"], run=run,
                                clients=None if selection is None else tuple(selection),
                                remove_existing=tuple(preview.get("remove_existing", ())))
    if _changed(preview, current):
        raise UninstallError("Something changed since the preview; nothing removed, the service keeps running",
                             code="uninstall_changed_since_preview")
    result = {"version": 1, "ok": True, "partial": False, "selective": bool(preview.get("selective")),
              "stage": "apply", "stamp": None, "journal": None, "clients": [], "nothing_to_do": preview["nothing_to_do"],
              "service": {"outcome": "absent", "remaining_clients": [], "remaining_dependencies": {}},
              "retained": preview["retained"], "data_deleted": False, "complete": False, "scope": preview["scope"],
              "manual_removal_required": preview["manual_removal_required"], "next_steps": []}
    if preview["nothing_to_do"]:
        result["clients"] = [{"client": item["client"], "status": _kept_status(item["action"])} for item in preview["clients"]]
        return result
    journal_dir = qs.new_journal(home / JOURNAL_SUBDIR)
    journal = journal_dir / "uninstall.json"
    result.update(stamp=journal_dir.name, journal=str(journal))

    def record():
        qs._atomic_write(journal, json.dumps(result, indent=1, ensure_ascii=False))
        journal.chmod(0o600)
    record()
    journal_root = home / qs.JOURNAL_SUBDIR
    port = settings.port
    # 2. Clients first; a late conflict skips that client only and makes the result partial.
    for item in preview["clients"]:
        client = next(c for c in CLIENTS if c.key == item["client"])
        done = {"client": client.key, "status": _kept_status(item["action"]), "ownership": item["ownership"]}
        result["clients"].append(done)
        if item["action"] != "remove":
            continue
        path = client.config_path(home)
        state, entry = read_entry(client, home)
        if (qs._sha(path) != item["file_sha256"] or state != "ok" or entry is None
                or qs._entry_hash(entry) != item["entry_sha256"]):
            done["status"] = "conflict-file-changed-since-preview"
            record()
            continue
        digest = item["entry_sha256"]
        appended = _appended_toml(journal_root, client.key, digest) if client.fmt.endswith("toml") else None
        try:
            backup = journal_dir / f"{client.key}.bak"
            done.update(backup=str(backup), backup_sha256=qs.backup_client_file(path, backup), backup_may_hold_secrets=True)
        except OSError:
            done["status"] = "backup-failed-entry-untouched"
            record()
            continue
        try:
            # Validated before anything is revoked or written: an unsafe removal leaves entry AND proof intact.
            qs.removal_text(client, home, toml_appended=appended, expected_sha256=digest)
        except qs.EntryChanged:
            done["status"] = "conflict-file-changed-since-preview"
            record()
            continue
        except (OSError, ValueError, qs.QuickstartError, RecursionError):
            done["status"] = "refused-removal-manual"
            record()
            continue
        # Revocation first, removal second. Revocations already written are never undone.
        revoked = []
        try:
            qs.revoke_proofs(journal_root, client.key, digest, journal_dir.name, progress=revoked)
        except (OSError, ValueError, qs.QuickstartError):
            done.update(status="revocation-failed-entry-untouched", revoked_journals=len(revoked))
            record()
            continue
        done["revoked_journals"] = len(revoked)
        done["status"] = "failed-entry-kept-unproven"  # write-ahead: true until the removal is verified
        record()
        try:
            qs.remove_client_entry(client, home, run=run, toml_appended=appended, expected_sha256=digest)
            state, entry = read_entry(client, home)
            if state == "ok" and entry is None:
                done["status"] = "removed"
        except qs.EntryChanged:
            # Changed DURING the revocation: no longer the entry the plan showed; it is left alone.
            done["status"] = "conflict-file-changed-since-preview"
        except (OSError, ValueError, KeyError, qs.QuickstartError, subprocess.SubprocessError):
            pass
        record()
    incomplete = [done["client"] for done, item in zip(result["clients"], preview["clients"])
                  if item["action"] == "remove" and done["status"] != "removed"]
    # 3. The service last: only when every planned removal succeeded and nothing may still use it.
    service, outcome = preview["service"], result["service"]
    remaining = remaining_dependencies(home, port)
    outcome.update(remaining_clients=sorted(remaining), remaining_dependencies=dict(sorted(remaining.items())))
    if incomplete:
        outcome["outcome"] = "kept-clients-incomplete" if service["state"] == "owned" else "absent"
        result.update(ok=False, partial=True, code="uninstall_partial")
        result["next_steps"].append("not removed: " + ", ".join(incomplete) + "; preview again (uninstall), then "
                                    "apply again; the service is kept meanwhile")
    elif service["state"] != "owned":
        outcome["outcome"] = "absent"
    elif remaining:
        outcome["outcome"] = "kept-clients-remaining"
        result["next_steps"] += _next_steps(remaining)
    elif service["action"] != "stop":
        # The plan announced a kept service; whatever happened since, this run does not stop it.
        outcome["outcome"] = "kept-as-planned"
        result["next_steps"].append("the dependencies seen at the preview are gone; preview again (uninstall) "
                                    "to plan the stop of the service")
    else:
        stopped = qs.stop_owned_service(service, service["sha256"], journal_dir, run=run,
                                        stop_timeout=stop_timeout, sleep=sleep)
        code = stopped.pop("code", None)
        outcome.update(stopped)
        if code:
            result.update(ok=False, partial=True, code=code)
            result["next_steps"].append(f"the stop is not confirmed and the plist is kept: run `launchctl bootout "
                                        f"gui/{os.getuid()}/{service['label']}` by hand, then uninstall again")
    result["stage"] = "done"
    record()
    return result


def _kept_status(action: str) -> str:
    return {"keep-unproven": "kept-unproven", "keep-foreign": "kept-foreign", "keep-unreadable": "kept-unreadable",
            "keep-unsafe-path": "kept-unsafe-path", "keep-ambiguous": "kept-ambiguous", "remove": "planned"}.get(action, action)


def settings_from_service(settings, home: Path):
    """Settings of the INSTALLED service (URL, token file, store, ops directory) read from its definition.

    A fresh shell rarely carries the variables the install chose; the plist does. Only path-like
    text values are taken, and ownership is still decided by ``plan``.
    """
    import plistlib
    from dataclasses import replace
    label = settings.service_label or "local.memory-os-v4"
    path = home / "Library/LaunchAgents" / (label + ".plist")
    try:
        if path.is_symlink() or not path.is_file():
            return settings
        env = plistlib.loads(path.read_bytes()).get("EnvironmentVariables", {})
        if not isinstance(env, dict):
            return settings
        changes = {}
        if isinstance(env.get("MEMORY_V4_URL"), str):
            changes["url"] = env["MEMORY_V4_URL"]
        for name, field in (("MEMORY_V4_TOKEN_FILE", "token_file"), ("MEMORY_V4_DIR", "store_dir"),
                            ("MEMORY_V4_OPS_DIR", "ops_dir")):
            if isinstance(env.get(name), str) and os.path.isabs(env[name]):
                changes[field] = Path(env[name])
        return replace(settings, service_label=label, **changes)
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException):
        return settings
