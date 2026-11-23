"""Internal service interface (4.1.1, lot L4): one contract, two implementations.

Contract: CONTRAT_4_1_1 §5. The operations are the same on both platforms: render and read the
definition (program, arguments, backend and profile installed), start, attest, stop, and say
what a manual stop is. ``Launchd`` is the 4.1.0 behaviour moved here unchanged (same commands,
same ``launchctl print`` attestation); ``SystemdUser`` drives ``systemd --user`` on the retained
Linux cell (``systemctl --user daemon-reload``, ``enable --now`` / ``disable --now``, attestation
through ``systemctl --user show`` read in a closed ``Key=Value`` form, never free text).

Shared rules: a timeout or an error of an observation is never evidence of absence, of a stop or
of a free port (``port_state`` below reads a port the same way); one stop, no retry, no forced kill
sent by this tool; nothing here removes a definition (the caller moves
it into its private journal, last); ``loginctl enable-linger`` is never run, only shown.

The implementation is chosen from ``cell.detect()["service_manager"]``: ``launchd`` on macOS,
``systemd-user`` only on the Linux platform of a retained cell and only when its presence
evidence is complete; anything else has no manager and installs no service.

Declared limit: the ``systemctl show`` shapes read here (``ExecStart={ path=… ; argv[]=… ; …}``,
``Environment=K=V …``) were written from the systemd documentation and are exercised against a
fake ``systemctl`` only; the real ``systemd --user`` of the cell is attested in its VM (gate P5).
"""
from __future__ import annotations

import errno
import os
import re
import socket
import subprocess
from pathlib import Path

DEFAULT_LABEL = "local.memory-os-v4"
ATTESTED_ENV = ("MEMORY_V4_TOKEN_FILE", "MEMORY_V4_DIR")


