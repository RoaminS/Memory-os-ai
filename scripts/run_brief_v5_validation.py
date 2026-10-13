#!/usr/bin/env python3
"""Collect sealed, gold-free P-A outputs on two frozen arms. Never score them."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from qualify_brief_v5_performance import export_arm, guarded, run_guarded, sealed_protocol, without_project_registry  # noqa: E402

BASELINE = 'fdd60cb0c9fee95f2f32471e93d77a2873a3d946'
CANDIDATE = '324aa68b905e491129549f8a289c64e04b61ec27'
DATE_FIELDS = {'created_at', 'event_time', 'valid_from', 'valid_until', 'last_accessed'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('Duplicate JSON key: ' + key)
        out[key] = value
    return out


def no_gold(value):
    if isinstance(value, dict):
        if 'expect' in value:
            raise ValueError('Gold/expect is forbidden in inputs')
        for child in value.values():
            no_gold(child)
    elif isinstance(value, list):
        for child in value:
            no_gold(child)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Fixture date must be an absolute ISO string')
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError('Fixture date needs an explicit timezone')
    return date.timestamp()


@contextmanager
def wall_clock(at):
    original = time.time
    time.time = lambda: at
    try:
        yield
    finally:
        time.time = original


@contextmanager
def bound_engine(server, engine):
    # Private sequential fixture only; set_engine closes the previous binding.
    # Context managers own the stores, and no live HTTP server shares this process.
    previous = server._engine
    server._engine = engine
    try:
        yield
    finally:
        server._engine = previous


@contextmanager
def forbid_neural():
    from memory_os_v4.embed import Embedder
    def forbidden(*args, **kwargs):
        raise AssertionError('Neural encoding is forbidden')
    encode, load = Embedder.encode, Embedder._ensure_model
    Embedder.encode = Embedder._ensure_model = forbidden
    try:
        yield
    finally:
        Embedder.encode, Embedder._ensure_model = encode, load


def load_inputs(path, manifest_path, expected, case_id_prefix='V5-VAL-'):
    if path.name == 'cases_v1.jsonl' or 'sealed_v1' in path.parts:
        raise ValueError('Gold source path forbidden; use the inputs-only copy')
    raw = path.read_bytes()
    manifest = json.loads(manifest_path.read_text(), object_pairs_hook=unique_object)
    if sha(raw) != expected or manifest['inputs_sha256'] != expected:
        raise ValueError('Input seal mismatch')
    cases = [json.loads(line, object_pairs_hook=unique_object) for line in raw.splitlines() if line.strip()]
    no_gold(cases)
    if len(cases) != 40 or manifest['cases'] != 40:
        raise ValueError('Expected all 40 sealed inputs')
    if case_id_prefix not in {'V5-VAL-', 'V5-VAL2-'}:
        raise ValueError('Explicit supported case ID prefix required')
    if [c['id'] for c in cases] != [f'{case_id_prefix}{i:03}' for i in range(1, 41)]:
        raise ValueError('Missing, duplicate or reordered case IDs')
    for key in ('fr', 'en'):
        if sum(c['language'] == key for c in cases) != manifest[key]:
            raise ValueError('Language manifest mismatch')
    for key, value in manifest['families'].items():
        if sum(c['family'] == key for c in cases) != value:
            raise ValueError('Family manifest mismatch')
    return cases, manifest


def compatibility(path, inputs_sha):
    if path is None:
        return None
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    required = {'inputs_sha256', 'approved_by', 'approved_at', 'reason',
                'baseline_client_id', 'baseline_metadata'}
    if set(value) != required or value['inputs_sha256'] != inputs_sha:
        raise ValueError('Compatibility amendment fields/seal mismatch')
    timestamp(value['approved_at'])
    if (not value['approved_by'] or not value['reason']
            or value['baseline_client_id'] not in {'negotiate', 'exact'}
            or value['baseline_metadata'] != 'unavailable'):
        raise ValueError('Explicit client-id policy and unavailable baseline metadata required')
    return value


def candidate_binding(path, candidate, inputs_sha, manifest_sha):
    if re.fullmatch(r'[0-9a-f]{40}', candidate) is None:
        raise ValueError('Candidate must be a full lowercase frozen Git SHA, never HEAD')
    if path is None:
        if candidate != CANDIDATE:
            raise ValueError('New frozen candidate requires an explicit input-seal binding')
        return None
    value = json.loads(path.read_text(), object_pairs_hook=unique_object)
    required = {'inputs_sha256', 'manifest_sha256', 'candidate_commit', 'baseline_commit',
                'bound_at', 'bound_by', 'reason'}
    if (set(value) != required or value['inputs_sha256'] != inputs_sha
            or value['manifest_sha256'] != manifest_sha or value['candidate_commit'] != candidate
            or value['baseline_commit'] != BASELINE or not value['bound_by'] or not value['reason']):
        raise ValueError('Frozen candidate binding mismatches inputs/manifest/arms')
    timestamp(value['bound_at'])
    return value


def validate_case(case, models, MemoryItem):
    from memory_os_v4 import Config
    allowed = {'id', 'language', 'family', 'now', 'items', 'blocks', 'tasks', 'request', 'relay'}
    if set(case) != allowed:
        raise ValueError('Unknown/missing case fields')
    timestamp(case['now'])
    if not all(isinstance(case[k], list) for k in ('items', 'blocks', 'tasks')):
        raise ValueError('Fixture collections must be lists')
    ids = []
    for source in case['items']:
        if set(source) - {f.name for f in fields(MemoryItem)}:
            raise ValueError('Unknown MemoryItem fields')
        item = dict(source)
        for key in DATE_FIELDS:
            if item.get(key) is not None:
                item[key] = timestamp(item[key])
        value = MemoryItem(**item)
        if not isinstance(value.tags, list) or not all(isinstance(v, str) for v in value.tags):
            raise ValueError('Item tags must be strings; no coercion')
        ids.append(value.id)
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate fixture item IDs')
    block_keys = []
    for block in case['blocks']:
        if (set(block) != {'name', 'text', 'project', 'scope'}
                or block['scope'] not in {'global', 'project'}
                or not isinstance(block['name'], str) or not block['name']
                or not isinstance(block['text'], str)
                or len(block['text']) > Config().block_char_limit
                or (block['scope'] == 'global' and block['project'] is not None)
                or (block['scope'] == 'project' and
                    (not isinstance(block['project'], str) or not block['project'] or block['project'] == '*'))):
            raise ValueError('Ambiguous block scope/project/fields')
        block_keys.append((block['project'], block['name']))
    if len(block_keys) != len(set(block_keys)):
        raise ValueError('Duplicate fixture block identity')
    task_ids = []
    for source in case['tasks']:
        payload = {k: v for k, v in source.items() if k not in {'dated_fixture', 'update'}}
        if not payload.get('task_id'):
            raise ValueError('Fixture task needs its explicit UUID, not a random generated ID')
        models.TaskCreateIn.model_validate(payload)
        task_ids.append(payload['task_id'])
        if 'dated_fixture' in source:
            timestamp(source['dated_fixture'])
        if 'update' in source:
            update = dict(source['update'])
            if {'expected_etag', 'task_id', 'project'} & update.keys():
                raise ValueError('Fixture update cannot invent ETag or override task identity')
            models.TaskUpdateIn.model_validate({**update, 'project': payload['project'],
                'task_id': payload['task_id'], 'expected_etag': 'observed-at-runtime'})
    if len(task_ids) != len(set(task_ids)):
        raise ValueError('Duplicate fixture task IDs')
    relay = case['relay']
    if set(relay) != {'unresolved', 'errors'} or not all(isinstance(v, list) for v in relay.values()):
        raise ValueError('Relay fixture must contain unresolved/errors lists, like L1a')
    request = dict(case['request'])
    supports_client = 'client_id' in models.SessionBriefIn.model_fields
    if not supports_client:
        request.pop('client_id', None)  # Type audit only; execution policy is explicit below.
    models.SessionBriefIn.model_validate(request)
    return supports_client


def observation(result, count):
    raw = encoded(result.model_dump(mode='json', by_alias=True, exclude_none=True))
    data = json.loads(result.content[0].text)
    rows = [('block:' + name, 'block', meta) for name, meta in data.get('blocks', {}).items()]
    rows += [(meta['id'], meta['kind'], meta) for meta in data.get('references', [])]
    units = []
    for unit_id, kind, meta in rows:
        units.append({'id': unit_id, 'kind': kind, 'offsets': meta.get('span'),
            'variant': {'full': 'full', 'sentence': 'first_sentence', 'reference': 'reference'}.get(meta.get('rendering')),
            'raw_metadata': meta})
    if all(u['offsets'] is not None for u in units):
        units.sort(key=lambda u: u['offsets'][0])
    return {'serialized_call_tool_result': raw, 'call_tool_result': json.loads(raw),
            'bytes': len(raw.encode('utf-8')), 'tokens': count(raw), 'sha256': sha(raw.encode()),
            'unit_layout': data.get('unit_layout'), 'units': units,
            'unit_metadata_available': data.get('unit_layout') is not None,
            'offset_basis': 'unicode code points in body without warning/notice prefix' if data.get('unit_layout') else None,
            'notices': data.get('notices', []), 'coverage': data.get('coverage'),
            'raw_references': data.get('references', []), 'raw_blocks': data.get('blocks', {})}


def collect_case(case, directory, amendment):
    import anyio
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.models import TaskCreateIn
    from memory_os_v4.store import MemoryItem
    from memory_os_v4.brief_tokens import counter
    from memory_os_v4.ops.durable_relay import DurableRelay
    from memory_os_v4.ops.relay import SessionToolCatalog
    from memory_os_v4.readiness import build_identity
    from qualify_brief_v5_performance import local_ready
    from memory_os_v4.ops.auth import create_token, read_token

    def forbidden(*args, **kwargs):
        raise AssertionError('Neural encoding is forbidden')
    count = counter()
    now = timestamp(case['now'])
    cfg = Config(data_dir=str(directory/'store'), embed_model='hash', hash_dim=16,
                 store_backend='sqlite_s1', work_state=bool(case['tasks']), project_registry=None)
    create_token(directory/'token')
    os.environ['MEMORY_V4_TOKEN_FILE'] = str(directory/'token')
    with wall_clock(now), forbid_neural():
        with MemoryV4(config=cfg) as seed, bound_engine(server, seed):
            for source in case['items']:
                value = dict(source)
                for key in DATE_FIELDS:
                    if value.get(key) is not None:
                        value[key] = timestamp(value[key])
                seed.store.append(MemoryItem(**value))
            for block in case['blocks']:
                if len(block['text']) > cfg.block_char_limit:
                    raise ValueError('Block would be truncated at seed; amend input instead')
                seed.blocks.set(**block)
            for source in case['tasks']:
                payload = {k: v for k, v in source.items() if k not in {'dated_fixture', 'update'}}
                TaskCreateIn.model_validate(payload)
                dated = timestamp(source.get('dated_fixture', case['now']))
                with wall_clock(dated):
                    created = server.dispatch('memory_task_create', payload)
                if created.get('ok') is not True:
                    raise ValueError('Fixture task create failed: ' + encoded(created))
                if 'update' in source:
                    with wall_clock(dated):
                        updated = server.dispatch('memory_task_update', {**source['update'],
                            'project': payload['project'], 'task_id': created['task_id'],
                            'expected_etag': created['etag']})
                    if updated.get('ok') is not True:
                        raise ValueError('Fixture task update failed: ' + encoded(updated))
        # Reopen derives hash indexes from the exact raw records; no neural model.
        with MemoryV4(config=cfg) as engine, bound_engine(server, engine):
            engine.embedder.encode = forbidden  # Brief itself must not encode even hash texts.
            readiness = anyio.run(local_ready, server, read_token(directory/'token'), build_identity()['commit'])
            catalog = SessionToolCatalog()
            catalog._tools = anyio.run(server.list_tools)
            supports_client = 'client_id' in server.models.SessionBriefIn.model_fields
            direct_args = dict(case['request'])
            if not supports_client and amendment['baseline_client_id'] == 'negotiate':
                direct_args.pop('client_id', None)
            transmitted = []
            class Ledger:
                def status(self):
                    return {'unresolved_count': len(case['relay']['unresolved']),
                            'errors': case['relay']['errors'], 'journal_poisoned': False}
            class Transport:
                async def submit(self, operation, *, replay):
                    class Session:
                        async def call_tool(self, name, args):
                            transmitted.append(dict(args))
                            return await server.call_tool(name, args)
                    return await operation(Session())
            relay = DurableRelay(Transport(), None)
            relay.journal = Ledger()
            relay.brief_wire_formats, relay.brief_budget_supported = catalog.brief_contract()
            relay.brief_client_supported = supports_client
            direct = anyio.run(server.call_tool, 'memory_session_brief', direct_args)
            relayed = anyio.run(relay.call, 'memory_session_brief', dict(direct_args))
            return {'server': {**observation(direct, count), 'arguments_sent': direct_args},
                    'relay': {**observation(relayed, count), 'arguments_sent': transmitted[-1] if transmitted else None},
                    'ready': readiness, 'neural_calls': 0,
                    'dated_fixture': {s['task_id']: s.get('dated_fixture', case['now']) for s in case['tasks']}}


def worker(args):
    sys.path.insert(0, str(args.worker_source/'v4/src'))
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem
    from memory_os_v4.readiness import build_identity
    cases, _ = load_inputs(args.inputs, args.manifest, args.inputs_sha256,
                           getattr(args, 'case_id_prefix', 'V5-VAL-'))
    amendment = compatibility(args.amendment, args.inputs_sha256)
    identity = build_identity()
    if identity.get('commit') != args.arm_commit or identity['identity_source'] != 'env_declared':
        raise ValueError('Frozen arm source identity refused')
    client_cases = []
    for case in cases:
        if not validate_case(case, models, MemoryItem) and 'client_id' in case['request']:
            client_cases.append(case['id'])
    audit = {'identity': identity, 'cases': len(cases), 'unsupported_client_id_cases': client_cases,
             'baseline_metadata': 'unavailable' if args.arm_commit == BASELINE else 'provided',
             'amendment_required': amendment is None, 'inference': False}
    (args.output/'audit.json').write_text(encoded(audit)+'\n')
    if not args.run:
        return
    if amendment is None:
        raise ValueError('Compatibility amendment required before any case execution')
    rows, hashes = [], {}
    for case in cases:
        pair = []
        for repeat in (1, 2):
            guarded()
            with tempfile.TemporaryDirectory(prefix=case['id']+'-', dir=args.output) as temporary:
                sample = collect_case(case, Path(temporary), amendment)
            row = {'case_id': case['id'], 'arm_commit': args.arm_commit, 'repeat': repeat,
                   'request': case['request'], **sample}
            rows.append(row)
            pair.append(sample)
            with (args.output/'outputs.jsonl').open('a') as out:
                out.write(encoded(row)+'\n')
        hashes[case['id']] = {path: {'runs': [p[path]['sha256'] for p in pair],
            'identical': pair[0][path]['serialized_call_tool_result'] == pair[1][path]['serialized_call_tool_result']}
            for path in ('server', 'relay')}
    result = {'complete': True, 'cases': len(cases), 'records': len(rows), 'neural_calls': 0,
              'determinism': hashes, 'evaluation': False, 'identity': identity}
    (args.output/'worker.json').write_text(encoded(result)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('inputs', 'manifest', 'table', 'output'):
        parser.add_argument('--'+flag, type=Path, required=True)
    parser.add_argument('--inputs-sha256', required=True)
    parser.add_argument('--amendment', type=Path)
    parser.add_argument('--candidate-commit', default=CANDIDATE,
                        help='Explicit full frozen SHA; v1 default remains 324aa68')
    parser.add_argument('--candidate-binding', type=Path,
                        help='Required seal binding for any new candidate')
    parser.add_argument('--case-id-prefix', choices=['V5-VAL-', 'V5-VAL2-'], default='V5-VAL-')
    parser.add_argument('--run', action='store_true', help='Explicit collection; default is zero-execution schema audit')
    parser.add_argument('--worker-source', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--arm-commit', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for key in ('inputs', 'manifest', 'table', 'output', 'amendment', 'worker_source', 'candidate_binding'):
        if getattr(args, key) is not None:
            setattr(args, key, getattr(args, key).resolve())
    if args.worker_source:
        worker(args)
        return 0
    if not args.output.is_relative_to(ROOT/'.local-qualification'):
        parser.error('Fresh output under clone .local-qualification required')
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'complete': False, 'evaluation': False, 'production_modified': False,
              'inference': False, 'neural_calls': 0, 'mode': 'run' if args.run else 'audit', 'arms': {}}
    try:
        if hasattr(os, 'getpriority') and os.getpriority(os.PRIO_PROCESS, 0) < 15:
            os.nice(15 - os.getpriority(os.PRIO_PROCESS, 0))
        _, protocol_sha = sealed_protocol()
        cases, manifest = load_inputs(args.inputs, args.manifest, args.inputs_sha256, args.case_id_prefix)
        amendment = compatibility(args.amendment, args.inputs_sha256)
        manifest_sha = sha(args.manifest.read_bytes())
        binding = candidate_binding(args.candidate_binding, args.candidate_commit, args.inputs_sha256, manifest_sha)
        report.update(inputs_sha256=args.inputs_sha256, manifest_sha256=sha(args.manifest.read_bytes()),
                      protocol_sha256=protocol_sha, amendment=amendment,
                      tokenizer_sha256=sha(args.table.read_bytes()), cases=len(cases),
                      candidate_commit=args.candidate_commit, candidate_binding=binding, case_id_prefix=args.case_id_prefix)
        if re.fullmatch(r'[0-9a-f]{40}', manifest['candidate_frozen']) is None:
            raise ValueError('Manifest source anchor must be a full frozen SHA')
        difference = subprocess.run(['git', 'diff', '--quiet', manifest['candidate_frozen'],
                                     args.candidate_commit, '--', 'v4/src'], cwd=ROOT).returncode
        if difference not in {0, 1}:
            raise ValueError('Cannot attest manifest/candidate source comparison')
        if difference and binding is None:
            raise ValueError('Manifest candidate differs from requested frozen engine')
        report['manifest_candidate_engine_identical'] = difference == 0
        runner = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True).strip():
            raise ValueError('Dirty runner source refused')
        report['runner_commit'] = runner
        from memory_os_v4.ops.qualified_runtime import inspect_current
        report['runtime'] = inspect_current(extras=('ops', 'brief'))
        if not report['runtime']['ok']:
            raise ValueError('Qualified runtime required')
        report['initial_resources'] = guarded()
        with tempfile.TemporaryDirectory(prefix='validation-private-', dir=args.output) as temporary:
            private = Path(temporary)
            jobs = []
            for label, commit in (('baseline', BASELINE), ('candidate', args.candidate_commit)):
                source = private/(label+'-source')
                export_arm(commit, source)
                output = args.output/label
                output.mkdir()
                home = private/(label+'-home')
                home.mkdir()
                env = {k: v for k, v in os.environ.items() if not k.startswith('MEMORY_V4_')}
                env = without_project_registry({**env, 'HOME': str(home), 'PYTHONPATH': str(source/'v4/src'),
                    'MEMORY_V4_MIN_FREE_BYTES': os.environ.get('MEMORY_V4_MIN_FREE_BYTES', '5000000000'),
                    'MEMORY_V4_CL100K_PATH': str(args.table), 'MEMORY_V4_BUILD_COMMIT': commit,
                    'MEMORY_V4_EMBED_MODEL': 'hash', 'MEMORY_V4_QUALIFICATION_METRICS': '0',
                    'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
                command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker-source', str(source),
                    '--arm-commit', commit, '--inputs', str(args.inputs), '--manifest', str(args.manifest),
                    '--inputs-sha256', args.inputs_sha256, '--table', str(args.table), '--output', str(output),
                    '--case-id-prefix', args.case_id_prefix]
                if args.amendment:
                    command += ['--amendment', str(args.amendment)]
                jobs.append((label, output, env, command))
            # Audit BOTH real schemas before seeding ANY case of EITHER arm.
            report['audits'] = {}
            for execution in ((False, True) if args.run else (False,)):
                if execution and amendment is None:
                    raise ValueError('Compatibility amendment required before any case execution')
                for label, output, env, command in jobs:
                    with (output/('worker.log' if execution else 'audit-worker.log')).open('w') as log:
                        rc = run_guarded(command + (['--run'] if execution else []), env=env, log=log)
                    if rc:
                        raise ValueError(label+' worker refused; evidence kept; no restart in this folder')
                    if execution:
                        report['arms'][label] = json.loads((output/'worker.json').read_text())
                    else:
                        report['audits'][label] = json.loads((output/'audit.json').read_text())
        report['complete'] = True
        report['compatibility_pending'] = amendment is None
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(encoded({'complete': report['complete'], 'mode': report['mode'], 'error': report.get('error')}))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
