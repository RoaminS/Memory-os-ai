"""No models/network: resident isolation, immutable reuse and confirmation gates."""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.e2e import amendment, data, resident, run, worker
from bench.e2e.guard import ResourceStop


@dataclass
class Config:
    data_dir: str
    embed_model: str = 'cached'
    device: str = 'cpu'
    embed_max_seq_length: int = 256
    embed_batch_size: int = 4
    embed_dtype: str = 'native'
    hash_dim: int = 32
    rerank_model: str | None = None


def pool_fixture(tmp_path):
    loads, closed = [], []
    encoder = object()
    def factory(cfg):
        loads.append(cfg.embed_model)
        return encoder
    memory = SimpleNamespace(_make_embedder=factory)
    class Engine:
        def __init__(self, config):
            self.config = config
            Path(config.data_dir).mkdir(parents=True)
            self.embedder = memory._make_embedder(config)
            self.store = SimpleNamespace(items={})
            self.retriever = SimpleNamespace(_reranker=None)
        def close(self):
            assert Path(self.config.data_dir).exists()  # close BEFORE cleanup
            closed.append(self.config.data_dir)
    memory.MemoryV4 = Engine
    server = SimpleNamespace(engine=None, TOOL_MODELS={}, dispatch=lambda n, a: {'ordinary': n})
    def set_engine(e):
        if server.engine is not None:
            server.engine.close()
        server.engine = e
    server.set_engine, server.get_engine = set_engine, lambda: server.engine
    pool = resident.ResidentPool(memory, server, Config(str(tmp_path / 'store')))
    pool.install()
    return pool, server, loads, closed


def test_resident_reuses_one_model_but_never_previous_conversation(tmp_path):
    pool, server, loads, closed = pool_fixture(tmp_path)
    assert pool.dispatch('memory_search', {}) == {'ordinary': 'memory_search'}
    first = pool.dispatch('eval_open_conversation', {'conversation_id': 'a' * 24})
    assert first['encoder_reused'] and first['store_empty']
    server.engine.store.items['secret-from-conversation-one'] = 'Paris'
    old = Path(server.engine.config.data_dir)
    second = pool.dispatch('eval_open_conversation', {'conversation_id': 'b' * 24})
    assert second['encoder_reused'] and second['store_empty']
    assert not old.exists() and len(loads) == 1 and len(closed) == 2
    assert not pool.dispatch('eval_open_conversation', {'conversation_id': 'a' * 24})['ok']


@pytest.mark.parametrize('arguments', [{'conversation_id': '../production'},
                                      {'conversation_id': 'a' * 24, 'data_dir': '/production'},
                                      {'conversation_id': 123}])
def test_private_reset_cannot_choose_a_path_or_extra_config(tmp_path, arguments):
    pool, server, loads, closed = pool_fixture(tmp_path)
    assert pool.dispatch('eval_open_conversation', arguments)['ok'] is False
    assert len(loads) == 1 and not closed


def test_resident_model_config_is_immutable_and_symlinks_not_deleted(tmp_path):
    pool, server, loads, closed = pool_fixture(tmp_path)
    with pytest.raises(ValueError, match='encoder configuration'):
        pool.factory(Config(str(tmp_path / 'store'), embed_model='different'))
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'keep').write_text('keep')
    link = tmp_path / 'link'
    link.symlink_to(foreign, target_is_directory=True)
    pool.previous = link
    assert pool.dispatch('eval_open_conversation', {'conversation_id': 'b' * 24})['ok'] is False
    assert (foreign / 'keep').read_text() == 'keep'


def test_usage_deltas_do_not_charge_startup_again():
    before = {'encoder_calls': 2, 'encoder_input_tokens': 20, 'encoder_ms': 10,
              'reranker_batch_size': 4}
    after = {**before, 'encoder_calls': 7, 'encoder_input_tokens': 120, 'encoder_ms': 110}
    delta = worker.usage_delta(after, before)
    assert delta['encoder_calls'] == 5 and delta['encoder_input_tokens'] == 100
    assert delta['encoder_ms'] == 100 and delta['reranker_batch_size'] == 4


