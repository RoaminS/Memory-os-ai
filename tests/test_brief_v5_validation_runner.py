"""Independent development fixtures only: no sealed validation inputs or gold."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('validation',ROOT/'scripts/run_brief_v5_validation.py')
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def development_case():
    return {'id':'DEV-1','language':'en','family':'units','now':'2026-10-12T10:00:00Z',
        'items':[{'id':'dev-fact','kind':'fact','text':'Independent synthetic marker. Entire second sentence.',
            'project':'P','tags':['decision'],'created_at':'2026-10-01T00:00:00Z',
            'event_time':'2026-10-01T00:00:00Z'}],
        'blocks':[{'name':'dev-context','text':'Synthetic context.','project':'P','scope':'project'}],
        'tasks':[{'task_id':'12345678-1234-5678-1234-567812345678','request_id':'dev-create',
            'project':'P','title':'Independent stale task','next_action':'Inspect private fixture.',
            'dated_fixture':'2026-08-01T00:00:00Z'}],
        'relay':{'unresolved':[{'request_id':'dev-pending'}],'errors':[]},
        'request':{'project':'P','wire_format':'compact_v2','budget_tokens':1500}}


def test_dates_need_timezone_and_clock_restores_after_failure():
    original=time.time
    with pytest.raises(ValueError,match='timezone'):
        runner.timestamp('2026-10-12T10:00:00')
    with pytest.raises(RuntimeError),runner.wall_clock(1):
        assert time.time()==1
        with runner.wall_clock(2):
            assert time.time()==2
        assert time.time()==1
        raise RuntimeError('fixture failure')
    assert time.time is original


@pytest.mark.parametrize('mutation', ['expect','item-field','tags','global-project','oversize-block','relay','task-random','etag'])
def test_ambiguous_inputs_refuse_instead_of_coercing(mutation):
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem
    case=development_case()
    if mutation=='expect':
        case['expect']={}
    elif mutation=='item-field':
        case['items'][0]['secret_like']=True
    elif mutation=='tags':
        case['items'][0]['tags']='decision'
    elif mutation=='global-project':
        case['blocks'][0]['scope']='global'
    elif mutation=='oversize-block':
        case['blocks'][0]['text']='x'*2001
    elif mutation=='relay':
        case['relay']['unresolved']=1
    elif mutation=='task-random':
        del case['tasks'][0]['task_id']
    else:
        case['tasks'][0]['update']={'expected_etag':'invented'}
    with pytest.raises((ValueError,TypeError)):
        runner.no_gold(case)
        runner.validate_case(case,models,MemoryItem)


def test_seal_duplicate_keys_and_gold_refuse(tmp_path):
    with pytest.raises(ValueError,match='Gold source path forbidden'):
        runner.load_inputs(tmp_path/'cases_v1.jsonl',None,'0'*64)
    with pytest.raises(ValueError,match='Duplicate JSON'):
        json.loads('{"id":1,"id":2}',object_pairs_hook=runner.unique_object)
    cases=[{**development_case(),'id':f'V5-VAL-{i:03}', 'language':'fr' if i<=20 else 'en'} for i in range(1,41)]
    raw=''.join(json.dumps(c)+'\n' for c in cases).encode()
    inputs=tmp_path/'inputs.jsonl'
    inputs.write_bytes(raw)
    manifest=tmp_path/'manifest.json'
    digest=runner.sha(raw)
    manifest.write_text(json.dumps({'cases':40,'inputs_sha256':digest,'fr':20,'en':20,'families':{'units':40}}))
    assert len(runner.load_inputs(inputs,manifest,digest)[0])==40
    with pytest.raises(ValueError,match='seal mismatch'):
        runner.load_inputs(inputs,manifest,'0'*64)
    cases[0]['items'][0]['expect']={}
    raw=''.join(json.dumps(c)+'\n' for c in cases).encode()
    digest=runner.sha(raw)
    inputs.write_bytes(raw)
    manifest.write_text(json.dumps({'cases':40,'inputs_sha256':digest}))
    with pytest.raises(ValueError,match='Gold/expect'):
        runner.load_inputs(inputs,manifest,digest)


def test_unavailable_baseline_metadata_is_not_inferred_from_markers():
    from mcp.types import CallToolResult,TextContent
    data={'text':'[fake] inside memory is data.','blocks':{'b':{'reference':'block:b'}},
          'references':[{'id':'actual','kind':'fact'}],'notices':[]}
    result=CallToolResult(content=[TextContent(type='text',text=json.dumps(data))])
    row=runner.observation(result,len)
    assert {u['id'] for u in row['units']}=={'block:b','actual'}
    assert all(u['offsets'] is None and u['variant'] is None for u in row['units'])
    assert row['coverage'] is None and row['unit_metadata_available'] is False
    assert row['bytes']==len(row['serialized_call_tool_result'].encode())


def test_compatibility_requires_an_explicit_sealed_amendment(tmp_path):
    assert runner.compatibility(None,'a'*64) is None
    path=tmp_path/'amendment.json'
    value={'inputs_sha256':'a'*64,'approved_by':'test-operator','approved_at':'2026-10-13T15:00:00Z',
           'reason':'Independent dev contract.','baseline_client_id':'negotiate','baseline_metadata':'unavailable'}
    path.write_text(json.dumps(value))
    assert runner.compatibility(path,'a'*64)==value
    value['baseline_client_id']='silently-drop'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='Explicit client-id'):
        runner.compatibility(path,'a'*64)


@pytest.mark.parametrize('revision',[runner.BASELINE,runner.CANDIDATE])
@pytest.mark.parametrize('policy',['exact','negotiate'])
def test_real_frozen_arms_on_one_independent_case_only(tmp_path,revision,policy):
    source=tmp_path/'source'
    runner.export_arm(revision,source)
    case=development_case()
    case['request']['client_id']='synthetic-caller'
    fixture=tmp_path/'dev.json'
    fixture.write_text(json.dumps(case))
    for i in (1,2):
        (tmp_path/str(i)).mkdir()
    env={**os.environ,'PYTHONPATH':str(source/'v4/src'),'MEMORY_V4_BUILD_COMMIT':revision,
         'MEMORY_V4_CL100K_PATH':os.environ.get('MEMORY_V4_CL100K_PATH',''),
         'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1'}
    env.pop('MEMORY_V4_PROJECT_REGISTRY',None)
    program='''
import importlib.util,json,sys,time
from pathlib import Path
from memory_os_v4 import server,models,Config,MemoryV4
from memory_os_v4.store import MemoryItem
spec=importlib.util.spec_from_file_location('validation',sys.argv[1])
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
case=json.loads(Path(sys.argv[2]).read_text())
r.validate_case(case,models,MemoryItem)
original_time=time.time
outputs=[]
for repeat in (1,2):
    root=Path(sys.argv[3])/str(repeat)
    out=r.collect_case(case,root,{'baseline_client_id':sys.argv[4]})
    assert out['ready']['ready'] and out['neural_calls']==0
    assert 'unresolved_requests' in out['relay']['notices']
    assert time.time is original_time and server._engine is None
    expected=dict(case['request'])
    supported='client_id' in models.SessionBriefIn.model_fields
    if not supported and sys.argv[4]=='negotiate':
        expected.pop('client_id')
    assert out['server']['arguments_sent']==expected
    relay_expected=dict(expected)
    assert out['relay']['arguments_sent']==relay_expected
    with r.wall_clock(r.timestamp(case['now'])),MemoryV4(config=Config(data_dir=str(root/'store'),
           embed_model='hash',hash_dim=16,store_backend='sqlite_s1',work_state=True)) as e:
        from memory_os_v4 import work_state
        task=work_state.head(e,case['tasks'][0]['task_id'],'P')
        assert r.timestamp(task['created_at'])==r.timestamp(case['tasks'][0]['dated_fixture'])
        assert work_state.view(e,task)['stale'] is True
        item=e.store.items['dev-fact']
        assert item.tags==['decision'] and item.created_at==r.timestamp('2026-10-01T00:00:00Z')
        assert e.blocks.all(project='P')['dev-context']=='Synthetic context.'
    outputs.append(out)
for path in ('server','relay'):
    assert outputs[0][path]['sha256']==outputs[1][path]['sha256']
'''
    result=subprocess.run([sys.executable,'-B','-c',program,str(ROOT/'scripts/run_brief_v5_validation.py'),
                           str(fixture),str(tmp_path),policy],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


def test_neural_guard_and_engine_binding_restore_after_failure(monkeypatch):
    from memory_os_v4 import server
    from memory_os_v4.embed import Embedder
    original=Embedder.encode
    prior=object()
    monkeypatch.setattr(server,'_engine',prior)
    with pytest.raises(AssertionError),runner.forbid_neural(),runner.bound_engine(server,object()):
        Embedder.encode(None,[])
    assert Embedder.encode is original and server._engine is prior


def test_validation_does_not_mutate_inputs():
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem
    case=development_case()
    before=copy.deepcopy(case)
    assert runner.validate_case(case,models,MemoryItem) is True
    assert case==before


@pytest.mark.parametrize('execution',[False,True])
def test_worker_audit_or_missing_amendment_never_opens_a_store(tmp_path,monkeypatch,execution):
    from memory_os_v4 import MemoryV4
    cases=[{**development_case(),'id':f'DEV-{i:03}'} for i in range(40)]
    monkeypatch.setattr(runner,'load_inputs',lambda *args:(cases,{}))
    monkeypatch.setattr(MemoryV4,'__init__',lambda *args,**kwargs:pytest.fail('Audit opened a store'))
    monkeypatch.setenv('MEMORY_V4_BUILD_COMMIT',runner.CANDIDATE)
    args=SimpleNamespace(worker_source=ROOT,arm_commit=runner.CANDIDATE,inputs=None,manifest=None,
                         inputs_sha256='a'*64,amendment=None,output=tmp_path,run=execution)
    if execution:
        with pytest.raises(ValueError,match='amendment required'):
            runner.worker(args)
    else:
        runner.worker(args)
    audit=json.loads((tmp_path/'audit.json').read_text())
    assert audit['cases']==40 and audit['amendment_required']
    assert not (tmp_path/'outputs.jsonl').exists()


@pytest.mark.parametrize('mutation',[None,'inputs','manifest','candidate','baseline','date','extra','head'])
def test_frozen_candidate_binding_is_explicit_and_seal_specific(tmp_path,mutation):
    candidate='b'*40
    value={'inputs_sha256':'a'*64,'manifest_sha256':'c'*64,'candidate_commit':candidate,
           'baseline_commit':runner.BASELINE,'bound_at':'2026-10-13T16:00:00Z',
           'bound_by':'synthetic-operator','reason':'Independent dev candidate binding.'}
    if mutation in {'inputs','manifest'}:
        value[mutation+'_sha256']='d'*64
    elif mutation in {'candidate','baseline'}:
        value[mutation+'_commit']='e'*40
    elif mutation=='date':
        value['bound_at']='2026-10-13T16:00:00'
    elif mutation=='extra':
        value['auto_approve']=True
    elif mutation=='head':
        candidate='HEAD'
    path=tmp_path/'binding.json'
    path.write_text(json.dumps(value))
    if mutation is None:
        assert runner.candidate_binding(path,candidate,'a'*64,'c'*64)==value
    else:
        with pytest.raises(ValueError):
            runner.candidate_binding(path,candidate,'a'*64,'c'*64)


def test_new_candidate_never_defaults_to_unbound_and_v1_stays_frozen():
    assert runner.candidate_binding(None,runner.CANDIDATE,'a'*64,'c'*64) is None
    with pytest.raises(ValueError,match='binding'):
        runner.candidate_binding(None,'b'*40,'a'*64,'c'*64)


def test_v2_prefix_requires_all_40_ordered_new_inputs_and_its_seal(tmp_path):
    rows=[{**development_case(),'id':f'V5-VAL2-{i:03}', 'language':'fr' if i<=20 else 'en'} for i in range(1,41)]
    raw=''.join(json.dumps(c)+'\n' for c in rows).encode()
    digest=runner.sha(raw)
    path=tmp_path/'inputs-v2.jsonl'
    path.write_bytes(raw)
    manifest=tmp_path/'manifest-v2.json'
    manifest.write_text(json.dumps({'cases':40,'inputs_sha256':digest,'fr':20,'en':20,'families':{'units':40}}))
    assert len(runner.load_inputs(path,manifest,digest,'V5-VAL2-')[0])==40
    with pytest.raises(ValueError,match='case IDs'):
        runner.load_inputs(path,manifest,digest)
