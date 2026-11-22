"""Seal the 4.1.0 artifacts and the tooling (repository AND external chain/supervisor).

Every gate starts ``pending``; ``release_ready`` is false and stays false until the final
verification. Never publishes, pushes or controls production. Builds nothing itself.
"""

import argparse
import configparser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

from release_410_checks import (
    CHAIN_NAME,
    PYTEST_PINS,
    RELEASE_VERSION,
    RUN_TAG_RE,
    SUPERVISOR_NAME,
    SUPERVISOR_SHA256,
    digest,
    new_gates,
    refuse_forbidden,
    set_gate,
    validate_local,
    verify_invariants,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [
    "make_release_410.py",
    "release_410_checks.py",
    "verify_release_manifest.py",
    "source_release_checks_410.py",
    "check_brief_perimeter_410.py",
    "prepare_release_410_envs.py",
    "qualify_install_410.sh",
    "qualify_install_410_helper.py",
    "qualify_lexical_wheel_410.py",
    "qualify_sqlite_release_410.py",
    "sqlite_release_worker_410.py",
    "installed_relay_e2e.py",
    "installed_relay_case.py",
    PYTEST_PINS,
    # reused without change (qualify_distribution_d1a.py: one minimal change, contract rev. 7 §8)
    "qualify_405_test_isolation.py",
    "qualify_distribution_d1a.py",
    "qualify_runtime_resolution.py",
    "check_runtime_resolution.py",
    "check_extra_markers.py",
    "qualify_schema8_copy.py",
    "schema8_inventory.py",
    "schema8_unchanged.py",
    "search_qualification.py",
    "qualify_release_copy.py",
    "release_copy_worker.py",
    "qualify_sqlite_activation.py",
    "test_home_guard.py",
]
COPY_TOOLS = {
    "runner": "qualify_sqlite_release_410.py",
    "worker": "sqlite_release_worker_410.py",
    "doctor": "qualify_release_copy.py",
    "profile": "qualify_sqlite_activation.py",
    "search_gate": "search_qualification.py",
    "migration_inventory": "schema8_inventory.py",
    "authority": "qualify_schema8_copy.py",
    "no_migration_inventory": "schema8_unchanged.py",
}
ENTRY_POINTS = {
    "memory-os-ai", "memory-os-v4", "memory-os-ai-v3", "memory-os-relay", "memory-os-admin", "memory-os-constraints",
}


def check_entry_points(wheel):
    """Console commands read from the built bytes, ``memory-os-constraints`` included."""
    with zipfile.ZipFile(wheel) as archive:
        names = [n for n in archive.namelist() if n.endswith(".dist-info/entry_points.txt")]
        if len(names) != 1:
            raise ValueError("Ambiguous wheel entry points")
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(names[0]).decode())
    scripts = dict(parser["console_scripts"]) if parser.has_section("console_scripts") else {}
    if not ENTRY_POINTS <= set(scripts) or scripts.get("memory-os-constraints") != "memory_os_constraints:main":
        raise ValueError("Wheel entry points incomplete: " + ", ".join(sorted(ENTRY_POINTS - set(scripts))))
    return True


