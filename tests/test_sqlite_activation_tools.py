"""Activation tooling on tiny disposable Hash stores; never production/ML/TCP."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import qualify_sqlite_activation as runner
import sqlite_activation_worker as worker
import migrate_sqlite_activation as adapter
import prepare_sqlite_activation_plists as plists
import qualify_sqlite_prepared_store as prepared


def source(directory):
    from memory_os_v4 import Config, MemoryV4
    with MemoryV4(config=Config(data_dir=str(directory), embed_model='hash', hash_dim=16)) as engine:
        engine.add_episode('Synthetic London preference', item_id='old', project='p')
        engine.add_facts([{'item_id': 'fact', 'text': 'Synthetic Lyon preference'}], project='p')
        engine.blocks.set('project_state', 'Synthetic initial state', project='p')


def test_copy_lifecycle_keeps_identity_cache_history_and_post_write_rollback(tmp_path, monkeypatch):
    from memory_os_v4.ops import sqlite_backup as s4
    source(tmp_path / 'snapshot')
    before = runner.inventory(tmp_path / 'snapshot')
    monkeypatch.setenv('MEMORY_V4_EMBED_MODEL', 'hash')
    monkeypatch.setenv('MEMORY_V4_HASH_DIM', '16')
    monkeypatch.setattr(s4.Budget, '__init__', lambda *a, **k: None)
    monkeypatch.setattr(s4.Budget, 'check', lambda *a: None)
    report = worker.run_copy(tmp_path, ['Synthetic London preference'], 2000000000, lambda: None,
                             private_drill=False)
    assert report['ok'], report.get('error')
    assert report['service_drill_qualified'] is False  # Unit fixture never claims TCP.
    assert report['source_copy_unchanged']
    assert runner.inventory(tmp_path / 'snapshot') == before
    assert report['timings']['cold']['corpus_encoded_texts'] > 0
    assert report['timings']['warm']['corpus_encoded_texts'] == 0
    assert report['online_backup']['qualified'] is True
    assert report['private_drill']['store_instance_id'] != report['store_instance_id']
    assert report['rollback_verified_after_new_writes']
    assert (tmp_path / 'rollback-jsonl/task-versions.jsonl').stat().st_size > 0
    proof = prepared.check_store(tmp_path / 'sqlite', report,
        {'queries': ['Synthetic London preference'], 'at_time': 2000000000}, lambda: None)
    assert proof['ok'] and proof['reopen_cache_misses'] == 0
    bad = {**report, 'store_instance_id': 'wrong-identity'}
    with pytest.raises(ValueError, match='identity/cut'):
        prepared.check_store(tmp_path / 'sqlite', bad,
            {'queries': ['Synthetic London preference'], 'at_time': 2000000000}, lambda: None)
    assert sum(p.stat().st_size for p in tmp_path.rglob('*') if p.is_file()) < 16 * 1024**2
    shutil.rmtree(tmp_path)  # Successful synthetic fixture only; failures retained.


def test_frozen_probe_comparison_refuses_reordered_missing_and_extra_queries():
    baseline = {'q': {'ids': ['a', 'b']}}
    worker.compare_probes(baseline, {'cold': baseline, 'warm': baseline})
    for bad in ({'q': {'ids': ['b', 'a']}}, {}, {**baseline, 'extra': {'ids': []}}):
        with pytest.raises(ValueError, match='Frozen search probes differ'):
            worker.compare_probes(baseline, {'cold': bad})


def test_copy_profile_never_inherits_live_paths_or_secrets(tmp_path):
    path = tmp_path / 'server.plist'
    env = {'MEMORY_V4_DIR': '/live', 'MEMORY_V4_TOKEN_FILE': '/private-token',
           'MEMORY_V4_BACKUP_KEY': '/private-key', 'MEMORY_V4_TOKEN': 'synthetic-secret',
           'MEMORY_V4_SERVICE_LABEL': 'live', 'MEMORY_V4_EMBED_MODEL': 'hash',
           'MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE': 'attested-v1', 'MEMORY_V4_USE_RECENCY': '0'}
    path.write_bytes(plistlib.dumps({'EnvironmentVariables': env}))
    profile = runner.profile_environment(path)
    assert not any(k in runner.DROP for k in profile)
    assert 'MEMORY_V4_TOKEN' not in profile
    assert profile['MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE'] == 'attested-v1'
    assert profile['MEMORY_V4_USE_RECENCY'] == '0'
    env['MEMORY_V4_PROJECT_REGISTRY'] = '/registry'
    path.write_bytes(plistlib.dumps({'EnvironmentVariables': env}))
    with pytest.raises(ValueError, match='Registry'):
        runner.profile_environment(path)


def test_snapshot_only_is_immutable_and_existing_output_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'resources', lambda path: {'free_bytes': 9_000_000_000})
    monkeypatch.setattr(runner.os, 'getpriority', lambda *args: 15)
    source(tmp_path / 'source')
    before = runner.inventory(tmp_path / 'source')
    out = tmp_path / '.local-qualification/snapshot-test'
    argv = ['tool', '--source', str(tmp_path / 'source'), '--output', str(out), '--snapshot-only']
    monkeypatch.setattr(sys, 'argv', argv)
    assert runner.main() == 0
    report = json.loads((out / 'report.json').read_text())
    assert report['ok'] and report['production_modified'] is False
    assert runner.inventory(tmp_path / 'source') == before
    assert (out / 'snapshot/writer.lock').exists()
    proof = (out / 'report.json').read_bytes()
    with pytest.raises(SystemExit):
        runner.main()
    assert (out / 'report.json').read_bytes() == proof
    shutil.rmtree(tmp_path)


def test_worker_executable_preserves_venv_symlink_and_plan_precedes_work(tmp_path, monkeypatch):
    import verify_release_manifest as gate
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    monkeypatch.setattr(runner, 'resources', lambda path: {})
    monkeypatch.setattr(runner.os, 'getpriority', lambda *a: 15)
    monkeypatch.setattr(gate, 'validate_local', lambda *a, **k: None)
    monkeypatch.setattr(gate, 'validate_relay', lambda *a, **k: None)
    source(tmp_path / 'source')
    release = tmp_path / 'release'
    release.mkdir()
    (release / 'installed-relay-proof.json').write_text('{}')
    (release / 'manifest.json').write_text(json.dumps({'version': '4.0.5', 'release_ready': True,
        'source_commit': 'a' * 40, 'artifacts': [{'name': 'fake.whl', 'sha256': 'b' * 64}]}))
    plist = tmp_path / 'server.plist'
    plist.write_bytes(plistlib.dumps({'EnvironmentVariables': {}}))
    py = tmp_path / 'venv/bin/python'
    py.parent.mkdir(parents=True)
    py.symlink_to(sys.executable)
    out = tmp_path / '.local-qualification/full-test'
    def fake_worker(command, output, env, guard, timeout):
        assert command[0] == str(py.absolute())
        assert command[0] != str(py.resolve())
        assert (output / 'plan.json').exists()
        assert env['PYTHONPATH'] == '' and env['HF_HUB_OFFLINE'] == '1'
        assert not any(k.startswith(('OPENAI_', 'ANTHROPIC_')) for k in env)
        (output / 'worker.json').write_text(json.dumps({'ok': True, 'service_drill_qualified': True}))
    monkeypatch.setattr(runner, 'run_worker', fake_worker)
    monkeypatch.setattr(sys, 'argv', ['tool', '--source', str(tmp_path / 'source'), '--output', str(out),
        '--runtime-python', str(py), '--release-manifest', str(release / 'manifest.json'),
        '--production-plist', str(plist)])
    assert runner.main() == 0
    assert json.loads((out / 'report.json').read_text())['ok']
    shutil.rmtree(tmp_path)


def test_normal_migration_keeps_modern_l3_status_and_original_export(tmp_path):
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.sqlite_migrate import export, _files
    original, target = tmp_path / 'source', tmp_path / 'sql'
    with MemoryV4(config=Config(data_dir=str(original), embed_model='hash', hash_dim=16)) as engine:
        identity = engine.store.meta['store_instance_id']
        engine.execute_request('memory_add_episode', 'p', {}, 'modern-ack',
            lambda: {'id': engine.add_episode('Synthetic acknowledged modern receipt', project='p').id},
            raw_args_sha256='b' * 64)
    before = _files(original)
    simulated = adapter.migrate_with_attestation(original, target)
    assert simulated['dry_run'] and not target.exists()
    result = adapter.migrate_with_attestation(original, target, apply=True)
    assert result['activation_adapter']['l3_receipts_preserved'] == 1
    assert _files(original) == before
    export(target, tmp_path / 'original-export', original=True)
    assert _files(tmp_path / 'original-export') == before
    with MemoryV4(config=Config(data_dir=str(target), store_backend='sqlite_s1',
                                embed_model='hash', hash_dim=16)) as engine:
        server._engine = engine
        try:
            assert engine.store.meta['store_instance_id'] == identity
            r = server.dispatch('memory_request_status', {'request_id': 'modern-ack', 'scope': 'p',
                 'operation': 'memory_add_episode', 'raw_args_sha256': 'b' * 64, 'store_instance_id': identity})
            assert r['status'] == 'completed', r
        finally:
            server._engine = None
    shutil.rmtree(tmp_path)


@pytest.mark.parametrize('boundary', ['after_s3_import', 'after_receipt_attestation',
                                    'after_attestation_commit', 'before_activation_publish'])
def test_adapter_failure_never_publishes_partial_or_changes_source(tmp_path, boundary):
    from memory_os_v4 import Config, MemoryV4
    from memory_os_v4.sqlite_migrate import _files
    original, target = tmp_path / 'source', tmp_path / 'sql'
    with MemoryV4(config=Config(data_dir=str(original), embed_model='hash', hash_dim=16)) as engine:
        engine.add_episode('Synthetic source item', project='p')
        engine.execute_request('synthetic', 'p', {}, 'ack', lambda: {'ok': True}, raw_args_sha256='b' * 64)
    before = _files(original)
    def fault(point):
        if point == boundary:
            raise OSError('synthetic injection')
    with pytest.raises(OSError, match='injection'):
        adapter.migrate_with_attestation(original, target, apply=True, fault=fault)
    assert not target.exists()
    assert _files(original) == before
    assert list(tmp_path.glob('.sql.activation-*'))  # Evidence retained by the tool.
    shutil.rmtree(tmp_path)  # Successful injection test owns this synthetic fixture.


def test_inactive_plists_preserve_schedules_paths_profile_and_flags(tmp_path):
    original = {'Label': 'synthetic.server', 'ProgramArguments': ['old'], 'KeepAlive': True,
        'EnvironmentVariables': {'MEMORY_V4_BUILD_COMMIT': 'a' * 40,
            'MEMORY_V4_CL100K_PATH': '/offline-table', 'MEMORY_V4_DIR': '/old-source'}}
    backup = {'Label': 'synthetic.backup', 'StartCalendarInterval': {'Hour': 4, 'Minute': 0},
        'EnvironmentVariables': {'MEMORY_V4_BACKUP_DIR': '/original-archives',
                                 'MEMORY_V4_BACKUP_KEY': '/original-key-file'}}
    health = {'Label': 'synthetic.health', 'StartCalendarInterval': {'Hour': 8, 'Minute': 30}}
    before = json.dumps([original, backup, health], sort_keys=True)
    jobs = plists.render(original, backup, health, tmp_path / 'sql', '/qualified/bin/python', work_state=True)
    assert json.dumps([original, backup, health], sort_keys=True) == before
    for job in jobs.values():
        env = job['EnvironmentVariables']
        assert env['MEMORY_V4_WORK_STATE'] == '1' and env['MEMORY_V4_WRITE_MODE'] == 'read_only'
        assert env['MEMORY_V4_STORE_BACKEND'] == 'sqlite_s1'
        assert env['MEMORY_V4_BACKUP_DIR'] == '/original-archives'
        assert env['MEMORY_V4_CL100K_PATH'] == '/offline-table'
    assert jobs['synthetic.backup']['StartCalendarInterval'] == backup['StartCalendarInterval']
    assert jobs['synthetic.backup']['ProgramArguments'][-4:] == ['--backend', 'sqlite', '--online', '--apply']
    assert '--reconcile-forgets' in jobs['synthetic.server.restore-drill']['ProgramArguments']
    assert jobs['synthetic.server.restore-drill']['StartCalendarInterval']['Weekday'] == 1
    health['EnvironmentVariables'] = {'MEMORY_V4_BACKUP_DIR': '/conflict'}
    with pytest.raises(ValueError, match='Conflicting operational paths'):
        plists.render(original, backup, health, tmp_path / 'sql', '/qualified/bin/python', work_state=False)


def test_runbook_environment_launcher_preserves_stdin_for_readiness_checks(tmp_path):
    import os
    doc = (Path(__file__).parents[1] / 'v4/docs/SQLITE_ACTIVATION_4_0_5.md').read_text()
    routine = doc[doc.index('sqlite_env_run() {'):doc.index('\nsqlite_env_run nice')]
    path = tmp_path / 'com.romain.memory-os-v4.plist'
    path.write_bytes(plistlib.dumps({'EnvironmentVariables': {'SYNTHETIC_PROFILE': 'loaded'}}))
    result = subprocess.run(['/bin/zsh', '-c', routine + '\nsqlite_env_run "$SQLITE_PY" -c '
        "'import os,sys; print(os.environ[\"SYNTHETIC_PROFILE\"]+\":\"+sys.stdin.read())'"],
        input='stdin-preserved', text=True, capture_output=True, timeout=10,
        env={**os.environ, 'SQLITE_PY': sys.executable, 'SQLITE_STAGE': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'loaded:stdin-preserved'
