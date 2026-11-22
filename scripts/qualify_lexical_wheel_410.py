#!/usr/bin/env python3
"""G6 (QUALIFY_4_1_0 §4, LEXICAL_PROFILE §11.2): the installed lexical wheel, without ML.

In the lexical venv of the installation (N3): pytest and its frozen closure are added (N4,
exact pins of ``qualify_410_pytest_pins.txt``, under the lexical constraints), the two ML-free
test files and their two support modules are copied OUTSIDE the checkout and run from there,
without PYTHONPATH and without any config/rootdir pointing at the sources. The proof records
``memory_os_v4.__file__`` (under site-packages), ``find_spec`` of the ML modules (all None)
and the test result. A pass obtained by importing ``v4/src`` does not count.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PINS = Path(__file__).with_name("qualify_410_pytest_pins.txt")
TEST_FILES = ("test_lexical_profile.py", "test_lexical_capabilities_410.py")
SUPPORT_FILES = ("lexical_fixture_410.py", "lexical_frozen_83ddead.py")
ML_MODULES = ("torch", "sentence_transformers", "transformers", "faiss")
PROBE = (
    "import importlib.util as u, json, os, sys; from pathlib import Path; import memory_os_v4; "
    "from memory_os_v4.ops.qualified_runtime import inspect_current; "
    "p = Path(memory_os_v4.__file__).resolve(); "
    "print(json.dumps({'package_path': str(p), "
    "'package_under_site_packages': 'site-packages' in p.parts and Path(sys.prefix).resolve() in p.parents, "
    "'find_spec': {n: (None if u.find_spec(n) is None else 'present') for n in %r}, "
    "'pythonpath_set': bool(os.environ.get('PYTHONPATH')), "
    "'runtime_ok': inspect_current(extras=('ops', 'brief'), profile='lexical')['ok'], "
    "'version': memory_os_v4.__version__}))" % (ML_MODULES,)
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(text):
    rows = {}
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if line:
            found = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            if not found:
                raise ValueError("Only exact pins are allowed: " + line)
            rows[canonical(found.group(1))] = found.group(2)
    if not rows:
        raise ValueError("Empty pin file")
    return rows


def check_pytest_install(report, wanted, locked):
    """pip may only install the frozen closure; a common dependency must stay at its lexical lock."""
    rows = []
    for item in report.get("install", []):
        name, version = canonical(item["metadata"]["name"]), item["metadata"]["version"]
        info = item.get("download_info", {})
        url, sha = info.get("url", ""), info.get("archive_info", {}).get("hashes", {}).get("sha256")
        if name in locked:
            raise ValueError("A locked runtime dependency would be reinstalled: " + name)
        if wanted.get(name) != version:
            raise ValueError("pip resolved outside the frozen pytest closure: %s %s" % (name, version))
        if not (url.startswith("https://files.pythonhosted.org/") and url.endswith(".whl") and sha):
            raise ValueError("Not a recorded PyPI wheel: " + name)
        rows.append({"name": name, "version": version, "url": url, "sha256": sha})
    if {r["name"] for r in rows} != set(wanted):
        raise ValueError("Frozen pytest closure not installed exactly")
    return sorted(rows, key=lambda r: r["name"])


def junit_counts(path):
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    return {name: sum(int(s.get(name, "0")) for s in suites) for name in ("tests", "failures", "errors", "skipped")}


def locks(text):
    return {canonical(m.group(1)): m.group(2) for m in (
        re.fullmatch(r"([A-Za-z0-9_.\-]+)==([^\s;]+)", raw.split("#")[0].strip()) for raw in text.splitlines()) if m}


def main(argv=None):
    if not __debug__:
        raise RuntimeError("Assertions required; do not use Python -O")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True, help="lexical venv of the installation (N3)")
    parser.add_argument("--output", type=Path, required=True, help="fresh folder OUTSIDE the checkout")
    parser.add_argument("--network-go", default="", help="N4 must be named: nominative GO of Romain")
    args = parser.parse_args(argv)
    from qualify_distribution_d1a import command
    from release_410_checks import artifact, refuse_forbidden, validate_lexical_wheel, validate_local
    from memory_os_v4.ops.provision import guard

    for given in (args.manifest, args.venv, args.output):
        refuse_forbidden(given)  # lexical, then resolved: before any read
    manifest = json.loads(args.manifest.read_text())
    validate_local(manifest, args.manifest.parent, require_source=False, require_copy=False)
    out, venv = args.output.absolute(), args.venv.absolute()
    python = venv / "bin/python"
    if out.exists() or ROOT in out.resolve().parents or out.resolve() == ROOT:
        parser.error("Fresh output outside the checkout required")
    if not python.is_file() or ROOT in venv.resolve().parents:
        parser.error("Lexical venv missing or inside the checkout")
    if "N4" not in args.network_go.split(","):
        parser.error("N4 (pytest closure from PyPI) needs its nominative GO: --network-go N4")
    if digest(PINS) != manifest["pytest_pins_sha256"]:
        raise ValueError("Frozen pytest pins differ from the sealed manifest")
    guard(out.parent)
    out.mkdir(parents=True, mode=0o700)
    tests = out / "tests"
    for name in ("home", "tmp", "pip-cache", "tests"):
        (out / name).mkdir(mode=0o700)
    report = {"format": "qualify-410-lexical-wheel-v1", "ok": False, "source_commit": manifest["source_commit"],
              "run_tag": manifest["run_tag"], "version": manifest["version"],
              "wheel_sha256": artifact(manifest, ".whl")["sha256"], "venv": str(venv),
              "pytest_pins_sha256": digest(PINS), "outside_checkout": True}
    env = {"PATH": str(venv / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
           "HOME": str(out / "home"), "TMPDIR": str(out / "tmp"), "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CACHE_DIR": str(out / "pip-cache"), "PYTHONNOUSERSITE": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    try:
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        wanted = pins(PINS.read_text())
        constraints = next(venv.glob("lib/python*/site-packages/memory_os_v4/resources/"
                                     "qualified_runtime_constraints_lexical_v1.txt"))
        if digest(constraints) != manifest["profiles"]["lexical"]["constraints_sha256"]:
            raise ValueError("Installed lexical constraints differ from the sealed ones")
        locked = locks(constraints.read_text())
        if set(wanted) & set(locked):
            raise ValueError("A pinned test dependency duplicates a lexical lock")
        # N4: wheels only, under the lexical constraints kept in force.
        command([str(python), "-B", "-m", "pip", "install", "--only-binary=:all:", "--index-url",
                 "https://pypi.org/simple", "--report", str(out / "pytest-pip-report.json"), "-c", str(constraints),
                 "-r", str(PINS)], out, env, out / "pytest-install.log", guard)
        report["pytest_distributions"] = check_pytest_install(
            json.loads((out / "pytest-pip-report.json").read_text()), wanted, locked)
        command([str(python), "-B", "-m", "pip", "check"], out, env, out / "pip-check.log", guard)
        report["pip_check_ok"] = True
        # Qualified runtime checked again after the addition: locks unchanged, still no ML.
        command([str(python), "-B", "-I", "-c", PROBE], tests, env, out / "probe.log", guard)
        probe = json.loads(next(line for line in reversed((out / "probe.log").read_text().splitlines())
                                if line.startswith("{")))
        report.update(package_path=probe["package_path"], find_spec=probe["find_spec"],
                      package_under_site_packages=probe["package_under_site_packages"]
                      and venv.resolve() in Path(probe["package_path"]).parents,
                      pythonpath_set=probe["pythonpath_set"], runtime_recheck_ok=probe["runtime_ok"])
        if any(probe["find_spec"].values()) or not probe["runtime_ok"] or probe["version"] != manifest["version"]:
            raise RuntimeError("Lexical environment is not ML-free at the qualified locks")
        report["test_files"] = {}
        for name in (*TEST_FILES, *SUPPORT_FILES):
            source = ROOT / "v4/tests" / name
            if digest(source) != manifest["qualification_source_sha256"]["v4/tests/" + name]:
                raise ValueError("Test file differs from the candidate: " + name)
            shutil.copyfile(source, tests / name)
            report["test_files"][name] = digest(tests / name)
        (tests / "pytest.ini").write_text("[pytest]\n")  # rootdir and config stay in this folder
        xml = out / "lexical-wheel.xml"
        result = None
        try:
            # No PYTHONPATH, no -c/rootdir towards the sources: the INSTALLED package is what runs.
            result = command([str(python), "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                              "--basetemp=" + str(out / "pytest-tmp"), "--junitxml=" + str(xml), *TEST_FILES],
                             tests, env, out / "lexical-wheel.log", guard)
            report["returncode"] = 0
        except RuntimeError as exc:
            report["returncode"] = 1
            report["error"] = str(exc)
        report["counts"] = junit_counts(xml)
        report["evidence_sha256"] = {p.name: digest(p) for p in sorted(out.iterdir()) if p.suffix in (".log", ".xml", ".json")}
        if result is None:
            raise RuntimeError("Lexical wheel tests failed; see lexical-wheel.log")
        report["ok"] = True
        validate_lexical_wheel(report, manifest)
    except (OSError, ValueError, KeyError, RuntimeError, StopIteration, ET.ParseError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report.get(k) for k in ("ok", "error", "counts")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
