"""4.1.3 one-command install (decision of Romain, 2026-11-26): ``memory_os_bootstrap.py --interactive``.

Offline. The real ``uv`` is never run (the fake of ``test_bootstrap_411``), the quickstart run from the venv is a fake
module of the test wheel that answers what the test wrote, ``/ready`` is a fake callable (and, once, a throwaway local
HTTP server on an ephemeral port), and the terminal is a fake object. Every expected client and service path is
computed by the PRODUCT (``ops/clients.py``, ``ops/service.py``): the bootstrap's own copy of these rules is
checked against it, never against itself.

Also: COPILOT_HOME (vscode on Linux only) in the bootstrap's relocation detection (Codex review of Lot 3, point 6).
"""
from __future__ import annotations

import argparse
import http.server
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import zipfile

import pytest

from memory_os_v4.ops import clients as product_clients, quickstart as qs, service as product_service
from memory_os_v4.ops.clients import ClientPathError
from memory_os_v4.ops.settings import OpsSettings
from setup_fixture_410 import tree
from test_bootstrap_411 import CELL, MAC, MINOR, UBUNTU, VERSION, Machine, bs, make_pin

pytestmark = pytest.mark.skipif(MINOR not in ((3, 11), (3, 12), (3, 13)), reason="needs a Python of a retained cell")
LABEL = "local.memory-os-v4"

# ---------------------------------------------------------------- fakes

FAKE_QUICKSTART = r'''import json, os, sys
if __name__ == "__main__":
    root = os.path.dirname(os.path.abspath(os.environ["HOME"]))
    config = json.load(open(os.path.join(root, "fakeqs.json")))
    argv = sys.argv[1:]
    mode = "apply" if "--apply" in argv else "preview"
    with open(os.path.join(root, "fakeqs-calls.jsonl"), "a") as log:
        log.write(json.dumps({"mode": mode, "argv": argv, "env": sorted(os.environ)}) + "\n")
    answer = config[mode]
    print(json.dumps(answer))
    sys.exit(0 if answer.get("ok") else 1)
'''


def make_quickstart_wheel(directory: Path) -> Path:
    path = directory / f"memory_os_ai-{VERSION}-py3-none-any.whl"
    info = f"memory_os_ai-{VERSION}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("memory_os_v4/__init__.py", f'__version__ = "{VERSION}"\n')
        archive.writestr("memory_os_v4/ops/__init__.py", "")
        archive.writestr("memory_os_v4/ops/cli.py", FAKE_QUICKSTART)
        archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: memory-os-ai\nVersion: {VERSION}\n")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")
    return path


class FakeTerminal:
    """The ``terminal`` factory and the terminal itself: records the prompt, answers one line."""

    def __init__(self, answer="y", on_read=None):
        self.answer, self.on_read, self.opened, self.written, self.closed = answer, on_read, 0, [], False

    def __call__(self):
        self.opened += 1
        return self

    def write(self, text):
        self.written.append(text)

    def readline(self):
        if self.on_read:
            self.on_read()
        return self.answer

    def close(self):
        self.closed = True


def product_path(key, home, os_kind, environ):
    """What ops/clients.py says: (path, None, None) or (None, code, message)."""
    client = next(c for c in product_clients.platform_clients(os_kind) if c.key == key)
    try:
        return str(client.config_path(Path(home), platform=os_kind, environ=environ)), None, None
    except ClientPathError as exc:
        return None, exc.code, str(exc)


def product_definition(home, os_kind, environ):
    manager = product_service.Launchd() if os_kind == "darwin" else product_service.SystemdUser(
        {"HOME": str(home), **environ})
    return str(manager.definition_path(LABEL, Path(home))), manager.plan_extras(LABEL)


