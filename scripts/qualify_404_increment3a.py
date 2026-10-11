#!/usr/bin/env python3
"""Sequential, offline/hash-only increment checks; fresh evidence directory required."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def test_counts(xml_path: Path, suite: str) -> dict:
    tree = ET.parse(xml_path).getroot()
    nodes = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    counts = {key: sum(int(node.attrib.get(key, 0)) for node in nodes)
              for key in ("tests", "failures", "errors", "skipped")}
    cases = list(tree.iter("testcase"))
    counts["tcp_cases"] = sum(
        case.attrib.get("classname", "").split(".")[-1] in {"test_server_http_e2e", "test_ops_e2e", "test_l3_tcp"}
        for case in cases)
    if suite != "unit":
        required = {"test_C07_thirty_restart_rotation_cycles",
                    "test_L4_identity_changed_survives_outage_catalog",
                    "test_L7a_registry_scopes_through_packaged_relay",
                    "test_C02_C04_C08_disconnect_and_new_relay_adopts_journal[partial-pending]",
                    "test_C02_C04_C08_disconnect_and_new_relay_adopts_journal[committed-completed]"}
        present = {c.attrib.get("name") for c in cases if c.attrib.get("classname", "").endswith("test_l3_tcp")}
        if not required <= present:
            raise RuntimeError("L3 physical TCP scenarios missing; qualification refused")
    minimum = {"unit": 1270, "tcp": 17, "full": 1287}[suite]
    if (counts["tests"] < minimum or counts["tests"] != len(cases)
            or any(counts[k] for k in ("failures", "errors", "skipped"))
            or (suite != "unit" and counts["tcp_cases"] < 17)):
        raise RuntimeError("Required tests failed, skipped or incomplete; qualification refused")
    return counts


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
              "l3_runtime_implemented": True, "l4_implemented": "candidate; physical qualification required", "l7a_registry_implemented": True,
              "l5_implemented": False, "l1a_implemented": False}
    env = dict(os.environ)
    # A host's selection override must not silently turn a full qualification into one test.
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("MEMORY_V4_PROJECT_REGISTRY", None)
    env.pop("MEMORY_V4_RELAY_JOURNAL_DIR", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1",
               PYTHONPATH=str(ROOT / "v4/src") + os.pathsep + str(ROOT / "v4"),
               MEMORY_V4_EMBED_MODEL="hash", MEMORY_V4_MAINTENANCE_MODE="disabled",
               MEMORY_V4_QUALIFICATION_METRICS="0", MEMORY_V4_DIR=str(output / "unused-store"),
               MEMORY_V4_OPS_DIR=str(output / "ops"), MEMORY_V4_CLIENT_ID="qualification",
               MEMORY_V4_RELAY_JOURNAL_MAX_DAYS="30", MEMORY_V4_RELAY_JOURNAL_MAX_BYTES="10485760")
    try:
        report["swap_guard"] = "unavailable_in_sandbox"
        if sys.platform == "darwin":
            probe = subprocess.run(["/usr/sbin/sysctl", "vm.swapusage"], capture_output=True, text=True, timeout=5)
            found = re.search(r"used = ([0-9.]+)([MG])", probe.stdout)
            if probe.returncode == 0 and found:
                used = float(found[1]) * (1024**2 if found[2] == "M" else 1024**3)
                report["swap_guard"] = {"used_bytes": used, "max_bytes": 8.5 * 1024**3}
                if used > 8.5 * 1024**3:
                    raise RuntimeError("Swap guard: above 8.5 GiB; no tests started")
        if shutil.disk_usage(output).free < 5_000_000_000:
            raise RuntimeError("Disk guard: less than 5 GB free; no tests started")
        pytest = [sys.executable, "-m", "pytest", "-q", "-m", "not slow", "-p", "no:cacheprovider",
                  "--basetemp", str(output / "pytest-tmp"), "--junitxml", str(output / "pytest.xml")]
        tcp = ["v4/tests/test_server_http_e2e.py", "v4/tests/test_ops_e2e.py", "v4/tests/test_l3_tcp.py"]
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
        counts = test_counts(output / "pytest.xml", args.suite)
        report["pytest"] = counts
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
