"""SETUP_4_1_0.md §4, §6, §7, §8: clause D2 on a whole fake machine.

Preview writes nothing; every touched config is backed up; rollback and uninstall keep every
byte of memory written since the install; no secret reaches a journal, a report or an error.
"""
from __future__ import annotations

import inspect
import json
import plistlib
from pathlib import Path

import pytest

from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.clients import classify
from setup_fixture_410 import (
    EXISTING, PATHS, SECRETS, SIX, all_text, client_files, data_inventory, install, plant_data, prepare,
    servers, set_entry, setup_context, tree,
)

PLIST = "Library/LaunchAgents/test.memory-os.plist"
STATE = ".local/state/memory-os"


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


def uninstaller():
    from memory_os_v4.ops import uninstall
    return uninstall


def claude_on_path(name):
    return "/usr/local/bin/" + name


# ---- preview: zero writes, proven by a full inventory ----

@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_preview_of_a_full_install_changes_nothing_on_the_machine(c, profile):
    before = tree(c.root)
    plan = prepare(c, profile=profile, clients=SIX, which=claude_on_path)
    assert plan["ok"] is True and plan["writes"] == "none (preview)" and plan["stage"] == "preview"
    assert tree(c.root) == before  # every path, mode, mtime and byte below the temporary root
    assert set(c.system.verbs("launchctl")) <= {"print"} and c.system.verbs("claude") == []
    assert c.seen.apply_calls == [] and not c.settings.token_file.exists()
    assert [item["action"] for item in plan["clients"]] == ["create"] * 6


@pytest.mark.parametrize("profile", ["lexical", "semantic"])
def test_refused_preview_changes_nothing_either(c, profile):
    set_entry(c.home, "cursor", EXISTING["legacy-v3"])
    before = tree(c.root)
    plan = prepare(c, profile=profile, clients=SIX, which=claude_on_path)
    assert plan["ok"] is False and tree(c.root) == before


def test_client_level_preview_changes_nothing(c):
    """Green today: the client preview is already write-free for the six clients."""
    before = tree(c.root)
    plan = qs.preview(home=c.home, python=c.python, env={"MEMORY_V4_URL": c.settings.url}, clients=SIX)
    assert tree(c.root) == before and plan["writes"] == "none (preview)"
    assert not any(secret in json.dumps(plan) for secret in SECRETS)


# ---- stable refusal codes ----

@pytest.mark.parametrize("case,code", [
    ("unknown-client", "client_unknown"), ("unreadable-config", "client_config_manual"),
    ("foreign-entry", "client_entry_conflict"), ("port", "port_or_label_occupied"),
    ("foreign-plist", "service_foreign"), ("store", "store_not_empty"), ("build", "build_unqualified"),
    ("disk", "disk_guard"), ("desktop-running", "client_app_running"), ("cli-missing", "client_cli_missing"),
])
def test_every_preflight_refusal_has_a_stable_code_and_writes_nothing(c, monkeypatch, case, code):
    from types import SimpleNamespace
    from memory_os_v4 import readiness
    clients, options = ("cursor",), {}
    if case == "unknown-client":
        clients = ("no-such-client",)
    elif case == "unreadable-config":
        (c.home / PATHS["cursor"]).write_text("{invalid")
    elif case == "foreign-entry":
        set_entry(c.home, "cursor", EXISTING["other"])
    elif case == "port":
        monkeypatch.setattr(qs, "_port_state", lambda port: "listening")
    elif case == "foreign-plist":
        path = c.home / PLIST
        path.parent.mkdir(parents=True)
        path.write_bytes(plistlib.dumps({"Label": "test.memory-os", "ProgramArguments": ["/foreign"]}))
    elif case == "store":
        plant_data(c)
    elif case == "build":
        monkeypatch.setattr(readiness, "build_identity", lambda: {"commit": "a" * 40, "identity_source": "env_declared"})
    elif case == "disk":
        monkeypatch.setattr(qs.shutil, "disk_usage", lambda *a: SimpleNamespace(free=1_000_000_000))
    elif case == "desktop-running":
        clients, c.system.desktop_running = ("claude-desktop",), True
    else:
        clients, options = ("claude-code",), {"which": lambda name: None}
    before = tree(c.root)
    plan = prepare(c, profile="lexical", clients=clients, **options)
    assert plan["ok"] is False and code in plan["blocker_codes"]
    assert len(plan["blocker_codes"]) == len(plan["blockers"])
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "preflight_refused"
    assert tree(c.root) == before


# ---- apply: the six clients, each backed up ----

def test_install_configures_the_six_clients_and_backs_up_each_original(c):
    originals = {key: (c.home / PATHS[key]).read_bytes() for key in SIX}
    result = install(c, profile="lexical", clients=SIX, which=claude_on_path)
    assert result["ok"] is True and result["stage"] == "complete"
    records = {record["client"]: record for record in result["clients"]["records"]}
    assert {key: record["status"] for key, record in records.items()} == dict.fromkeys(SIX, "applied")
    for key in SIX:
        backup = Path(records[key]["backup"])
        assert backup.read_bytes() == originals[key] and backup.stat().st_mode & 0o777 == 0o600
        assert classify(servers(c.home, key)["memory-os-ai"]) == "relay"
        assert "other" in servers(c.home, key) or "filesystem" in servers(c.home, key)
    assert c.system.verbs("launchctl").count("bootstrap") == 1


