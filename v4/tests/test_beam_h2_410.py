"""H2 harness (contract LEXICAL_PROFILE_4_1_0 section 10) on SYNTHETIC fixtures only.

Nothing here reads BEAM or loads Granite. The lexical arm is the real engine in a fresh
interpreter; the "semantic" arm of the end-to-end test is the real semantic engine with the
hash encoder (a declared configuration patch that only a synthetic specification accepts).
"""
import asyncio
import copy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from bench import evaluation_manifest
from bench.e2e import beam_h2
from bench.e2e.beam_campaign import summarize
from bench.e2e.client import offline_env

V4 = Path(__file__).resolve().parents[1]
DRIVER = r"""
import asyncio, json, sys
from pathlib import Path
from bench.e2e import beam_h2

arm, output, patch = sys.argv[1], Path(sys.argv[3]), sys.argv[4]
plan = json.loads(Path(sys.argv[2]).read_text())
block = None
if arm == 'lexical':
    block = beam_h2.MLBlock()
    sys.meta_path.insert(0, block)
asyncio.run(beam_h2.run_arm(plan, arm, output, block=block, config_patch=json.loads(patch) or None))
"""


class FakeHost(beam_h2.Host):
    """No Git, no niceness change; workers are the real run_arm in fresh interpreters."""

    def __init__(self, root):
        self.clone = root / 'clone'
        self.clone.mkdir(parents=True)
        self.head, self.code, self.lock = 'a' * 40, 'code-v1', 'numpy==2.4.2\n'
        self.launched, self.fail, self.guards, self.fail_guard = [], None, 0, None
        self.runtime_value, self.after_launch = None, None

    def attest(self, revision):
        if revision != self.head:
            raise ValueError('Cannot attest declared source commit')
        return {'v4/bench/e2e/beam_h2.py': self.code, 'v4/tests/test_beam_h2_410.py': 'tests-v1',
                'v4/docs/H2_RUN_v2.md': 'doc-v1'}

    def runtime(self):
        return copy.deepcopy(self.runtime_value)

    def archive(self, revision):
        return f'archive {revision} {self.code}'.encode()

    def environment(self):
        return self.lock

    def hardware(self):
        return {'os': 'fixture', 'cpu': 'fixture', 'accelerator': 'none', 'device': 'fixture',
                'python': '3.11', 'ram_gib': 16}

    def guard(self, directory):
        self.guards += 1
        if self.guards == self.fail_guard:
            raise beam_h2.ResourceStop('swap 9000000000 > 8500000000 bytes')
        return {'swap_bytes': 0, 'free_bytes': 10 ** 12}

    def launch(self, plan, directory, arm, manifest_sha256, deadline):
        self.launched.append(arm)
        if arm == self.fail:
            raise RuntimeError(f'Worker {arm} failed')
        if isinstance(self.fail, BaseException) and arm == 'semantic':
            raise self.fail
        run_worker(directory, arm, {} if arm == 'lexical' else {'embed_model': 'hash'})
        if self.after_launch:
            self.after_launch(arm)


def run_worker(directory, arm, patch):
    script = directory / 'driver.py'
    script.write_text(DRIVER)
    env = offline_env()
    env['PYTHONPATH'] = os.pathsep.join([str(V4), str(V4 / 'src')])
    output = directory / 'retrieval' / arm
    command = [sys.executable, '-B', str(script), arm, str(directory / 'plan.json'), str(output),
               json.dumps(patch)]
    done = subprocess.run(command, env=env, cwd=output, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]


@pytest.fixture
def frozen_plan(tmp_path):
    data, spec = beam_h2.selfcheck_corpus(tmp_path / 'corpus', model_cache=None)
    assert spec == beam_h2.Spec(dataset_sha256=hashlib.sha256(data.read_bytes()).hexdigest(), conversations=4,
                                messages=24, questions=40, scored=32, minimum_clusters=4, model_cache=None,
                                synthetic=True)
    host = FakeHost(tmp_path)
    directory = tmp_path / 'plan'
    done = beam_h2.prepare(directory, data, host.head, spec=spec, host=host)
    return directory, data, spec, host, done['manifest_sha256']


# ------------------------------------------------------------------ freeze
def test_frozen_protocol_values_are_those_of_section_10():
    values = beam_h2.frozen_values()
    assert beam_h2.SPEC == beam_h2.Spec(
        dataset_sha256='613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3',
        revision='3205395e897e7318c7b094ef4e6047b9b82dbb03', conversations=20, messages=5732, questions=400,
        scored=355, minimum_clusters=20, model_cache=str(beam_h2.GRANITE_CACHE), synthetic=False)
    assert (values['run_id'], values['seed'], values['top_k'], values['context_bytes']) == (
        'h2-lexical-parity-v1', 20261010, 10, 12000)
    assert values['bootstrap']['replicates'] == 2000 and values['bootstrap']['confidence'] == 0.95
    assert (beam_h2.POINT_MARGIN, beam_h2.CI_MARGIN) == (Fraction('0.02'), Fraction('0.03'))
    assert values['threshold']['point_margin'] == '1/50' and values['threshold']['ci_lower_margin'] == '3/100'
    assert values['model']['revision'] == '835ad14087e140460703cf0fae09f97d469d65c2'
    assert values['retrospective']['independence_claim'] is False
    assert values['retrospective']['tuning_allowed'] is False
    assert len(values['retrospective']['prior_exposure']) == 3


def test_arms_are_the_two_profiles_on_sqlite_and_differ_by_the_dense_channel_only():
    arms = beam_h2.arm_configs()
    lexical, semantic = arms['lexical'], arms['semantic']
    assert {key for key in lexical if lexical[key] != semantic[key]} == {'retrieval_profile', 'use_dense'}
    assert (lexical['retrieval_profile'], lexical['use_dense']) == ('lexical', False)
    assert (semantic['retrieval_profile'], semantic['use_dense']) == ('semantic', True)
    for arm in arms.values():
        assert (arm['store_backend'], arm['use_recency'], arm['lexical_guard_k']) == ('sqlite_s1', False, 3)
    assert semantic['embed_model'] == beam_h2.GRANITE and semantic['device'] == 'auto'


