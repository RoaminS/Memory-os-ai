"""4.1.3 ``scripts/install.sh``: the one-command install (``curl -fsSL .../install.sh | sh``).

Offline: the script is run by ``sh`` (and ``dash`` when present) with the script on stdin, exactly as under
``curl | sh``, against a local fixture "release" reached through ``file://`` URLs. The shipped script has NO
override of its download location: each test runs a copy whose single ``RELEASE_BASE=`` line is rewritten.
``start_new_session``: the run has no controlling terminal, like a CI job.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from setup_fixture_410 import tree
from test_bootstrap_411 import MINOR, Machine, bs

REPO = Path(__file__).resolve().parents[2]
INSTALL = REPO / "scripts" / "install.sh"
TEXT = INSTALL.read_text()
SHELLS = [shell for shell in ("/bin/sh", "/bin/dash") if os.path.exists(shell)]
BASE_LINE = re.compile(r'^  RELEASE_BASE="[^"\n]*"$', re.M)
PROTO_LINE = re.compile(r'^  CURL_PROTO="[^"\n]*"$', re.M)
README = (REPO / "README.md").read_text()
ONE_LINER = ("bash -o pipefail -c \"curl -q -fsSL --proto '=https' --proto-redir '=https' "
             "https://github.com/RoaminS/Memory-os-ai/releases/download/v4.1.3/install.sh | sh\"")
pytestmark = pytest.mark.skipif(MINOR not in ((3, 11), (3, 12), (3, 13)), reason="needs a Python of a retained cell")

MARKER_BOOTSTRAP = '''import json, os, sys
target = os.path.join(os.path.dirname(os.path.abspath(os.environ["HOME"])), "bootstrap-ran.json")
with open(target, "w") as handle:
    json.dump({"argv": sys.argv[1:], "executable": sys.executable}, handle)
'''


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Release:
    """A HOME, a PATH of tools, a fixture release directory and a private TMPDIR, all under ``tmp_path``."""

    def __init__(self, tmp_path: Path, *, bootstrap=MARKER_BOOTSTRAP, uv=True, uname=None, cell=None, python=None):
        self.root = tmp_path
        self.machine = Machine(tmp_path)
        self.home = self.machine.home
        self.release = tmp_path / "release"
        self.release.mkdir()
        self.tmpdir = tmp_path / "tmp"
        self.tmpdir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        python_target = python
        python = self.bin / f"python{MINOR[0]}.{MINOR[1]}"
        answer = f'case "$*" in *memory-os-cell:*) echo memory-os-cell:{cell}; exit 0 ;; esac\n' if cell else ""
        python.write_text(f'#!/bin/sh\n{answer}exec "{python_target or sys.executable}" "$@"\n')
        python.chmod(0o755)
        self.uv_dir = self.machine.tools if uv else None  # the fake uv reads its configuration next to itself
        if uname:
            shim = self.bin / "uname"
            shim.write_text(f"#!/bin/sh\necho {uname}\n")
            shim.chmod(0o755)
        self.publish(bootstrap)

    def publish(self, bootstrap, *, sums=None):
        data = bootstrap.encode() if isinstance(bootstrap, str) else bootstrap
        (self.release / "memory_os_bootstrap.py").write_bytes(data)
        (self.release / "SHA256SUMS").write_text(
            f"{sha(b'other')}  memory_os_ai-4.1.3-py3-none-any.whl\n{sha(data)}  memory_os_bootstrap.py\n"
            if sums is None else sums)

    def script(self, *, https_only=False) -> str:
        """The shipped script with its two test lines rewritten: file:// base, and file allowed unless https_only."""
        assert len(BASE_LINE.findall(TEXT)) == 1 and len(PROTO_LINE.findall(TEXT)) == 1
        text = BASE_LINE.sub(f'  RELEASE_BASE="file://{self.release}"', TEXT)
        return text if https_only else PROTO_LINE.sub('  CURL_PROTO="=https,file"', text)

    def run(self, *args, shell="/bin/sh", cwd=None, script=None, tmpdir=None):
        tools = f"{self.bin}:{self.uv_dir}" if self.uv_dir else str(self.bin)
        environ = {"HOME": str(self.home), "PATH": f"{tools}:/usr/bin:/bin",
                   "TMPDIR": str(self.tmpdir if tmpdir is None else tmpdir), "LANG": "en_US.UTF-8"}
        return subprocess.run([shell, "-s", "--", *args], input=self.script() if script is None else script,
                              capture_output=True, text=True,
                              env=environ, cwd=str(cwd or self.home), timeout=180, start_new_session=True)

    def ran(self):
        marker = self.root / "bootstrap-ran.json"
        return json.loads(marker.read_text()) if marker.exists() else None


