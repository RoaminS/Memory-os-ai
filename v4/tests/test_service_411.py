"""CONTRAT_4_1_1 §5, §7 (P5), §8: the service interface, ``systemd --user``, undo option (b), uninstall.

No network and no real service: ``launchctl`` and ``systemctl`` are either the in-process
``FakeSystem`` of the 4.1.0 setup fixture, or SCRIPTS written into a temporary directory that is
the whole ``PATH`` of the test (they record their argv and answer like ``print`` / ``show``,
with delays and failures on demand). ``loginctl`` exists nowhere: running it would fail the test.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_os_v4.ops import backup, cell, doctor, qualified_runtime, service as managers
from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.settings import OpsSettings
from setup_fixture_410 import (
    PATHS, SYSTEMD_SHOW_LOADED, SYSTEMD_SHOW_NOT_FOUND, data_inventory, install, plant_data, prepare, servers,
    setup_context, tree,
)

UBUNTU = 'ID=ubuntu\nVERSION_ID="24.04"\n'
UNIT = ".config/systemd/user/test.memory-os.service"
PLIST = "Library/LaunchAgents/test.memory-os.plist"
STATE = ".local/state/memory-os"


def linux_cell(**overrides) -> dict:
    facts = {"sys_platform": "linux", "machine": "aarch64", "python_version": (3, 11, 18), "implementation": "CPython",
             "sqlite_version": "3.53.1", "os_release": UBUNTU, "kernel_release": "6.8.0-88-generic\n",
             "environ": {"XDG_RUNTIME_DIR": "/run/user/1000"}, "which": lambda name: "/usr/bin/" + name,
             "exists": lambda path: str(path) in {"/run/systemd/system", "/run/user/1000/systemd"}}
    facts.update(overrides)
    return cell.detect(**facts)


def mac_cell(**overrides) -> dict:
    facts = {"sys_platform": "darwin", "machine": "arm64", "python_version": (3, 11, 14), "implementation": "CPython",
             "sqlite_version": "3.53.4", "os_release": None, "kernel_release": None, "environ": {},
             "which": lambda name: None, "exists": lambda path: str(path) == "/bin/launchctl"}
    facts.update(overrides)
    return cell.detect(**facts)


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


@pytest.fixture
def ubuntu(c, monkeypatch):
    """The fake machine of the setup fixture, seen as the retained Linux cell with its lock embedded."""
    report = linux_cell()
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
    monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {
        "catalog": "synthetic", "constraints": "synthetic", "profile_id": f"{profile}-{cell_id}-v1"})
    return c


def uninstaller():
    from memory_os_v4.ops import uninstall
    return uninstall


def journal_of(c, stamp) -> Path:
    return c.home / STATE / "quickstart" / stamp


def absent(c, *keys) -> None:
    """These clients have no configuration at all before the install (file and directory)."""
    for key in keys:
        path = c.home / PATHS[key]
        path.unlink()
        if key != "claude-code":
            path.parent.rmdir()


# ---------------------------------------------------------------- choice of the implementation

def test_the_manager_comes_from_the_detected_cell():
    assert isinstance(managers.select(mac_cell()), managers.Launchd)
    assert isinstance(managers.select(mac_cell(python_version=(3, 13, 1))), managers.Launchd)
    assert isinstance(managers.select(linux_cell()), managers.SystemdUser)
    # The Python minor is not a property of the service manager: the cell gate refuses it elsewhere.
    assert isinstance(managers.select(linux_cell(python_version=(3, 12, 4))), managers.SystemdUser)


@pytest.mark.parametrize("report", [
    lambda: linux_cell(os_release="ID=debian\nVERSION_ID=12\n"),
    lambda: linux_cell(os_release='ID=ubuntu\nVERSION_ID="22.04"\n'),
    lambda: linux_cell(machine="x86_64"),
    lambda: linux_cell(kernel_release="5.15.167.4-microsoft-standard-WSL2\n"),
    lambda: linux_cell(os_release=None),
    lambda: mac_cell(sys_platform="win32", machine="AMD64"),
])
def test_no_manager_outside_macos_and_the_retained_linux_platform(report):
    detected = report()
    assert managers.select(detected) is None
    assert managers.unavailable(detected).startswith("service_manager_unavailable: no supported service manager")


@pytest.mark.parametrize("missing, words", [
    ({"which": lambda name: None}, "systemctl on PATH"),
    ({"exists": lambda path: str(path) == "/run/user/1000/systemd"}, "a system booted with systemd"),
    ({"exists": lambda path: str(path) == "/run/systemd/system"}, "a running systemd user manager"),
])
def test_systemd_absent_on_the_retained_cell_is_an_actionable_refusal(missing, words):
    detected = linux_cell(**missing)
    assert detected["cell_id"] == "ubuntu2404-arm64-py311"
    assert managers.select(detected) is None
    message = managers.unavailable(detected)
    assert words in message and "no service is installed" in message and "systemctl --user status" in message


def test_a_journal_names_its_manager_and_an_old_one_is_launchd():
    assert isinstance(managers.manager_of({"label": "x"}), managers.Launchd)
    assert isinstance(managers.manager_of({"manager": "systemd-user"}), managers.SystemdUser)
    assert managers.manager_of({"manager": "upstart"}) is None and managers.manager_of({"manager": None}) is None


# ---------------------------------------------------------------- definitions

ARGV = ["/opt/memos/bin/python", "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
        "--port", "43219", "--warm"]
ENV = {"MEMORY_V4_DIR": "/home/u/.local/share/memory-os/store", "MEMORY_V4_TOKEN_FILE": "/home/u/.config/memory-os/http.token",
       "MEMORY_V4_STORE_BACKEND": "sqlite_s1", "MEMORY_V4_RETRIEVAL_PROFILE": "lexical",
       "ODD": 'a "quoted" \\ back 100% $HOME'}
EXPECTED_UNIT = [
    "# Memory OS AI: written by `memory-os-admin quickstart --apply`; removed by",
    "# `quickstart --undo <stamp> --with-service` or `memory-os-admin uninstall --apply`.",
    "[Unit]",
    "Description=Memory OS AI server (test.memory-os)",
    "",
    "[Service]",
    "Type=exec",
    'ExecStart="/opt/memos/bin/python" "-m" "memory_os_v4.server" "--transport" "http" "--host" "127.0.0.1" '
    '"--port" "43219" "--warm"',
    'Environment="MEMORY_V4_DIR=/home/u/.local/share/memory-os/store"',
    'Environment="MEMORY_V4_TOKEN_FILE=/home/u/.config/memory-os/http.token"',
    'Environment="MEMORY_V4_STORE_BACKEND=sqlite_s1"',
    'Environment="MEMORY_V4_RETRIEVAL_PROFILE=lexical"',
    'Environment="ODD=a \\"quoted\\" \\\\ back 100%% $HOME"',
    "Restart=always",
    "RestartSec=10",
    "StandardOutput=null",
    "StandardError=append:/home/u/.local/state/memory-os/ops/logs/server.err",
    "",
    "[Install]",
    "WantedBy=default.target",
]


def test_the_unit_is_generated_line_for_line():
    text = managers.SystemdUser().render("test.memory-os", ARGV, ENV, Path("/home/u/.local/state/memory-os/ops/logs/server.err"))
    assert text.split("\n") == EXPECTED_UNIT + [""]
    assert text.endswith("WantedBy=default.target\n")
    # server.err is opened in append mode by systemd, never truncated; nothing else is logged to a file.
    assert sum(line.startswith("StandardError=") for line in text.splitlines()) == 1
    assert "truncate:" not in text and "file:" not in text


def test_the_unit_is_read_back_exactly(tmp_path):
    manager = managers.SystemdUser()
    path = tmp_path / "x.service"
    path.write_text(manager.render("test.memory-os", ARGV, ENV, Path("/var/log/a%b/server.err")), encoding="utf-8")
    definition = manager.read_definition(path)
    assert definition == {"program": ARGV[0], "argv": ARGV, "env": ENV, "log": "/var/log/a%b/server.err"}
    assert managers.installed(manager, path)["backend"] == "sqlite_s1"
    assert managers.installed(manager, path)["profile"] == "lexical"
    assert manager.read_environment(path) == ENV


def test_exec_start_doubles_the_dollar_and_reads_it_back(tmp_path):
    manager = managers.SystemdUser()
    argv = ["/usr/bin/py$thon", "100%"]
    text = manager.render("l", argv, {}, Path("/log/server.err"))
    assert 'ExecStart="/usr/bin/py$$thon" "100%%"' in text.splitlines()
    path = tmp_path / "l.service"
    path.write_text(text, encoding="utf-8")
    assert manager.read_definition(path)["argv"] == argv


@pytest.mark.parametrize("argv, env, log", [
    (["python", "-m", "x"], {}, "/log/server.err"),                 # program not absolute
    (["/usr/bin/python", ";"], {}, "/log/server.err"),              # a lone semicolon separates commands
    (["/usr/bin/python", "a\nb"], {}, "/log/server.err"),           # control character in an argument
    (["/opt/my env/bin/python"], {}, "/log/server.err"),            # a space: `show` could not attest it exactly
    (["/usr/bin/python", 'say "hi"'], {}, "/log/server.err"),
    (["/usr/bin/python", "back\\slash"], {}, "/log/server.err"),
    (["/usr/bin/python"], {"MEMORY_V4_DIR": "/home/my user/store"}, "/log/server.err"),
    (["/usr/bin/python"], {"MEMORY_V4_TOKEN_FILE": "/home/u/a'b.token"}, "/log/server.err"),
    (["/usr/bin/python"], {"K": "a\nExecStartPre=/bin/x"}, "/log/server.err"),  # line injection through a value
    (["/usr/bin/python"], {}, "relative/server.err"),
    (["/usr/bin/python"], {}, "/log/server.err\n[Service]"),
])
def test_a_value_that_cannot_be_written_safely_is_refused(argv, env, log):
    with pytest.raises(ValueError):
        managers.SystemdUser().render("l", argv, env, Path(log))


@pytest.mark.parametrize("text", [
    "ExecStart=/bin/x\n",                                  # outside a section
    "[Service]\nExecStart=/bin/x \\\n  continued\n",      # continuation line
    "[Service]\nnot a key value\n",
    "[Service]\nA=1\n[Service]\nB=2\n",                    # repeated section
])
def test_a_unit_of_another_shape_is_not_parsed(text):
    assert managers.parse_unit(text) is None


@pytest.mark.parametrize("line", [
    "Environment=K=V",                       # not quoted: not written by us
    'Environment="K=V" "L=W"',               # two assignments on a line
    'Environment="K=%h/x"',                  # a specifier
    'Environment="K=a\\nb"',                 # an escape we never write
    'Environment="NOEQUALS"',
])
def test_an_environment_line_we_would_not_write_is_refused(tmp_path, line):
    path = tmp_path / "u.service"
    path.write_text("[Service]\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        managers.SystemdUser().read_environment(path)


def test_unit_state_is_owned_only_for_the_same_definition(tmp_path):
    manager = managers.SystemdUser()
    text = manager.render("l", ARGV, ENV, Path("/log/server.err"))
    path = tmp_path / "l.service"
    assert manager.state(path, text) == "absent"
    path.write_text(text, encoding="utf-8")
    assert manager.state(path, text) == "owned"
    path.write_text(text.replace("Restart=always", "Restart=no"), encoding="utf-8")
    assert manager.state(path, text) == "foreign-or-different"
    path.write_text(text + "ExecStartPre=/bin/other\n", encoding="utf-8")
    assert manager.state(path, text) == "foreign-or-different"
    path.write_text("ExecStart=/bin/x\n", encoding="utf-8")
    assert manager.state(path, text) == "invalid"
    path.write_bytes(b"\xff\xfe not text")
    assert manager.state(path, text) == "invalid"


def test_the_unit_lives_under_xdg_config_home_of_the_process_home_only(tmp_path):
    home = tmp_path / "home"
    default = home / ".config/systemd/user/l.service"
    assert managers.SystemdUser(environ={"HOME": str(home)}).definition_path("l", home) == default
    xdg = managers.SystemdUser(environ={"HOME": str(home), "XDG_CONFIG_HOME": str(home / "cfg")})
    assert xdg.definition_path("l", home) == home / "cfg/systemd/user/l.service"
    assert managers.SystemdUser(environ={"HOME": str(home), "XDG_CONFIG_HOME": "relative"}).definition_path("l", home) == default
    # Another selected HOME does not inherit the XDG directory of this process.
    assert xdg.definition_path("l", tmp_path / "other") == tmp_path / "other/.config/systemd/user/l.service"


def test_the_launchd_definition_is_the_4_1_0_plist_byte_for_byte(tmp_path):
    from memory_os_v4.ops.cli import _plist
    manager = managers.Launchd()
    log = Path("/log/server.err")
    assert manager.render("l", ARGV, ENV, log) == _plist("l", ARGV, env=ENV, keepalive=True, log=log)
    assert manager.definition_path("l", tmp_path) == tmp_path / "Library/LaunchAgents/l.plist"
    path = tmp_path / "l.plist"
    path.write_text(manager.render("l", ARGV, ENV, log), encoding="utf-8")
    assert manager.read_definition(path) == {"program": ARGV[0], "argv": ARGV, "env": ENV, "log": "/log/server.err"}
    assert managers.installed(manager, path)["profile"] == "lexical"
    uid = os.getuid()
    assert manager.status_argv("l") == ["launchctl", "print", f"gui/{uid}/l"]
    assert manager.stop_argv("l") == ["launchctl", "bootout", f"gui/{uid}/l"]
    assert manager.start_argv("l", path) == ["launchctl", "bootstrap", f"gui/{uid}", str(path)]


def test_the_macos_service_plan_keeps_its_exact_keys(c):
    plan = prepare(c, profile="lexical")
    assert plan["ok"], plan["blockers"]
    assert set(plan["service"]) == {"label", "path", "argv", "env", "plist", "state", "sha256", "loaded_state"}
    assert plan["service"]["path"] == str(c.home / PLIST)
    assert not any("linger" in notice for notice in plan["notices"])


# ---------------------------------------------------------------- fake commands in a temporary PATH

FAKE = r'''#!__PYTHON__ -I
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
tool = os.path.basename(sys.argv[0])
state = json.load(open(os.path.join(here, "state.json")))
with open(os.path.join(here, "argv.log"), "a") as log:
    log.write(json.dumps([tool] + sys.argv[1:]) + "\n")
def save():
    json.dump(state, open(os.path.join(here, "state.json"), "w"))
service = state.get("service")
verb = sys.argv[1] if tool == "launchctl" else sys.argv[2]
time.sleep(state.get("delay", {}).get(verb, 0))
if verb in state.get("fail", {}):
    sys.stderr.write("free text that must never be read as a state\n")
    sys.exit(state["fail"][verb])
if tool == "launchctl":
    if verb == "print":
        if not state["loaded"] or service is None:
            sys.stderr.write("Could not find service\n")
            sys.exit(113)
        print("program = " + service["argv"][0])
        print("arguments = {\n" + "\n".join(service["argv"]) + "\n}")
        for key, value in service["env"].items():
            print(key + " => " + value)
        if state.get("process", True):
            print("state = running\npid = 4242\nruns = 1")
        else:  # the job is loaded and launchd has no process for it (waiting to spawn it again)
            print("state = spawn scheduled\nruns = 3\nlast exit code = 1")
    elif verb == "bootstrap":
        state["loaded"] = True
    elif verb == "bootout":
        if not state.get("lingers"):
            state["loaded"] = False
    else:
        sys.exit(64)
    save()
    sys.exit(0)
assert sys.argv[1] == "--user", sys.argv
if verb == "show":
    unit = sys.argv[3]
    on_disk = service is not None and os.path.isfile(service["unit"])
    known = service is not None and unit == service["label"] + ".service" and (
        state["loaded"] or state.get("enabled") or on_disk)
    if not known:
        text = state["not_found"]
    else:
        text = state["template"]
        fields = dict(service, active="active" if state["loaded"] else "inactive",
                      sub="running" if state["loaded"] else "dead", pid="4242" if state["loaded"] else "0",
                      enabled="enabled" if state.get("enabled") else "disabled", restarts="0", code="0",
                      status="0", drop_ins="", reload="no")
        for name, value in fields.items():
            if isinstance(value, str):
                text = text.replace("@" + name.upper() + "@", value)
    lines = text.splitlines()
    for key, value in state.get("show", {}).items():  # a test replaces or drops (null) whole lines
        lines = [line for line in lines if not line.startswith(key + "=")]
        if value is not None:
            lines.append(key + "=" + value)
    sys.stdout.write("\n".join(lines) + "\n" + state.get("show_extra", ""))
elif verb == "daemon-reload":
    state["reloads"] = state.get("reloads", 0) + 1
elif verb == "enable":
    assert sys.argv[3] == "--now"
    state["loaded"] = state["enabled"] = True
elif verb == "disable":
    assert sys.argv[3] == "--now"
    if not state.get("lingers"):
        state["loaded"] = state["enabled"] = False
elif verb in ("stop", "start"):
    state["loaded"] = verb == "start"
else:
    sys.exit(64)
save()
'''


class Scripts:
    """``launchctl`` and ``systemctl`` as scripts: the directory holding them is the WHOLE ``PATH``."""

    def __init__(self, tmp_path, monkeypatch):
        self.bin = tmp_path / "fake-bin"
        self.bin.mkdir()
        for tool in ("launchctl", "systemctl"):
            (self.bin / tool).write_text(FAKE.replace("__PYTHON__", sys.executable), encoding="utf-8")
            (self.bin / tool).chmod(0o700)
        self.write({"loaded": False, "enabled": False, "service": None, "template": SYSTEMD_SHOW_LOADED,
                    "not_found": SYSTEMD_SHOW_NOT_FOUND})
        (self.bin / "argv.log").write_text("")
        monkeypatch.setenv("PATH", str(self.bin))  # nothing else: a real launchctl/systemctl cannot be reached
        self.timeout = None

    def write(self, state):
        (self.bin / "state.json").write_text(json.dumps(state))

    def state(self):
        return json.loads((self.bin / "state.json").read_text())

    def set(self, **changes):
        self.write({**self.state(), **changes})

    @property
    def service(self):
        return self.state()["service"]

    @service.setter
    def service(self, plan):  # the setup fixture hands the planned service to its fake system
        self.set(service=None if plan is None or not plan.get("path") else {
            "label": plan["label"], "path": plan["path"], "argv": plan["argv"], "env": plan["env"],
            # literal fields of the frozen ``systemctl show`` text
            "unit": plan["path"], "python": plan["argv"][0], "port": plan["argv"][plan["argv"].index("--port") + 1],
            "store": plan["env"]["MEMORY_V4_DIR"], "token": plan["env"]["MEMORY_V4_TOKEN_FILE"]})

    @property
    def calls(self):
        return [json.loads(line) for line in (self.bin / "argv.log").read_text().splitlines()]

    def verbs(self, tool):
        return [argv[2] if tool == "systemctl" else argv[1] for argv in self.calls if argv[0] == tool]

    def __call__(self, argv, **kwargs):
        """The real ``subprocess.run``; a test may shorten the timeouts so that a delay costs little."""
        assert argv[0] in {"launchctl", "systemctl"}, f"unexpected system command: {argv}"
        if self.timeout is not None and "timeout" in kwargs:
            kwargs["timeout"] = self.timeout
        return subprocess.run(argv, **kwargs)


@pytest.fixture
def scripts(tmp_path, monkeypatch):
    return Scripts(tmp_path, monkeypatch)


def planned(tmp_path, manager) -> dict:
    """A service plan and its definition on disk, as an install leaves them."""
    label = "test.memory-os"
    env = {"MEMORY_V4_TOKEN_FILE": str(tmp_path / "http.token"), "MEMORY_V4_DIR": str(tmp_path / "store"),
           "MEMORY_V4_STORE_BACKEND": "sqlite_s1"}
    argv = [str(tmp_path / "python"), "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
            "--port", "43219", "--warm"]
    path = manager.definition_path(label, tmp_path / "home")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manager.render(label, argv, env, tmp_path / "server.err"), encoding="utf-8")
    return {"label": label, "path": str(path), "argv": argv, "env": env, **manager.plan_extras(label)}


@pytest.mark.parametrize("kind", ["launchd", "systemd-user"])
def test_start_attest_stop_through_scripts_on_both_implementations(tmp_path, scripts, kind):
    manager = managers.MANAGERS[kind]()
    service = planned(tmp_path, manager)
    scripts.service = service
    tool = "launchctl" if kind == "launchd" else "systemctl"
    assert manager.observe(service, run=scripts) == {"state": "absent"}
    assert manager.start(service, run=scripts) == 0
    observed = manager.observe(service, run=scripts)
    assert observed["state"] == "loaded" and observed["pid"] == 4242 and observed["process_state"] == "running"
    assert manager.status(service["label"], run=subprocess.run) == "running"
    assert manager.stop(service, run=scripts) == "ok"
    assert manager.observe(service, run=scripts) == {"state": "absent"}
    assert manager.status(service["label"], run=subprocess.run) == "stopped"
    expected = (["print", "bootstrap", "print", "print", "bootout", "print", "print"] if kind == "launchd" else
                ["show", "daemon-reload", "enable", "show", "show", "disable", "show", "show"])
    assert scripts.verbs(tool) == expected
    assert {argv[0] for argv in scripts.calls} == {tool}  # never the other manager, never loginctl
    uid = os.getuid()
    if kind == "launchd":
        assert scripts.calls[1] == ["launchctl", "bootstrap", f"gui/{uid}", service["path"]]
        assert scripts.calls[4] == ["launchctl", "bootout", f"gui/{uid}/test.memory-os"]
    else:
        assert scripts.calls[1] == ["systemctl", "--user", "daemon-reload"]
        assert scripts.calls[2] == ["systemctl", "--user", "enable", "--now", "test.memory-os.service"]
        assert scripts.calls[5] == ["systemctl", "--user", "disable", "--now", "test.memory-os.service"]
        assert scripts.calls[0][:4] == ["systemctl", "--user", "show", "test.memory-os.service"]
        assert scripts.calls[0][5] == "--property=" + ",".join(managers.SHOW_PROPERTIES)


@pytest.mark.parametrize("kind, verb", [("launchd", "print"), ("systemd-user", "show")])
def test_a_timeout_or_an_error_of_the_observation_is_never_absence(tmp_path, scripts, kind, verb):
    manager = managers.MANAGERS[kind]()
    service = planned(tmp_path, manager)
    scripts.service = service
    scripts.set(fail={verb: 1})
    assert manager.observe(service, run=scripts) == {"state": "unknown"}
    assert manager.status(service["label"], run=subprocess.run) == "unknown"  # both managers: never "stopped"
    scripts.set(fail={}, delay={verb: 3})
    scripts.timeout = 0.3
    assert manager.observe(service, run=scripts) == {"state": "unknown"}
    assert manager.status(service["label"], run=subprocess.run, timeout=0.3) == "unknown"


@pytest.mark.parametrize("kind, verb", [("launchd", "bootout"), ("systemd-user", "disable")])
def test_a_stop_reports_failure_and_timeout_without_retry(tmp_path, scripts, kind, verb):
    manager = managers.MANAGERS[kind]()
    service = planned(tmp_path, manager)
    scripts.service = service
    scripts.set(loaded=True, fail={verb: 3})
    assert manager.stop(service, run=scripts) == "failed"
    scripts.set(fail={}, delay={verb: 3})
    scripts.timeout = 0.3
    assert manager.stop(service, run=scripts) == "timeout"
    tool = "launchctl" if kind == "launchd" else "systemctl"
    assert scripts.verbs(tool) == [verb, verb]  # one command per call: no retry, no kill


@pytest.mark.parametrize("step", ["daemon-reload", "enable"])
def test_a_failed_systemd_start_step_is_reported_and_nothing_else_runs(tmp_path, scripts, step):
    manager = managers.SystemdUser()
    service = planned(tmp_path, manager)
    scripts.service = service
    scripts.set(fail={step: 1})
    assert manager.start(service, run=scripts) == 1
    assert scripts.verbs("systemctl") == (["daemon-reload"] if step == "daemon-reload" else ["daemon-reload", "enable"])


# Frozen ``systemctl --user show`` outputs: literal text, independent of the code under test and of
# any plan (fixed paths). Variations are made by replacing whole literal lines.
FIXED = {"label": "test.memory-os", "path": "/home/u/.config/systemd/user/test.memory-os.service",
         "argv": ["/home/u/venv/bin/python", "-m", "memory_os_v4.server", "--transport", "http", "--host", "127.0.0.1",
                  "--port", "43219", "--warm"],
         "env": {"MEMORY_V4_TOKEN_FILE": "/home/u/.config/memory-os/http.token",
                 "MEMORY_V4_DIR": "/home/u/.local/share/memory-os/store"},
         "manager": "systemd-user"}
EXEC_LINE = ("ExecStart={ path=/home/u/venv/bin/python ; argv[]=/home/u/venv/bin/python -m memory_os_v4.server "
             "--transport http --host 127.0.0.1 --port 43219 --warm ; ignore_errors=no ; "
             "start_time=[Mon 2026-11-23 17:02:11 CET] ; stop_time=[n/a] ; pid=4242 ; code=(null) ; status=0/0 }")
ENV_LINE = ("Environment=MEMORY_V4_DIR=/home/u/.local/share/memory-os/store "
            "MEMORY_V4_TOKEN_FILE=/home/u/.config/memory-os/http.token MEMORY_V4_STORE_BACKEND=sqlite_s1 "
            "TOKENIZERS_PARALLELISM=false")
SHOW_ACTIVE_ENABLED = "\n".join([
    "MainPID=4242",
    "NRestarts=0",
    "ExecMainCode=0",
    "ExecMainStatus=0",
    EXEC_LINE,
    ENV_LINE,
    "LoadState=loaded",
    "ActiveState=active",
    "SubState=running",
    "FragmentPath=/home/u/.config/systemd/user/test.memory-os.service",
    "UnitFileState=enabled",
    "DropInPaths=",
    "NeedDaemonReload=no",
]) + "\n"
SHOW_STOPPED_DISABLED = (SHOW_ACTIVE_ENABLED.replace("MainPID=4242", "MainPID=0")
                         .replace("ActiveState=active", "ActiveState=inactive")
                         .replace("SubState=running", "SubState=dead")
                         .replace("UnitFileState=enabled", "UnitFileState=disabled")
                         .replace("pid=4242 ;", "pid=0 ;"))
SHOW_UNKNOWN_UNIT = ("MainPID=0\nNRestarts=0\nExecMainCode=0\nExecMainStatus=0\nEnvironment=\nLoadState=not-found\n"
                     "ActiveState=inactive\nSubState=dead\nFragmentPath=\nUnitFileState=\nDropInPaths=\n"
                     "NeedDaemonReload=no\n")
DROP_IN = "DropInPaths=/home/u/.config/systemd/user/test.memory-os.service.d/override.conf"


def frozen(text, code=0):
    """A ``run`` that answers ``systemctl --user show`` with this literal text and nothing else."""
    def run(argv, **kwargs):
        assert argv[:4] == ["systemctl", "--user", "show", "test.memory-os.service"], argv
        return SimpleNamespace(returncode=code, stdout=text, stderr="")
    return run


def test_the_frozen_show_texts_are_what_the_tests_think_they_are():
    assert SHOW_ACTIVE_ENABLED != SHOW_STOPPED_DISABLED and "UnitFileState=disabled" in SHOW_STOPPED_DISABLED
    assert EXEC_LINE in SHOW_STOPPED_DISABLED.replace("pid=0 ;", "pid=4242 ;")


def test_activity_and_activation_are_two_facts():
    observe = managers.SystemdUser().observe
    running = observe(FIXED, run=frozen(SHOW_ACTIVE_ENABLED))
    assert running == {"state": "loaded", "runs": 0, "last_exit_code": None, "pid": 4242, "process_state": "running",
                       "active": True, "running": True, "unit_file_state": "enabled"}
    assert observe(FIXED, run=frozen(SHOW_STOPPED_DISABLED)) == {"state": "absent"}
    # Active but NOT enabled (started by hand, or disabled since): loaded, and the activation is said as it is.
    by_hand = observe(FIXED, run=frozen(SHOW_ACTIVE_ENABLED.replace("UnitFileState=enabled", "UnitFileState=disabled")))
    assert by_hand["state"] == "loaded" and by_hand["active"] is True and by_hand["unit_file_state"] == "disabled"
    # Enabled but not running: it would come back at the next login, so it is not "absent".
    stopped = observe(FIXED, run=frozen(SHOW_STOPPED_DISABLED.replace("UnitFileState=disabled", "UnitFileState=enabled")))
    assert stopped["state"] == "loaded" and stopped["active"] is False and stopped["unit_file_state"] == "enabled"
    static = observe(FIXED, run=frozen(SHOW_STOPPED_DISABLED.replace("UnitFileState=disabled", "UnitFileState=static")))
    assert static["state"] == "loaded" and static["unit_file_state"] == "static"


SPACED = ("ExecStart={ path=/home/u/venv/bin/python ; argv[]=/home/u/venv/bin/python -m memory_os_v4.server "
          "--transport http --host 127.0.0.1 --port 43219 --warm --name my server ; ignore_errors=no ; "
          "start_time=[n/a] ; stop_time=[n/a] ; pid=4242 ; code=(null) ; status=0/0 }")


@pytest.mark.parametrize("old, new, expected", [
    # --- another unit, or something layered on ours: never "loaded"
    ("FragmentPath=/home/u/.config/systemd/user/test.memory-os.service",
     "FragmentPath=/etc/systemd/user/test.memory-os.service", "foreign-or-unattested"),
    ("DropInPaths=", DROP_IN, "foreign-or-unattested"),
    ("NeedDaemonReload=no", "NeedDaemonReload=yes", "foreign-or-unattested"),
    ("LoadState=loaded", "LoadState=masked", "foreign-or-unattested"),
    ("LoadState=loaded", "LoadState=error", "foreign-or-unattested"),
    # --- another command, compared word for word (never by prefix, never flattened)
    (EXEC_LINE, SPACED, "foreign-or-unattested"),
    (EXEC_LINE, EXEC_LINE.replace("--port 43219", "--port 43218"), "foreign-or-unattested"),
    (EXEC_LINE, EXEC_LINE.replace("path=/home/u/venv/bin/python", "path=/usr/bin/env"), "foreign-or-unattested"),
    (EXEC_LINE, EXEC_LINE.replace(" --warm ;", " ;"), "foreign-or-unattested"),
    (ENV_LINE, ENV_LINE.replace("/home/u/.local/share/memory-os/store", "/elsewhere"), "foreign-or-unattested"),
    (ENV_LINE, "Environment=TOKENIZERS_PARALLELISM=false", "foreign-or-unattested"),
    # --- a shape this code does not read in full: a refusal, never "ours"
    (EXEC_LINE, EXEC_LINE.replace(" ; ignore_errors=no", " ; ignore_errors=no ; flags=privileged"), "unknown"),
    (EXEC_LINE, EXEC_LINE.replace(" ; code=(null) ; status=0/0 }", ""), "unknown"),          # truncated
    (EXEC_LINE, EXEC_LINE + " ; ExecStartPost=/bin/other", "unknown"),
    (EXEC_LINE, "ExecStart=", "unknown"),
    (EXEC_LINE + "\n", "", "unknown"),                                                        # property missing
    ("DropInPaths=\n", "", "unknown"),
    ("NeedDaemonReload=no\n", "", "unknown"),
    ("UnitFileState=enabled\n", "", "unknown"),
    ("NeedDaemonReload=no", "NeedDaemonReload=maybe", "unknown"),
    ("UnitFileState=enabled", "UnitFileState=Enabled (preset)", "unknown"),
    (ENV_LINE, ENV_LINE.replace("MEMORY_V4_DIR=/home/u/.local/share/memory-os/store",
                                'MEMORY_V4_DIR="/home/u/my store"'), "unknown"),
    (ENV_LINE, ENV_LINE + " MEMORY_V4_DIR=/elsewhere", "unknown"),                             # repeated variable
    (ENV_LINE, ENV_LINE + " not-an-assignment", "unknown"),
    ("MainPID=4242", "MainPID=many", "unknown"),
    ("SubState=running", "SubState=running\nWarning: journal has been rotated", "unknown"),  # a line out of form
    ("SubState=running", "SubState=running\nSubState=dead", "unknown"),                       # repeated key
    ("LoadState=loaded\n", "", "unknown"),
])
def test_unexpected_show_output_is_refused(old, new, expected):
    assert old in SHOW_ACTIVE_ENABLED
    text = SHOW_ACTIVE_ENABLED.replace(old, new)
    assert managers.SystemdUser().observe(FIXED, run=frozen(text)) == {"state": expected}


@pytest.mark.parametrize("old, new", [
    ("DropInPaths=", DROP_IN),
    ("NeedDaemonReload=no", "NeedDaemonReload=yes"),
    (EXEC_LINE.replace("pid=4242 ;", "pid=0 ;"), SPACED),
    ("FragmentPath=/home/u/.config/systemd/user/test.memory-os.service", "FragmentPath=/etc/systemd/user/x.service"),
    ("DropInPaths=\n", ""),
    (ENV_LINE, 'Environment=MEMORY_V4_DIR="/home/u/my store"'),
])
def test_a_stopped_disabled_unit_is_absent_only_after_the_same_controls(old, new):
    """Inactive and disabled at our path is NOT enough: drop-ins, reload and command are checked first."""
    assert old in SHOW_STOPPED_DISABLED
    state = managers.SystemdUser().observe(FIXED, run=frozen(SHOW_STOPPED_DISABLED.replace(old, new)))["state"]
    assert state in {"foreign-or-unattested", "unknown"}


def test_a_plan_whose_words_cannot_be_compared_exactly_is_never_attested():
    """``show`` joins the words with spaces: a planned word holding a space could match by flattening."""
    plan = {**FIXED, "argv": FIXED["argv"] + ["--name", "my server"]}
    assert managers.SystemdUser().observe(plan, run=frozen(SHOW_ACTIVE_ENABLED.replace(EXEC_LINE, SPACED))) == {
        "state": "unknown"}


@pytest.mark.parametrize("text, expected", [
    (SHOW_UNKNOWN_UNIT, "absent"),
    (SHOW_UNKNOWN_UNIT.replace("MainPID=0", "MainPID=77"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("ActiveState=inactive", "ActiveState=active"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("DropInPaths=", DROP_IN), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("FragmentPath=", "FragmentPath=/run/systemd/transient/x.service"),
     "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("LoadState=not-found\n", ""), "unknown"),
    # Review counter-example: three reassuring lines and nothing else prove nothing.
    ("LoadState=not-found\nActiveState=inactive\nMainPID=0\n", "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("UnitFileState=\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("FragmentPath=\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("DropInPaths=\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("NeedDaemonReload=no\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("Environment=\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("SubState=dead\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("NRestarts=0\n", ""), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("ExecMainCode=0", "ExecMainCode=exited"), "unknown"),
    (SHOW_UNKNOWN_UNIT.replace("NeedDaemonReload=no", "NeedDaemonReload=perhaps"), "unknown"),
    (SHOW_UNKNOWN_UNIT + "ExecStart=\n", "absent"),                       # printed empty: still the plain case
    (SHOW_UNKNOWN_UNIT.replace("UnitFileState=", "UnitFileState=not-found"), "absent"),
    (SHOW_UNKNOWN_UNIT.replace("UnitFileState=", "UnitFileState=enabled"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("NeedDaemonReload=no", "NeedDaemonReload=yes"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("Environment=", "Environment=A=1"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT.replace("ActiveState=inactive", "ActiveState=failed"), "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT + EXEC_LINE + "\n", "foreign-or-unattested"),
    (SHOW_UNKNOWN_UNIT + "Unit test.memory-os.service could not be found.\n", "unknown"),
    ("", "unknown"),
])
def test_a_unit_systemd_does_not_know_is_absent_only_in_the_plain_case(text, expected):
    assert managers.SystemdUser().observe(FIXED, run=frozen(text)) == {"state": expected}


def test_a_failing_show_is_never_absence_even_with_a_reassuring_text():
    assert managers.SystemdUser().observe(FIXED, run=frozen(SHOW_UNKNOWN_UNIT, code=1)) == {"state": "unknown"}


def test_a_crashing_systemd_service_reports_runs_and_exit_code_in_closed_form():
    crashing = (SHOW_ACTIVE_ENABLED.replace("MainPID=4242", "MainPID=0").replace("NRestarts=0", "NRestarts=2")
                .replace("ExecMainCode=0", "ExecMainCode=1").replace("ExecMainStatus=0", "ExecMainStatus=3")
                .replace("ActiveState=active", "ActiveState=activating").replace("SubState=running", "SubState=auto-restart"))
    assert managers.SystemdUser().observe(FIXED, run=frozen(crashing)) == {
        "state": "loaded", "runs": 3, "last_exit_code": 3, "pid": None, "process_state": "auto-restart",
        "active": True, "running": False, "unit_file_state": "enabled"}
    noisy = crashing.replace("SubState=auto-restart", "SubState=Traceback (most recent call last)")
    assert managers.SystemdUser().observe(FIXED, run=frozen(noisy)) == {"state": "unknown"}


# ---- review round 3: no false absence, no false "stopped", whatever the branch

THREE_LINES = "LoadState=not-found\nActiveState=inactive\nMainPID=0\n"


def test_status_reads_the_same_strict_schema_as_observe():
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(THREE_LINES)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(THREE_LINES)) == "unknown"   # was "stopped"
    assert manager.status("test.memory-os", run=frozen(SHOW_UNKNOWN_UNIT)) == "stopped"
    assert manager.status("test.memory-os", run=frozen(SHOW_STOPPED_DISABLED)) == "stopped"
    assert manager.status("test.memory-os", run=frozen(SHOW_ACTIVE_ENABLED)) == "running"
    assert manager.status("test.memory-os", run=frozen(SHOW_UNKNOWN_UNIT, code=1)) == "unknown"
    masked = SHOW_STOPPED_DISABLED.replace("LoadState=loaded", "LoadState=masked")
    assert manager.status("test.memory-os", run=frozen(masked)) == "unknown"


def test_the_wait_of_a_backup_never_concludes_from_an_incomplete_observation(monkeypatch):
    manager = managers.SystemdUser()
    monkeypatch.setattr(backup, "_manager", lambda: manager)
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "free")
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    settings = SimpleNamespace(port=43219)
    monkeypatch.setattr(backup.subprocess, "run", frozen(THREE_LINES))
    assert backup._service_loaded("test.memory-os") is True
    assert backup._wait_stopped(settings, "test.memory-os", timeout=0.05) is False   # was True
    monkeypatch.setattr(backup.subprocess, "run", frozen(SHOW_STOPPED_DISABLED))
    assert backup._wait_stopped(settings, "test.memory-os", timeout=0.05) is True


@pytest.mark.parametrize("old, new", [
    ("SubState=dead", "SubState=???"),
    ("ExecMainCode=0", "ExecMainCode=exited"),
    ("NRestarts=0", "NRestarts=NaN"),
])
def test_invalid_values_on_a_conforming_stopped_unit_are_never_an_absence(old, new):
    """Review counter-examples, exactly: the definition must not be moved on such an observation."""
    assert old in SHOW_STOPPED_DISABLED
    text = SHOW_STOPPED_DISABLED.replace(old, new)
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(text)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(text)) == "unknown"


def test_stop_owned_service_keeps_the_unit_when_the_absence_is_not_valid(tmp_path):
    import hashlib
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    unit = tmp_path / "test.memory-os.service"
    unit.write_text("[Service]\n")
    service = {**FIXED, "path": str(unit)}
    text = SHOW_STOPPED_DISABLED.replace(FIXED["path"], str(unit)).replace("SubState=dead", "SubState=???")
    stopped = qs.stop_owned_service(service, hashlib.sha256(unit.read_bytes()).hexdigest(), journal, run=frozen(text))
    assert stopped == {"outcome": "refused-job-not-attested", "code": "service_changed_since_install"}
    assert unit.is_file() and list(journal.iterdir()) == []


INVALID = {"LoadState": ["???", "", "Loaded", "loaded (preset)"], "ActiveState": ["???", "", "Inactive", "dead"],
           "SubState": ["???", "", "Dead", "inactive"], "UnitFileState": ["???", "Disabled", "disabled; preset: enabled"],
           "MainPID": ["???", "", "-1", "0x0", "NaN"], "NRestarts": ["???", "", "NaN", "-1"],
           "ExecMainCode": ["???", "", "exited"], "ExecMainStatus": ["???", "", "0/SUCCESS"],
           "NeedDaemonReload": ["???", "", "No", "false"], "FragmentPath": ["???", "relative/x.service"],
           "DropInPaths": ["???", "relative.d/x.conf"], "Environment": ["???", 'A="b c"', "A=1 A=2"],
           "ExecStart": ["???", "{ path=/bin/x }"]}
MUTATIONS = [(name, prop, value) for name in ("stopped-disabled", "unknown-unit", "active-enabled")
             for prop, values in INVALID.items() for value in values]


@pytest.mark.parametrize("name, prop, value", MUTATIONS)
def test_mutation_table_an_invalid_value_never_gives_absent_nor_stopped(name, prop, value):
    """Each property in turn replaced by an invalid value, on each frozen output: never a conclusion."""
    base = {"stopped-disabled": SHOW_STOPPED_DISABLED, "unknown-unit": SHOW_UNKNOWN_UNIT,
            "active-enabled": SHOW_ACTIVE_ENABLED}[name]
    lines = [line for line in base.splitlines() if not line.startswith(prop + "=")]
    text = "\n".join(lines + [prop + "=" + value]) + "\n"
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(text)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(text)) == "unknown"


@pytest.mark.parametrize("name", ["stopped-disabled", "unknown-unit", "active-enabled"])
@pytest.mark.parametrize("prop", [p for p in managers.SHOW_PROPERTIES])
def test_mutation_table_a_missing_property_never_gives_absent_nor_stopped(name, prop):
    base = {"stopped-disabled": SHOW_STOPPED_DISABLED, "unknown-unit": SHOW_UNKNOWN_UNIT,
            "active-enabled": SHOW_ACTIVE_ENABLED}[name]
    if name == "unknown-unit" and prop == "ExecStart":
        pytest.skip("a unit systemd does not know has no command to print")
    text = "\n".join(line for line in base.splitlines() if not line.startswith(prop + "=")) + "\n"
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(text)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(text)) == "unknown"


def test_the_script_fake_prints_the_frozen_text_and_attestation_follows_it(tmp_path, scripts):
    manager = managers.SystemdUser()
    service = planned(tmp_path, manager)
    scripts.service = service
    scripts.set(loaded=True, enabled=True)
    assert manager.observe(service, run=scripts)["state"] == "loaded"
    for show, expected in (({"DropInPaths": "/x.d/override.conf"}, "foreign-or-unattested"),
                           ({"NeedDaemonReload": "yes"}, "foreign-or-unattested"),
                           ({"DropInPaths": None}, "unknown")):
        scripts.set(show=show)
        assert manager.observe(service, run=scripts)["state"] == expected
    scripts.set(show={}, show_extra="Warning: something happened\n")
    assert manager.observe(service, run=scripts) == {"state": "unknown"}


# ---- review round 4: every sub-field of ExecStart is validated; launchd reads absence strictly

EXEC_STOPPED = EXEC_LINE.replace("pid=4242 ;", "pid=0 ;")
EXEC_MUTATIONS = [
    ("code=(null)", "code=???"),                       # review counter-example
    ("path=/home/u/venv/bin/python", "path=relative"),  # review counter-example
    ("path=/home/u/venv/bin/python", "path="),
    ("path=/home/u/venv/bin/python", "path=/a b"),
    ("ignore_errors=no", "ignore_errors=maybe"),
    ("ignore_errors=no", "ignore_errors="),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[???]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=yesterday"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[]"),
    ("stop_time=[n/a]", "stop_time=[never]"),
    ("stop_time=[n/a]", "stop_time=n/a"),
    ("code=(null)", "code=EXITED"),
    ("code=(null)", "code="),
    ("code=(null)", "code=0"),
    ("status=0/0", "status=???"),
    ("status=0/0", "status=0"),
    ("status=0/0", "status=0/"),
    ("status=0/0", "status=ok/0"),
    ("status=0/0", "status=0/success"),
    (" --warm ;", " --warm  ;"),                        # an empty word
    ("status=0/0", "status=0/BOGUS"),                   # review counter-example (round 5)
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Xxx 2026-99-99 99:99:99 ZZZ]"),   # idem
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Xxx 2026-11-23 17:02:11 CET]"),   # unknown weekday
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Tue 2026-11-23 17:02:11 CET]"),   # not that day
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-02-30 17:02:11 CET]"),   # no such date
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-13-23 17:02:11 CET]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 25:02:11 CET]"),   # no such time
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:61:11 CET]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11 ZZZ]"),   # unknown zone
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11 +9900]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11 +0199]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11]"),
    ("stop_time=[n/a]", "stop_time=[Xxx 2026-99-99 99:99:99 ZZZ]"),
    ("status=0/0", "status=0/Success"),
    ("status=0/0", "status=15/SIGTERM"),
    ("status=0/0", "status=0/RTMIN+99"),
    ("argv[]=/home/u/venv/bin/python -m memory_os_v4.server --transport http --host 127.0.0.1 --port 43219 --warm",
     "argv[]="),
]


@pytest.mark.parametrize("base", ["stopped-disabled", "active-enabled"])
@pytest.mark.parametrize("old, new", EXEC_MUTATIONS + [("pid=4242 ;", "pid=NaN ;"), ("pid=4242 ;", "pid= ;"),
                                                       ("pid=4242 ;", "pid=-1 ;")])
def test_mutation_table_of_the_exec_start_sub_fields(base, old, new):
    """Each sub-field replaced by an invalid value: never ``absent``, never ``loaded``, never ``stopped``."""
    text = {"stopped-disabled": SHOW_STOPPED_DISABLED, "active-enabled": SHOW_ACTIVE_ENABLED}[base]
    old = old.replace("pid=4242 ;", "pid=0 ;") if base == "stopped-disabled" else old
    assert old in text
    mutated = text.replace(old, new)
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(mutated)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(mutated)) == "unknown"


@pytest.mark.parametrize("old, new", [
    ("code=(null) ; status=0/0", "code=exited ; status=0/SUCCESS"),
    ("code=(null) ; status=0/0", "code=exited ; status=1/FAILURE"),
    ("code=(null) ; status=0/0", "code=killed ; status=15/TERM"),
    ("code=(null) ; status=0/0", "code=dumped ; status=6/ABRT"),
    ("stop_time=[n/a]", "stop_time=[Mon 2026-11-23 17:05:00 UTC]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[n/a]"),
    ("ignore_errors=no", "ignore_errors=yes"),
    ("status=0/0", "status=203/EXEC"),
    ("status=0/0", "status=9/KILL"),
    ("status=0/0", "status=34/RTMIN+1"),
    ("status=0/0", "status=143/143"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Thu 2024-02-29 23:59:59 UTC]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11 +0100]"),
    ("start_time=[Mon 2026-11-23 17:02:11 CET]", "start_time=[Mon 2026-11-23 17:02:11 -03]"),
])
def test_the_documented_exec_start_values_are_still_read(old, new):
    stopped = SHOW_STOPPED_DISABLED.replace(old, new)
    assert old in SHOW_STOPPED_DISABLED
    assert managers.SystemdUser().observe(FIXED, run=frozen(stopped)) == {"state": "absent"}
    assert managers.SystemdUser().status("test.memory-os", run=frozen(stopped)) == "stopped"


def launchctl_answer(code, stdout=b"", stderr=b""):
    def run(argv, **kwargs):
        assert argv[:2] == ["launchctl", "print"], argv
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    return run


@pytest.mark.parametrize("code, stderr, expected", [
    (0, b"", "running"),
    (113, b'Bad request.\nCould not find service "test.memory-os" in domain for user gui: 501', "stopped"),
    (1, b"IPC failure", "unknown"),                    # review counter-example: was "stopped"
    (113, b"", "unknown"),                             # the code without the documented answer
    (1, b"Could not find service", "unknown"),         # the words without the documented code
    (5, b"Operation not permitted", "unknown"),
    (64, b"Usage: launchctl print ...", "unknown"),
])
def test_launchd_status_is_stopped_only_for_the_documented_absence(code, stderr, expected):
    manager = managers.Launchd()
    assert manager.status("test.memory-os", run=launchctl_answer(code, stderr=stderr)) == expected
    text = launchctl_answer(code, stderr=stderr.decode())  # the same with text output
    assert manager.status("test.memory-os", run=text) == expected


FOUND_NOT = b'Bad request.\nCould not find service "test.memory-os" in domain for user gui: 501'


@pytest.mark.parametrize("code, stderr", [
    (1, FOUND_NOT),            # review counter-example: the words with another code
    (113, b"IPC failure"),     # review counter-example: the code with another message
    (113, b""), (1, b"IPC failure"), (5, FOUND_NOT), (0, FOUND_NOT),
])
def test_launchd_has_one_predicate_of_absence_for_every_reader(monkeypatch, tmp_path, code, stderr):
    """observe, status, doctor and the wait of a backup: 113 AND the documented message, nothing else."""
    manager = managers.Launchd()
    service = {"label": "test.memory-os", "argv": ["/x/python"], "env": {}}
    as_text = launchctl_answer(code, stdout="", stderr=stderr.decode())
    assert manager.observe(service, run=as_text)["state"] != "absent"
    assert manager.status("test.memory-os", run=launchctl_answer(code, stderr=stderr)) != "stopped"
    assert managers.Launchd.absent(code, "", stderr.decode()) is False
    monkeypatch.setattr(backup, "_manager", lambda: manager)
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "free")
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    monkeypatch.setattr(backup.subprocess, "run", launchctl_answer(code, stderr=stderr))
    assert backup._service_loaded("test.memory-os") is True
    assert backup._wait_stopped(SimpleNamespace(port=43219), "test.memory-os", timeout=0.05) is False
    monkeypatch.setattr(managers, "host_manager", lambda: manager)
    assert doctor._service(doctor_settings(tmp_path)).status != "FAIL" or code == 0


def test_launchd_documented_absence_is_read_the_same_by_every_reader(monkeypatch):
    manager = managers.Launchd()
    service = {"label": "test.memory-os", "argv": ["/x/python"], "env": {}}
    assert manager.observe(service, run=launchctl_answer(113, stdout="", stderr=FOUND_NOT.decode())) == {"state": "absent"}
    assert manager.status("test.memory-os", run=launchctl_answer(113, stderr=FOUND_NOT)) == "stopped"
    assert managers.Launchd.absent(113, "", FOUND_NOT.decode()) is True


@pytest.mark.parametrize("code, stopped", [(113, True), (0, False), (1, False), (5, False), (-1, False)])
def test_the_launchd_wait_of_a_backup_never_concludes_from_an_error(monkeypatch, code, stopped):
    monkeypatch.setattr(backup, "_manager", lambda: managers.Launchd())
    monkeypatch.setattr(backup.subprocess, "run", launchctl_answer(code, stderr=FOUND_NOT if code == 113 else b"IPC failure"))
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "free")
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    assert backup._service_loaded("test.memory-os") is (not stopped)
    assert backup._wait_stopped(SimpleNamespace(port=43219), "test.memory-os", timeout=0.05) is stopped


# ---------------------------------------------------------------- refusals at the preview

def test_quickstart_refuses_when_systemd_is_absent_and_installs_nothing(c, monkeypatch):
    report = linux_cell(exists=lambda path: False)
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
    monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {"profile_id": "synthetic"})
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == ["service_manager_unavailable"]
    assert "systemd --user is not available" in plan["blockers"][0] and "nothing written" in plan["blockers"][0]
    assert not any("launchd" in blocker or "occupied" in blocker for blocker in plan["blockers"])
    assert plan["service"]["state"] == "unsupported" and plan["service"]["path"] is None
    assert plan["service"]["loaded_state"] == "unsupported"
    assert c.system.calls == []  # neither launchctl nor systemctl was even asked
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.apply_install(plan, c.settings, home=c.home, run=c.system, download_approved=True)
    assert refusal.value.code == "preflight_refused"
    assert tree(c.root) == before and not (c.home / UNIT).exists() and not (c.home / PLIST).exists()


@pytest.mark.parametrize("report", [
    lambda: linux_cell(os_release="ID=debian\nVERSION_ID=12\n"),
    lambda: linux_cell(kernel_release="5.15.167.4-microsoft-standard-WSL2\n"),
    lambda: linux_cell(python_version=(3, 12, 4)),
])
def test_a_cell_that_is_not_retained_gets_no_service(c, monkeypatch, report):
    detected = report()
    monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(detected))
    monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {"profile_id": "synthetic"})
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "runtime_unqualified" in plan["blocker_codes"]
    assert "port_or_label_occupied" not in plan["blocker_codes"]
    assert c.system.verbs("launchctl") == [] and set(c.system.verbs("systemctl")) <= {"show"}
    assert tree(c.root) == before


def test_the_occupied_message_names_the_manager_of_the_cell(ubuntu, monkeypatch):
    c = ubuntu
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: "occupied")
    plan = prepare(c, profile="lexical")
    assert plan["blocker_codes"] == ["port_or_label_occupied"]
    assert plan["blockers"] == ["port or systemd unit name occupied; nothing activated"]


def test_the_occupied_message_is_unchanged_on_macos(c, monkeypatch):
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: "occupied")
    plan = prepare(c, profile="lexical")
    assert plan["blockers"] == ["port or launchd label occupied; nothing activated"]


# ---------------------------------------------------------------- systemd --user: the whole path

def test_linux_preview_plans_a_unit_shows_linger_and_writes_nothing(ubuntu):
    c = ubuntu
    before = tree(c.root)
    plan = prepare(c, profile="lexical", clients=("cursor", "codex"))
    assert plan["ok"] is True, plan["blockers"]
    service = plan["service"]
    assert service["manager"] == "systemd-user" and service["path"] == str(c.home / UNIT)
    assert service["state"] == "absent" and service["loaded_state"] == "absent" and "plist" not in service
    lines = service["unit"].splitlines()
    assert "StandardError=append:" + str(c.settings.ops_dir / "logs/server.err") in lines
    assert 'Environment="MEMORY_V4_RETRIEVAL_PROFILE=lexical"' in lines
    assert 'Environment="MEMORY_V4_STORE_BACKEND=sqlite_s1"' in lines
    assert lines[7].startswith(f'ExecStart="{c.python}" "-m" "memory_os_v4.server" ')
    # loginctl enable-linger: shown as an option with its effect, never run.
    assert service["linger"]["command"] == 'loginctl enable-linger "$USER"' and service["linger"]["run_by_this_tool"] is False
    assert any("loginctl enable-linger" in notice and "after logout" in notice and "never run" in notice
               for notice in plan["notices"])
    assert set(c.system.verbs("systemctl")) == {"show"} and c.system.verbs("launchctl") == []
    assert tree(c.root) == before


def test_linux_install_attest_undo_with_service_keeps_data_and_removes_created_files(ubuntu):
    c = ubuntu
    absent(c, "cursor")  # cursor has no configuration before the install; codex has one
    codex_before = (c.home / PATHS["codex"]).read_bytes()
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    assert result["ok"] is True, result.get("error")
    unit = c.home / UNIT
    assert unit.is_file() and unit.stat().st_mode & 0o777 == 0o600
    assert result["service"]["manager"] == "systemd-user"
    assert result["steps"]["service"]["created"] is True
    assert result["steps"]["service"]["unit_sha256"] == qs._sha(unit) and "plist_sha256" not in result["steps"]["service"]
    assert c.system.verbs("systemctl")[-4:] != [] and "enable" in c.system.verbs("systemctl")
    order = [verb for verb in c.system.verbs("systemctl") if verb != "show"]
    assert order == ["daemon-reload", "enable"]
    assert c.system.verbs("launchctl") == []
    # Backend and profile are read back from the unit; the job is attested through ``show``.
    assert qs._installed_profile(c.settings, c.home) == "lexical" and qs._selected_backend(c.settings, c.home) == "sqlite"
    again = prepare(c, profile=None, clients=("cursor", "codex"))
    assert again["profile"] == "lexical" and again["service"]["state"] == "owned"
    assert again["service"]["loaded_state"] == "loaded"
    assert qs._job_observation(again["service"], run=c.system)["pid"] == 4242

    plant_data(c)
    log = c.settings.ops_dir / "logs/server.err"
    log.write_bytes(b"line written by the server\n")
    data = data_inventory(c)
    journal = journal_of(c, result["stamp"])
    calls_before = len(c.system.calls)
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["ok"] is True, undone
    assert undone["service"]["undo"] == "stopped-and-unit-moved" and undone["service"]["daemon_reload"] == "ok"
    moved = Path(undone["service"]["unit_moved_to"])
    assert not unit.exists() and moved.parent == journal and moved.is_file()
    # The sequence: attestation, ONE stop, absence observed, definition removed, then systemd told once.
    assert [argv[2] for argv in c.system.calls[calls_before:]] == ["show", "disable", "show", "daemon-reload"]
    # Undo (b): the file this install created is gone with its directory; the pre-existing one is as before.
    by_client = {item["client"]: item for item in undone["results"]}
    assert by_client["cursor"]["undo"] == "restored" and by_client["cursor"]["file"] == "removed-created-by-install"
    assert not (c.home / PATHS["cursor"]).exists() and not (c.home / ".cursor").exists()
    assert Path(by_client["cursor"]["file_moved_to"]).parent == journal
    assert by_client["codex"] == {"client": "codex", "undo": "restored"}
    assert (c.home / PATHS["codex"]).read_bytes() == codex_before
    # Data kept, log untouched (append-only for the service, never rewritten by us).
    assert data_inventory(c) == data and undone["data_restored_from_copy"] is False
    assert log.read_bytes() == b"line written by the server\n"
    assert {item["kind"] for item in undone["retained"]} >= {"store", "token", "journals", "logs"}


def test_linux_uninstall_stops_the_unit_last_and_keeps_every_byte_of_data(ubuntu):
    c = ubuntu
    absent(c, "cursor", "codex")
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    assert result["ok"] is True, result.get("error")
    plant_data(c)
    data = data_inventory(c)
    un = uninstaller()
    settings = un.settings_from_service(OpsSettings("http://127.0.0.1:1/mcp/", c.home / "x", c.home / "y", c.home / "z",
                                                    c.home / "k", c.home / "o", "test.memory-os"), c.home)
    assert settings.url == c.settings.url and settings.store_dir == c.settings.store_dir  # read from the unit
    before = tree(c.root)
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert tree(c.root) == before
    assert plan["ok"] is True and plan["service"]["action"] == "stop" and plan["service"]["manager"] == "systemd-user"
    assert plan["service"]["state"] == "owned" and plan["service"]["loaded_state"] == "loaded"
    planned_files = {item["client"]: item.get("file_action") for item in plan["clients"] if item["action"] == "remove"}
    assert planned_files == {"cursor": "remove-created-by-install", "codex": "remove-created-by-install"}
    calls_before = len(c.system.calls)
    done = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert done["ok"] is True, done
    assert done["service"]["outcome"] == "stopped-and-unit-moved" and not (c.home / UNIT).exists()
    assert Path(done["service"]["unit_moved_to"]).parent == Path(done["journal"]).parent
    tail = [argv[2] for argv in c.system.calls[calls_before:] if argv[0] == "systemctl"]
    assert tail[-3:] == ["disable", "show", "daemon-reload"] and tail.count("disable") == 1
    for key in ("cursor", "codex"):
        assert not (c.home / PATHS[key]).exists() and not (c.home / PATHS[key]).parent.exists()
    statuses = {item["client"]: item for item in done["clients"] if item["client"] in ("cursor", "codex")}
    assert all(item["status"] == "removed" and item["file"] == "removed-created-by-install" for item in statuses.values())
    assert data_inventory(c) == data and done["data_deleted"] is False
    assert un.plan(c.settings, home=c.home, python=c.python, run=c.system)["nothing_to_do"] is True


@pytest.mark.parametrize("mode, outcome, code", [
    ("fail", "stop-failed", "service_stop_failed"),
    ("timeout", "stop-timeout", "service_stop_timeout"),
    ("lingers", "stop-unconfirmed", "service_stop_unconfirmed"),
])
def test_linux_unit_stays_in_place_when_the_stop_is_not_confirmed(ubuntu, mode, outcome, code):
    c = ubuntu
    result = install(c, profile="lexical")
    c.system.bootout_mode = mode
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0.5, sleep=lambda s: None)
    assert undone["ok"] is False and undone["code"] == code and undone["service"]["undo"] == outcome
    assert (c.home / UNIT).is_file() and c.system.verbs("systemctl").count("disable") == 1
    assert "daemon-reload" not in c.system.verbs("systemctl")[-2:]


@pytest.mark.parametrize("observation", ["error", "timeout"])
def test_linux_observation_failure_after_the_stop_is_indeterminate(ubuntu, observation):
    c = ubuntu
    result = install(c, profile="lexical")
    c.system.observe_after_stop = observation
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0.5, sleep=lambda s: None)
    assert undone["service"] == {"undo": "stop-unconfirmed", "observation": "indeterminate", "port": "free"}
    assert (c.home / UNIT).is_file()


@pytest.mark.parametrize("change", ["replaced", "drop-in", "reload"])
def test_linux_unit_that_is_no_longer_attested_is_never_stopped(ubuntu, change):
    c = ubuntu
    result = install(c, profile="lexical")
    if change == "replaced":
        c.system.job_replaced = True
    elif change == "drop-in":
        c.system.drop_ins = str(c.home / UNIT) + ".d/override.conf"
    else:
        c.system.need_reload = "yes"
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["service"] == {"undo": "refused-job-not-attested"} and undone["code"] == "service_changed_since_install"
    assert "disable" not in c.system.verbs("systemctl") and (c.home / UNIT).is_file()


def test_linux_unit_changed_since_the_install_is_refused(ubuntu):
    c = ubuntu
    result = install(c, profile="lexical")
    unit = c.home / UNIT
    unit.write_text(unit.read_text() + "# edited by hand\n")
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["service"] == {"undo": "refused-changed-since-install"} and unit.is_file()
    assert "disable" not in c.system.verbs("systemctl")


@pytest.mark.parametrize("step", ["daemon-reload", "enable"])
def test_linux_failed_start_keeps_the_unit_and_is_rolled_back_without_a_stop(ubuntu, step):
    c = ubuntu
    if step == "enable":
        c.system.bootstrap_fails = True
    else:
        c.system.reload_fails = True
    result = install(c, profile="lexical")
    assert result["ok"] is False and result["code"] == "service_bootstrap_failed"
    assert result["error"].startswith("systemd enable failed; unit/journal retained")
    assert f"--undo {result['stamp']} --with-service" in result["error"]
    assert (c.home / UNIT).is_file() and result.get("clients") is None
    c.system.reload_fails = False
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["service"]["undo"] == "unit-moved-job-was-absent" and not (c.home / UNIT).exists()
    assert "disable" not in c.system.verbs("systemctl")


def test_linux_never_runs_loginctl_or_launchctl(ubuntu):
    c = ubuntu
    result = install(c, profile="lexical")
    qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert {argv[0] for argv in c.system.calls} == {"systemctl"}  # FakeSystem refuses any other command


def test_a_systemd_journal_is_never_stopped_through_another_manager(ubuntu, monkeypatch):
    c = ubuntu
    result = install(c, profile="lexical")
    install_json = journal_of(c, result["stamp"]) / "install.json"
    document = json.loads(install_json.read_text())
    document["service"]["manager"] = "upstart"
    install_json.write_text(json.dumps(document))
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["service"] == {"undo": "refused-job-not-attested"} and (c.home / UNIT).is_file()


# ---------------------------------------------------------------- the whole path through scripts in PATH

@pytest.mark.parametrize("kind", ["launchd", "systemd-user"])
def test_install_attest_stop_undo_uninstall_through_scripts(c, monkeypatch, scripts, kind):
    """install → attest → stop → undo (b) → reinstall → uninstall, with the real ``subprocess.run``."""
    if kind == "systemd-user":
        report = linux_cell()
        monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
        monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {"profile_id": "synthetic"})
    tool, definition = ("launchctl", PLIST) if kind == "launchd" else ("systemctl", UNIT)
    noun = "plist" if kind == "launchd" else "unit"
    c.system = scripts
    absent(c, "cursor")
    result = install(c, profile="lexical")
    assert result["ok"] is True, result.get("error")
    assert (c.home / definition).is_file() and scripts.state()["loaded"] is True
    assert prepare(c, profile=None)["service"]["loaded_state"] == "loaded"
    undone = qs.undo(result["stamp"], home=c.home, run=scripts, with_service=True, sleep=lambda s: None)
    assert undone["ok"] is True and undone["service"]["undo"] == f"stopped-and-{noun}-moved"
    assert scripts.state()["loaded"] is False and not (c.home / definition).exists()
    assert undone["results"][0]["file"] == "removed-created-by-install" and not (c.home / ".cursor").exists()
    stop = "bootout" if kind == "launchd" else "disable"
    assert scripts.verbs(tool).count(stop) == 1

    second = install(c, profile="lexical")  # the machine is as before the first install: it installs again
    assert second["ok"] is True, second.get("error")
    plant_data(c)
    data = data_inventory(c)
    un = uninstaller()
    done = un.apply(un.plan(c.settings, home=c.home, python=c.python, run=scripts), c.settings, home=c.home,
                    run=scripts, sleep=lambda s: None)
    assert done["ok"] is True and done["service"]["outcome"] == f"stopped-and-{noun}-moved"
    assert not (c.home / definition).exists() and not (c.home / ".cursor").exists()
    assert scripts.state()["loaded"] is False and scripts.verbs(tool).count(stop) == 2
    assert data_inventory(c) == data
    assert {argv[0] for argv in scripts.calls} == {tool}


# ---------------------------------------------------------------- macOS: the launchd path is unchanged

def test_macos_install_and_rollback_run_exactly_the_4_1_0_commands(c):
    result = install(c, profile="lexical")
    assert result["ok"] is True
    plist, uid = c.home / PLIST, os.getuid()
    assert result["steps"]["service"] == {**result["steps"]["service"], "created": True, "plist_sha256": qs._sha(plist)}
    assert "manager" not in result["service"] and "unit_sha256" not in result["steps"]["service"]
    target = ["launchctl", "print", f"gui/{uid}/test.memory-os"]
    launchctl = [argv for argv in c.system.calls if argv[0] == "launchctl"]
    assert [argv for argv in launchctl if argv[1] != "print"] == [["launchctl", "bootstrap", f"gui/{uid}", str(plist)]]
    assert all(argv == target for argv in launchctl if argv[1] == "print")
    before = len(c.system.calls)
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert c.system.calls[before:] == [target, ["launchctl", "bootout", f"gui/{uid}/test.memory-os"], target]
    assert set(undone["service"]) == {"undo", "plist_moved_to"} and undone["service"]["undo"] == "stopped-and-plist-moved"
    assert undone["results"] == [{"client": "cursor", "undo": "restored"}]  # a pre-existing file: output as in 4.1.0
    assert c.system.verbs("systemctl") == []


def test_macos_uninstall_output_for_pre_existing_files_has_no_new_key(c):
    install(c, profile="lexical", clients=("cursor", "codex"))
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert all("file_action" not in item for item in plan["clients"])
    assert "manager" not in plan["service"]
    done = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert done["ok"] is True and set(done["service"]) == {"outcome", "remaining_clients", "remaining_dependencies",
                                                          "plist_moved_to"}
    assert all("file" not in item for item in done["clients"])
    assert (c.home / PATHS["cursor"]).is_file() and (c.home / PATHS["codex"]).is_file()


# ---------------------------------------------------------------- undo option (b)

def test_the_journal_attests_prior_absence_before_the_write_and_the_digest_after(c):
    absent(c, "cursor")
    result = install(c, profile="lexical", clients=("cursor", "codex"))
    journal = json.loads((journal_of(c, result["stamp"]) / "journal.json").read_text())
    records = {record["client"]: record for record in journal["records"]}
    cursor = c.home / PATHS["cursor"]
    assert records["cursor"]["file_absent_before"] is True
    assert records["cursor"]["file_created_sha256"] == qs._sha(cursor)
    assert records["cursor"]["dirs_absent_before"] == [str(cursor.parent)]
    assert "backup" not in records["cursor"]
    assert "file_absent_before" not in records["codex"] and "file_created_sha256" not in records["codex"]
    assert records["codex"]["backup_sha256"]


def test_prior_absence_is_journaled_even_when_the_write_fails(c, monkeypatch):
    absent(c, "cursor")
    plan = prepare(c, profile="lexical")
    monkeypatch.setattr(qs, "_write_client", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(qs.QuickstartError):
        qs.apply_clients(plan, home=c.home, python=c.python, env=plan["client_env"], run=c.system)
    stamp = sorted((c.home / STATE / "quickstart").iterdir())[-1]
    record = json.loads((stamp / "journal.json").read_text())["records"][0]
    assert record["file_absent_before"] is True and "file_created_sha256" not in record
    assert record["status"] == "failed-or-partial"


@pytest.mark.parametrize("key", ["cursor", "codex", "vscode", "antigravity"])
def test_rollback_removes_a_client_file_the_install_created(c, key):
    absent(c, key)
    before = tree(c.home / PATHS[key].split("/")[0]) if (c.home / PATHS[key].split("/")[0]).exists() else None
    result = install(c, profile="lexical", clients=(key,))
    path = c.home / PATHS[key]
    created = path.read_bytes()
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    item = undone["results"][0]
    assert undone["ok"] is True and item["undo"] == "restored" and item["file"] == "removed-created-by-install"
    assert not path.exists() and not path.parent.exists()
    moved = Path(item["file_moved_to"])
    assert moved.parent == journal_of(c, result["stamp"]) and moved.read_bytes() == created  # moved, never destroyed
    assert item["dirs_removed"][0] == str(path.parent)
    if before is not None:  # directories that existed before (e.g. ``Library``) are still there, as they were
        assert (c.home / PATHS[key].split("/")[0]).is_dir()


def test_the_claude_code_file_is_never_taken_as_created_by_the_install(c):
    """``~/.claude.json`` is written by the ``claude`` CLI: its whole content is not proven ours."""
    absent(c, "claude-code")
    result = install(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/local/bin/" + name)
    path = c.home / PATHS["claude-code"]
    record = json.loads((journal_of(c, result["stamp"]) / "journal.json").read_text())["records"][0]
    assert record["status"] == "applied" and record["method"] == "claude-cli"
    assert "file_absent_before" not in record and "file_created_sha256" not in record
    # Even a journal claiming both attestations does not make a CLI-written file removable.
    forged = {**record, "file_absent_before": True, "file_created_sha256": qs._sha(path)}
    assert qs.created_file(forged, path) is None
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"] == [{"client": "claude-code", "undo": "restored"}]
    assert c.system.calls[-1][:3] == ["claude", "mcp", "remove"] and path.is_file()
    assert servers(c.home, "claude-code") == {}
    un = uninstaller()
    second = install(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/local/bin/" + name)
    assert second["ok"] is True
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert all("file_action" not in item for item in plan["clients"])


@pytest.mark.parametrize("key", ["cursor", "codex"])
def test_a_third_party_writing_between_our_write_and_the_digest_leaves_no_proof_of_creation(c, monkeypatch, key):
    """The digest journaled is the one of the bytes WE produced; a file that already differs carries no proof."""
    absent(c, key)
    path = c.home / PATHS[key]
    real_write = qs._write_client
    ours = {}

    def write_then_someone_else_writes(*args, **kwargs):
        method = real_write(*args, **kwargs)
        ours["bytes"] = path.read_bytes()
        # Another application adds its own configuration right after our write, before any read-back.
        if key == "codex":
            path.write_bytes(ours["bytes"] + b'\n[mcp_servers.theirs]\ncommand = "theirs"\n')
        else:
            data = json.loads(ours["bytes"])
            data["mcpServers"]["theirs"] = {"command": "theirs"}
            path.write_text(json.dumps(data, indent=2))
        return method
    monkeypatch.setattr(qs, "_write_client", write_then_someone_else_writes)
    result = install(c, profile="lexical", clients=(key,))
    assert result["ok"] is True
    record = json.loads((journal_of(c, result["stamp"]) / "journal.json").read_text())["records"][0]
    assert record["status"] == "applied" and record["file_absent_before"] is True
    assert "file_created_sha256" not in record  # no proof: what is on disk is not what we produced
    assert qs.created_file(record, path) is None
    plan = uninstaller().plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert "file_action" not in next(item for item in plan["clients"] if item["client"] == key)
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"] == [{"client": key, "undo": "restored", "file": "kept-not-proven-ours-alone"}]
    assert path.is_file() and servers(c.home, key) == {"theirs": {"command": "theirs"}}


def test_the_journaled_digest_is_computed_from_the_produced_text_before_the_write(c, monkeypatch):
    absent(c, "cursor")
    seen = []
    real_atomic = qs._atomic_write

    def atomic(path, text, *, expected):
        if path == c.home / PATHS["cursor"]:
            seen.append((qs.hashlib.sha256(text.encode("utf-8")).hexdigest(), path.exists()))
        return real_atomic(path, text, expected=expected)
    monkeypatch.setattr(qs, "_atomic_write", atomic)
    result = install(c, profile="lexical")
    record = json.loads((journal_of(c, result["stamp"]) / "journal.json").read_text())["records"][0]
    assert seen == [(record["file_created_sha256"], False)]


def test_a_created_file_changed_since_the_install_is_kept_and_only_our_entry_leaves(c):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    path = c.home / PATHS["cursor"]
    data = json.loads(path.read_text())
    data["mcpServers"]["added-by-the-user"] = {"command": "theirs"}
    path.write_text(json.dumps(data, indent=2))
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"] == [{"client": "cursor", "undo": "restored", "file": "kept-not-proven-ours-alone"}]
    assert servers(c.home, "cursor") == {"added-by-the-user": {"command": "theirs"}}


def test_a_pre_existing_file_is_never_removed_even_if_only_our_entry_is_in_it(c):
    path = c.home / PATHS["cursor"]
    path.write_text("{}\n")
    result = install(c, profile="lexical")
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"] == [{"client": "cursor", "undo": "restored"}]
    assert path.is_file() and servers(c.home, "cursor") == {}


@pytest.mark.parametrize("tamper", ["no-digest", "no-absence", "absence-not-true", "other-path", "bad-digest"])
def test_without_both_attestations_of_the_journal_the_file_stays(c, tamper):
    """An older journal, or one that does not attest prior absence AND the created digest: semantic removal."""
    absent(c, "cursor")
    result = install(c, profile="lexical")
    journal = journal_of(c, result["stamp"]) / "journal.json"
    document = json.loads(journal.read_text())
    record = document["records"][0]
    if tamper == "no-digest":
        del record["file_created_sha256"]
    elif tamper == "no-absence":
        del record["file_absent_before"]
    elif tamper == "absence-not-true":
        record["file_absent_before"] = "yes"
    elif tamper == "bad-digest":
        record["file_created_sha256"] = "0" * 64
    path = c.home / PATHS["cursor"]
    assert qs.created_file({**record, "path": "/elsewhere"} if tamper == "other-path" else record, path) is None
    if tamper != "other-path":
        journal.write_text(json.dumps(document))
        undone = qs.undo(result["stamp"], home=c.home, run=c.system)
        assert undone["results"][0]["undo"] == "restored" and undone["results"][0].get("file") != "removed-created-by-install"
        assert path.is_file() and servers(c.home, "cursor") == {}


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_a_created_file_that_became_a_link_is_not_removed(c, kind):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    path = c.home / PATHS["cursor"]
    record = json.loads((journal_of(c, result["stamp"]) / "journal.json").read_text())["records"][0]
    if kind == "symlink":
        real = c.home / "real.json"
        path.rename(real)
        path.symlink_to(real)
    else:
        os.link(path, c.home / "alias.json")
    assert qs.created_file(record, path) is None
    assert qs.remove_created_file(record, path, c.home, journal_of(c, result["stamp"]), "x") is None
    assert os.path.lexists(path)


def test_a_directory_the_install_created_stays_when_something_else_is_in_it(c):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    other = c.home / ".cursor/settings-of-the-user.json"
    other.write_text("{}")
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"][0]["file"] == "removed-created-by-install" and undone["results"][0]["dirs_removed"] == []
    assert other.read_text() == "{}" and not (c.home / PATHS["cursor"]).exists()


def test_recorded_directories_outside_the_parents_of_the_file_are_never_removed(c, tmp_path):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    victim = c.home / "empty-directory-of-the-user"
    victim.mkdir()
    journal = journal_of(c, result["stamp"])
    record = json.loads((journal / "journal.json").read_text())["records"][0]
    record["dirs_absent_before"] = [str(victim), str(c.home), str(tmp_path)]
    gone = qs.remove_created_file(record, c.home / PATHS["cursor"], c.home, journal, "cursor.created-by-install")
    assert gone["dirs_removed"] == [] and victim.is_dir() and c.home.is_dir()


def test_a_second_rollback_has_nothing_to_do(c):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    qs.undo(result["stamp"], home=c.home, run=c.system)
    again = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert again["results"] == [{"client": "cursor", "undo": "nothing-to-undo"}] and again["ok"] is True


def test_a_file_recreated_by_hand_after_the_rollback_is_not_ours(c):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    path = c.home / PATHS["cursor"]
    created = path.read_bytes()
    qs.undo(result["stamp"], home=c.home, run=c.system)
    path.parent.mkdir()
    path.write_bytes(created)  # the same bytes, back by hand: the proof was consumed
    again = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert again["results"] == [{"client": "cursor", "undo": "refused-proof-revoked"}] and path.read_bytes() == created
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    cursor = next(item for item in plan["clients"] if item["client"] == "cursor")
    assert cursor["action"] == "keep-unproven" and "file_action" not in cursor


def test_uninstall_removes_created_files_and_only_them(c):
    absent(c, "cursor")
    codex_before = (c.home / PATHS["codex"]).read_bytes()
    install(c, profile="lexical", clients=("cursor", "codex"))
    plant_data(c)
    data = data_inventory(c)
    un = uninstaller()
    before = tree(c.root)
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert tree(c.root) == before
    actions = {item["client"]: item.get("file_action") for item in plan["clients"]}
    assert actions["cursor"] == "remove-created-by-install" and actions["codex"] is None
    done = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert done["ok"] is True and done["data_deleted"] is False
    statuses = {item["client"]: item for item in done["clients"]}
    assert statuses["cursor"]["status"] == "removed" and statuses["cursor"]["file"] == "removed-created-by-install"
    assert Path(statuses["cursor"]["file_moved_to"]).parent == Path(done["journal"]).parent
    assert not (c.home / PATHS["cursor"]).exists() and not (c.home / ".cursor").exists()
    assert statuses["codex"]["status"] == "removed" and "file" not in statuses["codex"]
    assert (c.home / PATHS["codex"]).read_bytes() == codex_before
    assert data_inventory(c) == data


def test_uninstall_keeps_a_created_file_changed_since_the_preview(c):
    absent(c, "cursor")
    install(c, profile="lexical")
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    path = c.home / PATHS["cursor"]
    path.write_text(path.read_text() + "\n")
    with pytest.raises(un.UninstallError) as refusal:
        un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert refusal.value.code == "uninstall_changed_since_preview" and path.is_file()


def test_uninstall_with_consent_never_removes_the_file_of_an_unproven_entry(c):
    absent(c, "cursor")
    result = install(c, profile="lexical")
    journal = journal_of(c, result["stamp"]) / "journal.json"
    document = json.loads(journal.read_text())
    document["records"][0].update(revoked=True, revoked_by="test")
    journal.write_text(json.dumps(document))
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system, remove_existing=("cursor",))
    cursor = next(item for item in plan["clients"] if item["client"] == "cursor")
    assert cursor["ownership"] == "consent" and "file_action" not in cursor
    done = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert (c.home / PATHS["cursor"]).is_file() and servers(c.home, "cursor") == {}
    assert next(item for item in done["clients"] if item["client"] == "cursor")["status"] == "removed"


def test_rollback_and_uninstall_never_delete_data_whatever_the_files_created(c):
    absent(c, "cursor", "codex", "vscode")
    result = install(c, profile="lexical", clients=("cursor", "codex", "vscode"))
    plant_data(c)
    log = c.settings.ops_dir / "logs/server.err"
    log.write_bytes(b"server line\n")
    data = data_inventory(c)
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert undone["ok"] is True and data_inventory(c) == data and log.read_bytes() == b"server line\n"
    assert c.settings.token_file.is_file() and c.settings.store_dir.is_dir()
    for key in ("cursor", "codex", "vscode"):
        assert not (c.home / PATHS[key]).exists()


# ---------------------------------------------------------------- review round 6: confirmed stops, job without file

def crash_install(c, monkeypatch, *, guard=False):
    """An install whose service crashes at start (or breaches the resource guard): the stop path runs."""
    from memory_os_v4.ops import provision
    monkeypatch.setattr(qs, "_ready", lambda *a, **k: None)
    ticks = [0.0]  # the pauses ARE the time: a wait bounded by a monotonic clock ends without real waiting
    monkeypatch.setattr(qs.time, "sleep", lambda s: ticks.__setitem__(0, ticks[0] + s))
    monkeypatch.setattr(qs.time, "monotonic", lambda: ticks[0])
    if guard:
        counts = []

        def breach(*args):
            counts.append(1)
            if len(counts) > 1:
                raise provision.ProvisionError("Swap above cap")
        monkeypatch.setattr(provision, "guard", breach)
    else:
        c.system.crashing = (2, 1)
    plan = prepare(c, profile="lexical")
    assert plan["ok"], plan["blockers"]
    return qs.apply_install(plan, c.settings, home=c.home, run=c.system, clock=c.clock, stop_timeout=0.5)


def stop_report(result, guard):
    if guard:
        return result["guard_stop"]["stopped"], result["guard_stop"]["outcome"]
    return result["startup_failure"]["stopped"], result["startup_failure"]["stop_outcome"]


@pytest.fixture(params=["launchd", "systemd-user"])
def either(request, c, monkeypatch):
    if request.param == "systemd-user":
        report = linux_cell()
        monkeypatch.setattr(cell, "detect", lambda **kwargs: dict(report))
        monkeypatch.setattr(qualified_runtime, "cell_lock", lambda cell_id, *, profile: {"profile_id": "synthetic"})
    c.kind = request.param
    c.tool = "launchctl" if request.param == "launchd" else "systemctl"
    c.stop_verb = "bootout" if request.param == "launchd" else "disable"
    return c


@pytest.mark.parametrize("guard", [False, True])
def test_a_stop_during_install_is_reported_stopped_only_once_confirmed(either, monkeypatch, guard):
    c = either
    result = crash_install(c, monkeypatch, guard=guard)
    assert result["ok"] is False
    assert stop_report(result, guard) == (True, "confirmed")
    verbs = c.system.verbs(c.tool)
    assert verbs.count(c.stop_verb) == 1
    assert verbs.index(c.stop_verb) < len(verbs) - 1  # the job is observed again AFTER the stop


@pytest.mark.parametrize("guard", [False, True])
@pytest.mark.parametrize("mode, outcome", [("lingers", "stop-not-confirmed"), ("fail", "stop-failed"),
                                           ("timeout", "stop-timeout")])
def test_a_zero_return_code_of_the_stop_is_not_a_stop(either, monkeypatch, guard, mode, outcome):
    """Review counter-example: the stop command returns 0 and the job is still there."""
    c = either
    c.system.bootout_mode = mode
    result = crash_install(c, monkeypatch, guard=guard)
    assert stop_report(result, guard) == (False, outcome)
    assert c.system.verbs(c.tool).count(c.stop_verb) == 1  # one stop, no retry
    if not guard:
        assert "stop not confirmed (" + outcome + ")" in result["error"]
        assert f"--undo {result['stamp']} --with-service" in result["error"]


@pytest.mark.parametrize("guard", [False, True])
@pytest.mark.parametrize("observation", ["error", "timeout"])
def test_an_unreadable_observation_after_the_stop_is_not_a_confirmed_stop(either, monkeypatch, guard, observation):
    c = either
    c.system.observe_after_stop = observation
    result = crash_install(c, monkeypatch, guard=guard)
    assert stop_report(result, guard) == (False, "stop-not-confirmed")
    detail = result["guard_stop"] if guard else None
    if detail:
        assert detail["observation"] == "indeterminate"


@pytest.mark.parametrize("guard", [False, True])
def test_a_port_still_listening_after_the_stop_is_not_a_confirmed_stop(either, monkeypatch, guard):
    c = either
    seen = []
    real_run = c.system.__call__

    def port(number, **kwargs):
        return "listening" if c.system.stop_sent else "free"
    monkeypatch.setattr(qs, "_port_state", port)
    result = crash_install(c, monkeypatch, guard=guard)
    assert stop_report(result, guard) == (False, "stop-not-confirmed")
    if guard:
        assert result["guard_stop"]["port"] == "listening" and result["guard_stop"]["observation"] == "absent"
    assert seen == [] and real_run is not None


def test_the_confirmation_of_a_stop_is_bounded(either, monkeypatch):
    c = either
    c.system.bootout_mode = "lingers"
    plan = prepare(c, profile="lexical")
    c.system.loaded = c.system.enabled = True
    pauses, now = [], [0.0]

    def sleep(seconds):
        pauses.append(seconds)
        now[0] += seconds
    confirmed = qs._confirmed_stop(plan["service"], c.settings.port, run=c.system, stop_timeout=2.0, sleep=sleep,
                                   clock=lambda: now[0])
    assert confirmed["stopped"] is False and confirmed["outcome"] == "stop-not-confirmed"
    assert sum(pauses) == pytest.approx(2.0) and len(pauses) == 8


def test_uninstall_asks_the_manager_even_when_the_definition_is_missing(either):
    """Absence of the file is not the state of the job: ``absent`` only when the manager attests it."""
    c = either
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["service"]["state"] == "absent" and plan["service"]["loaded_state"] == "absent"
    assert plan["ok"] is True and plan["nothing_to_do"] is True
    assert c.system.verbs(c.tool) == ["print" if c.kind == "launchd" else "show"]  # asked, read-only


@pytest.mark.parametrize("case", ["job-loaded", "observation-fails", "observation-times-out"])
def test_uninstall_refuses_a_job_loaded_or_unattested_without_its_definition(either, case):
    """Review counter-example: the definition is gone, the job still runs (or cannot be read)."""
    c = either
    result = install(c, profile="lexical")
    assert result["ok"] is True
    Path(result["service"]["path"]).unlink()          # the definition disappears, the job keeps running
    if case != "job-loaded":
        c.system.stop_sent = True
        c.system.observe_after_stop = "error" if case == "observation-fails" else "timeout"
    un = uninstaller()
    before = tree(c.root)
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["service"]["state"] == "absent"
    assert plan["service"]["loaded_state"] == ("foreign-or-unattested" if case == "job-loaded" else "unknown")
    assert plan["ok"] is False and plan["blocker_codes"] == ["uninstall_job_without_definition"]
    assert "job still loaded or not attested while its definition is missing; nothing changed" in plan["blockers"][0]
    assert plan["nothing_to_do"] is False
    with pytest.raises(un.UninstallError) as refusal:
        un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert refusal.value.code == "preflight_refused"
    assert tree(c.root) == before                      # nothing changed: clients, journals, data
    assert c.stop_verb not in c.system.verbs(c.tool)[-3:] and servers(c.home, "cursor").get("memory-os-ai")


def test_uninstall_apply_revalidates_the_job_when_the_definition_is_missing(either):
    c = either
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)   # nothing installed, job absent
    assert plan["ok"] is True
    c.system.service = prepare(c, profile="lexical")["service"]
    c.system.loaded = c.system.enabled = True          # a job appears under the label after the preview
    with pytest.raises(un.UninstallError) as refusal:
        un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert refusal.value.code == "uninstall_changed_since_preview"


# ---------------------------------------------------------------- backup and doctor go through the interface

def test_backup_drives_launchd_with_the_same_three_commands(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(backup, "_manager", lambda: managers.Launchd())
    monkeypatch.setattr(backup, "_launchctl", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(backup.Path, "home", classmethod(lambda cls: tmp_path))

    def run(argv, **kwargs):
        calls.append(tuple(argv[1:]))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    monkeypatch.setattr(backup.subprocess, "run", run)
    settings = SimpleNamespace(service_label="test.memory-os")
    uid = os.getuid()
    assert backup._service_loaded("test.memory-os") is True
    assert backup._service_stop("test.memory-os") == 0 and backup._service_start(settings) == 0
    assert calls == [("print", f"gui/{uid}/test.memory-os"), ("bootout", f"gui/{uid}/test.memory-os"),
                     ("bootstrap", f"gui/{uid}", str(tmp_path / PLIST))]


@pytest.fixture
def systemd_backup(monkeypatch, tmp_path, scripts):
    """A real JSONL store, OUR unit on disk (frozen ``show`` text through the script fake), no launchd."""
    manager = managers.SystemdUser(environ={"HOME": str(tmp_path / "home")})
    store = tmp_path / "store"
    store.mkdir()
    (store / "items.jsonl").write_text("")
    (store / "meta.json").write_text("{}")
    from memory_os_v4.ops import provision
    home = tmp_path / "home"
    home.mkdir()
    settings = OpsSettings("http://127.0.0.1:43219/mcp/", tmp_path / "http.token", store, tmp_path / "backups",
                           tmp_path / "cfg/backup.key", tmp_path / "ops", "test.memory-os")
    resources = {"MEMORY_V4_CL100K_PATH": str(tmp_path / "table"), "HF_HUB_OFFLINE": "1"}
    monkeypatch.setattr(provision, "plan", lambda **kwargs: {"env": dict(resources)})
    monkeypatch.setattr(managers, "host_manager", lambda: manager)
    # The unit an install by THIS interpreter writes for these settings (lexical profile).
    service = qs._service_plan(settings, home, str(Path(sys.executable).absolute()), dict(resources),
                               profile="lexical", manager=manager)
    unit = Path(service["path"])
    unit.parent.mkdir(parents=True)
    unit.write_text(service["unit"], encoding="utf-8")
    scripts.service = service
    monkeypatch.setattr(backup, "_manager", lambda: manager)
    monkeypatch.setattr(backup, "_launchctl", lambda *args: (_ for _ in ()).throw(AssertionError("launchctl")))
    monkeypatch.setattr(backup.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "free")
    monkeypatch.setattr(backup, "_wait_healthy", lambda *a, **k: True)
    monkeypatch.setattr(backup, "_responder_pid", lambda settings: 4242)   # /health answered by the job's process
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    return SimpleNamespace(settings=settings, scripts=scripts, service=service, manager=manager)


def mutations(scripts):
    return [argv[2:] for argv in scripts.calls if argv[2] != "show"]


@pytest.mark.parametrize("enabled", [False, True])
def test_backup_restores_the_initial_activity_and_activation_exactly(systemd_backup, enabled):
    """Active and DISABLED before the backup: active and disabled after it, never enabled durably."""
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=enabled)
    record = backup.backup(b.settings)
    assert "error" not in record, record
    state = {"active": True, "running": True, "pid": 4242, "unit_file_state": "enabled" if enabled else "disabled"}
    assert record["service_state_before"] == state and record["service_state_after"] == state
    assert record["service_state_restored"] is True and record["restart_rc"] == 0
    assert record["evidence"].startswith("verified_bootout")
    assert mutations(b.scripts) == [["stop", "test.memory-os.service"], ["start", "test.memory-os.service"]]
    assert b.scripts.state()["loaded"] is True and b.scripts.state()["enabled"] is enabled


@pytest.mark.parametrize("enabled", [False, True])
def test_backup_does_not_start_a_unit_that_was_not_running(systemd_backup, enabled):
    b = systemd_backup
    b.scripts.set(loaded=False, enabled=enabled)
    record = backup.backup(b.settings)
    assert "error" not in record and record["evidence"].startswith("verified_no_writer")
    assert record["service_state_before"] == {"active": False, "running": False, "pid": None,
                                              "unit_file_state": "enabled" if enabled else "disabled"}
    assert mutations(b.scripts) == [] and "restart_rc" not in record
    assert b.scripts.state()["loaded"] is False and b.scripts.state()["enabled"] is enabled


@pytest.mark.parametrize("change", [
    {"fail": {"show": 1}},                                           # the observation fails
    {"show_extra": "Warning: free text\n"},                          # a line out of form
    {"show": {"DropInPaths": None}},                                 # a property is missing
    {"show": {"DropInPaths": "/x.service.d/override.conf"}},         # something layered on our unit
    {"show": {"NeedDaemonReload": "yes"}},
    {"show": {"FragmentPath": "/etc/systemd/user/test.memory-os.service"}},   # a unit of the same name, not ours
    {"show": {"Environment": "MEMORY_V4_DIR=/elsewhere MEMORY_V4_TOKEN_FILE=/elsewhere/t"}},
])
def test_backup_never_stops_a_unit_whose_state_or_identity_is_not_attested(systemd_backup, change):
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=True, **change)
    record = backup.backup(b.settings)
    assert record["ok"] is False and "nothing stopped" in record["error"]
    assert mutations(b.scripts) == [] and "restart_rc" not in record
    assert b.scripts.state()["loaded"] is True and b.scripts.state()["enabled"] is True


CODEX_COUNTEREXAMPLE = """[Unit]
Description=Memory OS AI server (test.memory-os)

