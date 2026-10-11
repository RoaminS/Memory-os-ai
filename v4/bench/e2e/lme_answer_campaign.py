"""DESCRIPTIVE LongMemEval-S answer campaign; no API, downloads or live store.

Benchmark source and qualified 4.0.3 engine source are attested separately.
Only Claude executes guarded retrieval/CLI calls outside the sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
import uuid

from .answer_campaign import (Ledger, accounting, atomic_json, contexts, execute_batch, tasks)
from .answer_pilot import local_runtime, validate_encoder
from .baselines import BM25
from .client import ToolClient, offline_env, project_for
from .codex_reader import CodexReader, MODEL, READER_PROMPT, capability, runtime_record
from .configuration import resolved_config
from .data import SESSION_POLICY, array_entries, cases, sha256
from .experiments import MODELS, private_path
from .guard import Limits, ensure_priority, sample
from .leaderboard import AddSearchAdapter
from .lme_grading import SOURCE, TYPES, get_anscheck_prompt, judge_payload, official_prompt, source_templates
from .pipeline import extract, pack
from .processes import cleanup
from .run import code_record, kill_group, model_record, verify_source
from .stats import EVIDENCE_POLICY, evidence_cluster_ids, estimate

VERSION = 'lme-s-answer-luna-v1'
PROFILE = 'lme-s-production-4.0.3'
DATA = Path('/Users/romain/Memory-os-ai/v4/bench/data/longmemeval_s.json')
DATA_SHA = '08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894'
ENGINE_COMMIT = '53030c2c99b09b5dc3f8467dda0a114e47477ee2'
PROTOCOL = Path(__file__).resolve().parents[1] / 'protocols/lme-s-answer-luna-v1.json'
SEED = 20261011


def source_archive(clone, revision):
    for prefix in (['git', '-C', str(clone)],
                   ['git', '--git-dir', str(clone / '.local-qualification/git-state')],
                   ['git', '--git-dir', str(clone / '.local-qualification/lexical-dev-git')]):
        result = subprocess.run([*prefix, 'archive', revision, 'v4/src'], cwd=clone,
                                capture_output=True, timeout=10)
        if result.returncode == 0:
            return result.stdout
    raise ValueError('Cannot attest qualified 4.0.3 engine source')


def extract_engine(archive_bytes, target):
    """No tar extractall, symlinks, traversal or overwrite; preserve all bytes."""
    target.mkdir(mode=0o700, exist_ok=False)
    manifest = {}
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        for member in archive:
            if member.isdir():
                continue
            relative = member.name.removeprefix('v4/src/')
            if (not member.isfile() or relative == member.name or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or member.size > 2_000_000):
                raise ValueError('Unsafe qualified engine archive')
            content = archive.extractfile(member).read()
            destination = target / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(content)
            manifest[relative] = hashlib.sha256(content).hexdigest()
    if 'memory_os_v4/config.py' not in manifest:
        raise ValueError('Qualified engine archive incomplete')
    return manifest


def engine_manifest(target):
    if target.is_symlink() or any(p.is_symlink() for p in target.rglob('*')):
        raise ValueError('Engine source symlinks forbidden')
    return {str(p.relative_to(target)): sha256(p) for p in sorted(target.rglob('*')) if p.is_file()}


def metadata(data, check):
    golds, rows = [], []
    # Streaming raw annotation and validated observable replay; never hold 500 haystacks.
    for entry, (observations, gold) in zip(array_entries(data, check=check), cases(data, check=check)):
        if entry['question_id'] != gold.id or gold.category not in TYPES or entry.get('answer') is None:
            raise ValueError('Unknown type, identity or missing reference; no task exclusion')
        if gold.evidence and not gold.answer_sessions:
            raise ValueError('Missing evidence-session annotation')
        if not gold.id.endswith('_abs') and not gold.evidence:
            raise ValueError('Non-abstention lacks evidence; no task exclusion')
        get_anscheck_prompt(gold.category, observations.question, entry['answer'], '', '_abs' in gold.id)
        golds.append(gold)
        rows.append({'qid': gold.id, 'question': observations.question, 'question_date': observations.date,
                     'question_type': gold.category, 'reference': entry['answer'], 'abstention': '_abs' in gold.id,
                     'excluded_future_turns': gold.excluded_future_turns})
    if len(rows) != 500 or len({r['qid'] for r in rows}) != 500:
        raise ValueError('Exactly all 500 LongMemEval-S questions required, abstentions included')
    clusters = evidence_cluster_ids(golds)
    if len(set(clusters.values())) < 30:
        raise ValueError('At least 30 evidence/derived-abs clusters required before any model call')
    for row in rows:
        row['cluster'] = clusters[row['qid']]
    return rows


def prepare(directory, revision, copy_proof, official_evaluator):
    clone, directory = private_path(directory)
    _, copy_proof = private_path(copy_proof)
    _, official_evaluator = private_path(official_evaluator)
    if directory.exists():
        raise ValueError('Fresh plan required; preserve old evidence, never restart')
    ensure_priority(Limits())
    check = lambda: sample(clone)
    check()
    verify_source(clone, revision, code_record())
    acquisition = json.loads((official_evaluator.parent / 'PROVENANCE.json').read_text())
    if (acquisition.get('url') != 'https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/src/evaluation/evaluate_qa.py'
            or acquisition.get('sha256') != sha256(official_evaluator) or acquisition.get('model_calls') != 0):
        raise ValueError('Official evaluator acquisition provenance missing/changed')
    templates = source_templates(official_evaluator)
    if sha256(DATA, check=check) != DATA_SHA:
        raise ValueError('Official S dataset fingerprint changed')
    copied = json.loads(copy_proof.read_text())
    if (copied.get('ok') is not True or copied.get('version') != '4.0.3'
            or copied.get('source_commit') != ENGINE_COMMIT or copied.get('production_modified') is not False
            or copied.get('rankings_identical_between_artifacts_and_reopen') is not True):
        raise ValueError('Qualified immutable 4.0.3 installed-copy proof required')
    caps = [capability('low'), capability('high', model=MODEL)]
    if not all(c['supported'] for c in caps):
        raise ValueError('Luna low/high unavailable; no substitution')
    rows = metadata(DATA, check)
    record = model_record(str(MODELS['granite']), check=check)
    record.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                  batch_size=32, max_seq_length=512)
    protocol = json.loads(PROTOCOL.read_text())
    directory.mkdir(mode=0o700)
    manifest = extract_engine(source_archive(clone, ENGINE_COMMIT), directory / 'engine-source')
    # resolved_config is benchmark-side; refuse any drift from the qualified Config.
    if sha256(clone / 'v4/src/memory_os_v4/config.py') != manifest['memory_os_v4/config.py']:
        raise ValueError('Benchmark configuration loader differs from qualified engine')
    if manifest['memory_os_v4/embed.py'] != copied['encoding_profile']['encoder_source_sha256']:
        raise ValueError('Copy proof encoder does not match immutable engine')
    atomic_json(directory / 'scoring.json', rows)
    plan = {'version': VERSION, 'source_commit': revision, 'code_files': code_record(),
            'protocol': protocol, 'protocol_sha256': sha256(PROTOCOL), 'data': str(DATA), 'dataset_sha256': DATA_SHA,
            'session_policy': SESSION_POLICY, 'selected': [{k: r[k] for k in ('qid', 'cluster', 'question_type', 'abstention')} for r in rows],
            'question_ids': [r['qid'] for r in rows], 'source_clusters': len({r['cluster'] for r in rows}),
            'bootstrap_audit': {'policy': EVIDENCE_POLICY,
                                'component_sizes': sorted(Counter(r['cluster'] for r in rows).values(), reverse=True),
                                'orphan_abs_questions': [r['qid'] for r in rows if r['qid'].endswith('_abs')
                                                        and r['qid'][:-4] not in {q['qid'] for q in rows}],
                                'conditional_on_fixed_distractors': True},
            'question_types': dict(Counter(r['question_type'] for r in rows)),
            'abstentions': sum(r['abstention'] for r in rows), 'scoring_sha256': sha256(directory / 'scoring.json'),
            'engine_source_commit': ENGINE_COMMIT, 'engine_files': manifest,
            'copy_proof': str(copy_proof), 'copy_proof_sha256': sha256(copy_proof),
            'encoder': record, 'runtime': local_runtime(), 'production_encoding_profile': copied['encoding_profile'],
            'engine_config': resolved_config(record['id'], '<private-store>', profile=PROFILE),
            'cli': runtime_record(), 'reader': protocol['reader'], 'judge': protocol['judge'],
            'prompts': {'reader': READER_PROMPT, 'judge_source': SOURCE,
                        'judge_templates_sha256': sha256(Path(__file__).with_name('lme_grading.py'))},
            'official_evaluator': str(official_evaluator), 'official_evaluator_sha256': sha256(official_evaluator),
            'official_provenance_sha256': sha256(official_evaluator.parent / 'PROVENANCE.json'),
            'official_templates': templates,
            'arms': ['memory', 'bm25'], 'tasks': 2000, 'max_attempts': protocol['max_attempts'],
            'caps': protocol['caps'], 'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024,
            'ingest_bytes': 16_000_000, 'claim': protocol['status'], 'seed': SEED}
    validate_encoder({'encoding_profile': copied['encoding_profile']}, plan)
    atomic_json(directory / 'plan.json', plan)
    atomic_json(directory / 'receipt.json', {'plan_sha256': sha256(directory / 'plan.json'),
                                           'prepared_before_inference': True, 'model_calls': 0})
    return plan


def frozen(directory, *, check_cli=True):
    clone, directory = private_path(directory)
    plan = json.loads((directory / 'plan.json').read_text())
    receipt = json.loads((directory / 'receipt.json').read_text())
    if (receipt['plan_sha256'] != sha256(directory / 'plan.json') or plan['version'] != VERSION
            or plan['protocol_sha256'] != sha256(PROTOCOL) or plan['protocol'] != json.loads(PROTOCOL.read_text())
            or plan['code_files'] != code_record() or plan['engine_source_commit'] != ENGINE_COMMIT
            or plan['engine_files'] != engine_manifest(directory / 'engine-source')
            or plan['scoring_sha256'] != sha256(directory / 'scoring.json')
            or plan['official_evaluator_sha256'] != sha256(Path(plan['official_evaluator']))
            or plan['official_provenance_sha256'] != sha256(Path(plan['official_evaluator']).parent / 'PROVENANCE.json')
            or plan['copy_proof_sha256'] != sha256(Path(plan['copy_proof']))):
        raise ValueError('Frozen protocol/source/receipts changed; amendment required')
    verify_source(clone, plan['source_commit'], code_record())
    if (sha256(Path(plan['data']), check=lambda: sample(clone)) != plan['dataset_sha256']
            or local_runtime() != plan['runtime']):
        raise ValueError('Frozen corpus/runtime changed')
    record = model_record(plan['encoder']['id'], check=lambda: sample(clone))
    record.update(device='auto -> mps required', precision='native; actual parameter dtype attested before capture',
                  batch_size=32, max_seq_length=512)
    if (record != plan['encoder'] or resolved_config(record['id'], '<private-store>', profile=PROFILE) != plan['engine_config']
            or check_cli and runtime_record() != plan['cli']):
        raise ValueError('Encoder/config/CLI changed; never silently resume')
    return plan


async def retrieval(plan, output):
    from .network import deny_ip_network
    deny_ip_network()
    import memory_os_v4
    if (memory_os_v4.__version__ != '4.0.3' or Path(memory_os_v4.__file__).resolve().parent.parent
            != output.parent / 'engine-source'):
        raise ValueError('Retrieval imported a different engine; no inference allowed')
    rows, timings = [], []
    started = time.monotonic()
    async with ToolClient(output / 'memory', plan['encoder']['id'], resident=True, config_profile=PROFILE) as client:
        profile = validate_encoder(await client.call('memory_status', {}), plan)
        atomic_json(output / 'encoder-attestation.json', profile)
        adapter = AddSearchAdapter(client, 'lme-s-answer-luna-v1-capture')
        with (output / 'contexts.jsonl').open('x') as stream:
            for observed, gold in cases(Path(plan['data'])):
                sample(output)
                opened = await client.call('eval_open_conversation', {'conversation_id': observed.id})
                if not opened['store_empty'] or not opened['encoder_reused']:
                    raise RuntimeError('Question isolation/residency failed')
                t = time.monotonic()
                quotes = extract(project_for(adapter.namespace, observed.id), observed.turns, plan['chunk_chars'])
                if sum(len(q.text.encode()) for q in quotes) > plan['ingest_bytes']:
                    raise RuntimeError('Ingestion byte cap exceeded')
                captured = await adapter._capture(observed.id, observed.turns, quotes)
                if captured['deduped'] or captured['added'] != len(quotes):
                    raise RuntimeError('Lossless same-arm capture failed')
                by_id = {q.id: q for q in quotes}
                # BM25 baseline persists identical input, overwriting a single private file.
                baseline_root = output / 'bm25'
                baseline_root.mkdir(exist_ok=True)
                bm25 = BM25(quotes, baseline_root, plan['encoder']['id'])
                ingest_seconds = time.monotonic() - t
                t = time.monotonic()
                found = await adapter.search(observed.id, observed.question, plan['top_k'])
                ranks = [by_id[h['id']] for h in found['results']]
                row = {'qid': gold.id, 'question': observed.question, 'question_date': observed.date,
                       'contexts': {s: [q.text for q in pack(r, plan['top_k'], plan['context_bytes'])]
                                    for s, r in [('memory', ranks), ('bm25', bm25.search(observed.question, plan['top_k']))]},
                       'retrieved_ids': {'memory': [q.id for q in ranks],
                                         'bm25': [q.id for q in bm25.search(observed.question, plan['top_k'])]}}
                stream.write(json.dumps(row) + '\n')
                stream.flush()
                rows.append(gold.id)
                timings.append({'qid': gold.id, 'ingest_seconds': ingest_seconds, 'search_seconds': time.monotonic() - t,
                                'store_close_ms': opened['conversation_close_ms'], 'store_init_ms': opened['conversation_init_ms']})
                atomic_json(output / 'progress.json', {'questions': len(rows), 'timings': timings, 'model_calls': 0})
    if rows != plan['question_ids']:
        raise RuntimeError('Incomplete retrieval denominator')
    atomic_json(output / 'report.json', {'complete': True, 'seconds': time.monotonic() - started, 'timings': timings,
                                        'contexts_sha256': sha256(output / 'contexts.jsonl'),
                                        'encoder_sha256': sha256(output / 'encoder-attestation.json'),
                                        'plan_sha256': sha256(output.parent / 'plan.json'), 'llm_calls': 0})


def retrieve(directory):
    clone, directory = private_path(directory)
    plan = frozen(directory)
    ensure_priority(Limits())
    output = directory / 'retrieval'
    output.mkdir(mode=0o700, exist_ok=False)
    engine = directory / 'engine-source'
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(output / 'server-registry'),
               MEMORY_EVAL_ENGINE_SOURCE=str(engine), PYTHONPATH=os.pathsep.join([str(engine), str(clone / 'v4')]))
    deadline = time.monotonic() + plan['caps']['retrieval_seconds']
    with (output / 'worker.log').open('x') as log:
        child = subprocess.Popen([sys.executable, '-m', 'bench.e2e.lme_answer_campaign', '_retrieve',
                                  '--plan-dir', str(directory)], cwd=output, env=env,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while child.poll() is None:
                sample(directory)
                if time.monotonic() >= deadline:
                    raise RuntimeError('Frozen retrieval time budget exceeded')
                time.sleep(.5)
            if child.returncode or not (output / 'report.json').exists():
                raise RuntimeError('Retrieval incomplete; no restart')
            frozen(directory)
        except BaseException as exc:
            atomic_json(output / 'stopped.json', {'reason': str(exc), 'restart': False})
            raise
        finally:
            try:
                cleanup(output / 'server-registry', env['EVAL_PROCESS_OWNER'])
            finally:
                kill_group(child)


def payload(probe, ctx, arm, kind, candidate):
    if kind == 'answer':
        return {'question': probe.question, 'question_date': probe.date, 'observations': ctx['contexts'][arm]}
    if not isinstance(candidate, str) or len(candidate.encode()) > 6000:
        raise ValueError('Reader answer exceeds frozen judge reserve; no truncation/retry')
    result = {'question_type': probe.category, 'question': probe.question, 'reference': probe.reference,
              'candidate': candidate, 'abstention': probe.abstention}
    if hasattr(probe, 'templates'):
        result['official_prompt'] = official_prompt(probe.templates, probe.category, probe.question,
                                                   probe.reference, candidate, probe.abstention)
    return result


def probes(directory):
    templates = json.loads((directory / 'plan.json').read_text())['official_templates']
    return {r['qid']: SimpleNamespace(question=r['question'], date=r['question_date'], category=r['question_type'],
                                      reference=r['reference'], abstention=r['abstention'], templates=templates,
                                      gold=SimpleNamespace(answers=[r['reference']]), rubric=())
            for r in json.loads((directory / 'scoring.json').read_text())}


class OfficialLuna(CodexReader):
    def __init__(self, *args, **kwargs):
        if kwargs['judge_model'] != MODEL or kwargs['judge_effort'] != 'high':
            raise ValueError('Frozen Luna high judge required')
        super().__init__(*args, **kwargs, raw_judge_prompt=judge_payload)


def answer(directory, batch_calls, resume):
    _, directory = private_path(directory)
    plan = frozen(directory)
    ensure_priority(Limits())
    if not 1 <= batch_calls <= plan['caps']['batch_calls']:
        raise ValueError('Frozen batch limit is 1..40 calls')
    ctx, golds = contexts(directory, plan), probes(directory)
    for qid, probe in golds.items():
        if ctx[qid]['question'] != probe.question or ctx[qid]['question_date'] != probe.date:
            raise ValueError('Observable question/date mismatch')
        for arm in plan['arms']:
            prompt = READER_PROMPT + json.dumps(payload(probe, ctx[qid], arm, 'answer', None), ensure_ascii=False)
            if len(prompt.encode()) > 24000:
                raise ValueError('Reader prompt cap infeasible; no calls or exclusions')
        reserved = judge_payload(payload(probe, ctx[qid], 'memory', 'correct', 'x' * 6000))
        if len(reserved.encode()) > 24000:
            raise ValueError('Official grader prompt cap infeasible; no calls or exclusions')
    result = execute_batch(directory, plan, ctx, golds, batch_calls, resume,
                           runner_factory=OfficialLuna, payload_builder=payload)
    frozen(directory)
    atomic_json(directory / 'progress.json', {**result, 'plan_sha256': sha256(directory / 'plan.json')})
    return result


def report(directory, plan):
    ledger = Ledger(directory, sha256(directory / 'plan.json'))
    try:
        rows = ledger.validated()
        values = {r[1]: json.loads(r[3]) for r in rows if r[2] == 'complete'}
        if (ledger.meta('status') != 'complete' or len(values) != plan['tasks']
                or set(values) != {t for t, _, _, _ in tasks(plan)}):
            raise ValueError('Full paired denominator required; incomplete campaign is not scored')
        points = []
        for q in plan['selected']:
            scores = {a: values[f"{q['qid']}/{a}/correct"] for a in plan['arms']}
            if any(type(v) is not bool for v in scores.values()):
                raise ValueError('Boolean yes/no verdicts required')
            points.append({**q, **scores})
        def summarize(selected):
            return {'n': len(selected), 'accuracy': {a: estimate([(p['cluster'], int(p[a])) for p in selected], SEED,
                                      repetitions=10000, minimum_clusters=30) for a in plan['arms']},
                    'paired_delta_accuracy': estimate([(p['cluster'], int(p['memory']) - int(p['bm25'])) for p in selected],
                                                      SEED, repetitions=10000, minimum_clusters=30)}
        output = {'complete': True, **summarize(points), 'question_results': points,
                  'by_question_type': {t: summarize([p for p in points if p['question_type'] == t]) for t in sorted(TYPES)},
                  'abstention_subset': summarize([p for p in points if p['abstention']]),
                  'accounting': accounting(rows), 'models': [plan['reader'], plan['judge']],
                  'full_context_arm': 'Not executed: not feasible within this approved CLI protocol; external published reference only',
                  'external_reference': {'paper': 'https://arxiv.org/html/2501.13956v1', 'table': 2,
                                         'zep_gpt4o': .712, 'full_context_gpt4o': .602,
                                         'warning': 'Different reader, GPT-4o judge and methodology; not a head-to-head ranking'},
                  'status': plan['claim'], 'bootstrap_policy': EVIDENCE_POLICY,
                  'conditional_on_fixed_distractors': True, 'independent_confirmation': False,
                  'plan_sha256': sha256(directory / 'plan.json'), 'ledger_sha256': sha256(directory / 'campaign.sqlite3')}
        ci = output['paired_delta_accuracy']['ci95']
        output['descriptive_numerical_criterion_met'] = ci is not None and ci[0] > 0
        output['leaderboard_claim_allowed'] = False
        output['interval_unavailable_policy'] = 'No numerical criterion; report estimate and reason, never fallback to question bootstrap'
        output['hypotheses'] = {}
        for arm in plan['arms']:
            path = directory / f'hypotheses-{arm}.jsonl'
            records = [{'question_id': q['qid'], 'hypothesis': values[f"{q['qid']}/{arm}/answer"],
                        'autoeval_label': {'model': MODEL, 'label': values[f"{q['qid']}/{arm}/correct"]}}
                       for q in plan['selected']]
            rendered = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records)
            if path.exists() and path.read_text() != rendered:
                raise ValueError('Previously exported hypotheses changed')
            if not path.exists():
                with path.open('x') as stream:
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
            output['hypotheses'][arm] = {'file': path.name, 'sha256': sha256(path)}
    finally:
        ledger.close()
    atomic_json(directory / 'report.json', output)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'retrieve', '_retrieve', 'answer', 'report'])
    parser.add_argument('--plan-dir', required=True, type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--copy-proof', type=Path)
    parser.add_argument('--official-evaluator', type=Path)
    parser.add_argument('--batch-calls', type=int, default=40)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    _, directory = private_path(args.plan_dir)
    if args.action == 'prepare':
        if not args.source_commit or not args.copy_proof or not args.official_evaluator:
            parser.error('--source-commit, --copy-proof and --official-evaluator required')
        prepare(directory, args.source_commit, args.copy_proof, args.official_evaluator)
    elif args.action == 'retrieve':
        retrieve(directory)
    elif args.action == '_retrieve':
        plan = frozen(directory, check_cli=False)
        asyncio.run(retrieval(plan, directory / 'retrieval'))
    elif args.action == 'answer':
        print(json.dumps(answer(directory, args.batch_calls, args.resume)))
    else:
        print(json.dumps(report(directory, frozen(directory))))


if __name__ == '__main__':
    main()
