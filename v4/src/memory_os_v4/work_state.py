"""Opt-in L7b SQLite authority. No model, file access, auto-close or implicit merge.

Mutations run inside the engine's one outer transaction/final L3 receipt.
Readers use the same engine lock; cursors refuse a changed source cut.
"""
from __future__ import annotations

import base64
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import math
import time
import unicodedata
import uuid

from .blocks import Block
from .projects import matches
from .sqlite_store import _json


class WorkError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def enabled(engine):
    return engine.config.store_backend == 'sqlite_s1' and engine.config.work_state


def require(engine):
    if engine.config.store_backend != 'sqlite_s1':
        raise WorkError('backend_unsupported')
    if not engine.config.work_state:
        raise WorkError('capability_disabled')


def stamp():
    return dt.datetime.fromtimestamp(time.time(), dt.timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def etag(engine, scope, object_id, revision):
    identity = engine.store.meta['store_instance_id']
    return 'w1:' + identity + ':' + digest([identity, scope, object_id, revision])


def conflict(engine, expected, current, scope, object_id, revision):
    if isinstance(expected, str) and expected.startswith('w1:') and expected.split(':')[1] != engine.store.meta['store_instance_id']:
        return {'ok': False, 'code': 'identity_changed'}
    tag = etag(engine, scope, object_id, revision) if current is not None else None
    if expected != tag:
        return {'ok': False, 'code': 'revision_conflict', 'current_etag': tag,
                'current_summary': current}
    return None


def head(engine, task_id, project):
    row = engine.store._connection.execute(
        'SELECT v.record_json FROM task_current c JOIN task_versions v '
        'ON c.task_id=v.task_id AND c.revision=v.revision WHERE c.task_id=?', (task_id,)).fetchone()
    if row:
        task = json.loads(row[0])
        return task if matches(task['project'], project) else None


def view(engine, task):
    value = dict(task)
    value['etag'] = etag(engine, task['project'], 'task:' + task['task_id'], task['revision'])
    value['stale'] = task['status'] != 'done' and time.time() - dt.datetime.fromisoformat(task['updated_at']).timestamp() > 14 * 86400
    return value


def all_tasks(engine, project):
    labels = getattr(project, 'labels', (project,))
    where = '' if project is None else ' WHERE v.project IN (' + ','.join('?' for _ in labels) + ')'
    return [json.loads(row[0]) for row in engine.store._connection.execute(
        'SELECT v.record_json FROM task_current c JOIN task_versions v '
        'ON c.task_id=v.task_id AND c.revision=v.revision' + where, () if project is None else labels)]


def duplicates(engine, project, title, exclude=None):
    def norm(text):
        return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())
    title = norm(title)
    tokens = set(title.split())
    candidates = []
    for task in all_tasks(engine, project):
        if task['status'] == 'done' or task['task_id'] == exclude:
            continue
        other = norm(task['title'])
        words = set(other.split())
        score = len(tokens & words) / max(1, len(tokens | words))
        if title == other or score >= .85:
            candidates.append({'task_id': task['task_id'], 'title': task['title'], 'etag': view(engine, task)['etag']})
    return sorted(candidates, key=lambda row: row['task_id'])[:5]


def write_task(engine, task):
    record = _json(task)
    engine.store.write('task_version', 'INSERT INTO task_versions VALUES(?,?,?,?,?)',
                       (task['task_id'], task['revision'], task['project'], record, digest(task)))
    engine.store.write('task_current', 'INSERT INTO task_current VALUES(?,?) ON CONFLICT(task_id) '
                       'DO UPDATE SET revision=excluded.revision', (task['task_id'], task['revision']))


