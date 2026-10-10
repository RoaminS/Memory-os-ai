"""BEAM schema/annotation firewall, clustered inference and no-tool CLI checks."""
import json
from pathlib import Path
import subprocess

import pytest

from bench.e2e.beam import CATEGORIES, conversations, decode
from bench.e2e.beam_campaign import select_pilot, summarize
from bench.e2e.codex_reader import CLI, CodexReader, PilotStop, capability, command, parse_events
from bench.e2e.configuration import resolved_config
from bench.e2e.data import sha256
from bench.e2e.run import verify_source


def row(cid='one'):
    probes = {}
    for cat in CATEGORIES:
        answer_key = {'abstention': 'ideal_response', 'contradiction_resolution': 'ideal_answer',
                      'instruction_following': 'expected_compliance', 'preference_following': 'expected_compliance',
                      'summarization': 'ideal_summary'}.get(cat, 'answer')
        probes[cat] = [{'question': f'Question {cat}?', answer_key: 'SCORER_SECRET',
                        'rubric': ['SCORER_RUBRIC_SECRET'], **({} if cat == 'abstention' else {'source_chat_ids': [0]})}]
    return {'conversation_id': cid, 'conversation_seed': 'SECRET', 'narratives': 'SECRET',
            'user_profile': 'SECRET', 'conversation_plan': 'SECRET', 'user_questions': 'SECRET',
            'chat': [[{'content': 'A recorded preference.', 'role': 'user', 'id': 0,
                       'index': 'SECRET', 'question_type': 'SECRET', 'time_anchor': 'March-15-2024'}]],
            'probing_questions': repr(probes)}


def write(tmp_path, rows):
    path = tmp_path / 'beam.jsonl'
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return path


def test_loader_firewall(tmp_path):
    c = list(conversations(write(tmp_path, [row()])))[0]
    assert len(c.probes) == 10
    assert 'SECRET' not in repr(c.turns)
    assert '[time_anchor: March-15-2024]' in c.turns[0].content
    assert c.probes[1].gold.evidence == (c.turns[0].id,)
    assert c.probes[0].gold.evidence == ()


@pytest.mark.parametrize('payload', ['__import__("os").system("bad")', '{"x":1,"x":2}',
                                    "{'x':1,'x':2}", "{'x': (1,2)}", '{"x":NaN}', "{'x': -1}"])
def test_literal_fail_closed(payload):
    with pytest.raises(ValueError):
        decode(payload, literal=True)


def test_literal_depth_budget():
    with pytest.raises(ValueError):
        decode('[' * 40 + '1' + ']' * 40, literal=True)


@pytest.mark.parametrize('mutation', ['duplicate_conversation', 'duplicate_turn', 'dangling', 'missing', 'unknown_category', 'empty_content'])
def test_schema_fail_closed(tmp_path, mutation):
    r = row()
    rows = [r]
    if mutation == 'duplicate_conversation':
        rows.append(r)
    elif mutation == 'duplicate_turn':
        r['chat'][0].append(dict(r['chat'][0][0]))
    elif mutation == 'empty_content':
        r['chat'][0][0]['content'] = ''
    else:
        p = decode(r['probing_questions'], literal=True)
        if mutation == 'dangling':
            p['event_ordering'][0]['source_chat_ids'] = [999]
        elif mutation == 'missing':
            del p['event_ordering'][0]['source_chat_ids']
        else:
            p['invented_category'] = p.pop('summarization')
        r['probing_questions'] = repr(p)
    with pytest.raises(ValueError):
        list(conversations(write(tmp_path, rows)))


def test_explicit_empty_not_fabricated(tmp_path):
    r = row()
    p = decode(r['probing_questions'], literal=True)
    p['summarization'][0]['source_chat_ids'] = []
    r['probing_questions'] = repr(p)
    c = list(conversations(write(tmp_path, [r])))[0]
    assert not c.probes[8].gold.evidence
    assert c.probes[8].gold.category == 'summarization'


def test_pilot_whole_conversations_deterministic(tmp_path):
    cs = list(conversations(write(tmp_path, [row(str(i)) for i in range(20)])))
    convs, ids = select_pilot(cs)
    assert len(convs) == 2 and len(ids) == 20
    assert select_pilot(list(reversed(cs))) == (convs, ids)
    assert all(p.id not in ids for c in cs if c.id not in convs for p in c.probes)


def metrics(recall, mrr):
    return {'recall': recall, 'mrr': mrr}


