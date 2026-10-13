"""Disclosed V5-VAL-038 is dev now. No V5-VAL v2 data or gold are read."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import anyio
import pytest

from memory_os_v4 import Config, MemoryV4, brief, server
from memory_os_v4.brief_coverage import population
from memory_os_v4.brief_selection import Unit, read_units, select
from memory_os_v4.brief_tokens import counter
from memory_os_v4.ops.durable_relay import DurableRelay
from memory_os_v4.store import MemoryItem

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('feasibility',ROOT/'scripts/brief_v5_feasibility_oracle.py')
oracle=importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


def pool(text):
    # Exact disclosed case population, text, project, ID and protection.
    return [Unit('z-crit','fact','memory',text,(1,0,0,0,'z-crit'),True)] + [
        Unit(f'z{i:02}','fact','memory',f'Note {i}: log detail of no particular importance for resuming.',
             (7,0,0,0,f'z{i:02}')) for i in range(30)]


def context(units):
    return {'id':'DEV-DISCLOSED-038','project':'acme-api','budget_tokens':200,
            'eligible':{'fact':31},
            'pool':[{'id':u.id,'kind':u.kind,'text':u.text,'protected':u.protected} for u in units],
            'notices':[], 'relay':{'unresolved':[{'request_id':'dev-pending'}],'errors':[]},
            'requirements':[{'id':'z-crit','allowed':['full','first_sentence'],'required_text':units[0].text.split('. ')[0]+'.'
                if '. ' in units[0].text else units[0].text}]}


@pytest.mark.parametrize('text,representation,expected',[
    ('Never replay an uncertain write.','full','Never replay an uncertain write.'),
    ('Stop. '+ 'Extra complete context. '*300,'sentence','Stop.')],ids=['disclosed-full','synthetic-sentence'])
def test_exact_oracle_feasible_protected_text_is_admitted_and_survives_relay(text,representation,expected):
    count=counter()
    units=pool(text)
    measured=oracle.oracle(context(units),count)
    assert measured['joint']['per_path']['prospective']['feasible_required_only_frame']
    base={'mode':'compact','project':'acme-api','wire_format':'compact_v2','budget_tokens':200,
          'coverage':population({'fact':31},units)}
    unchanged=json.dumps(base)
    result=select(base,units,[],count,6000,200)
    assert json.dumps(base)==unchanged
    chosen=read_units(result)
    assert [(u.id,u.representation) for u in chosen]==[('z-crit',representation)]
    assert chosen[0].text==expected
    assert isinstance(result['coverage']['totals'],list)
    assert result['coverage']['totals']==[31,31,1,0,30,0]
    assert 'coverage_detail_omitted' in result['notices']
    assert 'critical_omitted' not in result['notices']
    for data in (result,brief.rebudget(result,['unresolved_requests'])):
        assert brief.measures(data,count)[0]<=6000 and brief.measures(data,count)[1]<=200
        assert read_units(data)[0].text==chosen[0].text
        assert data['coverage']['totals']==[31,31,1,0,30,0]


def test_infeasible_protected_content_keeps_explicit_omission():
    count=counter()
    units=pool('averylongtoken'*1000)
    measured=oracle.oracle(context(units),count)
    assert not measured['joint']['per_path']['prospective']['feasible_required_only_frame']
    result=select({'mode':'compact','project':'acme-api','wire_format':'compact_v2','budget_tokens':200,
        'coverage':population({'fact':31},units)},units,[],count,6000,200)
    assert not any(u.id=='z-crit' for u in read_units(result) or [])
    assert 'critical_omitted' in result['notices']
    assert brief.measures(result,count)[1]<=200


def test_real_model_free_server_and_relay_include_disclosed_critical_fact(tmp_path,monkeypatch):
    units=pool('Never replay an uncertain write.')
    with MemoryV4(config=Config(data_dir=str(tmp_path/'store'),embed_model='hash',hash_dim=16)) as engine:
        for unit in units:
            engine.store.append(MemoryItem(id=unit.id,kind='fact',project='acme-api',text=unit.text,
                tags=['constraint','critical'] if unit.protected else [],created_at=1,event_time=1))
        monkeypatch.setattr(server,'_engine',engine)
        monkeypatch.setattr(server,'get_engine',lambda:engine)
        monkeypatch.setattr(engine.embedder,'encode',lambda *a:pytest.fail('Brief encoded'))
        class Ledger:
            def status(self):
                return {'unresolved_count':1,'errors':[],'journal_poisoned':False}
        class Transport:
            async def submit(self,operation,*,replay):
                class Session:
                    async def call_tool(self,name,args):
                        return await server.call_tool(name,args)
                return await operation(Session())
        relay=DurableRelay(Transport(),None)
        relay.journal=Ledger()
        relay.brief_wire_formats={'legacy','compact_v2'}
        relay.brief_budget_supported=True
        request={'project':'acme-api','wire_format':'compact_v2','budget_tokens':200}
        outputs=[]
        for _ in range(2):
            direct=anyio.run(server.call_tool,'memory_session_brief',request)
            relayed=anyio.run(relay.call,'memory_session_brief',request)
            outputs.append((direct.model_dump_json(),relayed.model_dump_json()))
            for result in (direct,relayed):
                data=json.loads(result.content[0].text)
                assert [u.id for u in read_units(data)]==['z-crit']
                assert read_units(data)[0].text=='Never replay an uncertain write.'
                assert data['coverage']['totals']==[31,31,1,0,30,0]
                assert brief.measures(data,counter())[1]<=200
        assert outputs[0]==outputs[1]


@pytest.fixture
def original_selector(tmp_path,monkeypatch):
    path=tmp_path/'original_selection.py'
    path.write_bytes(subprocess.run(['git','show','177eedb:v4/src/memory_os_v4/brief_selection.py'],
        cwd=ROOT,capture_output=True,check=True).stdout)
    spec=importlib.util.spec_from_file_location('memory_os_v4._original_selection',path)
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    spec.loader.exec_module(module)
    return module.select


def test_ordinary_units_do_not_force_new_coverage_reduction(original_selector):
    count=counter()
    units=[Unit('ordinary','fact','memory','Ordinary complete sentence. '*50,(7,),False)]
    base={'mode':'compact','project':'acme-api','wire_format':'compact_v2','budget_tokens':200,
        'coverage':population({'fact':1},units)}
    result=select(base,units,[],count,6000,200)
    assert result==original_selector(base,units,[],count,6000,200)


def test_disclosed_case_was_omitted_before_fix_despite_oracle_feasibility(original_selector):
    units=pool('Never replay an uncertain write.')
    count=counter()
    assert oracle.oracle(context(units),count)['joint']['per_path']['prospective']['feasible_required_only_frame']
    old=original_selector({'mode':'compact','project':'acme-api','wire_format':'compact_v2','budget_tokens':200,
        'coverage':population({'fact':31},units)},units,[],count,6000,200)
    assert not read_units(old) and 'critical_omitted' in old['notices']
