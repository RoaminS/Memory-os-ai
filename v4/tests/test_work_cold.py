"""Cold production-profile work authority: no model resolution/load/download."""
import hashlib

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4 import memory
from memory_os_v4.capabilities import deployed
from memory_os_v4.embed import HashEmbedder
from memory_os_v4.readiness import engine_readiness


def cold_config(path, **kw):
    return Config(data_dir=str(path), store_backend='sqlite_s1', work_state=True,
                  embed_model='unavailable-neural-fixture', device='auto', **kw)


def call(operation, **args):
    return server.dispatch(operation, {'project': 'p', **args})


def forbid_model(*_a, **_k):
    pytest.fail('model construction during cold work operation')


def test_cold_production_config_crud_brief_cas_rollback_and_reopen(tmp_path, monkeypatch):
    path = tmp_path / 'db'
    with MemoryV4(config=Config(data_dir=str(path), store_backend='sqlite_s1', embed_model='hash', hash_dim=16)) as seed:
        seed.add_facts([{'text': 'Fixture fact', 'project': 'p'}])
        previous_profile = dict(seed.store.meta['embed_compute_profile'])
        cache_before = seed.store._connection.execute('SELECT * FROM embedding_cache').fetchall()
    monkeypatch.setattr(memory, '_make_embedder', forbid_model)
    with MemoryV4(config=cold_config(path)) as engine:
        monkeypatch.setattr(server, '_engine', engine)
        assert engine.embedder is None and not engine._retrieval_ready
        assert engine_readiness(engine) == (True, 'ready_work_only')
        capabilities = deployed(engine, {})
        assert capabilities['work_state'] and not capabilities['retrieval_ready']
        assert capabilities['policies']['write_mode'] == 'read_write'
        assert not capabilities['encoding']['loaded']
        assert capabilities['encoding']['profile_source'] == 'historical_store_metadata'
        task = call('memory_task_create', title='Cold task', request_id='create')
        assert task['ok']
        changed = call('memory_task_update', task_id=task['task_id'], expected_etag=task['etag'],
                       patch={'next_action': 'Continue'}, reason='test', request_id='update')
        assert changed['ok'] and changed['revision'] == 2
        assert call('memory_task_list')['tasks'][0]['task_id'] == task['task_id']
        assert call('memory_task_get', task_id=task['task_id'], include_history=True)['history']
        assert call('memory_session_brief', focus_query='Fixture')['mode'] == 'compact'
        assert call('memory_block_compare_and_set', name='state', scope='project', text='Data', expected_etag=None,
                    reason='test', request_id='block')['ok']
        seen = []

        def fault(point):
            seen.append(point)
            if point == 'after_task_current':
                raise RuntimeError('injected')

        engine.store.fault_hook = fault
        failed = call('memory_task_update', task_id=task['task_id'], expected_etag=changed['etag'],
                      patch={'next_action': 'Must rollback'}, reason='test', request_id='partial')
        assert not failed['ok'] and seen
        engine.store.fault_hook = None
        assert call('memory_task_get', task_id=task['task_id'])['next_action'] == 'Continue'
        assert engine_readiness(engine) == (True, 'ready_work_only')
        assert engine.store.meta['embed_compute_profile'] == previous_profile
        assert engine.store.meta['embed_model'] == 'hash'
        assert engine.store._connection.execute('SELECT * FROM embedding_cache').fetchall() == cache_before
    with MemoryV4(config=cold_config(path)) as reopened:
        monkeypatch.setattr(server, '_engine', reopened)
        assert call('memory_task_get', task_id=task['task_id'])['revision'] == 2
        assert reopened.store.meta['embed_compute_profile'] == previous_profile


def test_unavailable_retrieval_is_explicit_and_work_remains_ready(tmp_path, monkeypatch):
    with MemoryV4(config=cold_config(tmp_path / 'db')) as engine:
        monkeypatch.setattr(server, '_engine', engine)
        monkeypatch.setattr(memory, '_make_embedder', lambda *_: (_ for _ in ()).throw(ImportError('offline model')))
        assert call('memory_search', query='test')['code'] == 'retrieval_unavailable'
        assert not engine._retrieval_ready and engine.embedder is None
        assert engine_readiness(engine) == (True, 'ready_work_only')
        assert call('memory_task_create', title='Still available', request_id='task')['ok']
        assert not engine.store.meta.get('embed_compute_profile')


def test_retrieval_lazy_hydration_is_attested_before_search(tmp_path, monkeypatch):
    with MemoryV4(config=cold_config(tmp_path / 'db')) as engine:
        monkeypatch.setattr(server, '_engine', engine)
        calls = []

        def stub(cfg):
            calls.append(cfg.embed_model)
            return HashEmbedder(dim=16)  # deterministic stub, no semantic proof

        monkeypatch.setattr(memory, '_make_embedder', stub)
        assert call('memory_search', query='test')['hits'] == []
        assert engine._retrieval_ready and len(calls) == 1
        assert engine.store.meta['embed_compute_profile']['model_revision'] == 'hash-v1'
        assert call('memory_search', query='test')['hits'] == [] and len(calls) == 1
        assert call('memory_task_list')['ok']


def test_disabled_cold_work_refuses_without_opening_store(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_engine', None)
    monkeypatch.setenv('MEMORY_V4_DATA_DIR', str(tmp_path / 'never'))
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', 'jsonl')
    monkeypatch.setattr(server, 'get_engine', forbid_model)
    assert call('memory_task_list')['code'] == 'backend_unsupported'
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', 'sqlite_s1')
    monkeypatch.setenv('MEMORY_V4_WORK_STATE', '0')
    assert call('memory_task_list')['code'] == 'capability_disabled'
    assert not (tmp_path / 'never').exists()


def test_cold_read_only_capability_and_no_task_write(tmp_path, monkeypatch):
    with MemoryV4(config=cold_config(tmp_path / 'db', write_mode='read_only')) as engine:
        monkeypatch.setattr(memory, '_make_embedder', forbid_model)
        monkeypatch.setattr(server, '_engine', engine)
        assert deployed(engine, {})['policies']['write_mode'] == 'read_only'
        before = hashlib.sha256(str(engine.store.meta).encode()).hexdigest()
        assert call('memory_task_list')['tasks'] == []
        result = call('memory_task_create', title='Forbidden', request_id='write')
        assert result['code'] == 'maintenance_window' and result['outcome'] == 'not_started'
        assert hashlib.sha256(str(engine.store.meta).encode()).hexdigest() == before
        assert engine.store._connection.execute('SELECT count(*) FROM task_versions').fetchone()[0] == 0
