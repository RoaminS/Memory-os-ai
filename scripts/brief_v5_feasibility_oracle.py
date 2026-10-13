#!/usr/bin/env python3
"""Exact required-only envelope feasibility, independent of candidate selection."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from itertools import product
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from qualify_brief_v5_performance import export_arm, guarded, run_guarded, sealed_protocol  # noqa: E402
from run_brief_v5_validation import CANDIDATE, unique_object  # noqa: E402

VARIANTS = {'full': 'full', 'first_sentence': 'sentence', 'reference': 'reference'}
KINDS = {'fact', 'procedure', 'episode', 'summary', 'doc', 'belief', 'task', 'block'}


def wire_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate(spec):
    if set(spec) != {'id', 'project', 'budget_tokens', 'eligible', 'pool', 'notices', 'relay', 'requirements'}:
        raise ValueError('Explicit context/requirements fields required; no gold/scorer format accepted')
    if not isinstance(spec['id'], str) or not spec['id'] or not isinstance(spec['project'], str):
        raise ValueError('Oracle ID/project must be strings')
    if type(spec['budget_tokens']) is not int or not 200 <= spec['budget_tokens'] <= 1500:
        raise ValueError('Exact compact budget must be 200..1500')
    if not isinstance(spec['notices'], list) or any(not isinstance(x, str) for x in spec['notices']):
        raise ValueError('Base notices must be an explicit ordered list')
    if set(spec['relay']) != {'unresolved', 'errors'} or any(not isinstance(v, list) for v in spec['relay'].values()):
        raise ValueError('Relay unresolved/errors lists required')
    if not isinstance(spec['pool'], list) or len(spec['pool']) > 66:
        raise ValueError('Explicit scoped pool required, bounded to 40+16+10')
    counts = Counter()
    ids = set()
    for unit in spec['pool']:
        if (set(unit) != {'id', 'kind', 'text', 'protected'} or unit['kind'] not in KINDS
                or not isinstance(unit['id'], str) or not unit['id'] or unit['id'] in ids
                or not isinstance(unit['text'], str) or type(unit['protected']) is not bool):
            raise ValueError('Invalid/duplicate unit; protected is explicit, never inferred')
        if unit['kind'] in {'block', 'task'} and not unit['id'].startswith(unit['kind']+':'):
            raise ValueError('Block/task unit needs its real reference prefix')
        ids.add(unit['id'])
        counts[unit['kind']] += 1
    if (counts['block'] > 16 or counts['task'] > 10
            or sum(n for k, n in counts.items() if k not in {'block', 'task'}) > 40):
        raise ValueError('Pool exceeds frozen group limits')
    eligible = spec['eligible']
    if (not isinstance(eligible, dict) or set(eligible)-KINDS
            or any(type(v) is not int or not 0 <= v <= 10**12 for v in eligible.values())
            or any(eligible.get(k, 0) < v for k, v in counts.items())):
        raise ValueError('Scoped eligible populations must cover the explicit pool')
    requirements = spec['requirements']
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 6:
        raise ValueError('1..6 explicit requirements per oracle query')
    seen = set()
    for req in requirements:
        if (set(req)-{'id', 'allowed', 'required_text'} or not {'id', 'allowed'} <= set(req)
                or req['id'] not in ids or req['id'] in seen or not isinstance(req['allowed'], list)
                or not req['allowed'] or set(req['allowed'])-VARIANTS.keys()
                or len(req['allowed']) != len(set(req['allowed']))
                or ('required_text' in req and not isinstance(req['required_text'], str))):
            raise ValueError('Requirement must identify a scoped pooled unit and explicit allowed variants')
        seen.add(req['id'])


def pool_units(spec):
    from memory_os_v4.brief_selection import Unit
    return [Unit(u['id'], u['kind'], 'block' if u['kind']=='block' else 'task' if u['kind']=='task' else 'memory',
                 u['text'], (index,), u['protected']) for index, u in enumerate(spec['pool'])]


def frame(spec, pool, chosen, *, shape, path, notice_order):
    # Rendering/count reducers only: NEVER select/build/rebudget/fits/memory_order.
    from memory_os_v4.brief_selection import assemble, notices_for
    from memory_os_v4.brief_coverage import population, COLUMNS
    base = {'mode': 'compact', 'project': spec['project'], 'coverage': population(spec['eligible'], pool),
            'wire_format': 'compact_v2', 'budget_tokens': spec['budget_tokens']}
    codes = list(spec['notices'])
    if shape != 'by_kind':
        del base['coverage']['by_kind']
        if shape == 'positional_totals':
            base['coverage']['totals'] = [base['coverage']['totals'][k] for k in COLUMNS]
        if notice_order == 'before_unit_notices':
            codes.append('coverage_detail_omitted')
    codes = notices_for(chosen, pool, codes)
    if path == 'prospective' or (path == 'relay' and any(spec['relay'].values())):
        codes.append('unresolved_requests')
    if shape != 'by_kind' and notice_order == 'after_unit_notices':
        # Detail can also disappear during final rebudget. Preserve its notice.
        codes.extend(['budget_saturated', 'coverage_detail_omitted'])
    return assemble(base, chosen, codes)


def enumerate_frames(spec, pool, chosen, count):
    from memory_os_v4.brief import serialized
    for path in ('server', 'relay', 'prospective'):
        for shape in ('by_kind', 'named_totals', 'positional_totals'):
            orders = ('before_unit_notices',) if shape == 'by_kind' else ('before_unit_notices', 'after_unit_notices')
            for order in orders:
                data = frame(spec, pool, chosen, shape=shape, path=path, notice_order=order)
                raw = serialized(data)
                size, tokens = len(raw.encode('utf-8')), count(raw)
                yield {'path': path, 'coverage_shape': shape, 'notice_order': order,
                    'bytes': size, 'tokens': tokens, 'within_budget': size <= 6000 and tokens <= spec['budget_tokens'],
                    'slack_bytes': 6000-size, 'slack_tokens': spec['budget_tokens']-tokens,
                    'serialized_call_tool_result': raw, 'sha256': digest(raw.encode()),
                    'notices': data['notices'], 'coverage': data['coverage'],
                    'variants': [{'id': u.id, 'rendering': u.representation} for u in chosen]}


def measure_requirements(spec, requirements, pool, count):
    by_id = {u.id: u for u in pool}
    choices = []
    for req in requirements:
        allowed = {VARIANTS[key] for key in req['allowed']}
        choices.append([v for v in by_id[req['id']].variants() if v.representation in allowed
                        and (not req.get('required_text') or req['required_text'] in v.text)])
    combinations = 1
    for values in choices:
        combinations *= len(values)
    if combinations > 729:
        raise ValueError('Oracle combination cap exceeded; no sampling/approximation')
    rows = []
    witnesses = {}
    for chosen in product(*choices):
        for row in enumerate_frames(spec, pool, list(chosen), count):
            key = (not row['within_budget'], row['tokens'], row['bytes'], row['sha256'])
            path = row['path']
            if path not in witnesses or key < witnesses[path][0]:
                witnesses[path] = (key, row)
            rows.append({k: v for k, v in row.items() if k != 'serialized_call_tool_result'})
    result = {'required_ids': [r['id'] for r in requirements], 'variants_per_requirement': [len(c) for c in choices],
              'frames': rows, 'per_path': {}}
    for path in ('server', 'relay', 'prospective'):
        candidates = [r for r in rows if r['path'] == path]
        fitting = [r for r in candidates if r['within_budget']]
        best = witnesses[path][1] if path in witnesses else None
        result['per_path'][path] = {'feasible_required_only_frame': bool(fitting), 'minimum_tokens_witness': best}
    return result


def oracle(spec, count):
    validate(spec)
    pool = pool_units(spec)
    return {'id': spec['id'], 'context_sha256': digest(wire_json(spec).encode()),
        'conditional_on_explicit_scope_pool_protection_and_base_notices': True,
        'selection_called': False, 'empty_frames': list(enumerate_frames(spec, pool, [], count)),
        'individual': [measure_requirements(spec, [req], pool, count) for req in spec['requirements']],
        'joint': measure_requirements(spec, spec['requirements'], pool, count)}


def worker(args):
    sys.path.insert(0, str(args.worker_source/'v4/src'))
    from memory_os_v4 import brief, brief_selection
    from memory_os_v4.brief_tokens import counter, status
    from memory_os_v4.readiness import build_identity
    def forbidden(*a, **kw):
        raise AssertionError('Candidate selection must never run in the feasibility oracle')
    brief.build = brief.rebudget = brief_selection.select = brief_selection.rebudget_units = forbidden
    identity = build_identity()
    if identity['commit'] != CANDIDATE or identity['identity_source'] != 'env_declared':
        raise ValueError('Frozen serializer identity refused')
    count = counter()
    specs = [json.loads(line, object_pairs_hook=unique_object) for line in args.specs.read_text().splitlines() if line.strip()]
    if not specs or len({s['id'] for s in specs}) != len(specs):
        raise ValueError('Nonempty uniquely identified oracle specs required')
    for spec in specs:
        validate(spec)
    with (args.output/'oracle.jsonl').open('x') as out:
        os.fchmod(out.fileno(), 0o600)
        for spec in specs:
            guarded()
            out.write(wire_json(oracle(spec, count))+'\n')
            out.flush()
    result = {'complete': True, 'specs': len(specs), 'identity': identity, 'tokenizer': status(),
              'selection_called': False, 'engine_opened': False, 'neural_calls': 0, 'evaluation': False}
    (args.output/'worker.json').write_text(wire_json(result)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('specs', 'table', 'output'):
        parser.add_argument('--'+key, type=Path, required=True)
    parser.add_argument('--specs-sha256', required=True)
    parser.add_argument('--worker-source', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    for key in ('specs', 'table', 'output', 'worker_source'):
        if getattr(args, key):
            setattr(args, key, getattr(args, key).resolve())
    if args.specs.name == 'cases_v1.jsonl' or 'sealed_v1' in args.specs.parts:
        parser.error('Never pass the gold; prepare oracle-only specs separately')
    if args.worker_source:
        worker(args)
        return 0
    if not args.output.is_relative_to(ROOT/'.local-qualification'):
        parser.error('New output inside clone .local-qualification required')
    args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'complete': False, 'evaluation': False, 'production_modified': False, 'neural_calls': 0}
    try:
        if digest(args.specs.read_bytes()) != args.specs_sha256:
            raise ValueError('Oracle spec seal mismatch')
        report.update(specs_sha256=args.specs_sha256, protocol_sha256=sealed_protocol()[1],
                      tokenizer_sha256=digest(args.table.read_bytes()), serializer_commit=CANDIDATE)
        if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True).strip():
            raise ValueError('Dirty oracle runner source refused')
        report['runner_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        from memory_os_v4.ops.qualified_runtime import inspect_current
        report['runtime'] = inspect_current(extras=('ops', 'brief'))
        if not report['runtime']['ok']:
            raise ValueError('Qualified runtime required')
        report['initial_resources'] = guarded()
        with tempfile.TemporaryDirectory(prefix='oracle-serializer-', dir=args.output) as temporary:
            source = Path(temporary)/'source'
            export_arm(CANDIDATE, source)
            env = {k: v for k, v in os.environ.items() if not k.startswith('MEMORY_V4_')}
            env.update(PYTHONPATH=str(source/'v4/src'), MEMORY_V4_BUILD_COMMIT=CANDIDATE,
                MEMORY_V4_CL100K_PATH=str(args.table), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                MEMORY_V4_MIN_FREE_BYTES=os.environ.get('MEMORY_V4_MIN_FREE_BYTES', '5000000000'),
                PYTHONDONTWRITEBYTECODE='1')
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker-source', str(source),
                '--specs', str(args.specs), '--specs-sha256', args.specs_sha256,
                '--table', str(args.table), '--output', str(args.output)]
            with (args.output/'worker.log').open('w') as log:
                if run_guarded(command, env=env, log=log):
                    raise ValueError('Oracle worker failed; evidence kept, no restart in this folder')
        report['worker'] = json.loads((args.output/'worker.json').read_text())
        report['complete'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(wire_json({'complete': report['complete'], 'error': report.get('error')}))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
