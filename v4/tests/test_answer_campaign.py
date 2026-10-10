"""Synthetic freshness, durable quota resume and complete-denominator proofs.

No encoder, CLI inference, API or remote dataset is used.
"""
import json
from types import SimpleNamespace

import pytest

from bench.e2e.answer_campaign import (Ledger, accounting, execute_batch, final_report,
                                      quota_rejection, tasks)
from bench.e2e.beam import CATEGORIES, conversations, decode, identity
from bench.e2e.heldout_audit import screening, selection, text_signatures


def row(index):
    probes = {}
    for category in CATEGORIES:
        key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
               'instruction_following': 'expected_compliance', 'preference_following': 'expected_compliance',
               'summarization': 'ideal_summary'}.get(category, 'answer')
        probes[category] = [{'question': f'Unique question {index} {category}', key: 'GOLD-SECRET',
                            'rubric': ['RUBRIC-SECRET'], **({} if category == 'abstention' else {'source_chat_ids': [0]})}]
    return {'conversation_id': str(index), 'conversation_seed': {'category': 'test', 'id': index,
            'title': f'title{index}', 'theme': f'theme{index}'}, 'narratives': f'narrative{index}',
            'conversation_plan': 'SECRET', 'user_profile': 'SECRET', 'user_questions': 'SECRET',
            'chat': [[{'id': 0, 'role': 'user', 'content': (f'conversation {index} ' * 40),
                       'index': 'SECRET', 'question_type': 'SECRET', 'time_anchor': None}]],
            'probing_questions': repr(probes)}


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return path


def test_freshness_excludes_whole_shared_scenario_and_groups_internal_dependence(tmp_path):
    seen = row(99)
    fresh = [row(i) for i in range(35)]
    fresh[0]['conversation_seed'] = seen['conversation_seed']
    fresh[2]['conversation_seed'] = fresh[1]['conversation_seed']
    data = write_rows(tmp_path / 'new', fresh)
    observed = write_rows(tmp_path / 'old', [seen])
    lme = tmp_path / 'lme'
    lme.write_text('[]')
    audit = screening(data, observed, lme)
    assert len(audit['excluded']) == 1
    assert 'seed' in audit['excluded'][0]['reasons']
    assert audit['independent_components'] == 33
    chosen = selection(data, audit)
    assert len(chosen) == 340 and len({q['cluster'] for q in chosen}) == 33
    assert identity('beam-conversation-v1', '0') not in {q['conversation'] for q in chosen}
    # Gold edits never affect selection or the observable source identities.
    write_rows(data, [dict(r, probing_questions=r['probing_questions'].replace('GOLD-SECRET', 'OTHER')) for r in fresh])
    assert selection(data, audit) == chosen


def test_insufficient_independent_conversations_refuses_before_inference(tmp_path):
    data = write_rows(tmp_path / 'new', [row(i) for i in range(35)])
    observed = write_rows(tmp_path / 'old', [row(i) for i in range(10)])
    lme = tmp_path / 'lme'
    lme.write_text('[]')
    audit = screening(data, observed, lme)
    with pytest.raises(ValueError, match='No inference authorized'):
        selection(data, audit)


def test_shared_longmem_question_excludes_whole_conversation(tmp_path):
    data = write_rows(tmp_path / 'new', [row(1)])
    observed = write_rows(tmp_path / 'old', [row(99)])
    lme = tmp_path / 'lme'
    lme.write_text(json.dumps([{'question': 'Unique question 1 abstention', 'haystack_sessions': []}]))
    assert screening(data, observed, lme)['excluded'][0]['reasons'] == ['question']


def test_shared_history_excerpt_and_normalization_screen():
    text = ' '.join(f'unique{i}' for i in range(150))
    assert text_signatures(text) & text_signatures('HEADER ' + text)
    assert text_signatures(text.upper()) == text_signatures(text)


def test_bounded_loader_keeps_historical_default_and_explicit_larger_node_budget(tmp_path):
    text = json.dumps(list(range(100001)))
    with pytest.raises(ValueError, match='node/depth'):
        decode(text)
    assert len(decode(text, max_nodes=500000)) == 100001
    c = list(conversations(write_rows(tmp_path / 'one', [row(1)]), max_nodes=500000))[0]
    assert 'SECRET' not in repr(c.turns)


