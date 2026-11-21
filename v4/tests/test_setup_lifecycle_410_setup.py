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


def test_running_claude_desktop_points_to_the_mcpb_route(c):
    c.system.desktop_running = True
    plan = prepare(c, profile="lexical", clients=("claude-desktop",))
    assert any(".mcpb" in blocker for blocker in plan["blockers"])


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
        assert token not in backup.read_text(errors="replace")  # verbatim copies predate the token


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
    set_entry(c.home, "antigravity", other_install)
    plan = uninstall_plan(c)
    actions = {item["client"]: item["action"] for item in plan["clients"]}
    assert actions["cursor"] == "keep-foreign" and actions["antigravity"] == "keep-foreign"
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert servers(c.home, "cursor")["memory-os-ai"] == EXISTING["legacy-v3"]
    assert servers(c.home, "antigravity")["memory-os-ai"] == other_install
    statuses = {item["client"]: item["status"] for item in result["clients"]}
    assert statuses["cursor"] == statuses["antigravity"] == "kept-foreign"


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


def test_uninstall_skips_a_config_changed_since_its_preview(c):
    installed(c)
    plan = uninstall_plan(c)
    path = c.home / PATHS["cursor"]
    path.write_text(path.read_text() + "\n")
    changed = path.read_bytes()
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    statuses = {item["client"]: item["status"] for item in result["clients"]}
    assert statuses["cursor"] == "conflict-file-changed-since-preview" and path.read_bytes() == changed
    assert statuses["vscode"] == "removed" and result["ok"] is False


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

def test_reinstall_on_a_kept_store_is_refused_without_the_explicit_flag(c):
    installed(c, clients=("cursor",))
    uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    data = data_inventory(c)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "store_not_empty" in plan["blocker_codes"]
    assert any("--reuse-store" in blocker for blocker in plan["blockers"])
    assert data_inventory(c) == data


def test_reuse_of_a_store_no_uninstall_journal_attests_is_refused(c):
    plant_data(c)
    data = data_inventory(c)
    plan = prepare(c, profile="lexical", reuse_store=True)
    assert plan["ok"] is False and "store_reuse_unattested" in plan["blocker_codes"]
    assert data_inventory(c) == data
