"""Frozen descriptive answer pilot. Retrieval offline; Codex subscription only.

No retries, API keys, official judge, held-out claims, or production stores.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
from statistics import mean
import subprocess
import sys
import time
import uuid

from .baselines import BM25
from .beam import CATEGORIES, DATA_SHA, conversations
from .beam_campaign import DATA, select_pilot
from .client import ToolClient, offline_env, project_for
from .codex_reader import (CodexReader, JUDGE_MODEL, MODEL, JUDGE_PROMPT, READER_PROMPT,
                           capability, parse_events, runtime_record)
from .configuration import resolved_config
from .data import sha256
from .experiments import MODELS, private_path
from .guard import Limits, ensure_priority, sample
from .leaderboard import AddSearchAdapter
from .pipeline import extract, pack
from .processes import cleanup
from .run import code_record, kill_group, model_record, verify_source, write_json

VERSION = 'answer-pilot-production-v2'
PROFILE = 'beam-c4-production-4.0.3'
RECOVERY_CHANGED_FILES = {'bench/e2e/answer_pilot.py', 'bench/e2e/codex_reader.py'}


def local_runtime():
    return {n: importlib.metadata.version(n) for n in ('torch', 'sentence-transformers', 'transformers', 'numpy')}


def select(items):
    """Ten categories, alternating two predetermined conversations; no scores."""
    cids, _ = select_pilot(items)
    by_id = {c.id: c for c in items}
    return cids, [min(p.id for p in by_id[cids[i % 2]].probes if p.gold.category == category)
                  for i, category in enumerate(CATEGORIES)]


def previous_evidence(clone, previous):
    """Revalidate one completed reader and immutable retrieval, never replay it."""
    _, previous = private_path(previous)
    original = json.loads((previous / 'plan.json').read_text())
    receipt = json.loads((previous / 'receipt.json').read_text())
    if (original['version'] != 'answer-pilot-production-v1' or 'historical' in original
            or sha256(previous / 'plan.json') != receipt['plan_sha256']):
        raise ValueError('Only the immutable first v1 infrastructure stop can be amended')
    verify_source(clone, original['source_commit'], original['code_files'])
    now = code_record()
    changed = {n for n in now if now[n] != original['code_files'].get(n)}
    if set(now) != set(original['code_files']) or not changed <= RECOVERY_CHANGED_FILES:
        raise ValueError('Recovery may change only the reader/harness; engine/retrieval unchanged')
    stopped = json.loads((previous / 'answers/stopped.json').read_text())
    usage_record = json.loads((previous / 'answers/calls/usage.json').read_text())
    if (stopped.get('reason') != 'Forbidden tool/event: isolation proof failed'
            or stopped.get('restart') is not False or stopped.get('cli_attempts') != 1
            or stopped.get('complete_calls') != 0 or usage_record.get('cli_attempts') != 1
            or usage_record.get('completed_calls') != []
            or list((previous / 'answers/calls').glob('*.events.jsonl')) != [previous / 'answers/calls/000.events.jsonl']
            or (previous / 'answers/predictions.jsonl').read_text().strip()):
        raise ValueError('Recovery requires exactly one stopped attempt, no judge/prediction/extra call')
    first, usage = parse_events((previous / 'answers/calls/000.events.jsonl').read_text(), 'answer')
    report = json.loads((previous / 'retrieval/report.json').read_text())
    if (report.get('complete') is not True or report.get('model_calls') != 0
            or report['plan_sha256'] != receipt['plan_sha256']
            or report['contexts_sha256'] != sha256(previous / 'retrieval/contexts.jsonl')
            or report['encoder_sha256'] != sha256(previous / 'retrieval/encoder-attestation.json')):
        raise ValueError('Historical retrieval incomplete or modified')
    contexts = [json.loads(line) for line in (previous / 'retrieval/contexts.jsonl').read_text().splitlines()]
    if len(contexts) != 10 or {r['qid'] for r in contexts} != set(original['question_ids']):
        raise ValueError('Historical contexts denominator changed')
    qid, arm = original['question_ids'][0], original['arms'][0]
    ctx = next(r for r in contexts if r['qid'] == qid)
    prompt = original['prompts']['reader'] + json.dumps({'question': ctx['question'], 'observations': ctx['contexts'][arm]}, ensure_ascii=False)
    import hashlib
    record = {'index': 0, 'kind': 'answer', 'model': original['reader']['model'],
              'effort': original['reader']['effort'], 'usage': usage, 'accepted': True,
              'seconds': 90, 'seconds_is_reserved_upper_bound': True,
              'events_sha256': sha256(previous / 'answers/calls/000.events.jsonl'),
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'prompt_attestation': 'Reconstructed from frozen source/plan/contexts; original temporary input was not persisted',
              'origin': 'revalidated historical completed reader; not a new CLI call'}
    files = ('plan.json', 'receipt.json', 'retrieval/report.json', 'retrieval/contexts.jsonl',
             'retrieval/encoder-attestation.json', 'answers/stopped.json', 'answers/predictions.jsonl',
             'answers/calls/usage.json', 'answers/calls/000.events.jsonl', 'answers/calls/000.stderr')
    historical = {'directory': str(previous), 'source_commit': original['source_commit'],
                  'files_sha256': {str(previous / f): sha256(previous / f) for f in files},
                  'qid': qid, 'arm': arm, 'answer': first, 'call': record,
                  'previous_attempts': 1, 'new_calls_max': 39, 'reserved_cli_seconds': 90,
                  'note': 'One response already observed; descriptive only. No restart of the old directory.'}
    return original, historical


def prepare(directory, revision, copy_proof, previous=None):
    clone, directory = private_path(directory)
    if directory.exists():
        raise ValueError('Fresh pilot directory required, no restart')
    verify_source(clone, revision, code_record())
    original, historical = previous_evidence(clone, previous) if previous is not None else (None, None)
    _, copy_proof = private_path(copy_proof)
    copied = json.loads(copy_proof.read_text())
    if (copied.get('ok') is not True or copied.get('version') != '4.0.3'
            or copied.get('source_commit') != (original['source_commit'] if original else revision)
            or copied.get('production_modified') is not False):
        raise ValueError('Matching installed production-copy attestation required before pilot')
    if sha256(DATA) != DATA_SHA:
        raise ValueError('Observed BEAM dataset changed')
    caps = [capability('low'), capability('high', model=JUDGE_MODEL)]
    if not all(c['supported'] for c in caps):
        raise ValueError('Frozen models unavailable; no substitution or model calls')
    convs, ids = select(list(conversations(DATA)))
    record = model_record(str(MODELS['granite']))
    record.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                  batch_size=32, max_seq_length=512)
    plan = {'version': VERSION, 'source_commit': revision, 'code_files': code_record(),
            'data': str(DATA), 'dataset_sha256': DATA_SHA, 'data_status': 'BEAM 100K already observed; NOT future held-out',
            'conversations': convs, 'question_ids': ids, 'selection': 'one per category, alternating two hash-selected conversations; no outcomes',
            'encoder': record, 'runtime': local_runtime(), 'engine_config': resolved_config(record['id'], '<private-store>', profile=PROFILE),
            'copy_proof': str(copy_proof), 'copy_proof_sha256': sha256(copy_proof),
            'production_encoding_profile': copied['encoding_profile'],
            'encoding_contract': {'device': 'mps', 'dtype_policy': 'native', 'batch_size': 32, 'max_seq_length': 512},
            'deviations': 'No recency inferred from annotation/wall clock; quote-only capture, supersession confined at 1.01, same-arm BM25. Production encoding settings preserved.',
            'cli': runtime_record(), 'reader': {'model': MODEL, 'effort': 'low'},
            'judge': {'model': JUDGE_MODEL, 'effort': 'high', 'not_published_gpt4o_judge': True},
            'prompts': {'reader': READER_PROMPT, 'judge': JUDGE_PROMPT},
            'arms': ['memory', 'bm25'], 'order': 'alternate arm order per frozen question; anonymous judge, no arm labels',
            'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024, 'ingest_bytes': 16_000_000,
            'caps': {'questions': 10, 'cli_calls': 40, 'cli_seconds': 3600, 'reader_call_seconds': 90,
                     'judge_call_seconds': 180, 'retrieval_seconds': 1200, 'prompt_bytes': 24000,
                     'observed_tokens_stop_threshold': 400000, 'tokens_hard_cap': False,
                     'incremental_api_usd': 0, 'concurrency': 1, 'nice': 15,
                     'swap_bytes': 8500000000, 'free_bytes': 5000000000, 'downloads': 0},
            'feasibility': 'Complete exactly 20 reader + 20 judge calls, valid JSON, no tool/refusal/error/quota stop; measure duration/usage. Any failure stops and preserves evidence. No retries.',
            'claim': 'Descriptive feasibility/accuracy only; no CI, ranking, superiority or comparison to published judge scores',
            'quota': 'Unknown remaining quota; usage tokens are NOT quota percentage; any limit/error stops, no API fallback'}
    validate_encoder({'encoding_profile': copied['encoding_profile']}, plan)
    if original is not None:
        invariant = set(plan) - {'version', 'source_commit', 'code_files'}
        if any(plan[k] != original.get(k) for k in invariant):
            raise ValueError('Recovery cannot change questions/models/prompts/caps/config/copy/CLI/runtime')
        validate_encoder({'encoding_profile': json.loads((Path(historical['directory']) / 'retrieval/encoder-attestation.json').read_text())}, plan)
        plan['historical'] = historical
        # A second amended directory from the same stopped pilot would violate
        # the global quota cap. Keep this exclusive ledger outside v1 evidence.
        ledger = clone / '.local-qualification' / ('answer-pilot-recovery-' + sha256(Path(historical['directory']) / 'plan.json') + '.json')
        if ledger.exists():
            raise ValueError('Historical pilot already amended; no second quota budget')
    directory.mkdir(mode=0o700)
    write_json(directory / 'plan.json', plan)
    write_json(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json'),
                                          'prepared_before_inference': historical is None,
                                          'prepared_before_further_inference': True})
    if historical is not None:
        with ledger.open('x') as f:
            json.dump({'new_directory': str(directory), 'plan_sha256': sha256(directory / 'plan.json')}, f)
        rdir = directory / 'retrieval'
        rdir.mkdir(mode=0o700)
        for name in ('contexts.jsonl', 'encoder-attestation.json'):
            shutil.copyfile(Path(historical['directory']) / 'retrieval' / name, rdir / name)
        report = json.loads((Path(historical['directory']) / 'retrieval/report.json').read_text())
        report.update(plan_sha256=sha256(directory / 'plan.json'),
                      reused_from=historical['directory'], new_encoder_runs=0,
                      original_report_sha256=sha256(Path(historical['directory']) / 'retrieval/report.json'))
        write_json(rdir / 'report.json', report)
        write_json(directory / 'historical-usage.json', historical)
    return plan


def frozen(directory, *, check_cli=True):
    clone, directory = private_path(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if plan['version'] != VERSION or sha256(directory / 'plan.json') != receipt['plan_sha256']:
        raise ValueError('Frozen protocol changed')
    verify_source(clone, plan['source_commit'], code_record())
    if code_record() != plan['code_files'] or sha256(Path(plan['data'])) != plan['dataset_sha256']:
        raise ValueError('Source/data changed')
    if sha256(Path(plan['copy_proof'])) != plan['copy_proof_sha256']:
        raise ValueError('Production-copy attestation changed')
    if any(sha256(Path(p)) != sha for p, sha in plan.get('historical', {}).get('files_sha256', {}).items()):
        raise ValueError('Preserved historical evidence changed; no further calls')
    observed = model_record(plan['encoder']['id'])
    observed.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                    batch_size=32, max_seq_length=512)
    if observed != plan['encoder'] or local_runtime() != plan['runtime']:
        raise ValueError('Model/dependency runtime changed')
    if resolved_config(observed['id'], '<private-store>', profile=PROFILE) != plan['engine_config']:
        raise ValueError('Encoding/retrieval configuration changed')
    if check_cli and runtime_record() != plan['cli']:
        raise ValueError('CLI changed since preregistration')
    return plan


def validate_encoder(status, plan):
    profile = status['encoding_profile']
    if (profile['device'] != 'mps' or profile['dtype'] != 'native' or profile['batch_size'] != 32
            or profile['max_seq_length'] != 512 or profile['parameter_devices'] != ['mps']
            or not profile['parameter_dtypes'] or profile['runtime'] != plan['runtime']
            or profile['model_revision'] != Path(plan['encoder']['id']).name):
        raise ValueError('Production encoding profile not attested; no CPU fallback or inference with unknown dtype')
    if 'production_encoding_profile' in plan and profile != plan['production_encoding_profile']:
        raise ValueError('Pilot and installed production-copy encoding profiles differ')
    return profile


async def retrieval(plan, output):
    from .network import deny_ip_network
    deny_ip_network()
    rows, timings = [], []
    started = time.monotonic()
    async with ToolClient(output / 'memory', plan['encoder']['id'], resident=True, config_profile=PROFILE) as client:
        profile = validate_encoder(await client.call('memory_status', {}), plan)
        write_json(output / 'encoder-attestation.json', profile)  # BEFORE retrieval/answers
        adapter = AddSearchAdapter(client, 'beam-c4-v1-capture')
        for c in conversations(Path(plan['data']), max_row=plan.get('beam_max_row', 8_000_000),
                           max_nodes=plan.get('beam_max_nodes', 100_000)):
            if c.id not in plan['conversations']:
                continue
            opened = await client.call('eval_open_conversation', {'conversation_id': c.id})
            if not opened['store_empty'] or not opened['encoder_reused']:
                raise RuntimeError('Conversation isolation/residency failed')
            t = time.monotonic()
            quotes = extract(project_for(adapter.namespace, c.id), c.turns, plan['chunk_chars'])
            if sum(len(q.text.encode()) for q in quotes) > plan['ingest_bytes']:
                raise RuntimeError('Ingest byte cap exceeded')
            captured = await adapter._capture(c.id, c.turns, quotes)
            if captured['deduped'] or captured['added'] != len(quotes):
                raise RuntimeError('Lossless same-arm capture failed')
            by_id = {q.id: q for q in quotes}
            baseline_root = output / 'bm25' / c.id
            baseline_root.mkdir(parents=True)
            bm25 = BM25(quotes, baseline_root, plan['encoder']['id'])
            timings.append({'conversation': c.id, 'ingest_seconds': time.monotonic() - t})
            for probe in c.probes:
                if probe.id not in plan['question_ids']:
                    continue
                found = await adapter.search(c.id, probe.question, plan['top_k'])
                ranks = [by_id[h['id']] for h in found['results']]
                # Never pass category, evidence, answer or rubric to the reader.
                rows.append({'qid': probe.id, 'question': probe.question,
                             'contexts': {s: [q.text for q in pack(r, plan['top_k'], plan['context_bytes'])]
                                          for s, r in [('memory', ranks), ('bm25', bm25.search(probe.question, plan['top_k']))]}})
    if len(rows) != len(plan['question_ids']) or {r['qid'] for r in rows} != set(plan['question_ids']):
        raise RuntimeError('Incomplete pilot retrieval denominator')
    with (output / 'contexts.jsonl').open('x') as f:
        for row in rows:
            f.write(json.dumps(row) + '\n')
    write_json(output / 'report.json', {'complete': True, 'seconds': time.monotonic() - started, 'timings': timings,
                                       'contexts_sha256': sha256(output / 'contexts.jsonl'),
                                       'encoder_sha256': sha256(output / 'encoder-attestation.json'), 'model_calls': 0,
                                       'plan_sha256': sha256(output.parent / 'plan.json')})


def run_retrieval(directory):
    clone, directory = private_path(directory)
    plan = frozen(directory)
    ensure_priority(Limits())
    sample(directory)
    output = directory / 'retrieval'
    output.mkdir(mode=0o700, exist_ok=False)
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(output / 'server-registry'),
               PYTHONPATH=os.pathsep.join([str(clone / 'v4'), str(clone / 'v4/src')]))
    deadline = time.monotonic() + plan['caps']['retrieval_seconds']
    with (output / 'worker.log').open('x') as log:
        child = subprocess.Popen([sys.executable, '-m', 'bench.e2e.answer_pilot', '_retrieve', '--plan-dir', str(directory)],
                                 cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                sample(directory)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Frozen retrieval budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'report.json').is_file():
                raise RuntimeError('Retrieval failed; no restart')
            frozen(directory)
        except BaseException as exc:
            write_json(output / 'stopped.json', {'complete': False, 'reason': str(exc), 'restart': False})
            raise
        finally:
            try:
                cleanup(output / 'server-registry', env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)


def read_and_judge(directory):
    _, directory = private_path(directory)
    plan = frozen(directory)
    ensure_priority(Limits())
    sample(directory)
    rdir = directory / 'retrieval'
    report = json.loads((rdir / 'report.json').read_text())
    if (not report['complete'] or report['plan_sha256'] != sha256(directory / 'plan.json')
            or report['contexts_sha256'] != sha256(rdir / 'contexts.jsonl')
            or report['encoder_sha256'] != sha256(rdir / 'encoder-attestation.json')):
        raise ValueError('Retrieval evidence changed/incomplete')
    validate_encoder({'encoding_profile': json.loads((rdir / 'encoder-attestation.json').read_text())}, plan)
    contexts = [json.loads(line) for line in (rdir / 'contexts.jsonl').read_text().splitlines()]
    if len(contexts) != len(plan['question_ids']) or {r['qid'] for r in contexts} != set(plan['question_ids']):
        raise ValueError('Incomplete/duplicate pilot contexts')
    by_id = {r['qid']: r for r in contexts}
    golds = {p.id: p for c in conversations(Path(plan['data'])) for p in c.probes if p.id in by_id}
    if any(by_id[q]['question'] != golds[q].question for q in by_id):
        raise ValueError('Question mismatch')
    output = directory / 'answers'
    output.mkdir(mode=0o700, exist_ok=False)  # no rerun of paid/quota calls
    runner = CodexReader(output / 'calls', 'low', lambda: sample(directory), seconds=3600,
                         max_calls=40, max_tokens=400000, judge_model=JUDGE_MODEL, judge_effort='high')
    historical = plan.get('historical')
    if historical:
        runner.seed(historical['call'])
        runner.deadline -= historical['reserved_cli_seconds']
    results = []
    try:
        with (output / 'predictions.jsonl').open('x') as stream:
            for i, qid in enumerate(plan['question_ids']):
                probe, ctx = golds[qid], by_id[qid]
                for system in (plan['arms'] if i % 2 == 0 else list(reversed(plan['arms']))):
                    answer = (historical['answer'] if historical and qid == historical['qid'] and system == historical['arm']
                              else runner.answer(probe.question, ctx['contexts'][system]))
                    correct = runner.judge(probe.question, answer, probe.gold.answers[0], list(probe.rubric))
                    row = {'qid': qid, 'arm': system, 'answer': answer, 'correct': correct, 'category': probe.gold.category}
                    results.append(row)
                    stream.write(json.dumps(row) + '\n')
                    stream.flush()
        frozen(directory)
        if len(results) != 20 or len(runner.calls) != 40:
            raise RuntimeError('Incomplete paired answer pilot')
        write_json(output / 'report.json', {'complete': True, 'questions': 10, 'model_calls': len(runner.calls),
                   'reader_calls': 20, 'judge_calls': 20, 'tokens': runner.tokens,
                   'new_cli_calls': runner.attempts - (1 if historical else 0),
                   'historical_reader_calls': 1 if historical else 0,
                   'historical_cli_seconds_reserved': historical['reserved_cli_seconds'] if historical else 0,
                   'cli_seconds': sum(c['seconds'] for c in runner.calls), 'quota_remaining': None,
                   'accuracy': {s: mean(r['correct'] for r in results if r['arm'] == s) for s in plan['arms']},
                   'api_usd': 0, 'ci95': None, 'claim': plan['claim'], 'models': [plan['reader'], plan['judge']],
                   'plan_sha256': sha256(directory / 'plan.json'), 'predictions_sha256': sha256(output / 'predictions.jsonl')})
    except BaseException as exc:
        write_json(output / 'stopped.json', {'complete': False, 'reason': str(exc), 'cli_attempts': runner.attempts,
                                            'complete_calls': len(runner.calls), 'tokens_known': runner.tokens,
                                            'restart': False, 'api_fallback': False})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'retrieve', 'answer', '_retrieve'])
    parser.add_argument('--plan-dir', required=True, type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--copy-proof', type=Path)
    parser.add_argument('--previous-plan', type=Path,
                        help='Explicit v1 infrastructure-stop amendment; reuse retrieval and first completed reader')
    args = parser.parse_args(argv)
    _, directory = private_path(args.plan_dir)
    if args.operation == 'prepare':
        if not args.source_commit or not args.copy_proof:
            parser.error('--source-commit and --copy-proof required')
        prepare(directory, args.source_commit, args.copy_proof, args.previous_plan)
    elif args.operation == 'retrieve':
        run_retrieval(directory)
    elif args.operation == '_retrieve':
        asyncio.run(retrieval(frozen(directory, check_cli=False), directory / 'retrieval'))
    else:
        read_and_judge(directory)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