def test_one_mcp_start_for_two_conversations_and_correctly_scoped_metrics(tmp_path, monkeypatch):
    entered, opened = [], []
    turns = [data.Turn(str(i), str(i), 'user', f'City {i}', '2023-01-01T00:00:00+00:00') for i in range(2)]
    inputs = [data.Inputs(str(i) * 24, (t,), 'What city?', t.date) for i, t in enumerate(turns)]
    gs = [data.Gold(str(i), (t.id,), (f'City {i}',), 'user', (t.session,), 0) for i, t in enumerate(turns)]
    monkeypatch.setattr(worker, 'cases', lambda _: iter(zip(inputs, gs)))
    class Client:
        def __init__(self, root, *args):
            self.root = root
        async def __aenter__(self):
            entered.append(1)
            return self
        async def __aexit__(self, *args):
            pass
        async def call(self, name, payload):
            if name == 'eval_open_conversation':
                opened.append(payload['conversation_id'])
                self.quote = None
                return {'encoder_reused': True, 'store_empty': True,
                        'conversation_close_ms': 1, 'conversation_init_ms': 2}
            if name == 'memory_chat_save':
                self.quote = payload['facts'][0]
                return {'facts': {'added': [self.quote['item_id']], 'deduped': [], 'superseded': []}}
            return {'hits': [{'id': self.quote['item_id'], 'text': self.quote['text'], 'score': 1}]}
    monkeypatch.setattr(worker, 'ToolClient', Client)
    cfg = {'data': 'unused', 'limit': 2, 'selected_ids': ['0', '1'], 'case_seconds': 2,
           'model': 'hash', 'transport': 'stdio', 'resident': True, 'run_id': 'run',
           'top_k': 10, 'ingest_bytes': 10000, 'context_bytes': 12000}
    out = tmp_path / 'memory.jsonl'
    asyncio.run(worker.run(cfg, 'memory', out, tmp_path))
    rows = [json.loads(s) for s in out.read_text().splitlines()]
    assert len(entered) == 1 and opened == ['0' * 24, '1' * 24]
    assert [r['retrieved_turn_ids'] for r in rows] == [['0'], ['1']]
    assert json.loads((tmp_path / 'memory-lifecycle.json').read_text())['startup_ms'] >= 0


@pytest.mark.parametrize('exception,classification', [(TimeoutError('budget'), 'budget'),
                                                       (RuntimeError('wrong provenance'), 'correctness')])
def test_resident_error_is_recorded_and_stops_before_next_question(tmp_path, monkeypatch, exception, classification):
    gs = [data.Gold(str(i), ('t',), ('x',), 'user', (), 0) for i in range(2)]
    monkeypatch.setattr(worker, 'cases', lambda _: iter((SimpleNamespace(id=g.id), g) for g in gs))
    called = []
    async def evaluate(*a):
        called.append(1)
        raise exception
    monkeypatch.setattr(worker, 'evaluate', evaluate)
    cfg = {'data': 'unused', 'limit': 2, 'case_seconds': 2, 'resident': True, 'top_k': 10}
    out = tmp_path / 'rows'
    with pytest.raises(type(exception)):
        asyncio.run(worker.run_cases(cfg, 'bm25', out, tmp_path))
    assert called == [1] and json.loads(out.read_text())['failure_class'] == classification


def test_time_budget_distinguished_from_corruption_and_resource_stop():
    assert run.termination_class('Frozen wall-time budget exceeded') == 'budget'
    assert run.termination_class('Worker memory failed', {'memory': 3}) == 'budget'
    assert run.termination_class('Worker memory failed', {'memory': 1}) == 'correctness'
    assert run.termination_class('Frozen inputs changed') == 'correctness'
    assert run.termination_class('swap 9000000000 > 8500000000 bytes') == 'resource'


def campaign_fixture(directory):
    directory.mkdir()
    cfg = {'selected_ids': ['q'], 'source_commit': 'a' * 40}
    report = {'complete': True, 'all_cases_executed_without_failure': True, 'selected_count': 1}
    (directory / 'frozen.json').write_text(json.dumps(cfg))
    report['frozen_sha256'] = data.sha256(directory / 'frozen.json')
    (directory / 'report.json').write_text(json.dumps(report))
    rows = [{'qid': 'q', 'system': s, 'status': 'ok'} for s in ('memory', 'bm25')]
    (directory / 'predictions.jsonl').write_text('\n'.join(map(json.dumps, rows)))
    (directory / 'proofs.json').write_text(json.dumps({
        n: data.sha256(directory / n) for n in ('report.json', 'frozen.json', 'predictions.jsonl')}))
    return directory


