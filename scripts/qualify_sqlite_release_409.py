"""Sequential installed 4.0.7->4.0.9 schema8 copy drill. No migration/service control."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import time

from qualify_distribution_d1a import command
from qualify_release_copy import private_doctor
from qualify_sqlite_activation import profile_environment
from release_copy_worker import QUERIES
from search_qualification import SEARCH_CRITERION, search_hit_counts
from release_409_checks import digest, validate_copy, validate_local

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v4/src"))
from memory_os_v4.ops.provision import guard, min_free_bytes  # noqa: E402


def start_disk_budget(source, free):
    from memory_os_v4.ops.storage import policy

    footprint = sum(
        path.stat().st_size for path in source.glob("store.sqlite3*") if path.is_file()
    )
    selected, reserve, work = policy(footprint)
    required = (
        max(7_000_000_000, 5_000_000_000 + 20 * footprint)
        if selected == "legacy5gb"
        else reserve + work + 4 * footprint + 256 * 1024**2
    )
    return {
        "source_footprint_bytes": footprint,
        "required_start_free_bytes": required,
        "observed_start_free_bytes": free,
        "s4_not_reused": True,
        "storage_policy": selected,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--environments", type=Path, required=True)
    p.add_argument("--baseline-python", type=Path, required=True)
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--production-plist", type=Path, required=True)
    p.add_argument("--expected-instance-id", required=True)
    p.add_argument("--output-root", type=Path, required=True)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text())
    validate_local(manifest, a.manifest.parent, require_copy=False)
    SOURCE = manifest["source_commit"]
    INSTANCE = a.expected_instance_id
    root = a.output_root.absolute()
    if (
        root.exists()
        or root.is_symlink()
        or not root.resolve().is_relative_to(ROOT / ".local-qualification")
        or root.resolve() == (ROOT / ".local-qualification").resolve()
    ):
        raise ValueError(
            "Fresh private output below clone .local-qualification required"
        )
    if a.source_dir.resolve().is_relative_to(
        root.resolve()
    ) or root.resolve().is_relative_to(a.source_dir.resolve()):
        raise ValueError("Copy/source paths must be disjoint")
    guard(root.parent)
    root.mkdir(mode=0o700)
    tools = {
        "runner": Path(__file__),
        "worker": Path(__file__).with_name("sqlite_release_worker_409.py"),
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
        report["disk_budget"] = start_disk_budget(
            a.source_dir, shutil.disk_usage(root).free
        )
        required = report["disk_budget"]["required_start_free_bytes"]
        free = report["disk_budget"]["observed_start_free_bytes"]
        if free < required:
            raise ValueError(
                f"Installed S4 qualification needs {required} free bytes; wait, do not skip S4"
            )
        os.setpriority(os.PRIO_PROCESS, 0, 15)
        declared = plistlib.loads(a.production_plist.read_bytes()).get(
            "EnvironmentVariables", {}
        )
        if (
            Path(declared.get("MEMORY_V4_DIR", "")).resolve() != a.source_dir.resolve()
            or declared.get("MEMORY_V4_STORE_BACKEND") != "sqlite_s1"
            or declared.get("MEMORY_V4_WORK_STATE") != "1"
        ):
            raise ValueError(
                "Source path/backend/tasks must match the production plist"
            )
        report["production_plist_sha256"] = digest(a.production_plist)
        report["expected_instance_id"] = INSTANCE
        report["source_dir"] = str(a.source_dir.resolve())
        profile = profile_environment(a.production_plist)
        paths = json.loads(a.environments.read_text())
        if not (
            paths.get("ok") is True
            and paths.get("version") == manifest["version"]
            and paths.get("source_commit") == SOURCE
            and paths.get("artifacts") == manifest["artifacts"]
        ):
            raise ValueError(
                "Installed environments are not qualified for this manifest"
            )
        interpreters = {
            "baseline": a.baseline_python.absolute(),
            **{
                k: Path(paths[k + "_environment"]).absolute() / "bin/python"
                for k in ("wheel", "sdist")
            },
        }
        for executable in interpreters.values():
            if not executable.is_file() or ROOT in executable.parents:
                raise ValueError("Installed environments must be outside checkout")
        (root / "home").mkdir(mode=0o700)
        plan = {
            "format": "sqlite-release-409-v1",
            "source_commit": SOURCE,
            "version": manifest["version"],
            "queries": list(QUERIES),
            "at_time": time.time(),
            "expected_instance_id": INSTANCE,
            "criterion": {
                "startup_seconds_max": 120,
                "total_seconds_max": 900,
                "corpus_encoded_texts": 0,
                "exact_rankings": True,
                "authority_identical": True,
                "search_non_vacuity": SEARCH_CRITERION,
            },
        }
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan, indent=2) + "\n")
        (root / "copy-owner.json").write_text('{"owner":"sqlite-release-409-v1"}\n')
        worker = root / "worker.py"
        shutil.copyfile(
            Path(__file__).with_name("sqlite_release_worker_409.py"), worker
        )
        shutil.copyfile(tools["guard"], root / "qualification_guard.py")
        shutil.copyfile(tools["capacity"], root / "qualification_limits.py")
        for module in (
            "schema8_inventory.py",
            "schema8_unchanged.py",
            "qualify_schema8_copy.py",
            "search_qualification.py",
            "release_copy_worker.py",
        ):
            shutil.copyfile(ROOT / "scripts" / module, root / module)
        env = {k: os.environ[k] for k in ("PATH", "LANG") if k in os.environ}
        profile = {
            **profile,
            **{
                k: v
                for k, v in declared.items()
                if k in ("HF_HUB_CACHE", "MEMORY_V4_CL100K_PATH")
            },
        }
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

        for setting in (
            "MEMORY_V4_SQLITE_BACKUP_MAX_BYTES",
            "MEMORY_V4_SQLITE_STORAGE_POLICY",
        ):
            if setting in os.environ:
                env[setting] = os.environ[setting]

        def monitor(path):
            guard(path)
            if time.monotonic() - started > 900:
                raise RuntimeError("Copy total time budget exceeded")

        command(
            [
                str(interpreters["wheel"]),
                "-B",
                str(worker),
                "--plan",
                str(plan_path),
                "--kind",
                "snapshot",
                "--snapshot-source",
                str(a.source_dir.absolute()),
            ],
            root,
            env,
            root / "snapshot.log",
            monitor,
        )
        logical = json.loads((root / "snapshot.json").read_text())
        report["snapshot"] = logical
        if logical["store_instance_id"] != INSTANCE or logical["schema_version"] != 8:
            raise ValueError("Source identity/schema mismatch")
        cut_sha = digest(root / "snapshot.sqlite3")
        for name in ("baseline", "wheel", "sdist"):
            target = root / (name + "-store")
            target.mkdir(mode=0o700)
            shutil.copyfile(root / "snapshot.sqlite3", target / "store.sqlite3")
            (target / "store.sqlite3").chmod(0o600)
        for name in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"):
            executable = interpreters[name.removesuffix("-warm")]
            env["MEMORY_V4_BUILD_COMMIT"] = SOURCE
            if name == "baseline":
                env.pop("MEMORY_V4_BUILD_COMMIT", None)
            command(
                [
                    str(executable),
                    "-B",
                    str(worker),
                    "--plan",
                    str(plan_path),
                    "--kind",
                    name,
                ],
                root,
                env,
                root / (name + ".log"),
                monitor,
            )
            report["workers"][name] = json.loads((root / (name + ".json")).read_text())
        if digest(root / "snapshot.sqlite3") != cut_sha:
            raise ValueError("Sealed source cut changed")
        if digest(a.production_plist) != report["production_plist_sha256"]:
            raise ValueError("Production definition changed during qualification")
        report["source_cut_sha256"] = cut_sha
        report["search_hit_counts"] = {
            name: search_hit_counts(
                row["search"],
                QUERIES,
                worker=name,
                reported=row.get("search_hit_counts"),
            )
            for name, row in report["workers"].items()
        }
        report.update(
            authority_identical=True,
            search_identical_to_baseline=True,
            rankings_identical_between_artifacts_and_reopen=True,
            warm_encoded_texts=0,
        )
        # Reuse the established private --warm HTTP/doctor drill, with this
        # harness's effective guard. No production port/token/service used.
        import qualify_release_copy as old

        old_guard = old.resources
        try:
            old.resources = monitor
            report["doctor"] = private_doctor(
                interpreters["wheel"],
                root / "wheel-store",
                root,
                env,
                SOURCE,
                version=manifest["version"],
            )
        finally:
            old.resources = old_guard
        report["s4"] = {}
        for kind in ("wheel", "sdist"):
            s4root = root / ("s4-" + kind)
            s4root.mkdir(mode=0o700)
            s4env = {
                **env,
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
            command(
                [str(interpreters[kind]), "-B", "-c", code],
                root,
                s4env,
                s4root / "s4.log",
                monitor,
            )
            lines = (s4root / "s4.log").read_text().splitlines()
            report["s4"][kind] = json.loads(
                next(line for line in reversed(lines) if line.startswith("{"))
            )
        report["seconds"] = time.monotonic() - started
        report["ok"] = True
        validate_copy(report, manifest)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        report["seconds"] = time.monotonic() - started
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {k: report.get(k) for k in ("ok", "error", "source_sql_writes", "seconds")}
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
