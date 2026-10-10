"""Offline LRU eligibility audit. Source roots are attested, never inferred from row IDs."""
from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import itertools
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from .beam import decode
from .data import array_entries, sha256
from .guard import Limits, ensure_priority, sample
from .mab_acquisition import (CAP, DATASET, FILE_SHA, FIELDS, GO_DATE, GO_QUOTE,
                              JSONL_CAP, MAX_ROW, REMOTE, REVISION, write_json)

MAX_DB = 350_000_000
MAX_QUESTIONS = 100_000
MAX_NODES = 1_000_000
METADATA_FIELDS = {'demo', 'haystack_sessions', 'keypoints', 'previous_events', 'qa_pair_ids',
                   'question_dates', 'question_ids', 'question_types', 'source'}
REQUIRED_OBSERVED = {'beam-100k': ('beam', '613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3'),
                     'beam-500k': ('beam', '3f18f51f9a02e3e2104c77826b0915852f48be9517f33c09f297872eef8871f6'),
                     'lme-s': ('longmemeval', '08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894'),
                     'lme-oracle': ('longmemeval', None), 'locomo': ('locomo', None)}


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def digest(text):
    return hashlib.sha256(text.encode()).digest()


def text_signatures(text, check=lambda: None):
    """O(words) rolling selector, SHA-256 verification; shared 95 words imply overlap.

    Keep only 95 token strings. The rolling 64-bit value selects minima; only
    SHA-256 of the actual normalized 64-word excerpt decides a match.
    """
    value = unicodedata.normalize('NFKC', text).casefold()
    yield 'text', digest(' '.join(value.split()))
    base, mask, width = 1099511628211, (1 << 64) - 1, 64
    power = pow(base, width - 1, 1 << 64)
    tokens, hashes, minimum = deque(maxlen=95), deque(), deque()
    rolling, previous = 0, None
    for index, match in enumerate(re.finditer(r'\S+', value)):
        if index % 2048 == 0:
            check()
        token = match.group()
        tokens.append(token)
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), 'big')
        if len(hashes) == width:
            rolling = (rolling - hashes.popleft() * power) & mask
        rolling = (rolling * base + h) & mask
        hashes.append(h)
        if index < width - 1:
            continue
        position = index - width + 1
        while minimum and minimum[-1][0] >= rolling:
            minimum.pop()
        minimum.append((rolling, position))
        while minimum[0][1] <= position - 32:
            minimum.popleft()
        if position >= 31 and minimum[0] != previous:
            offset = minimum[0][1] - (position - 31)
            yield 'excerpt', digest(' '.join(itertools.islice(tokens, offset, offset + width)))
            previous = minimum[0]


def rows(path, check):
    with path.open('rb') as stream:
        count = 0
        while raw := stream.readline(MAX_ROW + 1):
            check()
            count += 1
            if len(raw) > MAX_ROW or not raw.endswith(b'\n') or count > 1000:
                raise ValueError('Bounded newline-terminated audit rows required')
            yield decode(raw.decode(), max_bytes=MAX_ROW, max_nodes=MAX_NODES)


def inspect_row(row, index):
    if not isinstance(row, dict) or set(row) != FIELDS:
        raise ValueError('Unexpected LRU top-level schema')
    context, questions, answers, metadata = (row[k] for k in ('context', 'questions', 'answers', 'metadata'))
    if not isinstance(context, str) or not context.strip() or not isinstance(metadata, dict):
        raise ValueError('Nonempty context and metadata object required')
    if not isinstance(questions, list) or not questions or any(not isinstance(q, str) or not q.strip() for q in questions):
        raise ValueError('Nonempty question strings required')
    if not isinstance(answers, list) or len(questions) != len(answers):
        raise ValueError('Question/reference alignment failed')
    valid = [i for i, refs in enumerate(answers) if isinstance(refs, list) and refs and
             all(isinstance(a, str) and a.strip() for a in refs)]
    problems = []
    unknown = sorted(set(metadata) - METADATA_FIELDS)
    if unknown:
        problems.append('unknown_metadata_fields')
    for key in ('qa_pair_ids', 'question_ids', 'question_dates', 'question_types', 'previous_events'):
        values = metadata.get(key)
        if values is not None and (not isinstance(values, list) or (values and len(values) != len(questions))):
            problems.append(f'unaligned_{key}')
    if problems:
        valid = []
    source = metadata.get('source')
    if not isinstance(source, str) or not source.strip():
        source = '<unidentified-source-family>'
    return {'row': index, 'context_sha256': hashlib.sha256(context.encode()).hexdigest(),
            'context_bytes': len(context.encode()), 'source_family': source,
            'questions': len(questions), 'usable_reference_questions': len(valid),
            'invalid_reference_questions': len(questions) - len(valid),
            'usable_question_hashes': sorted({digest(normalized(questions[i])).hex() for i in valid}),
            'schema_problems': problems, 'metadata_keys': sorted(metadata),
            'unknown_metadata_keys': unknown,
            'reference_aliases': sum(len(refs) for refs in answers if isinstance(refs, list)),
            'annotation_presence': {k: bool(metadata.get(k)) for k in
                                    ('keypoints', 'haystack_sessions', 'previous_events', 'question_dates')},
            'evidence_ids_available': False,
            'input_policy': 'context only; question at search; all metadata/gold excluded from ingestion'}