def job_run(argv, *, run, timeout):
    """One system command reduced to (return code, stdout, stderr); a timeout or OS error gives None.

    Callers only ever copy the command name and its return code into what we produce (§8).
    """
    try:
        result = run(argv, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None, '', ''
    return result.returncode, result.stdout or '', result.stderr or ''


def port_state(port, *, timeout: float = 0.5) -> str:
    """``listening`` | ``free`` | ``unknown`` for the loopback TCP port of the service: THE reading of a
    port, shared by quickstart, undo, uninstall and backup.

    ``free`` is ONLY the documented refusal of a connection to a port nobody listens on
    (``ECONNREFUSED``, the answer of the loopback interface on macOS and Linux). A timeout
    (``EWOULDBLOCK`` / ``ETIMEDOUT``), a permission error (``EACCES`` / ``EPERM``), any other error
    code, an exception while creating or connecting the socket, or a value that is not a port, is
    ``unknown``: never a free port.
    """
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        return "unknown"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            code = probe.connect_ex(("127.0.0.1", port))
    except (OSError, OverflowError, TypeError, ValueError):
        return "unknown"
    if code == 0:
        return "listening"
    return "free" if code == errno.ECONNREFUSED else "unknown"


def _stop_run(argv, *, run):
    """One stop command; 'ok', 'failed' (non-zero code) or 'timeout'. Output is never kept."""
    try:
        result = run(argv, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return 'timeout'
    except (subprocess.SubprocessError, OSError):
        return 'failed'
    return 'ok' if result.returncode == 0 else 'failed'


class Launchd:
    """macOS: ``~/Library/LaunchAgents/<label>.plist``, ``launchctl bootstrap`` / ``bootout`` / ``print``."""

    kind = "launchd"
    noun = "plist"            # word used in outcomes, journal keys and messages
    text_key = "plist"        # key of the rendered definition in a service plan
    step_key = "plist_sha256"  # key of the definition digest in ``install.json``
    occupied = "port or launchd label occupied; nothing activated"
    ABSENT_CODE = 113         # ``launchctl print`` of a job that is not loaded ("Could not find service")

    @classmethod
    def absent(cls, code, stdout: str, stderr: str) -> bool:
        """THE predicate of absence of a launchd job, used by every reader (``observe``, ``status`` and,
        through it, doctor and the wait of a backup): return code 113 AND "Could not find service".
        The code with another message, or the message with another code, is indeterminate."""
        return code == cls.ABSENT_CODE and "Could not find service" in ((stdout or "") + (stderr or ""))

    def definition_path(self, label: str, home: Path) -> Path:
        return home / "Library/LaunchAgents" / (label + ".plist")

    def render(self, label, argv, env, log) -> str:
        from .cli import _plist
        return _plist(label, argv, env=env, keepalive=True, log=log)

    def read_environment(self, path: Path):
        """``EnvironmentVariables`` of the plist as stored (callers check its type); parse errors propagate."""
        import plistlib
        return plistlib.loads(path.read_bytes()).get('EnvironmentVariables', {})

    def read_definition(self, path: Path) -> dict:
        import plistlib
        document = plistlib.loads(path.read_bytes())
        argv = document.get('ProgramArguments')
        env = document.get('EnvironmentVariables', {})
        if not isinstance(argv, list) or not argv or not isinstance(env, dict):
            raise ValueError('unexpected service definition')
        return {'program': argv[0], 'argv': list(argv), 'env': dict(env), 'log': document.get('StandardErrorPath')}

    def state(self, path: Path, text: str) -> str:
        import plistlib
        state = "absent"
        if path.exists():
            try:
                current = plistlib.loads(path.read_bytes())
                expected = plistlib.loads(text.encode())
                state = "owned" if current == expected else "foreign-or-different"
            except (ValueError, plistlib.InvalidFileException):
                state = "invalid"
        return state

    def plan_extras(self, label: str) -> dict:
        return {}

    def notices(self, label: str) -> list:
        return []

    # ---- commands ----
    def _target(self, label: str) -> str:
        return f"gui/{os.getuid()}/{label}"

    def status_argv(self, label: str) -> list:
        return ["launchctl", "print", self._target(label)]

    def stop_argv(self, label: str) -> list:
        return ["launchctl", "bootout", self._target(label)]

    def start_argv(self, label: str, path) -> list:
        return ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)]

    def start(self, service: dict, *, run):
        """Load the definition; the return code of the command (None: timeout or OS error)."""
        return job_run(self.start_argv(service['label'], service['path']), run=run, timeout=15)[0]

    def start_failed(self, stamp: str) -> str:
        return ('launchd bootstrap failed; plist/journal retained; no automatic repair '
                f'(roll back with quickstart --undo {stamp} --with-service)')

    def observe(self, service: dict, *, run, timeout: float = 5) -> dict:
        code, stdout, stderr = job_run(self.status_argv(service['label']), run=run, timeout=timeout)
        if code is None:
            return {'state': 'unknown'}  # a timeout is never evidence of absence
        if code == 0:
            # A plist on disk alone cannot attest the currently loaded launchd job.
            # Check the launchctl output's program and first argument, not just a label.
            program = re.search(r"(?m)^\s*program = (.+?)\s*$", stdout)
            args = re.search(r"arguments = \{\s*\n([\s\S]*?)\n\s*\}", stdout)

            def unquote(text):
                return text.strip().strip('"')
            actual_args = [unquote(line) for line in args.group(1).splitlines() if line.strip()] if args else []
            if not program or not args or unquote(program.group(1)) != service['argv'][0] or actual_args != service['argv']:
                return {'state': 'foreign-or-unattested'}
            for key in ATTESTED_ENV:
                match = re.search(r"(?m)^\s*" + re.escape(key) + r" => (.+?)\s*$", stdout)
                if not match or unquote(match.group(1)) != service['env'][key]:
                    return {'state': 'foreign-or-unattested'}

            def number(key):
                match = re.search(r'(?m)^\s*' + re.escape(key) + r' = (-?\d+)\s*$', stdout)
                return int(match[1]) if match else None
            # A closed shape only (launchd states are lowercase words): never free text of the output (§8).
            process_state = re.search(r'(?m)^\s*state = ([a-z][a-z -]{0,31})\s*$', stdout)
            pid, word = number('pid'), process_state[1] if process_state else None
            # ``running``: a PROCESS of this attested job exists now (``pid`` > 0 and ``state = running``).
            # A job that is merely loaded (waiting, spawn scheduled, crash-looping) is not a running service.
            return {'state': 'loaded', 'runs': number('runs'), 'last_exit_code': number('last exit code'),
                    'pid': pid, 'process_state': word,
                    'running': pid is not None and pid > 0 and word == 'running'}
        if self.absent(code, stdout, stderr):
            return {'state': 'absent'}
        return {'state': 'unknown'}  # permissions/syntax/IPC failures are not evidence of absence

    def stop(self, service: dict, *, run) -> str:
        """One ``launchctl bootout``; 'ok', 'failed' or 'timeout'."""
        return _stop_run(self.stop_argv(service['label']), run=run)

    def after_remove(self, service: dict, *, run) -> dict:
        return {}

    def manual_stop(self, label: str) -> str:
        return f"launchctl bootout {self._target(label)}"

    def manual_start(self, label: str) -> str:
        return "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist"

    def status(self, label: str, *, run, timeout=None) -> str:
        """Unattested view for doctor and the wait of a backup: 'running' (job loaded), 'stopped' or 'unknown'.

        ``stopped`` is ONLY the documented absence: return code 113 with "Could not find service",
        the answer ``observe`` reads as absent. Any other failure of ``launchctl print`` (IPC,
        permission, syntax, timeout) is ``unknown``, which is never "stopped". The command is
        always bounded (5 s unless the caller says otherwise): a reader never waits forever.
        """
        try:
            result = run(self.status_argv(label), capture_output=True, check=False,
                         timeout=5 if timeout is None else timeout)
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        if result.returncode == 0:
            return "running"

        def text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        return "stopped" if self.absent(result.returncode, text(result.stdout), text(result.stderr)) else "unknown"

    def status_detail(self, label: str, *, run, timeout=None) -> tuple:
        """``(status, word)``: a launchd job that is ``stopped`` is not loaded at all."""
        state = self.status(label, run=run, timeout=timeout)
        return state, {"running": "loaded", "stopped": "not loaded"}.get(state)