[Service]
Type=exec
ExecStart="/usr/bin/sleep" "600"
ExecStop="/bin/true"
Environment="MEMORY_V4_DIR=@STORE@"
Environment="MEMORY_V4_TOKEN_FILE=@TOKEN@"
Restart=always

[Install]
WantedBy=default.target
"""


def test_backup_refuses_a_unit_that_only_carries_the_two_expected_variables(systemd_backup, tmp_path):
    """Review counter-example: another program, the two attested variables, one more directive.

    The reference is what ``render`` produces for these settings, never the file under test:
    even when systemd reports exactly this foreign command as running at our path, nothing stops.
    """
    b = systemd_backup
    unit = Path(b.service["path"])
    unit.write_text(CODEX_COUNTEREXAMPLE.replace("@STORE@", str(b.settings.store_dir))
                    .replace("@TOKEN@", str(b.settings.token_file)))
    b.scripts.set(loaded=True, enabled=False, show={
        "ExecStart": "{ path=/usr/bin/sleep ; argv[]=/usr/bin/sleep 600 ; ignore_errors=no ; start_time=[n/a] ; "
                     "stop_time=[n/a] ; pid=4242 ; code=(null) ; status=0/0 }"})
    # The file-derived reference of the previous revision would have attested it:
    read = b.manager.read_definition(unit)
    assert b.manager.observe({"label": "test.memory-os", "path": str(unit), "argv": read["argv"], "env": read["env"]},
                             run=b.scripts)["state"] == "loaded"
    record = backup.backup(b.settings)
    assert record["ok"] is False and "unit not written by this installation" in record["error"]
    assert "nothing stopped" in record["error"] and mutations(b.scripts) == [] and "restart_rc" not in record
    assert b.scripts.state()["loaded"] is True


@pytest.mark.parametrize("edit", [
    lambda text: text.replace("Restart=always", "Restart=always\nExecStop=/bin/true"),           # one directive more
    lambda text: text.replace("[Install]", "[Socket]\nListenStream=1\n\n[Install]"),           # one section more
    lambda text: text.replace("--warm", "--cold"),                                                # another argument
    lambda text: text.replace('"-m" "memory_os_v4.server"', '"-m" "other.server"'),
    lambda text: text.replace("StandardError=append:", "StandardError=file:"),                    # another log mode
    lambda text: text.replace('Environment="MEMORY_V4_BM25_PROFILE=scoped_literal"\n', ""),       # one variable less
    lambda text: text + "ExecStartPre=/bin/other \\\n  continued\n",                             # not our shape
])
def test_backup_refuses_any_unit_that_render_would_not_produce(systemd_backup, edit):
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=True)
    unit = Path(b.service["path"])
    original = unit.read_text()
    assert edit(original) != original
    unit.write_text(edit(original))
    record = backup.backup(b.settings)
    assert record["ok"] is False and "nothing stopped" in record["error"] and mutations(b.scripts) == []


def test_backup_refuses_a_unit_of_another_store_or_another_interpreter_or_no_definition(systemd_backup, tmp_path):
    from dataclasses import replace
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=True)
    unit = Path(b.service["path"])
    other = replace(b.settings, store_dir=tmp_path / "another-store")
    assert "nothing stopped" in backup.backup(other)["error"]          # settings of another installation
    original = unit.read_text()
    unit.write_text(original.replace(str(Path(sys.executable).absolute()), "/usr/bin/python3"))
    assert "unit not written by this installation" in backup.backup(b.settings)["error"]   # no journal attests that file
    unit.unlink()  # no definition of ours, yet systemd runs a unit of that name
    assert "nothing stopped" in backup.backup(b.settings)["error"]
    assert mutations(b.scripts) == []


def test_backup_says_when_the_state_could_not_be_put_back(systemd_backup):
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=False, fail={"start": 1})
    record = backup.backup(b.settings)
    assert record["restart_rc"] == 1 and record["service_state_restored"] is False
    assert record["service_state_after"] == {"active": False, "running": False, "pid": None,
                                             "unit_file_state": "disabled"}
    assert b.scripts.state()["enabled"] is False  # still never enabled


def test_backup_systemd_commands_never_touch_the_activation(systemd_backup):
    b = systemd_backup
    assert b.manager.pause_argv("l") == ["systemctl", "--user", "stop", "l.service"]
    assert b.manager.resume_argv("l") == ["systemctl", "--user", "start", "l.service"]
    b.scripts.set(loaded=True, enabled=False)
    assert backup._service_stop("test.memory-os") == 0 and backup._service_start(b.settings) == 0
    assert not {"enable", "disable", "daemon-reload"} & set(b.scripts.verbs("systemctl"))


@pytest.fixture
def launchd_backup(monkeypatch, tmp_path, scripts):
    """A real JSONL store and OUR plist on disk; ``launchctl`` is the script of the temporary PATH."""
    from memory_os_v4.ops import provision
    manager = managers.Launchd()
    home = tmp_path / "home"
    home.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (store / "items.jsonl").write_text("")
    (store / "meta.json").write_text("{}")
    settings = OpsSettings("http://127.0.0.1:43219/mcp/", tmp_path / "http.token", store, tmp_path / "backups",
                           tmp_path / "cfg/backup.key", tmp_path / "ops", "test.memory-os")
    resources = {"MEMORY_V4_CL100K_PATH": str(tmp_path / "table"), "HF_HUB_OFFLINE": "1"}
    monkeypatch.setattr(provision, "plan", lambda **kwargs: {"env": dict(resources)})
    monkeypatch.setattr(managers, "host_manager", lambda: manager)
    monkeypatch.setattr(backup, "_manager", lambda: manager)
    service = qs._service_plan(settings, home, str(Path(sys.executable).absolute()), dict(resources),
                               profile="lexical", manager=manager)
    plist = Path(service["path"])
    plist.parent.mkdir(parents=True)
    plist.write_text(service["plist"], encoding="utf-8")
    scripts.service = service
    monkeypatch.setattr(backup.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "free")
    monkeypatch.setattr(backup, "_wait_healthy", lambda *a, **k: True)
    monkeypatch.setattr(backup, "_responder_pid", lambda settings: 4242)   # /health answered by the job's process
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    return SimpleNamespace(settings=settings, scripts=scripts, service=service, plist=plist, manager=manager)


def test_launchd_backup_of_an_attested_job_runs_the_same_commands_as_before(launchd_backup):
    b = launchd_backup
    b.scripts.set(loaded=True)
    record = backup.backup(b.settings)
    assert "error" not in record and record["evidence"].startswith("verified_bootout") and record["restart_rc"] == 0
    uid = os.getuid()
    target = f"gui/{uid}/test.memory-os"
    # print (attested before), bootout, print (stop observed), print (re-attested before the writer and
    # port probes), print (re-attested AFTER them, right before the start: review round 10), bootstrap,
    # print (process of our job attested after the health wait)
    assert b.scripts.calls == [["launchctl", "print", target], ["launchctl", "bootout", target],
                               ["launchctl", "print", target], ["launchctl", "print", target],
                               ["launchctl", "print", target],
                               ["launchctl", "bootstrap", f"gui/{uid}", str(b.plist)],
                               ["launchctl", "print", target]]
    assert record["service_restored"] is True and record["archive_ok"] is True and record["ok"] is True
    assert not {"service_state_before", "service_state_after", "service_state_restored"} & set(record)
    assert b.scripts.state()["loaded"] is True


def test_launchd_backup_of_a_stopped_attested_job_stops_and_starts_nothing(launchd_backup):
    b = launchd_backup
    record = backup.backup(b.settings)
    assert "error" not in record and record["evidence"].startswith("verified_no_writer")
    assert b.scripts.verbs("launchctl") == ["print"] and "restart_rc" not in record


def test_launchd_backup_never_boots_out_a_job_that_is_not_attested_ours(launchd_backup):
    """Review counter-example: ``print`` succeeds, but the loaded job is another program under our label."""
    b = launchd_backup
    foreign = dict(b.scripts.service, argv=["/usr/bin/sleep", "600"])
    b.scripts.set(loaded=True, service=foreign)
    record = backup.backup(b.settings)
    assert record["ok"] is False and "cannot be attested" in record["error"] and "nothing stopped" in record["error"]
    assert b.scripts.verbs("launchctl") == ["print"] and "restart_rc" not in record
    assert b.scripts.state()["loaded"] is True


@pytest.mark.parametrize("case", ["print-fails", "other-store", "no-definition", "definition-edited", "other-token"])
def test_launchd_backup_refuses_before_any_bootout(launchd_backup, tmp_path, case):
    b = launchd_backup
    b.scripts.set(loaded=True)
    settings = b.settings
    if case == "print-fails":
        b.scripts.set(fail={"print": 1})                 # IPC failure: indeterminate, never "not loaded"
    elif case == "other-store":
        from dataclasses import replace
        settings = replace(settings, store_dir=tmp_path / "another-store")
    elif case == "no-definition":
        b.plist.unlink()                                 # a job runs under our label, without a plist of ours
    elif case == "definition-edited":
        b.plist.write_text(b.plist.read_text().replace("<string>-m</string><string>memory_os_v4.server</string>",
                                                       "<string>-m</string><string>other.server</string>"))
    else:
        b.scripts.set(service=dict(b.scripts.service, env={**b.service["env"], "MEMORY_V4_TOKEN_FILE": "/elsewhere"}))
    record = backup.backup(settings)
    assert record["ok"] is False and "nothing stopped" in record["error"], record
    assert "bootout" not in b.scripts.verbs("launchctl") and "bootstrap" not in b.scripts.verbs("launchctl")
    assert b.scripts.state()["loaded"] is True


@pytest.mark.parametrize("argv", [
    ["/tmp/foreign/bin/python3", "-m", "memory_os_v4.server"],          # review counter-example (round 5)
    ["/opt/venv/bin/python3.11", "-m", "memory_os_v4.server", "--transport", "http", "--port", "43219"],
    ["/usr/bin/sleep", "600"],
])
def test_launchd_backup_refuses_any_plist_this_installation_did_not_write(launchd_backup, tmp_path, argv):
    """No manual way in: a plist that ``render`` would not produce and no install journal attests is
    refused, even with a Python running our server module and the token file and store of these settings."""
    from memory_os_v4.ops.cli import _plist
    b = launchd_backup
    env = {"MEMORY_V4_TOKEN_FILE": str(b.settings.token_file), "MEMORY_V4_DIR": str(b.settings.store_dir)}
    b.plist.write_text(_plist("test.memory-os", argv, env=env, keepalive=True, log=tmp_path / "server.err"))
    b.scripts.set(loaded=True, service=dict(b.scripts.service, argv=argv, env=env))  # and the job runs exactly that
    record = backup.backup(b.settings)
    assert record["ok"] is False
    assert "plist not written by this installation; nothing stopped; reinstall with quickstart or stop the service " \
           "yourself" in record["error"]
    assert "bootout" not in b.scripts.verbs("launchctl") and "bootstrap" not in b.scripts.verbs("launchctl")
    assert b.scripts.state()["loaded"] is True and "restart_rc" not in record


def test_there_is_no_manual_way_into_the_backup_attestation():
    import inspect
    source = inspect.getsource(backup)
    assert "_legacy_launchd_plan" not in source and "read_definition" not in source


def test_backup_falls_back_to_launchd_where_no_manager_exists(monkeypatch):
    monkeypatch.setattr(managers, "host_manager", lambda: None)
    assert isinstance(backup._manager(), managers.Launchd)


def doctor_settings(tmp_path, label="test.memory-os"):
    return OpsSettings("http://127.0.0.1:9/mcp/", tmp_path / "t", tmp_path / "s", tmp_path / "b", tmp_path / "k",
                       tmp_path / "o", label)


@pytest.mark.parametrize("code, status, detail", [
    (0, "PASS", "launchd job test.memory-os loaded"),
    (113, "FAIL", "launchd job test.memory-os not loaded"),
])
def test_doctor_keeps_its_launchd_command_and_messages(monkeypatch, tmp_path, code, status, detail):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=code, stdout=b"", stderr=b"Could not find service" if code else b"")
    monkeypatch.setattr(managers, "host_manager", lambda: managers.Launchd())
    monkeypatch.setattr(doctor.subprocess, "run", run)
    check = doctor._service(doctor_settings(tmp_path))
    assert (check.status, check.detail) == (status, detail)
    assert calls == [(["launchctl", "print", f"gui/{os.getuid()}/test.memory-os"],
                      {"capture_output": True, "check": False, "timeout": 5})]
    if status == "FAIL":
        assert check.hint == "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"


def test_doctor_launchd_warnings_are_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(managers, "host_manager", lambda: managers.Launchd())
    unset = doctor._service(doctor_settings(tmp_path, label=None))
    assert (unset.status, unset.detail) == ("WARN", "MEMORY_V4_SERVICE_LABEL not set: launchd state not checked")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("launchctl")))
    for manager in (managers.Launchd(), None):  # no manager at all: as before on such a platform
        monkeypatch.setattr(managers, "host_manager", lambda manager=manager: manager)
        check = doctor._service(doctor_settings(tmp_path))
        assert (check.status, check.detail) == ("WARN", "launchd status unavailable (platform or timeout)")


def test_doctor_reads_systemd_through_the_interface(monkeypatch, tmp_path, scripts):
    manager = managers.SystemdUser()
    service = planned(tmp_path, manager)
    scripts.service = service
    monkeypatch.setattr(managers, "host_manager", lambda: manager)
    stopped = doctor._service(doctor_settings(tmp_path))
    # Review round 7: ``LoadState=loaded, ActiveState=inactive`` is a LOADED unit; it is said inactive.
    assert (stopped.status, stopped.detail) == ("FAIL", "systemd unit test.memory-os loaded but inactive"), scripts.calls
    assert stopped.hint == "systemctl --user enable --now <label>.service"
    Path(service["path"]).unlink()  # systemd knows no such unit: only then "not loaded"
    absent = doctor._service(doctor_settings(tmp_path))
    assert (absent.status, absent.detail) == ("FAIL", "systemd unit test.memory-os not loaded")
    Path(service["path"]).write_text(manager.render(service["label"], service["argv"], service["env"],
                                                    tmp_path / "server.err"), encoding="utf-8")
    scripts.set(loaded=True)
    assert doctor._service(doctor_settings(tmp_path)).detail == "systemd unit test.memory-os loaded"
    scripts.set(fail={"show": 1})
    unknown = doctor._service(doctor_settings(tmp_path))
    assert (unknown.status, unknown.detail) == ("WARN", "systemd status unavailable (platform or timeout)")
    assert set(scripts.verbs("systemctl")) == {"show"}  # doctor only reads


def test_settings_name_the_definition_through_the_interface(monkeypatch, tmp_path):
    settings = doctor_settings(tmp_path)
    assert settings.launchd_plist == Path.home() / "Library/LaunchAgents/test.memory-os.plist"
    monkeypatch.setattr(managers, "host_manager", lambda: managers.SystemdUser(environ={"HOME": str(Path.home())}))
    assert settings.service_definition == Path.home() / ".config/systemd/user/test.memory-os.service"
    monkeypatch.setattr(managers, "host_manager", lambda: None)
    assert settings.service_definition is None
    assert doctor_settings(tmp_path, label=None).launchd_plist is None


# ---------------------------------------------------------------- review round 7: a failed observation is never
# an absence, a confirmed stop, a free port nor a permission to act. Every counter-example of the review is here,
# on launchd AND systemd where both apply, with mutations around it.

import errno  # noqa: E402

REAL_WRITER_PROCESSES = backup._writer_processes
REAL_BACKUP_PORT_STATE = backup._port_state
REAL_WAIT_STOPPED = backup._wait_stopped
REAL_RESPONDER_PID = backup._responder_pid
REAL_QS_PORT_STATE = qs._port_state
NOT_FREE = [errno.ETIMEDOUT, errno.EACCES, errno.EPERM, errno.EWOULDBLOCK, errno.EAGAIN, errno.EHOSTUNREACH,
            errno.ENETUNREACH, errno.ECONNRESET, errno.EADDRNOTAVAIL, errno.EINPROGRESS, errno.EINTR, 1, -1, 9999]


def fake_socket(monkeypatch, answer, *, opened=None):
    """``socket.socket`` of ``ops/service.py`` replaced: ``connect_ex`` returns ``answer`` (or raises it)."""
    class Probe:
        def __init__(self, *args):
            if answer == "no-socket":
                raise OSError(errno.EMFILE, "too many open files")
            if opened is not None:
                opened.append(args)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, seconds):
            assert seconds > 0

        def connect_ex(self, address):
            assert address[0] == "127.0.0.1"
            if isinstance(answer, BaseException):
                raise answer
            return answer
    monkeypatch.setattr(managers.socket, "socket", Probe)


def definition_of(c) -> Path:
    return c.home / (PLIST if c.kind == "launchd" else UNIT)


def noun_of(c) -> str:
    return "plist" if c.kind == "launchd" else "unit"


def observe_verb(c) -> str:
    return "print" if c.kind == "launchd" else "show"


# ---- 1. the port: only a refused connection is a free port ----

def test_a_port_is_free_only_when_the_connection_is_refused(monkeypatch):
    fake_socket(monkeypatch, 0)
    assert managers.port_state(43219) == "listening"
    fake_socket(monkeypatch, errno.ECONNREFUSED)
    assert managers.port_state(43219) == "free"
    assert REAL_QS_PORT_STATE(43219) == "free" and REAL_BACKUP_PORT_STATE(43219) == "free"


@pytest.mark.parametrize("code", NOT_FREE)
def test_any_other_connection_error_is_an_unknown_port_for_every_reader(monkeypatch, code):
    """Review counter-example: ``connect_ex`` answering ETIMEDOUT (or EACCES…) was read as a free port."""
    fake_socket(monkeypatch, code)
    assert managers.port_state(43219) == "unknown"
    assert REAL_QS_PORT_STATE(43219) == "unknown" and REAL_QS_PORT_STATE(43219, timeout=0.1) == "unknown"
    assert REAL_BACKUP_PORT_STATE(43219) == "unknown"


@pytest.mark.parametrize("answer", ["no-socket", OSError(errno.EACCES, "denied"), TimeoutError("timed out"),
                                    OverflowError("port"), ValueError("closed")])
def test_a_probe_that_raises_is_an_unknown_port(monkeypatch, answer):
    fake_socket(monkeypatch, answer)
    assert managers.port_state(43219) == "unknown"


@pytest.mark.parametrize("port", [None, 0, -1, 65536, True, "43219", 43219.0])
def test_a_value_that_is_not_a_port_is_unknown_and_opens_no_socket(monkeypatch, port):
    opened = []
    fake_socket(monkeypatch, errno.ECONNREFUSED, opened=opened)
    assert managers.port_state(port) == "unknown" and opened == []


@pytest.mark.parametrize("code", [errno.ETIMEDOUT, errno.EACCES, errno.EWOULDBLOCK, 0])
def test_confirmed_stop_is_never_confirmed_on_a_port_that_is_not_free(either, monkeypatch, code):
    """Review counter-example, exactly: the manager attests the absence, the port answers ETIMEDOUT."""
    c = either
    plan = prepare(c, profile="lexical")
    c.system.loaded = c.system.enabled = True
    fake_socket(monkeypatch, code)
    monkeypatch.setattr(qs, "_port_state", REAL_QS_PORT_STATE)
    now = [0.0]
    confirmed = qs._confirmed_stop(plan["service"], c.settings.port, run=c.system, stop_timeout=1.0,
                                   sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    assert qs._job_state(plan["service"], run=c.system) == "absent"   # the manager does attest the absence
    assert confirmed == {"stopped": False, "outcome": "stop-not-confirmed", "observation": "absent",
                         "port": "listening" if code == 0 else "unknown"}
    fake_socket(monkeypatch, errno.ECONNREFUSED)                       # control: the same stop, port refused
    c.system.loaded = c.system.enabled = True
    assert qs._confirmed_stop(plan["service"], c.settings.port, run=c.system, stop_timeout=1.0,
                              sleep=lambda s: None, clock=lambda: 0.0) == {"stopped": True, "outcome": "confirmed"}


@pytest.mark.parametrize("port_state", ["unknown", "listening"])
def test_preview_and_apply_refuse_a_port_that_is_not_attested_free(either, monkeypatch, port_state):
    c = either
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: port_state)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and "port_or_label_occupied" in plan["blocker_codes"] and plan["port_state"] == port_state
    said = [text for text in plan["blockers"] if "occupied" in text][0]
    assert ("its state is unknown, which is never a free port" in said) is (port_state == "unknown")
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: "free")
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is True
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: port_state)  # changes between preview and apply
    with pytest.raises(qs.QuickstartError):
        qs.apply_install(plan, c.settings, home=c.home, run=c.system, clock=c.clock)
    assert not definition_of(c).exists() and "bootstrap" not in c.system.verbs() and "enable" not in c.system.verbs("systemctl")


# ---- reserve (a): the same confirmation everywhere (manager AND port) ----

@pytest.mark.parametrize("port_state", ["unknown", "listening"])
def test_rollback_does_not_move_the_definition_while_the_port_is_not_free(either, monkeypatch, port_state):
    """``stop_owned_service`` said stopped-and-moved without looking at the port."""
    c = either
    result = install(c, profile="lexical")
    content = definition_of(c).read_bytes()
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: port_state if c.system.stop_sent else "free")
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=lambda s: None)
    assert out["ok"] is False and out["code"] == "service_stop_unconfirmed"
    assert out["service"] == {"undo": "stop-unconfirmed", "observation": "absent", "port": port_state}
    assert definition_of(c).read_bytes() == content and f"{noun_of(c)}_moved_to" not in out["service"]
    assert c.system.verbs(c.tool).count(c.stop_verb) == 1


@pytest.mark.parametrize("port_state", ["unknown", "listening"])
def test_uninstall_does_not_move_the_definition_while_the_port_is_not_free(either, monkeypatch, port_state):
    c = either
    install(c, profile="lexical")
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    content = definition_of(c).read_bytes()
    seen = []

    def port(number, **kw):
        seen.append(number)
        return port_state if c.system.stop_sent else "free"
    monkeypatch.setattr(qs, "_port_state", port)
    result = un.apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=lambda s: None)
    assert result["ok"] is False and result["code"] == "service_stop_unconfirmed"
    assert result["service"]["outcome"] == "stop-unconfirmed" and result["service"]["port"] == port_state
    assert definition_of(c).read_bytes() == content and seen and set(seen) == {c.settings.port}


def test_a_service_plan_without_a_port_is_never_stopped(either):
    c = either
    result = install(c, profile="lexical")
    journal = journal_of(c, result["stamp"])
    service = json.loads((journal / "install.json").read_text())["service"]
    assert qs._service_port(service) == c.settings.port
    for argv in (["x"], [service["argv"][0], "--port"], [service["argv"][0], "--port", "0"],
                 [service["argv"][0], "--port", "70000"], [service["argv"][0], "--port", "1", "--port", "2"]):
        assert qs._service_port({**service, "argv": argv}) is None
        out = qs.stop_owned_service({**service, "argv": argv}, qs._sha(definition_of(c)), journal, run=c.system)
        assert out == {"outcome": "refused-job-not-attested", "code": "service_changed_since_install"}
    assert c.stop_verb not in c.system.verbs(c.tool) and definition_of(c).is_file()


# ---- reserve (b): the announced delay is a total time on a monotonic clock ----

@pytest.mark.parametrize("path", ["confirmed-stop", "stop-owned-service"])
def test_the_stop_timeout_is_a_total_time_observations_included(either, monkeypatch, path):
    """Review simulation: observations of five seconds, ``stop_timeout=10`` lasted 215 seconds."""
    c = either
    result = install(c, profile="lexical")
    c.system.bootout_mode = "lingers"
    now, limits = [1000.0], []

    def slow(argv, **kwargs):
        if list(argv[:2]) == ["launchctl", "print"] or list(argv[:3]) == ["systemctl", "--user", "show"]:
            limits.append(kwargs["timeout"])
            now[0] += min(5.0, kwargs["timeout"])       # every observation takes all the time it is given
        return c.system(argv, **kwargs)

    def port(number, *, timeout=0.5):
        now[0] += timeout
        return "free"
    monkeypatch.setattr(qs, "_port_state", port)

    def sleep(seconds):
        now[0] += seconds
    journal = journal_of(c, result["stamp"])
    service = json.loads((journal / "install.json").read_text())["service"]
    sha = qs._sha(definition_of(c))
    if path == "stop-owned-service":
        limits.clear()  # the attestation BEFORE the stop is not part of the confirmation
        started = None

        def clock():
            return now[0]
        real_bootout = qs._bootout

        def bootout(service, *, run):
            nonlocal started
            sent = real_bootout(service, run=run)
            started = now[0]
            limits.clear()
            return sent
        monkeypatch.setattr(qs, "_bootout", bootout)
        out = qs.stop_owned_service(service, sha, journal, run=slow, stop_timeout=10, sleep=sleep, clock=clock)
        assert out["outcome"] == "stop-unconfirmed" and out["observation"] == "loaded"
    else:
        started = now[0]
        out = qs._confirmed_stop(service, c.settings.port, run=slow, stop_timeout=10, sleep=sleep, clock=lambda: now[0])
        assert out["outcome"] == "stop-not-confirmed" and out["stopped"] is False
    elapsed = now[0] - started
    assert 10.0 <= elapsed <= 10.1 + 1e-9, elapsed       # was 215 s; the only overrun is the port floor (0.1 s)
    assert limits and max(limits) <= 5.0 and min(limits) > 0 and len(limits) <= 3
    assert c.system.verbs(c.tool).count(c.stop_verb) == 1 and definition_of(c).is_file()


def test_one_complete_round_is_made_even_with_a_zero_timeout(either, monkeypatch):
    c = either
    result = install(c, profile="lexical")
    service = json.loads((journal_of(c, result["stamp"]) / "install.json").read_text())["service"]
    limits = []

    def port(number, *, timeout=0.5):
        limits.append(timeout)
        return "free"
    monkeypatch.setattr(qs, "_port_state", port)
    assert qs._confirmed_stop(service, c.settings.port, run=c.system, stop_timeout=0, sleep=lambda s: None,
                              clock=lambda: 5.0) == {"stopped": True, "outcome": "confirmed"}
    assert limits == [0.1]


# ---- 2. uninstall: the state of the job starts unknown and only the manager makes it absent ----

def test_uninstall_without_a_service_manager_reports_unknown_and_refuses(c, monkeypatch):
    """Review counter-example: no manager gave ``absent/absent`` with zero observation."""
    monkeypatch.setattr(managers, "host_manager", lambda: None)
    un = uninstaller()
    facts = un._service(c.settings, c.home, c.python, run=c.system)
    assert facts["state"] == "absent" and facts["loaded_state"] == "unknown" and facts["manager_available"] is False
    before = tree(c.root)
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is False and plan["blocker_codes"] == ["service_manager_unavailable"]
    assert plan["nothing_to_do"] is False and plan["service"]["action"] == "none-job-not-attested-absent"
    assert "unknown, never absent" in plan["blockers"][0]
    with pytest.raises(un.UninstallError) as refusal:
        un.apply(plan, c.settings, home=c.home, run=c.system)
    assert refusal.value.code == "preflight_refused" and tree(c.root) == before
    assert c.system.verbs("launchctl") == [] and c.system.verbs("systemctl") == []


@pytest.mark.parametrize("case, expected", [("job-absent", "absent"), ("job-loaded", "foreign-or-unattested"),
                                            ("observation-fails", "unknown"), ("observation-times-out", "unknown")])
def test_uninstall_asks_the_manager_when_the_definition_vanishes_while_it_is_read(either, monkeypatch, case, expected):
    """Review counter-example: the file disappears after the first look, before ``_service_plan``:
    the initial ``loaded_state="absent"`` survived with zero observation."""
    c = either
    result = install(c, profile="lexical")
    assert result["ok"] is True
    if case == "job-absent":
        c.system.loaded = c.system.enabled = False
    elif case != "job-loaded":
        c.system.stop_sent = True
        c.system.observe_after_stop = "error" if case == "observation-fails" else "timeout"
    real_plan = qs._service_plan

    def vanish_then_plan(*args, **kwargs):
        definition_of(c).unlink(missing_ok=True)
        return real_plan(*args, **kwargs)
    monkeypatch.setattr(qs, "_service_plan", vanish_then_plan)
    un = uninstaller()
    asked = len(c.system.calls)
    facts = un._service(c.settings, c.home, c.python, run=c.system)
    assert facts["state"] == "absent" and facts["loaded_state"] == expected
    assert [argv for argv in c.system.calls[asked:] if argv[0] == c.tool], "the manager was asked"
    definition_of(c).write_bytes(b"")  # put something back so that the next look starts with a file again
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["service"]["loaded_state"] == expected
    if expected != "absent":
        assert plan["ok"] is False and "uninstall_job_without_definition" in plan["blocker_codes"]
        assert plan["nothing_to_do"] is False and plan["service"]["action"] == "none-job-not-attested-absent"


def test_uninstall_never_starts_from_an_absent_job(either, monkeypatch):
    """Mutation: whatever the manager cannot say stays ``unknown``; nothing in ``_service`` writes ``absent``."""
    c = either
    install(c, profile="lexical")
    monkeypatch.setattr(qs, "_job_state", lambda service, **kw: "unknown")
    un = uninstaller()
    assert un._service(c.settings, c.home, c.python, run=c.system)["loaded_state"] == "unknown"
    definition_of(c).write_text("not ours")
    foreign = un._service(c.settings, c.home, c.python, run=c.system)
    assert foreign["state"] != "owned" and foreign["loaded_state"] == "unknown"


# ---- 3. undo / uninstall: a missing or substituted definition never skips the observation of the job ----

@pytest.mark.parametrize("case, observation", [("job-loaded", "loaded"), ("observation-fails", "indeterminate"),
                                               ("observation-times-out", "indeterminate")])
def test_rollback_refuses_when_the_definition_is_missing_and_the_job_is_not_attested_absent(either, case, observation):
    """Review counter-example: ``undo --with-service`` succeeded (``plist-already-absent``) with the job loaded."""
    c = either
    result = install(c, profile="lexical")
    definition_of(c).unlink()
    if case != "job-loaded":
        c.system.stop_sent = True
        c.system.observe_after_stop = "error" if case == "observation-fails" else "timeout"
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is False and out["code"] == "service_changed_since_install"
    assert out["service"] == {"undo": "refused-job-without-definition", "observation": observation, "port": "free"}
    assert c.stop_verb not in c.system.verbs(c.tool)       # nothing is stopped without its attested definition
    assert c.system.verbs(c.tool)[-1] == observe_verb(c)    # and the manager WAS asked


def test_rollback_says_already_absent_only_after_the_manager_attested_it(either):
    c = either
    result = install(c, profile="lexical")
    definition_of(c).unlink()
    c.system.loaded = c.system.enabled = False
    before = len(c.system.calls)
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is True and out["service"] == {"undo": f"{noun_of(c)}-already-absent"}
    assert [argv for argv in c.system.calls[before:] if argv[0] == c.tool], "an absence is observed, never assumed"


@pytest.mark.parametrize("kind", ["symlink", "dangling-symlink", "directory", "fifo"])
@pytest.mark.parametrize("loaded", [True, False])
def test_rollback_refuses_a_link_or_a_directory_standing_for_the_definition(either, kind, loaded):
    """Review counter-example: a link or a directory at the path gave ``plist-already-absent``."""
    c = either
    result = install(c, profile="lexical")
    path = definition_of(c)
    content = path.read_bytes()
    path.unlink()
    if kind == "symlink":
        (c.root / "same-bytes").write_bytes(content)
        path.symlink_to(c.root / "same-bytes")
    elif kind == "dangling-symlink":
        path.symlink_to(c.root / "nowhere")
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    c.system.loaded = c.system.enabled = loaded
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is False and out["code"] == "service_changed_since_install"
    assert out["service"] == {"undo": "refused-definition-not-a-file"}
    assert c.stop_verb not in c.system.verbs(c.tool) and os.path.lexists(path)
    assert qs._definition_kind(path) == "other" and qs._definition_kind(c.root / "nothing-here") == "missing"


@pytest.mark.parametrize("case", ["job-loaded", "observation-fails"])
def test_uninstall_refuses_when_the_definition_vanishes_after_its_revalidation(either, monkeypatch, case):
    """Uninstall stayed exposed to a definition disappearing after the revalidation of ``apply``."""
    c = either
    install(c, profile="lexical")
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["action"] == "stop"
    real = un.remaining_dependencies

    def vanish(home, port, **kwargs):
        if "removed" not in kwargs:                     # the call of ``apply``, after its revalidation
            definition_of(c).unlink()
            if case == "observation-fails":
                c.system.stop_sent = True
                c.system.observe_after_stop = "error"
        return real(home, port, **kwargs)
    monkeypatch.setattr(un, "remaining_dependencies", vanish)
    result = un.apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=lambda s: None)
    assert result["ok"] is False and result["partial"] is True and result["code"] == "service_changed_since_install"
    assert result["service"]["outcome"] == "refused-job-without-definition"
    assert result["service"]["observation"] == ("loaded" if case == "job-loaded" else "indeterminate")
    assert c.stop_verb not in c.system.verbs(c.tool)
    assert "is missing while its job is loaded or cannot be read; nothing stopped" in result["next_steps"][-1]
    assert "the stop is not confirmed" not in result["next_steps"][-1]


# ---- 4. backup: pgrep and the port are observations; when they fail there is no "no writer" ----

def pgrep_answer(code, stdout="", error=None):
    def run(argv, **kwargs):
        assert argv[0] == "pgrep" and kwargs.get("timeout"), argv
        if error is not None:
            raise error
        return SimpleNamespace(returncode=code, stdout=stdout, stderr="")
    return run


@pytest.mark.parametrize("code, stdout, error, expected", [
    (1, "", None, []),                                                        # documented: no process matched
    (0, "4242 python -m memory_os_v4.server\n", None, ["4242 python -m memory_os_v4.server"]),
    (2, "", None, None),                                                      # review counter-example: rc=2 gave []
    (3, "", None, None), (-9, "", None, None), (127, "", None, None),
    (1, "4242 something printed with a no-match code\n", None, None),
    (0, "", None, None), (0, "not-a-pid python\n", None, None),
    (0, "", subprocess.TimeoutExpired(["pgrep"], 10), None),
    (0, "", FileNotFoundError("pgrep"), None), (0, "", PermissionError("pgrep"), None),
])
def test_pgrep_says_no_writer_only_with_its_documented_no_match_code(monkeypatch, code, stdout, error, expected):
    monkeypatch.setattr(backup.subprocess, "run", pgrep_answer(code, stdout, error))
    assert REAL_WRITER_PROCESSES() == expected


def test_pgrep_never_counts_this_process_as_a_writer(monkeypatch):
    monkeypatch.setattr(backup.subprocess, "run", pgrep_answer(0, f"{os.getpid()} python -m pytest\n"))
    assert REAL_WRITER_PROCESSES() == []


@pytest.mark.parametrize("writers, port, expected", [
    ([], "free", "none"), (None, "free", "unknown"), ([], "unknown", "unknown"), (None, "unknown", "unknown"),
    (["4242 x"], "free", "alive"), ([], "listening", "alive"), (None, "listening", "alive"), (["4242 x"], "unknown", "alive"),
])
def test_no_writer_needs_both_observations_to_succeed_and_be_negative(monkeypatch, writers, port, expected):
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: writers)
    monkeypatch.setattr(backup, "_port_state", lambda number, **kw: port)
    assert backup._no_writer(SimpleNamespace(port=43219)) == expected


@pytest.mark.parametrize("kind", ["launchd", "systemd-user"])
@pytest.mark.parametrize("failure", ["pgrep-rc-2", "pgrep-timeout", "port-eacces", "port-timeout"])
def test_the_wait_of_a_backup_never_ends_on_a_failed_process_or_port_observation(monkeypatch, kind, failure):
    """Review counter-example: manager stopped + ``pgrep rc=2`` / ``EACCES`` / timeout → ``_wait_stopped=True``."""
    manager = managers.Launchd() if kind == "launchd" else managers.SystemdUser()
    stopped = launchctl_answer(113, stderr=FOUND_NOT) if kind == "launchd" else frozen(SHOW_STOPPED_DISABLED)

    def run(argv, **kwargs):
        if argv[0] == "pgrep":
            return pgrep_answer(1 if failure.startswith("port") else 2, "", subprocess.TimeoutExpired(argv, 10)
                                if failure == "pgrep-timeout" else None)(argv, **kwargs)
        return stopped(argv, **kwargs)
    monkeypatch.setattr(backup, "_manager", lambda: manager)
    monkeypatch.setattr(backup.subprocess, "run", run)
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    fake_socket(monkeypatch, {"port-eacces": errno.EACCES, "port-timeout": errno.ETIMEDOUT}.get(failure, errno.ECONNREFUSED))
    settings = SimpleNamespace(port=43219)
    assert backup._service_status("test.memory-os") == "stopped"            # the manager does say stopped
    assert backup._wait_stopped(settings, "test.memory-os", timeout=0.05) is False
    monkeypatch.setattr(backup.subprocess, "run", lambda argv, **kw: (pgrep_answer(1) if argv[0] == "pgrep" else stopped)(argv, **kw))
    fake_socket(monkeypatch, errno.ECONNREFUSED)                             # control: both observations succeed
    assert backup._wait_stopped(settings, "test.memory-os", timeout=0.05) is True


def test_the_wait_of_a_backup_is_bounded_by_a_monotonic_clock(monkeypatch):
    now, rounds = [50.0], []
    monkeypatch.setattr(backup, "_service_status", lambda label, **kw: rounds.append(now[0]) or "unknown")
    monkeypatch.setattr(backup, "_no_writer", lambda settings, **kw: "none")
    monkeypatch.setattr(backup.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(backup.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    assert backup._wait_stopped(SimpleNamespace(port=43219), "x", timeout=3.5) is False
    assert now[0] == pytest.approx(53.5) and rounds == [50.0, 51.0, 52.0, 53.0]  # no round at the deadline


def test_a_status_that_raises_is_unknown_for_the_backup(monkeypatch):
    class Broken:
        kind = "launchd"

        def status(self, label, *, run, timeout=None):
            raise RuntimeError("anything")
    monkeypatch.setattr(backup, "_manager", lambda: Broken())
    assert backup._service_status("x") == "unknown" and backup._service_loaded("x") is True


@pytest.fixture(params=["launchd", "systemd-user"])
def any_backup(request):
    b = request.getfixturevalue("launchd_backup" if request.param == "launchd" else "systemd_backup")
    b.kind = request.param
    b.tool = "launchctl" if request.param == "launchd" else "systemctl"
    b.verbs = lambda: b.scripts.verbs(b.tool)
    b.start_verb = "bootstrap" if request.param == "launchd" else "start"
    b.stop_verb = "bootout" if request.param == "launchd" else "stop"
    return b


@pytest.mark.parametrize("failure", ["pgrep-rc-2", "pgrep-missing", "port-eacces", "port-timeout"])
def test_backup_of_a_stopped_service_is_not_qualified_on_a_failed_observation(any_backup, monkeypatch, failure):
    """Review counter-example: ``verified_no_writer`` rested on ``pgrep rc=2`` → ``[]`` or ``EACCES`` → closed."""
    b = any_backup
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == "pgrep":
            if failure == "pgrep-missing":
                raise FileNotFoundError("pgrep")
            return SimpleNamespace(returncode=2 if failure == "pgrep-rc-2" else 1, stdout="", stderr="")
        return real_run(argv, **kwargs)
    monkeypatch.setattr(backup.subprocess, "run", run)
    monkeypatch.setattr(backup, "_writer_processes", REAL_WRITER_PROCESSES)
    monkeypatch.setattr(backup, "_port_state", REAL_BACKUP_PORT_STATE)
    fake_socket(monkeypatch, {"port-eacces": errno.EACCES, "port-timeout": errno.ETIMEDOUT}.get(failure, errno.ECONNREFUSED))
    record = backup.backup(b.settings)
    assert record["ok"] is False and record["qualified"] is False and "evidence" not in record
    assert "could not be observed" in record["error"] and "not the absence of a writer" in record["error"]
    assert not b.settings.backup_dir.exists() or not list(b.settings.backup_dir.iterdir())
    assert b.stop_verb not in b.verbs() and b.start_verb not in b.verbs() and "restart_rc" not in record
    fake_socket(monkeypatch, errno.ECONNREFUSED)                             # control: the same backup, readable
    monkeypatch.setattr(backup.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="")
                        if argv[0] == "pgrep" else real_run(argv, **kw))
    again = backup.backup(b.settings)
    assert again["ok"] is True and again["evidence"].startswith("verified_no_writer")


@pytest.mark.parametrize("failure", ["pgrep-rc-2", "port-eacces"])
def test_backup_of_a_running_service_is_not_qualified_when_the_stop_cannot_be_confirmed(any_backup, monkeypatch, failure):
    b = any_backup
    b.scripts.set(loaded=True)
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: None if failure == "pgrep-rc-2" else [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "unknown" if failure == "port-eacces" else "free")
    monkeypatch.setattr(backup, "_wait_stopped", lambda settings, label: REAL_WAIT_STOPPED(settings, label, timeout=0))
    record = backup.backup(b.settings)
    assert record["ok"] is False and record["qualified"] is False and "stop not confirmed" in record["error"]
    assert record["stop_confirmed"] is False
    # Failed writer/port probe is indeterminate: never authorises a restart (no exception to fail-closed).
    assert record["restart"] == "not-restarted-writer-unknown" and "restart_rc" not in record
    assert record["service_restored"] is False and "restart_manual" in record
    assert b.verbs().count(b.stop_verb) == 1 and b.start_verb not in b.verbs()
    assert b.scripts.state()["loaded"] is False
    assert not b.settings.backup_dir.exists() or not list(b.settings.backup_dir.iterdir())


# ---- 5. backup: the restart follows an observation, never the zero return code of the stop ----

def test_backup_does_not_restart_on_the_zero_return_code_of_the_stop_alone(any_backup, monkeypatch):
    """Review counter-example: stop rc=0, ``_wait_stopped`` False → the ``finally`` restarted anyway."""
    b = any_backup
    b.scripts.set(loaded=True)
    monkeypatch.setattr(backup, "_service_stop", lambda label: 0)            # returns zero, stops nothing
    monkeypatch.setattr(backup, "_wait_stopped", lambda settings, label: REAL_WAIT_STOPPED(settings, label, timeout=0))
    record = backup.backup(b.settings)
    assert record["ok"] is False and "stop not confirmed" in record["error"]
    assert record["stop_confirmed"] is False and record["restart"] == "not-needed-service-still-running"
    assert "restart_rc" not in record and b.start_verb not in b.verbs()
    assert b.scripts.state()["loaded"] is True


def test_backup_takes_no_action_when_the_state_after_the_stop_is_unknown(any_backup, monkeypatch):
    b = any_backup
    b.scripts.set(loaded=True)

    def never_confirmed(settings, label):
        b.scripts.set(fail={"print" if b.kind == "launchd" else "show": 5})   # the manager stops answering
        return False
    monkeypatch.setattr(backup, "_wait_stopped", never_confirmed)
    record = backup.backup(b.settings)
    assert record["ok"] is False and record["stop_confirmed"] is False
    assert record["restart"] == "not-attempted-state-unknown" and "restart_rc" not in record
    assert b.start_verb not in b.verbs() and b.verbs().count(b.stop_verb) == 1
    assert record["restart_manual"] == ("launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"
                                        if b.kind == "launchd" else "systemctl --user start test.memory-os.service")


def test_backup_restart_after_a_confirmed_stop_says_so(any_backup):
    b = any_backup
    b.scripts.set(loaded=True)
    record = backup.backup(b.settings)
    assert record["ok"] is True and record["stop_confirmed"] is True and record["restart"] == "after-confirmed-stop"
    assert record["restart_rc"] == 0 and b.verbs().count(b.start_verb) == 1


def test_a_backup_command_that_does_not_answer_is_a_failure(monkeypatch):
    monkeypatch.setattr(backup.subprocess, "run", lambda argv, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(argv, kw["timeout"])))
    assert backup._launchctl("bootout", "gui/0/x") == -1 and backup._command(["systemctl", "--user", "stop", "x"]) == -1


# ---- reserve (c): doctor never says "not loaded" for a loaded unit ----

@pytest.mark.parametrize("text, status, detail", [
    (SHOW_STOPPED_DISABLED, "FAIL", "systemd unit test.memory-os loaded but inactive"),       # the review's case
    (SHOW_STOPPED_DISABLED.replace("UnitFileState=disabled", "UnitFileState=enabled"), "FAIL",
     "systemd unit test.memory-os loaded but inactive"),
    (SHOW_STOPPED_DISABLED.replace("ActiveState=inactive", "ActiveState=failed").replace("SubState=dead", "SubState=failed"),
     "FAIL", "systemd unit test.memory-os loaded but failed"),
    (SHOW_UNKNOWN_UNIT, "FAIL", "systemd unit test.memory-os not loaded"),
    (SHOW_ACTIVE_ENABLED, "PASS", "systemd unit test.memory-os loaded"),
    (SHOW_STOPPED_DISABLED.replace("SubState=dead", "SubState=???"), "WARN", "systemd status unavailable (platform or timeout)"),
    (THREE_LINES, "WARN", "systemd status unavailable (platform or timeout)"),
])
def test_doctor_says_inactive_for_a_loaded_inactive_unit(monkeypatch, tmp_path, text, status, detail):
    monkeypatch.setattr(managers, "host_manager", lambda: managers.SystemdUser())
    monkeypatch.setattr(doctor.subprocess, "run", frozen(text))
    check = doctor._service(doctor_settings(tmp_path))
    assert (check.status, check.detail) == (status, detail)
    assert "not loaded" not in check.detail or "LoadState=not-found" in text


def test_status_detail_and_status_always_agree():
    manager = managers.SystemdUser()
    for text in (SHOW_STOPPED_DISABLED, SHOW_ACTIVE_ENABLED, SHOW_UNKNOWN_UNIT, THREE_LINES):
        state, word = manager.status_detail("test.memory-os", run=frozen(text))
        assert state == manager.status("test.memory-os", run=frozen(text)) and (word is None) is (state == "unknown")
    launchd = managers.Launchd()
    for code, stderr in ((0, b""), (113, FOUND_NOT), (113, b"IPC failure"), (1, FOUND_NOT), (5, b"")):
        state, word = launchd.status_detail("test.memory-os", run=launchctl_answer(code, stderr=stderr))
        assert state == launchd.status("test.memory-os", run=launchctl_answer(code, stderr=stderr))
        assert word == {"running": "loaded", "stopped": "not loaded", "unknown": None}[state]


def test_uninstall_does_not_read_an_unreadable_path_as_a_missing_definition(either, monkeypatch):
    """Adversarial pass: ``lstat`` failing (permission, I/O) is not ``FileNotFoundError``."""
    c = either
    install(c, profile="lexical")
    un = uninstaller()

    def denied(path, *args, **kwargs):
        if str(path) == str(definition_of(c)):
            raise PermissionError(errno.EACCES, "denied")
        return real_lstat(path, *args, **kwargs)
    real_lstat = os.lstat
    assert qs._definition_kind(definition_of(c)) == "file"
    monkeypatch.setattr(qs.os, "lstat", denied)
    assert qs._definition_kind(definition_of(c)) == "unknown"
    asked = len(c.system.calls)
    facts = un._service(c.settings, c.home, c.python, run=c.system)
    assert facts["state"] == "unreadable" and facts["loaded_state"] == "unknown"
    assert c.system.calls[asked:] == []
    journal = journal_of(c, sorted(os.listdir(c.home / STATE / "quickstart"))[0])
    service = json.loads((journal / "install.json").read_text())["service"]
    out = qs.stop_owned_service(service, "0" * 64, journal, run=c.system)
    assert out == {"outcome": "refused-definition-not-a-file", "code": "service_changed_since_install"}
    assert c.stop_verb not in c.system.verbs(c.tool)
    monkeypatch.setattr(qs.os, "lstat", real_lstat)
    monkeypatch.setattr(qs, "_definition_kind", lambda path: "unknown")
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is False and plan["blocker_codes"] == ["uninstall_service_foreign"]
    assert plan["service"]["state"] == "unreadable" and plan["nothing_to_do"] is False


def test_backup_asks_the_manager_after_a_stop_command_that_failed(any_backup, monkeypatch):
    """Adversarial pass: a non-zero stop is no proof that nothing stopped; "stopped" observed → put back."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_stop = backup._service_stop
    monkeypatch.setattr(backup, "_service_stop", lambda label: real_stop(label) and 0 or 5)  # took effect, says 5
    record = backup.backup(b.settings)
    assert record["ok"] is False and "failed" in record["error"] and record["stop_confirmed"] is False
    assert record["restart"] == "after-manager-attested-stopped" and record["restart_rc"] == 0
    assert b.scripts.state()["loaded"] is True and b.verbs().count(b.start_verb) == 1


