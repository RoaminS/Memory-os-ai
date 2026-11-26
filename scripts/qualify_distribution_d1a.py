#!/usr/bin/env python3
"""D1a light evidence; optional offline artifact checks only after machine guard.

No dependency/model download, inference, HTTP server, production or client write.
Installed smokes reuse existing dependencies (not a fresh core-only size claim).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import venv
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
UNITS = ["tests/test_distribution_static.py", "v4/tests/test_distribution_d1a.py", "v4/tests/test_quickstart.py", "v4/tests/test_qualified_runtime.py",
         "v4/tests/test_distribution_d1c_fixes.py",
         "v4/tests/test_work_cold.py", "v4/tests/test_readiness.py",
         "v4/tests/test_ops.py", "v4/tests/test_ops_doctor.py", "v4/tests/test_server_handlers.py"]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv, cwd, env, log, monitor=None):
    started = time.monotonic()
    with log.open("x") as stream:
        child = subprocess.Popen(argv, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                if monitor:
                    monitor(cwd)
                if time.monotonic() - started > 600:
                    raise RuntimeError("Step wall-time budget exceeded")
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            raise
    if child.returncode:
        raise RuntimeError(f"Failed step: {log.name}, exit {child.returncode}")
    return {"argv": argv, "elapsed_ms": round(1000 * (time.monotonic() - started), 3), "log_sha256": digest(log)}


def qualification_environment(environ) -> dict:
    """Environment of every step run by this tool. 4.1.3 (D2): no legacy v3 path or switch; the source tree
    is v4 only (``memory_os_ai`` is no longer shipped nor importable from the sources)."""
    env = {key: value for key, value in environ.items()
           if not key.startswith("MEMORY_V4_") and key != "MEMORY_TEST_HASH_V3"}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0", PYTHONPATH="v4/src:v4",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--build", action="store_true", help="Disk default >=5 GB (operator override >=3 GB), measured swap <=8.5 GB; no downloads")
    parser.add_argument("--source-commit", help="Exact sealed SHA required with --build")
    parser.add_argument("--build-python", type=Path, help="Already-equipped interpreter for offline build (defaults to current Python)")
    args = parser.parse_args()
    if args.build and not args.source_commit:
        parser.error("--build requires --source-commit")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification") or output.exists():
        parser.error("Fresh folder under clone .local-qualification required; preserve old evidence")
    output.mkdir(parents=True)
    report = {"ok": False, "source_commit": args.source_commit, "production_modified": False,
              "downloads": 0, "model_calls": 0, "steps": {}, "installed_dependency_mode": "existing dependencies reused; not a clean dependency install"}
    env = qualification_environment(os.environ)
    if args.build_python:
        env["MEMORY_TEST_BUILD_PYTHON"] = str(args.build_python.resolve())
    try:
        provision = load("d1_guard", ROOT / "v4/src/memory_os_v4/ops/provision.py")
        report["min_free_bytes"] = provision.min_free_bytes()
        if "MEMORY_V4_MIN_FREE_BYTES" in os.environ:
            env["MEMORY_V4_MIN_FREE_BYTES"] = str(report["min_free_bytes"])
        xml = output / "unit.xml"
        report["steps"]["unit"] = command([sys.executable, "-B", "-m", "pytest", "-c", "pyproject.toml", *UNITS,
            "-q", "-p", "no:cacheprovider", "--basetemp=" + str(output / "pytest-tmp"),
            "--junitxml=" + str(xml)], ROOT, env, output / "unit.log")
        tree = ET.parse(xml).getroot()
        suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
        counts = {key: sum(int(s.get(key, "0")) for s in suites) for key in ("tests", "errors", "failures", "skipped")}
        if counts["errors"] or counts["failures"] or counts["skipped"]:
            raise RuntimeError("Unit proof incomplete")
        report["unit_counts"] = counts
        shutil.rmtree(output / "pytest-tmp")  # disposable fixtures only; failed runs/logs/XML remain
        if args.build:
            guard = provision.guard
            guard(output)
            os.setpriority(os.PRIO_PROCESS, 0, 15)
            env["MEMORY_OS_BUILD_SOURCE_COMMIT"] = args.source_commit
            build_python = str(args.build_python.resolve()) if args.build_python else sys.executable
            direct, via_sdist = output / "direct", output / "via-sdist"
            report["steps"]["direct_build"] = command([build_python, "-B", "-m", "build", "--no-isolation",
                "--wheel", "--outdir", str(direct)], ROOT, env, output / "direct-build.log", guard)
            guard(output)
            report["steps"]["sdist_build"] = command([build_python, "-B", "-m", "build", "--no-isolation",
                "--outdir", str(via_sdist)], ROOT, env, output / "sdist-build.log", guard)
            verify = load("d1_descriptor", ROOT / "v4/src/memory_os_v4/build_descriptor.py").verify
            descriptors = []
            wheel_sources = [next(direct.glob("*.whl")), next(via_sdist.glob("*.whl"))]
            report["steps"]["extra_markers"] = command([build_python, "-B",
                str(ROOT / "scripts/check_extra_markers.py"), "--pyproject", str(ROOT / "pyproject.toml"),
                *[value for wheel in wheel_sources for value in ("--wheel", str(wheel))]],
                ROOT, env, output / "extra-markers.log", guard)
            report["artifact_sha256"] = {str(p.relative_to(output)): digest(p) for folder in (direct, via_sdist) for p in folder.iterdir()}
            for index, wheel in enumerate(wheel_sources):
                guard(output)
                installed = output / f"payload-{index}"
                with zipfile.ZipFile(wheel) as archive:
                    for name in archive.namelist():
                        path = Path(name)
                        if path.is_absolute() or ".." in path.parts:
                            raise RuntimeError("Unsafe wheel member")
                        if name.startswith("memory_os_ai/"):
                            raise RuntimeError("Legacy v3 package shipped in the wheel (removed in 4.1.3)")
                        if name.startswith("memory_os_v4/") and not name.endswith("/"):
                            target = installed / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.read(name))
                version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
                identity = verify(installed, version)
                if not identity["ok"] or identity["commit"] != args.source_commit:
                    raise RuntimeError("Installed descriptor proof failed")
                descriptors.append(identity)
                home = output / f"home-{index}"
                home.mkdir()
                private = output / f"venv-{index}"
                venv.EnvBuilder(with_pip=True, system_site_packages=True).create(private)
                # The operator may run from a dedicated offline test runtime.
                sites = [p for p in sys.path if p and Path(p).name == "site-packages"]
                site = next((private / "lib").glob("python*/site-packages"))
                (site / "existing-test-dependencies.pth").write_text("\n".join(sites) + "\n")
                isolated = {**env, "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                            "HF_HOME": str(home / "hf"), "HF_HUB_CACHE": str(home / "hf/hub"),
                            "MEMORY_V4_DIR": str(home / "unused-store"), "PYTHONPATH": ""}
                python = private / "bin/python"
                report["steps"][f"install-{index}"] = command([str(python), "-B", "-m", "pip", "--isolated", "install",
                    "--no-index", "--no-deps", "--force-reinstall", str(wheel)], output, isolated, output / f"install-{index}.log", guard)
                for executable in ("memory-os-ai", "memory-os-v4", "memory-os-relay", "memory-os-admin",
                                   "memory-os-constraints"):
                    if not (private / "bin" / executable).is_file():
                        raise RuntimeError("Installed console command missing: " + executable)
                if (private / "bin" / "memory-os-ai-v3").exists():
                    raise RuntimeError("Legacy memory-os-ai-v3 command installed (removed in 4.1.3)")
                # Relay main is a blocking stdio client: verify entrypoint import without calling it.
                smoke = '''import importlib.metadata, json, pathlib, sys
from memory_os_v4.readiness import build_identity
from memory_os_v4.ops.qualified_runtime import check_requirements, inspect_current
info = build_identity()
assert info['identity_source'] == 'embedded_descriptor', info
assert check_requirements(importlib.metadata.distribution('memory-os-ai').requires)['ok']
assert inspect_current(extras=('ops', 'brief'))['ok'], inspect_current(extras=('ops', 'brief'))
import memory_os_v4
assert pathlib.Path(memory_os_v4.__file__).is_relative_to(pathlib.Path(sys.prefix)), memory_os_v4.__file__
for entry in importlib.metadata.distribution('memory-os-ai').entry_points:
 if entry.group == 'console_scripts': assert callable(entry.load())
assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules
print(json.dumps(info,sort_keys=True))
'''
                report["steps"][f"smoke-{index}"] = command([str(python), "-B", "-c", smoke], output, isolated, output / f"smoke-{index}.log")
                report["steps"][f"provision-preview-{index}"] = command([str(private / "bin/memory-os-admin"), "provision"],
                    output, isolated, output / f"provision-preview-{index}.log")
                # Installed quickstart preview: isolated HOME/port/label, absent resources,
                # no service/config/token/cache write. No real assistant is invoked.
                preview_smoke = '''import json, os, pathlib, socket, subprocess, sys, uuid
home = pathlib.Path(os.environ['HOME'])
def snapshot():
 return {str(p.relative_to(home)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b'') for p in home.rglob('*')}
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
env = {**os.environ, 'MEMORY_V4_URL': f'http://127.0.0.1:{port}/mcp/',
       'MEMORY_V4_SERVICE_LABEL': 'local.memory-os-preview-' + uuid.uuid4().hex}
before = snapshot()
run = subprocess.run([str(pathlib.Path(sys.executable).parent/'memory-os-admin'), 'quickstart',
 '--clients', 'cursor', '--profile', 'lexical', '--report'], env=env, capture_output=True, text=True, timeout=30)
assert snapshot() == before, 'Installed preview wrote to HOME'
assert run.returncode == 0, run.stdout + run.stderr
info = json.loads(run.stdout)
assert info['writes'] == 'none' and info['stage'] == 'preview' and info['ok']
assert info['backend'] == 'sqlite' and info['work_state'] is True and info['runtime_qualified'] is True
print(json.dumps(info, sort_keys=True))
'''
                report["steps"][f"quickstart-preview-{index}"] = command([str(python), "-B", "-c", preview_smoke],
                    output, isolated, output / f"quickstart-preview-{index}.log", guard)
            if descriptors[0] != descriptors[1]:
                raise RuntimeError("Wheel/sdist descriptor mismatch")
            report["identity"] = descriptors[0]
            report["artifact_checked"] = True
        report["ok"] = True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
