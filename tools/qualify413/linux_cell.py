#!/usr/bin/env python3
"""4.1.3 Lot 3 (decision D6): gates P1 and P3-P7 of a Linux lexical cell, on a disposable GitHub-hosted runner.

Transposed from the 4.1.1 qualification harness (``qual411/harness/portes.py``, run then on a Lima VM). The judgements
are the same; the means are those of a runner where there is no production to observe:

* P1  portable lexical suite ``v4/tests`` from the SOURCES (``PYTHONPATH=v4/src:v4``, probed: package of the
      checkout, distribution not installed, no ``MEMORY_V4_BUILD_COMMIT``), in a venv of the cell interpreter holding
      the exact cell lock and the test pins (``scripts/qualify_410_pytest_pins.txt``); the audited selection of the
      4.1.1 P1 gate ported to this tree (``tools/qualify413/p1_deselection_<cell>.json``: file ignores, node
      deselects, cell section, expected skips; every entry motivated); 0 failure, 0 error, only listed skips.
* P3  bootstrap: a copy of the template OUTSIDE the checkout receives a TEST pin through its own ``fill_pin`` (wheel of
      this run, lock of the cell from the wheel's manifest); previews and expected refusals only, WITHOUT network
      (``unshare -n``; a connection attempt is shown to fail), inventory of the whole P3 root identical before/after.
* P4  real install outside the checkout: package under ``site-packages``, no PYTHONPATH, ``find_spec`` of the ML = None,
      versions = lock, no extra package, ``doctor`` 0 FAIL, client configurations attested.
* P5  service under a real ``systemd --user``: ``quickstart --apply`` (test label and port, never 8765), then
      ``uninstall --apply``: stop attested, port free, unit removed, created client files removed, DATA KEPT,
      journals append-only (revocations excepted).
* P6  fresh lexical store on frozen data (``tools/qualify413/p6_worker.py``): write, search, reopen, backup, restore,
      identical rankings at the three moments, PRAGMA read back.
* P7  installed lexical relay (``scripts/installed_relay_case.py``): connection, outage, no replay, reconnection.

The runner is disposable: P4/P5 use its real HOME (``systemd --user`` reads units there) and ``loginctl enable-linger``
for its user; nothing of an operator machine exists there. Output: ``<output>/report.json`` with every check and the
SHA-256 of the artifacts and logs, to be linked to the P9 manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
ML_MODULES = ("torch", "sentence_transformers", "transformers", "faiss")
PRAGMAS = {"journal_mode": "wal", "synchronous": 2, "fullfsync": 1, "checkpoint_fullfsync": 1, "foreign_keys": 1}
RELAY_KEYS = ("installed", "ready", "roundtrip", "failure_reported", "reconnected", "no_replay", "clean_shutdown",
              "local_status_during_outage")
TEST_CLIENTS = ("cursor", "gemini-cli", "codex")
TEST_LABEL = "local.memory-os-qual413"
ALL_GATES = ("P1", "P3", "P4", "P5", "P6", "P7")
CELLS = ("ubuntu2404-arm64-py311", "ubuntu2404-x86_64-py311")

# ---------------------------------------------------------------- P1 (shared with the local replay and the tests)
# Port of the 4.1.1 P1 gate (qual411/harness/portes.py: VARIABLES_P1, SONDE_P1, p1, evaluer_junit). The list keeps the
# keys of p1_deselection_v7.json (fichier, noeud, categorie, motif, preuve, couverture, raison) so that every ported
# entry can be compared with its audited original.
P1_FORMAT = "qualify413-p1-deselection-v1"
P1_VARIABLES = {"MEMORY_TEST_HASH_V3": "1", "MEMORY_V4_EMBED_MODEL": "hash", "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "PYTHONOPTIMIZE": "0",
                "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
P1_PROBE = ("import importlib.metadata as md, json, os, memory_os_v4\n"
            "try:\n md.version('memory-os-ai'); installed = True\n"
            "except md.PackageNotFoundError:\n installed = False\n"
            "print(json.dumps({'package': memory_os_v4.__file__, 'distribution_installed': installed,\n"
            " 'pythonpath': os.environ.get('PYTHONPATH'), 'build_commit': os.environ.get('MEMORY_V4_BUILD_COMMIT')}))\n")
P1_REQUIRED = {"ignore": ("fichier", "categorie", "motif", "couverture"),
               "deselect": ("noeud", "categorie", "motif", "preuve", "couverture"),
               "sauts_prevus": ("fichier", "raison", "motif")}


def p1_list_problems(listed, cell) -> list:
    """Why a P1 list cannot be used for ``cell`` (empty: usable). Every entry is motivated: a known category, a
    ``motif``, the observed error line (``preuve``) for a node and where the behaviour IS covered (``couverture``)."""
    problems = []
    if not isinstance(listed, dict) or listed.get("format") != P1_FORMAT:
        return [f"format is not {P1_FORMAT}"]
    if listed.get("cellule") != cell:
        problems.append(f"list of cell {listed.get('cellule')!r}, not {cell}")
    categories = listed.get("categories")
    if not isinstance(categories, dict) or not all(isinstance(v, str) and v for v in categories.values()):
        return problems + ["categories: a non-empty description per code is required"]
    cells = listed.get("par_cellule", {})
    if not isinstance(cells, dict) or set(cells) - set(CELLS):
        problems.append("par_cellule: unknown cell")
        cells = {}
    sections = [("lexicale", listed.get("lexicale", {}))] + [(f"par_cellule.{c}", s) for c, s in cells.items()]
    seen = set()
    for where, section in sections:
        if not isinstance(section, dict) or set(section) - {"ignore", "deselect"}:
            problems.append(f"{where}: only 'ignore' and 'deselect' are admitted")
            continue
        for kind in ("ignore", "deselect"):
            for entry in section.get(kind, []):
                key = entry.get("fichier" if kind == "ignore" else "noeud") if isinstance(entry, dict) else None
                if not (isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry[k].strip()
                                                        for k in P1_REQUIRED[kind])):
                    problems.append(f"{where}.{kind}: entry not motivated: {entry!r}"[:300])
                elif entry["categorie"] not in categories:
                    problems.append(f"{where}.{kind}: unknown category {entry['categorie']!r} for {key}")
                elif (kind == "ignore" and (".." in key or not key.endswith(".py"))) or \
                        (kind == "deselect" and (not key.startswith("tests/") or ".." in key or "::" not in key)):
                    problems.append(f"{where}.{kind}: not a v4/tests path: {key}")
                elif key in seen:
                    problems.append(f"{where}.{kind}: listed twice: {key}")
                seen.add(key)
    for entry in listed.get("sauts_prevus", []):
        if not (isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry[k].strip()
                                                for k in P1_REQUIRED["sauts_prevus"])):
            problems.append(f"sauts_prevus: entry not motivated: {entry!r}"[:300])
    return problems


def p1_selection(listed, cell) -> list:
    """The pytest options of the list, as the 4.1.1 gate built them: ``--ignore`` per file of ``v4/tests``,
    ``--deselect`` per node (common section, then the section of the cell)."""
    common = listed.get("lexicale", {})
    own = listed.get("par_cellule", {}).get(cell, {})
    return ([f"--ignore=v4/tests/{e['fichier']}" for e in common.get("ignore", []) + own.get("ignore", [])]
            + [f"--deselect=v4/{e['noeud']}" for e in common.get("deselect", []) + own.get("deselect", [])])


def p1_environment(venv: Path, home: Path, root: Path = ROOT) -> dict:
    """The P1 environment, rebuilt from nothing (never inherited): the sources of the checkout on PYTHONPATH, as in
    the 4.1.1 gate and the v9 "full" gate (``scripts/qualify_405_test_isolation.py``); never MEMORY_V4_BUILD_COMMIT."""
    return {"HOME": str(home), "PATH": f"{venv}/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "TZ": "Europe/Paris",
            "TMPDIR": "/tmp", "PYTHONPATH": os.pathsep.join(str(root / p) for p in ("v4/src", "v4")), **P1_VARIABLES}


def p1_workdir(root: Path, name: str):
    """``--basetemp`` and JUnit paths under ``<repo>/.local-qualification``, which a fresh checkout does not have
    (pytest creates the basetemp itself but not its parent: local replay from a depth-1 clone)."""
    free = root / ".local-qualification"
    free.mkdir(mode=0o700, exist_ok=True)
    return free / name, free / f"{name}-junit.xml"


def p1_pytest_argv(python: Path, listed, cell, basetemp: Path, junit: Path) -> list:
    return [str(python), "-B", "-m", "pytest", "-c", "pyproject.toml", "-q", "-p", "no:cacheprovider",
            "-o", "faulthandler_timeout=60", f"--basetemp={basetemp}", f"--junitxml={junit}", "-rs", "v4/tests",
            "-m", "not slow", *p1_selection(listed, cell)]


def p1_probe_ok(probe: dict, env: dict, root: Path = ROOT) -> bool:
    package = Path(probe.get("package") or "/").resolve()
    return (package.is_relative_to((root / "v4/src").resolve()) and probe.get("distribution_installed") is False
            and probe.get("build_commit") is None and probe.get("pythonpath") == env["PYTHONPATH"])


def judge_p1_junit(junit: Path, expected_skips) -> dict:
    """0 failure, 0 error, and every skip listed (file + reason), otherwise blocking (4.1.1 ``evaluer_junit``)."""
    cases = list(ET.parse(junit).getroot().iter("testcase"))
    failures, errors, expected, blocking = [], [], [], []
    for case in cases:
        name = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None:
            failures.append(name)
        if case.find("error") is not None:
            errors.append(name)
        skip = case.find("skipped")
        if skip is not None:
            reason = ((skip.get("message") or "") + " " + (skip.text or "")).strip()
            module = next((part for part in (case.get("classname") or "").split(".") if part.startswith("test_")), "")
            known = any(e["fichier"].rsplit("/", 1)[-1] == module + ".py" and e["raison"] in reason
                        for e in expected_skips)
            (expected if known else blocking).append({"test": name, "reason": reason[:200]})
    return {"tests": len(cases), "failures": failures[:50], "errors": errors[:50], "expected_skips": expected,
            "blocking_skips": blocking[:50], "n_failures": len(failures), "n_errors": len(errors),
            "n_blocking_skips": len(blocking), "ok": bool(cases) and not failures and not errors and not blocking}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root: Path, exclude=()) -> dict:
    """Every entry under ``root`` (path -> kind and SHA-256 of files, link targets): a write is a difference."""
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.split("/", 1)[0] in exclude:
            continue
        mode = oct(path.lstat().st_mode & 0o7777)
        if path.is_symlink():
            out[rel] = "link:" + os.readlink(path)
        elif path.is_file():
            out[rel] = f"file:{mode}:{sha256(path)}"
        elif path.is_dir():
            out[rel] = f"dir:{mode}"
    return out


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    if port in (8765, 8799):
        return free_port()
    return port


class Refused(RuntimeError):
    pass


class Run:
    def __init__(self, output: Path, cell: str, dist: Path, cpython: str, gates):
        self.output = output.resolve()
        self.cell, self.cpython, self.gates = cell, cpython, gates
        self.R = Path(os.environ.get("RUNNER_TEMP", "/tmp")).resolve() / "qual413" / cell
        if self.R.exists() or self.output.exists():
            raise Refused("fresh run root and output required; previous evidence is never overwritten")
        self.R.mkdir(parents=True, mode=0o700)
        (self.output / "logs").mkdir(parents=True)
        self.report = {"schema_version": 1, "tool": "tools/qualify413/linux_cell.py", "cell": cell,
                       "commit": os.environ.get("GITHUB_SHA"), "run_id": os.environ.get("GITHUB_RUN_ID"),
                       "runner": {"os": os.environ.get("RUNNER_OS"), "arch": os.environ.get("RUNNER_ARCH")},
                       "gates": {}, "artifacts": {}, "ok": False}
        self.dist = dist.resolve()
        self.current = None

    # ---------------------------------------------------------------- plumbing
    def check(self, name, ok, detail=None):
        entry = {"check": name, "ok": bool(ok), **({"detail": detail} if detail is not None else {})}
        self.report["gates"][self.current]["checks"].append(entry)
        print(("PASS " if ok else "FAIL ") + f"[{self.current}] {name}", flush=True)
        if not ok:
            raise Refused(f"{self.current}: {name}: {json.dumps(detail, default=str)[:2000]}")

    def step(self, name, argv, *, env=None, cwd=None, timeout=600, expect=(0,), sudo_netless=False):
        log = self.output / "logs" / f"{self.current}-{name}.log"
        argv = [str(part) for part in argv]
        if sudo_netless:
            # No network: a new network namespace (only a DOWN loopback), then back to the runner user.
            user = os.environ.get("USER") or os.getlogin()
            envs = [f"{k}={v}" for k, v in (env or {}).items()]
            argv = ["sudo", "-n", "unshare", "--net", "--", "sudo", "-n", "-u", user, "--", "env", "-i", *envs, *argv]
            env = None
        with open(log, "wb") as stream:
            completed = subprocess.run(argv, env=env, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                       timeout=timeout)
        self.report["gates"][self.current].setdefault("steps", {})[name] = {"rc": completed.returncode,
                                                                            "log_sha256": sha256(log)}
        if completed.returncode not in expect:
            raise Refused(f"{self.current}: step {name} exited {completed.returncode} (expected {expect}); "
                          f"see {log.name}: {log.read_text(errors='replace')[-1500:]}")
        return completed.returncode, log.read_text(errors="replace")

    @staticmethod
    def last_json(text):
        """The last JSON object or array printed by a command (logs may hold notices before it)."""
        decoder = json.JSONDecoder()
        found = None
        for start in [m.start() for m in re.finditer(r"(?m)^[\[{]", text)]:
            try:
                found, _ = decoder.raw_decode(text[start:])
            except ValueError:
                continue
        if found is None:
            raise Refused("no JSON document in the output: " + text[-500:])
        return found

    # ---------------------------------------------------------------- common setup
    def setup(self):
        self.current = "setup"
        self.report["gates"]["setup"] = {"checks": []}
        sums = (self.dist / "SHA256SUMS").read_text().split("\n")
        declared = {line.split()[1]: line.split()[0] for line in sums if line.strip()}
        wheels = sorted(self.dist.glob("memory_os_ai-*-py3-none-any.whl"))
        sdists = sorted(self.dist.glob("memory_os_ai-*.tar.gz"))
        self.check("one wheel and one sdist of this run", len(wheels) == 1 and len(sdists) == 1,
                   [p.name for p in self.dist.iterdir()])
        self.wheel, self.sdist = wheels[0], sdists[0]
        for artifact in (self.wheel, self.sdist):
            self.report["artifacts"][artifact.name] = sha256(artifact)
        self.check("artifacts match SHA256SUMS of the build job",
                   all(declared.get(name) == digest for name, digest in self.report["artifacts"].items()),
                   {"declared": declared, "measured": self.report["artifacts"]})
        self.version = self.wheel.name.split("-")[1]
        self.uv = shutil.which("uv")
        rc, out = self.step("uv-version", [self.uv, "--version"])
        self.check("uv 0.13.9 (the resolver of the frozen locks)", out.split()[1] == "0.13.9", out.strip())
        self.step("cpython-install", [self.uv, "python", "install", self.cpython], timeout=900)
        rc, out = self.step("cpython-find", [self.uv, "python", "find", self.cpython])
        self.python = Path(out.strip().splitlines()[-1])
        rc, out = self.step("cpython-sqlite", [self.python, "-I", "-c",
                                               "import json,platform,sqlite3,sys; print(json.dumps({'python': "
                                               "platform.python_version(), 'impl': platform.python_implementation(), "
                                               "'sqlite': sqlite3.sqlite_version}))"])
        facts = self.last_json(out)
        loaded = tuple(int(x) for x in facts["sqlite"].split("."))
        self.check(f"CPython {self.cpython} with SQLite >= 3.51.3", facts["python"] == self.cpython
                   and facts["impl"] == "CPython" and loaded >= (3, 51, 3), facts)
        self.report["interpreter"] = {**facts, "path": str(self.python)}
        with zipfile.ZipFile(self.wheel) as archive:
            manifest_name = f"memory_os_v4/resources/qualified_runtime_manifest_lexical_{self.cell}_v1.json"
            constraints_name = f"memory_os_v4/resources/qualified_runtime_constraints_lexical_{self.cell}_v1.txt"
            names = set(archive.namelist())
            self.check(f"the wheel embeds the lock of {self.cell}", {manifest_name, constraints_name} <= names,
                       sorted(n for n in names if self.cell in n))
            self.manifest = json.loads(archive.read(manifest_name))
            self.constraints_text = archive.read(constraints_name).decode()
        self.constraints = self.R / "cell-constraints.txt"
        self.constraints.write_text(self.constraints_text)

    def venv(self, name) -> Path:
        path = self.R / name
        self.step(f"venv-{name}", [self.uv, "venv", "--python", self.python, path])
        return path

    def uv_install(self, venv: Path, name, *requirements, hashes=False, extra=()):
        argv = [self.uv, "pip", "install", "--python", venv / "bin/python", "--only-binary", ":all:", *extra]
        if hashes:
            argv += ["--require-hashes"]
        self.step(f"install-{name}", [*argv, *requirements], timeout=900,
                  env={**os.environ, "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never"})

    # ---------------------------------------------------------------- P1
    def hashed_lock(self) -> Path:
        """The cell lock with the SHA-256 of every admitted wheel (from the wheel's frozen manifest), for
        ``--require-hashes``: the embedded constraints file carries versions only."""
        lines = []
        for item in self.manifest["artifacts"]:
            hashes = " ".join(f"--hash=sha256:{wheel['sha256']}" for wheel in item["wheels"])
            lines.append(f"{item['name']}=={item['version']} {hashes}")
        path = self.R / "cell-lock-hashed.txt"
        path.write_text("\n".join(lines) + "\n")
        return path

    def p1(self):
        # Not "*venv": the fake interpreter of test_bootstrap_411.py answers for any path ending in "venv/bin/python"
        # (rewrite suffix, :207/:781), hence also for the base interpreter of the suite (local replay of CI run 1).
        venv = self.venv("p1-lexical")
        self.uv_install(venv, "lock", "-r", self.hashed_lock(), hashes=True)
        # setuptools and wheel: the build backend that the distribution tests import (memory_os_build.py). The
        # 4.1.1 harness got them from the seeding of a CPython 3.11 venv; `uv venv` seeds nothing (CI run 1).
        self.uv_install(venv, "test-pins", "-r", ROOT / "scripts/qualify_410_pytest_pins.txt", "pytest-asyncio==1.3.0",
                        "ruff==0.17.6", "setuptools==80.9.0", "wheel==0.48.0")
        path = ROOT / f"tools/qualify413/p1_deselection_{self.cell}.json"
        if not path.is_file():
            raise Refused(f"P1: no selection list {path.name} (the 4.1.1 gate refused without one)")
        listed = json.loads(path.read_text())
        problems = p1_list_problems(listed, self.cell)
        own = listed.get("par_cellule", {}).get(self.cell, {})
        self.report["gates"]["P1"]["selection"] = {
            "file": path.name, "sha256": sha256(path), "origine": listed.get("origine"),
            "ignored_files": len(listed.get("lexicale", {}).get("ignore", [])) + len(own.get("ignore", [])),
            "deselected_nodes": len(listed.get("lexicale", {}).get("deselect", [])) + len(own.get("deselect", [])),
            "of_the_cell": len(own.get("deselect", [])), "expected_skips": len(listed.get("sauts_prevus", []))}
        self.report["artifacts"][path.name] = sha256(path)
        self.check(f"selection list {P1_FORMAT}: list of this cell, known categories, every entry motivated",
                   not problems, problems[:20])
        # The test write guard (scripts/test_home_guard.py) admits, under the operator's real HOME, only the session
        # HOME and <repo>/.local-qualification; on the runner the checkout and --output are under /home/runner, so
        # the JUnit file is written there first, then copied into the report (CI run 1).
        basetemp, junit_written = p1_workdir(ROOT, f"p1-{self.cell}")
        junit = self.output / "p1-junit.xml"
        home = self.R / "p1-home"
        home.mkdir()
        env = p1_environment(venv, home)
        for name, argv in (("lint-correctness", ["tests", "v4/src", "v4/tests", "v4/bench", "scripts", "--select", "E9,F"]),
                           ("lint-default", ["tests", "scripts"])):
            self.step(name, [venv / "bin/ruff", "check", "--no-cache", *argv], env=env, cwd=ROOT)
        rc, out = self.step("probe", [venv / "bin/python", "-B", "-c", P1_PROBE], env=env, cwd=ROOT)
        probe = self.last_json(out)
        self.check("the package imported is that of the SOURCES of the checkout; the distribution is not installed; "
                   "no MEMORY_V4_BUILD_COMMIT", p1_probe_ok(probe, env), probe)
        argv = p1_pytest_argv(venv / "bin/python", listed, self.cell, basetemp, junit_written)
        rc, _ = self.step("suite", argv, env=env, cwd=ROOT, timeout=2400, expect=(0, 1))
        if not junit_written.is_file():
            raise Refused(f"P1: pytest wrote no JUnit report (rc={rc}); see logs/P1-suite.log")
        shutil.copyfile(junit_written, junit)
        verdict = judge_p1_junit(junit, listed.get("sauts_prevus", []))
        self.report["gates"]["P1"].update(tests=verdict["tests"], expected_skips=verdict["expected_skips"])
        self.report["artifacts"]["p1-junit.xml"] = sha256(junit)
        self.check("portable lexical suite from the sources: pytest exit 0, 0 failure, 0 error",
                   rc == 0 and verdict["tests"] and not verdict["n_failures"] and not verdict["n_errors"],
                   {"rc": rc, **{k: verdict[k] for k in ("tests", "n_failures", "n_errors", "failures", "errors")}})
        self.check("every skip is listed with its reason (an unexpected skip blocks)", not verdict["n_blocking_skips"],
                   verdict["blocking_skips"])
        shutil.rmtree(basetemp, ignore_errors=True)

    # ---------------------------------------------------------------- P3
    def p3(self):
        d = self.R / "p3"
        for sub in ("template", "pinned", "home", "logs-unused"):
            (d / sub).mkdir(parents=True, mode=0o700)
        template, pinned, home = d / "template/memory_os_bootstrap.py", d / "pinned/memory_os_bootstrap.py", d / "home"
        shutil.copyfile(ROOT / "scripts/memory_os_bootstrap.py", template)
        lock = {"constraints": sorted(f"{a['name']}=={a['version']}" for a in self.manifest["artifacts"]),
                "artifacts": [{"name": a["name"], "version": a["version"], "filename": w["filename"], "url": w["url"],
                               "sha256": w["sha256"], "size": w["size"]}
                              for a in self.manifest["artifacts"] for w in a["wheels"]]}
        partial = {"schema_version": 1, "state": "test", "package": "memory-os-ai", "version": self.version,
                   "wheel": {"filename": self.wheel.name, "sha256": self.report["artifacts"][self.wheel.name],
                             "size": self.wheel.stat().st_size, "content_sha256": "0" * 64,
                             "source": "artifact of this CI run (qualification, test state)"},
                   "cells": {self.cell: {"lexical": lock}}}
        (d / "partial-pin.json").write_text(json.dumps(partial))
        fill = ("import importlib.util, json, sys\n"
                "template, partial, wheel, out = sys.argv[1:5]\n"
                "spec = importlib.util.spec_from_file_location('bootstrap_template', template)\n"
                "b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)\n"
                "pin = json.loads(open(partial).read())\n"
                "pin['wheel']['content_sha256'] = b.wheel_content_fingerprint(wheel)\n"
                "for cell, profiles in pin['cells'].items():\n"
                "    for profile, lock in profiles.items():\n"
                "        lock['lock_sha256'] = b.lock_fingerprint(cell, profile, lock)\n"
                "open(out, 'x').write(b.fill_pin(open(template).read(), pin))\n"
                "print(json.dumps({'state': pin['state'], 'wheel_sha256': pin['wheel']['sha256']}))\n")
        (d / "tmp").mkdir(mode=0o700)
        env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TMPDIR": str(d / "tmp")}
        rc, out = self.step("fill-pin", [self.python, "-I", "-B", "-c", fill, template, d / "partial-pin.json",
                                         self.wheel, pinned], env=env, sudo_netless=True)
        filled = self.last_json(out)
        self.check("TEST pin set by fill_pin of the template copy (wheel of this run)", filled["state"] == "test"
                   and filled["wheel_sha256"] == self.report["artifacts"][self.wheel.name]
                   and sha256(template) == sha256(ROOT / "scripts/memory_os_bootstrap.py"), filled)
        before = inventory(self.R)            # the WHOLE disposable root (logs are written outside it)

        def preview(name, script, *options, expect):
            rc, out = self.step(name, [self.python, "-I", "-B", script, "--uv", self.uv, "--venv", home / ".memory-os/venv",
                                       "--work-dir", home / ".memory-os/work", "--json", *options],
                                env=env, sudo_netless=True, expect=expect)
            return self.last_json(out)

        seen = preview("preview-lexical", pinned, "--profile", "lexical", "--python", self.python, expect=(0,))
        self.check(f"lexical preview accepted, cell {self.cell} detected, test pin, wheel of this run",
                   seen.get("ok") is True and seen.get("stage") == "preview" and not seen.get("blocker_codes")
                   and (seen.get("cell") or {}).get("cell_id") == self.cell
                   and (seen.get("release") or {}).get("state") == "test"
                   and ((seen.get("release") or {}).get("wheel") or {}).get("sha256") == self.report["artifacts"][self.wheel.name]
                   and (seen.get("lock") or {}).get("profile") == "lexical"
                   and (seen.get("no_write_check") or {}).get("identical") is True, seen.get("blocker_codes"))
        seen = preview("refusal-semantic", pinned, "--profile", "semantic", "--python", self.python, expect=(1,))
        self.check("expected refusal: semantic profile on this cell (profile_not_qualified_on_cell)",
                   seen.get("ok") is False and "profile_not_qualified_on_cell" in (seen.get("blocker_codes") or []),
                   seen.get("blocker_codes"))
        seen = preview("refusal-template-preview", template, "--profile", "lexical", "--python", self.python, expect=(1,))
        self.check("expected refusal: unfilled template (release_pin_unfilled)",
                   seen.get("ok") is False and "release_pin_unfilled" in (seen.get("blocker_codes") or []),
                   seen.get("blocker_codes"))
        seen = preview("refusal-template-apply", template, "--profile", "lexical", "--python", self.python, "--apply",
                       expect=(1,))
        self.check("expected refusal: unfilled template, --apply refused before any write",
                   seen.get("ok") is False and seen.get("code") == "preflight_refused"
                   and str(seen.get("writes") or "").startswith("none by the bootstrap"),
                   {"code": seen.get("code"), "writes": seen.get("writes")})
        after = inventory(self.R)
        changed = sorted(set(before) ^ set(after) | {k for k in before if k in after and before[k] != after[k]})
        self.check("no write: inventory of the whole disposable root (HOME and TMPDIR included) identical",
                   before == after, changed[:50])
        probe = ("import socket\ntry:\n    socket.create_connection(('1.1.1.1', 443), timeout=3)\n    print('CONNECTED')\n"
                 "except OSError as exc:\n    print('REFUSED', type(exc).__name__)\n")
        rc, out = self.step("netless-probe", [self.python, "-I", "-c", probe], env=env, sudo_netless=True)
        self.check("no network: under the same confinement a connection attempt fails", "REFUSED" in out
                   and "CONNECTED" not in out, out.strip())

    # ---------------------------------------------------------------- P4 + P5
    def install(self):
        venv = self.venv("venv")
        self.uv_install(venv, "wheel-no-deps", "--no-deps", self.wheel)
        rc, out = self.step("constraints-path", [venv / "bin/memory-os-constraints", "lexical"])
        located = Path(out.strip().splitlines()[-1])
        self.check("memory-os-constraints names the lock of this cell from the installed package",
                   located.name == f"qualified_runtime_constraints_lexical_{self.cell}_v1.txt"
                   and venv in located.parents and located.read_text() == self.constraints_text, str(located))
        self.uv_install(venv, "closure", "-c", located, f"memory-os-ai[ops,brief] @ file://{self.wheel}")
        return venv

    def p4(self):
        self.venv_path = venv = self.install()
        probe = ("import importlib.metadata as md, importlib.util, json, os, sys, memory_os_v4\n"
                 "from memory_os_v4.ops import qualified_runtime as q\n"
                 "installed = {d.metadata['Name']: d.version for d in md.distributions()}\n"
                 f"cell = {self.cell!r}\n"
                 "print(json.dumps({'package': memory_os_v4.__file__, 'prefix': sys.prefix,\n"
                 " 'pythonpath': os.environ.get('PYTHONPATH'),\n"
                 f" 'find_spec': {{m: importlib.util.find_spec(m) is not None for m in {ML_MODULES!r}}},\n"
                 " 'runtime': q.inspect_current(profile='lexical', extras=('ops', 'brief'), cell_id=cell),\n"
                 " 'extra': q.check_no_extra_packages(installed, profile='lexical', extras=('ops', 'brief'), cell_id=cell)}))\n")
        rc, out = self.step("probe", [venv / "bin/python", "-I", "-B", "-c", probe], cwd=self.R,
                            env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin"})
        facts = self.last_json(out)
        self.check("module under the venv's site-packages, without PYTHONPATH",
                   Path(facts["package"]).is_relative_to(venv) and "site-packages" in facts["package"]
                   and not facts["pythonpath"], facts["package"])
        self.check("find_spec of the ML = None", not any(facts["find_spec"].values()), facts["find_spec"])
        self.check("versions = lock of the cell", facts["runtime"].get("ok") is True, facts["runtime"].get("mismatches"))
        self.check("no extra package", facts["extra"].get("ok") is True and not facts["extra"].get("unexpected"),
                   facts["extra"])

    def user_systemd(self) -> dict:
        uid = os.getuid()
        self.step("enable-linger", ["sudo", "-n", "loginctl", "enable-linger", os.environ.get("USER") or os.getlogin()])
        bus = Path(f"/run/user/{uid}/bus")
        deadline = time.monotonic() + 60
        while not bus.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{uid}", "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}"}
        rc, out = self.step("user-manager", ["systemctl", "--user", "is-system-running"], env=env, expect=(0, 1))
        self.check("a real systemd --user manager (linger enabled on this disposable runner)",
                   bus.exists() and out.strip().splitlines()[-1] in ("running", "degraded"), out.strip())
        return env

    def p5(self):
        venv, home = self.venv_path, Path.home()
        port = free_port()
        admin = venv / "bin/memory-os-admin"
        store = self.R / "p5-store"          # ONE explicit disposable store for every command of the gate
        env = {**self.user_systemd(), "MEMORY_V4_URL": f"http://127.0.0.1:{port}/mcp/", "MEMORY_V4_DIR": str(store),
               "MEMORY_V4_SERVICE_LABEL": TEST_LABEL, "PATH": f"{venv}/bin:/usr/bin:/bin"}
        for key in [k for k in env if k.startswith("MEMORY_V4_")
                    and k not in ("MEMORY_V4_URL", "MEMORY_V4_SERVICE_LABEL", "MEMORY_V4_DIR")]:
            env.pop(key)
        client_files = {"cursor": ".cursor/mcp.json", "gemini-cli": ".gemini/settings.json", "codex": ".codex/config.toml"}
        created = {rel for key, rel in client_files.items() if not (home / rel).exists()}
        unit_rel = f".config/systemd/user/{TEST_LABEL}.service"
        unit = home / unit_rel
        roots = (".config/memory-os", ".local/state/memory-os", ".config/systemd/user", "memory-os-backups",
                 *{rel.split("/", 1)[0] for rel in client_files.values()})

        def snapshot():
            """Inventory of every installation location under HOME, plus the store (judged apart)."""
            out = {}
            for root in roots:
                if (home / root).exists():
                    out.update({f"{root}/{rel}": v for rel, v in inventory(home / root).items()})
            return out

        helper = ROOT / "scripts/qualify_install_410_helper.py"

        def store_logical(name):
            """``store_logical`` of the 4.1.0 install qualification, run by the venv (product receipt reader)."""
            code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from qualify_install_410_helper import "
                    "store_logical; print(json.dumps(store_logical(sys.argv[2])))")
            rc, out = self.step(name, [venv / "bin/python", "-I", "-B", "-c", code, helper.parent, store], env=env,
                                cwd=self.R)
            return self.last_json(out)

        def store_kept(before, after):
            """``store_compare`` of the 4.1.0 install qualification, run by the venv too (product receipt reader)."""
            pair = self.R / f"store-pair-{len(self.report['gates']['P5'].get('steps', {}))}.json"
            pair.write_text(json.dumps({"before": before, "after": after}))
            code = ("import json, sys; sys.path.insert(0, sys.argv[1]); from qualify_install_410_helper import "
                    "store_compare; d = json.load(open(sys.argv[2])); print(json.dumps(store_compare(d['before'], "
                    "d['after'])))")
            rc, out = self.step(f"store-compare-{pair.stem}", [venv / "bin/python", "-I", "-B", "-c", code,
                                                               helper.parent, pair], env=env, cwd=self.R)
            verdict = self.last_json(out)
            same = bool(before.get("store_instance_id")) and before.get("store_instance_id") == after.get("store_instance_id")
            return same and verdict["store_logical_unchanged"] is True, {"instance_unchanged": same, **verdict}

        def store_counters():
            rc, out = self.step(f"store-counters-{len(self.report['gates']['P5'].get('steps', {}))}",
                                [admin, "doctor", "--store-only", "--json"], env=env, cwd=self.R, expect=(0, 1))
            data = self.last_json(out)
            data = data[0] if isinstance(data, list) and data else data
            if not isinstance(data, dict):
                return {"raw": str(data)[:300]}
            journal = data.get("change_journal") or {}
            return {"live_total": data.get("live_total"), "facts_valid": data.get("facts_valid"),
                    "superseded_ids": data.get("superseded_ids"), "journal_events": journal.get("events")}

        clients = ",".join(TEST_CLIENTS)
        before_preview = snapshot()
        rc, out = self.step("quickstart-preview", [admin, "quickstart", "--profile", "lexical", "--clients", clients,
                                                   "--report"], env=env, cwd=self.R)
        preview = self.last_json(out)
        self.check("quickstart preview accepted", preview.get("ok") is True and preview.get("writes") == "none"
                   and preview.get("downloads_required_bytes") == 0, preview.get("blocker_codes"))
        self.check("the preview wrote nothing (installation locations inventoried before/after)",
                   snapshot() == before_preview and not store.exists())

        def apply(name, *extra):
            rc, out = self.step(name, [admin, "quickstart", "--profile", "lexical", "--clients", clients, "--apply",
                                       *extra, "--report"], env=env, cwd=self.R, timeout=900)
            result = self.last_json(out)
            self.check(f"{name}: quickstart --apply under systemd --user", result.get("ok") is True
                       and isinstance(result.get("stamp"), str), result.get("error"))
            active = self.step(f"{name}-active", ["systemctl", "--user", "is-active", f"{TEST_LABEL}.service"],
                               env=env, expect=(0, 3))[1].strip()
            self.check(f"{name}: unit written and active", unit.is_file() and active == "active", active)
            return result

        def stopped(name):
            state = self.step(f"{name}-state", ["systemctl", "--user", "is-active", f"{TEST_LABEL}.service"], env=env,
                              expect=(0, 3, 4))[1].strip()
            with socket.socket() as sock:
                sock.settimeout(1)
                free = sock.connect_ex(("127.0.0.1", port)) != 0
            self.check(f"{name}: stop attested, port free, unit removed", state in ("inactive", "unknown") and free
                       and not unit.exists(), {"state": state, "port_free": free, "unit": unit.exists()})
            self.check(f"{name}: client files created by the install removed",
                       all(not (home / rel).exists() for rel in created), sorted(created))
            self.check(f"{name}: data kept (store present)", (store / "store.sqlite3").is_file())

        # 1. install, then roll it back (quickstart --undo STAMP --with-service)
        first = apply("apply-1")
        counters_running = store_counters()
        rc, out = self.step("undo", [admin, "quickstart", "--undo", first["stamp"], "--with-service"], env=env,
                            cwd=self.R, timeout=600, expect=(0, 1))
        undone = self.last_json(out)
        self.check("rollback: quickstart --undo --with-service restored every client", rc == 0 and all(
            r.get("undo") in ("restored", "nothing-to-undo") for r in undone.get("results", [])), undone.get("results"))
        stopped("rollback")
        self.check("rollback: the store is the same store, nothing lost", store_counters() == counters_running)
        # 2. install again, then uninstall; the uninstall is judged path by path
        apply("apply-2", "--demo")             # the opt-in demo saves and searches through the relay: real data
        rc, out = self.step("doctor", [admin, "doctor", "--json"], env=env, cwd=self.R, expect=(0, 1))
        checks = self.last_json(out)
        failing = [c for c in (checks if isinstance(checks, list) else checks.get("checks", []))
                   if isinstance(c, dict) and c.get("status") == "FAIL"]
        self.check("doctor: 0 FAIL", rc == 0 and not failing, failing)
        sys.path.insert(0, str(ROOT / "v4/src"))
        from memory_os_v4.ops.clients import classify, platform_clients, read_entry
        attested = {}
        for client in platform_clients("linux"):
            if client.key in TEST_CLIENTS:
                state, entry = read_entry(client, home, platform="linux")
                attested[client.key] = {"state": state, "class": classify(entry)}
        self.check("client configurations attested (relay entries)",
                   all(v == {"state": "ok", "class": "relay"} for v in attested.values()), attested)
        counters = store_counters()
        self.check("the store holds data before the uninstall (demo item through the relay)",
                   isinstance(counters.get("live_total"), int) and counters["live_total"] >= 1, counters)
        # Non-empty witnesses that the uninstall must keep: a real backup and its key (S4), and the token.
        self.step("backup-witness", ["nice", "-n", "15", admin, "backup", "--backend", "sqlite", "--online", "--apply"],
                  env=env, cwd=self.R, timeout=600)
        witnesses = {"token": home / ".config/memory-os/http.token", "backup key": home / ".config/memory-os/backup.key"}
        backups = sorted(p for p in (home / "memory-os-backups").rglob("*") if p.is_file()) \
            if (home / "memory-os-backups").is_dir() else []
        self.check("non-empty witnesses exist before the uninstall (token, backup key, backup)",
                   all(p.is_file() and p.stat().st_size > 0 for p in witnesses.values())
                   and any(p.stat().st_size > 0 for p in backups), {k: p.is_file() for k, p in witnesses.items()})
        logical_before = store_logical("store-logical-before-preview")
        before = snapshot()
        logs = {rel: (home / rel).stat().st_size for rel in before if rel.startswith(".local/state/memory-os/")
                and "/logs/" in rel and (home / rel).is_file()}
        logs_raw = {rel: (home / rel).read_bytes() for rel in logs}
        journals_raw = {rel: (home / rel).read_bytes() for rel in before
                        if rel.startswith(".local/state/memory-os/quickstart/") and rel.endswith(".json")}
        rc, out = self.step("uninstall-preview", [admin, "uninstall"], env=env, cwd=self.R, expect=(0, 1))
        logical_preview = store_logical("store-logical-after-preview")
        kept, detail = store_kept(logical_before, logical_preview)
        self.check("uninstall preview wrote nothing (installation locations, permissions, store content)",
                   snapshot() == before and kept and detail.get("store_raw_hash_identical") is True, detail)
        rc, out = self.step("uninstall-apply", [admin, "uninstall", "--apply"], env=env, cwd=self.R, timeout=600,
                            expect=(0, 1))
        result = self.last_json(out)
        self.check("uninstall --apply", rc == 0 and result.get("ok") is True, result.get("error"))
        stopped("uninstall")
        after = snapshot()

        def revocation_only(old, new):
            """The ONLY journal rewrite admitted (4.1.1 ``revocation_seule``): records go from not revoked to
            ``revoked: true`` with an author; nothing else is added, removed or changed."""
            try:
                a, b = json.loads(old), json.loads(new)
            except (TypeError, ValueError):
                return False
            if not (isinstance(a, dict) and isinstance(b, dict) and isinstance(a.get("records"), list)
                    and isinstance(b.get("records"), list) and len(a["records"]) == len(b["records"])):
                return False
            if {k: v for k, v in a.items() if k != "records"} != {k: v for k, v in b.items() if k != "records"}:
                return False
            revoked = 0
            for old_record, new_record in zip(a["records"], b["records"]):
                if old_record == new_record:
                    continue
                rest = lambda d: {k: v for k, v in d.items() if k not in ("revoked", "revoked_by")}  # noqa: E731
                if not (isinstance(old_record, dict) and isinstance(new_record, dict) and not old_record.get("revoked")
                        and new_record.get("revoked") is True and new_record.get("revoked_by")
                        and rest(old_record) == rest(new_record)):
                    return False
                revoked += 1
            return revoked > 0
        unexpected = []
        for rel in sorted(set(before) | set(after)):
            old, new = before.get(rel), after.get(rel)
            if old == new:
                continue
            if rel == unit_rel and new is None:
                continue                                    # service definition removed
            if new is None and (rel in created or any(c.startswith(rel + "/") for c in created)):
                continue                                    # client file created by the install, removed
            if old is None and rel.startswith(".local/state/memory-os/uninstall"):
                continue                                    # uninstall journal
            if rel in logs and new is not None and (home / rel).read_bytes()[:logs[rel]] == logs_raw[rel]:
                continue                                    # log appended only (prefix = previous file)
            if old is None and "/logs/" in rel and rel.startswith(".local/state/memory-os/"):
                continue                                    # new log
            if rel in journals_raw and new is not None and revocation_only(journals_raw[rel], (home / rel).read_bytes()):
                continue                                    # proof revoked, field by field
            if rel.startswith(".config/systemd/user/") and old == "dir":
                continue                                    # empty unit directory left by systemd
            unexpected.append({"path": rel, "before": old, "after": new})
        self.check("uninstall changed only what its protocol allows (token, key, backups, journals kept)",
                   not unexpected, unexpected[:30])
        self.check("journals never removed nor shortened", all(rel in after for rel in journals_raw)
                   and bool(journals_raw), sorted(journals_raw))
        kept, detail = store_kept(logical_before, store_logical("store-logical-after-uninstall"))
        self.check("store content kept: same instance, every table identical, old receipts unchanged, only the "
                   "engine's own closing receipts added", kept, detail)
        self.check("store counters unchanged", store_counters() == counters, counters)

    # ---------------------------------------------------------------- P6
    def p6(self):
        venv = self.venv_path
        d = self.R / "p6"
        d.mkdir(mode=0o700)
        worker = ROOT / "tools/qualify413/p6_worker.py"
        spec = importlib.util.spec_from_file_location("qualify413_p6_worker", worker)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        (d / "plan.json").write_text(json.dumps({"format": module.PLAN_FORMAT, "source_commit": self.report["commit"],
                                                 "version": self.version,
                                                 "lexical": {"fixed_data_sha256": module.fixed_data_sha256()}}))
        (d / "copy-owner.json").write_text(json.dumps({"owner": module.PLAN_FORMAT}) + "\n")
        env = {"HOME": str(d / "home"), "PATH": "/usr/bin:/bin", "PYTHONOPTIMIZE": "0", "MEMORY_V4_DIR": str(d / "lexical-store"),
               "MEMORY_V4_OPS_DIR": str(d / "ops"), "MEMORY_V4_BACKUP_DIR": str(d / "backups"),
               "MEMORY_V4_BACKUP_KEY": str(d / "backup.key"), "MEMORY_V4_TOKEN_FILE": str(d / "never-a-real.token")}
        (d / "home").mkdir()
        self.step("worker", ["nice", "-n", "15", venv / "bin/python", "-I", "-B", worker, "--plan", d / "plan.json",
                             "--kind", "lexical-store"], env=env, cwd=d, timeout=900)
        line = json.loads((d / "lexical-store.json").read_text())
        shutil.copyfile(d / "lexical-store.json", self.output / "p6-lexical-store.json")
        self.report["artifacts"]["p6-lexical-store.json"] = sha256(self.output / "p6-lexical-store.json")
        moments = line.get("rankings", {})
        self.check("write, search, reopen, backup, restore", line.get("ok") is True
                   and (line.get("backup") or {}).get("ok") is True and (line.get("restore") or {}).get("ok") is True
                   and (line.get("restore") or {}).get("archive_sha256") == (line.get("backup") or {}).get("archive_sha256"))
        self.check("identical rankings at the three moments on the frozen queries and facts",
                   set(moments) == {"after_write", "after_reopen", "after_restore"}
                   and moments["after_write"] == moments["after_reopen"] == moments["after_restore"]
                   and list(moments["after_write"]) == list(module.LEXICAL_QUERIES)
                   and line.get("added") == len(module.LEXICAL_FACTS))
        self.check("every ranking is non-empty, without duplicate, and made of the frozen facts only",
                   all(isinstance(hits, list) and hits and len(set(hits)) == len(hits)
                       and set(hits) <= set(module.LEXICAL_FACTS) for hits in moments["after_write"].values()),
                   {"queries": len(moments.get("after_write", {}))})
        self.check("PRAGMA read back on the store", line.get("pragmas") == PRAGMAS, line.get("pragmas"))
        self.check("lexical marker on the store and its restoration",
                   (line.get("marker") or {}).get("name") == "lexical" and line.get("marker") == line.get("restored_marker"),
                   line.get("marker"))
        self.check("store written by the installed wheel, without ML",
                   Path(line.get("package_path", "/")).is_relative_to(venv)
                   and not any((line.get("find_spec") or {"x": 1}).values()), line.get("package_path"))
        self.report["gates"]["P6"]["cross_refusal"] = ("not applicable on a Linux cell: it needs the semantic "
                                                       "environment, which exists on macos-arm64-py311 only")

    # ---------------------------------------------------------------- P7
    def p7(self):
        venv = self.venv_path
        out = self.output / "p7.json"
        env = {"HOME": str(self.R / "p7-home"), "PATH": "/usr/bin:/bin", "PYTHONOPTIMIZE": "0"}
        (self.R / "p7-home").mkdir()
        self.step("installed-relay-case", [venv / "bin/python", "-B", ROOT / "scripts/installed_relay_case.py",
                                           "--artifact", self.wheel, "--sha256", self.report["artifacts"][self.wheel.name],
                                           "--source-commit", self.report["commit"], "--version", self.version,
                                           "--kind", "wheel", "--profile", "lexical", "--output", out],
                  env=env, cwd=self.R, timeout=900, expect=(0, 1))
        case = json.loads(out.read_text())
        self.report["artifacts"]["p7.json"] = sha256(out)
        self.check("installed lexical relay: connection, outage, no replay, reconnection",
                   case.get("ok") is True and all(case.get(key) is True for key in RELAY_KEYS),
                   {key: case.get(key) for key in (*RELAY_KEYS, "error")})

    # ---------------------------------------------------------------- driver
    def run(self) -> int:
        try:
            self.setup()
            for gate in ALL_GATES:
                if gate not in self.gates:
                    continue
                if gate in ("P5", "P6", "P7") and not hasattr(self, "venv_path"):
                    raise Refused(f"{gate} needs the P4 installation")
                self.current = gate
                self.report["gates"][gate] = {"checks": []}
                getattr(self, gate.lower())()
                self.report["gates"][gate]["ok"] = True
            self.report["ok"] = True
        except (Refused, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            self.report["error"] = f"{type(exc).__name__}: {exc}"
            if self.current in self.report["gates"]:
                self.report["gates"][self.current]["ok"] = False
        finally:
            self.report["logs"] = {p.name: sha256(p) for p in sorted((self.output / "logs").iterdir())}
            (self.output / "report.json").write_text(json.dumps(self.report, indent=1, sort_keys=True, default=str) + "\n")
            print(json.dumps({k: self.report.get(k) for k in ("cell", "commit", "ok", "error")}, indent=1))
        return 0 if self.report["ok"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cell", required=True, choices=CELLS)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--cpython", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gates", default=",".join(ALL_GATES))
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("refused: a Linux runner is required", file=sys.stderr)
        return 2
    gates = tuple(g.strip().upper() for g in args.gates.split(",") if g.strip())
    if set(gates) - set(ALL_GATES):
        parser.error("unknown gate")
    return Run(args.output, args.cell, args.dist, args.cpython, gates).run()


if __name__ == "__main__":
    raise SystemExit(main())