@pytest.mark.parametrize("kind", ["symlink-to-the-same-bytes", "hardlink"])
def test_a_journal_never_attests_a_definition_reached_through_a_link(either, monkeypatch, kind):
    """Adversarial pass: the journal fallback compared a digest read THROUGH a link; the same bytes
    behind a link made the definition ``owned`` (and a backup would have stopped and restarted it)."""
    c = either
    install(c, profile="lexical")
    path = definition_of(c)
    content = path.read_bytes()
    un = uninstaller()
    other_python = str(c.home / "runtime/bin/python-of-another-venv")   # only the journal can attest it
    assert un._service(c.settings, c.home, other_python, run=c.system)["state"] == "owned"
    if kind == "hardlink":
        os.link(path, c.root / "second-name")
    else:
        (c.root / "same-bytes").write_bytes(content)
        path.unlink()
        path.symlink_to(c.root / "same-bytes")
    for python in (other_python, c.python):
        facts = un._service(c.settings, c.home, python, run=c.system)
        assert facts["state"] != "owned" and facts["loaded_state"] == "unknown"
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is False and plan["blocker_codes"] == ["uninstall_service_foreign"]


# ---------------------------------------------------------------- review round 8

# ---- 1. every successful outcome of stop_owned_service needs the manager AND a free port ----

