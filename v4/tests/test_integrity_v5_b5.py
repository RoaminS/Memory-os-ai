import json
import time
import pytest
from memory_os_v4 import Config,MemoryV4,server
from memory_os_v4.changes import listing,prune,initialize,statistics
from memory_os_v4.corrections import CorrectionError

@pytest.fixture
def memory(tmp_path,monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',work_state=True,embed_model='hash',hash_dim=16)) as m:
        monkeypatch.setattr(server,'_engine',m)
        yield m


def count(m): return m.store._connection.execute('SELECT count(*) FROM change_events').fetchone()[0]


def test_feed_transaction_receipt_replay_read_and_noop(memory):
    m=memory
    args={'project':'P','facts':[{'item_id':'one','text':'Synthetic assertion.'}],'request_id':'add'}
    r=server.dispatch('memory_add_facts',args)
    assert r['change_event']['sequence']>0
    assert count(m)==1
    assert server.dispatch('memory_add_facts',args)==r and count(m)==1
    before=m.store._connection.execute('SELECT * FROM store_metadata').fetchall()
    first=listing(m,'P',limit=1)
    assert len(first['events'])==1 and not first['has_more']
    assert first['coverage']=='prospective' and first['next_cursor']
    assert listing(m,'P',first['next_cursor'])['events']==[]
    assert before==m.store._connection.execute('SELECT * FROM store_metadata').fetchall()
    dup=server.dispatch('memory_add_facts',{'project':'P','facts':[{'text':'Synthetic assertion.'}],'request_id':'dup'})
    assert dup['deduped'] and count(m)==2
    assert listing(m,'P',first['next_cursor'])['events'][0]['no_op']
    assert 'Synthetic assertion.' not in json.dumps(listing(m,'P'))
    assert m.store.check_integrity()


def test_scope_mac_restore_cursor(memory):
    m=memory
    m.add_facts([{'text':'Private P assertion.'}],project='P')
    m.add_facts([{'text':'Private Q assertion.'}],project='Q')
    p=listing(m,'P')
    assert all(e['project']=='P' for e in p['events'])
    with pytest.raises(CorrectionError,match='cursor_invalid'): listing(m,'Q',p['next_cursor'])
    with pytest.raises(CorrectionError): listing(m,'P',p['next_cursor'][:-2]+'zz')
    with pytest.raises(CorrectionError,match='cursor_invalid'): listing(m,'P','W10')
    initialize(m.store,reset=True)
    with pytest.raises(CorrectionError,match='identity_changed'): listing(m,'P',p['next_cursor'])
    assert count(m)==2


def test_preview_refusal_and_atomic_rollback_have_no_events(memory):
    m=memory
    m.add_facts([{'item_id':'old','text':'Original assertion.','event_time':time.time()-20}],project='P')
    before=count(m)
    result=server.dispatch('memory_add_facts',{'project':'P','mode':'preview','reason':'Inspect.','facts':[{'text':'Changed assertion.','replaces':['old']}]})
    assert result['ok'] and count(m)==before
    bad=server.dispatch('memory_add_facts',{'project':'P','mode':'apply','reason':'Inspect.','request_id':'bad','facts':result['prepared_facts']})
    assert bad['ok'] is False and count(m)==before
    def fail(point):
        if point=='after_change_event': raise OSError('synthetic failure before receipt')
    m.store.fault_hook=fail
    with pytest.raises(OSError): m.add_facts([{'item_id':'not-committed','text':'Rollback assertion.'}],project='P')
    m.store.fault_hook=None
    assert count(m)==before and m.store.get('not-committed') is None
    assert m.store.check_integrity()


def test_task_block_forget_journal_and_secret_reason(memory):
    m=memory
    t=server.dispatch('memory_task_create',{'project':'P','title':'Synthetic task','request_id':'task'})
    assert t['change_event'] and listing(m,'P')['events'][-1]['refs'][0]['kind']=='task'
    server.dispatch('memory_block_set',{'project':'P','name':'rules','text':'Synthetic rules','request_id':'block'})
    assert listing(m,'P')['events'][-1]['refs'][0]['kind']=='block'
    m.add_facts([{'item_id':'forget-me','text':'Synthetic fact.'}],project='P')
    assert m.forget('forget-me',reason='password=synthetic-secret-value',project='P')
    event=listing(m,'P')['events'][-1]
    assert event['reason']=='[redacted]' and event['refs'][0]['id']=='forget-me'
    assert not m.forget('absent',project='P')
    assert listing(m,'P')['events'][-1]==event


def test_retention_explicit_window_and_expired_cursor(memory,monkeypatch):
    m=memory
    initial=listing(m,'P')['next_cursor']
    monkeypatch.setattr(time,'time',lambda:1000000.)
    m.add_facts([{'text':'Old synthetic assertion.'}],project='P')
    monkeypatch.setattr(time,'time',lambda:5000000.)
    from memory_os_v4.maintenance import MaintenancePolicy
    m._maintenance_policy=MaintenancePolicy('apply')
    result=prune(m,before=2000000.,dry_run=True)
    assert result['eligible_events']==1 and count(m)==1
    prune(m,before=2000000.,dry_run=False)
    assert count(m)==0
    with pytest.raises(CorrectionError,match='cursor_expired'): listing(m,'P',initial)
    m.add_facts([{'text':'New synthetic assertion.'}],project='P')
    assert listing(m,'P')['events'][0]['sequence']==2


def test_history_validation_and_jsonl_refusal(tmp_path,monkeypatch):
    from memory_os_v4.models import TimelineIn
    from pydantic import ValidationError
    for args in ({},{'view':'changes'},{'view':'changes','project':'P','query':'hidden-filter'}):
        with pytest.raises(ValidationError): TimelineIn(**args)
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash')) as m:
        monkeypatch.setattr(server,'_engine',m)
        r=server.dispatch('memory_timeline',{'view':'changes','project':'P'})
        assert r['code']=='backend_unsupported'


def test_journal_stats_alert_and_health_render(memory,monkeypatch,tmp_path):
    from memory_os_v4 import changes
    from memory_os_v4.ops import health
    m=memory
    m.add_facts([{'text':'Synthetic stats.'}],project='P')
    monkeypatch.setattr(changes,'ALERT_BYTES',1)
    stats=statistics(m.store._connection)
    assert stats['alert'] and stats['events']==1
    from memory_os_v4.ops.settings import OpsSettings
    s=OpsSettings(url='http://127.0.0.1:9/mcp/',store_dir=m.data_dir,ops_dir=tmp_path/'ops',backup_dir=tmp_path/'backup',token_file=tmp_path/'token',backup_key=tmp_path/'key',service_label=None)
    monkeypatch.setattr(health,'http_get',lambda url,settings,**kw:(200,b'{}'))
    monkeypatch.setattr(health,'last_good_backup_age',lambda settings:0)
    monkeypatch.setattr(health,'last_drill',lambda settings:None)
    from memory_os_v4.ops import storage
    monkeypatch.setattr(storage,'margin',lambda settings:{'ok':True,'volumes':{'source':{'free_bytes':20_000_000_000}}})
    r=health.report(s)
    assert r['alerts'][0]['category']=='journal_size'
    assert 'journal_size' in health.render(r)


def test_changes_cold_without_work_state_and_read_only(tmp_path,monkeypatch):
    from memory_os_v4.maintenance import WritePolicy
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',embed_model='missing-no-download',work_state=False),defer_retrieval=True) as m:
        monkeypatch.setattr(server,'_engine',m)
        monkeypatch.setattr(m,'_write_policy',WritePolicy('read_only'))
        monkeypatch.setattr(m,'ensure_retrieval',lambda:(_ for _ in ()).throw(AssertionError('No model for feed')))
        before=m.store._connection.execute('SELECT * FROM store_metadata').fetchall()
        r=server.dispatch('memory_timeline',{'view':'changes','project':'P'})
        assert r['ok'] and r['events']==[]
        assert m.store._connection.execute('SELECT * FROM store_metadata').fetchall()==before
        assert not m._retrieval_ready