# ---------------------------------------------------------------------------------------------- systemd --user

SHOW_PROPERTIES = ("LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "FragmentPath", "DropInPaths",
                   "NeedDaemonReload", "NRestarts", "ExecMainCode", "ExecMainStatus", "ExecStart", "Environment")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_WORD = re.compile(r"[a-z][a-z-]{0,31}")
_PLAIN = re.compile(r"[^\s\x00-\x1f\x7f\"'\\;]+")  # a word ``systemctl show`` prints as it is
# The whole ``ExecStart=`` value of ``systemctl show``. ``exec_shown`` matches it in full AND validates
# every sub-field by VALUE: absolute path, plain words, yes/no, ``[n/a]`` or a timestamp that really
# exists (known weekday that is the weekday of that date, valid date and time, time zone of a closed
# list or a numeric offset), an integer pid, a closed list of exit codes (``(null)`` while nothing
# exited, else the waitid() code names systemd prints), and a status that is ``<integer>/<integer>``
# or ``<integer>/<NAME>`` with NAME among the documented exit-status and signal names. Any other
# value makes the whole observation indeterminate.
EXEC_CODES = ("(null)", "exited", "killed", "dumped", "trapped", "stopped", "continued")
EXIT_STATUS_NAMES = frozenset("""
SUCCESS FAILURE INVALIDARGUMENT NOTIMPLEMENTED NOPERMISSION NOTINSTALLED NOTCONFIGURED NOTRUNNING
CHDIR NICE FDS EXEC MEMORY LIMITS OOM_ADJUST SIGNAL_MASK STDIN STDOUT CHROOT IOPRIO TIMERSLACK SECUREBITS
SETSCHEDULER CPUAFFINITY GROUP USER CAPABILITIES CGROUP SETSID CONFIRM STDERR PAM NETWORK NAMESPACE
NO_NEW_PRIVILEGES SECCOMP SELINUX_CONTEXT PERSONALITY APPARMOR ADDRESS_FAMILIES RUNTIME_DIRECTORY CHOWN
SMACK_PROCESS_LABEL KEYRING STATE_DIRECTORY CACHE_DIRECTORY LOGS_DIRECTORY CONFIGURATION_DIRECTORY NUMA_POLICY
CREDENTIALS BPF KSM
USAGE DATAERR NOINPUT NOUSER NOHOST UNAVAILABLE SOFTWARE OSERR OSFILE CANTCREAT IOERR TEMPFAIL PROTOCOL NOPERM CONFIG
""".split())
SIGNAL_NAMES = frozenset("""
HUP INT QUIT ILL TRAP ABRT BUS FPE KILL USR1 SEGV USR2 PIPE ALRM TERM STKFLT CHLD CONT STOP TSTP TTIN TTOU URG
XCPU XFSZ VTALRM PROF WINCH IO PWR SYS RTMIN RTMAX
""".split())
TIME_ZONES = frozenset("""
UTC GMT WET WEST BST IST CET CEST EET EEST MSK EST EDT CST CDT MST MDT PST PDT AKST AKDT HST JST KST
AEST AEDT ACST ACDT AWST NZST NZDT SAST
""".split())
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIME_FIELD = r"\[([^\]\n]*)\]"
_EXEC_SHAPE = re.compile(
    r"\{ path=(/[^\s\x00-\x1f\x7f;]*) ; argv\[\]=([^\s;]+(?: [^\s;]+)*) ; ignore_errors=(?:yes|no) ; "
    r"start_time=" + _TIME_FIELD + r" ; stop_time=" + _TIME_FIELD + r" ; pid=[0-9]{1,10} ; "
    r"code=(?:" + "|".join(re.escape(code) for code in EXEC_CODES) + r") ; "
    r"status=([0-9]{1,5})/([0-9A-Z_+-]{1,24}) \}")
_STAMP = re.compile(r"([A-Z][a-z]{2}) ([0-9]{4})-([0-9]{2})-([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) (\S{1,5})")


def _valid_time(text: str) -> bool:
    """``n/a``, or ``Www YYYY-MM-DD HH:MM:SS ZONE`` naming a moment that exists."""
    import datetime
    if text == "n/a":
        return True
    match = _STAMP.fullmatch(text)
    if not match or match[1] not in _WEEKDAYS:
        return False
    try:
        moment = datetime.datetime(*(int(match[index]) for index in range(2, 8)))
    except ValueError:
        return False
    if _WEEKDAYS[moment.weekday()] != match[1]:
        return False
    zone = match[8]
    offset = re.fullmatch(r"[+-]([0-9]{2})([0-9]{2})?", zone)
    if offset:
        return int(offset[1]) <= 14 and int(offset[2] or 0) < 60
    return zone in TIME_ZONES


def _valid_status(name: str) -> bool:
    if re.fullmatch(r"[0-9]{1,5}", name) or name in EXIT_STATUS_NAMES or name in SIGNAL_NAMES:
        return True
    realtime = re.fullmatch(r"RTMIN\+([0-9]{1,2})|RTMAX-([0-9]{1,2})", name)
    return bool(realtime) and int(realtime[1] or realtime[2]) <= 32


