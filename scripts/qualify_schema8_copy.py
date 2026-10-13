#!/usr/bin/env python3
"""Guarded source-only schema7->8 copy/search rehearsal; no service control.

A source rehearsal is NOT installed wheel/sdist or TCP release qualification.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time

from schema8_inventory import fingerprint, inventory, readonly, verify_upgrade
from search_qualification import SEARCH_CRITERION, search_hit_counts
from release_copy_worker import QUERIES

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)


def engine_authority(value):
    tables = value["tables"].copy()
    tables.pop("embedding_cache")  # queries may append derived cache entries
    metadata = dict(tables.pop("store_metadata"))
    metadata.pop("chunk_map", None)
    main = json.loads(metadata["metadata"])
    main.pop("version", None)
    metadata["metadata"] = json.dumps(main, sort_keys=True)
    return fingerprint(
        {
            "schema": value["schema"],
            "objects": value["objects"],
            "tables": tables,
            "metadata": metadata,
        }
    )


def worker(plan_path, kind, layout):
    plan = json.loads(plan_path.read_text())
    root = plan_path.parent.resolve()
    if (
        plan["format"] != "schema8-source-copy-v1"
        or not (root / "owner.json").is_file()
    ):
        raise ValueError("Owned private copy plan required")
    if layout:
        sys.path.insert(0, str(layout / "v4/src"))
    import memory_os_v4
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.embed import Embedder, _resolve_device
    from memory_os_v4.sqlite_store import SQLiteStore

    if kind == "baseline":
        import importlib.metadata as md

        if md.version("memory-os-ai") != "4.0.5" or memory_os_v4.__version__ != "4.0.5":
            raise ValueError("Installed baseline 4.0.5 required")
        if not Path(memory_os_v4.__file__).is_relative_to(
            Path(sys.executable).parent.parent
        ):
            raise ValueError("Installed baseline only")
    else:
        from memory_os_v4.readiness import build_identity

        if (
            not layout
            or not Path(memory_os_v4.__file__).is_relative_to(layout)
            or build_identity()["commit"] != plan["candidate_commit"]
            or build_identity()["identity_source"] != "env_declared"
        ):
            raise ValueError("Archived source identity mismatch")
    store_path = root / ("baseline-store" if kind == "baseline" else "candidate-store")
    cfg = Config.from_env()
    cfg.data_dir, cfg.store_backend, cfg.work_state, cfg.write_mode = (
        str(store_path),
        "sqlite_s1",
        True,
        "read_only",
    )
    if (
        cfg.embed_model == "hash"
        or _resolve_device(cfg.device) != "mps"
        or cfg.project_registry
    ):
        raise ValueError("Production MPS encoder / inactive registry required")
    if cfg.bm25_profile != "scoped_literal" or cfg.lexical_guard_k != 3:
        raise ValueError("Production C4 required")

    # No downloading / external connections. The private HTTP drill is separate.
    def denied(*args, **kwargs):
        raise RuntimeError("IP network forbidden in copy worker")

    socket.socket.connect = socket.create_connection = denied
    with closing(readonly(store_path / "store.sqlite3")) as con:
        old = inventory(con)
    proof = None
    started = time.time()
    migration_start = time.monotonic()
    if kind == "candidate":
        with closing(SQLiteStore(store_path)) as store:
            upgraded = inventory(store._connection)
            proof = verify_upgrade(old, upgraded, started=started, ended=time.time())
        before = upgraded
    else:
        before = old
    migration_seconds = time.monotonic() - migration_start if proof else 0
    encoded = []
    original = Embedder.encode

    def counted(self, texts):
        encoded.extend(texts)
        return original(self, texts)

    Embedder.encode = counted
    t = time.monotonic()
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()
        seconds, corpus = time.monotonic() - t, len(encoded)
        engine.store.touch = lambda *args, **kwargs: (
            None
        )  # COPY ONLY; prevent access-history writes.
        queries = {}
        for query in plan["queries"]:
            queries[query] = [
                h.item.id
                for h in engine.search(
                    query, project=None, top_k=10, at_time=plan["at_time"], rerank=False
                )
            ]
        after = inventory(engine.store._connection)
        if engine_authority(before) != engine_authority(after):
            raise ValueError(
                "Authority/history/receipts/tasks/journal changed during reads"
            )
        journal = dict(after["tables"]["store_metadata"]).get("change_journal")
        journal = json.loads(journal) if journal else None
        result = {
            "ok": True,
            "kind": kind,
            "package_version": memory_os_v4.__version__,
            "migration_seconds": migration_seconds,
            "startup_seconds": seconds,
            "corpus_encoded_texts": corpus,
            "cache_hits": engine._embedding_cache.hits,
            "cache_misses": engine._embedding_cache.misses,
            "encoding_profile": engine.status()["encoding_profile"],
            "search": queries,
            "search_hit_counts": {q: len(ids) for q, ids in queries.items()},
            "store_instance_id": engine.store.meta["store_instance_id"],
            "total": engine.status()["total"],
            "authority_sha256": engine_authority(after),
            "migration": proof,
            "journal": {
                "generation": journal["generation"],
                "coverage_start": journal["coverage_start"],
                "events": len(after["tables"]["change_events"]),
            }
            if journal
            else None,
        }
    write(root / (kind + ".json"), result)


def compare(rows, expected_identity):
    if set(rows) != {"baseline", "candidate", "candidate-warm"}:
        raise ValueError("Missing/unexpected copy workers")
    base, first, warm = (
        rows[name] for name in ("baseline", "candidate", "candidate-warm")
    )
    counts = {}
    for name, row in rows.items():
        counts[name] = search_hit_counts(row["search"], QUERIES, worker=name,
                                        reported=row.get("search_hit_counts"))
        if (
            not row["ok"]
            or row["store_instance_id"] != expected_identity
            or row["total"] != base["total"]
            or row["encoding_profile"] != base["encoding_profile"]
            or row["search"] != base["search"]
            or row["corpus_encoded_texts"] != 0
            or row["cache_misses"] != 0
            or row["startup_seconds"] > 120
        ):
            raise ValueError("Copy/search/profile/cache criterion failed: " + name)
    if (
        not first["migration"]["ok"]
        or first["migration"]["from_schema"] != 7
        or first["migration"]["to_schema"] != 8
        or first["migration_seconds"] > 120
        or first["journal"] != warm["journal"]
        or first["journal"]["events"] != 0
        or first["authority_sha256"] != warm["authority_sha256"]
    ):
        raise ValueError("Migration / reopen journal criterion failed")
    return {
        "ok": True,
        "search_identical_to_baseline": True,
        "search_non_vacuity": SEARCH_CRITERION,
        "search_hit_counts": counts,
        "corpus_encoded_texts": 0,
        "release_qualified": False,
        "production_activation": False,
    }


def offline_environment(parent, profile, home, operator_home, minimum):
    """A private HOME must not redirect discovery of the existing offline weights."""
    env = {
        k: v
        for k, v in parent.items()
        if k in ("PATH", "LANG", "HF_HOME", "HF_HUB_CACHE", "MEMORY_V4_CL100K_PATH")
    }
    env.setdefault("HF_HOME", str(operator_home / ".cache/huggingface"))
    env.update(
        profile,
        HOME=str(home),
        PYTHONPATH="",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        MEMORY_V4_MIN_FREE_BYTES=str(minimum),
        MEMORY_V4_WORK_STATE="1",
        MEMORY_V4_STORE_BACKEND="sqlite_s1",
        MEMORY_V4_WRITE_MODE="read_only",
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1",
    )
    return env


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--worker", choices=("baseline", "candidate", "candidate-warm"))
    p.add_argument("--plan", type=Path)
    p.add_argument("--layout", type=Path)
    p.add_argument("--candidate-commit")
    p.add_argument("--baseline-python", type=Path)
    p.add_argument("--source-dir", type=Path)
    p.add_argument("--production-plist", type=Path)
    p.add_argument("--expected-instance-id")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    if a.worker:
        worker(a.plan, a.worker, a.layout)
        return 0
    if not all(
        (
            a.candidate_commit,
            a.baseline_python,
            a.source_dir,
            a.production_plist,
            a.expected_instance_id,
            a.output,
        )
    ):
        p.error("All copy parameters required")
    from qualify_distribution_d1a import command
    from qualify_sqlite_activation import profile_environment, QUERIES

    sys.path.insert(0, str(ROOT / "v4/src"))
    from memory_os_v4.ops.provision import guard, min_free_bytes

    root = a.output.absolute()
    if (
        root.exists()
        or root.is_symlink()
        or not root.resolve().is_relative_to(ROOT / ".local-qualification")
    ):
        raise ValueError(
            "Fresh private output under checkout .local-qualification required"
        )
    source = a.source_dir.resolve()
    if source.is_relative_to(root.resolve()) or root.resolve().is_relative_to(source):
        raise ValueError("Source/copy paths must be disjoint")
    guard(root.parent)
    root.mkdir(parents=True, mode=0o700)
    report = {
        "ok": False,
        "production_activation": False,
        "release_qualified": False,
        "source_sql_writes": False,
        "source_commit": a.candidate_commit,
        "min_free_bytes": min_free_bytes(),
    }
    t = time.monotonic()
    try:
        # Archive attests exact tracked source; no dependence on the worker cwd.
        commit = subprocess.run(
            ["git", "rev-parse", a.candidate_commit + "^{commit}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if commit != a.candidate_commit:
            raise ValueError("Complete candidate commit required")
        report["scripts_sha256"] = {}
        for relative in (
            "scripts/qualify_schema8_copy.py",
            "scripts/schema8_inventory.py",
            "scripts/search_qualification.py",
            "scripts/release_copy_worker.py",
            "scripts/qualify_distribution_d1a.py",
            "scripts/qualify_sqlite_activation.py",
            "v4/src/memory_os_v4/ops/provision.py",
        ):
            sealed_tool = subprocess.run(
                ["git", "show", commit + ":" + relative],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
            actual_tool = (ROOT / relative).read_bytes()
            if sealed_tool != actual_tool:
                raise ValueError(
                    "Copy tooling differs from declared commit: " + relative
                )
            report["scripts_sha256"][relative] = hashlib.sha256(actual_tool).hexdigest()
        archive = subprocess.run(
            ["git", "archive", commit, "v4/src", "pyproject.toml"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        layout = root / "candidate-source"
        layout.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tf:
            tf.extractall(layout, filter="data")
        report["archive_sha256"] = hashlib.sha256(archive).hexdigest()
        profile = profile_environment(a.production_plist)
        import plistlib

        declared = plistlib.loads(a.production_plist.read_bytes()).get(
            "EnvironmentVariables", {}
        )
        if (
            Path(declared.get("MEMORY_V4_DIR", "")).resolve() != source
            or declared.get("MEMORY_V4_STORE_BACKEND") != "sqlite_s1"
            or declared.get("MEMORY_V4_WORK_STATE") != "1"
        ):
            raise ValueError("Source/backend/tasks do not match production definition")
        plist_sha = hashlib.sha256(a.production_plist.read_bytes()).hexdigest()
        plan = {
            "format": "schema8-source-copy-v1",
            "candidate_commit": commit,
            "at_time": time.time(),
            "queries": list(QUERIES),
            "criterion": {
                "exact_authority": True,
                "exact_top10": True,
                "search_non_vacuity": SEARCH_CRITERION,
                "corpus_encoded_texts": 0,
                "cache_misses": 0,
                "startup_seconds_max": 120,
                "migration_seconds_max": 120,
                "total_seconds_max": 900,
            },
            "source_schema": 7,
            "target_schema": 8,
        }
        plan_path = root / "plan.json"
        write(plan_path, plan)
        report["plan_sha256"] = hashlib.sha256(plan_path.read_bytes()).hexdigest()
        write(root / "owner.json", {"owner": "schema8-source-copy-v1"})
        (root / "home").mkdir(mode=0o700)
        # HF_HUB_CACHE can be set in the plist even though the older profile
        # helper did not list that upstream variable.
        if "HF_HUB_CACHE" in declared:
            if not isinstance(declared["HF_HUB_CACHE"], str):
                raise ValueError("Plist cache path must be a string")
            profile["HF_HUB_CACHE"] = declared["HF_HUB_CACHE"]
        env = offline_environment(
            os.environ, profile, root / "home", Path.home(), min_free_bytes()
        )
        os.setpriority(os.PRIO_PROCESS, 0, 15)

        def monitor(path):
            guard(path)
            if time.monotonic() - t > 900:
                raise RuntimeError("Frozen 900-second budget exceeded")

        # Installed baseline performs SQLite API backup: no engine opens production.
        snippet = (
            "import sys, json; import memory_os_v4; "
            'assert memory_os_v4.__version__ == "4.0.5"; '
            "from pathlib import Path; from memory_os_v4.ops.sqlite_backup import snapshot; "
            "print(json.dumps(snapshot(Path(sys.argv[1]),Path(sys.argv[2]))))"
        )
        command(
            [
                str(a.baseline_python.absolute()),
                "-B",
                "-c",
                snippet,
                str(source / "store.sqlite3"),
                str(root / "snapshot.sqlite3"),
            ],
            root,
            env,
            root / "snapshot.log",
            monitor,
        )
        sealed = root / "snapshot.sqlite3"
        cut_sha = hashlib.sha256(sealed.read_bytes()).hexdigest()
        with closing(readonly(sealed)) as con:
            cut = inventory(con)
        meta = json.loads(dict(cut["tables"]["store_metadata"])["metadata"])
        if cut["schema"] != 7 or meta["store_instance_id"] != a.expected_instance_id:
            raise ValueError("Source cut identity/schema mismatch")
        for name in ("baseline", "candidate"):
            path = root / (name + "-store")
            path.mkdir(mode=0o700)
            shutil.copyfile(sealed, path / "store.sqlite3")
            (path / "store.sqlite3").chmod(0o600)
        rows = {}
        for name in ("baseline", "candidate", "candidate-warm"):
            runtime = (
                a.baseline_python.absolute()
                if name == "baseline"
                else Path(sys.executable).absolute()
            )
            worker_env = env.copy()
            argv = [
                str(runtime),
                "-B",
                str(Path(__file__).resolve()),
                "--worker",
                name,
                "--plan",
                str(plan_path),
            ]
            if name != "baseline":
                worker_env["MEMORY_V4_BUILD_COMMIT"] = commit
                argv += ["--layout", str(layout)]
            command(argv, root, worker_env, root / (name + ".log"), monitor)
            rows[name] = json.loads((root / (name + ".json")).read_text())
        report["workers"] = rows
        report.update(compare(rows, a.expected_instance_id))
        if (
            hashlib.sha256(sealed.read_bytes()).hexdigest() != cut_sha
            or hashlib.sha256(a.production_plist.read_bytes()).hexdigest() != plist_sha
        ):
            raise ValueError("Sealed cut or production definition changed")
        report["snapshot_sha256"] = cut_sha
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report.update(ok=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        report["seconds"] = time.monotonic() - t
        write(root / "report.json", report)
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in (
                    "ok",
                    "error",
                    "release_qualified",
                    "production_activation",
                    "seconds",
                )
            }
        )
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
