"""B2: read-only impact planning, logical revisions and controlled correction checks."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import time
import uuid

from .extract import normalize_client_facts, _coerce_time
from .fact_updates import duplicate_compatible, equivalent_assertions, replacement_sweep, validate_replacements


class CorrectionError(ValueError):
    def __init__(self, code, message='', *, outcome='not_started', details=None):
        self.code, self.outcome, self.details = code, outcome, details or {}
        super().__init__(message or code)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def revision(item, store=None):
    record = item.to_dict()
    if store is not None and hasattr(store, '_connection'):
        row = store._connection.execute('SELECT record_json FROM item_versions WHERE item_id=? '
                                        'ORDER BY revision DESC LIMIT 1', (item.id,)).fetchone()
        if row:
            record.update(json.loads(row[0]))  # Unknown logical fields are part of CAS.
    return digest({k: v for k, v in record.items() if k not in {'hash', 'access_count', 'last_accessed'}})


def actor(client=None):
    return {**(client or {'client_id': 'unknown'}), 'authenticated': False}


def controlled(mode=None, expected_revisions=None, confirmed_impacts=None, preview_digest=None):
    return mode is not None or any(v is not None for v in (expected_revisions, confirmed_impacts, preview_digest))


class Shadow:
    """No files, receipts, indexes or references to mutable authority objects."""
    def __init__(self, store):
        self.items = {it.id: deepcopy(it) for it in store.all(True)}

    def get(self, item_id):
        return self.items.get(item_id)

    def all(self, include_deleted=True):
        return [it for it in self.items.values() if include_deleted or not it.deleted]

    def append(self, item):
        self.items[item.id] = item


def focus_impacts(store, item, initial_revisions=None):
    shadow = Shadow(store)
    before = {it.id: it.valid_until for it in shadow.all()}
    revs = initial_revisions if initial_revisions is not None else {it.id: revision(it, store) for it in store.all()}
    shadow.append(deepcopy(item))
    pairs = replacement_sweep(shadow, project=item.project, focus=item)
    impacts = [{'target_id': old, 'successor_id': new, 'expected_revision': revs.get(old),
                'before_valid_until': before.get(old), 'valid_until': shadow.get(old).valid_until,
                'mechanism': 'explicit_replaces' if old in item.replaces and new == item.id else 'single_identity'}
               for old, new in pairs]
    return impacts, shadow


def prepare(engine, facts, project, parent_ids=None, event_time=None, source='client', reason=None, context=None):
    """Prepared identities/times make preview/apply stable; never encode or append."""
    normalized = normalize_client_facts(facts)
    prepared = []
    now = time.time()
    shadow = Shadow(engine.store)
    initial = {it.id: revision(it, engine.store) for it in engine.store.all(True)}
    impacts = []
    for nf, raw in zip(normalized, facts):
        for key in ('event_time', 'valid_from', 'valid_until'):
            if raw.get(key) is not None and (_coerce_time(raw[key]) is None or not math.isfinite(_coerce_time(raw[key]))):
                raise CorrectionError('invalid_correction_time')
        from .compensation import check
        restored = check(engine, raw, project, now)
        p = dict(nf)
        if restored:
            for key in ('entities','tags','importance','confidence','keywords','facets'):
                if key in restored:
                    p[key] = deepcopy(restored[key])
        if raw.get('compensates') is not None:
            p['compensates'] = deepcopy(raw['compensates'])
        p['item_id'] = raw.get('item_id') or uuid.uuid4().hex
        if p['item_id'] in shadow.items:
            raise CorrectionError('invalid_target', 'duplicate proposed item id')
        p['event_time'] = nf['event_time'] if nf['event_time'] is not None else event_time if event_time is not None else now
        p['valid_from'] = nf['valid_from'] if nf['valid_from'] is not None else p['event_time']
        for key in ('event_time', 'valid_from', 'valid_until'):
            if p.get(key) is not None:
                p[key] = datetime.fromtimestamp(p[key], timezone.utc).isoformat()
        for key in ('keywords', 'facets'):
            if raw.get(key) is not None:
                p[key] = deepcopy(raw[key])
        prepared.append({k: v for k, v in p.items() if v is not None})
        spec = dict(normalize_client_facts([p])[0], **{k: p[k] for k in ('keywords', 'facets') if k in p},
                    item_id=p['item_id'], project=project, parent_ids=parent_ids, source=source)
        item = engine._build_items([spec], kind=p['kind'])[0]
        try:
            validate_replacements(item, shadow)
        except ValueError as exc:
            code = 'invalid_correction_time' if 'strictly after' in str(exc) or 'valid_until must' in str(exc) else 'invalid_target'
            raise CorrectionError(code, str(exc)) from exc
        if any(duplicate_compatible(old, item) and equivalent_assertions(old, item) for old in shadow.all(False)):
            continue
        if item.kind == 'fact' and (item.replaces or (item.identity or {}).get('cardinality') == 'single'):
            step, shadow = focus_impacts(shadow, item, initial)
            impacts.extend(step)
        else:
            shadow.append(item)
    expected = {i['target_id']: i['expected_revision'] for i in impacts if i['expected_revision'] is not None}
    payload = {'instance': engine.store.meta.get('store_instance_id'), 'project': str(project), 'facts': prepared,
               'parent_ids': list(parent_ids or []) if context is None else [], 'context': context,
               'compensation': any(f.get('compensates') for f in prepared),
               'reason': reason, 'expected_revisions': expected, 'impacts': impacts}
    return {'ok': True, 'outcome': 'not_started', 'prepared_facts': prepared, 'expected_revisions': expected,
            'impacts': impacts, 'preview_digest': digest(payload), 'store_instance_id': payload['instance'],
            'compensation': payload['compensation']}


def precheck(store, expected_revisions, project):
    for item_id, expected in (expected_revisions or {}).items():
        old = store.get(item_id)
        if old is None or old.project != project:
            raise CorrectionError('invalid_target')
        if revision(old, store) != expected:
            raise CorrectionError('revision_conflict')


def guard(plan, *, verified, reason=None, expected_revisions=None, confirmed_impacts=None,
          preview_digest=None, request_id=None):
    impacts = plan['impacts']
    if plan.get('compensation') and (not verified or preview_digest is None):
        raise CorrectionError('compensation_preview_required')
    if not verified:
        if len(impacts) > 3:
            raise CorrectionError('impacts_confirmation_required', 'Legacy >3 impacts requires controlled preview/apply')
        return
    if not impacts:
        return
    if not reason or not reason.strip():
        raise CorrectionError('reason_required')
    if not request_id:
        raise CorrectionError('request_id_required')
    if expected_revisions != plan['expected_revisions']:
        raise CorrectionError('revision_conflict')
    simple = len(impacts) == 1 and impacts[0]['mechanism'] == 'explicit_replaces'
    if not simple or preview_digest is not None or confirmed_impacts is not None:
        if preview_digest is None:
            raise CorrectionError('impacts_confirmation_required', 'Preview required for multiple/implicit impacts')
        if preview_digest != plan['preview_digest']:
            raise CorrectionError('impact_mismatch', 'Obtain a fresh preview with stable IDs/times and reason')
        targets = sorted({i['target_id'] for i in impacts})
        if sorted(confirmed_impacts or []) != targets:
            raise CorrectionError('impacts_confirmation_required')
