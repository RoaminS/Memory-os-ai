"""Per-question lexical interventions on PRIVATE dev captures; never loads ML."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from statistics import mean

from .baselines import BM25
from .client import project_for
from .pipeline import extract, pack, tokens
from .stats import score


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def saved_audit(directory, selected_ids):
    """Read existing traces only; no indexing, capture or performance campaign."""
    wanted = set(selected_ids)
    rows = {}
    for system in ('memory', 'bm25'):
        with (directory / f'{system}.jsonl').open() as stream:
            for line in stream:
                row = json.loads(line)
                if row['qid'] in wanted:
                    key = (system, row['qid'])
                    if key in rows or row['status'] != 'ok':
                        raise ValueError('Historical rows duplicate/failed')
                    rows[key] = row
    if set(rows) != {(s, q) for s in ('memory', 'bm25') for q in wanted}:
        raise ValueError('Historical selected traces incomplete')
    details = []
    for qid in selected_ids:
        a, b = rows['memory', qid], rows['bm25', qid]
        trace = a['candidate_trace'] or a['rank_trace']
        lexical = [h for h in trace if h['signals']['bm25']['score'] is not None]
        global_ranks = [h['signals']['bm25']['rank'] for h in lexical]
        details.append({'qid': qid, 'memory_turns': len(a['retrieved_turn_ids']),
                        'reference_turns': len(b['retrieved_turn_ids']),
                        'delta_recall': a['recall'] - b['recall'] if a['recall'] is not None else None,
                        'delta_mrr': a['mrr'] - b['mrr'] if a['mrr'] is not None else None,
                        'facts_visible_in_global_pool': len(lexical),
                        'global_rank_1_missing_from_fact_trace': bool(global_ranks) and min(global_ranks) > 1,
                        'reranker_executed': any('rerank' in h['signals'] for h in a['rank_trace']),
                        'reference_quotes_missing_from_visible_pool': sorted(
                            set(b['selected_quote_ids']) - {h['id'] for h in trace}),
                        'scope': 'saved bounded trace, NOT exhaustive corpus attribution'})
    return {'n': len(details), 'reranked': sum(d['reranker_executed'] for d in details),
            'rank1_not_visible_questions': sum(d['global_rank_1_missing_from_fact_trace'] for d in details),
            'mean_visible_facts': mean(d['facts_visible_in_global_pool'] for d in details),
            'memory_less_than_10_turns': sum(d['memory_turns'] < 10 for d in details),
            'mean_memory_turns': mean(d['memory_turns'] for d in details),
            'mean_reference_turns': mean(d['reference_turns'] for d in details),
            'details': details}


async def capture_case(inputs, gold, root, namespace, saved=None, check=lambda: None):
    """Real ChatSave handler/validation, with hash encoder and fresh JSONL store.

    Gold reaches only the stage scorer after rankings are finalized. Costs here
    are diagnostic HASH costs, never substituted for the real MCP campaign.
    """
    from memory_os_v4 import Config, MemoryV4, server
    from .leaderboard import AddSearchAdapter
    project = project_for(namespace, inputs.id)
    quotes = extract(project, inputs.turns, 1024)
    class Client:
        async def call(self, name, payload):
            check()
            return server.dispatch(name, payload)
    cfg = Config(data_dir=str(root / 'store'), embed_model='hash',
                 use_dense=False, use_entities=False, use_recency=False)
    engine = MemoryV4(config=cfg)
    server.set_engine(engine)
    try:
        captured = await AddSearchAdapter(Client(), namespace)._capture(inputs.id, inputs.turns, quotes)
        check()
        baseline_root = root / 'baseline'
        baseline_root.mkdir()
        reference = BM25(quotes, baseline_root, 'hash')
        return interventions(engine, inputs, gold, quotes, captured, reference, saved, check)
    finally:
        server.set_engine(None)


def interventions(engine, inputs, gold, quotes, captured, reference, saved, check):
    from memory_os_v4.bm25 import BM25Index
    from memory_os_v4.memory import index_text
    from memory_os_v4.text import tokenize
    project = engine.store.get(quotes[0].id).project if quotes else None
    by_id = {q.id: q for q in quotes}
    items = engine.store.all(include_deleted=False)
    facts = [it for it in items if it.kind == 'fact' and it.project == project]
    if set(by_id) != {it.id for it in facts} or captured['deduped']:
        raise RuntimeError('Quote identity/provenance lost at capture')
    stages = []
    def record(name, ids, global_hits=()):
        check()
        ranked = [by_id[i] for i in ids]
        selected = pack(ranked, 10, 12000)
        tids = list(dict.fromkeys(q.turn_id for q in selected))
        measured = score(tids, None, gold, 10)
        stages.append({'stage': name, 'ranked_quote_ids': ids[:100],
                       'selected_quote_ids': [q.id for q in selected],
                       'turn_ids': tids, 'effective_top_k_turns': len(tids),
                       'byte_cap_truncated': len(selected) < min(10, len(ranked)),
                       'recall': measured['recall'], 'mrr': measured['mrr'],
                       'evidence_found': sorted(set(tids) & set(gold.evidence)),
                       'global_top_before_kind_filter': [
                           {'id': i, 'kind': engine.store.get(i).kind, 'score': s}
                           for i, s in global_hits]})
    record('reference', [q.id for q in reference.search(inputs.question, 100)])
    indexes = {}
    for name, literal, header, stem, mixed, cap in [
        ('payload_literal', True, False, False, False, None),
        ('header_literal', True, True, False, False, None),
        ('header_legacy_nostem', False, True, False, False, None),
        ('header_legacy_stem', False, True, True, False, None),
        ('mixed_stem_scoring_only', False, True, True, True, None),
        ('mixed_stem_top100_before_filter', False, True, True, True, 100),
        ('mixed_nostem_scoring_only', False, True, False, True, None),
        ('E3_raw_top30_before_filter', False, True, False, True, 30),
    ]:
        check()
        tok = tokens if literal else lambda text: tokenize(text, stem=stem)
        idx = BM25Index()
        for item in items if mixed else facts:
            idx.add(item.id, tok(index_text(item, engine.config) if header else item.text))
        hits = idx.search(tok(inputs.question), cap or idx.n_docs)
        record(name, [i for i, _ in hits if i in by_id], hits[:cap or 100] if mixed else ())
        indexes[name] = {'n_docs': idx.n_docs, 'avgdl': idx.avgdl,
                         'query_tokens': tok(inputs.question),
                         'postings_df': {t: len(idx.postings.get(t, {})) for t in tok(inputs.question)}}
        if name == 'payload_literal':
            payload_index = idx
    # Execute corrected ENGINE retrieval, rather than swapping the benchmark.
    engine.bm25 = engine.retriever.bm25 = payload_index
    engine.config = engine.retriever.config = replace(engine.config, bm25_profile='scoped_literal')
    hits = engine.search(inputs.question, top_k=100, project=project, kinds=['fact'], rerank=False)
    record('corrected_engine', [h.item.id for h in hits])
    if saved is not None:
        record('E3_observed_after_CE', saved['ranked_quote_ids'])
    reference_found = set(stages[0]['evidence_found'])
    for i, stage in enumerate(stages):
        found = set(stage['evidence_found'])
        previous = set(stages[i - 1]['evidence_found']) if i else found
        stage['lost_vs_reference'] = sorted(reference_found - found)
        stage['gained_vs_reference'] = sorted(found - reference_found)
        stage['lost_vs_previous'] = sorted(previous - found)
        stage['gained_vs_previous'] = sorted(found - previous)
    corrected = next(s for s in stages if s['stage'] == 'corrected_engine')
    parity = corrected['selected_quote_ids'] == stages[0]['selected_quote_ids']
    if not parity:
        raise RuntimeError('Corrected engine/reference parity failed')
    return {'qid': gold.id, 'capture': captured, 'turns': len(inputs.turns),
            'quotes': len(quotes), 'episodes': sum(it.kind == 'episode' for it in items),
            'indexed_items': len(items), 'unit': 'one fact per 1024-character client quote; one episode per raw turn',
            'deduped': captured['deduped'], 'chunk_owner_count': len(engine.store.chunk_owner),
            'filters': {'project': project, 'kinds': ['fact'], 'at_time': None},
            'quote_provenance': [{'id': q.id, 'turn_id': q.turn_id,
                                  'text_sha256': hashlib.sha256(q.text.encode()).hexdigest(),
                                  'header': index_text(engine.store.get(q.id), engine.config).split('\n')[0],
                                  'literal_tokens_sha256': digest(tokens(q.text)),
                                  'legacy_tokens_sha256': digest(tokenize(q.text))} for q in quotes],
            'indexes': indexes, 'stages': stages, 'exact_selected_quote_parity': parity,
            'limits': 'diagnostic hash dispatch; CE is historical, not a fresh randomized intervention'}
