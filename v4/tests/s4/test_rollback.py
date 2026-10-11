"""B06: offline rollback retains current authority, changes identity, never activates."""
import json
from pathlib import Path

import pytest

from memory_os_v4 import Config, MemoryV4
from memory_os_v4.sqlite_migrate import _files, export, migrate


def setup_rollback(tmp_path):
    original, target = tmp_path / 'jsonl', tmp_path / 'sqlite'
    with MemoryV4(config=Config(data_dir=str(original), embed_model='hash', hash_dim=16)) as engine:
        engine.add_facts([{'item_id': 'old', 'text': 'Synthetic city Paris'}], project='p')
        engine.add_episode('Synthetic later erased evidence', item_id='erase', project='p')
        engine.blocks.set('project_state', 'Before migration', project='p')
    original_bytes = _files(original)
    migrate(original, target, apply=True)
    with MemoryV4(config=Config(data_dir=str(target), embed_model='hash', hash_dim=16,
                              store_backend='sqlite_s1')) as engine:
        old_id = engine.store.meta['store_instance_id']
        def batch():
            engine.add_facts([{'item_id': 'new', 'text': 'Synthetic city Lyon', 'replaces': ['old']}],
                             project='p', event_time=2000000000)
            engine.add_episode('Synthetic acknowledged addition', item_id='added', project='p')
            engine.forget('erase', project='p')
            engine.blocks.set('project_state', 'After migration', project='p')
            return {'ok': True, 'ids': ['new', 'added']}
        engine.execute_request('post_migration_batch', 'p', {}, 'ack', batch)
        expected = {name: [r[0] for r in engine.store._connection.execute(query)] for name, query in (
            ('items.jsonl', 'SELECT record_json FROM item_versions ORDER BY rowid'),
            ('blocks.jsonl', 'SELECT record_json FROM block_versions ORDER BY sequence'))}
    return original, target, original_bytes, old_id, expected


def test_B06_current_export_preserves_correction_forget_blocks_receipts_and_new_identity(tmp_path):
    original, target, original_bytes, old_id, expected = setup_rollback(tmp_path)
    before = _files(target)
    with pytest.raises(ValueError, match='lose data'):
        export(target, tmp_path / 'unsafe-old', original=True)
    out = tmp_path / 'rollback'
    result = export(target, out, rollback=True)
    assert result['rollback'] and not result['reopening_allowed']
    assert _files(original) == original_bytes and _files(target) == before
    for name, rows in expected.items():
        assert (out / name).read_text().splitlines() == rows
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['store_instance_id'] != old_id and meta['restored_from_instance_id'] == old_id
    event = json.loads((out / 'rollback.json').read_text())
    receipts = [json.loads(line) for line in (out / 'requests.jsonl').read_text().splitlines()]
    assert next(r for r in receipts if r['request_id'] == 'ack')['result']['ids'] == ['new', 'added']
    assert receipts[-1]['result'] == event
    with MemoryV4(config=Config(data_dir=str(out), embed_model='hash', hash_dim=16,
                              write_mode='read_only')) as engine:
        assert engine.store.get('old').invalidated_by == 'new'
        assert engine.store.get('erase').deleted and engine.store.get('added')
        assert engine.blocks.get('project_state', project='p') == 'After migration'
        assert engine.store.meta['store_instance_id'] == meta['store_instance_id']


def test_B06_each_export_write_publication_boundary_preserves_source_and_never_partial_target(tmp_path):
    _, target, _, _, _ = setup_rollback(tmp_path)
    before, boundaries = _files(target), []
    export(target, tmp_path / 'trace', rollback=True, fault_hook=boundaries.append)
    for i, boundary in enumerate(boundaries):
        destination = tmp_path / ('fail-' + str(i))
        def fault(point):
            if point == boundary:
                raise OSError('injected export boundary')
        with pytest.raises(OSError):
            export(target, destination, rollback=True, fault_hook=fault)
        assert _files(target) == before
        assert destination.exists() is (boundary == 'after_export_publish')
        if destination.exists():
            assert json.loads((destination / 'rollback.json').read_text())['ok']
            assert all(Path(destination / p).is_file() for p in ('items.jsonl', 'requests.jsonl', 'meta.json'))
