"""Readiness caches positives only, never a write/fault or live readiness state."""
import json
import sqlite3

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.read_audit import ReadAudit
from memory_os_v4.readiness import engine_readiness


@pytest.fixture
def engine(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend='sqlite_s1', work_state=True,
                               embed_model='hash', hash_dim=16)) as value:
        value.add_facts([{'item_id':'one', 'text':'Synthetic readiness evidence.'}], project='P')
        yield value


def counted(monkeypatch, obj, name):
    calls = []
    original = getattr(obj, name)
    def wrapper(*a, **k):
        calls.append(1)
        return original(*a, **k)
    monkeypatch.setattr(obj, name, wrapper)
    return calls


def test_cached_positive_reaudits_after_every_writer_generation_and_rollback(engine, monkeypatch):
    calls = counted(monkeypatch, engine.store, 'check_integrity')
    assert engine_readiness(engine) == (True, 'ready')
    assert engine_readiness(engine) == (True, 'ready')
    assert len(calls) == 1
    engine.add_facts([{'text':'Another synthetic assertion.'}], project='P')
    assert engine_readiness(engine)[0] and len(calls) == 2
    with pytest.raises(ValueError):
        with engine.store.transaction():
            raise ValueError('Synthetic rollback')
    assert engine_readiness(engine)[0] and len(calls) == 3
    with engine.store.transaction():
        assert engine_readiness(engine) == (False, 'store_transaction_active')
    assert engine_readiness(engine)[0] and len(calls) == 4


@pytest.mark.parametrize('damage', ['poisoned', 'index', 'lease', 'model', 'closed'])
def test_live_failures_never_hidden_by_cached_audit(engine, damage):
    assert engine_readiness(engine)[0]
    if damage == 'poisoned':
        engine.store._poisoned = True
    elif damage == 'index':
        engine.bm25.remove('one')
    elif damage == 'lease':
        engine._writer_lock.close()
    elif damage == 'model':
        engine.config.embed_model = 'synthetic-missing'
    elif damage == 'closed':
        engine.close()
    assert not engine_readiness(engine)[0]
    engine.store._poisoned = False


def test_external_commit_invalidates_immediately(engine):
    assert engine_readiness(engine)[0]
    with sqlite3.connect(engine.store.db_path) as other:
        other.execute('DROP TRIGGER item_versions_no_update')
        other.execute("UPDATE item_versions SET record_sha256=?", ('0'*64,))
    assert engine_readiness(engine) == (False, 'store_integrity')


def test_in_memory_fault_has_at_most_one_second_detection_bound(engine, monkeypatch):
    from memory_os_v4 import read_audit
    clock = [100.]
    monkeypatch.setattr(read_audit.time, 'monotonic', lambda:clock[0])
    assert engine_readiness(engine)[0]
    engine.store.items['one'].text = 'Synthetic untracked memory fault'
    clock[0] += 1.0
    assert engine_readiness(engine) == (False, 'store_integrity')


def test_failures_and_unstable_audits_never_cached(monkeypatch):
    audit = ReadAudit()
    calls = []
    def fail():
        calls.append(1)
        return False
    for _ in range(2):
        assert audit.run(lambda:1, fail, bool) is False
    assert len(calls) == 2
    stamps = iter([1,2])
    with pytest.raises(ValueError, match='changed during'):
        audit.run(lambda:next(stamps), lambda:True, bool)
    assert audit._entry is None


def test_ttl_bounded_copies_and_clock_backwards(monkeypatch):
    from memory_os_v4 import read_audit
    clock, calls = [100.], []
    monkeypatch.setattr(read_audit.time, 'monotonic', lambda:clock[0])
    audit = ReadAudit()
    def check():
        calls.append(1)
        return {'ok':True}
    first = audit.run(lambda:1, check, bool, max_age=999)
    first['ok'] = False
    assert audit.run(lambda:1, check, bool)['ok'] is True
    clock[0] += 1
    audit.run(lambda:1, check, bool, max_age=999)
    assert len(calls) == 2
    clock[0] -= 5
    audit.run(lambda:1, check, bool)
    assert len(calls) == 3


@pytest.mark.parametrize('damage', ['modify', 'add', 'remove', 'descriptor', 'symlink'])
def test_descriptor_cache_never_hides_changed_payload(tmp_path, damage):
    import hashlib
    from memory_os_v4.build_descriptor import verify, NAME
    package = tmp_path/'memory_os_v4'
    package.mkdir()
    payload = package/'fixture.py'
    payload.write_text('synthetic = 1\n')
    files = {'memory_os_v4/fixture.py':hashlib.sha256(payload.read_bytes()).hexdigest()}
    descriptor = {'schema_version':1, 'version':'4.0.8.dev1', 'source_commit':'a'*40,
                  'source_inventory_sha256':'b'*64, 'files':files,
                  'payload_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    (package/NAME).write_text(json.dumps(descriptor))
    assert verify(tmp_path, '4.0.8.dev1', max_age=1)['ok']
    assert verify(tmp_path, '4.0.8.dev1', max_age=1)['ok']
    if damage == 'modify':
        payload.write_text('synthetic = 2\n')
    elif damage == 'add':
        (package/'added.py').write_text('synthetic = 2\n')
    elif damage == 'remove':
        payload.unlink()
    elif damage == 'descriptor':
        (package/NAME).write_text('{}')
    else:
        payload.unlink()
        payload.symlink_to(package/NAME)
    assert not verify(tmp_path, '4.0.8.dev1', max_age=1)['ok']