@pytest.mark.parametrize("port_state", ["listening", "unknown"])
def test_a_second_run_after_an_unconfirmed_stop_does_not_move_the_definition_while_the_port_is_not_free(
        either, monkeypatch, port_state):
    """Review counter-example, exactly: first run ``stop-unconfirmed`` (job gone, port busy); the second run
    took the "job already absent" branch and moved the definition with ZERO probe of the port."""
    c = either
    result = install(c, profile="lexical")
    content = definition_of(c).read_bytes()
    probes = []

    def port(number, **kw):
        probes.append(number)
        return port_state
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: port_state if c.system.stop_sent else "free")
    first = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=lambda s: None)
    assert first["service"]["undo"] == "stop-unconfirmed" and first["service"]["observation"] == "absent"
    monkeypatch.setattr(qs, "_port_state", port)
    second = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=lambda s: None)
    assert second["ok"] is False and second["code"] == "service_stop_unconfirmed"
    assert second["service"] == {"undo": "absence-unconfirmed", "observation": "absent", "port": port_state}
    assert probes == [c.settings.port]                                  # the port IS probed
    assert definition_of(c).read_bytes() == content and f"{noun_of(c)}_moved_to" not in second["service"]
    assert c.system.verbs(c.tool).count(c.stop_verb) == 1               # and no second stop is sent
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: "free")  # control: the port is released
    third = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert third["ok"] is True and third["service"]["undo"] == f"{noun_of(c)}-moved-job-was-absent"
    assert not definition_of(c).exists()