class OneLiner:
    """A Machine (temporary HOME, fake uv, pinned test wheel) whose quickstart is the fake above."""

    def __init__(self, tmp_path, *, home="home", host=None):
        self.m = Machine(tmp_path, home=home)
        self.m.wheel = make_quickstart_wheel(self.m.wheelhouse)
        self.m.pin = make_pin(self.m.wheel, cells=tuple(bs.CELLS), profiles=bs.PROFILES)
        self.m.configure(wheel=str(self.m.wheel))
        self.m.host = dict(host or MAC)
        self.home, self.root = self.m.home, tmp_path
        self.environ = {key: value for key, value in self.m.environ.items() if key != "XDG_CONFIG_HOME"}
        self.cli = {}  # command name -> path answered by ``which``
        self.ready_answer = {"ok": True, "code": None, "status": 200, "ready": True, "reason": "ready",
                             "version": VERSION}
        self.ready_calls = []
        self.answers(apply={"ok": True, "stage": "complete", "stamp": "20261126T101500Z-aaaa"})

    @property
    def os_kind(self):
        return self.m.host["os"]

    def touch(self, relative, text="{}\n"):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def preview(self, clients, **changes):
        """A quickstart preview built from the PRODUCT rules for this HOME, OS and environment."""
        environ = {key: value for key, value in self.environ.items() if key in ("XDG_CONFIG_HOME", "COPILOT_HOME")}
        rows = [{"client": key, "path": product_path(key, self.home, self.os_kind, environ)[0], "action": "create"}
                for key in clients]
        definition, extras = product_definition(self.home, self.os_kind, environ)
        answer = {"ok": True, "stage": "preview", "writes": "none (preview)", "blockers": [], "blocker_codes": [],
                  "profile": "lexical", "cell": {"cell_id": CELL}, "clients": rows, "unknown_clients": [],
                  "service": {"label": LABEL, "path": definition, "state": "absent", **extras}, "port": 8765,
                  "token": {"path": str(self.home / ".config/memory-os/http.token"), "state": "absent"},
                  "downloads_required_bytes": 0, "notices": []}
        answer.update(changes)
        return answer

    def answers(self, *, preview=None, apply=None):
        path = self.root / "fakeqs.json"
        current = json.loads(path.read_text()) if path.exists() else {"preview": {}, "apply": {}}
        if preview is not None:
            current["preview"] = preview
        if apply is not None:
            current["apply"] = apply
        path.write_text(json.dumps(current))

    def qs_calls(self):
        log = self.root / "fakeqs-calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def which(self, name, path=None):
        return self.cli.get(name)

    def ready(self, url, token_path, *, expected_version=None):
        self.ready_calls.append((url, token_path, expected_version))
        return dict(self.ready_answer)

    def options(self, *argv):
        options = bs.parser().parse_args(["--uv", str(self.m.uv), "--interactive", *argv])
        options.replay = []
        return options

    def run(self, *argv, terminal=None, pin=None):
        out = io.StringIO()
        report = bs.interactive(self.options(*argv), pin=bs.load_pin(json.dumps(self.m.pin if pin is None else pin)),
                                run=self.m.run, environ=self.environ, host=self.m.host, out=out,
                                terminal=terminal if terminal is not None else FakeTerminal("y"), ready=self.ready,
                                which=self.which, sleep=lambda seconds: None)
        return report, out.getvalue()

    def main(self, *argv, terminal=None, pin=None):
        out = io.StringIO()
        code = bs.main(["--uv", str(self.m.uv), "--interactive", *argv],
                       pin=bs.load_pin(json.dumps(self.m.pin if pin is None else pin)), run=self.m.run,
                       environ=self.environ, host=self.m.host, out=out,
                       terminal=terminal if terminal is not None else FakeTerminal("y"), ready=self.ready,
                       which=self.which, sleep=lambda seconds: None)
        return code, out.getvalue()

    def inventory(self):
        """The whole temporary root, minus the fake uv log (written by ``uv --version`` itself)."""
        if self.m.log.exists():
            self.m.log.unlink()
        return tree(self.root)


@pytest.fixture
def o(tmp_path):
    one = OneLiner(tmp_path)
    one.touch(".codex/config.toml", "")
    one.touch(".cursor/mcp.json")
    one.answers(preview=one.preview(["codex", "cursor"]))
    return one


# ---------------------------------------------------------------- the bootstrap's copy of the product rules

def test_the_client_table_is_the_product_one_field_by_field():
    product = {c.key: c for c in product_clients.CLIENTS}
    assert tuple(bs.CLIENT_TABLE) == tuple(product)
    for key, record in bs.CLIENT_TABLE.items():
        client = product[key]
        assert (record["name"], record["darwin"], record["linux"]) == (client.name, client.path, client.linux_path), key
        assert tuple(record.get("override", ())) == client.override_env, key
        assert tuple(record.get("linux_override", ())) == client.linux_override_env, key
        assert record.get("xdg") == client.xdg_subpath, key
    assert bs.LINUX_UNSUPPORTED_REASONS == product_clients.LINUX_UNSUPPORTED_REASONS
    for os_kind in ("darwin", "linux"):
        table = bs.client_override_env(os_kind)
        for client in product_clients.platform_clients(os_kind):
            assert table.get(client.key, ()) == client.override_env, (os_kind, client.key)
    assert bs.client_override_env("linux")["vscode"] == ("COPILOT_HOME",) and "vscode" not in bs.client_override_env("darwin")


