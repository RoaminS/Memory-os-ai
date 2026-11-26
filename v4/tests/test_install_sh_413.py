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

    def __init__(self, tmp_path: Path, *, bootstrap=MARKER_BOOTSTRAP, uv=True, uname=None):
        self.root = tmp_path
        self.machine = Machine(tmp_path)
        self.home = self.machine.home
        self.release = tmp_path / "release"
        self.release.mkdir()
        self.tmpdir = tmp_path / "tmp"
        self.tmpdir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        python = self.bin / f"python{MINOR[0]}.{MINOR[1]}"
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
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

    def script(self) -> str:
        assert len(BASE_LINE.findall(TEXT)) == 1
        return BASE_LINE.sub(f'  RELEASE_BASE="file://{self.release}"', TEXT)

    def run(self, *args, shell="/bin/sh", cwd=None):
        tools = f"{self.bin}:{self.uv_dir}" if self.uv_dir else str(self.bin)
        environ = {"HOME": str(self.home), "PATH": f"{tools}:/usr/bin:/bin", "TMPDIR": str(self.tmpdir),
                   "LANG": "en_US.UTF-8"}
        return subprocess.run([shell, "-s", "--", *args], input=self.script(), capture_output=True, text=True,
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
    code = "\n".join(line for line in TEXT.splitlines() if not line.lstrip().startswith("#"))
    assert "latest" not in code and ":-" not in code.replace("${TMPDIR:-/tmp}", "")  # no default/override variable
    assert "MEMORY_OS" not in code and "INSTALL_BASE" not in code
    assert not re.search(r"\|\s*(ba|z|da)?sh\b", code)          # never pipes anything into a shell
    assert "astral.sh/uv/install" not in TEXT and "pip install" not in code  # uv is never downloaded
    assert '--interactive --uv "$UV" "$@" </dev/null' in code
    assert "--proto-redir '=https'" in code and 'main "$@"' in code.splitlines()[-1]


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
        assert "No terminal (/dev/tty)" in done.stdout and "nothing written" in done.stdout
    assert tree(r.home) == home_before and list(r.tmpdir.iterdir()) == []
    assert {" ".join(call["argv"][:2]) for call in r.machine.calls()} == {"--version"}
