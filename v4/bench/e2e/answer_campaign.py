"""Held-out paired E2E campaign, subscription only; explicit quota/batch resume.

No API, automatic retry, partial-denominator claim or mutable frozen plan.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid

from .answer_pilot import PROFILE, local_runtime, retrieval, validate_encoder
from .beam import DATA_SHA, REVISION, S_SHA, conversations
from .beam_campaign import DATA, S_DATA
from .client import offline_env
from .codex_reader import (CodexReader, JUDGE_MODEL, MODEL, JUDGE_PROMPT, READER_PROMPT,
                           capability, runtime_record)
from .configuration import resolved_config
from .data import sha256
from .experiments import MODELS, private_path
from .guard import Limits, ensure_priority, sample
from .heldout_audit import MAX_NODES, MAX_ROW, screening, selection
from .processes import cleanup
from .run import code_record, kill_group, model_record, verify_source, write_json
from .stats import estimate

VERSION = 'heldout-answer-beam-500k-v1'
SEED = 20261011
PROTOCOL = Path(__file__).resolve().parents[2] / 'docs/ANSWER_HELDOUT_PROTOCOL_v1.json'


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def engine_matches_copy(clone, copied):
    current = {p: v for p, v in code_record().items() if p.startswith('src/')}
    for prefix in ([ 'git', '-C', str(clone)],
                   ['git', '--git-dir', str(clone / '.local-qualification/git-state')],
                   ['git', '--git-dir', str(clone / '.local-qualification/lexical-dev-git')]):
        result = subprocess.run([*prefix, 'archive', copied['source_commit'], 'v4/src'],
                                cwd=clone, capture_output=True, timeout=10)
        if result.returncode == 0:
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                old = {m.name.removeprefix('v4/'): hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                       for m in archive if m.isfile() and m.name.endswith('.py')}
            if current != old:
                raise ValueError('Engine differs from the qualified installed 4.0.3 copy')
            return
    raise ValueError('Cannot attest qualified engine source')


def prepare(directory, source_commit, data, provenance, copy_proof):
    clone, directory = private_path(directory)
    _, data = private_path(data)
    _, provenance = private_path(provenance)
    _, copy_proof = private_path(copy_proof)
    if directory.exists():
        raise ValueError('Fresh plan directory required')
    ensure_priority(Limits())
    sample(clone)
    verify_source(clone, source_commit, code_record())
    protocol = json.loads(PROTOCOL.read_text())
    origin = json.loads(provenance.read_text())
    if (origin.get('dataset') != 'Mohammadta/BEAM' or origin.get('revision') != REVISION
            or origin.get('file') != 'data/500K-00000-of-00001.parquet'
            or origin.get('licence') != 'CC-BY-SA-4.0' or not origin.get('romain_download_go')
            or origin.get('converter_script_sha256') != sha256(clone / 'scripts/acquire_beam_500k.py')
            or origin.get('conversations') != 35 or origin.get('jsonl_sha256') != sha256(data)
            or not 0 < origin.get('download_bytes', 0) <= 110_000_000
            or sha256(provenance.parent / 'beam_500k.parquet') != origin.get('parquet_sha256')):
        raise ValueError('Approved bounded BEAM 500K provenance required')
    if sha256(DATA) != DATA_SHA or sha256(S_DATA) != S_SHA:
        raise ValueError('Observed comparison corpus changed')
    # All screening/selection happens before retrieval and before any reader.
    audit = screening(data, DATA, S_DATA, check=lambda: sample(clone))
    directory.mkdir(mode=0o700)
    write_json(directory / 'audit.json', audit)
    try:
        selected = selection(data, audit)
    except ValueError as exc:
        write_json(directory / 'REFUSED.json', {'reason': str(exc), 'inference_calls': 0,
                                               'new_corpus_go_required': True})
        raise
    copied = json.loads(copy_proof.read_text())
    if (copied.get('ok') is not True or copied.get('version') != '4.0.3'
            or copied.get('production_modified') is not False):
        raise ValueError('Qualified production-copy proof required')
    engine_matches_copy(clone, copied)
    if not capability('low')['supported'] or not capability('high', model=JUDGE_MODEL)['supported']:
        raise ValueError('Frozen models unavailable; no substitution')
    model = model_record(str(MODELS['granite']))
    model.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                 batch_size=32, max_seq_length=512)
    plan = {'version': VERSION, 'source_commit': source_commit, 'code_files': code_record(),
            'protocol': protocol, 'protocol_sha256': sha256(PROTOCOL),
            'data': str(data), 'dataset_sha256': sha256(data),
            'provenance': str(provenance), 'provenance_sha256': sha256(provenance),
            'audit_sha256': sha256(directory / 'audit.json'), 'observed_files': audit['files'],
            'selected': selected, 'question_ids': [r['qid'] for r in selected],
            'conversations': sorted({r['conversation'] for r in selected}),
            'encoder': model, 'runtime': local_runtime(),
            'engine_config': resolved_config(model['id'], '<private-store>', profile=PROFILE),
            'copy_proof': str(copy_proof), 'copy_proof_sha256': sha256(copy_proof),
            'production_encoding_profile': copied['encoding_profile'],
            'cli': runtime_record(), 'reader': {'model': MODEL, 'effort': 'low'},
            'judge': {'model': JUDGE_MODEL, 'effort': 'high'},
            'prompts': {'reader': READER_PROMPT, 'judge': JUDGE_PROMPT},
            'arms': ['memory', 'bm25'], 'top_k': 10, 'context_bytes': 12000,
            'chunk_chars': 1024, 'ingest_bytes': 80_000_000,
            'beam_max_row': MAX_ROW, 'beam_max_nodes': MAX_NODES,
            'caps': protocol['caps'], 'claim': protocol['claim'],
            'tasks': len(selected) * 4, 'max_attempts': len(selected) * 4 + 200}
    validate_encoder({'encoding_profile': copied['encoding_profile']}, plan)
    atomic_json(directory / 'plan.json', plan)
    atomic_json(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json'),
                                           'frozen_before_retrieval_and_inference': True})
    return plan


def frozen(directory, check_cli=True):
    clone, directory = private_path(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if plan.get('version') != VERSION or sha256(directory / 'plan.json') != receipt['plan_sha256']:
        raise ValueError('Frozen plan modified')
    verify_source(clone, plan['source_commit'], code_record())
    if plan['code_files'] != code_record() or sha256(PROTOCOL) != plan['protocol_sha256']:
        raise ValueError('Frozen source/protocol changed')
    for path, expected in {**plan['observed_files'], plan['data']: plan['dataset_sha256'],
                           plan['provenance']: plan['provenance_sha256'],
                           plan['copy_proof']: plan['copy_proof_sha256'],
                           str(directory / 'audit.json'): plan['audit_sha256']}.items():
        if sha256(Path(path)) != expected:
            raise ValueError('Frozen evidence changed')
    model = model_record(plan['encoder']['id'])
    model.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                 batch_size=32, max_seq_length=512)
    if model != plan['encoder'] or local_runtime() != plan['runtime']:
        raise ValueError('Frozen encoder/runtime changed')
    if resolved_config(model['id'], '<private-store>', profile=PROFILE) != plan['engine_config']:
        raise ValueError('Frozen production profile changed')
    if check_cli and runtime_record() != plan['cli']:
        raise ValueError('CLI changed; amendment required, never silently resume')
    return plan


def retrieve(directory):
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
        child = subprocess.Popen([sys.executable, '-m', 'bench.e2e.answer_campaign', '_retrieve',
                                  '--plan-dir', str(directory)], cwd=output, env=env,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                sample(directory)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Frozen retrieval budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'report.json').exists():
                raise RuntimeError('Retrieval incomplete; no automatic restart')
            frozen(directory)
        except BaseException as exc:
            atomic_json(output / 'stopped.json', {'reason': str(exc), 'restart': False})
            raise
        finally:
            try:
                cleanup(output / 'server-registry', env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)


def contexts(directory, plan):
    root = directory / 'retrieval'
    report = json.loads((root / 'report.json').read_text())
    if (report.get('complete') is not True or report['plan_sha256'] != sha256(directory / 'plan.json')
            or report['contexts_sha256'] != sha256(root / 'contexts.jsonl')
            or report['encoder_sha256'] != sha256(root / 'encoder-attestation.json')):
        raise ValueError('Retrieval incomplete/changed')
    validate_encoder({'encoding_profile': json.loads((root / 'encoder-attestation.json').read_text())}, plan)
    rows = [json.loads(line) for line in (root / 'contexts.jsonl').read_text().splitlines()]
    if len(rows) != len(plan['question_ids']) or {r['qid'] for r in rows} != set(plan['question_ids']):
        raise ValueError('Retrieval denominator changed')
    return {r['qid']: r for r in rows}


def quota_rejection(events):
    """Narrow pre-generation limit allowlist; unknown failure is NOT resumable.

    Usage remains unknown, never invented as zero. No tools/answer/reasoning,
    completed turn, or other startup error may accompany this rejection.
    """
    try:
        rows = [json.loads(line) for line in events.splitlines()]
    except ValueError:
        return False
    seen, thread, turn = False, False, False
    for row in rows:
        if not isinstance(row, dict):
            return False
        kind = row.get('type')
        if kind == 'thread.started':
            if thread or turn or seen or not set(row) <= {'type', 'thread_id'}:
                return False
            thread = True
            continue
        if kind == 'turn.started':
            if turn or seen or set(row) != {'type'}:
                return False
            turn = True
            continue
        if kind == 'error':
            if not set(row) <= {'type', 'message', 'code'}:
                return False
            error = row
        elif kind == 'turn.failed':
            if set(row) != {'type', 'error'} or not isinstance(row['error'], dict):
                return False
            error = row['error']
            if not set(error) <= {'message', 'code'}:
                return False
        else:
            return False
        message = error.get('message', '')
        if (not isinstance(message, str) or not message.startswith("You've hit your usage limit")
                or error.get('code') not in {None, 'usage_limit_reached'}):
            return False
        seen = True
    return seen


class Ledger:
    """Owned SQLite durable receipts; no lost/uncertain attempt ever replayed."""
    def __init__(self, directory, plan_sha):
        self.root = directory
        self.db = sqlite3.connect(directory / 'campaign.sqlite3', timeout=0)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);'
                             'CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, task TEXT, state TEXT, '
                             'value TEXT, usage TEXT, seconds REAL, files TEXT);')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('plan', plan_sha))
            self.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('status', 'new'))
        if self.meta('plan') != plan_sha:
            raise ValueError('Ledger belongs to another frozen plan')

    def meta(self, key):
        return self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()[0]

    def status(self, value):
        with self.db:
            self.db.execute('UPDATE meta SET value=? WHERE key=?', (value, 'status'))

    def validated(self):
        rows = self.db.execute('SELECT id,task,state,value,usage,seconds,files FROM attempts ORDER BY id').fetchall()
        for _, _, state, _, _, _, files in rows:
            if state in {'pending', 'blocked'}:
                raise ValueError('Uncertain/blocked attempt: no replay; operator review required')
            inventory = json.loads(files)
            if not inventory or any(sha256(self.root / p) != h for p, h in inventory.items()):
                raise ValueError('Call receipt artifacts changed')
        return rows

    def begin(self, task):
        with self.db:
            result = self.db.execute('INSERT INTO attempts (task,state) VALUES (?,?)', (task, 'pending'))
        return result.lastrowid

    def finish(self, index, state, value, usage, seconds, root):
        inventory = {str(p.relative_to(self.root)): sha256(p) for p in sorted(root.iterdir()) if p.is_file()}
        # Logs/prompt/usage durable BEFORE committing the result receipt.
        for p in root.iterdir():
            if p.is_file():
                with p.open('rb') as f:
                    os.fsync(f.fileno())
        for folder in (root, root.parent, self.root):
            descriptor = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with self.db:
            self.db.execute('UPDATE attempts SET state=?,value=?,usage=?,seconds=?,files=? WHERE id=?',
                            (state, json.dumps(value), json.dumps(usage), seconds, json.dumps(inventory), index))

    def close(self):
        self.db.close()


def accounting(rows):
    known = [json.loads(r[4]) for r in rows if r[4] and json.loads(r[4]) is not None]
    return {'cli_attempts': len(rows), 'accepted_calls': sum(r[2] == 'complete' for r in rows),
            'quota_rejections': sum(r[2] == 'quota' for r in rows),
            'unknown_usage_attempts': len(rows) - len(known),
            'known_input_tokens': sum(u['input_tokens'] for u in known),
            'known_output_tokens': sum(u['output_tokens'] for u in known),
            'known_cached_input_tokens': sum(u['cached_input_tokens'] for u in known),
            'known_total_tokens': sum(u['input_tokens'] + u['output_tokens'] for u in known),
            'cli_seconds': sum(r[5] or 0 for r in rows), 'quota_remaining': None,
            'api_usd': 0, 'tokens_are_not_quota_percent': True}


def tasks(plan):
    for i, qid in enumerate(plan['question_ids']):
        for arm in (plan['arms'] if i % 2 == 0 else list(reversed(plan['arms']))):
            for kind in ('answer', 'correct'):
                yield f'{qid}/{arm}/{kind}', qid, arm, kind


def execute_batch(directory, plan, ctx, golds, batch_calls, resume, runner_factory=CodexReader, check=None,
                  payload_builder=None):
    """Injectable lightweight runner for tests; production always CodexReader."""
    check = check or (lambda: sample(directory))
    check()
    lock = directory / 'answer.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Concurrent/uncertain process lock exists; inspect before any resume') from exc
    os.close(descriptor)
    ledger = None
    try:
        ledger = Ledger(directory, sha256(directory / 'plan.json'))
        rows = ledger.validated()
        state = ledger.meta('status')
        if resume != (state in {'quota', 'batch'}):
            raise ValueError('Explicit --resume required only after a quota/batch stop')
        if state not in {'new', 'quota', 'batch'}:
            raise ValueError('Blocked/running/completed campaign cannot restart')
        ledger.status('running')
        accepted = {r[1]: json.loads(r[3]) for r in rows if r[2] == 'complete'}
        if len(accepted) != sum(r[2] == 'complete' for r in rows):
            raise ValueError('Duplicate accepted task; no result replay')
        start, performed = time.monotonic(), 0
        for task, qid, arm, kind in tasks(plan):
            if task in accepted:
                continue
            check()
            usage = accounting(rows)
            if (usage['cli_attempts'] >= plan['max_attempts']
                    or usage['known_total_tokens'] >= plan['caps']['observed_tokens_stop_threshold']
                    or usage['cli_seconds'] >= plan['caps']['cli_seconds']):
                ledger.status('budget')
                return {'status': 'budget', **usage}
            if performed >= batch_calls or time.monotonic() - start >= plan['caps']['batch_seconds']:
                ledger.status('batch')
                return {'status': 'batch', **usage}
            probe = golds[qid]
            if ctx[qid]['question'] != probe.question:
                raise ValueError('Question mismatch')
            # Scorer labels/category/reference never reach reader input.
            payload = ({'question': probe.question, 'observations': ctx[qid]['contexts'][arm]} if kind == 'answer'
                       else {'question': probe.question, 'candidate': accepted[f'{qid}/{arm}/answer'],
                             'reference': probe.gold.answers[0], 'rubric': list(probe.rubric)})
            if payload_builder is not None:
                payload = payload_builder(probe, ctx[qid], arm, kind,
                                          accepted.get(f'{qid}/{arm}/answer'))
            index = ledger.begin(task)  # reserve BEFORE any possible quota consumption
            call_root = directory / 'calls' / f'{index:05d}'
            call_root.parent.mkdir(mode=0o700, exist_ok=True)
            runner = runner_factory(call_root, 'low', check, seconds=min(plan['caps']['batch_seconds'] - (time.monotonic() - start),
                                                plan['caps']['cli_seconds'] - usage['cli_seconds']), max_calls=1,
                                    max_tokens=plan['caps']['observed_tokens_stop_threshold'],
                                    judge_model=plan.get('judge', {}).get('model', JUDGE_MODEL), judge_effort='high')
            try:
                value = runner.invoke(payload, kind)
            except Exception:
                usage_path = call_root / 'usage.json'
                record = json.loads(usage_path.read_text())['attempt_records'] if usage_path.exists() else []
                event_path = call_root / '000.events.jsonl'
                is_quota = (event_path.exists() and event_path.stat().st_size <= 2_000_000
                            and quota_rejection(event_path.read_text()))
                r = record[0] if len(record) == 1 else {}
                ledger.finish(index, 'quota' if is_quota else 'blocked', None,
                              r.get('usage'), r.get('seconds'), call_root)
                ledger.status('quota' if is_quota else 'blocked')
                if is_quota:
                    return {'status': 'quota', **accounting(ledger.validated())}
                raise
            r = runner.records[0]
            if not r['accepted'] or r['usage'] is None:
                raise ValueError('Missing accepted receipt/usage; no subsequent call')
            ledger.finish(index, 'complete', value, r['usage'], r['seconds'], call_root)
            accepted[task] = value
            performed += 1
            rows = ledger.validated()
        ledger.status('complete')
        return {'status': 'complete', **accounting(rows)}
    except BaseException:
        if ledger is not None and ledger.meta('status') == 'running':
            ledger.status('blocked')
        raise
    finally:
        if ledger is not None:
            ledger.close()
        lock.unlink()


def validate_inputs(plan, ctx, golds):
    """Check every selected input before spending quota; never drop a question."""
    if set(golds) != set(plan['question_ids']):
        raise ValueError('Gold denominator incomplete')
    for qid in plan['question_ids']:
        probe = golds[qid]
        if ctx[qid]['question'] != probe.question:
            raise ValueError('Question mismatch before any inference')
        payloads = [(READER_PROMPT, {'question': probe.question, 'observations': ctx[qid]['contexts'][arm]})
                    for arm in plan['arms']]
        payloads.append((JUDGE_PROMPT, {'question': probe.question, 'candidate': '',
                                      'reference': probe.gold.answers[0], 'rubric': list(probe.rubric)}))
        if any(len((prefix + json.dumps(payload, ensure_ascii=False)).encode()) > 24000
               for prefix, payload in payloads):
            raise ValueError('Frozen prompt budget infeasible; no question exclusion or model calls')


def answer(directory, batch_calls, resume):
    _, directory = private_path(directory)
    plan = frozen(directory)
    if not 1 <= batch_calls <= plan['caps']['batch_calls']:
        raise ValueError('Batch must be 1..40 calls, fixed global caps unchanged')
    ensure_priority(Limits())
    ctx = contexts(directory, plan)
    golds = {p.id: p for c in conversations(Path(plan['data']), max_row=MAX_ROW, max_nodes=MAX_NODES)
             for p in c.probes if p.id in ctx}
    validate_inputs(plan, ctx, golds)
    result = execute_batch(directory, plan, ctx, golds, batch_calls, resume)
    frozen(directory)  # Attest unchanged source/runtime after the batch too.
    atomic_json(directory / 'progress.json', {**result, 'plan_sha256': sha256(directory / 'plan.json')})
    return result


def final_report(directory, plan):
    ledger = Ledger(directory, sha256(directory / 'plan.json'))
    try:
        rows = ledger.validated()
        if ledger.meta('status') != 'complete' or sum(r[2] == 'complete' for r in rows) != plan['tasks']:
            raise ValueError('Only a complete frozen paired denominator permits a claim')
        values = {r[1]: json.loads(r[3]) for r in rows if r[2] == 'complete'}
        if set(values) != {task for task, _, _, _ in tasks(plan)}:
            raise ValueError('Final task identities changed')
        points = []
        for q in plan['selected']:
            scores = {a: values[f"{q['qid']}/{a}/correct"] for a in plan['arms']}
            if not all(type(v) is bool for v in scores.values()):
                raise ValueError('Judge boolean required')
            points.append({**q, **scores})
        delta = estimate([(p['cluster'], int(p['memory']) - int(p['bm25'])) for p in points],
                         SEED, repetitions=10000, minimum_clusters=30)
        output = {'complete': True, 'questions': len(points), 'paired_delta_accuracy': delta,
                  'accuracy': {a: estimate([(p['cluster'], int(p[a])) for p in points], SEED,
                                          repetitions=10000, minimum_clusters=30) for a in plan['arms']},
                  'criterion_met': delta['ci95'] is not None and delta['ci95'][0] > 0,
                  'accounting': accounting(rows), 'models': [plan['reader'], plan['judge']],
                  'full_context_arm': 'Excluded: 500K conversation exceeds the frozen CLI reader path; no API authorization',
                  'claim': plan['claim'], 'plan_sha256': sha256(directory / 'plan.json'),
                  'ledger_sha256': sha256(directory / 'campaign.sqlite3')}
    finally:
        ledger.close()
    atomic_json(directory / 'report.json', output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'retrieve', '_retrieve', 'answer', 'report'])
    parser.add_argument('--plan-dir', type=Path, required=True)
    parser.add_argument('--data', type=Path)
    parser.add_argument('--provenance', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--copy-proof', type=Path)
    parser.add_argument('--batch-calls', type=int, default=40)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    _, directory = private_path(args.plan_dir)
    if args.operation == 'prepare':
        if not all((args.data, args.provenance, args.source_commit, args.copy_proof)):
            parser.error('prepare requires --data --provenance --source-commit --copy-proof')
        prepare(directory, args.source_commit, args.data, args.provenance, args.copy_proof)
    elif args.operation == 'retrieve':
        retrieve(directory)
    elif args.operation == '_retrieve':
        asyncio.run(retrieval(frozen(directory, check_cli=False), directory / 'retrieval'))
    elif args.operation == 'answer':
        print(json.dumps(answer(directory, args.batch_calls, args.resume), indent=2))
    else:
        print(json.dumps(final_report(directory, frozen(directory)), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
