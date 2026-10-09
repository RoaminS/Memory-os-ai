"""Amend an unexecuted S preflight, retaining the byte-identical C4 winner."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types

from .amendment import (S_PATH, S_SHA, freeze, load_plan, record_outcome,
                        valid_complete, verify_outcome)
from .data import SESSION_POLICY, cases, inventory, sha256
from .experiments import private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .lexical_c4 import ARM, CRITERION, DEV_FIXED, DEV_VERSION, SHARED, S_FIXED, eligible, exclusive_json, validate_config
from .network import deny_ip_network
from .pipeline import extract
from .run import code_record, main as run_campaign, model_record, verify_source, write_json
from .stats import cluster_ids, score

VERSION = 'lexical-c4-s-v2'
CHANGES = {'bench/e2e/data.py', 'bench/e2e/stats.py'}
ADDITION = 'bench/e2e/lexical_s_v2.py'


def old_reader(clone, revision):
    """Load only the archived stdlib reader for exact unique-session comparison."""
    metadata = clone / '.local-qualification/git-state'
    git = ['git', '--git-dir', str(metadata)] if metadata.is_dir() else ['git', '-C', str(clone)]
    result = subprocess.run([*git, 'show', revision + ':v4/bench/e2e/data.py'],
                            capture_output=True, text=True, timeout=10, check=True)
    name = '_memory_s_v2_archived_data'
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(result.stdout, '<archived stdlib data.py>', 'exec'), module.__dict__)
    return module


def attest_candidate(directory, check):
    clone, directory = private_path(directory)
    dev = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if (dev['version'] != DEV_VERSION or dev['arms'] != [ARM]
            or receipt['plan_sha256'] != sha256(directory / 'plan.json')
            or dev['criterion'] != CRITERION or len(set(dev['selected_ids'])) != 100
            or any(dev[k] != v for k, v in {**SHARED, **DEV_FIXED}.items())):
        raise ValueError('Original fixed C4 development required')
    verify_source(clone, dev['source_commit'], dev['code_files'])
    current = code_record()
    if (set(current) != set(dev['code_files']) | {ADDITION}
            or any(current[k] != v for k, v in dev['code_files'].items() if k not in CHANGES)):
        raise ValueError('Candidate/capture/reference or unapproved source changed')
    report, cfg, proof = valid_complete(directory / 'C4', check, dev['selected_ids'])
    validate_config(dev, cfg)
    outcome = verify_outcome(dev, directory, ARM, check)
    if outcome['proof'] != proof or not eligible(report):
        raise ValueError('Original C4 gate/proof no longer valid')
    if sha256(Path(dev['data']), check=check) != dev['dataset_sha256']:
        raise ValueError('Oracle data changed')
    if sha256(directory / 'selection.json') != dev['selection_sha256']:
        raise ValueError('Development selection changed')
    if model_record(dev['models']['minilm']['id'], check=check) != dev['models']['minilm']:
        raise ValueError('Candidate model changed')
    lock = json.loads((directory / 'confirmation-lock.json').read_text())
    _, failed = private_path(Path(lock['output']))
    if (lock['arm'] != ARM or {p.name for p in failed.iterdir()} != {'winner.json'}
            or sha256(failed / 'winner.json') != lock['winner_sha256']):
        raise ValueError('Only failed unexecuted v1 preflight can be amended')
    chosen = json.loads((failed / 'winner.json').read_text())
    if (chosen['proof'] != proof or chosen['source_commit'] != dev['source_commit']
            or chosen['code_files'] != dev['code_files'] or chosen['arm'] != ARM
            or chosen['development_plan_sha256'] != sha256(directory / 'plan.json')
            or chosen['resolved_engine_config'] != cfg['resolved_engine_config']):
        raise ValueError('Byte-identical original winner required')
    return dev, chosen, failed


def dev_equivalence(dev, directory, check):
    clone, _ = private_path(directory)
    old = old_reader(clone, dev['source_commit'])
    wanted = set(dev['selected_ids'])
    archived = {g.id: (i, g) for i, g in old.cases(Path(dev['data']), check=check) if g.id in wanted}
    current = {g.id: (i, g) for i, g in cases(Path(dev['data']), check=check) if g.id in wanted}
    if list(current) != dev['selected_ids'] or list(archived) != dev['selected_ids']:
        raise ValueError('Development membership drift')
    for qid in wanted:
        a, ga = archived[qid]
        b, gb = current[qid]
        if (gb.turn_aliases or vars(a) | {'turns': [vars(t) for t in a.turns]}
                != vars(b) | {'turns': [vars(t) for t in b.turns]}
                or any(getattr(ga, k) != getattr(gb, k) for k in vars(ga))):
            raise ValueError('Reader altered original unique-session development')
    count = 0
    for line in (directory / 'C4/predictions.jsonl').read_text().splitlines():
        row = json.loads(line)
        measured = score(row['retrieved_turn_ids'], row['response'], current[row['qid']][1], dev['top_k'])
        if any(row[k] != v for k, v in measured.items()):
            raise ValueError('Scoring amendment altered original development metrics')
        count += 1
    if count != 200:
        raise ValueError('All 200 paired dev predictions must remain identical')
    return {'observations_identical': 100, 'predictions_rescored_identically': count,
            'neural_encodings': 0, 'previous_proofs_sha256': sha256(directory / 'C4/proofs.json')}


def size_case(observations):
    quotes = extract(observations.id, observations.turns, 1024)
    chars = sum(len(t.content) for t in observations.turns) + sum(len(q.text) for q in quotes)
    return {'turns': len(observations.turns), 'quotes': len(quotes), 'indexed_chars': chars,
            'vector_bytes_minimum': (len(quotes) + len(observations.turns)) * 384 * 4}


def estimate(dev, directory, check):
    """No tokenizer/model: character-volume extrapolation, not measured S time."""
    rows = [json.loads(s) for s in (directory / 'C4/memory.jsonl').read_text().splitlines()]
    dev_chars = sum(size_case(i)['indexed_chars'] for i, g in cases(Path(dev['data']), check=check)
                    if g.id in set(dev['selected_ids']))
    ingestion_seconds = sum(r['ingest_ms'] for r in rows) / 1000
    sizes = [size_case(i) for i, _ in cases(S_PATH, check=check)]
    point = ingestion_seconds * sum(s['indexed_chars'] for s in sizes) / dev_chars
    return {'method': 'observed dev ingestion seconds * S/dev indexed character volume; NO S model execution',
            'dev_ingestion_seconds': ingestion_seconds, 'dev_indexed_chars': dev_chars,
            'S_indexed_chars': sum(s['indexed_chars'] for s in sizes),
            'linear_ingestion_hours': point / 3600,
            'planning_hours_1_to_4x': [point / 3600, 4 * point / 3600],
            'peak_case': {k: max(s[k] for s in sizes) for k in sizes[0]},
            'process_ram_planning_bytes': [2_000_000_000, 6_000_000_000],
            'rss_measured_on_S': False, 'runtime_model_python_indexes_not_in_vector_lower_bound': True,
            'nonlinear_index_and_dedup_costs_may_exceed_estimate': True,
            'wall_cap_hours': 48, 'swap_stop_bytes': 8_500_000_000, 'disk_stop_bytes': 5_000_000_000}


def prepare(directory, output, revision, check):
    clone, output = private_path(output)
    verify_source(clone, revision, code_record())
    dev, chosen, failed = attest_candidate(directory, check)  # before S access
    equivalence = dev_equivalence(dev, directory, check)
    lockpath = directory / 'confirmation-amendment-lock-v2.json'
    if output.exists() or lockpath.exists():
        raise ValueError('One amended S preflight only; old reservations preserved')
    output.mkdir(mode=0o700)
    # Preserve the exact original winner bytes, rather than rewriting its source.
    with (output / 'winner.json').open('xb') as stream:
        stream.write((failed / 'winner.json').read_bytes())
        stream.flush()
        os.fsync(stream.fileno())
    exclusive_json(lockpath, {'output': str(output), 'winner_sha256': sha256(output / 'winner.json'), 'arm': ARM})
    if sha256(S_PATH, check=check) != S_SHA:
        raise ValueError('Official S data SHA mismatch')
    data = inventory(S_PATH, 's', check=check)
    golds = [g for _, g in cases(S_PATH, check=check)]
    if len(golds) != 500 or len({g.id for g in golds}) != 500:
        raise ValueError('All 500 S questions required')
    if len(data['duplicate_question_ids']) != 15:
        raise ValueError('Expected audited 15 duplicate-session questions; review required')
    write_json(output / 'duplicate-audit.json', {'policy': SESSION_POLICY,
               'question_ids': data['duplicate_question_ids'], 'groups': data['duplicate_session_groups']})
    write_json(output / 'dev-equivalence.json', equivalence)
    write_json(output / 'estimate.json', estimate(dev, directory, check))
    plan = {k: dev[k] for k in ('models', 'arms', 'capture_namespace', *SHARED)}
    plan.update(S_FIXED)
    plan.update(version=VERSION, source_commit=revision, code_files=code_record(), criterion=CRITERION,
                data=str(S_PATH), dataset_sha256=S_SHA, dataset=data, session_policy=SESSION_POLICY,
                selected_ids=[g.id for g in golds], development_directory=str(directory), winner=chosen,
                original_candidate_source=dev['source_commit'],
                oracle_question_overlap=sorted(set(dev['oracle_question_ids']) & {g.id for g in golds}),
                source_clusters=len(set(cluster_ids(golds).values())), independent_questions=False,
                claim='dated identical copies; overlapping oracle; NOT independent; NOT official leaderboard',
                evidence_files={p.name: sha256(p) for p in output.iterdir()},
                dev_plan_sha256=sha256(directory / 'plan.json'))
    freeze(output, plan)
    return plan


def inputs(directory, revision, check):
    plan = load_plan(directory, revision)
    if (plan['version'] != VERSION or plan['arms'] != [ARM] or plan['criterion'] != CRITERION
            or plan['code_files'] != code_record() or plan['session_policy'] != SESSION_POLICY
            or plan['dataset_sha256'] != S_SHA or plan['data'] != str(S_PATH)
            or plan['dataset']['session_policy'] != SESSION_POLICY
            or set(plan['evidence_files']) != {'winner.json', 'duplicate-audit.json', 'dev-equivalence.json', 'estimate.json'}
            or len(set(plan['selected_ids'])) != 500 or len(plan['selected_ids']) != 500
            or any(plan[k] != v for k, v in {**SHARED, **S_FIXED}.items())):
        raise ValueError('Frozen S v2 protocol changed')
    devdir = Path(plan['development_directory'])
    dev, chosen, _ = attest_candidate(devdir, check)  # no S yet
    if (chosen != plan['winner'] or sha256(devdir / 'plan.json') != plan['dev_plan_sha256']
            or plan['original_candidate_source'] != dev['source_commit']
            or any(plan[k] != dev[k] for k in ('models', 'capture_namespace', *SHARED))):
        raise ValueError('Original candidate/development proof changed')
    for name, checksum in plan['evidence_files'].items():
        if Path(name).name != name or sha256(directory / name, check=check) != checksum:
            raise ValueError('Amendment evidence changed')
    lock = json.loads((devdir / 'confirmation-amendment-lock-v2.json').read_text())
    if lock != {'output': str(directory), 'winner_sha256': plan['evidence_files']['winner.json'], 'arm': ARM}:
        raise ValueError('Amendment reservation changed')
    if sha256(S_PATH, check=check) != S_SHA:
        raise ValueError('S input changed')
    return plan


def validate_execution(plan, cfg):
    validate_config(plan, cfg)
    if cfg['dataset']['session_policy'] != SESSION_POLICY or cfg['dataset'] != plan['dataset']:
        raise ValueError('Actual session policy/data audit changed')


def run(directory, revision, check):
    plan = inputs(directory, revision, check)
    target = directory / 'C4'
    if (directory / 'C4-outcome.json').exists():
        outcome = verify_outcome(plan, directory, ARM, check)
        if outcome['status'] == 'complete':
            _, cfg, _ = valid_complete(target, check, plan['selected_ids'])
            validate_execution(plan, cfg)
    else:
        if target.exists():
            raise ValueError('Unreceipted partial S campaign preserved; no silent restart')
        args = ['--data', str(S_PATH), '--variant', 's', '--source-commit', revision,
                '--output', str(target), '--model', plan['models']['minilm']['id'], '--systems', 'memory,bm25',
                '--resident', '--limit', '500', '--engine-overrides', json.dumps(ARM['engine_overrides'])]
        for k in ('top_k', 'context_bytes', 'chunk_chars', 'trace_k', 'warm_searches', 'seed',
                  'run_seconds', 'case_seconds', 'ingest_bytes'):
            args += ['--' + k.replace('_', '-'), str(plan[k])]
        status = run_campaign(args)
        check()
        if not status:
            _, cfg, _ = valid_complete(target, check, plan['selected_ids'])
            validate_execution(plan, cfg)
        outcome = record_outcome(plan, directory, ARM, target, status, check)
    inputs(directory, revision, check)
    verdict = {'status': outcome['status'], 'all_500_executed': outcome['status'] == 'complete',
               'numerical_criterion_met': outcome['status'] == 'complete' and eligible(outcome['report']),
               'independent_confirmation_passed': False, 'leaderboard_rank': 'not evaluated',
               'oracle_question_overlap': len(plan['oracle_question_overlap']), 'source_clusters': plan['source_clusters'],
               'session_policy': SESSION_POLICY, 'official_scoring': False}
    write_json(directory / 'verdict.json', verdict)
    return verdict


def main(argv=None):
    deny_ip_network()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'run'])
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--dev-dir', type=Path, default=Path('.local-qualification/lexical-c4-dev-v1'))
    parser.add_argument('--plan-dir', type=Path, default=Path('.local-qualification/lexical-c4-s-v2'))
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        parser.error('Exact source commit required')
    started, last = time.monotonic(), 0.0
    budget = 900 if args.operation == 'prepare' else 173700
    clone = Path(__file__).resolve().parents[3]
    def check():
        nonlocal last
        if time.monotonic() - started > budget:
            raise ResourceStop('S amendment wall-time budget exceeded')
        if time.monotonic() - last > .5:
            sample(clone, Limits())
            last = time.monotonic()
    try:
        ensure_priority(Limits())
        check()
        _, directory = private_path(args.plan_dir)
        if args.operation == 'prepare':
            _, devdir = private_path(args.dev_dir)
            plan = prepare(devdir, directory, args.source_commit, check)
            print(json.dumps({'plan_sha256': sha256(directory / 'plan.json'), 'questions': len(plan['selected_ids']),
                              'winner_sha256': plan['evidence_files']['winner.json'],
                              'estimate': json.loads((directory / 'estimate.json').read_text())}))
        else:
            print(json.dumps(run(directory, args.source_commit, check)))
        return 0
    except (ResourceStop, ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
