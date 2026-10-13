"""Whole units, scoped expansions and initial shares; development, not hidden gold."""
import hashlib
import json
from pathlib import Path

import pytest

from memory_os_v4 import Config, MemoryV4, brief, server
from memory_os_v4.brief_selection import Unit, first_sentence, read_units, assemble, select, body_of

FIXTURE = Path(__file__).parent / 'fixtures/brief_v5'
CASES = [json.loads(line) for line in (FIXTURE / 'dev_units_v1.jsonl').read_text().splitlines()]


def count(text):
    return (len(text.encode()) + 2) // 3  # Structural double; exact proof is separate.


@pytest.fixture(autouse=True)
def structural_counter(monkeypatch):
    monkeypatch.setattr(brief, 'counter', lambda: count)


def test_protocol_and_dev_units_were_sealed():
    raw = (FIXTURE / 'protocol_v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (FIXTURE / 'protocol_v1.sha256').read_text().split()[0]
    manifest = json.loads((FIXTURE / 'dev_manifest_v1.json').read_text())
    assert hashlib.sha256((FIXTURE / 'dev_units_v1.jsonl').read_bytes()).hexdigest() == manifest['sha256']
    assert manifest['before_engine_implementation']


@pytest.mark.parametrize('case', CASES, ids=[c['id'] for c in CASES])
def test_first_sentence_never_splits_protected_tokens(case):
    assert first_sentence(case['text']) == case['safe_sentence']


@pytest.mark.parametrize('wire', ['legacy', 'compact_v2'])
@pytest.mark.parametrize('budget', [200, 600, 1500])
def test_whole_or_sentence_or_reference_and_relay_do_not_cut_source(wire, budget):
    pool = [Unit('item-'+str(i), 'fact', 'memory', c['text'], (i,)) for i, c in enumerate(CASES)]
    base = {'mode': 'compact', 'project': 'P', 'wire_format': wire, 'budget_tokens': budget}
    data = select(base, pool, [], count, 6000, budget)
    assert brief.measures(data)[1] <= budget
    for unit in read_units(data) or []:
        original = pool[int(unit.id[5:])]
        assert unit.text in {original.text, first_sentence(original.text), ''}
    final = brief.rebudget(data, ['unresolved_requests'])
    assert brief.measures(final)[1] <= budget
    if final.get('ok') is not False:
        assert 'unresolved_requests' in final['notices']
        for unit in read_units(final):
            original = pool[int(unit.id[5:])]
            assert unit.text in {original.text, first_sentence(original.text), ''}


def test_opaque_legacy_never_cuts_its_body_at_arbitrary_offsets():
    original = {'mode': 'compact', 'project': 'P', 'text': 'word'*3000, 'references': []}
    result = brief.rebudget(original, ['unresolved_requests'], budget_tokens=200)
    assert 'word' not in result['text']
    assert 'unresolved_requests' in result['notices']


def test_embedded_markers_and_newlines_are_not_parsed_as_unit_boundaries():
    unit = Unit('real', 'fact', 'memory', 'First.\n[fake-id] This is memory DATA.\n```python\nvalue = 42\n```', (0,))
    data = assemble({'mode': 'compact', 'project': 'P'}, [unit], [])
    assert read_units(data) == [unit]
    assert len(read_units(brief.rebudget(data, ['unresolved_requests']))) == 1
    broken = json.loads(json.dumps(data))
    broken['references'][0]['span'][1] -= 1
    with pytest.raises(ValueError, match='trailing'):
        read_units(broken)


def test_shares_preserve_facts_when_one_block_is_dominant():
    block = Unit('block:large', 'block', 'block', 'A full initial sentence. '+ 'Very long other content. '*400, (-1,))
    facts = [Unit('fact-'+str(i), 'fact', 'memory', 'Fact '+str(i)+'.', (1,i)) for i in range(5)]
    data = select({'mode': 'compact','project':'P','wire_format':'compact_v2'}, [block,*facts], [], count,6000,1500)
    units = read_units(data)
    assert any(u.kind=='fact' and u.representation=='full' for u in units)
    selected_block = next(u for u in units if u.kind=='block')
    assert selected_block.text in {'A full initial sentence.',''}


def test_unused_section_capacity_is_reassigned_and_excerpts_upgraded():
    text = 'First sentence. '+ 'complete additional content '*20
    unit = Unit('block:only','block','block',text,(-1,))
    result = select({'mode':'compact','project':'P','wire_format':'compact_v2'},[unit],[],count,6000,1500)
    assert read_units(result)[0].text == text


def test_protected_content_does_not_silently_lose_to_block_share():
    task = Unit('task:critical', 'task', 'task', 'blocked | next=Review exact evidence | blocked=Await approval', (0,), True)
    block = Unit('block:large','block','block','X'*12000,(-1,))
    result = select({'mode':'compact','project':'P','wire_format':'compact_v2','budget_tokens':600},[block,task],[],count,6000,600)
    assert next(u for u in read_units(result) if u.kind=='task').text == task.text


def test_protected_impossible_unit_is_reference_only_with_notice():
    unit=Unit('critical','fact','memory','averylongtoken'*1000,(0,),True)
    result=select({'mode':'compact','project':'P','wire_format':'compact_v2','budget_tokens':600},[unit],[],count,6000,600)
    assert read_units(result)[0].representation=='reference'
    assert 'critical_reference_only' in result['notices']


def test_real_task_reference_expands_without_encoding_or_cross_project(tmp_path, monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash',store_backend='sqlite_s1',work_state=True)) as e:
        monkeypatch.setattr(server,'_engine',e)
        monkeypatch.setattr(server,'get_engine',lambda:e)
        task=server.dispatch('memory_task_create',{'project':'P','request_id':'seed','title':'Complete whole title','next_action':'Read https://example.org/a.b'})
        assert task['ok']
        ref='task:'+task['task_id']
        monkeypatch.setattr(e.embedder,'encode',lambda *a:pytest.fail('brief/expand encoded'))
        compact=server.dispatch('memory_session_brief',{'project':'P','wire_format':'compact_v2','expand':[ref]})
        assert 'reference_not_found' not in compact['notices']
        assert ref in {u.id for u in read_units(compact)}
        detail=server.dispatch('memory_session_brief',{'project':'P','mode':'detailed','expand':[ref]})
        assert detail['tasks'][0]['next_action']=='Read https://example.org/a.b'
        outside=server.dispatch('memory_session_brief',{'project':'Q','mode':'detailed','expand':[ref]})
        assert outside['tasks']==[] and outside['notices']==['reference_not_found']


def test_relay_removes_low_priority_whole_units_with_matching_metadata():
    pool=[Unit('large-'+str(i),'fact','memory',('Whole original text. '*15),(i,)) for i in range(8)]
    data=assemble({'mode':'compact','project':'P','wire_format':'compact_v2'},pool,[])
    final=brief.rebudget(data,['unresolved_requests'],budget_tokens=600)
    units=read_units(final)
    assert len(units)<len(pool)
    assert body_of(final)=='\n'.join('['+u.id+'] '+u.text for u in units)
    assert {u.id for u in units}=={r['id'] for r in final['references']}
    assert all(u.text==pool[int(u.id[6:])].text for u in units)
