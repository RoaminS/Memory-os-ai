"""Preregister resident development, proof-checked resume and ONE S candidate."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import re
from pathlib import Path
import sys
import time

from .configuration import resolved_config
from .data import cases, inventory, sha256
from .experiments import arms, private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .partitions import validate as validate_selection
from .run import code_record, main as run_campaign, model_record, verify_source, write_json

S_SHA = '08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894'
S_PATH = Path('/Users/romain/Memory-os-ai/v4/bench/data/longmemeval_s.json')


def freeze(directory, plan):
    write_json(directory / 'plan.json', plan)
    write_json(directory / 'receipt.json', {'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
                                           'plan_sha256': sha256(directory / 'plan.json')})


def load_plan(directory, revision):
    clone, directory = private_path(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if sha256(directory / 'plan.json') != receipt['plan_sha256'] or plan['source_commit'] != revision:
        raise ValueError('Frozen plan/source mismatch')
    verify_source(clone, revision, code_record())
    return plan


def artifact_proof(directory, check):
    proof = json.loads((directory / 'proofs.json').read_text())
    if not {'report.json', 'frozen.json', 'predictions.jsonl'} <= set(proof):
        raise ValueError('Incomplete campaign proof')
    for name, expected in proof.items():
        if Path(name).name != name or sha256(directory / name, check=check) != expected:
            raise ValueError('Campaign artifact hash mismatch')
    return {'proofs_sha256': sha256(directory / 'proofs.json'), 'files': proof}


def valid_complete(directory, check, expected_ids=None):
    proof = artifact_proof(directory, check)
    report = json.loads((directory / 'report.json').read_text())
    cfg = json.loads((directory / 'frozen.json').read_text())
    if not report['complete'] or not report['all_cases_executed_without_failure']:
        raise ValueError('Complete error-free arm required')
    if report['frozen_sha256'] != proof['files']['frozen.json']:
        raise ValueError('Report/frozen receipt mismatch')
    ids = cfg['selected_ids']
    if expected_ids is not None and ids != expected_ids:
        raise ValueError('Selected question ids changed')
    rows = [json.loads(s) for s in (directory / 'predictions.jsonl').read_text().splitlines()]
    keys = [(r['system'], r['qid']) for r in rows]
    expected = {(system, qid) for system in ('memory', 'bm25') for qid in ids}
    if (len(keys) != len(set(keys)) or set(keys) != expected or
            any(r['status'] != 'ok' for r in rows) or report['selected_count'] != len(ids)):
        raise ValueError('Unsafe denominators or failed predictions')
    return report, cfg, proof


def validate_c0(old, plan, check):
    clone, old = private_path(old)
    report, cfg, proof = valid_complete(old / 'C0', check)
    previous = json.loads((old / 'plan.json').read_text())
    receipt = json.loads((old / 'receipt.json').read_text())
    if (sha256(old / 'plan.json') != receipt['plan_sha256'] or previous['arms'] != arms() or
            sha256(old / 'selection.json') != previous['selection_sha256'] or
            receipt['selection_sha256'] != previous['selection_sha256']):
        raise ValueError('Original preregistration changed')
    verify_source(clone, cfg['source_commit'], cfg['code_files'])
    # Reuse scores only: production core, quotes, BM25, response/scorer unchanged.
    current = code_record()
    invariant = {k: v for k, v in cfg['code_files'].items()
                 if k.startswith('src/') or k in {'bench/e2e/pipeline.py', 'bench/e2e/baselines.py',
                                                  'bench/e2e/answers.py', 'bench/e2e/leaderboard.py', 'bench/e2e/stats.py',
                                                  'bench/e2e/data.py', 'bench/e2e/planning.py', 'bench/e2e/configuration.py'}}
    if any(current.get(k) != v for k, v in invariant.items()):
        raise ValueError('C0 scoring/retrieval invariants changed; reuse refused')
    if cfg['run_id'] != plan['capture_namespace'] or cfg['systems'] != ['memory', 'bm25']:
        raise ValueError('C0 namespace/systems mismatch')
    if (cfg['dataset']['sha256'] != plan['dataset_sha256'] or
            cfg['selected_ids'] != plan['selected_ids'] or cfg['model_record'] != plan['models']['minilm']):
        raise ValueError('C0 data/selection/model mismatch')
    for field in ('top_k', 'context_bytes', 'trace_k', 'warm_searches', 'seed', 'chunk_chars'):
        if cfg[field] != plan[field]:
            raise ValueError('C0 retrieval/context protocol changed')
    expected_config = resolved_config(plan['models']['minilm']['id'], '<private fresh store per question>')
    if cfg['resolved_engine_config'] != expected_config or cfg.get('engine_overrides') or cfg.get('unique_turns'):
        raise ValueError('C0 engine profile changed')
    return {'directory': str((old / 'C0').resolve()), 'proof': proof,
            'protocol': cfg['protocol'], 'scores_reusable': True,
            'latency_comparable_to_resident': False, 'report': report}


def prepare_dev(old, output, revision, check):
    clone, output = private_path(output)
    if output.exists():
        raise ValueError('NEW amendment directory required')
    verify_source(clone, revision, code_record())
    previous = json.loads((old / 'plan.json').read_text())
    data = Path(previous['data'])
    if sha256(data, check=check) != previous['dataset_sha256']:
        raise ValueError('Oracle changed')
    selection = json.loads((old / 'selection.json').read_text())
    golds = [g for _, g in cases(data, check=check)]
    allowed = validate_selection(selection, golds, previous['dataset_sha256'])
    selected = [g.id for g in golds if g.id in allowed]
    models = previous['models']
    for record in models.values():
        if model_record(record['id'], check=check) != record:
            raise ValueError('Cached model changed')
    from .run import capture_namespace
    plan = {'version': 'resident-dev-v2', 'source_commit': revision, 'data': str(data),
            'dataset_sha256': previous['dataset_sha256'], 'selected_ids': selected,
            'selection_sha256': sha256(old / 'selection.json'), 'models': models,
            'arms': arms()[1:], 'seed': 20261009, 'capture_namespace': capture_namespace(20261009),
            'top_k': 10, 'context_bytes': 12000, 'trace_k': 100, 'warm_searches': 3, 'chunk_chars': 1024,
            'granite_seconds': 7200, 'other_arm_seconds': 3600, 'case_seconds': 300,
            'campaign_seconds': 22500, 'ingest_bytes': 16_000_000,
            'residency': 'one resident model/MCP process per arm; fresh store per observable conversation',
            'budget_policy': 'wall-time only -> infaisable and continue; resource/integrity/correctness -> STOP ALL',
            'selection_rule': previous['selection_rule'], 'final_tie_break': 'lexicographic arm id', 'preregistered_arms_unchanged': True,
            'prior_plan_sha256': sha256(old / 'plan.json'), 'old_directory': str(old.resolve()),
            'oracle_question_ids': [g.id for g in golds],
            'justification': '33 Granite cases: 1287s ingestion/151s startup, 368.6 encoder tokens/s; 103 cases extrapolate 4020s ingestion; 7200s allows 1.79x plus IO/CI. CPU1/batch4 unchanged.'}
    plan['reused_C0'] = validate_c0(old, plan, check)
    output.mkdir(mode=0o700)
    write_json(output / 'selection.json', selection)
    freeze(output, plan)
    return plan


def check_inputs(plan, directory, check):
    if sha256(Path(plan['data']), check=check) != plan['dataset_sha256']:
        raise ValueError('Frozen dataset changed')
    for record in plan['models'].values():
        if model_record(record['id'], check=check) != record:
            raise ValueError('Frozen model changed')
    if plan['version'] == 'resident-dev-v2':
        if sha256(directory / 'selection.json') != plan['selection_sha256']:
            raise ValueError('Frozen development split changed')
        c0 = validate_c0(Path(plan['old_directory']), plan, check)
        if c0 != plan['reused_C0']:
            raise ValueError('Reused C0 changed')


def campaign_args(plan, directory, arm, output):
    args = ['--data', plan['data'], '--variant', 'oracle' if plan['version'] == 'resident-dev-v2' else 's',
            '--source-commit', plan['source_commit'], '--output', str(output),
            '--model', plan['models'][arm['model']]['id'], '--systems', 'memory,bm25', '--limit', '500',
            '--resident', '--top-k', str(plan['top_k']), '--context-bytes', str(plan['context_bytes']),
            '--trace-k', str(plan['trace_k']), '--warm-searches', str(plan['warm_searches']),
            '--engine-overrides', json.dumps(arm.get('engine_overrides', {})),
            '--chunk-chars', str(arm.get('chunk_chars', 1024)), '--query-mode', arm.get('query_mode', 'none'),
            '--ingest-bytes', str(plan['ingest_bytes']), '--case-seconds', str(plan['case_seconds'])]
    if plan['version'] == 'resident-dev-v2':
        args += ['--selection', str(directory / 'selection.json'), '--run-seconds',
                 str(plan['granite_seconds'] if arm['model'] == 'granite' else plan['other_arm_seconds'])]
    else:
        args += ['--run-seconds', str(plan['run_seconds'])]
    if arm.get('reranker'):
        args += ['--reranker', plan['models'][arm['reranker']]['id']]
    if arm.get('unique_turns'):
        args += ['--unique-turns']
    return args


def validate_arm_config(plan, arm, cfg):
    from .run import capture_namespace
    reranker = plan['models'][arm['reranker']]['id'] if arm.get('reranker') else None
    expected = {'source_commit': plan['source_commit'], 'run_id': capture_namespace(plan['seed']),
                'resident': True, 'protocol': 'e2e-resident-v2', 'systems': ['memory', 'bm25'],
                'model_record': plan['models'][arm['model']], 'top_k': plan['top_k'],
                'context_bytes': plan['context_bytes'], 'seed': plan['seed'],
                'warm_searches': plan['warm_searches'], 'trace_k': plan['trace_k'],
                'chunk_chars': arm.get('chunk_chars', 1024), 'unique_turns': arm.get('unique_turns', False),
                'query_mode': arm.get('query_mode', 'none'), 'engine_overrides': arm.get('engine_overrides', {}),
                'reranker': reranker, 'selected_ids': plan['selected_ids']}
    if any(cfg.get(k) != v for k, v in expected.items()) or cfg['dataset']['sha256'] != plan['dataset_sha256']:
        raise ValueError('Arm configuration differs from preregistration')
    resolved = resolved_config(plan['models'][arm['model']]['id'], '<private fresh store per question>',
                               arm.get('engine_overrides'), reranker)
    if cfg['resolved_engine_config'] != resolved:
        raise ValueError('Resolved engine differs from frozen arm')


def record_outcome(plan, directory, arm, output, status, check):
    if status == 0:
        report, cfg, proof = valid_complete(output, check, plan['selected_ids'])
        validate_arm_config(plan, arm, cfg)
        result = {'status': 'complete', 'proof': proof, 'report': report}
    else:
        path = output / 'report.json'
        report = json.loads(path.read_text()) if path.exists() else json.loads((output / 'aborted.json').read_text())
        if report.get('termination_class') != 'budget':
            raise ResourceStop('Correctness/integrity/resource failure; no following arm allowed')
        # Never hide a correctness row behind a later timeout.
        rows_path = output / 'predictions.jsonl'
        if rows_path.exists():
            rows = [json.loads(s) for s in rows_path.read_text().splitlines()]
            if any(r.get('failure_class') == 'correctness' for r in rows):
                raise ResourceStop('Correctness failure before budget exhaustion')
        result = {'status': 'infaisable', 'proof': artifact_proof(output, check) if path.exists() else {
            'aborted_sha256': sha256(output / 'aborted.json')}, 'reason': report.get('stopped', report.get('reason'))}
    outcome = {'arm': arm, 'source_commit': plan['source_commit'],
               'plan_sha256': sha256(directory / 'plan.json'), 'directory': str(output), **result}
    write_json(directory / (arm['id'] + '-outcome.json'), outcome)
    return outcome


def verify_outcome(plan, directory, arm, check):
    outcome = json.loads((directory / (arm['id'] + '-outcome.json')).read_text())
    if (outcome['arm'] != arm or outcome['source_commit'] != plan['source_commit'] or
            outcome['plan_sha256'] != sha256(directory / 'plan.json')):
        raise ValueError('Resume outcome bound to different plan/source')
    output = directory / arm['id']
    if outcome['directory'] != str(output):
        raise ValueError('Resume directory changed')
    if outcome['status'] == 'complete':
        report, cfg, proof = valid_complete(output, check, plan['selected_ids'])
        validate_arm_config(plan, arm, cfg)
        if proof != outcome['proof'] or report != outcome['report']:
            raise ValueError('Completed arm changed')
    elif outcome['status'] == 'infaisable':
        proof = artifact_proof(output, check) if (output / 'report.json').exists() else {
            'aborted_sha256': sha256(output / 'aborted.json')}
        if proof != outcome['proof']:
            raise ValueError('Infeasible arm changed')
    else:
        raise ValueError('Unknown resume status')
    return outcome


def execute_dev(directory, revision, check):
    plan = load_plan(directory, revision)
    if plan['version'] != 'resident-dev-v2' or plan['arms'] != arms()[1:]:
        raise ValueError('Fixed development amendment required')
    check_inputs(plan, directory, check)
    receipt = sha256(directory / 'plan.json')
    for arm in plan['arms']:
        check()
        if sha256(directory / 'plan.json') != receipt:
            raise ValueError('Plan changed during campaign')
        if (directory / (arm['id'] + '-outcome.json')).exists():
            verify_outcome(plan, directory, arm, check)
            continue
        output = directory / arm['id']
        if output.exists():
            raise ValueError('Unreceipted partial arm; preserve it, prepare a NEW amendment')
        status = run_campaign(campaign_args(plan, directory, arm, output))
        check()  # physical swap/disk guard still stops ALL arms
        record_outcome(plan, directory, arm, output, status, check)
    write_json(directory / 'development-terminal.json', {'complete': True, 'claim': 'development only'})


def select_winner(plan, directory, check):
    candidates = []
    for arm in plan['arms']:
        outcome = verify_outcome(plan, directory, arm, check)
        if outcome['status'] != 'complete':
            continue
        report = outcome['report']
        recall = report['paired_delta_memory']['bm25']['mean']
        mrr = report['paired_other_delta_memory']['bm25']['mrr']['mean']
        latency = report['systems']['memory']['end_to_end_ms']['p50']
        if any(v is None or not math.isfinite(v) for v in (recall, mrr, latency)):
            raise ValueError('Winner metrics unavailable')
        candidates.append(((-recall, -mrr, latency, arm['id']), arm, outcome['proof']))
    if not candidates:
        raise ValueError('No complete feasible candidate; no S run allowed')
    _, arm, proof = min(candidates, key=lambda x: x[0])
    return {'arm': arm, 'development_proof': proof, 'rule': plan['selection_rule'],
            'selection_is_not_evidence_of_superiority': True}


def prepare_confirmation(directory, output, revision, check):
    dev = load_plan(directory, revision)
    if dev['version'] != 'resident-dev-v2':
        raise ValueError('Resident development amendment required')
    check_inputs(dev, directory, check)
    winner = select_winner(dev, directory, check)  # NO S access before winner selection
    _, output = private_path(output)
    if output.exists() or (directory / 'confirmation-lock.json').exists():
        raise ValueError('One frozen S candidate only; confirmation already reserved')
    output.mkdir(mode=0o700)
    write_json(output / 'winner.json', winner)  # durable winner BEFORE S metadata/labels are read
    write_json(directory / 'confirmation-lock.json', {'output': str(output),
               'winner_sha256': sha256(output / 'winner.json'), 'arm': winner['arm']})
    if sha256(S_PATH, check=check) != S_SHA:
        raise ValueError('LongMemEval-S official input SHA mismatch')
    data = inventory(S_PATH, 's', check=check)
    golds = [g for _, g in cases(S_PATH, check=check)]
    if len(golds) != 500:
        raise ValueError('Confirmation must include all 500 S questions')
    overlap = sorted(set(dev['oracle_question_ids']) & {g.id for g in golds})
    from .stats import cluster_ids
    clusters = len(set(cluster_ids(golds).values()))
    model = winner['arm']['model']
    budget = 604800 if model == 'granite' else 172800
    plan = {'version': 'resident-confirmation-s-v1', 'source_commit': revision,
            'data': str(S_PATH), 'dataset_sha256': S_SHA, 'dataset': data,
            'selected_ids': [g.id for g in golds], 'models': dev['models'],
            'winner': winner, 'winner_sha256': sha256(output / 'winner.json'),
            'development_directory': str(directory), 'development_plan_sha256': sha256(directory / 'plan.json'),
            'top_k': 10, 'context_bytes': 12000, 'trace_k': 100 if winner['arm'].get('unique_turns') else 0,
            'warm_searches': 1, 'case_seconds': 7200, 'run_seconds': budget,
            'campaign_seconds': budget + 900, 'ingest_bytes': 16_000_000, 'seed': 20261009,
            'bootstrap': 2000, 'confidence': .95, 'cluster_level': 'all shared observable haystack sessions',
            'observed_source_clusters': clusters, 'oracle_question_overlap': overlap,
            'independent_questions': not overlap, 'no_retuning_no_second_candidate': True,
            'criterion': 'complete 500/500; paired clustered IC95 lower delta recall >0 AND lower delta MRR >=0; unavailable IC never passes',
            'claim_limit': 'Overlap with observed oracle -> cross-variant evaluation, NOT independent confirmation; shared-source single cluster -> CI unavailable',
            'budget_justification': '115k raw tokens/q; two indexed representations ~230k tokens/q; observed Granite 368.6 tokens/s -> 312000s/500; 604800s ~1.94x. Other models 48h cap; per conversation 2h; no input truncation.'}
    freeze(output, plan)
    return plan


def execute_confirmation(directory, revision, check):
    plan = load_plan(directory, revision)
    if plan['version'] != 'resident-confirmation-s-v1':
        raise ValueError('Frozen S plan required')
    if sha256(directory / 'winner.json') != plan['winner_sha256']:
        raise ValueError('Frozen winner changed')
    devdir = Path(plan['development_directory'])
    dev = load_plan(devdir, revision)
    if sha256(devdir / 'plan.json') != plan['development_plan_sha256'] or select_winner(dev, devdir, check) != plan['winner']:
        raise ValueError('Development proof/winner changed')
    lock = json.loads((devdir / 'confirmation-lock.json').read_text())
    if lock != {'output': str(directory), 'winner_sha256': plan['winner_sha256'], 'arm': plan['winner']['arm']}:
        raise ValueError('Single-candidate confirmation lock mismatch')
    check_inputs(plan, directory, check)
    arm = plan['winner']['arm']
    output = directory / arm['id']
    if (directory / (arm['id'] + '-outcome.json')).exists():
        outcome = verify_outcome(plan, directory, arm, check)
    else:
        if output.exists():
            raise ValueError('Partial confirmation preserved; no silent restart')
        status = run_campaign(campaign_args(plan, directory, arm, output))
        check()
        outcome = record_outcome(plan, directory, arm, output, status, check)
    verdict = confirmation_verdict(plan, outcome)
    write_json(directory / 'confirmation-verdict.json', verdict)
    return verdict


def confirmation_verdict(plan, outcome):
    numerical = False
    if outcome['status'] == 'complete':
        report = outcome['report']
        recall = report['paired_delta_memory']['bm25']['ci95']
        mrr = report['paired_other_delta_memory']['bm25']['mrr']['ci95']
        numerical = bool(recall and mrr and recall[0] > 0 and mrr[0] >= 0)
    verdict = {'status': outcome['status'], 'all_500_executed': outcome['status'] == 'complete',
               'numerical_criterion_met': numerical,
               'independent_confirmation_passed': numerical and plan['independent_questions'],
               'overlap_questions': len(plan['oracle_question_overlap']),
               'clusters': plan['observed_source_clusters'], 'leaderboard_rank': 'not evaluated'}
    return verdict


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation', required=True)
    for op in ('prepare-dev', 'run-dev', 'prepare-s', 'run-s'):
        a = sub.add_parser(op)
        a.add_argument('--source-commit', required=True)
        if op == 'prepare-dev':
            a.add_argument('--previous', type=Path, required=True)
        else:
            a.add_argument('--plan-dir', type=Path, required=True)
        if op.startswith('prepare'):
            a.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        p.error("Exact source commit (40 hex) required")
    clone = Path(__file__).resolve().parents[3]
    limits = Limits()
    started, last = time.monotonic(), 0.0
    budget = 900 if args.operation.startswith('prepare') else 22500
    def check():
        nonlocal last
        if time.monotonic() - started > budget:
            raise ResourceStop('Amendment campaign wall-time budget exceeded')
        if time.monotonic() - last >= .5:
            sample(clone, limits)
            last = time.monotonic()
    try:
        ensure_priority(limits)
        check()
        if args.operation == 'prepare-dev':
            plan = prepare_dev(args.previous.resolve(), args.output.resolve(), args.source_commit, check)
            print(json.dumps({'plan': str(args.output / 'plan.json'), 'dev_questions': len(plan['selected_ids']),
                              'C0_reused': True, 'C0_latency_comparable': False}))
        elif args.operation == 'run-dev':
            execute_dev(args.plan_dir.resolve(), args.source_commit, check)
        elif args.operation == 'prepare-s':
            plan = prepare_confirmation(args.plan_dir.resolve(), args.output.resolve(), args.source_commit, check)
            print(json.dumps({'plan': str(args.output / 'plan.json'), 'winner': plan['winner']['arm']['id'],
                              'questions': len(plan['selected_ids']), 'overlap': len(plan['oracle_question_overlap']),
                              'clusters': plan['observed_source_clusters'], 'independent': plan['independent_questions']}))
        else:
            plan = load_plan(args.plan_dir.resolve(), args.source_commit)
            budget = plan['campaign_seconds']
            print(json.dumps(execute_confirmation(args.plan_dir.resolve(), args.source_commit, check)))
        return 0
    except (ResourceStop, ValueError, OSError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
