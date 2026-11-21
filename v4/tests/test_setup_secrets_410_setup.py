"""SETUP_4_1_0.md §8: no secret on the FAILURE paths either.

Sentinels for the bearer token, the backup key and a foreign secret are injected where a failure
could carry them: the server log, subprocess stdout/stderr, doctor details, exceptions. Journals,
results, errors and command output must hold allow-listed, structured diagnostics only.
"""
from __future__ import annotations

import json

import pytest

from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.doctor import Check
from setup_fixture_410 import (
    BACKUP_KEY, LEAK_SECRET, PATHS, SECRETS, SIX, all_text, install, prepare, setup_context,
)

PLIST = "Library/LaunchAgents/test.memory-os.plist"
STATE = ".local/state/memory-os"
STARTUP_FAILURE_KEYS = {"state", "runs", "last_exit_code", "pid", "process_state", "exception_types",
                        "log_path", "log_bytes", "stop_attempted", "stopped"}


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


def forbidden(c):
    values = [LEAK_SECRET, *SECRETS, BACKUP_KEY.decode(), BACKUP_KEY.hex()]
    if c.settings.token_file.exists():
        values.append(c.settings.token_file.read_text().strip())
    return values


def assert_clean(c, *documents):
    shown = "".join(json.dumps(document, default=str) for document in documents) + all_text(c.home / STATE)
    leaked = [value[:12] for value in forbidden(c) if value in shown]
    assert not leaked, f"secret reached a journal, result or error: {leaked}"


def plant_key(c):
    c.settings.backup_key.parent.mkdir(parents=True, exist_ok=True)
    c.settings.backup_key.write_bytes(BACKUP_KEY)
    c.settings.backup_key.chmod(0o600)


# ---- the server log of a crashing service ----

def crash(c, monkeypatch, clients=("cursor",)):
    plant_key(c)
    monkeypatch.setattr(qs, "_ready", lambda *a, **k: None)
    monkeypatch.setattr(qs.time, "sleep", lambda *a: pytest.fail("a known startup failure must not wait"))
    log = c.settings.ops_dir / "logs/server.err"

    def write_log():
        token = c.settings.token_file.read_text().strip()
        log.write_text("\n".join([
            "x" * 9000,
            "Authorization: Bearer " + token,
            "MEMORY_V4_BACKUP_KEY bytes " + BACKUP_KEY.decode() + " hex " + BACKUP_KEY.hex(),
            "upstream said api_key=" + LEAK_SECRET + " for " + SECRETS[0],
            "Traceback (most recent call last):",
            '  File "/srv/x.py", line 3, in start  # ' + LEAK_SECRET,
            "Leak5c6d7e8fSentinelError: " + LEAK_SECRET,
            "AttributeError: 'Server' object has no attribute '" + LEAK_SECRET + "'",
        ]) + "\n")
        c.system.crashing = (2, 1)
    c.system.hooks[("launchctl", "bootstrap")] = write_log
    result = install(c, profile="lexical", clients=clients)
    return result, log


def test_startup_failure_is_reported_as_structured_facts_never_as_a_log_tail(c, monkeypatch):
    result, log = crash(c, monkeypatch)
    assert result["ok"] is False and result["code"] == "service_startup_failed"
    failure = result["startup_failure"]
    assert set(failure) <= STARTUP_FAILURE_KEYS and "stderr_tail" not in failure
    assert failure["runs"] == 2 and failure["last_exit_code"] == 1
    assert failure["exception_types"] == ["AttributeError"]  # known class names only, never their message
    assert failure["log_path"] == str(log) and failure["log_bytes"] == log.stat().st_size
    assert str(log) in result["error"] and "AttributeError" not in result["error"]
    assert_clean(c, result)


def test_startup_failure_log_stays_private_and_untouched(c, monkeypatch):
    result, log = crash(c, monkeypatch)
    assert LEAK_SECRET in log.read_text()  # the evidence is kept where it was, for the operator
    assert log.parent.stat().st_mode & 0o777 == 0o700
    assert c.system.verbs().count("bootout") == 1 and result["startup_failure"]["stopped"] is True


