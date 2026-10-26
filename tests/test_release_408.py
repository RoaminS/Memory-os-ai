"""Model-free RC checks: migration/reopen and non-vacuity cannot be waived."""

import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pytest
import release_408_checks as gate
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
        "migration": None,
        "schema_check": {**migration,"from_schema":8,"to_schema":8},
        "version":"4.0.8",
        "migration_seconds": 1,
        "journal": {"generation": "g", "coverage_start": 1, "events": 0},
        "authority": {"sha256": "a", "schema_version":8},
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
        "readiness_audit":{"max_reuse_seconds":1.0,"live_checks_every_request":True,"untracked_memory_fault_detection_seconds":1.0},
    }
    manifest = {
        "version": "4.0.8",
        "source_commit": "c",
        "artifacts": [],
        "copy_scripts_sha256": {},
    }
    p = {
        "ok": True,
        "version": "4.0.8",
        "baseline_version": "4.0.7",
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
        "authority_identical":True,
        "rankings_identical_between_artifacts_and_reopen":True,
        "warm_encoded_texts":0,
        "doctor": {
            "ok": True,
            "ready": {
                "version": "4.0.8",
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
                    "archive":"private.mosb", "archive_sha256":"sha",
                },
                "drill": {
                    "ok": True,
                    "private_reopen_verified": True,
                    "verified": True,
                    "forget_reconciliation": "complete",
                    "restored_from_instance_id": "i",
                    "level":"service", "archive":"private.mosb", "archive_sha256":"sha",
                    "server_check":{"match":True,"version":"4.0.8","commit":"c","corpus_reencodings":0,"private_only":True,"production_modified":False},
                },
            }
            for n in ("wheel", "sdist")
        },
    }
    p["workers"]["baseline"]["version"] = "4.0.7"
    return p, manifest


def test_valid_copy_preserves_same_schema8_contract():
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
    p["workers"]["sdist"]["schema_check"]["tables_preserved"]["facts"]["sha256"] = (
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
    path.write_text('{"version":"4.0.8"}')
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
    import prepare_release_408_envs as prep
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
    m = {"version": "4.0.8", "source_commit": "c", "artifacts": artifacts}
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


@pytest.mark.parametrize('what', ['reset-journal', 'authority', 'schema', 'missing-s4', 'old-baseline', 'migration'])
def test_no_migration_does_not_waive_authority_or_s4(what):
    p,m = proof()
    if what == 'reset-journal':
        p['workers']['wheel']['journal']['events'] = 0
        p['workers']['baseline']['journal']['events'] = 7
    elif what == 'authority':
        p['workers']['wheel']['authority']['sha256'] = 'other'
    elif what == 'schema':
        p['workers']['baseline']['schema_check']['from_schema'] = 7
    elif what == 'missing-s4':
        p['s4'] = {}
    elif what == 'old-baseline':
        p['baseline_version'] = '4.0.5'
    else:
        p['workers']['wheel']['migration'] = {'ok':True}
    with pytest.raises((ValueError, KeyError)):
        gate.validate_copy(p,m)


def test_existing_nonempty_journal_is_preserved():
    p,m = proof()
    for row in p['workers'].values():
        row['journal']['events'] = 123
    gate.validate_copy(p,m)


def test_exhaustive_same_schema_inventory_on_real_store(tmp_path, monkeypatch):
    from contextlib import closing
    from memory_os_v4 import Config, MemoryV4, server
    from schema8_inventory import inventory
    from schema8_unchanged import verify_unchanged
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',work_state=True,embed_model='hash',hash_dim=16)) as engine:
        monkeypatch.setattr(server, '_engine',engine)
        assert server.dispatch('memory_add_facts',{'project':'P','facts':[{'text':'Synthetic source assertion.'}],'request_id':'fact'})['added']
        assert server.dispatch('memory_task_create',{'project':'P','title':'Synthetic task','request_id':'task'})['ok']
        server.dispatch('memory_block_set',{'project':'P','name':'state','text':'Synthetic state','request_id':'block'})
        before = inventory(engine.store._connection)
        assert before['tables']['change_events'] and before['tables']['request_receipts']
        assert verify_unchanged(before, copy.deepcopy(before))['ok']
        for table in before['tables']:
            altered = copy.deepcopy(before)
            altered['tables'][table].append(['unexpected row'])
            with pytest.raises(ValueError):
                verify_unchanged(before, altered)
        from memory_os_v4.sqlite_store import SQLiteStore
        # No model or retrieval required to establish the same-schema open.
    # Normal writer close intentionally writes a final flush receipt. Establish
    # the sealed cut AFTER it, as the installed harness does with backup().
    with closing(SQLiteStore(tmp_path)) as seeded:
        metadata = dict(seeded.meta)
        metadata['version'] = '4.0.7'
        seeded.save_meta(metadata)
        before = inventory(seeded._connection)
    with closing(SQLiteStore(tmp_path)) as reopened:
        assert verify_unchanged(before, inventory(reopened._connection))['ok']
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',work_state=True,
                               embed_model='hash',hash_dim=16,write_mode='read_only')) as read_only:
        read_only.ensure_retrieval()
        assert verify_unchanged(before, inventory(read_only.store._connection))['ok']


