"""Sequential installed 4.1.0 SQLite drills. No migration, no service control, no download.

``--mode copy`` (G9, G10): installed copy of the production store, schema 8 -> 8, baseline
4.0.7 / wheel / sdist workers and reopenings, private doctor, S4 x2, plus the 4.1.0 addition:
the lexical profile is refused on the copy without any write.
``--mode lexical-store`` (G11): brand-new lexical store through the installed lexical wheel,
on fixed data and queries; the opposite (semantic) open, run by the installed semantic wheel
environment (ML closure present), is refused without mutation.
The production plist and store are only READ. ``--print-start-budget`` runs nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MIN_COPY_START = 9_200_000_000
PLAN_FORMAT = "sqlite-release-410-v1"


def digest(path):
    from release_410_checks import read_bytes  # guard before every open, link targets included

    return hashlib.sha256(read_bytes(path)).hexdigest()


def hashed_inventory(directory):
    """Every entry below ``directory`` with its content SHA-256 (names, sizes and dates are not enough)."""
    rows = {}
    directory = Path(directory)
    for path in sorted(directory.rglob("*")):
        rel = str(path.relative_to(directory))
        info = path.lstat()
        if path.is_symlink():
            rows[rel] = {"type": "link", "target": os.readlink(path)}
        elif path.is_dir():
            rows[rel] = {"type": "dir", "mode": info.st_mode & 0o7777}
        else:
            rows[rel] = {"type": "file", "mode": info.st_mode & 0o7777, "bytes": info.st_size, "sha256": digest(path)}
    text = json.dumps(rows, sort_keys=True).encode()
    return rows, hashlib.sha256(text).hexdigest()


def sealed_pieces(root, extra=()):
    """SHA-256 of the pieces a harness leaves: logs and JSON at the top of its folder, plus the named
    pieces of its sub-folders (S4 logs, sealed archives). All are re-verified, presence and bytes, at G15."""
    root = Path(root)
    pieces = {path.name: digest(path) for path in sorted(root.iterdir())
              if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log") and path.name != "report.json"}
    for rel in extra:
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise ValueError("Piece to seal is missing: " + rel)
        pieces[rel] = digest(path)
    return pieces


COMMON_DRILL = """import json, os, sys; from contextlib import closing; from pathlib import Path
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.ops import sqlite_backup as s4
from memory_os_v4.sqlite_store import SQLiteStore
s=OpsSettings.from_env()
with closing(SQLiteStore(Path(os.environ['MEMORY_V4_DIR']))):
    d=s4.restore_drill(s,archive=Path(sys.argv[1]),apply=True,start_server=True,reconcile=True)