# ---------------------------------------------------------------- the shipped script

def test_the_shipped_script_is_posix_pinned_to_its_release_and_has_no_override():
    for shell in SHELLS:
        assert subprocess.run([shell, "-n", str(INSTALL)], capture_output=True).returncode == 0, shell
    assert TEXT.startswith("#!/bin/sh\n") and os.access(INSTALL, os.X_OK)
    tags = re.findall(r'^  RELEASE_TAG="(v[0-9]+\.[0-9]+\.[0-9]+)"$', TEXT, re.M)
    assert tags == ["v4.1.3"] and TEXT.count("RELEASE_TAG=") == 1
    assert BASE_LINE.findall(TEXT) == [
        '  RELEASE_BASE="https://github.com/RoaminS/Memory-os-ai/releases/download/$RELEASE_TAG"']
    assert PROTO_LINE.findall(TEXT) == ['  CURL_PROTO="=https"']
    code = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))
    assert "latest" not in code and ":-" not in code.replace("${TMPDIR:-/tmp}", "")  # no default/override variable
    assert "MEMORY_OS" not in code and "INSTALL_BASE" not in code
    assert not re.search(r"\|\s*(ba|z|da)?sh\b", code)          # never pipes anything into a shell
    assert "astral.sh/uv/install" not in TEXT and "pip install" not in code  # uv is never downloaded
    assert '"$PY" -I -S -B "$WORK/memory_os_bootstrap.py" --interactive --uv "$UV" "$@" </dev/null' in code
    assert '"$PY" -I -S -B -c "$CELL_PROBE"' in code       # no site, no .pth, no sitecustomize, no bytecode
    curls = [line.strip() for line in code.splitlines() if re.search(r"\bcurl -", line)]
    assert curls == ['curl -q -fsSL --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" -o "$WORK/$NAME" '
                     '"$RELEASE_BASE/$NAME" \\']                       # -q first: no curlrc
    assert code.splitlines()[-1] == 'main "$@" "--memory-os-install-script-complete"'
    assert TEXT.splitlines()[3].startswith("trap ") and "incomplete" in TEXT.splitlines()[3]
    header = TEXT[:TEXT.index("END_MARKER=")]
    assert "nothing is written in $HOME, in the store or in any client configuration, and no" in header
    assert "The only writes before it are the two verified files" in header and "removed at exit" in header
    # the README one-liner: curlrc ignored, HTTPS only for the request and its redirects, failure propagated
    assert README.count(ONE_LINER) == 1
    assert ONE_LINER.replace("https://github.com/RoaminS/Memory-os-ai/releases/download/v4.1.3",
                             "<release-asset-url>") in header


# ---------------------------------------------------------------- refusals: nothing is run

@pytest.mark.parametrize("shell", SHELLS)
def test_a_checksum_mismatch_refuses_and_runs_nothing(tmp_path, shell):
    r = Release(tmp_path)
    r.publish(MARKER_BOOTSTRAP, sums=f"{sha(b'something else')}  memory_os_bootstrap.py\n")
    done = r.run(shell=shell)
    assert done.returncode == 1 and "checksum mismatch" in done.stderr and "nothing was run" in done.stderr
    assert r.ran() is None and list(r.tmpdir.iterdir()) == []


