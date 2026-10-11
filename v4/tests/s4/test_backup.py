"""Small Hash/SQLite fixtures: pinned cut, failure boundaries, crypto/quarantine."""
import io
import gzip
import datetime as dt
import hashlib
import json
import signal
import subprocess
import sys
import os
from pathlib import Path
import plistlib
import tarfile
from types import SimpleNamespace

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.ops import sqlite_backup as s4
from memory_os_v4.ops import storage
from memory_os_v4.ops.auth import write_secret_file
from memory_os_v4.ops.backup import BackupError, encrypt, verified_archives
from memory_os_v4.ops.cli import _plist, cmd_setup
from memory_os_v4.ops.health import last_drill
from memory_os_v4.ops.settings import OpsSettings


@pytest.fixture
def case(tmp_path):
    settings = OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=tmp_path / 'token',
                           store_dir=tmp_path / 'store', backup_dir=tmp_path / 'backups',
                           backup_key=tmp_path / 'key', ops_dir=tmp_path / 'ops', service_label=None)
    write_secret_file(settings.backup_key, b'k' * 32)
    with MemoryV4(config=Config(data_dir=str(settings.store_dir), embed_model='hash',
                              store_backend='sqlite_s1')) as engine:
        engine.add_episode('Synthetic evidence one', project='p')
        engine.blocks.set('working_rules', 'Synthetic prior block', project='p')
        engine.execute_request('memory_block_set', 'p', {'text': 'Synthetic current block'},
                               'fixture-explicit',
                               lambda: engine.blocks.set('working_rules', 'Synthetic current block', project='p'),
                               raw_args_sha256='0' * 64)
        yield settings, engine


@pytest.fixture
def budget(monkeypatch):
    # Only synthetic unit fixtures bypass OS instrumentation. The campaign
    # launcher independently enforces real disk/swap; never run it below guard.
    class SyntheticBudget:
        def __init__(self, settings):
            pass

        def check(self):
            pass
    monkeypatch.setattr(s4, 'Budget', SyntheticBudget)


def logical(path, *, sealed=False):
    con = s4.readonly(path, sealed=sealed)
    try:
        con.execute('BEGIN')
        return s4.inventory(con)
    finally:
        con.close()


