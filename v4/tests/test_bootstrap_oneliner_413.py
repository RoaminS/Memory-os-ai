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

from memory_os_v4.ops import clients as product_clients, provision as product_provision, quickstart as qs, \
    service as product_service
from memory_os_v4.ops.clients import ClientPathError
from memory_os_v4.ops.settings import OpsSettings
from setup_fixture_410 import tree
from test_bootstrap_411 import CELL, MAC, MINOR, UBUNTU, VERSION, Machine, bs, make_pin

REAL_PROVISION_PLAN = product_provision.plan  # captured before any fixture replaces it
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
    sys.exit(config.get("exit", {}).get(mode, 0 if answer.get("ok") else 1))
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


LEXICAL_RESOURCE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}  # provision.plan(cl100k), embedded table


def product_settings(home: Path) -> OpsSettings:
    """OpsSettings.from_env() for ``home`` with no MEMORY_V4_* variable (the quickstart run from the venv)."""
    return OpsSettings(url="http://127.0.0.1:8765/mcp/", token_file=home / ".config/memory-os/http.token",
                       store_dir=home / ".local/share/memory-os/store", backup_dir=home / "memory-os-backups",
                       backup_key=home / ".config/memory-os/backup.key", ops_dir=home / ".local/state/memory-os",
                       service_label=None)


def seal(preview: dict) -> dict:
    """The preview with the plan_digest the PRODUCT computes for it (what cli quickstart prints)."""
    preview = {key: value for key, value in preview.items() if key != "plan_digest"}
    return {**preview, "plan_digest": qs.plan_digest(preview)}


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

    def preview(self, clients, *, cell=CELL, **changes):
        """A quickstart preview built from the PRODUCT rules (clients.py, _service_plan, plan_digest)."""
        environ = {key: value for key, value in self.environ.items() if key in ("XDG_CONFIG_HOME", "COPILOT_HOME")}
        rows = [{"client": key, "path": product_path(key, self.home, self.os_kind, environ)[0], "action": "create",
                 "file_sha256": None, "desired_sha256": "d" * 64, "current_class": "absent", "ownership": "unproven"}
                for key in clients]
        python = str(self.home / ".memory-os" / "venv" / "bin" / "python")
        settings = product_settings(self.home)
        manager = product_service.Launchd() if self.os_kind == "darwin" else product_service.SystemdUser(
            {"HOME": str(self.home), **environ})
        service = qs._service_plan(settings, self.home, python, LEXICAL_RESOURCE_ENV, backend="sqlite",
                                   profile="lexical", manager=manager)
        service["loaded_state"] = "absent"
        answer = {"ok": True, "stage": "preview", "writes": "none (preview)", "blockers": [], "blocker_codes": [],
                  "profile": "lexical", "backend": "sqlite", "python": python, "cell": {"cell_id": cell},
                  "clients": rows, "unknown_clients": [], "service": service, "port": 8765,
                  "token": {"path": str(settings.token_file), "state": "absent"},
                  "client_env": {"MEMORY_V4_URL": settings.url, "MEMORY_V4_TOKEN_FILE": str(settings.token_file),
                                 "MEMORY_V4_OPS_DIR": str(settings.ops_dir)},
                  "identity": {"version": VERSION, "commit": "0" * 40}, "replace_existing": [],
                  "provision": {"resources": ["cl100k"], "ready": True, "download_bytes": 0, "table_source": "embedded",
                                "table_path": None, "files": [], "env": dict(LEXICAL_RESOURCE_ENV)},
                  "write_targets": qs.write_targets(settings, self.home),
                  "downloads_required_bytes": 0, "notices": []}
        answer.update(changes)
        return seal(answer)

    def answers(self, *, preview=None, apply=None, exit=None):
        path = self.root / "fakeqs.json"
        current = json.loads(path.read_text()) if path.exists() else {"preview": {}, "apply": {}}
        if preview is not None:
            current["preview"] = preview
        if apply is not None:
            current["apply"] = apply
        if exit is not None:
            current["exit"] = exit
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
    assert str(settings.store_dir) == str(tmp_path / bs.STORE_SUBPATH)  # the legacy default, before the selection
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
    assert "No terminal" in text and "the bootstrap wrote nothing" in text
    for words in (bs.WRITES_BEFORE_ANSWER, bs.CHECKED_BEFORE, bs.CHECKED_AFTER, "the only thing created in TMPDIR",
                  "the xcrun cache that Apple's python3 launcher may create", "removed at exit",
                  "nothing is written in HOME, in the store or in any client configuration, and no service",
                  f"interpreter      : {o.home}/.memory-os/venv/bin/python",
                  "service program  : " + bs.quote([str(o.home / ".memory-os/venv/bin/python"), "-m",
                                                    "memory_os_v4.server", "--transport", "http", "--host",
                                                    "127.0.0.1", "--port", "8765", "--warm"]),
                  "acquisitions", "wheel " + o.m.wheel.name, "bytes fetched by --apply", f"venv             : {o.home}",
                  f"{LABEL} on http://127.0.0.1:8765/mcp/", "manager launchd",
                  str(o.home / "Library/LaunchAgents" / (LABEL + ".plist")),
                  str(o.home / ".config/memory-os/http.token"), "created (0600), never printed",
                  str(o.home / ".codex/config.toml"), str(o.home / ".cursor/mcp.json"),
                  str(o.home / ".local/share/memory-os/store")):
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
    digest = json.loads((o.root / "fakeqs.json").read_text())["preview"]["plan_digest"]
    assert calls[1]["argv"] == ["quickstart", "--profile", "lexical", "--clients", "codex,cursor", "--apply",
                                "--expect-plan", digest]
    for call in calls:  # the allow-listed environment, for the preview and the apply alike
        assert set(call["env"]) <= set(bs.CHILD_ENV_KEEP) | {"PYTHONDONTWRITEBYTECODE", "__CF_USER_TEXT_ENCODING",
                                                             "XDG_CONFIG_HOME"}
    assert o.ready_calls == [(bs.READY_URL, str(o.home / ".config/memory-os/http.token"), VERSION)]
    venv = o.home / ".memory-os" / "venv"
    admin = str(venv / "bin" / "memory-os-admin")
    bound = ["env", *bs.installation_env(report["announced"])]
    assert shlex.split(report["next"]["undo"]) == [*bound, admin, "quickstart", "--undo", "20261126T101500Z-aaaa",
                                                   "--with-service"]
    assert shlex.split(report["next"]["doctor"]) == [*bound, admin, "doctor"]
    assert "installed and ready" in text and "doctor" in text and "--undo 20261126T101500Z-aaaa" in text
    assert not [name for name in tree(o.home) if "__pycache__" in name]  # quickstart commands run with -B
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