def observed_units(path, format_name, check):
    """Known schemas only; no answer or scorer annotations used in overlap detection."""
    if format_name == 'text':
        if path.stat().st_size > MAX_ROW:
            raise ValueError('Observed public text exceeds audit input budget')
        yield 'text', path.read_text()
        return
    entries = rows(path, check) if format_name in {'beam', 'mab'} else array_entries(path, check=check)
    for row in entries:
        check()
        if format_name == 'beam':
            probes = decode(row['probing_questions'], literal=True, max_bytes=MAX_ROW, max_nodes=MAX_NODES)
            questions = [entry['question'] for group in probes.values() for entry in group]
            sessions = [[turn['content'] for turn in session] for session in row['chat']]
        elif format_name == 'longmemeval':
            questions = [row['question']]
            sessions = [[turn.get('content', '') for turn in session] for session in row['haystack_sessions']]
        elif format_name == 'locomo':
            questions = [q['question'] for q in row['qa']]
            sessions = [[turn['text'] for turn in turns] for key, turns in row['conversation'].items()
                        if re.fullmatch(r'session_\d+', key)]
        elif format_name == 'mab':
            questions, sessions = row['questions'], [[row['context']]]
        else:
            raise ValueError('Unknown observed corpus format')
        for question in questions:
            yield 'question', normalized(question)
        for turns in sessions:
            # Whole sessions catch copies that cross turn boundaries; individual
            # turns catch copies embedded in a different session.
            for text in [*turns, '\n'.join(turns)]:
                if not isinstance(text, str):
                    raise ValueError('Non-string observed text')
                if text.strip():
                    yield 'text', text


def source_review(path, clone, check):
    if path is None:
        return {}, None
    if path.stat().st_size > 2_000_000:
        raise ValueError('Source review byte budget exceeded')
    before = sha256(path, check=check)
    review = json.loads(path.read_text())
    if review.get('question_blind') is not True or not review.get('reviewer') or not review.get('date'):
        raise ValueError('Dated question-blind source review required')
    entries, proofs = {}, {}
    for entry in review['contexts']:
        cid = entry['context_sha256']
        if not re.fullmatch('[0-9a-f]{64}', cid) or cid in entries:
            raise ValueError('Unique context hashes required in source review')
        required = ('canonical_work_id', 'original_source_url', 'original_licence', 'licence_url',
                    'mapping_proof_path', 'mapping_proof_sha256')
        if any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in required):
            raise ValueError('Original-work identity, licence and mapping evidence required')
        if entry.get('intended_use_permitted') is not True:
            raise ValueError('Original-source use has not been reviewed')
        if type(entry.get('previously_observed')) is not bool:
            raise ValueError('Prior observation of original public source must be reviewed')
        if any(not entry[k].startswith('https://') for k in ('original_source_url', 'licence_url')):
            raise ValueError('HTTPS primary-source references required')
        proof = Path(entry['mapping_proof_path']).resolve()
        if (not proof.is_relative_to(clone / '.local-qualification') or proof.stat().st_size > 2_000_000
                or sha256(proof, check=check) != entry['mapping_proof_sha256']):
            raise ValueError('Clone-private mapping evidence missing/changed')
        proofs[str(proof)] = entry['mapping_proof_sha256']
        entries[cid] = entry
    if sha256(path, check=check) != before:
        raise ValueError('Source review changed during loading')
    return entries, {'path': str(path), 'sha256': before, 'proofs': proofs,
                     'reviewer': review['reviewer'], 'date': review['date']}