@pytest.mark.parametrize("sums,words", [
    ("", "has 0 lines"),
    (f"{sha(b'x')}  memory_os_bootstrap.py\n{sha(b'y')}  memory_os_bootstrap.py\n", "has 2 lines"),
    ("NOTAHEX  memory_os_bootstrap.py\n", "no valid SHA-256"),
    (f"{sha(b'x')[:63]}  memory_os_bootstrap.py\n", "no valid SHA-256"),
    (f"{sha(b'x')}  other_bootstrap.py\n", "has 0 lines"),
])
def test_a_missing_repeated_or_malformed_checksum_line_refuses(tmp_path, sums, words):
    r = Release(tmp_path)
    r.publish(MARKER_BOOTSTRAP, sums=sums)
    done = r.run()
    assert done.returncode == 1 and words in done.stderr and r.ran() is None and list(r.tmpdir.iterdir()) == []


@pytest.mark.parametrize("missing", ["memory_os_bootstrap.py", "SHA256SUMS"])
def test_a_download_failure_refuses_even_with_matching_stale_files_at_hand(tmp_path, missing):
    r = Release(tmp_path)
    stale = tmp_path / "stale"
    stale.mkdir()
    for name in ("memory_os_bootstrap.py", "SHA256SUMS"):
        shutil.copyfile(r.release / name, stale / name)   # an earlier attempt, matching each other
    (r.release / missing).unlink()
    done = r.run(cwd=stale)
    assert done.returncode == 1 and f"download of {missing}" in done.stderr and "nothing was run" in done.stderr
    assert r.ran() is None and list(r.tmpdir.iterdir()) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_without_uv_it_explains_and_exits_before_any_download(tmp_path, shell):
    if shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("a uv is installed in /usr/bin or /bin")
    r = Release(tmp_path, uv=False)
    before = tree(tmp_path)
    done = r.run(shell=shell)
    assert done.returncode == 1 and "uv >= 0.13.9 is required" in done.stderr
    assert "never downloads it" in done.stderr and "docs.astral.sh/uv" in done.stderr
    assert "Nothing was downloaded or written" in done.stderr
    assert r.ran() is None and tree(tmp_path) == before


# ---------------------------------------------------------------- the verified bootstrap and its arguments

def test_the_verified_bootstrap_runs_interactive_with_the_explicit_uv(tmp_path):
    r = Release(tmp_path, uname="Darwin")
    done = r.run()
    assert done.returncode == 0, done.stderr
    seen = r.ran()
    assert seen["argv"] == ["--interactive", "--uv", str(r.machine.uv)]
    assert "verified: SHA-256 " + sha((r.release / "memory_os_bootstrap.py").read_bytes()) in done.stdout
    assert list(r.tmpdir.iterdir()) == []  # the private download directory is removed at exit


def test_the_shipped_protocols_refuse_anything_but_https(tmp_path):
    r = Release(tmp_path)
    done = r.run(script=r.script(https_only=True))          # file:// base, shipped CURL_PROTO="=https"
    assert done.returncode == 1 and "download of memory_os_bootstrap.py" in done.stderr and r.ran() is None
    assert list(r.tmpdir.iterdir()) == []


@pytest.mark.parametrize("shell", SHELLS)
def test_a_truncated_script_never_runs_and_never_exits_zero(tmp_path, shell):
    """From the end of the guard line on, every cut (every 97 bytes, and each byte of the last line) fails."""
    r = Release(tmp_path)
    script = r.script()
    guard_end = script.index("\n", script.index("\ntrap ") + 1) + 1
    tail = script.rindex("\nmain ") + 1
    failures = []
    for args in ((), ("--clients", "codex")):               # with user options, a cut after "$@" must fail too
        for cut in sorted(set(range(guard_end, len(script) - 1, 97)) | set(range(tail, len(script) - 1))):
            done = r.run(*args, shell=shell, script=script[:cut])
            if done.returncode == 0 or r.ran() is not None:
                failures.append((args, cut))
    assert failures == []
    assert list(r.tmpdir.iterdir()) == []
    done = r.run("--clients", "codex", shell=shell, script=script[:-1])  # only the final newline missing: complete
    assert done.returncode == 0 and r.ran()["argv"][-2:] == ["--clients", "codex"]


