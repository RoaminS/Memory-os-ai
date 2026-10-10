"""Pilot contracts with synthetic data/events only; zero model calls."""
import json
from types import SimpleNamespace

import pytest

from bench.e2e.answer_pilot import select, validate_encoder
from bench.e2e.beam import CATEGORIES, Conversation, Probe
from bench.e2e.codex_reader import JUDGE_MODEL, capability, command, runtime_record, PilotStop
from bench.e2e.configuration import resolved_config
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.embed import Embedder, HashEmbedder


def test_ten_questions_cover_categories_and_ignore_outcomes():
    items = [Conversation(str(i), str(i), (), tuple(Probe(f'{i}-{j}', f'Question {j}',
                    SimpleNamespace(category=c), ()) for j, c in enumerate(CATEGORIES))) for i in range(20)]
    convs, ids = select(items)
    assert len(convs) == 2 and len(ids) == 10
    assert len({q.split('-')[1] for q in ids}) == 10
    assert select(list(reversed(items))) == (convs, ids)


def test_production_profile_does_not_change_observed_historical_plans():
    prod = resolved_config('hash', '/private/copy', profile='beam-c4-production-4.0.3')
    historic = resolved_config('hash', '/private/copy', profile='beam-c4-4.0.2')
    assert (prod['device'], prod['embed_dtype'], prod['embed_batch_size']) == ('auto', 'native', 32)
    assert (historic['device'], historic['embed_batch_size']) == ('cpu', 4)
    assert prod['lexical_guard_k'] == 3 and prod['bm25_profile'] == 'scoped_literal'


def test_stronger_judge_is_pinned_without_api_or_fallback(tmp_path):
    argv = command(tmp_path, tmp_path / 'schema', 'high', JUDGE_MODEL)
    assert argv[3] == 'gpt-6-astra' and argv[5] == 'model_reasoning_effort=high'
    assert 'forced_login_method="chatgpt"' in argv and 'model_provider="openai"' in argv
    with pytest.raises(ValueError):
        command(tmp_path, tmp_path / 'schema', 'low', JUDGE_MODEL)
    catalogue = tmp_path / 'models.json'
    catalogue.write_text(json.dumps({'models': [{'slug': JUDGE_MODEL, 'supported_reasoning_levels': [{'effort': 'high'}]}]}))
    assert capability('high', catalogue, model=JUDGE_MODEL)['supported']
    assert not capability('low', catalogue, model=JUDGE_MODEL)['supported']


def test_api_login_is_rejected_without_printing_credentials(monkeypatch):
    monkeypatch.setattr('bench.e2e.codex_reader.subprocess.run', lambda argv, **kw:
                        SimpleNamespace(returncode=0, stdout='version' if '--version' in argv else 'Logged in using an API key: masked', stderr=''))
    with pytest.raises(PilotStop, match='ChatGPT login'):
        runtime_record()


@pytest.mark.parametrize('field,value', [('device', 'cpu'), ('batch_size', 4), ('dtype', 'float32'),
                                        ('parameter_devices', ['cpu']), ('parameter_dtypes', []), ('model_revision', None)])
def test_pilot_rejects_encoder_mismatch_before_llm(field, value):
    profile = {'device': 'mps', 'dtype': 'native', 'batch_size': 32, 'max_seq_length': 512,
               'parameter_devices': ['mps'], 'parameter_dtypes': ['torch.bfloat16'],
               'model_revision': 'revision', 'runtime': {'torch': 'frozen'}}
    plan = {'runtime': profile['runtime'], 'encoder': {'id': '/snapshots/revision'}}
    assert validate_encoder({'encoding_profile': profile}, plan) == profile
    with pytest.raises(ValueError):
        validate_encoder({'encoding_profile': {**profile, field: value}}, plan)


