"""Freeze five development treatments BEFORE running them; no test selection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from .data import cases, sha256
from .guard import Limits, ResourceStop, ensure_priority, sample
from .partitions import split
from .run import code_record, main as run_campaign, model_record, verify_source, write_json

MODEL_ROOT = Path('/Users/romain/.cache/huggingface/hub')
MODELS = {
    'minilm': MODEL_ROOT / 'models--sentence-transformers--all-MiniLM-L6-v2/snapshots/1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
    'granite': MODEL_ROOT / 'models--ibm-granite--granite-embedding-97m-multilingual-r2/snapshots/835ad14087e140460703cf0fae09f97d469d65c2',
    'reranker': MODEL_ROOT / 'models--tomaarsen--ms-marco-ettin-17m-reranker/snapshots/5e03204c30c684bc4d8a61c49665d3ed4b65a1dc',
}


def arms():
    return [
        {'id': 'C0', 'model': 'minilm'},
        {'id': 'E1-granite', 'model': 'granite'},
        {'id': 'E2-sparse', 'model': 'minilm', 'engine_overrides': {
            'w_bm25': 3.0, 'w_dense': .25, 'w_entity': 0.0, 'use_entities': False,
            'dense_norm': 'fixed', 'full_candidate_scoring': True}},
        {'id': 'E3-lexical-rerank', 'model': 'minilm', 'reranker': 'reranker',
         'engine_overrides': {'use_dense': False, 'use_entities': False,
                              'use_stemming': False, 'w_bm25': 1.0, 'w_dense': 0.0,
                              'w_entity': 0.0, 'candidates': 30, 'rerank_top_n': 30,
                              'rerank_weight': 1.0}},
        {'id': 'E4-multiquery', 'model': 'minilm', 'query_mode': 'lexical'},
        {'id': 'E5-chunk-diversity', 'model': 'minilm', 'chunk_chars': 512, 'unique_turns': True},
    ]


def private_path(path):
    clone = Path(__file__).resolve().parents[3]
    path = path.resolve()
    if not path.is_relative_to(clone / '.local-qualification'):
        raise ValueError('Private output under clone/.local-qualification required')
    return clone, path


def prepare(data, output, revision, check):
    clone, output = private_path(output)
    if output.exists():
        raise ValueError('New preregistration directory required')
    verify_source(clone, revision, code_record())
    dataset_sha = sha256(data, check=check)
    golds = [g for _, g in cases(data, check=check)]
    selection = split(golds, dataset_sha)
    records = {name: model_record(str(path), check=check) for name, path in MODELS.items()}
    output.mkdir(mode=0o700)
    write_json(output / 'selection.json', selection)
    plan = {'version': 'dev-fusion-v1', 'source_commit': revision,
            'data': str(data.resolve()), 'dataset_sha256': dataset_sha,
            'selection_sha256': sha256(output / 'selection.json'), 'models': records,
            'arms': arms(), 'systems': ['memory', 'bm25'], 'top_k': 10, 'context_bytes': 12000,
            'trace_k': 100, 'warm_searches': 3, 'seed': 20261009, 'bootstrap': 2000,
            'run_seconds_per_arm': 1500, 'campaign_seconds': 9600,
            'primary': 'paired delta Recall@10 memory minus SAME-arm BM25',
            'secondary': 'paired delta MRR; answer proxies descriptive only',
            'selection_rule': 'Among E1-E5: highest paired development recall delta vs SAME-arm BM25, then paired MRR delta, then lowest p50 latency; freeze ONE winner before independent test',
            'confirmation': 'NEW independent data required; one frozen winner versus BM25, paired 95% clustered CI lower delta recall >0, lower delta MRR >=0; report n, failures and latency',
            'multiplicity': 'Five development comparisons are exploratory, NEVER significance or leaderboard claims',
            'no_retuning': True, 'all_oracle_previously_observed': True, 'test_ids': []}
    write_json(output / 'plan.json', plan)
    write_json(output / 'receipt.json', {'frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'plan_sha256': sha256(output / 'plan.json'),
                                       'selection_sha256': plan['selection_sha256']})
    return plan


def execute(directory, check, revision=None):
    clone, directory = private_path(directory)
    plan_path = directory / 'plan.json'
    plan = json.loads(plan_path.read_text())
    if revision is not None and plan['source_commit'] != revision:
        raise ValueError('Declared source differs from preregistration')
    receipt = json.loads((directory / 'receipt.json').read_text())
    if sha256(plan_path) != receipt['plan_sha256'] or plan['arms'] != arms():
        raise ValueError('Preregistration changed')
    selection = directory / 'selection.json'
    if sha256(selection) != receipt['selection_sha256'] or plan['selection_sha256'] != receipt['selection_sha256']:
        raise ValueError('Development selection changed')
    if sha256(Path(plan['data']), check=check) != plan['dataset_sha256']:
        raise ValueError('Dataset changed')
    for name, record in plan['models'].items():
        if model_record(str(MODELS[name]), check=check) != record:
            raise ValueError('Cached model changed')
    verify_source(clone, plan['source_commit'], code_record())
    # No automatic resume/overwrite: preserve failed arms and their denominators.
    for arm in plan['arms']:
        check()
        if sha256(plan_path) != receipt['plan_sha256'] or sha256(selection) != receipt['selection_sha256']:
            raise ResourceStop('Preregistration changed during campaign')
        output = directory / arm['id']
        if output.exists():
            raise ValueError('Arm already attempted; create a new preregistration, never overwrite')
        command = ['--data', plan['data'],
                   '--variant', 'oracle', '--source-commit', plan['source_commit'],
                   '--output', str(output), '--model', plan['models'][arm['model']]['id'],
                   '--systems', ','.join(plan['systems']), '--selection', str(selection),
                   '--trace-k', str(plan['trace_k']), '--warm-searches', str(plan['warm_searches']), '--limit', '500',
                   '--run-seconds', str(plan['run_seconds_per_arm']),
                   '--engine-overrides', json.dumps(arm.get('engine_overrides', {})),
                   '--chunk-chars', str(arm.get('chunk_chars', 1024)),
                   '--query-mode', arm.get('query_mode', 'none')]
        if arm.get('reranker'):
            command.extend(['--reranker', plan['models'][arm['reranker']]['id']])
        if arm.get('unique_turns'):
            command.append('--unique-turns')
        # Run the existing supervisor directly: nested process supervisors could
        # interrupt cleanup of owned SDK server children on resource exhaustion.
        status = run_campaign(command)
        check()
        if sha256(plan_path) != receipt['plan_sha256'] or sha256(selection) != receipt['selection_sha256']:
            raise ResourceStop('Preregistration changed during arm')
        if status:
            raise ResourceStop(f"Arm {arm['id']} failed; no later arm started")
    write_json(directory / 'campaign-complete.json', {'complete': True, 'claim': 'development ONLY'})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation', required=True)
    a = sub.add_parser('prepare')
    a.add_argument('--data', type=Path, required=True)
    a.add_argument('--output', type=Path, required=True)
    a.add_argument('--source-commit', required=True)
    a = sub.add_parser('run')
    a.add_argument('--plan-dir', type=Path, required=True)
    a.add_argument('--source-commit', required=True)
    args = p.parse_args(argv)
    limits = Limits()
    clone = Path(__file__).resolve().parents[3]
    started = time.monotonic()
    last_check = 0.0
    def check():
        nonlocal last_check
        if time.monotonic() - started > 9600:
            raise ResourceStop('Campaign wall-time exceeded')
        if time.monotonic() - last_check >= .5:
            sample(clone, limits)
            last_check = time.monotonic()
    try:
        ensure_priority(limits)
        check()
        if args.operation == 'prepare':
            plan = prepare(args.data, args.output, args.source_commit, check)
            print(json.dumps({'plan': str(args.output / 'plan.json'), 'arms': len(plan['arms'])}))
        else:
            execute(args.plan_dir, check, args.source_commit)
        return 0
    except (ResourceStop, ValueError, OSError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