def test_release408_dispatch_remains_closed_without_installed_evidence(tmp_path):
    import verify_release_manifest as verifier
    path=tmp_path/'manifest.json'
    path.write_text('{"version":"4.0.8","release_ready":false}')
    original=path.read_bytes()
    assert verifier.main([str(path)]) == 1
    assert path.read_bytes() == original


def test_every_new_release_tool_is_attested():
    import make_release_408 as make
    wanted={'make_release_408.py','release_408_checks.py','prepare_release_408_envs.py',
            'qualify_sqlite_release_408.py','sqlite_release_worker_408.py','record_release_408_gate.py',
            'score_release_408_gate.py','source_release_checks_408.py','schema8_unchanged.py'}
    assert wanted <= set(make.TOOLS)
    assert all((make.ROOT/'scripts'/name).is_file() for name in make.TOOLS)
    assert make.BASE == '9ecb4d06f68e2c7efddf77aad66f4ed5f147f356'


@pytest.mark.parametrize('bad', ['archive', 'version', 'corpus_reencodings', 'level'])
def test_s4_old_or_unlinked_service_proof_never_qualifies(bad):
    p,m = proof()
    drill=p['s4']['wheel']['drill']
    if bad == 'archive':
        drill['archive'] = 'some-other.mosb'
    elif bad == 'level':
        drill['level'] = 'files'
    else:
        drill['server_check'][bad] = '4.0.7' if bad == 'version' else 1
    with pytest.raises(ValueError):
        gate.validate_copy(p,m)


def test_version_only_guard_uses_qualified_functional_base(monkeypatch, tmp_path):
    import subprocess
    old='__version__ = "4.0.8"\n'
    new='__version__ = "4.0.8"\n'
    calls=[]
    def query(argv, **kwargs):
        calls.append(argv)
        if argv[1] == 'diff':
            return ''
        return old if argv[2].startswith('6182aa5') else new
    monkeypatch.setattr(subprocess, 'check_output', query)
    gate.verify_engine_version_only(tmp_path, 'c'*40, '4.0.8')
    assert calls[0][3] == '9ecb4d06f68e2c7efddf77aad66f4ed5f147f356'
    monkeypatch.setattr(subprocess, 'check_output', lambda *a,**k: 'v4/src/memory_os_v4/ops/doctor.py\n')
    with pytest.raises(ValueError, match='beyond version'):
        gate.verify_engine_version_only(tmp_path, 'c'*40, '4.0.8')


def test_copy_budget_keeps_s4_reserve_even_with_three_gb_override(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from qualify_sqlite_release_408 import start_disk_budget
    monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', '3000000000')
    monkeypatch.delenv('MEMORY_V4_SQLITE_STORAGE_POLICY', raising=False)
    assert start_disk_budget(tmp_path, 5_200_000_000)['required_start_free_bytes'] == 7_000_000_000
    fake = SimpleNamespace(glob=lambda pattern:[SimpleNamespace(is_file=lambda:True,
                                        stat=lambda:SimpleNamespace(st_size=200_000_000))])
    assert start_disk_budget(fake, 5_200_000_000)['required_start_free_bytes'] == 9_000_000_000


def test_exact_tool_inventory_cannot_be_downgraded_or_substituted():
    import make_release_408 as make
    hashes={name:gate.digest(make.ROOT/'scripts'/name) for name in make.TOOLS}
    gate.verify_tooling(make.ROOT, hashes)
    for change in ('missing','extra','changed'):
        altered=dict(hashes)
        if change == 'missing':
            altered.pop('schema8_unchanged.py')
        elif change == 'extra':
            altered['unsealed.py']='0'*64
        else:
            altered['qualify_sqlite_release_408.py']='0'*64
        with pytest.raises(ValueError):
            gate.verify_tooling(make.ROOT, altered)
