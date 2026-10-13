#!/usr/bin/env python3
"""Sealed P-A synthetic 10k benchmark: paired sources, real private TCP/relay, no model."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'v4/tests/fixtures/brief_v5'
sys.path.insert(0, str(ROOT / 'v4/src'))


def sealed_protocol():
    raw = (FIXTURE / 'protocol_v1.json').read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != (FIXTURE / 'protocol_v1.sha256').read_text().split()[0]:
        raise ValueError('P-A protocol seal mismatch')
    return json.loads(raw), digest


def stats(values):
    ordered = sorted(values)
    return {name: ordered[min(len(ordered)-1, int((len(ordered)-1)*quantile+.5))]
            for name, quantile in [('p50',.50),('p95',.95),('max',1)]}


def guarded():
    from qualify_405_test_isolation import resource_sample
    return resource_sample(True)


def query(index):
    # Frozen same query order and scope for both sources; not validation gold.
    return {'project':'P', 'focus_query':f'review synthetic topic {index % 10}',
            'wire_format':'compact_v2', 'budget_tokens':1500}


def run_guarded(command, *, env, log):
    """Own process group; a guard/timeout also stops its private TCP/relay children."""
    process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
    try:
        deadline=time.monotonic()+900
        while process.poll() is None:
            guarded()
            if time.monotonic()>=deadline:
                raise TimeoutError('Private worker budget exceeded')
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return process.returncode
    finally:
        # No reuse of a PID after wait: group belongs only to this worker tree.
        try:
            os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL)
                process.wait(timeout=5)


def measure_result(result, count):
    if result.isError:
        raise ValueError('Brief tool error')
    data = json.loads(result.content[0].text)
    if data.get('ok') is False:
        raise ValueError('Brief unavailable')
    wire = json.dumps(result.model_dump(mode='json',by_alias=True,exclude_none=True),
                      ensure_ascii=False,separators=(',',':'))
    row = {'bytes':len(wire.encode()),'tokens':count(wire)}
    if row['bytes'] > 6000 or row['tokens'] > 1500:
        raise ValueError('Envelope budget exceeded')
    return row


def record(directory, rows, row):
    rows.append(row)
    with (directory/'measurements.jsonl').open('a') as output:
        output.write(json.dumps(row)+'\n')


def worker(source, directory, protocol):
    # This is a fresh interpreter; shadow all installed engine code by the
    # selected frozen export before imports. The harness itself stays identical.
    sys.path.insert(0, str(source / 'v4/src'))
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.store import MemoryItem
    from memory_os_v4.brief_tokens import counter
    from memory_os_v4.ops.auth import create_token, read_token
    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    count = counter()
    store = directory / 'store'
    cfg = Config(data_dir=str(store),store_backend='sqlite_s1',work_state=True,embed_model='hash',hash_dim=16)
    with MemoryV4(config=cfg) as seed:
        values=[]
        for i in range(protocol['performance']['seed_items']):
            tags=['decision'] if i%50==0 else ['constraint'] if i%100==1 else []
            values.append(MemoryItem(id=f'synthetic-{i:05}',kind='procedure' if i%10==0 else 'fact',
                text=f'Synthetic topic {i%10}: review the complete evidence for unit {i}. Keep the approved boundary.',
                project='P',tags=tags,created_at=1,event_time=i+1,importance=.5))
        seed.store.append_many(values)
    rows=[]
    with MemoryV4(config=cfg) as engine:
        engine.ensure_retrieval()  # Hash-only startup excluded from warm brief latency.
        server.set_engine(engine)
        def forbidden(*args, **kwargs):
            raise AssertionError('Brief encoded a text')
        engine.embedder.encode = forbidden
        try:
            for index in range(protocol['performance']['warmups']+protocol['performance']['sequential_calls']):
                sample=guarded()
                started=time.perf_counter()
                result=anyio.run(server.call_tool,'memory_session_brief',query(index))
                ms=(time.perf_counter()-started)*1000
                row=measure_result(result,count)
                if index>=protocol['performance']['warmups']:
                    record(directory, rows, {'path':'server','index':index-protocol['performance']['warmups'],
                                             'ms':ms,**row,'resources':sample})
        finally:
            server.set_engine(None)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    token=directory/'token'
    create_token(token)
    env={**os.environ,'PYTHONPATH':str(source/'v4/src'),'MEMORY_V4_DIR':str(store),
         'MEMORY_V4_STORE_BACKEND':'sqlite_s1','MEMORY_V4_WORK_STATE':'1','MEMORY_V4_EMBED_MODEL':'hash',
         'MEMORY_V4_HASH_DIM':'16','MEMORY_V4_TOKEN_FILE':str(token),'MEMORY_V4_OPS_DIR':str(directory/'ops'),
         'MEMORY_V4_RELAY_JOURNAL_DIR':str(directory/'ops/relay'),
         'MEMORY_V4_PROJECT_REGISTRY':'','MEMORY_V4_URL':f'http://127.0.0.1:{port}/mcp/',
         'MEMORY_V4_QUALIFICATION_METRICS':'0','MEMORY_V4_MAINTENANCE_MODE':'disabled',
         'MEMORY_V4_WRITE_MODE':'read_write','MEMORY_V4_CLIENT_ID':'P-A-synthetic'}
    code='''
import sys
from memory_os_v4 import server
engine=server.get_engine()
engine.ensure_retrieval()
def forbidden(*args,**kwargs):
    raise AssertionError("Brief encoded a text")
engine.embedder.encode=forbidden
sys.argv=[sys.argv[0],"--transport","http","--host","127.0.0.1","--port",sys.argv[1]]
server.run()
'''
    with (directory/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-B','-c',code,str(port)],cwd=directory,env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+60
            while True:
                guarded()
                try:
                    req=urllib.request.Request(f'http://127.0.0.1:{port}/ready',headers={'Authorization':'Bearer '+read_token(token)})
                    with urllib.request.urlopen(req,timeout=1) as response:
                        if response.status==200:
                            break
                except Exception:
                    pass
                if process.poll() is not None or time.monotonic()>=deadline:
                    raise ValueError('Private synthetic server not ready; inspect server.log')
                time.sleep(.05)
            async def transport():
                params=StdioServerParameters(command=sys.executable,args=['-B','-m','memory_os_v4.ops.relay'],env=env)
                async with stdio_client(params) as (r,w), ClientSession(r,w) as session:
                    await session.initialize()
                    for index in range(protocol['performance']['warmups']+protocol['performance']['sequential_calls']):
                        sample=guarded()
                        started=time.perf_counter()
                        with anyio.fail_after(10):
                            result=await session.call_tool('memory_session_brief',query(index))
                        ms=(time.perf_counter()-started)*1000
                        row=measure_result(result,count)
                        if index>=protocol['performance']['warmups']:
                            record(directory, rows, {'path':'transport','index':index-protocol['performance']['warmups'],
                                                     'ms':ms,**row,'resources':sample})
            anyio.run(transport)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    result={'complete':True,'neural_calls':0,'measurements':rows}
    for path in ('server','transport'):
        result[path]={field:stats([row[field] for row in rows if row['path']==path]) for field in ('ms','bytes','tokens')}
    (directory/'worker.json').write_text(json.dumps(result,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--table',type=Path,required=True)
    parser.add_argument('--worker-source',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args()
    protocol,digest=sealed_protocol()
    os.environ.update(MEMORY_V4_CL100K_PATH=str(args.table.resolve()),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',PYTHONDONTWRITEBYTECODE='1')
    if args.worker_source:
        worker(args.worker_source.resolve(),args.output.resolve(),protocol)
        return 0
    output=args.output.resolve()
    if not output.is_relative_to(ROOT/'.local-qualification'):
        parser.error('Fresh output in clone .local-qualification required')
    output.mkdir(parents=True,exist_ok=False)
    report={'ok':False,'protocol_sha256':digest,'production_modified':False,'inference':False,
            'claim':'synthetic warm brief latency only; no measured utility gain','workers':{}}
    try:
        if hasattr(os,'getpriority') and os.getpriority(os.PRIO_PROCESS,0)<15:
            os.nice(15-os.getpriority(os.PRIO_PROCESS,0))
        report['initial_resources']=guarded()
        from memory_os_v4.ops.qualified_runtime import inspect_current
        report['runtime']=inspect_current(extras=('ops','brief'))
        if not report['runtime']['ok']:
            raise ValueError('Qualified runtime required')
        sha=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip()
        dirty=subprocess.run(['git','status','--porcelain','--untracked-files=no'],cwd=ROOT,text=True,capture_output=True,check=True).stdout.strip()
        if dirty:
            raise ValueError('Dirty tracked source refused')
        report.update(source_commit=sha,baseline_commit=protocol['base_commit'],order=['baseline','candidate'],
                      machine={'platform':sys.platform,'uname':list(os.uname())},tokenizer_sha256=hashlib.sha256(args.table.read_bytes()).hexdigest())
        with tempfile.TemporaryDirectory(prefix='private-synthetic-',dir=output) as temporary:
            private=Path(temporary)
            baseline=private/'baseline-source'
            baseline.mkdir()
            archive=subprocess.run(['git','archive',protocol['base_commit'],'v4/src'],cwd=ROOT,capture_output=True,check=True).stdout
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(baseline,filter='data')
            for label,source,commit in [('baseline',baseline,protocol['base_commit']),('candidate',ROOT,sha)]:
                report['last_resources']=guarded()
                directory=private/label
                directory.mkdir()
                env={**os.environ,'MEMORY_V4_BUILD_COMMIT':commit}
                with (output/(label+'.log')).open('w') as log:
                    command=[sys.executable,'-B',str(Path(__file__).resolve()),'--worker-source',str(source),
                             '--table',str(args.table.resolve()),'--output',str(directory)]
                    try:
                        returncode=run_guarded(command,env=env,log=log)
                    finally:
                        for evidence in ('server.log','measurements.jsonl'):
                            if (directory/evidence).exists():
                                shutil.copy2(directory/evidence,output/(label+'-'+evidence))
                # Preserve failed worker logs but clean synthetic stores on exit.
                if returncode:
                    if (directory/'server.log').exists():
                        shutil.copy2(directory/'server.log',output/(label+'-server.log'))
                    raise ValueError(label+' worker failed; no restart in this folder')
                data=json.loads((directory/'worker.json').read_text())
                report['workers'][label]=data
                (output/(label+'-worker.json')).write_text(json.dumps(data,indent=2)+'\n')
        candidate=report['workers']['candidate']
        report['criterion_met']=(candidate['server']['ms']['p95']<=protocol['performance']['server_p95_ms']
                                 and candidate['transport']['ms']['p95']<=protocol['performance']['transport_p95_ms'])
        report['ok']=report['criterion_met']
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'ok':report['ok'],'error':report.get('error')}))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