def exec_shown(value: str):
    """Match object of a whole, VALID ``ExecStart=`` value (group 1: path, group 2: words), else None."""
    match = _EXEC_SHAPE.fullmatch(value)
    if not match or not _valid_time(match[3]) or not _valid_time(match[4]) or not _valid_status(match[6]):
        return None
    return match


_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s\"'\\]*)")
LINGER_EFFECT = ("keeps the systemd user manager of this account, and so this service, running after logout and "
                 "starts it at boot; without it the service runs only while a session of this user is open")


def _unit_quote(value, *, exec_line=False) -> str:
    """One double-quoted unit-file word: backslash and quote escaped, ``%`` (and ``$`` on ExecStart) doubled."""
    if not isinstance(value, str) or _CONTROL.search(value):
        raise ValueError("a service definition value holds a control character")
    out = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if exec_line:
        out = out.replace("$", "$$")
    return '"' + out + '"'


def _unit_unquote(body: str, *, exec_line=False) -> str:
    """Inverse of ``_unit_quote`` on the inside of the quotes; anything we would not have written is refused."""
    out, index = [], 0
    while index < len(body):
        char = body[index]
        pair = body[index:index + 2]
        if char == "\\":
            if pair not in ("\\\\", '\\"'):
                raise ValueError("unexpected escape")
            out.append(pair[1])
            index += 2
        elif char == "%" or (exec_line and char == "$"):
            if pair != char * 2:
                raise ValueError("unexpected specifier or variable")
            out.append(char)
            index += 2
        else:
            out.append(char)
            index += 1
    return "".join(out)


def parse_unit(text: str):
    """``{section: [(key, value), …]}`` of a unit of the plain shape we write, else None.

    Comments and blank lines are ignored. A line outside a section, a line without ``=``, a
    continuation line or a repeated section is not our shape.
    """
    sections, current = {}, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if current in sections:
                return None
            sections[current] = []
            continue
        if current is None or "=" not in line or line.endswith("\\"):
            return None
        key, value = line.split("=", 1)
        sections[current].append((key.strip(), value.strip()))
    return sections


def _words(value: str, *, exec_line=False) -> list:
    """The double-quoted words of a value, separated by single spaces; any other shape is refused."""
    words, index = [], 0
    while index < len(value):
        match = _QUOTED.match(value, index)
        if not match:
            raise ValueError("unexpected word")
        words.append(_unit_unquote(match[1], exec_line=exec_line))
        index = match.end()
        if index < len(value):
            if value[index] != " ":
                raise ValueError("unexpected separator")
            index += 1
    return words


def parse_show(stdout: str):
    """``systemctl show`` output as a dict, in a closed form: only the requested ``Key=Value`` lines.

    A line of another shape, an unrequested key or a repeated key gives None: the observation is
    then indeterminate, never an absence.
    """
    found = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in SHOW_PROPERTIES or key in found:
            return None
        found[key] = value
    return found


# Closed vocabularies of the properties read (systemd.unit / systemd.service / systemctl documentation).
LOAD_STATES = frozenset({"stub", "loaded", "not-found", "bad-setting", "error", "merged", "masked"})
ACTIVE_STATES = frozenset({"active", "reloading", "inactive", "failed", "activating", "deactivating", "maintenance",
                           "refreshing"})
SUB_STATES = frozenset({
    "dead", "condition", "start-pre", "start", "start-post", "running", "exited", "reload", "reload-signal",
    "reload-notify", "mounting", "stop", "stop-watchdog", "stop-sigterm", "stop-sigkill", "stop-post",
    "final-watchdog", "final-sigterm", "final-sigkill", "failed", "dead-before-auto-restart",
    "failed-before-auto-restart", "dead-resources-pinned", "auto-restart", "auto-restart-queued", "cleaning"})
UNIT_FILE_STATES = frozenset({"", "enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "masked",
                              "masked-runtime", "static", "disabled", "indirect", "generated", "transient", "bad",
                              "not-found"})
_NUMBER = re.compile(r"[0-9]{1,10}")
_PATH_SHOWN = re.compile(r"/[^\s\x00-\x1f\x7f]*")


def valid_show(shown: dict) -> bool:
    """Schema AND values of a parsed ``systemctl show``, checked before ANY conclusion is drawn from it.

    Every requested property is present (``ExecStart`` alone may be omitted, and only for a unit
    that is not ``loaded``); states belong to their closed vocabulary; counters are integers;
    paths are absolute paths or empty; ``ExecStart`` is empty or the whole documented form;
    ``Environment`` is empty or plain assignments without a repeated name. Anything else makes
    the whole observation indeterminate, in every branch: absent, loaded, stopped or running.
    """
    required = set(SHOW_PROPERTIES) - ({"ExecStart"} if shown.get("LoadState") != "loaded" else set())
    if not required <= set(shown):
        return False
    if (shown["LoadState"] not in LOAD_STATES or shown["ActiveState"] not in ACTIVE_STATES
            or shown["SubState"] not in SUB_STATES or shown["UnitFileState"] not in UNIT_FILE_STATES
            or shown["NeedDaemonReload"] not in {"yes", "no"}):
        return False
    if any(not _NUMBER.fullmatch(shown[key]) for key in ("MainPID", "NRestarts", "ExecMainCode", "ExecMainStatus")):
        return False
    if shown["FragmentPath"] and not _PATH_SHOWN.fullmatch(shown["FragmentPath"]):
        return False
    if shown["DropInPaths"] and any(not _PATH_SHOWN.fullmatch(word) for word in shown["DropInPaths"].split(" ")):
        return False
    command = shown.get("ExecStart", "")
    if command and exec_shown(command) is None:
        return False
    if shown["LoadState"] == "loaded" and not command:
        return False
    names = []
    for word in (shown["Environment"].split(" ") if shown["Environment"] else []):
        assignment = _ASSIGNMENT.fullmatch(word)
        if not assignment or assignment[1] in names:
            return False
        names.append(assignment[1])
    return True