def test_the_service_and_token_defaults_are_the_product_ones(tmp_path, monkeypatch):
    for key in [key for key in os.environ if key.startswith("MEMORY_V4_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = OpsSettings.from_env()
    assert (settings.url, settings.ready_url, settings.port) == (bs.SERVICE_URL, bs.READY_URL, bs.SERVICE_PORT)
    assert str(settings.token_file) == str(tmp_path / bs.TOKEN_SUBPATH)
    assert str(settings.store_dir) == str(tmp_path / bs.STORE_SUBPATH)
    assert str(settings.ops_dir) == str(tmp_path / bs.OPS_SUBPATH) and settings.service_label is None
    planned = qs._service_plan(settings, tmp_path, sys.executable, {}, backend="sqlite", profile="lexical", manager=None)
    assert planned["label"] == bs.SERVICE_LABEL
    assert set(bs.WRITING_ACTIONS) == {"create", "replace", "unchanged"}


def _xdg_cases(home: Path):
    (home / "real" / "cfg").mkdir(parents=True)
    (home / "link").symlink_to(home / "real", target_is_directory=True)
    return {"absent": {}, "empty": {"XDG_CONFIG_HOME": ""}, "under": {"XDG_CONFIG_HOME": str(home / "real" / "cfg")},
            "under-trailing": {"XDG_CONFIG_HOME": str(home / "real" / "cfg") + "/"},
            "relative": {"XDG_CONFIG_HOME": "cfg"}, "outside": {"XDG_CONFIG_HOME": "/elsewhere/config"},
            "dotdot": {"XDG_CONFIG_HOME": str(home / "real" / ".." / "cfg")},
            "control": {"XDG_CONFIG_HOME": str(home) + "/c\x01fg"},
            "symlink": {"XDG_CONFIG_HOME": str(home / "link" / "cfg")}}


RELOCATIONS = [{}, {"COPILOT_HOME": ""}, {"COPILOT_HOME": "/somewhere"}, {"CODEX_HOME": ""},
               {"CLAUDE_CONFIG_DIR": "/c"}, {"GEMINI_CLI_HOME": "g"}, {"GROK_HOME": ""}]


@pytest.mark.parametrize("os_kind", ["darwin", "linux", "win32"])
def test_every_client_path_and_refusal_is_the_product_one(tmp_path, os_kind):
    home = tmp_path / "home"
    home.mkdir()
    cases = list(_xdg_cases(home).values()) + RELOCATIONS + [{**RELOCATIONS[1], "XDG_CONFIG_HOME": "cfg"}]
    for environ in cases:
        for key in bs.CLIENT_TABLE:
            mine = bs.client_path(key, str(home), os_kind=os_kind, environ=environ)
            assert mine == product_path(key, home, os_kind, environ), (os_kind, key, environ)


def test_the_service_definition_is_the_product_one(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert bs.service_definition(str(home), manager_kind="launchd", environ={}) == \
        (product_definition(home, "darwin", {})[0], None)
    for name, environ in _xdg_cases(home).items():
        mine, problem = bs.service_definition(str(home), manager_kind="systemd-user", environ=environ)
        theirs = product_definition(home, "linux", environ)[0]
        if name in ("outside", "dotdot", "control"):
            assert mine is None and problem, name  # refused before any question (quickstart refuses outside HOME)
        else:
            assert (mine, problem) == (theirs, None), name
    assert bs.service_definition(str(home), manager_kind=None, environ={})[0] is None


# ---------------------------------------------------------------- COPILOT_HOME (Codex Lot 3, point 6)

@pytest.mark.parametrize("value", ["", "/somewhere/copilot"])
def test_copilot_home_relocates_vscode_on_linux_only(value):
    found = bs.detect_client_env_overrides({"COPILOT_HOME": value}, "vscode", os_kind="linux")
    assert [(item["client"], item["variable"], item["selected"], item["code"]) for item in found] == \
        [("vscode", "COPILOT_HOME", True, "client_env_override")]
    assert "COPILOT_HOME" in found[0]["message"] and "without guessing a path" in found[0]["message"]
    other = bs.detect_client_env_overrides({"COPILOT_HOME": value}, "codex", os_kind="linux")
    assert [(item["client"], item["selected"]) for item in other] == [("vscode", False)]
    assert bs.detect_client_env_overrides({"COPILOT_HOME": value}, "vscode", os_kind="darwin") == []
    assert bs.detect_client_env_overrides({}, "vscode", os_kind="linux") == []
    # never forwarded: neither to the probes nor to the quickstart preview
    environ = {"HOME": "/home/u", "PATH": "/usr/bin", "COPILOT_HOME": value}
    assert "COPILOT_HOME" not in bs.child_environment(environ) and "COPILOT_HOME" not in bs.quickstart_environment(environ)


def test_copilot_home_in_the_preview_and_at_apply_on_linux(tmp_path):
    m = Machine(tmp_path)
    m.host = dict(UBUNTU)
    m.environ["COPILOT_HOME"] = ""
    code, report = m.main("--profile", "lexical", "--cpython", "3.11", "--clients", "vscode")
    assert code == 1 and report["blocker_codes"] == ["client_env_override"] and "COPILOT_HOME" in report["blockers"][0]
    assert [item["client"] for item in report["client_env_overrides"]] == ["vscode"]
    code, report = m.main("--profile", "lexical", "--cpython", "3.11", "--clients", "codex")
    assert code == 0 and report["blockers"] == []
    assert any(notice.startswith("client_env_override (non-selected client vscode)") for notice in report["notices"])
    del m.environ["COPILOT_HOME"]
    code, report = m.main("--profile", "lexical", "--cpython", "3.11", "--clients", "vscode")
    assert code == 0 and report["client_env_overrides"] == []
    # apply re-checks: a COPILOT_HOME that appears after the preflight refuses before any write
    options = bs.parser().parse_args(["--uv", str(m.uv), "--profile", "lexical", "--cpython", "3.11", "--clients",
                                      "vscode", "--apply"])
    options.replay, options.host = [], m.host
    pin = bs.load_pin(json.dumps(m.pin))
    plan = bs.build_plan(options, pin=pin, run=m.run, environ=m.environ, host=m.host)
    before = tree(m.home)
    applied = bs.apply_plan(plan, options, pin=pin, run=m.run, environ={**m.environ, "COPILOT_HOME": ""})
    assert applied["code"] == "client_env_override" and applied["writes"].startswith("none by the bootstrap")
    assert tree(m.home) == before


def test_copilot_home_is_ignored_for_vscode_on_macos(tmp_path):
    m = Machine(tmp_path)
    m.environ["COPILOT_HOME"] = ""
    code, report = m.main("--profile", "lexical", "--clients", "vscode")
    assert code == 0 and report["client_env_overrides"] == [] and not any("vscode" in n for n in report["notices"])


# ---------------------------------------------------------------- detection of the clients (read-only)

def detect(one, explicit=None):
    return bs.detect_clients(str(one.home), os_kind=one.os_kind, environ=one.environ, which=one.which,
                             explicit=explicit)


def test_clients_are_detected_by_their_file_or_their_cli_at_the_product_paths(tmp_path):
    one = OneLiner(tmp_path)
    one.touch(".codex/config.toml", "")
    one.touch(".cursor/mcp.json")
    one.touch("Library/Application Support/Code/User/mcp.json")
    one.cli.update(claude="/opt/bin/claude", gemini="/opt/bin/gemini")
    one.environ["COPILOT_HOME"] = ""  # a Linux variable: ignored for VS Code on macOS
    before = one.inventory()
    found = detect(one)
    assert [item["client"] for item in found["selected"]] == ["claude-code", "codex", "vscode", "cursor", "gemini-cli"]
    for item in found["selected"]:
        assert item["path"] == product_path(item["client"], one.home, "darwin", {})[0]
    assert found["selected"][0]["evidence"] == ["claude command on PATH (/opt/bin/claude)"]
    assert found["selected"][0]["written_by"].startswith("claude mcp add --scope user")
    assert found["selected"][1]["evidence"] == ["configuration file present"]
    assert found["excluded"] == [] and found["blockers"] == [] and one.inventory() == before


def test_a_relocated_unsafe_or_already_configured_client_is_excluded_never_guessed(tmp_path):
    one = OneLiner(tmp_path)
    one.cli.update(codex="/opt/bin/codex")
    one.environ["CODEX_HOME"] = ""
    one.touch(".claude.json")                                            # no claude CLI
    one.touch(".cursor/mcp.json", '{"mcpServers": {"memory-os-ai": {"command": "x"}}}')
    real = one.touch("dotfiles/gemini-settings.json")
    (one.home / ".gemini").mkdir()
    (one.home / ".gemini" / "settings.json").symlink_to(real)
    found = detect(one)
    assert found["selected"] == [] and found["blockers"] == []
    assert {item["client"]: item["code"] for item in found["excluded"]} == {
        "codex": "client_env_override", "claude-code": "client_cli_missing", "cursor": "client_entry_present",
        "gemini-cli": "client_path_unsafe"}


def test_linux_detection_honours_copilot_home_and_zed_xdg_like_the_product(tmp_path):
    one = OneLiner(tmp_path, host=UBUNTU)
    one.touch(".copilot/mcp-config.json")
    one.touch("cfg/zed/settings.json")
    one.environ["XDG_CONFIG_HOME"] = str(one.home / "cfg")
    found = detect(one)
    assert [(item["client"], item["path"]) for item in found["selected"]] == [
        ("vscode", product_path("vscode", one.home, "linux", {})[0]),
        ("zed", product_path("zed", one.home, "linux", {"XDG_CONFIG_HOME": str(one.home / "cfg")})[0])]
    assert found["selected"][1]["path"] == str(one.home / "cfg" / "zed" / "settings.json")
    for value in ("", "/somewhere"):
        one.environ["COPILOT_HOME"] = value
        found = detect(one)
        assert [item["client"] for item in found["selected"]] == ["zed"]
        vscode = [item for item in found["excluded"] if item["client"] == "vscode"]
        assert [item["code"] for item in vscode] == ["client_env_override"]
        assert vscode[0]["reason"] == product_path("vscode", one.home, "linux", {"COPILOT_HOME": value})[2]
    del one.environ["COPILOT_HOME"]
    for value, code in (("cfg", "client_xdg_invalid"), ("/elsewhere/config", "client_xdg_outside_home")):
        one.environ["XDG_CONFIG_HOME"] = value
        found = detect(one)
        zed = [item for item in found["excluded"] if item["client"] == "zed"]
        assert [item["code"] for item in zed] == [code]
    # windsurf and claude-desktop have no Linux path: never detected, listed as not configurable
    one.environ.pop("XDG_CONFIG_HOME")
    codes = {item["client"]: item["code"] for item in detect(one)["excluded"]}
    assert codes == {"claude-desktop": "client_unsupported_on_os", "windsurf": "client_unsupported_on_os"}


@pytest.mark.parametrize("clients,environ,code", [
    ("vscode", {"COPILOT_HOME": ""}, "client_env_override"),
    ("windsurf", {}, "client_unsupported_on_os"),
    ("claude-desktop", {}, "client_unsupported_on_os"),
    ("zed", {"XDG_CONFIG_HOME": "relative"}, "client_xdg_invalid"),
    ("nope", {}, "client_unknown"),
    ("codex,", {}, "client_unknown"),
    ("claude-code", {}, "client_cli_missing"),
])
def test_named_clients_that_cannot_be_configured_refuse_before_the_question_on_linux(tmp_path, clients, environ, code):
    one = OneLiner(tmp_path, host=UBUNTU)
    one.environ.update(environ)
    terminal = FakeTerminal("y")
    before = one.inventory()
    report, text = one.run("--cpython", "3.11", "--clients", clients, terminal=terminal)
    assert report["stage"] == "refused" and code in report["plan"]["blocker_codes"]
    assert terminal.opened == 0 and one.inventory() == before and "REFUSED" in text


# ---------------------------------------------------------------- preview, question, nothing written before y

def test_without_a_terminal_it_is_the_complete_preview_and_nothing_is_written(o):
    before = o.inventory()
    code, text = o.main(terminal=lambda: None)
    assert code == bs.EXIT_NOT_CONFIRMED == 3
    assert "No terminal" in text and "nothing written" in text
    for words in ("acquisitions", "wheel " + o.m.wheel.name, "bytes fetched by --apply", f"venv             : {o.home}",
                  f"{LABEL} on http://127.0.0.1:8765/mcp/", "manager launchd",
                  str(o.home / "Library/LaunchAgents" / (LABEL + ".plist")),
                  str(o.home / ".config/memory-os/http.token"), "created (0600), never printed",
                  str(o.home / ".codex/config.toml"), str(o.home / ".cursor/mcp.json"), str(o.home / ".memory-os-v4")):
        assert words in text, words
    assert o.inventory() == before and o.qs_calls() == []
    report, _ = o.run(terminal=lambda: None)
    assert report["stage"] == "no-terminal" and report["writes"] == "none (preview)" and report["steps"] == []
    assert [item["client"] for item in report["announced"]["clients"]] == ["codex", "cursor"]
    assert report["announced"]["profile"] == "lexical" and report["plan"]["profile"] == "lexical"


@pytest.mark.parametrize("answer", ["", "n", "N", "no", "yess", "y es", "oui", "ye", "\x1b", "yes please"])
def test_any_answer_but_y_or_yes_writes_nothing(o, answer):
    terminal = FakeTerminal(answer)
    before = o.inventory()
    code, text = o.main(terminal=terminal)
    assert code == 3 and terminal.written == [bs.CONFIRM_PROMPT] and "[y/N]" in bs.CONFIRM_PROMPT
    assert terminal.closed and "Not confirmed" in text and o.inventory() == before and o.qs_calls() == []


def test_a_cli_is_detected_only_through_an_absolute_path_of_a_non_empty_path(tmp_path):
    one = OneLiner(tmp_path)
    one.cli.update(codex="codex")                       # relative: what an empty PATH would give
    assert detect(one)["selected"] == []
    one.cli.update(codex="/opt/bin/codex")
    one.environ["PATH"] = ""
    assert detect(one)["selected"] == []
    one.environ["PATH"] = "/opt/bin"
    assert [item["client"] for item in detect(one)["selected"]] == ["codex"]


def test_ctrl_c_at_the_question_is_a_no(o):
    def interrupted():
        raise KeyboardInterrupt
    terminal = FakeTerminal("y", on_read=interrupted)
    before = o.inventory()
    code, text = o.main(terminal=terminal)
    assert code == 3 and terminal.closed and "Not confirmed" in text and o.inventory() == before


def test_only_y_or_yes_is_a_yes():
    assert all(bs.confirmed(answer) for answer in ("y", "Y", "yes", "YES", " yes ", "y\r"))
    assert not any(bs.confirmed(answer) for answer in ("", None, "n", "yy", "yes!", "ja", "o", "1", "true"))


def test_main_returns_zero_after_y_and_a_second_run_refuses_the_existing_venv(o):
    code, text = o.main()
    assert code == 0, text
    terminal = FakeTerminal("y")
    report, _ = o.run(terminal=terminal)  # a second run reuses nothing: the venv exists now
    assert report["stage"] == "refused" and "venv_exists" in report["plan"]["blocker_codes"] and terminal.opened == 0


def test_after_y_every_step_runs_in_order_with_exactly_the_announced_clients(o):
    report, text = o.run()
    assert report["ok"] is True and report["stage"] == "complete", report.get("error")
    assert [step["step"] for step in report["steps"]] == ["venv", "install", "quickstart_preview", "quickstart_apply",
                                                          "ready"]
    assert o.m.verbs() == ["--version", "--version", "venv --no-config", "pip install"]  # plan computed again after y
    calls = o.qs_calls()
    assert [call["mode"] for call in calls] == ["preview", "apply"]
    assert calls[0]["argv"] == ["quickstart", "--profile", "lexical", "--clients", "codex,cursor"]
    assert calls[1]["argv"] == ["quickstart", "--profile", "lexical", "--clients", "codex,cursor", "--apply"]
    for call in calls:  # the allow-listed environment, for the preview and the apply alike
        assert set(call["env"]) <= set(bs.CHILD_ENV_KEEP) | {"PYTHONDONTWRITEBYTECODE", "__CF_USER_TEXT_ENCODING",
                                                             "XDG_CONFIG_HOME"}
    assert o.ready_calls == [(bs.READY_URL, str(o.home / ".config/memory-os/http.token"), VERSION)]
    venv = o.home / ".memory-os" / "venv"
    admin = str(venv / "bin" / "memory-os-admin")
    assert shlex.split(report["next"]["undo"]) == [admin, "quickstart", "--undo", "20261126T101500Z-aaaa",
                                                   "--with-service"]
    assert shlex.split(report["next"]["doctor"]) == [admin, "doctor"]
    assert "installed and ready" in text and "doctor" in text and "--undo 20261126T101500Z-aaaa" in text
    saved = Path(report["report_path"])
    assert saved.parent == Path(report["plan"]["work_dir"]) and json.loads(saved.read_text())["ok"] is True
    assert "Bearer" not in saved.read_text() and "_quickstart_plan" not in saved.read_text()


def test_a_blocker_of_the_quickstart_preview_after_y_stops_before_apply(o):
    o.answers(preview=o.preview(["codex", "cursor"], ok=False, blockers=["port_or_label_occupied: synthetic"],
                                blocker_codes=["port_or_label_occupied"]))
    report, text = o.run()
    assert report["ok"] is False and report["code"] == "quickstart_blocked"
    assert [call["mode"] for call in o.qs_calls()] == ["preview"] and o.ready_calls == []
    assert [step["step"] for step in report["steps"]] == ["venv", "install", "quickstart_preview"]
    assert "is kept" in text and "--profile lexical --clients codex,cursor" in text


@pytest.mark.parametrize("label,change", [
    ("client path", lambda o, p: p["clients"][0].update(path=str(o.home / "elsewhere.toml"))),
    ("extra client", lambda o, p: p["clients"].append({"client": "zed", "path": "/x", "action": "create"})),
    ("missing client", lambda o, p: p["clients"].pop()),
    ("refused client", lambda o, p: p["clients"][1].update(action="refuse-existing-other")),
    ("service label", lambda o, p: p["service"].update(label="other.label")),
    ("service path", lambda o, p: p["service"].update(path="/elsewhere/x.plist")),
    ("service manager", lambda o, p: p["service"].update(manager="systemd-user")),
    ("service owned", lambda o, p: p["service"].update(state="owned")),
    ("port", lambda o, p: p.update(port=9999)),
    ("token", lambda o, p: p["token"].update(path="/elsewhere/token")),
    ("download", lambda o, p: p.update(downloads_required_bytes=1_681_126)),
    ("profile", lambda o, p: p.update(profile="semantic")),
    ("cell", lambda o, p: p.update(cell={"cell_id": "ubuntu2404-arm64-py311"})),
    ("unknown", lambda o, p: p.update(unknown_clients=["x"])),
    ("not a preview", lambda o, p: p.update(writes="something")),
])
def test_a_quickstart_preview_that_differs_from_the_announcement_stops_before_apply(o, label, change):
    preview = o.preview(["codex", "cursor"])
    change(o, preview)
    o.answers(preview=preview)
    report, text = o.run()
    assert report["code"] == "interactive_preview_mismatch", (label, report.get("error"))
    assert report["differences"] and "quickstart --apply was NOT run" in report["error"]
    assert [call["mode"] for call in o.qs_calls()] == ["preview"] and o.ready_calls == []
    assert "quickstart_apply" not in [step["step"] for step in report["steps"]]


def test_a_failed_quickstart_apply_names_its_undo_and_skips_ready(o):
    o.answers(apply={"ok": False, "stage": "service", "code": "service_bootstrap_failed", "error": "synthetic",
                     "stamp": "20261126T101500Z-bbbb"})
    report, text = o.run()
    assert report["code"] == "quickstart_apply_failed" and o.ready_calls == []
    assert report["quickstart_apply"]["stamp"] == "20261126T101500Z-bbbb"
    assert "--undo 20261126T101500Z-bbbb --with-service" in text


def test_a_ready_check_that_fails_is_reported_after_its_attempts(o):
    o.ready_answer = {"ok": False, "code": "ready_unreachable", "status": None}
    report, text = o.run()
    assert report["code"] == "ready_check_failed" and len(o.ready_calls) == bs.READY_ATTEMPTS
    assert "--undo 20261126T101500Z-aaaa" in text and report["ok"] is False


def test_a_change_between_the_preview_and_the_answer_stops_with_nothing_written(o):
    terminal = FakeTerminal("y", on_read=lambda: o.touch(".gemini/settings.json"))
    report, text = o.run(terminal=terminal)
    assert report["code"] == "preflight_changed" and report["steps"] == [] and o.qs_calls() == []
    assert not (o.home / ".memory-os" / "venv").exists() and not (o.home / ".memory-os").exists()


# ---------------------------------------------------------------- refusals happen before the question

def _with_pin(o, change):
    pin = json.loads(json.dumps(o.m.pin))
    change(pin)
    return pin


@pytest.mark.parametrize("label,setup,code", [
    ("template", lambda o: {"pin": json.loads(bs.RELEASE_PIN)}, "release_pin_unfilled"),
    ("semantic", lambda o: {"argv": ["--profile", "semantic"]}, "interactive_profile_lexical_only"),
    ("no client", lambda o: [p.unlink() for p in (o.home / ".codex/config.toml", o.home / ".cursor/mcp.json")]
     and {}, "no_client_detected"),
    ("store", lambda o: o.touch(".memory-os-v4/store.sqlite3", "x") and {}, "existing_installation"),
    ("service", lambda o: o.touch(f"Library/LaunchAgents/{LABEL}.plist", "x") and {}, "existing_installation"),
    ("venv", lambda o: (o.home / ".memory-os" / "venv").mkdir(parents=True) or {}, "venv_exists"),
    ("old sqlite", lambda o: setattr(o.m, "rewrite", (os.path.basename(sys.executable),
                                                      lambda seen: {**seen, "sqlite": [3, 50, 4]})) or {},
     "sqlite_runtime_too_old"),
    ("old uv", lambda o: o.m.configure(version_line="uv 0.13.8") or {}, "uv_too_old"),
    ("cell", lambda o: setattr(o.m, "host", {**MAC, "arch": "x86_64", "machine": "x86_64"}) or {}, "cell_not_retained"),
    ("no manager", lambda o: setattr(o.m, "host", {**MAC, "service_manager": {"kind": "launchd", "available": False,
                                                                             "attestation": "presence only"}}) or {},
     "service_manager_unavailable"),
    ("lock", lambda o: {"pin": _with_pin(o, lambda p: p["cells"].pop(CELL))}, "cell_lock_missing"),
])
def test_every_refusal_comes_before_the_question(o, label, setup, code):
    extra = setup(o) or {}
    terminal = FakeTerminal("y")
    before = o.inventory()
    rc, text = o.main(*extra.get("argv", []), terminal=terminal, pin=extra.get("pin"))
    assert rc == 1 and terminal.opened == 0, label
    assert code in text and "refused before any question" in text, label
    assert o.inventory() == before and o.qs_calls() == []


@pytest.mark.parametrize("flag", ["--apply", "--json", "--acquire-cpython"])
def test_interactive_excludes_the_script_route_flags(o, flag, capsys):
    rc = bs.main(["--uv", str(o.m.uv), "--interactive", flag], environ=o.environ, host=o.m.host,
                 terminal=FakeTerminal("y"))
    assert rc == 2 and "--interactive excludes" in capsys.readouterr().err and o.m.calls() == []


def test_the_quickstart_apply_command_approves_no_download():
    argv = bs.quickstart_command("/v", "lexical", "codex", apply=True, download_approved=False)
    assert argv[-1] == "--apply" and "--download-approved" not in argv
    assert bs.quickstart_command("/v", "lexical", "codex", apply=True)[-2:] == ["--apply", "--download-approved"]


# ---------------------------------------------------------------- terminal and /ready, for real

def test_the_terminal_is_read_and_written_directly(tmp_path):
    master, slave = os.openpty()
    try:
        terminal = bs.Terminal(slave)
        os.write(master, b"yes\n")
        assert terminal.readline() == "yes"
        terminal.write(bs.CONFIRM_PROMPT)
        assert os.read(master, 200).decode().endswith("[y/N] ")
    finally:
        os.close(master)
        os.close(slave)
    code = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('b', sys.argv[1]); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.open_terminal() is None)")
    done = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path(bs.__file__))], capture_output=True,
                          text=True, start_new_session=True, stdin=subprocess.DEVNULL, timeout=60)
    assert done.stdout.strip() == "True"  # a process without a controlling terminal gets the preview only


