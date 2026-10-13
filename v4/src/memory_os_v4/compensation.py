"""B4: explicit compensating assertions; never reopen an old validity window."""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from .corrections import CorrectionError, digest
from .extract import _coerce_time


def secret_like(text):
    # Synthetic FR/EN controls sealed before implementation. Heuristic, not DLP.
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|'
                 r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b|'
                 r'\bbearer\s+[A-Za-z0-9._~-]{8,}|'
                 r'\b(?:api[_ -]?key|password|passwd|mot de passe|jeton(?: accès)?|access[_ -]?token)'
                 r'\s*[:=]\s*["\x27]?[A-Za-z0-9_!./+~-]{6,}', text, re.I):
        return True
    return any(urlsplit(url).username is not None or urlsplit(url).password is not None
               for url in re.findall(r'https?://[^\s<>"\x27]+', text))


def check(engine, raw, project, now=None):
    mark = raw.get('compensates')
    if mark is None:
        return
    if engine.config.store_backend != 'sqlite_s1':
        raise CorrectionError('backend_unsupported')
    source = engine.store.get(mark['restore_id'])
    if (source is None or source.deleted or source.kind != 'fact' or source.project != project
            or {'sensitive', 'secret', 'credential'} & {t.casefold() for t in source.tags}
            or secret_like(source.text)):
        raise CorrectionError('source_not_restorable')
    historical = None
    for record in engine.store.raw_history(source.id):
        rev = digest({k: v for k, v in record.items() if k not in {'hash', 'access_count', 'last_accessed'}})
        if rev == mark['restore_revision']:
            historical = record
            break
    audit = None
    for item in engine.store.all(True):
        if item.project != project:
            continue
        for record in engine.store.raw_history(item.id):
            candidate = record.get('correction')
            if candidate and candidate['audit_id'] == mark['audit_id']:
                audit = candidate
                break
        if audit:
            break
    if (historical is None or audit is None or historical.get('deleted')
            or secret_like(historical['text']) or {'sensitive', 'secret', 'credential'} &
            {t.casefold() for t in historical.get('tags', [])}
            or raw.get('text') != historical['text'] or raw.get('identity') != historical.get('identity')
            or raw.get('kind', 'fact') != 'fact'):
        raise CorrectionError('source_not_restorable')
    matching = [i for i in audit['impacts'] if i['target_id'] == source.id
                and i['expected_revision'] == mark['restore_revision']]
    if not matching or set(raw.get('replaces') or []) != {i['successor_id'] for i in matching}:
        raise CorrectionError('source_not_restorable')
    now = time.time() if now is None else now
    for key in ('event_time', 'valid_from'):
        t = _coerce_time(raw[key]) if raw.get(key) is not None else now
        if t is None or not now - 300 <= t <= now + 5:
            raise CorrectionError('invalid_correction_time')
    if raw.get('valid_until') is not None:
        raise CorrectionError('invalid_correction_time')
