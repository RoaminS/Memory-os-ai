"""SETUP_4_1_0.md §6 and §7: stopping the service can fail, and memories must stay recoverable.

A failed, timed-out or unconfirmed stop never moves the plist; a job replaced after the preview
is never stopped; an interrupted install can be rolled back. The data proofs use a REAL
temporary SQLite store opened by the engine (lexical profile, no ML): transactions committed
after the install are searched back after the rollback or the uninstall.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memory_os_v4.ops import quickstart as qs
from setup_fixture_410 import (
    PATHS, SIX, contents, install, prepare, read_memories, servers, set_entry, setup_context, tree, write_memories,
)

PLIST = "Library/LaunchAgents/test.memory-os.plist"
STATE = ".local/state/memory-os"
STOP_FAILURES = {"fail": "service_stop_failed", "timeout": "service_stop_timeout", "lingers": "service_stop_unconfirmed"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


def uninstaller():
    from memory_os_v4.ops import uninstall
    return uninstall


def claude_on_path(name):
    return "/usr/local/bin/" + name


def uninstall_plan(c):
    return uninstaller().plan(c.settings, home=c.home, python=c.python, run=c.system)


def no_sleep(seconds):
    return None


# ---- rollback: the stop must be confirmed before the plist moves ----

@pytest.mark.parametrize("mode", sorted(STOP_FAILURES))
def test_rollback_keeps_the_plist_when_the_stop_is_not_confirmed(c, mode):
    result = install(c, profile="lexical")
    plist = (c.home / PLIST).read_bytes()
    c.system.bootout_mode = mode
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=no_sleep)
    assert out["ok"] is False and out["code"] == STOP_FAILURES[mode]
    assert out["service"]["undo"] == STOP_FAILURES[mode].replace("service_", "").replace("_", "-")
    assert "plist_moved_to" not in out["service"]
    assert (c.home / PLIST).read_bytes() == plist  # never moved after a stop that is not proven
    assert not list((c.home / STATE).rglob("*.plist"))
    assert c.system.verbs().count("bootout") == 1  # no retry loop, no forced kill


@pytest.mark.parametrize("mode", sorted(STOP_FAILURES))
def test_rollback_whose_stop_failed_is_finished_by_a_second_run(c, mode):
    """Contract §23: the clients are already restored; the second run only has the service left to stop."""
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    c.system.bootout_mode = mode
    first = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=no_sleep)
    assert first["ok"] is False and {item["undo"] for item in first["results"]} == {"restored"}
    files = {key: (c.home / PATHS[key]).read_bytes() for key in ("cursor", "codex")}
    c.system.bootout_mode = "ok"
    again = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert {item["undo"] for item in again["results"]} == {"nothing-to-undo"}
    assert again["ok"] is True and again["service"]["undo"] == "stopped-and-plist-moved"
    assert not (c.home / PLIST).exists() and {key: (c.home / PATHS[key]).read_bytes() for key in files} == files
    third = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert third["ok"] is True and third["service"] == {"undo": "plist-already-absent"}
    assert c.system.verbs().count("bootout") == 2  # one per run that reached the stop, never a retry loop


def test_rollback_confirms_the_stop_by_observing_the_job_absent(c):
    result = install(c, profile="lexical")
    before = len(c.system.calls)
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    after = [argv[:2] for argv in c.system.calls[before:]]
    stop = after.index(["launchctl", "bootout"])
    assert ["launchctl", "print"] in after[:stop], "the job identity is re-checked before the stop"
    assert ["launchctl", "print"] in after[stop + 1:], "the job is observed absent after the stop"
    assert out["ok"] is True and out["service"]["undo"] == "stopped-and-plist-moved"


@pytest.mark.parametrize("observation", ["error", "timeout"])
def test_rollback_treats_an_unobservable_job_as_not_stopped(c, observation):
    """``bootout`` returned 0 but the job can no longer be observed: indeterminate is never 'absent'."""
    result = install(c, profile="lexical")
    plist = (c.home / PLIST).read_bytes()
    c.system.observe_after_stop = observation
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=no_sleep)
    assert out["ok"] is False and out["code"] == "service_stop_unconfirmed"
    assert out["service"]["undo"] == "stop-unconfirmed" and out["service"]["observation"] == "indeterminate"
    assert (c.home / PLIST).read_bytes() == plist and "plist_moved_to" not in out["service"]
    assert not list((c.home / STATE).rglob("*.plist"))


@pytest.mark.parametrize("observation", ["error", "timeout"])
def test_uninstall_treats_an_unobservable_job_as_not_stopped(c, observation):
    install(c, profile="lexical")
    plan = uninstall_plan(c)
    plist = (c.home / PLIST).read_bytes()
    c.system.observe_after_stop = observation
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=no_sleep)
    assert result["ok"] is False and result["partial"] is True and result["code"] == "service_stop_unconfirmed"
    assert result["service"]["outcome"] == "stop-unconfirmed" and result["service"]["observation"] == "indeterminate"
    assert (c.home / PLIST).read_bytes() == plist and "plist_moved_to" not in result["service"]


def test_rollback_never_stops_a_job_replaced_since_the_install(c):
    result = install(c, profile="lexical")
    c.system.job_replaced = True
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["service"]["undo"] == "refused-job-not-attested" and out["code"] == "service_changed_since_install"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


@pytest.mark.parametrize("damage", ["install-mode", "journal-mode", "directory-mode", "symlink",
                                    "hardlink-install", "hardlink-journal", "owner", "root-symlink"])
def test_rollback_trusts_only_a_private_journal_on_safe_paths(c, damage, monkeypatch):
    result = install(c, profile="lexical")
    journal_dir = c.home / STATE / "quickstart" / result["stamp"]
    if damage == "hardlink-install":
        os.link(journal_dir / "install.json", c.root / "second-name-of-install.json")
    elif damage == "hardlink-journal":
        os.link(journal_dir / "journal.json", journal_dir / "second-name.json")
    elif damage == "owner":  # every file now belongs to "someone else"
        uid = os.getuid()
        monkeypatch.setattr(qs.os, "getuid", lambda: uid + 1)
    elif damage == "root-symlink":
        root = c.home / STATE / "quickstart"
        real = root.with_name("quickstart.real")
        root.rename(real)
        root.symlink_to(real)
    elif damage == "install-mode":
        (journal_dir / "install.json").chmod(0o644)
    elif damage == "journal-mode":
        (journal_dir / "journal.json").chmod(0o640)
    elif damage == "directory-mode":
        journal_dir.chmod(0o755)
    else:
        real = journal_dir.with_name(journal_dir.name + ".real")
        journal_dir.rename(real)
        journal_dir.symlink_to(real)
    before = (c.home / PATHS["cursor"]).read_bytes()
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert refusal.value.code == "journal_unsafe"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()
    assert (c.home / PATHS["cursor"]).read_bytes() == before


def test_rollback_keeps_the_service_while_another_client_is_wired_to_it(c):
    """Contract §23: the rollback applies the uninstall rule. Install A created the service, install B wired VS Code."""
    first = install(c, profile="lexical")
    install(c, profile="lexical", clients=("vscode",))
    out = qs.undo(first["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is True and out["service"]["undo"] == "kept-clients-remaining"
    assert out["service"]["remaining_clients"] == ["vscode"]
    assert out["service"]["remaining_dependencies"] == {"vscode": "wired"}
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
    assert "memory-os-ai" not in servers(c.home, "cursor") and "memory-os-ai" in servers(c.home, "vscode")


def test_rollback_keeps_the_service_when_a_client_could_not_be_restored(c):
    """Contract §23: a client still configured is never left pointing to a stopped service."""
    result = install(c, profile="lexical")
    entry = servers(c.home, "cursor")["memory-os-ai"]
    set_entry(c.home, "cursor", {**entry, "command": "/edited/venv/bin/python3"})
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["results"] == [{"client": "cursor", "undo": "refused-entry-changed-since-apply"}]
    assert out["ok"] is False and out["code"] == "undo_partial"
    assert out["service"] == {"undo": "kept-clients-incomplete"}
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True


def test_rollback_does_not_remove_an_entry_whose_proof_was_already_consumed(c):
    """A revoked record authorises nothing: the same entry, re-created by hand, is not removed by a second undo."""
    result = install(c, profile="lexical")
    entry = servers(c.home, "cursor")["memory-os-ai"]
    assert qs.undo(result["stamp"], home=c.home, run=c.system)["results"] == [{"client": "cursor", "undo": "restored"}]
    set_entry(c.home, "cursor", entry)
    again = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert again["results"] == [{"client": "cursor", "undo": "refused-proof-revoked"}] and again["ok"] is False
    assert servers(c.home, "cursor")["memory-os-ai"] == entry


def test_rollback_never_pushes_a_foreign_previous_entry_through_the_claude_cli(c):
    """The CLI cannot express it (and its env would sit on a command line): refused before any change."""
    from setup_fixture_410 import FOREIGN_SECRET
    previous = {"type": "http", "url": "http://x.invalid/mcp", "headers": {"Authorization": "Bearer " + FOREIGN_SECRET}}
    set_entry(c.home, "claude-code", previous)
    result = install(c, profile="lexical", clients=("claude-code", "cursor"), replace_existing=("claude-code",),
                     which=lambda name: "/usr/local/bin/claude")
    assert result["ok"] is True
    ours = servers(c.home, "claude-code")["memory-os-ai"]
    calls = len(c.system.calls)
    out = qs.undo(result["stamp"], home=c.home, run=c.system)
    by = {item["client"]: item["undo"] for item in out["results"]}
    assert by == {"claude-code": "refused-removal-manual", "cursor": "restored"} and out["ok"] is False
    assert servers(c.home, "claude-code")["memory-os-ai"] == ours  # untouched, still proven
    assert not [argv for argv in c.system.calls[calls:] if argv[0] == "claude"]
    assert FOREIGN_SECRET not in json.dumps(out) + json.dumps(c.system.calls)
    backup = Path(result["clients"]["records"][0]["backup"])
    assert FOREIGN_SECRET in backup.read_text()  # the previous entry survives in the private backup only


def test_rollback_reports_a_failing_claude_cli_and_still_handles_the_other_clients(c):
    result = install(c, profile="lexical", clients=("claude-code", "cursor"), which=lambda name: "/usr/local/bin/claude")
    c.system.claude_fails = True
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    by = {item["client"]: item["undo"] for item in out["results"]}
    assert by == {"claude-code": "failed-entry-kept-unproven", "cursor": "restored"}
    assert out["ok"] is False and out["service"] == {"undo": "kept-clients-incomplete"} and "bootout" not in c.system.verbs()


def test_rollback_of_a_journal_that_cannot_describe_its_service_never_stops_it(c):
    """A 4.0.9 ``install.json`` has no ``service`` section: the job cannot be attested."""
    result = install(c, profile="lexical")
    path = c.home / STATE / "quickstart" / result["stamp"] / "install.json"
    document = json.loads(path.read_text())
    del document["service"]
    path.write_text(json.dumps(document))
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["service"] == {"undo": "refused-job-not-attested"} and out["code"] == "service_changed_since_install"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists()


def test_a_journal_below_a_symbolic_link_higher_on_the_path_is_not_trusted(c):
    result = install(c, profile="lexical")
    state = c.home / STATE
    real = state.with_name("memory-os.real")
    state.rename(real)
    state.symlink_to(real)
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.undo(result["stamp"], home=c.home, run=c.system)
    assert refusal.value.code == "journal_unsafe"


def test_crlf_client_files_keep_their_line_endings(c):
    for key in ("cursor", "codex"):
        path = c.home / PATHS[key]
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    originals = {key: (c.home / PATHS[key]).read_bytes() for key in ("cursor", "codex")}
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    for key in originals:
        written = (c.home / PATHS[key]).read_bytes()
        assert b"memory-os-ai" in written and b"\n" not in written.replace(b"\r\n", b"")
    assert (c.home / PATHS["codex"]).read_bytes().startswith(originals["codex"])  # prior bytes are a prefix
    qs.undo(result["stamp"], home=c.home, run=c.system)
    assert {key: (c.home / PATHS[key]).read_bytes() for key in originals} == originals


# ---- interrupted install ----

def interrupted(c, clients=("cursor",)):
    c.system.bootstrap_fails = True
    result = install(c, profile="lexical", clients=clients)
    assert result["ok"] is False and result["stage"] == "service" and result["code"] == "service_bootstrap_failed"
    assert result["steps"]["service"]["created"] is True and "clients" not in result
    return result


def test_interrupted_install_leaves_evidence_and_no_client_change(c):
    originals = {key: (c.home / PATHS[key]).read_bytes() for key in SIX}
    result = interrupted(c)
    assert (c.home / PLIST).exists() and c.settings.token_file.exists() and c.system.loaded is False
    assert {key: (c.home / PATHS[key]).read_bytes() for key in SIX} == originals
    journal = json.loads((c.home / STATE / "quickstart" / result["stamp"] / "install.json").read_text())
    assert journal["ok"] is False and journal["stage"] == "service"


def test_interrupted_install_is_rolled_back_without_a_stop_when_the_job_never_loaded(c):
    result = interrupted(c)
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is True and out["service"]["undo"] == "plist-moved-job-was-absent"
    assert "bootout" not in c.system.verbs() and not (c.home / PLIST).exists()
    assert Path(out["service"]["plist_moved_to"]).is_file()
    assert c.settings.token_file.exists()  # the token is data of the user: kept


def test_second_install_after_an_interrupted_one_is_refused_until_it_is_rolled_back(c):
    interrupted(c)
    c.system.bootstrap_fails = False
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "service_not_loaded" in plan["blocker_codes"]
    assert any("--undo" in blocker and "--with-service" in blocker for blocker in plan["blockers"])


# ---- uninstall: order, stop failures, late conflict ----

def installed(c, clients=SIX):
    return install(c, profile="lexical", clients=clients, which=claude_on_path)


def test_uninstall_removes_the_clients_before_it_stops_the_service(c):
    installed(c)
    before = len(c.system.calls)
    uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    after = [argv[:3] for argv in c.system.calls[before:]]
    assert after.index(["claude", "mcp", "remove"]) < [argv[:2] for argv in after].index(["launchctl", "bootout"])


@pytest.mark.parametrize("mode", sorted(STOP_FAILURES))
def test_uninstall_reports_a_partial_result_when_the_stop_is_not_confirmed(c, mode):
    installed(c)
    plist = (c.home / PLIST).read_bytes()
    plan = uninstall_plan(c)
    c.system.bootout_mode = mode
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=no_sleep)
    assert result["ok"] is False and result["partial"] is True and result["code"] == STOP_FAILURES[mode]
    assert result["service"]["outcome"] == STOP_FAILURES[mode].replace("service_", "").replace("_", "-")
    assert (c.home / PLIST).read_bytes() == plist and "plist_moved_to" not in result["service"]
    assert {item["client"]: item["status"] for item in result["clients"] if item["client"] in SIX} == dict.fromkeys(SIX, "removed")
    assert any("launchctl bootout" in step for step in result["next_steps"])
    # A later run finishes the job once the stop works: nothing is lost, nothing is done twice.
    c.system.bootout_mode = "ok"
    again = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    assert again["ok"] is True and again["service"]["outcome"] == "stopped-and-plist-moved"
    assert not (c.home / PLIST).exists()


def test_uninstall_refuses_a_job_replaced_after_its_preview(c):
    installed(c)
    plan = uninstall_plan(c)
    c.system.job_replaced = True
    before = tree(c.root)
    with pytest.raises(uninstaller().UninstallError) as refusal:
        uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "uninstall_changed_since_preview"
    assert tree(c.root) == before and "bootout" not in c.system.verbs()


def test_uninstall_late_conflict_gives_a_partial_result_and_keeps_the_service(c):
    """A file rewritten while the uninstall runs: that client is skipped, the service stays up."""
    installed(c)
    plan = uninstall_plan(c)
    cursor = c.home / PATHS["cursor"]

    def rewrite_cursor():  # happens during the first mutation (Claude Code goes first)
        cursor.write_text(cursor.read_text() + "\n")
    c.system.hooks[("claude", "mcp", "remove")] = rewrite_cursor
    result = uninstaller().apply(plan, c.settings, home=c.home, run=c.system)
    statuses = {item["client"]: item["status"] for item in result["clients"]}
    assert statuses["claude-code"] == "removed" and statuses["cursor"] == "conflict-file-changed-since-preview"
    assert result["ok"] is False and result["partial"] is True and result["code"] == "uninstall_partial"
    assert result["service"]["outcome"] == "kept-clients-incomplete"
    assert "bootout" not in c.system.verbs() and (c.home / PLIST).exists() and c.system.loaded is True
    assert "memory-os-ai" in servers(c.home, "cursor")


# ---- data: recoverable memories, not only preserved bytes ----

def test_real_store_memories_written_after_the_install_survive_a_rollback_with_service(c):
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    keys = write_memories(c, 3)
    identity = read_memories(c, keys)["instance_id"]
    more = write_memories(c, 2, tag="second-session")
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is True and out["service"]["undo"] == "stopped-and-plist-moved"
    after = read_memories(c, {**keys, **more})
    assert all(after["found"].values()) and len(after["found"]) == 5
    assert after["instance_id"] == identity  # the same store, not a copy put back in service


def test_real_store_memories_survive_an_uninstall_and_stay_searchable(c):
    installed(c, clients=("cursor", "vscode"))
    keys = write_memories(c, 4)
    identity = read_memories(c, keys)["instance_id"]
    files = contents(c.settings.store_dir)
    result = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
    assert result["ok"] is True and result["data_deleted"] is False
    assert contents(c.settings.store_dir) == files  # the uninstall itself never opens the store for writing
    after = read_memories(c, keys)
    assert all(after["found"].values()) and after["instance_id"] == identity
    again = write_memories(c, 1, tag="after-uninstall")  # still a working store
    assert all(read_memories(c, again)["found"].values())


@pytest.mark.parametrize("mode", sorted(STOP_FAILURES))
def test_real_store_memories_survive_a_failed_stop(c, mode):
    installed(c, clients=("cursor",))
    keys = write_memories(c, 2)
    plan = uninstall_plan(c)
    c.system.bootout_mode = mode
    uninstaller().apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=no_sleep)
    assert all(read_memories(c, keys)["found"].values())


def test_real_store_uncheckpointed_wal_is_left_alone_by_rollback(c):
    """A writer still holding the store (WAL not merged): the rollback neither opens nor copies it."""
    import sqlite3
    result = install(c, profile="lexical", clients=("cursor",))
    write_memories(c, 1)
    database = c.settings.store_dir / "store.sqlite3"
    holder = sqlite3.connect(database)
    try:
        holder.execute("PRAGMA wal_autocheckpoint=0")
        holder.execute("CREATE TABLE IF NOT EXISTS setup_probe_410 (value TEXT)")
        holder.execute("INSERT INTO setup_probe_410 VALUES ('written after install, still in the WAL')")
        holder.commit()
        wal = Path(str(database) + "-wal")
        assert wal.stat().st_size > 0
        before = contents(c.settings.store_dir)
        qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
        assert contents(c.settings.store_dir) == before
        row = holder.execute("SELECT value FROM setup_probe_410").fetchone()
        assert row == ("written after install, still in the WAL",)
    finally:
        holder.close()


def test_real_store_uncheckpointed_wal_is_left_alone_by_uninstall(c):
    """Same proof for the uninstall: a live writer's unmerged WAL is neither opened nor touched."""
    import sqlite3
    install(c, profile="lexical", clients=("cursor",))
    keys = write_memories(c, 1)
    database = c.settings.store_dir / "store.sqlite3"
    holder = sqlite3.connect(database)
    try:
        holder.execute("PRAGMA wal_autocheckpoint=0")
        holder.execute("CREATE TABLE IF NOT EXISTS setup_probe_410 (value TEXT)")
        holder.execute("INSERT INTO setup_probe_410 VALUES ('unmerged at uninstall time')")
        holder.commit()
        assert Path(str(database) + "-wal").stat().st_size > 0
        before = tree(c.settings.store_dir)  # bytes, modes AND mtimes: not even opened for writing
        result = uninstaller().apply(uninstall_plan(c), c.settings, home=c.home, run=c.system)
        assert result["ok"] is True and tree(c.settings.store_dir) == before
        assert holder.execute("SELECT value FROM setup_probe_410").fetchone() == ("unmerged at uninstall time",)
    finally:
        holder.close()
    assert all(read_memories(c, keys)["found"].values())  # and the engine still recovers everything


def test_real_lexical_store_helpers_work_today(c):
    """Green today: the proof harness itself (real engine, no ML) writes, reopens and finds."""
    import sys
    keys = write_memories(c, 2)
    after = read_memories(c, keys)
    assert all(after["found"].values()) and after["instance_id"]
    assert (c.settings.store_dir / "store.sqlite3").is_file()
    assert os.environ["HOME"] != "/Users/romain"
    assert not [name for name in ("torch", "faiss", "sentence_transformers") if name in sys.modules]
