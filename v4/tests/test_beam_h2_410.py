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
from bench.e2e.beam import CATEGORIES
from bench.e2e.beam_campaign import summarize
from bench.e2e.client import offline_env

V4 = Path(__file__).resolve().parents[1]
TOPICS = ['harbour lighthouse', 'saffron risotto', 'violin recital', 'glacier trek', 'pottery kiln',
          'telescope mirror']
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


def conversation(index):
    turns = [{'content': f'Conversation {index}: we discussed the {topic} plan number {index}{position}.',
              'role': 'user' if position % 2 == 0 else 'assistant', 'id': position, 'index': 'SECRET',
              'question_type': 'SECRET', 'time_anchor': None} for position, topic in enumerate(TOPICS)]
    probes = {}
    for position, category in enumerate(CATEGORIES):
        key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
               'instruction_following': 'expected_compliance', 'preference_following': 'expected_compliance',
               'summarization': 'ideal_summary'}.get(category, 'answer')
        topic = TOPICS[position % len(TOPICS)]
        probe = {'question': f'What was said about the {topic} ({category})?', key: 'SCORER_SECRET',
                 'rubric': ['SCORER_RUBRIC_SECRET']}
        if category != 'abstention':  # one explicit empty annotation per conversation, as in BEAM
            probe['source_chat_ids'] = [] if category == 'summarization' else [position % len(TOPICS)]
        probes[category] = [probe]
    return {'conversation_id': f'conv-{index}', 'conversation_seed': 'SECRET', 'narratives': 'SECRET',
            'user_profile': 'SECRET', 'conversation_plan': 'SECRET', 'user_questions': 'SECRET',
            'chat': [turns[:3], turns[3:]], 'probing_questions': repr(probes)}


class FakeHost(beam_h2.Host):
    """No Git, no niceness change; workers are the real run_arm in fresh interpreters."""

    def __init__(self, root):
        self.clone = root / 'clone'
        self.clone.mkdir()
        self.head, self.code, self.lock = 'a' * 40, 'code-v1', 'numpy==2.4.2\n'
        self.launched, self.fail = [], None

    def attest(self, revision):
        if revision != self.head:
            raise ValueError('Cannot attest declared source commit')
        return {'code_files': {'bench/e2e/beam_h2.py': self.code}, 'extra_files': {}}

    def archive(self, revision):
        return f'archive {revision} {self.code}'.encode()

    def environment(self):
        return self.lock

    def hardware(self):
        return {'os': 'fixture', 'cpu': 'fixture', 'accelerator': 'none', 'device': 'fixture',
                'python': '3.11', 'ram_gib': 16}

    def guard(self, directory):
        return {'swap_bytes': 0, 'free_bytes': 10 ** 12}

    def launch(self, plan, directory, arm, manifest_sha256, deadline):
        self.launched.append(arm)
        if arm == self.fail:
            raise RuntimeError(f'Worker {arm} failed')
        run_worker(directory, arm, {} if arm == 'lexical' else {'embed_model': 'hash'})


def run_worker(directory, arm, patch):
    script = directory / 'driver.py'
    script.write_text(DRIVER)
    env = offline_env()
    env['PYTHONPATH'] = os.pathsep.join([str(V4), str(V4 / 'src')])
    output = directory / 'retrieval' / arm
    command = [sys.executable, '-B', str(script), arm, str(directory / 'plan.json'), str(output), json.dumps(patch)]
    done = subprocess.run(command, env=env, cwd=output, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-3000:]


@pytest.fixture
def frozen_plan(tmp_path):
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    data = corpus / 'beam.jsonl'
    data.write_text(''.join(json.dumps(conversation(index)) + '\n' for index in range(4)))
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    spec = beam_h2.Spec(dataset_sha256=digest, conversations=4, messages=24, questions=40, scored=32,
                        minimum_clusters=4, model_cache=None, synthetic=True)
    (corpus / 'PROVENANCE.json').write_text(json.dumps({
        'revision': spec.revision, 'license': 'cc-by-sa-4.0', 'derived_jsonl': {'sha256': digest}}))
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


def test_real_launcher_refuses_a_plan_that_is_not_the_frozen_h2_specification(frozen_plan):
    """The real worker process re-attests the freeze itself: a synthetic plan stops before any engine."""
    directory, data, spec, host, fingerprint = frozen_plan
    plan = json.loads((directory / 'plan.json').read_text())
    (directory / 'retrieval/lexical').mkdir(parents=True)
    with pytest.raises(RuntimeError, match='Worker lexical failed'):
        beam_h2.Host().launch(plan, directory, 'lexical', fingerprint, time.monotonic() + 120)
    assert 'STOP: ' in (directory / 'retrieval/lexical/worker.log').read_text()
    assert sorted(path.name for path in (directory / 'retrieval/lexical').iterdir()) == ['worker.log']


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
