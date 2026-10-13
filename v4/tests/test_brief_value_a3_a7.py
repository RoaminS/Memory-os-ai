"""Synthetic development tests; never the 40 hidden validation golds."""
from collections import Counter
from types import SimpleNamespace

import anyio
import pytest

from memory_os_v4 import Config, MemoryV4, brief, server
from memory_os_v4.brief_selection import Unit, assemble, read_units
from memory_os_v4.brief_coverage import COLUMNS, population
from memory_os_v4.brief_value import memory_order, origin, task_order
from memory_os_v4.ops.relay import SessionToolCatalog, brief_client_arguments
from memory_os_v4.store import MemoryItem


def item(key, tags=(), kind='fact', importance=.5, when=1, source='chat_save'):
    return MemoryItem(id=key, kind=kind, text='Complete '+key+'.', project='P', tags=list(tags),
                      importance=importance, event_time=when, source=source)


@pytest.fixture(autouse=True)
def structural_counter(monkeypatch):
    monkeypatch.setattr(brief, 'counter', lambda: lambda text: (len(text.encode())+2)//3)


def totals(data):
    values = data['coverage']['totals']
    return dict(zip(COLUMNS, values)) if isinstance(values, list) else values


def test_frozen_value_order_and_date_is_only_a_tie_break():
    values = [item('fact', when=999), item('decision', ['decision'], when=1),
              item('procedure', kind='procedure'), item('change', ['change'], source='client:Claude'),
              item('critical', ['constraint','critical'])]
    ranked = sorted(values, key=lambda it: memory_order(it, set(), {}, 'Codex'))
    assert [v.id for v in ranked] == ['critical','decision','procedure','change','fact']
    assert memory_order(values[0], {'fact'}, {}, 'Codex')[0] == 0
    a, b = item('a', importance=.9, when=1), item('b',importance=.1,when=999)
    assert memory_order(a,set(),{},None) < memory_order(b,set(),{},None)
    assert memory_order(a,set(),{'b':1},None) > memory_order(b,set(),{'b':1},None)


@pytest.mark.parametrize('caller,source,tags,expected',[
    ('Codex','client:Claude',['change'],6), ('Codex','client:codex',['change'],7),
    (None,'client:Claude',['change'],7), ('unknown','client:Claude',['change'],7),
    ('Codex','chat_save',['change'],7), ('Codex','chat_save',['change','client:Claude'],6),
    ('Codex','client:Claude',['change','client:Grok'],7),
    ('Codex','client:Claude',[],7), ('ＣＯＤＥＸ','client:codex',['update'],7),
])
def test_other_client_requires_explicit_consistent_declaration(caller,source,tags,expected):
    assert memory_order(item('x',tags,source=source),set(),{},caller)[0] == expected
    assert origin(item('x', ['client:Claude','client:unknown'])) is None
    assert origin(item('x', ['client:Claude','client:Claude'])) is None


def test_stale_is_last_without_closing_and_requested_first():
    task={'task_id':'x','updated_at':'2026-10-12T10:00:00+00:00','stale':False,'status':'blocked'}
    assert task_order(task,set())[0] == 2
    assert task_order({**task,'status':'open'},set())[0] == 3
    assert task_order({**task,'stale':True},set())[0] == 8
    assert task_order({**task,'stale':True},{'task:x'})[0] == 0


def test_actual_engine_ranks_value_before_forty_item_pool(tmp_path, monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash')) as e:
        values = [item('recent-'+str(i),when=999) for i in range(45)] + [item('old-decision',['decision'])]
        for value in values:
            e.store.append(value)
        monkeypatch.setattr(e.embedder,'encode',lambda *a:pytest.fail('brief encoded'))
        out=brief.build(e,'P',wire_format='compact_v2')
        units=read_units(out)
        assert units[0].id == 'old-decision'
        counts=totals(out)
        assert counts['eligible']==46 and counts['pool']==40 and counts['outside_pool']==6
        assert counts['omitted']==40-counts['content']-counts['reference_only']
        assert 'budget_saturated' in out['notices']


def test_counts_are_scoped_valid_sensitive_filtered_and_unique(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash')) as e:
        e.store.append(item('allowed'))
        e.store.append(item('secret',['sensitive']))
        e.store.append(MemoryItem(**{**item('foreign').to_dict(),'project':'Q'}))
        e.store.append(MemoryItem(**{**item('invalid').to_dict(),'valid_until':1}))
        out=brief.build(e,'P',expand=['allowed'],wire_format='compact_v2')
        assert totals(out)['eligible'] == totals(out)['pool'] == 1
        assert {u.id for u in read_units(out)} == {'allowed'}
        assert out['coverage']['by_kind']['fact']['content']==1


def test_relay_recalculates_counts_after_whole_unit_drop():
    units=[Unit('id-'+str(i),'fact','memory','Entire sentence. '*10,(i,)) for i in range(8)]
    data=assemble({'mode':'compact','project':'P','wire_format':'compact_v2',
                   'coverage':population({'fact':12},units)},units,[])
    out=brief.rebudget(data,['unresolved_requests'],budget_tokens=600)
    counts=totals(out)
    assert counts['eligible']==12 and counts['pool']==8 and counts['outside_pool']==4
    assert counts['content']==len(read_units(out))
    assert counts['omitted']==8-counts['content']


@pytest.mark.parametrize('wire', ['legacy','compact_v2'])
@pytest.mark.parametrize('budget', [200,600,1500])
def test_small_budget_keeps_exact_totals_even_if_kind_details_do_not_fit(wire,budget):
    units=[Unit('id-'+str(i),kind,'memory','A complete sentence.',(i,))
           for i,kind in enumerate(['fact','episode','summary','procedure','doc'])]
    data=assemble({'mode':'compact','project':'P','wire_format':wire,
                   'coverage':population(dict(Counter(u.kind for u in units)),units)},units,[])
    out=brief.rebudget(data,['unresolved_requests'],budget_tokens=budget)
    assert brief.measures(out)[1]<=budget
    counts=totals(out)
    assert counts['eligible']==counts['pool']==5
    assert sum(counts[k] for k in ('content','reference_only','omitted'))==5
    if 'by_kind' not in out['coverage']:
        assert 'coverage_detail_omitted' in out['notices']


def test_task_eligible_count_not_limited_by_page_and_expansion_is_scoped(tmp_path,monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash',store_backend='sqlite_s1',work_state=True)) as e:
        monkeypatch.setattr(server,'get_engine',lambda:e)
        monkeypatch.setattr(server,'_engine',e)
        for i in range(12):
            assert server.dispatch('memory_task_create',{'project':'P','request_id':'create-'+str(i),
                                   'title':'Task '+str(i),'next_action':'Review evidence'})['ok']
        server.dispatch('memory_task_create',{'project':'Q','request_id':'other','title':'Foreign','next_action':'Wait'})
        out=brief.build(e,'P',wire_format='compact_v2')
        assert totals(out)['eligible']==12 and totals(out)['pool']==10
        assert out['coverage']['by_kind']['task']['eligible']==12


def test_catalog_negotiates_caller_and_relay_overrides_only_model_identity():
    catalog=SessionToolCatalog()
    assert not catalog.brief_client_supported()
    catalog._tools=anyio.run(server.list_tools)
    assert catalog.brief_client_supported()
    args={'project':'P','client_id':'fake','focus_query':'Topic'}
    assert brief_client_arguments(args,SimpleNamespace(name='Codex',version='1')) == {**args,'client_id':'Codex'}
    assert args['client_id']=='fake'
