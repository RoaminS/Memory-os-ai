"""Model-free RC checks: migration/reopen and non-vacuity cannot be waived."""

import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pytest
import release_407_checks as gate
from release_copy_worker import QUERIES


def proof():
    profile = {
        "device": "mps",
        "dtype": "native",
        "batch_size": 32,
        "max_seq_length": 512,
        "parameter_devices": ["mps"],
        "model_revision": "revision",
        "encoder_source_sha256": "encoder",
    }
    migration = {
        "ok": True,
        "from_schema": 7,
        "to_schema": 8,
        "store_instance_id": "i",
        "tables_preserved": {"facts": {"rows": 2, "sha256": "a"}},
    }
    row = {
        "ok": True,
        "store_instance_id": "i",
        "total": 2,
        "search": {q: ["id"] for q in QUERIES},
        "search_hit_counts": {q: 1 for q in QUERIES},
        "encoding_profile": profile,
        "cache_misses": 0,
        "corpus_encoded_texts": 0,
        "startup_seconds": 1,
        "migration": migration,
        "migration_seconds": 1,
        "journal": {"generation": "g", "coverage_start": 1, "events": 0},
        "authority": {"sha256": "a"},
    }
    caps = {
        "store": {"backend": "sqlite_s1", "instance_id": "i"},
        "work_state": True,
        "retrieval_ready": True,
        "projects": {"registry_enabled": False},
        "policies": {
            "write_mode": "read_only",
            "bm25_profile": "scoped_literal",
            "lexical_guard_k": 3,
        },
        "runtime": {"ok": True},
        "brief": {"tokenizer": {"ready": True}},
    }
    manifest = {
        "version": "4.0.7rc1",
        "source_commit": "c",
        "artifacts": [],
        "copy_scripts_sha256": {},
    }
    p = {
        "ok": True,
        "version": "4.0.7rc1",
        "baseline_version": "4.0.5",
        "source_commit": "c",
        "artifacts": [],
        "source_sql_writes": False,
        "production_modified": False,
        "scripts_sha256": {},
        "expected_instance_id": "i",
        "workers": {
            n: copy.deepcopy(row)
            for n in ("baseline", "wheel", "sdist", "wheel-warm", "sdist-warm")
        },
        "seconds": 1,
        "search_identical_to_baseline": True,
        "doctor": {
            "ok": True,
            "ready": {
                "version": "4.0.7rc1",
                "commit": "c",
                "identity_source": "embedded_descriptor",
                "encoding_profile": profile,
                "capabilities": caps,
            },
        },
        "s4": {
            n: {
                "backup": {
                    "ok": True,
                    "qualified": True,
                    "source_sql_writes": False,
                    "store_instance_id": "i",
                    "verified": True,
                },
                "drill": {
                    "ok": True,
                    "private_reopen_verified": True,
                    "verified": True,
                    "forget_reconciliation": "complete",
                    "restored_from_instance_id": "i",
                },
            }
            for n in ("wheel", "sdist")
        },
    }
    return p, manifest


def test_valid_copy_preserves_installed_migration_contract():
    p, m = proof()
    gate.validate_copy(p, m)


@pytest.mark.parametrize(
    "target", ["baseline", "wheel", "sdist", "wheel-warm", "sdist-warm"]
)
def test_empty_equality_is_never_a_qualified_search(target):
    p, m = proof()
    p["workers"][target]["search"] = {q: [] for q in QUERIES}
    p["workers"][target]["search_hit_counts"] = {q: 0 for q in QUERIES}
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


@pytest.mark.parametrize(
    "field,value",
    [("cache_misses", 1), ("corpus_encoded_texts", 1), ("startup_seconds", 121)],
)
def test_cache_or_time_deviation_refused(field, value):
    p, m = proof()
    p["workers"]["wheel"][field] = value
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


def test_changed_old_authority_and_reopen_generation_refused():
    p, m = proof()
    p["workers"]["sdist"]["migration"]["tables_preserved"]["facts"]["sha256"] = (
        "changed"
    )
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)
    p, m = proof()
    p["workers"]["wheel-warm"]["journal"]["generation"] = "other"
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


def test_backup_drill_must_reopen_privately():
    p, m = proof()
    p["s4"]["sdist"]["drill"]["private_reopen_verified"] = False
    with pytest.raises(ValueError):
        gate.validate_copy(p, m)


def test_rc_verifier_dispatch_is_fail_closed(tmp_path):
    import verify_release_manifest as v

    path = tmp_path / "manifest.json"
    path.write_text('{"version":"4.0.7rc1"}')
    assert v.main([str(path)]) == 1


def test_real_worker_search_shape_compares_ranks_not_elapsed_seconds():
    p, m = proof()
    for n, row in enumerate(p["workers"].values()):
        row["search"] = {
            q: {"ids": ["id", "other"], "scores": [1, 0.5], "seconds": n + 0.1}
            for q in QUERIES
        }
        row["search_hit_counts"] = {q: 2 for q in QUERIES}
    gate.validate_copy(p, m)
    p["workers"]["sdist"]["search"][QUERIES[0]]["ids"].reverse()
    with pytest.raises(ValueError, match="Worker mismatch"):
        gate.validate_copy(p, m)


def test_env_preparation_executes_actual_sdist_install_offline(tmp_path, monkeypatch):
    import json
    import prepare_release_407_envs as prep
    from memory_os_v4.ops import provision

    monkeypatch.setattr(prep, "validate_local", lambda *a, **k: None)
    monkeypatch.setattr(prep, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(provision, "guard", lambda path: None)
    wheel = tmp_path / "wheel"
    wheel.mkdir()
    (wheel / "__init__.py").write_text("")
    dist = tmp_path / "wheel-1.dist-info"
    dist.mkdir()
    monkeypatch.setattr(
        prep.subprocess,
        "check_output",
        lambda *a, **k: json.dumps([str(wheel), str(dist)]),
    )

    def create(builder, path):
        (path / "lib/python3.11/site-packages").mkdir(parents=True)
        (path / "bin").mkdir()
        (path / "bin/python").touch()

    monkeypatch.setattr(prep.venv.EnvBuilder, "create", create)
    commands = []
    monkeypatch.setattr(prep, "command", lambda cmd, *a: commands.append(cmd))
    release = tmp_path / "release"
    (release / "artifacts").mkdir(parents=True)
    artifacts = []
    for name in ("pkg.whl", "pkg.tar.gz"):
        f = release / "artifacts" / name
        f.write_bytes(name.encode())
        artifacts.append({"name": name, "sha256": prep.digest(f)})
    m = {"version": "4.0.7rc1", "source_commit": "c", "artifacts": artifacts}
    path = release / "manifest.json"
    path.write_text(json.dumps(m))
    out = tmp_path / "external-venvs"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prep",
            "--manifest",
            str(path),
            "--output",
            str(out),
            "--build-python",
            sys.executable,
        ],
    )
    assert prep.main() == 0
    install = [c for c in commands if "install" in c]
    assert len(install) == 2
    assert install[0][-1].endswith(".whl") and install[1][-1].endswith(".tar.gz")
    assert all(
        "--no-index" in c and "--no-deps" in c and "--no-build-isolation" in c
        for c in install
    )
    assert json.loads((out / "environments.json").read_text())["ok"] is True
    assert (out / "sdist/lib/python3.11/site-packages/wheel/__init__.py").is_file()
