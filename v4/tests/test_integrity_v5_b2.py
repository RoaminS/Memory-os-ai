"""B2 development matrix; synthetic private stores, no models/TCP/live state."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.corrections import revision
from memory_os_v4.maintenance import WritePolicy
from memory_os_v4.models import FactIn
from memory_os_v4.sqlite_store import MIN_SQLITE
from memory_os_v4.store import MemoryItem

SQL = pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason='Unqualified SQLite runtime')


def snapshot(m):
    if m.config.store_backend == 'jsonl':
        return m.store.items_path.read_bytes()
    return {table: m.store._connection.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
            for table in ('items', 'item_versions', 'request_receipts', 'store_metadata', 'receipt_counter', 'embedding_cache')}


@pytest.fixture
def mem(tmp_path, monkeypatch):
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        pytest.skip('Unqualified SQLite runtime')
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend='sqlite_s1', embed_model='hash', hash_dim=16)) as m:
        monkeypatch.setattr(server, '_engine', m)
        yield m


def seed(m, item_id='old', *, project='P', identity=None, text='Service uses port 8000.'):
    f = {'item_id': item_id, 'text': text, 'event_time': '2026-10-12T10:00:00Z'}
    if identity:
        f['identity'] = identity
    m.add_facts([f], project=project)
    return revision(m.store.get(item_id), m.store)


def correction(old='old', new='new', *, text='Service uses port 9000.'):
    return {'item_id': new, 'text': text, 'event_time': '2026-10-13T10:00:00Z', 'replaces': [old]}


def call(facts=None, **kwargs):
    return server.dispatch('memory_add_facts', {'project': 'P', 'facts': facts or [correction()], **kwargs})


def test_logical_revision_excludes_access_and_includes_unknown_authority():
    it = MemoryItem(id='a', kind='fact', text='Synthetic.', event_time=100)
    first = revision(it)
    it.access_count = 99
    it.last_accessed = 999
    it.hash = 'derived'
    assert revision(it) == first
    class Connection:
        def execute(self, *args):
            return SimpleNamespace(fetchone=lambda: (json.dumps(it.to_dict() | {'future_field': 'preserved'}),))
    assert revision(it, SimpleNamespace(_connection=Connection())) != first
    it.deleted = True
    assert revision(it) != first


def test_single_explicit_fast_path_and_durable_audit(mem):
    rev = seed(mem)
    r = call(reason='Operator changed port.', expected_revisions={'old': rev}, request_id='fast',
             client_provenance={'client_id': 'codex', 'client_version': 'synthetic'})
    assert r['added'] == ['new'] and r['superseded'] == [['old', 'new']]
    assert r['impacts'][0]['expected_revision'] == rev
    assert r['changed_by'] == {'client_id': 'codex', 'client_version': 'synthetic', 'authenticated': False}
    assert r['notices'] == []
    audit = mem.store.raw_history('new')[-1]['correction']
    assert audit['verified'] and audit['reason'] == 'Operator changed port.' and audit['impacts']


def test_read_revisions_and_access_do_not_break_cas(mem):
    rev = seed(mem)
    hit = server.dispatch('memory_search', {'project': 'P', 'query': 'Service port'})['hits'][0]
    assert hit['revision'] == rev
    result = server.dispatch('memory_get_context', {'project': 'P', 'query': 'Service port'})
    ref = result['references'][0]
    assert ref['id'] == 'old' and ref['revision'] == rev
    assert result['context'][ref['span'][0]:ref['span'][1]].endswith('Service uses port 8000.')
    mem.flush()
    assert revision(mem.store.get('old'), mem.store) == rev
    assert call(reason='Port changed.', expected_revisions={'old': rev}, request_id='after-read')['added'] == ['new']


@pytest.mark.parametrize('missing,code', [('reason','reason_required'), ('expected_revisions','revision_conflict'), ('request_id','request_id_required')])
def test_controlled_missing_fields_do_not_downgrade_or_write(mem, missing, code):
    rev = seed(mem)
    controls = {'mode': 'apply', 'reason': 'Change.', 'expected_revisions': {'old': rev}, 'request_id': 'bad'}
    controls.pop(missing)
    before = snapshot(mem)
    r = call(**controls)
    assert r['ok'] is False and r['code'] == code and r['outcome'] == 'not_started'
    assert snapshot(mem) == before


def test_stale_cas_refused_after_concurrent_correction(mem):
    rev = seed(mem)
    assert call()['superseded']
    before = snapshot(mem)
    r = call([correction(new='third')], reason='Stale correction.', expected_revisions={'old': rev}, request_id='stale')
    assert r['code'] == 'revision_conflict' and r['outcome'] == 'not_started'
    assert snapshot(mem) == before


def test_same_request_id_replays_before_rechecking_closed_target(mem):
    rev = seed(mem)
    args = dict(reason='Change.', expected_revisions={'old': rev}, request_id='replay')
    first = call(**args)
    before = snapshot(mem)
    assert call(**args) == first
    assert snapshot(mem) == before


def test_preview_is_model_free_receipt_free_and_allowed_read_only(mem, monkeypatch):
    seed(mem)
    before = snapshot(mem)
    monkeypatch.setattr(mem, '_write_policy', WritePolicy('read_only'))
    monkeypatch.setattr(mem, 'ensure_retrieval', lambda: (_ for _ in ()).throw(AssertionError('Preview must not encode')))
    r = call(mode='preview', reason='Inspect.')
    assert r['ok'] and r['outcome'] == 'not_started' and len(r['impacts']) == 1
    assert snapshot(mem) == before
    assert FactIn.model_validate(r['prepared_facts'][0])


@pytest.mark.parametrize('n', [2, 3, 4])
def test_multiple_impacts_need_bound_preview_then_apply(mem, n):
    old_ids = [f'old-{i}' for i in range(n)]
    for item_id in old_ids:
        seed(mem, item_id=item_id, text=f'Unique service {item_id} uses port 8000.')
    f = correction(old=old_ids[0]) | {'replaces': old_ids}
    p = call([f], mode='preview', reason='All services changed.')
    before = snapshot(mem)
    bad = call(p['prepared_facts'], mode='apply', reason='All services changed.', expected_revisions=p['expected_revisions'], request_id='no-confirm')
    assert bad['code'] == 'impacts_confirmation_required' and snapshot(mem) == before
    r = call(p['prepared_facts'], mode='apply', reason='All services changed.', expected_revisions=p['expected_revisions'],
             preview_digest=p['preview_digest'], confirmed_impacts=old_ids, request_id='confirmed')
    assert len(r['impacts']) == len(r['superseded']) == n


def test_implicit_single_identity_requires_preview_even_for_one_impact(mem):
    ident = {'subject_id': 'service:a', 'predicate': 'port', 'cardinality': 'single'}
    rev = seed(mem, identity=ident)
    f = correction() | {'identity': ident, 'replaces': []}
    before = snapshot(mem)
    r = call([f], reason='Change.', expected_revisions={'old': rev}, request_id='implicit')
    assert r['code'] == 'impacts_confirmation_required' and snapshot(mem) == before
    p = call([f], mode='preview', reason='Change.')
    r = call(p['prepared_facts'], reason='Change.', expected_revisions=p['expected_revisions'], preview_digest=p['preview_digest'], confirmed_impacts=['old'], request_id='implicit-preview')
    assert r['superseded'] == [['old', 'new']]


@pytest.mark.parametrize('tamper', ['facts', 'reason', 'confirmed_impacts', 'instance'])
def test_preview_binding_tampering_refused(mem, tamper):
    seed(mem)
    p = call(mode='preview', reason='Change.')
    args = dict(mode='apply', reason='Change.', expected_revisions=p['expected_revisions'],
                preview_digest=p['preview_digest'], confirmed_impacts=['old'], request_id='tampered')
    facts = deepcopy(p['prepared_facts'])
    if tamper == 'facts':
        facts[0]['text'] = 'Different value.'
    elif tamper == 'reason':
        args['reason'] = 'Different rationale.'
    elif tamper == 'confirmed_impacts':
        args['confirmed_impacts'] = []
    else:
        mem.store.meta['store_instance_id'] = 'another-instance'
    before = snapshot(mem)
    r = call(facts, **args)
    assert r['ok'] is False and r['outcome'] == 'not_started'
    assert snapshot(mem) == before


def test_legacy_replacement_warns_and_has_actual_impacts(mem):
    seed(mem)
    r = call()
    assert r['notices'] == ['correction_unverified']
    assert len(r['impacts']) == len(r['superseded']) == 1
    assert r['changed_by']['authenticated'] is False
    assert mem.store.get('new').correction['verified'] is False


def test_legacy_over_three_is_explicitly_refused_before_write(mem):
    ids = [f'old-{i}' for i in range(4)]
    for item_id in ids:
        seed(mem, item_id=item_id, text=f'Old unique {item_id}.')
    before = snapshot(mem)
    r = call([correction() | {'replaces': ids}])
    assert r['code'] == 'impacts_confirmation_required' and snapshot(mem) == before


@pytest.mark.parametrize('mode', ['preview', 'apply'])
def test_jsonl_controlled_refuses_and_legacy_keeps_behavior(tmp_path, monkeypatch, mode):
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend='jsonl', embed_model='hash', hash_dim=16)) as m:
        monkeypatch.setattr(server, '_engine', m)
        seed(m)
        before = snapshot(m)
        r = call(mode=mode)
        assert r['code'] == 'backend_unsupported' and r['outcome'] == 'not_started'
        assert snapshot(m) == before
        r = call()
        assert r['superseded'] == [['old','new']] and r['notices'] == ['correction_unverified']


@pytest.mark.parametrize('tool', ['memory_add_facts', 'memory_chat_save'])
def test_cold_jsonl_refusal_does_not_construct_engine(monkeypatch, tool):
    monkeypatch.setattr(server, '_engine', None)
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', 'jsonl')
    monkeypatch.setattr(server, 'get_engine', lambda: (_ for _ in ()).throw(AssertionError('Must refuse before engine')))
    args = {'mode': 'preview', 'facts': [correction()]}
    if tool == 'memory_chat_save':
        args.update(project_label='P', messages=[{'role':'assistant','content':'Synthetic.'}])
    else:
        args['project'] = 'P'
    assert server.dispatch(tool, args)['code'] == 'backend_unsupported'


def test_chat_preview_and_apply_share_controls_before_any_episode(mem):
    seed(mem)
    args = {'project_label':'P','messages':[{'role':'assistant','content':'Context.'}], 'summary':'Summary.',
            'facts':[correction()],'reason':'Change.','mode':'preview'}
    before = snapshot(mem)
    p = server.dispatch('memory_chat_save', args)
    assert p['ok'] and snapshot(mem) == before
    apply = args | {'mode':'apply','facts':p['prepared_facts'],'expected_revisions':p['expected_revisions'],
                    'preview_digest':p['preview_digest'],'confirmed_impacts':['old'],'request_id':'chat'}
    bad = server.dispatch('memory_chat_save', apply | {'messages':[{'role':'assistant','content':'Tampered.'}]})
    assert bad['code'] == 'impact_mismatch' and snapshot(mem) == before
    r = server.dispatch('memory_chat_save', apply)
    assert r['episodes']==1 and len(r['facts']['impacts'])==1 and r['impacts']==r['facts']['impacts']


def test_direct_python_controlled_apply_has_idempotent_receipt(mem):
    rev = seed(mem)
    kwargs = {'project':'P','reason':'Change.','expected_revisions':{'old':rev},'request_id':'facade'}
    first = mem.add_facts([correction()], **kwargs)
    before = snapshot(mem)
    assert mem.add_facts([correction()], **kwargs) == first and snapshot(mem) == before


@pytest.mark.parametrize('backend', ['sqlite_s1', 'jsonl'])
def test_partial_legacy_batch_never_hides_committed_invalidation(tmp_path, monkeypatch, backend):
    if backend == 'sqlite_s1' and sqlite3.sqlite_version_info < MIN_SQLITE:
        pytest.skip('Unqualified SQLite runtime')
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend=backend, embed_model='hash', hash_dim=16)) as m:
        monkeypatch.setattr(server, '_engine', m)
        seed(m)
        seed(m, 'other', project='Q', text='Other project.')
        before = snapshot(m)
        r = call([correction(), correction(old='other', new='bad')])
        assert r['ok'] is False
        if backend == 'jsonl':
            assert r['outcome']=='uncertain' and r['code']=='legacy_partial'
            assert len(r['impacts'])==1 and r['changed_by']['authenticated'] is False
            assert m.store.get('old').invalidated_by=='new'
        else:
            assert snapshot(m)==before


def test_provenance_negotiation_does_not_break_old_server():
    from mcp.types import Tool
    from memory_os_v4.ops.relay import SessionToolCatalog
    c=SessionToolCatalog()
    c._tools=[Tool(name='memory_add_facts',inputSchema={'type':'object','properties':{}})]
    assert c.fact_client_supported('memory_add_facts') is False
    c._tools[0].inputSchema['properties']['client_provenance']={}
    assert c.fact_client_supported('memory_add_facts') is True


@SQL
def test_genuinely_cold_preview_never_constructs_model(tmp_path, monkeypatch):
    from memory_os_v4 import memory
    monkeypatch.setattr(memory, '_make_embedder', lambda cfg: (_ for _ in ()).throw(AssertionError('No model')))
    with MemoryV4(config=Config(data_dir=str(tmp_path), store_backend='sqlite_s1', embed_model='unavailable-model'), defer_retrieval=True) as m:
        monkeypatch.setattr(server, '_engine', m)
        m.store.append(MemoryItem(id='old',kind='fact',text='Old.',project='P',event_time=100,valid_from=100))
        before=snapshot(m)
        r=call(mode='preview',reason='Change.')
        assert r['ok'] and not m._retrieval_ready and snapshot(m)==before


@SQL
def test_lazy_preview_defers_model_without_enabling_tasks(tmp_path, monkeypatch):
    from memory_os_v4 import memory
    monkeypatch.setattr(server, '_engine', None)
    monkeypatch.setenv('MEMORY_V4_DIR',str(tmp_path))
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND','sqlite_s1')
    monkeypatch.setenv('MEMORY_V4_EMBED_MODEL','unavailable-model')
    monkeypatch.setenv('MEMORY_V4_WORK_STATE','0')
    monkeypatch.setattr(memory,'_make_embedder',lambda cfg: (_ for _ in ()).throw(AssertionError('No model')))
    try:
        r=call([{'text':'New ordinary fact.'}],mode='preview')
        assert r['ok'] and not server._engine._retrieval_ready and not server._engine.config.work_state
        assert server._engine.data_dir==tmp_path
    finally:
        if server._engine is not None:
            server._engine.close()


@pytest.mark.parametrize('when', ['2026-10-11T10:00:00Z','2026-10-12T10:00:00Z'])
def test_controlled_equal_or_earlier_time_refused_without_writes(mem, when):
    rev=seed(mem)
    before=snapshot(mem)
    r=call([correction()|{'event_time':when}],reason='Change.',expected_revisions={'old':rev},request_id='time')
    assert r['code']=='invalid_correction_time' and r['outcome']=='not_started' and snapshot(mem)==before


def test_absent_audit_is_not_injected_into_plain_authority(mem):
    seed(mem)
    assert 'correction' not in mem.store.raw_history('old')[-1]
    server.dispatch('memory_search',{'project':'P','query':'port'})
    mem.flush()
    assert 'correction' not in mem.store.raw_history('old')[-1]


def test_historical_identity_insertion_preview_and_apply_match(mem):
    ident={'subject_id':'service:a','predicate':'port','cardinality':'single'}
    mem.add_facts([{'item_id':'early','text':'Early value.','event_time':100,'identity':ident}],project='P')
    mem.add_facts([{'item_id':'later','text':'Later value.','event_time':300,'identity':ident}],project='P')
    f={'item_id':'middle','text':'Middle value.','event_time':'1970-01-01T00:03:20Z','identity':ident}
    p=call([f],mode='preview',reason='Dated middle state.')
    assert len(p['impacts'])==2 and p['expected_revisions'].keys()=={'early'}
    r=call(p['prepared_facts'],mode='apply',reason='Dated middle state.',expected_revisions=p['expected_revisions'],
           confirmed_impacts=['early','middle'],preview_digest=p['preview_digest'],request_id='history')
    assert len(r['impacts'])==2 and mem.store.get('early').valid_until==200 and mem.store.get('middle').valid_until==300