def _env(key, value):
    return lambda o, p: p["service"]["env"].update({key: value})


@pytest.mark.parametrize("label,change,words", [
    ("client path", lambda o, p: p["clients"][0].update(path=str(o.home / "elsewhere.toml")), "codex: path"),
    ("extra client", lambda o, p: p["clients"].append({"client": "zed", "path": "/x", "action": "create"}), "clients"),
    ("missing client", lambda o, p: p["clients"].pop(), "clients"),
    ("refused client", lambda o, p: p["clients"][1].update(action="refuse-existing-other"), "cursor: action"),
    ("service label", lambda o, p: p["service"].update(label="other.label"), "service 'other.label'"),
    ("service path", lambda o, p: p["service"].update(path="/elsewhere/x.plist"), "/elsewhere/x.plist"),
    ("service manager", lambda o, p: p["service"].update(manager="systemd-user"), "service manager"),
    ("service owned", lambda o, p: p["service"].update(state="owned"), "service state 'owned'"),
    ("job loaded", lambda o, p: p["service"].update(loaded_state="loaded"), "service job state"),
    ("service program", lambda o, p: p["service"]["argv"].__setitem__(0, "/other/python"), "service program"),
    ("service port arg", lambda o, p: p["service"]["argv"].__setitem__(-2, "9999"), "service program"),
    ("store", _env("MEMORY_V4_DIR", "/elsewhere/store"), "service environment differs on MEMORY_V4_DIR"),
    ("ops dir", _env("MEMORY_V4_OPS_DIR", "/elsewhere/ops"), "differs on MEMORY_V4_OPS_DIR"),
    ("service token", _env("MEMORY_V4_TOKEN_FILE", "/elsewhere/t"), "differs on MEMORY_V4_TOKEN_FILE"),
    ("service label env", _env("MEMORY_V4_SERVICE_LABEL", "x"), "differs on MEMORY_V4_SERVICE_LABEL"),
    ("extra env", _env("MEMORY_V4_EMBED_MODEL", "x"), "differs on MEMORY_V4_EMBED_MODEL"),
    ("port", lambda o, p: p.update(port=9999), "port 9999"),
    ("token", lambda o, p: p["token"].update(path="/elsewhere/token"), "token '/elsewhere/token'"),
    ("download", lambda o, p: p.update(downloads_required_bytes=1_681_126), "resource downloads"),
    ("profile", lambda o, p: p.update(profile="semantic"), "profile 'semantic'"),
    ("cell", lambda o, p: p.update(cell={"cell_id": "ubuntu2404-arm64-py311"}), "cell"),
    ("unknown", lambda o, p: p.update(unknown_clients=["x"]), "clients"),
    ("not a preview", lambda o, p: p.update(writes="something"), "not a quickstart preview"),
    ("interpreter", lambda o, p: p.update(python="/other/python"), "interpreter '/other/python'"),
    ("backend", lambda o, p: p.update(backend="jsonl"), "backend 'jsonl'"),
    ("client env", lambda o, p: p["client_env"].update(MEMORY_V4_OPS_DIR="/elsewhere"), "client environment"),
])
def test_a_quickstart_preview_that_differs_from_the_announcement_stops_before_apply(o, label, change, words):
    preview = o.preview(["codex", "cursor"])
    change(o, preview)
    o.answers(preview=seal(preview))  # a coherent digest: only the announced property differs
    report, text = o.run()
    assert report["code"] == "interactive_preview_mismatch", (label, report.get("error"))
    assert any(words in item for item in report["differences"]), (label, report["differences"])
    assert not any("plan_digest" in item for item in report["differences"]), label
    assert "quickstart --apply was NOT run" in report["error"]
    assert [call["mode"] for call in o.qs_calls()] == ["preview"] and o.ready_calls == []
    assert "quickstart_apply" not in [step["step"] for step in report["steps"]]


