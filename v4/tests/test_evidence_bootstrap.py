"""S-only evidence/abs bootstrap, fixed thresholds and pre-model failures."""
from dataclasses import replace
import json

import pytest

from bench.e2e import data, lexical_s_v3 as amended, run, stats
from bench.e2e.data import Gold
from bench.e2e.lexical_s_v3 import BOOTSTRAP


def gold(i, evidence=True):
    return Gold(f'q{i}', ('proof:0',) if evidence else (), ('x',), 'category',
                ('common-distractor',), 0, answer_sessions=(f's{i}',))


def test_common_distractors_do_not_merge_evidence_policy():
    gs = [gold(i) for i in range(30)]
    assert len(set(stats.cluster_ids(gs).values())) == 1
    audit = stats.bootstrap_preflight(gs, stats.EVIDENCE_POLICY, 30)
    assert audit['all_haystack_clusters'] == 1 and audit['primary_contributing_clusters'] == 30
    assert audit['conditional_on_fixed_distractor_corpus']


def test_shared_proof_and_abs_links_transitive_without_fake_independence():
    a = gold(1)
    abs_a = replace(gold(2), id='q1_abs', evidence=(), answer_sessions=())
    twin = replace(gold(3), answer_sessions=a.answer_sessions)
    absent_base = replace(gold(4), id='absent_abs', answer_sessions=())
    clusters = stats.evidence_cluster_ids([a, abs_a, twin, absent_base])
    assert clusters[a.id] == clusters[abs_a.id] == clusters[twin.id]
    assert clusters[absent_base.id] != clusters[a.id]
    assert len(set(stats.evidence_cluster_ids([a, abs_a, twin], link_abs=False).values())) == 2


def test_threshold_is_contributing_clusters_not_total_questions():
    with pytest.raises(ValueError, match='29 contributing clusters < 30'):
        stats.bootstrap_preflight([gold(i) for i in range(29)], stats.EVIDENCE_POLICY, 30)
    with pytest.raises(ValueError, match='0 contributing clusters'):
        stats.bootstrap_preflight([gold(i, evidence=False) for i in range(500)], stats.EVIDENCE_POLICY, 30)
    for policy, minimum in [('haystack', 30), (stats.EVIDENCE_POLICY, 2)]:
        with pytest.raises(ValueError, match='Fixed S v3'):
            stats.bootstrap_preflight([gold(i) for i in range(30)], policy, minimum)
    with pytest.raises(ValueError, match='labels missing'):
        stats.bootstrap_preflight([replace(gold(i), answer_sessions=()) for i in range(30)], stats.EVIDENCE_POLICY, 30)


def test_unavailable_ci_preserves_mean_and_never_falls_back():
    few = [(str(i), .1) for i in range(29)]
    result = stats.estimate(few, 1, 2000, minimum_clusters=30)
    assert result['mean'] == .1 and result['ci95'] is None
    assert '30' in result['reason']
    assert stats.estimate(few, 1, 2000)['ci95'] is not None  # legacy default unchanged
    assert stats.estimate(few + [('30', .1)], 1, 2000, minimum_clusters=30)['ci95'] is not None


def test_report_and_pairing_use_frozen_policy():
    gs = [gold(i) for i in range(30)]
    rows = [{'qid': g.id, 'system': system, 'status': 'ok', 'recall': recall, 'mrr': .5,
             'end_to_end_ms': 1} for g in gs for system, recall in [('memory', .8), ('bm25', .7)]]
    legacy = stats.summarize(rows, gs, ['memory', 'bm25'], 20261009)
    assert legacy['paired_delta_memory']['bm25']['ci95'] is None
    assert legacy['bootstrap']['level'] == 'shared-haystack-session connected components'
    current = stats.summarize(rows, gs, ['memory', 'bm25'], 20261009,
                              cluster_policy=stats.EVIDENCE_POLICY, minimum_clusters=30)
    assert current['paired_delta_memory']['bm25']['clusters'] == 30
    assert current['paired_delta_memory']['bm25']['ci95'][0] > 0
    assert all(current['bootstrap'][k] == v for k, v in BOOTSTRAP.items())