def test_prepare_freezes_a_valid_retrospective_manifest(frozen_plan):
    directory, data, spec, host, fingerprint = frozen_plan
    manifest = json.loads((directory / 'manifest.json').read_text())
    evaluation_manifest.validate(manifest, directory)
    assert manifest['run_id'] == 'h2-lexical-parity-v1' and manifest['seed'] == 20261010
    assert manifest['selection_rule'] == 'retrospective_no_selection'
    assert manifest['retrospective']['independence_claim'] is False
    assert manifest['retrospective']['tuning_allowed'] is False
    assert manifest['retrospective']['prior_exposure'] == beam_h2.PRIOR_EXPOSURE
    assert [system['name'] for system in manifest['systems']] == ['lexical', 'semantic']
    assert manifest['systems'][0]['config'] == beam_h2.arm_configs()['lexical']
    assert manifest['systems'][1]['config'] == beam_h2.arm_configs()['semantic']
    dataset = manifest['datasets'][0]
    assert dataset['evaluation_count'] == 40 and 'test_count' not in dataset
    split = json.loads((directory / 'splits.json').read_text())
    assert set(split) == {'evaluation'} and len(split['evaluation']) == 40
    assert 'SECRET' not in (directory / 'splits.json').read_text()
    assert manifest['tasks'] == [{'kind': 'retrieval', 'metrics': ['recall_at_k'],
                                  'scoring_rule': manifest['tasks'][0]['scoring_rule']}]
    lock = json.loads((directory / 'frozen.json').read_text())
    assert lock['manifest_sha256'] == fingerprint == hashlib.sha256(
        (directory / 'manifest.json').read_bytes()).hexdigest()
    assert not (directory / 'retrieval').exists()  # prepare never runs an engine
    assert beam_h2.frozen(directory, fingerprint, spec=spec, host=host)['audit']['scored'] == 32


def test_prepare_refuses_an_existing_plan_a_clone_path_or_another_corpus(frozen_plan, tmp_path):
    directory, data, spec, host, _ = frozen_plan
    with pytest.raises(ValueError, match='never overwritten'):
        beam_h2.prepare(directory, data, host.head, spec=spec, host=host)
    with pytest.raises(ValueError, match='outside the Git clone'):
        beam_h2.prepare(host.clone / 'plan', data, host.head, spec=spec, host=host)
    with pytest.raises(ValueError, match='exact source Git commit'):
        beam_h2.prepare(tmp_path / 'short', data, 'abc', spec=spec, host=host)
    with pytest.raises(ValueError, match='Cannot attest'):
        beam_h2.prepare(tmp_path / 'other-commit', data, 'b' * 40, spec=spec, host=host)
    with pytest.raises(ValueError, match='counts differ'):
        beam_h2.prepare(tmp_path / 'counts', data, host.head, spec=replace(spec, scored=33), host=host)
    with pytest.raises(ValueError, match='checksum differs'):  # the real frozen corpus identity
        beam_h2.prepare(tmp_path / 'real', data, host.head, spec=replace(beam_h2.SPEC, model_cache=None),
                        host=host)
    assert not any((tmp_path / name).exists() for name in ('short', 'other-commit', 'counts', 'real'))


def tamper_plan(directory, data, host, monkeypatch):
    plan = json.loads((directory / 'plan.json').read_text())
    plan['threshold']['point_margin'] = '1/10'
    (directory / 'plan.json').write_text(json.dumps(plan))


def tamper_plan_and_manifest_record(directory, data, host, monkeypatch):
    tamper_plan(directory, data, host, monkeypatch)
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['plan'] = beam_h2.record(directory / 'plan.json')
    (directory / 'manifest.json').write_text(json.dumps(manifest))


def tamper_arm(directory, data, host, monkeypatch):
    original = beam_h2.arm_configs

    def changed():
        arms = original()
        arms['semantic']['use_recency'] = True
        return arms
    monkeypatch.setattr(beam_h2, 'arm_configs', changed)


CHANGES = {
    'plan': tamper_plan,
    'plan_and_manifest': tamper_plan_and_manifest_record,
    'manifest': lambda directory, data, host, monkeypatch: (directory / 'manifest.json').write_text(
        (directory / 'manifest.json').read_text().replace('"top_k": 10', '"top_k": 20')),
    'lock': lambda directory, data, host, monkeypatch: (directory / 'frozen.json').write_text(
        json.dumps({'protocol_version': '1.0', 'manifest_sha256': '0' * 64, 'frozen_at': 'now'})),
    'splits': lambda directory, data, host, monkeypatch: (directory / 'splits.json').write_text(
        json.dumps({'evaluation': json.loads((directory / 'splits.json').read_text())['evaluation'][:-1]})),
    'corpus': lambda directory, data, host, monkeypatch: data.write_text(data.read_text() + '\n'),
    'commit': lambda directory, data, host, monkeypatch: setattr(host, 'head', 'c' * 40),
    'code': lambda directory, data, host, monkeypatch: setattr(host, 'code', 'code-v2'),
    'runtime_record': lambda directory, data, host, monkeypatch: (
        directory / 'runtime.json').write_text('{}'),
    'query_budget_in_code': lambda directory, data, host, monkeypatch: monkeypatch.setattr(
        beam_h2, 'QUERY_BYTES', 8192),
    'expected_encoder_in_code': lambda directory, data, host, monkeypatch: monkeypatch.setitem(
        beam_h2.EXPECTED_ENCODER, 'device', 'cpu'),
    'environment': lambda directory, data, host, monkeypatch: setattr(host, 'lock', 'numpy==9.9.9\n'),
    'arm_config': tamper_arm,
    'threshold_in_code': lambda directory, data, host, monkeypatch: monkeypatch.setattr(
        beam_h2, 'POINT_MARGIN', Fraction(5, 100)),
    'seed_in_code': lambda directory, data, host, monkeypatch: monkeypatch.setattr(beam_h2, 'SEED', 1),
}


@pytest.mark.parametrize('change', sorted(CHANGES))
def test_retrieve_refuses_anything_that_differs_from_the_freeze(frozen_plan, monkeypatch, change):
    directory, data, spec, host, fingerprint = frozen_plan
    beam_h2.frozen(directory, fingerprint, spec=spec, host=host)  # precondition: the untouched freeze passes
    CHANGES[change](directory, data, host, monkeypatch)
    with pytest.raises(ValueError):
        beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
    assert host.launched == [] and not (directory / 'retrieval').exists()


