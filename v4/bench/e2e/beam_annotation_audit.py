"""Read-only annotation diagnostics: no answers, references, encoder or model calls."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .beam import decode, evidence_ids, identity
from .guard import Limits, ensure_priority, sample
from .heldout_audit import MAX_NODES, MAX_ROW


def row_diagnostics(row):
    chat_ids = {t['id'] for session in row['chat'] for t in session}
    probes = decode(row['probing_questions'], literal=True, max_bytes=MAX_ROW, max_nodes=MAX_NODES)
    issues, counts = [], Counter()
    for category, entries in probes.items():
        for index, entry in enumerate(entries):
            status, absent = 'valid_ids', []
            if 'source_chat_ids' not in entry:
                status = 'abstention_without_ids' if category == 'abstention' else 'missing_ids'
            else:
                try:
                    refs = evidence_ids(entry['source_chat_ids'])
                    absent = sorted(set(refs) - chat_ids)
                    if absent:
                        status = 'dangling_ids'
                    elif category == 'abstention' and refs:
                        status = 'abstention_positive_ids'
                except ValueError:
                    status = 'invalid_id_type'
            counts[status] += 1
            if status not in {'valid_ids', 'abstention_without_ids'}:
                alternate = entry.get('conversation_references')
                issues.append({'source_conversation_id': row['conversation_id'],
                               'qid_hash': identity(row['conversation_id'], entry.get('question')),
                               'category': category, 'probe_index': index, 'status': status,
                               'entry_keys': sorted(entry), 'absent_chat_ids': absent,
                               'conversation_references_type': type(alternate).__name__,
                               'conversation_references_element_types': sorted({type(v).__name__ for v in alternate})
                               if isinstance(alternate, list) else []})
    return counts, issues


def audit_file(data, expected_sha256, check):
    """Bounded sequential stream; report only structural metadata, never gold text."""
    counts, issues, seen, digest = Counter(), [], set(), hashlib.sha256()
    with data.open('rb') as stream:
        while True:
            check()
            raw = stream.readline(MAX_ROW + 1)
            if not raw:
                break
            if len(raw) > MAX_ROW or len(seen) >= 100:
                raise ValueError('Annotation audit row/count budget exceeded')
            digest.update(raw)
            row = decode(raw.decode(), max_bytes=MAX_ROW, max_nodes=MAX_NODES)
            cid = row['conversation_id']
            if cid in seen:
                raise ValueError('Duplicate conversation id')
            seen.add(cid)
            row_counts, row_issues = row_diagnostics(row)
            counts.update(row_counts)
            issues.extend(row_issues)
            if len(issues) > 5000:
                raise ValueError('Diagnostic issue budget exceeded')
    if digest.hexdigest() != expected_sha256:
        raise ValueError('Dataset SHA-256 changed; diagnostic refused')
    return {'complete': True, 'inference_calls': 0, 'source_modified': False,
            'dataset_sha256': digest.hexdigest(), 'conversations': len(seen),
            'counts': dict(counts), 'issues': issues,
            'policy': 'diagnostics only; no annotation mapping, exclusions or protocol relaxation'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    clone = Path(__file__).resolve().parents[3]
    local = clone / '.local-qualification'
    data, output = args.data.resolve(), args.output.resolve()
    if not data.is_relative_to(local) or not output.is_relative_to(local) or output.exists():
        parser.error('Existing clone-private data and fresh clone-private output required')
    ensure_priority(Limits())
    check = lambda: sample(clone)
    check()
    report = audit_file(data, args.expected_sha256, check)
    output.mkdir(mode=0o700, parents=True)
    with (output / 'report.json').open('x') as target:
        json.dump(report, target, indent=2)
    print(json.dumps({'complete': True, 'conversations': report['conversations'], 'counts': report['counts']}))


if __name__ == '__main__':
    main()
