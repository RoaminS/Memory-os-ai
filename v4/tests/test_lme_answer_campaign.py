"""Synthetic protocol, isolation, official grading and durable batch checks.

No downloaded dataset, neural encoding, CLI inference or API call.
"""
import io
import importlib.util
import inspect
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from bench.e2e import lme_answer_campaign as campaign
from bench.e2e.answer_campaign import Ledger, execute_batch
from bench.e2e.codex_reader import MODEL, PilotStop, command, parse_events, recover_usage
from bench.e2e.configuration import resolved_config
from bench.e2e.data import Gold, Inputs
from bench.e2e.lme_grading import TYPES, get_anscheck_prompt, judge_payload, official_prompt, source_templates


def events(text, tool=False):
    return '\n'.join(json.dumps(e) for e in [
        {'type': 'thread.started'}, {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'type': 'command_execution' if tool else 'agent_message', 'text': text}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 1, 'cached_input_tokens': 0}},
    ])


@pytest.mark.parametrize('task', sorted(TYPES))
def test_official_templates_include_exact_fields_and_yes_no(task):
    prompt = get_anscheck_prompt(task, 'QUESTION', 'REFERENCE', 'CANDIDATE')
    assert 'Question: QUESTION' in prompt and 'Model Response: CANDIDATE' in prompt
    assert ('Rubric: REFERENCE' if task == 'single-session-preference' else 'Correct Answer: REFERENCE') in prompt
    assert prompt.endswith('Answer yes or no only.')
    if task == 'temporal-reasoning':
        assert 'predicting 19 days when the answer is 18' in prompt
    if task == 'knowledge-update':
        assert 'some previous information along with an updated answer' in prompt
    if task == 'single-session-preference':
        assert 'does not need to reflect all the points' in prompt


def test_abstention_uses_official_override_for_any_type():
    prompt = get_anscheck_prompt('temporal-reasoning', 'Q', 'EXPLANATION', 'INSUFFICIENT_EVIDENCE', True)
    assert 'Explanation: EXPLANATION' in prompt and 'unanswerable' in prompt
    assert 'off-by-one' not in prompt


@pytest.mark.parametrize('text,expected', [('yes', True), (' NO\n', False), ('YES', True)])
def test_raw_official_verdict_keeps_usage_and_isolation(text, expected):
    value, usage = parse_events(events(text), 'correct', raw_yes_no=True)
    assert value is expected and usage['input_tokens'] == 10


@pytest.mark.parametrize('text', ['yesterday', 'yes, because', '{"correct":true}', '', 'maybe', 'no yes'])
def test_noncanonical_grader_stops_without_silently_scoring_no(text):
    with pytest.raises(PilotStop, match='yes or no'):
        parse_events(events(text), 'correct', raw_yes_no=True)


def test_raw_grader_does_not_allow_tools_and_stopped_usage_recoverable():
    raw = events('yes', tool=True)
    with pytest.raises(PilotStop, match='Forbidden tool'):
        parse_events(raw, 'correct', raw_yes_no=True)
    assert recover_usage(raw)['output_tokens'] == 1


def test_luna_high_command_is_raw_no_schema_still_chatgpt_isolated(tmp_path):
    args = command(tmp_path, None, 'high', MODEL)
    assert '--output-schema' not in args and 'model_reasoning_effort=high' in args
    assert 'forced_login_method="chatgpt"' in args and 'mcp_servers={}' in args
    assert '--ignore-rules' in args and 'web_search="disabled"' in args


def tar_bytes(name='v4/src/memory_os_v4/config.py', content=b'config'):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo(name)
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return stream.getvalue()


def test_engine_extraction_exact_tamper_and_no_overwrite(tmp_path):
    root = tmp_path / 'engine'
    manifest = campaign.extract_engine(tar_bytes(), root)
    assert campaign.engine_manifest(root) == manifest
    (root / 'memory_os_v4/config.py').write_text('drift')
    assert campaign.engine_manifest(root) != manifest
    with pytest.raises(FileExistsError):
        campaign.extract_engine(tar_bytes(), root)


@pytest.mark.parametrize('name', ['v4/src/../escape', '/tmp/escape', 'v4/src/../../escape'])
def test_engine_archive_traversal_refused(tmp_path, name):
    with pytest.raises(ValueError, match='Unsafe'):
        campaign.extract_engine(tar_bytes(name), tmp_path / 'engine')