def test_retrieve_requires_the_reviewed_manifest_digest_and_the_frozen_specification(frozen_plan):
    directory, data, spec, host, fingerprint = frozen_plan
    with pytest.raises(ValueError, match='manifest changed'):
        beam_h2.retrieve(directory, '0' * 64, spec=spec, host=host)
    with pytest.raises(ValueError):  # a synthetic plan is never accepted as the real H2 specification
        beam_h2.retrieve(directory, fingerprint, spec=replace(beam_h2.SPEC, model_cache=None), host=host)
    assert host.launched == [] and not (directory / 'retrieval').exists()


def test_real_source_attestation_refuses_an_unknown_commit():
    with pytest.raises(ValueError):
        beam_h2.Host().attest('0' * 40)


def test_real_worker_process_reattests_the_freeze_before_any_engine(frozen_plan):
    """The fixture's commit is unknown to Git: the real worker stops on the source attestation."""
    directory, data, spec, host, fingerprint = frozen_plan
    plan = json.loads((directory / 'plan.json').read_text())
    (directory / 'retrieval/lexical').mkdir(parents=True)
    with pytest.raises(RuntimeError, match='Worker lexical failed'):
        beam_h2.Host().launch(plan, directory, 'lexical', fingerprint, time.monotonic() + 120)
    assert 'STOP: ' in (directory / 'retrieval/lexical/worker.log').read_text()
    assert sorted(path.name for path in (directory / 'retrieval/lexical').iterdir()) == ['worker.log']
    # Without the self-check flag a synthetic plan is refused as "not the frozen H2 specification".
    assert beam_h2.main(['_worker', '--plan-dir', str(directory), '--arm', 'semantic', '--manifest-sha256',
                         fingerprint]) == 2
    with pytest.raises(ValueError, match='both arms'):
        beam_h2.retrieve(directory, fingerprint, arms=('lexical',))


def test_selfcheck_runs_the_same_pipeline_on_invented_rows_and_says_so(tmp_path):
    host = FakeHost(tmp_path)
    result = beam_h2.selfcheck(tmp_path / 'check', host.head, host=host, model_cache=None)
    assert result['synthetic'] is True and result['complete'] is True
    assert host.launched == ['lexical', 'semantic']
    assert 'BEAM' not in (tmp_path / 'check/corpus/synthetic.jsonl').read_text()
    with pytest.raises(ValueError, match='new self-check directory'):
        beam_h2.selfcheck(tmp_path / 'check', host.head, host=host, model_cache=None)
    partial = beam_h2.selfcheck(tmp_path / 'lexical-only', host.head, host=host, model_cache=None,
                                arms=('lexical',))
    assert partial['complete'] is False and partial['recall_at_10'] is None
    assert partial['arms']['lexical']['ml_free'] is True and partial['arms']['semantic'] is None


def test_cli_requires_its_frozen_arguments(tmp_path, capsys):
    for arguments in (['prepare', '--plan-dir', str(tmp_path / 'p')],
                      ['retrieve', '--plan-dir', str(tmp_path / 'p')],
                      ['_worker', '--plan-dir', str(tmp_path / 'p'), '--arm', 'lexical']):
        with pytest.raises(SystemExit):
            beam_h2.main(arguments)
    assert beam_h2.main(['retrieve', '--plan-dir', str(tmp_path / 'p'), '--manifest-sha256', '0' * 64]) == 2
    assert not (tmp_path / 'p').exists()


# ----------------------------------------------------------------- verdict
F = Fraction


@pytest.mark.parametrize('lexical,semantic,interval,executed,failed,expected', [
    (F(45, 100), F(46, 100), [F(-2, 100), F(1, 100)], 400, 0, (True, True, True)),
    (F(44, 100), F(46, 100), [F(-3, 100), F(0)], 400, 0, (True, True, True)),  # both bounds exactly reached
    (F(50, 100), F(46, 100), [F(1, 100), F(6, 100)], 400, 0, (True, True, True)),
    (F(4399, 10000), F(46, 100), [F(-3, 100), F(0)], 400, 0, (False, True, False)),  # point criterion only
    (F(45, 100), F(46, 100), [F(-301, 10000), F(1, 100)], 400, 0, (True, False, False)),  # interval only
    (F(45, 100), F(46, 100), None, 400, 0, (True, False, False)),  # missing interval never passes
    (F(45, 100), F(46, 100), [F(-2, 100), F(1, 100)], 400, 1, (True, True, False)),  # one failure
    (F(45, 100), F(46, 100), [F(-2, 100), F(1, 100)], 399, 0, (True, True, False)),  # one question missing
], ids=['met', 'met_on_both_bounds', 'met_when_higher', 'point_only_fails', 'ci_only_fails', 'no_interval',
        'failure', 'not_all_executed'])
def test_verdict_is_the_mechanical_threshold(lexical, semantic, interval, executed, failed, expected):
    outcome = beam_h2.verdict(lexical, semantic, interval, executed=executed, expected=400, failed=failed)
    assert (outcome['point_criterion_met'], outcome['ci_criterion_met'], outcome['threshold_met']) == expected
    assert outcome['decision'] == ('lexical_may_become_recommended_default' if expected[2] else
                                   'semantic_stays_recommended_lexical_light_profile_gap_published')
    assert 'no claim of superior recall' in outcome['claim']


def test_exact_boundaries_do_not_depend_on_binary_rounding():
    # 0.05 - 0.02 > 0.03 in binary floating point; the frozen rule is ">=" on the true ratios.
    assert 0.05 - 0.02 > 0.03
    assert beam_h2.verdict(F(3, 100), F(5, 100), [F(-3, 100), F(0)], executed=400, expected=400,
                           failed=0)['threshold_met'] is True
    assert beam_h2.exact_recall(1 / 3, 3) == F(1, 3) and beam_h2.exact_recall(0.0, 7) == 0
    with pytest.raises(ValueError):
        beam_h2.exact_recall(0.3, 3)


def paired_rows(clusters=24):
    rows, groups = [], {}
    for cluster in range(clusters):
        for question in range(3 + cluster % 4):
            evidence = 1 + (cluster + question) % 3
            lexical = F((cluster * 7 + question) % (evidence + 1), evidence)
            semantic = F((cluster * 5 + question * 3) % (evidence + 1), evidence)
            groups.setdefault(f'c{cluster:02d}', []).append(lexical - semantic)
            rows.append({'qid': f'{cluster}-{question}', 'conversation': f'c{cluster:02d}',
                         'memory': {'recall': float(lexical), 'mrr': 0.0},
                         'bm25': {'recall': float(semantic), 'mrr': 0.0}})
    return rows, groups