def test_inventory_reads_identity_from_real_store_metadata(case):
    settings, engine = case
    assert engine.store._connection.execute(
        "SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
    assert logical(settings.store_dir / s4.DB)['store_instance_id'] == engine.store.meta['store_instance_id']


def test_simulation_creates_no_ops_or_archive(case):
    settings, _ = case
    before = logical(settings.store_dir / s4.DB)
    result = s4.backup(settings)
    assert result['mode'] == 'simulation' and not result['qualified']
    assert not settings.ops_dir.exists() and not settings.backup_dir.exists()
    assert logical(settings.store_dir / s4.DB) == before


def test_B01_writer_after_pin_is_outside_snapshot_cut(case, tmp_path):
    settings, engine = case
    before = logical(settings.store_dir / s4.DB)

    def write_after_pin(point):
        if point == 'after_pin':
            engine.add_episode('Synthetic evidence two AFTER cut', project='p')

    cut = s4.snapshot(settings.store_dir / s4.DB, tmp_path / 'copy.sqlite3', fault=write_after_pin)
    assert cut == before == logical(tmp_path / 'copy.sqlite3', sealed=True)
    now = logical(settings.store_dir / s4.DB)
    assert now['snapshot_receipt_sequence'] > cut['snapshot_receipt_sequence']
    assert now['tables']['items']['rows'] == 2 and cut['tables']['items']['rows'] == 1


def test_encrypted_backup_exact_drill_and_success_temp_cleanup(case, budget):
    settings, _ = case
    before = logical(settings.store_dir / s4.DB)
    record = s4.backup(settings, apply=True)
    assert record['ok'] and record['qualified'] and record['verified'], record
    assert record['level'] == 'snapshot' and not record['restore_reopening_qualified']
    assert before['tables']['block_versions']['rows'] == 2
    assert before['tables']['request_receipts']['rows'] >= 3
    assert not list(settings.backup_dir.glob('sqlite-s4-*'))
    assert logical(settings.store_dir / s4.DB) == before
    result = s4.restore_drill(settings, apply=True)
    assert result['ok'] and result['quarantined'] and not result['reopening_allowed'], result
    assert result['forget_reconciliation'] == 'pending' and result['forgets_replayed'] == []
    assert not list(settings.ops_dir.glob('sqlite-drill-*'))
    assert last_drill(settings)['level'] == 'files'
    refusal = s4.restore_drill(settings, apply=True, start_server=True)
    assert not refusal['ok'] and 'reopening unavailable' in refusal['error']


@pytest.mark.parametrize('boundary', ['before_source_open', 'after_pin', 'before_backup', 'after_backup',
                                     'after_snapshot_verify', 'before_archive_verify', 'before_publish', 'after_publish'])
def test_B02_B05_failures_never_register_success_or_change_source(case, budget, boundary):
    settings, _ = case
    before = logical(settings.store_dir / s4.DB)

    def fault(point):
        if point == boundary:
            raise OSError('injected ' + point)

    result = s4.backup(settings, apply=True, fault=fault)
    assert not result['ok'] and not result['qualified'] and not result['verified']
    assert result['preserved_partial'] and Path(result['preserved_partial']).is_dir()
    assert verified_archives(settings) == {}
    assert logical(settings.store_dir / s4.DB) == before


def test_B03_corrupt_ciphertext_is_refused_and_failure_kept(case, budget):
    settings, _ = case
    record = s4.backup(settings, apply=True)
    assert record['ok'], record
    archive = settings.backup_dir / record['archive']
    blob = bytearray(archive.read_bytes())
    blob[-1] ^= 1
    bad = settings.backup_dir / 'corrupt.mosb'
    bad.write_bytes(blob)
    result = s4.restore_drill(settings, bad, apply=True)
    assert not result['ok'] and result['quarantined']
    assert Path(result['preserved_partial']).is_dir()


@pytest.mark.parametrize('kind', ['traversal', 'duplicate', 'oversized', 'gzip_bomb'])
def test_B03_archive_members_checked_before_extraction(tmp_path, kind, monkeypatch):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as tar:
        name = '../outside' if kind == 'traversal' else 'memory-os-v4/store.sqlite3'
        count = 2 if kind == 'duplicate' else 1
        for _ in range(count):
            row = tarfile.TarInfo(name)
            row.size = 8
            tar.addfile(row, io.BytesIO(b'12345678'))
    if kind == 'oversized':
        monkeypatch.setattr(s4, 'MAX_DB', 4)
    if kind == 'gzip_bomb':
        monkeypatch.setattr(s4, 'MAX_DB', 4)
        monkeypatch.setattr(s4, 'MAX_MANIFEST', 4)
        output = io.BytesIO(gzip.compress(b'x' * 100_000))
    archive = tmp_path / 'bad.mosb'
    archive.write_bytes(encrypt(output.getvalue(), b'k' * 32))
    with pytest.raises(BackupError, match='Decompressed' if kind == 'gzip_bomb' else 'Unsafe'):
        s4.verify_archive(archive, b'k' * 32, tmp_path / 'target')
    assert not (tmp_path.parent / 'outside').exists()


def test_storage_margin_reports_pressure_and_guard_refuses_before_any_copy(case, monkeypatch):
    settings, _ = case
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1_550_000_000))
    space = storage.margin(settings)
    assert not space['ok'] and space['volumes']['backup']['margin_bytes'] < 0
    result = s4.backup(settings, apply=True)
    assert not result['ok'] and 'storage_margin_insufficient' in result['error']
    assert not settings.backup_dir.exists() and not settings.ops_dir.exists()


def test_source_closed_without_wal_shm_is_not_repaired(case):
    settings, engine = case
    engine.close()
    assert not Path(str(settings.store_dir / s4.DB) + '-shm').exists()
    before = {p.name: p.read_bytes() for p in settings.store_dir.iterdir() if p.is_file()}
    with pytest.raises(BackupError, match='existing WAL/SHM'):
        s4.readonly(settings.store_dir / s4.DB)
    assert before == {p.name: p.read_bytes() for p in settings.store_dir.iterdir() if p.is_file()}


def test_s4_lease_prevents_overlapping_backup_and_drill(case):
    settings, _ = case
    with pytest.raises(BackupError, match='traversal'):
        s4.plain_path(settings.ops_dir / '..' / 'store')
    fd = s4.lease(settings)
    try:
        with pytest.raises(BlockingIOError):
            s4.lease(settings)
    finally:
        os.close(fd)


