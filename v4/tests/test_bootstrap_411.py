"""4.1.1 lot L3 (contract §4): the bootstrap, its preview, the durable venv driven by uv.

No network, and the real ``uv`` is never executed: a fake ``uv`` (a script in a temporary
directory) records its arguments and its environment and simulates versions and failures. The
bootstrap is a standalone script outside the importable package: it is loaded here by path.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from memory_os_v4.ops import cell, quickstart as qs, sqlite_runtime
from memory_os_v4.sqlite_store import MIN_SQLITE
from setup_fixture_410 import install, prepare, setup_context, tree
from test_cell_411 import NOT_RETAINED, RETAINED, as_cell, lock_everything

REPO = Path(__file__).resolve().parents[2]
REAL_READY = qs._ready  # captured at import, before any fixture replaces it
SCRIPT = REPO / "scripts" / "memory_os_bootstrap.py"


def _load():
    spec = importlib.util.spec_from_file_location("memory_os_bootstrap", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


bs = _load()
MINOR = sys.version_info[:2]
pytestmark = pytest.mark.skipif(MINOR not in ((3, 11), (3, 12), (3, 13)), reason="needs a Python of a retained cell")
CELL = f"macos-arm64-py{MINOR[0]}{MINOR[1]}"
MAC = {"os": "darwin", "sys_platform": "darwin", "arch": "arm64", "machine": "arm64", "distribution": None,
       "wsl": False, "service_manager": {"kind": "launchd", "available": True, "attestation": "presence only"}}
UBUNTU = {"os": "linux", "sys_platform": "linux", "arch": "arm64", "machine": "aarch64",
          "distribution": {"id": "ubuntu", "version_id": "24.04", "conflicts": []}, "wsl": False,
          "service_manager": {"kind": "systemd-user", "available": True, "attestation": "presence only"}}
VERSION = "4.1.1"

# ---------------------------------------------------------------- fakes

FAKE_CLI = '''import json, os, sys
if __name__ == "__main__":
    blocked = %(blocked)r
    print(json.dumps({"ok": not blocked, "stage": "preview", "writes": "none (preview)",
                      "blockers": ["runtime_unqualified: synthetic"] if blocked else [],
                      "blocker_codes": ["runtime_unqualified"] if blocked else [],
                      "argv": sys.argv[1:], "prefix": sys.prefix, "cell": {"cell_id": %(cell)r},
                      "downloads_required_bytes": sorted(os.environ),
                      "sqlite_attestation": {"route": "a", "ok": True}}))
    sys.exit(1 if blocked else 0)
'''


def make_wheel(directory: Path, *, blocked=False) -> Path:
    """A tiny memory-os-ai wheel whose ``ops/cli.py`` prints a quickstart-like preview."""
    path = directory / f"memory_os_ai-{VERSION}-py3-none-any.whl"
    info = f"memory_os_ai-{VERSION}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("memory_os_v4/__init__.py", f'__version__ = "{VERSION}"\n')
        archive.writestr("memory_os_v4/ops/__init__.py", "")
        archive.writestr("memory_os_v4/ops/cli.py", FAKE_CLI % {"blocked": blocked, "cell": CELL})
        archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: memory-os-ai\nVersion: {VERSION}\n")
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")
        archive.writestr(f"memory_os_ai-{VERSION}.data/data/share/doc/memory-os-ai/NOTE.md", "doc\n")
    return path


def make_pin(wheel: Path, *, cells=(CELL,), profiles=("lexical",), state="test") -> dict:
    def lock(cell_id, profile):
        tag = "cp3" + cell_id[-2:]
        body = {"constraints": ["tiktoken==0.12.0", "regex==2026.1.15"],
                "artifacts": [{"name": name, "version": version, "filename": f"{name}-{version}-{tag}-{tag}-any.whl",
                               "url": f"https://files.pythonhosted.org/packages/{name}-{version}-{tag}.whl",
                               "sha256": hashlib.sha256(f"{name}{cell_id}{profile}".encode()).hexdigest(),
                               "size": 1000 + index}
                              for index, (name, version) in enumerate((("tiktoken", "0.12.0"), ("regex", "2026.1.15")))]}
        body["lock_sha256"] = bs.lock_fingerprint(cell_id, profile, body)
        return body
    return {"schema_version": 1, "state": state, "package": "memory-os-ai", "version": VERSION,
            "wheel": {"filename": wheel.name, "sha256": bs.sha256_file(wheel), "size": wheel.stat().st_size,
                      "content_sha256": bs.wheel_content_fingerprint(wheel),
                      "source": "https://files.pythonhosted.org/packages/" + wheel.name},
            "cells": {cell_id: {profile: lock(cell_id, profile) for profile in profiles
                                if profile != "semantic" or cell_id in bs.SEMANTIC_CELLS} for cell_id in cells}}


FAKE_UV = r'''#!%(python)s
"""Fake uv: records argv and environment, simulates versions and failures. Never the real uv."""
import hashlib, json, os, sys, zipfile
here = os.path.dirname(os.path.abspath(__file__))
config = json.load(open(os.path.join(here, "fakeuv.json")))
argv = sys.argv[1:]
with open(config["log"], "a") as log:
    log.write(json.dumps({"argv": argv, "env": dict(os.environ), "name": os.path.basename(sys.argv[0])}) + "\n")
if argv == ["--version"]:
    print(config["version_line"])
    sys.exit(0)
verb = " ".join(argv[:2])
if verb in config["fail"]:
    print("error: simulated failure", file=sys.stderr)
    sys.exit(2)
if verb == "python install":
    open(os.path.join(here, "managed-" + argv[-1]), "w").close()
elif argv[0] == "venv":
    request = argv[argv.index("--python") + 1]
    if "--managed-python" in argv and not os.path.exists(os.path.join(here, "managed-" + request)):
        print("error: no interpreter found for " + request + " (downloads disabled)", file=sys.stderr)
        sys.exit(2)
    venv, base = argv[-1], config["python"]
    site = os.path.join(venv, "lib", "python%%d.%%d" %% sys.version_info[:2], "site-packages")
    os.makedirs(site)
    os.makedirs(os.path.join(venv, "bin"))
    os.symlink(base, os.path.join(venv, "bin", "python"))
    with open(os.path.join(venv, "pyvenv.cfg"), "w") as cfg:
        cfg.write("home = " + os.path.dirname(base) + "\ninclude-system-site-packages = false\n")
elif verb == "pip install":
    python = argv[argv.index("--python") + 1]
    venv = os.path.dirname(os.path.dirname(python))
    site = os.path.join(venv, "lib", "python%%d.%%d" %% sys.version_info[:2], "site-packages")
    lines = open(argv[argv.index("-r") + 1]).read().splitlines()
    with open(os.path.join(here, "requirements-seen.txt"), "w") as seen:
        seen.write("\n".join(lines))
    record = []
    with zipfile.ZipFile(config["wheel"]) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            if ".data/" in name:
                target = os.path.join(venv, name.split(".data/data/", 1)[1])
                listed = os.path.relpath(target, site)
            else:
                target, listed = os.path.join(site, name), name
            if name.endswith("RECORD"):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if config.get("tamper") and name.endswith("ops/__init__.py"):
                data = b"# changed after download\n"
            with open(target, "wb") as handle:
                handle.write(data)
            record.append(listed + ",sha256=x," + str(len(data)))
        info = [n for n in archive.namelist() if n.endswith("RECORD")][0].rsplit("/", 1)[0]
    for extra in ("INSTALLER", "REQUESTED", "uv_cache.json"):
        open(os.path.join(site, info, extra), "w").write("uv\n")
        record.append(info + "/" + extra + ",,")
    record.append(info + "/RECORD,,")
    open(os.path.join(site, info, "RECORD"), "w").write("\n".join(record) + "\n")
else:
    print("error: unexpected fake uv command", file=sys.stderr)
    sys.exit(3)
'''


class Machine:
    """A temporary HOME, a fake uv outside it, a test wheel and the pin that names it."""

    def __init__(self, tmp_path: Path, *, home="home", tools="tools", version="0.13.9", blocked=False):
        self.root = tmp_path
        self.home = tmp_path / home
        self.home.mkdir(parents=True)
        self.tools = tmp_path / tools
        self.tools.mkdir(parents=True)
        self.log = tmp_path / "uv-calls.jsonl"
        self.wheelhouse = tmp_path / "wheelhouse"
        self.wheelhouse.mkdir()
        self.wheel = make_wheel(self.wheelhouse, blocked=blocked)
        self.pin = make_pin(self.wheel, cells=tuple(bs.CELLS), profiles=bs.PROFILES)
        self.uv = self.tools / "uv"
        self.uv.write_text(FAKE_UV % {"python": sys.executable})
        self.uv.chmod(0o755)
        self.config = {"log": str(self.log), "version_line": f"uv {version} (6b2d39dcc 2026-11-17)", "fail": [],
                       "wheel": str(self.wheel),
                       "python": os.path.realpath(getattr(sys, "_base_executable", None) or sys.executable)}
        self.configure()
        self.environ = {"HOME": str(self.home), "PATH": os.environ["PATH"], "PYTHONPATH": "/nowhere",
                        "UV_PYTHON_DOWNLOADS": "automatic", "VIRTUAL_ENV": "/elsewhere",
                        "UV_CACHE_DIR": "/elsewhere/cache", "MEMORY_V4_URL": "http://127.0.0.1:8765/mcp/",
                        "MEMORY_V4_DIR": "/elsewhere/store", "MEMORY_V4_TOKEN_FILE": "/elsewhere/token",
                        "XDG_CONFIG_HOME": "/elsewhere/config", "HF_HUB_CACHE": "/elsewhere/hf"}
        self.host = dict(MAC)
        self.rewrite = None  # (path suffix, function(dict) -> dict): what a probed interpreter answers

    def configure(self, **changes):
        self.config.update(changes)
        (self.tools / "fakeuv.json").write_text(json.dumps(self.config))

    def run(self, argv, **kwargs):
        result = subprocess.run(argv, **kwargs)
        if self.rewrite and bs.PROBE_SCRIPT in argv and argv[0].endswith(self.rewrite[0]) and result.returncode == 0:
            result = SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(self.rewrite[1](json.loads(result.stdout))))
        return result

    def calls(self) -> list:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def verbs(self) -> list:
        return [" ".join(call["argv"][:2]) for call in self.calls()]

    def main(self, *argv, pin=None, uv=True) -> tuple:
        out = io.StringIO()
        arguments = [*(["--uv", str(self.uv)] if uv else []), "--json", *argv]
        code = bs.main(arguments, pin=bs.load_pin(json.dumps(self.pin if pin is None else pin)), run=self.run,
                       environ=self.environ, host=self.host, out=out)
        return code, json.loads(out.getvalue())


@pytest.fixture
def m(tmp_path):
    return Machine(tmp_path)


def standalone_answer(python="3.12.16", sqlite=(3, 53, 1), build="20261110", standalone=True):
    def rewrite(seen):
        seen.update(python=[int(part) for part in python.split(".")], sqlite=list(sqlite), build=build,
                    standalone=standalone, sys_version=python + " (main, Nov 10 2026) [Clang]")
        return seen
    return rewrite


# ---------------------------------------------------------------- the script itself

def test_the_bootstrap_is_one_standard_library_script_outside_the_importable_package():
    assert SCRIPT.is_file() and "v4/src" not in SCRIPT.as_posix() and SCRIPT.parent.name == "scripts"
    modules = set()
    for node in ast.walk(ast.parse(SCRIPT.read_text())):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module.split(".")[0])
    assert modules <= set(sys.stdlib_module_names) and not any(name.startswith("memory_os") for name in modules)
    assert bs.load_pin()["state"] == "template"  # the published copy is produced by fill_pin, never committed


def test_the_constants_are_those_of_the_product_never_a_second_truth():
    assert bs.CELLS == cell._CELL_FACTS and tuple(bs.CELLS) == cell.RETAINED_CELLS
    assert bs.SEMANTIC_CELLS == cell.SEMANTIC_CELLS and bs.MIN_SQLITE == MIN_SQLITE
    assert bs.UV_MINIMUM == sqlite_runtime.UV_MINIMUM == (0, 13, 9)
    assert bs.STANDALONE == sqlite_runtime.STANDALONE_CPYTHON and bs.STANDALONE_BUILD == sqlite_runtime.STANDALONE_BUILD
    assert bs.STANDALONE_SOURCE == sqlite_runtime.STANDALONE_SOURCE
    assert bs.STANDALONE_SQLITE_DECLARED == sqlite_runtime.STANDALONE_SQLITE_DECLARED
    assert bs.EPHEMERAL_MARKERS == sqlite_runtime.EPHEMERAL_MARKERS and bs.DEFAULT_VENV == sqlite_runtime.DEFAULT_VENV
    assert {profile: "memory-os-ai[" + extras + "]" for profile, extras in bs.EXTRAS.items()} == \
        {profile: requirement for profile, (requirement, _) in qs.PROFILE_INSTALL.items()}
    assert set(bs.STANDALONE) <= set(bs.CELLS)
    assert all(record["request"].startswith(f"cpython-{record['python']}-") for record in bs.STANDALONE.values())


@pytest.mark.parametrize("name", sorted(RETAINED) + sorted(NOT_RETAINED))
def test_cell_matching_agrees_with_the_product_detection(name, monkeypatch):
    """Same facts, same verdict as ``ops/cell.py``: the bootstrap cannot name another cell."""
    captured = {}
    real = cell.detect

    def spy(**facts):
        captured.update(facts)
        return real(**facts)
    monkeypatch.setattr(cell, "detect", spy)
    expected = (RETAINED.get(name) or NOT_RETAINED[name])()
    host = bs.detect_host(**{key: captured[key] for key in ("sys_platform", "machine", "os_release", "kernel_release",
                                                            "environ", "which", "exists")})
    found = bs.match_cell(host, captured["python_version"], captured["implementation"].lower())
    assert (found["cell_id"], found["label"], found["refusal"]) == \
        (expected["cell_id"], expected["label"], expected["refusal"])
    assert host["service_manager"]["kind"] == expected["service_manager"]["kind"]
    assert host["service_manager"]["available"] == expected["service_manager"]["available"]


def test_wsl_and_other_implementations_are_refused_like_the_product():
    assert bs.match_cell({**UBUNTU, "wsl": True}, (3, 11, 18))["refusal"].startswith("WSL is not a retained")
    assert bs.match_cell(MAC, (3, 11, 14), "pypy")["cell_id"] is None


def test_the_probe_reads_the_target_like_the_product_probe():
    mine = bs.probe_python(sys.executable, run=subprocess.run, environ=dict(os.environ))
    theirs = sqlite_runtime.probe_target(sys.executable)
    assert mine["ok"] and mine["sqlite"] == theirs["version"] == sqlite3.sqlite_version
    assert mine["python"] == theirs["target"]["python"] == ".".join(map(str, sys.version_info[:3]))
    assert mine["sqlite_ok"] == theirs["ok"] and mine["standalone"] == theirs["target"]["standalone"]


# ---------------------------------------------------------------- release pin

def test_the_repository_copy_is_a_template_that_previews_and_refuses_to_apply(m):
    pin = bs.load_pin()
    assert pin["ok"] is False and pin["code"] == "release_pin_unfilled" and pin["state"] == "template"
    before = tree(m.root)
    out = io.StringIO()
    code = bs.main(["--uv", str(m.uv), "--profile", "lexical", "--json", "--apply"], run=m.run, environ=m.environ,
                   host=m.host, out=out)
    report = json.loads(out.getvalue())
    assert code == 1 and report["code"] == "preflight_refused" and "release_pin_unfilled" in report["blocker_codes"]
    assert report["cell"]["proof"]["state"] == "non testé" and report["writes"].startswith("none by the bootstrap")
    assert m.verbs() == ["--version"]
    m.log.unlink()
    assert tree(m.root) == before


def test_fill_pin_produces_a_published_bootstrap_identified_by_its_digest(tmp_path, m):
    source = SCRIPT.read_text()
    filled = bs.fill_pin(source, m.pin)
    assert filled != source and bs.fill_pin(filled, m.pin) == filled  # idempotent, markers kept
    assert filled.count(bs.PIN_BEGIN) == source.count(bs.PIN_BEGIN) and filled.count("\nRELEASE_PIN = r") == 1
    assert source.rsplit(bs.PIN_END, 1)[1] == filled.rsplit(bs.PIN_END, 1)[1]
    namespace = {"__name__": "published", "__file__": str(tmp_path / "x.py")}
    exec(compile(filled, "published.py", "exec"), namespace)
    loaded = namespace["load_pin"]()
    assert loaded["ok"] and loaded["version"] == VERSION and loaded["wheel"]["sha256"] == m.pin["wheel"]["sha256"]
    assert hashlib.sha256(filled.encode()).hexdigest() != hashlib.sha256(source.encode()).hexdigest()


def _broken(pin, change):
    pin = json.loads(json.dumps(pin))
    change(pin)
    return pin


@pytest.mark.parametrize("label,change", [
    ("sdist", lambda p: p["cells"][CELL]["lexical"]["artifacts"][0].update(filename="tiktoken-0.12.0.tar.gz")),
    ("no hash", lambda p: p["cells"][CELL]["lexical"]["artifacts"][0].update(sha256="abc")),
    ("http", lambda p: p["cells"][CELL]["lexical"]["artifacts"][0].update(url="http://example.invalid/x.whl")),
    ("marker", lambda p: p["cells"][CELL]["lexical"]["constraints"].__setitem__(0, 'tiktoken==0.12.0; os_name=="x"')),
    ("range", lambda p: p["cells"][CELL]["lexical"]["constraints"].__setitem__(0, "tiktoken>=0.12.0")),
    ("no artifact", lambda p: p["cells"][CELL]["lexical"]["artifacts"].pop()),
    ("no constraint", lambda p: p["cells"][CELL]["lexical"]["constraints"].pop()),
    ("itself", lambda p: p["cells"][CELL]["lexical"]["constraints"].append("memory-os-ai==4.1.1")),
    ("stale lock digest", lambda p: p["cells"][CELL]["lexical"]["artifacts"][0].update(size=7)
     or p["cells"][CELL]["lexical"].update(lock_sha256="0" * 64)),
    ("semantic elsewhere", lambda p: p["cells"]["macos-arm64-py312"].update(
        semantic=p["cells"]["macos-arm64-py312"]["lexical"])),
    ("unknown cell", lambda p: p["cells"].update({"debian-12-arm64-py311": p["cells"][CELL]})),
    ("wheel digest", lambda p: p["wheel"].update(sha256="zz")),
    ("no content digest", lambda p: p["wheel"].pop("content_sha256")),
    ("other version", lambda p: p.update(version="4.1.2")),
    ("schema", lambda p: p.update(schema_version=2)),
])
def test_an_incoherent_pin_is_refused_whole(m, label, change):
    pin = _broken(m.pin, change)
    loaded = bs.load_pin(json.dumps(pin))
    assert loaded["ok"] is False and loaded["code"] == "release_pin_invalid", label
    with pytest.raises(ValueError):
        bs.fill_pin(SCRIPT.read_text(), pin)
    assert bs.load_pin("{not json")["code"] == "release_pin_invalid"


def test_the_lock_digest_binds_cell_profile_constraints_and_artifacts(m):
    lock = m.pin["cells"][CELL]["lexical"]
    digest = bs.lock_fingerprint(CELL, "lexical", lock)
    assert digest == lock["lock_sha256"] == bs.lock_fingerprint(CELL, "lexical", json.loads(json.dumps(lock)))
    other = json.loads(json.dumps(lock))
    other["artifacts"][0]["sha256"] = "f" * 64
    assert len({digest, bs.lock_fingerprint("macos-arm64-py312", "lexical", lock),
                bs.lock_fingerprint(CELL, "semantic", lock), bs.lock_fingerprint(CELL, "lexical", other)}) == 4


def test_the_content_digest_covers_site_packages_only(tmp_path, m):
    digest = bs.wheel_content_fingerprint(m.wheel)
    other = tmp_path / "other"
    other.mkdir()
    changed = other / m.wheel.name
    with zipfile.ZipFile(m.wheel) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, b"other doc\n" if ".data/" in name else b"x,y,z\n" if name.endswith("RECORD") else data)
    assert bs.wheel_content_fingerprint(changed) == digest  # data files and RECORD are outside
    with zipfile.ZipFile(changed, "a") as target:
        target.writestr("memory_os_v4/extra.py", "")
    assert bs.wheel_content_fingerprint(changed) != digest


# ---------------------------------------------------------------- preview: no write, no network

def test_the_preview_of_the_published_script_writes_nothing_and_runs_only_uv_version(tmp_path):
    """The real thing: the filled script run as a process, HOME inventoried before and after."""
    m = Machine(tmp_path, home="my home", tools="my tools")
    published = tmp_path / "published" / "memory_os_bootstrap.py"
    published.parent.mkdir()
    published.write_text(bs.fill_pin(SCRIPT.read_text(), m.pin))
    (m.home / ".memory-os").mkdir()
    (m.home / ".memory-os" / "kept.txt").write_text("user data")
    manifest = tmp_path / "published" / "p9.json"
    lock = m.pin["cells"][CELL]["lexical"]
    manifest.write_text(json.dumps({"schema_version": 1, "kind": bs.P9_KIND, "artifact": {"sha256": m.pin["wheel"]["sha256"]},
                                    "cells": [{"cell_id": CELL, "lock_sha256": lock["lock_sha256"], "state": "PASS",
                                               "means": "machine"}]}))
    home_before, root_before = tree(m.home), tree(tmp_path)
    environ = {"HOME": str(m.home), "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
    argv = [sys.executable, "-I", "-B", str(published), "--uv", str(m.uv), "--profile", "lexical",
            "--p9-manifest", str(manifest)]
    done = subprocess.run([*argv, "--json"], capture_output=True, text=True, env=environ, cwd=str(m.home), timeout=120)
    report = json.loads(done.stdout)
    assert tree(m.home) == home_before
    assert done.returncode == (0 if sys.platform == "darwin" and report["system"]["arch"] == "arm64" else 1)
    # No absolute claim: writes are "none detected" on a named scope, the network is "not proven here".
    assert report["stage"] == "preview" and report["writes"] == bs.PREVIEW_WRITES and report["network"] == bs.PREVIEW_NETWORK
    assert "detected on the inventoried destinations" in bs.PREVIEW_WRITES and "not proven" in bs.PREVIEW_NETWORK
    assert report["no_write_check"]["identical"] is True and "not detected here" in report["no_write_check"]["limit"]
    assert report["bootstrap"]["sha256"] == bs.sha256_file(published) != bs.sha256_file(SCRIPT)
    assert report["venv"] == str(m.home / ".memory-os" / "venv") and not Path(report["venv"]).exists()
    assert report["uv"]["version"] == "0.13.9" and report["interpreter"]["sqlite"] == sqlite3.sqlite_version
    assert m.verbs() == ["--version"] and all(call["name"] == "uv" for call in m.calls())
    if done.returncode == 0:
        assert report["cell"]["cell_id"] == CELL and report["cell"]["proof"]["state"] == "PASS"
        assert shlex.split(report["next"])[-1] == "--apply" and str(published) in shlex.split(report["next"])
    text = subprocess.run(argv, capture_output=True, text=True, env=environ, cwd=str(m.home), timeout=120).stdout
    assert "manifeste non signé" in text and "acquisitions" in text and "proof state" in text
    m.log.unlink()
    assert tree(tmp_path) == root_before


def test_the_preview_reports_everything_the_contract_lists(m):
    before = tree(m.root)
    code, report = m.main("--profile", "lexical")
    assert code == 0 and report["ok"] is True and report["blockers"] == []
    assert report["system"] == {"os": "darwin", "arch": "arm64", "machine": "arm64", "distribution": None, "wsl": False}
    assert report["service_manager"]["kind"] == "launchd" and report["route"] == "a"
    assert report["interpreter"]["python"] == ".".join(map(str, sys.version_info[:3]))
    assert report["interpreter"]["sqlite"] == sqlite3.sqlite_version and report["interpreter"]["probed"] == sys.executable
    assert report["cell"]["cell_id"] == CELL and report["cell"]["proof"]["state"] == "non testé"
    assert report["cell"]["proof"]["limit"] == bs.UNSIGNED and "manifeste non signé" in bs.UNSIGNED
    assert report["lock"] == {"cell_id": CELL, "profile": "lexical", "constraints": 2, "artifacts": 2, "bytes": 2001,
                              "sha256": m.pin["cells"][CELL]["lexical"]["lock_sha256"]}
    attested = report["sqlite_attestation"]
    assert attested["venv"]["state"] == "not_created" and attested["server"]["state"] == "not_running"
    assert attested["base_interpreter"]["version"] == sqlite3.sqlite_version and attested["minimum"] == "3.51.3"
    for item in report["acquisitions"]:
        assert set(item) == {"object", "source", "version", "size", "destination", "sha256", "when"}
        assert item["object"] and item["source"] and item["destination"]
    objects = [item["object"] for item in report["acquisitions"]]
    assert objects[:2] == ["bootstrap (this script)", "uv"] and "wheel " + m.wheel.name in objects
    assert sum(name.startswith("wheel ") for name in objects) == 3 and "cl100k_base.tiktoken" in objects
    cache = next(item for item in report["acquisitions"] if item["object"] == "uv cache")
    assert cache["destination"] == report["uv_cache"] == os.path.join(report["work_dir"], "uv-cache")
    assert os.path.dirname(report["work_dir"]) == str(m.home / ".memory-os") and not os.path.exists(report["work_dir"])
    assert {report["venv"], report["work_dir"], report["uv_cache"]} <= set(report["no_write_check"]["scope"])
    assert not any(name.startswith("CPython") for name in objects)  # route (a): no interpreter acquisition
    assert report["download_bytes"] == m.wheel.stat().st_size + 2001
    assert "_argv" not in report and set(report["commands"]) == {"venv", "install", "quickstart_preview"}
    assert m.verbs() == ["--version"]
    m.log.unlink()
    assert tree(m.root) == before


def test_the_semantic_profile_names_its_model_and_stays_on_its_cell(m):
    if CELL in bs.SEMANTIC_CELLS:
        code, report = m.main("--profile", "semantic")
        assert code == 0 and "semantic model" in [item["object"] for item in report["acquisitions"]]
    else:
        code, report = m.main("--profile", "semantic")
        assert code == 1 and report["blocker_codes"] == ["profile_not_qualified_on_cell"]


def test_no_profile_is_ever_chosen_by_default(m):
    code, report = m.main()
    assert code == 1 and report["blocker_codes"] == ["profile_required"]


# ---- proof state: local P9 manifest only

def p9(m, tmp_path, *, artifact=None, cells=None, text=None) -> str:
    lock = m.pin["cells"][CELL]["lexical"]["lock_sha256"]
    document = {"schema_version": 1, "kind": bs.P9_KIND,
                "artifact": {"sha256": artifact or m.pin["wheel"]["sha256"], "filename": m.wheel.name},
                "cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "PASS", "means": "machine"}]
                if cells is None else cells}
    path = tmp_path / "p9 manifest.json"
    path.write_text(json.dumps(document) if text is None else text)
    return str(path)


def test_without_manifest_the_cell_is_not_tested(m):
    _, report = m.main("--profile", "lexical")
    assert report["cell"]["proof"] == {"state": "non testé", "reason": "manifest_not_provided", "means": None,
                                       "limit": bs.UNSIGNED, "manifest": None,
                                       "source": "local file only; the preview never fetches a proof"}


@pytest.mark.parametrize("state", ["PASS", "FAIL"])
def test_a_matching_manifest_gives_its_state(m, tmp_path, state):
    lock = m.pin["cells"][CELL]["lexical"]["lock_sha256"]
    path = p9(m, tmp_path, cells=[{"cell_id": "macos-arm64-py999", "lock_sha256": lock, "state": "PASS"},
                                  {"cell_id": CELL, "lock_sha256": lock, "state": state, "means": "machine"}])
    code, report = m.main("--profile", "lexical", "--p9-manifest", path)
    proof = report["cell"]["proof"]
    assert code == 0  # a published FAIL is announced, not hidden; the decision stays with the user
    assert (proof["state"], proof["reason"], proof["means"]) == (state, "manifest_matches_artifact_cell_and_lock", "machine")
    assert proof["manifest"] == {"path": path, "sha256": bs.sha256_file(path)} and proof["limit"] == bs.UNSIGNED
    assert "P9 manifest" in [item["object"] for item in report["acquisitions"]]


@pytest.mark.parametrize("reason,build", [
    ("artifact_mismatch", lambda m, lock: {"artifact": "e" * 64}),
    ("cell_absent_from_manifest", lambda m, lock: {"cells": [{"cell_id": "ubuntu2404-arm64-py311", "lock_sha256": lock,
                                                              "state": "PASS"}]}),
    ("lock_mismatch", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": "d" * 64, "state": "PASS"}]}),
    ("lock_mismatch", lambda m, lock: {"cells": [{"cell_id": CELL, "state": "PASS"}]}),
    ("state_not_proven", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "non testé"}]}),
    ("state_not_proven", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "pass"}]}),
    ("manifest_ambiguous", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "PASS"},
                                                      {"cell_id": CELL, "lock_sha256": lock, "state": "FAIL"}]}),
    ("manifest_unreadable", lambda m, lock: {"text": "{not json"}),
    ("manifest_unreadable", lambda m, lock: {"text": json.dumps({"schema_version": 1, "kind": "something else",
                                                                 "artifact": {"sha256": "a"}, "cells": []})}),
    ("manifest_malformed", lambda m, lock: {"cells": {CELL: "PASS"}}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": []}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": {"x": 1}}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": [lock], "state": "PASS"}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": [CELL], "lock_sha256": lock, "state": "PASS"}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "PASS",
                                                       "means": {"a": []}}]}),
    ("manifest_malformed", lambda m, lock: {"cells": [{"cell_id": CELL, "lock_sha256": lock, "state": "PASS"}, "x"]}),
    ("manifest_malformed", lambda m, lock: {"cells": [None]}),
    ("manifest_malformed", lambda m, lock: {"text": json.dumps({"schema_version": 1, "kind": "memory-os-ai-p9-manifest",
                                                                "artifact": ["a"], "cells": []})}),
    ("manifest_malformed", lambda m, lock: {"text": json.dumps({"schema_version": 1, "kind": "memory-os-ai-p9-manifest",
                                                                "artifact": {"sha256": 7}, "cells": []})}),
    ("manifest_malformed", lambda m, lock: {"text": json.dumps({"schema_version": 1, "kind": "memory-os-ai-p9-manifest"})}),
    ("manifest_unreadable", lambda m, lock: {"text": "[1, 2]"}),
    ("manifest_unreadable", lambda m, lock: {"text": "null"}),
    ("manifest_unreadable", lambda m, lock: {"text": json.dumps({"schema_version": True, "kind": "memory-os-ai-p9-manifest",
                                                                 "artifact": {"sha256": "a"}, "cells": []})}),
    ("manifest_unreadable", lambda m, lock: {"text": json.dumps({"schema_version": [1], "kind": {}})}),
])
def test_a_manifest_that_does_not_match_exactly_proves_nothing(m, tmp_path, reason, build):
    path = p9(m, tmp_path, **build(m, m.pin["cells"][CELL]["lexical"]["lock_sha256"]))
    code, report = m.main("--profile", "lexical", "--p9-manifest", path)
    assert code == 0 and (report["cell"]["proof"]["state"], report["cell"]["proof"]["reason"]) == ("non testé", reason)


def test_an_absent_manifest_file_and_another_profile_prove_nothing(m, tmp_path):
    _, report = m.main("--profile", "lexical", "--p9-manifest", str(tmp_path / "absent.json"))
    assert (report["cell"]["proof"]["state"], report["cell"]["proof"]["reason"]) == ("non testé", "manifest_unreadable")
    if CELL in bs.SEMANTIC_CELLS:  # the lock digest binds the profile: a lexical proof is not a semantic one
        _, report = m.main("--profile", "semantic", "--p9-manifest", p9(m, tmp_path))
        assert (report["cell"]["proof"]["state"], report["cell"]["proof"]["reason"]) == ("non testé", "lock_mismatch")


# ---- uv

@pytest.mark.parametrize("line,code", [("uv 0.13.8", "uv_too_old"), ("uv 0.10.8 (Homebrew 2026-03-03)", "uv_too_old"),
                                       ("uv 0.13.9rc1", "uv_unreadable"), ("something else", "uv_unreadable"),
                                       ("", "uv_unreadable")])
def test_an_old_or_unreadable_uv_is_refused_with_a_remedy(m, line, code):
    m.configure(version_line=line)
    before = tree(m.home)
    rc, report = m.main("--profile", "lexical", "--apply")
    assert rc == 1 and report["blocker_codes"] == [code] and report["code"] == "preflight_refused"
    assert "0.13.9" in report["blockers"][0] and "--uv" in report["blockers"][0] and "nothing written" in report["blockers"][0]
    assert m.verbs() == ["--version"] and tree(m.home) == before
    assert report["sqlite_attestation"]["venv"]["state"] == "not_created"  # failure reports carry it too


@pytest.mark.parametrize("version", ["0.13.9", "0.13.10", "0.14.0", "1.0.0"])
def test_the_minimum_uv_and_later_are_accepted(tmp_path, version):
    m = Machine(tmp_path, version=version)
    code, report = m.main("--profile", "lexical")
    assert code == 0 and report["uv"]["version"] == version and report["uv"]["sha256"] == bs.sha256_file(m.uv)


def test_uv_is_never_looked_up_and_uvx_is_never_accepted(m, tmp_path):
    code, report = m.main("--profile", "lexical", uv=False)
    assert code == 1 and report["blocker_codes"] == ["uv_missing"] and m.calls() == []
    uvx = m.tools / "uvx"
    uvx.write_text(m.uv.read_text())
    uvx.chmod(0o755)
    out = io.StringIO()
    code = bs.main(["--uv", str(uvx), "--profile", "lexical", "--json"], pin=bs.load_pin(json.dumps(m.pin)),
                   run=m.run, environ=m.environ, host=m.host, out=out)
    assert code == 1 and json.loads(out.getvalue())["blocker_codes"] == ["uvx_refused"] and m.calls() == []
    plain = m.tools / "notes.txt"
    plain.write_text("not a program")
    out = io.StringIO()
    bs.main(["--uv", str(plain), "--profile", "lexical", "--json"], pin=bs.load_pin(json.dumps(m.pin)), run=m.run,
            environ=m.environ, host=m.host, out=out)
    assert json.loads(out.getvalue())["blocker_codes"] == ["uv_not_executable"]


# ---- cells

@pytest.mark.parametrize("host,label", [
    ({**UBUNTU, "distribution": {"id": "debian", "version_id": "12", "conflicts": []}}, "debian-12-arm64"),
    ({**MAC, "arch": "x86_64", "machine": "x86_64"}, "macos-x86_64"),
    ({**UBUNTU, "distribution": {"id": "ubuntu", "version_id": "22.04", "conflicts": []}}, "ubuntu-22.04-arm64"),
    ({**UBUNTU, "wsl": True}, "WSL"),
    ({"os": "other", "sys_platform": "win32", "arch": "amd64", "machine": "AMD64", "distribution": None, "wsl": False,
      "service_manager": {"kind": None, "available": False, "attestation": "presence only"}}, "win32-amd64"),
])
def test_a_cell_that_is_not_retained_is_refused_and_never_announced_supported(m, host, label):
    m.host = host
    before = tree(m.home)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["blocker_codes"] == ["cell_not_retained"] and report["cell"]["cell_id"] is None
    assert label in report["blockers"][0] and "unqualified" in report["blockers"][0]
    assert report["cell"]["proof"]["state"] == "non testé" and report["lock"] is None
    assert m.verbs() == ["--version"] and tree(m.home) == before


def test_a_python_outside_the_cells_is_offered_the_named_standalone_of_the_system(m):
    m.rewrite = (os.path.basename(sys.executable), lambda seen: {**seen, "python": [3, 10, 14]})
    code, report = m.main("--profile", "lexical")
    assert code == 1 and report["blocker_codes"] == ["cell_not_retained"]
    assert "macos-arm64-py310" in report["blockers"][0] and "--cpython 3.12 | --cpython 3.13" in report["blockers"][0]
    m.host = dict(UBUNTU)  # Ubuntu 24.04: the system Python is 3.12 with SQLite 3.45.1
    m.rewrite = (os.path.basename(sys.executable), lambda seen: {**seen, "python": [3, 12, 3], "sqlite": [3, 45, 1]})
    code, report = m.main("--profile", "lexical")
    assert code == 1 and report["blocker_codes"] == ["sqlite_runtime_too_old", "cell_not_retained"]
    assert "version 3.45.1, minimum 3.51.3" in report["blockers"][0] and "--cpython 3.11" in report["blockers"][0]
    assert "no other journal mode" in report["blockers"][0]


def test_a_retained_cell_without_pinned_lock_is_refused(m):
    pin = make_pin(m.wheel, cells=("ubuntu2404-arm64-py311",))
    code, report = m.main("--profile", "lexical", pin=pin)
    assert code == 1 and report["blocker_codes"] == ["cell_lock_missing"] and report["cell"]["cell_id"] == CELL


def test_the_target_interpreter_is_probed_not_the_one_running_the_bootstrap(m):
    other = m.root / "other python" / "bin" / "python3"
    other.parent.mkdir(parents=True)
    other.symlink_to(sys.executable)
    m.rewrite = ("other python/bin/python3", lambda seen: {**seen, "sqlite": [3, 50, 4]})
    code, report = m.main("--profile", "lexical", "--python", str(other))
    assert code == 1 and report["blocker_codes"] == ["sqlite_runtime_too_old"]
    assert report["interpreter"]["probed"] == str(other) and report["interpreter"]["sqlite"] == "3.50.4"
    assert report["sqlite_attestation"]["base_interpreter"]["version"] == "3.50.4" != sqlite3.sqlite_version
    code, report = m.main("--profile", "lexical", "--python", str(m.root / "absent"))
    assert code == 1 and report["blocker_codes"] == ["python_unprobed"]
    cached = m.home / ".cache" / "uv" / "x" / "bin" / "python"
    code, report = m.main("--profile", "lexical", "--python", str(cached))
    assert code == 1 and report["blocker_codes"] == ["python_ephemeral"]


def test_an_existing_venv_is_never_cleared_or_reused(m):
    venv = m.home / ".memory-os" / "venv"
    venv.mkdir(parents=True)
    (venv / "kept").write_text("mine")
    before = tree(m.home)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["blocker_codes"] == ["venv_exists"] and tree(m.home) == before
    assert m.verbs() == ["--version"]


# ---------------------------------------------------------------- apply: route (a)

def test_apply_creates_the_durable_venv_through_uv_with_spaces_everywhere(tmp_path):
    m = Machine(tmp_path, home="my home", tools="my tools")
    code, report = m.main("--profile", "lexical", "--clients", "claude-code,codex", "--venv", "~/my venv", "--apply")
    assert code == 0 and report["ok"] is True and report["stage"] == "complete", report.get("error")
    venv = m.home / "my venv"
    python = str(venv / "bin" / "python")
    calls = m.calls()
    assert [" ".join(call["argv"][:2]) for call in calls] == ["--version", "venv --no-config", "pip install"]
    assert calls[1]["argv"] == ["venv", "--no-config", "--no-project", "--offline", "--no-python-downloads",
                                "--python", sys.executable, str(venv)]  # "~" expanded, one argument, no shell
    work = Path(report["work_dir"])
    assert work.parent == m.home and work.name.startswith("bootstrap-") and stat.S_IMODE(work.stat().st_mode) == 0o700
    requirements = str(work / f"requirements-{CELL}-lexical.txt")
    assert calls[2]["argv"] == ["pip", "install", "--no-config", "--no-python-downloads", "--python", python,
                                "--require-hashes", "--only-binary", ":all:", "--link-mode", "copy", "-r", requirements]
    for call in calls:
        assert call["name"] == "uv" and "uvx" not in " ".join(call["argv"])
        assert call["env"]["UV_PYTHON_DOWNLOADS"] == "never" and call["env"]["UV_NO_CONFIG"] == "1"
        assert "VIRTUAL_ENV" not in call["env"] and "PYTHONPATH" not in call["env"]
        assert not any(key.startswith(("MEMORY_V4_", "XDG_", "HF_")) for key in call["env"])
    # the uv cache is named by the bootstrap, inside its private directory: never uv's default, never inherited
    assert [call["env"].get("UV_CACHE_DIR") for call in calls] == [None, str(work / "uv-cache"), str(work / "uv-cache")]
    assert calls[1]["env"]["UV_OFFLINE"] == "1" and "UV_OFFLINE" not in calls[2]["env"]
    lines = Path(requirements).read_text().splitlines()
    assert lines[1] == f"memory-os-ai[ops,brief]=={VERSION} --hash=sha256:{m.pin['wheel']['sha256']}"
    lock = m.pin["cells"][CELL]["lexical"]
    assert sorted(lines[2:]) == sorted(f"{item['name']}=={item['version']} --hash=sha256:{item['sha256']}"
                                       for item in lock["artifacts"])
    assert stat.S_IMODE(Path(requirements).stat().st_mode) == 0o600
    # every command is shown quoted: pasting it gives back the exact argument list
    assert shlex.split(report["commands"]["venv"]) == [str(m.uv), *calls[1]["argv"]]
    assert shlex.split(report["commands"]["install"]) == [str(m.uv), *calls[2]["argv"]]
    assert "'" in report["commands"]["venv"]
    # the venv interpreter spoke for itself, then the installed artifact was checked
    assert report["venv_interpreter"]["probed"] == python
    attested = report["sqlite_attestation"]
    assert attested["venv"] == {"state": "attested", "version": sqlite3.sqlite_version, "ok": True, "code": None,
                                "python": ".".join(map(str, sys.version_info[:3])), "read_by": "venv interpreter"}
    assert attested["server"]["state"] == "not_running"
    installed = report["installed"]
    assert installed["ok"] and installed["version"] == VERSION and installed["inside_venv"] is True
    assert installed["content_sha256"] == m.pin["wheel"]["content_sha256"] and installed["files"] == 5
    # quickstart: preview only, from the venv, isolated from PYTHONPATH
    quickstart = report["quickstart"]
    assert quickstart["ok"] is True and quickstart["writes"] == "none (preview)" and quickstart["cell_id"] == CELL
    # the child environment is an allow-list: nothing that steers the product is inherited
    inherited = set(quickstart["downloads_required_bytes"])  # the fake CLI echoes its environment keys there
    assert inherited <= set(bs.CHILD_ENV_KEEP) | {"PYTHONDONTWRITEBYTECODE", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert "HOME" in inherited and not any(key.startswith(("MEMORY_V4_", "UV_", "XDG_CONFIG", "HF_")) for key in inherited)
    step = report["steps"][-1]
    assert step["step"] == "quickstart_preview" and shlex.split(step["command"]) == [
        python, "-I", "-m", "memory_os_v4.ops.cli", "quickstart", "--profile", "lexical", "--clients", "claude-code,codex"]
    assert "--apply" not in step["command"]
    assert shlex.split(report["next"]) == [python, "-I", "-m", "memory_os_v4.ops.cli", "quickstart", "--profile",
                                           "lexical", "--clients", "claude-code,codex", "--apply", "--download-approved"]
    saved = json.loads(Path(report["report_path"]).read_text())
    assert saved["ok"] is True and saved["sqlite_attestation"] == attested and "_argv" not in saved
    assert report["report_path"] == str(work / "report.json") and stat.S_IMODE((work / "report.json").stat().st_mode) == 0o600
    assert sorted(path.name for path in work.iterdir()) == sorted([Path(requirements).name, "report.json", "uv-cache"])
    assert {path.name for path in m.home.iterdir()} == {"my venv", work.name}  # nothing else was written in HOME
    assert report["writes"] == [str(work), requirements, str(work / "uv-cache"), str(venv)]


def test_apply_from_a_wheelhouse_is_offline_and_checks_the_wheel_first(m):
    code, report = m.main("--profile", "lexical", "--wheelhouse", str(m.wheelhouse), "--apply")
    assert code == 0, report.get("error")
    install_call = m.calls()[-1]
    assert install_call["argv"][-4:] == ["--offline", "--no-index", "--find-links", str(m.wheelhouse)]
    assert install_call["env"]["UV_OFFLINE"] == "1" and report["wheel_local"]["sha256"] == m.pin["wheel"]["sha256"]
    assert report["download_bytes"] == 0 and all("wheelhouse" in item["source"] or item["source"] == m.pin["wheel"]["source"]
                                                 for item in report["acquisitions"] if item["object"].startswith("wheel "))


def test_a_wheel_that_is_not_the_pinned_one_is_refused_before_anything(m, tmp_path):
    pin = json.loads(json.dumps(m.pin))
    pin["wheel"]["sha256"] = "a" * 64
    before = tree(m.home)
    code, report = m.main("--profile", "lexical", "--wheelhouse", str(m.wheelhouse), "--apply", pin=pin)
    assert code == 1 and report["blocker_codes"] == ["wheel_mismatch"] and tree(m.home) == before
    empty = tmp_path / "empty"
    empty.mkdir()
    code, report = m.main("--profile", "lexical", "--wheelhouse", str(empty), pin=pin)
    assert report["blocker_codes"] == ["wheel_missing"]


def test_an_installed_artifact_that_differs_from_the_pin_stops_before_quickstart(m):
    m.configure(tamper=True)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["code"] == "artifact_mismatch" and report["installed"]["ok"] is False
    assert report["installed"]["content_sha256"] != report["installed"]["expected_content_sha256"]
    assert report["quickstart"] is None and [s["step"] for s in report["steps"]] == ["venv", "install"]
    assert report["venv_kept"] is True and report["sqlite_attestation"]["venv"]["state"] == "attested"
    assert json.loads(Path(report["report_path"]).read_text())["code"] == "artifact_mismatch"


@pytest.mark.parametrize("failing,code,steps", [("venv --no-config", "venv_creation_failed", ["venv"]),
                                                ("pip install", "install_failed", ["venv", "install"])])
def test_a_uv_failure_is_reported_with_the_attestation_and_nothing_is_removed(m, failing, code, steps):
    m.configure(fail=[failing])
    rc, report = m.main("--profile", "lexical", "--apply")
    assert rc == 1 and report["ok"] is False and report["code"] == code and report["error"].startswith(code)
    assert [step["step"] for step in report["steps"]] == steps and report["steps"][-1]["returncode"] == 2
    attested = report["sqlite_attestation"]
    assert attested["server"]["state"] == "not_running" and attested["base_interpreter"]["version"] == sqlite3.sqlite_version
    assert attested["venv"]["state"] == ("attested" if "install" in steps else "not_created")
    assert report["venv_kept"] is ("install" in steps) and report["quickstart"] is None
    assert json.loads(Path(report["report_path"]).read_text())["sqlite_attestation"] == attested
    assert "python install" not in m.verbs()


def test_a_venv_whose_sqlite_is_unfit_gets_nothing_installed(m):
    m.rewrite = ("venv/bin/python", lambda seen: {**seen, "sqlite": [3, 50, 4]})
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["code"] == "sqlite_runtime_too_old" and "non testé" in report["error"]
    assert report["sqlite_attestation"]["venv"] == {"state": "refused", "version": "3.50.4", "ok": False,
                                                    "code": "sqlite_runtime_too_old", "read_by": "venv interpreter",
                                                    "python": ".".join(map(str, sys.version_info[:3]))}
    assert m.verbs() == ["--version", "venv --no-config"]


def test_blockers_of_the_quickstart_preview_are_reported_not_hidden(tmp_path):
    m = Machine(tmp_path, blocked=True)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["code"] == "quickstart_blocked" and report["installed"]["ok"] is True
    assert report["quickstart"]["blocker_codes"] == ["runtime_unqualified"] and report.get("next") is None


# ---------------------------------------------------------------- apply: route (i)

@pytest.fixture
def mi(m):
    m.pin = make_pin(m.wheel, cells=tuple(bs.CELLS))
    return m


def test_route_i_names_the_cpython_and_downloads_nothing_without_the_consent(mi):
    m = mi
    code, report = m.main("--profile", "lexical", "--cpython", "3.12")
    assert code == 0 and report["route"] == "i" and report["cell"]["cell_id"] == "macos-arm64-py312"
    cpython = [item for item in report["acquisitions"] if item["object"].startswith("CPython")]
    assert cpython == [{"object": "CPython 3.12.16", "source": bs.STANDALONE_SOURCE, "version": "3.12.16+20261110",
                        "size": 25_400_000, "destination": str(m.home / ".local/share/uv/python"),
                        "sha256": None, "when": "--apply --acquire-cpython (skipped by uv when already installed)"}]
    assert report["interpreter"]["sqlite"] is None and report["interpreter"]["sqlite_declared"] == "3.53.1"
    assert report["sqlite_attestation"]["base_interpreter"] == {"version": None, "ok": None, "declared": "3.53.1",
                                                                "python": "3.12.16", "read_by": None}
    assert any("DECLARED" in notice for notice in report["notices"])
    assert shlex.split(report["commands"]["acquire_cpython"]) == [
        str(m.uv), "python", "install", "--no-config", "--no-bin", "cpython-3.12.16-macos-aarch64-none"]
    assert str(m.home / ".local/share/uv/python") in report["no_write_check"]["scope"]
    assert m.verbs() == ["--version"]
    code, report = m.main("--profile", "lexical", "--cpython", "3.12", "--apply")
    assert code == 1 and report["code"] == "venv_creation_failed" and "--acquire-cpython" in report["error"]
    assert "python install" not in m.verbs() and report["sqlite_attestation"]["venv"]["state"] == "not_created"
    assert all(call["env"].get("UV_PYTHON_DOWNLOADS") == "never" for call in m.calls())


def test_route_i_with_the_named_consent_installs_and_attests_the_standalone(mi):
    m = mi
    m.rewrite = ("venv/bin/python", standalone_answer())
    code, report = m.main("--profile", "lexical", "--cpython", "3.12", "--apply", "--acquire-cpython")
    # the fake wheel prints the cell of the test interpreter; only the bootstrap side is asserted here
    calls = m.calls()
    request = "cpython-3.12.16-macos-aarch64-none"
    assert calls[1]["argv"] == ["python", "install", "--no-config", "--no-bin", request]
    assert {call["env"].get("UV_PYTHON_INSTALL_DIR") for call in calls[1:]} == {str(m.home / ".local/share/uv/python")}
    assert calls[1]["env"]["UV_PYTHON_DOWNLOADS"] == "manual"  # the only step that may fetch an interpreter
    venv = str(m.home / ".memory-os" / "venv")
    assert calls[2]["argv"] == ["venv", "--no-config", "--no-project", "--offline", "--no-python-downloads",
                                "--managed-python", "--python", request, venv]
    assert [call["env"]["UV_PYTHON_DOWNLOADS"] for call in calls] == ["never", "manual", "never", "never"]
    assert sum(" ".join(call["argv"][:2]) == "python install" for call in calls) == 1
    assert report["standalone"]["build_state"] == "attested" and report["standalone"]["build"] == "20261110"
    assert report["sqlite_attestation"]["venv"]["version"] == "3.53.1" and report["venv_interpreter"]["python"] == "3.12.16"
    assert report["installed"]["ok"] is True and code == 0, report.get("error")


@pytest.mark.parametrize("answer,code", [
    (standalone_answer(build="20250101"), "standalone_mismatch"),
    (standalone_answer(python="3.12.13"), "standalone_mismatch"),
    (standalone_answer(standalone=False), "standalone_mismatch"),
    (standalone_answer(python="3.13.18"), "venv_cell_mismatch"),
    (standalone_answer(sqlite=(3, 50, 4)), "sqlite_runtime_too_old"),
])
def test_route_i_refuses_another_interpreter_than_the_named_build(mi, answer, code):
    m = mi
    m.rewrite = ("venv/bin/python", answer)
    rc, report = m.main("--profile", "lexical", "--cpython", "3.12", "--apply", "--acquire-cpython")
    assert rc == 1 and report["code"] == code and report["installed"] is None
    assert "pip install" not in m.verbs() and report["venv_kept"] is True


def test_an_unproven_build_is_refused_and_only_a_pinned_sys_version_settles_it(mi):
    """An exact Python version does not identify a build: without proof nothing is installed."""
    m = mi
    m.rewrite = ("venv/bin/python", standalone_answer(build=None))
    code, report = m.main("--profile", "lexical", "--cpython", "3.12", "--apply", "--acquire-cpython")
    assert code == 1 and report["code"] == "standalone_build_unattested" and "non testé" in report["error"]
    assert report["standalone"]["build_state"] == "unattested" and report["installed"] is None
    assert "pip install" not in m.verbs() and report["quickstart"] is None
    for wanted, state, rc in (("3.12.16 (main, Nov 10 2026) [Clang]", "attested", 0), ("3.12.16 (other)", "mismatch", 1)):
        other = Machine(m.root / state)
        other.pin = make_pin(other.wheel, cells=tuple(bs.CELLS))
        other.pin["cells"]["macos-arm64-py312"]["lexical"]["interpreter"] = {"sys_version": wanted}
        other.rewrite = ("venv/bin/python", standalone_answer(build=None))
        code, report = other.main("--profile", "lexical", "--cpython", "3.12", "--apply", "--acquire-cpython")
        assert code == rc and report["standalone"]["build_state"] == state


def test_route_i_exists_only_where_a_standalone_is_named(mi):
    m = mi
    code, report = m.main("--profile", "lexical", "--cpython", "3.11")
    assert code == 1 and report["blocker_codes"] == ["standalone_not_named"]
    assert "--cpython 3.12 | --cpython 3.13" in report["blockers"][0]
    m.host = dict(UBUNTU)
    code, report = m.main("--profile", "lexical", "--cpython", "3.11")
    assert code == 0 and report["cell"]["cell_id"] == "ubuntu2404-arm64-py311"
    assert report["service_manager"]["kind"] == "systemd-user"
    assert "cpython-3.11.18-linux-aarch64-gnu" in report["commands"]["acquire_cpython"]
    code, report = m.main("--profile", "lexical", "--cpython", "3.12")
    assert code == 1 and report["blocker_codes"] == ["standalone_not_named"] and "ubuntu-24.04-arm64-py312" in report["blockers"][0]
    code, report = m.main("--profile", "lexical", "--cpython", "3.11", "--python", sys.executable)
    assert "python_choice_conflict" in report["blocker_codes"]


def test_the_consent_option_means_nothing_alone(m, capsys):
    assert bs.main(["--uv", str(m.uv), "--profile", "lexical", "--acquire-cpython"]) == 2
    assert bs.main(["--uv", str(m.uv), "--profile", "lexical", "--apply", "--acquire-cpython"]) == 2
    assert "consent" in capsys.readouterr().err and m.calls() == []


def test_no_command_ever_names_uvx_or_applies_quickstart(mi):
    m = mi
    m.rewrite = ("venv/bin/python", standalone_answer())
    _, report = m.main("--profile", "lexical", "--cpython", "3.12", "--apply", "--acquire-cpython")
    seen = [call["argv"] for call in m.calls()] + [shlex.split(step["command"]) for step in report["steps"]]
    assert seen and not any("uvx" in part for argv in seen for part in argv)
    assert all(call["name"] == "uv" for call in m.calls())
    assert not any("--apply" in argv or "launchctl" in argv or "systemctl" in argv for argv in seen)


# ---------------------------------------------------------------- L2 reservations closed in the product

def test_standalone_plan_expands_the_home_and_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/some one")
    plan = sqlite_runtime.standalone_plan("macos-arm64-py312", uv="~/my tools/uv")
    assert plan["venv"] == "~/.memory-os/venv" and plan["venv_path"] == "/Users/some one/.memory-os/venv"
    assert plan["commands"][1][-1] == "/Users/some one/.memory-os/venv" and plan["commands"][0][0] == "/Users/some one/my tools/uv"
    assert not any(part.startswith("~") for command in plan["commands"] for part in command)
    assert [shlex.split(line) for line in plan["command_lines"]] == plan["commands"]
    assert "'/Users/some one/.memory-os/venv'" in plan["command_lines"][1]
    assert plan["request"] == "cpython-3.12.16-macos-aarch64-none" and plan["build"] == "20261110"
    message = sqlite_runtime.attestation({"ok": False, "code": "sqlite_runtime_too_old", "version": "3.50.4",
                                          "minimum": "3.51.3"}, python="/x/python", cell_id="macos-arm64-py312",
                                         built_by="other")["message"]
    assert "'/Users/some one/.memory-os/venv'" in message


def test_probe_target_executes_the_target_and_never_guesses():
    seen = sqlite_runtime.probe_target(sys.executable)
    assert seen["ok"] == sqlite_runtime.inspect()["ok"] and seen["version"] == sqlite3.sqlite_version
    assert seen["probed"] == sys.executable and seen["target"]["python"] == ".".join(map(str, sys.version_info[:3]))
    assert seen["checks"] == sqlite_runtime.inspect()["checks"] and seen["writes"] == "none"

    def answering(text, code=0):
        def run(argv, **kwargs):
            assert argv[1:5] == ["-I", "-S", "-B", "-c"] and kwargs["timeout"] == sqlite_runtime.PROBE_TIMEOUT
            return SimpleNamespace(returncode=code, stdout=text, stderr="")
        return run
    base = {"executable": "/v/bin/python", "base_prefix": "/b", "python": [3, 12, 16], "implementation": "cpython",
            "sys_version": "3.12.16", "standalone": True, "build": "20261110", "sqlite": [3, 50, 4], "json1": True,
            "wal_compiled": True, "fts5": True, "loaded": True}
    old = sqlite_runtime.probe_target("/v/bin/python", run=answering(json.dumps(base)))
    assert (old["ok"], old["code"], old["version"]) == (False, "sqlite_runtime_too_old", "3.50.4")
    fit = sqlite_runtime.probe_target("/v/bin/python", run=answering(json.dumps({**base, "sqlite": [3, 53, 1]})))
    assert fit["ok"] and fit["target"]["build"] == "20261110" and fit["target"]["standalone"] is True
    for run in (answering("not json"), answering(json.dumps(base), code=1), answering(json.dumps({"python": [3]})),
                answering("")):
        refused = sqlite_runtime.probe_target("/v/bin/python", run=run)
        assert (refused["ok"], refused["code"], refused["target"]) == (False, "sqlite_runtime_unattested", None)

    def missing(argv, **kwargs):
        raise FileNotFoundError(argv[0])
    assert sqlite_runtime.probe_target("/absent/python", run=missing)["code"] == "sqlite_runtime_unattested"


HERE = ".".join(map(str, sys.version_info[:3]))


def probed(python="3.12.16", sqlite="3.53.1", *, build="20261110", standalone=True, path="/v/bin/python") -> dict:
    version = tuple(int(part) for part in sqlite.split("."))
    ok = version >= MIN_SQLITE
    return {"ok": ok, "code": None if ok else "sqlite_runtime_too_old", "version": sqlite, "minimum": "3.51.3",
            "checks": {"loaded": True, "version": ok, "json1": True, "wal_compiled": True}, "fts5": True,
            "writes": "none", "probed": path,
            "target": {"executable": path, "base_prefix": "/managed", "python": python, "implementation": "cpython",
                       "standalone": standalone, "build": build, "sys_version": python}}


@pytest.mark.parametrize("report,ok,code,state", [
    (probed(), True, None, "attested"),
    (probed(build=None), False, "standalone_build_unattested", "unattested"),
    (probed(build="20250101"), False, "standalone_build_mismatch", "mismatch"),
    (probed(python="3.12.13"), False, "standalone_python_mismatch", "attested"),
    (probed(sqlite="3.50.4"), False, "sqlite_runtime_too_old", "attested"),
    (probed(standalone=False), False, "standalone_not_python_build_standalone", "attested"),
])
def test_standalone_check_pins_version_sqlite_and_build(report, ok, code, state):
    checked = sqlite_runtime.standalone_check(report, "macos-arm64-py312")
    assert (checked["ok"], checked["code"], checked["build_state"]) == (ok, code, state)
    assert checked["expected"] == {"python": "3.12.16", "build": "20261110", "sqlite_minimum": "3.51.3"}
    assert sqlite_runtime.standalone_check(report, "macos-arm64-py311")["code"] == "standalone_not_named"


@pytest.fixture
def c(tmp_path, monkeypatch):
    return setup_context(tmp_path, monkeypatch)


def test_the_plan_attests_the_target_python_not_the_running_command(c, monkeypatch):
    """Reservation (c): ``python`` differs from this interpreter, so it is probed and speaks for itself."""
    as_cell(monkeypatch, RETAINED["macos-arm64-py312"]())
    lock_everything(monkeypatch)
    seen = []

    def probe(python, **kwargs):
        seen.append(python)
        return probed(path=python)
    monkeypatch.setattr(sqlite_runtime, "probe_target", probe)
    plan = prepare(c, profile="lexical")
    attested = plan["sqlite_attestation"]
    assert seen == [c.python] and plan["ok"] is True, plan["blockers"]
    assert attested["interpreter"] == {"version": "3.53.1", "ok": True, "code": None, "executable": c.python,
                                       "target_python": c.python, "read_by": "target", "python": "3.12.16"}
    assert attested["route"] == "i" and attested["provider"] == "python-build-standalone"
    assert attested["command_interpreter"]["version"] == sqlite3.sqlite_version
    assert attested["command_interpreter"]["executable"] == sys.executable
    assert plan["sqlite_runtime_target"]["version"] == "3.53.1" and plan["sqlite_runtime"]["version"] == sqlite3.sqlite_version
    assert qs.report(plan)["sqlite_attestation"]["interpreter"] == {"version": "3.53.1", "ok": True, "code": None,
                                                                    "read_by": "target", "python": "3.12.16"}


def test_the_cell_is_the_one_of_the_target_python(c, monkeypatch):
    monkeypatch.setattr(sqlite_runtime, "probe_target", lambda python, **kw: probed(python="3.10.14", path=python))
    plan = prepare(c, profile="lexical")
    assert plan["cell"]["python"] == "3.10" and "runtime_unqualified" in plan["blocker_codes"]
    assert any("py310 is not one of the retained cells" in blocker for blocker in plan["blockers"])


@pytest.mark.parametrize("report,code,words", [
    (lambda path: probed(python=HERE, sqlite="3.50.4", path=path), "sqlite_runtime_too_old", "is unfit (version 3.50.4, minimum 3.51.3)"),
    (lambda path: {**probed(path=path), "ok": False, "code": "sqlite_runtime_unattested", "version": None, "target": None},
     "sqlite_runtime_unattested", "could not be read back by executing it"),
])
def test_an_unfit_or_unreadable_target_is_refused_even_if_this_command_is_fit(c, monkeypatch, report, code, words):
    monkeypatch.setattr(sqlite_runtime, "probe_target", lambda python, **kw: report(python))
    before = tree(c.root)
    plan = prepare(c, profile="lexical")
    assert plan["ok"] is False and plan["blocker_codes"] == [code]
    assert f"SQLite loaded by the target interpreter {c.python} {words}" in plan["blockers"][0]
    assert "nothing written" in plan["blockers"][0] and plan["sqlite_runtime"]["ok"] is True
    assert plan["sqlite_attestation"]["route"] == "none" and plan["sqlite_attestation"]["interpreter"]["read_by"] == "target"
    assert tree(c.root) == before


def test_the_running_interpreter_is_not_probed_when_it_is_the_target(c, monkeypatch):
    def forbidden(python, **kwargs):
        raise AssertionError("no process for the interpreter running the command")
    monkeypatch.setattr(sqlite_runtime, "probe_target", forbidden)
    plan = qs.prepare(c.settings, home=c.home, python=sys.executable, clients=("cursor",), run=c.system, profile="lexical")
    assert plan["sqlite_attestation"]["interpreter"]["read_by"] == "command" and "sqlite_runtime_target" not in plan
    assert plan["sqlite_attestation"]["interpreter"]["python"] == ".".join(map(str, sys.version_info[:3]))


def test_a_failed_apply_report_still_carries_both_sqlite_values(c):
    """Reservation (d): the attestation is in the journal from the first record, failures included."""
    c.system.bootstrap_fails = True
    result = install(c, profile="lexical")
    assert result["ok"] is False and result["code"]
    attested = result["sqlite_attestation"]
    assert attested["interpreter"]["version"] == sqlite3.sqlite_version
    assert attested["server"] == {"version": None, "ok": None, "state": "not_running", "code": None}
    journal = json.loads((c.home / qs.JOURNAL_SUBDIR / result["stamp"] / "install.json").read_text())
    assert journal["sqlite_attestation"] == attested


def test_a_server_refused_at_readiness_is_said_in_the_failure_report(c, monkeypatch):
    """The REAL readiness check refuses; the report keeps the very version the server published."""
    from memory_os_v4.config import LEXICAL_MARKER, LEXICAL_POLICY
    from memory_os_v4.ops import auth
    plan = prepare(c, profile="lexical")
    body = {"commit": c.identity["commit"], "version": c.identity["version"], "ready": True, "reason": "ready",
            "capabilities": {"store": {"backend": "sqlite_s1", "instance_id": "synthetic", "schema_version": 8,
                                       "sqlite_version": "3.50.4"},
                             "work_state": True, "retrieval_profile": LEXICAL_MARKER,
                             "policies": {"ranking_policy": LEXICAL_POLICY, "bm25_profile": "scoped_literal",
                                          "lexical_guard_k": 3},
                             "encoding": {"state": "absent_by_profile", "profile": None},
                             "brief": {"tokenizer": {"ready": True}}}}
    monkeypatch.setattr(auth, "http_get", lambda *a, **k: (200, json.dumps(body)))
    monkeypatch.setattr(qs, "_ready", REAL_READY)
    result = qs.apply_install(plan, c.settings, home=c.home, run=c.system, clock=c.clock)
    assert result["ok"] is False and result["code"] == "sqlite_runtime_too_old"
    assert result["sqlite_attestation"]["server"] == {"version": "3.50.4", "ok": False, "state": "too_old",
                                                      "code": "sqlite_runtime_too_old"}
    assert qs.attestation_summary(result["sqlite_attestation"])["server"]["version"] == "3.50.4"
    journal = json.loads((c.home / qs.JOURNAL_SUBDIR / result["stamp"] / "install.json").read_text())
    assert journal["sqlite_attestation"]["server"]["version"] == "3.50.4"


def test_the_cli_refusal_and_reports_carry_the_attestation(c, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    plan = prepare(c, profile="lexical")
    summary = qs.attestation_summary(plan["sqlite_attestation"])
    assert summary["interpreter"]["version"] == sqlite3.sqlite_version and summary["server"]["state"] == "not_running"
    assert not any("/" in str(value) for part in summary.values() if isinstance(part, dict) for value in part.values())
    assert qs.attestation_summary(None) is None
    blocked = {**plan, "ok": False, "blockers": ["synthetic"], "blocker_codes": ["synthetic"]}
    monkeypatch.setattr(qs, "prepare", lambda *a, **kw: blocked)
    monkeypatch.setattr(qs, "settings_for_install", lambda s, backend=None: (s, "sqlite"))
    args = SimpleNamespace(undo=None, clients="cursor", backend=None, profile="lexical", replace_existing=None,
                           apply=True, report=False, download_approved=False, demo=False)
    assert cli.cmd_quickstart(args, c.settings) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is False and printed["code"] == "preflight_refused" and printed["sqlite_attestation"] == summary

    def failing(*a, **kw):
        raise qs.QuickstartError("synthetic refusal inside prepare", code="client_unknown")
    monkeypatch.setattr(qs, "prepare", failing)  # no plan at all: the report still says what this interpreter reads
    assert cli.cmd_quickstart(args, c.settings) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["code"] == "client_unknown" and printed["sqlite_attestation"]["server"]["state"] == "not_running"
    assert printed["sqlite_attestation"]["interpreter"] == {"version": sqlite3.sqlite_version, "ok": True, "code": None,
                                                            "read_by": "command", "python": HERE}


# ---------------------------------------------------------------- destinations: nothing overwritten, no link followed

def test_an_existing_work_directory_is_refused_and_its_files_are_never_truncated(m):
    work = m.home / "work"
    work.mkdir()
    victim = work / f"requirements-{CELL}-lexical.txt"
    victim.write_text("user data that an O_TRUNC would have destroyed")
    before = tree(m.home)
    code, report = m.main("--profile", "lexical", "--work-dir", str(work), "--apply")
    assert code == 1 and report["blocker_codes"] == ["work_dir_exists"] and tree(m.home) == before
    assert m.verbs() == ["--version"] and victim.read_text().startswith("user data")


def test_write_new_never_truncates_follows_or_shares(tmp_path):
    target = tmp_path / "new.txt"
    bs.write_new(str(target), "first")
    assert target.read_text() == "first" and stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        bs.write_new(str(target), "second")
    assert target.read_text() == "first"
    outside = tmp_path / "outside.txt"
    outside.write_text("kept")
    for link in (tmp_path / "symlink.txt", tmp_path / "dangling.txt"):
        link.symlink_to(outside if link.name == "symlink.txt" else tmp_path / "nowhere")
        with pytest.raises(OSError):
            bs.write_new(str(link), "redirected")
    hard = tmp_path / "hard.txt"
    os.link(outside, hard)
    with pytest.raises(FileExistsError):
        bs.write_new(str(hard), "redirected")
    assert outside.read_text() == "kept" and not (tmp_path / "nowhere").exists()


@pytest.mark.parametrize("option", ["--venv", "--work-dir"])
def test_a_symbolic_link_on_the_way_to_a_destination_is_refused(m, option):
    real = m.root / "elsewhere"
    real.mkdir()
    (m.home / "link").symlink_to(real, target_is_directory=True)
    before = tree(m.root)
    code, report = m.main("--profile", "lexical", option, str(m.home / "link" / "target"), "--apply")
    assert code == 1 and set(report["blocker_codes"]) == {"destination_unsafe"}
    assert str(m.home / "link") + " is a symbolic link" in report["blockers"][0]
    m.log.unlink()
    assert tree(m.root) == before and list(real.iterdir()) == []


def test_path_problem_names_links_files_and_accepts_what_does_not_exist_yet(tmp_path):
    home = tmp_path / "home"
    (home / "real" / "deep").mkdir(parents=True)
    (home / "file").write_text("x")
    (home / "link").symlink_to(home / "real")
    assert bs.path_problem(str(home / "real" / "deep" / "new" / "newer"), str(home)) is None
    assert bs.path_problem(str(home / "real" / "deep"), str(home)) is None
    assert "is a symbolic link" in bs.path_problem(str(home / "link" / "deep"), str(home))
    assert "is not a directory" in bs.path_problem(str(home / "file" / "below"), str(home))
    os.link(home / "file", home / "twin")
    assert "several hard links" in bs.path_problem(str(home / "file"), str(home))
    assert bs.path_problem(str(home), str(home)) is None


def test_a_venv_that_appears_after_the_preflight_is_never_handed_to_uv(m, monkeypatch):
    venv = m.home / ".memory-os" / "venv"
    real = bs.write_new

    def racing(path, text):
        real(path, text)
        venv.mkdir(parents=True, exist_ok=True)  # someone creates it between the preflight and uv venv
        (venv / "theirs").write_text("kept")
    monkeypatch.setattr(bs, "write_new", racing)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["code"] == "venv_exists" and "uv venv was not run" in report["error"]
    assert m.verbs() == ["--version"] and (venv / "theirs").read_text() == "kept" and report["steps"] == []
    assert report["sqlite_attestation"]["venv"]["state"] == "not_created"


def test_a_work_directory_that_appears_after_the_preflight_is_not_reused(m, monkeypatch):
    real = bs.child_environment
    state = {}

    def racing(environ):
        if "plan" in state:
            os.makedirs(state["plan"], exist_ok=True)
        return real(environ)
    original = bs.build_plan

    def spy(*args, **kwargs):
        plan = original(*args, **kwargs)
        state["plan"] = plan["work_dir"]
        return plan
    monkeypatch.setattr(bs, "build_plan", spy)
    monkeypatch.setattr(bs, "child_environment", racing)
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 1 and report["code"] == "work_dir_exists" and report["report_path"] is None
    assert os.listdir(state["plan"]) == [] and m.verbs() == ["--version"]


def test_a_destination_that_changes_during_the_preview_invalidates_it(m, monkeypatch):
    real, calls = bs._inventory, []

    def changing(paths):
        calls.append(1)
        seen = real(paths)
        if len(calls) == 2:
            seen[0][3] = 12345  # the venv destination "changed" between the two inventories
        return seen
    monkeypatch.setattr(bs, "_inventory", changing)
    code, report = m.main("--profile", "lexical")
    assert code == 1 and report["ok"] is False and report["blocker_codes"] == ["preview_destination_changed"]
    assert report["no_write_check"]["identical"] is False and report["venv"] in report["blockers"][0]
    assert report["writes"].startswith("CHANGE DETECTED") and report["venv"] in report["writes"]
    assert report.get("next") is None


def test_the_probe_of_a_target_loads_neither_pth_nor_sitecustomize(m):
    """``-I -S``: a .pth of the probed venv would otherwise run before the probe says anything."""
    code, report = m.main("--profile", "lexical", "--apply")
    assert code == 0, report.get("error")
    venv = Path(report["venv"])
    site = next((venv / "lib").glob("python*/site-packages"))
    witness = m.root / "pth-ran"
    (site / "zz_witness.pth").write_text(f"import pathlib; pathlib.Path({str(witness)!r}).write_text('ran')\n")
    probe = bs.probe_python(str(venv / "bin" / "python"), run=subprocess.run, environ=m.environ)
    assert probe["ok"] and probe["sqlite"] == sqlite3.sqlite_version and not witness.exists()
    assert sqlite_runtime.probe_target(str(venv / "bin" / "python"))["version"] == sqlite3.sqlite_version
    assert not witness.exists()
