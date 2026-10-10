"""No inference: exact startup warnings, rejected-call accounting and one recovery."""
import json

import pytest

from bench.e2e.codex_reader import (CodexReader, PilotStop, STARTUP_WARNINGS,
                                    command, parse_events, recover_usage)

USAGE = {'input_tokens': 9165, 'output_tokens': 21, 'cached_input_tokens': 0}


def stream(warnings=(), value=None):
    return [
        {'type': 'thread.started'},
        *[{'type': 'item.completed', 'item': {'id': str(i), 'type': 'error', 'message': message}}
          for i, message in enumerate(warnings)],
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(value or {'answer': 'INSUFFICIENT_EVIDENCE'})}},
        {'type': 'turn.completed', 'usage': USAGE},
    ]


def lines(events):
    return '\n'.join(map(json.dumps, events))


def test_exact_real_startup_warnings_and_stopped_usage():
    answer, usage = parse_events(lines(stream(sorted(STARTUP_WARNINGS))), 'answer')
    assert answer == 'INSUFFICIENT_EVIDENCE' and usage == USAGE
    assert recover_usage(lines(stream(sorted(STARTUP_WARNINGS)))) == USAGE


@pytest.mark.parametrize('variant', ['late', 'changed_text', 'duplicate', 'extra_key', 'top_error', 'started_item'])
def test_warning_allowlist_has_no_generic_exception(variant):
    message = sorted(STARTUP_WARNINGS)[0]
    events = stream([message])
    if variant == 'late':
        events[1], events[2] = events[2], events[1]
    elif variant == 'changed_text':
        events[1]['item']['message'] += ' '
    elif variant == 'duplicate':
        events.insert(2, events[1])
    elif variant == 'extra_key':
        events[1]['item']['command'] = 'hidden tool'
    elif variant == 'top_error':
        events[1] = {'type': 'error', 'message': message}
    else:
        events[1]['type'] = 'item.started'
    with pytest.raises(PilotStop):
        parse_events(lines(events), 'answer')
    assert recover_usage(lines(events)) == USAGE  # cost, never acceptance


@pytest.mark.parametrize('kind', ['command_execution', 'mcp_tool_call', 'web_search', 'file_change', 'future_new_tool'])
def test_tool_is_forbidden_even_with_allowlisted_message(kind):
    events = stream(sorted(STARTUP_WARNINGS))
    events.insert(-1, {'type': 'item.completed', 'item': {'id': 'tool', 'type': kind,
                              'message': sorted(STARTUP_WARNINGS)[0]}})
    with pytest.raises(PilotStop, match='Forbidden tool'):
        parse_events(lines(events), 'answer')
    assert recover_usage(lines(events)) == USAGE


def test_duplicate_turn_usage_stays_unknown_and_blocked():
    events = stream()
    events.append(events[-1])
    assert recover_usage(lines(events)) is None
    with pytest.raises(PilotStop):
        parse_events(lines(events), 'answer')


def test_command_suppresses_known_warning_and_disables_code_mode(tmp_path):
    argv = command(tmp_path, tmp_path / 'schema', 'low')
    assert 'suppress_unstable_features_warning=true' in argv
    assert 'features.code_mode.enabled=false' in argv
    assert 'code_mode' not in argv  # no conflicting legacy boolean override
    for feature in ('skip_host_skill_discovery', 'shell_tool', 'code_mode_host'):
        assert argv[argv.index(feature) - 1] == ('--enable' if feature == 'skip_host_skill_discovery' else '--disable')


@pytest.mark.parametrize('failure', ['tool', 'exit', 'guard', 'schema'])
def test_rejected_attempt_still_counts_known_usage(tmp_path, monkeypatch, failure):
    import bench.e2e.codex_reader as reader
    monkeypatch.setattr(reader, 'capability', lambda effort: {'supported': True})
    events = stream()
    if failure == 'tool':
        events.insert(-1, {'type': 'item.completed', 'item': {'type': 'mcp_tool_call'}})
    if failure == 'schema':
        events[-2]['item']['text'] = '{"wrong":true}'
    class Child:
        returncode = 1 if failure == 'exit' else 0
        def poll(self):
            return None if failure == 'guard' else self.returncode
    def popen(argv, **kwargs):
        kwargs['stdout'].write(lines(events))
        kwargs['stdout'].flush()
        return Child()
    monkeypatch.setattr(reader.subprocess, 'Popen', popen)
    monkeypatch.setattr(reader, 'kill_group', lambda child: None)
    checks = [0]
    def check():
        checks[0] += 1
        if failure == 'guard' and checks[0] > 1:
            raise PilotStop('synthetic guard')
    runner = CodexReader(tmp_path / 'calls', 'low', check)
    with pytest.raises(PilotStop):
        runner.answer('question', ['evidence'])
    assert runner.attempts == 1 and runner.tokens == 9186 and runner.calls == []
    usage = json.loads((runner.root / 'usage.json').read_text())
    assert usage['pending_usage_unknown'] is False
    assert usage['attempt_records'][0]['accepted'] is False
    assert usage['attempt_records'][0]['usage'] == USAGE
    assert (runner.root / '000.prompt.txt').exists()