def test_reuse_rejects_changed_artifacts_and_wrong_denominators(tmp_path):
    directory = campaign_fixture(tmp_path / 'C0')
    report, cfg, proof = amendment.valid_complete(directory, lambda: None, ['q'])
    assert report['selected_count'] == 1
    with pytest.raises(ValueError, match='question ids'):
        amendment.valid_complete(directory, lambda: None, ['other'])
    (directory / 'predictions.jsonl').write_text('modified')
    with pytest.raises(ValueError, match='hash mismatch'):
        amendment.valid_complete(directory, lambda: None)


@pytest.mark.parametrize('classification,continues', [('budget', True), ('correctness', False), ('resource', False)])
def test_only_budget_failure_is_nonblocking(tmp_path, classification, continues):
    directory = tmp_path / 'plan'
    directory.mkdir()
    (directory / 'plan.json').write_text('{}')
    output = directory / 'E1'
    output.mkdir()
    (output / 'aborted.json').write_text(json.dumps({'termination_class': classification, 'reason': 'stop'}))
    plan = {'source_commit': 'a' * 40}
    arm = {'id': 'E1'}
    if continues:
        result = amendment.record_outcome(plan, directory, arm, output, 2, lambda: None)
        assert result['status'] == 'infaisable'
    else:
        with pytest.raises(ResourceStop):
            amendment.record_outcome(plan, directory, arm, output, 2, lambda: None)
        assert not (directory / 'E1-outcome.json').exists()


def test_resume_skips_receipted_arms_and_budget_does_not_skip_following(tmp_path, monkeypatch):
    arm1, arm2 = amendment.arms()[1:3]
    (tmp_path / 'plan.json').write_text('{}')
    plan = {'version': 'resident-dev-v2', 'arms': amendment.arms()[1:]}
    monkeypatch.setattr(amendment, 'load_plan', lambda *a: plan)
    monkeypatch.setattr(amendment, 'check_inputs', lambda *a: None)
    monkeypatch.setattr(amendment, 'campaign_args', lambda p, d, a, o: [a['id']])
    launched, received = [], []
    monkeypatch.setattr(amendment, 'run_campaign', lambda args: launched.append(args[0]) or 1)
    def record(p, d, a, o, status, check):
        received.append(a['id'])
        (d / (a['id'] + '-outcome.json')).write_text('{}')
        return {'status': 'infaisable'}
    monkeypatch.setattr(amendment, 'record_outcome', record)
    monkeypatch.setattr(amendment, 'verify_outcome', lambda *a: {'status': 'infaisable'})
    amendment.execute_dev(tmp_path, 'source', lambda: None)
    assert launched[:2] == [arm1['id'], arm2['id']] and len(launched) == 5
    amendment.execute_dev(tmp_path, 'source', lambda: None)
    assert len(launched) == 5  # resume does not retry infeasible or completed arms


def test_winner_fixed_by_paired_deltas_not_memory_recall_alone(tmp_path, monkeypatch):
    candidates = [{'id': 'E1'}, {'id': 'E2'}, {'id': 'E3'}]
    reports = {
        'E1': (0.02, -.01, 100), 'E2': (.03, .0, 200), 'E3': (.03, .01, 300)}
    def outcome(plan, d, arm, check):
        r, m, lat = reports[arm['id']]
        return {'status': 'complete', 'proof': {'id': arm['id']}, 'report': {
            'paired_delta_memory': {'bm25': {'mean': r}},
            'paired_other_delta_memory': {'bm25': {'mrr': {'mean': m}}},
            'systems': {'memory': {'end_to_end_ms': {'p50': lat}}}}}
    monkeypatch.setattr(amendment, 'verify_outcome', outcome)
    result = amendment.select_winner({'arms': candidates, 'selection_rule': 'fixed'}, tmp_path, lambda: None)
    assert result['arm']['id'] == 'E3'