def create(engine, args, project):
    task_id = str(uuid.UUID(args.task_id)) if args.task_id else str(uuid.uuid4())
    if engine.store._connection.execute('SELECT 1 FROM task_current WHERE task_id=?', (task_id,)).fetchone():
        current = head(engine, task_id, project)
        return {'ok': False, 'code': 'revision_conflict',
                'current_etag': view(engine, current)['etag'] if current else None,
                'current_summary': current and {'title': current['title'], 'status': current['status']}}
    now = stamp()
    owner = args.owner.model_dump() if args.owner else None
    task = {'task_id': task_id, 'project': project, 'context_id': args.context_id,
            'title': args.title, 'status': 'open', 'owner': owner, 'owner_authenticated': False,
            'owner_source': 'client_declared_unattested', 'next_action': args.next_action,
            'blocked_reason': None, 'resolution': None, 'evidence': [e.model_dump() for e in args.evidence],
            'revision': 1, 'created_at': now, 'updated_at': now,
            'provenance': {'owner_at_creation': owner, 'reason': 'create', 'authenticated': False,
                           'client': args.client_provenance.model_dump() if args.client_provenance else None}}
    warnings = duplicates(engine, engine.projects.resolve(project, read=True), args.title)
    write_task(engine, task)
    return {'ok': True, **view(engine, task), 'possible_duplicates': warnings}


def update(engine, args, project):
    current = head(engine, args.task_id, project)
    if current is None:
        return {'ok': False, 'code': 'not_found'}
    failure = conflict(engine, args.expected_etag,
                       {'title': current['title'], 'status': current['status']},
                       current['project'], 'task:' + args.task_id, current['revision'])
    if failure:
        return failure
    patch = args.patch.model_dump(exclude_unset=True)
    reopen = patch.pop('reopen', False)
    status = patch.get('status', current['status'])
    if current['status'] == 'done' and status != 'done':
        if status != 'open':
            return {'ok': False, 'code': 'invalid_transition'}
        if not reopen:
            return {'ok': False, 'code': 'reopen_required'}
    task = {**current, **patch}
    if args.evidence is not None:
        task['evidence'] = [e.model_dump() for e in args.evidence]
    if status == 'blocked' and not ((task.get('blocked_reason') or '').strip() and (task.get('next_action') or '').strip()):
        return {'ok': False, 'code': 'invalid_transition'}
    if status == 'done' and not ((task.get('resolution') or '').strip() and task.get('evidence')):
        return {'ok': False, 'code': 'invalid_transition'}
    if status == 'open':
        task['blocked_reason'] = None
    if task == current:
        return {'ok': True, **view(engine, current), 'unchanged': True}
    task.update(revision=current['revision'] + 1, updated_at=stamp(),
                provenance={'reason': args.reason, 'reopen': reopen,
                            'owner_changed': task['owner'] != current['owner'], 'authenticated': False,
                            'client': args.client_provenance.model_dump() if args.client_provenance else None})
    write_task(engine, task)
    return {'ok': True, **view(engine, task)}


def get(engine, args, project):
    task = head(engine, args.task_id, project)
    if task is None:
        return {'ok': False, 'code': 'not_found'}
    result = {'ok': True, **view(engine, task)}
    if args.include_history:
        result['history'] = [json.loads(r[0]) for r in engine.store._connection.execute(
            'SELECT record_json FROM task_versions WHERE task_id=? ORDER BY revision', (args.task_id,))]
    return result