class SystemdUser:
    """Linux: ``$XDG_CONFIG_HOME/systemd/user/<label>.service`` driven through ``systemctl --user``."""

    kind = "systemd-user"
    noun = "unit"
    text_key = "unit"
    step_key = "unit_sha256"
    occupied = "port or systemd unit name occupied; nothing activated"

    def __init__(self, environ=None):
        self.environ = os.environ if environ is None else environ

    def unit(self, label: str) -> str:
        return label + ".service"

    def definition_path(self, label: str, home: Path) -> Path:
        """``$XDG_CONFIG_HOME/systemd/user/<label>.service``; default ``~/.config/systemd/user/``.

        ``XDG_CONFIG_HOME`` belongs to the environment of the process: it is followed when
        ``home`` is the HOME of this process, and ignored for any other selected HOME (the
        default below that HOME is used). A relative value is ignored, as the XDG specification says.

        Declared limit: an absolute ``XDG_CONFIG_HOME`` OUTSIDE the HOME is followed here and then
        refused by the callers (``quickstart._safe_path``: every path we write stays below the
        selected HOME). Such a machine gets no service from this tool; the unit is to be written
        by hand, or ``XDG_CONFIG_HOME`` moved below the HOME.
        """
        base = self.environ.get("XDG_CONFIG_HOME") or ""
        own_home = Path(self.environ.get("HOME") or os.path.expanduser("~"))
        root = Path(base) if os.path.isabs(base) and home == own_home else home / ".config"
        return root / "systemd/user" / self.unit(label)

    def render(self, label, argv, env, log) -> str:
        """The unit, line for line: restart like launchd ``KeepAlive``, ``server.err`` in append-only mode."""
        if not argv or not os.path.isabs(argv[0]) or any(word == ";" for word in argv):
            raise ValueError("the service program must be an absolute path")
        # ``systemctl show`` prints the command as words joined by spaces and quotes environment
        # values that need it: a word or an attested value holding whitespace, a quote, a backslash
        # or a semicolon could not be compared exactly afterwards. Refused here, before any write.
        if any(not _PLAIN.fullmatch(word) for word in argv):
            raise ValueError("a service argument holds whitespace or a quoting character: the running command "
                             "could not be attested exactly (install under a path without spaces)")
        if any(not _PLAIN.fullmatch(str(env.get(key, "-"))) for key in ATTESTED_ENV):
            raise ValueError("the token file or store path holds whitespace or a quoting character: the running "
                             "service could not be attested exactly (use paths without spaces)")
        log = str(log)
        if not os.path.isabs(log) or _CONTROL.search(log) or log.endswith("\\"):
            raise ValueError("the service log must be a plain absolute path")
        lines = ["# Memory OS AI: written by `memory-os-admin quickstart --apply`; removed by",
                 "# `quickstart --undo <stamp> --with-service` or `memory-os-admin uninstall --apply`.",
                 "[Unit]",
                 f"Description=Memory OS AI server ({label})",
                 "",
                 "[Service]",
                 "Type=exec",
                 "ExecStart=" + " ".join(_unit_quote(word, exec_line=True) for word in argv)]
        lines += ["Environment=" + _unit_quote(f"{key}={value}") for key, value in env.items()]
        lines += ["Restart=always",
                  "RestartSec=10",
                  "StandardOutput=null",
                  "StandardError=append:" + log.replace("%", "%%"),
                  "",
                  "[Install]",
                  "WantedBy=default.target",
                  ""]
        return "\n".join(lines)

    def _service_section(self, path: Path) -> list:
        sections = parse_unit(path.read_bytes().decode("utf-8"))
        if sections is None or "Service" not in sections:
            raise ValueError("unexpected unit file")
        return sections["Service"]

    def read_environment(self, path: Path) -> dict:
        env = {}
        for key, value in self._service_section(path):
            if key != "Environment":
                continue
            words = _words(value)
            if len(words) != 1 or "=" not in words[0]:
                raise ValueError("unexpected Environment line")
            name, content = words[0].split("=", 1)
            env[name] = content
        return env

    def read_definition(self, path: Path) -> dict:
        section = self._service_section(path)
        starts = [value for key, value in section if key == "ExecStart"]
        logs = [value for key, value in section if key == "StandardError"]
        if len(starts) != 1:
            raise ValueError("unexpected ExecStart")
        argv = _words(starts[0], exec_line=True)
        if not argv:
            raise ValueError("unexpected ExecStart")
        log = None
        if len(logs) == 1 and logs[0].startswith("append:"):
            log = logs[0][len("append:"):].replace("%%", "%")
        return {'program': argv[0], 'argv': argv, 'env': self.read_environment(path), 'log': log}

    def state(self, path: Path, text: str) -> str:
        if not path.exists():
            return "absent"
        try:
            current = parse_unit(path.read_bytes().decode("utf-8"))
        except ValueError:
            return "invalid"
        if current is None:
            return "invalid"
        return "owned" if current == parse_unit(text) else "foreign-or-different"

    def plan_extras(self, label: str) -> dict:
        return {"manager": self.kind,
                "linger": {"command": 'loginctl enable-linger "$USER"', "effect": LINGER_EFFECT,
                           "run_by_this_tool": False}}

    def notices(self, label: str) -> list:
        return ['Optional, never run by this tool: `loginctl enable-linger "$USER"` ' + LINGER_EFFECT]

    # ---- commands ----
    def status_argv(self, label: str) -> list:
        return ["systemctl", "--user", "show", self.unit(label), "--no-pager",
                "--property=" + ",".join(SHOW_PROPERTIES)]

    def reload_argv(self) -> list:
        return ["systemctl", "--user", "daemon-reload"]

    def stop_argv(self, label: str) -> list:
        return ["systemctl", "--user", "disable", "--now", self.unit(label)]

    def start_argv(self, label: str, path=None) -> list:
        return ["systemctl", "--user", "enable", "--now", self.unit(label)]

    def start(self, service: dict, *, run):
        """``daemon-reload`` then ``enable --now``; the first non-zero return code (None: timeout or OS error).

        Declared TOCTOU limit (contract rev. 5, D6 bis): the caller validates the unit right
        before this call; a substitution during ``daemon-reload`` is followed by ``enable --now``.
        """
        code = job_run(self.reload_argv(), run=run, timeout=15)[0]
        if code != 0:
            return code
        return job_run(self.start_argv(service['label']), run=run, timeout=15)[0]

    def start_failed(self, stamp: str) -> str:
        return ('systemd enable failed; unit/journal retained; no automatic repair '
                f'(roll back with quickstart --undo {stamp} --with-service)')

    def _show(self, label: str, *, run, timeout=5):
        code, stdout, _ = job_run(self.status_argv(label), run=run, timeout=timeout)
        if code != 0:
            return None  # timeout, bus or permission failure: never evidence of absence
        shown = parse_show(stdout)
        return shown if shown is not None and valid_show(shown) else None

    @staticmethod
    def _idle(shown: dict) -> bool:
        return shown["ActiveState"] in {"inactive", "failed"} and shown["MainPID"] == "0"

    @staticmethod
    def _terminal_idle(shown: dict) -> bool:
        """True only for a coherent, terminal stopped state with no main process."""
        return shown["MainPID"] == "0" and (
            (shown["ActiveState"] == "inactive" and shown["SubState"] == "dead")
            or (shown["ActiveState"] == "failed" and shown["SubState"] == "failed")
        )

    @staticmethod
    def _operational(shown: dict) -> bool:
        """True only for a main process that exists now: active, running, ``MainPID`` > 0.

        ``activating`` / ``auto-restart``, ``reloading``, ``deactivating``, ``active`` / ``exited``
        or any state with ``MainPID=0`` is not a running service.
        """
        return (shown["ActiveState"] == "active" and shown["SubState"] == "running"
                and _NUMBER.fullmatch(shown["MainPID"]) is not None and int(shown["MainPID"]) > 0)

    def _attest(self, shown: dict, service: dict) -> str:
        """``ours`` | ``foreign`` | ``unknown`` for a unit systemd reports as ``loaded``.

        ``ours`` needs EVERY requested property, the unit file at our path with nothing layered
        on it (no drop-in, no pending reload), and the command and the two attested variables
        equal to the planned ones, word for word. A value of a shape this code does not read in
        full (a property missing, an ``ExecStart`` that is not exactly the documented form, an
        environment holding quoted or escaped words) is ``unknown``: a refusal, never "ours".
        """
        if not set(SHOW_PROPERTIES) <= set(shown):
            return "unknown"
        if shown["NeedDaemonReload"] not in {"yes", "no"}:
            return "unknown"
        if shown["FragmentPath"] != service['path'] or shown["DropInPaths"] or shown["NeedDaemonReload"] != "no":
            return "foreign"
        if not service.get('argv') or any(not isinstance(service.get('env', {}).get(key), str) for key in ATTESTED_ENV):
            return "foreign"  # a plan that names no command or no attested path proves nothing
        started = exec_shown(shown["ExecStart"])
        if not started or any(not _PLAIN.fullmatch(word) for word in service['argv']):
            return "unknown"  # not the documented shape, or a plan whose words could not be compared exactly
        if started[1] != service['argv'][0] or started[2].split(" ") != list(service['argv']):
            return "foreign"
        environment = {}
        for word in (shown["Environment"].split(" ") if shown["Environment"] else []):
            assignment = _ASSIGNMENT.fullmatch(word)
            if not assignment or assignment[1] in environment:
                return "unknown"
            environment[assignment[1]] = assignment[2]
        if any(environment.get(key) != service['env'][key] for key in ATTESTED_ENV):
            return "foreign"
        return "ours"

    @staticmethod
    def _attest_absence(shown: dict) -> str:
        """``absent`` | ``foreign-or-unattested`` | ``unknown`` for a unit systemd reports as ``not-found``.

        An explicit, complete schema: EVERY requested property is present (``ExecStart`` alone
        may be omitted, a unit without a command having none to print; when printed it is
        empty); numeric fields are numeric; no fragment, no drop-in, no environment, no pending
        reload, activation empty (or ``not-found``), not active, no process. A property missing
        or of another shape is ``unknown``; a well-formed output saying anything else (a
        process, an activity, a file somewhere) is another unit: not absent.
        """
        if not set(SHOW_PROPERTIES) - {"ExecStart"} <= set(shown):
            return 'unknown'
        if any(not re.fullmatch(r"-?[0-9]{1,10}", shown[key]) for key in ("NRestarts", "ExecMainCode", "ExecMainStatus")):
            return 'unknown'
        if shown["NeedDaemonReload"] not in {"yes", "no"} or not _WORD.fullmatch(shown["ActiveState"]) \
                or not _WORD.fullmatch(shown["SubState"]):
            return 'unknown'
        plain = (shown.get("ExecStart", "") == "" and shown["FragmentPath"] == "" and shown["DropInPaths"] == ""
                 and shown["Environment"] == "" and shown["UnitFileState"] in {"", "not-found"}
                 and shown["NeedDaemonReload"] == "no" and shown["ActiveState"] == "inactive"
                 and shown["SubState"] == "dead" and shown["MainPID"] == "0")
        return 'absent' if plain else 'foreign-or-unattested'

    def observe(self, service: dict, *, run, timeout: float = 5) -> dict:
        """``absent`` | ``loaded`` (attested ours) | ``foreign-or-unattested`` | ``unknown``.

        ``absent``: no main process, not active, and either systemd knows no unit of that name
        at all, or the unit is OUR definition (attested like a loaded one: path, no drop-in, no
        pending reload, command, variables) and is disabled: the state between the stop and
        the removal of the definition. ``loaded``: attested ours, and active or enabled; it also
        says ``active`` (a process or an activity, ``ActiveState``) and ``unit_file_state``
        (the activation, ``UnitFileState``), which are two different facts. A unit of the same
        name coming from anywhere else is never ours; an output this code does not read in
        full is ``unknown``.

        A ``loaded`` answer holds three different facts: ``active`` (the unit is not in a terminal
        stopped state: it runs, starts, stops or waits for a restart, so it must be stopped before
        a copy), ``running`` (a process of this unit exists NOW: ``ActiveState=active`` AND
        ``SubState=running`` AND ``MainPID`` > 0, the only thing that may be called a running
        service) and ``unit_file_state`` (the activation).
        """
        shown = self._show(service['label'], run=run, timeout=timeout)
        if shown is None:
            return {'state': 'unknown'}
        load = shown["LoadState"]
        if load == "not-found":
            return {'state': self._attest_absence(shown)}
        if load != "loaded":
            return {'state': 'foreign-or-unattested'}
        attested = self._attest(shown, service)
        if attested != "ours":
            return {'state': 'unknown' if attested == "unknown" else 'foreign-or-unattested'}
        enabled = shown["UnitFileState"]
        if not re.fullmatch(r"[a-z][a-z-]{0,31}", enabled):
            return {'state': 'unknown'}
        if self._idle(shown):
            # Values were validated by ``_show``; a non-terminal or mismatched sub-state (e.g.
            # ``auto-restart`` or ``stop-sigterm`` with ``ActiveState=inactive`` and ``MainPID=0``)
            # is contradictory and is NEVER reported as inactive or absent, whatever ``UnitFileState``.
            if not self._terminal_idle(shown):
                return {'state': 'unknown'}
            if enabled == "disabled":
                return {'state': 'absent'}

        def number(key):
            value = shown.get(key, "")
            return int(value) if re.fullmatch(r"-?[0-9]{1,10}", value) else None
        exited = (number("ExecMainCode") or 0) != 0  # a main process has already exited at least once
        restarts, pid = number("NRestarts") or 0, number("MainPID")
        sub_state = shown.get("SubState", "")
        return {'state': 'loaded', 'runs': restarts + (1 if exited else 0),
                'last_exit_code': number("ExecMainStatus") if exited else None, 'pid': pid or None,
                'process_state': sub_state if _WORD.fullmatch(sub_state) else None,
                'active': not self._terminal_idle(shown), 'running': self._operational(shown),
                'unit_file_state': enabled}

    def pause_argv(self, label: str) -> list:
        """Stop the running unit WITHOUT touching its activation (backup: the state is put back as it was)."""
        return ["systemctl", "--user", "stop", self.unit(label)]

    def resume_argv(self, label: str) -> list:
        return ["systemctl", "--user", "start", self.unit(label)]

    def stop(self, service: dict, *, run) -> str:
        """One ``systemctl --user disable --now``; 'ok', 'failed' or 'timeout'."""
        return _stop_run(self.stop_argv(service['label']), run=run)

    def after_remove(self, service: dict, *, run) -> dict:
        """The definition has left its directory: systemd is told once; a failure changes nothing else."""
        return {"daemon_reload": "ok" if job_run(self.reload_argv(), run=run, timeout=15)[0] == 0 else "failed"}

    def manual_stop(self, label: str) -> str:
        return f"systemctl --user disable --now {self.unit(label)}"

    def manual_start(self, label: str) -> str:
        return "systemctl --user enable --now <label>.service"

    def status(self, label: str, *, run, timeout=None) -> str:
        """Unattested view for doctor and for the wait of a backup: 'running', 'stopped' or 'unknown'.

        Same strict reading as ``observe`` (``valid_show``: complete schema, closed values).
        ``stopped`` is said only for a complete, valid output showing no process and no
        activity: the plain "no such unit" case (``_attest_absence``) or a ``loaded`` unit that
        is inactive. Any other load state, and anything that cannot be read in full, is
        ``unknown``, which is never "stopped".
        """
        return self.status_detail(label, run=run, timeout=timeout)[0]

    def status_detail(self, label: str, *, run, timeout=None) -> tuple:
        """``(status, word)`` where the word says WHICH stopped or running it is, for doctor.

        ``stopped`` covers two different facts that are never said with the same word: systemd
        knows no unit of that name (``not loaded``), or the unit is loaded and inactive
        (``loaded but inactive``, or ``loaded but failed``). ``running`` (not stopped) is said
        ``loaded`` only with a main process (active, running, ``MainPID`` > 0); a unit that is
        starting, stopping or waiting for a restart is ``loaded but not running``: doctor never
        reports it as a healthy service. ``unknown`` has no word.
        """
        shown = self._show(label, run=run, timeout=5 if timeout is None else timeout)
        if shown is None:
            return "unknown", None
        if shown["LoadState"] == "not-found":
            absence = self._attest_absence(shown)
            if absence == "absent":
                return "stopped", "not loaded"
            return ("running", "active") if not self._idle(shown) else ("unknown", None)
        if shown["LoadState"] != "loaded":
            return "unknown", None
        if self._terminal_idle(shown):
            return "stopped", "loaded but " + shown["ActiveState"]
        if self._idle(shown):
            return "unknown", None
        return "running", "loaded" if self._operational(shown) else "loaded but not running"