def external_tool(path, name):
    path = Path(path)
    if not path.is_absolute() or path.name != name or path.is_symlink() or not path.is_file():
        raise ValueError("External tool must be the absolute regular file " + name)
    if ROOT in path.resolve().parents:
        raise ValueError("External tooling lives outside the repository")
    return {"path": str(path), "sha256": digest(path)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact-proof", type=Path, required=True)
    p.add_argument("--full-proof", type=Path, required=True)
    p.add_argument("--preconditions-proof", type=Path, required=True)
    p.add_argument("--lint", nargs=3, action="append", metavar=("NAME", "LOG", "OK_MARKER"), required=True)
    p.add_argument("--source-commit", required=True)
    p.add_argument("--run-tag", required=True)
    p.add_argument("--chain", type=Path, required=True)
    p.add_argument("--supervisor", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    for given in (a.artifact_proof, a.full_proof, a.preconditions_proof, a.chain, a.supervisor, a.output,
                  *(path for row in a.lint for path in row[1:])):
        refuse_forbidden(given)  # lexical then resolved, before any read
    out = a.output.absolute()
    if out.exists() or not out.resolve().is_relative_to(ROOT / ".local-qualification"):
        p.error("Fresh owned release directory required")
    if re.fullmatch(RUN_TAG_RE, a.run_tag) is None:
        p.error("Invalid RUN_TAG")
    commit = subprocess.check_output(["git", "rev-parse", a.source_commit + "^{commit}"], cwd=ROOT, text=True).strip()
    if commit != a.source_commit:
        raise ValueError("Complete source SHA required")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != commit or \
            subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True).strip():
        raise ValueError("Clean tree at the candidate commit required")
    invariants = verify_invariants(ROOT, commit)
    external = {"chain": external_tool(a.chain, CHAIN_NAME), "supervisor": external_tool(a.supervisor, SUPERVISOR_NAME)}
    if external["supervisor"]["sha256"] != SUPERVISOR_SHA256:
        raise ValueError("Supervisor is not the byte-identical 4.0.9 one")
    stamp = "%s %s %s" % (commit, external["chain"]["sha256"], a.run_tag)
    art = json.loads(a.artifact_proof.read_text())
    parent = a.artifact_proof.parent
    if not art.get("ok") or art["source_commit"] != commit:
        raise ValueError("Artifact proof not this commit")
    if [row[0] for row in a.lint] != ["lint1", "lint2"]:
        raise ValueError("Exactly lint1 then lint2 required")
    out.mkdir(mode=0o700)
    (out / "artifacts").mkdir(mode=0o700)
    (out / "evidence").mkdir(mode=0o700)
    m = {
        "version": RELEASE_VERSION,
        "source_commit": commit,
        "run_tag": a.run_tag,
        "release_ready": False,
        "open_blockers": [],
        "invariants": invariants,
        "profiles": invariants["profiles"],
        "proof_transfer": "none: no proof is transferred automatically (QUALIFY_4_1_0 §1); 4.0.9 was never "
                          "qualified separately; P-A is never replayed and G13 needs a reasoned decision of Romain",
        "gates": new_gates(),
        "decisions": {},
        "measures": {
            "M1": {},
            "M2": {"state": "non mesuré", "effect_on_release_ready": "none"},
            "M3": None,
            "M4": {"state": "historical reference (H2_RUN_v3.md), not replayed, never a gate of the final commit"},
        },
        "artifacts": [],
        "evidence": {},
        "qualification_tooling_sha256": {n: digest(ROOT / "scripts" / n) for n in TOOLS},
        "external_tooling_sha256": external,
        "pytest_pins_sha256": digest(ROOT / "scripts" / PYTEST_PINS),
    }
    for rel in ("direct/memory_os_ai-" + RELEASE_VERSION + "-py3-none-any.whl",
                "via-sdist/memory_os_ai-" + RELEASE_VERSION + ".tar.gz"):
        src = parent / rel
        if digest(src) != art["artifact_sha256"][rel]:  # hashes drawn from the built bytes
            raise ValueError("Artifact changed")
        target = out / "artifacts" / src.name
        shutil.copyfile(src, target)
        m["artifacts"].append({"name": target.name, "bytes": target.stat().st_size, "sha256": digest(target)})
    m["entry_points_checked"] = check_entry_points(out / "artifacts" / m["artifacts"][0]["name"])
    lints = {"source_commit": commit, "run_tag": a.run_tag, "lints": []}
    for name, log, marker in a.lint:
        # The chain writes the marker only for returncode 0 without any suspect process.
        if Path(marker).read_text().strip() != stamp:
            raise ValueError("Lint marker is not this run's success marker: " + name)
        target = out / "evidence" / (name + ".log")
        target.write_bytes(Path(log).read_bytes())
        lints["lints"].append({"name": name, "returncode": 0, "log_sha256": digest(target)})
    (out / "evidence/lints.json").write_text(json.dumps(lints, indent=2) + "\n")
    for name, source in [("artifacts", a.artifact_proof), ("full", a.full_proof),
                         ("preconditions", a.preconditions_proof), ("lints", out / "evidence/lints.json")]:
        target = out / "evidence" / (name + ".json")
        if source != target:
            target.write_bytes(source.read_bytes())
        m["evidence"][name] = {"path": str(target.relative_to(out)), "sha256": digest(target)}
    paths = [
        ROOT / "conftest.py",
        ROOT / "MANIFEST.in",
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((ROOT / "tests").rglob("*.py")),
        *sorted((ROOT / "v4/tests").rglob("*.py")),
        *sorted((ROOT / "src").rglob("*.py")),
        *sorted((ROOT / "v4/src").rglob("*.py")),
    ]
    m["qualification_source_sha256"] = {str(x.relative_to(ROOT)): digest(x) for x in paths}
    m["source_expected_v3_tests"] = next(
        c for c in json.loads(a.full_proof.read_text())["commands"] if c["name"] == "v3")["counts"]["tests"]
    m["copy_scripts_sha256"] = {n: digest(ROOT / "scripts" / v) for n, v in COPY_TOOLS.items()}
    m["copy_scripts_sha256"]["capacity"] = digest(ROOT / "v4/src/memory_os_v4/ops/sqlite_limits.py")
    m["copy_scripts_sha256"]["guard"] = digest(ROOT / "v4/src/memory_os_v4/ops/provision.py")
    from sqlite_release_worker_410 import fixed_data_sha256

    m["lexical_store_scripts_sha256"] = {n: m["copy_scripts_sha256"][n] for n in ("runner", "worker", "guard")}
    m["lexical_store_fixed_data_sha256"] = fixed_data_sha256()
    m["installed_relay_scripts_sha256"] = {
        n: digest(ROOT / "scripts" / v)
        for n, v in [("runner", "installed_relay_e2e.py"), ("case", "installed_relay_case.py")]
    }
    tw = out / "evidence/twine-check-strict.log"
    with tw.open("x") as stream:
        subprocess.run([sys.executable, "-B", "-m", "twine", "check", "--strict",
                        *[str(out / "artifacts" / x["name"]) for x in m["artifacts"]]],
                       stdout=stream, stderr=subprocess.STDOUT, check=True)
    m["twine_evidence"] = {"path": str(tw.relative_to(out)), "sha256": digest(tw)}
    validate_local(m, out, require_source=False, require_copy=False)
    # G1-G3 are established by what was just checked; every other gate stays pending.
    set_gate(m, "G1", "pass", name="preconditions")
    set_gate(m, "G2", "pass", name="full")
    m["gates"]["G2"]["evidence"].append("lints")
    set_gate(m, "G3", "pass", name="artifacts")
    (out / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    print(json.dumps({"assembled": True, "version": RELEASE_VERSION, "source_commit": commit,
                      "run_tag": a.run_tag, "release_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