def test_client_level_backup_is_the_pre_image(c, tmp_path):
    """Green today: a backup is taken before each write, private, inside the journal."""
    env = {"MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file)}
    originals = {key: (c.home / PATHS[key]).read_bytes() for key in SIX}
    plan = qs.preview(home=c.home, python=c.python, env=env, clients=SIX)
    result = qs.apply_clients(plan, home=c.home, python=c.python, env=env, run=c.system,
                              journal_root=c.home / STATE / "quickstart")
    for record in result["records"]:
        assert record["status"] == "applied"
        assert Path(record["backup"]).read_bytes() == originals[record["client"]]
    assert (c.home / STATE / "quickstart").stat().st_mode & 0o777 == 0o700


# ---- no secret logged ----

def test_no_secret_reaches_any_journal_report_or_plist(c):
    result = install(c, profile="lexical", clients=SIX, which=claude_on_path)
    token = c.settings.token_file.read_text().strip()
    assert len(token) >= 43
    secrets = (token, *SECRETS)
    journals = all_text(c.home / STATE)
    plist = (c.home / PLIST).read_text()
    shown = json.dumps(result) + journals + plist
    assert not any(secret in shown for secret in secrets)
    assert str(c.settings.token_file) in plist  # the path, never the value
    for backup in (c.home / STATE).rglob("*.bak"):
        assert backup.stat().st_mode & 0o777 == 0o600 and backup.parent.stat().st_mode & 0o777 == 0o700


def test_a_pre_existing_secret_survives_only_in_the_private_backup(c):
    """An old direct-HTTP entry carried this very token: the verbatim 0600 backup keeps it, nothing else does."""
    token = "T" * 43
    c.settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    c.settings.token_file.write_text(token + "\n")
    c.settings.token_file.chmod(0o600)
    set_entry(c.home, "cursor", {"type": "http", "url": c.settings.url, "headers": {"Authorization": "Bearer " + token}})
    result = install(c, profile="lexical", clients=("cursor",), replace_existing=("cursor",))
    assert result["ok"] is True
    backup = Path(result["clients"]["records"][0]["backup"])
    assert token in backup.read_text() and backup.stat().st_mode & 0o777 == 0o600
    assert token not in json.dumps(result) + all_text(c.home / STATE) + (c.home / PLIST).read_text()
    assert result["clients"]["records"][0]["backup_may_hold_secrets"] is True


def test_cli_output_of_a_full_install_holds_no_secret(c, monkeypatch, capsys):
    """The real command handler, with the fake machine injected below it."""
    from memory_os_v4.ops import cli
    from memory_os_v4.ops.doctor import Check
    from setup_fixture_410 import fake_probe
    real_prepare, real_apply = qs.prepare, qs.apply_install

    def fake_prepare(settings, **kwargs):
        plan = real_prepare(c.settings, home=c.home, python=c.python, run=c.system, which=claude_on_path,
                            **{k: v for k, v in kwargs.items() if k not in ("home", "python", "run", "which")})
        c.system.service = plan.get("service")
        return plan

    def fake_apply(plan, settings, **kwargs):
        return real_apply(plan, c.settings, home=c.home, run=c.system, probe=fake_probe,
                          doctor_run=lambda s: [Check("server", "PASS", "synthetic")],
                          **{k: v for k, v in kwargs.items() if k in ("download_approved", "demo")})
    monkeypatch.setattr(qs, "prepare", fake_prepare)
    monkeypatch.setattr(qs, "apply_install", fake_apply)
    monkeypatch.setattr(qs, "settings_for_install", lambda settings, **kw: (c.settings, kw.get("backend") or "sqlite"))
    argv = ["quickstart", "--profile", "lexical", "--clients", ",".join(SIX)]
    shown = []
    for extra in ([], ["--report"], ["--apply"], ["--apply", "--report"]):
        assert cli.main(argv + extra) == 0
        out = capsys.readouterr()
        shown.append(out.out + out.err)
    token = c.settings.token_file.read_text().strip()
    assert not any(secret in text for text in shown for secret in (token, *SECRETS))
    assert json.loads(shown[2])["profile"] == "lexical"


def test_refusal_messages_hold_no_secret(c):
    set_entry(c.home, "cursor", EXISTING["direct-http"])
    plan = prepare(c, profile="lexical", clients=("cursor",))
    assert plan["ok"] is False and not any(secret in json.dumps(plan) for secret in SECRETS)
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert not any(secret in str(refusal.value) for secret in SECRETS)


# ---- rollback: clients (and, on request, the service this install created); never the data ----

def test_default_undo_restores_the_clients_and_leaves_service_and_data(c):
    originals = {key: (c.home / PATHS[key]).read_bytes() for key in SIX}
    result = install(c, profile="lexical", clients=SIX, which=claude_on_path)
    plant_data(c)
    data = data_inventory(c)
    out = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert {item["client"]: item["undo"] for item in out["results"]} == dict.fromkeys(SIX, "restored")
    assert {key: (c.home / PATHS[key]).read_bytes() for key in SIX} == originals
    assert data_inventory(c) == data and "bootout" not in c.system.verbs()
    assert (c.home / PLIST).exists() and out["service"] == {"undo": "not-requested"}


def test_rollback_with_service_keeps_every_write_made_since_the_install(c):
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    plant_data(c)
    data = data_inventory(c)
    assert len(data) >= 5  # store, wal, backup, key, token
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert data_inventory(c) == data  # new writes kept: no file removed, none restored from a copy
    assert c.system.verbs().count("bootout") == 1 and c.system.loaded is False
    assert not (c.home / PLIST).exists()
    moved = Path(out["service"]["plist_moved_to"])
    assert out["service"]["undo"] == "stopped-and-plist-moved" and moved.is_file()
    assert moved.parent.name == result["stamp"]  # kept in the journal of that install, not deleted
    retained = {item["kind"]: item for item in out["retained"]}
    assert {"store", "backups", "backup-key", "token", "resources"} <= set(retained)
    assert retained["store"]["path"] == str(c.settings.store_dir) and retained["store"]["exists"] is True
    assert out["data_restored_from_copy"] is False


def test_rollback_never_stops_a_service_this_install_did_not_create(c):
    install(c, profile="lexical")
    second = install(c, profile="lexical", clients=("vscode",))
    assert second["steps"].get("service", {}).get("created") is not True
    out = qs.undo(second["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["service"]["undo"] == "not-created-by-this-install"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


def test_rollback_refuses_a_service_changed_since_the_install(c):
    result = install(c, profile="lexical")
    path = c.home / PLIST
    path.write_text(path.read_text().replace("--warm", "--warm-edited"))
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["service"]["undo"] == "refused-changed-since-install"
    assert "bootout" not in c.system.verbs() and path.exists()


def test_client_level_undo_never_touches_store_token_or_backups(c):
    """Green today: undo is a semantic removal of our entry; data is out of its reach."""
    env = {"MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file)}
    plan = qs.preview(home=c.home, python=c.python, env=env, clients=("cursor", "vscode"))
    result = qs.apply_clients(plan, home=c.home, python=c.python, env=env, run=c.system,
                              journal_root=c.home / STATE / "quickstart")
    plant_data(c)
    c.settings.token_file.write_text("t" * 43 + "\n")
    data = tree(c.settings.store_dir), tree(c.settings.backup_dir), c.settings.token_file.read_bytes()
    out = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert all(item["undo"] == "restored" for item in out["results"])
    assert (tree(c.settings.store_dir), tree(c.settings.backup_dir), c.settings.token_file.read_bytes()) == data
    assert c.system.calls == []


# ---- uninstall: entries and service go, data stays ----

def installed(c, clients=SIX):
    result = install(c, profile="lexical", clients=clients, which=claude_on_path)
    plant_data(c)
    return result


def uninstall_plan(c, **kwargs):
    return uninstaller().plan(c.settings, home=c.home, python=c.python, run=c.system, **kwargs)


def our_entry(c, key, python="/old/venv/bin/python"):
    """What our tool writes for this installation — here planted by hand, so no journal proves it."""
    from memory_os_v4.ops.clients import CLIENTS
    client = next(item for item in CLIENTS if item.key == key)
    return qs.desired_entry(client, python, {"MEMORY_V4_URL": c.settings.url,
                                             "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file),
                                             "MEMORY_V4_OPS_DIR": str(c.settings.ops_dir)})


def test_uninstall_preview_writes_nothing_and_lists_what_stays(c):
    installed(c)
    before = tree(c.root)
    calls = len(c.system.calls)
    plan = uninstall_plan(c)
    assert tree(c.root) == before and plan["ok"] is True and plan["writes"] == "none (preview)"
    assert all(argv[:2] in (["launchctl", "print"], ["pgrep", "-x"]) for argv in c.system.calls[calls:])
    actions = {item["client"]: item["action"] for item in plan["clients"]}
    assert {key: actions[key] for key in SIX} == dict.fromkeys(SIX, "remove")
    assert plan["service"]["state"] == "owned"
    kinds = {item["kind"]: item for item in plan["retained"]}
    assert {"store", "backups", "backup-key", "token", "resources", "journals", "logs"} <= set(kinds)
    assert not any(secret in json.dumps(plan) for secret in SECRETS)


def test_uninstall_removes_entries_and_service_and_keeps_every_byte_of_data(c):
    install_result = installed(c)
    data = data_inventory(c)
    journals = {str(p) for p in (c.home / STATE / "quickstart").rglob("*") if p.is_file()}
    result = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    assert result["ok"] is True and result["data_deleted"] is False
    assert {item["client"]: item["status"] for item in result["clients"] if item["client"] in SIX} == dict.fromkeys(SIX, "removed")
    for key in SIX:
        table = servers(c.home, key)
        assert "memory-os-ai" not in table and ("other" in table or "filesystem" in table)
    assert c.system.verbs().count("bootout") == 1 and not (c.home / PLIST).exists()
    assert Path(result["service"]["plist_moved_to"]).is_file()
    assert data_inventory(c) == data
    assert all(Path(path).exists() for path in journals)  # install journals and backups are kept
    assert all(Path(item["path"]).exists() for item in result["retained"] if item["exists"])
    assert install_result["stamp"] != result["stamp"]


def test_uninstall_restores_the_original_client_files(c):
    installed(c)
    uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    assert {key: (c.home / PATHS[key]).read_text() for key in SIX} == client_files()


def test_uninstall_backs_up_each_config_it_touches_and_logs_no_secret(c):
    installed(c)
    token = c.settings.token_file.read_text().strip()
    before = {key: (c.home / PATHS[key]).read_bytes() for key in SIX}
    result = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    journal = Path(result["journal"])
    assert journal.stat().st_mode & 0o777 == 0o600 and journal.parent.stat().st_mode & 0o777 == 0o700
    for item in result["clients"]:
        if item["client"] in SIX:
            assert Path(item["backup"]).read_bytes() == before[item["client"]]
    shown = json.dumps(result) + all_text(journal.parent)
    assert not any(secret in shown for secret in (token, *SECRETS))


def test_uninstall_keeps_entries_that_are_not_this_installation(c):
    installed(c)
    set_entry(c.home, "cursor", EXISTING["legacy-v3"])
    other_install = {"command": "/other/venv/bin/python", "args": ["-m", "memory_os_v4.ops.relay"],
                     "env": {"MEMORY_V4_TOKEN_FILE": "/other/http.token", "MEMORY_V4_URL": "http://127.0.0.1:9999/mcp/"}}
    set_entry(c.home, "vscode", other_install)
    plan = uninstall_plan(c)
    actions = {item["client"]: item["action"] for item in plan["clients"]}
    assert actions["cursor"] == "keep-foreign" and actions["vscode"] == "keep-foreign"
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, "cursor")["memory-os-ai"] == EXISTING["legacy-v3"]
    assert servers(c.home, "vscode")["memory-os-ai"] == other_install
    statuses = {item["client"]: item["status"] for item in result["clients"]}
    assert statuses["cursor"] == statuses["vscode"] == "kept-foreign"
    assert result["service"]["outcome"] == "stopped-and-plist-moved"  # neither points to our service


@pytest.mark.parametrize("case", ["hand-written-copy", "env-wrapper", "extra-argument"])
def test_uninstall_removes_only_what_our_journal_proves(c, case):
    """Same destination, relay-looking, but not written by us: kept, and the service stays for it."""
    installed(c, clients=("vscode",))
    entry = our_entry(c, "cursor")
    if case == "env-wrapper":
        entry = {**entry, "command": "/usr/bin/env", "args": ["python", "-m", "memory_os_v4.ops.relay"]}
    elif case == "extra-argument":
        entry = {**entry, "args": [*entry["args"], "--debug"]}
    set_entry(c.home, "cursor", entry)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert (by["vscode"]["action"], by["vscode"]["ownership"]) == ("remove", "journal")
    assert (by["cursor"]["action"], by["cursor"]["ownership"]) == ("keep-unproven", "unproven")
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, "cursor")["memory-os-ai"] == entry and "memory-os-ai" not in servers(c.home, "vscode")
    assert result["service"]["outcome"] == "kept-clients-remaining"
    assert result["service"]["remaining_clients"] == ["cursor"]
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


def test_uninstall_removes_an_unproven_entry_only_with_named_consent(c):
    installed(c, clients=("vscode",))
    entry = our_entry(c, "cursor")
    set_entry(c.home, "cursor", entry)
    plan = uninstall_plan(c, remove_existing=("cursor",))
    by = {item["client"]: item for item in plan["clients"]}
    assert (by["cursor"]["action"], by["cursor"]["ownership"]) == ("remove", "consent")
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert "memory-os-ai" not in servers(c.home, "cursor")
    assert result["service"]["outcome"] == "stopped-and-plist-moved"
    with pytest.raises(uninstaller().UninstallError) as refusal:
        uninstall_plan(c, clients=("vscode",), remove_existing=("cursor",))
    assert refusal.value.code == "client_consent_unselected"


# ---- selective uninstall: the shared service outlives a partial selection ----

def test_selective_uninstall_keeps_the_service_while_other_clients_are_wired_to_it(c):
    installed(c)
    plan = uninstall_plan(c, clients=("cursor",))
    assert [item["client"] for item in plan["clients"]] == ["cursor"]
    assert plan["service"]["action"] == "keep-clients-remaining"
    assert plan["service"]["remaining_clients"] == sorted(set(SIX) - {"cursor"})
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["ok"] is True and result["selective"] is True and result["partial"] is False
    assert result["service"]["outcome"] == "kept-clients-remaining"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
    assert "memory-os-ai" not in servers(c.home, "cursor")
    for key in set(SIX) - {"cursor"}:
        assert classify(servers(c.home, key)["memory-os-ai"]) == "relay"


def test_service_stops_only_when_the_last_wired_client_is_removed(c):
    installed(c, clients=("cursor", "vscode", "codex"))
    for selection in (("cursor",), ("codex",)):
        result = uninstaller().apply(uninstall_plan(c, clients=selection), c.settings, home=c.home, run=c.system)
        assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()
    last = uninstaller().apply(uninstall_plan(c, clients=("vscode",)), c.settings, home=c.home, run=c.system)
    assert last["service"]["outcome"] == "stopped-and-plist-moved" and last["service"]["remaining_clients"] == []
    assert c.system.verbs().count("bootout") == 1 and not (c.home / PLIST).exists()


def test_remaining_clients_are_looked_up_in_the_whole_registry_not_in_the_selection(c):
    """A client the user did not name, and one our registry knows outside the six, both keep the service."""
    from setup_fixture_410 import write
    installed(c, clients=("cursor",))
    write(c.home, ".codeium/windsurf/mcp_config.json", json.dumps({"mcpServers": {"memory-os-ai": our_entry(c, "windsurf")}}))
    plan = uninstall_plan(c, clients=("cursor",))
    assert plan["service"]["remaining_clients"] == ["windsurf"]
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()


# ---- a removal durably revokes the proof it used ----

def journal_records(c, key):
    found = []
    for path in sorted((c.home / STATE / "quickstart").glob("*/journal.json")):
        found += [record for record in json.loads(path.read_text())["records"] if record["client"] == key]
    return found


def test_uninstall_revokes_the_proof_so_a_hand_made_copy_is_not_removed_later(c):
    """Install Cursor + VS Code, uninstall Cursor, re-create its entry by hand, uninstall Cursor again."""
    installed(c, clients=("cursor", "vscode"))
    entry = servers(c.home, "cursor")["memory-os-ai"]
    first = uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    assert {item["client"]: item["status"] for item in first["clients"]} == {"cursor": "removed"}
    assert first["service"]["outcome"] == "kept-clients-remaining" and first["service"]["remaining_clients"] == ["vscode"]
    records = journal_records(c, "cursor")
    assert records and all(record["revoked"] is True and record["revoked_by"] == first["stamp"] for record in records)
    assert all("revoked" not in record for record in journal_records(c, "vscode"))

    set_entry(c.home, "cursor", entry)  # byte-for-byte the entry our journal once proved
    plan = uninstall_plan(c, clients=("cursor",))
    item, = plan["clients"]
    assert (item["action"], item["ownership"]) == ("keep-unproven", "unproven")
    second = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, "cursor")["memory-os-ai"] == entry  # not removed without named consent
    assert second["service"]["outcome"] == "kept-clients-remaining"
    assert second["service"]["remaining_clients"] == ["cursor", "vscode"]
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True


def test_revoked_proof_no_longer_lets_quickstart_replace_the_entry(c):
    """The very entry our journal proved, re-created by hand after its removal, seen from a new interpreter."""
    installed(c, clients=("cursor", "vscode"))
    entry = servers(c.home, "cursor")["memory-os-ai"]
    other = c.home / "runtime2/bin/python"
    other.parent.mkdir(parents=True)
    other.write_text("# synthetic executable, never executed")
    other.chmod(0o700)
    c.python = str(other)
    proven, = [item for item in prepare(c, profile="lexical", clients=("cursor",))["clients"]]
    assert (proven["action"], proven["ownership"]) == ("replace", "journal")  # before the removal: proven
    uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    set_entry(c.home, "cursor", entry)
    item, = prepare(c, profile="lexical", clients=("cursor",))["clients"]
    assert (item["action"], item["ownership"]) == ("refuse-existing-relay-unproven", "unproven")


def test_the_proof_is_revoked_before_the_entry_is_removed(c, monkeypatch):
    """Fail-safe order: if the removal then fails, the entry is left unproven, never removable by default."""
    installed(c, clients=("cursor", "vscode"))
    plan = uninstall_plan(c, clients=("cursor",))
    real = qs._atomic_write
    order = []

    def spy(path, text, **expectation):
        name = Path(path).name
        if name == "journal.json" and '"revoked": true' in text:
            order.append("revoke")
        if name == "mcp.json" and str(path).endswith(PATHS["cursor"]):
            order.append("remove")
            raise OSError("disk full")
        return real(path, text, **expectation)
    monkeypatch.setattr(qs, "_atomic_write", spy)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert order[:2] == ["revoke", "remove"]
    assert result["ok"] is False and result["partial"] is True
    assert {item["client"]: item["status"] for item in result["clients"]} == {"cursor": "failed-entry-kept-unproven"}
    monkeypatch.setattr(qs, "_atomic_write", real)
    assert "memory-os-ai" in servers(c.home, "cursor")
    assert uninstall_plan(c, clients=("cursor",))["clients"][0]["action"] == "keep-unproven"


def test_every_matching_journal_is_revoked_and_nothing_else_is_touched(c):
    """Three journals prove the same Cursor entry (ours, a copy of ours, a 4.0.9 one); a fourth does not."""
    import shutil
    result = installed(c, clients=("cursor", "vscode"))
    root = c.home / STATE / "quickstart"
    entry = servers(c.home, "cursor")["memory-os-ai"]
    ours = root / result["stamp"]
    twin = root / ("20261101-101010-" + "b" * 32)
    shutil.copytree(ours, twin)
    for path in twin.iterdir():
        path.chmod(0o600)
    twin.chmod(0o700)

    def legacy(stamp, applied):
        directory = root / stamp
        directory.mkdir(mode=0o700)
        journal = directory / "journal.json"
        journal.write_text(json.dumps({"version": 1, "stamp": stamp, "records": [{
            "client": "cursor", "path": str(c.home / PATHS["cursor"]), "action": "create", "status": "applied",
            "previous_entry": None, "applied_entry": applied, "method": "json-merge"}]}))
        journal.chmod(0o600)
        return journal
    old_format = legacy("20261001-120000-" + "a" * 32, entry)
    unrelated = legacy("20260901-120000-" + "c" * 32, {**entry, "command": "/some/other/venv/bin/python"})
    unrelated_bytes = unrelated.read_bytes()
    vscode_before = [record for record in json.loads((ours / "journal.json").read_text())["records"]
                     if record["client"] == "vscode"]

    done = uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    for journal in (ours / "journal.json", twin / "journal.json", old_format):
        records = [r for r in json.loads(journal.read_text())["records"] if r["client"] == "cursor"]
        assert records and all(r["revoked"] is True and r["revoked_by"] == done["stamp"] for r in records)
        assert journal.stat().st_mode & 0o777 == 0o600 and journal.parent.stat().st_mode & 0o777 == 0o700
        assert journal.stat().st_nlink == 1
    assert unrelated.read_bytes() == unrelated_bytes  # another digest: not a proof of this entry, not rewritten
    assert [record for record in json.loads((ours / "journal.json").read_text())["records"]
            if record["client"] == "vscode"] == vscode_before
    assert done["clients"][0]["revoked_journals"] == 3
    set_entry(c.home, "cursor", entry)
    assert uninstall_plan(c, clients=("cursor",))["clients"][0]["action"] == "keep-unproven"


def test_a_failed_revocation_leaves_the_client_untouched_and_the_result_partial(c, monkeypatch):
    installed(c, clients=("cursor", "vscode"))
    plan = uninstall_plan(c, clients=("cursor", "vscode"))
    config = (c.home / PATHS["cursor"]).read_bytes()
    journals = {path: path.read_bytes() for path in (c.home / STATE / "quickstart").glob("*/journal.json")}
    real = qs._atomic_write

    def failing(path, text, **expectation):
        if Path(path).name == "journal.json" and any(
                record["client"] == "cursor" and record.get("revoked") for record in json.loads(text)["records"]):
            raise OSError("read-only file system")
        return real(path, text, **expectation)
    monkeypatch.setattr(qs, "_atomic_write", failing)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    statuses = {item["client"]: item["status"] for item in result["clients"]}
    assert statuses == {"cursor": "revocation-failed-entry-untouched", "vscode": "removed"}
    assert (c.home / PATHS["cursor"]).read_bytes() == config  # no write to the client file
    assert result["ok"] is False and result["partial"] is True and result["code"] == "uninstall_partial"
    assert result["service"]["outcome"] == "kept-clients-incomplete" and "bootout" not in c.system.verbs()
    monkeypatch.setattr(qs, "_atomic_write", real)
    cursor_records = [r for path in journals for r in json.loads(path.read_text())["records"] if r["client"] == "cursor"]
    assert cursor_records and not any(r.get("revoked") for r in cursor_records)  # the proof is intact
    again = uninstall_plan(c, clients=("cursor",))
    assert (again["clients"][0]["action"], again["clients"][0]["ownership"]) == ("remove", "journal")


def test_a_revocation_failing_on_the_second_journal_keeps_the_first_and_resumes(c, monkeypatch):
    """Verdict n° 5, amendment 2: two journals prove the entry; the write of the SECOND one fails.

    The first revocation is already durable: the proof is not 'intact'. What is guaranteed is the
    client file untouched, acquired revocations kept, a partial result, the service kept — and a
    later run that finishes the work from the journal that still proves the entry.
    """
    import shutil
    result = installed(c, clients=("cursor",))
    root = c.home / STATE / "quickstart"
    twin = root / ("20261101-101010-" + "b" * 32)
    shutil.copytree(root / result["stamp"], twin)
    for path in twin.iterdir():
        path.chmod(0o600)
    twin.chmod(0o700)
    plan = uninstall_plan(c, clients=("cursor",))
    assert (plan["clients"][0]["action"], plan["clients"][0]["ownership"]) == ("remove", "journal")
    config = (c.home / PATHS["cursor"]).read_bytes()
    real = qs._atomic_write
    revocations = []

    def failing(path, text, **expectation):
        if Path(path).name == "journal.json" and '"revoked": true' in text:
            revocations.append(str(path))
            if len(revocations) == 2:
                raise OSError("read-only file system")
        return real(path, text, **expectation)
    monkeypatch.setattr(qs, "_atomic_write", failing)
    partial = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    item, = partial["clients"]
    assert item["status"] == "revocation-failed-entry-untouched" and item["revoked_journals"] == 1
    assert (c.home / PATHS["cursor"]).read_bytes() == config  # client untouched
    assert partial["ok"] is False and partial["partial"] is True and partial["code"] == "uninstall_partial"
    assert partial["service"]["outcome"] == "kept-clients-incomplete"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
    states = [bool(record.get("revoked")) for record in journal_records(c, "cursor")]
    assert sorted(states) == [False, True]  # the acquired revocation is kept, the other journal still proves
    assert len(revocations) == 2 and revocations[0] != revocations[1]

    monkeypatch.setattr(qs, "_atomic_write", real)  # resumption, once the journal can be written again
    again = uninstall_plan(c, clients=("cursor",))
    assert (again["clients"][0]["action"], again["clients"][0]["ownership"]) == ("remove", "journal")
    done = uninstaller().apply(again, c.settings, home=c.home, run=c.system)
    assert done["ok"] is True and done["clients"][0]["status"] == "removed" and done["clients"][0]["revoked_journals"] == 1
    assert all(record["revoked"] is True for record in journal_records(c, "cursor"))
    assert "memory-os-ai" not in servers(c.home, "cursor")
    assert done["service"]["outcome"] == "stopped-and-plist-moved" and c.system.verbs().count("bootout") == 1


def test_undo_revokes_the_proof_too(c):
    result = installed(c, clients=("cursor", "vscode"))
    entry = servers(c.home, "cursor")["memory-os-ai"]
    qs.undo(result["stamp"], home=c.home, run=c.system)
    assert all(record["revoked"] is True for record in journal_records(c, "cursor"))
    set_entry(c.home, "cursor", entry)
    assert uninstall_plan(c, clients=("cursor",))["clients"][0]["action"] == "keep-unproven"


# ---- the service is kept for every entry that depends, or may depend, on it ----

DEFAULT_URL = "http://127.0.0.1:8765/mcp/"  # the relay's default; the fake machine never opens it
README_UVX = {"command": "uvx", "args": ["--python", "3.11", "--no-python-downloads", "--from",
                                         "memory-os-ai[ops,brief]==4.1.0", "memory-os-relay"]}
MODULE_RELAY = {"command": "/x/venv/bin/python", "args": ["-m", "memory_os_v4.ops.relay"]}


def with_url(entry, url):
    return {**entry, "env": {"MEMORY_V4_URL": url}}


# entry -> dependency on a service listening on DEFAULT_URL.
# ``none`` only where the entry ITSELF positively establishes another destination.
DEPENDENTS = {
    # wired: the entry names our service explicitly
    "console-script-explicit-same-url": (with_url({"command": "memory-os-relay"}, DEFAULT_URL), "wired"),
    "module-relay-explicit-same-url": (with_url(MODULE_RELAY, DEFAULT_URL), "wired"),
    "uvx-explicit-same-url": (with_url(README_UVX, DEFAULT_URL), "wired"),
    "direct-http-same-url": ({"type": "http", "url": DEFAULT_URL}, "wired"),
    "direct-http-no-trailing-slash": ({"serverUrl": "http://127.0.0.1:8765/mcp"}, "wired"),
    "direct-http-localhost-name": ({"httpUrl": "http://localhost:8765/mcp/"}, "wired"),
    # indeterminate: the effective environment of the client process is not established by its config
    "readme-uvx-no-environment": (README_UVX, "indeterminate"),
    "console-script-no-environment": ({"command": "memory-os-relay"}, "indeterminate"),
    "module-relay-no-environment": (MODULE_RELAY, "indeterminate"),
    "module-relay-token-file-only": ({**MODULE_RELAY, "env": {"MEMORY_V4_TOKEN_FILE": "/t"}}, "indeterminate"),
    "variable-in-url": (with_url({"command": "memory-os-relay"}, "${MEMORY_URL}"), "indeterminate"),
    "url-not-a-string": ({**MODULE_RELAY, "env": {"MEMORY_V4_URL": 8765}}, "indeterminate"),
    # indeterminate: the command decides by itself where it connects
    "shell-wrapper": ({"command": "/bin/sh", "args": ["-c", "exec python -m memory_os_v4.ops.relay"]}, "indeterminate"),
    "shell-wrapper-with-explicit-other-url": (with_url({"command": "/bin/sh", "args": [
        "-c", "MEMORY_V4_URL=http://127.0.0.1:8765/mcp/ exec python -m memory_os_v4.ops.relay"]},
        "http://127.0.0.1:9999/mcp/"), "indeterminate"),
    "foreign-program": ({"command": "/opt/proxy", "args": ["--upstream", "memory_os_v4.ops.relay"]}, "indeterminate"),
    "opaque-command": ({"command": "/opt/bin/mon-relais"}, "indeterminate"),
    "opaque-command-with-other-url": (with_url({"command": "/opt/bin/mon-relais"}, "http://127.0.0.1:9999/mcp/"),
                                      "indeterminate"),
    "class-other": (EXISTING["other"], "indeterminate"),
    "legacy-v3-inside-a-wrapper": ({"command": "/bin/sh", "args": ["-c", "python -m memory_os_ai.server"]}, "indeterminate"),
    "http-and-command-mixed": ({"command": "/opt/x", "url": "http://127.0.0.1:9999/mcp/"}, "indeterminate"),
    # none: another destination is positively established by the entry itself
    "console-script-explicit-other-port": (with_url({"command": "memory-os-relay"}, "http://127.0.0.1:9999/mcp/"), "none"),
    "module-relay-explicit-other-port": (with_url(MODULE_RELAY, "http://127.0.0.1:9999/mcp/"), "none"),
    "module-relay-explicit-other-host": (with_url(MODULE_RELAY, "http://10.0.0.5:8765/mcp/"), "none"),
    "direct-http-other-port": ({"type": "http", "url": "http://127.0.0.1:9999/mcp/"}, "none"),
    # Contract §23: on OUR port a NAME may resolve to this machine; only another port, or a canonical
    # IPv4 address of another machine, positively establishes another destination.
    "direct-http-other-host": ({"type": "http", "url": "https://example.com:8765/mcp/"}, "indeterminate"),
    "direct-http-other-host-other-port": ({"type": "http", "url": "https://example.com/mcp/"}, "none"),
    "direct-http-other-machine-address": ({"type": "http", "url": "http://10.0.0.5:8765/mcp/"}, "none"),
    "direct-http-alias-trailing-dot": ({"url": "http://localhost.:8765/mcp/"}, "indeterminate"),
    "direct-http-alias-any-address": ({"serverUrl": "http://0.0.0.0:8765/mcp/"}, "indeterminate"),
    "direct-http-alias-short-form": ({"url": "http://127.1:8765/mcp/"}, "indeterminate"),
    "direct-http-alias-mapped-ipv6": ({"url": "http://[::ffff:127.0.0.1]:8765/mcp/"}, "indeterminate"),
    "direct-http-alias-leading-zero": ({"url": "http://0177.0.0.1:8765/mcp/"}, "indeterminate"),
    "direct-http-ipv6-loopback": ({"url": "http://[::1]:8765/mcp/"}, "wired"),
    "direct-http-parser-differential": ({"url": "http://127.0.0.1:8765\\@example.com/mcp/"}, "indeterminate"),
    "direct-http-userinfo": ({"url": "http://user@example.com:9999/mcp/"}, "indeterminate"),
    "uvx-from-a-url-explicit-other-url": (with_url({"command": "uvx", "args": [
        "--from", "memory-os-ai @ https://example.com/fork.whl", "memory-os-relay"]}, "http://127.0.0.1:9999/mcp/"),
        "indeterminate"),
    "uvx-python-path-explicit-other-url": (with_url({"command": "uvx", "args": [
        "--python", "/any/python", "--from", "memory-os-ai", "memory-os-relay"]}, "http://127.0.0.1:9999/mcp/"),
        "indeterminate"),
    "legacy-v3-relative-interpreter": ({"command": "python3", "args": ["-m", "memory_os_ai.server"]}, "indeterminate"),
    "legacy-v3-with-pythonpath": ({"command": "/old/python", "args": ["-m", "memory_os_ai.server"],
                                   "env": {"PYTHONPATH": "/x"}}, "indeterminate"),
    "legacy-v3-exact-module": (EXISTING["legacy-v3"], "none"),
    # verdict n° 5, amendment 1: uvx with an explicit foreign URL, and contradictory URL fields
    "uvx-explicit-other-port": (with_url(README_UVX, "http://127.0.0.1:9999/mcp/"), "none"),
    "uvx-foreign-package-explicit-other-url": (with_url({"command": "uvx", "args": [
        "--from", "some-other-package", "memory-os-relay"]}, "http://127.0.0.1:9999/mcp/"), "indeterminate"),
    "uvx-env-file-explicit-other-url": (with_url({"command": "uvx", "args": [
        "--env-file", "/x/.env", "--from", "memory-os-ai[ops,brief]==4.1.0", "memory-os-relay"]},
        "http://127.0.0.1:9999/mcp/"), "indeterminate"),
    "direct-http-contradictory-urls": ({"url": "http://127.0.0.1:9999/mcp/", "serverUrl": DEFAULT_URL}, "indeterminate"),
    "direct-http-two-foreign-urls-that-differ": ({"url": "http://127.0.0.1:9999/mcp/",
                                                  "httpUrl": "https://example.com/mcp"}, "indeterminate"),
    "direct-http-agreeing-urls": ({"url": DEFAULT_URL, "serverUrl": "http://localhost:8765/mcp"}, "wired"),
    "direct-http-agreeing-foreign-urls": ({"url": "http://127.0.0.1:9999/mcp/",
                                           "httpUrl": "http://127.0.0.1:9999/mcp"}, "none"),
}
# The entry's own ``env`` is known to override the inherited environment only in these clients
# (sources read by the reviewer: VS Code, Codex, Gemini CLI). Antigravity is not established: its
# sources were not read, so an explicit foreign URL there stays ``indeterminate``.
ENV_PRECEDENCE_ESTABLISHED = ("vscode", "codex", "gemini-cli")
ENV_PRECEDENCE_NOT_ESTABLISHED = ("claude-code", "claude-desktop", "cursor", "antigravity", None)
OTHER_URL = "http://127.0.0.1:9999/mcp/"
EXPLICIT_FOREIGN_RELAYS = {
    "module-relay": with_url(MODULE_RELAY, OTHER_URL),
    "console-script": with_url({"command": "memory-os-relay"}, OTHER_URL),
    "uvx": with_url(README_UVX, OTHER_URL),
}


def default_url_install(c, clients=("cursor",)):
    from dataclasses import replace
    c.settings = replace(c.settings, url=DEFAULT_URL)
    assert str(c.settings.token_file) == str(c.home / ".config/memory-os/http.token")  # the relay default
    return installed(c, clients=clients)


@pytest.mark.parametrize("case", sorted(DEPENDENTS))
def test_service_dependency_is_none_only_when_another_destination_is_established(c, case):
    from dataclasses import replace
    entry, expected = DEPENDENTS[case]
    settings = replace(c.settings, url=DEFAULT_URL)
    # ``vscode``: the client the uninstall test below plants the entry in (env precedence established).
    assert uninstaller().service_dependency(entry, settings, home=c.home, client="vscode") == expected


def test_the_clients_where_the_entry_environment_is_known_to_win_are_an_explicit_list():
    assert uninstaller().ENV_PRECEDENCE_ESTABLISHED == frozenset(ENV_PRECEDENCE_ESTABLISHED)


@pytest.mark.parametrize("client", [*ENV_PRECEDENCE_ESTABLISHED, *ENV_PRECEDENCE_NOT_ESTABLISHED])
@pytest.mark.parametrize("form", sorted(EXPLICIT_FOREIGN_RELAYS))
def test_explicit_foreign_url_frees_the_service_only_where_env_precedence_is_established(c, client, form):
    """Verdict n° 5: 'the entry env overrides the inherited one' is proven for some clients only."""
    from dataclasses import replace
    settings = replace(c.settings, url=DEFAULT_URL)
    entry = EXPLICIT_FOREIGN_RELAYS[form]
    options = {} if client is None else {"client": client}
    expected = "none" if client in ENV_PRECEDENCE_ESTABLISHED else "indeterminate"
    assert uninstaller().service_dependency(entry, settings, home=c.home, **options) == expected
    # Naming OUR service explicitly is ``wired`` everywhere: the safe direction needs no proof.
    ours = with_url({k: v for k, v in entry.items() if k != "env"}, DEFAULT_URL)
    assert uninstaller().service_dependency(ours, settings, home=c.home, **options) == "wired"


@pytest.mark.parametrize("client,expected", [("vscode", "none"), ("codex", "none"),
                                             ("cursor", "indeterminate"), ("claude-desktop", "indeterminate")])
def test_explicit_url_against_a_contradictory_inherited_environment(c, monkeypatch, client, expected):
    """The tool's own environment names our service while the entry names another one, and conversely."""
    monkeypatch.setenv("MEMORY_V4_URL", c.settings.url)
    assert uninstaller().service_dependency(with_url(MODULE_RELAY, OTHER_URL), c.settings, home=c.home,
                                            client=client) == expected
    monkeypatch.setenv("MEMORY_V4_URL", OTHER_URL)
    assert uninstaller().service_dependency(with_url(MODULE_RELAY, c.settings.url), c.settings, home=c.home,
                                            client=client) == "wired"


@pytest.mark.parametrize("key", ["claude-desktop", "claude-code", "antigravity", "vscode"])
def test_uninstall_keeps_the_service_for_an_explicit_foreign_url_in_a_client_not_established(c, key):
    """Same entry, three clients: the service stops only where the entry env is known to win."""
    installed(c, clients=("cursor",))
    entry = with_url(MODULE_RELAY, OTHER_URL)
    set_entry(c.home, key, entry)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    established = key in ENV_PRECEDENCE_ESTABLISHED
    assert by[key]["action"] == "keep-foreign" and by[key]["dependency"] == ("none" if established else "indeterminate")
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, key)["memory-os-ai"] == entry
    if established:
        assert result["service"]["outcome"] == "stopped-and-plist-moved" and result["service"]["remaining_clients"] == []
    else:
        assert result["service"]["outcome"] == "kept-clients-remaining"
        assert result["service"]["remaining_dependencies"] == {key: "indeterminate"}
        assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True


