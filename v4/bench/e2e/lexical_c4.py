"""One prospective lexical-guard treatment; gated, frozen S validation only."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from .amendment import (S_PATH, S_SHA, freeze, load_plan, record_outcome,
                        valid_complete, validate_arm_config, verify_outcome)
from .configuration import resolved_config
from .data import cases, inventory, sha256
from .experiments import private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .lexical_dev import arms as old_arms, parity, require_diagnosis
from .network import deny_ip_network
from .run import code_record, main as run_campaign, model_record, verify_source, write_json

ARM = {'id': 'C4', 'model': 'minilm', 'engine_overrides': {
    'bm25_profile': 'scoped_literal', 'lexical_guard_k': 3}}
DEV_VERSION = 'lexical-c4-dev-v1'
S_VERSION = 'lexical-c4-s-v1'
CRITERION = 'complete error-free denominator; paired IC95 lower delta recall >0 AND mean delta MRR >=0; missing IC never passes'
INVARIANTS = ('bench/e2e/pipeline.py', 'bench/e2e/baselines.py', 'bench/e2e/data.py',
              'bench/e2e/leaderboard.py', 'bench/e2e/stats.py', 'bench/e2e/answers.py')
SHARED = {'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024,
          'seed': 20261009, 'bootstrap': 2000, 'ingest_bytes': 16_000_000}
DEV_FIXED = {'case_seconds': 300, 'run_seconds': 3600, 'campaign_seconds': 4500,
             'trace_k': 100, 'warm_searches': 3}
S_FIXED = {'case_seconds': 7200, 'run_seconds': 172800, 'campaign_seconds': 173700,
           'trace_k': 0, 'warm_searches': 1}


def eligible(report):
    """Prospective C4 gate; do not reinterpret previous C2/C3 eligibility."""
    recall = report['paired_delta_memory']['bm25']
    mrr = report['paired_other_delta_memory']['bm25']['mrr']
    interval, mrr_interval = recall.get('ci95'), mrr.get('ci95')
    values = [recall.get('mean'), mrr.get('mean'), *(interval or []), *(mrr_interval or [])]
    return bool(report.get('complete') is True and report.get('all_cases_executed_without_failure') is True
                and interval and len(interval) == 2 and mrr_interval and len(mrr_interval) == 2
                and all(type(v) in (int, float) and math.isfinite(v) for v in values)
                and interval[0] > 0 and mrr['mean'] >= 0)


def historical(previous, check):
    """Attest old results without rerunning arms or reading S."""
    clone, previous = private_path(previous)
    old = json.loads((previous / 'plan.json').read_text())
    receipt = json.loads((previous / 'receipt.json').read_text())
    if (old['version'] != 'lexical-dev-v1' or old['arms'] != old_arms()
            or receipt['plan_sha256'] != sha256(previous / 'plan.json')
            or len(old['selected_ids']) != 100 or len(set(old['selected_ids'])) != 100
            or any(old[k] != v for k, v in {**SHARED, 'trace_k': 100, 'warm_searches': 3}.items())
            or sha256(previous / 'selection.json') != old['selection_sha256']):
        raise ValueError('Complete unchanged lexical-dev-v1 required')
    _, resident = private_path(Path(old['previous']))
    if sha256(resident / 'plan.json') != old['prior_plan_sha256']:
        raise ValueError('Original development membership changed')
    prior = json.loads((resident / 'plan.json').read_text())
    if old['selected_ids'] != prior['selected_ids'][:100]:
        raise ValueError('Previously fixed 100-case order required')
    require_diagnosis(old, previous, check)
    proofs = {}
    for arm in old['arms']:
        _, cfg, proof = valid_complete(previous / arm['id'], check, old['selected_ids'])
        if (cfg['source_commit'] != old['source_commit'] or cfg['code_files'] != old['code_files']
                or cfg['engine_overrides'] != arm['engine_overrides']
                or cfg['dataset']['sha256'] != old['dataset_sha256']
                or cfg['model_record'] != old['models']['minilm']):
            raise ValueError('Historical arm does not match original plan')
        proofs[arm['id']] = proof
    verify_source(clone, old['source_commit'], old['code_files'])
    rows = [json.loads(s) for s in (previous / 'C1/predictions.jsonl').read_text().splitlines()]
    if not parity(rows, old['selected_ids']):
        raise ValueError('Historical C1 exact parity unproven')
    current = code_record()
    if any(old['code_files'][p] != current[p] for p in INVARIANTS):
        raise ValueError('Capture/reference/scorer changed')
    return old, {'plan_sha256': sha256(previous / 'plan.json'), 'arms': proofs,
                 'diagnosis_proofs_sha256': sha256(previous / 'diagnosis/proofs.json')}, prior


def prepare_dev(previous, output, revision, check):
    clone, output = private_path(output)
    if output.exists():
        raise ValueError('New prospective C4 directory required')
    verify_source(clone, revision, code_record())
    old, proof, resident = historical(previous, check)
    if sha256(Path(old['data']), check=check) != old['dataset_sha256']:
        raise ValueError('Oracle data changed')
    if model_record(old['models']['minilm']['id'], check=check) != old['models']['minilm']:
        raise ValueError('Cached MiniLM changed')
    output.mkdir(mode=0o700)
    write_json(output / 'selection.json', json.loads((previous / 'selection.json').read_text()))
    plan = {k: old[k] for k in ('data', 'dataset_sha256', 'selected_ids', 'models',
                              'capture_namespace', 'top_k', 'context_bytes', 'chunk_chars',
                              'trace_k', 'warm_searches', 'seed', 'bootstrap', 'ingest_bytes')}
    plan.update(version=DEV_VERSION, source_commit=revision, code_files=code_record(), arms=[ARM],
                historical_directory=str(previous.resolve()), historical_proof=proof,
                selection_sha256=sha256(output / 'selection.json'), case_seconds=300,
                run_seconds=3600, campaign_seconds=4500, criterion=CRITERION,
                oracle_question_ids=resident['oracle_question_ids'], independent_questions=False,
                claim='observed development only; one fixed 3-item BM25 prefix, no retuning',
                confirmation={'version': S_VERSION, 'sha256': S_SHA, 'questions': 500,
                              'case_seconds': 7200, 'run_seconds': 172800, 'campaign_seconds': 173700,
                              'warm_searches': 1, 'trace_k': 0, 'criterion': CRITERION,
                              'claim': 'S overlaps observed oracle; NOT independent; NOT leaderboard'})
    freeze(output, plan)
    return plan


def inputs(directory, revision, check):
    plan = load_plan(directory, revision)
    if plan['version'] not in {DEV_VERSION, S_VERSION} or plan['arms'] != [ARM]:
        raise ValueError('One fixed C4 treatment required')
    if plan['code_files'] != code_record() or plan['criterion'] != CRITERION:
        raise ValueError('Frozen protocol/source changed')
    fixed = DEV_FIXED if plan['version'] == DEV_VERSION else S_FIXED
    denominator = 100 if plan['version'] == DEV_VERSION else 500
    if (any(plan[k] != v for k, v in {**SHARED, **fixed}.items())
            or len(plan['selected_ids']) != denominator
            or len(set(plan['selected_ids'])) != denominator):
        raise ValueError('Preregistered budgets/denominator changed')
    if sha256(Path(plan['data']), check=check) != plan['dataset_sha256']:
        raise ValueError('Frozen dataset changed')
    if model_record(plan['models']['minilm']['id'], check=check) != plan['models']['minilm']:
        raise ValueError('Frozen cached model changed')
    if plan['version'] == DEV_VERSION:
        old, proof, _ = historical(Path(plan['historical_directory']), check)
        if (proof != plan['historical_proof'] or old['selected_ids'] != plan['selected_ids']
                or any(plan[k] != old[k] for k in ('data', 'dataset_sha256', 'models', 'capture_namespace'))
                or sha256(directory / 'selection.json') != plan['selection_sha256']):
            raise ValueError('Development evidence/membership changed')
    return plan


def validate_config(plan, cfg):
    validate_arm_config(plan, ARM, cfg)
    expected = {'code_files': plan['code_files'], 'transport': 'stdio',
                'limit': len(plan['selected_ids']), 'run_seconds': plan['run_seconds'],
                'case_seconds': plan['case_seconds'], 'ingest_bytes': plan['ingest_bytes'],
                'bootstrap_replicates': plan['bootstrap'], 'confidence': .95,
                'selection_sha256': plan.get('selection_sha256'),
                'selection_filter': plan['version'] == DEV_VERSION}
    if any(cfg.get(k) != v for k, v in expected.items()):
        raise ValueError('Actual configuration differs from frozen C4 protocol')
    if cfg['resolved_engine_config'] != resolved_config(plan['models']['minilm']['id'],
            '<private fresh store per question>', ARM['engine_overrides']):
        raise ValueError('Actual C4 engine differs')


def verified_outcome(plan, directory, check):
    outcome = verify_outcome(plan, directory, ARM, check)
    if outcome['status'] == 'complete':
        _, cfg, _ = valid_complete(directory / 'C4', check, plan['selected_ids'])
        validate_config(plan, cfg)
    return outcome


def execute(plan, directory, revision, check):
    inputs(directory, revision, check)
    if (directory / 'C4-outcome.json').exists():
        outcome = verified_outcome(plan, directory, check)
    else:
        target = directory / 'C4'
        if target.exists():
            raise ValueError('Unreceipted partial arm preserved; no silent restart')
        args = ['--data', plan['data'], '--variant', 'oracle' if plan['version'] == DEV_VERSION else 's',
                '--source-commit', revision, '--output', str(target),
                '--model', plan['models']['minilm']['id'], '--systems', 'memory,bm25',
                '--resident', '--limit', str(len(plan['selected_ids'])), '--run-seconds', str(plan['run_seconds']),
                '--case-seconds', str(plan['case_seconds']), '--ingest-bytes', str(plan['ingest_bytes']),
                '--engine-overrides', json.dumps(ARM['engine_overrides'])]
        for flag in ('top_k', 'context_bytes', 'chunk_chars', 'trace_k', 'warm_searches', 'seed'):
            args += ['--' + flag.replace('_', '-'), str(plan[flag])]
        if plan['version'] == DEV_VERSION:
            args += ['--selection', str(directory / 'selection.json')]
        status = run_campaign(args)
        check()
        if not status:
            _, cfg, _ = valid_complete(target, check, plan['selected_ids'])
            validate_config(plan, cfg)
        outcome = record_outcome(plan, directory, ARM, target, status, check)
    inputs(directory, revision, check)
    passed = outcome['status'] == 'complete' and eligible(outcome['report'])
    verdict = {'status': outcome['status'], 'numerical_criterion_met': passed,
               'winner': ARM if passed else None, 'selected_count': len(plan['selected_ids']),
               'independent_confirmation_passed': False, 'leaderboard_rank': 'not evaluated',
               'oracle_question_overlap': len(plan.get('oracle_question_overlap', [])),
               'claim': plan['claim'], 'plan_sha256': sha256(directory / 'plan.json')}
    write_json(directory / 'verdict.json', verdict)
    return verdict


def winner(directory, revision, check):
    dev = inputs(directory, revision, check)
    if dev['version'] != DEV_VERSION:
        raise ValueError('Frozen C4 development required')
    outcome = verified_outcome(dev, directory, check)
    if outcome['status'] != 'complete' or not eligible(outcome['report']):
        raise ValueError('C4 development gate failed; NO S access allowed')
    return dev, {'arm': ARM, 'proof': outcome['proof'], 'development_plan_sha256': sha256(directory / 'plan.json'),
                 'source_commit': revision, 'code_files': dev['code_files'],
                 'resolved_engine_config': resolved_config(dev['models']['minilm']['id'],
                     '<private fresh store per question>', ARM['engine_overrides'])}


def prepare_s(directory, output, revision, check):
    dev, chosen = winner(directory, revision, check)  # MUST precede even S stat/hash
    _, output = private_path(output)
    lock = directory / 'confirmation-lock.json'
    if output.exists() or lock.exists():
        raise ValueError('One C4 S attempt already reserved; no second candidate')
    output.mkdir(mode=0o700)
    exclusive_json(output / 'winner.json', chosen)
    exclusive_json(lock, {'output': str(output), 'winner_sha256': sha256(output / 'winner.json'), 'arm': ARM})
    if sha256(S_PATH, check=check) != S_SHA:
        raise ValueError('LongMemEval-S SHA mismatch; reservation preserved')
    data = inventory(S_PATH, 's', check=check)
    golds = [g for _, g in cases(S_PATH, check=check)]
    if len(golds) != 500 or len({g.id for g in golds}) != 500:
        raise ValueError('All 500 distinct S questions required')
    from .stats import cluster_ids
    overlap = sorted(set(dev['oracle_question_ids']) & {g.id for g in golds})
    plan = {k: dev[k] for k in ('source_commit', 'code_files', 'arms', 'models', 'capture_namespace',
                               'top_k', 'context_bytes', 'chunk_chars', 'seed', 'bootstrap', 'ingest_bytes')}
    plan.update(dev['confirmation'])
    plan.pop('sha256')
    plan.pop('questions')
    plan.update(data=str(S_PATH), dataset=data, dataset_sha256=S_SHA,
                selected_ids=[g.id for g in golds], development_directory=str(directory), winner=chosen,
                winner_sha256=sha256(output / 'winner.json'), oracle_question_overlap=overlap,
                independent_questions=False, source_clusters=len(set(cluster_ids(golds).values())),
                no_retuning_no_second_candidate=True)
    freeze(output, plan)
    return plan


def exclusive_json(path, value):
    """A second process cannot reserve another attempt; persist before S read."""
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def run_s(directory, revision, check):
    # Revalidate winner BEFORE touching S again, not after an S hash/read.
    plan = load_plan(directory, revision)
    if plan['version'] != S_VERSION:
        raise ValueError('Frozen C4 S plan required')
    devdir = Path(plan['development_directory'])
    dev, chosen = winner(devdir, revision, check)
    lock = json.loads((devdir / 'confirmation-lock.json').read_text())
    if (chosen != plan['winner'] or sha256(directory / 'winner.json') != plan['winner_sha256']
            or lock != {'output': str(directory), 'winner_sha256': plan['winner_sha256'], 'arm': ARM}
            or any(plan[k] != v for k, v in dev['confirmation'].items() if k not in {'sha256', 'questions'})
            or any(plan[k] != dev[k] for k in ('models', 'capture_namespace', *SHARED))
            or plan['dataset_sha256'] != S_SHA or len(plan['selected_ids']) != 500):
        raise ValueError('Frozen S winner/protocol changed')
    return execute(plan, directory, revision, check)


def main(argv=None):
    deny_ip_network()
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='operation', required=True)
    for op in ('prepare-dev', 'run-dev', 'prepare-s', 'run-s'):
        sub = subs.add_parser(op)
        sub.add_argument('--source-commit', required=True)
        if op == 'prepare-dev':
            sub.add_argument('--previous', required=True, type=Path)
        else:
            sub.add_argument('--plan-dir', required=True, type=Path)
        if op.startswith('prepare'):
            sub.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        parser.error('Exact 40-character source commit required')
    clone = Path(__file__).resolve().parents[3]
    started, last = time.monotonic(), 0.0
    budget = 900 if args.operation.startswith('prepare') else 4500
    def check():
        nonlocal last
        if time.monotonic() - started > budget:
            raise ResourceStop('C4 campaign wall-time budget exceeded')
        if time.monotonic() - last >= .5:
            sample(clone, Limits())
            last = time.monotonic()
    try:
        ensure_priority(Limits())
        check()
        if args.operation == 'prepare-dev':
            prepare_dev(args.previous.resolve(), args.output.resolve(), args.source_commit, check)
        else:
            _, directory = private_path(args.plan_dir)
            if args.operation == 'prepare-s':
                prepare_s(directory, args.output.resolve(), args.source_commit, check)
            elif args.operation == 'run-s':
                budget = 173700  # fixed prospectively, never operator-supplied
                print(json.dumps(run_s(directory, args.source_commit, check)))
            else:
                plan = inputs(directory, args.source_commit, check)
                if plan['version'] != DEV_VERSION:
                    raise ValueError('C4 development plan required')
                print(json.dumps(execute(plan, directory, args.source_commit, check)))
        return 0
    except (ResourceStop, ValueError, OSError, KeyError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
