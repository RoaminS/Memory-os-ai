"""Small Hash/SQLite fixtures: pinned cut, failure boundaries, crypto/quarantine."""
import io
import gzip
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
from memory_os_v4.ops.cli import _plist
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