@pytest.mark.parametrize('events', [
    [{'type': 'turn.completed', 'usage': {}}],
    [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'answer'}},
     {'type': 'error', 'message': "You've hit your usage limit. Try later."}],
    [{'type': 'error', 'message': 'Connection reset'}],
    [{'type': 'error', 'message': 'rate limit'}],
    [{'type': 'item.completed', 'item': {'type': 'command_execution'}},
     {'type': 'error', 'message': "You've hit your usage limit"}],
])
def test_unknown_or_generated_or_tool_errors_are_not_quota_resumable(events):
    assert not quota_rejection('\n'.join(json.dumps(e) for e in events))


def test_only_pre_generation_usage_limit_is_resumable():
    assert quota_rejection(json.dumps({'type': 'thread.started'}) + '\n' +
                           json.dumps({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit. Try later."}}))
    assert not quota_rejection('malformed')


def setup_campaign(tmp_path, count=2):
    plan = {'question_ids': [str(i) for i in range(count)], 'arms': ['memory', 'bm25'],
            'selected': [{'qid': str(i), 'cluster': str(i), 'conversation': str(i), 'category': 'test'} for i in range(count)],
            'tasks': count * 4, 'max_attempts': count * 4 + 200,
            'caps': {'observed_tokens_stop_threshold': 16000000, 'cli_seconds': 60000, 'batch_seconds': 3600},
            'reader': {'model': 'gpt-5.6-luna', 'effort': 'low'},
            'judge': {'model': 'gpt-6-astra', 'effort': 'high'}, 'claim': 'Only reproduced BM25 comparison'}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    ctx = {str(i): {'question': f'question{i}', 'contexts': {'memory': ['NEW-VALUE'], 'bm25': ['OLD-VALUE']}} for i in range(count)}
    golds = {str(i): SimpleNamespace(question=f'question{i}', gold=SimpleNamespace(answers=['GOLD-SECRET']), rubric=('RUBRIC-SECRET',)) for i in range(count)}
    return plan, ctx, golds


class FakeRunner:
    calls = []
    failure_at = None
    mode = 'quota'
    def __init__(self, root, *args, **kwargs):
        self.root = root
        root.mkdir()
        self.records = []
    def invoke(self, payload, field):
        self.calls.append((payload, field))
        usage = {'input_tokens': 100, 'output_tokens': 10, 'cached_input_tokens': 20}
        if len(self.calls) == self.failure_at:
            record = {'accepted': False, 'usage': None, 'seconds': 1}
            events = {'type': 'error', 'message': "You've hit your usage limit" if self.mode == 'quota' else 'Connection reset'}
        else:
            record = {'accepted': True, 'usage': usage, 'seconds': 1}
            events = {'type': 'fake_completed'}
        self.records.append(record)
        (self.root / 'usage.json').write_text(json.dumps({'attempt_records': self.records}))
        (self.root / '000.events.jsonl').write_text(json.dumps(events))
        if not record['accepted']:
            raise RuntimeError('synthetic failure')
        return payload['observations'][0] if field == 'answer' else payload['candidate'] == 'NEW-VALUE'


@pytest.fixture(autouse=True)
def reset_runner():
    FakeRunner.calls, FakeRunner.failure_at, FakeRunner.mode = [], None, 'quota'


def execute(tmp_path, plan, ctx, golds, batch=40, resume=False):
    return execute_batch(tmp_path, plan, ctx, golds, batch, resume, FakeRunner, lambda: None)


def test_quota_resume_reuses_reader_and_retains_unknown_usage(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    FakeRunner.failure_at = 2  # first reader complete, first judge quota-rejected
    result = execute(tmp_path, plan, ctx, golds)
    assert result['status'] == 'quota' and result['unknown_usage_attempts'] == 1
    with pytest.raises(ValueError, match='Explicit --resume'):
        execute(tmp_path, plan, ctx, golds)
    result = execute(tmp_path, plan, ctx, golds, resume=True)
    assert result['status'] == 'complete' and result['cli_attempts'] == 9 and result['accepted_calls'] == 8
    assert result['known_total_tokens'] == 880
    assert sum(field == 'answer' for _, field in FakeRunner.calls) == 4
    for payload, field in FakeRunner.calls:
        if field == 'answer':
            assert set(payload) == {'question', 'observations'}
            assert 'SECRET' not in repr(payload)


def test_manual_batches_are_one_frozen_budget_not_new_campaigns(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    result = execute(tmp_path, plan, ctx, golds, batch=3)
    assert result['status'] == 'batch'
    assert execute(tmp_path, plan, ctx, golds, resume=True)['accepted_calls'] == 8
    with pytest.raises(ValueError):
        execute(tmp_path, plan, ctx, golds, resume=True)


def test_unknown_failure_blocks_replay(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    FakeRunner.failure_at, FakeRunner.mode = 1, 'unknown'
    with pytest.raises(RuntimeError):
        execute(tmp_path, plan, ctx, golds)
    with pytest.raises(ValueError, match='Uncertain/blocked'):
        execute(tmp_path, plan, ctx, golds, resume=True)
    assert len(FakeRunner.calls) == 1


def test_uncertain_reservation_and_concurrent_lock_never_replay(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    (tmp_path / 'answer.lock').write_text('uncertain process')
    with pytest.raises(ValueError, match='lock'):
        execute(tmp_path, plan, ctx, golds)
    assert not FakeRunner.calls
    (tmp_path / 'answer.lock').unlink()
    from bench.e2e.data import sha256
    ledger = Ledger(tmp_path, sha256(tmp_path / 'plan.json'))
    ledger.begin('0/memory/answer')
    ledger.close()
    with pytest.raises(ValueError, match='Uncertain/blocked'):
        execute(tmp_path, plan, ctx, golds)
    assert not FakeRunner.calls


def test_changed_receipt_blocks_resume(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    execute(tmp_path, plan, ctx, golds, batch=1)
    (tmp_path / 'calls/00001/000.events.jsonl').write_text('tampered')
    with pytest.raises(ValueError, match='artifacts changed'):
        execute(tmp_path, plan, ctx, golds, resume=True)
    assert len(FakeRunner.calls) == 1


def test_global_attempt_cap_does_not_reset_after_quota(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path)
    plan['max_attempts'] = 2
    FakeRunner.failure_at = 2
    execute(tmp_path, plan, ctx, golds)
    assert execute(tmp_path, plan, ctx, golds, resume=True)['status'] == 'budget'
    assert len(FakeRunner.calls) == 2


def test_complete_paired_report_and_refusal_of_partial_denominator(tmp_path):
    plan, ctx, golds = setup_campaign(tmp_path, count=30)
    execute(tmp_path, plan, ctx, golds, batch=40)
    with pytest.raises(ValueError, match='complete frozen paired'):
        final_report(tmp_path, plan)
    while True:
        result = execute(tmp_path, plan, ctx, golds, resume=True)
        if result['status'] == 'complete':
            break
    report = final_report(tmp_path, plan)
    assert report['criterion_met'] and report['questions'] == 30
    assert report['paired_delta_accuracy']['ci95'] == [1.0, 1.0]
    assert report['accounting']['cli_attempts'] == 120 and report['accounting']['api_usd'] == 0
    assert len(list(tasks(plan))) == 120


def test_unknown_accounting_is_not_invented_as_zero():
    result = accounting([(1, 'task', 'quota', 'null', 'null', 1, '{}')])
    assert result['unknown_usage_attempts'] == 1 and result['known_total_tokens'] == 0


@pytest.mark.parametrize('event', [
    {'type': 'error', 'message': "You've hit your usage limit", 'tool_calls': []},
    {'type': 'turn.failed', 'error': {'message': "You've hit your usage limit", 'code': 'connection_lost'}},
    {'type': 'turn.failed', 'error': {'message': "You've hit your usage limit", 'tool': 'shell'}},
    ['unexpected'],
])
def test_quota_allowlist_rejects_unknown_fields_and_nonobjects(event):
    assert not quota_rejection(json.dumps(event))


def test_all_prompts_preflight_without_gold_in_reader_or_silent_exclusion(tmp_path):
    from bench.e2e.answer_campaign import validate_inputs
    plan, ctx, golds = setup_campaign(tmp_path)
    validate_inputs(plan, ctx, golds)
    golds['1'].gold.answers = ['X' * 24001]
    with pytest.raises(ValueError, match='no question exclusion or model calls'):
        validate_inputs(plan, ctx, golds)
    assert not FakeRunner.calls
