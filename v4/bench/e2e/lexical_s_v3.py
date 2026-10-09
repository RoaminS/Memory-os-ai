"""Metadata-only S v3 freeze; guarded execution with evidence/abs bootstrap."""
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

from . import stats
from .amendment import S_SHA, freeze, load_plan, record_outcome, valid_complete, verify_outcome
from .data import SESSION_POLICY, cases, sha256
from .experiments import private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .lexical_c4 import ARM, CRITERION, SHARED, S_FIXED, eligible, exclusive_json, validate_config
from .lexical_s_v2 import dev_equivalence, VERSION as PREVIOUS_VERSION
from .network import deny_ip_network
from .run import code_record, main as run_campaign, model_record, verify_source, write_json

VERSION = 'lexical-c4-s-v3'
ADDITION = 'bench/e2e/lexical_s_v3.py'
CHANGES = {'bench/e2e/data.py', 'bench/e2e/stats.py', 'bench/e2e/run.py'}
BOOTSTRAP = {'policy': stats.EVIDENCE_POLICY, 'minimum_clusters': stats.EVIDENCE_MIN_CLUSTERS,
             'confidence': .95, 'replicates': 2000, 'seed': 20261009,
             'conditional_on_fixed_distractor_corpus': True}


def archived_stats(clone, revision):
    metadata = clone / '.local-qualification/git-state'
    git = ['git', '--git-dir', str(metadata)] if metadata.is_dir() else ['git', '-C', str(clone)]
    result = subprocess.run([*git, 'show', revision + ':v4/bench/e2e/stats.py'],
                            capture_output=True, text=True, timeout=10, check=True)
    name = 'bench.e2e._s_v3_archived_stats'
    module = types.ModuleType(name)
    module.__package__ = 'bench.e2e'
    sys.modules[name] = module
    exec(compile(result.stdout, '<archived stats.py>', 'exec'), module.__dict__)
    return module


def dev_unchanged(dev, directory):
    """A lightweight regression over observed dev, never S/model execution."""
    clone, _ = private_path(directory)
    equivalence = dev_equivalence(dev, directory, lambda: None)
    rows = [json.loads(s) for s in (directory / 'C4/predictions.jsonl').read_text().splitlines()]
    wanted = set(dev['selected_ids'])
    golds = [g for _, g in cases(Path(dev['data'])) if g.id in wanted]
    current = stats.summarize(rows, golds, ['memory', 'bm25'], dev['seed'])
    old = archived_stats(clone, dev['source_commit']).summarize(rows, golds, ['memory', 'bm25'], dev['seed'])
    report = json.loads((directory / 'C4/report.json').read_text())
    if current != old or any(report[k] != v for k, v in current.items()):
        raise ValueError('Development bootstrap/report changed')
    return {**equivalence, 'all_development_metrics_and_intervals_identical': True,
            'development_bootstrap': current['bootstrap']}


def attest_previous(directory):
    clone, directory = private_path(directory)
    previous = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if (receipt['plan_sha256'] != sha256(directory / 'plan.json') or previous['version'] != PREVIOUS_VERSION
            or previous['arms'] != [ARM] or previous['criterion'] != CRITERION
            or previous['dataset_sha256'] != S_SHA or previous['session_policy'] != SESSION_POLICY
            or len(previous['selected_ids']) != 500 or len(set(previous['selected_ids'])) != 500
            or any(previous[k] != v for k, v in {**SHARED, **S_FIXED}.items())
            or {p.name for p in directory.iterdir()} != {'plan.json', 'receipt.json', *previous['evidence_files']}):
        raise ValueError('Only complete unexecuted fixed S v2 can be amended')
    verify_source(clone, previous['source_commit'], previous['code_files'])
    current = code_record()
    if (set(current) != set(previous['code_files']) | {ADDITION}
            or any(current[k] != v for k, v in previous['code_files'].items() if k not in CHANGES)):
        raise ValueError('Engine/capture/reference or unapproved source changed')
    for name, checksum in previous['evidence_files'].items():
        if Path(name).name != name or sha256(directory / name) != checksum:
            raise ValueError('Previous evidence changed')
    _, devdir = private_path(Path(previous['development_directory']))
    dev = json.loads((devdir / 'plan.json').read_text())
    if sha256(devdir / 'plan.json') != previous['dev_plan_sha256']:
        raise ValueError('Original development plan changed')
    verify_source(clone, dev['source_commit'], dev['code_files'])
    report, cfg, proof = valid_complete(devdir / 'C4', lambda: None, dev['selected_ids'])
    validate_config(dev, cfg)
    outcome = verify_outcome(dev, devdir, ARM, lambda: None)
    chosen = json.loads((directory / 'winner.json').read_text())
    if (not eligible(report) or outcome['proof'] != proof or previous['winner'] != chosen
            or chosen['arm'] != ARM or chosen['proof'] != proof
            or chosen['source_commit'] != dev['source_commit']
            or chosen['code_files'] != dev['code_files']
            or chosen['resolved_engine_config'] != cfg['resolved_engine_config']
            or any(previous[k] != dev[k] for k in ('models', 'capture_namespace', *SHARED))):
        raise ValueError('Original fixed C4 candidate/proof changed')
    lock = json.loads((devdir / 'confirmation-amendment-lock-v2.json').read_text())
    if lock != {'output': str(directory), 'winner_sha256': previous['evidence_files']['winner.json'], 'arm': ARM}:
        raise ValueError('Original S v2 reservation changed')
    return previous, dev, devdir