def test_actual_parameter_precision_not_numpy_output(monkeypatch):
    encoder = Embedder('fake', device='cpu', dtype='native')
    model = SimpleNamespace(parameters=lambda: iter([SimpleNamespace(dtype='torch.bfloat16', device=SimpleNamespace(type='cpu'))]))
    monkeypatch.setattr(encoder, '_ensure_model', lambda: model)
    profile = encoder.encoding_profile()
    assert profile['parameter_dtypes'] == ['torch.bfloat16'] and profile['output_dtype'] == 'float32'
    assert profile['model_revision'] is None


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_attested_runtime_change_rebuilds_corpus_once(tmp_path, monkeypatch, backend):
    import memory_os_v4.memory as module
    calls = []
    revision = ['first']
    class Fake(HashEmbedder):
        def encoding_profile(self):
            return {'parameter_dtypes': [revision[0]], 'model_revision': 'same-weights'}
        def encode(self, texts):
            calls.extend(texts)
            return super().encode(texts)
    monkeypatch.setattr(module, '_make_embedder', lambda cfg: Fake())
    cfg = Config(data_dir=str(tmp_path), embed_model='hash', store_backend=backend)
    with MemoryV4(config=cfg) as engine:
        engine.add_facts([{'text': 'Immutable record', 'item_id': 'fact'}], event_time=100)
        assert engine.status()['encoding_profile']['parameter_dtypes'] == ['first']
    revision[0] = 'changed'
    calls.clear()
    with MemoryV4(config=cfg) as engine:
        assert engine.store.get('fact').text == 'Immutable record'
    assert len(calls) == 1
    calls.clear()
    with MemoryV4(config=cfg):
        pass
    assert calls == []


def test_complete_pilot_is_40_calls_without_reader_gold_leak(tmp_path, monkeypatch):
    import bench.e2e.answer_pilot as pilot
    from bench.e2e.data import sha256
    plan = {'question_ids': [str(i) for i in range(10)], 'arms': ['memory', 'bm25'],
            'claim': 'descriptive', 'reader': {'model': 'gpt-5.6-luna', 'effort': 'low'},
            'judge': {'model': 'gpt-6-astra', 'effort': 'high'}}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    rdir = tmp_path / 'retrieval'
    rdir.mkdir()
    rows = [{'qid': str(i), 'question': f'Question {i}', 'contexts': {'memory': ['observable'], 'bm25': ['observable']}} for i in range(10)]
    (rdir / 'contexts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (rdir / 'encoder-attestation.json').write_text('{}')
    (rdir / 'report.json').write_text(json.dumps({'complete': True, 'contexts_sha256': sha256(rdir / 'contexts.jsonl'),
        'encoder_sha256': sha256(rdir / 'encoder-attestation.json'), 'plan_sha256': sha256(tmp_path / 'plan.json')}))
    monkeypatch.setattr(pilot, 'private_path', lambda d: (tmp_path.parent, d))
    monkeypatch.setattr(pilot, 'frozen', lambda d: plan)
    monkeypatch.setattr(pilot, 'sample', lambda d: {})
    monkeypatch.setattr(pilot, 'ensure_priority', lambda limits: None)
    monkeypatch.setattr(pilot, 'validate_encoder', lambda s, p: {})
    probes = [SimpleNamespace(id=str(i), question=f'Question {i}',
                            gold=SimpleNamespace(answers=('SCORER_SECRET',), category='category'), rubric=('RUBRIC_SECRET',)) for i in range(10)]
    monkeypatch.setattr(pilot, 'conversations', lambda data: [SimpleNamespace(probes=probes)])
    plan['data'] = str(tmp_path / 'fake')
    class FakeReader:
        def __init__(self, root, effort, check, **kw):
            assert effort == 'low' and kw['judge_model'] == 'gpt-6-astra' and kw['judge_effort'] == 'high'
            assert kw['max_calls'] == 40 and kw['seconds'] == 3600
            self.calls, self.attempts, self.tokens = [], 0, 0
        def record(self):
            self.attempts += 1
            self.calls.append({'seconds': 1})
        def answer(self, question, context):
            assert 'SECRET' not in repr((question, context))
            self.record()
            return 'descriptive answer'
        def judge(self, question, answer, reference, rubric):
            assert reference == 'SCORER_SECRET' and rubric == ['RUBRIC_SECRET']
            self.record()
            return True
    monkeypatch.setattr(pilot, 'CodexReader', FakeReader)
    pilot.read_and_judge(tmp_path)
    result = json.loads((tmp_path / 'answers/report.json').read_text())
    assert result['model_calls'] == 40 and result['accuracy'] == {'memory': 1, 'bm25': 1}
    assert result['ci95'] is None and result['api_usd'] == 0
    with pytest.raises(FileExistsError):
        pilot.read_and_judge(tmp_path)