def audit(data, database, observed, check, review=None):
    """Only target fingerprints persist. Observed text is streamed and discarded."""
    review = review or {}
    records, parent = [], {}
    def root(cid):
        while parent[cid] != cid:
            parent[cid] = parent[parent[cid]]
            cid = parent[cid]
        return cid
    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)

    with sqlite3.connect(database) as conn:
        conn.executescript('''PRAGMA cache_size=-8192; PRAGMA temp_store=FILE;
        CREATE TABLE fingerprints(kind TEXT, value BLOB, cid INTEGER,
          PRIMARY KEY(kind,value,cid)) WITHOUT ROWID;
        CREATE TABLE overlaps(cid INTEGER, corpus TEXT, kind TEXT,
          PRIMARY KEY(cid,corpus,kind)) WITHOUT ROWID;
        CREATE TABLE seen(value BLOB PRIMARY KEY) WITHOUT ROWID;
        CREATE TEMP TABLE batch(kind TEXT,value BLOB,PRIMARY KEY(kind,value)) WITHOUT ROWID;''')
        def budget():
            check()
            if conn.execute('PRAGMA page_count').fetchone()[0] * conn.execute('PRAGMA page_size').fetchone()[0] > MAX_DB:
                raise ValueError('Fingerprint database byte budget exceeded')
        for index, row in enumerate(rows(data, check)):
            if index >= 110:
                raise ValueError('Pinned context count exceeded')
            record = inspect_row(row, index)
            records.append(record)
            parent[index] = index
            if sum(r['questions'] for r in records) > MAX_QUESTIONS:
                raise ValueError('Question audit count budget exceeded')
            signatures = itertools.chain(text_signatures(row['context'], check),
                                         (('question', digest(normalized(q))) for q in row['questions']))
            conn.executemany('INSERT OR IGNORE INTO fingerprints VALUES(?,?,?)',
                             ((kind, value, index) for kind, value in signatures))
            conn.commit()
            budget()
        # Shared full text, long excerpts or exact questions link components.
        cursor = conn.execute('SELECT kind,value,cid FROM fingerprints ORDER BY kind,value,cid')
        for _, group in itertools.groupby(cursor, key=lambda row: (row[0], row[1])):
            owners = [row[2] for row in group]
            for cid in owners[1:]:
                union(owners[0], cid)
        fingerprint_components = len({root(cid) for cid in parent})
        identities = {}
        for r in records:
            proof = review.get(r['context_sha256'])
            # An unreviewed source family is conservatively ONE root, never
            # promoted to one independent book per row/QA ID/context hash.
            labels = [('work', proof['canonical_work_id']), ('url', proof['original_source_url'])] if proof else [('unresolved-family', r['source_family'])]
            r['source_attested'] = proof is not None
            r['canonical_work_id'] = proof['canonical_work_id'] if proof else None
            r['original_licence'] = proof['original_licence'] if proof else None
            for label in labels:
                if label in identities:
                    union(identities[label], r['row'])
                else:
                    identities[label] = r['row']
        for item in observed:
            check()
            corpus = item['name']
            pending = []
            def flush():
                if not pending:
                    return
                conn.executemany('INSERT OR IGNORE INTO batch VALUES(?,?)', pending)
                conn.execute('INSERT OR IGNORE INTO overlaps SELECT DISTINCT f.cid,?,f.kind '
                             'FROM fingerprints f JOIN batch b USING(kind,value)', (corpus,))
                conn.execute('DELETE FROM batch')
                pending.clear()
                conn.commit()
                budget()
            for kind, text in observed_units(item['path'], item['format'], check):
                value = digest(normalized(text))
                if kind == 'question':
                    signatures = [(kind, value)]
                else:
                    # Repeated distractors and oracle/S sessions are hashed once.
                    if conn.execute('SELECT 1 FROM seen WHERE value=?', (value,)).fetchone():
                        continue
                    conn.execute('INSERT INTO seen VALUES(?)', (value,))
                    signatures = text_signatures(text, check)
                for signature in signatures:
                    pending.append(signature)
                    if len(pending) >= 1024:
                        flush()
            flush()
            conn.commit()
            budget()
        overlaps = list(conn.execute('SELECT cid,corpus,kind FROM overlaps ORDER BY cid,corpus,kind'))
    groups = {}
    for r in records:
        r['cluster'] = root(r['row'])
        groups.setdefault(r['cluster'], []).append(r)
    excluded = {root(cid) for cid, _, _ in overlaps}
    excluded.update(root(r['row']) for r in records if
                    review.get(r['context_sha256'], {}).get('previously_observed') is True)
    eligible = {key: group for key, group in groups.items() if key not in excluded and
                all(r['source_attested'] for r in group)}
    available = set()
    for group in eligible.values():
        for r in group:
            available.update(r['usable_question_hashes'])
    reasons = []
    if len(eligible) < 30:
        reasons.append('fewer_than_30_fresh_attested_source_groups')
    if len(available) < 400:
        reasons.append('fewer_than_400_unique_usable_questions_in_fresh_attested_groups')
    if len(records) != 110:
        reasons.append('pinned_context_count_mismatch')
    families = Counter(r['source_family'] for r in records)
    return {'complete': True, 'verdict': 'NO-GO' if reasons else 'GO', 'reasons': reasons,
            'campaign_authorized': False, 'requires_sealed_plan_and_protocol': True, 'inference_calls': 0,
            'contexts': len(records), 'questions': sum(r['questions'] for r in records),
            'usable_reference_questions': sum(r['usable_reference_questions'] for r in records),
            'all_unique_usable_questions': len({q for r in records for q in r['usable_question_hashes']}),
            'fingerprint_only_components': fingerprint_components,
            'fresh_attested_groups': len(eligible), 'fresh_unique_usable_questions': len(available),
            'conservative_components': len(groups), 'excluded_components': len(excluded),
            'source_families': dict(families), 'source_identity_unresolved_contexts': sum(not r['source_attested'] for r in records),
            'overlaps': [{'row': cid, 'corpus': corpus, 'kind': kind} for cid, corpus, kind in overlaps],
            'eligible_clusters': sorted(eligible), 'records': records,
            'evidence': 'Answer references usable for E2E judging; no mapped positive evidence IDs asserted. '
                        'keypoints/has_answer are scorer-only, not retrieval truth or ingestion inputs.',
            'limitations': ['No semantic or model-pretraining independence guarantee',
                           'Generic source labels and QA IDs cannot attest original books',
                           'Shared 95-word excerpts conservatively merge roots, including possible boilerplate',
                           'GO is dataset eligibility only, never inference authorization'],
            'fingerprint_policy': 'NFKC/casefold whitespace normalization; exact questions/text; '
                                  '64-word SHA-256 shingles selected by 32-shingle rolling minima'}


