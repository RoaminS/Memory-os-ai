"""Regression tests for scoring, preregistration, isolation and failure guards."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from bench.e2e import answers, data, diagnose, experiments, partitions, pipeline, planning, worker
from bench.e2e.configuration import resolved_config
from bench.e2e.guard import ResourceStop


def gold(qid, sessions=(), category='preference'):
    return data.Gold(qid, ('turn',), ('Paris',), category, sessions, 0)


def test_response_proxies_normalization_aliases_and_multiset():
    assert answers.response_metrics('THE Paris!', ('paris',)) == {
        'answer_em': 1., 'answer_f1': 1., 'answer_contains': 1.}
    m = answers.response_metrics('I visited Paris yesterday', ('Paris',))
    assert m == {'answer_em': 0., 'answer_f1': .4, 'answer_contains': 1.}
    assert answers.response_metrics('Paris', ('Lyon', 'Paris'))['answer_em'] == 1
    assert answers.response_metrics('bike bike', ('bike',))['answer_f1'] == pytest.approx(2 / 3)
    assert answers.normalize('ＳＡＭＳＵＮＧ Galaxy—S22') == 'samsung galaxy s22'


def test_contains_token_boundaries_contiguity_empty_and_negation_limitation():
    assert answers.response_metrics('312 bikes', ('12',))['answer_contains'] == 0
    assert answers.response_metrics('New very big York', ('New York',))['answer_contains'] == 0
    assert answers.response_metrics(None, ('Paris',))['answer_f1'] == 0
    assert answers.response_metrics('x', ('!!!',))['answer_em'] is None
    # This known limitation is intentional, documented as a nonofficial proxy.
    assert answers.response_metrics('not Paris', ('Paris',))['answer_contains'] == 1


def test_dev_partition_no_shared_sources_no_fake_test():
    gs = [gold('a', ('shared',)), gold('b', ('shared',))]
    gs += [gold(str(i), (str(i),), 'user') for i in range(20)]
    plan = partitions.split(gs, 'sha')
    assert plan == partitions.split(list(reversed(gs)), 'sha')
    dev = partitions.validate(plan, gs, 'sha')
    assert ('a' in dev) == ('b' in dev)
    assert plan['test_ids'] == []
    assert dev.isdisjoint(plan['quarantine_observed_ids'])
    for mutated in ({**plan, 'dataset_sha256': 'other'},
                    {**plan, 'dev_ids': plan['quarantine_observed_ids']}):
        with pytest.raises(ValueError, match='development'):
            partitions.validate(mutated, gs, 'sha')


@pytest.mark.parametrize('overrides', [{'data_dir': '/production'}, {'use_dense': 1},
                                      {'w_dense': -1}, {'w_bm25': float('nan')},
                                      {'candidates': 201}, {'rerank_weight': 2},
                                      {'fusion': 'unknown'}])
def test_options_cannot_change_store_or_escape_budgets(overrides):
    with pytest.raises(ValueError):
        resolved_config('hash', '/private', overrides)


def test_fixed_five_treatments_rerank_has_no_unscaled_tail():
    arms = experiments.arms()
    assert len(arms) == 6  # five treatments plus current control
    e3 = next(a for a in arms if a['id'].startswith('E3'))
    cfg = resolved_config('cached', '/private', e3['engine_overrides'], 'cached-reranker')
    assert cfg['candidates'] == cfg['rerank_top_n'] == 30
    assert cfg['use_dense'] is cfg['use_entities'] is False
    assert cfg['rerank_weight'] == 1
    assert cfg['device'] == cfg['rerank_device'] == 'cpu'


def test_multiquery_only_observed_question_and_bounded():
    q = 'How long did I work in the Alpine sales team?'
    assert planning.extra_queries(q, 'none') == []
    queries = planning.extra_queries(q, 'lexical')
    assert 1 <= len(queries) <= 2
    assert all(len(s) <= 300 for s in queries)
    assert 'FUTURE-GOLD' not in str(queries)


def test_context_diversity_still_shares_byte_and_quote_caps():
    qs = [pipeline.Quote(str(i), 'first' if i < 3 else 'second', 'x' * 10, 'date') for i in range(4)]
    assert len(pipeline.pack(qs, 2, 100)) == 2
    assert [q.turn_id for q in pipeline.pack(qs, 2, 100, unique_turns=True)] == ['first', 'second']
    assert len(pipeline.pack(qs, 2, 11, unique_turns=True)) == 1


def test_selected_late_question_is_not_cut_off_by_source_index(tmp_path, monkeypatch):
    gs = [gold(str(i)) for i in range(4)]
    monkeypatch.setattr(worker, 'cases', lambda _: iter((SimpleNamespace(id=g.id), g) for g in gs))
    async def evaluate(*args):
        return {'status': 'ok', 'retrieved_turn_ids': ['turn'], 'response': 'Paris'}
    monkeypatch.setattr(worker, 'evaluate', evaluate)
    out = tmp_path / 'rows'
    cfg = {'data': 'unused', 'selected_ids': ['3'], 'selection_filter': True,
           'limit': 1, 'case_seconds': 1, 'top_k': 10}
    asyncio.run(worker.run(cfg, 'memory', out, tmp_path))
    assert json.loads(out.read_text())['qid'] == '3'


def test_resource_stop_prevents_campaign_or_directory_creation(tmp_path, monkeypatch):
    def stop(*_):
        raise ResourceStop('swap unavailable')
    monkeypatch.setattr(experiments, 'ensure_priority', stop)
    assert experiments.main(['prepare', '--data', str(tmp_path / 'missing'),
                             '--output', str(tmp_path / 'new'), '--source-commit', 'a' * 40]) == 2
    assert not (tmp_path / 'new').exists()


def test_diagnostic_keeps_na_and_exposes_latency_without_overclaim():
    g = gold('a')
    rows = [{'system': 'memory', 'qid': 'a', 'status': 'ok', 'recall': 0., 'mrr': 0.,
             'ndcg': 0., 'response': 'I visited Paris yesterday', 'retrieved_turn_ids': [],
             'init_ms': 10, 'ingest_ms': 5, 'retrieval_ms': 1, 'end_to_end_ms': 20},
            {'system': 'bm25', 'qid': 'a', 'status': 'ok', 'recall': 1., 'mrr': 1.,
             'ndcg': 1., 'response': 'Paris', 'retrieved_turn_ids': ['turn']}]
    report, rescored, examples = diagnose.diagnostic(rows, [g], repetitions=0)
    assert report['mrr_questions'] == {'loss': 1, 'gain': 0, 'tie': 0}
    assert report['latency']['unattributed_ms']['sum_ms'] == 4
    assert rescored[0]['answer_f1'] == .4
    assert examples[0]['delta_mrr'] == -1
    assert report['paired_other_delta_memory']['bm25']['mrr']['mean'] == -1


def test_preregistration_change_stops_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, 'private_path', lambda path: (tmp_path, path))
    (tmp_path / 'plan.json').write_text(json.dumps({'arms': []}))
    (tmp_path / 'receipt.json').write_text(json.dumps({'plan_sha256': 'wrong'}))
    with pytest.raises(ValueError, match='Preregistration'):
        experiments.execute(tmp_path, lambda: None)


def test_missing_rerank_signal_fails_instead_of_silent_success(tmp_path, monkeypatch):
    t = data.Turn('turn', 'session', 'user', 'Paris', '2023-01-01T00:00:00+00:00')
    inputs = data.Inputs('q', (t,), 'What city?', t.date)
    class Client:
        def __init__(self, *args):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def call(self, name, payload):
            if name == 'memory_chat_save':
                self.id = payload['facts'][0]['item_id']
                return {'facts': {'added': [self.id], 'deduped': [], 'superseded': []}}
            return {'hits': [{'id': self.id, 'text': 'Paris', 'score': 1, 'signals': {}}]}
    monkeypatch.setattr(worker, 'ToolClient', Client)
    cfg = {'run_id': 'run', 'model': 'hash', 'transport': 'dispatch', 'reranker': 'cached',
           'top_k': 10, 'context_bytes': 12000, 'ingest_bytes': 10000}
    with pytest.raises(RuntimeError, match='silent fallback'):
        asyncio.run(worker.evaluate(inputs, 'memory', cfg, tmp_path))


def test_rerank_counter_enforces_cpu_sequence_and_batch_budget(tmp_path, monkeypatch):
    import sys
    from bench.e2e import usage
    class Encoder:
        def encode(self, sentences, **kwargs):
            return []
    class Cross:
        max_length = 999
        def tokenizer(self, left, right, **kwargs):
            assert kwargs['max_length'] == 256 and kwargs['truncation']
            assert len(left) <= 4
            return {'attention_mask': SimpleNamespace(sum=lambda: len(left) * 3)}
        def predict(self, pairs, **kwargs):
            assert kwargs['batch_size'] == 4 and self.max_length == 256
            return [1.] * len(pairs)
    monkeypatch.setitem(sys.modules, 'sentence_transformers', SimpleNamespace(
        SentenceTransformer=Encoder, CrossEncoder=Cross))
    monkeypatch.setattr(usage, '_installed', False)
    monkeypatch.setattr(usage, '_counts', {})
    monkeypatch.setattr(usage, '_path', None)
    path = tmp_path / 'usage'
    usage.install(path, 'cached')
    assert Cross().predict([('q', 'text')] * 5) == [1.] * 5
    counts = usage.read(path)
    assert counts['reranker_calls'] == 1 and counts['reranker_pairs'] == 5
    assert counts['reranker_input_tokens'] == 15


def test_campaign_stops_after_failed_arm_without_trying_next(tmp_path, monkeypatch):
    monkeypatch.setattr(experiments, 'private_path', lambda p: (tmp_path, p))
    monkeypatch.setattr(experiments, 'verify_source', lambda *a: None)
    records = {name: {'id': str(path)} for name, path in experiments.MODELS.items()}
    monkeypatch.setattr(experiments, 'model_record', lambda p, **kw: {'id': str(p)})
    d = tmp_path / 'dataset'
    d.write_text('[]')
    selection = tmp_path / 'selection.json'
    selection.write_text('{}')
    from bench.e2e.data import sha256
    plan = {'arms': experiments.arms(), 'data': str(d), 'dataset_sha256': sha256(d),
            'models': records, 'source_commit': 'a' * 40, 'systems': ['memory', 'bm25'],
            'trace_k': 100, 'warm_searches': 3, 'run_seconds_per_arm': 1500,
            'selection_sha256': sha256(selection)}
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan))
    (tmp_path / 'receipt.json').write_text(json.dumps({
        'plan_sha256': sha256(plan_path), 'selection_sha256': sha256(selection)}))
    called = []
    monkeypatch.setattr(experiments, 'run_campaign', lambda args: called.append(args) or 1)
    with pytest.raises(ResourceStop, match='no later arm'):
        experiments.execute(tmp_path, lambda: None)
    assert len(called) == 1
    assert '--selection' in called[0]
    assert not (tmp_path / 'campaign-complete.json').exists()


@pytest.mark.parametrize('warm_searches,trace_k,expected_calls', [(3, 0, 5), (0, 100, 3)])
def test_warm_queries_count_cost_without_replacing_first_prediction(tmp_path, monkeypatch,
                                                                    warm_searches, trace_k, expected_calls):
    t = data.Turn('turn', 's', 'user', 'Paris', '2023-01-01T00:00:00+00:00')
    inputs = data.Inputs('q', (t,), 'What city?', t.date)
    class Client:
        def __init__(self, *args):
            self.searches = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def call(self, name, payload):
            if name == 'memory_chat_save':
                self.quote = payload['facts'][0]
                return {'facts': {'added': [self.quote['item_id']], 'deduped': [], 'superseded': []}}
            self.searches += 1
            # Warm calls deliberately disagree: they must never change scoring.
            return {'hits': [{'id': self.quote['item_id'], 'text': self.quote['text'],
                              'score': 1, 'signals': {}}] if self.searches == 1 else []}
    monkeypatch.setattr(worker, 'ToolClient', Client)
    cfg = {'run_id': 'run', 'model': 'hash', 'transport': 'dispatch', 'warm_searches': warm_searches, 'trace_k': trace_k,
           'top_k': 10, 'context_bytes': 12000, 'ingest_bytes': 10000}
    row = asyncio.run(worker.evaluate(inputs, 'memory', cfg, tmp_path))
    assert row['retrieved_turn_ids'] == ['turn']
    assert row['tool_calls'] == expected_calls and len(row['warm_search_ms']) == warm_searches
    assert row['primary_request_k'] == 10
    assert row['shutdown_ms'] >= 0
