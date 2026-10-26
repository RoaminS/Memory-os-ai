"""Seal exact RC artifacts; evidence pending stays pending. Never publish or control production."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tomllib
from release_408_checks import digest, validate_local, verify_engine_version_only

ROOT = Path(__file__).resolve().parents[1]
BASE = "9ecb4d06f68e2c7efddf77aad66f4ed5f147f356"
TOOLS = [
    "make_release_408.py",
    "release_408_checks.py",
    "verify_release_manifest.py",
    "qualify_sqlite_release_408.py",
    "record_release_408_gate.py",
    "score_release_408_gate.py",
    "sqlite_release_worker_408.py",
    "qualify_schema8_copy.py",
    "schema8_inventory.py",
    "schema8_unchanged.py",
    "search_qualification.py",
    "qualify_distribution_d1a.py",
    "qualify_405_test_isolation.py",
    "source_release_checks_408.py",
    "installed_relay_case.py",
    "installed_relay_e2e.py",
    "qualify_release_copy.py",
    "release_copy_worker.py",
    "qualify_sqlite_activation.py",
    "prepare_release_408_envs.py",
    "test_home_guard.py",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--artifact-proof", type=Path, required=True)
    p.add_argument("--full-proof", type=Path)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    out = a.output.absolute()
    if out.exists() or not out.resolve().is_relative_to(ROOT / ".local-qualification"):
        p.error("Fresh owned release directory required")
    commit = subprocess.check_output(
        ["git", "rev-parse", a.source_commit + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    if commit != a.source_commit:
        raise ValueError("Complete source SHA required")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    verify_engine_version_only(ROOT, commit, version)
    art = json.loads(a.artifact_proof.read_text())
    parent = a.artifact_proof.parent
    if not art.get("ok") or art["source_commit"] != commit:
        raise ValueError("Artifact proof not this commit")
    out.mkdir(mode=0o700)
    (out / "artifacts").mkdir(mode=0o700)
    (out / "evidence").mkdir(mode=0o700)
    m = {
        "version": version,
        "source_commit": commit,
        "release_ready": False,
        "open_blockers": [],
        "frozen_functional_base": BASE,
        "engine_diff_from_frozen_base_empty": True,
        "gate_transfer": "RC recollection required; no new usage/leaderboard claim",
        "checks": {
            "production_copy": "pending",
            "installed_relays": "pending",
            "gate_b_rc": "pending",
        },
        "artifacts": [],
        "evidence": {},
        "qualification_tooling_sha256": {
            n: digest(ROOT / "scripts" / n) for n in TOOLS
        },
    }
    for rel in (
        "direct/memory_os_ai-" + version + "-py3-none-any.whl",
        "via-sdist/memory_os_ai-" + version + ".tar.gz",
    ):
        src = parent / rel
        if digest(src) != art["artifact_sha256"][rel]:
            raise ValueError("Artifact changed")
        target = out / "artifacts" / src.name
        shutil.copyfile(src, target)
        m["artifacts"].append(
            {
                "name": target.name,
                "bytes": target.stat().st_size,
                "sha256": digest(target),
            }
        )
    for name, source in [("artifacts", a.artifact_proof), ("full", a.full_proof)]:
        if source is not None:
            target = out / "evidence" / (name + ".json")
            target.write_bytes(source.read_bytes())
            m["evidence"][name] = {
                "path": str(target.relative_to(out)),
                "sha256": digest(target),
            }
    paths = [
        ROOT / "conftest.py",
        ROOT / "MANIFEST.in",
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        *sorted((ROOT / "v4/tests").rglob("*.py")),
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "v4/src").rglob("*.py")),
    ]
    m["qualification_source_sha256"] = {
        str(x.relative_to(ROOT)): digest(x) for x in paths
    }
    m["source_expected_v3_tests"] = (
        next(
            c
            for c in json.loads(a.full_proof.read_text())["commands"]
            if c["name"] == "v3"
        )["counts"]["tests"]
        if a.full_proof
        else None
    )
    tools = {
        "runner": "qualify_sqlite_release_408.py",
        "worker": "sqlite_release_worker_408.py",
        "doctor": "qualify_release_copy.py",
        "profile": "qualify_sqlite_activation.py",
        "search_gate": "search_qualification.py",
        "migration_inventory": "schema8_inventory.py",
        "authority": "qualify_schema8_copy.py",
        "no_migration_inventory": "schema8_unchanged.py",
    }
    m["copy_scripts_sha256"] = {
        n: digest(ROOT / "scripts" / v) for n, v in tools.items()
    }
    m["copy_scripts_sha256"]["capacity"] = digest(
        ROOT / "v4/src/memory_os_v4/ops/sqlite_limits.py"
    )
    m["copy_scripts_sha256"]["guard"] = digest(
        ROOT / "v4/src/memory_os_v4/ops/provision.py"
    )
    m["installed_relay_scripts_sha256"] = {
        n: digest(ROOT / "scripts" / v)
        for n, v in [
            ("runner", "installed_relay_e2e.py"),
            ("case", "installed_relay_case.py"),
        ]
    }
    tw = out / "evidence/twine-check-strict.log"
    subprocess.run(
        [
            __import__("sys").executable,
            "-B",
            "-m",
            "twine",
            "check",
            "--strict",
            *[str(out / "artifacts" / x["name"]) for x in m["artifacts"]],
        ],
        stdout=tw.open("x"),
        stderr=subprocess.STDOUT,
        check=True,
    )
    m["twine_evidence"] = {"path": str(tw.relative_to(out)), "sha256": digest(tw)}
    validate_local(m, out, require_source=False, require_copy=False)
    (out / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    print(
        json.dumps(
            {
                "assembled": True,
                "version": version,
                "source_commit": commit,
                "release_ready": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