def test_weekly_template_is_nice_and_does_not_activate_anything(tmp_path):
    xml = _plist('synthetic.restore-drill', ['/bin/false'], weekday=1, hour=3, minute=30,
                 nice=15, log=tmp_path / 'drill.err')
    value = plistlib.loads(xml.encode())
    assert value['Nice'] == 15
    assert value['StartCalendarInterval'] == {'Weekday': 1, 'Hour': 3, 'Minute': 30}
    assert not (tmp_path / 'drill.err').exists()


@pytest.mark.parametrize('backend,commit', [('jsonl', 'a' * 40), ('sqlite_s1', 'invalid')])
def test_s4_job_templates_refuse_unqualified_backend_build_before_token(case, monkeypatch, backend, commit):
    settings, _ = case
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', backend)
    monkeypatch.setenv('MEMORY_V4_BUILD_COMMIT', commit)
    assert cmd_setup(SimpleNamespace(sqlite_s4_jobs=True), settings) == 2
    assert not settings.token_file.exists()


def test_reconciled_drill_requires_authority_and_keeps_production_quarantined(case, budget, monkeypatch):
    from memory_os_v4.ops import sqlite_probe
    settings, engine = case
    record = s4.backup(settings, apply=True)
    assert record['ok'], record
    engine.forget(engine.store.all()[0].id, project='p')
    calls = []

    def fake_private_probe(root, supplied, recovery, *, guard):
        calls.append(recovery['store_instance_id'])
        assert recovery['forget_reconciliation'] == 'complete'
        return {'match': True, 'private_only': True, 'corpus_reencodings': 0}
    monkeypatch.setattr(sqlite_probe, 'probe', fake_private_probe)
    result = s4.restore_drill(settings, apply=True, start_server=True, reconcile=True)
    assert result['ok'] and result['level'] == 'service' and result['private_reopen_verified'], result
    assert result['quarantined'] and not result['reopening_allowed']
    assert result['forgets_replayed'][0]['outcome'] == 'deleted'
    assert calls == [result['store_instance_id']]
    assert last_drill(settings)['private_reopen_verified']


def test_late_authority_mutation_does_not_report_private_drill_success(case, budget, monkeypatch):
    from memory_os_v4.ops import sqlite_probe
    settings, engine = case
    assert s4.backup(settings, apply=True)['ok']

    def fake_private_probe(root, supplied, recovery, *, guard):
        engine.add_episode('Synthetic concurrent late mutation', project='p')
        return {'match': True}
    monkeypatch.setattr(sqlite_probe, 'probe', fake_private_probe)
    result = s4.restore_drill(settings, apply=True, start_server=True, reconcile=True)
    assert not result['ok'] and not result['private_reopen_verified']
    assert 'Authority changed' in result['error'] and Path(result['preserved_partial']).exists()