def listing(engine, args, project):
    """Indexed keyset pages; parse only bounded candidates, never a project dump.

    The cursor pins stale classification time and the immutable project cut.
    A task write invalidates it; aliases and filter changes cannot cross scope.
    """
    con = engine.store._connection
    labels = tuple(getattr(project, 'labels', (project,)))
    # Immutable append-only versions make rowid monotonic. Each project MAX is
    # covered by task_versions_cut, independent of the requested status filter.
    cuts = [(label, con.execute('SELECT COALESCE(MAX(rowid),0) FROM task_versions '
                              'WHERE project=?', (label,)).fetchone()[0]) for label in labels]
    binding = {'version': 2, 'store': engine.store.meta['store_instance_id'],
               'project': list(labels), 'filters': digest([sorted(args.status), args.owner, args.limit]),
               'cut': digest(cuts)}
    at, after = time.time(), None
    if args.cursor:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(args.cursor))
            if any(cursor.get(k) != v for k, v in binding.items()):
                raise ValueError('changed source cut or scope')
            at, after = cursor['at'], cursor['after']
            if (type(at) not in (float, int) or not math.isfinite(at)
                    or not 0 <= time.time() - at <= 86400 or not isinstance(after, list)
                    or len(after) != 3 or type(after[0]) is not int or after[0] not in range(4)
                    or type(after[1]) not in (float, int) or not math.isfinite(after[1])
                    or not isinstance(after[2], str)):
                raise ValueError('invalid cursor')
        except (ValueError, TypeError, KeyError):
            return {'ok': False, 'code': 'cursor_stale'}
    cutoff = at - 14 * 86400
    candidates = []
    # Each branch has equality on the leading index fields, a range on time,
    # ORDER/LIMIT in SQL. Merge at most (labels * statuses * 2 * (limit+1))
    # candidates; no unbounded Python scan/sort. done shares the non-blocked tier.
    for label in labels:
        for status in sorted(set(args.status)):
            for stale in ((False,) if status == 'done' else (False, True)):
                tier = int(stale) * 2 + int(status != 'blocked')
                if after is not None and tier < after[0]:
                    continue
                params = [label, status]
                where = 'l.project=? AND l.status=?'
                if args.owner is not None:
                    where += ' AND l.owner_id=?'
                    params.append(args.owner)
                if status != 'done':
                    where += ' AND l.updated_epoch' + ('<?' if stale else '>=?')
                    params.append(cutoff)
                if after is not None and tier == after[0]:
                    where += ' AND (l.updated_epoch<? OR (l.updated_epoch=? AND l.task_id>?))'
                    params.extend([-after[1], -after[1], after[2]])
                sql = ('SELECT l.updated_epoch,l.task_id,v.record_json FROM task_lookup l '
                       'JOIN task_versions v ON v.task_id=l.task_id AND v.revision=l.revision WHERE ' + where +
                       ' ORDER BY l.updated_epoch DESC,l.task_id LIMIT ?')
                for epoch, task_id, record in con.execute(sql, [*params, args.limit + 1]):
                    candidates.append(((tier, -epoch, task_id), record))
    candidates.sort(key=lambda candidate: candidate[0])
    selected = candidates[:args.limit]
    page = []
    for (tier, _epoch, _id), record in selected:
        value = view(engine, json.loads(record))
        value['stale'] = tier >= 2
        page.append(value)
    following = None
    if len(candidates) > args.limit:
        following = base64.urlsafe_b64encode(_json({**binding, 'at': at, 'after': selected[-1][0]}).encode()).decode()
    return {'ok': True, 'tasks': page, 'cursor': following, 'count': len(page)}


def block_cas(engine, args, project):
    target = None if args.scope == 'global' else project
    key = _json((target, args.name))
    old = engine.store._connection.execute('SELECT sequence,record_json FROM block_versions '
                                            'WHERE block_key=? ORDER BY sequence DESC LIMIT 1', (key,)).fetchone()
    revision, current = (old[0], json.loads(old[1])) if old else (0, None)
    scope = 'global' if target is None else target
    failure = conflict(engine, args.expected_etag, current, scope, 'block:' + args.name, revision)
    if failure:
        return failure
    if len(args.text) > engine.config.block_char_limit:
        return {'ok': False, 'code': 'block_too_large'}
    evidence = [e.model_dump() for e in args.evidence] if args.evidence is not None else (current or {}).get('evidence', [])
    if current is not None and current['text'] == args.text and current.get('evidence', []) == evidence:
        return {'ok': True, 'etag': etag(engine, scope, 'block:' + args.name, revision), 'revision': revision, 'unchanged': True}
    block = Block(args.name, args.text, time.time(), target, args.scope)
    record = {**(current or {}), **asdict(block), 'cas': True, 'reason': args.reason,
              'client_provenance': args.client_provenance.model_dump() if args.client_provenance else None,
              'evidence': evidence}
    sql = engine.store.write('block_version', 'INSERT INTO block_versions(block_key,record_json,record_sha256) VALUES(?,?,?)',
                            (key, _json(record), digest(record)))
    engine.blocks.load()
    return {'ok': True, 'etag': etag(engine, scope, 'block:' + args.name, sql.lastrowid), 'revision': sql.lastrowid}