def test_bootstrap_is_deterministic_and_is_the_beam_v2_procedure():
    rows, groups = paired_rows()
    first = beam_h2.paired_bootstrap(groups)
    assert first == beam_h2.paired_bootstrap(copy.deepcopy(groups)) and first[0] <= first[1]
    assert first != beam_h2.paired_bootstrap(groups, seed=beam_h2.SEED + 1)
    reference = summarize(rows, [row['qid'] for row in rows])['paired']['recall']  # lexical as "memory"
    assert [float(value) for value in first] == pytest.approx(reference['ci95'], abs=1e-12)
    assert float(sum(map(sum, groups.values())) / len(rows)) == pytest.approx(reference['mean'], abs=1e-12)
    assert beam_h2.paired_bootstrap(dict(list(groups.items())[:19])) is None  # fewer than 20 conversations
    with pytest.raises(ValueError):
        beam_h2.paired_bootstrap(groups, replicates=1000)


# -------------------------------------------------------------- end to end
def test_lexical_worker_imports_no_ml_and_the_run_scores_only_when_complete(frozen_plan):
    directory, data, spec, host, fingerprint = frozen_plan
    result = beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
    assert host.launched == ['lexical', 'semantic']
    lexical, semantic = result['arms']['lexical'], result['arms']['semantic']
    assert lexical['ml_free'] is True and lexical['ml_import_attempts'] == []
    assert set(lexical['ml_modules']) >= {'start', 'import', 'end'}
    assert not any(lexical['ml_modules'].values())
    assert lexical['config_patch'] == {} and lexical['engine_version'] == '4.1.0'
    assert Path(lexical['engine_file']).is_relative_to(V4 / 'src')
    marker = {'name': 'lexical', 'policy': 'lex-4.1.0-v1'}
    assert [c['marker'] for c in lexical['conversations']] == [marker] * 4
    assert all(c['vector_count'] == 0 and c['items']['fact'] == 6 for c in lexical['conversations'])
    assert semantic['config_patch'] == {'embed_model': 'hash'}
    assert all(c['marker'] is None and c['vector_count'] > 0 for c in semantic['conversations'])
    assert (result['expected'], result['executed'], result['failed'], result['scored']) == (40, 40, 0, 32)
    assert result['complete'] is True and result['synthetic'] is True and result['clusters'] == 4
    assert result['recall_at_10']['lexical']['value'] == 1.0  # each question names its evidence turn
    delta = result['paired_delta']
    assert Fraction(delta['exact']) == (Fraction(result['recall_at_10']['lexical']['exact'])
                                        - Fraction(result['recall_at_10']['semantic']['exact']))
    assert delta['ci95'][0] <= delta['mean'] <= delta['ci95'][1]
    assert result['threshold_met'] == (result['point_criterion_met'] and result['ci_criterion_met'])
    output = directory / 'retrieval'
    for arm in beam_h2.ARMS:  # the workers never write an annotation, and leave no store behind
        text = (output / arm / 'rankings.jsonl').read_text()
        assert len(text.splitlines()) == 40 and 'SECRET' not in text and 'category' not in text
        assert list((output / arm / 'stores').iterdir()) == []
    assert len((output / 'questions.jsonl').read_text().splitlines()) == 40
    with pytest.raises(FileExistsError):  # one attempt per frozen manifest
        beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
    invalid = beam_h2.assemble(json.loads((directory / 'plan.json').read_text()), directory, None,
                               replace(spec, synthetic=False))
    assert invalid['threshold_met'] is False and invalid['recall_at_10'] is None
    assert 'semantic: injected engine or configuration' in invalid['invalid_reasons']


@pytest.mark.parametrize('failing', ['lexical', 'semantic'])
def test_a_failed_arm_is_never_scored_and_never_meets_the_threshold(frozen_plan, failing):
    directory, data, spec, host, fingerprint = frozen_plan
    host.fail = failing
    result = beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
    assert host.launched == (['lexical'] if failing == 'lexical' else ['lexical', 'semantic'])
    assert result['complete'] is False and result['threshold_met'] is False
    assert result['decision'] == 'semantic_stays_recommended_lexical_light_profile_gap_published'
    assert (result['executed'], result['failed']) == (0, 40)
    assert result['recall_at_10'] is None and result['paired_delta'] is None and result['scored'] is None
    assert not (directory / 'retrieval/questions.jsonl').exists()
    assert json.loads((directory / 'retrieval/result.json').read_text())['invalid_reasons']


def test_lexical_arm_refuses_an_interpreter_that_already_holds_or_cannot_block_ml(frozen_plan, monkeypatch):
    directory, data, spec, host, fingerprint = frozen_plan
    plan = json.loads((directory / 'plan.json').read_text())
    monkeypatch.setattr('bench.e2e.network.deny_ip_network', lambda: None)
    with pytest.raises(RuntimeError, match='fresh interpreter'):
        asyncio.run(beam_h2.run_arm(plan, 'lexical', directory, block=None))
    monkeypatch.setitem(sys.modules, 'torch', object())
    with pytest.raises(RuntimeError, match='fresh interpreter'):
        asyncio.run(beam_h2.run_arm(plan, 'lexical', directory, block=beam_h2.MLBlock()))
    assert not (directory / 'stores').exists()


# ------------------------------------------------ freeze revision 2 (Codex verdict on freeze v1)
def invalid_and_unscored(directory, result):
    stored = json.loads((directory / 'retrieval/result.json').read_text())
    assert stored['threshold_met'] is False and stored['complete'] is False and stored['invalid_reasons']
    assert stored['decision'] == 'semantic_stays_recommended_lexical_light_profile_gap_published'
    assert stored['recall_at_10'] is None and stored['paired_delta'] is None and stored['scored'] is None
    assert not (directory / 'retrieval/questions.jsonl').exists()
    assert result is None or (result['threshold_met'] is False and result['invalid_reasons'])
    return stored['invalid_reasons']


def test_a_final_resource_stop_after_both_arms_leaves_an_invalid_result_and_no_score(frozen_plan):
    """Point 1: every final gate runs BEFORE the scorer; both arms had completed here."""
    directory, data, spec, host, fingerprint = frozen_plan
    host.fail_guard = 2  # the first guard opens the run, the second is the final gate
    result = beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
    assert host.launched == ['lexical', 'semantic'] and host.guards == 2
    assert all((directory / 'retrieval' / arm / 'attestation.json').exists() for arm in beam_h2.ARMS)
    reasons = invalid_and_unscored(directory, result)
    assert reasons == ['final gates: swap 9000000000 > 8500000000 bytes']
    assert not (directory / 'retrieval/resources.json').exists()