@pytest.mark.parametrize("label,change", [
    ("absent", lambda p: p.pop("plan_digest")),
    ("forged", lambda p: p.update(plan_digest="e" * 64)),
    ("not hex", lambda p: p.update(plan_digest="digest")),
    ("stale", lambda p: p["clients"][0].update(file_sha256="f" * 64)),  # content changed, digest kept
])
def test_a_preview_whose_digest_is_not_its_own_stops_before_apply(o, label, change):
    preview = o.preview(["codex", "cursor"])
    change(preview)
    o.answers(preview=preview)
    report, _ = o.run()
    assert report["code"] == "interactive_preview_mismatch", label
    assert any("plan_digest" in item for item in report["differences"]), label
    assert [call["mode"] for call in o.qs_calls()] == ["preview"]


def test_an_ok_preview_with_a_failing_process_is_not_trusted(o):
    o.answers(exit={"preview": 3})
    report, _ = o.run()
    assert report["code"] == "quickstart_returncode" and [call["mode"] for call in o.qs_calls()] == ["preview"]
    assert o.ready_calls == [] and "quickstart_apply" not in [step["step"] for step in report["steps"]]


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


# ---------------------------------------------------------------- round 2 (Codex r1 of the one-liner, decisions of Romain)

class RecordingOut(io.StringIO):
    """stdout that remembers how much had been flushed (a pipe or ``tee`` shows only that much)."""

    flushed = 0

    def flush(self):
        super().flush()
        self.flushed = len(self.getvalue())


def test_the_whole_preview_is_flushed_before_the_question(o):
    out = RecordingOut()
    seen = {}

    class Watching(FakeTerminal):
        def write(self, text):
            seen["flushed"], seen["total"] = out.flushed, len(out.getvalue())
            super().write(text)
    bs.interactive(o.options(), pin=bs.load_pin(json.dumps(o.m.pin)), run=o.m.run, environ=o.environ, host=o.m.host,
                   out=out, terminal=Watching("n"), ready=o.ready, which=o.which)
    assert seen["total"] > 0 and seen["flushed"] == seen["total"]
    assert "ONE-COMMAND INSTALL" in out.getvalue()[:seen["flushed"]]


def test_follow_up_commands_name_the_announced_installation(o, monkeypatch):
    o.environ.update(MEMORY_V4_SERVICE_LABEL="other.label", MEMORY_V4_DIR="/elsewhere/store",
                     MEMORY_V4_URL="http://127.0.0.1:9999/mcp/", MEMORY_V4_TOKEN_FILE="/elsewhere/token",
                     MEMORY_V4_OPS_DIR="/elsewhere/ops")
    report, text = o.run()
    assert report["ok"] is True, report.get("error")
    announced = report["announced"]
    for name in ("doctor", "undo", "uninstall"):
        words = shlex.split(report["next"][name].split(" (preview)")[0])
        assert words[0] == "env" and words[1:6] == bs.installation_env(announced), name
        assert words[1:6] == [f"MEMORY_V4_SERVICE_LABEL={LABEL}", "MEMORY_V4_URL=http://127.0.0.1:8765/mcp/",
                              f"MEMORY_V4_DIR={o.home}/.local/share/memory-os/store",
                              f"MEMORY_V4_TOKEN_FILE={o.home}/.config/memory-os/http.token",
                              f"MEMORY_V4_OPS_DIR={o.home}/.local/state/memory-os"]
        assert report["next"][name] in text
    # what the product reads from exactly those assignments is the announced installation
    for key in [key for key in os.environ if key.startswith("MEMORY_V4_")]:
        monkeypatch.delenv(key)
    for assignment in bs.installation_env(announced):
        key, value = assignment.split("=", 1)
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOME", str(o.home))
    settings = OpsSettings.from_env()
    assert (settings.service_label, settings.url, str(settings.store_dir), str(settings.token_file),
            str(settings.ops_dir)) == (LABEL, bs.SERVICE_URL, announced["store"], announced["token"]["path"],
                                       announced["ops_dir"])


def test_the_manual_continuation_after_a_stop_is_bound_too(o):
    o.answers(preview=o.preview(["codex"]))  # differs: cursor was announced
    report, text = o.run()
    assert report["code"] == "interactive_preview_mismatch"
    assert "env MEMORY_V4_SERVICE_LABEL=local.memory-os-v4 MEMORY_V4_URL=" in text


