"""Freeze, diagnose and run corrected lexical development (100 old dev cases)."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
import tempfile
import time

from .amendment import freeze, load_plan, valid_complete
from .data import cases, sha256
from .experiments import private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .lexical_diagnose import capture_case, saved_audit
from .network import deny_ip_network
from .run import code_record, main as run_campaign, model_record, verify_source, write_json


def arms():
    return [{'id': 'C1', 'engine_overrides': {'bm25_profile': 'scoped_literal',
             'use_dense': False, 'use_entities': False}},
            {'id': 'C2', 'engine_overrides': {'bm25_profile': 'scoped_literal'}},
            {'id': 'C3', 'engine_overrides': {'bm25_profile': 'scoped_literal', 'lexical_first': True}}]


def prepare(previous, output, revision, check):
    clone, output = private_path(output)
    if output.exists():
        raise ValueError('New preregistration directory required')
    verify_source(clone, revision, code_record())
    prior = json.loads((previous / 'plan.json').read_text())
    receipt = json.loads((previous / 'receipt.json').read_text())
    if prior['version'] != 'resident-dev-v2' or sha256(previous / 'plan.json') != receipt['plan_sha256']:
        raise ValueError('Frozen resident-dev-v2 required')
    if len(prior['selected_ids']) != 103 or len(set(prior['selected_ids'])) != 103:
        raise ValueError('Expected exactly the 103 previously observed development ids')
    selection_path = previous / 'selection.json'
    selection = json.loads(selection_path.read_text())
    if sha256(selection_path) != prior['selection_sha256']:
        raise ValueError('Prior development selection changed')
    # No S path/access. No new split, no selection by gold or past results.
    if set(prior['selected_ids']) != set(selection['dev_ids']):
        raise ValueError('Prior ids do not match development membership')
    data = Path(prior['data'])
    if sha256(data, check=check) != prior['dataset_sha256']:
        raise ValueError('Prior oracle dataset changed')
    record = prior['models']['minilm']
    if model_record(record['id'], check=check) != record:
        raise ValueError('Previously cached MiniLM changed')
    selected = prior['selected_ids'][:100]
    proof = {}
    for arm in ('E1-granite', 'E3-lexical-rerank'):
        _, cfg, artifacts = valid_complete(previous / arm, check, prior['selected_ids'])
        if cfg['dataset']['sha256'] != prior['dataset_sha256']:
            raise ValueError('Historical traces/data mismatch')
        # Capture/baseline/scorer are not permitted to change to obtain parity.
        for path in ('bench/e2e/pipeline.py', 'bench/e2e/baselines.py', 'bench/e2e/data.py',
                     'bench/e2e/leaderboard.py', 'bench/e2e/stats.py', 'bench/e2e/answers.py'):
            if cfg['code_files'][path] != code_record()[path]:
                raise ValueError('Capture/reference/scorer changed')
        proof[arm] = artifacts
    output.mkdir(mode=0o700)
    write_json(output / 'selection.json', selection)
    plan = {'version': 'lexical-dev-v1', 'source_commit': revision,
            'code_files': code_record(), 'data': str(data), 'dataset_sha256': prior['dataset_sha256'],
            'previous': str(previous.resolve()), 'prior_plan_sha256': sha256(previous / 'plan.json'),
            'historical_proofs': proof, 'selected_ids': selected,
            'excluded_ids': prior['selected_ids'][100:],
            'selection_rule': 'first 100 in frozen resident-dev-v2 order, independent of results',
            'selection_sha256': sha256(output / 'selection.json'),
            'models': {'minilm': record}, 'capture_namespace': prior['capture_namespace'],
            'arms': arms(), 'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024,
            'trace_k': 100, 'warm_searches': 3, 'bootstrap': 2000, 'seed': 20261009,
            'case_seconds': 300, 'arm_seconds': 3600, 'campaign_seconds': 11700,
            'ingest_bytes': 16_000_000,
            'C1_gate': '100/100 exact ordered selected_quote_ids equal reference BM25',
            'winner_rule': 'C2/C3 only; lower IC95 delta recall >0 AND lower IC95 delta MRR >=0; then highest mean recall delta, MRR delta, lowest p50 warm-search latency, arm ID',
            'claim': 'development only; no S/leaderboard claim or automatic confirmation',
            'budget_policy': 'wall time -> infeasible/continue; correctness/integrity/resources -> STOP ALL'}
    freeze(output, plan)
    return plan


def inputs(directory, revision, check):
    plan = load_plan(directory, revision)
    if plan['version'] != 'lexical-dev-v1' or plan['arms'] != arms():
        raise ValueError('Fixed lexical development plan required')
    if sha256(Path(plan['data']), check=check) != plan['dataset_sha256']:
        raise ValueError('Dataset changed')
    if sha256(directory / 'selection.json') != plan['selection_sha256']:
        raise ValueError('Selection changed')
    previous = Path(plan['previous'])
    if sha256(previous / 'plan.json') != plan['prior_plan_sha256']:
        raise ValueError('Previous plan changed')
    prior = json.loads((previous / 'plan.json').read_text())
    if plan['selected_ids'] != prior['selected_ids'][:100] or plan['excluded_ids'] != prior['selected_ids'][100:]:
        raise ValueError('100-case membership changed')
    for arm, expected in plan['historical_proofs'].items():
        if valid_complete(previous / arm, check, prior['selected_ids'])[2] != expected:
            raise ValueError('Historical evidence changed')
    record = plan['models']['minilm']
    if model_record(record['id'], check=check) != record:
        raise ValueError('Frozen MiniLM changed')
    return plan


async def diagnose(plan, directory, check):
    output = directory / 'diagnosis'
    if output.exists():
        raise ValueError('Existing diagnosis preserved; no overwrite')
    output.mkdir(mode=0o700)
    previous = Path(plan['previous'])
    for arm in plan['historical_proofs']:
        write_json(output / f'{arm}-saved-audit.json', saved_audit(previous / arm, plan['selected_ids']))
    with (previous / 'E3-lexical-rerank/memory.jsonl').open() as stream:
        saved = {r['qid']: r for r in map(json.loads, stream) if r['qid'] in plan['selected_ids']}
    wanted = set(plan['selected_ids'])
    result = []
    with (output / 'questions.jsonl').open('x') as stream:
        for observations, gold in cases(Path(plan['data']), check=check):
            if gold.id not in wanted:
                continue
            check()
            with tempfile.TemporaryDirectory(prefix='capture-', dir=output) as temporary:
                row = await capture_case(observations, gold, Path(temporary),
                                         plan['capture_namespace'], saved[gold.id], check)
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            stream.flush()
            result.append(row)
            print(f"diagnosis {len(result)}/100", flush=True)
    if [r['qid'] for r in result] != plan['selected_ids']:
        raise ValueError('Diagnostic denominator/order mismatch')
    from .stats import estimate, cluster_ids
    golds = [g for _, g in cases(Path(plan['data']), check=check) if g.id in wanted]
    clusters = cluster_ids(golds)
    effects = {}
    for name in (s['stage'] for s in result[0]['stages']):
        effects[name] = {}
        for metric in ('recall', 'mrr'):
            pairs = []
            for row in result:
                a = next(s for s in row['stages'] if s['stage'] == name)[metric]
                b = row['stages'][0][metric]
                if a is not None and b is not None:
                    pairs.append((clusters[row['qid']], a - b))
            effects[name][metric] = estimate(pairs, plan['seed'], plan['bootstrap'], check)
    write_json(output / 'report.json', {'complete': True, 'n': len(result), 'paired_vs_reference': effects,
              'parity': all(r['exact_selected_quote_parity'] for r in result),
              'qualifier': 'controlled lexical reconstruction with hash; historical CE is observational'})
    write_json(output / 'proofs.json', {name: sha256(output / name) for name in
                                      ('questions.jsonl', 'report.json', 'E1-granite-saved-audit.json',
                                       'E3-lexical-rerank-saved-audit.json')})


def parity(rows, ids):
    index = {(r['system'], r['qid']): r for r in rows}
    if len(index) != len(rows) or set(index) != {(s, q) for s in ('memory', 'bm25') for q in ids}:
        raise ValueError('Missing/duplicate parity rows')
    return all(index['memory', q]['selected_quote_ids'] == index['bm25', q]['selected_quote_ids'] for q in ids)


def eligible(report):
    a = report['paired_delta_memory']['bm25']['ci95']
    b = report['paired_other_delta_memory']['bm25']['mrr']['ci95']
    return bool(a and b and a[0] > 0 and b[0] >= 0)


def validate_config(plan, arm, cfg, revision):
    from .configuration import resolved_config
    expected = resolved_config(plan['models']['minilm']['id'], '<private fresh store per question>', arm['engine_overrides'])
    if (cfg['engine_overrides'] != arm['engine_overrides'] or cfg['source_commit'] != revision
            or cfg['resolved_engine_config'] != expected or cfg['model_record'] != plan['models']['minilm']
            or cfg['run_id'] != plan['capture_namespace'] or cfg['resident'] is not True
            or cfg['protocol'] != 'e2e-resident-v2' or cfg['transport'] != 'stdio' or cfg['limit'] != 100
            or cfg['code_files'] != plan['code_files'] or cfg['selection_sha256'] != plan['selection_sha256']
            or cfg['run_seconds'] != plan['arm_seconds'] or cfg['case_seconds'] != plan['case_seconds']
            or cfg['ingest_bytes'] != plan['ingest_bytes']
            or cfg['systems'] != ['memory', 'bm25'] or cfg.get('reranker') is not None
            or cfg.get('unique_turns') or cfg.get('query_mode', 'none') != 'none'
            or cfg['selected_ids'] != plan['selected_ids']
            or any(cfg[k] != plan[k] for k in ('top_k', 'context_bytes', 'chunk_chars', 'trace_k', 'warm_searches', 'seed'))
            or cfg['bootstrap_replicates'] != plan['bootstrap']
            or cfg['dataset']['sha256'] != plan['dataset_sha256']):
        raise ValueError('Arm configuration differs from frozen plan')


def run(plan, directory, revision, check):
    outcomes = []
    for arm in plan['arms']:
        check()
        load_plan(directory, revision)  # no source/protocol drift while running
        target = directory / arm['id']
        outcome_path = directory / (arm['id'] + '-outcome.json')
        if outcome_path.exists():
            outcome = json.loads(outcome_path.read_text())
            if outcome['plan_sha256'] != sha256(directory / 'plan.json') or outcome['arm'] != arm:
                raise ValueError('Resume plan mismatch')
            if outcome['status'] != 'complete':
                # A receipted wall-time failure is terminal for that arm, not
                # a reason to prevent the following arm during resume.
                if outcome['status'] != 'infaisable' or arm['id'] == 'C1':
                    raise ValueError('C1 unproven or unknown resume status')
                for name, checksum in outcome['partial_proof'].items():
                    if sha256(target / name) != checksum:
                        raise ValueError('Infeasible evidence changed')
                outcomes.append(outcome)
                continue
            report, cfg, proof = valid_complete(target, check, plan['selected_ids'])
            if outcome['proof'] != proof:
                raise ValueError('Resumed completed evidence changed')
        else:
            if target.exists():
                raise ValueError('Unreceipted partial arm preserved; create new plan')
            args = ['--data', plan['data'], '--variant', 'oracle', '--source-commit', revision,
                    '--output', str(target), '--model', plan['models']['minilm']['id'],
                    '--systems', 'memory,bm25', '--limit', '100', '--resident',
                    '--selection', str(directory / 'selection.json'), '--top-k', '10',
                    '--context-bytes', '12000', '--chunk-chars', '1024', '--trace-k', '100',
                    '--warm-searches', '3', '--run-seconds', str(plan['arm_seconds']),
                    '--case-seconds', str(plan['case_seconds']),
                    '--ingest-bytes', str(plan['ingest_bytes']),
                    '--engine-overrides', json.dumps(arm['engine_overrides'])]
            status = run_campaign(args)
            check()
            outcome = {'arm': arm, 'plan_sha256': sha256(directory / 'plan.json')}
            if status:
                path = target / 'report.json'
                stopped = json.loads(path.read_text()) if path.exists() else json.loads((target / 'aborted.json').read_text())
                if stopped.get('termination_class') != 'budget':
                    raise ResourceStop('Correctness/integrity/resource failure')
                prediction_path = target / 'predictions.jsonl'
                if prediction_path.exists() and any(r.get('failure_class') == 'correctness' for r in
                        map(json.loads, prediction_path.read_text().splitlines())):
                    raise ResourceStop('Correctness failure preceding timeout')
                outcome.update(status='infaisable', reason=stopped,
                               partial_proof={p.name: sha256(p) for p in
                                              (target / 'report.json', target / 'aborted.json',
                                               target / 'predictions.jsonl', target / 'frozen.json')
                                              if p.exists()})
                write_json(outcome_path, outcome)
                if arm['id'] == 'C1':
                    raise ResourceStop('C1 parity unproven; fusion arms blocked')
                outcomes.append(outcome)
                continue
            report, cfg, proof = valid_complete(target, check, plan['selected_ids'])
            outcome.update(status='complete', proof=proof)
        validate_config(plan, arm, cfg, revision)
        rows = [json.loads(line) for line in (target / 'predictions.jsonl').read_text().splitlines()]
        if arm['id'] == 'C1' and not parity(rows, plan['selected_ids']):
            raise ResourceStop('C1 differs from BM25; no fusion arm started')
        outcome.update(parity=parity(rows, plan['selected_ids']), eligible=eligible(report), report=report)
        write_json(outcome_path, outcome)
        outcomes.append(outcome)
    candidates = [o for o in outcomes if o.get('eligible') and o['arm']['id'] != 'C1']
    def key(o):
        r = o['report']
        return (-r['paired_delta_memory']['bm25']['mean'],
                -r['paired_other_delta_memory']['bm25']['mrr']['mean'],
                r['systems']['memory']['warm_search_ms']['p50'], o['arm']['id'])
    winner = min(candidates, key=key)['arm'] if candidates else None
    check()
    load_plan(directory, revision)
    write_json(directory / 'development-verdict.json', {'terminal': True, 'winner': winner,
               'superiority_claim': False, 'confirmation_started': False,
               'reason': 'observed dev only; independent confirmation still required'})


def main(argv=None):
    deny_ip_network()
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='op', required=True)
    for op in ('prepare', 'diagnose', 'run'):
        a = sub.add_parser(op)
        a.add_argument('--source-commit', required=True)
        if op == 'prepare':
            a.add_argument('--previous', required=True, type=Path)
            a.add_argument('--output', required=True, type=Path)
        else:
            a.add_argument('--plan-dir', required=True, type=Path)
    args = p.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        p.error('Exact source commit required')
    clone = Path(__file__).resolve().parents[3]
    limits = Limits()
    start, last = time.monotonic(), 0.0
    budget = 11700 if args.op == 'run' else 900
    def check():
        nonlocal last
        if time.monotonic() - start > budget:
            raise ResourceStop('Lexical protocol wall-time budget exceeded')
        if time.monotonic() - last >= .5:
            sample(clone, limits)
            last = time.monotonic()
    try:
        ensure_priority(limits)
        check()
        if args.op == 'prepare':
            prepare(args.previous.resolve(), args.output.resolve(), args.source_commit, check)
        else:
            _, directory = private_path(args.plan_dir)
            plan = inputs(directory, args.source_commit, check)
            if args.op == 'diagnose':
                asyncio.run(diagnose(plan, directory, check))
            else:
                run(plan, directory, args.source_commit, check)
        return 0
    except (ResourceStop, ValueError, OSError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