@pytest.mark.parametrize("case", sorted(DEPENDENTS))
def test_uninstall_keeps_the_service_for_an_entry_that_depends_or_may_depend_on_it(c, case):
    entry, dependency = DEPENDENTS[case]
    default_url_install(c)
    set_entry(c.home, "vscode", entry)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert by["cursor"]["action"] == "remove" and by["vscode"]["action"].startswith("keep-")
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, "vscode")["memory-os-ai"] == entry and "memory-os-ai" not in servers(c.home, "cursor")
    if dependency == "none":
        assert result["service"]["outcome"] == "stopped-and-plist-moved" and result["service"]["remaining_clients"] == []
        assert c.system.verbs().count("bootout") == 1
    else:
        assert result["service"]["outcome"] == "kept-clients-remaining"
        assert result["service"]["remaining_clients"] == ["vscode"]
        assert result["service"]["remaining_dependencies"] == {"vscode": dependency}
        assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
        assert any("vscode" in step for step in result["next_steps"])


@pytest.mark.parametrize("entry", [README_UVX, {"command": "memory-os-relay"}, MODULE_RELAY])
@pytest.mark.parametrize("tool_env", ["unset", "our-url", "another-url"])
def test_inherited_environment_is_never_assumed_for_a_relay_without_an_explicit_url(c, monkeypatch, entry, tool_env):
    """Service on port 43219, relay default 8765: the client may have been launched with
    ``MEMORY_V4_URL`` pointing here and pass it on without repeating it in its config. Neither the
    default nor the environment of the uninstall command establishes what that process sees."""
    if tool_env == "unset":
        monkeypatch.delenv("MEMORY_V4_URL", raising=False)
    else:
        monkeypatch.setenv("MEMORY_V4_URL", c.settings.url if tool_env == "our-url" else "http://127.0.0.1:9999/mcp/")
    assert c.settings.url != DEFAULT_URL
    assert uninstaller().service_dependency(entry, c.settings, home=c.home) == "indeterminate"


