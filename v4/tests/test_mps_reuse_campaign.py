"""Historical control integrity, narrow overlays and hash-only archived stdio."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import pytest

from bench.e2e import mps_reuse_campaign as campaign
from bench.e2e.configuration import resolved_config
from bench.e2e.data import sha256


def reference(root, monkeypatch):
    old, bridge = root / 'cpu', root / 'bridge'
    (old / 'retrieval').mkdir(parents=True)
    bridge.mkdir()
    rows = [{'qid': str(i), 'conversation': str(i % 20),
             **{s: {'recall': None if i < 45 else 1., 'mrr': None if i < 45 else 1.}
                for s in ('memory', 'bm25')}} for i in range(400)]
    (old / 'retrieval/predictions.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    (old / 'retrieval/contexts.jsonl').write_text('')
    cfg = resolved_config('hash', '<private-store>', profile='beam-c4-4.0.2')
    cfg.pop('embed_dtype')
    cfg.pop('batch_facts')
    model = {'id': 'hash', 'max_seq_length': 512}
    shared = {'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024}
    plan = {'version': 'beam-c4-retrieval-v2', 'source_commit': campaign.CPU_SOURCE,
            'dataset_sha256': campaign.DATA_SHA, 'model_record': model, 'engine_config': cfg, **shared}
    campaign.write(old / 'plan.json', plan)
    campaign.write(old / 'receipt.json', {'plan_sha256': sha256(old / 'plan.json')})
    campaign.write(old / 'retrieval/report.json', {'complete': True, 'questions': 400,
        'recall_questions': 355, 'clusters': 20, 'plan_sha256': sha256(old / 'plan.json'),
        'predictions_sha256': sha256(old / 'retrieval/predictions.jsonl'),
        'contexts_sha256': sha256(old / 'retrieval/contexts.jsonl')})
    monkeypatch.setattr(campaign, 'runtime', lambda: {'frozen': True})
    campaign.write(bridge / 'plan.json', {'model': model, 'runtime': campaign.runtime(),
        'data_sha256': campaign.DATA_SHA, **shared,
        'encoding': {'cpu': {'device': 'cpu', 'embed_dtype': 'native', 'embed_batch_size': 4, 'batch_facts': False}}})
    campaign.write(bridge / 'receipt.json', {'plan_sha256': sha256(bridge / 'plan.json')})
    for job in ('ab', 'ba'):
        p = bridge / 'source/.local-qualification/measurement' / job
        p.mkdir(parents=True)
        campaign.write(p / 'complete.json', {'rows': [{'arms': {a: {'quality':
            [r for r in rows if r['conversation'] == str(i)]} for a in ('cpu', 'mps')}} for i in range(3)]})
    return old, bridge, rows


def test_full_beam_reference_and_exact_60_question_bridge(tmp_path, monkeypatch):
    old, bridge, _ = reference(tmp_path, monkeypatch)
    plan, rows, proof = campaign.validate_reference(old, bridge)
    assert len(rows) == 400 and len(proof) == 9
    assert plan['source_commit'] == campaign.CPU_SOURCE


def test_other_model_or_context_never_reused_as_granite(tmp_path, monkeypatch):
    old, bridge, _ = reference(tmp_path, monkeypatch)
    p = bridge / 'plan.json'
    data = json.loads(p.read_text())
    data['model']['max_seq_length'] = 256
    campaign.write(p, data)
    with pytest.raises(ValueError, match='Granite'):
        campaign.validate_reference(old, bridge)


def test_modified_historical_predictions_stop_before_any_worker(tmp_path, monkeypatch):
    old, bridge, _ = reference(tmp_path, monkeypatch)
    with (old / 'retrieval/predictions.jsonl').open('a') as f:
        f.write('{}\n')
    with pytest.raises(ValueError, match='evidence changed'):
        campaign.validate_reference(old, bridge)


def test_bridge_metric_drift_blocks_reference_reuse(tmp_path, monkeypatch):
    old, bridge, _ = reference(tmp_path, monkeypatch)
    p = bridge / 'source/.local-qualification/measurement/ab/complete.json'
    d = json.loads(p.read_text())
    d['rows'][0]['arms']['cpu']['quality'][0]['memory']['recall'] = .4
    campaign.write(p, d)
    with pytest.raises(ValueError, match='Recent CPU scores'):
        campaign.validate_reference(old, bridge)


def test_nonrepeatable_mps_candidate_cannot_be_reinterpreted_as_same_arm(tmp_path, monkeypatch):
    old, bridge, _ = reference(tmp_path, monkeypatch)
    p = bridge / 'source/.local-qualification/measurement/ba/complete.json'
    d = json.loads(p.read_text())
    d['rows'][0]['arms']['mps']['quality'][0]['memory']['recall'] = .4
    campaign.write(p, d)
    with pytest.raises(ValueError, match='not repeatable'):
        campaign.validate_reference(old, bridge)


def test_overlay_changes_only_three_reviewed_files():
    clone = Path(campaign.__file__).resolve().parents[3]
    payload = campaign.archive(clone, campaign.CPU_SOURCE)
    files = campaign.overlay(payload, (clone / 'v4/bench/e2e/usage.py').read_bytes())
    before = campaign.records(payload)
    from hashlib import sha256 as digest
    changed = {name.removeprefix('v4/') for name, value in files.items()
               if name.endswith('.py') and digest(value).hexdigest() != before[name.removeprefix('v4/')]}
    assert changed == {'src/memory_os_v4/embed.py', 'bench/e2e/configuration.py', 'bench/e2e/usage.py'}
    assert b'rerank_device="cpu"' in files['v4/bench/e2e/configuration.py']
    assert b'embed_model=model, device="mps"' in files['v4/bench/e2e/configuration.py']
    with pytest.raises(ValueError, match='Archived source'):
        # An unrelated tar payload must not silently receive the overlay.
        import io
        import tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            for name in ('v4/bench/e2e/configuration.py', 'v4/src/memory_os_v4/embed.py'):
                info = tarfile.TarInfo(name)
                info.size = 4
                tar.addfile(info, io.BytesIO(b'pass'))
        campaign.overlay(stream.getvalue(), b'pass')


def test_archived_source_worker_runs_from_untracked_cwd_with_hash_only():
    from bench.e2e.beam import CATEGORIES, conversations
    clone = Path(campaign.__file__).resolve().parents[3]
    private = clone / '.local-qualification'
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='reuse-archived-hash-', dir=private) as name:
        root = Path(name)
        payload = campaign.archive(clone, campaign.CPU_SOURCE)
        files = campaign.overlay(payload, (clone / 'v4/bench/e2e/usage.py').read_bytes())
        for name, content in files.items():
            p = root / 'source' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        probes = {}
        for cat in CATEGORIES:
            key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
                   'instruction_following': 'expected_compliance', 'preference_following': 'expected_compliance',
                   'summarization': 'ideal_summary'}.get(cat, 'answer')
            probes[cat] = [{'question': f'Observed preference for {cat}?', key: 'scorer-only',
                           'rubric': ['scorer-only'], **({} if cat == 'abstention' else {'source_chat_ids': [0]})}]
        row = {'conversation_id': 'synthetic', 'conversation_seed': 'SECRET', 'narratives': 'SECRET',
               'user_profile': 'SECRET', 'conversation_plan': 'SECRET', 'user_questions': 'SECRET',
               'chat': [[{'content': 'A recorded preference.', 'role': 'user', 'id': 0,
                         'index': 'SECRET', 'question_type': 'SECRET', 'time_anchor': None}]],
               'probing_questions': repr(probes)}
        data = root / 'data.jsonl'
        data.write_text(json.dumps(row) + '\n')
        c = next(conversations(data))
        cfg = resolved_config('hash', '<private-store>', profile='beam-c4-4.0.2')
        cfg.pop('embed_dtype')
        cfg.pop('batch_facts')
        # This fixture targets the immutable historical CPU archive, not today's Config.
        cfg.pop('maintenance_mode')
        campaign.write(root / 'plan.json', {'model': {'id': 'hash'}, 'cpu_config': cfg,
            'data': str(data), 'namespace': 'beam-c4-v1-capture', 'chunk_chars': 1024,
            'top_k': 10, 'context_bytes': 12000, 'selected': [{'id': c.id, 'probes': [p.id for p in c.probes]}]})
        output = root / 'source/.local-qualification/measurement'
        output.mkdir(parents=True)
        registry = output / 'server-registry'
        env = campaign.offline_env()
        env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(registry),
                   PYTHONPATH=os.pathsep.join([str(root / 'source/v4/src'), str(root / 'source/v4')]))
        try:
            result = subprocess.run([sys.executable, campaign.__file__, '_worker', '--plan-dir', str(root)],
                                    env=env, cwd=output, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            done = json.loads((output / 'complete.json').read_text())
            assert done['questions'] == 10 and done['usage']['llm_calls'] == 0
        finally:
            campaign.cleanup(registry, env['EVAL_PROCESS_OWNER'])


def comparison(root, monkeypatch):
    old, _, rows = reference(root, monkeypatch)
    output = root / 'candidate/source/.local-qualification/measurement'
    output.mkdir(parents=True)
    (output / 'predictions.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    campaign.write(output / 'complete.json', {'usage': {'llm_calls': 0},
        'captures': [{'id': str(i), 'ingest_seconds': 40.} for i in range(20)]})
    plan = {'cpu_reference': str(old), 'equivalence': campaign.EQUIVALENCE, 'claim': 'test only',
            'selected': [{'id': str(i), 'probes': [r['qid'] for r in rows if r['conversation'] == str(i)]}
                         for i in range(20)]}
    return root / 'candidate', plan, output


def test_historical_join_full_denominator_and_twenty_cluster_equivalence(tmp_path, monkeypatch):
    directory, plan, _ = comparison(tmp_path, monkeypatch)
    result = campaign.compare(directory, plan)
    assert result['criterion_met'] and result['questions'] == 400
    assert result['quality']['questions'] == 355 and result['quality']['clusters'] == 20
    assert result['quality']['metrics']['recall']['ci'] == [0., 0.]
    assert result['performance']['max'] == 40.


@pytest.mark.parametrize('failure', ['bm25', 'duplicate', 'unpaired'])
def test_historical_join_rejects_changed_controls_or_denominators(tmp_path, monkeypatch, failure):
    directory, plan, output = comparison(tmp_path, monkeypatch)
    path = output / 'predictions.jsonl'
    rows = campaign.lines(path)
    if failure == 'bm25':
        rows[100]['bm25']['recall'] = .8
    elif failure == 'duplicate':
        rows[100] = rows[101]
    else:
        rows[100]['memory']['recall'] = None
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    with pytest.raises(ValueError):
        campaign.compare(directory, plan)
