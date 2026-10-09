"""Protocol guards and causal reconstruction on small synthetic observations."""
import asyncio
from copy import deepcopy

import pytest

from bench.e2e.data import Gold, Inputs, Turn
from bench.e2e.lexical_dev import arms, eligible, parity, validate_config
from bench.e2e.lexical_diagnose import capture_case
from bench.e2e.configuration import resolved_config


def test_interventions_use_real_capture_and_exact_quote_parity(tmp_path):
    observations = Inputs('opaque', (
        Turn('s:0', 's', 'user', 'My café purchase was A 東京 apple.', '2024-01-01T00:00:00+00:00'),
        Turn('s:1', 's', 'assistant', 'This apple café recommendation ' * 100, '2024-01-01T00:00:00+00:00'),
    ), 'What café purchase was A 東京?', '2024-01-02T00:00:00+00:00')
    gold = Gold('q', ('s:0',), ('apple',), 'synthetic', ('s',), 0)
    result = asyncio.run(capture_case(observations, gold, tmp_path, 'unit-only'))
    assert result['exact_selected_quote_parity']
    assert result['episodes'] == 2 and result['quotes'] > 2
    assert result['capture']['added'] == result['quotes'] and result['deduped'] == 0
    assert result['chunk_owner_count'] == 0
    stages = {s['stage']: s for s in result['stages']}
    assert stages['payload_literal']['selected_quote_ids'] == stages['reference']['selected_quote_ids']
    assert result['indexes']['mixed_stem_scoring_only']['n_docs'] == result['quotes'] + 2
    assert result['indexes']['header_legacy_nostem']['query_tokens'] != result['indexes']['payload_literal']['query_tokens']


def test_arms_and_reference_profile_are_fixed():
    assert [a['id'] for a in arms()] == ['C1', 'C2', 'C3']
    cfg = resolved_config('hash', '/unused', arms()[0]['engine_overrides'])
    assert not cfg['use_dense'] and not cfg['use_entities']
    assert cfg['bm25_profile'] == 'scoped_literal' and not cfg['use_recency']
    with pytest.raises(ValueError, match='requires scoped_literal'):
        resolved_config('hash', '/unused', {'lexical_first': True})


def test_c1_exact_order_not_just_same_recall():
    rows = [{'qid': 'q', 'system': s, 'selected_quote_ids': ['a', 'b']}
            for s in ('memory', 'bm25')]
    assert parity(rows, ['q'])
    changed = deepcopy(rows)
    changed[0]['selected_quote_ids'].reverse()
    assert not parity(changed, ['q'])
    with pytest.raises(ValueError, match='Missing/duplicate'):
        parity(rows + [rows[0]], ['q'])


def test_no_winner_on_equal_recall_or_missing_interval():
    def report(recall, mrr):
        return {'paired_delta_memory': {'bm25': {'ci95': recall}},
                'paired_other_delta_memory': {'bm25': {'mrr': {'ci95': mrr}}}}
    assert eligible(report([.001, .03], [0, .05]))
    assert not eligible(report([0, .03], [0, .05]))
    assert not eligible(report([.001, .03], [-.001, .05]))
    assert not eligible(report(None, [0, .05]))


def test_frozen_config_rejects_protocol_and_source_drift():
    arm = arms()[0]
    plan = {'models': {'minilm': {'id': 'hash'}}, 'capture_namespace': 'unit',
            'selected_ids': ['q'], 'top_k': 10, 'context_bytes': 12000, 'chunk_chars': 1024,
            'trace_k': 100, 'warm_searches': 3, 'seed': 20261009, 'bootstrap': 2000,
            'dataset_sha256': 'd' * 64, 'code_files': {'test.py': 'digest'},
            'selection_sha256': 'e' * 64, 'arm_seconds': 3600, 'case_seconds': 300,
            'ingest_bytes': 16_000_000}
    cfg = {**{k: plan[k] for k in ('selected_ids', 'top_k', 'context_bytes', 'chunk_chars',
                                 'trace_k', 'warm_searches', 'seed')},
           'engine_overrides': arm['engine_overrides'], 'source_commit': 'a' * 40,
           'model_record': {'id': 'hash'}, 'run_id': 'unit', 'resident': True,
           'systems': ['memory', 'bm25'], 'reranker': None, 'query_mode': 'none',
           'protocol': 'e2e-resident-v2', 'transport': 'stdio', 'limit': 100,
           'code_files': plan['code_files'], 'selection_sha256': plan['selection_sha256'],
           'run_seconds': 3600, 'case_seconds': 300, 'ingest_bytes': 16_000_000,
           'unique_turns': False, 'bootstrap_replicates': 2000,
           'dataset': {'sha256': 'd' * 64}, 'resolved_engine_config':
           resolved_config('hash', '<private fresh store per question>', arm['engine_overrides'])}
    validate_config(plan, arm, cfg, 'a' * 40)
    for key, value in [('source_commit', 'b' * 40), ('query_mode', 'lexical'),
                       ('unique_turns', True), ('warm_searches', 1),
                       ('bootstrap_replicates', 100), ('seed', 1),
                       ('selected_ids', ['new']), ('resident', False),
                       ('transport', 'dispatch'), ('code_files', {}),
                       ('ingest_bytes', 999), ('limit', 99)]:
        with pytest.raises(ValueError, match='differs'):
            validate_config(plan, arm, {**cfg, key: value}, 'a' * 40)
