"""B09: all engine/server write boundaries refuse before receipt; reads work."""
import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.maintenance import MaintenanceWindow

WRITES = [
    ('memory_chat_save', {'messages': [{'role': 'assistant', 'content': 'Synthetic'}], 'project_label': 'p'}),
    ('memory_add_facts', {'facts': [{'text': 'Synthetic'}], 'project': 'p'}),
    ('memory_block_set', {'name': 'working_rules', 'text': 'Synthetic', 'project': 'p'}),
    ('memory_forget', {'item_id': 'missing', 'project': 'p', 'reason': 'Synthetic'}),
    ('memory_ingest', {'path': '/missing/synthetic.txt', 'project': 'p'}),
    ('memory_migrate_v3', {'log_path': '/missing/synthetic.jsonl'}),
    ('memory_consolidate', {'dry_run': False}),
    ('memory_compact', {'dry_run': False}),
]


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
@pytest.mark.parametrize('name,args', WRITES)
def test_every_mutation_refused_before_receipt(tmp_path, monkeypatch, backend, name, args):
    with MemoryV4(config=Config(data_dir=str(tmp_path / 'store'), embed_model='hash',
                              store_backend=backend, write_mode='read_only')) as engine:
        monkeypatch.setattr(server, '_engine', engine)
        before = dict(engine.requests.records)
        result = server.dispatch(name, {**args, 'request_id': 'denied'})
        assert result['code'] == 'maintenance_window' and result['outcome'] == 'not_started', result
        preflight = server.dispatch('memory_request_status', {'operation': name, 'arguments': {**args, 'request_id': 'denied'}})
        assert preflight['code'] == 'maintenance_window' and preflight['outcome'] == 'not_started'
        assert engine.requests.records == before and engine.store.all() == []
        assert server.dispatch('memory_status', {})['total'] == 0
        with pytest.raises(MaintenanceWindow):
            engine.add_episode('No write', project='p')
        with pytest.raises(MaintenanceWindow):
            engine.blocks.set('working_rules', 'No write', project='p')
        with pytest.raises(MaintenanceWindow):
            engine.flush()
        engine.config.write_mode = 'read_write'
        monkeypatch.setenv('MEMORY_V4_WRITE_MODE', 'read_write')
        assert engine.write_status()['mode'] == 'read_only'  # Frozen before store open.


def test_cold_window_does_not_open_or_encode(tmp_path, monkeypatch):
    monkeypatch.setattr(server, '_engine', None)
    monkeypatch.setattr(server, 'get_engine', lambda: pytest.fail('Cold refusal must not open the engine'))
    monkeypatch.setenv('MEMORY_V4_WRITE_MODE', 'read_only')
    monkeypatch.setenv('MEMORY_V4_DIR', str(tmp_path / 'never-created'))
    result = server.dispatch('memory_block_set', {'name': 'working_rules', 'text': 'No write'})
    assert result['code'] == 'maintenance_window' and not (tmp_path / 'never-created').exists()


def test_invalid_window_fails_before_store_creation(tmp_path):
    with pytest.raises(ValueError, match='write_mode'):
        MemoryV4(config=Config(data_dir=str(tmp_path / 'missing'), embed_model='hash', write_mode='typo'))
    assert not (tmp_path / 'missing').exists()
