"""Production-configured C4 order/filters/restarts using only the hash encoder."""
from dataclasses import replace

import pytest

from memory_os_v4 import Config, MemoryV4


def cfg(root, **kw):
    return Config(data_dir=str(root), embed_model='hash', bm25_profile='scoped_literal',
                  use_entities=False, use_recency=False, lexical_guard_k=3, **kw)


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_guard_preserves_three_lexical_items_and_fused_tail(tmp_path, monkeypatch, backend):
    config = cfg(tmp_path / 'store', store_backend=backend, w_bm25=.01, w_dense=1.0)
    with MemoryV4(config=config) as engine:
        engine.add_facts([{'item_id': str(i), 'text': 'apple ' * (6 - i) + 'pear ' * i}
                          for i in range(6)], project='P')
        engine.add_facts([{'item_id': 'dense', 'text': 'semantic candidate'},
                          {'item_id': 'none', 'text': 'no signal'}], project='P')
        engine.add_facts([{'item_id': 'foreign', 'text': 'apple'}], project='OTHER')
        dense = [('foreign', 99), ('dense', 1.0), ('5', .9), ('4', .8), ('3', .7),
                 ('2', .3), ('1', .2), ('0', .1)]
        monkeypatch.setattr(engine.retriever, '_dense_candidates', lambda q, n: dense)
        config.lexical_guard_k = 0
        fused = engine.search('apple', top_k=8, project='P', kinds=['fact'])
        assert fused[0].item.id == 'dense'
        config.lexical_guard_k = 3
        lexical = [i for i, _ in engine.retriever.lexical_index(['fact'], 'P').search(['apple'], 8)]
        anchors = lexical[:3]
        hits = engine.search('apple', top_k=5, project='P', kinds=['fact'])
        expected = anchors + [h.item.id for h in fused if h.item.id not in anchors]
        assert [h.item.id for h in hits] == expected[:5]
        assert 'dense' in expected[:5] and 'foreign' not in expected
        assert len({h.item.id for h in hits}) == 5
        assert [h.score for h in hits] == [1 / i for i in range(1, 6)]
        assert all(h.signals['lexical_guard_anchor'] for h in hits[:3])
        assert not any(h.signals['lexical_guard_anchor'] for h in hits[3:])
        assert [h.item.id for h in engine.search('apple', top_k=2, kinds=['fact'], project='P')] == anchors[:2]


def test_guard_is_final_after_reranker_and_handles_no_lexical_match(tmp_path, monkeypatch):
    with MemoryV4(config=cfg(tmp_path / 'store')) as engine:
        engine.add_facts([{'item_id': 'a', 'text': 'apple red'},
                          {'item_id': 'b', 'text': 'apple green'},
                          {'item_id': 'c', 'text': 'semantic candidate'}], project='P')
        monkeypatch.setattr(engine.retriever, '_dense_candidates', lambda q, n: [('c', 1), ('b', .8), ('a', .2)])
        monkeypatch.setattr(engine.retriever, '_rerank_hits', lambda q, hits, rerank: sorted(hits, key=lambda h: h.item.id, reverse=True))
        assert [h.item.id for h in engine.search('apple', project='P')] == ['a', 'b', 'c']
        hits = engine.search('absent', project='P')
        assert [h.item.id for h in hits] == ['c', 'b', 'a']
        assert not any(h.signals['lexical_guard_anchor'] for h in hits)


@pytest.mark.parametrize('backend', ['jsonl', 'sqlite_s1'])
def test_env_profile_switch_reopen_and_rollback_without_text_encoding(tmp_path, monkeypatch, backend):
    monkeypatch.setenv('MEMORY_V4_DIR', str(tmp_path / 'store'))
    monkeypatch.setenv('MEMORY_V4_EMBED_MODEL', 'hash')
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', backend)
    monkeypatch.setenv('MEMORY_V4_BM25_PROFILE', 'legacy')
    monkeypatch.setenv('MEMORY_V4_LEXICAL_GUARD_K', '0')
    with MemoryV4(data_dir=str(tmp_path / 'store')) as engine:
        engine.add_facts([{'item_id': 'a', 'text': '東京 apple'},
                          {'item_id': 'b', 'text': 'apple'},
                          {'item_id': 'c', 'text': 'pear'}], project='P')
    from memory_os_v4.embed import HashEmbedder
    original = HashEmbedder.encode
    def query_only(self, texts):
        assert list(texts) in (['東京'], ['apple'])
        return original(self, texts)
    monkeypatch.setattr(HashEmbedder, 'encode', query_only)
    monkeypatch.setenv('MEMORY_V4_BM25_PROFILE', 'scoped_literal')
    monkeypatch.setenv('MEMORY_V4_LEXICAL_GUARD_K', '3')
    monkeypatch.setenv('MEMORY_V4_FUSION', 'cc')
    with MemoryV4(data_dir=str(tmp_path / 'store')) as engine:
        assert engine.config.lexical_guard_k == 3 and engine.config.fusion == 'cc'
        assert engine.search('東京', project='P')[0].item.id == 'a'
        assert len(engine.store.items) == 3
    monkeypatch.setenv('MEMORY_V4_BM25_PROFILE', 'legacy')
    monkeypatch.setenv('MEMORY_V4_LEXICAL_GUARD_K', '0')
    with MemoryV4(data_dir=str(tmp_path / 'store')) as engine:
        assert engine.search('apple', project='P')
        assert len(engine.store.items) == 3


@pytest.mark.parametrize('options', [dict(lexical_guard_k=-1), dict(lexical_guard_k=101),
    dict(lexical_guard_k=True), dict(lexical_guard_k=1.5), dict(bm25_profile='legacy'),
    dict(use_bm25=False), dict(lexical_first=True)])
def test_invalid_policies_fail_before_creating_store(tmp_path, options):
    root = tmp_path / 'never-created'
    with pytest.raises(ValueError, match='lexical_guard_k'):
        MemoryV4(config=replace(cfg(root), **options))
    assert not root.exists()


def test_c4_defaults_and_sqlite_stays_opt_in():
    config = Config()
    assert config.bm25_profile == 'scoped_literal' and config.lexical_guard_k == 3
    assert config.store_backend == 'jsonl'
    config.validate_lexical_policy()


@pytest.mark.parametrize('name,value', [('MEMORY_V4_BM25_PROFILE', 'legacy'),
    ('MEMORY_V4_USE_BM25', 'false'), ('MEMORY_V4_LEXICAL_FIRST', 'true')])
def test_existing_env_alternative_opts_out_of_default_guard(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    config = Config.from_env()
    assert config.lexical_guard_k == 0
    config.validate_lexical_policy()
    monkeypatch.setenv('MEMORY_V4_LEXICAL_GUARD_K', '3')
    with pytest.raises(ValueError):
        Config.from_env().validate_lexical_policy()


def test_benchmark_historical_base_is_not_redefined_by_release_defaults():
    from bench.e2e.configuration import resolved_config
    config = resolved_config('hash', '/unused')
    assert config['bm25_profile'] == 'legacy' and config['lexical_guard_k'] == 0
    c4 = resolved_config('hash', '/unused', {'bm25_profile': 'scoped_literal', 'lexical_guard_k': 3})
    assert c4['lexical_guard_k'] == 3
