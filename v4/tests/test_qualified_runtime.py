"""Runtime contract tests: metadata only, no model/dependency download."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from memory_os_v4.ops import qualified_runtime as runtime

ROOT = Path(__file__).resolve().parents[2]


def test_pip_resolution_gate_accepts_lock_and_rejects_actual_failure_shape():
    import importlib.util
    spec = importlib.util.spec_from_file_location('resolution_gate', ROOT / 'scripts/check_runtime_resolution.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    expected = {**runtime.expected_versions(extras=('ops', 'brief')), 'memory-os-ai': '4.0.6'}
    report = {'environment': {'sys_platform': 'darwin', 'platform_machine': 'arm64', 'python_version': '3.11'},
              'install': [{'metadata': {'name': n, 'version': v}} for n, v in expected.items()]}
    assert gate.check(report, '4.0.6')['ok']
    report['install'] = [item for item in report['install'] if item['metadata']['name'] != 'mcp']
    report['install'] += [{'metadata': {'name': 'mcp', 'version': '2.3.0'}},
                          {'metadata': {'name': 'mcp-types', 'version': '2.3.0'}}]
    proof = gate.check(report, '4.0.6')
    assert not proof['ok'] and 'mcp' in proof['mismatches'] and 'mcp-types' in proof['unexpected_packages']
    report['install'].append(report['install'][0])
    assert not gate.check(report, '4.0.6')['ok']


def test_closure_generator_obeys_markers_extras_and_specifiers():
    import importlib.util
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('closure_generator', ROOT / 'scripts/seal_runtime_closure.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = {'sys_platform': 'darwin', 'python_version': '3.11'}
    def distribution(name):
        requires = ['b>=1; extra == "crypto"', 'windows; sys_platform == "win32"'] if name == 'a' else []
        return SimpleNamespace(version='1.0', requires=requires, read_text=lambda *a: 'Synthetic METADATA')
    assert set(module.closure(['a[crypto]>=1'], env, distribution)) == {'a', 'b'}
    assert set(module.closure(['a>=1'], env, distribution)) == {'a'}
    with pytest.raises(ValueError, match='does not satisfy'):
        module.closure(['a>=2'], env, distribution)


def test_resolution_gate_attests_exact_wheel_metadata_hash_and_full_plan(tmp_path):
    import hashlib
    import importlib.util
    import zipfile
    spec = importlib.util.spec_from_file_location('resolution_wheel_gate', ROOT / 'scripts/check_runtime_resolution.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    wheel = tmp_path / 'fixture.whl'
    values = runtime.expected_versions(extras=('ops', 'brief'))
    metadata = 'Name: memory-os-ai\nVersion: 4.0.6\n' + ''.join(f'Requires-Dist: {n}=={v}\n' for n, v in values.items())
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('memory_os_ai-4.0.6.dist-info/METADATA', metadata)
    report = {'environment': {'sys_platform': 'darwin', 'platform_machine': 'arm64', 'python_version': '3.11'},
              'install': [{'metadata': {'name': n, 'version': v}} for n, v in values.items()]}
    package = {'metadata': {'name': 'memory-os-ai', 'version': '4.0.6'},
               'download_info': {'archive_info': {'hashes': {'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}}}}
    report['install'].append(package)
    assert gate.check(report, '4.0.6', wheel=wheel)['ok']
    package['download_info']['archive_info']['hashes']['sha256'] = '0'*64
    assert not gate.check(report, '4.0.6', wheel=wheel)['ok']
    package['download_info']['archive_info']['hashes']['sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    report['environment']['python_version'] = '3.12'
    assert not gate.check(report, '4.0.6', wheel=wheel)['ok']


def test_network_gate_uses_guarded_dry_run_private_home_and_no_credentials(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import zipfile
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('network_gate', ROOT / 'scripts/qualify_runtime_resolution.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    values = runtime.expected_versions(extras=('ops', 'brief'))
    wheel = tmp_path / 'fixture.whl'
    metadata = 'Name: memory-os-ai\nVersion: 4.0.6\n' + ''.join(f'Requires-Dist: {n}=={v}\n' for n, v in values.items())
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('memory_os_ai-4.0.6.dist-info/METADATA', metadata)
    (tmp_path / 'pyproject.toml').write_text('[project]\nversion = "4.0.6"\n')
    output = tmp_path / '.local-qualification/resolution'
    report = {'environment': {'sys_platform': 'darwin', 'platform_machine': 'arm64', 'python_version': '3.11'},
              'install': [{'metadata': {'name': n, 'version': v}} for n, v in {**values, 'memory-os-ai': '4.0.6'}.items()]}
    report['install'][-1]['download_info'] = {'archive_info': {'hashes': {'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()}}}
    called = []
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    monkeypatch.setattr(gate, 'guard', lambda p: called.append('guard'))
    monkeypatch.setattr(gate.os, 'setpriority', lambda *a: None)
    monkeypatch.setenv('TRIAL_SECRET', 'synthetic-canary')
    def command(argv, cwd, env, log, monitor):
        assert called == ['guard']
        assert '--dry-run' in argv and '--ignore-installed' in argv and '--only-binary=:all:' in argv
        assert 'TRIAL_SECRET' not in env and env['PIP_CONFIG_FILE'] == gate.os.devnull
        assert Path(env['HOME']) == output / 'home' and monitor is gate.guard
        (output / 'pip-report.json').write_text(json.dumps(report))
        return {'simulated': True}
    monkeypatch.setattr(gate, 'command', command)
    monkeypatch.setattr(sys, 'argv', ['tool', '--wheel', str(wheel), '--output', str(output)])
    assert gate.main() == 0
    assert json.loads((output / 'report.json').read_text())['installed'] is False


def test_project_pins_match_shipped_qualified_profile():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    assert runtime.check_requirements(project['dependencies'])['ok']
    assert runtime.catalog()['versions']['torch'] == '2.10.0'
    assert runtime.catalog()['versions']['sentence-transformers'] == '5.2.3'
    assert runtime.check_requirements(project['dependencies'] + project['optional-dependencies']['ops'] +
                                      project['optional-dependencies']['brief'], extras=('ops', 'brief'))['ok']


def test_shipped_closure_and_constraints_match_ledger():
    resource = runtime.PROFILE.parent
    sealed = json.loads((resource / 'qualified_runtime_closure_v1.json').read_text())
    assert sealed['versions'] == runtime.catalog()['versions']
    assert sealed['extra_versions'] == runtime.catalog()['extra_versions']
    actual = dict(line.split('==') for line in (resource / 'qualified_runtime_constraints_v1.txt').read_text().splitlines()
                  if line and not line.startswith('#'))
    assert actual == runtime.expected_versions(extras=('ops', 'brief'))
    assert sealed['source_package_version'] == '4.0.5'
    assert 'mcp-types' not in actual and actual['mcp'] == '1.26.0'


def test_transport_and_optional_drift_refused_without_model():
    values = runtime.expected_versions(extras=('ops', 'brief'))
    for name, version in [('mcp', '2.3.0'), ('cryptography', '50.0.2'), ('tiktoken', '0.13.0')]:
        proof = runtime.compare({**values, name: version}, extras=('ops', 'brief'))
        assert not proof['ok'] and set(proof['mismatches']) == {name}


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
    server_values = runtime.expected_versions(extras=('ops', 'brief'))
    info['capabilities']['runtime'] = {'actual': server_values}
    assert next(c for c in doctor._capabilities(info) if c.area == 'server-runtime').status == 'PASS'
    info['capabilities']['runtime']['actual'] = {**server_values, 'mcp': '2.3.0'}
    check = next(c for c in doctor._capabilities(info) if c.area == 'server-runtime')
    assert check.status == 'FAIL' and 'mcp' in check.detail


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