def test_rotation_keeps_pinned_unknown_and_modified_archives(case):
    settings, _ = case
    settings.backup_dir.mkdir()
    records = []
    for i in range(45):
        day = dt.datetime(2025, 1, 1) + dt.timedelta(days=i)
        stamp = day.strftime('%Y%m%d-%H%M%S')
        name = s4.PREFIX + stamp + '.mosb'
        path = settings.backup_dir / name
        path.write_bytes(b'synthetic archive ' + str(i).encode())
        records.append({'ok': True, 'qualified': True, 'verified': True, 'archive': name,
                        'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'stamp': stamp})
    log = settings.ops_dir / 'logs/backup.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(''.join(json.dumps(r) + '\n' for r in records))
    unknown = settings.backup_dir / (s4.PREFIX + 'unknown.mosb')
    unknown.write_bytes(b'keep unqualified')
    modified = settings.backup_dir / records[0]['archive']
    modified.write_bytes(b'keep altered evidence')
    pinned = records[-1]['archive']
    removed = s4.rotate_archives(settings, pinned=pinned)
    assert removed and (settings.backup_dir / pinned).exists() and unknown.exists() and modified.exists()
    # A vanished pinned archive must never trigger a second round of rotation.
    before = {p.name for p in settings.backup_dir.iterdir()}
    with pytest.raises(BackupError, match='Pinned'):
        s4.rotate_archives(settings, pinned=s4.PREFIX + 'missing.mosb')
    assert {p.name for p in settings.backup_dir.iterdir()} == before


@pytest.mark.parametrize('boundary', ['before_rotation_unlink', 'after_rotation_unlink',
                                     'before_rotation_fsync', 'before_rotation_registration',
                                     'after_rotation_registration'])
def test_rotation_sigkill_keeps_pinned_unknown_and_source_intact(case, boundary):
    settings, _ = case
    before = logical(settings.store_dir / s4.DB)
    settings.backup_dir.mkdir()
    records = []
    for i in range(40):
        stamp = (dt.datetime(2025, 1, 1) + dt.timedelta(days=i)).strftime('%Y%m%d-%H%M%S')
        name = s4.PREFIX + stamp + '.mosb'
        path = settings.backup_dir / name
        path.write_bytes(('synthetic archive ' + str(i)).encode())
        records.append({'ok': True, 'qualified': True, 'verified': True, 'archive': name,
                        'archive_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'stamp': stamp})
    log = settings.ops_dir / 'logs/backup.jsonl'
    log.parent.mkdir(parents=True)
    log.write_text(''.join(json.dumps(r) + '\n' for r in records))
    pinned = records[-1]['archive']
    unknown = settings.backup_dir / 'unknown.mosb'
    unknown.write_bytes(b'keep evidence')
    code = '''
import os,signal,sys
from pathlib import Path
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.ops.sqlite_backup import rotate_archives
def fault(point):
    if point==sys.argv[4]: os.kill(os.getpid(),signal.SIGKILL)
root=Path(sys.argv[2])
settings=OpsSettings(url='http://127.0.0.1:9/mcp/',token_file=root/'token',store_dir=root/'store',
                     backup_key=root/'key',backup_dir=Path(sys.argv[1]),ops_dir=root,service_label=None)
rotate_archives(settings,pinned=sys.argv[3],fault_hook=fault)
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(settings.backup_dir),
                             str(settings.ops_dir), pinned, boundary], capture_output=True, timeout=15)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    assert unknown.read_bytes() == b'keep evidence'
    assert s4.sha256(settings.backup_dir / pinned) == records[-1]['archive_sha256']
    assert logical(settings.store_dir / s4.DB) == before
    s4.rotate_archives(settings, pinned=pinned)  # recovery is safe, unknown never purged
    assert unknown.exists() and (settings.backup_dir / pinned).exists()


BACKUP_WORKER = '''
import json,os,pathlib,signal,sys
from types import SimpleNamespace
from memory_os_v4.ops import sqlite_backup as s4
from memory_os_v4.ops.settings import OpsSettings
data,point,witness=sys.argv[1:]
values=json.loads(data)
for key in ('token_file','store_dir','backup_dir','backup_key','ops_dir'): values[key]=pathlib.Path(values[key])
settings=OpsSettings(**values)
s4.Budget=lambda _:SimpleNamespace(check=lambda:None)  # Synthetic fixed tiny fixture only.
def fault(boundary):
    if boundary==point: os.kill(os.getpid(),signal.SIGKILL)
result=s4.backup(settings,apply=True,fault=fault)
if not result['ok']: raise RuntimeError(result)
with open(witness,'x') as stream:
    stream.write(json.dumps(result));stream.flush();os.fsync(stream.fileno())
os.kill(os.getpid(),signal.SIGKILL)
'''


@pytest.mark.parametrize('boundary', ['before_source_open', 'after_pin', 'before_backup', 'after_backup',
                                      'after_snapshot_verify', 'before_archive_verify', 'before_publish', 'after_publish',
                                      'before_registration', 'after_registration', 'after_acknowledgement'])
def test_backup_sigkill_no_source_loss_and_only_intact_registered_snapshots(case, boundary, tmp_path):
    settings, _ = case
    before = logical(settings.store_dir / s4.DB)
    witness = tmp_path / 'backup-ack.json'
    process = subprocess.run([sys.executable, '-B', '-c', BACKUP_WORKER,
                              json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in settings.__dict__.items()}),
                              boundary, str(witness)], env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                              capture_output=True, timeout=15)
    assert process.returncode == -signal.SIGKILL, process.stderr.decode()[-1000:]
    assert logical(settings.store_dir / s4.DB) == before
    qualified = verified_archives(settings)
    assert bool(qualified) is (boundary in {'after_registration', 'after_acknowledgement'})
    for name, (sha, _) in qualified.items():
        archive = settings.backup_dir / name
        assert s4.sha256(archive) == sha and archive.stat().st_nlink == 1
        target = tmp_path / ('verified-' + boundary)
        manifest = s4.verify_archive(archive, b'k' * 32, target)
        assert manifest['logical_inventory'] == before
    assert witness.exists() is (boundary == 'after_acknowledgement')
