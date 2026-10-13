"""Historical restore on tiny copies; no network/model or production authority."""
import hashlib
import errno
import json
import os
import shutil
import signal
import subprocess
import sys

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.ops.backup import BackupError
from memory_os_v4.ops import sqlite_backup as s4
from memory_os_v4.ops import sqlite_recovery as recovery
from memory_os_v4.sqlite_store import SQLiteStore


def logical(path):
    con = s4.readonly(path, sealed=True)
    try:
        return s4.inventory(con)
    finally:
        con.close()


@pytest.fixture
def restored(tmp_path):
    source = MemoryV4(config=Config(data_dir=str(tmp_path / 'source'), store_backend='sqlite_s1',
                                  embed_model='hash', hash_dim=16))
    try:
        source.add_episodes_bulk([{'item_id': 'keep', 'text': 'Synthetic retained searchable evidence', 'project': 'p'},
                                  {'item_id': 'erase', 'text': 'Synthetic confidential erased evidence', 'project': 'p'}])
        source.blocks.set('working_rules', 'Synthetic historical block', project='p')
        source.execute_request('synthetic', 'p', {}, 'old-explicit', lambda: {'ok': True},
                               raw_args_sha256='a' * 64)
        target = tmp_path / 'restored'
        target.mkdir()
        inventory = s4.snapshot(source.store.db_path, target / s4.DB)
        manifest = {'logical_inventory': inventory}
        source.forget('erase', project='p')
        source.add_episode('Synthetic added and forgotten AFTER snapshot', project='p', item_id='later')
        source.forget('later', project='p')
        yield source, target, manifest
    finally:
        source.close()


def test_reconcile_forgets_identity_history_and_no_source_writes(restored, monkeypatch):
    source, target, manifest = restored
    current = s4.readonly(source.store.db_path)
    before = s4.inventory(current)
    current.close()
    result = recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest)
    current = s4.readonly(source.store.db_path)
    assert s4.inventory(current) == before
    current.close()
    assert result['store_instance_id'] != source.store.meta['store_instance_id']
    assert result['restored_from_instance_id'] == source.store.meta['store_instance_id']
    assert {r['item_id']: r['outcome'] for r in result['forgets_replayed']} == {'erase': 'deleted', 'later': 'absent_from_snapshot'}
    assert result['forget_reconciliation'] == 'complete' and not result['reopening_allowed']
    with MemoryV4(config=Config(data_dir=str(target), store_backend='sqlite_s1', embed_model='hash',
                              hash_dim=16, write_mode='read_only')) as engine:
        assert engine._embedding_cache.misses == 0
        assert engine.store.get('erase').deleted and len(engine.store.history('erase')) == 2
        assert engine.blocks.get('working_rules', project='p') == 'Synthetic historical block'
        assert engine.requests.records[('p', 'old-explicit')]['result'] == {'ok': True}
        assert engine.requests.records[('operator:s4', result['recovery_request_id'])]['result'] == result
        assert not engine.store.get('later')
        monkeypatch.setattr(server, '_engine', engine)
        status = server.dispatch('memory_request_status', {'request_id': 'old-explicit',
                                  'store_instance_id': source.store.meta['store_instance_id']})
        assert status['reason'] == 'store_identity_missing_or_changed'
        assert 'erase' not in {h.item.id for h in engine.search('confidential erased', project='p')}