def test_historical_seed_spends_budget_and_cannot_be_seeded_twice(tmp_path, monkeypatch):
    runner = CodexReader(tmp_path / 'calls', 'low', lambda: None, max_calls=1)
    runner.seed({'index': 0, 'kind': 'answer', 'usage': USAGE, 'seconds': 90})
    with pytest.raises(PilotStop, match='only once'):
        runner.seed({'kind': 'answer', 'usage': USAGE})
    with pytest.raises(PilotStop, match='budget'):
        runner.answer('no second call', [])
    assert runner.tokens == 9186 and runner.attempts == 1


def test_previous_evidence_requires_unchanged_engine_and_single_attempt(tmp_path, monkeypatch):
    import bench.e2e.answer_pilot as pilot
    from bench.e2e.data import sha256
    d = tmp_path / 'old'
    (d / 'retrieval').mkdir(parents=True)
    (d / 'answers/calls').mkdir(parents=True)
    original = {'version': 'answer-pilot-production-v1', 'source_commit': 'old',
                'code_files': {'src/engine.py': 'same', 'bench/e2e/codex_reader.py': 'old'},
                'question_ids': [str(i) for i in range(10)], 'arms': ['memory', 'bm25'],
                'reader': {'model': 'gpt-5.6-luna', 'effort': 'low'}, 'prompts': {'reader': 'frozen prompt'}}
    (d / 'plan.json').write_text(json.dumps(original))
    (d / 'receipt.json').write_text(json.dumps({'plan_sha256': sha256(d / 'plan.json')}))
    contexts = [{'qid': str(i), 'question': f'Q{i}', 'contexts': {'memory': ['observable'], 'bm25': []}} for i in range(10)]
    (d / 'retrieval/contexts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in contexts))
    (d / 'retrieval/encoder-attestation.json').write_text('{}')
    (d / 'retrieval/report.json').write_text(json.dumps({'complete': True, 'model_calls': 0,
         'plan_sha256': sha256(d / 'plan.json'), 'contexts_sha256': sha256(d / 'retrieval/contexts.jsonl'),
         'encoder_sha256': sha256(d / 'retrieval/encoder-attestation.json')}))
    stopped = {'reason': 'Forbidden tool/event: isolation proof failed', 'restart': False,
               'cli_attempts': 1, 'complete_calls': 0}
    (d / 'answers/stopped.json').write_text(json.dumps(stopped))
    (d / 'answers/predictions.jsonl').write_text('')
    (d / 'answers/calls/000.stderr').write_text('')
    (d / 'answers/calls/000.events.jsonl').write_text(lines(stream(sorted(STARTUP_WARNINGS))))
    (d / 'answers/calls/usage.json').write_text(json.dumps({'cli_attempts': 1, 'completed_calls': []}))
    monkeypatch.setattr(pilot, 'private_path', lambda path: (tmp_path, path))
    monkeypatch.setattr(pilot, 'verify_source', lambda *a: None)
    code = {'src/engine.py': 'same', 'bench/e2e/codex_reader.py': 'new'}
    monkeypatch.setattr(pilot, 'code_record', lambda: code)
    before = {str(p): p.read_bytes() for p in d.rglob('*') if p.is_file()}
    _, evidence = pilot.previous_evidence(tmp_path, d)
    assert evidence['call']['usage'] == USAGE and evidence['new_calls_max'] == 39
    assert evidence['answer'] == 'INSUFFICIENT_EVIDENCE' and evidence['qid'] == '0'
    assert before == {str(p): p.read_bytes() for p in d.rglob('*') if p.is_file()}
    code['src/engine.py'] = 'changed'
    with pytest.raises(ValueError, match='engine/retrieval unchanged'):
        pilot.previous_evidence(tmp_path, d)
    code['src/engine.py'] = 'same'
    stopped['cli_attempts'] = 2
    (d / 'answers/stopped.json').write_text(json.dumps(stopped))
    with pytest.raises(ValueError, match='exactly one stopped'):
        pilot.previous_evidence(tmp_path, d)


def test_prepare_amendment_freezes_reuse_and_blocks_second_budget(tmp_path, monkeypatch):
    import bench.e2e.answer_pilot as pilot
    from bench.e2e.data import sha256
    monkeypatch.setattr(pilot, 'private_path', lambda p: (tmp_path, p))
    monkeypatch.setattr(pilot, 'verify_source', lambda *a: None)
    code = {'src/engine.py': 'same', 'bench/e2e/codex_reader.py': 'old'}
    monkeypatch.setattr(pilot, 'code_record', lambda: dict(code))
    data = tmp_path / 'data'
    data.write_text('dummy observed data')
    monkeypatch.setattr(pilot, 'DATA', data)
    monkeypatch.setattr(pilot, 'DATA_SHA', sha256(data))
    monkeypatch.setattr(pilot, 'capability', lambda *a, **kw: {'supported': True})
    monkeypatch.setattr(pilot, 'conversations', lambda *a: [])
    monkeypatch.setattr(pilot, 'select', lambda *a: (['c0', 'c1'], [str(i) for i in range(10)]))
    monkeypatch.setattr(pilot, 'model_record', lambda *a: {'id': 'frozen-revision'})
    monkeypatch.setattr(pilot, 'local_runtime', lambda: {'torch': 'frozen'})
    monkeypatch.setattr(pilot, 'runtime_record', lambda: {'sha256': 'same-cli'})
    monkeypatch.setattr(pilot, 'resolved_config', lambda *a, **kw: {'device': 'mps'})
    monkeypatch.setattr(pilot, 'validate_encoder', lambda *a: None)
    copy = tmp_path / 'copy-proof.json'
    copy.write_text(json.dumps({'ok': True, 'version': '4.0.3', 'source_commit': 'old',
                               'production_modified': False, 'encoding_profile': {}}))
    monkeypatch.setattr(pilot, 'VERSION', 'answer-pilot-production-v1')
    old = tmp_path / 'old'
    pilot.prepare(old, 'old', copy)
    (old / 'retrieval').mkdir()
    (old / 'answers/calls').mkdir(parents=True)
    rows = [{'qid': str(i), 'question': f'Q{i}', 'contexts': {'memory': ['observation'], 'bm25': []}} for i in range(10)]
    (old / 'retrieval/contexts.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    (old / 'retrieval/encoder-attestation.json').write_text('{}')
    (old / 'retrieval/report.json').write_text(json.dumps({'complete': True, 'model_calls': 0,
        'plan_sha256': sha256(old / 'plan.json'), 'contexts_sha256': sha256(old / 'retrieval/contexts.jsonl'),
        'encoder_sha256': sha256(old / 'retrieval/encoder-attestation.json')}))
    (old / 'answers/stopped.json').write_text(json.dumps({'reason': 'Forbidden tool/event: isolation proof failed',
            'restart': False, 'cli_attempts': 1, 'complete_calls': 0}))
    (old / 'answers/predictions.jsonl').write_text('')
    (old / 'answers/calls/usage.json').write_text(json.dumps({'cli_attempts': 1, 'completed_calls': []}))
    (old / 'answers/calls/000.events.jsonl').write_text(lines(stream(sorted(STARTUP_WARNINGS))))
    (old / 'answers/calls/000.stderr').write_text('')
    before = {str(p): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    monkeypatch.setattr(pilot, 'VERSION', 'answer-pilot-production-v2')
    code['bench/e2e/codex_reader.py'] = 'new'
    (tmp_path / '.local-qualification').mkdir()
    new = tmp_path / 'new'
    result = pilot.prepare(new, 'new', copy, old)
    assert result['caps']['cli_calls'] == 40 and result['historical']['new_calls_max'] == 39
    assert before == {str(p): p.read_bytes() for p in old.rglob('*') if p.is_file()}
    assert (new / 'retrieval/contexts.jsonl').read_bytes() == (old / 'retrieval/contexts.jsonl').read_bytes()
    assert json.loads((new / 'receipt.json').read_text())['prepared_before_inference'] is False
    assert pilot.frozen(new) == result
    with pytest.raises(ValueError, match='already amended'):
        pilot.prepare(tmp_path / 'another', 'new', copy, old)
    assert not (tmp_path / 'another').exists()
    (old / 'answers/calls/000.stderr').write_text('tampered')
    with pytest.raises(ValueError, match='historical evidence changed'):
        pilot.frozen(new)