def verify_acquisition(directory, check):
    p = json.loads((directory / 'PROVENANCE.json').read_text())
    expected = {'dataset': DATASET, 'split': 'Long_Range_Understanding', 'pinned_revision': REVISION,
                'file': REMOTE, 'licence': 'MIT', 'parquet_sha256': FILE_SHA, 'contexts': 110,
                'inference_calls': 0, 'download_cap_bytes': CAP}
    if any(p.get(key) != value for key, value in expected.items()):
        raise ValueError('Approved pinned acquisition provenance required')
    if (p.get('revision') != REVISION
            or p.get('romain_download_go', {}).get('quote') != GO_QUOTE
            or p.get('romain_download_go', {}).get('date') != GO_DATE
            or type(p.get('download_bytes')) is not int or not 0 < p['download_bytes'] <= CAP
            or p['download_bytes'] != p.get('upstream_physical_bytes')
            or not 0 < p.get('jsonl_bytes', 0) <= JSONL_CAP):
        raise ValueError('GO/physical byte/revision provenance mismatch')
    files = {'mab_lru.parquet': FILE_SHA, 'mab_lru.jsonl': p['jsonl_sha256'],
             'README.upstream.md': p['card_sha256'], 'upstream-info.json': p['upstream_info_sha256'],
             'upstream-tree.json': p['upstream_tree_sha256']}
    for name, expected_hash in files.items():
        if sha256(directory / name, check=check) != expected_hash:
            raise ValueError(f'Acquisition evidence changed: {name}')
    if ((directory / 'mab_lru.parquet').stat().st_size != p['download_bytes'] or
            (directory / 'mab_lru.jsonl').stat().st_size != p['jsonl_bytes']):
        raise ValueError('Acquisition byte sizes changed')
    if p.get('acquisition_script_sha256') != sha256(Path(__file__).with_name('mab_acquisition.py'), check=check):
        raise ValueError('Acquisition implementation changed')
    return p


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path, required=True)
    parser.add_argument('--observed-registry', type=Path, required=True)
    parser.add_argument('--source-review', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    clone = Path(__file__).resolve().parents[3]
    acquisition, output = args.acquisition.resolve(), args.output.resolve()
    if any(not p.is_relative_to(clone / '.local-qualification') for p in (acquisition, output)) or output.exists():
        parser.error('Clone-private acquisition and fresh audit output required')
    output.mkdir(mode=0o700, parents=True)
    try:
        ensure_priority(Limits())
        started = time.monotonic()
        def check():
            sample(clone)
            if time.monotonic() - started > 7200:
                raise ValueError('Zero-inference audit wall-time budget exceeded')
        check()
        provenance = verify_acquisition(acquisition, check)
        provenance_hash = sha256(acquisition / 'PROVENANCE.json', check=check)
        if args.observed_registry.stat().st_size > 2_000_000:
            raise ValueError('Observed registry byte budget exceeded')
        registry_hash = sha256(args.observed_registry, check=check)
        registry = json.loads(args.observed_registry.read_text())
        items = registry['corpora']
        if not set(REQUIRED_OBSERVED) <= {r['name'] for r in items} or len({r['name'] for r in items}) != len(items):
            raise ValueError('All known observed corpora and unique registry names required')
        hashes = {}
        for item in items:
            path = Path(item['path'])
            item['path'] = path if path.is_absolute() else clone / path
            before = sha256(item['path'], check=check)
            if item['name'] in REQUIRED_OBSERVED:
                required_format, required_sha = REQUIRED_OBSERVED[item['name']]
                if item['format'] != required_format or (required_sha and before != required_sha):
                    raise ValueError('Required observed base corpus was substituted')
            if item.get('sha256') is not None and before != item['sha256']:
                raise ValueError('Observed corpus fingerprint differs from frozen registry')
            hashes[str(item['path'])] = before
        review, review_record = source_review(args.source_review, clone, check)
        report = audit(acquisition / 'mab_lru.jsonl', output / 'fingerprints.sqlite', items, check, review)
        # Neither candidate nor observed inputs may change during the scan.
        verify_acquisition(acquisition, check)
        if (sha256(acquisition / 'PROVENANCE.json', check=check) != provenance_hash or
                sha256(acquisition / 'mab_lru.jsonl', check=check) != provenance['jsonl_sha256'] or
                sha256(args.observed_registry, check=check) != registry_hash):
            raise ValueError('Candidate provenance/JSONL/registry changed during audit')
        for name, before in hashes.items():
            if sha256(Path(name), check=check) != before:
                raise ValueError('Observed corpus changed during audit')
        if review_record:
            for name, before in {review_record['path']: review_record['sha256'], **review_record['proofs']}.items():
                if sha256(Path(name), check=check) != before:
                    raise ValueError('Source review or mapping evidence changed during audit')
        report.update(licence=provenance['licence'], licence_scope=provenance['licence_scope'],
                      provenance_sha256=provenance_hash,
                      dataset_sha256=provenance['jsonl_sha256'], source_review=review_record,
                      observed_files=hashes, observed_registry_sha256=registry_hash,
                      audit_script_sha256=sha256(Path(__file__), check=check),
                      seconds=time.monotonic() - started)
        write_json(output / 'report.json', report)
        template = {'reviewer': None, 'date': None, 'question_blind': True,
                    'contexts': [{'context_sha256': r['context_sha256'], 'source_family': r['source_family'],
                                  'canonical_work_id': None, 'original_source_url': None, 'original_licence': None,
                                  'licence_url': None, 'mapping_proof_path': None, 'mapping_proof_sha256': None,
                                  'intended_use_permitted': False, 'previously_observed': None} for r in report['records']
                                 if not r['source_attested']]}
        write_json(output / 'source-review-template.json', template)
    except Exception as exc:
        write_json(output / 'FAILED.json', {'complete': False, 'verdict': 'NO-GO',
                                          'reason': str(exc), 'failure_type': type(exc).__name__,
                                          'inference_calls': 0, 'campaign_authorized': False,
                                          'restart_in_place': False})
        raise
    print(json.dumps({k: report[k] for k in ('verdict', 'contexts', 'questions', 'fresh_attested_groups',
                                           'fresh_unique_usable_questions', 'reasons', 'inference_calls')}))
    if report['verdict'] == 'NO-GO':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