def test_the_standalone_cpython_is_announced_before_the_question_and_fetched_only_after_y(tmp_path):
    one = OneLiner(tmp_path)
    one.m.pin = make_pin(one.m.wheel, cells=tuple(bs.CELLS))
    one.touch(".cursor/mcp.json")
    one.answers(preview=one.preview(["cursor"], cell="macos-arm64-py313"))
    terminal = FakeTerminal("n")
    report, text = one.run("--cpython", "3.13", terminal=terminal)
    asked = text.index("No terminal") if "No terminal" in text else len(text)
    line = next(item for item in text[:asked].splitlines() if item.startswith("  download after y"))
    assert "CPython 3.13.18" in line and "25600000 bytes (about 25.6 MB)" in line and bs.STANDALONE_SOURCE in line
    assert str(one.home / ".local/share/uv/python") in line
    assert "| after y (skipped by uv when already installed)" in text
    assert report["stage"] == "declined" and one.m.verbs() == ["--version"]  # nothing acquired without y
    # with y: the acquisition happens after the answer, then the attestation of the downloaded build
    one.m.rewrite = ("venv/bin/python", lambda seen: {**seen, "python": [3, 13, 18], "sqlite": [3, 53, 1],
                                                      "build": "20261110", "standalone": True,
                                                      "sys_version": "3.13.18 (main) [Clang]"})
    calls_at_answer = []
    report, text = one.run("--cpython", "3.13", terminal=FakeTerminal(
        "y", on_read=lambda: calls_at_answer.append(list(one.m.verbs()))))
    assert calls_at_answer == [["--version", "--version"]]  # only uv --version before the answer
    assert one.m.verbs()[2:5] == ["--version", "python install", "venv --no-config"]
    assert report["ok"] is True, report.get("error")
    assert report["apply"]["standalone"]["build_state"] == "attested"


def test_ubuntu_announces_its_standalone_cpython_with_its_size(tmp_path):
    one = OneLiner(tmp_path, host=UBUNTU)
    one.m.pin = make_pin(one.m.wheel, cells=tuple(bs.CELLS))
    one.touch(".cursor/mcp.json")
    report, text = one.run("--cpython", "3.11", terminal=lambda: None)
    assert report["stage"] == "no-terminal", report["plan"]["blockers"]
    line = next(item for item in text.splitlines() if item.startswith("  download after y"))
    assert "CPython 3.11.18" in line and "49500000 bytes (about 49.5 MB)" in line
    assert "manager systemd-user" in text and one.m.verbs() == ["--version"]


def test_the_semantic_refusal_points_to_the_readme_section(o):
    report, text = o.run("--profile", "semantic", terminal=FakeTerminal("y"))
    assert "interactive_profile_lexical_only" in report["plan"]["blocker_codes"]
    assert "'Semantic profile (recommended)'" in text
    readme = (Path(bs.__file__).resolve().parents[1] / "README.md").read_text()
    assert "**Semantic profile (recommended)**" in readme


# ---- the product side of the binding: plan_digest and --expect-plan

def test_the_bootstrap_digest_is_the_product_digest_of_a_real_plan(c):
    from setup_fixture_410 import prepare
    plan = prepare(c, profile="lexical")
    assert plan["ok"], plan["blockers"]
    printed = json.loads(json.dumps({**plan, "plan_digest": qs.plan_digest(plan)}, ensure_ascii=False))
    assert bs.quickstart_plan_digest(printed) == qs.plan_digest(plan) == printed["plan_digest"]
    assert (bs.PLAN_DIGEST_VERSION, bs.PLAN_DIGEST_CLIENT_KEYS, bs.PLAN_DIGEST_SERVICE_KEYS) == \
        (qs.PLAN_DIGEST_VERSION, qs.PLAN_DIGEST_CLIENT_KEYS, qs.PLAN_DIGEST_SERVICE_KEYS)


@pytest.mark.parametrize("label,change", [
    ("service owned", lambda p: p["service"].update(state="owned")),
    ("job loaded", lambda p: p["service"].update(loaded_state="loaded")),
    ("service argv", lambda p: p["service"]["argv"].append("--other")),
    ("service env", lambda p: p["service"]["env"].update(MEMORY_V4_DIR="/elsewhere")),
    ("definition", lambda p: p["service"].update(path="/elsewhere.plist")),
    ("client file", lambda p: p["clients"][0].update(file_sha256="f" * 64)),
    ("client path", lambda p: p["clients"][0].update(path="/elsewhere.json")),
    ("client action", lambda p: p["clients"][0].update(action="replace")),
    ("token", lambda p: p["token"].update(path="/elsewhere/token")),
    ("profile", lambda p: p.update(profile="semantic")),
    ("python", lambda p: p.update(python="/other/python")),
    ("download", lambda p: p.update(downloads_required_bytes=1)),
    ("consent", lambda p: p.update(replace_existing=["cursor"])),
])
def test_the_plan_digest_moves_with_every_written_property(c, label, change):
    from setup_fixture_410 import prepare
    plan = prepare(c, profile="lexical")
    other = json.loads(json.dumps(plan))
    change(other)
    assert qs.plan_digest(other) != qs.plan_digest(plan), label
    unchanged = json.loads(json.dumps(plan))
    unchanged.update(created_at=0, disk_free_bytes=1, port_state="unknown")  # volatile facts are outside
    assert qs.plan_digest(unchanged) == qs.plan_digest(plan)


