"""ABBA/equivalence and isolated subprocess smoke with hash only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import numpy as np
import pytest

from bench.e2e import indexing_mps_campaign as campaign
from bench.e2e.configuration import resolved_config


EQ = {'minimum_clusters': 3, 'bootstrap': 1000, 'seed': 4, 'ci': .975, 'margin': .01}


def quality(delta=0):
    return [{'qid': str(i), 'conversation': str(i // 2),
             'cpu': {'recall': .5, 'mrr': .5},
             'mps': {'recall': .5 + delta, 'mrr': .5 + delta}} for i in range(6)]


def test_equivalence_requires_both_ci_inside_frozen_margin():
    assert campaign.equivalent(quality(), EQ)['equivalent']
    assert not campaign.equivalent(quality(-.02), EQ)['equivalent']
    assert not campaign.equivalent(quality(.02), EQ)['equivalent']


def test_missing_cluster_ci_is_inconclusive_never_passes():
    result = campaign.equivalent(quality()[:2], EQ)
    assert result['clusters'] == 1 and not result['equivalent']
    assert result['metrics']['recall']['ci'] is None


def test_private_encoding_profile_is_complete_and_never_tunes_c4():
    cfg = resolved_config('hash', 'private', profile='beam-c4-4.0.2', encoding=campaign.ENCODING['mps'])
    assert cfg['device'] == 'mps' and cfg['embed_dtype'] == 'float32'
    assert cfg['lexical_guard_k'] == 3 and cfg['embed_max_seq_length'] == 512
    assert cfg['batch_facts'] is False and cfg['use_recency'] is False
    with pytest.raises(ValueError):
        resolved_config('hash', 'private', profile='beam-c4-4.0.2', encoding={'device': 'mps'})


def artifacts(directory):
    plan = {'selected': [{'id': str(i), 'probes': [str(i)]} for i in range(3)],
            'jobs': ['ab', 'ba'], 'targets': {'minimum_paired_speedup': 1.5},
            'equivalence': EQ, 'claim': 'development only'}
    for job in plan['jobs']:
        root = directory / 'source/.local-qualification/measurement' / job
        rows = []
        for i in range(3):
            arms = {}
            for arm in campaign.ENCODING:
                (root / arm).mkdir(parents=True, exist_ok=True)
                campaign.write(root / arm / f'{i}.json', {'ids': ['a'], 'items': [], 'receipts': []})
                np.save(root / arm / f'{i}.npy', np.ones((1, 1), dtype=np.float32))
                arms[arm] = {'ingest_seconds': 800 if arm == 'cpu' else 200 + 50 * i,
                    'capture': {'added': 1}, 'usage': {'llm_calls': 0},
                    'quality': [{'qid': str(i), 'memory': {'recall': 1., 'mrr': 1.},
                                 'bm25': {'recall': 1., 'mrr': 1.}}]}
            rows.append({'id': str(i), 'arms': arms})
        campaign.write(root / 'complete.json', {'rows': rows})
    return plan


def test_ab_ba_repeats_are_not_six_independent_clusters(tmp_path):
    result = campaign.compare(tmp_path, artifacts(tmp_path))
    assert result['criterion_met']
    assert result['quality']['clusters'] == 3 and result['quality']['questions'] == 3
    assert result['performance']['p50'] == 250


def test_performance_failure_keeps_complete_report(tmp_path):
    plan = artifacts(tmp_path)
    p = tmp_path / 'source/.local-qualification/measurement/ba/complete.json'
    obj = json.loads(p.read_text())
    obj['rows'][0]['arms']['mps']['ingest_seconds'] = 700
    campaign.write(p, obj)
    result = campaign.compare(tmp_path, plan)
    assert result['complete'] and not result['criterion_met']


def test_raw_identity_difference_blocks_even_if_quality_equal(tmp_path):
    plan = artifacts(tmp_path)
    p = tmp_path / 'source/.local-qualification/measurement/ba/mps/0.json'
    campaign.write(p, {'items': [{'text': 'Changed provenance'}]})
    with pytest.raises(ValueError, match='provenance'):
        campaign.compare(tmp_path, plan)


def test_direct_file_worker_from_untracked_cwd_imports_correctly_hash_only():
    from bench.e2e.beam import CATEGORIES, conversations
    from bench.e2e.client import offline_env
    from bench.e2e.processes import cleanup
    clone = Path(campaign.__file__).resolve().parents[3]
    private = clone / '.local-qualification'
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mps-hash-smoke-', dir=private) as name:
        root = Path(name)
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
        plan = {'model': {'id': 'hash'}, 'data': str(data), 'selected': [{'id': c.id}],
                'encoding': campaign.ENCODING, 'schedule': {'ab': [[c.id, ['cpu', 'mps']]]},
                'cooldown_seconds': 0, 'chunk_chars': 1024, 'top_k': 10, 'context_bytes': 12000}
        campaign.write(root / 'plan.json', plan)
        output = root / 'source/.local-qualification/measurement/ab'
        output.mkdir(parents=True)
        registry = output / 'server-registry'
        env = offline_env()
        env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(registry),
                   PYTHONPATH=os.pathsep.join([str(clone / 'v4/src'), str(clone / 'v4')]))
        try:
            result = subprocess.run([sys.executable, campaign.__file__, '_worker', '--plan-dir', str(root),
                                     '--job', 'ab'], env=env, cwd=output, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            output_rows = json.loads((output / 'complete.json').read_text())['rows']
            assert output_rows[0]['arms']['mps']['usage']['llm_calls'] == 0
            assert len(output_rows[0]['arms']['cpu']['quality']) == 10
        finally:
            cleanup(registry, env['EVAL_PROCESS_OWNER'])
