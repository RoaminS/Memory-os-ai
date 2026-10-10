"""Preregistered AB/BA MPS/float32 development screen, no production activation."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import platform
import random
import signal
from statistics import mean, median
import subprocess
import sys
import tarfile
import time
import uuid

from bench.e2e.baselines import BM25
from bench.e2e.beam import conversations
from bench.e2e.client import ToolClient, offline_env, project_for
from bench.e2e.data import sha256
from bench.e2e.guard import Limits, ResourceStop, ensure_priority, sample
from bench.e2e.indexing_campaign import DATA, DATA_SHA, MODEL, archive_records, git, rss_bytes, select, snapshot, write
from bench.e2e.leaderboard import AddSearchAdapter
from bench.e2e.pipeline import extract, pack
from bench.e2e.processes import cleanup
from bench.e2e.run import code_record, kill_group, model_record
from bench.e2e.stats import score

VERSION = 'indexing-mps-v2'
ENCODING = {a: {'device': a, 'embed_dtype': 'native' if a == 'cpu' else 'float32',
                'embed_batch_size': 4, 'batch_facts': False} for a in ('cpu', 'mps')}


def runtime():
    packages = ('torch', 'transformers', 'sentence-transformers', 'numpy', 'mcp', 'rank-bm25')
    return {'node': platform.node(), 'machine': platform.machine(), 'platform': platform.platform(),
            'python': sys.version, 'packages': {p: importlib.metadata.version(p) for p in packages}}


def prepare(clone, directory, revision):
    if directory.exists() or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Fresh plan directory and full source commit required')
    ensure_priority(Limits())
    sample(clone)
    import torch
    if not torch.backends.mps.is_available():
        raise ResourceStop('MPS unavailable; no fallback/alternative arm authorized')
    if sha256(DATA) != DATA_SHA:
        raise ValueError('Dataset checksum changed')
    payload = git(clone, 'archive', revision, 'v4/src', 'v4/bench/e2e')
    if archive_records(payload) != code_record():
        raise ValueError('Candidate sources differ from commit')
    items = select(list(conversations(DATA)))
    record = model_record(str(MODEL))
    record['max_seq_length'] = 512
    plan = {'version': VERSION, 'source_commit': revision, 'archive_sha256': hashlib.sha256(payload).hexdigest(),
            'harness_sha256': sha256(Path(__file__)), 'runtime': runtime(), 'data': str(DATA),
            'data_sha256': DATA_SHA, 'model': record, 'encoding': ENCODING, 'jobs': ['ab', 'ba'],
            'selected': [{'id': c.id, 'source_id': c.source_id,
                          'probes': [p.id for p in c.probes]} for c in items],
            'schedule': {'ab': [[c.id, ['cpu', 'mps']] for c in items],
                         'ba': [[c.id, ['mps', 'cpu']] for c in reversed(items)]},
            'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024,
            'wall_seconds_per_job': 10800, 'cooldown_seconds': 60,
            'rss_bytes': 4 * 1024**3, 'mps_driver_bytes': 2 * 1024**3,
            'nice': 15, 'concurrency': 1, 'swap_bytes': 8_500_000_000, 'free_bytes': 5_000_000_000,
            'targets': {'p50': 300, 'p95': 450, 'max': 600, 'minimum_paired_speedup': 1.5},
            'equivalence': {'margin': .01, 'ci': .975, 'bootstrap': 10000,
                            'seed': 20261010, 'minimum_clusters': 3},
            'claim': 'observed BEAM development screen on three clusters; not independent confirmation; '
                     'not population p95, answer accuracy or leaderboard rank'}
    directory.mkdir(mode=0o700, parents=True)
    (directory / 'source.tar').write_bytes(payload)
    source = directory / 'source'
    source.mkdir()
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for member in archive:
            if (not (member.isdir() or member.isfile()) or
                    not (source / member.name).resolve().is_relative_to(source)):
                raise ValueError('Unsafe archive')
        archive.extractall(source, filter='data')
    write(directory / 'plan.json', plan)
    write(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json')})
    return plan


def frozen(directory):
    plan = json.loads((directory / 'plan.json').read_text())
    if (plan['version'] != VERSION or plan['harness_sha256'] != sha256(Path(__file__)) or
            sha256(directory / 'plan.json') != json.loads((directory / 'receipt.json').read_text())['plan_sha256']):
        raise ValueError('Plan/harness changed')
    if runtime() != plan['runtime'] or plan['encoding'] != ENCODING:
        raise ValueError('Runtime/encoding changed')
    if sha256(Path(plan['data'])) != plan['data_sha256']:
        raise ValueError('Dataset changed')
    record = model_record(plan['model']['id'])
    record['max_seq_length'] = 512
    if record != plan['model']:
        raise ValueError('Model changed')
    tar = directory / 'source.tar'
    if sha256(tar) != plan['archive_sha256']:
        raise ValueError('Archive changed')
    root = directory / 'source/v4'
    found = {str(p.relative_to(root)): sha256(p) for parent in (root / 'src', root / 'bench/e2e')
             for p in parent.rglob('*.py')}
    if found != archive_records(tar.read_bytes()):
        raise ValueError('Extracted source changed')
    return plan


async def worker(plan, job, output):
    from bench.e2e.network import deny_ip_network
    deny_ip_network()
    selected = {c.id: c for c in conversations(Path(plan['data']))
                if c.id in {r['id'] for r in plan['selected']}}
    rows, clients, adapters, startups = [], {}, {}, {}
    async with AsyncExitStack() as stack:
        # Two models resident, ONE active encode/capture/search at a time.
        first = plan['schedule'][job][0][1]
        for arm in first:
            t = time.monotonic()
            client = await stack.enter_async_context(ToolClient(output / 'memory' / arm,
                plan['model']['id'], resident=True, config_profile='beam-c4-4.0.2',
                encoding=plan['encoding'][arm]))
            usage = json.loads((output / 'memory' / arm / 'usage.json').read_text())
            if plan['model']['id'] != 'hash':
                if usage['model_device'] != arm or (arm == 'mps' and usage['model_dtype'] != 'torch.float32'):
                    raise RuntimeError('Encoder device/precision mismatch; no silent fallback')
            startups[arm] = {'seconds': time.monotonic() - t, 'dtype': usage.get('model_dtype')}
            clients[arm], adapters[arm] = client, AddSearchAdapter(client, 'beam-c4-v1-capture')
        # Model-load warmup is excluded and does not leak into the first timed pair.
        for cid, order in plan['schedule'][job]:
            c = selected[cid]
            record = {'id': cid, 'source_id': c.source_id, 'arms': {}, 'quality': []}
            for arm in order:
                if plan['cooldown_seconds']:
                    await asyncio.sleep(plan['cooldown_seconds'])
                opened = await clients[arm].call('eval_open_conversation', {'conversation_id': cid})
                if not opened['store_empty'] or not opened['encoder_reused']:
                    raise RuntimeError('Resident store isolation failed')
                usage_path = output / 'memory' / arm / 'usage.json'
                before = json.loads(usage_path.read_text())
                t = time.monotonic()
                project = project_for(adapters[arm].namespace, cid)
                quotes = extract(project, c.turns, plan['chunk_chars'])
                capture = await adapters[arm]._capture(cid, c.turns, quotes)
                elapsed = time.monotonic() - t
                if capture['deduped'] or capture['added'] != len(quotes):
                    raise RuntimeError('Lossless capture failed')
                usage = json.loads(usage_path.read_text())
                out = output / arm
                out.mkdir(exist_ok=True)
                quote_by_id = {q.id: q for q in quotes}
                baseline_root = out / 'bm25' / cid
                baseline_root.mkdir(parents=True)
                baseline = BM25(quotes, baseline_root, plan['model']['id'])
                results = []
                for probe in c.probes:
                    found = await adapters[arm].search(cid, probe.question, plan['top_k'])
                    retrieved = pack([quote_by_id[h['id']] for h in found['results']],
                                     plan['top_k'], plan['context_bytes'])
                    lexical = pack(baseline.search(probe.question, plan['top_k']),
                                   plan['top_k'], plan['context_bytes'])
                    results.append({'qid': probe.id, 'conversation': cid,
                        'memory': score([q.turn_id for q in retrieved], None, probe.gold, plan['top_k']),
                        'bm25': score([q.turn_id for q in lexical], None, probe.gold, plan['top_k'])})
                snapshot(output / 'memory' / arm / 'conversations' / cid, out / cid)
                record['arms'][arm] = {'ingest_seconds': elapsed, 'open': opened, 'capture': capture,
                    'usage': {k: usage[k] - before[k] for k in
                        ('encoder_ms', 'encoder_calls', 'encoder_texts', 'encoder_input_tokens',
                         'padded_input_tokens', 'llm_calls')}, 'quality': results}
            rows.append(record)
            write(output / 'rows.json', rows)
    write(output / 'complete.json', {'job': job, 'startup': startups, 'rows': rows})


def run_job(directory, plan, job):
    output = directory / 'source/.local-qualification/measurement' / job
    output.mkdir(parents=True, exist_ok=False)
    registry = output / 'server-registry'
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(registry),
        PYTHONPATH=os.pathsep.join([str(directory / 'source/v4/src'), str(directory / 'source/v4')]))
    command = [sys.executable, str(Path(__file__).resolve()), '_worker',
               '--plan-dir', str(directory), '--job', job]
    samples = []
    with (output / 'worker.log').open('x') as log:
        child = subprocess.Popen(command, env=env, cwd=output, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + plan['wall_seconds_per_job']
        try:
            while child.poll() is None:
                resources = sample(directory)
                resources['rss_bytes'] = rss_bytes(child, registry)
                resources['mps_driver_bytes'] = 0
                for p in (output / 'memory').glob('*/usage.json'):
                    resources['mps_driver_bytes'] += json.loads(p.read_text()).get('mps_driver_bytes', 0)
                samples.append(resources)
                if (resources['rss_bytes'] > plan['rss_bytes'] or
                        resources['mps_driver_bytes'] > plan['mps_driver_bytes'] or time.monotonic() > deadline):
                    raise ResourceStop('RSS/MPS/wall-time budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'complete.json').exists():
                raise RuntimeError('Worker failed; no restart/fallback')
        finally:
            try:
                cleanup(registry, env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)
                write(output / 'resource-samples.json', samples)


def equivalent(rows, config):
    groups = {}
    for r in rows:
        if r['cpu']['recall'] is not None:
            if r['mps']['recall'] is None:
                raise ValueError('Unpaired quality denominator')
            groups.setdefault(r['conversation'], []).append(r)
    output = {'clusters': len(groups), 'questions': sum(map(len, groups.values())), 'metrics': {}}
    keys = sorted(groups)
    for metric in ('recall', 'mrr'):
        def delta(rs):
            return mean(r['mps'][metric] - r['cpu'][metric] for r in rs)
        values = [r for g in groups.values() for r in g]
        interval = None
        if len(keys) >= config['minimum_clusters']:
            rng = random.Random(config['seed'])
            draws = sorted(delta([r for k in rng.choices(keys, k=len(keys)) for r in groups[k]])
                           for _ in range(config['bootstrap']))
            tail = (1 - config['ci']) / 2
            interval = [draws[math.floor(tail * len(draws))],
                        draws[min(len(draws) - 1, math.ceil((1 - tail) * len(draws)) - 1)]]
        output['metrics'][metric] = {'delta': delta(values) if values else None, 'ci': interval}
    output['equivalent'] = all(r['ci'] is not None and r['ci'][0] > -config['margin'] and
                              r['ci'][1] < config['margin'] for r in output['metrics'].values())
    return output


def compare(directory, plan):
    import numpy as np
    rows, pairs, quality, expected = {}, [], {}, {c['id']: c['probes'] for c in plan['selected']}
    roots = {j: directory / 'source/.local-qualification/measurement' / j for j in plan['jobs']}
    for j, root in roots.items():
        values = json.loads((root / 'complete.json').read_text())['rows']
        rows[j] = {r['id']: r for r in values}
        if len(values) != len(expected) or set(rows[j]) != set(expected):
            raise ValueError('Incomplete/duplicate conversation denominator')
    for cid in expected:
        seconds = {a: [] for a in ENCODING}
        for j, root in roots.items():
            r = rows[j][cid]
            a, b = (json.loads((root / arm / f'{cid}.json').read_text()) for arm in ENCODING)
            if a != b or r['arms']['cpu']['capture'] != r['arms']['mps']['capture']:
                raise ValueError('Items/provenance/receipt/capture difference')
            va, vb = (np.load(root / arm / f'{cid}.npy', allow_pickle=False) for arm in ENCODING)
            if (va.shape != vb.shape or not np.isfinite(va).all() or not np.isfinite(vb).all() or
                    any(np.max(abs(np.linalg.norm(v, axis=1) - 1)) > .02 for v in (va, vb))):
                raise ValueError('Non-finite/invalid vector geometry')
            q = {arm: {p['qid']: p for p in r['arms'][arm]['quality']} for arm in ENCODING}
            if any(len(r['arms'][arm]['quality']) != len(expected[cid]) or
                   set(q[arm]) != set(expected[cid]) for arm in ENCODING):
                raise ValueError('Incomplete/duplicate question denominator')
            for qid in expected[cid]:
                if q['cpu'][qid]['bm25'] != q['mps'][qid]['bm25']:
                    raise ValueError('Same-arm lexical reference changed')
                quality.setdefault(qid, []).append({'qid': qid, 'conversation': cid,
                    **{arm: q[arm][qid]['memory'] for arm in ENCODING}})
            for arm in ENCODING:
                if r['arms'][arm]['usage']['llm_calls']:
                    raise ValueError('LLM calls are forbidden')
                seconds[arm].append(r['arms'][arm]['ingest_seconds'])
        pairs.append({'id': cid, **{a: seconds[a] for a in ENCODING},
                      'median_speedup': median(seconds['cpu']) / median(seconds['mps'])})
    averaged = []
    for repeats in quality.values():
        if len(repeats) != 2:
            raise ValueError('Both AB/BA repetitions required')
        row = {'qid': repeats[0]['qid'], 'conversation': repeats[0]['conversation']}
        for arm in ENCODING:
            row[arm] = {m: None if repeats[0][arm][m] is None else mean(r[arm][m] for r in repeats)
                        for m in ('recall', 'mrr')}
        averaged.append(row)
    eq = equivalent(averaged, plan['equivalence'])
    times = sorted(t for p in pairs for t in p['mps'])
    perf = {'p50': median(times), 'p95_nearest_rank': times[math.ceil(.95 * len(times)) - 1],
            'max': max(times)}
    met = all(p['median_speedup'] >= plan['targets']['minimum_paired_speedup'] for p in pairs)
    met = met and perf['p50'] <= 300 and perf['p95_nearest_rank'] <= 450 and perf['max'] <= 600
    return {'complete': True, 'pairs': pairs, 'performance': perf, 'performance_met': met,
            'quality': eq, 'criterion_met': bool(met and eq['equivalent']), 'claim': plan['claim']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'run', 'compare', '_worker'])
    parser.add_argument('--plan-dir', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--job', choices=['ab', 'ba'])
    args = parser.parse_args(argv)
    clone, directory = Path(__file__).resolve().parents[3], args.plan_dir.resolve()
    if not directory.is_relative_to(clone / '.local-qualification'):
        raise ValueError('Only private clone qualification paths allowed')
    if args.operation == 'prepare':
        prepare(clone, directory, args.source_commit or '')
    elif args.operation == '_worker':
        plan = json.loads((directory / 'plan.json').read_text())
        asyncio.run(worker(plan, args.job, directory / 'source/.local-qualification/measurement' / args.job))
    else:
        plan = frozen(directory)
        if args.operation == 'run':
            def terminate(signum, frame):
                raise ResourceStop('Operator interrupted')
            signal.signal(signal.SIGTERM, terminate)
            ensure_priority(Limits())
            sample(directory)
            with (directory / 'started.json').open('x') as f:
                json.dump({'jobs_authorized': 2}, f)
            try:
                for job in plan['jobs']:
                    frozen(directory)
                    run_job(directory, plan, job)
                write(directory / 'report.json', compare(directory, plan))
            except BaseException as exc:
                write(directory / 'stopped.json', {'complete': False, 'reason': str(exc), 'restart': False})
                write(directory / 'report.json', {'complete': False, 'criterion_met': False, 'error': str(exc)})
                raise
        else:
            write(directory / 'report.json', compare(directory, plan))


if __name__ == '__main__':
    main()
