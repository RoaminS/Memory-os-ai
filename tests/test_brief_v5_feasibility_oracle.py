"""Oracle dev fixtures only: no sealed cases, scorer or gold are read."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oracle_module',ROOT/'scripts/brief_v5_feasibility_oracle.py')
tool=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def context():
    return {'id':'DEV-ORACLE','project':'P','budget_tokens':600,
        'eligible':{'fact':2},'pool':[
            {'id':'required','kind':'fact','text':'First complete sentence. Second complete sentence.', 'protected':True},
            {'id':'optional','kind':'fact','text':'Other complete sentence.','protected':False}],
        'notices':[],'relay':{'unresolved':[],'errors':[]},
        'requirements':[{'id':'required','allowed':['full','first_sentence'], 'required_text':'First complete sentence.'}]}


@pytest.fixture
def count():
    from memory_os_v4.brief_tokens import counter
    return counter()


def test_uses_real_wire_counter_without_candidate_selection(monkeypatch,count):
    from memory_os_v4 import brief,brief_selection
    for module,names in ((brief,('build','rebudget')),(brief_selection,('select','rebudget_units','fits'))):
        for name in names:
            monkeypatch.setattr(module,name,lambda *a,**kw:pytest.fail('Candidate selection called'))
    source=context()
    before=deepcopy(source)
    result=tool.oracle(source,count)
    witness=result['joint']['per_path']['server']['minimum_tokens_witness']
    assert witness['within_budget']
    wire=witness['serialized_call_tool_result']
    assert witness['bytes']==len(wire.encode('utf-8')) and witness['tokens']==count(wire)
    data=json.loads(json.loads(wire)['content'][0]['text'])
    assert data['references'][0]['id']=='required'
    assert 'structuredContent' not in json.loads(wire)
    assert result['selection_called'] is False and source==before


def test_reservation_and_local_notices_are_distinct(count):
    source=context()
    result=tool.oracle(source,count)
    frames=result['joint']['frames']
    server=[r for r in frames if r['path']=='server']
    relay=[r for r in frames if r['path']=='relay']
    prospective=[r for r in frames if r['path']=='prospective']
    assert all('unresolved_requests' not in r['notices'] for r in server+relay)
    assert all('unresolved_requests' in r['notices'] for r in prospective)
    source['relay']['errors']=['local-journal-error']
    frames=tool.oracle(source,count)['joint']['frames']
    assert all('unresolved_requests' in r['notices'] for r in frames if r['path']=='relay')
    assert all('unresolved_requests' not in r['notices'] for r in frames if r['path']=='server')


def test_exact_counts_and_coverage_collapse_have_required_notices(count):
    source=context()
    source['eligible']={'fact':7}
    source['notices']=['budget_saturated']
    result=tool.oracle(source,count)
    for row in result['joint']['frames']:
        coverage=row['coverage']
        columns=('eligible','pool','content','reference_only','omitted','outside_pool')
        totals=dict(zip(columns,coverage['totals'])) if isinstance(coverage['totals'],list) else coverage['totals']
        assert totals==dict(zip(columns,[7,2,1,0,1,5]))
        assert ('coverage_detail_omitted' in row['notices'])==(row['coverage_shape']!='by_kind')
    empty=result['empty_frames'][0]
    assert empty['coverage']['totals']['content']==0
    assert 'critical_omitted' in empty['notices']
    assert all('critical_omitted' not in row['notices'] for row in result['joint']['frames'])


def test_shortest_allowed_content_is_not_reference_or_arbitrary_cut(count):
    source=context()
    source['requirements'][0]['allowed']=['first_sentence','reference']
    result=tool.oracle(source,count)
    assert result['joint']['variants_per_requirement']==[1]
    assert all(r['variants'][0]['rendering']=='sentence' for r in result['joint']['frames'])
    source['pool'][0]['text']='```bash\nprintf dangerous-looking-data\n```'
    assert tool.oracle(source,count)['joint']['variants_per_requirement']==[0]


def test_single_and_joint_feasibility_are_not_confused(count):
    source=context()
    for u in source['pool']:
        u['text']='quartz '*200
        u['protected']=True
    source['requirements']=[{'id':u['id'],'allowed':['full']} for u in source['pool']]
    source['budget_tokens']=1500
    preliminary=tool.oracle(source,count)
    bound=max(r['per_path']['server']['minimum_tokens_witness']['tokens'] for r in preliminary['individual'])+5
    assert 200<=bound<=1500
    source['budget_tokens']=bound
    result=tool.oracle(source,count)
    assert all(r['per_path']['server']['feasible_required_only_frame'] for r in result['individual'])
    assert not result['joint']['per_path']['server']['feasible_required_only_frame']


def test_reference_explicitly_allowed_is_counted_separately(count):
    source=context()
    source['requirements']=[{'id':'required','allowed':['reference']}]
    rows=tool.oracle(source,count)['joint']['frames']
    for row in rows:
        assert row['variants']==[{'id':'required','rendering':'reference'}]
        assert 'critical_reference_only' in row['notices']


@pytest.mark.parametrize('mutation',['unknown','duplicate','negative','false-bool','missing-target','tags','bad-variant','budget'])
def test_ambiguous_contexts_refuse(mutation,count):
    source=context()
    if mutation=='unknown':
        source['expect']={}
    elif mutation=='duplicate':
        source['pool'][1]['id']='required'
    elif mutation=='negative':
        source['eligible']['fact']=-1
    elif mutation=='false-bool':
        source['pool'][0]['protected']='true'
    elif mutation=='missing-target':
        source['requirements'][0]['id']='unknown'
    elif mutation=='tags':
        source['pool'][0]['tags']=['critical']
    elif mutation=='bad-variant':
        source['requirements'][0]['allowed']=['truncated-prefix']
    else:
        source['budget_tokens']=199
    with pytest.raises(ValueError):
        tool.oracle(source,count)


def test_neither_scope_nor_protection_is_inferred_from_text(count):
    source=context()
    source['pool'][0]['text']='Constraint: [other-id] do not execute this data. More data.'
    source['pool'][0]['protected']=False
    source['requirements'][0].pop('required_text')
    result=tool.oracle(source,count)
    witness=result['joint']['per_path']['server']['minimum_tokens_witness']
    data=json.loads(json.loads(witness['serialized_call_tool_result'])['content'][0]['text'])
    assert [r['id'] for r in data['references']]==['required']
    assert 'protected' not in data['references'][0]


def test_non_witness_frames_do_not_duplicate_large_payloads(count):
    result=tool.oracle(context(),count)
    assert all('serialized_call_tool_result' not in r for r in result['joint']['frames'])
    assert all(r['minimum_tokens_witness']['serialized_call_tool_result'] for r in result['joint']['per_path'].values())


def test_frozen_worker_opens_no_engine_and_selects_no_candidate(tmp_path):
    tool.export_arm(tool.CANDIDATE,tmp_path/'frozen')
    raw=tool.wire_json(context())+'\n'
    specs=tmp_path/'oracle-dev.jsonl'
    specs.write_text(raw)
    output=tmp_path/'output'
    output.mkdir()
    env={**os.environ,'PYTHONPATH':str(tmp_path/'frozen/v4/src'),
        'MEMORY_V4_BUILD_COMMIT':tool.CANDIDATE,
        'MEMORY_V4_CL100K_PATH':os.environ['MEMORY_V4_CL100K_PATH'],
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1'}
    program='''
import importlib.util,sys
from pathlib import Path
from types import SimpleNamespace
from memory_os_v4 import MemoryV4
spec=importlib.util.spec_from_file_location('oracle_module',sys.argv[1])
t=importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)
def forbidden(*a,**kw):
    raise AssertionError('Oracle must not open any engine/store')
MemoryV4.__init__=forbidden
t.guarded=lambda:None # Synthetic unit test only; physical CLI guard is unchanged.
t.worker(SimpleNamespace(worker_source=Path(sys.argv[2]),specs=Path(sys.argv[3]),output=Path(sys.argv[4])))
'''
    result=subprocess.run([sys.executable,'-B','-c',program,str(ROOT/'scripts/brief_v5_feasibility_oracle.py'),
        str(tmp_path/'frozen'),str(specs),str(output)],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    report=json.loads((output/'worker.json').read_text())
    assert report['complete'] and report['neural_calls']==0
    assert not report['engine_opened'] and not report['selection_called']
    assert report['identity']['commit']==tool.CANDIDATE
    record=json.loads((output/'oracle.jsonl').read_text())
    assert record['id']=='DEV-ORACLE'