print(json.dumps({'drill':d}))
if not d.get('ok'): raise RuntimeError(d)
"""


def footprint(source):
    return sum(path.stat().st_size for path in Path(source).glob("store.sqlite3*") if path.is_file())


def start_disk_budget(source, free, *, policy=None):
    """max(9.2 GB, dynamic budget), F = DB + WAL + SHM measured now (QUALIFY_4_1_0 §5)."""
    if policy is None:
        from memory_os_v4.ops.storage import policy
    size = footprint(source)
    if size <= 0:
        raise ValueError("Source store footprint unreadable")
    selected, reserve, work = policy(size)
    # 4.0.9 budget, plus one more private copy of the cut (lexical refusal store) and one more
    # restoration of the common sealed archive by the second artifact (restored copy + its work area).
    dynamic = (max(7_000_000_000, 5_000_000_000 + 20 * size) if selected == "legacy5gb"
               else reserve + work + 4 * size + 256 * 1024**2) + 3 * size
    return {"source_footprint_bytes": size, "dynamic_budget_bytes": dynamic,
            "required_start_free_bytes": max(MIN_COPY_START, dynamic), "observed_start_free_bytes": free,
            "s4_not_reused": True, "storage_policy": selected}


def baseline_from_plist(plist, expected):
    """The baseline interpreter comes from the production plist, then is checked: no substitution."""
    declared = plistlib.loads(Path(plist).read_bytes())
    program = declared.get("ProgramArguments", [None])[0]
    if not program or Path(program) != Path(expected) or Path(program).resolve() != Path(expected).resolve():
        raise ValueError("Baseline interpreter of the production plist differs from the expected one")
    return Path(program), declared.get("EnvironmentVariables", {})


def fresh_root(path):
    root = Path(path).absolute()
    if (root.exists() or root.is_symlink() or not root.resolve().is_relative_to(ROOT / ".local-qualification")
            or root.resolve() == (ROOT / ".local-qualification").resolve()):
        raise ValueError("Fresh private output below clone .local-qualification required")
    return root


def worker_files(root, tools):
    worker = root / "worker.py"
    shutil.copyfile(tools["worker"], worker)
    shutil.copyfile(tools["guard"], root / "qualification_guard.py")
    (root / "copy-owner.json").write_text('{"owner":"%s"}\n' % PLAN_FORMAT)
    return worker


def semantic_wheel_environment(a, manifest):
    """The installed semantic wheel environment of this run (``environments.json``), for the opposite open.

    The engine checks the ML closure before the store profile: the lexical venv has no faiss and would
    be refused by ``semantic_dependency_missing`` without ever reaching the store.
    """
    from release_410_checks import artifact

    paths = json.loads(a.environments.read_text())
    row = (paths.get("environments") or {}).get("wheel") or {}
    lexical = (paths.get("environments") or {}).get("lexical") or {}
    if not (paths.get("ok") is True and paths.get("version") == manifest["version"]
            and paths.get("source_commit") == manifest["source_commit"]
            and paths.get("artifacts") == manifest["artifacts"] and paths.get("run_tag") == manifest["run_tag"]):
        raise ValueError("Installed environments are not qualified for this manifest")
    closure = row.get("ml_find_spec")
    if not (row.get("profile") == "semantic" and row.get("artifact_sha256") == artifact(manifest, ".whl")["sha256"]
            and row.get("path") == paths.get("wheel_environment")
            and isinstance(closure, dict) and closure and all(value is True for value in closure.values())):
        raise ValueError("Semantic wheel environment is not the sealed wheel with its ML closure")
    if Path(lexical.get("path") or "").resolve() != a.lexical_environment.resolve():
        raise ValueError("Environments were attested with another lexical environment")
    environment = Path(paths["wheel_environment"]).absolute()
    python = environment / "bin/python"
    if not python.is_file() or ROOT in python.parents or environment.resolve() == a.lexical_environment.resolve():
        raise ValueError("Semantic environment must be outside checkout and distinct from the lexical one")
    return environment, python, {"path": str(environment), "artifact_sha256": row["artifact_sha256"],
                                 "environments_sha256": digest(a.environments)}


def lexical_store(a, manifest):
    """G11."""
    from qualify_distribution_d1a import command
    from memory_os_v4.ops.provision import guard, min_free_bytes
    from release_410_checks import artifact, validate_lexical_store
    from sqlite_release_worker_410 import fixed_data_sha256

    root = fresh_root(a.output_root)
    python = (a.lexical_environment / "bin/python").absolute()
    if not python.is_file() or ROOT in python.parents:
        raise ValueError("Lexical environment must be outside checkout")
    guard(root.parent)
    root.mkdir(mode=0o700)
    tools = {"runner": Path(__file__), "worker": Path(__file__).with_name("sqlite_release_worker_410.py"),
             "guard": ROOT / "v4/src/memory_os_v4/ops/provision.py"}
    report = {"format": "qualify-410-lexical-store-v1", "ok": False, "version": manifest["version"],
              "source_commit": manifest["source_commit"], "run_tag": manifest["run_tag"],
              "wheel_sha256": artifact(manifest, ".whl")["sha256"],
              "scripts_sha256": {k: digest(v) for k, v in tools.items()},
              "fixed_data_sha256": fixed_data_sha256(), "production_modified": False}
    started = time.monotonic()
    try:
        if report["scripts_sha256"] != manifest["lexical_store_scripts_sha256"]:
            raise ValueError("Lexical store tools differ from sealed manifest")
        semantic, semantic_python, report["semantic_environment"] = semantic_wheel_environment(a, manifest)
        (root / "home").mkdir(mode=0o700)
        plan = {"format": PLAN_FORMAT, "source_commit": manifest["source_commit"], "version": manifest["version"],
                "lexical": {"fixed_data_sha256": report["fixed_data_sha256"]}}
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        worker = worker_files(root, tools)
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        env.update(HOME=str(root / "home"), PYTHONPATH="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONOPTIMIZE="0", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                   MEMORY_V4_MIN_FREE_BYTES=str(min_free_bytes()), MEMORY_V4_BUILD_COMMIT=manifest["source_commit"],
                   MEMORY_V4_CL100K_PATH=os.environ.get("MEMORY_V4_CL100K_PATH", ""),
                   MEMORY_V4_DIR=str(root / "lexical-store"), MEMORY_V4_OPS_DIR=str(root / "ops"),
                   MEMORY_V4_BACKUP_DIR=str(root / "backups"), MEMORY_V4_BACKUP_KEY=str(root / "backup.key"),
                   MEMORY_V4_URL="http://127.0.0.1:9/mcp/", MEMORY_V4_TOKEN_FILE=str(root / "never-production.token"),
                   MEMORY_V4_SERVICE_LABEL="")

        def run(executable, kind):
            command([str(executable), "-B", str(worker), "--plan", str(plan_path), "--kind", kind], root, env,
                    root / (kind + ".log"), guard)
            return json.loads((root / (kind + ".json")).read_text())

        row = run(python, "lexical-store")
        report.update(rankings=row["rankings"], marker=row["marker"], restored_marker=row["restored_marker"],
                      backup=row["backup"], restore=row["restore"], find_spec=row["find_spec"],
                      package_path=row["package_path"], store_instance_id=row["store_instance_id"],
                      package_under_site_packages="site-packages" in Path(row["package_path"]).parts
                      and (a.lexical_environment.resolve() in Path(row["package_path"]).parents))
        # Opposite profile, by the installed SEMANTIC wheel (the lexical venv lacks the ML closure and
        # would never reach the store): inventories with content hashes taken by this parent, no worker alive.
        before_rows, before = hashed_inventory(root / "lexical-store")
        refused = run(semantic_python, "semantic-refusal")
        after_rows, after = hashed_inventory(root / "lexical-store")
        report["opposite_profile_open"] = {
            **refused, "inventory_before_sha256": before, "inventory_after_sha256": after,
            "inventory_entries": len(before_rows), "files_created": sorted(set(after_rows) - set(before_rows)),
            "package_under_semantic_environment": "site-packages" in Path(refused.get("package_path", "")).parts
            and semantic.resolve() in Path(refused.get("package_path", "")).parents}
        report["added"] = row["added"]
        report["evidence_sha256"] = sealed_pieces(root, ["backups/" + row["backup"]["archive"]])
        report["seconds"] = time.monotonic() - started
        report["ok"] = True
        validate_lexical_store(report, manifest)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        report["seconds"] = time.monotonic() - started
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report.get(k) for k in ("ok", "error", "seconds")}))
    return 0 if report["ok"] else 1


def copy(a, manifest):
    """G9, G10."""
    from qualify_distribution_d1a import command
    from qualify_release_copy import private_doctor
    from qualify_sqlite_activation import profile_environment
    from release_copy_worker import QUERIES
    from search_qualification import SEARCH_CRITERION, search_hit_counts
    from release_410_checks import artifact, validate_copy
    from memory_os_v4.ops.provision import guard, min_free_bytes

    SOURCE = manifest["source_commit"]
    INSTANCE = a.expected_instance_id
    root = fresh_root(a.output_root)
    if a.source_dir.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(a.source_dir.resolve()):
        raise ValueError("Copy/source paths must be disjoint")
    guard(root.parent)
    root.mkdir(mode=0o700)
    tools = {
        "runner": Path(__file__),
        "worker": Path(__file__).with_name("sqlite_release_worker_410.py"),
        "guard": ROOT / "v4/src/memory_os_v4/ops/provision.py",
        "doctor": Path(__file__).with_name("qualify_release_copy.py"),
        "profile": Path(__file__).with_name("qualify_sqlite_activation.py"),
        "migration_inventory": Path(__file__).with_name("schema8_inventory.py"),
        "authority": Path(__file__).with_name("qualify_schema8_copy.py"),
        "no_migration_inventory": Path(__file__).with_name("schema8_unchanged.py"),
        "search_gate": Path(__file__).with_name("search_qualification.py"),
        "capacity": ROOT / "v4/src/memory_os_v4/ops/sqlite_limits.py",
    }
    report = {
        "ok": False,
        "version": manifest["version"],
        "baseline_version": "4.0.7",
        "source_commit": SOURCE,
        "run_tag": manifest["run_tag"],
        "artifacts": manifest["artifacts"],
        "source_sql_writes": False,
        "production_modified": False,
        "min_free_bytes": min_free_bytes(),
        "workers": {},
        "scripts_sha256": {k: digest(v) for k, v in tools.items()},
    }
    started = time.monotonic()
    try:
        if report["scripts_sha256"] != manifest["copy_scripts_sha256"]:
            raise ValueError("Copy tools differ from sealed manifest")
        # F is measured again here, immediately before the copy.
        report["disk_budget"] = start_disk_budget(a.source_dir, shutil.disk_usage(root).free)
        required = report["disk_budget"]["required_start_free_bytes"]
        free = report["disk_budget"]["observed_start_free_bytes"]
        if free < required:
            raise ValueError(f"Installed S4 qualification needs {required} free bytes; wait, do not skip S4")
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        baseline, declared = baseline_from_plist(a.production_plist, a.baseline_python)
        if (Path(declared.get("MEMORY_V4_DIR", "")).resolve() != a.source_dir.resolve()
                or declared.get("MEMORY_V4_STORE_BACKEND") != "sqlite_s1" or declared.get("MEMORY_V4_WORK_STATE") != "1"):
            raise ValueError("Source path/backend/tasks must match the production plist")
        report["production_plist_sha256"] = digest(a.production_plist)
        report["expected_instance_id"] = INSTANCE
        report["source_dir"] = str(a.source_dir.resolve())
        report["baseline_python"] = str(baseline)
        profile = profile_environment(a.production_plist)
        paths = json.loads(a.environments.read_text())
        if not (paths.get("ok") is True and paths.get("version") == manifest["version"]
                and paths.get("source_commit") == SOURCE and paths.get("artifacts") == manifest["artifacts"]
                and paths.get("run_tag") == manifest["run_tag"]):
            raise ValueError("Installed environments are not qualified for this manifest")
        interpreters = {"baseline": baseline.absolute(),
                        **{k: Path(paths[k + "_environment"]).absolute() / "bin/python" for k in ("wheel", "sdist")}}
        for executable in interpreters.values():
            if not executable.is_file() or ROOT in executable.parents:
                raise ValueError("Installed environments must be outside checkout")
        (root / "home").mkdir(mode=0o700)
        plan = {
            "format": PLAN_FORMAT,
            "source_commit": SOURCE,
            "version": manifest["version"],
            "queries": list(QUERIES),
            "at_time": time.time(),
            "expected_instance_id": INSTANCE,
            "criterion": {"startup_seconds_max": 120, "total_seconds_max": 900, "corpus_encoded_texts": 0,
                          "exact_rankings": True, "authority_identical": True, "search_non_vacuity": SEARCH_CRITERION,
                          "lexical_open_refused_without_write": True},
        }
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        worker = worker_files(root, tools)
        shutil.copyfile(tools["capacity"], root / "qualification_limits.py")
        for module in ("schema8_inventory.py", "schema8_unchanged.py", "qualify_schema8_copy.py",
                       "search_qualification.py", "release_copy_worker.py"):
            shutil.copyfile(ROOT / "scripts" / module, root / module)
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        profile = {**profile, **{k: v for k, v in declared.items() if k in ("HF_HUB_CACHE", "MEMORY_V4_CL100K_PATH")}}
        env.update(
            profile,
            HOME=str(root / "home"),
            PYTHONPATH="",
            PYTHONNOUSERSITE="1",
            PYTHONDONTWRITEBYTECODE="1",
            HF_HOME=os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface")),
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            PYTHONOPTIMIZE="0",
            MEMORY_V4_MIN_FREE_BYTES=str(min_free_bytes()),
            MEMORY_V4_STORE_BACKEND="sqlite_s1",
            MEMORY_V4_WORK_STATE="1",
            MEMORY_V4_WRITE_MODE="read_only",
            OMP_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            VECLIB_MAXIMUM_THREADS="1",
        )
        for setting in ("MEMORY_V4_SQLITE_BACKUP_MAX_BYTES", "MEMORY_V4_SQLITE_STORAGE_POLICY"):
            if setting in os.environ:
                env[setting] = os.environ[setting]
        if env.get("MEMORY_V4_RETRIEVAL_PROFILE", "semantic") != "semantic":
            raise ValueError("The production copy is qualified in the semantic profile")

        def monitor(path):
            guard(path)
            if time.monotonic() - started > 900:
                raise RuntimeError("Copy total time budget exceeded")

        def run(executable, kind, *extra, environment=None):
            command([str(executable), "-B", str(worker), "--plan", str(plan_path), "--kind", kind, *extra],
                    root, environment or env, root / (kind + ".log"), monitor)

        run(interpreters["wheel"], "snapshot", "--snapshot-source", str(a.source_dir.absolute()))
        logical = json.loads((root / "snapshot.json").read_text())
        report["snapshot"] = logical
        if logical["store_instance_id"] != INSTANCE or logical["schema_version"] != 8:
            raise ValueError("Source identity/schema mismatch")
        cut_sha = digest(root / "snapshot.sqlite3")
        for name in ("baseline", "wheel", "sdist", "lexical-refusal"):
            target = root / (name + "-store")
            target.mkdir(mode=0o700)
            shutil.copyfile(root / "snapshot.sqlite3", target / "store.sqlite3")
            (target / "store.sqlite3").chmod(0o600)
        for name in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"):
            worker_env = dict(env, MEMORY_V4_BUILD_COMMIT=SOURCE)
            if name == "baseline":
                worker_env.pop("MEMORY_V4_BUILD_COMMIT", None)
            run(interpreters[name.removesuffix("-warm")], name, environment=worker_env)
            report["workers"][name] = json.loads((root / (name + ".json")).read_text())
        # 4.1.0 addition: lexical open of an untouched private copy of the cut, refused, no write.
        refusal_store = root / "lexical-refusal-store"
        before_rows, before = hashed_inventory(refusal_store)
        lexical_env = {k: v for k, v in env.items() if k not in ("MEMORY_V4_WRITE_MODE", "MEMORY_V4_STORE_BACKEND")}
        lexical_env["MEMORY_V4_BUILD_COMMIT"] = SOURCE
        run(interpreters["wheel"], "lexical-refusal", environment=lexical_env)
        after_rows, after = hashed_inventory(refusal_store)
        report["lexical_open_refusal"] = {
            **json.loads((root / "lexical-refusal.json").read_text()),
            "inventory_before_sha256": before, "inventory_after_sha256": after,
            "inventory_entries": len(before_rows), "files_created": sorted(set(after_rows) - set(before_rows)),
            "store_sha256_is_source_cut": digest(refusal_store / "store.sqlite3") == cut_sha}
        if digest(root / "snapshot.sqlite3") != cut_sha:
            raise ValueError("Sealed source cut changed")
        if digest(a.production_plist) != report["production_plist_sha256"]:
            raise ValueError("Production definition changed during qualification")
        report["source_cut_sha256"] = cut_sha
        report["search_hit_counts"] = {
            name: search_hit_counts(row["search"], QUERIES, worker=name, reported=row.get("search_hit_counts"))
            for name, row in report["workers"].items()}
        report.update(authority_identical=True, search_identical_to_baseline=True,
                      rankings_identical_between_artifacts_and_reopen=True, warm_encoded_texts=0)
        # Reuse the established private --warm HTTP/doctor drill, with this
        # harness's effective guard. No production port/token/service used.
        import qualify_release_copy as old

        old_guard = old.resources
        try:
            old.resources = monitor
            report["doctor"] = private_doctor(interpreters["wheel"], root / "wheel-store", root,
                                              dict(env, MEMORY_V4_BUILD_COMMIT=SOURCE), SOURCE,
                                              version=manifest["version"])
        finally:
            old.resources = old_guard
        report["s4"] = {}
        for kind in ("wheel", "sdist"):
            s4root = root / ("s4-" + kind)
            s4root.mkdir(mode=0o700)
            s4env = {
                **env,
                "MEMORY_V4_BUILD_COMMIT": SOURCE,
                "MEMORY_V4_DIR": str(root / (kind + "-store")),
                "MEMORY_V4_OPS_DIR": str(s4root / "ops"),
                "MEMORY_V4_BACKUP_DIR": str(s4root / "backups"),
                "MEMORY_V4_BACKUP_KEY": str(s4root / "backup.key"),
                "MEMORY_V4_URL": "http://127.0.0.1:9/mcp/",
                "MEMORY_V4_TOKEN_FILE": str(s4root / "never-production.token"),
                "MEMORY_V4_SERVICE_LABEL": "",
            }
            # S4 is an ONLINE backup: as in production, the writer stays open (WAL/SHM present)
            # during backup + drill; never repair/create WAL from the backup side.
            code = """import json, os; from contextlib import closing; from pathlib import Path
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.ops.backup import load_or_create_key
from memory_os_v4.ops import sqlite_backup as s4
from memory_os_v4.sqlite_store import SQLiteStore
s=OpsSettings.from_env(); load_or_create_key(s.backup_key)
with closing(SQLiteStore(Path(os.environ['MEMORY_V4_DIR']))):
    b=s4.backup(s,apply=True)
    if not b.get('ok'): raise RuntimeError(b)
    d=s4.restore_drill(s,archive=s.backup_dir / b['archive'],apply=True,start_server=True,reconcile=True)