def test_a_freeze_drift_seen_only_at_the_end_is_never_scored(frozen_plan):
    directory, data, spec, host, fingerprint = frozen_plan
    host.after_launch = lambda arm: arm == 'semantic' and setattr(host, 'lock', 'numpy==9.9.9\n')
    reasons = invalid_and_unscored(directory, beam_h2.retrieve(directory, fingerprint, spec=spec, host=host))
    assert reasons == ['final gates: Interpreter environment changed']


def test_a_scoring_error_or_an_interruption_never_leaves_a_positive_result(frozen_plan, monkeypatch,
                                                                           tmp_path):
    directory, data, spec, host, fingerprint = frozen_plan

    def broken(value, evidence):
        raise ZeroDivisionError('scorer defect')
    monkeypatch.setattr(beam_h2, 'exact_recall', broken)
    reasons = invalid_and_unscored(directory, beam_h2.retrieve(directory, fingerprint, spec=spec, host=host))
    assert reasons == ["scoring: ZeroDivisionError('scorer defect')"]
    monkeypatch.undo()
    other = FakeHost(tmp_path / 'second')
    data, spec = beam_h2.selfcheck_corpus(tmp_path / 'second/corpus', model_cache=None)
    done = beam_h2.prepare(tmp_path / 'second/plan', data, other.head, spec=spec, host=other)
    other.fail = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        beam_h2.retrieve(tmp_path / 'second/plan', done['manifest_sha256'], spec=spec, host=other)
    assert 'interrupted' in invalid_and_unscored(tmp_path / 'second/plan', None)[0]


RUNTIME = {'sys_path': ['/clone/v4/src', '/clone/v4', '/site'], 'executable': '/bin/python',
           'prefix': '/venv', 'base_prefix': '/base', 'python': '3.11',
           'distributions': ['torch==2.10.0 @ /site'],
           'files': {'/site/torch/__init__.py': 'a' * 64}}


def test_runtime_content_is_frozen_and_checked_at_the_start_and_before_scoring(tmp_path):
    """Point 5: same versions, another content or another import path -> refused."""
    host = FakeHost(tmp_path)
    host.runtime_value = copy.deepcopy(RUNTIME)
    data, spec = beam_h2.selfcheck_corpus(tmp_path / 'corpus', model_cache=None)
    directory = tmp_path / 'plan'
    fingerprint = beam_h2.prepare(directory, data, host.head, spec=spec, host=host)['manifest_sha256']
    plan = beam_h2.frozen(directory, fingerprint, spec=spec, host=host)
    assert plan['runtime']['files'] == 1 and plan['runtime']['sys_path'] == RUNTIME['sys_path']
    assert json.loads((directory / 'manifest.json').read_text())['h2']['runtime'] == plan['files']['runtime']
    for change in (lambda value: value['files'].__setitem__('/site/torch/__init__.py', 'b' * 64),
                   lambda value: value['sys_path'].append('/elsewhere'),
                   lambda value: value['files'].__setitem__('/elsewhere/torch.py', 'c' * 64)):
        host.runtime_value = copy.deepcopy(RUNTIME)
        change(host.runtime_value)
        with pytest.raises(ValueError, match='Interpreter runtime changed'):
            beam_h2.frozen(directory, fingerprint, spec=spec, host=host)
        with pytest.raises(ValueError, match='Interpreter runtime changed'):
            beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)
        beam_h2.frozen(directory, fingerprint, spec=spec, host=host, runtime=False)  # light check only
    assert host.launched == [] and not (directory / 'retrieval').exists()


def test_tree_digest_sees_a_content_change_at_constant_name_and_version(tmp_path):
    package = tmp_path / 'site/torch'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('version = "2.10.0"\n')
    (package / '__pycache__').mkdir()
    (package / '__pycache__/x.pyc').write_text('cache')
    (tmp_path / 'site/link.py').symlink_to(package / '__init__.py')
    before = beam_h2.tree_files(tmp_path / 'site')
    assert list(before) == [str(package / '__init__.py')]  # caches and links are not content
    (package / '__init__.py').write_text('version = "2.10.0"\nimport os\n')
    assert beam_h2.tree_files(tmp_path / 'site') != before
    with pytest.raises(ValueError, match='run_beam_h2.sh'):  # pytest is not the frozen launcher
        beam_h2.runtime_record(beam_h2.Host.clone)


def test_loaded_modules_must_come_from_the_frozen_runtime_or_the_attested_clone(tmp_path):
    site, clone, outside = tmp_path / 'site', tmp_path / 'clone', tmp_path / 'outside'
    for folder in (site, clone / 'v4/src/engine', outside):
        folder.mkdir(parents=True)
    files = {site / 'dep.py': 'dep = 1\n', clone / 'v4/src/engine/core.py': 'core = 1\n',
             outside / 'torch.py': 'evil = 1\n'}
    for path, text in files.items():
        path.write_text(text)
    digest = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items()}
    runtime = {'sys_path': [str(clone / 'v4/src'), str(clone / 'v4'), str(site)],
               'files': {str(site / 'dep.py'): digest[site / 'dep.py']}}
    attested = {'v4/src/engine/core.py': digest[clone / 'v4/src/engine/core.py']}

    def _module(path):
        value = type(sys)('fixture')
        value.__file__ = str(path)
        return value
    good = {'dep': _module(site / 'dep.py'), 'engine.core': _module(clone / 'v4/src/engine/core.py'),
            'sys': sys}
    proof = beam_h2.loaded_origins(runtime, attested, clone, {**good, 'torch.ops': _module('_ops.py')})
    assert proof['problems'] == [] and proof['files'] == 2 and proof['by_root'] == {str(site): 1, 'clone': 1}
    assert proof['in_memory_modules'] == {'torch.ops': '_ops.py'}  # built in memory, recorded, not a file
    shadow = beam_h2.loaded_origins(runtime, attested, clone,
                                    {**good, 'torch': _module(outside / 'torch.py')})
    assert shadow['problems'] == [f"torch: loaded from outside the frozen paths: {outside / 'torch.py'}"]
    (site / 'dep.py').write_text('dep = 2\n')
    changed = beam_h2.loaded_origins(runtime, attested, clone, good)
    assert changed['problems'] == [f"dep: content differs from the freeze: {site / 'dep.py'}"]
    unattested = beam_h2.loaded_origins(runtime, {}, clone, good)
    assert any('engine.core: loaded from outside' in problem for problem in unattested['problems'])