def test_engine_archive_symlink_refused(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo('v4/src/memory_os_v4/config.py')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc/passwd'
        archive.addfile(member)
    with pytest.raises(ValueError, match='Unsafe'):
        campaign.extract_engine(stream.getvalue(), tmp_path / 'engine')


def test_source_archive_from_untracked_cwd_is_clone_anchored(monkeypatch, tmp_path):
    clone = tmp_path / 'clone'
    child = clone / '.local-qualification/output'
    child.mkdir(parents=True)
    monkeypatch.chdir(child)
    seen = []
    def run(argv, **kwargs):
        seen.append(kwargs['cwd'])
        return SimpleNamespace(returncode=0, stdout=tar_bytes())
    monkeypatch.setattr(campaign.subprocess, 'run', run)
    assert campaign.source_archive(clone, campaign.ENGINE_COMMIT) == tar_bytes()
    assert seen == [clone]


def test_metadata_500_includes_abstention_links_not_shared_distractors(monkeypatch, tmp_path):
    raws, pairs = [], []
    for i in range(500):
        qid = '0_abs' if i == 499 else str(i)
        abs_task = i == 499
        raws.append({'question_id': qid, 'answer': 'REFERENCE'})
        pairs.append((Inputs(str(i), (), 'Q', '2020-01-01'), Gold(qid, () if abs_task else ('turn',),
                     ('REFERENCE',), 'multi-session', ('COMMON-DISTRACTOR',), 0,
                     answer_sessions=() if abs_task else (f'proof-{i}',))))
    monkeypatch.setattr(campaign, 'array_entries', lambda *a, **kw: iter(raws))
    monkeypatch.setattr(campaign, 'cases', lambda *a, **kw: iter(pairs))
    rows = campaign.metadata(tmp_path / 'unused', lambda: None)
    assert len(rows) == 500 and sum(r['abstention'] for r in rows) == 1
    assert len({r['cluster'] for r in rows}) == 499
    assert rows[0]['cluster'] == rows[-1]['cluster']
    pairs[2] = (pairs[2][0], Gold('2', ('turn',), ('REFERENCE',), 'multi-session', (), 0))
    with pytest.raises(ValueError, match='evidence-session'):
        campaign.metadata(tmp_path / 'unused', lambda: None)


def test_production_profile_confines_mutations_preserving_historical_profile():
    cfg = resolved_config('hash', '<private>', profile=campaign.PROFILE)
    assert (cfg['device'], cfg['embed_dtype'], cfg['embed_batch_size']) == ('auto', 'native', 32)
    assert cfg['dedup_cos'] == cfg['supersede_cos'] == 1.01
    assert cfg['bm25_profile'] == 'scoped_literal' and cfg['lexical_guard_k'] == 3
    old = resolved_config('hash', '<private>', profile='historical')
    assert old['device'] == 'cpu' and old['lexical_guard_k'] == 0


def test_reader_payload_question_date_only_and_official_grader_uses_gold():
    p = SimpleNamespace(question='Q', date='DATE', category='temporal-reasoning', reference='SECRET', abstention=False)
    ctx = {'contexts': {'memory': ['OBSERVATION']}}
    reader = campaign.payload(p, ctx, 'memory', 'answer', None)
    assert set(reader) == {'question', 'question_date', 'observations'}
    assert 'SECRET' not in repr(reader) and 'temporal-reasoning' not in repr(reader)
    judge = campaign.payload(p, ctx, 'memory', 'correct', '18 days')
    assert 'SECRET' in judge_payload(judge) and 'memory' not in judge
    with pytest.raises(ValueError, match='exceeds'):
        campaign.payload(p, ctx, 'memory', 'correct', 'x' * 6001)


class FakeLuna:
    models = []
    def __init__(self, root, effort, check, **kwargs):
        self.models.append((effort, kwargs['judge_model'], kwargs['judge_effort']))
        self.root = root
        root.mkdir()
        self.records = []
    def invoke(self, payload, kind):
        if kind == 'answer':
            assert 'reference' not in payload and 'question_type' not in payload
            value = 'RESPONSE'
        else:
            assert 'reference' in payload and 'abstention' in payload
            value = True
        r = {'accepted': True, 'usage': {'input_tokens': 10, 'output_tokens': 1, 'cached_input_tokens': 0}, 'seconds': 1}
        self.records.append(r)
        (self.root / 'usage.json').write_text(json.dumps({'attempt_records': self.records}))
        return value


def test_official_luna_batch_manual_resume_uses_same_durable_ledger(tmp_path):
    plan = {'question_ids': ['q'], 'arms': ['memory', 'bm25'], 'max_attempts': 10,
            'judge': {'model': MODEL, 'effort': 'high'},
            'caps': {'cli_seconds': 100, 'batch_seconds': 100, 'observed_tokens_stop_threshold': 10000}}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    probe = SimpleNamespace(question='Q', date='D', reference='SECRET', category='multi-session', abstention=False,
                            gold=SimpleNamespace(answers=['SECRET']), rubric=())
    ctx = {'q': {'question': 'Q', 'contexts': {'memory': ['M'], 'bm25': ['B']}}}
    result = execute_batch(tmp_path, plan, ctx, {'q': probe}, 2, False, runner_factory=FakeLuna,
                           check=lambda: None, payload_builder=campaign.payload)
    assert result['status'] == 'batch' and result['accepted_calls'] == 2
    result = execute_batch(tmp_path, plan, ctx, {'q': probe}, 2, True, runner_factory=FakeLuna,
                           check=lambda: None, payload_builder=campaign.payload)
    assert result['status'] == 'complete' and result['accepted_calls'] == 4
    assert all(m == ('low', MODEL, 'high') for m in FakeLuna.models)


def test_primary_report_counts_abstentions_and_missing_subgroup_ci(tmp_path):
    plan = {'question_ids': [str(i) for i in range(31)], 'arms': ['memory', 'bm25'], 'tasks': 124,
            'reader': {'model': MODEL, 'effort': 'low'}, 'judge': {'model': MODEL, 'effort': 'high'},
            'selected': [{'qid': str(i), 'cluster': str(i), 'question_type': 'multi-session', 'abstention': i == 0} for i in range(31)],
            'claim': 'DESCRIPTIVE'}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    ledger = Ledger(tmp_path, campaign.sha256(tmp_path / 'plan.json'))
    for task, qid, arm, kind in campaign.tasks(plan):
        index = ledger.begin(task)
        root = tmp_path / str(index)
        root.mkdir()
        (root / 'proof').write_text('synthetic')
        ledger.finish(index, 'complete', ('RESPONSE' if kind == 'answer' else arm == 'memory'),
                      {'input_tokens': 10, 'output_tokens': 1, 'cached_input_tokens': 0}, 1, root)
    ledger.status('complete')
    ledger.close()
    result = campaign.report(tmp_path, plan)
    assert result['n'] == 31 and result['abstention_subset']['n'] == 1
    assert result['abstention_subset']['paired_delta_accuracy']['ci95'] is None
    assert result['descriptive_numerical_criterion_met'] is True
    assert result['leaderboard_claim_allowed'] is False and result['independent_confirmation'] is False
    assert result['accounting']['accepted_calls'] == 124


def test_official_judge_factory_forbids_astra_substitution(tmp_path):
    with pytest.raises(ValueError, match='Luna high'):
        campaign.OfficialLuna(tmp_path / 'calls', 'low', lambda: None, judge_model='gpt-6-astra', judge_effort='high')


def test_official_source_ast_preserves_whitespace_never_executes_api(tmp_path):
    path = tmp_path / 'evaluate_qa.py'
    source = inspect.getsource(get_anscheck_prompt)
    source = source.replace('In addition, do not', 'In addition,  do not').replace('days. If', 'days.If')
    path.write_text('raise RuntimeError("MUST NEVER EXECUTE")\n' + source)
    templates = source_templates(path)
    actual = official_prompt(templates, 'temporal-reasoning', 'Q', 'A', 'R', False)
    assert 'In addition,  do not' in actual
    assert 'days.If' in actual
    assert official_prompt(templates, 'multi-session', 'Q', 'A', 'R', True) == get_anscheck_prompt('multi-session', 'Q', 'A', 'R', True)
    path.write_text(source.replace('off-by-one', 'off-by-two'))
    with pytest.raises(ValueError, match='grader changed'):
        source_templates(path)


def load_acquirer():
    path = Path(__file__).resolve().parents[2] / 'scripts/acquire_lme_grader.py'
    spec = importlib.util.spec_from_file_location('test_lme_grader_acquirer', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response(io.BytesIO):
    def __init__(self, content, url, declared=None):
        super().__init__(content)
        self.url = url
        self.headers = {'Content-Length': str(len(content) if declared is None else declared)}
    def geturl(self):
        return self.url


def test_small_grader_acquisition_provenance_freshness_and_failure_kept(tmp_path, monkeypatch):
    acquire = load_acquirer()
    monkeypatch.setattr(acquire, 'private_path', lambda p: (tmp_path, p))
    body = inspect.getsource(get_anscheck_prompt).encode()
    root = tmp_path / 'grader'
    result = acquire.acquire(root, opener=lambda url, **kw: Response(body, url), check=lambda: None)
    assert result['bytes'] == len(body) and result['model_calls'] == result['api_calls'] == 0
    assert result['sha256'] == campaign.sha256(root / 'evaluate_qa.py')
    assert result['upstream_git_revision'] is None
    with pytest.raises(FileExistsError):
        acquire.acquire(root, check=lambda: None)
    failed = tmp_path / 'oversized'
    with pytest.raises(ValueError, match='physical size'):
        acquire.acquire(failed, opener=lambda url, **kw: Response(b'', url, declared=20001), check=lambda: None)
    assert json.loads((failed / 'FAILED.json').read_text())['preserved'] is True
    assert not (failed / 'PROVENANCE.json').exists()


def test_small_grader_redirect_and_truncation_refused(tmp_path, monkeypatch):
    acquire = load_acquirer()
    monkeypatch.setattr(acquire, 'private_path', lambda p: (tmp_path, p))
    for name, response, message in [('redirect', Response(b'x', 'https://elsewhere'), 'redirect'),
                                    ('truncated', Response(b'x', acquire.URL, declared=2), 'truncated')]:
        with pytest.raises(ValueError, match=message):
            acquire.acquire(tmp_path / name, opener=lambda *a, **kw: response, check=lambda: None)
        assert (tmp_path / name / 'FAILED.json').exists()