def preview(engine, args, project):
    from .temporal import is_valid_at
    proposals = [{'source_item_id': i.id, 'title': i.text[:512], 'at': i.event_time,
                  'requires_confirmation': True} for i in engine.store.all(False)
                 if matches(i.project, project) and 'todo' in i.tags and is_valid_at(i, time.time())
                 and 'sensitive' not in i.tags][:20] if args.import_todos else []
    return {'ok': True, 'read_only': True, 'possible_duplicates': duplicates(engine, project, args.title) if args.title else [],
            'proposals': proposals, 'automatic_import': False}


def check_integrity(store):
    for task_id, revision, project, record, checksum in store._connection.execute('SELECT * FROM task_versions'):
        task = json.loads(record)
        if (digest(task) != checksum or (task['task_id'], task['revision'], task['project']) != (task_id, revision, project)
                or not project or project == '*' or task.get('status') not in {'open', 'blocked', 'done'}
                or not isinstance(task.get('title'), str) or not 1 <= len(task['title']) <= 512
                or not isinstance(task.get('next_action'), str) or len(task['next_action']) > 1000
                or task.get('owner_authenticated') is not False or not isinstance(task.get('evidence'), list)
                or len(task['evidence']) > 32 or not isinstance(task.get('provenance'), dict)):
            raise WorkError('task_integrity_failed')
        try:
            uuid.UUID(task_id)
            for key in ('created_at', 'updated_at'):
                if dt.datetime.fromisoformat(task[key]).utcoffset() != dt.timedelta(0):
                    raise ValueError('UTC timestamps required')
        except (ValueError, TypeError, KeyError):
            raise WorkError('task_integrity_failed') from None
    if store._connection.execute('SELECT 1 FROM task_versions v LEFT JOIN task_current c '
                                 'ON c.task_id=v.task_id WHERE c.task_id IS NULL LIMIT 1').fetchone():
        raise WorkError('task_integrity_failed')
    if store._connection.execute('SELECT 1 FROM task_versions GROUP BY task_id HAVING '
                                 'COUNT(DISTINCT project)<>1 OR MAX(revision)<>COUNT(*) LIMIT 1').fetchone():
        raise WorkError('task_integrity_failed')
    if store._connection.execute('SELECT 1 FROM task_current c WHERE c.revision != '
                                 '(SELECT max(v.revision) FROM task_versions v WHERE v.task_id=c.task_id) LIMIT 1').fetchone():
        raise WorkError('task_integrity_failed')

    # Known old archives have no derived lookup. Current schema must match the
    # deterministic projection exactly; never silently trust a tampered index.
    from .sqlite_store import TASK_LOOKUP_SELECT
    if store._connection.execute("SELECT 1 FROM sqlite_master WHERE name='task_lookup'").fetchone():
        current = 'SELECT task_id,revision,project,status,updated_epoch,owner_id FROM task_lookup'
        for left, right in ((current, TASK_LOOKUP_SELECT), (TASK_LOOKUP_SELECT, current)):
            if store._connection.execute('SELECT 1 FROM (' + left + ' EXCEPT ' + right + ') LIMIT 1').fetchone():
                raise WorkError('task_integrity_failed')