@pytest.mark.parametrize("port_state", ["listening", "unknown"])
def test_a_missing_definition_is_already_absent_only_with_a_free_port(either, monkeypatch, port_state):
    """Review counter-example: the "definition missing" branch said ``plist-already-absent`` without a probe."""
    c = either
    result = install(c, profile="lexical")
    definition_of(c).unlink()
    c.system.loaded = c.system.enabled = False
    probes = []
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: probes.append(number) or port_state)
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert out["ok"] is False and out["code"] == "service_stop_unconfirmed"
    assert out["service"] == {"undo": "absence-unconfirmed", "observation": "absent", "port": port_state}
    assert probes == [c.settings.port] and c.stop_verb not in c.system.verbs(c.tool)
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: probes.append(number) or "free")
    again = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
    assert again["ok"] is True and again["service"] == {"undo": f"{noun_of(c)}-already-absent"} and len(probes) == 2


@pytest.mark.parametrize("port_state", ["listening", "unknown"])
def test_uninstall_does_not_finish_on_an_absent_job_with_a_port_that_is_not_free(either, monkeypatch, port_state):
    c = either
    install(c, profile="lexical")
    c.system.loaded = c.system.enabled = False          # interrupted install: definition ours, job absent
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["action"] == "stop"
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: port_state)
    result = un.apply(plan, c.settings, home=c.home, run=c.system, stop_timeout=0, sleep=lambda s: None)
    assert result["ok"] is False and result["code"] == "service_stop_unconfirmed"
    assert result["service"]["outcome"] == "absence-unconfirmed" and result["service"]["port"] == port_state
    assert definition_of(c).is_file() and "port is not attested free" in result["next_steps"][-1]