def test_apply_refuses_a_plan_that_is_not_the_accepted_one_before_any_write(c):
    from memory_os_v4.ops.doctor import Check
    from setup_fixture_410 import fake_probe, prepare
    plan = prepare(c, profile="lexical")
    before = tree(c.home)
    with pytest.raises(qs.QuickstartError) as refused:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system, probe=fake_probe, clock=c.clock,
                         doctor_run=lambda s: [Check("server", "PASS", "synthetic")], expected_plan="0" * 64)
    assert refused.value.code == "plan_digest_mismatch" and "nothing written" in str(refused.value)
    assert tree(c.home) == before and not (c.home / qs.JOURNAL_SUBDIR).exists()
    result = qs.apply_install(plan, c.settings, home=c.home, run=c.system, probe=fake_probe, clock=c.clock,
                              doctor_run=lambda s: [Check("server", "PASS", "synthetic")],
                              expected_plan=qs.plan_digest(plan))
    assert result["ok"] is True, result.get("error")


def test_the_cli_prints_the_digest_and_hands_expect_plan_to_apply(monkeypatch, capsys):
    from memory_os_v4.ops import cli
    plan = {"ok": True, "profile": "lexical", "clients": [], "service": {"label": LABEL}, "blockers": []}
    seen = {}
    monkeypatch.setattr(qs, "settings_for_install", lambda s, backend=None: (s, "sqlite"))
    monkeypatch.setattr(qs, "prepare", lambda *a, **k: plan)

    def apply_install(p, s, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "stage": "complete"}
    monkeypatch.setattr(qs, "apply_install", apply_install)
    args = argparse.Namespace(undo=None, clients="cursor", backend=None, profile="lexical", replace_existing=None,
                              apply=False, report=False, download_approved=False, demo=False, expect_plan=None)
    assert cli.cmd_quickstart(args, None) == 0
    assert json.loads(capsys.readouterr().out)["plan_digest"] == qs.plan_digest(plan)
    args.apply = True
    assert cli.cmd_quickstart(args, None) == 0 and "expected_plan" not in seen  # unchanged call without the option
    args.expect_plan = "a" * 64
    assert cli.cmd_quickstart(args, None) == 0 and seen["expected_plan"] == "a" * 64
    capsys.readouterr()
    for argv in (["quickstart", "--profile", "lexical", "--expect-plan", "a" * 64],
                 ["quickstart", "--profile", "lexical", "--apply", "--expect-plan", "not-a-digest"]):
        with pytest.raises(SystemExit) as refused:
            cli.main(argv)
        assert refused.value.code == 2 and "--expect-plan" in capsys.readouterr().err


@pytest.fixture
def c(tmp_path, monkeypatch):
    from setup_fixture_410 import setup_context
    return setup_context(tmp_path, monkeypatch)


# ---------------------------------------------------------------- round 3 (Codex r2 of the one-liner)

