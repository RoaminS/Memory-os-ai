"""Pre-registered BEAM retrieval-only C4 versus same-arm BM25 campaign."""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
import json
import os
from pathlib import Path
import random
from statistics import mean
import subprocess
import sys
import time
import uuid

from .baselines import BM25
from .beam import CATEGORIES, DATA_SHA, audit, conversations, identity
from .client import ToolClient, project_for
from .configuration import resolved_config
from .data import sha256
from .experiments import MODELS, private_path
from .guard import Limits, ResourceStop, ensure_priority, sample
from .leaderboard import AddSearchAdapter
from .pipeline import extract, pack
from .run import code_record, kill_group, model_record, verify_source, write_json
from .stats import score

VERSION = 'beam-c4-retrieval-v2'
DATA = Path('/Users/romain/Memory-os-ai/v4/bench/data/beam/beam_100k.jsonl')
S_DATA = DATA.parent.parent / 'longmemeval_s.json'
SEED = 20261010


def select_pilot(items):
    """Two whole conversations; one retrieval probe/category in each; no outcome access."""
    selected = sorted(items, key=lambda c: identity('beam-pilot-v1', c.source_id))[:2]
    ids = []
    for c in selected:
        for category in CATEGORIES:
            ids.append(min((p.id for p in c.probes if p.gold.category == category)))
    return [c.id for c in selected], ids


def summarize(rows, expected_ids, minimum=20):
    """Question-weighted paired estimates, resampling WHOLE conversations."""
    if len({r['qid'] for r in rows}) != len(rows) or set(r['qid'] for r in rows) != set(expected_ids):
        raise ValueError('Incomplete/duplicate paired question denominator')
    groups = defaultdict(list)
    for row in rows:
        if row['memory']['recall'] is not None:
            if row['bm25']['recall'] is None:
                raise ValueError('Unpaired retrieval denominator')
            groups[row['conversation']].append(row)
    keys = sorted(groups)
    available = len(keys) >= minimum
    output = {'complete': True, 'questions': len(rows), 'recall_questions': sum(map(len, groups.values())),
              'clusters': len(keys), 'minimum_clusters': minimum, 'bootstrap_replicates': 2000,
              'bootstrap_unit': 'whole conversation; question-weighted within sampled conversations',
              'confidence': .95, 'seed': SEED, 'paired': {}, 'systems': {}}
    for system in ('memory', 'bm25'):
        output['systems'][system] = {metric: mean(r[system][metric] for g in groups.values() for r in g)
                                     for metric in ('recall', 'mrr')}
    for metric in ('recall', 'mrr'):
        values = [r['memory'][metric] - r['bm25'][metric] for g in groups.values() for r in g]
        interval = None
        if available:
            rng = random.Random(SEED)
            draws = []
            for _ in range(2000):
                sampled = [r for key in rng.choices(keys, k=len(keys)) for r in groups[key]]
                draws.append(mean(r['memory'][metric] - r['bm25'][metric] for r in sampled))
            draws.sort()
            interval = [draws[49], draws[1949]]
        output['paired'][metric] = {'mean': mean(values), 'ci95': interval}
    output['numerical_criterion_met'] = bool(available and output['paired']['recall']['ci95'][0] > 0
                                            and output['paired']['mrr']['mean'] >= 0)
    output['claim'] = 'local retrieval criterion only; not official BEAM answer accuracy or leaderboard rank'
    return output


