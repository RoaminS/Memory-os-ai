"""Prospective gate, frozen execution and no-S-before-selection regressions."""
from copy import deepcopy
import json

import pytest

from bench.e2e import lexical_c4 as c4
from bench.e2e.configuration import resolved_config
from bench.e2e.data import Gold, Inputs, sha256
from bench.e2e.run import capture_namespace


def report(mrr=0, recall_lower=.001):
    return {'complete': True, 'all_cases_executed_without_failure': True,
            'paired_delta_memory': {'bm25': {'mean': .02, 'ci95': [recall_lower, .04]}},
            'paired_other_delta_memory': {'bm25': {'mrr': {'mean': mrr, 'ci95': [-.03, .04]}}}}


def plan():
    return {'version': c4.DEV_VERSION, 'source_commit': 'a' * 40, 'code_files': {'a': 'b'}, 'data': '/synthetic',
            'arms': [c4.ARM], 'models': {'minilm': {'id': 'hash'}}, 'capture_namespace': capture_namespace(20261009),
            'selected_ids': ['q'], 'top_k': 10, 'context_bytes': 12000, 'trace_k': 100,
            'warm_searches': 3, 'chunk_chars': 1024, 'seed': 20261009, 'bootstrap': 2000,
            'ingest_bytes': 16_000_000, 'case_seconds': 300, 'run_seconds': 3600,
            'dataset_sha256': 'd' * 64, 'selection_sha256': 'e' * 64,
            'criterion': c4.CRITERION, 'claim': 'dev only'}


def config(p):
    return {**{k: p[k] for k in ('source_commit', 'code_files', 'selected_ids', 'top_k',
            'context_bytes', 'trace_k', 'warm_searches', 'chunk_chars', 'seed', 'run_seconds',
            'case_seconds', 'ingest_bytes', 'selection_sha256')},
            'run_id': p['capture_namespace'], 'resident': True, 'protocol': 'e2e-resident-v2',
            'systems': ['memory', 'bm25'], 'model_record': p['models']['minilm'], 'transport': 'stdio',
            'limit': len(p['selected_ids']), 'bootstrap_replicates': 2000, 'confidence': .95,
            'selection_filter': True, 'unique_turns': False, 'query_mode': 'none', 'reranker': None,
            'engine_overrides': deepcopy(c4.ARM['engine_overrides']), 'dataset': {'sha256': p['dataset_sha256']},
            'resolved_engine_config': resolved_config('hash', '<private fresh store per question>', c4.ARM['engine_overrides'])}


def test_gate_is_mean_mrr_not_lower_mrr_ci_and_rejects_bad_results():
    assert c4.eligible(report())  # MRR CI may cross zero; not non-inferiority proof.
    assert not c4.eligible(report(mrr=-.00001))
    assert not c4.eligible(report(recall_lower=0))
    assert not c4.eligible({**report(), 'complete': False})
    assert not c4.eligible({**report(), 'all_cases_executed_without_failure': False})
    for value in (None, float('nan'), float('inf')):
        bad = report(mrr=value)
        assert not c4.eligible(bad)
    bad = report()
    bad['paired_delta_memory']['bm25']['ci95'] = None
    assert not c4.eligible(bad)


def test_one_fixed_policy_and_frozen_config_reject_every_drift():
    assert c4.ARM == {'id': 'C4', 'model': 'minilm', 'engine_overrides': {
        'bm25_profile': 'scoped_literal', 'lexical_guard_k': 3}}
    p, cfg = plan(), config(plan())
    c4.validate_config(p, cfg)
    for key, value in [('source_commit', 'b' * 40), ('code_files', {}), ('top_k', 11),
                       ('case_seconds', 301), ('bootstrap_replicates', 100), ('selection_sha256', 'wrong'),
                       ('unique_turns', True), ('query_mode', 'lexical'), ('transport', 'dispatch'),
                       ('engine_overrides', {'bm25_profile': 'scoped_literal', 'lexical_guard_k': 2})]:
        with pytest.raises(ValueError):
            c4.validate_config(p, {**cfg, key: value})
    with pytest.raises(ValueError):
        resolved_config('hash', '/unused', {'lexical_guard_k': 3})