def git(repo, *arguments):
    command = ['git', '-C', str(repo), '-c', 'user.name=fixture', '-c', 'user.email=f@example.invalid',
               '-c', 'commit.gpgsign=false', *arguments]
    done = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def test_source_attestation_covers_resources_tests_and_documents_and_refuses_strays(tmp_path):
    """Point 8: not only *.py; nothing untracked may sit in an importable tree."""
    repo = tmp_path / 'repo'
    (repo / 'v4/src/engine/resources').mkdir(parents=True)
    (repo / 'v4/tests').mkdir()
    (repo / 'v4/src/engine/core.py').write_text('core = 1\n')
    (repo / 'v4/src/engine/resources/catalog.json').write_text('{"revision": "x"}\n')
    (repo / 'v4/tests/test_x.py').write_text('def test_x(): pass\n')
    git(tmp_path, 'init', '-q', str(repo))
    git(repo, 'add', '-A')
    git(repo, 'commit', '-q', '-m', 'fixture')
    head = git(repo, 'rev-parse', 'HEAD')
    paths, strict = ('v4/src', 'v4/tests/test_x.py'), ('v4/src',)
    files = beam_h2.attest_tree(repo, head, paths, strict)
    assert sorted(files) == ['v4/src/engine/core.py', 'v4/src/engine/resources/catalog.json',
                             'v4/tests/test_x.py']
    (repo / 'v4/src/engine/__pycache__').mkdir()
    (repo / 'v4/src/engine/__pycache__/core.pyc').write_text('cache')
    assert beam_h2.attest_tree(repo, head, paths, strict) == files  # bytecode caches are never read (see env)
    for name, text in (('v4/src/engine/resources/catalog.json', '{"revision": "y"}\n'),
                       ('v4/tests/test_x.py', 'def test_x(): assert 0\n')):
        original = (repo / name).read_text()
        (repo / name).write_text(text)
        with pytest.raises(ValueError, match='does not match the working tree'):
            beam_h2.attest_tree(repo, head, paths, strict)
        (repo / name).write_text(original)
    (repo / 'v4/src/torch.py').write_text('shadow = 1\n')
    with pytest.raises(ValueError, match='outside the declared commit'):
        beam_h2.attest_tree(repo, head, paths, strict)
    (repo / 'v4/src/torch.py').unlink()
    with pytest.raises(ValueError, match='Cannot attest'):
        beam_h2.attest_tree(repo, head, ('v4/src', 'v4/docs/missing.md'), strict)
    assert {'v4/tests/test_beam_h2_410.py', 'v4/docs/H2_RUN_v2.md', 'v4/docs/LEXICAL_PROFILE_4_1_0.md',
            'v4/bench/protocols/v1.md', 'scripts/run_beam_h2.sh'} <= set(beam_h2.ATTESTED)


def test_tests_and_documents_are_archived_with_the_freeze(frozen_plan):
    directory, data, spec, host, fingerprint = frozen_plan
    receipt = json.loads((directory / 'receipt.json').read_text())
    assert receipt['tests_and_documents'] == {'v4/tests/test_beam_h2_410.py': 'tests-v1',
                                              'v4/docs/H2_RUN_v2.md': 'doc-v1'}
    assert json.loads((directory / 'plan.json').read_text())['attested_files'] == host.attest(host.head)


def test_worker_environment_is_explicit_and_inherits_no_cache_or_import_variable(frozen_plan, tmp_path):
    """Point 6: the Hugging Face cache is the frozen one; inherited redirections are refused."""
    directory, data, spec, host, fingerprint = frozen_plan
    plan = json.loads((directory / 'plan.json').read_text())
    plan['spec']['model_cache'] = str(beam_h2.GRANITE_CACHE)
    expected = beam_h2.worker_environment(plan, 'semantic', Path('/clone'), tmp_path / 'out')
    assert expected['HF_HUB_CACHE'] == '/Users/romain/.cache/huggingface/hub'
    assert expected['HF_HOME'] == '/Users/romain/.cache/huggingface'
    assert expected['PYTHONPATH'] == '/clone/v4/src:/clone/v4' and expected['PYTHONSAFEPATH'] == '1'
    assert expected['PYTHONPYCACHEPREFIX'] == str(tmp_path / 'out/pycache')
    assert expected['HF_HUB_OFFLINE'] == '1' and expected['PYTORCH_ENABLE_MPS_FALLBACK'] == '0'
    forbidden = plan['forbidden_env_prefixes']
    clean = {**expected, 'PATH': '/usr/bin', 'HOME': '/h'}
    assert beam_h2.environment_problems(expected, forbidden, clean) == []
    for name in ('HF_HUB_CACHE', 'HF_HOME'):
        assert beam_h2.environment_problems(expected, forbidden, {**expected, name: '/tmp/other'}) == [
            f'{name} must be {expected[name]!r}']
    for name in ('TRANSFORMERS_CACHE', 'SENTENCE_TRANSFORMERS_HOME', 'HF_ENDPOINT', 'HUGGINGFACE_HUB_CACHE',
                 'XDG_CACHE_HOME', 'PYTHONSTARTUP', 'PYTHONHOME', 'DYLD_INSERT_LIBRARIES', 'MEMORY_V4_DEVICE',
                 'PYTORCH_MPS_PREFER_METAL', 'TORCH_HOME'):
        assert beam_h2.environment_problems(expected, forbidden, {**expected, name: 'x'}) == [
            f'{name} is inherited']
    missing = {key: value for key, value in expected.items() if key != 'HF_HUB_OFFLINE'}
    assert beam_h2.environment_problems(expected, forbidden, missing) == ["HF_HUB_OFFLINE must be '1'"]
    lexical = beam_h2.worker_environment(plan, 'lexical', Path('/clone'), tmp_path / 'out')
    assert 'PYTORCH_ENABLE_MPS_FALLBACK' not in lexical
    assert lexical['HF_HUB_CACHE'] == expected['HF_HUB_CACHE']


