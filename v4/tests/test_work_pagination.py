"""L7b SQL projection, cursor boundary and bounded 10k regression (no neural model)."""
import datetime as dt
import json
import statistics
import time
import uuid

import pytest

from memory_os_v4 import Config, MemoryV4, models, work_state as work
from memory_os_v4.projects import ProjectScope
from memory_os_v4.sqlite_store import _json


@pytest.fixture
def engine(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path / 'db'), store_backend='sqlite_s1',
                               embed_model='hash', hash_dim=16, work_state=True)) as value:
        yield value


def seed(engine, tasks):
    with engine.store.transaction():
        for i, (project, status, at) in enumerate(tasks):
            value = {'task_id': str(uuid.UUID(int=i+1)), 'revision': 1, 'project': project,
                     'status': status, 'updated_at': dt.datetime.fromtimestamp(at, dt.timezone.utc).isoformat(),
                     'owner': {'client_id': 'codex'}, 'title': 'fixture'}
            # Minimal projection fixture; not an authority-inventory proof.
            work.write_task(engine, value)


def listed(engine, project, **args):
    return work.listing(engine, models.TaskListIn(project=str(project), **args), project)


def test_indexed_10k_list50_under_30ms_and_bounded_parsing(engine, monkeypatch):
    now = time.time()
    seed(engine, [('p', 'open', now-i) for i in range(10000)])
    monkeypatch.setattr(work, 'all_tasks', lambda *_: pytest.fail('unbounded project load'))
    loads = json.loads
    parsed = []

    def measured(record, *a, **kw):
        parsed.append(record)
        return loads(record, *a, **kw)

    monkeypatch.setattr(work.json, 'loads', measured)
    durations = []
    for _ in range(100):
        start = time.perf_counter()
        result = listed(engine, 'p', limit=50)
        durations.append((time.perf_counter()-start)*1000)
        assert result['ok'] and len(result['tasks']) == 50
    assert len(parsed) == 5000  # no 1M full-record decodings
    p95 = statistics.quantiles(durations, n=100, method='inclusive')[94]
    print(f'list50 10k p95={p95:.3f} ms')
    assert p95 < 30
    plan = engine.store._connection.execute('EXPLAIN QUERY PLAN SELECT * FROM task_lookup '
         'WHERE project=? AND status=? AND updated_epoch>=? ORDER BY updated_epoch DESC,task_id LIMIT ?',
         ('p', 'open', now-14*86400, 51)).fetchall()
    assert any('task_lookup_page' in row[-1] for row in plan)
    assert not any('TEMP B-TREE' in row[-1] for row in plan)


def test_order_aliases_microsecond_boundary_and_frozen_cursor(engine, monkeypatch):
    now = 1800000000.123456
    cutoff = now-14*86400
    monkeypatch.setattr(work.time, 'time', lambda: now)
    seed(engine, [('p', 'open', now), ('old', 'blocked', cutoff),
                  ('p', 'blocked', cutoff-0.001), ('p', 'open', cutoff-1), ('p', 'done', now)])
    project = ProjectScope('p', ('p', 'old'))
    first = listed(engine, project, limit=1)
    assert first['tasks'][0]['task_id'] == str(uuid.UUID(int=2))
    assert not first['tasks'][0]['stale']  # strict >14d, fraction preserved
    monkeypatch.setattr(work.time, 'time', lambda: now+1)
    second = listed(engine, project, limit=1, cursor=first['cursor'])
    assert second['tasks'][0]['task_id'] == str(uuid.UUID(int=1))
    ids = [first['tasks'][0]['task_id'], second['tasks'][0]['task_id']]
    page = second
    while page['cursor']:
        page = listed(engine, project, limit=1, cursor=page['cursor'])
        ids += [t['task_id'] for t in page['tasks']]
    assert ids == [str(uuid.UUID(int=i)) for i in (2,1,3,4)]
    assert listed(engine, 'p', limit=1, cursor=first['cursor'])['code'] == 'cursor_stale'
    assert listed(engine, project, limit=1, owner='claude', cursor=first['cursor'])['code'] == 'cursor_stale'
    with engine.store.transaction():
        task = json.loads(engine.store._connection.execute('SELECT record_json FROM task_versions WHERE task_id=?', (ids[0],)).fetchone()[0])
        task['revision'] = 2
        work.write_task(engine, task)
    assert listed(engine, project, limit=1, cursor=first['cursor'])['code'] == 'cursor_stale'


def test_projection_rollback_is_atomic(engine):
    seed(engine, [('p', 'open', time.time())])
    before = engine.store._connection.execute('SELECT * FROM task_lookup').fetchall()
    with pytest.raises(RuntimeError), engine.store.transaction():
        task = json.loads(engine.store._connection.execute('SELECT record_json FROM task_versions').fetchone()[0])
        task.update(revision=2, status='blocked')
        work.write_task(engine, task)
        raise RuntimeError('injected before final receipt')
    assert engine.store._connection.execute('SELECT * FROM task_lookup').fetchall() == before
    assert engine.store._connection.execute('SELECT count(*) FROM task_versions').fetchone()[0] == 1
    assert _json(before)  # fixture remains inspectable


def test_known_schema6_copy_upgrades_lookup_without_changing_task_authority(tmp_path):
    import sqlite3
    from memory_os_v4.sqlite_store import SQLiteStore
    path = tmp_path / 'schema6'
    with MemoryV4(config=Config(data_dir=str(path), store_backend='sqlite_s1', embed_model='hash',
                               hash_dim=16, work_state=True)) as value:
        from memory_os_v4 import server
        from unittest.mock import patch
        with patch.object(server, '_engine', value):
            assert server.dispatch('memory_task_create', {'project': 'p', 'title': 'Survives upgrade', 'request_id': 'one'})['ok']
        authority = value.store._connection.execute('SELECT * FROM task_versions').fetchall()
    con = sqlite3.connect(path / 'store.sqlite3')
    con.execute('DROP TRIGGER task_lookup_insert')
    con.execute('DROP TRIGGER task_lookup_update')
    con.execute('DROP TABLE task_lookup')
    con.execute('DROP INDEX task_versions_cut')
    con.execute('DELETE FROM schema_migrations WHERE version=7')
    con.execute('PRAGMA user_version=6')
    con.commit()
    con.close()
    from contextlib import closing
    with closing(SQLiteStore(path)) as store:
        assert store._connection.execute('PRAGMA user_version').fetchone()[0] == 7
        assert store._connection.execute('SELECT * FROM task_versions').fetchall() == authority
        assert store._connection.execute('SELECT count(*) FROM task_lookup').fetchone()[0] == 1
        assert store.check_integrity()