MANAGERS = {Launchd.kind: Launchd, SystemdUser.kind: SystemdUser}


def select(detected: dict):
    """The manager of a detected cell report, or None (no service is installed or driven there)."""
    from . import cell
    manager = detected.get("service_manager") or {}
    if manager.get("kind") == Launchd.kind:
        return Launchd()
    if (manager.get("kind") == SystemdUser.kind and manager.get("available")
            and cell.linux_platform_retained(detected)):
        return SystemdUser()
    return None


def unavailable(detected: dict) -> str:
    """Actionable refusal for a cell without a usable service manager; nothing is installed."""
    from . import cell
    manager = detected.get("service_manager") or {}
    label = detected.get("label") or "this platform"
    if manager.get("kind") == SystemdUser.kind and cell.linux_platform_retained(detected):
        evidence = manager.get("evidence") or {}
        missing = [name for name, present in (("systemctl on PATH", evidence.get("systemctl")),
                                              ("a system booted with systemd", evidence.get("system_booted")),
                                              ("a running systemd user manager", evidence.get("user_manager_dir")))
                   if not present]
        return ("service_manager_unavailable: systemd --user is not available (missing: " + ", ".join(missing)
                + "); no service is installed. Open a real login session of this user (console or ssh, so that "
                "XDG_RUNTIME_DIR and the user manager exist), check `systemctl --user status`, then re-preview; "
                "a container or a session without systemd is not supported")
    return (f"service_manager_unavailable: no supported service manager on {label} (launchd on macOS, "
            "systemd --user on the retained Linux cell); no service is installed")


def host_manager():
    """The manager of the machine running this command (read-only detection, no process)."""
    from . import cell
    return select(cell.detect())


def manager_of(service: dict):
    """The manager a service plan or a journal names (``manager`` key; absent: launchd, the 4.1.0 journals)."""
    kind = service.get("manager", Launchd.kind) if isinstance(service, dict) else None
    found = MANAGERS.get(kind) if isinstance(kind, str) else None
    return found() if found else None


def installed(manager, path: Path) -> dict:
    """What the definition on disk installs: program, arguments, backend and profile (parse errors propagate)."""
    definition = manager.read_definition(path)
    env = definition['env']
    return {'program': definition['program'], 'argv': definition['argv'],
            'backend': env.get('MEMORY_V4_STORE_BACKEND'),
            'profile': env.get('MEMORY_V4_RETRIEVAL_PROFILE', 'semantic'), 'log': definition['log']}
