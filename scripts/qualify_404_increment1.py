#!/usr/bin/env python3
"""Sequential, offline/hash-only increment checks; fresh evidence directory required."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("unit", "tcp", "full"), default="unit")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification"):
        parser.error("output must be a fresh directory inside this clone's .local-qualification")
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "suite": args.suite, "ok": False, "commands": [],
              "neural_inference": False, "production_modified": False,
              "tcp_qualified": False, "release_qualified": False,
              "l3_l4_runtime_implemented": False}
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1",
               PYTHONPATH=str(ROOT / "v4/src") + os.pathsep + str(ROOT / "v4"),
               MEMORY_V4_EMBED_MODEL="hash", MEMORY_V4_MAINTENANCE_MODE="disabled",
               MEMORY_V4_QUALIFICATION_METRICS="0", MEMORY_V4_DIR=str(output / "unused-store"))
    try:
        if shutil.disk_usage(output).free < 5_000_000_000:
            raise RuntimeError("Disk guard: less than 5 GB free; no tests started")
        pytest = [sys.executable, "-m", "pytest", "-q", "-m", "not slow", "-p", "no:cacheprovider",
                  "--basetemp", str(output / "pytest-tmp"), "--junitxml", str(output / "pytest.xml")]
        tcp = ["v4/tests/test_server_http_e2e.py", "v4/tests/test_ops_e2e.py"]
        if args.suite == "tcp":
            pytest += tcp
        else:
            pytest += ["v4/tests"]
            if args.suite == "unit":
                pytest += [f"--ignore={p}" for p in tcp]
        commands = [("pytest", pytest),
                    ("lint-project", ["ruff", "check", "--no-cache", "--config", "pyproject.toml", "src", "tests", "scripts"]),
                    ("lint-v4", ["ruff", "check", "--no-cache", "--config", "pyproject.toml", "src", "tests", "v4/src", "v4/tests", "v4/bench", "scripts", "--select", "E9,F"])]
        for label, command in commands:
            print(f"Running {label} ({args.suite})", flush=True)
            started = time.monotonic()
            with (output / f"{label}.log").open("w") as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=900, check=False)
            report["commands"].append({"label": label, "argv": command, "returncode": completed.returncode,
                                       "seconds": time.monotonic() - started})
            if completed.returncode:
                raise RuntimeError(f"{label} failed; evidence preserved")
        suites = ET.parse(output / "pytest.xml").getroot()
        nodes = [suites] if suites.tag == "testsuite" else list(suites.iter("testsuite"))
        counts = {key: sum(int(node.attrib.get(key, 0)) for node in nodes)
                  for key in ("tests", "failures", "errors", "skipped")}
        report["pytest"] = counts
        if counts["skipped"] or not counts["tests"]:
            raise RuntimeError("Required tests skipped or absent; qualification refused")
        # Snapshot deterministic/hash-only output sizes and dispatch timing, not neural performance.
        if args.suite != "tcp":
            measure = [sys.executable, str(ROOT / "scripts/qualification_baseline_404.py"),
                       "--output", str(output / "hash-baseline")]
            with (output / "baseline.log").open("w") as log:
                result = subprocess.run(measure, cwd=ROOT, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=60, check=False)
            if result.returncode:
                raise RuntimeError("Hash baseline failed; evidence preserved")
        report["tcp_qualified"] = args.suite in {"tcp", "full"}
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        report["source_sha256"] = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for base in (ROOT / "v4/src", ROOT / "v4/tests") for p in sorted(base.rglob("*.py"))}
        report["evidence_sha256"] = {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.glob("*.log"))}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "tcp_qualified": report["tcp_qualified"],
                      "error": report.get("error"), "report": str(output / "report.json")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
