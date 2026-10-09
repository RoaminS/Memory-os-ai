"""Real capture, scoped retrieval and derived-cache regressions; no neural model."""
from dataclasses import replace

import pytest

from bench.e2e.baselines import BM25
from bench.e2e.pipeline import Quote
from memory_os_v4 import Config, MemoryV4
from memory_os_v4.bm25 import BM25Index
from memory_os_v4.models import ChatSaveIn
from memory_os_v4.server import _h_chat_save
from memory_os_v4.text import lexical_tokens


def config(root, **kw):
    return Config(data_dir=str(root), embed_model='hash', use_dense=False,
                  use_entities=False, use_recency=False, candidates=2,
                  bm25_profile='scoped_literal', **kw)


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_real_chat_capture_equals_reference_despite_episodes_and_other_projects(tmp_path, backend):
    quotes = [Quote('a', 't:0', 'Café A 2024-02-01 apple apple', '2024-02-01'),
              Quote('b', 't:1', 'Café B apple banana', '2024-02-01'),
              Quote('c', 't:2', 'naïve 東京 C', '2024-02-01'),
              Quote('d', 't:3', 'the a I user assistant', '2024-02-01')]
    baseline_root = tmp_path / 'baseline'
    baseline_root.mkdir()
    baseline = BM25(quotes, baseline_root, 'hash')
    with MemoryV4(config=config(tmp_path / 'store', store_backend=backend)) as engine:
        _h_chat_save(engine, ChatSaveIn(project_label='P', event_time='2024-02-01T00:00:00Z',
                       messages=[{'role': 'user', 'content': 'apple ' * 50}],
                       facts=[{'item_id': q.id, 'text': q.text} for q in quotes]))
        engine.add_facts([{'item_id': f'foreign-{i}', 'text': f'apple Café {i}'}
                          for i in range(30)], project='OTHER')
        for query in ['Café apple', 'A', '2024-02-01', '東京', 'the a I', 'absent', 'apple apple']:
            hits = engine.search(query, top_k=10, kinds=['fact'], project='P', rerank=False)
            assert [h.item.id for h in hits] == [q.id for q in baseline.search(query, 10)]
            scoped = engine.retriever.lexical_index(['fact'], 'P')
            assert scoped.n_docs == 4
            assert [h.score for h in hits] == sorted([h.score for h in hits], reverse=True)
            for hit in hits:
                assert hit.score == scoped.score_doc(lexical_tokens(query, engine.config), hit.item.id)


def test_scoped_statistics_and_mutations_are_independent():
    idx = BM25Index()
    idx.add('a', ['apple', 'apple'])
    idx.add('b', ['apple', 'pear'])
    scope = idx.scoped({'a'})
    assert scope.avgdl == 2 and scope.n_docs == 1
    idx.remove('a')
    assert scope.search(['apple'], 10) == [('a', scope.score_doc(['apple'], 'a'))]


def test_scope_cache_tracks_same_count_validity_and_index_replacement(tmp_path):
    with MemoryV4(config=config(tmp_path / 'store')) as engine:
        engine.add_facts([{'item_id': 'a', 'text': 'apple'}], project='P', event_time=10)
        first = engine.retriever.lexical_index(['fact'], 'P', 20)
        engine.store.get('a').valid_until = 15
        assert engine.retriever.lexical_index(['fact'], 'P', 20).n_docs == 0
        engine.store.get('a').valid_until = None
        engine.bm25.add('a', ['pear'])
        assert engine.retriever.lexical_index(['fact'], 'P', 20).search(['apple'], 10) == []
        assert first.search(['apple'], 10)
        rebuilt = BM25Index()
        rebuilt.add('a', ['banana'])
        engine.bm25 = engine.retriever.bm25 = rebuilt
        assert engine.retriever.lexical_index(['fact'], 'P', 20).search(['banana'], 10)


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_reopen_profile_change_rebuilds_lexical_without_encoding(tmp_path, monkeypatch, backend):
    cfg = config(tmp_path / 'store', store_backend=backend)
    with MemoryV4(config=replace(cfg, bm25_profile='legacy')) as engine:
        engine.add_facts([{'item_id': 'a', 'text': '東京 I A Café'}], project='P')
        engine.flush()
    from memory_os_v4.embed import HashEmbedder
    def forbidden(*args, **kwargs):
        raise AssertionError('Lexical rebuild must not encode stored texts')
    monkeypatch.setattr(HashEmbedder, 'encode', forbidden)
    with MemoryV4(config=cfg) as engine:
        assert [h.item.id for h in engine.search('東京', project='P', kinds=['fact'])] == ['a']
        engine.flush()
    with MemoryV4(config=cfg) as engine:
        assert [h.item.id for h in engine.search('I', project='P', kinds=['fact'])] == ['a']


def test_lexical_first_keeps_prefix_and_fills_missing_with_dense(tmp_path):
    cfg = config(tmp_path / 'store', lexical_first=True)
    cfg.use_dense = True
    with MemoryV4(config=cfg) as engine:
        engine.add_facts([{'item_id': 'a', 'text': 'apple apple'},
                          {'item_id': 'b', 'text': 'apple'},
                          {'item_id': 'c', 'text': 'pear'}], project='P')
        monkey = lambda qvec, n: [('c', 1.0), ('b', .5), ('a', .1)]
        engine.retriever._dense_candidates = monkey
        hits = engine.search('apple', top_k=3, project='P', kinds=['fact'])
        assert [h.item.id for h in hits] == ['a', 'b', 'c']
        assert [h.score for h in hits] == [1, .5, 1 / 3]


def test_environment_and_invalid_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORY_V4_BM25_PROFILE', 'scoped_literal')
    monkeypatch.setenv('MEMORY_V4_LEXICAL_FIRST', '1')
    assert Config.from_env().lexical_first
    with pytest.raises(ValueError, match='Unknown BM25 profile'):
        MemoryV4(config=replace(config(tmp_path / 'bad'), bm25_profile='typo'))
    assert not (tmp_path / 'bad').exists()