print(json.dumps({'backup':b,'drill':d}))
if not d.get('ok'): raise RuntimeError(d)
"""
            command([str(interpreters[kind]), "-B", "-c", code], root, s4env, s4root / "s4.log", monitor)
            lines = (s4root / "s4.log").read_text().splitlines()
            report["s4"][kind] = json.loads(next(line for line in reversed(lines) if line.startswith("{")))
        # G10 "same archive, two artifacts": the archive sealed by the wheel exercise is restored by the
        # sdist artifact too (same key, same backup folder, its own work area). Both S4 exercises above stay.
        sealed = report["s4"]["wheel"]["backup"]
        archive = root / "s4-wheel" / "backups" / sealed["archive"]
        common = {"archive": sealed["archive"], "archive_sha256": digest(archive),
                  "drills": {"wheel": {**report["s4"]["wheel"]["drill"], "artifact_sha256": artifact(manifest, ".whl")["sha256"]}}}
        if common["archive_sha256"] != sealed["archive_sha256"]:
            raise ValueError("Common sealed archive changed after its backup")
        common_root = root / "s4-common-sdist"
        common_root.mkdir(mode=0o700)
        common_env = {
            **env,
            "MEMORY_V4_BUILD_COMMIT": SOURCE,
            "MEMORY_V4_DIR": str(root / "wheel-store"),
            "MEMORY_V4_OPS_DIR": str(common_root / "ops"),
            "MEMORY_V4_BACKUP_DIR": str(root / "s4-wheel" / "backups"),
            "MEMORY_V4_BACKUP_KEY": str(root / "s4-wheel" / "backup.key"),
            "MEMORY_V4_URL": "http://127.0.0.1:9/mcp/",
            "MEMORY_V4_TOKEN_FILE": str(common_root / "never-production.token"),
            "MEMORY_V4_SERVICE_LABEL": "",
        }
        command([str(interpreters["sdist"]), "-B", "-c", COMMON_DRILL, str(archive)], root, common_env,
                common_root / "s4.log", monitor)
        lines = (common_root / "s4.log").read_text().splitlines()
        common["drills"]["sdist"] = {**json.loads(next(line for line in reversed(lines) if line.startswith("{")))["drill"],
                                     "artifact_sha256": artifact(manifest, ".tar.gz")["sha256"]}
        common["archive_sha256_after"] = digest(archive)
        report["s4_common"] = common
        report["evidence_sha256"] = sealed_pieces(root, ["s4-wheel/s4.log", "s4-sdist/s4.log", "s4-common-sdist/s4.log",
                                                         "s4-wheel/backups/" + common["archive"]])
        report["seconds"] = time.monotonic() - started
        report["ok"] = True
        validate_copy(report, manifest)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        report["seconds"] = time.monotonic() - started
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report.get(k) for k in ("ok", "error", "source_sql_writes", "seconds")}))
    return 0 if report["ok"] else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("copy", "lexical-store"), default="copy")
    p.add_argument("--print-start-budget", action="store_true",
                   help="print max(9.2 GB, dynamic budget) in bytes for --source-dir, then exit; runs nothing")
    p.add_argument("--manifest", type=Path)
    p.add_argument("--environments", type=Path)
    p.add_argument("--lexical-environment", type=Path)
    p.add_argument("--baseline-python", type=Path)
    p.add_argument("--source-dir", type=Path)
    p.add_argument("--production-plist", type=Path)
    p.add_argument("--expected-instance-id")
    p.add_argument("--output-root", type=Path)
    a = p.parse_args(argv)
    sys.path.insert(0, str(ROOT / "v4/src"))
    from release_410_checks import refuse_forbidden

    for name in ("manifest", "environments", "lexical_environment", "baseline_python", "source_dir", "production_plist",
                 "output_root"):
        if getattr(a, name) is not None:
            refuse_forbidden(getattr(a, name))  # lexical then resolved, before any read or copy
    if a.print_start_budget:
        if a.source_dir is None:
            p.error("--source-dir required")
        print(start_disk_budget(a.source_dir, shutil.disk_usage(Path.home()).free)["required_start_free_bytes"])
        return 0
    needed = (("manifest", "environments", "lexical_environment", "output_root") if a.mode == "lexical-store" else
              ("manifest", "environments", "baseline_python", "source_dir", "production_plist",
               "expected_instance_id", "output_root"))
    missing = [name for name in needed if getattr(a, name) is None]
    if missing:
        p.error("missing: " + ", ".join("--" + name.replace("_", "-") for name in missing))
    from release_410_checks import validate_local

    manifest = json.loads(a.manifest.read_text())
    validate_local(manifest, a.manifest.parent, require_copy=False, require_source=a.mode == "copy")
    return lexical_store(a, manifest) if a.mode == "lexical-store" else copy(a, manifest)


if __name__ == "__main__":
    raise SystemExit(main())