def test_startup_failure_through_the_cli_prints_no_secret(c, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    result, _ = crash(c, monkeypatch)
    monkeypatch.setattr(qs, "prepare", lambda settings, **kw: {"ok": True, "blockers": [], "clients": []})
    monkeypatch.setattr(qs, "apply_install", lambda plan, settings, **kw: result)
    monkeypatch.setattr(qs, "settings_for_install", lambda settings, **kw: (c.settings, "sqlite"))
    assert cli.main(["quickstart", "--profile", "lexical", "--apply"]) == 1
    out = capsys.readouterr()
    assert not [value for value in forbidden(c) if value in out.out + out.err]


# ---- subprocess stdout / stderr ----

@pytest.mark.parametrize("failure", ["none", "claude", "bootstrap", "rollback-stop", "uninstall-stop"])
def test_subprocess_output_is_never_copied(c, failure):
    plant_key(c)
    c.system.noise = f"\nwarning: token={LEAK_SECRET} key={BACKUP_KEY.hex()}\n"
    c.system.claude_fails = failure == "claude"
    c.system.bootstrap_fails = failure == "bootstrap"
    documents = [install(c, profile="lexical", clients=("claude-code", "cursor"),
                         which=lambda name: "/usr/local/bin/claude")]
    assert documents[0]["ok"] is (failure not in ("claude", "bootstrap"))
    if failure == "rollback-stop":
        c.system.bootout_mode = "fail"
        documents.append(qs.undo(documents[0]["stamp"], home=c.home, run=c.system, with_service=True,
                                 stop_timeout=0, sleep=lambda s: None))
        assert documents[-1]["ok"] is False
    elif failure == "uninstall-stop":
        from memory_os_v4.ops import uninstall
        plan = uninstall.plan(c.settings, home=c.home, python=c.python, run=c.system)
        c.system.bootout_mode = "timeout"
        documents += [plan, uninstall.apply(plan, c.settings, home=c.home, run=c.system,
                                            stop_timeout=0, sleep=lambda s: None)]
        assert documents[-1]["ok"] is False
    assert_clean(c, *documents)
    if failure != "none":
        failed = documents[-1]
        assert isinstance(failed["code"], str) and failed["code"] != "quickstart_refused"


def test_client_level_cli_failure_reports_the_return_code_only(c, tmp_path):
    """Green today: a failing ``claude`` command gives its code, never its output."""
    c.system.noise, c.system.claude_fails = "\nstderr with " + LEAK_SECRET, True
    env = {"MEMORY_V4_URL": c.settings.url, "MEMORY_V4_TOKEN_FILE": str(c.settings.token_file)}
    plan = qs.preview(home=c.home, python=c.python, env=env, clients=("claude-code",))
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.apply_clients(plan, home=c.home, python=c.python, env=env, run=c.system,
                         journal_root=c.home / STATE / "quickstart")
    assert LEAK_SECRET not in str(refusal.value) + all_text(c.home / STATE)


# ---- doctor details ----

@pytest.mark.parametrize("status", ["PASS", "WARN", "FAIL"])
def test_doctor_details_are_not_journaled(c, status):
    plant_key(c)
    checks = [Check("server", "PASS", "ok"),
              Check("store", status, "strict check failed: " + LEAK_SECRET, "see " + BACKUP_KEY.hex())]
    result = install(c, profile="lexical", doctor_checks=checks)
    assert result["ok"] is (status != "FAIL")
    assert result["doctor"] == [{"area": "server", "status": "PASS"}, {"area": "store", "status": status}]
    if status == "FAIL":
        assert result["code"] == "doctor_failed" and "store" in result["error"]
    assert_clean(c, result)


def test_doctor_output_on_a_damaged_installation_prints_no_secret(c, monkeypatch, tmp_path):
    """Green today: real ``doctor.run`` over hostile files — details come from templates."""
    from dataclasses import replace
    from memory_os_v4.ops import doctor
    monkeypatch.setenv("MEMORY_V4_RELAY_JOURNAL_DIR", str(tmp_path / "relay"))
    settings = replace(c.settings, url="http://127.0.0.1:9/mcp/", service_label=None)
    settings.store_dir.mkdir(parents=True)
    (settings.store_dir / "items.jsonl").write_text('{"id": "a", "text": "' + LEAK_SECRET + '", "project": "p"\n')
    settings.token_file.parent.mkdir(parents=True, exist_ok=True)
    settings.token_file.write_text(LEAK_SECRET + "!not-a-token\n")
    settings.token_file.chmod(0o600)
    settings.backup_key.write_bytes(BACKUP_KEY + b"-too-long")
    settings.backup_key.chmod(0o600)
    checks = doctor.run(settings, home=c.home)
    shown = doctor.render(checks) + json.dumps([check.__dict__ for check in checks])
    assert not [value for value in (LEAK_SECRET, *SECRETS, BACKUP_KEY.decode(), BACKUP_KEY.hex()) if value in shown]
    assert {check.area: check.status for check in checks}["store"] == "FAIL"


# ---- exceptions ----

@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError, KeyError])
def test_foreign_exception_text_is_reduced_to_its_type(c, error):
    """An exception that is not ours is journaled by class name only, with a stable code."""
    async def probe(python, env, **kwargs):
        raise error("upstream body: " + LEAK_SECRET)
    result = install(c, profile="lexical", probe=probe)
    assert result["ok"] is False and result["error"] == error.__name__ and result["code"] == "relay_probe_failed"
    assert_clean(c, result)


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
def test_cli_reduces_a_foreign_exception_to_its_type(c, monkeypatch, capsys, error):
    """Green today: the command handler never prints the text of an exception it did not build."""
    from memory_os_v4.ops import cli

    def boom(*args, **kwargs):
        raise error("path /x and secret " + LEAK_SECRET)
    monkeypatch.setattr(qs, "settings_for_install", boom)
    assert cli.main(["quickstart", "--clients", "cursor"]) == 1
    out = capsys.readouterr()
    assert LEAK_SECRET not in out.out + out.err and json.loads(out.out)["error"] == error.__name__


def test_refusal_with_every_client_holding_foreign_secrets_shows_none(c):
    for key in ("cursor", "antigravity"):
        path = c.home / PATHS[key]
        path.write_text('{"mcpServers": {"memory-os-ai": {"url": "http://x", "headers": {"Authorization": "Bearer '
                        + LEAK_SECRET + '"}}}}')
    plan = prepare(c, profile="lexical", clients=SIX, which=lambda name: "/usr/local/bin/claude")
    assert plan["ok"] is False
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system)
    assert LEAK_SECRET not in json.dumps(plan) + str(refusal.value)
    assert not [secret for secret in SECRETS if secret in json.dumps(plan)]