def test_uninstall_keeps_the_service_for_a_relay_that_may_inherit_its_destination(c, monkeypatch):
    monkeypatch.setenv("MEMORY_V4_URL", "http://127.0.0.1:9999/mcp/")  # the tool's own environment proves nothing
    installed(c, clients=("cursor",))
    set_entry(c.home, "antigravity", README_UVX)
    result = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining"
    assert result["service"]["remaining_dependencies"] == {"antigravity": "indeterminate"}
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
    assert servers(c.home, "antigravity")["memory-os-ai"] == README_UVX and "memory-os-ai" not in servers(c.home, "cursor")


def test_uninstall_keeps_the_service_for_an_opaque_command(c):
    """``/opt/bin/mon-relais`` shows no marker; nothing proves it does not call our relay."""
    installed(c, clients=("cursor",))
    opaque = {"command": "/opt/bin/mon-relais"}
    set_entry(c.home, "antigravity", opaque)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert (by["antigravity"]["action"], by["antigravity"]["dependency"]) == ("keep-foreign", "indeterminate")
    assert plan["service"]["action"] == "keep-clients-remaining"
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()
    assert any("antigravity" in step for step in result["next_steps"])
    assert servers(c.home, "antigravity")["memory-os-ai"] == opaque


def test_strict_ownership_still_excludes_the_console_script_that_counts_as_a_dependency(c):
    default_url_install(c)
    set_entry(c.home, "antigravity", README_UVX)
    by = {item["client"]: item for item in uninstall_plan(c)["clients"]}
    assert qs.relay_form(README_UVX) is False
    assert (by["antigravity"]["action"], by["antigravity"]["ownership"]) == ("keep-unproven", "unproven")
    assert by["antigravity"]["dependency"] == "indeterminate"
    explicit = with_url(README_UVX, DEFAULT_URL)
    set_entry(c.home, "antigravity", explicit)
    by = {item["client"]: item for item in uninstall_plan(c)["clients"]}
    assert qs.relay_form(explicit) is False and by["antigravity"]["dependency"] == "wired"


