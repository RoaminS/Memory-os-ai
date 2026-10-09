"""Copy policy and amended preflight on tiny synthetic corpora, no encoder."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from bench.e2e import data, lexical_s_v2 as amended, pipeline, stats
from bench.e2e.lexical_c4 import ARM, DEV_FIXED, SHARED


def entry():
    session = [{'role': 'user', 'content': 'Paris', 'has_answer': True},
               {'role': 'assistant', 'content': 'Visit a museum'}]
    return {'question_id': 'q', 'question_date': '2023/05/30 (Tue) 00:00',
            'question_type': 'synthetic', 'question': 'What city?', 'answer': 'Paris',
            'haystack_sessions': [session, deepcopy(session)], 'haystack_session_ids': ['s', 's'],
            'haystack_dates': ['2023/05/23 (Tue) 00:00', '2023/05/29 (Mon) 00:00'],
            'answer_session_ids': ['s']}


def read(tmp_path, e):
    path = tmp_path / 'data.json'
    path.write_text(json.dumps([e]))
    return next(data.cases(path))


def test_all_copies_keep_dates_distinct_ids_and_one_logical_proof(tmp_path):
    inputs, gold = read(tmp_path, entry())
    assert len(inputs.turns) == 4 and len({t.id for t in inputs.turns}) == 4
    assert len({t.session for t in inputs.turns}) == 2
    assert inputs.turns[0].date != inputs.turns[-1].date
    quotes = pipeline.extract(inputs.id, inputs.turns)
    assert len({q.id for q in quotes}) == 4
    assert len({q.text for q in quotes}) == 4
    assert gold.sessions == ('s', 's')
    for tid in (inputs.turns[0].id, inputs.turns[2].id):
        assert stats.score([tid], 'Paris', gold, 10)['recall'] == 1
    all_metrics = stats.score([t.id for t in inputs.turns], 'Paris', gold, 10)
    assert all_metrics['recall'] == 1 and all_metrics['ndcg'] == 1
    inv = data.inventory(tmp_path / 'data.json', 's')
    assert inv['duplicate_question_ids'] == ['q']
    assert inv['session_policy'] == data.SESSION_POLICY


def test_duplicate_irrelevant_copy_burns_a_rank_and_k_slot(tmp_path):
    inputs, gold = read(tmp_path, entry())
    irrelevant_a, irrelevant_b = inputs.turns[1].id, inputs.turns[3].id
    relevant = inputs.turns[0].id
    assert stats.score([irrelevant_a, irrelevant_b, relevant], '', gold, 10)['mrr'] == 1 / 3
    assert stats.score([irrelevant_a, irrelevant_b, relevant], '', gold, 2)['recall'] == 0


def test_annotations_do_not_affect_physical_identity_or_observations(tmp_path):
    e = entry()
    original, _ = read(tmp_path, e)
    e['answer'] = 'FORBIDDEN'
    e['question_type'] = 'changed'
    e['haystack_sessions'][1][0]['has_answer'] = False
    altered, gold = read(tmp_path, e)
    assert original == altered
    assert 'FORBIDDEN' not in json.dumps(asdict(altered))
    assert stats.score([altered.turns[2].id], '', gold, 10)['recall'] == 1


def test_future_copy_is_not_ingested_but_earlier_equivalent_copy_scores(tmp_path):
    e = entry()
    e['question_date'] = '2023/05/25 (Thu) 00:00'
    e['haystack_sessions'][0][0]['has_answer'] = False
    inputs, gold = read(tmp_path, e)
    assert len(inputs.turns) == 2 and gold.excluded_future_turns == 2
    assert stats.score([inputs.turns[0].id], '', gold, 10)['recall'] == 1


def test_same_date_still_has_unique_capture_ids_and_text(tmp_path):
    e = entry()
    e['haystack_dates'][1] = e['haystack_dates'][0]
    inputs, _ = read(tmp_path, e)
    quotes = pipeline.extract(inputs.id, inputs.turns)
    assert len({q.id for q in quotes}) == 4 and len({q.text for q in quotes}) == 4


def test_conflicting_duplicates_and_alignment_remain_errors(tmp_path):
    e = entry()
    e['haystack_sessions'][1][0]['content'] = 'London'
    with pytest.raises(ValueError, match='contents differ'):
        read(tmp_path, e)
    e = entry()
    e['haystack_dates'] = []
    with pytest.raises(ValueError, match='alignment'):
        read(tmp_path, e)


def test_source_clustering_does_not_treat_copies_as_independent(tmp_path):
    _, gold = read(tmp_path, entry())
    other = data.Gold('q2', (), (), 'synthetic', ('s',), 0)
    clusters = stats.cluster_ids([gold, other])
    assert len(set(clusters.values())) == 1


def test_unique_session_unchanged_identity_and_score(tmp_path):
    e = entry()
    for field in ('haystack_sessions', 'haystack_session_ids', 'haystack_dates'):
        e[field] = e[field][:1]
    inputs, gold = read(tmp_path, e)
    assert [t.id for t in inputs.turns] == ['s:0', 's:1'] and not gold.turn_aliases
    assert stats.score(['s:1', 's:0'], 'Paris', gold, 10)['mrr'] == .5


def test_preflight_amendment_keeps_winner_bytes_and_old_lock(tmp_path, monkeypatch):
    devdir, failed, output = tmp_path / 'dev', tmp_path / 'failed', tmp_path / 'new'
    devdir.mkdir()
    failed.mkdir()
    (devdir / 'plan.json').write_text('{}')
    (devdir / 'confirmation-lock.json').write_text('old-lock-kept')
    original_bytes = b'{"arm": {"id": "C4"}, "preserved": true}\n'
    (failed / 'winner.json').write_bytes(original_bytes)
    dev = {'models': {'minilm': {'id': 'hash'}}, 'arms': [ARM], 'capture_namespace': 'fixed',
           'source_commit': 'a' * 40, 'oracle_question_ids': [f'q{i}' for i in range(500)], **SHARED, **DEV_FIXED}
    chosen = json.loads(original_bytes)
    monkeypatch.setattr(amended, 'private_path', lambda p: (tmp_path, p.resolve()))
    monkeypatch.setattr(amended, 'verify_source', lambda *a: None)
    monkeypatch.setattr(amended, 'code_record', lambda: {'synthetic': 'sha'})
    monkeypatch.setattr(amended, 'attest_candidate', lambda *a: (dev, chosen, failed))
    monkeypatch.setattr(amended, 'dev_equivalence', lambda *a: {'predictions_rescored_identically': 200})
    def checksum(path, **kw):
        if path == amended.S_PATH:
            assert (output / 'winner.json').read_bytes() == original_bytes
            assert (devdir / 'confirmation-amendment-lock-v2.json').exists()
            return amended.S_SHA
        return data.sha256(path)
    monkeypatch.setattr(amended, 'sha256', checksum)
    audit = {'duplicate_question_ids': [f'q{i}' for i in range(15)], 'duplicate_session_groups': []}
    monkeypatch.setattr(amended, 'inventory', lambda *a, **kw: audit)
    golds = [data.Gold(f'q{i}', (), (), 'synth', (str(i),), 0) for i in range(500)]
    monkeypatch.setattr(amended, 'cases', lambda *a, **kw: iter((None, g) for g in golds))
    monkeypatch.setattr(amended, 'estimate', lambda *a: {'estimated_only': True})
    plan = amended.prepare(devdir, output, 'b' * 40, lambda: None)
    assert len(plan['selected_ids']) == 500 and plan['winner'] == chosen
    assert (failed / 'winner.json').read_bytes() == original_bytes
    assert (devdir / 'confirmation-lock.json').read_text() == 'old-lock-kept'
    assert plan['evidence_files']['winner.json'] == data.sha256(failed / 'winner.json')
    with pytest.raises(ValueError, match='One amended'):
        amended.prepare(devdir, tmp_path / 'another', 'b' * 40, lambda: None)


def test_no_s_access_without_original_candidate_attestation(tmp_path, monkeypatch):
    monkeypatch.setattr(amended, 'private_path', lambda p: (tmp_path, p.resolve()))
    monkeypatch.setattr(amended, 'code_record', lambda: {})
    monkeypatch.setattr(amended, 'verify_source', lambda *a: None)
    def changed(*args):
        raise ValueError('candidate changed')
    monkeypatch.setattr(amended, 'attest_candidate', changed)
    monkeypatch.setattr(amended, 'sha256', lambda *a, **kw: pytest.fail('S accessed before attestation'))
    with pytest.raises(ValueError, match='candidate changed'):
        amended.prepare(tmp_path, tmp_path / 'new', 'b' * 40, lambda: None)


def test_archived_reader_and_200_predictions_match_without_model(tmp_path, monkeypatch):
    clone = Path(__file__).resolve().parents[2]
    # Actual archived reader + tiny synthetic data, never the S corpus/model.
    monkeypatch.setattr(amended, 'private_path', lambda p: (clone, p.resolve()))
    entries = []
    for i in range(100):
        e = entry()
        e['question_id'] = f'q{i}'
        for key in ('haystack_sessions', 'haystack_session_ids', 'haystack_dates'):
            e[key] = e[key][:1]
        entries.append(e)
    dataset = tmp_path / 'unique.json'
    dataset.write_text(json.dumps(entries))
    dev = {'source_commit': '3d7f0b380c684b0b428e7d98fc327c8b718c0927',
           'selected_ids': [f'q{i}' for i in range(100)], 'data': str(dataset), 'top_k': 10}
    (tmp_path / 'C4').mkdir()
    predictions = []
    for _, gold in data.cases(dataset):
        for system in ('memory', 'bm25'):
            predictions.append({'qid': gold.id, 'system': system, 'retrieved_turn_ids': ['s:0'],
                                'response': 'Paris', **stats.score(['s:0'], 'Paris', gold, 10)})
    path = tmp_path / 'C4/predictions.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in predictions))
    (tmp_path / 'C4/proofs.json').write_text('{}')
    result = amended.dev_equivalence(dev, tmp_path, lambda: None)
    assert result['predictions_rescored_identically'] == 200
    predictions[0]['mrr'] = 0
    path.write_text(''.join(json.dumps(r) + '\n' for r in predictions))
    with pytest.raises(ValueError, match='metrics'):
        amended.dev_equivalence(dev, tmp_path, lambda: None)