def test_the_readme_one_liner_fails_when_the_download_fails(tmp_path):
    bash = shutil.which("bash", path="/bin:/usr/bin")
    if not bash:
        pytest.skip("no bash")
    inner = ONE_LINER[len('bash -o pipefail -c "'):-1].replace(
        "https://github.com/RoaminS/Memory-os-ai/releases/download/v4.1.3/install.sh", f"file://{tmp_path}/absent.sh")
    environ = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    for protocols, label in (("'=https'", "shipped: file refused"), ("'=https,file'", "file allowed, absent")):
        line = inner.replace("'=https'", protocols)
        done = subprocess.run([bash, "-o", "pipefail", "-c", line], capture_output=True, text=True, env=environ,
                              timeout=60, start_new_session=True)
        assert done.returncode != 0, label
        bare = subprocess.run([bash, "-c", line], capture_output=True, text=True, env=environ, timeout=60,
                              start_new_session=True)
        assert bare.returncode == 0, label                  # why pipefail: a bare "curl | sh" reports success


def test_on_macos_a_python_outside_the_cells_gets_the_standalone_cpython_3_13(tmp_path):
    r = Release(tmp_path, uname="Darwin", cell="other")
    done = r.run()
    assert done.returncode == 0, done.stderr
    assert r.ran()["argv"] == ["--interactive", "--uv", str(r.machine.uv), "--cpython", "3.13"]
    assert "the standalone CPython 3.13" in done.stdout and "downloaded only after y" in done.stdout
    r = Release(tmp_path / "retained", uname="Darwin", cell="retained")
    assert r.run().returncode == 0 and r.ran()["argv"] == ["--interactive", "--uv", str(r.machine.uv)]
    r = Release(tmp_path / "explicit", uname="Darwin", cell="other")
    assert r.run("--python", "/opt/py/bin/python3.12").returncode == 0
    assert r.ran()["argv"][3:] == ["--python", "/opt/py/bin/python3.12"]
    assert r.run("--cpython", "3.12").returncode == 0 and r.ran()["argv"][3:] == ["--cpython", "3.12"]


def test_on_macos_the_cell_probe_reads_the_real_interpreter(tmp_path):
    r = Release(tmp_path, uname="Darwin")                    # the test interpreter: a Python of a retained cell
    assert r.run().returncode == 0 and r.ran()["argv"] == ["--interactive", "--uv", str(r.machine.uv)]


def test_on_linux_the_standalone_cpython_route_is_chosen_unless_one_is_given(tmp_path):
    r = Release(tmp_path, uname="Linux")
    assert r.run().returncode == 0 and r.ran()["argv"][-2:] == ["--cpython", "3.11"]
    assert r.run("--python", "/opt/py/bin/python3.11").returncode == 0
    assert r.ran()["argv"] == ["--interactive", "--uv", str(r.machine.uv), "--python", "/opt/py/bin/python3.11"]
    assert r.run("--cpython", "3.11", "--clients", "codex").returncode == 0
    assert r.ran()["argv"][3:] == ["--cpython", "3.11", "--clients", "codex"]