def test_attested_schema5_historical_copy_is_verified_before_current_upgrade(restored):
    import sqlite3
    source, target, _ = restored
    before_source = s4.readonly(source.store.db_path)
    before = s4.inventory(before_source)
    before_source.close()
    # Construct a historical v5 fixture from an EMPTY v6 extension, copy only.
    con = sqlite3.connect(target / s4.DB)
    con.execute('DROP TABLE change_events')
    con.execute("DELETE FROM store_metadata WHERE key='change_journal'")
    con.execute('DROP TABLE task_lookup')
    con.execute('DROP TRIGGER task_lookup_insert')
    con.execute('DROP TRIGGER task_lookup_update')
    con.execute('DROP TABLE task_current')
    con.execute('DROP TABLE task_versions')
    con.execute('DELETE FROM schema_migrations WHERE version>=6')
    con.execute('PRAGMA user_version=5')
    con.commit()
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    original = s4.readonly(target / s4.DB, sealed=True)
    manifest = {'logical_inventory': s4.inventory(original)}
    original.close()
    assert manifest['logical_inventory']['schema_version'] == 5
    result = recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest)
    assert result['schema_upgrade'] == {'from': 5, 'to': __import__('memory_os_v4.sqlite_store',fromlist=['SCHEMA_VERSION']).SCHEMA_VERSION}
    restored_store = SQLiteStore(target)
    assert restored_store._connection.execute('SELECT count(*) FROM task_current').fetchone()[0] == 0
    assert restored_store.get('erase').deleted
    restored_store.close()
    current = s4.readonly(source.store.db_path)
    assert s4.inventory(current) == before
    current.close()


def test_missing_authority_or_bad_lineage_keeps_target_identical(restored, tmp_path):
    source, target, manifest = restored
    before = hashlib.sha256((target / s4.DB).read_bytes()).hexdigest()
    with pytest.raises(BackupError):
        recovery.prepare_recovery(target / s4.DB, tmp_path / 'missing.sqlite3', manifest)
    source.store.save_meta({**source.store.meta, 'store_instance_id': 'wrong-lineage'})
    with pytest.raises(BackupError, match='lineage'):
        recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest)
    assert hashlib.sha256((target / s4.DB).read_bytes()).hexdigest() == before


def test_empty_tombstone_list_has_exhaustive_proof(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path / 'source'), store_backend='sqlite_s1',
                              embed_model='hash', hash_dim=16)) as source:
        source.add_episode('Synthetic never forgotten', project='p')
        target = tmp_path / 'target'
        target.mkdir()
        manifest = {'logical_inventory': s4.snapshot(source.store.db_path, target / s4.DB)}
        result = recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest)
        assert result['forgets_replayed'] == []
        assert result['reconciliation']['empty_list_proof'] == 'complete immutable history scanned'


def test_divergent_immutable_history_refused(restored):
    source, target, _ = restored
    copied = SQLiteStore(target)
    try:
        item = copied.get('keep')
        item.text = 'Synthetic forged divergent history'
        copied.append(item)
    finally:
        copied.close()
    manifest = {'logical_inventory': logical(target / s4.DB)}
    with pytest.raises(BackupError, match='immutable snapshot history'):
        recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest)


def test_source_changed_after_proof_prevents_recovery(restored):
    source, target, manifest = restored
    before = logical(target / s4.DB)

    def fault(point):
        if point == 'after_reconciliation_proof':
            source.add_episode('Synthetic late mutation', project='p')
    with pytest.raises(BackupError, match='Authority changed'):
        recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest, fault=fault)
    assert logical(target / s4.DB) == before


@pytest.mark.parametrize('boundary', ['before_item_version', 'after_item_head', 'after_chunk_map',
                                      'after_metadata', 'after_receipt', 'before_commit',
                                      'after_commit', 'before_recovery_ack'])
def test_recovery_exception_matrix_never_partial_success(restored, boundary):
    source, target, manifest = restored
    before = logical(target / s4.DB)

    def fault(point):
        if point == boundary:
            raise OSError(errno.ENOSPC, 'injected ENOSPC boundary ' + point)
    with pytest.raises(OSError, match='ENOSPC'):
        recovery.prepare_recovery(target / s4.DB, source.store.db_path, manifest, fault=fault)
    after = logical(target / s4.DB)
    if boundary in {'after_commit', 'before_recovery_ack'}:
        assert after['store_instance_id'] != before['store_instance_id']
        assert after['tables']['request_receipts']['rows'] == before['tables']['request_receipts']['rows'] + 1
        con = s4.readonly(target / s4.DB, sealed=True)
        try:
            assert con.execute("SELECT json_extract(record_json,'$.deleted') FROM item_versions WHERE item_id='erase' ORDER BY revision DESC LIMIT 1").fetchone()[0] == 1
        finally:
            con.close()
    else:
        assert after == before


