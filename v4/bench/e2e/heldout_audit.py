"""Scorer-only freshness screening. Nothing from this audit reaches retrieval."""
from __future__ import annotations

from collections import deque
import hashlib
import json
from pathlib import Path

from .beam import CATEGORIES, conversations, decode, identity, text_key
from .data import array_entries, sha256

MAX_ROW = 16_000_000
MAX_NODES = 500_000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def signatures(row):
    """Conservative exact/lexical screen, NOT a proof of semantic independence."""
    keys = {('source', str(row['conversation_id']))}
    seed = row['conversation_seed']
    if not isinstance(seed, dict) or not all(k in seed for k in ('category', 'id', 'title', 'theme')):
        raise ValueError('Seed provenance required for held-out audit')
    # Reused seed ids within a category count as dependent even if rewritten.
    keys.add(('seed', digest([seed['category'], seed['id']])))
    keys.add(('seed-title', text_key(seed['title'])))
    keys.add(('seed-theme', text_key(seed['theme'])))
    narrative = text_key(row['narratives'])
    if len(narrative) >= 80:
        keys.add(('narrative', digest(narrative)))
    probes = decode(row['probing_questions'], literal=True)
    for entries in probes.values():
        for p in entries:
            keys.add(('question', text_key(p['question'])))
    for session in row['chat']:
        for turn in session:
            keys.update(text_signatures(turn['content']))
    return {k for k in keys if k[1]}


def text_signatures(text):
    value = text_key(text)
    keys = {('turn', digest(value))} if len(value) >= 160 else set()
    words = value.split()
    # Position-independent winnowing: 64-word shingles, 32-shingle window.
    # A shared 95-word sequence guarantees a shared selected fingerprint;
    # unlike fixed offsets, inserting a header cannot hide copied histories.
    minimum = deque()
    previous = None
    for i in range(len(words) - 63):
        h = digest(words[i:i + 64])
        while minimum and minimum[-1][0] >= h:
            minimum.pop()
        minimum.append((h, i))
        while minimum[0][1] <= i - 32:
            minimum.popleft()
        if i >= 31 and minimum[0] != previous:
            keys.add(('excerpt', minimum[0][0]))
            previous = minimum[0]
    return keys


def raw_rows(path):
    with Path(path).open('rb') as stream:
        while raw := stream.readline(MAX_ROW + 1):
            if len(raw) > MAX_ROW or not raw.endswith(b'\n'):
                raise ValueError('Bounded newline-terminated BEAM rows required')
            yield decode(raw.decode(), max_bytes=MAX_ROW, max_nodes=MAX_NODES)


def screening(data, observed_beam, observed_lme, check=lambda: None):
    """Stream histories; retain fingerprints only. Exclude whole conversations."""
    observed = set()
    for row in raw_rows(observed_beam):
        check()
        observed.update(signatures(row))
    for entry in array_entries(Path(observed_lme), check=check):
        observed.add(('question', text_key(entry['question'])))
        for session in entry['haystack_sessions']:
            for turn in session:
                observed.update(text_signatures(turn.get('content', '')))
    rows = []
    seen = set()
    for row in raw_rows(data):
        check()
        cid = identity('beam-conversation-v1', row['conversation_id'])
        if cid in seen:
            raise ValueError('Duplicate source conversations')
        seen.add(cid)
        keys = signatures(row)
        overlap = keys & observed
        rows.append((cid, row['conversation_id'], keys, sorted({k[0] for k in overlap})))
    parent = {cid: cid for cid, _, _, overlap in rows if not overlap}
    def root(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid
    owners = {}
    for cid, _, keys, overlap in rows:
        if overlap:
            continue
        for key in sorted(keys):
            if key in owners:
                a, b = root(cid), root(owners[key])
                parent[max(a, b)] = min(a, b)
            else:
                owners[key] = cid
    return {'eligible': sorted(parent), 'clusters': {cid: root(cid) for cid in sorted(parent)},
            'excluded': [{'conversation': cid, 'source_id': sid, 'reasons': overlap}
                         for cid, sid, _, overlap in rows if overlap],
            'total_conversations': len(rows), 'independent_components': len({root(c) for c in parent}),
            'files': {str(Path(p).resolve()): sha256(Path(p)) for p in (data, observed_beam, observed_lme)},
            'limits': 'Exact seed/title/theme/narrative/question and text/excerpt fingerprints; semantic or model-training contamination cannot be excluded'}


def selection(data, audit):
    """One hash-selected question/category/conversation, no answer or outcome."""
    eligible = set(audit['eligible'])
    chosen = []
    for c in conversations(Path(data), max_row=MAX_ROW, max_nodes=MAX_NODES):
        if c.id not in eligible:
            continue
        for category in CATEGORIES:
            probes = [p for p in c.probes if p.gold.category == category]
            if not probes:
                raise ValueError('All ten categories required per eligible conversation')
            probe = min(probes, key=lambda p: digest(['heldout-answer-v1', p.id]))
            chosen.append({'qid': probe.id, 'conversation': c.id, 'category': category,
                           'cluster': audit['clusters'][c.id]})
    chosen.sort(key=lambda p: digest(['heldout-answer-order-v1', p['qid']]))
    if not 300 <= len(chosen) <= 350 or len({q['cluster'] for q in chosen}) < 30:
        raise ValueError(f"Freshness/CI preflight refused: {len(chosen)} questions, {len(set(audit['clusters'].values()))} clusters; need 300–350 and >=30. No inference authorized.")
    return chosen