@pytest.mark.parametrize("shell", SHELLS)
def test_without_a_terminal_the_real_bootstrap_previews_and_writes_nothing(tmp_path, shell):
    probe = Machine(tmp_path / "pin")  # only to build a pinned test release of the real bootstrap
    filled = bs.fill_pin((REPO / "scripts" / "memory_os_bootstrap.py").read_text(), probe.pin)
    r = Release(tmp_path / "run", bootstrap=filled)
    (r.home / ".cursor").mkdir()
    (r.home / ".cursor" / "mcp.json").write_text("{}\n")
    home_before = tree(r.home)
    done = r.run(shell=shell)
    on_cell = sys.platform == "darwin" and os.uname().machine == "arm64"
    assert done.returncode == (3 if on_cell else 1), done.stdout + done.stderr
    assert "ONE-COMMAND INSTALL" in done.stdout and str(r.home / ".cursor" / "mcp.json") in done.stdout
    if on_cell:
        assert "No terminal (/dev/tty)" in done.stdout and "the bootstrap wrote nothing" in done.stdout
    assert tree(r.home) == home_before and list(r.tmpdir.iterdir()) == []
    assert {" ".join(call["argv"][:2]) for call in r.machine.calls()} == {"--version"}


# ---------------------------------------------------------------- round 3: TMPDIR and the interpreter flags

def _temporary(done) -> Path:
    line = next(line for line in done.stdout.splitlines() if line.startswith("Temporary directory (removed at exit): "))
    return Path(line.split(": ", 1)[1])


@pytest.mark.parametrize("kind", ["inside", "symlink", "missing"])
def test_a_tmpdir_in_home_is_never_used_before_the_answer(tmp_path, kind):
    r = Release(tmp_path)
    inside = r.home / "tmp"
    inside.mkdir()
    tmpdir = {"inside": inside, "missing": tmp_path / "absent-tmp"}.get(kind)
    if kind == "symlink":
        tmpdir = tmp_path / "linked-tmp"
        tmpdir.symlink_to(inside, target_is_directory=True)
    before = tree(r.home)
    done = r.run(tmpdir=tmpdir)
    assert done.returncode == 0, done.stderr
    assert "the temporary directory is created under" in done.stdout
    used = _temporary(done)
    home = Path(os.path.realpath(r.home))
    assert home not in used.parents and not used.exists()  # outside HOME, removed at exit
    assert tree(r.home) == before and list(inside.iterdir()) == []
    assert r.ran() is not None


def test_an_ordinary_tmpdir_is_used_as_is(tmp_path):
    r = Release(tmp_path)
    done = r.run()
    assert done.returncode == 0 and "created under" not in done.stdout
    assert _temporary(done).parent == Path(os.path.realpath(r.tmpdir)) and list(r.tmpdir.iterdir()) == []


def test_the_interpreter_runs_without_site_pth_sitecustomize_or_bytecode(tmp_path):
    """A Python installed under HOME, whose site-packages hold a .pth and a sitecustomize that write a witness:
    with -I -S -B neither runs, and nothing at all is written in HOME before the answer."""
    home_python = tmp_path / "home-python"
    created = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(home_python)], capture_output=True,
                             text=True, timeout=120)
    assert created.returncode == 0, created.stderr
    witness = tmp_path / "site-ran"
    site = next((home_python / "lib").glob("python*/site-packages"))
    (site / "zz_witness.pth").write_text(f"import pathlib; pathlib.Path({str(witness)!r}).write_text('pth')\n")
    (site / "sitecustomize.py").write_text(f"import pathlib; pathlib.Path({str(witness)!r}).write_text('site')\n")
    r = Release(tmp_path / "run", uname="Darwin", python=home_python / "bin" / "python")
    shutil.move(str(home_python), str(r.home / "python"))   # the interpreter now lives under HOME
    (r.bin / f"python{MINOR[0]}.{MINOR[1]}").write_text(f'#!/bin/sh\nexec "{r.home}/python/bin/python" "$@"\n')
    before = tree(r.home)
    done = r.run()
    assert done.returncode == 0, done.stderr
    assert not witness.exists() and r.ran() is not None
    assert tree(r.home) == before
    assert not [path for path in r.home.rglob("__pycache__")]