def _clean_product_env(monkeypatch, home):
    for key in [key for key in os.environ if key.startswith("MEMORY_V4_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(home))


@pytest.mark.parametrize("legacy", [False, True])
def test_the_announced_store_is_the_one_quickstart_really_selects(tmp_path, monkeypatch, legacy):
    home = tmp_path / "home"
    home.mkdir()
    if legacy:
        (home / ".memory-os-v4").mkdir()
    _clean_product_env(monkeypatch, home)
    selected, backend = qs.settings_for_install(OpsSettings.from_env(), home=home)
    assert backend == "sqlite" and str(selected.store_dir) == bs.store_directory(str(home))
    assert bs.store_directory(str(home)) == str(home / (".memory-os-v4" if legacy else ".local/share/memory-os/store"))


def _announce(home, *, clients, python, host=MAC):
    options = argparse.Namespace(clients=clients, profile="lexical")
    extras = bs.interactive_extras(options, host=host, environ={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                                   which=lambda name, path=None: None)
    announced = extras["announced"]
    announced["python"] = python
    announced["service"].update(bs.expected_service(python=python, store=announced["store"],
                                                    ops_dir=announced["ops_dir"], token=announced["token"]["path"]))
    return extras


def test_end_to_end_a_fresh_home_real_selection_real_prepare_and_the_comparison_agree(c, monkeypatch):
    """The real chain of the CLI on a fresh HOME: from_env, settings_for_install, prepare with the real resource
    plan, the printed preview; the bootstrap's announcement for the same HOME compares equal to it."""
    from memory_os_v4.ops import provision
    monkeypatch.setattr(provision, "plan", REAL_PROVISION_PLAN)
    _clean_product_env(monkeypatch, c.home)
    settings, backend = qs.settings_for_install(OpsSettings.from_env(), home=c.home)
    plan = qs.prepare(settings, home=c.home, python=c.python, clients=("cursor",), run=c.system, profile="lexical",
                      backend=backend)
    assert plan["ok"], plan["blockers"]
    printed = json.loads(json.dumps({**plan, "plan_digest": qs.plan_digest(plan)}, ensure_ascii=False))
    extras = _announce(c.home, clients="cursor", python=c.python)
    assert extras["blockers"] == [] and extras["announced"]["store"] == str(c.home / ".local/share/memory-os/store")
    cell_id = (plan.get("cell") or {}).get("cell_id")
    assert bs.compare_preview(printed, extras["announced"], cell_id=cell_id) == []
    # the regression this test pins: announcing the legacy store makes the comparison refuse
    extras["announced"]["store"] = str(c.home / ".memory-os-v4")
    extras["announced"]["service"].update(bs.expected_service(python=c.python, store=extras["announced"]["store"],
                                                              ops_dir=extras["announced"]["ops_dir"],
                                                              token=extras["announced"]["token"]["path"]))
    assert any("MEMORY_V4_DIR" in item for item in bs.compare_preview(printed, extras["announced"], cell_id=cell_id))


def _write_token(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("T" * 43 + "\n")
    path.chmod(0o600)


def test_a_token_removed_between_preview_and_apply_is_refused_before_any_write(c):
    from memory_os_v4.ops.doctor import Check
    from setup_fixture_410 import fake_probe, prepare
    _write_token(c.settings.token_file)
    plan = prepare(c, profile="lexical")
    assert plan["token"]["state"] == "ok" and plan["write_targets"]["token"]["kind"] == "file"
    accepted = qs.plan_digest(plan)
    c.settings.token_file.unlink()
    again = prepare(c, profile="lexical")
    assert qs.plan_digest(again) != accepted
    before = tree(c.home)
    for applied in (plan, again):  # the plan of the apply process, or the one the caller accepted
        with pytest.raises(qs.QuickstartError) as refused:
            qs.apply_install(applied, c.settings, home=c.home, run=c.system, probe=fake_probe, clock=c.clock,
                             doctor_run=lambda s: [Check("server", "PASS", "synthetic")], expected_plan=accepted)
        assert refused.value.code == "plan_digest_mismatch"
    assert tree(c.home) == before


def test_a_token_whose_identity_changed_moves_the_digest(c):
    from setup_fixture_410 import prepare
    _write_token(c.settings.token_file)
    first = qs.plan_digest(prepare(c, profile="lexical"))
    c.settings.token_file.unlink()
    _write_token(c.settings.token_file)  # same value, another file
    assert qs.plan_digest(prepare(c, profile="lexical")) != first
    assert "T" * 43 not in json.dumps(prepare(c, profile="lexical"))  # never its value


def test_the_semantic_manual_route_with_refs_main_removed_has_another_digest(tmp_path, monkeypatch):
    """Codex r2: same zero download bytes, but the ref and provenance writes differ: the digest must differ."""
    from memory_os_v4.ops import provision
    monkeypatch.delenv("MEMORY_V4_CL100K_PATH", raising=False)
    monkeypatch.setattr(provision, "matches", lambda path, record: True)  # every model file present
    record = provision.catalog()
    ref = tmp_path / ("models--" + record["model_id"].replace("/", "--")) / "refs" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(record["revision"])
    with_ref = REAL_PROVISION_PLAN(resources=("granite", "cl100k"), cache_dir=tmp_path)
    ref.unlink()
    without_ref = REAL_PROVISION_PLAN(resources=("granite", "cl100k"), cache_dir=tmp_path)
    assert with_ref["download_bytes"] == without_ref["download_bytes"] == 0
    assert with_ref["ready"] is True and without_ref["ready"] is False
    base = {"profile": "semantic", "service": {}, "clients": []}
    assert qs.plan_digest({**base, "provision": with_ref}) != qs.plan_digest({**base, "provision": without_ref})


@pytest.mark.parametrize("label,change", [
    ("token state", lambda p: p["token"].update(state="ok")),
    ("token identity", lambda p: p["write_targets"].update(token={"kind": "file", "mode": 0o600, "ino": 1})),
    ("store", lambda p: p["write_targets"].update(store={"kind": "directory", "entries": 3})),
    ("ops dir", lambda p: p["write_targets"].update(ops_dir={"kind": "directory", "entries": 1})),
    ("journals", lambda p: p["write_targets"].update(journals={"kind": "directory", "entries": 1})),
    ("provision ready", lambda p: p["provision"].update(ready=False)),
    ("provision files", lambda p: p["provision"].update(files=[{"target": "/x", "present": False}])),
    ("provision env", lambda p: p["provision"]["env"].update(HF_HUB_CACHE="/x")),
])
def test_the_digest_covers_token_resources_and_every_write_target(c, label, change):
    from setup_fixture_410 import prepare
    plan = json.loads(json.dumps(prepare(c, profile="lexical")))
    other = json.loads(json.dumps(plan))
    change(other)
    assert qs.plan_digest(other) != qs.plan_digest(plan), label
    assert bs.quickstart_plan_digest(other) == qs.plan_digest(other), label


@pytest.mark.parametrize("label,change,words", [
    ("token reused", lambda o, p: p["token"].update(state="ok"), "token state 'ok'"),
    ("token bad mode", lambda o, p: p["token"].update(state="bad-permissions:0o644"), "token state"),
    ("token file", lambda o, p: p["write_targets"].update(token={"kind": "file", "mode": 0o600, "nlink": 1}),
     "token file identity"),
    ("store used", lambda o, p: p["write_targets"].update(store={"kind": "directory", "entries": 2}), "store is not new"),
    ("store file", lambda o, p: p["write_targets"].update(store={"kind": "file"}), "store is not new"),
    ("download", lambda o, p: p["provision"].update(ready=False), "resource plan"),
    ("table", lambda o, p: p["provision"].update(table_source="download"), "resource plan"),
    ("model", lambda o, p: p["provision"].update(resources=["granite", "cl100k"]), "resource plan"),
])
def test_the_comparison_covers_token_state_store_and_resources(o, label, change, words):
    preview = o.preview(["codex", "cursor"])
    change(o, preview)
    o.answers(preview=seal(preview))
    report, _ = o.run()
    assert report["code"] == "interactive_preview_mismatch", label
    assert any(words in item for item in report["differences"]), (label, report["differences"])
    assert [call["mode"] for call in o.qs_calls()] == ["preview"]


class FailingOut(io.StringIO):
    def __init__(self, fail):
        super().__init__()
        self.fail = fail

    def write(self, text):
        if self.fail == "write":
            raise BrokenPipeError(32, "Broken pipe")
        return super().write(text)

    def flush(self):
        if self.fail == "flush":
            raise OSError(5, "Input/output error")


@pytest.mark.parametrize("fail", ["write", "flush"])
def test_a_preview_that_cannot_be_written_out_asks_nothing(o, fail, capsys):
    terminal = FakeTerminal("y")
    before = o.inventory()
    code = bs.main(["--uv", str(o.m.uv), "--interactive"], pin=bs.load_pin(json.dumps(o.m.pin)), run=o.m.run,
                   environ=o.environ, host=o.m.host, out=FailingOut(fail), terminal=terminal, ready=o.ready,
                   which=o.which)
    assert code == 1 and terminal.opened == 0 and o.qs_calls() == [] and o.inventory() == before
    assert "preview_output_failed" in capsys.readouterr().err


def test_the_quickstart_commands_never_write_bytecode():
    for argv in (bs.quickstart_command("/v", "lexical", "codex"),
                 bs.quickstart_command("/v", "lexical", "codex", apply=True, download_approved=False,
                                       expect_plan="a" * 64)):
        assert argv[1:4] == ["-I", "-B", "-m"]


# ---------------------------------------------------------------- round 4 (Codex r3 of the one-liner)

@pytest.mark.parametrize("where", ["ops", "logs", "journals"])
def test_a_work_directory_inside_the_state_directory_does_not_invalidate_the_digest(c, where):
    """Codex r3 #2: the bootstrap creates its work directory, previews, then writes report.json there before
    quickstart --apply: the digest of the apply process must still be the accepted one."""
    from setup_fixture_410 import prepare
    work = {"ops": c.settings.ops_dir, "logs": c.settings.ops_dir / "logs",
            "journals": c.home / qs.JOURNAL_SUBDIR}[where]
    work.mkdir(parents=True, mode=0o700)                     # created by the bootstrap before the preview
    (work / "requirements-x.txt").write_text("x\n")
    (work / "uv-cache").mkdir()
    accepted = qs.plan_digest(prepare(c, profile="lexical"))
    (work / "report.json").write_text("{}")                 # written by the bootstrap after the preview
    assert qs.plan_digest(prepare(c, profile="lexical")) == accepted


@pytest.mark.parametrize("setup", ["empty", "file", "dangling link", "with data"])
def test_an_existing_legacy_store_is_refused_before_the_question_and_never_touched(o, setup):
    legacy = o.home / ".memory-os-v4"
    if setup == "empty":
        legacy.mkdir()
    elif setup == "file":
        legacy.write_text("x")
    elif setup == "dangling link":
        legacy.symlink_to(o.root / "nowhere")
    else:
        legacy.mkdir()
        (legacy / "items.jsonl").write_text("{}\n")
    terminal = FakeTerminal("y")
    before = o.inventory()
    report, text = o.run(terminal=terminal)
    assert report["stage"] == "refused" and "legacy_store_present" in report["plan"]["blocker_codes"]
    assert terminal.opened == 0 and o.inventory() == before and o.qs_calls() == []
    assert "never created there" in text


def test_a_new_store_resolving_into_the_legacy_one_is_refused(tmp_path):
    one = OneLiner(tmp_path)
    one.touch(".cursor/mcp.json")
    elsewhere = tmp_path / "legacy-target"
    elsewhere.mkdir()
    (one.home / ".local" / "share" / "memory-os").mkdir(parents=True)
    (one.home / ".local" / "share" / "memory-os" / "store").symlink_to(one.home / "Memory-os-ai",
                                                                        target_is_directory=True)
    (one.home / "Memory-os-ai").mkdir()
    report, _ = one.run(terminal=FakeTerminal("y"))
    assert "store_path_forbidden" in report["plan"]["blocker_codes"]


@pytest.mark.parametrize("name", ["--work-dir", "--venv"])
def test_a_work_directory_or_venv_inside_the_store_is_refused(o, name):
    terminal = FakeTerminal("y")
    report, _ = o.run(name, str(o.home / ".local/share/memory-os/store/inside"), terminal=terminal)
    assert "destination_in_store" in report["plan"]["blocker_codes"] and terminal.opened == 0


@pytest.mark.parametrize("where", [".local/state/memory-os", ".local/state/memory-os/logs",
                                   ".local/state/memory-os/quickstart"])
def test_a_work_directory_inside_the_state_directory_is_accepted(o, where):
    report, _ = o.run("--work-dir", str(o.home / where))
    assert report["ok"] is True, report.get("error") or report["plan"]["blockers"]
    assert (o.home / where / "report.json").exists()


def test_a_store_that_fills_between_preview_and_apply_moves_the_digest(c):
    """The store's emptiness IS a write decision: its entries stay in the digest (unlike ops and journals)."""
    from setup_fixture_410 import prepare
    c.settings.store_dir.mkdir(parents=True)
    plan = prepare(c, profile="lexical")
    assert plan["write_targets"]["store"]["entries"] == 0
    accepted = qs.plan_digest(plan)
    (c.settings.store_dir / "store.sqlite3").write_bytes(b"")
    assert qs.plan_digest(prepare(c, profile="lexical")) != accepted


# ---------------------------------------------------------------- round 5 (Codex r4 of the one-liner)

@pytest.mark.parametrize("option,relative,code", [
    ("--work-dir", ".memory-os-v4/build", "destination_in_legacy_store"),        # Codex r4 counter-example 2
    ("--venv", ".memory-os-v4/venv", "destination_in_legacy_store"),
    ("--venv", ".memory-os-v4", "destination_in_legacy_store"),
    ("--work-dir", ".local/share/memory-os/store/build", "destination_in_store"),
])
def test_no_bootstrap_destination_may_create_the_historical_or_the_new_store(o, option, relative, code):
    terminal = FakeTerminal("y")
    before = o.inventory()
    report, _ = o.run(option, str(o.home / relative), terminal=terminal)
    assert code in report["plan"]["blocker_codes"] and terminal.opened == 0
    assert o.inventory() == before and not (o.home / ".memory-os-v4").exists()


@pytest.mark.parametrize("relative,code", [
    (".local/share/memory-os/store/python", "destination_in_store"),             # Codex r4 counter-example 1
    (".memory-os-v4/python", "destination_in_legacy_store"),
])
def test_the_uv_python_directory_may_not_lie_in_a_store(tmp_path, relative, code):
    one = OneLiner(tmp_path)
    one.m.pin = make_pin(one.m.wheel, cells=tuple(bs.CELLS))
    one.touch(".cursor/mcp.json")
    one.environ["UV_PYTHON_INSTALL_DIR"] = str(one.home / relative)
    terminal = FakeTerminal("y")
    report, _ = one.run("--cpython", "3.13", terminal=terminal)
    assert code in report["plan"]["blocker_codes"] and terminal.opened == 0
    assert one.m.verbs() == ["--version"]                                       # nothing acquired


def test_a_link_into_the_historical_location_is_refused_too(o):
    (o.home / "build-link").symlink_to(o.home / ".memory-os-v4", target_is_directory=True)  # dangling: absent store
    report, _ = o.run("--work-dir", str(o.home / "build-link" / "w"), terminal=FakeTerminal("y"))
    assert "destination_in_legacy_store" in report["plan"]["blocker_codes"]


def test_the_probes_forward_the_redirected_xcrun_cache_only():
    environ = {"HOME": "/h", "PATH": "/usr/bin", "xcrun_db": "/w/memory-os-install.x/xcrun_db", "xcrun_log": "1"}
    assert bs.child_environment(environ)["xcrun_db"] == "/w/memory-os-install.x/xcrun_db"
    assert "xcrun_log" not in bs.child_environment(environ)
    assert "xcrun_db" not in bs.uv_environment(environ)
