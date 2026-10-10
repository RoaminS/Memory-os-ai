"""One MPS arm versus frozen historical BEAM CPU; development equivalence only."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
from statistics import mean, median
import subprocess
import sys
import tarfile
import time
import uuid

from bench.e2e.baselines import BM25
from bench.e2e.beam import DATA_SHA, conversations
from bench.e2e.client import ToolClient, offline_env, project_for
from bench.e2e.data import sha256
from bench.e2e.guard import Limits, ResourceStop, ensure_priority, sample
from bench.e2e.leaderboard import AddSearchAdapter
from bench.e2e.pipeline import extract, pack
from bench.e2e.processes import cleanup
from bench.e2e.run import code_record, kill_group, model_record
from bench.e2e.stats import score

VERSION = 'mps-beam-reuse-v1'
CPU_SOURCE = '95276a8123838bfe9f7937f6325307d6be88ac39'
EQUIVALENCE = {'margin': .01, 'ci': .975, 'bootstrap': 10000,
               'seed': 20261010, 'minimum_clusters': 20}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines()]


def records(payload):
    from bench.e2e.indexing_campaign import archive_records
    return archive_records(payload)


def archive(clone, revision):
    from bench.e2e.indexing_campaign import git
    return git(clone, 'archive', revision, 'v4/src', 'v4/bench/e2e')


def runtime():
    from bench.e2e.indexing_mps_campaign import runtime as actual
    return actual()


def bridge_scores(bridge, job='ab', arm='mps'):
    p = bridge / 'source/.local-qualification/measurement' / job / 'complete.json'
    return [q for r in json.loads(p.read_text())['rows'] for q in r['arms'][arm]['quality']]


def numerics(bridge):
    """Read saved arrays only; no model or new inference."""
    import numpy as np
    output = []
    for job in ('ab', 'ba'):
        root = bridge / 'source/.local-qualification/measurement' / job
        for p in sorted((root / 'cpu').glob('*.npy')):
            q = root / 'mps' / p.name
            cpu, mps = [np.load(f, allow_pickle=False).astype(np.float64) for f in (p, q)]
            if cpu.shape != mps.shape or not np.isfinite(cpu).all() or not np.isfinite(mps).all():
                raise ValueError('Invalid saved numerical snapshot')
            a, b = np.linalg.norm(cpu, axis=1), np.linalg.norm(mps, axis=1)
            cosine = (cpu * mps).sum(axis=1) / (a * b)
            output.append({'job': job, 'conversation': p.stem, 'sha256': [sha256(p), sha256(q)],
                'cpu_max_norm_error': float(abs(a - 1).max()),
                'mps_max_norm_error': float(abs(b - 1).max()),
                'mean_cosine': float(cosine.mean()), 'min_cosine': float(cosine.min())})
    return output


def validate_reference(reference, bridge):
    """Never equate LME MiniLM/256 with BEAM Granite/512."""
    cpu = json.loads((reference / 'plan.json').read_text())
    receipt = json.loads((reference / 'receipt.json').read_text())
    report = json.loads((reference / 'retrieval/report.json').read_text())
    recent = json.loads((bridge / 'plan.json').read_text())
    if (cpu['version'] != 'beam-c4-retrieval-v2' or cpu['source_commit'] != CPU_SOURCE or
            receipt['plan_sha256'] != sha256(reference / 'plan.json') or
            report['plan_sha256'] != receipt['plan_sha256'] or not report['complete'] or
            report['questions'] != 400 or report['recall_questions'] != 355 or report['clusters'] != 20):
        raise ValueError('Only complete, frozen 400-question BEAM Granite CPU reference accepted')
    for name in ('predictions', 'contexts'):
        if report[name + '_sha256'] != sha256(reference / f'retrieval/{name}.jsonl'):
            raise ValueError('Historical prediction/context evidence changed')
    if (cpu['dataset_sha256'] != DATA_SHA or cpu['model_record'] != recent['model'] or
            recent['encoding']['cpu'] != {'device': 'cpu', 'embed_dtype': 'native',
                'embed_batch_size': 4, 'batch_facts': False} or recent['runtime'] != runtime() or
            cpu['model_record']['max_seq_length'] != 512 or
            recent['data_sha256'] != DATA_SHA):
        raise ValueError('Granite/config/runtime bridge mismatch')
    from bench.e2e.configuration import resolved_config
    current = resolved_config(cpu['model_record']['id'], '<private-store>',
                              profile='beam-c4-4.0.2', encoding=recent['encoding']['cpu'])
    if current.pop('embed_dtype') != 'native' or current.pop('batch_facts') is not False:
        raise ValueError('CPU bridge has changed precision/capture')
    if current != cpu['engine_config'] or any(cpu[k] != recent[k] for k in ('top_k', 'context_bytes', 'chunk_chars')):
        raise ValueError('Historical CPU configuration differs from bridge')
    if json.loads((bridge / 'receipt.json').read_text())['plan_sha256'] != sha256(bridge / 'plan.json'):
        raise ValueError('ABBA plan changed')
    values = lines(reference / 'retrieval/predictions.jsonl')
    by = {r['qid']: r for r in values}
    if (len(by) != 400 or len(values) != 400 or len({r['conversation'] for r in values}) != 20 or
            sum(r['memory']['recall'] is not None for r in values) != 355):
        raise ValueError('Invalid historical denominator')
    paths = [reference / 'plan.json', reference / 'receipt.json', reference / 'retrieval/report.json',
             reference / 'retrieval/predictions.jsonl', reference / 'retrieval/contexts.jsonl',
             bridge / 'plan.json', bridge / 'receipt.json']
    for job in ('ab', 'ba'):
        p = bridge / 'source/.local-qualification/measurement' / job / 'complete.json'
        observed = bridge_scores(bridge, job, 'cpu')
        gpu = bridge_scores(bridge, job)
        if (len(observed) != 60 or len({q['qid'] for q in observed}) != 60 or len(gpu) != 60 or
                {q['qid'] for q in observed} != {q['qid'] for q in gpu} or
                len({q['conversation'] for q in observed}) != 3):
            raise ValueError('Complete three-conversation CPU bridge required')
        for q in observed:
            if q['qid'] not in by or any(q[s] != by[q['qid']][s] for s in ('memory', 'bm25')):
                raise ValueError('Recent CPU scores differ from historical reference')
        baseline = {q['qid']: q for q in bridge_scores(bridge)}
        for q in gpu:
            if (q['conversation'] != by[q['qid']]['conversation'] or
                    q['bm25'] != by[q['qid']]['bm25'] or any(
                        q[s] != baseline[q['qid']][s] for s in ('memory', 'bm25'))):
                raise ValueError('MPS bridge is not repeatable or changes BM25')
        paths.append(p)
    return cpu, values, {str(p.resolve()): sha256(p) for p in paths}


def overlay(payload, usage_source):
    """Only device, model compute dtype and private counters change."""
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}
    replacements = {
        'v4/bench/e2e/configuration.py': (
            b'embed_model=model, device="cpu"', b'embed_model=model, device="mps"'),
        'v4/src/memory_os_v4/embed.py': (
            b'cached = SentenceTransformer(self.model_name, device=self.device)',
            b'cached = SentenceTransformer(self.model_name, device=self.device, '
            b'model_kwargs={"torch_dtype": torch.float32})')}
    for name, (old, new) in replacements.items():
        if files[name].count(old) != 1:
            raise ValueError('Archived source differs from reviewed overlay')
        files[name] = files[name].replace(old, new)
    files['v4/bench/e2e/usage.py'] = usage_source
    return files


def prepare(clone, directory, revision, reference, bridge):
    if directory.exists() or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Fresh plan and exact harness commit required')
    ensure_priority(Limits())
    sample(clone)
    import torch
    if not torch.backends.mps.is_available():
        raise ResourceStop('MPS unavailable; no fallback')
    cpu, values, proof = validate_reference(reference, bridge)
    harness_code = code_record()
    if records(archive(clone, revision)) != harness_code:
        raise ValueError('Harness source does not match commit')
    payload = archive(clone, CPU_SOURCE)
    if records(payload) != cpu['code_files']:
        raise ValueError('Historical CPU source archive differs')
    if sha256(Path(cpu['data'])) != DATA_SHA:
        raise ValueError('Dataset changed')
    model = model_record(cpu['model_record']['id'])
    model['max_seq_length'] = 512
    if model != cpu['model_record']:
        raise ValueError('Historical Granite snapshot changed')
    items = list(conversations(Path(cpu['data'])))
    if {p.id for c in items for p in c.probes} != {r['qid'] for r in values}:
        raise ValueError('Corpus and historical denominator differ')
    files = overlay(payload, (clone / 'v4/bench/e2e/usage.py').read_bytes())
    directory.mkdir(mode=0o700, parents=True)
    (directory / 'cpu-source.tar').write_bytes(payload)
    source = directory / 'source'
    for name, content in files.items():
        p = source / name
        if not p.resolve().is_relative_to(source):
            raise ValueError('Unsafe archive path')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    gpu_bridge = bridge_scores(bridge)
    bridge_ids = {r['conversation'] for r in gpu_bridge}
    # Reproduce the observed three first, failing closed before the other 17.
    ordered = [c for c in items if c.id in bridge_ids] + [c for c in items if c.id not in bridge_ids]
    selected = [{'id': c.id, 'source_id': c.source_id, 'probes': [p.id for p in c.probes]} for c in ordered]
    plan = {'version': VERSION, 'source_commit': revision, 'cpu_source_commit': CPU_SOURCE,
        'harness_code': harness_code, 'mps_encoding': {'device': 'mps', 'compute_dtype': 'float32',
            'max_seq_length': 512, 'episode_batch_size': 4, 'facts_scalar': True},
        'harness_sha256': sha256(Path(__file__)), 'archive_sha256': sha256(directory / 'cpu-source.tar'),
        'overlay_files': {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
        'historical_evidence': proof, 'cpu_reference': str(reference.resolve()), 'bridge': str(bridge.resolve()),
        'mps_bridge': {r['qid']: {s: r[s] for s in ('memory', 'bm25')} for r in gpu_bridge},
        'runtime': runtime(), 'data': cpu['data'], 'data_sha256': DATA_SHA, 'model': model,
        'cpu_config': cpu['engine_config'], 'selected': selected,
        'top_k': cpu['top_k'], 'context_bytes': cpu['context_bytes'], 'chunk_chars': cpu['chunk_chars'],
        'namespace': 'beam-c4-v1-capture', 'equivalence': EQUIVALENCE,
        'wall_seconds': 7200, 'conversation_seconds': 600,
        'rss_bytes': 4 * 1024**3, 'mps_driver_bytes': 2 * 1024**3,
        'jobs': 1, 'concurrency': 1, 'nice': 15, 'swap_bytes': 8_500_000_000, 'free_bytes': 5_000_000_000,
        'claim': 'observed BEAM development equivalence vs historical CPU; not contemporary paired '
                 'speed experiment, independent confirmation, answer accuracy or leaderboard rank'}
    write(directory / 'plan.json', plan)
    write(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json')})
    return plan


def frozen(directory):
    plan = json.loads((directory / 'plan.json').read_text())
    if (plan['version'] != VERSION or plan['harness_sha256'] != sha256(Path(__file__)) or
            sha256(directory / 'plan.json') != json.loads((directory / 'receipt.json').read_text())['plan_sha256'] or
            plan['equivalence'] != EQUIVALENCE or plan['runtime'] != runtime() or
            plan['harness_code'] != code_record()):
        raise ValueError('Frozen protocol/runtime changed')
    if sha256(directory / 'cpu-source.tar') != plan['archive_sha256']:
        raise ValueError('CPU source archive changed')
    found = {str(p.relative_to(directory / 'source')): sha256(p)
             for p in (directory / 'source/v4').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    if found != plan['overlay_files']:
        raise ValueError('Reviewed source overlay changed')
    for name, checksum in plan['historical_evidence'].items():
        if sha256(Path(name)) != checksum:
            raise ValueError('Historical evidence changed')
    if sha256(Path(plan['data'])) != plan['data_sha256']:
        raise ValueError('Dataset changed')
    record = model_record(plan['model']['id'])
    record['max_seq_length'] = 512
    if record != plan['model']:
        raise ValueError('Model changed')
    return plan


async def worker(plan, output):
    from bench.e2e.network import deny_ip_network
    from bench.e2e.configuration import resolved_config
    deny_ip_network()
    expected_config = dict(plan['cpu_config'], device='mps')
    if resolved_config(plan['model']['id'], '<private-store>', profile='beam-c4-4.0.2') != expected_config:
        raise ValueError('Archived MPS config differs beyond device')
    startup = time.monotonic()
    rows, captures = [], []
    async with ToolClient(output / 'memory', plan['model']['id'], resident=True,
                          config_profile='beam-c4-4.0.2') as client:
        startup_seconds = time.monotonic() - startup
        usage_path = output / 'memory/usage.json'
        usage = json.loads(usage_path.read_text())
        if plan['model']['id'] != 'hash' and (usage['model_device'] != 'mps' or
                                             usage['model_dtype'] != 'torch.float32'):
            raise RuntimeError('Actual MPS/float32 mismatch')
        adapter = AddSearchAdapter(client, plan['namespace'])
        with (output / 'predictions.jsonl').open('x') as predictions:
            corpus = {c.id: c for c in conversations(Path(plan['data']))}
            for chosen in plan['selected']:
                c = corpus[chosen['id']]
                (output / 'progress.json').write_text(json.dumps({'id': c.id, 'phase': 'start'}))
                opened = await client.call('eval_open_conversation', {'conversation_id': c.id})
                if not opened['store_empty'] or not opened['encoder_reused']:
                    raise RuntimeError('Resident isolation failed')
                t = time.monotonic()
                quotes = extract(project_for(adapter.namespace, c.id), c.turns, plan['chunk_chars'])
                capture = await adapter._capture(c.id, c.turns, quotes)
                elapsed = time.monotonic() - t
                if capture['deduped'] or capture['added'] != len(quotes):
                    raise ValueError('Lossless capture failed')
                captures.append({'id': c.id, 'ingest_seconds': elapsed, 'capture': capture,
                    'observable_quotes_sha256': hashlib.sha256(json.dumps(
                        [(q.id, q.turn_id, q.text) for q in quotes], ensure_ascii=False).encode()).hexdigest()})
                root = output / 'bm25' / c.id
                root.mkdir(parents=True)
                bm25 = BM25(quotes, root, plan['model']['id'])
                quote_by_id = {q.id: q for q in quotes}
                for probe in c.probes:
                    found = await adapter.search(c.id, probe.question, plan['top_k'])
                    memory = pack([quote_by_id[h['id']] for h in found['results']],
                                  plan['top_k'], plan['context_bytes'])
                    lexical = pack(bm25.search(probe.question, plan['top_k']), plan['top_k'], plan['context_bytes'])
                    row = {'qid': probe.id, 'conversation': c.id, 'category': probe.gold.category,
                        'memory': score([q.turn_id for q in memory], None, probe.gold, plan['top_k']),
                        'bm25': score([q.turn_id for q in lexical], None, probe.gold, plan['top_k'])}
                    prior = plan.get('mps_bridge', {}).get(probe.id)
                    if prior is not None and any(row[s] != prior[s] for s in ('memory', 'bm25')):
                        raise ValueError('Archived overlay differs from unchanged observed MPS candidate')
                    rows.append(row)
                    predictions.write(json.dumps(row, allow_nan=False) + '\n')
                    predictions.flush()
                write(output / 'captures.json', captures)
    write(output / 'complete.json', {'startup_seconds': startup_seconds, 'questions': len(rows),
        'captures': captures, 'usage': json.loads(usage_path.read_text())})


def compare(directory, plan):
    from bench.e2e.indexing_mps_campaign import equivalent
    root = directory / 'source/.local-qualification/measurement'
    actual = lines(root / 'predictions.jsonl')
    before = lines(Path(plan['cpu_reference']) / 'retrieval/predictions.jsonl')
    expected = {p for c in plan['selected'] for p in c['probes']}
    if (len(actual) != len(expected) or len(before) != len(expected) or
            {r['qid'] for r in actual} != expected or {r['qid'] for r in before} != expected):
        raise ValueError('Incomplete/duplicate question denominator')
    by = {r['qid']: r for r in before}
    pairs = []
    for r in actual:
        cpu = by[r['qid']]
        if cpu['conversation'] != r['conversation'] or cpu['bm25'] != r['bm25']:
            raise ValueError('Historical conversation/BM25 mismatch')
        if (cpu['memory']['recall'] is None) != (r['memory']['recall'] is None):
            raise ValueError('Historical evidence denominator mismatch')
        pairs.append({'qid': r['qid'], 'conversation': r['conversation'],
                      'cpu': cpu['memory'], 'mps': r['memory']})
    eq = equivalent(pairs, plan['equivalence'])
    done = json.loads((root / 'complete.json').read_text())
    captures = done['captures']
    wanted = {c['id'] for c in plan['selected']}
    if len(captures) != len(wanted) or {c['id'] for c in captures} != wanted or done['usage']['llm_calls']:
        raise ValueError('Incomplete capture or forbidden LLM calls')
    times = sorted(c['ingest_seconds'] for c in captures)
    perf = {'p50': median(times), 'p95_nearest_rank': times[math.ceil(.95 * len(times)) - 1],
            'max': max(times), 'historical_cpu_speedup_not_paired': True}
    met = perf['p50'] <= 300 and perf['p95_nearest_rank'] <= 450 and perf['max'] <= 600
    return {'complete': True, 'questions': len(actual), 'quality': eq, 'performance': perf,
            'predictions_sha256': sha256(root / 'predictions.jsonl'),
            'complete_sha256': sha256(root / 'complete.json'),
            'historical_predictions_sha256': sha256(Path(plan['cpu_reference']) / 'retrieval/predictions.jsonl'),
            'performance_met': met, 'criterion_met': bool(met and eq['equivalent']),
            'systems': {a: {metric: mean(r[a][metric] for r in pairs if r[a][metric] is not None)
                           for metric in ('recall', 'mrr')} for a in ('cpu', 'mps')}, 'claim': plan['claim']}


def run(directory, plan):
    from bench.e2e.indexing_campaign import rss_bytes
    ensure_priority(Limits())
    sample(directory)
    with (directory / 'started.json').open('x') as f:
        json.dump({'jobs_authorized': 1}, f)
    output = directory / 'source/.local-qualification/measurement'
    output.mkdir(parents=True)
    registry = output / 'server-registry'
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(registry),
        PYTORCH_ENABLE_MPS_FALLBACK='0', PYTORCH_MPS_HIGH_WATERMARK_RATIO='0.25',
        PYTORCH_MPS_LOW_WATERMARK_RATIO='0.20',
        PYTHONPATH=os.pathsep.join([str(directory / 'source/v4/src'), str(directory / 'source/v4')]))
    samples = []
    with (output / 'worker.log').open('x') as log:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '_worker',
            '--plan-dir', str(directory)], env=env, cwd=output, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        deadline, progress_time, last = time.monotonic() + plan['wall_seconds'], time.monotonic(), None
        try:
            while child.poll() is None:
                resources = sample(directory)
                resources['rss_bytes'] = rss_bytes(child, registry)
                p = output / 'memory/usage.json'
                resources['mps_driver_bytes'] = json.loads(p.read_text()).get('mps_driver_bytes', 0) if p.exists() else 0
                samples.append(resources)
                p = output / 'progress.json'
                progress = p.read_bytes() if p.exists() else None
                if progress != last:
                    progress_time, last = time.monotonic(), progress
                if (time.monotonic() > deadline or time.monotonic() - progress_time > plan['conversation_seconds'] or
                        resources['rss_bytes'] > plan['rss_bytes'] or resources['mps_driver_bytes'] > plan['mps_driver_bytes']):
                    raise ResourceStop('Frozen wall/conversation/RSS/MPS budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'complete.json').exists():
                raise RuntimeError('MPS worker failed; no restart')
            frozen(directory)
            write(directory / 'report.json', compare(directory, plan))
        except BaseException as exc:
            write(directory / 'stopped.json', {'complete': False, 'reason': str(exc), 'restart': False})
            write(directory / 'report.json', {'complete': False, 'criterion_met': False, 'error': str(exc)})
            raise
        finally:
            try:
                cleanup(registry, env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)
                write(output / 'resource-samples.json', samples)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['audit', 'prepare', 'run', 'compare', '_worker'])
    parser.add_argument('--plan-dir', type=Path)
    parser.add_argument('--source-commit')
    args = parser.parse_args(argv)
    clone = Path(__file__).resolve().parents[3]
    reference = clone / '.local-qualification/beam-c4-retrieval-v2'
    bridge = clone / '.local-qualification/indexing-mps-v2-runtime-fix'
    if args.operation == 'audit':
        cpu, values, proof = validate_reference(reference, bridge)
        print(json.dumps({'reference': cpu['source_commit'], 'questions': len(values),
            'bridge_questions': 60, 'bridge_orders': 2, 'evidence_sha256': proof,
            'saved_vector_numerics': numerics(bridge)}, indent=2))
        return
    if args.plan_dir is None:
        parser.error('--plan-dir required')
    directory = args.plan_dir.resolve()
    if not directory.is_relative_to(clone / '.local-qualification'):
        raise ValueError('Only private clone paths allowed')
    if args.operation == 'prepare':
        prepare(clone, directory, args.source_commit or '', reference, bridge)
    elif args.operation == '_worker':
        plan = json.loads((directory / 'plan.json').read_text())
        asyncio.run(worker(plan, directory / 'source/.local-qualification/measurement'))
    else:
        plan = frozen(directory)
        if args.operation == 'run':
            def stop(signum, frame):
                raise ResourceStop('Operator interrupted')
            signal.signal(signal.SIGTERM, stop)
            run(directory, plan)
        else:
            write(directory / 'report.json', compare(directory, plan))


if __name__ == '__main__':
    main()
