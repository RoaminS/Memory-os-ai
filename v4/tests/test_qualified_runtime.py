"""Runtime contract tests: metadata only, no model/dependency download."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from memory_os_v4.ops import qualified_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]


def test_project_pins_match_shipped_qualified_profile():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    assert runtime.check_requirements(project['dependencies'])['ok']
    assert runtime.catalog()['versions']['torch'] == '2.10.0'
    assert runtime.catalog()['versions']['sentence-transformers'] == '5.2.3'


@pytest.mark.parametrize('name', list(runtime.catalog()['versions']))
def test_exact_guard_refuses_each_drift_even_patch_version(name):
    expected = runtime.catalog()['versions']
    proof = runtime.compare({**expected, name: expected[name] + '.1'})
    assert not proof['ok'] and set(proof['mismatches']) == {name}


def test_missing_package_is_explicit_and_unsafe_loose_metadata_rejected():
    def missing(name):
        raise runtime.metadata.PackageNotFoundError(name)
    proof = runtime.inspect_current(version_getter=missing)
    assert not proof['ok'] and all(v is None for v in proof['actual'].values())
    requirements = [name + '==' + v for name, v in runtime.catalog()['versions'].items()]
    requirements = [r for r in requirements if not r.startswith('torch==')] + ['torch>=2.10,<2.11']
    assert not runtime.check_requirements(requirements)['ok']


def test_metadata_inspection_never_imports_encoder_or_torch():
    code = '''import sys,json
from memory_os_v4.ops.qualified_runtime import inspect_current
print(json.dumps(inspect_current()))
assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['profile_id'] == runtime.catalog()['profile_id']


def test_doctor_checks_deployed_versions_not_client_versions(monkeypatch):
    from memory_os_v4.ops import doctor
    values = runtime.catalog()['versions']
    info = {'version': '4.0.5', 'commit': 'a'*40, 'encoding_profile': {'device': 'mps', 'runtime': values}}
    info['capabilities'] = {'schema_version': 1, 'engine': {'version': info['version'], 'commit': info['commit']},
        'store': {'backend': 'sqlite_s1'}, 'encoding': {'profile': info['encoding_profile']},
        'projects': {'registry_sha256': None}, 'brief': {}}
    assert next(c for c in doctor._capabilities(info) if c.area == 'encoder-runtime').status == 'PASS'
    info['encoding_profile']['runtime'] = {**values, 'numpy': '2.5.0'}
    check = next(c for c in doctor._capabilities(info) if c.area == 'encoder-runtime')
    assert check.status == 'FAIL' and 'numpy' in check.detail


@pytest.mark.parametrize('warm', [False, True])
def test_server_warm_honors_explicit_retrieval_request(monkeypatch, warm):
    from types import SimpleNamespace
    from memory_os_v4 import server
    import uvicorn
    calls = []
    engine = SimpleNamespace(ensure_retrieval=lambda: calls.append('retrieval'))
    monkeypatch.setattr(server, 'get_engine', lambda: calls.append('engine') or engine)
    monkeypatch.setattr(server, '_build_http_app', lambda *a: 'synthetic-app')
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **kw: calls.append('serve'))
    server._run_http('127.0.0.1', 43219, warm=warm)
    assert calls == (['engine', 'retrieval', 'serve'] if warm else ['serve'])


def test_warm_real_private_sqlite_hydrates_once_before_serving(tmp_path, monkeypatch):
    from memory_os_v4 import Config, MemoryV4, server, memory
    from memory_os_v4.embed import HashEmbedder
    import uvicorn
    calls = []
    cfg = Config(data_dir=str(tmp_path / 'sql'), store_backend='sqlite_s1', work_state=True,
                 embed_model='never-load-neural-fixture')
    with MemoryV4(config=cfg) as engine:
        assert not engine._retrieval_ready and engine.embedder is None
        def synthetic_factory(config):
            calls.append('synthetic-model')
            return HashEmbedder(dim=16)  # no model import/inference/download
        def serve(*args, **kwargs):
            assert engine._retrieval_ready
            calls.append('serve')
        monkeypatch.setattr(memory, '_make_embedder', synthetic_factory)
        monkeypatch.setattr(server, 'get_engine', lambda: engine)
        monkeypatch.setattr(server, '_build_http_app', lambda *a: 'synthetic-app')
        monkeypatch.setattr(uvicorn, 'run', serve)
        server._run_http('127.0.0.1', 43219, warm=True)
        server._run_http('127.0.0.1', 43219, warm=True)
        assert calls == ['synthetic-model', 'serve', 'serve']