def prepare(directory, revision):
    clone, directory = private_path(directory)
    if directory.exists():
        raise ValueError('Fresh preregistration directory required')
    verify_source(clone, revision, code_record())
    audited = audit(DATA, DATA.parent / 'PROVENANCE.json', S_DATA)
    if audited['overlap_question_ids'] or audited['overlap_turns']:
        raise ValueError('Overlap detected: amendment required BEFORE inference')
    pilot_conversations, pilot_questions = select_pilot(list(conversations(DATA)))
    record = model_record(str(MODELS['granite']))
    record['max_seq_length'] = 512
    config = resolved_config(record['id'], '<private-store>', profile='beam-c4-4.0.2')
    plan = {'version': VERSION, 'source_commit': revision, 'code_files': code_record(),
            'data': str(DATA), 'dataset_sha256': DATA_SHA, 'audit': audited,
            'model_record': record, 'engine_config': config, 'systems': ['memory', 'bm25'],
            'capture': 'observable chat content/role/session/id and explicit time_anchor only; no metadata/gold enrichment',
            'config_deviations': {'device': 'cpu', 'embed_batch_size': 4, 'use_recency': False,
                                  'reason': 'Synthetic capture time; no timestamp inferred from annotations or wall clock. C4 defaults and max_seq_length=512 retained.'},
            'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024, 'ingest_bytes': 16_000_000,
            'seed': SEED, 'bootstrap': 2000, 'minimum_clusters': 20,
            'primary': 'paired delta annotated-turn Recall@10 memory minus SAME-arm BM25, packed same context budget',
            'criterion': 'all 400 questions complete, no failure, 20 conversation clusters; IC95 lower paired recall delta >0 AND mean paired MRR delta >=0; missing IC never passes',
            'missing_evidence': '40 abstention + 5 explicit empty annotations: retrieve all, exclude only from recall/MRR; publish ids; no evidence inferred',
            'retrieval_pilot_conversations': pilot_conversations, 'retrieval_pilot_question_ids': pilot_questions,
            'retrieval_confirmatory_policy': 'all 20 conversations, fixed C4 before any pilot; no outcome-based tuning; pilot never authorizes retuning',
            'answer_reader': {'status': 'skipped-by-romain-2026-10-10',
                              'model_calls_authorized': 0, 'not_part_of_this_plan': True},
            'budgets': {'pilot_retrieval_seconds': 7200, 'full_retrieval_seconds': 28800,
                        'conversation_seconds': 3600, 'swap_bytes': 8_500_000_000, 'free_bytes': 5_000_000_000,
                        'nice': 15, 'concurrency': 1, 'downloads': 0},
            'no_retuning': True, 'official_submission': False}
    directory.mkdir(mode=0o700)
    write_json(directory / 'audit.json', audited)
    write_json(directory / 'plan.json', plan)
    write_json(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json'), 'source_commit': revision,
                                           'prepared_before_inference': True})
    return plan


