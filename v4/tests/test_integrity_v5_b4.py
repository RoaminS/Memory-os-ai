import json,hashlib,time
from datetime import datetime,timezone
from pathlib import Path
import pytest
from memory_os_v4 import Config,MemoryV4,server
from memory_os_v4.compensation import secret_like
from memory_os_v4.corrections import revision

def test_sealed_secret_controls():
    path=Path('v4/tests/fixtures/integrity_v5/secret_like_b4_v1.json')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==path.with_suffix('.sha256').read_text().split()[0]
    for c in json.loads(path.read_text())['cases']: assert secret_like(c['text'])==c['secret_like'],c['id']

def test_compensation(tmp_path,monkeypatch):
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',embed_model='hash',hash_dim=16)) as m:
        monkeypatch.setattr(server,'_engine',m)
        m.add_facts([{'item_id':'old','text':'Service uses port 8000.','event_time':time.time()-120}],project='P')
        rev=revision(m.store.get('old'),m.store)
        f={'item_id':'new','text':'Service uses port 9000.','event_time':datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat(),'replaces':['old']}
        r=server.dispatch('memory_add_facts',{'project':'P','facts':[f],'reason':'Mistake.','expected_revisions':{'old':rev},'request_id':'first'})
        audit=r['audits'][0]['audit_id']
        comp={'text':'Service uses port 8000.','compensates':{'audit_id':audit,'restore_id':'old','restore_revision':rev},'replaces':['new']}
        p=server.dispatch('memory_add_facts',{'project':'P','facts':[comp],'mode':'preview','reason':'Restore correct port.'})
        assert p['ok'],p
        assert p['prepared_facts'][0]['compensates']==comp['compensates']
        a=server.dispatch('memory_add_facts',{'project':'P','facts':p['prepared_facts'],'mode':'apply','reason':'Restore correct port.','expected_revisions':p['expected_revisions'],'preview_digest':p['preview_digest'],'confirmed_impacts':['new'],'request_id':'comp'})
        assert 'error' not in a,a
        assert a['audits'][0]['compensates']==comp['compensates']
        assert m.store.get('old').invalidated_by=='new'
        assert m.store.get('new').invalidated_by==a['added'][0]

@pytest.mark.parametrize('alter', ['forgotten','sensitive','wrong_project','wrong_audit','wrong_revision','wrong_text','wrong_head','past','no_preview'])
def test_compensation_refusal_without_mutation(tmp_path,monkeypatch,alter):
    from copy import deepcopy
    with MemoryV4(config=Config(data_dir=str(tmp_path),store_backend='sqlite_s1',embed_model='hash',hash_dim=16)) as m:
        monkeypatch.setattr(server,'_engine',m)
        m.add_facts([{'item_id':'old','text':'Synthetic original assertion.','event_time':time.time()-120}],project='P')
        rev=revision(m.store.get('old'),m.store)
        r=server.dispatch('memory_add_facts',{'project':'P','facts':[{'item_id':'new','text':'Synthetic wrong assertion.','event_time':datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat(),'replaces':['old']}],'reason':'Synthetic change.','expected_revisions':{'old':rev},'request_id':'seed-change'})
        mark={'audit_id':r['audits'][0]['audit_id'],'restore_id':'old','restore_revision':rev}
        f={'text':'Synthetic original assertion.','replaces':['new'],'compensates':mark}
        project='P'
        if alter=='forgotten': m.forget('old')
        elif alter=='sensitive':
            old=deepcopy(m.store.get('old'));old.tags=['sensitive'];m.store.append(old)
        elif alter=='wrong_project': project='Q'
        elif alter=='wrong_audit': mark['audit_id']='a'*64
        elif alter=='wrong_revision': mark['restore_revision']='b'*64
        elif alter=='wrong_text': f['text']='Arbitrary different text.'
        elif alter=='wrong_head': f['replaces']=['old']
        elif alter=='past': f['event_time']=datetime.fromtimestamp(time.time()-301,timezone.utc).isoformat()
        args={'project':project,'facts':[f],'reason':'Restore.'}
        if alter!='no_preview': args['mode']='preview'
        before=m.store._connection.execute('SELECT * FROM item_versions').fetchall()
        result=server.dispatch('memory_add_facts',args)
        assert result.get('ok') is False,result
        assert result['code'] in {'source_not_restorable','invalid_correction_time','compensation_preview_required'},result
        assert m.store._connection.execute('SELECT * FROM item_versions').fetchall()==before


def test_compensation_not_accepted_in_chat_and_jsonl(tmp_path,monkeypatch):
    from memory_os_v4.models import ChatSaveIn
    from pydantic import ValidationError
    comp={'text':'Synthetic.','compensates':{'audit_id':'a'*64,'restore_id':'old','restore_revision':'b'*64}}
    with pytest.raises(ValidationError): ChatSaveIn(messages=[{'role':'user','content':'Synthetic'}],facts=[comp])
    with MemoryV4(config=Config(data_dir=str(tmp_path),embed_model='hash',hash_dim=16)) as m:
        monkeypatch.setattr(server,'_engine',m)
        result=server.dispatch('memory_add_facts',{'project':'P','facts':[comp],'mode':'preview'})
        assert result['code']=='backend_unsupported'
        assert m.store.all()==[]