def test_every_successful_outcome_of_stop_owned_service_probed_the_port(either, monkeypatch):
    """Mutation guard: the three successes (stopped-and-moved, moved-job-was-absent, already-absent)."""
    c = either
    probes = []
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: probes.append(number) or "free")
    for scenario in ("running", "job-absent", "definition-missing"):
        for path in c.home.glob(".local/state/memory-os/quickstart/*"):
            pass
        result = install(c, profile="lexical")
        assert result["ok"] is True, result.get("error")
        if scenario != "running":
            c.system.loaded = c.system.enabled = False
        if scenario == "definition-missing":
            definition_of(c).unlink()
        del probes[:]
        out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True)
        assert out["ok"] is True, out
        assert out["service"]["undo"] == {"running": f"stopped-and-{noun_of(c)}-moved",
                                          "job-absent": f"{noun_of(c)}-moved-job-was-absent",
                                          "definition-missing": f"{noun_of(c)}-already-absent"}[scenario]
        assert probes == [c.settings.port], scenario
        c.system.stop_sent = False


# ---- 5 (reserve). a stop command that fails or does not answer: the state is observed and reported ----

@pytest.mark.parametrize("mode, outcome", [("fail", "stop-failed"), ("timeout", "stop-timeout")])
@pytest.mark.parametrize("took_effect", [False, True])
def test_quickstart_observes_and_reports_after_a_stop_command_that_failed(either, monkeypatch, mode, outcome, took_effect):
    c = either
    result = install(c, profile="lexical")
    content = definition_of(c).read_bytes()
    service = json.loads((journal_of(c, result["stamp"]) / "install.json").read_text())["service"]
    if took_effect:  # the command fails or times out, and the job is gone all the same
        def gone():
            c.system.loaded = c.system.enabled = False
        c.system.hooks[("launchctl", "bootout") if c.kind == "launchd" else ("systemctl", "--user", "disable")] = gone
    c.system.bootout_mode = mode
    seen = "absent" if took_effect else "loaded"
    confirmed = qs._confirmed_stop(service, c.settings.port, run=c.system, stop_timeout=0, sleep=lambda s: None)
    assert confirmed == {"stopped": False, "outcome": outcome, "observation": seen, "port": "free"}
    assert c.system.verbs(c.tool)[-1] == observe_verb(c)       # observed AFTER the failed command
    c.system.loaded = c.system.enabled = True
    out = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, stop_timeout=0, sleep=lambda s: None)
    assert out["ok"] is False and out["code"] == "service_" + outcome.replace("-", "_")
    assert out["service"] == {"undo": outcome, "observation": seen, "port": "free"}   # reported, never a success
    assert definition_of(c).read_bytes() == content
    assert c.system.verbs(c.tool).count(c.stop_verb) == 2      # one per call, never a retry


# ---- 2. backup: a writer SEEN alive forbids the restart ----

@pytest.mark.parametrize("sighting", ["writer-process", "port-listening"])
def test_backup_never_restarts_while_a_writer_is_seen_alive(any_backup, monkeypatch, sighting):
    """Review counter-example, exactly: manager stopped + writer alive → ``after-manager-attested-stopped``
    and a restart, that is a second writer."""
    b = any_backup
    b.scripts.set(loaded=True)
    monkeypatch.setattr(backup, "_writer_processes",
                        lambda **kw: ["4242 python -m memory_os_v4.server"] if sighting == "writer-process" else [])
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: "listening" if sighting == "port-listening" else "free")
    monkeypatch.setattr(backup, "_wait_stopped", lambda settings, label: REAL_WAIT_STOPPED(settings, label, timeout=0))
    record = backup.backup(b.settings)
    assert record["ok"] is False and record["qualified"] is False and record["stop_confirmed"] is False
    assert record["restart"] == "not-restarted-writer-alive" and "restart_rc" not in record
    assert b.start_verb not in b.verbs() and b.scripts.state()["loaded"] is False
    assert record["restart_manual"] == ("launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"
                                        if b.kind == "launchd" else "systemctl --user start test.memory-os.service")
    assert not b.settings.backup_dir.exists() or not list(b.settings.backup_dir.iterdir())


@pytest.mark.parametrize("writers, port, restart", [
    ([], "free", "after-manager-attested-stopped"),                 # both probes succeed and prove no writer
    (None, "free", "not-restarted-writer-unknown"),                 # failed probe: indeterminate, never restart
    ([], "unknown", "not-restarted-writer-unknown"),
    (None, "unknown", "not-restarted-writer-unknown"),
    (["4242 x"], "unknown", "not-restarted-writer-alive"),
    (None, "listening", "not-restarted-writer-alive"),
    (["4242 x"], "listening", "not-restarted-writer-alive"),
])
def test_backup_tells_an_unavailable_observation_from_a_sighting(any_backup, monkeypatch, writers, port, restart):
    b = any_backup
    b.scripts.set(loaded=True)
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: writers)
    monkeypatch.setattr(backup, "_port_state", lambda number, **kw: port)
    monkeypatch.setattr(backup, "_wait_stopped", lambda settings, label: False)
    record = backup.backup(b.settings)
    assert record["ok"] is False and record["restart"] == restart
    assert (b.start_verb in b.verbs()) is restart.startswith("after-")
    assert ("restart_manual" in record) is (not restart.startswith("after-"))


# ---- 3. backup: the definition started again is the one attested before the stop ----