class _Ready(http.server.BaseHTTPRequestHandler):
    answer, status, seen = {}, 200, []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Authorization")))
        body = json.dumps(type(self).answer).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_check_ready_sends_the_token_in_the_header_only(tmp_path):
    token = tmp_path / "http.token"
    secret = "A" * 43
    token.write_text(secret + "\n")
    token.chmod(0o600)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ready)
    assert server.server_address[1] != 8765
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/ready"
    try:
        _Ready.answer, _Ready.status = {"ready": True, "reason": "ready", "version": VERSION}, 200
        result = bs.check_ready(url, str(token), expected_version=VERSION)
        assert result["ok"] is True and _Ready.seen[-1] == ("/ready", "Bearer " + secret)
        assert secret not in json.dumps(result)
        assert bs.check_ready(url, str(token), expected_version="9.9.9")["code"] == "ready_version_mismatch"
        _Ready.answer = {"ready": False, "reason": "work-only", "version": VERSION}
        assert bs.check_ready(url, str(token))["code"] == "ready_not_ready"
        _Ready.status = 401
        assert bs.check_ready(url, str(token))["code"] == "ready_refused"
        token.chmod(0o644)
        assert bs.check_ready(url, str(token))["code"] == "token_unreadable"
        token.chmod(0o600)
        link = tmp_path / "link.token"
        link.symlink_to(token)
        assert bs.check_ready(url, str(link))["code"] == "token_unreadable"
        bad = tmp_path / "bad.token"
        bad.write_text("short\n")
        bad.chmod(0o600)
        assert bs.check_ready(url, str(bad))["code"] == "token_unreadable"
    finally:
        server.shutdown()
        server.server_close()
    assert bs.check_ready(url, str(token), timeout=1)["code"] == "ready_unreachable"  # nothing listens any more


def test_the_options_namespace_is_never_mutated(o):
    options = o.options()
    snapshot = dict(vars(options))
    bs.interactive(options, pin=bs.load_pin(json.dumps(o.m.pin)), run=o.m.run, environ=o.environ, host=o.m.host,
                   out=io.StringIO(), terminal=lambda: None, ready=o.ready, which=o.which)
    assert vars(options) == snapshot and isinstance(options, argparse.Namespace)
