"""B5: prospective, transactional change feed. No embedding/model access."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid

from .corrections import CorrectionError
from .sqlite_store import _json

ALERT_BYTES = 10 * 1024 * 1024
READS = {'status', 'flush', 'memory_status', 'memory_request_status', 'memory_session_brief',
         'memory_search', 'memory_get_context', 'memory_timeline', 'memory_block_get',
         'memory_list_documents', 'memory_search_occurrences', 'memory_task_get',
         'memory_task_list', 'memory_task_preview'}


def initialize(store, *, reset=False):
    row = store._connection.execute("SELECT value_json FROM store_metadata WHERE key='change_journal'").fetchone()
    if row and not reset:
        return json.loads(row[0])
    with store.transaction():
        high = store._connection.execute('SELECT COALESCE(MAX(sequence),0) FROM change_events').fetchone()[0]
        state = {'generation': uuid.uuid4().hex, 'key': secrets.token_hex(32),
                 'coverage_start': time.time(), 'floor': high, 'high': high}
        store.write('journal_state', "INSERT INTO store_metadata VALUES('change_journal',?) "
                    'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json', (_json(state),))
    return state


def state(store):
    return json.loads(store._connection.execute("SELECT value_json FROM store_metadata WHERE key='change_journal'").fetchone()[0])


def cut(store):
    con = store._connection
    return {table: con.execute(f'SELECT COALESCE(MAX(rowid),0) FROM {table}').fetchone()[0]
            for table in ('item_versions', 'block_versions', 'task_versions')}


def record(engine, operation, payload, result, before):
    if operation in READS or (isinstance(result, dict) and result.get('ok') is False) or result is False:
        return None
    groups = {}
    for table, kind in [('item_versions',None),('block_versions','block'),('task_versions','task')]:
        for (raw,) in engine.store._connection.execute(f'SELECT record_json FROM {table} WHERE rowid>? ORDER BY rowid', (before[table],)):
            row = json.loads(raw)
            if kind is None:
                previous = engine.store._connection.execute('SELECT record_json FROM item_versions WHERE item_id=? AND rowid<=? ORDER BY revision DESC LIMIT 1', (row['id'], before[table])).fetchone()
                if previous:
                    from .corrections import digest
                    logical=lambda r:digest({k:v for k,v in r.items() if k not in {'hash','access_count','last_accessed'}})
                    if logical(row)==logical(json.loads(previous[0])):
                        continue
            project = row.get('project')
            if project is None:  # Explicit global block scope, never leak to a project.
                project = None
            ident = row.get('id') or row.get('task_id') or 'block:' + row.get('name', '')
            ref = {'id': ident, 'kind': kind or row['kind']}
            if kind is None:
                from .corrections import digest
                ref['revision'] = digest({k:v for k,v in row.items() if k not in {'hash','access_count','last_accessed'}})
            elif row.get('revision') is not None:
                ref['revision'] = row['revision']
            groups.setdefault(project, {})[(ref['kind'], ref['id'])] = ref
    data = result if isinstance(result, dict) else {}
    facts = data.get('facts', data)
    source = payload.get('kwargs', payload)
    project = source.get('project') or source.get('project_label')
    if not groups and (data.get('deduped') or facts.get('deduped') or data.get('unchanged')) and project:
        groups[str(engine.projects.resolve(project, read=False))] = {}
    if not groups:
        return None  # metadata/cache/initialization, not a semantic mutation
    actor = data.get('changed_by') or source.get('client_provenance') or {'client_id':'unknown'}
    actor = {**actor, 'authenticated':False}
    reason = source.get('reason')
    from .compensation import secret_like
    if reason and secret_like(reason):
        reason = '[redacted]'
    impacts = data.get('impacts', facts.get('impacts', []))
    audits = data.get('audits', facts.get('audits', []))
    journal = state(engine.store)
    last = None
    for scope, refs in sorted(groups.items(), key=lambda p: p[0] or ''):
        sequence = journal['high'] + 1
        value = {'sequence': sequence, 'event_id': uuid.uuid4().hex, 'operation':operation,
                 'project':scope, 'committed_at':time.time(), 'changed_by':actor, 'reason':reason,
                 'impacts': [i for i in impacts if engine.store.get(i['target_id']) and engine.store.get(i['target_id']).project == scope],
                 'refs':list(refs.values()), 'audit_ids':[a['audit_id'] for a in audits if engine.store.get(a['item_id']) and engine.store.get(a['item_id']).project == scope],
                 'no_op':not refs}
        raw = _json(value)
        engine.store.write('change_event', 'INSERT INTO change_events VALUES(?,?,?,?,?,?,?)',
                           (sequence,value['event_id'],operation,'global' if scope is None else 'project:'+scope,raw,hashlib.sha256(raw.encode()).hexdigest(),value['committed_at']))
        journal['high'] = sequence
        last = {'event_id':value['event_id'], 'sequence':sequence}
    engine.store.write('journal_state', "UPDATE store_metadata SET value_json=? WHERE key='change_journal'", (_json(journal),))
    if isinstance(result, dict) and operation.startswith('memory_'):
        result['change_event'] = last
    return last


def _encode(value, key):
    raw = _json(value).encode()
    mac = hmac.new(bytes.fromhex(key), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + mac).decode().rstrip('=')


def listing(engine, project, cursor=None, limit=30):
    if engine.config.store_backend != 'sqlite_s1':
        raise CorrectionError('backend_unsupported')
    if project is None or not str(project) or str(project) == '*':
        raise CorrectionError('invalid_project')
    with engine._lock:
        engine._ensure_open()
        labels = list(getattr(project, 'labels', (str(project),)))
        journal = state(engine.store)
        binding = {'v':1,'store':engine.store.meta['store_instance_id'], 'generation':journal['generation'], 'scope':labels}
        after = journal['floor']
        if cursor is not None:
            try:
                if len(cursor)>2048:
                    raise ValueError('size')
                raw = base64.b64decode(cursor + '='*((-len(cursor))%4), altchars=b'-_', validate=True)
                # 4.1.3: only the canonical spelling (the one _encode produces) is accepted. The decoder
                # above ignores the padding bits of the last character and accepts explicit '=' padding,
                # so several strings mapped to the same bytes; re-encoding closes that malleability.
                if base64.urlsafe_b64encode(raw).decode().rstrip('=') != cursor:
                    raise ValueError('non-canonical cursor')
                body,mac = raw[:-32],raw[-32:]
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError('cursor object required')
                # Untrusted stale binding may only produce a refusal, never data.
                if value.get('store') != binding['store'] or value.get('generation') != binding['generation']:
                    raise CorrectionError('identity_changed')
                if not hmac.compare_digest(hmac.new(bytes.fromhex(journal['key']),body,hashlib.sha256).digest(),mac):
                    raise ValueError('MAC')
                if {k:value.get(k) for k in binding} != binding or type(value.get('sequence')) is not int:
                    raise ValueError('scope/sequence')
                after = value['sequence']
                if after < journal['floor']:
                    raise CorrectionError('cursor_expired')
                if after > journal['high']:
                    raise ValueError('future')
            except CorrectionError:
                raise
            except (ValueError,TypeError,KeyError):
                raise CorrectionError('cursor_invalid') from None
        placeholders = ','.join('?' for _ in labels)
        rows = engine.store._connection.execute(
            f'SELECT record_json FROM change_events WHERE project IN ({placeholders}) AND sequence>? ORDER BY sequence LIMIT ?',
            (*('project:'+label for label in labels),after,limit+1)).fetchall()
        events = [json.loads(row[0]) for row in rows[:limit]]
        next_seq = events[-1]['sequence'] if events else after
        return {'ok':True,'events':events,'next_cursor':_encode({**binding,'sequence':next_seq},journal['key']),
                'has_more':len(rows)>limit,'coverage_start':journal['coverage_start'], 'coverage':'prospective',
                'notices':['historical_changes_not_observable'], 'store_instance_id':binding['store']}


def statistics(con):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='change_events'").fetchone():
        return {'available':False,'events':0,'json_bytes':0,'alert_bytes':ALERT_BYTES}
    count,size = con.execute('SELECT count(*),COALESCE(sum(length(CAST(record_json AS BLOB))),0) FROM change_events').fetchone()
    return {'available':True,'events':count,'json_bytes':size,'alert_bytes':ALERT_BYTES,
            'alert':size>=ALERT_BYTES,'automatic_purge':False}


def prune(engine, *, before, dry_run=True):
    engine.require_maintenance(dry_run)
    if before > time.time()-30*86400:
        raise CorrectionError('invalid_retention_cut')
    with engine._lock:
        rows = engine.store._connection.execute('SELECT sequence FROM change_events WHERE committed_at<? ORDER BY sequence',(before,)).fetchall()
        # Only a prefix can be removed; otherwise a cursor could silently skip a gap.
        first_kept = engine.store._connection.execute('SELECT MIN(sequence) FROM change_events WHERE committed_at>=?',(before,)).fetchone()[0]
        eligible = [r[0] for r in rows if first_kept is None or r[0]<first_kept]
        if not dry_run and eligible:
            engine.require_writes()
            with engine.store.transaction():
                journal=state(engine.store)
                engine.store.write('change_retention','DELETE FROM change_events WHERE sequence<=?',(max(eligible),))
                journal['floor']=max(eligible)
                engine.store.write('journal_state',"UPDATE store_metadata SET value_json=? WHERE key='change_journal'",(_json(journal),))
        return {'dry_run':dry_run,'eligible_events':len(eligible),'automatic_purge':False}


def check_integrity(store):
    journal=state(store)
    if (not isinstance(journal.get('key'),str) or len(bytes.fromhex(journal['key']))!=32
            or not isinstance(journal.get('generation'),str) or not journal['generation']
            or type(journal.get('floor')) is not int or type(journal.get('high')) is not int
            or not 0<=journal['floor']<=journal['high']):
        raise ValueError('Invalid journal state')
    for seq,event_id,op,scope,raw,checksum,at in store._connection.execute('SELECT * FROM change_events'):
        obj=json.loads(raw)
        key='global' if obj['project'] is None else 'project:'+obj['project']
        if (hashlib.sha256(raw.encode()).hexdigest()!=checksum or
                (obj['sequence'],obj['event_id'],obj['operation'],key,obj['committed_at'])!=(seq,event_id,op,scope,at)
                or seq>journal['high']):
            raise ValueError('Invalid committed change event')