def test_an_entry_exactly_equal_to_ours_is_unchanged_whatever_its_history(c):
    """Revoked proof or not: an entry already equal to the one we would write needs no write and no consent."""
    installed(c, clients=("cursor", "vscode"))
    entry = servers(c.home, "cursor")["memory-os-ai"]
    uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    set_entry(c.home, "cursor", entry)
    plan = prepare(c, profile="lexical", clients=("cursor",))
    assert plan["ok"] is True and plan["clients"][0]["action"] == "unchanged"


def test_uninstall_has_no_option_to_force_the_stop_of_a_shared_service(c):
    from memory_os_v4.ops import cli
    for flag in ("--stop-service", "--force-stop", "--service-only"):
        with pytest.raises(SystemExit) as stop:
            cli.main(["uninstall", "--clients", "cursor", flag])
        assert stop.value.code == 2
    assert not {"stop_service", "force_stop"} & set(inspect.signature(uninstaller().apply).parameters)


def test_uninstall_reads_the_settings_of_the_installed_service_from_its_definition(c):
    """A fresh shell does not carry the variables the install chose; the plist does (paths only)."""
    from memory_os_v4.ops.settings import OpsSettings
    installed(c, clients=("cursor",))
    blank = OpsSettings("http://127.0.0.1:8765/mcp/", c.home / "x.token", c.home / "x-store",
                        c.settings.backup_dir, c.settings.backup_key, c.home / "x-ops", "test.memory-os")
    found = uninstaller().settings_from_service(blank, c.home)
    assert (found.url, found.token_file, found.store_dir, found.ops_dir) == (
        c.settings.url, c.settings.token_file, c.settings.store_dir, c.settings.ops_dir)
    plan = uninstaller().plan(found, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["state"] == "owned"
    assert {item["client"]: item["action"] for item in plan["clients"]}["cursor"] == "remove"
    (c.home / PLIST).unlink()
    assert uninstaller().settings_from_service(blank, c.home) == blank  # nothing installed: nothing assumed


def test_a_selective_uninstall_does_not_need_to_attest_a_job_it_will_not_stop(c):
    """Contract §23: the unattested job refuses the plan only when the plan would stop it."""
    installed(c, clients=("cursor", "vscode"))
    c.system.job_replaced = True
    kept = uninstall_plan(c, clients=("cursor",))
    assert kept["ok"] is True and kept["service"]["action"] == "keep-clients-remaining"
    assert kept["service"]["loaded_state"] == "foreign-or-unattested"
    full = uninstall_plan(c)
    assert full["ok"] is False and full["blocker_codes"] == ["uninstall_service_foreign"]
    result = uninstaller().apply(kept, c.settings, home=c.home, run=c.system)
    assert result["ok"] is True and result["service"]["outcome"] == "kept-clients-remaining"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


def test_a_client_config_behind_a_symbolic_link_is_kept_and_keeps_the_service(c):
    """Never written through a link; and since its entry cannot be vouched for, the service stays."""
    installed(c, clients=("cursor", "vscode"))
    path = c.home / PATHS["vscode"]
    real = path.with_name("mcp.real.json")
    path.rename(real)
    path.symlink_to(real)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert plan["ok"] is True and by["vscode"]["action"] == "keep-unsafe-path" and by["cursor"]["action"] == "remove"
    assert plan["service"]["remaining_dependencies"] == {"vscode": "indeterminate"}
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()
    assert "memory-os-ai" in json.loads(real.read_text())["servers"] and path.is_symlink()


def test_the_pending_decision_on_the_default_profile_is_one_constant(c, monkeypatch):
    """R1 is provisional: no default today; a default would be this constant, never a guess from packages."""
    assert qs.NEW_INSTALL_DEFAULT_PROFILE is None
    monkeypatch.setattr(qs, "NEW_INSTALL_DEFAULT_PROFILE", "lexical")
    plan = qs.prepare(c.settings, home=c.home, python=c.python, clients=("cursor",), run=c.system)
    assert plan["ok"] is True and plan["profile"] == "lexical"


def test_a_project_scope_entry_of_claude_code_keeps_the_service(c):
    """``~/.claude.json`` also holds per-project servers: never written by us, counted as dependencies."""
    installed(c, clients=("cursor",))
    path = c.home / PATHS["claude-code"]
    data = json.loads(path.read_text())
    data["projects"] = {"/work/repo": {"mcpServers": {"memory-os-ai": {"type": "http", "url": c.settings.url}}}}
    path.write_text(json.dumps(data, indent=2) + "\n")
    plan = uninstall_plan(c)
    assert plan["service"]["action"] == "keep-clients-remaining"
    assert plan["service"]["remaining_dependencies"] == {"claude-code": "wired"}
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()
    assert json.loads(path.read_text())["projects"] == data["projects"]  # never touched


def test_uninstall_says_what_it_cannot_inspect_before_it_stops_the_service(c):
    installed(c, clients=("cursor",))
    plan = uninstall_plan(c)
    assert plan["service"]["action"] == "stop" and any(".mcp.json" in step for step in plan["next_steps"])


def test_removal_refuses_a_json_file_with_duplicate_keys_and_keeps_the_proof(c):
    """Contract §5.3 on the way out too: rewriting would silently drop one of the duplicates."""
    installed(c, clients=("cursor", "vscode"))
    path = c.home / PATHS["cursor"]
    text = path.read_text().rstrip()[:-1].rstrip() + ',\n\t"theme": "dark",\n\t"theme": "light"\n}\n'
    path.write_text(text)
    result = uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    assert result["clients"][0]["status"] == "refused-removal-manual" and path.read_text() == text
    assert result["ok"] is False and result["partial"] is True and result["service"]["outcome"] == "kept-clients-incomplete"
    assert not any(record.get("revoked") for record in journal_records(c, "cursor"))


def test_toml_removal_is_parsed_before_it_is_written(c):
    """A hand-written Codex entry the line-based removal would mangle: refused, file and proof untouched."""
    installed(c, clients=("cursor",))
    path = c.home / PATHS["codex"]
    text = path.read_text() + ('\n[mcp_servers.memory-os-ai]\ncommand = "/old/venv/bin/python"\n'
                               'args = [\n"-m",\n"memory_os_v4.ops.relay",\n]\n'
                               '[mcp_servers.memory-os-ai.tools]\nallowed = ["x"]\n')
    path.write_text(text)
    plan = uninstall_plan(c, clients=("codex",), remove_existing=("codex",))
    assert plan["clients"][0]["action"] == "remove"
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["clients"][0]["status"] == "refused-removal-manual" and path.read_text() == text


def test_a_foreign_toml_entry_holding_a_date_does_not_break_the_previews(c):
    from setup_fixture_410 import write
    installed(c, clients=("cursor",))
    write(c.home, PATHS["codex"], '[mcp_servers.memory-os-ai]\ncommand = "x"\nsince = 2024-01-01\n')
    by = {item["client"]: item for item in uninstall_plan(c)["clients"]}
    assert by["codex"]["action"] == "keep-foreign"
    item, = qs.preview(home=c.home, python=c.python, env={"MEMORY_V4_URL": c.settings.url}, clients=("codex",))["clients"]
    assert item["action"] == "refuse-toml-existing-manual"


def test_an_unreadable_client_file_is_a_refusal_not_a_crash(c):
    installed(c, clients=("cursor", "vscode"))
    path = c.home / PATHS["vscode"]
    path.chmod(0o000)
    try:
        by = {item["client"]: item for item in uninstall_plan(c)["clients"]}
        assert by["vscode"]["action"] == "keep-unreadable" and by["vscode"]["dependency"] == "indeterminate"
        item, = qs.preview(home=c.home, python=c.python, env={"MEMORY_V4_URL": c.settings.url}, clients=("vscode",))["clients"]
        assert item["action"] == "refuse-unreadable"
    finally:
        path.chmod(0o600)


# ---- Codex implementation review n° 1 ----

FOREIGN_NOW = {"command": "/someone/else/bin/tool", "args": ["serve"], "env": {"WRITTEN": "during-the-revocation"}}


def rewrite_during_revocation(c, monkeypatch, key="cursor"):
    """The client entry is replaced by someone else right after OUR revocation of its proof is written."""
    real = qs._atomic_write
    seen = []

    def hooked(path, text, **expectation):
        real(path, text, **expectation)
        if Path(path).name == "journal.json" and '"revoked": true' in text and not seen:
            seen.append(str(path))
            set_entry(c.home, key, FOREIGN_NOW)
    monkeypatch.setattr(qs, "_atomic_write", hooked)
    return seen


def test_uninstall_never_removes_an_entry_that_changed_during_its_own_revocation(c, monkeypatch):
    """Compare-then-write at removal time: the digest is re-checked on the bytes the write is built from."""
    installed(c, clients=("cursor",))
    plan = uninstall_plan(c)
    seen = rewrite_during_revocation(c, monkeypatch)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert seen, "the modification must happen during the revocation of this very client"
    assert servers(c.home, "cursor")["memory-os-ai"] == FOREIGN_NOW  # neither removed nor overwritten
    item, = [entry for entry in result["clients"] if entry["client"] == "cursor"]
    assert item["status"] == "conflict-file-changed-since-preview" and item["revoked_journals"] == 1
    assert result["ok"] is False and result["partial"] is True and result["code"] == "uninstall_partial"
    assert result["service"]["outcome"] == "kept-clients-incomplete"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True


def test_undo_never_removes_an_entry_that_changed_during_its_own_revocation(c, monkeypatch):
    result = installed(c, clients=("cursor",))
    seen = rewrite_during_revocation(c, monkeypatch)
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert seen and servers(c.home, "cursor")["memory-os-ai"] == FOREIGN_NOW
    assert out["results"] == [{"client": "cursor", "undo": "refused-entry-changed-since-apply"}]
    assert out["ok"] is False and out["service"] == {"undo": "kept-clients-incomplete"}
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


def test_claude_code_entry_changed_during_its_revocation_is_not_removed_through_the_cli(c, monkeypatch):
    installed(c, clients=("claude-code",))
    plan = uninstall_plan(c)
    seen = rewrite_during_revocation(c, monkeypatch, key="claude-code")
    calls = len(c.system.calls)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert seen and servers(c.home, "claude-code")["memory-os-ai"] == FOREIGN_NOW
    assert not [argv for argv in c.system.calls[calls:] if argv[0] == "claude"]
    assert result["partial"] is True and result["service"]["outcome"] == "kept-clients-incomplete"


@pytest.mark.parametrize("key", ["cursor", "codex"])
def test_uninstall_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared(c, monkeypatch, key):
    """Codex implementation review n° 3: prepare the temporary file first, verify the target last."""
    installed(c, clients=(key,))
    plan = uninstall_plan(c)
    real = qs.tempfile.mkstemp
    path = c.home / PATHS[key]
    seen = []

    def mkstemp(*args, **kwargs):
        made = real(*args, **kwargs)
        if kwargs.get("prefix") == f".{path.name}." and not seen:
            seen.append(1)
            path.write_text(path.read_text().replace(c.python, "/someone/else/bin/tool"))
        return made
    monkeypatch.setattr(qs.tempfile, "mkstemp", mkstemp)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    item, = [entry for entry in result["clients"] if entry["client"] == key]
    assert seen and item["status"] == "conflict-file-changed-since-preview"
    assert servers(c.home, key)["memory-os-ai"]["command"] == "/someone/else/bin/tool"
    assert result["partial"] is True and result["service"]["outcome"] == "kept-clients-incomplete"
    assert "bootout" not in c.system.verbs()
    assert not [p.name for p in path.parent.iterdir() if p.name.startswith("." + path.name + ".")]


def test_a_service_announced_as_kept_is_not_stopped_when_the_other_client_vanishes_after_the_preview(c):
    """The fate of the service and the dependencies outside the selection are part of the revalidated plan."""
    installed(c, clients=("cursor", "vscode"))
    plan = uninstall_plan(c, clients=("cursor",))
    assert plan["service"]["action"] == "keep-clients-remaining" and plan["service"]["remaining_clients"] == ["vscode"]
    path = c.home / PATHS["vscode"]
    data = json.loads(path.read_text())
    del data["servers"]["memory-os-ai"]  # VS Code is not in the selection: its file is not compared by digest
    path.write_text(json.dumps(data, indent=2) + "\n")
    before = tree(c.root)
    with pytest.raises(uninstaller().UninstallError) as refusal:
        uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "uninstall_changed_since_preview"
    assert tree(c.root) == before and "bootout" not in c.system.verbs() and c.system.loaded is True


def test_a_kept_service_stays_kept_even_if_its_last_dependency_vanishes_during_the_apply(c, monkeypatch):
    installed(c, clients=("cursor", "vscode"))
    plan = uninstall_plan(c, clients=("cursor",))
    real = qs._atomic_write

    def hooked(path, text, **expectation):
        real(path, text, **expectation)
        if str(path).endswith(PATHS["cursor"]):  # while the apply runs, after its revalidation
            vscode = c.home / PATHS["vscode"]
            data = json.loads(vscode.read_text())
            data["servers"].pop("memory-os-ai", None)
            vscode.write_text(json.dumps(data, indent=2) + "\n")
    monkeypatch.setattr(qs, "_atomic_write", hooked)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["ok"] is True and result["service"]["outcome"] == "kept-as-planned"
    assert result["service"]["remaining_clients"] == [] and any("preview again" in step for step in result["next_steps"])
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True


@pytest.mark.parametrize("dangling", [False, True])
def test_a_config_behind_a_symbolic_link_in_a_parent_directory_keeps_the_service(c, dangling):
    """Same path rule for the plan and for the dependency scan: a linked ancestor is never vouched for."""
    installed(c, clients=("cursor",))
    directory = c.home / ".gemini"  # Antigravity and Gemini CLI both live below it
    real = directory.with_name(".gemini.real")
    directory.rename(real)
    directory.symlink_to(c.home / "nowhere" if dangling else real)
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert by["antigravity"]["action"] == "keep-unsafe-path" and by["gemini-cli"]["action"] == "keep-unsafe-path"
    assert plan["service"]["remaining_dependencies"] == {"antigravity": "indeterminate", "gemini-cli": "indeterminate"}
    assert uninstaller().remaining_dependencies(c.home, c.settings.port, removed={"cursor"}) == plan["service"]["remaining_dependencies"]
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()


def test_a_replace_consumes_the_proof_of_the_entry_it_replaces(c):
    """Install E1, replace it by E2, uninstall E2, re-create E1 by hand: no journal may still vouch for E1."""
    installed(c, clients=("cursor", "vscode"))
    first = servers(c.home, "cursor")["memory-os-ai"]  # E1
    other = c.home / "runtime2/bin/python"
    other.parent.mkdir(parents=True)
    other.write_text("# synthetic executable, never executed")
    other.chmod(0o700)
    plan = qs.preview(home=c.home, python=str(other), clients=("cursor",),
                      env={"MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file),
                           "MEMORY_V4_OPS_DIR": str(c.settings.ops_dir)})
    assert (plan["clients"][0]["action"], plan["clients"][0]["ownership"]) == ("replace", "journal")
    replaced = qs.apply_clients(plan, home=c.home, python=str(other), env={
        "MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file),
        "MEMORY_V4_OPS_DIR": str(c.settings.ops_dir)}, run=c.system)
    assert replaced["records"][0]["status"] == "applied" and replaced["records"][0]["revoked_journals"] == 1
    second = servers(c.home, "cursor")["memory-os-ai"]  # E2
    assert second != first and uninstall_plan(c, clients=("cursor",))["clients"][0]["ownership"] == "journal"
    done = uninstaller().apply(uninstall_plan(c, clients=("cursor",)), c.settings, home=c.home, run=c.system)
    assert done["clients"][0]["status"] == "removed"
    set_entry(c.home, "cursor", first)  # E1 again, by hand
    item, = uninstall_plan(c, clients=("cursor",))["clients"]
    assert (item["action"], item["ownership"]) == ("keep-unproven", "unproven")
    again = qs.preview(home=c.home, python=str(other), clients=("cursor",),
                       env={"MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file),
                            "MEMORY_V4_OPS_DIR": str(c.settings.ops_dir)})
    assert again["clients"][0]["action"] == "refuse-existing-relay-unproven"
    assert all(record.get("revoked") is True for record in journal_records(c, "cursor"))


def test_a_toml_file_with_both_server_tables_is_ambiguous_never_a_proof_of_no_dependency(c):
    """``[mcp.servers.memory-os-ai]`` next to ``[mcp_servers.*]``: which table Codex reads is not ours to guess."""
    from setup_fixture_410 import write
    installed(c, clients=("cursor",))
    write(c.home, PATHS["codex"], '[mcp_servers.other]\ncommand = "x"\n\n[mcp.servers.memory-os-ai]\n'
          'command = "memory-os-relay"\n[mcp.servers.memory-os-ai.env]\n'
          f'MEMORY_V4_URL = "{c.settings.url}"\n')
    plan = uninstall_plan(c)
    by = {item["client"]: item for item in plan["clients"]}
    assert (by["codex"]["action"], by["codex"]["dependency"]) == ("keep-ambiguous", "indeterminate")
    assert plan["service"]["remaining_dependencies"] == {"codex": "indeterminate"}
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert result["service"]["outcome"] == "kept-clients-remaining" and "bootout" not in c.system.verbs()
    assert any("codex" in step for step in result["next_steps"])


def test_uninstall_is_idempotent(c):
    installed(c)
    uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    before = tree(c.home)
    plan = uninstall_plan(c)
    assert plan["ok"] is True and plan["nothing_to_do"] is True and plan["service"]["state"] == "absent"
    assert {item["action"] for item in plan["clients"]} <= {"absent", "missing-config"}
    assert tree(c.home) == before


def test_uninstall_refuses_a_service_that_is_not_ours(c):
    installed(c)
    path = c.home / PLIST
    path.write_bytes(plistlib.dumps({"Label": "test.memory-os", "ProgramArguments": ["/foreign", "serve"]}))
    plan = uninstall_plan(c)
    assert plan["ok"] is False and plan["blocker_codes"] == ["uninstall_service_foreign"]
    before = tree(c.root)
    with pytest.raises(uninstaller().UninstallError) as refusal:
        uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "preflight_refused"
    assert tree(c.root) == before and "bootout" not in c.system.verbs()


def test_uninstall_refuses_while_claude_desktop_runs(c):
    installed(c)
    c.system.desktop_running = True
    plan = uninstall_plan(c)
    assert plan["ok"] is False and "client_app_running" in plan["blocker_codes"]


def test_uninstall_refuses_globally_when_anything_changed_since_its_preview(c):
    """Conflict seen before any mutation: nothing is removed, the service keeps running."""
    installed(c)
    plan = uninstall_plan(c)
    path = c.home / PATHS["cursor"]
    path.write_text(path.read_text() + "\n")
    before = tree(c.root)
    with pytest.raises(uninstaller().UninstallError) as refusal:
        uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "uninstall_changed_since_preview"
    assert tree(c.root) == before
    assert "bootout" not in c.system.verbs() and c.system.verbs("claude").count("mcp") == 1  # the install only


def test_uninstall_never_claims_to_be_complete_and_names_what_it_cannot_see(c):
    installed(c)
    plan = uninstall_plan(c)
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    for document in (plan, result):
        manual = {item["kind"]: item for item in document["manual_removal_required"]}
        assert {"claude-desktop-extension", "claude-code-hooks", "auxiliary-launchd-jobs", "python-environment"} <= set(manual)
        extension = manual["claude-desktop-extension"]
        assert extension["inspected"] is False and extension["present"] is None  # unknown, never asserted
        assert extension["procedure"].startswith("If present:") and "Extensions" in extension["procedure"]
        assert "check" in extension["procedure"].lower() and "is installed" not in extension["procedure"].lower()
        assert document["complete"] is False
    assert "complete" not in json.dumps(result["scope"]).lower()


def test_no_bundle_is_shipped_by_this_phase():
    """Green today: no ``mcpb`` command and no bundle builder until the architecture is decided (R7)."""
    import importlib.util
    from memory_os_v4.ops import cli
    with pytest.raises(SystemExit) as stop:
        cli.main(["mcpb", "--output", "x.mcpb"])
    assert stop.value.code == 2
    assert importlib.util.find_spec("memory_os_v4.ops.mcpb") is None


def test_uninstall_has_no_way_to_delete_data(c):
    from memory_os_v4.ops import cli
    module = uninstaller()
    for function in (module.plan, module.apply):
        assert not {"purge", "delete_data", "remove_store", "force"} & set(inspect.signature(function).parameters)
    for flag in ("--purge", "--delete-data", "--force"):
        with pytest.raises(SystemExit) as stop:
            cli.main(["uninstall", flag])
        assert stop.value.code == 2
    source = Path(module.__file__).read_text()
    assert "rmtree" not in source and "store_dir.unlink" not in source


def test_uninstall_cli_previews_by_default_and_applies_only_on_request(c, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    module = uninstaller()
    seen = []
    monkeypatch.setattr(module, "plan", lambda settings, **kw: seen.append("plan") or {
        "ok": True, "writes": "none (preview)", "clients": [], "blocker_codes": []})
    monkeypatch.setattr(module, "apply", lambda plan, settings, **kw: seen.append("apply") or {
        "ok": True, "data_deleted": False})
    assert cli.main(["uninstall"]) == 0 and seen == ["plan"]
    assert json.loads(capsys.readouterr().out)["writes"] == "none (preview)"
    assert cli.main(["uninstall", "--apply"]) == 0 and seen == ["plan", "plan", "apply"]
    assert json.loads(capsys.readouterr().out)["data_deleted"] is False


# ---- after an uninstall: the kept store is never adopted silently ----

def test_reinstall_on_a_kept_store_is_refused_and_no_reattach_option_exists(c):
    """Reattaching a kept store is the upgrade runbook's job, out of this phase (R4)."""
    from memory_os_v4.ops import cli
    installed(c, clients=("cursor",))
    uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    data = data_inventory(c)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "store_not_empty" in plan["blocker_codes"]
    assert any("runbook" in blocker for blocker in plan["blockers"])
    assert data_inventory(c) == data
    assert "reuse_store" not in inspect.signature(qs.prepare).parameters
    with pytest.raises(SystemExit) as stop:
        cli.main(["quickstart", "--profile", "lexical", "--reuse-store"])
    assert stop.value.code == 2