def test_oracle_rejects_s_bootstrap_before_data_or_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(run, 'cases', lambda *a, **kw: pytest.fail('Oracle data must not be touched'))
    with pytest.raises(SystemExit) as error:
        run.main(['--data', str(tmp_path / 'unused'), '--variant', 'oracle',
                  '--output', str(tmp_path / 'never-created'), '--source-commit', 'a' * 40,
                  '--bootstrap-policy', stats.EVIDENCE_POLICY, '--bootstrap-min-clusters', '30'])
    assert error.value.code == 2 and not (tmp_path / 'never-created').exists()


def test_s_preflight_stops_before_any_model_worker(tmp_path, monkeypatch):
    output = tmp_path / 'refused'
    monkeypatch.setattr(run, 'ensure_priority', lambda *a: None)
    monkeypatch.setattr(run, 'sample', lambda *a: {'swap_bytes': 0, 'free_bytes': 10_000_000_000})
    monkeypatch.setattr(run, 'inventory', lambda *a, **kw: {'sha256': 'x'})
    monkeypatch.setattr(run, 'model_record', lambda *a, **kw: {'id': 'hash'})
    monkeypatch.setattr(run, 'cases', lambda *a, **kw: iter((None, gold(i)) for i in range(29)))
    monkeypatch.setattr(run, 'supervise', lambda *a, **kw: pytest.fail('No model worker after insufficient groups'))
    status = run.main(['--data', str(tmp_path / 'unused'), '--variant', 's', '--output', str(output),
                       '--source-commit', 'a' * 40, '--systems', 'memory,bm25',
                       '--bootstrap-policy', stats.EVIDENCE_POLICY, '--bootstrap-min-clusters', '30'])
    assert status == 2
    preflight = json.loads((output / 'bootstrap-preflight.json').read_text())
    assert preflight['model_workers_started'] == 0 and preflight['available'] is False
    assert not (output / 'frozen.json').exists()


def test_answer_session_labels_are_scorer_only(tmp_path):
    entry = {'question_id': 'q', 'question_date': '2023/05/30 (Tue) 00:00', 'question': 'Which city?',
             'question_type': 'x', 'answer': 'Paris', 'haystack_sessions': [[{'role': 'user', 'content': 'Paris'}]],
             'haystack_session_ids': ['s'], 'haystack_dates': ['2023/05/23 (Tue) 00:00'],
             'answer_session_ids': ['proof-only-secret']}
    path = tmp_path / 'tiny.json'
    path.write_text(json.dumps([entry]))
    inputs, gold_labels = next(data.cases(path))
    entry['answer_session_ids'] = ['different']
    path.write_text(json.dumps([entry]))
    changed, _ = next(data.cases(path))
    assert changed == inputs and gold_labels.answer_sessions == ('proof-only-secret',)


def test_metadata_freeze_preserves_v2_winner_and_never_reads_s(tmp_path, monkeypatch):
    previous, output, devdir = tmp_path / 'v2', tmp_path / 'v3', tmp_path / 'dev'
    previous.mkdir()
    devdir.mkdir()
    (previous / 'winner.json').write_bytes(b'{"kept": true}\n')
    (previous / 'plan.json').write_text('{}')
    prior = {'version': 'lexical-c4-s-v2', 'source_clusters': 1, 'evidence_files': {'winner.json': 'old'},
             'selected_ids': [str(i) for i in range(500)], 'criterion': amended.CRITERION, 'arms': [amended.ARM]}
    monkeypatch.setattr(amended, 'private_path', lambda p: (tmp_path, p.resolve()))
    monkeypatch.setattr(amended, 'verify_source', lambda *a: None)
    monkeypatch.setattr(amended, 'code_record', lambda: {'tiny': 'sha'})
    monkeypatch.setattr(amended, 'attest_previous', lambda *a: (prior, {}, devdir))
    monkeypatch.setattr(amended, 'dev_unchanged', lambda *a: {'all_development_metrics_and_intervals_identical': True})
    monkeypatch.setattr(amended, 'run_campaign', lambda *a: pytest.fail('No campaign during freeze'))
    plan = amended.prepare(previous, output, 'a' * 40)
    assert plan['bootstrap'] == BOOTSTRAP and plan['criterion'] == prior['criterion']
    assert plan['source_clusters'] is None and plan['previous_all_haystack_clusters'] == 1
    assert (output / 'winner.json').read_bytes() == (previous / 'winner.json').read_bytes()
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['plan_sha256'] == data.sha256(output / 'plan.json')
    with pytest.raises(ValueError, match='already reserved'):
        amended.prepare(previous, tmp_path / 'again', 'a' * 40)