@pytest.mark.parametrize('bad', [report(mrr=-.01), report(recall_lower=0)])
def test_failed_gate_never_hashes_stats_or_reads_s(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(c4, 'inputs', lambda *a: plan())
    monkeypatch.setattr(c4, 'verified_outcome', lambda *a: {'status': 'complete', 'report': bad})
    monkeypatch.setattr(c4, 'inventory', lambda *a, **kw: pytest.fail('S accessed before gate'))
    monkeypatch.setattr(c4, 'sha256', lambda *a, **kw: pytest.fail('S hashed before gate'))
    with pytest.raises(ValueError, match='NO S access'):
        c4.prepare_s(tmp_path / 'dev', tmp_path / 's', 'a' * 40, lambda: None)
    assert not (tmp_path / 's').exists()


def test_winner_and_unique_lock_exist_before_first_s_access(tmp_path, monkeypatch):
    devdir, output = tmp_path / 'dev', tmp_path / 's'
    devdir.mkdir()
    (devdir / 'plan.json').write_text('{}')
    dev = plan()
    dev['oracle_question_ids'] = [f'q{i}' for i in range(500)]
    dev['confirmation'] = {'version': c4.S_VERSION, 'sha256': c4.S_SHA, 'questions': 500,
                           'case_seconds': 7200, 'run_seconds': 172800, 'campaign_seconds': 173700,
                           'warm_searches': 1, 'trace_k': 0, 'criterion': c4.CRITERION,
                           'claim': 'S overlaps observed oracle; NOT independent; NOT leaderboard'}
    chosen = {'arm': c4.ARM, 'proof': {'files': 'frozen'}}
    monkeypatch.setattr(c4, 'winner', lambda *a: (dev, chosen))
    monkeypatch.setattr(c4, 'private_path', lambda p: (tmp_path, p.resolve()))
    def checksum(path, **kw):
        if path == c4.S_PATH:
            assert json.loads((output / 'winner.json').read_text()) == chosen
            assert (devdir / 'confirmation-lock.json').exists()
            return c4.S_SHA
        return sha256(path)
    monkeypatch.setattr(c4, 'sha256', checksum)
    monkeypatch.setattr(c4, 'inventory', lambda *a, **kw: {'questions': 500})
    golds = [Gold(f'q{i}', ('t',), ('answer',), 'synthetic', ('s',), 0) for i in range(500)]
    monkeypatch.setattr(c4, 'cases', lambda *a, **kw: iter((Inputs('opaque', (), '', ''), g) for g in golds))
    p = c4.prepare_s(devdir, output, 'a' * 40, lambda: None)
    assert p['selected_ids'] == [g.id for g in golds]
    assert len(p['oracle_question_overlap']) == 500 and p['independent_questions'] is False
    assert p['run_seconds'] == 172800 and p['trace_k'] == 0
    with pytest.raises(ValueError, match='One C4 S attempt'):
        c4.prepare_s(devdir, tmp_path / 'second-s', 'a' * 40, lambda: None)
    # One shared-source cluster must never pass the numeric CI gate.
    bad = report()
    bad['paired_delta_memory']['bm25']['ci95'] = None
    assert not c4.eligible(bad)


def test_executable_campaign_arguments_and_verdict_use_same_arm(tmp_path, monkeypatch):
    p, cfg = plan(), config(plan())
    (tmp_path / 'plan.json').write_text('{}')
    seen = []
    monkeypatch.setattr(c4, 'inputs', lambda *a: p)
    monkeypatch.setattr(c4, 'run_campaign', lambda args: seen.append(args) or 0)
    monkeypatch.setattr(c4, 'valid_complete', lambda *a: (report(), cfg, {'files': 'proof'}))
    monkeypatch.setattr(c4, 'record_outcome', lambda *a: {'status': 'complete', 'report': report()})
    verdict = c4.execute(p, tmp_path, 'a' * 40, lambda: None)
    args = seen[0]
    assert args[args.index('--variant') + 1] == 'oracle'
    assert args[args.index('--selection') + 1] == str(tmp_path / 'selection.json')
    assert args[args.index('--systems') + 1] == 'memory,bm25'
    assert json.loads(args[args.index('--engine-overrides') + 1]) == c4.ARM['engine_overrides']
    assert verdict['winner'] == c4.ARM and verdict['independent_confirmation_passed'] is False


def test_s_launch_revalidates_winner_before_any_dataset_access(tmp_path, monkeypatch):
    p = {**plan(), 'version': c4.S_VERSION, 'development_directory': str(tmp_path / 'dev')}
    monkeypatch.setattr(c4, 'load_plan', lambda *a: p)
    def bad_winner(*a):
        raise ValueError('development proof changed')
    monkeypatch.setattr(c4, 'winner', bad_winner)
    monkeypatch.setattr(c4, 'inputs', lambda *a: pytest.fail('S accessed before renewed gate'))
    with pytest.raises(ValueError, match='proof changed'):
        c4.run_s(tmp_path, 'a' * 40, lambda: None)


def test_single_attempt_reservation_is_exclusive(tmp_path):
    path = tmp_path / 'lock.json'
    c4.exclusive_json(path, {'attempt': 'first'})
    with pytest.raises(FileExistsError):
        c4.exclusive_json(path, {'attempt': 'second'})
    assert json.loads(path.read_text()) == {'attempt': 'first'}


@pytest.mark.parametrize('changes', [{'run_seconds': 3601}, {'top_k': 11},
                                   {'selected_ids': ['q'] * 100}])
def test_rehashed_parameter_changes_still_fail_preregistered_constants(tmp_path, monkeypatch, changes):
    p = {**plan(), 'selected_ids': [f'q{i}' for i in range(100)],
         'campaign_seconds': 4500, **changes}
    monkeypatch.setattr(c4, 'load_plan', lambda *a: p)
    monkeypatch.setattr(c4, 'code_record', lambda: p['code_files'])
    monkeypatch.setattr(c4, 'sha256', lambda *a, **kw: pytest.fail('Data read after invalid constants'))
    with pytest.raises(ValueError, match='budgets/denominator'):
        c4.inputs(tmp_path, 'a' * 40, lambda: None)


def test_prepare_dev_freezes_before_execution_with_no_s_access(tmp_path, monkeypatch):
    previous, output = tmp_path / 'previous', tmp_path / 'new'
    previous.mkdir()
    (previous / 'selection.json').write_text('{"dev_ids": ["q"]}')
    data = tmp_path / 'oracle.json'
    data.write_text('[]')
    old = {**plan(), 'data': str(data), 'dataset_sha256': sha256(data),
           'selected_ids': [f'q{i}' for i in range(100)]}
    monkeypatch.setattr(c4, 'private_path', lambda p: (tmp_path, p.resolve()))
    monkeypatch.setattr(c4, 'verify_source', lambda *a: None)
    monkeypatch.setattr(c4, 'code_record', lambda: {'synthetic.py': 'frozen'})
    monkeypatch.setattr(c4, 'historical', lambda *a: (old, {'plan_sha256': 'old'},
                         {'oracle_question_ids': [f'q{i}' for i in range(500)]}))
    monkeypatch.setattr(c4, 'model_record', lambda *a, **kw: {'id': 'hash'})
    monkeypatch.setattr(c4, 'inventory', lambda *a, **kw: pytest.fail('No S access during dev freeze'))
    monkeypatch.setattr(c4, 'run_campaign', lambda *a: pytest.fail('No results before preregistration'))
    frozen = c4.prepare_dev(previous, output, 'a' * 40, lambda: None)
    assert frozen['arms'] == [c4.ARM] and len(frozen['selected_ids']) == 100
    assert frozen['confirmation']['sha256'] == c4.S_SHA
    assert frozen['confirmation']['criterion'] == c4.CRITERION
    assert json.loads((output / 'receipt.json').read_text())['plan_sha256'] == sha256(output / 'plan.json')
