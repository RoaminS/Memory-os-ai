"""Read-only historical diagnosis and nonofficial re-score; no models or network."""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
import time

from .answers import VERSION, response_metrics
from .data import cases, sha256
from .guard import Limits, ResourceStop, ensure_priority, sample
from .run import code_record, verify_source, write_json
from .stats import percentile, summarize


def diagnostic(rows, golds, repetitions=2000, check=None):
    gold_by_id = {g.id: g for g in golds}
    rescored = []
    for old in rows:
        row = dict(old)
        if row['status'] != 'not_applicable':
            row.update(response_metrics(row.get('response'), gold_by_id[row['qid']].answers))
        else:
            row.update(answer_em=None, answer_f1=None, answer_contains=None)
        rescored.append(row)
    systems = sorted({r['system'] for r in rows})
    report = summarize(rescored, golds, systems, 20261009, repetitions, check)
    memory = {r['qid']: r for r in rows if r['system'] == 'memory'}
    bm25 = {r['qid']: r for r in rows if r['system'] == 'bm25'}
    examples, categories = [], defaultdict(list)
    for qid, a in memory.items():
        b = bm25.get(qid)
        if not b or a.get('mrr') is None or b.get('mrr') is None:
            continue
        delta = a['mrr'] - b['mrr']
        examples.append({'qid': qid, 'category': gold_by_id[qid].category,
                         'delta_mrr': delta, 'memory_ids': a['retrieved_turn_ids'],
                         'bm25_ids': b['retrieved_turn_ids'],
                         'gold_ids': list(gold_by_id[qid].evidence)})
        categories[gold_by_id[qid].category].append((delta, a['recall'] - b['recall']))
    phases = {}
    entries = list(memory.values())
    total = sum(r.get('end_to_end_ms', 0) for r in entries)
    for phase in ('init_ms', 'ingest_ms', 'retrieval_ms', 'response_ms', 'end_to_end_ms'):
        values = [r[phase] for r in entries if phase in r]
        phases[phase] = {'p50': percentile(values, .5), 'sum_ms': sum(values),
                         'share_of_total': sum(values) / total if total else None}
    counted = sum(phases[p]['sum_ms'] for p in ('init_ms', 'ingest_ms', 'retrieval_ms', 'response_ms'))
    phases['unattributed_ms'] = {'sum_ms': total - counted,
                                'share_of_total': (total - counted) / total if total else None,
                                'warning': 'includes shutdown, extraction and uninstrumented overhead; NOT pure MCP overhead'}
    report.update(response_judge=VERSION + ' NONOFFICIAL', latency=phases,
                  mrr_questions={'loss': sum(e['delta_mrr'] < 0 for e in examples),
                                 'gain': sum(e['delta_mrr'] > 0 for e in examples),
                                 'tie': sum(e['delta_mrr'] == 0 for e in examples)},
                  deltas_by_category={c: {'n': len(v), 'mrr': sum(x for x, _ in v) / len(v),
                                         'recall': sum(x for _, x in v) / len(v)}
                                      for c, v in categories.items()},
                  causal_limit='Historical predictions lack chunk/signal traces; rank loss is observed, attribution needs development ablations',
                  qualification='Historically observed oracle; NOT unseen test or leaderboard')
    return report, rescored, sorted(examples, key=lambda x: (x['delta_mrr'], x['qid']))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--campaign', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--source-commit', required=True)
    args = p.parse_args(argv)
    clone = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(clone / '.local-qualification'):
        p.error('New output under clone/.local-qualification required')
    limits = Limits()
    started = time.monotonic()
    last_check = 0.0
    def check():
        nonlocal last_check
        if time.monotonic() - started > 300:
            raise ResourceStop('Diagnosis exceeds 300-second budget')
        if time.monotonic() - last_check >= .5:
            sample(clone, limits)
            last_check = time.monotonic()
    try:
        ensure_priority(limits)
        check()
        verify_source(clone, args.source_commit, code_record())
        frozen_path = args.campaign / 'frozen.json'
        prediction_path = args.campaign / 'predictions.jsonl'
        frozen = json.loads(frozen_path.read_text())
        proof = json.loads((args.campaign / 'proofs.json').read_text())
        for path in (frozen_path, prediction_path):
            if sha256(path, check=check) != proof[path.name]:
                raise ValueError('Historical campaign input proof mismatch')
        data = Path(frozen['data'])
        if sha256(data, check=check) != frozen['dataset']['sha256']:
            raise ValueError('Dataset mismatch')
        allowed = set(frozen['selected_ids'])
        golds = [g for _, g in cases(data, check=check) if g.id in allowed]
        rows = [json.loads(line) for line in prediction_path.read_text().splitlines()]
        expected = {(s, g.id) for s in frozen['systems'] for g in golds}
        keys = [(r['system'], r['qid']) for r in rows]
        if len(keys) != len(set(keys)) or set(keys) != expected:
            raise ValueError('Missing/duplicate historical predictions; denominators unsafe')
        report, rescored, examples = diagnostic(rows, golds, check=check)
        check()
        if (sha256(frozen_path) != proof[frozen_path.name] or
                sha256(prediction_path) != proof[prediction_path.name] or
                sha256(data, check=check) != frozen['dataset']['sha256']):
            raise ValueError('Historical inputs changed during re-score')
        output.mkdir(mode=0o700)
        write_json(output / 'report.json', report)
        write_json(output / 'per-question.json', examples)
        with (output / 'rescored.jsonl').open('x') as f:
            for row in rescored:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        write_json(output / 'receipt.json', {'dataset_sha256': frozen['dataset']['sha256'],
                   'input_predictions_sha256': proof['predictions.jsonl'],
                   'scorer': VERSION, 'scorer_source_commit': args.source_commit, 'bootstrap': 2000, 'output_files': {
                       name: sha256(output / name) for name in ('report.json', 'per-question.json', 'rescored.jsonl')}})
        print(str(output / 'report.json'))
        return 0
    except (ResourceStop, ValueError, OSError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