def test_conversation_bootstrap_not_question_bootstrap():
    rows = [{'qid': f'{i}-{j}', 'conversation': str(i), 'memory': metrics(.8, .6), 'bm25': metrics(.6, .5)}
            for i in range(20) for j in range(2)]
    report = summarize(rows, [r['qid'] for r in rows])
    assert report['clusters'] == 20 and report['numerical_criterion_met']
    assert report['paired']['recall']['ci95'] == pytest.approx([.2, .2])
    for r in rows:
        r['conversation'] = 'same'
    report = summarize(rows, [r['qid'] for r in rows])
    assert report['paired']['recall']['ci95'] is None and not report['numerical_criterion_met']


def test_missing_question_blocks():
    with pytest.raises(ValueError):
        summarize([], ['missing'])


def test_profile_preserves_historical_and_pins_c4():
    historical = resolved_config('hash', '/tmp/test')
    c4 = resolved_config('hash', '/tmp/test', profile='beam-c4-4.0.2')
    assert (historical['bm25_profile'], historical['lexical_guard_k'], historical['embed_max_seq_length']) == ('legacy', 0, 256)
    assert (c4['bm25_profile'], c4['lexical_guard_k'], c4['embed_max_seq_length']) == ('scoped_literal', 3, 512)
    assert not c4['use_recency'] and c4['embed_batch_size'] == 4


def test_cli_exact_model_effort_and_isolation():
    argv = command(Path('/private/tmp/fresh'), Path('/private/tmp/fresh/schema.json'), 'minimal')
    assert argv[:7] == [CLI, 'exec', '-m', 'gpt-5.6-luna', '-c', 'model_reasoning_effort=minimal', '--ignore-user-config']
    for flag in ('--ephemeral', '--strict-config', '--ignore-user-config'):
        assert flag in argv
    assert 'mcp_servers={}' in argv and 'web_search="disabled"' in argv
    assert 'forced_login_method="chatgpt"' in argv and 'history.persistence="none"' in argv
    assert 'shell_tool' in argv and 'plugins' in argv and 'hooks' in argv


def test_unsupported_effort_zero_calls(tmp_path, monkeypatch):
    cache = tmp_path / 'catalogue.json'
    cache.write_text(json.dumps({'models': [{'slug': 'gpt-5.6-luna', 'supported_reasoning_levels': [{'effort': 'low'}]}]}))
    assert not capability('minimal', cache)['supported']
    assert capability('low', cache)['supported']
    monkeypatch.setattr('bench.e2e.codex_reader.capability', lambda effort: {'supported': False})
    reader = CodexReader(tmp_path / 'calls', 'minimal', lambda: None)
    with pytest.raises(PilotStop):
        reader.answer('question', [])
    assert reader.calls == []


def events(value):
    return '\n'.join(json.dumps(e) for e in [
        {'type': 'thread.started'}, {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(value)}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2, 'cached_input_tokens': 4}}])


def test_events_usage_and_output():
    answer, usage = parse_events(events({'answer': 'hello'}), 'answer')
    assert answer == 'hello' and usage['input_tokens'] == 10
    assert parse_events(events({'correct': False}), 'correct')[0] is False


@pytest.mark.parametrize('item', ['command_execution', 'mcp_tool_call', 'web_search', 'file_change', 'future_new_tool'])
def test_tool_events_block(item):
    with pytest.raises(PilotStop):
        parse_events(json.dumps({'type': 'item.started', 'item': {'type': item}}), 'answer')


@pytest.mark.parametrize('value', [{'correct': 'yes'}, {'correct': 1}, {'correct': True, 'extra': 1}])
def test_judge_strict_bool(value):
    with pytest.raises(PilotStop):
        parse_events(events(value), 'correct')


def test_verify_source_from_untracked_private_worker_directory(tmp_path, monkeypatch):
    clone = tmp_path / 'clone'
    metadata = clone / '.local-qualification' / 'git-state'
    source = clone / 'v4' / 'src'
    evaluator = clone / 'v4' / 'bench' / 'e2e'
    source.mkdir(parents=True)
    evaluator.mkdir(parents=True)
    (source / 'core.py').write_text('VALUE = 1\n')
    (evaluator / 'worker.py').write_text('VALUE = 2\n')
    metadata.parent.mkdir(parents=True)
    subprocess.run(['git', 'init', '--separate-git-dir', str(metadata), str(clone)],
                   check=True, capture_output=True)
    subprocess.run(['git', '-C', str(clone), 'add', 'v4/src', 'v4/bench/e2e'], check=True)
    subprocess.run(['git', '-C', str(clone), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture'],
                   check=True, capture_output=True)
    revision = subprocess.run(['git', '-C', str(clone), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    code = {'src/core.py': sha256(source / 'core.py'),
            'bench/e2e/worker.py': sha256(evaluator / 'worker.py')}
    untracked = clone / '.local-qualification' / 'plan' / 'pilot-retrieval'
    untracked.mkdir(parents=True)
    monkeypatch.chdir(untracked)
    verify_source(clone, revision, code)