def test_confirmation_freezes_one_winner_before_reading_S_and_rejects_second(tmp_path, monkeypatch):
    devdir, out = tmp_path / 'dev', tmp_path / 'confirmation'
    devdir.mkdir()
    (devdir / 'plan.json').write_text('{}')
    dev = {'version': 'resident-dev-v2', 'oracle_question_ids': ['0'], 'models': {}}
    winner = {'arm': {'id': 'E2', 'model': 'minilm'}, 'development_proof': {}, 'rule': 'fixed'}
    monkeypatch.setattr(amendment, 'load_plan', lambda *a: dev)
    monkeypatch.setattr(amendment, 'check_inputs', lambda *a: None)
    monkeypatch.setattr(amendment, 'select_winner', lambda *a: winner)
    monkeypatch.setattr(amendment, 'private_path', lambda path: (tmp_path, path))
    dataset = tmp_path / 's'
    dataset.write_text('test input')
    monkeypatch.setattr(amendment, 'S_PATH', dataset)
    monkeypatch.setattr(amendment, 'S_SHA', data.sha256(dataset))
    def inventory(*a, **kw):
        assert json.loads((out / 'winner.json').read_text()) == winner
        return {'questions': 500}
    monkeypatch.setattr(amendment, 'inventory', inventory)
    gs = [data.Gold(str(i), ('t',), ('answer',), 'user', ('shared-distractor',), 0) for i in range(500)]
    monkeypatch.setattr(amendment, 'cases', lambda *a, **kw: iter((None, g) for g in gs))
    plan = amendment.prepare_confirmation(devdir, out, 'source', lambda: None)
    assert plan['oracle_question_overlap'] == ['0'] and not plan['independent_questions']
    assert plan['observed_source_clusters'] == 1 and len(plan['selected_ids']) == 500
    assert plan['trace_k'] == 0 and plan['run_seconds'] == 172800
    with pytest.raises(ValueError, match='One frozen S candidate'):
        amendment.prepare_confirmation(devdir, tmp_path / 'second', 'source', lambda: None)


def test_frozen_arm_rejects_changed_engine_or_retrieval_budget():
    from bench.e2e.configuration import resolved_config
    plan = {'source_commit': 'a' * 40, 'seed': 17, 'models': {'minilm': {'id': 'hash'}},
            'top_k': 10, 'context_bytes': 12000, 'trace_k': 0, 'warm_searches': 1,
            'selected_ids': ['q'], 'dataset_sha256': 'data'}
    arm = {'id': 'E2', 'model': 'minilm', 'engine_overrides': {'w_dense': .25}}
    cfg = {'source_commit': 'a' * 40, 'seed': 17, 'run_id': run.capture_namespace(17),
           'model_record': {'id': 'hash'}, 'top_k': 10, 'context_bytes': 12000,
           'trace_k': 0, 'warm_searches': 1, 'selected_ids': ['q'], 'dataset': {'sha256': 'data'},
           'resident': True, 'protocol': 'e2e-resident-v2', 'systems': ['memory', 'bm25'],
           'chunk_chars': 1024, 'unique_turns': False, 'query_mode': 'none', 'reranker': None,
           'engine_overrides': {'w_dense': .25},
           'resolved_engine_config': resolved_config('hash', '<private fresh store per question>', {'w_dense': .25})}
    amendment.validate_arm_config(plan, arm, cfg)
    with pytest.raises(ValueError, match='preregistration'):
        amendment.validate_arm_config(plan, arm, {**cfg, 'top_k': 100})
    with pytest.raises(ValueError, match='Resolved engine'):
        amendment.validate_arm_config(plan, arm, {**cfg, 'resolved_engine_config': {}})


def test_guard_refuses_preregistration_before_reading_dataset(tmp_path, monkeypatch):
    def stop(*a):
        raise ResourceStop('nice unavailable')
    monkeypatch.setattr(amendment, 'ensure_priority', stop)
    assert amendment.main(['prepare-dev', '--source-commit', 'a' * 40,
                           '--previous', str(tmp_path / 'missing'), '--output', str(tmp_path / 'new')]) == 2
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('recall,mrr,overlap,expected', [([.01, .03], [0., .02], [], True),
                                                     ([0., .03], [0., .02], [], False),
                                                     ([.01, .03], [-.001, .02], [], False),
                                                     (None, None, [], False),
                                                     ([.01, .03], [0., .02], ['observed'], False)])
def test_confirmation_requires_both_bounds_and_independent_questions(recall, mrr, overlap, expected):
    plan = {'independent_questions': not overlap, 'oracle_question_overlap': overlap,
            'observed_source_clusters': 1 if recall is None else 100}
    outcome = {'status': 'complete', 'report': {'paired_delta_memory': {'bm25': {'ci95': recall}},
               'paired_other_delta_memory': {'bm25': {'mrr': {'ci95': mrr}}}}}
    verdict = amendment.confirmation_verdict(plan, outcome)
    assert verdict['independent_confirmation_passed'] is expected
    if overlap:
        assert verdict['numerical_criterion_met'] and not verdict['independent_confirmation_passed']
