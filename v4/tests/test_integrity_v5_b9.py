"""B9 synthetic physical exits at every reached compensation write frontier."""
from collections import Counter
from datetime import datetime,timezone
import json
import os
import shutil
import subprocess
import sys
import time

from memory_os_v4 import Config,MemoryV4,server
from memory_os_v4.corrections import revision


def engine(path):
    return MemoryV4(config=Config(data_dir=str(path),store_backend='sqlite_s1',embed_model='hash',hash_dim=16))


def seed(directory):
    with engine(directory) as m:
        server._engine=m
        m.add_facts([{'item_id':'old','text':'Synthetic correct state.','event_time':time.time()-120}],project='P')
        rev=revision(m.store.get('old'),m.store)
        r=server.dispatch('memory_add_facts',{'project':'P','facts':[{'item_id':'new','text':'Synthetic incorrect state.','replaces':['old'],'event_time':datetime.fromtimestamp(time.time()-60,timezone.utc).isoformat()}],'expected_revisions':{'old':rev},'reason':'Synthetic incorrect update.','request_id':'correction'})
        p=server.dispatch('memory_add_facts',{'project':'P','facts':[{'item_id':'restored','text':'Synthetic correct state.','replaces':['new'],'compensates':{'audit_id':r['audits'][0]['audit_id'],'restore_id':'old','restore_revision':rev}}],'mode':'preview','reason':'Synthetic compensation.'})
        args={'project':'P','facts':p['prepared_facts'],'reason':'Synthetic compensation.','expected_revisions':p['expected_revisions'],'preview_digest':p['preview_digest'],'confirmed_impacts':['new'],'request_id':'compensation','mode':'apply'}
        return args,m.store.get('old').to_dict()


def test_every_compensation_frontier_survives_physical_exit(tmp_path,monkeypatch):
    original=tmp_path/'base';args,old=seed(original)
    args_file=tmp_path/'arguments.json';args_file.write_text(json.dumps(args))
    probe=tmp_path/'probe';shutil.copytree(original,probe)
    points=[]
    with engine(probe) as m:
        monkeypatch.setattr(server,'_engine',m)
        m.store.fault_hook=points.append
        result=server.dispatch('memory_add_facts',args)
        m.store.fault_hook=None
        assert result['added']==['restored']
    assert {'before_change_event','after_change_event','before_receipt','after_receipt','before_commit','after_commit'}<=set(points)
    worker='''
import json,os,sys
from pathlib import Path
from memory_os_v4 import Config,MemoryV4,server
path=Path(sys.argv[1]); point=sys.argv[2]; occurrence=int(sys.argv[3]); calls=0
m=MemoryV4(config=Config(data_dir=str(path),store_backend='sqlite_s1',embed_model='hash',hash_dim=16));server._engine=m
args=json.loads(Path(sys.argv[4]).read_text())
def fault(p):
 global calls
 if p==point:
  calls+=1
  if calls==occurrence: os._exit(91)
m.store.fault_hook=fault
r=server.dispatch('memory_add_facts',args)
print('ACK',json.dumps(r),flush=True)
os._exit(92)
'''
    occurrences=Counter()
    for index,point in enumerate(points):
        occurrences[point]+=1
        target=tmp_path/('crash-'+str(index));shutil.copytree(original,target)
        run=subprocess.run([sys.executable,'-B','-c',worker,str(target),point,str(occurrences[point]),str(args_file)],capture_output=True,text=True,timeout=20,env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','MEMORY_V4_EMBED_MODEL':'hash','PYTHONDONTWRITEBYTECODE':'1'})
        assert run.returncode==91,(point,run.stdout,run.stderr)
        assert 'ACK' not in run.stdout
        with engine(target) as reopened:
            assert reopened.store.check_integrity()
            assert reopened.store.get('old').to_dict()==old
            complete=reopened.requests.records.get((server._request_scope('memory_add_facts',{'project':'P'}),'compensation'))
            restored=reopened.store.get('restored')
            if point=='after_commit':
                assert complete and restored and complete['result']['added']==['restored']
                assert complete['result']['change_event']['event_id']==reopened.store._connection.execute('SELECT event_id FROM change_events ORDER BY sequence DESC LIMIT 1').fetchone()[0]
                assert reopened.store.get('new').invalidated_by=='restored'
                assert restored.correction['compensates']['restore_id']=='old'
            else:
                assert complete is None and restored is None
                assert reopened.store.get('new').valid_until is None
                assert reopened.store._connection.execute('SELECT count(*) FROM change_events').fetchone()[0]==2
        shutil.rmtree(target)
    (tmp_path/'matrix.json').write_text(json.dumps({'frontiers':len(points),'points':dict(occurrences),'acknowledged_lost':0,'partial_success':0,'replay_count':0},indent=2))
    server._engine=None
