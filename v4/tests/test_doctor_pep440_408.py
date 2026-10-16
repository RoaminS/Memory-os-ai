import json

import pytest

from memory_os_v4.ops import doctor
from memory_os_v4.ops.settings import OpsSettings


@pytest.mark.parametrize('version', ['4.0.7', '4.0.7rc1', '4.0.8.dev1', '4.0.7.post1', '4.0.7rc1+local'])
def test_server_doctor_accepts_pep440_attested_versions(tmp_path, monkeypatch, version):
    settings = OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=tmp_path/'token', store_dir=tmp_path/'store',
                           ops_dir=tmp_path/'ops', backup_dir=tmp_path/'backup', backup_key=tmp_path/'key', service_label=None)
    payload = {'ok':True, 'ready':True, 'version':version, 'commit':'a'*40, 'commit_source':'embedded_descriptor'}
    monkeypatch.setattr(doctor, 'http_get', lambda url, settings: (200, json.dumps(payload).encode()))
    monkeypatch.setattr(doctor, '_get_without_token', lambda url:401)
    assert next(c for c in doctor._server(settings) if c.area == 'ready').status == 'PASS'
    payload['commit'] = 'invalid'
    assert next(c for c in doctor._server(settings) if c.area == 'ready').status == 'FAIL'


@pytest.mark.parametrize('version', [None, 407, '', '4.0.7wat', ' 4.0.7', '4.0.7 ', '4.0.7; rm'])
def test_invalid_versions_refused(version):
    assert not doctor._pep440_version(version)