def fork_snapshot(target, directory):
    directory.mkdir()
    shutil.copyfile(target / s4.DB, directory / s4.DB)
    return directory / s4.DB


def recovery_boundaries(source, target, manifest, tmp_path):
    boundaries = []
    recovery.prepare_recovery(fork_snapshot(target, tmp_path / 'trace'), source.store.db_path,
                              manifest, fault=boundaries.append)
    assert len(boundaries) >= 20
    return boundaries


def assert_atomic_recovery(path, manifest, committed):
    after = logical(path)
    before = manifest['logical_inventory']
    if not committed:
        assert after == before
        return
    assert after['store_instance_id'] != before['store_instance_id']
    store = SQLiteStore(path.parent)
    try:
        assert store.get('erase').deleted and not store.get('keep').deleted
        assert len(store.history('erase')) == 2
        assert after['tables']['request_receipts']['rows'] == before['tables']['request_receipts']['rows'] + 1
        control = recovery.SQLiteRequests(store)
        receipt = next(v for v in control.records.values() if v['operation'] == 's4_restore_recovery')
        assert receipt['result']['recovery_event_sha256'] == recovery.digest(store.meta['recovery_history'][-1])
    finally:
        store.close()


def test_all_recovery_write_boundaries_enospc_are_atomic(restored, tmp_path):
    source, target, manifest = restored
    boundaries = recovery_boundaries(source, target, manifest, tmp_path)
    for ordinal, label in enumerate(boundaries):
        path = fork_snapshot(target, tmp_path / ('enospc-' + str(ordinal)))
        count = 0

        def fault(point):
            nonlocal count
            here = count
            count += 1
            if here == ordinal:
                raise OSError(errno.ENOSPC, 'injected full-disk syscall boundary')
        with pytest.raises(OSError):
            recovery.prepare_recovery(path, source.store.db_path, manifest, fault=fault)
        assert_atomic_recovery(path, manifest, label in {'after_commit', 'before_recovery_ack'})


WORKER = '''
import json,os,pathlib,signal,sys
from memory_os_v4.ops.sqlite_recovery import prepare_recovery
target,source,manifest,ordinal,witness=sys.argv[1:]
count=0
def fault(point):
    global count
    here=count
    count+=1
    if here==int(ordinal): os.kill(os.getpid(),signal.SIGKILL)
result=prepare_recovery(pathlib.Path(target),pathlib.Path(source),json.loads(manifest),fault=fault)
with open(witness,'x') as stream:
    stream.write(json.dumps(result));stream.flush();os.fsync(stream.fileno())
os.kill(os.getpid(),signal.SIGKILL)
'''


def test_sigkill_every_recovery_boundary_and_after_ack_loses_no_acknowledged_write(restored, tmp_path):
    source, target, manifest = restored
    boundaries = recovery_boundaries(source, target, manifest, tmp_path)
    for ordinal, label in enumerate([*boundaries, 'after_acknowledgement']):
        path = fork_snapshot(target, tmp_path / ('kill-' + str(ordinal)))
        witness = path.parent / 'ack.json'
        result = subprocess.run([sys.executable, '-B', '-c', WORKER, str(path), str(source.store.db_path),
                                 json.dumps(manifest), str(ordinal), str(witness)],
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                capture_output=True, timeout=15)
        assert result.returncode == -signal.SIGKILL, result.stderr.decode()[-1000:]
        committed = label in {'after_commit', 'before_recovery_ack', 'after_acknowledgement'}
        # Recovery of the disposable WAL first; immutable reads cannot see uncheckpointed commits.
        reopened = SQLiteStore(path.parent)
        reopened.close()
        assert_atomic_recovery(path, manifest, committed)
        assert witness.exists() is (label == 'after_acknowledgement')
        if witness.exists():
            acknowledged = json.loads(witness.read_text())
            con = s4.readonly(path, sealed=True)
            try:
                durable = con.execute("SELECT record_json FROM request_receipts WHERE request_id=?",
                                      (acknowledged['recovery_request_id'],)).fetchone()[0]
                assert json.loads(durable)['result'] == acknowledged
            finally:
                con.close()