def prepare(previous_directory, output, revision):
    # Metadata and small dev regression only: no S scan, model load or campaign.
    clone, output = private_path(output)
    verify_source(clone, revision, code_record())
    previous, dev, devdir = attest_previous(previous_directory)
    equivalence = dev_unchanged(dev, devdir)
    lockpath = devdir / 'confirmation-amendment-lock-v3.json'
    if output.exists() or lockpath.exists():
        raise ValueError('S v3 already reserved; no silent overwrite')
    output.mkdir(mode=0o700)
    for name in previous['evidence_files']:
        with (output / name).open('xb') as stream:
            stream.write((previous_directory / name).read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    write_json(output / 'dev-bootstrap-equivalence.json', equivalence)
    exclusive_json(lockpath, {'output': str(output), 'winner_sha256': sha256(output / 'winner.json'), 'arm': ARM})
    plan = dict(previous)
    plan.update(version=VERSION, source_commit=revision, code_files=code_record(), bootstrap_inference=BOOTSTRAP,
                previous_directory=str(previous_directory), previous_plan_sha256=sha256(previous_directory / 'plan.json'),
                source_clusters=None, previous_all_haystack_clusters=previous['source_clusters'],
                evidence_only_clusters_observed_by_Claude=495,
                cluster_preflight='recompute evidence+abs groups on official S before any model worker; require 30 primary groups',
                claim='conditional fixed-distractor evidence/abs CI; observed oracle overlap; NOT independent or official',
                evidence_files={p.name: sha256(p) for p in output.iterdir()})
    freeze(output, plan)
    return plan


def inputs(directory, revision):
    plan = load_plan(directory, revision)
    if (plan['version'] != VERSION or plan['bootstrap_inference'] != BOOTSTRAP
            or plan['code_files'] != code_record() or plan['arms'] != [ARM] or plan['criterion'] != CRITERION
            or plan['dataset_sha256'] != S_SHA or plan['session_policy'] != SESSION_POLICY
            or len(set(plan['selected_ids'])) != 500 or len(plan['selected_ids']) != 500
            or any(plan[k] != v for k, v in {**SHARED, **S_FIXED}.items())):
        raise ValueError('S v3 source/protocol changed')
    previous_directory = Path(plan['previous_directory'])
    previous, _, devdir = attest_previous(previous_directory)
    if sha256(previous_directory / 'plan.json') != plan['previous_plan_sha256']:
        raise ValueError('S v2 plan changed')
    for k in ('winner', 'data', 'dataset', 'selected_ids', 'models', 'capture_namespace', 'dev_plan_sha256'):
        if plan[k] != previous[k]:
            raise ValueError('V3 changed frozen candidate/data')
    for name, checksum in plan['evidence_files'].items():
        if Path(name).name != name or sha256(directory / name) != checksum:
            raise ValueError('S v3 evidence changed')
    lock = json.loads((devdir / 'confirmation-amendment-lock-v3.json').read_text())
    if lock != {'output': str(directory), 'winner_sha256': plan['evidence_files']['winner.json'], 'arm': ARM}:
        raise ValueError('S v3 reservation changed')
    return plan


def validate_execution(plan, cfg, report):
    validate_config(plan, cfg)
    if (cfg.get('bootstrap_policy') != BOOTSTRAP['policy']
            or cfg.get('bootstrap_min_clusters') != BOOTSTRAP['minimum_clusters']
            or cfg['dataset'] != plan['dataset']
            or any(report['bootstrap'].get(k) != v for k, v in BOOTSTRAP.items())):
        raise ValueError('Actual frozen S bootstrap/candidate differs')


def run(directory, revision, check):
    plan = inputs(directory, revision)
    if sha256(Path(plan['data']), check=check) != S_SHA:
        raise ValueError('Official frozen S input changed; no worker allowed')
    if model_record(plan['models']['minilm']['id'], check=check) != plan['models']['minilm']:
        raise ValueError('Frozen C4 model changed; no worker allowed')
    target = directory / 'C4'
    if (directory / 'C4-outcome.json').exists():
        outcome = verify_outcome(plan, directory, ARM, check)
        if outcome['status'] == 'complete':
            report, cfg, _ = valid_complete(target, check, plan['selected_ids'])
            validate_execution(plan, cfg, report)
    else:
        if target.exists():
            raise ValueError('Partial S campaign preserved; no silent restart')
        args = ['--data', plan['data'], '--variant', 's', '--source-commit', revision,
                '--output', str(target), '--model', plan['models']['minilm']['id'], '--systems', 'memory,bm25',
                '--resident', '--limit', '500', '--engine-overrides', json.dumps(ARM['engine_overrides']),
                '--bootstrap-policy', BOOTSTRAP['policy'], '--bootstrap-min-clusters', str(BOOTSTRAP['minimum_clusters'])]
        for k in ('top_k', 'context_bytes', 'chunk_chars', 'trace_k', 'warm_searches', 'seed',
                  'run_seconds', 'case_seconds', 'ingest_bytes'):
            args += ['--' + k.replace('_', '-'), str(plan[k])]
        status = run_campaign(args)  # guard + cluster preflight BEFORE model worker
        check()
        aborted = target / 'aborted.json'
        if status and aborted.exists():
            reason = json.loads(aborted.read_text())['reason']
            if reason.startswith('S bootstrap unavailable:'):
                verdict = {'status': 'IC_unavailable', 'numerical_criterion_met': False,
                           'independent_confirmation_passed': False, 'model_workers_started': 0,
                           'reason': reason, 'bootstrap': BOOTSTRAP}
                write_json(directory / 'verdict.json', verdict)
                return verdict
        if not status:
            report, cfg, _ = valid_complete(target, check, plan['selected_ids'])
            validate_execution(plan, cfg, report)
        outcome = record_outcome(plan, directory, ARM, target, status, check)
    inputs(directory, revision)
    passed = outcome['status'] == 'complete' and eligible(outcome['report'])
    verdict = {'status': outcome['status'], 'numerical_criterion_met': passed,
               'independent_confirmation_passed': False, 'bootstrap': BOOTSTRAP,
               'oracle_question_overlap': len(plan['oracle_question_overlap']), 'leaderboard_rank': 'not evaluated'}
    write_json(directory / 'verdict.json', verdict)
    return verdict


def main(argv=None):
    deny_ip_network()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'run'])
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--previous-dir', type=Path, default=Path('.local-qualification/lexical-c4-s-v2'))
    parser.add_argument('--plan-dir', type=Path, default=Path('.local-qualification/lexical-c4-s-v3'))
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}', args.source_commit):
        parser.error('Exact full source commit required')
    try:
        _, directory = private_path(args.plan_dir)
        if args.operation == 'prepare':
            _, previous = private_path(args.previous_dir)
            plan = prepare(previous, directory, args.source_commit)
            print(json.dumps({'plan_sha256': sha256(directory / 'plan.json'), 'bootstrap': plan['bootstrap_inference'],
                              'questions': len(plan['selected_ids'])}))
        else:
            ensure_priority(Limits())
            clone = Path(__file__).resolve().parents[3]
            started = time.monotonic()
            def check():
                if time.monotonic() - started > 173700:
                    raise ResourceStop('S v3 wall-time budget exceeded')
                sample(clone, Limits())
            check()
            print(json.dumps(run(directory, args.source_commit, check)))
        return 0
    except (ResourceStop, ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        print(f'STOP: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