def substitute(b, how):
    path = Path(b.service["path"])
    text = path.read_text(encoding="utf-8")
    if how == "command-replaced":       # the review's simulation: same unit name, another command
        path.write_text(text.replace("memory_os_v4.server", "another.module"), encoding="utf-8")
    elif how == "comment-added":        # same plan for a parser, other bytes
        path.write_text(text + ("\n# edited\n" if b.kind == "systemd-user" else "\n<!-- edited -->\n"), encoding="utf-8")
    elif how == "symlink-to-the-same-bytes":
        other = path.parent.parent / "same-bytes"
        other.write_text(text, encoding="utf-8")
        path.unlink()
        path.symlink_to(other)
    elif how == "removed":
        path.unlink()
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.parametrize("how", ["command-replaced", "comment-added", "symlink-to-the-same-bytes", "removed"])
def test_backup_never_restarts_a_definition_substituted_during_the_backup(any_backup, monkeypatch, how, confirmed):
    """Review counter-example: ``_service_start`` started whatever stood at the path; a substitution during
    the backup was enough, in both branches (confirmed stop, and manager-attested stop)."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_wait = backup._wait_stopped

    def wait(settings, label):
        done = REAL_WAIT_STOPPED(settings, label, timeout=30) if confirmed else False
        substitute(b, how)              # the definition changes while the service is down
        return done
    assert real_wait is not None
    monkeypatch.setattr(backup, "_wait_stopped", wait)
    monkeypatch.setattr(backup.time, "sleep", lambda s: None)
    record = backup.backup(b.settings)
    assert record["stop_confirmed"] is confirmed
    assert record["restart"] == "not-restarted-definition-changed" and "restart_rc" not in record
    assert b.start_verb not in b.verbs() and "restart_manual" in record
    assert record["ok"] is False                 # even with a verified archive: the service was not put back
    assert b.scripts.state()["loaded"] is False


def test_backup_restart_predicate_refuses_stopped_when_observe_says_foreign(systemd_backup, monkeypatch):
    """Review simulation (systemd): command replaced → ``status`` says "stopped" while ``observe`` says
    ``foreign-or-unattested``; the restart predicate accepted it."""
    b = systemd_backup
    b.scripts.set(loaded=True)
    before = backup._attested_state(b.settings, b.manager)
    digest = backup._definition_digest(b.settings, b.manager)
    b.scripts.set(loaded=False, service=dict(b.scripts.service, python="/foreign/bin/other-server"))
    plan = {"label": "test.memory-os", "path": b.service["path"], "argv": b.service["argv"], "env": b.service["env"]}
    assert b.manager.status("test.memory-os", run=subprocess.run) == "stopped"
    assert b.manager.observe(plan, run=subprocess.run)["state"] == "foreign-or-unattested"
    for confirmed in (True, False):
        assert backup._restart_decision(b.settings, b.manager, before, digest, confirmed) == "not-attempted-state-unknown"
    assert [argv[2] for argv in b.scripts.calls if argv[2] != "show"] == []


@pytest.mark.parametrize("digest_before", [None, "0" * 64])
def test_backup_restart_needs_the_digest_attested_before_the_stop(any_backup, digest_before):
    b = any_backup
    before = backup._attested_state(b.settings, b.manager)
    assert backup._definition_digest(b.settings, b.manager) == qs._sha(Path(b.service["path"]))
    assert backup._restart_decision(b.settings, b.manager, before, digest_before, True) == "not-restarted-definition-changed"
    assert set(b.verbs()) == {"print" if b.kind == "launchd" else "show"}   # only the attestation above: reads


def test_backup_does_not_restart_when_the_activation_is_no_longer_the_attested_one(systemd_backup, monkeypatch):
    b = systemd_backup
    b.scripts.set(loaded=True, enabled=True)

    def wait(settings, label):
        b.scripts.set(enabled=False)    # someone disabled the unit while it was down
        return True
    monkeypatch.setattr(backup, "_wait_stopped", wait)
    record = backup.backup(b.settings)
    assert record["restart"] == "not-restarted-definition-changed" and "restart_rc" not in record
    assert "start" not in b.scripts.verbs("systemctl") and record["ok"] is False
    assert record["verified"] is True            # the archive itself is good; the service was not put back


# ---- 4 (reserve). the wait of a backup: observations bounded by what remains, no success after the deadline ----

def slow_wait(monkeypatch, *, status_cost, writers_cost, port_cost):
    """A fake time where every observation takes all the time it is given, up to its own cost."""
    now, limits = [100.0], []

    def status(label, *, timeout=5.0):
        limits.append(("status", timeout))
        now[0] += min(status_cost, timeout)
        return "stopped" if timeout >= status_cost else "unknown"   # cut short: it answered nothing

    def writers(*, timeout=10.0):
        limits.append(("pgrep", timeout))
        now[0] += min(writers_cost, timeout)
        return [] if timeout >= writers_cost else None

    def port(number, *, timeout=0.5):
        limits.append(("port", timeout))
        now[0] += min(port_cost, timeout)
        return "free" if timeout >= port_cost else "unknown"
    monkeypatch.setattr(backup, "_service_status", status)
    monkeypatch.setattr(backup, "_writer_processes", writers)
    monkeypatch.setattr(backup, "_port_state", port)
    monkeypatch.setattr(backup.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(backup.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now, limits


def test_the_wait_of_a_backup_refuses_a_success_obtained_after_the_deadline(monkeypatch):
    """Review simulation: ``timeout=30`` → success at 32 s (a round started at 29 s ran 3 s)."""
    now, limits = slow_wait(monkeypatch, status_cost=0.0, writers_cost=0.0, port_cost=0.0)
    calls = []

    def status(label, *, timeout=5.0):       # nothing for 29 s, then an answer that takes 3 s
        calls.append(now[0])
        if now[0] < 129.0:
            return "running"
        now[0] += min(3.0, timeout)
        return "stopped" if timeout >= 3.0 else "unknown"
    monkeypatch.setattr(backup, "_service_status", status)
    assert backup._wait_stopped(SimpleNamespace(port=43219), "x", timeout=30.0) is False   # was True at 32 s
    assert now[0] <= 130.0 + 0.05 and max(calls) < 130.0


def test_the_wait_of_a_backup_starts_no_round_at_the_deadline(monkeypatch):
    """Review simulation: ``timeout=1`` → a new round started at the deadline, success at 11 s."""
    now, limits = slow_wait(monkeypatch, status_cost=0.0, writers_cost=10.0, port_cost=0.0)
    assert backup._wait_stopped(SimpleNamespace(port=43219), "x", timeout=1.0) is False     # was True at 11 s
    assert now[0] - 100.0 <= 1.0 + 0.05                                                   # not 11 s
    assert all(limit <= 1.0 + 1e-9 for _, limit in limits) and ("pgrep", 1.0) in limits     # bounded by what remains


def test_the_wait_of_a_backup_still_succeeds_in_time(monkeypatch):
    now, limits = slow_wait(monkeypatch, status_cost=2.0, writers_cost=3.0, port_cost=0.25)
    assert backup._wait_stopped(SimpleNamespace(port=43219), "x", timeout=30.0) is True
    assert now[0] - 100.0 == pytest.approx(5.25)
    assert limits == [("status", 5.0), ("pgrep", 10.0), ("port", 0.5)]


# ---- (b) no service manager: the refusal says what to do ----

def test_the_refusal_without_a_service_manager_is_actionable(c, monkeypatch):
    monkeypatch.setattr(managers, "host_manager", lambda: None)
    plan = uninstaller().plan(c.settings, home=c.home, python=c.python, run=c.system)
    said = plan["blockers"][0]
    assert plan["blocker_codes"] == ["service_manager_unavailable"] and "What to do: (1)" in said
    assert "`memory-os-admin uninstall` again from a login session" in said and "`clients[].path`" in said
    assert all(isinstance(item["path"], str) for item in plan["clients"])   # the paths the message points to


def test_the_wait_of_a_backup_refuses_an_answer_that_overran_its_limit(monkeypatch):
    """Second guard of the same counter-example: an observation that ignores its limit and answers
    "stopped" at 32 s for ``timeout=30`` is still not a success."""
    now, limits = slow_wait(monkeypatch, status_cost=0.0, writers_cost=0.0, port_cost=0.0)

    def status(label, *, timeout=5.0):
        if now[0] < 129.0:
            return "running"
        now[0] += 3.0                         # overruns whatever limit it was given
        return "stopped"
    monkeypatch.setattr(backup, "_service_status", status)
    assert backup._wait_stopped(SimpleNamespace(port=43219), "x", timeout=30.0) is False
    assert now[0] == pytest.approx(132.0)


# ---------------------------------------------------------------- review round 9

# ---- 1 & 2. backup restart ALWAYS re-probes writers/port and refuses unknown ----

@pytest.mark.parametrize("late, expected", [
    ("writer-appeared", "not-restarted-writer-alive"),
    ("port-taken", "not-restarted-writer-alive"),
    ("pgrep-failed", "not-restarted-writer-unknown"),
    ("port-failed", "not-restarted-writer-unknown"),
])
def test_backup_rechecks_writers_and_port_even_after_confirmed_stop(any_backup, monkeypatch, late, expected):
    """Round 9 blocker 1: a writer, listening port or probe failure appearing during the snapshot
    must block the restart even when the earlier stop had been confirmed.

    Round 11 (D6 bis, ii): it also DISQUALIFIES the copy. The absence of a writer must be proven
    over the whole copy (probe before AND after); this test used to freeze the reverse
    (``qualified`` and ``archive_ok`` true, archive eligible for the rotation)."""
    b = any_backup
    b.scripts.set(loaded=True)
    phase = {"after_snapshot": False}
    probes = []
    real_snapshot = backup.snapshot

    def snap(store):
        files = real_snapshot(store)
        phase["after_snapshot"] = True
        return files

    def writers(**kw):
        if phase["after_snapshot"]:
            probes.append("pgrep")
            if late == "writer-appeared":
                return ["9999 python -m memory_os_v4.server"]
            if late == "pgrep-failed":
                return None
        return []

    def port(number, **kw):
        if phase["after_snapshot"]:
            probes.append("port")
            if late == "port-taken":
                return "listening"
            if late == "port-failed":
                return "unknown"
        return "free"

    monkeypatch.setattr(backup, "snapshot", snap)
    monkeypatch.setattr(backup, "_writer_processes", writers)
    monkeypatch.setattr(backup, "_port_state", port)
    record = backup.backup(b.settings)
    assert record["stop_confirmed"] is True and "pgrep" in probes
    assert record["restart"] == expected and "restart_rc" not in record
    assert "restart_manual" in record and b.start_verb not in b.verbs()
    assert record["qualified"] is False and record["verified"] is False and record["archive_ok"] is False
    assert record["service_restored"] is False and record["ok"] is False
    assert "after the copy" in record["error"] and "nothing qualified" in record["error"]
    assert ("is alive after the copy" in record["error"]) is (expected == "not-restarted-writer-alive")
    assert "archive" not in record and backup.verified_archives(b.settings) == {}
    assert not b.settings.backup_dir.exists() or not list(b.settings.backup_dir.iterdir())   # nothing to rotate
    from memory_os_v4.ops import health
    assert health.last_good_backup_age(b.settings) is None


def test_backup_never_restarts_when_writer_seen_then_probe_fails(any_backup, monkeypatch):
    """Round 9 point 2: writer observed alive during stop wait, then probe fails before restart."""
    b = any_backup
    b.scripts.set(loaded=True)
    calls = [0]

    def writers(**kw):
        calls[0] += 1
        return ["4242 python -m memory_os_v4.server"] if calls[0] == 1 else None

    monkeypatch.setattr(backup, "_writer_processes", writers)
    monkeypatch.setattr(backup, "_port_state", lambda number, **kw: "free")
    monkeypatch.setattr(backup, "_wait_stopped", lambda settings, label: REAL_WAIT_STOPPED(settings, label, timeout=0))
    record = backup.backup(b.settings)
    assert record["stop_confirmed"] is False and record["restart"] == "not-restarted-writer-unknown"
    assert "restart_rc" not in record and b.start_verb not in b.verbs() and "restart_manual" in record


# ---- 3. systemd contradictory inactive/failed sub-states are unknown for every UnitFileState ----

@pytest.mark.parametrize("unit_state", ["enabled", "disabled", "static"])
@pytest.mark.parametrize("active_state, sub_state", [
    ("inactive", "auto-restart"),
    ("inactive", "stop-sigterm"),
    ("inactive", "final-sigkill"),
    ("inactive", "failed"),
    ("failed", "dead"),
    ("failed", "auto-restart"),
])
def test_systemd_contradictory_inactive_substate_is_unknown_whatever_unit_file_state(
        systemd_backup, unit_state, active_state, sub_state):
    """Round 9 blocker 3: ActiveState=inactive + MainPID=0 + SubState=auto-restart/stop-sigterm
    must NEVER return active=False or absent, whatever UnitFileState."""
    text = (SHOW_STOPPED_DISABLED
            .replace("UnitFileState=disabled", f"UnitFileState={unit_state}")
            .replace("ActiveState=inactive", f"ActiveState={active_state}")
            .replace("SubState=dead", f"SubState={sub_state}"))
    manager = managers.SystemdUser()
    assert manager.observe(FIXED, run=frozen(text)) == {"state": "unknown"}
    assert manager.status("test.memory-os", run=frozen(text)) == "unknown"
    assert manager.status_detail("test.memory-os", run=frozen(text)) == ("unknown", None)
    b = systemd_backup
    unit_text = text.replace(FIXED["path"], b.service["path"])
    for key in ("MEMORY_V4_TOKEN_FILE", "MEMORY_V4_DIR"):
        unit_text = unit_text.replace(f"{key}={FIXED['env'][key]}", f"{key}={b.service['env'][key]}")
    old_argv = " ".join(FIXED["argv"])
    new_argv = " ".join(b.service["argv"])
    unit_text = unit_text.replace(f"path={FIXED['argv'][0]} ; argv[]={old_argv}",
                                  f"path={b.service['argv'][0]} ; argv[]={new_argv}")
    monkeypatch_run = frozen(unit_text)
    assert manager.observe({"label": "test.memory-os", "path": b.service["path"],
                            "argv": b.service["argv"], "env": b.service["env"]},
                           run=monkeypatch_run) == {"state": "unknown"}


# ---- 4. uninstall with missing definition + absent job MUST probe port in plan() and apply() ----

@pytest.mark.parametrize("port_state", ["listening", "unknown"])
def test_uninstall_missing_definition_and_absent_job_probes_port_in_plan_and_apply(either, monkeypatch, port_state):
    """Round 9 blocker 4 (uninstall.py:528): missing definition + absent job without a free port
    must NEVER yield ok=True, nothing_to_do=True or outcome='absent'."""
    c = either
    un = uninstaller()
    probes = []
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: probes.append(number) or port_state)
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert probes == [c.settings.port]
    assert plan["ok"] is False and plan["nothing_to_do"] is False
    assert plan["blocker_codes"] == ["service_stop_unconfirmed"]
    assert plan["service"]["action"] == "absence-unconfirmed" and plan["service"]["port_state"] == port_state
    # Control: free port gives ok=True, nothing_to_do=True, and apply re-probes port cleanly.
    del probes[:]
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: probes.append(number) or "free")
    ok_plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert ok_plan["ok"] is True and ok_plan["nothing_to_do"] is True and ok_plan["service"]["port_state"] == "free"
    # Port becomes busy/unreadable between preview and apply: refused, never nothing_to_do success.
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: port_state)
    with pytest.raises(un.UninstallError) as exc:
        un.apply(ok_plan, c.settings, home=c.home, run=c.system)
    assert exc.value.code == "uninstall_changed_since_preview"


@pytest.mark.parametrize("port_state", ["listening", "unknown"])
def test_uninstall_missing_definition_reprobes_port_after_client_removal(either, monkeypatch, port_state):
    """Round 9 blocker 4 (uninstall.py:616): definition missing at plan time, clients removed, then
    port becomes busy/unreadable before completion → absence-unconfirmed, never ok=True absent."""
    c = either
    install(c, profile="lexical")
    definition_of(c).unlink()
    c.system.loaded = c.system.enabled = False
    un = uninstaller()
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: "free")
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["nothing_to_do"] is False and plan["service"]["state"] == "absent"
    probes = []

    def port_later(number, **kw):
        probes.append(number)
        # First probe is plan revalidation inside apply(); second is post-client-removal recheck.
        return "free" if len(probes) == 1 else port_state

    monkeypatch.setattr(qs, "_port_state", port_later)
    result = un.apply(plan, c.settings, home=c.home, run=c.system)
    assert len(probes) == 2
    assert result["ok"] is False and result["partial"] is True and result["code"] == "service_stop_unconfirmed"
    assert result["service"]["outcome"] == "absence-unconfirmed" and result["service"]["port"] == port_state


# ---- 5. backup ok vs archive_ok: foreign HTTP 200 after failed restart never gives ok=True ----

@pytest.mark.parametrize("failure", ["start-rc-1", "start-rc-0-not-attested"])
def test_backup_ok_is_false_when_restart_fails_even_if_another_process_answers_http_200(
        any_backup, monkeypatch, failure):
    """Round 9 blocker 5 + archive eligibility separation: verified archive + foreign HTTP 200
    while our service is not restored → ok=False, service_restored=False, archive_ok=True,
    and verified_archives() still includes the valid archive."""
    b = any_backup
    b.scripts.set(loaded=True)
    if failure == "start-rc-1":
        b.scripts.set(fail={b.start_verb: 1})
    else:
        # Start returns 0, but does not actually bring our attested service back up.
        monkeypatch.setattr(backup, "_service_start", lambda settings: 0)
    monkeypatch.setattr(backup, "_wait_healthy", lambda *a, **k: True)  # foreign HTTP 200 on the port
    record = backup.backup(b.settings)
    assert record["qualified"] is True and record["verified"] is True
    assert record["archive_ok"] is True and record["service_restored"] is False and record["ok"] is False
    assert "restart_manual" in record
    if b.kind == "systemd-user":
        assert record["service_state_restored"] is False
    # Archive remains eligible in verified_archives despite service restoration failure.
    eligible = backup.verified_archives(b.settings)
    assert record["archive"] in eligible and eligible[record["archive"]][0] == record["archive_sha256"]


# ---- reserve 1: _no_writer shares ONE monotonic deadline across pgrep and port ----

def test_no_writer_shares_one_deadline_across_pgrep_and_port(monkeypatch):
    """Round 9 reserve 1: timeout=1.0 where pgrep consumes 1.0 s must return at 1.0 s (not 1.5 s)
    without invoking _port_state; a 0.75 s pgrep leaves only 0.25 s for _port_state."""
    now, limits = slow_wait(monkeypatch, status_cost=0.0, writers_cost=1.0, port_cost=0.5)
    assert backup._no_writer(SimpleNamespace(port=43219), timeout=1.0) == "unknown"
    assert now[0] - 100.0 == pytest.approx(1.0)
    assert limits == [("pgrep", 1.0)]

    now, limits = slow_wait(monkeypatch, status_cost=0.0, writers_cost=0.75, port_cost=0.25)
    assert backup._no_writer(SimpleNamespace(port=43219), timeout=1.0) == "none"
    assert now[0] - 100.0 == pytest.approx(1.0)
    assert limits[0] == ("pgrep", 1.0) and limits[1][0] == "port" and limits[1][1] == pytest.approx(0.25)


# ---- reserve 2: quickstart startup_failure preserves stop_observation and stop_port ----

@pytest.mark.parametrize("mode, outcome", [("lingers", "stop-not-confirmed"), ("fail", "stop-failed"),
                                           ("timeout", "stop-timeout")])
def test_quickstart_startup_failure_preserves_stop_observation_and_port(either, monkeypatch, mode, outcome):
    """Round 9 reserve 2: startup_failure must keep observation and port from _confirmed_stop."""
    c = either
    c.system.bootout_mode = mode
    result = crash_install(c, monkeypatch, guard=False)
    sf = result["startup_failure"]
    assert sf["stop_attempted"] is True and sf["stopped"] is False and sf["stop_outcome"] == outcome
    assert sf["stop_observation"] == "loaded" and sf["stop_port"] == "free"


# ---------------------------------------------------------------- review round 10

def not_operational(b):
    """The job stays loaded / the unit stays known, with NO process (the review's counter-example)."""
    if b.kind == "launchd":
        b.scripts.set(process=False)                    # ``state = spawn scheduled``, no pid
    else:
        b.scripts.set(show={"ActiveState": "activating", "SubState": "auto-restart", "MainPID": "0"})


def foreign_job(b):
    return dict(b.scripts.service, argv=["/usr/bin/sleep", "600"], python="/foreign/bin/other-server")


# ---- 1. a service is "running" / "put back" only with a process of the attested job ----

@pytest.mark.parametrize("active_state, sub_state, pid, running", [
    ("active", "running", "4242", True),
    ("activating", "auto-restart", "0", False),          # the review's case, exactly
    ("activating", "start", "4242", False),
    ("active", "running", "0", False),
    ("active", "exited", "4242", False),
    ("reloading", "reload", "4242", False),
    ("deactivating", "stop-sigterm", "4242", False),
])
def test_systemd_says_running_only_with_a_main_process(active_state, sub_state, pid, running):
    text = (SHOW_ACTIVE_ENABLED.replace("ActiveState=active", f"ActiveState={active_state}")
            .replace("SubState=running", f"SubState={sub_state}").replace("MainPID=4242", f"MainPID={pid}"))
    manager = managers.SystemdUser()
    seen = manager.observe(FIXED, run=frozen(text))
    assert seen["state"] == "loaded" and seen["active"] is True and seen["running"] is running
    assert manager.status_detail("test.memory-os", run=frozen(text)) == (
        "running", "loaded" if running else "loaded but not running")


@pytest.mark.parametrize("tail, running", [
    ("state = running\npid = 4242\n", True),
    ("state = running\n", False),                       # no pid
    ("state = running\npid = 0\n", False),
    ("state = spawn scheduled\nruns = 3\nlast exit code = 1\n", False),
    ("state = waiting\npid = 4242\n", False),
    ("pid = 4242\n", False),                            # no state line: not read as running
    ("", False),                                        # a job that is only loaded
])
def test_launchd_says_running_only_with_a_pid_and_the_running_state(tail, running):
    service = {"label": "test.memory-os", "argv": ["/p/python", "-m", "memory_os_v4.server"],
               "env": {"MEMORY_V4_TOKEN_FILE": "/t/http.token", "MEMORY_V4_DIR": "/d/store"}}
    text = ("program = /p/python\narguments = {\n/p/python\n-m\nmemory_os_v4.server\n}\n"
            "MEMORY_V4_TOKEN_FILE => /t/http.token\nMEMORY_V4_DIR => /d/store\n" + tail)
    seen = managers.Launchd().observe(service, run=lambda argv, **kw: SimpleNamespace(returncode=0, stdout=text, stderr=""))
    assert seen["state"] == "loaded" and seen["running"] is running


def test_the_backup_attestation_tells_a_loaded_job_from_a_running_one(any_backup):
    b = any_backup
    b.scripts.set(loaded=True)
    assert backup._attested_state(b.settings, b.manager)["running"] is True
    not_operational(b)
    state = backup._attested_state(b.settings, b.manager)
    assert state["active"] is True and state["running"] is False   # still to be stopped, never "running"


def test_backup_never_reports_a_service_put_back_without_a_process_of_our_job(any_backup, monkeypatch):
    """Review round 10, blocker 1, exactly: the start returns 0, the unit is ``activating`` /
    ``auto-restart`` with ``MainPID=0`` (launchd: the job is loaded, no pid), another process answers
    HTTP 200, and the state compared with ``before`` was accepted: ``service_restored=True, ok=True``."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_start = backup._service_start

    def start(settings):
        code = real_start(settings)
        not_operational(b)                              # loaded again, and no process ever comes up
        return code
    monkeypatch.setattr(backup, "_service_start", start)
    monkeypatch.setattr(backup, "_wait_healthy", lambda *a, **k: True)       # the foreign HTTP 200
    record = backup.backup(b.settings)
    assert record["restart"] == "after-confirmed-stop" and record["restart_rc"] == 0
    assert record["server_restarted_healthy"] is True
    assert record["service_restored"] is False and record["ok"] is False
    assert record["archive_ok"] is True and record["verified"] is True and "restart_manual" in record
    assert b.verbs().count(b.start_verb) == 1           # reported, never retried
    if b.kind == "systemd-user":
        assert record["service_state_after"] == {"active": True, "running": False, "pid": None,
                                                 "unit_file_state": "disabled"}
        assert record["service_state_restored"] is False
    assert record["archive"] in backup.verified_archives(b.settings)


# ---- 2. "still running" is a success only on a final digest and attestation, AFTER the health wait ----

@pytest.mark.parametrize("during_wait", ["nothing", "service-vanished", "definition-changed", "not-operational",
                                         "job-foreign"])
def test_backup_still_running_branch_is_revalidated_after_the_health_wait(any_backup, monkeypatch, during_wait):
    """Review round 10, blocker 2: no final digest, no new launchd attestation, the systemd one read
    BEFORE the wait. The service vanishing or its definition changing during that wait, with a
    foreign HTTP answer, gave an overall success on both managers."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_snapshot = backup.snapshot

    def snap(store):
        files = real_snapshot(store)
        b.scripts.set(loaded=True)                      # back by itself (KeepAlive / Restart=always) after the copy
        return files

    def healthy(*args, **kwargs):                       # what happens WHILE the health is awaited
        if during_wait == "service-vanished":
            b.scripts.set(loaded=False)
        elif during_wait == "definition-changed":
            substitute(b, "comment-added")
        elif during_wait == "not-operational":
            not_operational(b)
        elif during_wait == "job-foreign":
            b.scripts.set(service=foreign_job(b))
        return True                                     # HTTP 200, whoever answers
    monkeypatch.setattr(backup, "snapshot", snap)
    monkeypatch.setattr(backup, "_wait_healthy", healthy)
    record = backup.backup(b.settings)
    assert record["stop_confirmed"] is True and record["restart"] == "not-needed-service-still-running"
    assert "restart_rc" not in record and b.start_verb not in b.verbs() and b.verbs().count(b.stop_verb) == 1
    assert record["server_restarted_healthy"] is True and record["archive_ok"] is True
    if during_wait == "nothing":
        assert record["service_restored"] is True and record["ok"] is True and "restart_manual" not in record
        if b.kind == "systemd-user":
            assert record["service_state_restored"] is True
        return
    assert record["service_restored"] is False and record["ok"] is False and "restart_manual" in record
    if b.kind == "systemd-user":
        assert record["service_state_restored"] is False


# ---- 3. everything that authorises the start is read again AFTER the probes, right before the command ----

def after_the_stop(b, monkeypatch, action):
    """Run ``action`` during the writer/port probe that precedes the restart (never during the stop wait)."""
    real_probe, stopped, probes = backup._no_writer, [], []

    def wait(settings, label):
        done = REAL_WAIT_STOPPED(settings, label, timeout=30)
        stopped.append(done)
        return done

    def probe(settings, **kwargs):
        answer = real_probe(settings, **kwargs)
        if stopped:
            if len(probes) == 1:                        # 0: the probe that follows the copy; 1: before the restart
                action()                                # the probe is long: this happens while it runs
            probes.append(answer)
        return answer
    monkeypatch.setattr(backup, "_wait_stopped", wait)
    monkeypatch.setattr(backup, "_no_writer", probe)
    return probes


@pytest.mark.parametrize("how", ["command-replaced", "comment-added", "symlink-to-the-same-bytes", "removed"])
def test_backup_never_starts_a_definition_substituted_during_the_probe_before_the_restart(any_backup, monkeypatch, how):
    """Review round 10, blocker 3, exactly: digest and attestation were read BEFORE ``_no_writer``; the
    definition replaced during that probe gave ``after-confirmed-stop`` and a start without revalidation."""
    b = any_backup
    b.scripts.set(loaded=True)
    probes = after_the_stop(b, monkeypatch, lambda: substitute(b, how))
    record = backup.backup(b.settings)
    assert record["stop_confirmed"] is True and probes == ["none", "none"]  # the probes saw nothing wrong
    assert record["restart"] == "not-restarted-definition-changed" and "restart_rc" not in record
    assert b.start_verb not in b.verbs() and b.scripts.state()["loaded"] is False
    assert record["service_restored"] is False and record["ok"] is False and "restart_manual" in record
    assert record["archive_ok"] is True


@pytest.mark.parametrize("change, restart", [("job-foreign", "not-attempted-state-unknown"),
                                             ("activation-changed", "not-restarted-definition-changed"),
                                             ("started-by-someone", "not-needed-service-still-running")])
def test_backup_attests_the_unit_again_after_the_probe_before_the_restart(systemd_backup, monkeypatch, change, restart):
    b = systemd_backup
    b.kind = "systemd-user"
    b.scripts.set(loaded=True, enabled=False)

    def action():
        if change == "job-foreign":
            b.scripts.set(service=foreign_job(b))
        elif change == "activation-changed":
            b.scripts.set(enabled=True)
        else:
            b.scripts.set(loaded=True)
    after_the_stop(b, monkeypatch, action)
    record = backup.backup(b.settings)
    assert record["restart"] == restart and "restart_rc" not in record
    assert "start" not in b.scripts.verbs("systemctl")


def test_backup_reads_the_attestation_between_the_probes_and_the_start(any_backup, monkeypatch):
    """The order itself: attest, probe, attest AGAIN, start, and the final attestation after the health wait."""
    b = any_backup
    b.scripts.set(loaded=True)
    events = []
    real_attest, real_start, real_digest = backup._attested_state, backup._service_start, backup._definition_digest
    monkeypatch.setattr(backup, "_attested_state", lambda *a, **k: events.append("attest") or real_attest(*a, **k))
    monkeypatch.setattr(backup, "_definition_digest", lambda *a, **k: events.append("digest") or real_digest(*a, **k))
    monkeypatch.setattr(backup, "_service_start", lambda settings: events.append("start") or real_start(settings))
    monkeypatch.setattr(backup, "_wait_healthy", lambda *a, **k: events.append("healthy") or True)
    monkeypatch.setattr(backup, "_responder_pid", lambda settings: events.append("responder") or 4242)
    after_the_stop(b, monkeypatch, lambda: events.append("probe"))
    record = backup.backup(b.settings)
    assert record["service_restored"] is True and record["ok"] is True
    assert events == ["digest", "attest", "digest",                         # before the stop
                      "digest", "attest", "probe", "digest", "attest", "digest", "start",
                      "healthy", "digest", "attest", "digest", "responder"]  # the proof comes last


def test_backup_stops_nothing_when_the_definition_changes_while_it_is_attested(any_backup, monkeypatch):
    """Adversarial pass: the digest kept for the restart was read after the attestation only."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_attest, calls = backup._attested_state, []

    def attest(settings, manager):
        state = real_attest(settings, manager)
        if not calls:
            substitute(b, "comment-added")              # replaced right after it was read
        calls.append(state)
        return state
    monkeypatch.setattr(backup, "_attested_state", attest)
    record = backup.backup(b.settings)
    assert record["ok"] is False and "changed while it was being attested; nothing stopped" in record["error"]
    assert b.stop_verb not in b.verbs() and b.start_verb not in b.verbs() and "restart" not in record
    assert b.scripts.state()["loaded"] is True


# ---- 4. uninstall: an incomplete client removal never dispenses with the final observation ----

@pytest.mark.parametrize("case, outcome, observation", [
    ("all-absent", "absent", None),
    ("port-listening", "absence-unconfirmed", "absent"),
    ("port-unknown", "absence-unconfirmed", "absent"),
    ("job-loaded", "refused-job-without-definition", "foreign-or-unattested"),
    ("observation-fails", "absence-unconfirmed", "indeterminate"),
])
def test_uninstall_partial_without_a_definition_still_observes_the_manager_and_the_port(
        either, monkeypatch, case, outcome, observation):
    """Review round 10, reserve 4, exactly: definition absent at the plan + one client removal
    incomplete gave ``service.outcome == "absent"`` with ZERO final probe."""
    c = either
    install(c, profile="lexical")
    definition_of(c).unlink()
    c.system.loaded = c.system.enabled = False
    un = uninstaller()
    probes, marks = [], []

    def port(number, **kwargs):
        probes.append(number)
        late = len(probes) > 1                          # 1: revalidation of the plan; 2: the final observation
        return {"port-listening": "listening", "port-unknown": "unknown"}.get(case, "free") if late else "free"
    monkeypatch.setattr(qs, "_port_state", lambda number, **kw: "free")
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["state"] == "absent" and plan["nothing_to_do"] is False
    monkeypatch.setattr(qs, "_port_state", port)

    def failing_backup(path, target):                   # the removal of the client stays incomplete
        marks.append(len(c.system.verbs(c.tool)))
        if case == "job-loaded":
            c.system.loaded = c.system.enabled = True
        elif case == "observation-fails":
            c.system.stop_sent = True
            c.system.observe_after_stop = "error"
        raise OSError("no space left")
    monkeypatch.setattr(qs, "backup_client_file", failing_backup)
    result = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert {item["client"]: item["status"] for item in result["clients"]}["cursor"] == "backup-failed-entry-untouched"
    assert result["ok"] is False and result["partial"] is True and result["code"] == "uninstall_partial"
    assert c.system.verbs(c.tool)[marks[0]:] == [observe_verb(c)]            # the manager was asked again, read-only
    assert probes == [c.settings.port, c.settings.port]                      # and so was the port
    assert result["service"]["outcome"] == outcome and result["service"].get("observation") == observation
    assert result["service"]["port"] == {"port-listening": "listening", "port-unknown": "unknown"}.get(case, "free")
    assert servers(c.home, "cursor").get("memory-os-ai")                     # the entry is still there


def test_uninstall_journal_says_nothing_about_the_service_before_it_was_observed(either, monkeypatch):
    """Adversarial pass: the write-ahead journal said ``outcome: absent`` before any observation of this run."""
    c = either
    install(c, profile="lexical")
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    real = un.remaining_dependencies

    def interrupted(home, port, **kwargs):
        if "removed" not in kwargs:                     # the call of ``apply``, after the client removals
            raise RuntimeError("interrupted")
        return real(home, port, **kwargs)
    monkeypatch.setattr(un, "remaining_dependencies", interrupted)
    with pytest.raises(RuntimeError):
        un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    journals = list((c.home / STATE / "uninstall").glob("*/uninstall.json"))
    assert len(journals) == 1 and json.loads(journals[0].read_text())["service"]["outcome"] == "pending"
    assert c.stop_verb not in c.system.verbs(c.tool)


# ---- 5. health and doctor read the validity of an archive like the rotation does ----

def test_health_and_doctor_count_a_valid_archive_whose_service_was_not_put_back(any_backup):
    """Review round 10, reserve 5: ``archive_ok=True`` (qualified, verified, eligible for the rotation)
    with ``ok=False`` was announced as "no backup" by ``last_good_backup_age`` and by doctor."""
    from memory_os_v4.ops import health
    b = any_backup
    b.scripts.set(loaded=True, fail={b.start_verb: 1})
    record = backup.backup(b.settings)
    assert record["archive_ok"] is True and record["ok"] is False and record["service_restored"] is False
    assert record["archive"] in backup.verified_archives(b.settings)
    age = health.last_good_backup_age(b.settings)
    assert age is not None and age <= 0.1
    check = doctor._backup(b.settings)
    assert check.status == "PASS" and "last verified qualified backup" in check.detail


@pytest.mark.parametrize("fields, counted", [
    ({"archive_ok": True, "ok": False}, True),          # 4.1.1: the archive is good, the service was not put back
    ({"archive_ok": True, "ok": True}, True),
    ({"archive_ok": False, "ok": True}, False),         # ``archive_ok`` decides when it exists
    ({"archive_ok": "yes", "ok": True}, False),
    ({"ok": True}, True),                               # older and SQLite records: ``ok``, as before
    ({"ok": False}, False),
    ({}, False),
])
def test_every_reader_of_the_backup_log_applies_the_same_archive_rule(tmp_path, fields, counted):
    from memory_os_v4.ops import health
    settings = doctor_settings(tmp_path)
    settings.backup_dir.mkdir()
    archive = settings.backup_dir / "memory-os-v4-20261123-101500.mosb"
    archive.write_bytes(b"archive bytes")
    (settings.ops_dir / "logs").mkdir(parents=True)
    line = {"stamp": "20261123-101500", "qualified": True, "verified": True, "archive": archive.name,
            "archive_sha256": backup.sha256(archive), **fields}
    (settings.ops_dir / "logs/backup.jsonl").write_text(json.dumps(line) + "\n")
    assert backup.archive_valid(line) is counted
    assert (archive.name in backup.verified_archives(settings)) is counted
    assert (health.last_good_backup_age(settings) is not None) is counted
    assert (doctor._backup(settings).detail != "no verified qualified backup yet") is counted


# ---- adversarial pass: other readers and other actions of the same two families ----

@pytest.mark.parametrize("active_state, sub_state, pid", [("activating", "auto-restart", "0"),
                                                          ("deactivating", "stop-sigterm", "4242")])
def test_doctor_never_passes_a_unit_without_a_running_main_process(monkeypatch, tmp_path, active_state, sub_state, pid):
    text = (SHOW_ACTIVE_ENABLED.replace("ActiveState=active", f"ActiveState={active_state}")
            .replace("SubState=running", f"SubState={sub_state}").replace("MainPID=4242", f"MainPID={pid}"))
    monkeypatch.setattr(managers, "host_manager", lambda: managers.SystemdUser())
    monkeypatch.setattr(doctor.subprocess, "run", frozen(text))
    check = doctor._service(doctor_settings(tmp_path))
    assert (check.status, check.detail) == ("WARN", "systemd unit test.memory-os loaded but not running")


def test_rollback_does_not_stop_a_job_whose_definition_changed_while_it_was_attested(either):
    """Adversarial pass: the digest of the definition was checked BEFORE the observation of the job (up to
    5 s), and the stop followed without a second look."""
    c = either
    result = install(c, profile="lexical")
    assert result["ok"] is True
    path, fired = definition_of(c), []

    def replace():
        if not fired:                                   # during the attestation that precedes the stop
            path.write_text(path.read_text(encoding="utf-8") + (
                "\n<!-- edited -->\n" if c.kind == "launchd" else "\n# edited\n"), encoding="utf-8")
        fired.append(1)
    c.system.hooks[("launchctl", "print") if c.kind == "launchd" else ("systemctl", "--user", "show")] = replace
    undone = qs.undo(result["stamp"], home=c.home, run=c.system, with_service=True, sleep=lambda s: None)
    assert fired and undone["ok"] is False
    assert undone["service"]["undo"] == "refused-changed-since-install"
    assert c.stop_verb not in c.system.verbs(c.tool) and c.system.loaded is True
    assert path.is_file()                               # nothing moved either



# ---------------------------------------------------------------- review round 11 (contract rev. 5, D6 bis)

# ---- 1. the HTTP responder must BE the process the manager attests ----

@pytest.mark.parametrize("payload, pid", [
    ({"ok": True, "server": "memory-os-v4", "pid": 4242}, 4242),
    ({"ok": True, "server": "memory-os-v4"}, None),                     # an older build: identifies nobody
    ({"ok": True, "server": "another-server", "pid": 4242}, None),
    ({"ok": False, "server": "memory-os-v4", "pid": 4242}, None),
    ({"ok": 1, "server": "memory-os-v4", "pid": 4242}, None),
    ({"ok": True, "server": "memory-os-v4", "pid": "4242"}, None),
    ({"ok": True, "server": "memory-os-v4", "pid": True}, None),
    ({"ok": True, "server": "memory-os-v4", "pid": 0}, None),
    ([4242], None), (None, None),
])
def test_a_responder_is_identified_only_by_a_complete_answer(monkeypatch, tmp_path, payload, pid):
    from memory_os_v4.ops import auth
    assert auth.responder_pid(payload) == pid
    settings = doctor_settings(tmp_path)
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (200, json.dumps(payload).encode()))
    assert auth.health_responder_pid(settings) == pid and REAL_RESPONDER_PID(settings) == pid
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (503, json.dumps(payload).encode()))
    assert auth.health_responder_pid(settings) is None                  # only an HTTP 200 identifies
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (200, b"not json"))
    assert auth.health_responder_pid(settings) is None
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (-1, b""))
    assert auth.health_responder_pid(settings) is None


def answer_health(monkeypatch, payload):
    """The REAL identification of the responder, fed with one ``/health`` answer."""
    from memory_os_v4.ops import auth
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (200, json.dumps(payload).encode()))
    monkeypatch.setattr(backup, "_responder_pid", REAL_RESPONDER_PID)


FOREIGN_ANSWERS = [
    {"ok": True, "server": "memory-os-v4", "transport": "http", "pid": 999},    # the review's case: another PID
    {"ok": True, "server": "memory-os-v4", "transport": "http"},                # no PID at all
    {"ok": True, "server": "something-else", "pid": 4242},
    {"ok": False, "server": "memory-os-v4", "pid": 4242},
]


@pytest.mark.parametrize("answer", FOREIGN_ANSWERS)
@pytest.mark.parametrize("branch", ["restarted", "still-running"])
def test_backup_service_put_back_needs_the_responder_to_be_the_attested_process(any_backup, monkeypatch, answer, branch):
    """Review round 11, point 1: the job is attested RUNNING (pid 4242), ``/health`` answers 200, and
    the answer comes from another process; both proofs were accepted together."""
    b = any_backup
    b.scripts.set(loaded=True)
    if branch == "still-running":
        real_snapshot = backup.snapshot

        def snap(store):
            files = real_snapshot(store)
            b.scripts.set(loaded=True)                  # back by itself after the copy
            return files
        monkeypatch.setattr(backup, "snapshot", snap)
    answer_health(monkeypatch, answer)
    record = backup.backup(b.settings)
    assert record["restart"] == ("after-confirmed-stop" if branch == "restarted" else "not-needed-service-still-running")
    assert backup._attested_state(b.settings, b.manager)["pid"] == 4242     # the job itself is attested running
    assert record["server_restarted_healthy"] is True
    assert record["service_restored"] is False and record["ok"] is False and "restart_manual" in record
    assert record["archive_ok"] is True
    if b.kind == "systemd-user":
        assert record["service_state_restored"] is False
    answer_health(monkeypatch, {"ok": True, "server": "memory-os-v4", "transport": "http", "pid": 4242})
    proven = backup._restoration(b.settings, b.manager, {"active": True, "unit_file_state": record.get(
        "service_state_before", {}).get("unit_file_state")}, backup._definition_digest(b.settings, b.manager))
    assert proven[1] is True                             # control: the same state, answered by the job's process


@pytest.mark.parametrize("case", ["other-pid", "no-pid", "other-server", "not-ok", "job-without-process"])
def test_install_succeeds_only_when_ready_is_answered_by_the_attested_process(either, case):
    """Review round 11, arbitration (a): ``_ready`` and the relay prove an application, not that the
    installed job provides it; the positive branch skipped the observation of the job."""
    c = either
    before = (c.home / PATHS["cursor"]).read_bytes()
    if case == "other-pid":
        c.ready["pid"] = 999                            # a running job (pid 4242) and another process answering
    elif case == "no-pid":
        del c.ready["pid"]
    elif case == "other-server":
        c.ready["server"] = "something-else"
    elif case == "not-ok":
        c.ready["ok"] = False
    else:
        c.system.process = False                        # loaded, no process: whoever answers is not the job
    result = install(c, profile="lexical")
    assert result["ok"] is False and result["code"] == "service_startup_failed"
    assert "not by the attested process of the installed service" in result["error"]
    assert result["responder"]["same_process"] is False and result["stage"] == "service"
    assert result["responder"]["job_running"] is (case != "job-without-process")
    assert "clients" not in result and (c.home / PATHS["cursor"]).read_bytes() == before
    assert c.system.verbs(c.tool)[-1] == observe_verb(c)                     # the job was observed, read-only


def test_install_control_the_job_process_answers(either):
    c = either
    c.system.pid = c.ready["pid"] = 31337
    result = install(c, profile="lexical")
    assert result["ok"] is True and "responder" not in result


# ---- 2. an archive is qualified only when no writer is proven over the WHOLE copy ----

@pytest.mark.parametrize("running", [True, False])
@pytest.mark.parametrize("late, said", [("writer", "is alive after the copy"), ("port", "is alive after the copy"),
                                        ("pgrep-fails", "could not be observed after the copy"),
                                        ("port-fails", "could not be observed after the copy")])
def test_backup_qualifies_nothing_when_a_writer_is_not_excluded_after_the_copy(any_backup, monkeypatch, running,
                                                                               late, said):
    """Review round 11, point 5 / arbitration (c): the probe preceded ``snapshot()`` only, in both
    branches (service stopped by us, service already stopped)."""
    from memory_os_v4.ops import health
    b = any_backup
    b.scripts.set(loaded=running)
    copied, real_snapshot = [], backup.snapshot

    def snap(store):
        files = real_snapshot(store)
        copied.append(1)
        return files
    monkeypatch.setattr(backup, "snapshot", snap)
    monkeypatch.setattr(backup, "_writer_processes", lambda **kw: (
        (["77 python -m memory_os_v4.server"] if late == "writer" else None if late == "pgrep-fails" else [])
        if copied else []))
    monkeypatch.setattr(backup, "_port_state", lambda port, **kw: (
        ("listening" if late == "port" else "unknown" if late == "port-fails" else "free") if copied else "free"))
    record = backup.backup(b.settings)
    assert copied and said in record["error"] and "nothing qualified" in record["error"]
    assert record["qualified"] is False and record["verified"] is False and record["archive_ok"] is False
    assert record["ok"] is False and "archive" not in record
    assert backup.verified_archives(b.settings) == {} and health.last_good_backup_age(b.settings) is None
    assert not b.settings.backup_dir.exists() or not list(b.settings.backup_dir.iterdir())
    if running:
        assert b.start_verb not in b.verbs() and record["service_restored"] is False


def test_backup_probes_the_writers_before_and_after_the_copy(any_backup, monkeypatch):
    b = any_backup
    events, real_snapshot, real_probe = [], backup.snapshot, backup._no_writer
    monkeypatch.setattr(backup, "snapshot", lambda store: events.append("copy") or real_snapshot(store))
    monkeypatch.setattr(backup, "_no_writer", lambda settings, **kw: events.append("probe") or real_probe(settings, **kw))
    record = backup.backup(b.settings)                                       # the service is already stopped
    assert record["ok"] is True and record["qualified"] is True and events == ["probe", "copy", "probe"]


# ---- 3. Claude Code: "restored" only after the file was read back ----

@pytest.mark.parametrize("previous", [False, True])
def test_undo_of_claude_code_is_restored_only_when_the_file_says_so(c, previous):
    """Review round 11, point 8: ``claude mcp remove`` / ``add`` returning zero without the expected
    state gave ``restored``."""
    if not previous:
        absent(c, "claude-code")
    result = install(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/local/bin/" + name,
                     **({"replace_existing": ("claude-code",)} if previous else {}))
    assert result["ok"] is True, result.get("error")
    ours = servers(c.home, "claude-code")["memory-os-ai"]
    c.system.claude_noop = True                         # the CLI returns 0 and changes nothing
    undone = qs.undo(result["stamp"], home=c.home, run=c.system)
    assert undone["results"] == [{"client": "claude-code", "undo": "unconfirmed-entry-state-after-claude-cli"}]
    assert undone["ok"] is False and undone["code"] == "undo_partial"
    assert servers(c.home, "claude-code")["memory-os-ai"] == ours


def test_uninstall_of_claude_code_is_removed_only_when_the_file_says_so(c):
    absent(c, "claude-code")
    assert install(c, profile="lexical", clients=("claude-code",), which=lambda name: "/usr/local/bin/" + name)["ok"]
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system, clients=("claude-code",))
    assert plan["ok"] is True
    c.system.claude_noop = True
    result = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert [item["status"] for item in result["clients"]] == ["failed-entry-kept-unproven"]
    assert result["ok"] is False and servers(c.home, "claude-code").get("memory-os-ai")


# ---- 4. exact wording: a job that is loaded without a process is not "still running" ----

def test_backup_does_not_call_a_job_without_a_process_still_running(any_backup, monkeypatch):
    """Review round 11, point 9: ``active=True, running=False`` gave ``not-needed-service-still-running``."""
    b = any_backup
    b.scripts.set(loaded=True)
    real_snapshot = backup.snapshot

    def snap(store):
        files = real_snapshot(store)
        b.scripts.set(loaded=True)                      # loaded again after the copy…
        not_operational(b)                              # …and no process ever comes up
        return files
    monkeypatch.setattr(backup, "snapshot", snap)
    record = backup.backup(b.settings)
    assert record["restart"] == "not-restarted-service-not-running" and "restart_rc" not in record
    assert b.start_verb not in b.verbs() and record["service_restored"] is False and record["ok"] is False
    assert "restart_manual" in record and record["archive_ok"] is True


# ---- 5. cheap re-readings right before the action (the rest is the declared TOCTOU limit) ----

@pytest.mark.parametrize("guard", [False, True])
def test_install_does_not_stop_a_job_whose_definition_changed_while_it_was_attested(either, monkeypatch, guard):
    """Review round 11, point 3: the digest was read BEFORE ``_job_state()``, then the stop was sent."""
    c = either
    real_state, fired = qs._job_state, []

    def state(service, **kwargs):
        answer = real_state(service, **kwargs)
        if answer == "loaded" and not fired:            # the attestation that precedes the automatic stop
            path = definition_of(c)
            path.write_text(path.read_text(encoding="utf-8") + (
                "\n<!-- edited -->\n" if c.kind == "launchd" else "\n# edited\n"), encoding="utf-8")
            fired.append(1)
        return answer
    monkeypatch.setattr(qs, "_job_state", state)
    result = crash_install(c, monkeypatch, guard=guard)
    assert fired and result["ok"] is False
    assert c.stop_verb not in c.system.verbs(c.tool) and c.system.loaded is True
    assert "guard_stop" not in result and "stop_attempted" not in result.get("startup_failure", {})


def test_install_does_not_start_a_definition_replaced_right_after_it_was_written(either, monkeypatch):
    """Review round 11, point 4: nothing compared the definition between its creation and the start."""
    from memory_os_v4.ops import auth
    c = either
    real_write = auth.write_secret_file

    def write(path, data, **kwargs):
        done = real_write(path, data, **kwargs)
        if Path(path) == definition_of(c):
            Path(path).write_bytes(data + b"\n# replaced\n")
        return done
    monkeypatch.setattr(auth, "write_secret_file", write)
    result = install(c, profile="lexical")
    assert result["ok"] is False and result["code"] == "preflight_changed"
    assert "changed before its start" in result["error"] and c.system.loaded is False
    assert not {"bootstrap", "enable", "daemon-reload"} & set(c.system.verbs(c.tool))


def test_a_definition_appearing_during_the_observation_is_not_already_absent(either, tmp_path):
    """Review round 11, point 7: the path was not read again after the probes."""
    c = either
    service = prepare(c, profile="lexical")["service"]
    path = definition_of(c)

    def appear():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("appeared", encoding="utf-8")
    c.system.hooks[("launchctl", "print") if c.kind == "launchd" else ("systemctl", "--user", "show")] = appear
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    outcome = qs.stop_owned_service(service, None, journal, run=c.system, port=c.settings.port, sleep=lambda s: None)
    assert outcome["outcome"] == "refused-changed-since-install" and outcome["code"]
    assert path.read_text(encoding="utf-8") == "appeared" and c.stop_verb not in c.system.verbs(c.tool)


def test_uninstall_does_not_say_absent_when_a_definition_appeared_during_the_final_observation(either, monkeypatch):
    c = either
    install(c, profile="lexical")
    path = definition_of(c)
    path.unlink()
    c.system.loaded = c.system.enabled = False
    un = uninstaller()
    plan = un.plan(c.settings, home=c.home, python=c.python, run=c.system)
    assert plan["ok"] is True and plan["service"]["state"] == "absent"
    real_port, probes = qs._port_state, []

    def port(number, **kwargs):
        probes.append(number)
        if len(probes) == 2:                            # the final probe: the definition appears meanwhile
            path.write_text("appeared", encoding="utf-8")
        return real_port(number, **kwargs)
    monkeypatch.setattr(qs, "_port_state", port)
    result = un.apply(plan, c.settings, home=c.home, run=c.system, sleep=lambda s: None)
    assert result["ok"] is False and result["service"]["outcome"] == "refused-service-changed"
    assert result["code"] == "service_changed_since_install"