def frozen(directory):
    clone, directory = private_path(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if plan['version'] != VERSION or receipt['plan_sha256'] != sha256(directory / 'plan.json'):
        raise ValueError('Frozen plan changed')
    verify_source(clone, plan['source_commit'], plan['code_files'])
    if sha256(Path(plan['data'])) != plan['dataset_sha256']:
        raise ValueError('Dataset changed')
    observed = model_record(plan['model_record']['id'])
    observed['max_seq_length'] = 512
    if observed != plan['model_record']:
        raise ValueError('Encoder snapshot changed')
    if resolved_config(plan['model_record']['id'], '<private-store>', profile='beam-c4-4.0.2') != plan['engine_config']:
        raise ValueError('Engine configuration changed')
    return plan


async def retrieval_worker(plan, output, pilot):
    # Every model/data mutation remains inside a private, nonce-owned MCP stdio.
    from .network import deny_ip_network
    deny_ip_network()
    model = plan['model_record']['id']
    rows = []
    expected = []
    startup = time.monotonic()
    async with ToolClient(output / 'memory', model, resident=True, config_profile='beam-c4-4.0.2') as client:
        startup_seconds = time.monotonic() - startup
        adapter = AddSearchAdapter(client, 'beam-c4-v1-capture')
        with (output / 'contexts.jsonl').open('x') as contexts, (output / 'predictions.jsonl').open('x') as predictions:
            for c in conversations(Path(plan['data'])):
                if pilot and c.id not in plan['retrieval_pilot_conversations']:
                    continue
                opened = await client.call('eval_open_conversation', {'conversation_id': c.id})
                if not opened['store_empty'] or not opened['encoder_reused']:
                    raise RuntimeError('Isolation/resident encoder proof failed')
                project = project_for(adapter.namespace, c.id)
                started = time.monotonic()
                quotes = extract(project, c.turns, plan['chunk_chars'])
                if sum(len(q.text.encode()) for q in quotes) > plan['ingest_bytes']:
                    raise ResourceStop('Ingestion byte budget exceeded')
                quote_by_id = {q.id: q for q in quotes}
                capture = await adapter._capture(c.id, c.turns, quotes)
                if capture['deduped'] or capture['added'] != len(quotes):
                    raise RuntimeError('Lossless capture parity failed')
                ingest_seconds = time.monotonic() - started
                baseline_root = output / 'bm25' / c.id
                baseline_root.mkdir(parents=True)
                started = time.monotonic()
                bm25 = BM25(quotes, baseline_root, model)
                baseline_seconds = time.monotonic() - started
                for probe in c.probes:
                    if pilot and probe.id not in plan['retrieval_pilot_question_ids']:
                        continue
                    expected.append(probe.id)
                    started = time.monotonic()
                    found = await adapter.search(c.id, probe.question, plan['top_k'])
                    memory_ms = (time.monotonic() - started) * 1000
                    ranked = [quote_by_id[h['id']] for h in found['results']]
                    started = time.monotonic()
                    baseline = bm25.search(probe.question, plan['top_k'])
                    baseline_ms = (time.monotonic() - started) * 1000
                    selected = {system: pack(result, plan['top_k'], plan['context_bytes'])
                                for system, result in [('memory', ranked), ('bm25', baseline)]}
                    row = {'qid': probe.id, 'conversation': c.id, 'category': probe.gold.category,
                           'startup_seconds': startup_seconds, 'conversation_ingest_seconds': ingest_seconds,
                           'bm25_ingest_seconds': baseline_seconds, 'search_ms': {'memory': memory_ms, 'bm25': baseline_ms},
                           **{system: score([q.turn_id for q in ctx], None, probe.gold, plan['top_k'])
                              for system, ctx in selected.items()}}
                    rows.append(row)
                    predictions.write(json.dumps(row, allow_nan=False) + '\n')
                    predictions.flush()
                    # Reader journal carries NO category, reference, rubric, evidence,
                    # answers or metadata: annotations are read only in judge phase.
                    contexts.write(json.dumps({'qid': probe.id, 'conversation': c.id, 'question': probe.question,
                                               'contexts': {system: [q.text for q in ctx] for system, ctx in selected.items()}}) + '\n')
                    contexts.flush()
                (output / 'progress').write_text(c.id)
    report = summarize(rows, expected, minimum=20)
    report['pilot'] = pilot
    report['usage'] = json.loads((output / 'memory/usage.json').read_text())
    report['contexts_sha256'] = sha256(output / 'contexts.jsonl')
    report['predictions_sha256'] = sha256(output / 'predictions.jsonl')
    report['plan_sha256'] = sha256(output.parent / 'plan.json')
    # Verify full independent denominator instead of accepting worker-selected ids.
    actual_expected = [p.id for c in conversations(Path(plan['data'])) for p in c.probes
                       if not pilot or p.id in plan['retrieval_pilot_question_ids']]
    if set(expected) != set(actual_expected):
        raise ValueError('Question selection mismatch')
    write_json(output / 'report.json', report)


def run_retrieval(directory, pilot):
    clone, directory = private_path(directory)
    plan = frozen(directory)
    ensure_priority(Limits())
    sample(directory)
    output = directory / ('pilot-retrieval' if pilot else 'retrieval')
    output.mkdir(mode=0o700, exist_ok=False)
    from .client import offline_env
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(output / 'server-registry'),
               PYTHONPATH=os.pathsep.join([str(clone / 'v4'), str(clone / 'v4/src')]))
    maximum = plan['budgets']['pilot_retrieval_seconds' if pilot else 'full_retrieval_seconds']
    deadline = time.monotonic() + maximum
    progress_time, last = time.monotonic(), None
    with (output / 'worker.log').open('x') as log:
        child = subprocess.Popen([sys.executable, '-m', 'bench.e2e.beam_campaign', '_worker', '--plan-dir', str(directory),
                                  *(['--pilot'] if pilot else [])], env=env, stdout=log, stderr=subprocess.STDOUT,
                                 cwd=output, start_new_session=True)
        try:
            while child.poll() is None:
                sample(directory)
                progress = (output / 'progress').read_text() if (output / 'progress').exists() else None
                if progress != last:
                    progress_time, last = time.monotonic(), progress
                if time.monotonic() > deadline or time.monotonic() - progress_time > plan['budgets']['conversation_seconds']:
                    raise ResourceStop('Frozen campaign/conversation budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'report.json').exists():
                raise RuntimeError('Retrieval worker failed; preserve partial evidence, no restart')
        except BaseException as exc:
            write_json(output / 'stopped.json', {'complete': False, 'reason': str(exc)})
            raise
        finally:
            from .processes import cleanup
            try:
                cleanup(output / 'server-registry', env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'pilot-retrieval', 'retrieve', '_worker'])
    parser.add_argument('--plan-dir', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--pilot', action='store_true')
    args = parser.parse_args(argv)
    _, directory = private_path(args.plan_dir)
    if args.operation == 'prepare':
        if not args.source_commit:
            parser.error('--source-commit required')
        prepare(directory, args.source_commit)
    elif args.operation == '_worker':
        plan = frozen(directory)
        asyncio.run(retrieval_worker(plan, directory / ('pilot-retrieval' if args.pilot else 'retrieval'), args.pilot))
    elif args.operation in {'pilot-retrieval', 'retrieve'}:
        run_retrieval(directory, args.operation == 'pilot-retrieval')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