def real_like(directory):
    plan = json.loads((directory / 'plan.json').read_text())
    plan['runtime'] = {'files': 1}
    start = {'environment_problems': [], 'runtime_verified': True,
             'loaded_modules': {'problems': [], 'files': 10}}
    binding = {'problems': [], 'weights_equal': True, 'tokenizer_equal': True}
    encoder = {'model_revision': beam_h2.GRANITE_REVISION, 'device': 'mps', 'parameter_devices': ['mps'],
               'parameter_dtypes': ['torch.bfloat16'], 'binding': binding}
    usage = {'encoder_calls': 73, 'encoder_texts': 89, 'model_device': 'mps', 'model_dtype': 'torch.bfloat16',
             'llm_calls': 0, 'reranker_calls': 0}
    common = {'engine': 'memory_os_v4', 'config_patch': {}, 'questions': 40, 'rankings_sha256': 'r', **start}
    return plan, copy.deepcopy({**common, 'ml_free': True}), {**common, 'encoder': encoder, 'usage': usage}


SEMANTIC_DEFECTS = {
    'usage_missing': (lambda proof: proof.pop('usage'), 'no encoder usage record'),
    'usage_not_a_record': (lambda proof: proof.__setitem__('usage', []), 'no encoder usage record'),
    'usage_zero_calls': (lambda proof: proof['usage'].__setitem__('encoder_calls', 0),
                         'usage record invalid'),
    'usage_calls_missing': (lambda proof: proof['usage'].pop('encoder_calls'), 'usage record invalid'),
    'usage_calls_not_integer': (lambda proof: proof['usage'].__setitem__('encoder_calls', '73'),
                                'usage record invalid'),
    'usage_over_ceiling': (lambda proof: proof['usage'].update(encoder_calls=100_001, encoder_texts=100_001),
                           'encoder call budget exceeded'),
    'usage_other_device': (lambda proof: proof['usage'].__setitem__('model_device', 'cpu'),
                           'another device or dtype'),
    'usage_llm_call': (lambda proof: proof['usage'].__setitem__('llm_calls', 1), 'LLM or reranker'),
    'device_cpu': (lambda proof: proof['encoder'].update(device='cpu', parameter_devices=['cpu']),
                   "encoder device resolved to 'cpu'"),
    'dtype_float32': (lambda proof: proof['encoder'].__setitem__('parameter_dtypes', ['torch.float32']),
                      "encoder parameter_dtypes resolved to ['torch.float32']"),
    'other_revision': (lambda proof: proof['encoder'].__setitem__('model_revision', 'f' * 40),
                       'not the frozen Granite revision'),
    'no_encoder': (lambda proof: proof.__setitem__('encoder', None), 'no encoder attestation'),
    'no_binding': (lambda proof: proof['encoder'].pop('binding'), 'not bound to the frozen snapshot'),
    'weights_differ': (lambda proof: proof['encoder']['binding'].__setitem__('weights_equal', False),
                       'not bound to the frozen snapshot'),
    'tokenizer_differs': (lambda proof: proof['encoder']['binding'].__setitem__('tokenizer_equal', False),
                          'not bound to the frozen snapshot'),
    'binding_problem': (lambda proof: proof['encoder']['binding'].__setitem__('problems', ['other cache']),
                        'not bound to the frozen snapshot'),
    'environment_unchecked': (lambda proof: proof.pop('environment_problems'), 'not attested at start'),
    'runtime_unverified': (lambda proof: proof.__setitem__('runtime_verified', False),
                           'not attested at start'),
    'origins_missing': (lambda proof: proof.pop('loaded_modules'), 'origins of loaded modules'),
    'origin_outside': (lambda proof: proof['loaded_modules'].__setitem__('problems', ['torch: outside']),
                       'origins of loaded modules'),
    'rankings_changed': (lambda proof: proof.__setitem__('rankings_sha256', 'other'), 'rankings differ'),
    'patched': (lambda proof: proof.__setitem__('config_patch', {'device': 'cpu'}), 'injected engine'),
}


@pytest.mark.parametrize('defect', sorted(SEMANTIC_DEFECTS))
def test_each_missing_or_divergent_worker_proof_invalidates_the_run(frozen_plan, defect):
    """Points 2, 4, 5, 6: frozen device/dtype, mandatory usage record, runtime and snapshot binding."""
    directory, data, spec, host, fingerprint = frozen_plan
    plan, lexical, semantic = real_like(directory)
    real = replace(spec, synthetic=False)
    assert beam_h2.arm_problems('lexical', lexical, plan, real, 40, 'r') == []
    assert beam_h2.arm_problems('semantic', semantic, plan, real, 40, 'r') == []  # precondition
    change, message = SEMANTIC_DEFECTS[defect]
    change(semantic)
    problems = beam_h2.arm_problems('semantic', semantic, plan, real, 40, 'r')
    assert problems and any(message in problem for problem in problems), problems
    assert beam_h2.arm_problems('semantic', None, plan, real, 40, 'r') == ['semantic: no attestation']
    lexical['ml_free'] = False
    assert beam_h2.arm_problems('lexical', lexical, plan, real, 40, 'r') == ['lexical: ML-free proof missing']


def test_a_real_semantic_arm_without_usage_record_invalidates_the_whole_result(frozen_plan):
    """Point 4, end to end: the completed synthetic run is re-assembled with the usage record removed."""
    directory, data, spec, host, fingerprint = frozen_plan
    assert beam_h2.retrieve(directory, fingerprint, spec=spec, host=host)['complete'] is True
    plan, _, semantic = real_like(directory)
    path = directory / 'retrieval/semantic/attestation.json'
    stored = json.loads(path.read_text())
    for strip in (False, True):
        proof = {**stored, **semantic, 'questions': 40, 'rankings_sha256': stored['rankings_sha256']}
        if strip:
            del proof['usage']
        path.write_text(json.dumps(proof))
        plan['runtime'] = None
        result = beam_h2.assemble(plan, directory, None, spec)
        assert result['complete'] is not strip
    assert invalid_and_unscored(directory, result) == ['semantic: no encoder usage record']


