"""D1c refusals and TCP launch arguments, runnable without sockets/models."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from memory_os_v4.ops import cli
from memory_os_v4.ops.settings import OpsSettings


@pytest.mark.parametrize('failure', ['malformed-commit', 'identity-conflict', 'bad-config'])
def test_setup_s4_refuses_before_token_or_any_file_write(tmp_path, monkeypatch, capsys, failure):
    from memory_os_v4 import readiness
    settings = OpsSettings('http://127.0.0.1:9/mcp/', tmp_path/'config/token', tmp_path/'store',
                           tmp_path/'backup', tmp_path/'key', tmp_path/'ops', 'synthetic')
    monkeypatch.setenv('MEMORY_V4_STORE_BACKEND', 'sqlite_s1')
    monkeypatch.setenv('MEMORY_V4_BUILD_COMMIT', 'synthetic-sensitive-invalid')
    if failure == 'identity-conflict':
        monkeypatch.setenv('MEMORY_V4_BUILD_COMMIT', 'a'*40)
        def conflict():
            raise ValueError('synthetic-sensitive-identity-conflict')
        monkeypatch.setattr(readiness, 'build_identity', conflict)
    elif failure == 'bad-config':
        from memory_os_v4 import Config
        monkeypatch.setenv('MEMORY_V4_BUILD_COMMIT', 'a'*40)
        def invalid_config():
            raise ValueError('synthetic-sensitive-config-invalid')
        monkeypatch.setattr(Config, 'from_env', invalid_config)
    def no_token(*args):
        pytest.fail('Token creation must not be attempted on refused setup')
    monkeypatch.setattr(cli, 'cmd_token', no_token)
    from memory_os_v4.ops import auth
    monkeypatch.setattr(auth, 'create_token', no_token)
    before = sorted(tmp_path.rglob('*'))
    assert cli.cmd_setup(SimpleNamespace(sqlite_s4_jobs=True, write_launchd=None), settings) == 2
    assert sorted(tmp_path.rglob('*')) == before and not settings.token_file.exists()
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not result['ok'] and result['code'] == 'sqlite_s4_not_configured'
    assert 'synthetic-sensitive' not in output.out + output.err


@pytest.mark.parametrize('warm', [False, True])
def test_tcp_harness_launches_effective_requested_mode_without_real_tcp(tmp_path, monkeypatch, warm):
    # Run the real inline child argv-building code, but replace process/HTTP/server.
    spec = importlib.util.spec_from_file_location('d1c_l3_harness', Path(__file__).with_name('test_l3_tcp.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    harness = module.Harness.__new__(module.Harness)  # no bind/real process
    harness.root = tmp_path
    harness.port = 43219
    harness.token = tmp_path/'token'
    module.create_token(harness.token)
    harness.env = {'MEMORY_V4_EMBED_MODEL': 'unavailable-fixture'}
    harness.stop = lambda: None
    processes, endpoints = [], []
    def popen(argv, **kwargs):
        processes.append(argv)
        return SimpleNamespace(poll=lambda: None)
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    def urlopen(request, **kwargs):
        endpoints.append(request.full_url)
        return Response()
    monkeypatch.setattr(module.subprocess, 'Popen', popen)
    monkeypatch.setattr(module.urllib.request, 'urlopen', urlopen)
    harness.start(warm=warm)
    assert endpoints == [f'http://127.0.0.1:43219/{"ready" if warm else "health"}']
    command = processes[0]
    assert command[-1] == ('warm' if warm else 'cold')
    from memory_os_v4 import server
    observed = []
    monkeypatch.setattr(server, 'run', lambda: observed.append(list(sys.argv)))
    monkeypatch.setattr(sys, 'argv', ['-c', *command[3:]])
    exec(compile(command[2], 'isolated-tcp-child', 'exec'), {'__name__': '__main__'})
    assert '--warm' in observed[0] if warm else '--warm' not in observed[0]
    assert observed[0][1:5] == ['--transport', 'http', '--port', '43219']
