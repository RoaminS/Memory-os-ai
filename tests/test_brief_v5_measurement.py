"""Pure protocol/measurement tests, no performance run, TCP, or real machine guard."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('brief_v5_perf',ROOT/'scripts/qualify_brief_v5_performance.py')
perf=importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)


def test_measurement_uses_sealed_parameters_and_frozen_query_sequence():
    protocol,digest=perf.sealed_protocol()
    assert len(digest)==64
    assert protocol['performance']['seed_items']==10000
    assert protocol['performance']['warmups']==10
    assert protocol['performance']['sequential_calls']==100
    assert [perf.query(i) for i in range(10)]==[perf.query(i+10) for i in range(10)]
    assert perf.stats(list(range(100))) == {'p50':50,'p95':94,'max':99}


def test_measurement_refuses_tool_errors_and_oversized_envelopes():
    with pytest.raises(ValueError,match='tool error'):
        perf.measure_result(SimpleNamespace(isError=True),len)
    from memory_os_v4.brief import envelope
    with pytest.raises(ValueError,match='budget exceeded'):
        perf.measure_result(envelope({'text':'x'*7000,'wire_format':'compact_v2'}),len)


def test_measurement_refuses_dirty_protocol_seal(tmp_path,monkeypatch):
    monkeypatch.setattr(perf,'FIXTURE',tmp_path)
    (tmp_path/'protocol_v1.json').write_text('{}')
    (tmp_path/'protocol_v1.sha256').write_text('0'*64)
    with pytest.raises(ValueError,match='seal mismatch'):
        perf.sealed_protocol()


@pytest.mark.parametrize('value', [None, '', '/operator/registry.json'])
def test_private_environment_unsets_registry_without_mutating_parent(value):
    parent={'MEMORY_V4_BUILD_COMMIT':'a'*40,'HF_HUB_OFFLINE':'1'}
    if value is not None:
        parent['MEMORY_V4_PROJECT_REGISTRY']=value
    before=dict(parent)
    child=perf.without_project_registry(parent)
    assert 'MEMORY_V4_PROJECT_REGISTRY' not in child
    assert child=={k:v for k,v in before.items() if k!='MEMORY_V4_PROJECT_REGISTRY'}
    assert parent==before


def invoke_worker_entry(tmp_path,monkeypatch,arm,stub):
    # main() is a CLI entry point: unlike a subprocess, this test shares the
    # caller's environment. Restore *all* keys, including direct updates/pop
    # inside main(), on success and on failure.
    source=tmp_path/arm
    with monkeypatch.context() as isolated:
        isolated.setattr(sys,'argv',['tool','--worker-source',str(source),'--output',str(tmp_path/'out'),
                                    '--table',str(tmp_path/'table')])
        isolated.setattr(perf,'worker',stub)
        with patch.dict(os.environ, {'MEMORY_V4_PROJECT_REGISTRY':''}):
            return perf.main()


@pytest.mark.parametrize('arm', ['baseline','candidate'])
def test_worker_entry_clears_registry_before_worker_or_engine_import(tmp_path,monkeypatch,arm):
    before=dict(os.environ)
    observed=[]
    def stub(*args):
        assert 'MEMORY_V4_PROJECT_REGISTRY' not in os.environ
        assert os.environ['MEMORY_V4_CL100K_PATH']==str((tmp_path/'table').resolve())
        observed.append(args[0])
    assert invoke_worker_entry(tmp_path,monkeypatch,arm,stub)==0
    assert observed==[tmp_path/arm]
    assert dict(os.environ)==before


@pytest.mark.parametrize('order', [('worker','consumer'),('consumer','worker')])
@pytest.mark.parametrize('fails', [False,True])
def test_worker_entry_restores_environment_in_both_orders_even_on_failure(tmp_path,monkeypatch,order,fails):
    qualified_table=tmp_path/'qualified-table'
    qualified_table.write_bytes(b'inherited-table-fixture')
    monkeypatch.setenv('MEMORY_V4_CL100K_PATH',str(qualified_table))
    monkeypatch.setenv('MEMORY_V4_PROJECT_REGISTRY','/operator/registry.json')
    monkeypatch.setenv('HF_HUB_OFFLINE','operator-value')
    monkeypatch.delenv('TRANSFORMERS_OFFLINE',raising=False)
    monkeypatch.delenv('PYTHONDONTWRITEBYTECODE',raising=False)
    before=dict(os.environ)
    def stub(*args):
        assert 'MEMORY_V4_PROJECT_REGISTRY' not in os.environ
        for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','PYTHONDONTWRITEBYTECODE'):
            assert os.environ[key]=='1'
        if fails:
            raise RuntimeError('worker failed')
    def consumer():
        assert dict(os.environ)==before
        assert Path(os.environ['MEMORY_V4_CL100K_PATH']).read_bytes()==b'inherited-table-fixture'
    for step in order:
        if step=='consumer':
            consumer()
        elif fails:
            with pytest.raises(RuntimeError,match='worker failed'):
                invoke_worker_entry(tmp_path,monkeypatch,'candidate',stub)
        else:
            assert invoke_worker_entry(tmp_path,monkeypatch,'candidate',stub)==0
        assert dict(os.environ)==before
    consumer()


@pytest.mark.parametrize('revision', ['fdd60cb','9bd4f73'])
def test_real_baseline_and_candidate_registry_loader_with_clean_environment(tmp_path,revision):
    # No engine/encoder/server or resource workload: reproduce the failing
    # Config.from_env -> ProjectRegistry.load path against the actual sources.
    source=tmp_path/'source'
    source.mkdir()
    archive=subprocess.run(['git','archive',revision,'v4/src'],cwd=ROOT,
                           capture_output=True,check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(source,filter='data')
    parent={**os.environ,'PYTHONPATH':str(source/'v4/src'),'MEMORY_V4_PROJECT_REGISTRY':'',
            'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    program='''
from memory_os_v4.config import Config
from memory_os_v4.projects import ProjectRegistry
c=Config.from_env()
assert c.project_registry is None
assert ProjectRegistry.load(c.project_registry).document is None
'''
    result=subprocess.run([sys.executable,'-B','-c',program],cwd=tmp_path,
                          env=perf.without_project_registry(parent),capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('revision', ['fdd60cb', 'HEAD'])
def test_frozen_arm_authenticated_readiness_before_measurement(tmp_path, revision):
    source=tmp_path/'arm-source'
    perf.export_arm(revision,source)
    commit=subprocess.run(['git','rev-parse',revision],cwd=ROOT,capture_output=True,
                          text=True,check=True).stdout.strip()
    assert (source/'pyproject.toml').is_file()
    assert not (source/'.git').exists()
    assert not (source/'v4/src/memory_os_v4/_build_identity.json').exists()
    token=tmp_path/'token'
    token.write_text('synthetic-pa-ready-'+'x'*40)
    token.chmod(0o600)
    env=perf.without_project_registry({**os.environ,'PYTHONPATH':str(source/'v4/src'),
        'MEMORY_V4_BUILD_COMMIT':commit,'MEMORY_V4_TOKEN_FILE':str(token),
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','PYTHONDONTWRITEBYTECODE':'1'})
    # No TCP, tokenizer, 10k seed or model: real frozen /ready, empty disposable
    # hash store, then reproduce the missing-pyproject failure as a negative control.
    program='''
import importlib.util
from pathlib import Path
import sys
import anyio
from memory_os_v4 import Config, MemoryV4, server
spec=importlib.util.spec_from_file_location('harness',sys.argv[1])
harness=importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
token=Path(sys.argv[2]).read_text()
with MemoryV4(config=Config(data_dir=sys.argv[3],store_backend='sqlite_s1',
                          work_state=True,embed_model='hash',hash_dim=16)) as engine:
    engine.ensure_retrieval()
    server.set_engine(engine)
    def forbidden(*args,**kwargs):
        raise AssertionError('Readiness must not encode')
    engine.embedder.encode=forbidden
    try:
        data=anyio.run(harness.local_ready,server,token,sys.argv[4])
        assert data['ready'] is True and data['identity_source']=='env_declared'
        project=Path(sys.argv[5])/'pyproject.toml'
        content=project.read_bytes()
        project.unlink()
        try:
            try:
                anyio.run(harness.local_ready,server,token,sys.argv[4])
            except ValueError as exc:
                assert 'build_unqualified' in str(exc)
            else:
                raise AssertionError('Missing source layout must refuse readiness')
        finally:
            project.write_bytes(content)
    finally:
        server.set_engine(None)
'''
    result=subprocess.run([sys.executable,'-B','-c',program,str(ROOT/'scripts/qualify_brief_v5_performance.py'),
                           str(token),str(tmp_path/'store'),commit,str(source)],cwd=tmp_path,
                          env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('status,patch', [(503,{}),(200,{'ready':False}),
    (200,{'commit':'b'*40}),(200,{'identity_source':'unqualified'})])
def test_measurement_requires_ready_and_the_arm_identity(status,patch):
    data={'ready':True,'commit':'a'*40,'identity_source':'env_declared',**patch}
    with pytest.raises(ValueError,match='readiness refused'):
        perf.require_ready(status,data,'a'*40)