def test_budgets_are_declared_in_their_effective_units(frozen_plan, monkeypatch, tmp_path):
    """Point 3: byte limits are bytes; 512 is the encoder sequence length, not the query budget."""
    directory, data, spec, host, fingerprint = frozen_plan
    manifest = json.loads((directory / 'manifest.json').read_text())
    budgets = manifest['budgets']
    assert [budgets[name] for name in ('memory_bytes', 'ingest_tokens', 'context_tokens', 'query_tokens',
                                       'model_calls', 'wall_seconds', 'api_usd')] == [
        16_000_000, 16_000_000, 12_000, 4096, 100_000, 28_800, 0]
    for name in ('ingest_tokens', 'context_tokens', 'query_tokens'):
        assert budgets['units'][name].startswith('UTF-8 BYTES, not tokens')
    assert 512 not in budgets.values() and 3000 not in budgets.values() and 4_000_000 not in budgets.values()
    embedding = manifest['models']['embedding']
    assert embedding['max_seq_length'] == 512
    assert 'NOT the shared query budget' in embedding['max_seq_length_meaning']
    assert embedding['expected_resolution'] == {'device': 'mps', 'parameter_devices': ['mps'],
                                                'parameter_dtypes': ['torch.bfloat16']}
    assert 'no timing claim' in manifest['timing']['note']
    assert 'incident review' in manifest['h2']['retry']
    plan = json.loads((directory / 'plan.json').read_text())
    assert plan['audit']['longest_question_bytes'] < 100 and plan['query_bytes'] == 4096
    monkeypatch.setattr(beam_h2, 'QUERY_BYTES', 10)
    with pytest.raises(ValueError, match='query byte budget'):
        beam_h2.prepare(tmp_path / 'long', data, host.head, spec=spec, host=host)


class FakeModule:
    def __init__(self, state=None, config=None):
        self.auto_model = self
        self.state, self.config_value = state, config

    def state_dict(self):
        return self.state

    def get_config_dict(self):
        return self.config_value


class FakeModel(list):
    tokenizer = None


def test_loaded_weights_and_tokenizer_are_bound_to_the_frozen_snapshot_files(tmp_path, monkeypatch):
    """Point 6: content equality with the hashed snapshot, whatever path a name resolved to."""
    torch = pytest.importorskip('torch')
    from safetensors.torch import save_file
    import huggingface_hub.constants
    hub = tmp_path / 'hub'
    snapshot = hub / 'models--fixture/snapshots/rev'
    (snapshot / '1_Pooling').mkdir(parents=True)
    state = {'embeddings.weight': torch.arange(12, dtype=torch.float32).reshape(3, 4),
             'layer.bias': torch.ones(4, dtype=torch.float32)}
    save_file(state, str(snapshot / 'model.safetensors'))
    tokenizer = {'version': '1.0', 'truncation': None, 'padding': None, 'model': {'vocab': {'a': 0, 'b': 1}}}
    (snapshot / 'tokenizer.json').write_text(json.dumps(tokenizer))
    (snapshot / '1_Pooling/config.json').write_text(json.dumps({'pooling_mode_cls_token': True}))
    (snapshot / 'config.json').write_text('{}')
    (snapshot / 'modules.json').write_text('[]')
    encoder = {'id': 'fixture/model', 'snapshot': str(snapshot), 'hub_cache': str(hub),
               'snapshot_files': {name: hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
                                  for name in beam_h2.BOUND_FILES}}
    monkeypatch.setattr(huggingface_hub.constants, 'HF_HUB_CACHE', str(hub))
    resolved = {'root': snapshot}
    monkeypatch.setattr(huggingface_hub, 'try_to_load_from_cache',
                        lambda repo, name: str(resolved['root'] / name))

    def model(weights=None, loaded_tokenizer=None, pooling=None):
        weights = weights or {key: tensor.clone() for key, tensor in state.items()}
        value = FakeModel([FakeModule(state=weights),
                           FakeModule(config=pooling or {'pooling_mode_cls_token': True, 'extra': 1})])
        text = json.dumps(loaded_tokenizer or {**tokenizer, 'truncation': {'max_length': 512}})
        backend = type('B', (), {'to_str': lambda self: text})()
        value.tokenizer = type('T', (), {'backend_tokenizer': backend})()
        return value
    good = beam_h2.bind_snapshot(model(), encoder)
    assert good['problems'] == [] and good['weights_equal'] and good['tokenizer_equal']
    assert good['tensors'] == 2
    assert good['parameters'] == 16 and good['bound_files'] == encoder['snapshot_files']
    half = beam_h2.bind_snapshot(model({key: tensor.to(torch.bfloat16) for key, tensor in state.items()}),
                                 encoder)
    assert half['problems'] == []  # same values in the dtype the model was loaded in
    changed = {**state, 'layer.bias': torch.full((4,), 1.5)}
    assert 'loaded weights differ from model.safetensors (0 names, 1 tensors)' in beam_h2.bind_snapshot(
        model(changed), encoder)['problems']
    renamed = {'embeddings.weight': state['embeddings.weight'], 'other.bias': state['layer.bias']}
    assert beam_h2.bind_snapshot(model(renamed), encoder)['weights_equal'] is False
    other = beam_h2.bind_snapshot(model(loaded_tokenizer={**tokenizer, 'model': {'vocab': {'a': 0}}}),
                                  encoder)
    assert other['problems'] == ['loaded tokenizer differs from tokenizer.json']
    assert beam_h2.bind_snapshot(model(pooling={'pooling_mode_cls_token': False}), encoder)['problems'] == [
        'loaded pooling differs from 1_Pooling/config.json']
    resolved['root'] = tmp_path / 'elsewhere'
    elsewhere = beam_h2.bind_snapshot(model(), encoder)['problems']
    assert elsewhere == [f'the model name does not resolve to the frozen snapshot: {name}'
                         for name in ('model.safetensors', 'tokenizer.json')]
    resolved['root'] = snapshot
    monkeypatch.setattr(huggingface_hub.constants, 'HF_HUB_CACHE', str(tmp_path / 'other-cache'))
    assert beam_h2.bind_snapshot(model(), encoder)['problems'] == ['Hugging Face cache is not the frozen one']
    monkeypatch.setattr(huggingface_hub.constants, 'HF_HUB_CACHE', str(hub))
    (snapshot / 'tokenizer.json').write_text(json.dumps({**tokenizer, 'version': '1.1'}))
    assert 'snapshot file changed: tokenizer.json' in beam_h2.bind_snapshot(model(), encoder)['problems']
    proof = {'model_revision': beam_h2.GRANITE_REVISION, **beam_h2.EXPECTED_ENCODER, 'binding': good}
    plan = {'model': beam_h2.frozen_values()['model']}
    assert beam_h2.encoder_problems(proof, plan) == []
    assert beam_h2.encoder_problems({**proof, 'device': 'cpu'}, plan) == [
        "encoder device resolved to 'cpu', frozen expectation 'mps'"]
