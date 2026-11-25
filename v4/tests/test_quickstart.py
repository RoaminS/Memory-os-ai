"""Tests for quickstart (target v4/tests/test_quickstart.py): fake HOME, no network, no real client."""
import json
import os
from pathlib import Path

import pytest

from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.provision import guard as provision_guard
from memory_os_v4.ops.provision import plan as provision_plan

PY = "/opt/venv/bin/python"
ENV = {"MEMORY_V4_URL": "http://127.0.0.1:8765/mcp/", "MEMORY_V4_TOKEN_FILE": "/x/http.token"}


@pytest.fixture(autouse=True)
def no_socket(monkeypatch):
    """preview() also reports the service port (8765 by default): never probe a real one from these tests."""
    monkeypatch.setattr(qs, "_port_state", lambda port, **kw: "free")


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b"")
            for p in sorted(root.rglob("*"))}


def write(home: Path, rel: str, data, mode=0o644) -> Path:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(p, mode)
    return p


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    write(h, ".cursor/mcp.json", {"mcpServers": {"other": {"command": "x", "env": {"API_KEY": "s3cret"}}}}, 0o600)
    write(h, "Library/Application Support/Code/User/mcp.json", {"servers": {}, "inputs": []})
    write(h, ".config/zed/settings.json", '{\n  // my comment\n  "theme": "One Dark"\n}')
    write(h, ".grok/config.toml", '[mcp_servers.memory-os-ai]\ncommand = "old"\nargs = []\n')
    write(h, ".codex/config.toml", 'model = "gpt"\n')
    return h


def test_preview_writes_nothing_and_classifies(home, tmp_path):
    before = tree(tmp_path)
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor", "vscode", "zed", "grok", "codex", "nope"))
    assert tree(tmp_path) == before                     # Q1: zero writes
    acts = {c["client"]: c["action"] for c in plan["clients"]}
    assert acts == {"cursor": "create", "vscode": "create", "zed": "refuse-jsonc-manual",
                    "grok": "refuse-toml-existing-manual", "codex": "create"}
    assert plan["unknown_clients"] == ["nope"] and plan["token"]["state"] == "absent"
    assert "s3cret" not in json.dumps(plan)             # foreign config never exposed


def test_apply_merges_only_our_key_and_keeps_mode(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor", "vscode", "codex"))
    res = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "j")
    st = {r["client"]: r["status"] for r in res["records"]}
    assert st == {"cursor": "applied", "vscode": "applied", "codex": "applied"}
    cur = json.loads((home / ".cursor/mcp.json").read_text())
    assert cur["mcpServers"]["other"]["env"]["API_KEY"] == "s3cret"          # other entry intact
    assert cur["mcpServers"]["memory-os-ai"]["args"] == ["-m", "memory_os_v4.ops.relay"]
    assert (home / ".cursor/mcp.json").stat().st_mode & 0o777 == 0o600       # mode kept
    vs = json.loads((home / "Library/Application Support/Code/User/mcp.json").read_text())
    assert vs["servers"]["memory-os-ai"]["type"] == "stdio" and vs["inputs"] == []
    assert 'model = "gpt"' in (home / ".codex/config.toml").read_text()
    jdir = Path(res["journal"]).parent
    assert jdir.stat().st_mode & 0o777 == 0o700 and Path(res["journal"]).stat().st_mode & 0o777 == 0o600
    assert "s3cret" not in json.dumps(res)


def test_conflict_when_file_changed_after_preview(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor",))
    write(home, ".cursor/mcp.json", {"mcpServers": {"changed": {"command": "y"}}}, 0o600)
    res = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "j")
    assert res["records"][0]["status"] == "conflict-file-changed-since-preview"
    assert "memory-os-ai" not in (home / ".cursor/mcp.json").read_text()


def test_undo_restores_and_refuses_if_entry_changed(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor", "vscode"))
    res = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "j")
    p = home / "Library/Application Support/Code/User/mcp.json"
    data = json.loads(p.read_text())
    data["servers"]["memory-os-ai"]["args"] = ["edited-by-user"]
    p.write_text(json.dumps(data))
    out = {r["client"]: r["undo"] for r in qs.undo(res["stamp"], home=home, journal_root=tmp_path / "j")["results"]}
    assert out == {"cursor": "restored", "vscode": "refused-entry-changed-since-apply"}   # Q3
    cur = json.loads((home / ".cursor/mcp.json").read_text())
    assert "memory-os-ai" not in cur["mcpServers"] and "other" in cur["mcpServers"]


def test_toml_append_when_absent_and_claude_via_cli(home, tmp_path):
    calls = []

    class R:
        returncode = 0
        stdout = stderr = ""

    def fake_run(argv, **kw):
        calls.append(argv)
        return R()

    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",))
    assert plan["clients"][0]["action"] == "create"
    qs._write_client(next(c for c in qs.CLIENTS if c.key == "claude-code"), home,
                     qs.desired_entry(next(c for c in qs.CLIENTS if c.key == "claude-code"), PY, ENV), run=fake_run)
    assert calls[-1][:6] == ["claude", "mcp", "add", "--scope", "user", "--transport"]
    assert not any("TOKEN=" in a and "http.token" not in a for a in calls[-1])    # no token value


def test_report_is_anonymized(home):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor",))
    rep = json.dumps(qs.report(plan))
    assert str(Path.home()) not in rep or str(Path.home()) == "/"
    assert "s3cret" not in rep and "desired" not in rep


@pytest.fixture
def install_context(home, monkeypatch):
    from types import SimpleNamespace
    from memory_os_v4.ops.settings import OpsSettings
    from memory_os_v4.ops import provision
    from memory_os_v4 import readiness, __version__
    python = home / 'runtime/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('# synthetic executable, never executed')
    python.chmod(0o700)
    settings = OpsSettings('http://127.0.0.1:43219/mcp/', home / 'token', home / 'store',
                           home / 'backups', home / 'key', home / 'ops', 'test.memory-os')
    resource = {'ok': True, 'ready': True, 'download_bytes': 0,
                'peak_additional_bytes_upper_bound': 1_000_000, 'resources': ['granite', 'cl100k'],
                'env': {'HF_HUB_CACHE': str(home / 'cache'), 'MEMORY_V4_CL100K_PATH': str(home / 'table'),
                        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}}
    identity = {'version': __version__, 'commit': 'a'*40, 'source_dirty': False,
                'identity_source': 'embedded_descriptor'}
    monkeypatch.setattr(qs.sys, 'platform', 'darwin')
    monkeypatch.setattr(qs.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(qs.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=20_000_000_000))
    monkeypatch.setattr(qs, '_port_state', lambda p, **kw: 'free')
    monkeypatch.setattr(readiness, 'build_identity', lambda: dict(identity))
    monkeypatch.setattr(provision, 'plan', lambda **kw: dict(resource))
    monkeypatch.setattr(provision, 'apply', lambda *a, **kw: dict(resource))
    monkeypatch.setattr(provision, 'guard', lambda *args: None)
    # 4.1.1 L3: the synthetic target interpreter is never executed; it answers like this process.
    monkeypatch.setattr(qs.sqlite_runtime, 'probe_target', lambda python, **kw: qs.sqlite_runtime.inspect())
    loaded = [False]
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ['launchctl', 'print']:
            if not loaded[0]:
                return SimpleNamespace(returncode=113, stdout='', stderr='Could not find service')
            service = qs._service_plan(settings, home, str(python), resource['env'])
            text = f"program = {python}\narguments = {{\n" + '\n'.join(service['argv']) + '\n}\n'
            text += '\n'.join(f'{k} => {v}' for k, v in service['env'].items())
            text += '\nstate = running\npid = 4242\n'  # the process the server answers with (D6 bis)
            return SimpleNamespace(returncode=0, stdout=text, stderr='')
        if argv[:2] == ['launchctl', 'bootstrap']:
            loaded[0] = True
        if argv[:2] == ['launchctl', 'bootout']:
            loaded[0] = False  # 4.1.1 L4: a stop is confirmed by observing the job absent afterwards
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    ready = {'version': __version__, 'capabilities': {'store': {'instance_id': 'synthetic'}},
             'ok': True, 'server': 'memory-os-v4', 'pid': 4242}
    monkeypatch.setattr(qs, '_ready', lambda *a, **kw: ready)
    return SimpleNamespace(home=home, python=str(python), settings=settings, resource=resource,
                           identity=identity, run=run, calls=calls, loaded=loaded, ready=ready)


def installation(c, clients=('cursor',), backend=None):
    return qs.prepare(c.settings, home=c.home, python=c.python, clients=clients, backend=backend, run=c.run,
                      profile='semantic')  # 4.1.0 setup contract §2.1: explicit for a new install


def synthetic_resource_guard(monkeypatch, free):
    """Actual provision guard with synthetic disk/swap; no sysctl or heavy job."""
    from types import SimpleNamespace
    from memory_os_v4.ops import provision
    monkeypatch.setattr(provision.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=free))
    monkeypatch.setattr(provision, 'sys_platform', lambda: 'darwin')
    monkeypatch.setattr(provision.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout='used = 0.00M', stderr=''))


@pytest.mark.parametrize('override,minimum', [
    (None, 5_000_000_000), ('3000000000', 3_000_000_000), ('4500000000', 4_500_000_000),
    ('8000000000', 8_000_000_000), ('0003000000000', 3_000_000_000)])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_operator_disk_minimum_preview_and_real_guard_align(
        install_context, monkeypatch, override, minimum, offset):
    from memory_os_v4.ops import provision
    if override is None:
        monkeypatch.delenv('MEMORY_V4_MIN_FREE_BYTES', raising=False)
    else:
        monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', override)
    c = install_context
    synthetic_resource_guard(monkeypatch, minimum + offset)
    before = tree(c.home)
    p = installation(c)
    assert provision.min_free_bytes() == minimum
    assert p['disk_min_free_bytes'] == p['disk_required_free_bytes'] == minimum
    assert qs.report(p)['disk_min_free_bytes'] == minimum
    assert qs.report(p)['disk_required_free_bytes'] == minimum
    assert p['ok'] is (offset >= 0), p['blockers']
    if offset < 0:
        assert any(f'effective minimum {minimum} bytes' in b for b in p['blockers'])
        with pytest.raises(provision.ProvisionError, match=f'effective minimum {minimum} bytes'):
            provision_guard(c.home)
    else:
        provision_guard(c.home)
    assert tree(c.home) == before


@pytest.mark.parametrize('free', [4_999_999_999, 5_000_000_000])
def test_operator_override_keeps_full_peak_plus_margin(install_context, monkeypatch, free):
    c = install_context
    monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', '3000000000')
    c.resource['peak_additional_bytes_upper_bound'] = 4_000_000_000
    synthetic_resource_guard(monkeypatch, free)
    p = installation(c)
    assert p['disk_min_free_bytes'] == 3_000_000_000
    assert p['disk_required_free_bytes'] == 5_000_000_000
    assert p['ok'] is (free >= 5_000_000_000)
    # A resource peak may make the preview stricter than the runtime floor.
    provision_guard(c.home)


@pytest.mark.parametrize('override', [
    '2999999999', '0', '-1', '', 'invalid', '3000000000.0', '3e9', '+3000000000', ' 3000000000'])
def test_invalid_operator_disk_policy_refuses_preview_guard_and_resources_without_writes(
        install_context, monkeypatch, override):
    from memory_os_v4.ops import provision
    monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', override)
    monkeypatch.setattr(provision.shutil, 'disk_usage', lambda *a: pytest.fail('Invalid policy before disk access'))
    monkeypatch.setattr(provision, 'catalog', lambda: pytest.fail('Invalid policy before resource reads'))
    c = install_context
    before = tree(c.home)
    for action in (provision.min_free_bytes, lambda: provision_guard(c.home),
                   lambda: installation(c), provision_plan):
        with pytest.raises(provision.ProvisionError, match='MEMORY_V4_MIN_FREE_BYTES.*3000000000'):
            action()
    assert tree(c.home) == before and c.calls == []


def test_invalid_operator_disk_policy_cli_refuses_cleanly(install_context, monkeypatch, capsys):
    from types import SimpleNamespace
    from memory_os_v4.ops import cli
    c = install_context
    monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', '2999999999')
    before = tree(c.home)
    args = SimpleNamespace(undo=None, clients='cursor', backend='sqlite', apply=False, report=False)
    assert cli.cmd_quickstart(args, c.settings) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal['ok'] is False and refusal['code'] == 'quickstart_refused'
    assert 'MEMORY_V4_MIN_FREE_BYTES' in refusal['error'] and '3000000000' in refusal['error']
    assert tree(c.home) == before


def test_operator_disk_override_never_relaxes_swap_limit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from memory_os_v4.ops import provision
    monkeypatch.setenv('MEMORY_V4_MIN_FREE_BYTES', '3000000000')
    synthetic_resource_guard(monkeypatch, 3_000_000_000)
    monkeypatch.setattr(provision.subprocess, 'run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout='used = 8200.00M', stderr=''))
    with pytest.raises(provision.ProvisionError, match='Swap guard: more than 8.5 GB'):
        provision_guard(tmp_path)


def test_complete_preview_is_zero_write_and_no_activation(install_context):
    c = install_context
    before = tree(c.home)
    p = installation(c)
    assert p['ok'] and tree(c.home) == before
    assert all(argv[1] == 'print' for argv in c.calls)
    assert p['service']['env']['MEMORY_V4_STORE_BACKEND'] == 'sqlite_s1'
    assert p['service']['env']['MEMORY_V4_WORK_STATE'] == 'true'
    assert p['service']['env']['MEMORY_V4_DEVICE'] == 'auto'
    assert p['service']['env']['MEMORY_V4_LEXICAL_GUARD_K'] == '3'


@pytest.mark.parametrize('runs,process_state,pid', [(2, 'running', 123), (1, 'spawn scheduled', None)])
@pytest.mark.parametrize('ownership_changed', [False, True])
def test_crashing_new_service_fails_fast_with_masked_tail_and_attested_stop(
        install_context, monkeypatch, runs, process_state, pid, ownership_changed):
    c = install_context
    client_before = (c.home / '.cursor/mcp.json').read_bytes()
    p = installation(c)
    monkeypatch.setattr(qs, '_ready', lambda *a, **k: None)
    original = c.run
    def run(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[:2] == ['launchctl', 'bootstrap']:
            from memory_os_v4.ops.auth import read_token
            token = read_token(c.settings.token_file)
            log = c.settings.ops_dir / 'logs/server.err'
            log.write_text('x' * 9000 + '\nBearer ' + token +
                           "\nAttributeError: 'Server' object has no attribute 'list_tools'\n")
            if ownership_changed:
                monkeypatch.setattr(qs, '_job_state', lambda *a, **k: 'foreign-or-unattested')
        if argv[:2] == ['launchctl', 'print'] and c.loaded[0]:
            result.stdout = result.stdout.replace('\nstate = running\npid = 4242\n', '')  # this job crashes
            result.stdout += f'\nruns = {runs}\nlast exit code = 1\nstate = {process_state}\n'
            if pid:
                result.stdout += f'pid = {pid}\n'
        return result
    monkeypatch.setattr(qs.time, 'sleep', lambda *a: pytest.fail('Known failure must not wait for timeout'))
    result = qs.apply_install(p, c.settings, home=c.home, run=run, timeout=180)
    assert not result['ok'] and 'Server startup failed' in result['error']
    failed = result['startup_failure']
    assert failed['runs'] == runs and failed['last_exit_code'] == 1
    # 4.1.0 setup contract §8: structured facts from a closed list, never a tail of the log.
    assert failed['exception_types'] == ['AttributeError'] and 'stderr_tail' not in failed
    from memory_os_v4.ops.auth import read_token
    assert read_token(c.settings.token_file) not in json.dumps(result)
    stopped = [a for a in c.calls if a[:2] == ['launchctl', 'bootout']]
    assert bool(stopped) is (not ownership_changed)
    assert 'clients' not in result and (c.home / '.cursor/mcp.json').read_bytes() == client_before
    assert c.settings.token_file.exists()  # Evidence retained, no unsafe cleanup.


@pytest.mark.parametrize('case', ['unknown', 'json-invalid', 'foreign-port', 'foreign-job',
                                  'foreign-plist', 'store-nonempty', 'token-mode', 'bad-build'])
def test_preflight_refuses_before_any_write(install_context, monkeypatch, case):
    from memory_os_v4 import readiness
    c = install_context
    clients = ('cursor',)
    if case == 'unknown':
        clients = ('no-client',)
    elif case == 'json-invalid':
        (c.home / '.cursor/mcp.json').write_text('{invalid')
    elif case == 'foreign-port':
        monkeypatch.setattr(qs, '_port_state', lambda p, **kw: 'listening')
    elif case == 'foreign-job':
        monkeypatch.setattr(qs, '_job_state', lambda *a, **k: 'foreign-or-unattested')
    elif case == 'foreign-plist':
        import plistlib
        path = c.home / 'Library/LaunchAgents/test.memory-os.plist'
        path.parent.mkdir(parents=True)
        path.write_bytes(plistlib.dumps({'Label': 'test.memory-os', 'ProgramArguments': ['/foreign']}))
    elif case == 'store-nonempty':
        c.settings.store_dir.mkdir()
        (c.settings.store_dir / 'history').write_text('keep')
    elif case == 'token-mode':
        c.settings.token_file.write_text('x'*43)
        c.settings.token_file.chmod(0o644)
    else:
        monkeypatch.setattr(readiness, 'build_identity', lambda: {'commit': 'a'*40, 'identity_source': 'env_declared'})
    before = tree(c.home)
    if case == 'token-mode':
        with pytest.raises(ValueError):
            installation(c, clients)
    else:
        p = installation(c, clients)
        assert not p['ok']
        with pytest.raises(qs.QuickstartError, match='Preflight refused'):
            qs.apply_install(p, c.settings, home=c.home, run=c.run)
    assert tree(c.home) == before
    assert not any(a[1] in ('bootstrap', 'bootout') for a in c.calls)


def test_download_consent_precedes_token_journal_and_service(install_context):
    c = install_context
    c.resource.update(download_bytes=200, ready=False)
    plan = installation(c)
    before = tree(c.home)
    with pytest.raises(qs.QuickstartError, match='download-approved'):
        qs.apply_install(plan, c.settings, home=c.home, run=c.run)
    assert tree(c.home) == before


def test_apply_order_idempotence_demo_and_semantic_undo(install_context):
    from memory_os_v4.ops.doctor import Check
    c = install_context
    probes = []
    async def probe(python, env, **kwargs):
        probes.append(kwargs)
        return {'connected': True, 'real_client_usage': 'unmeasured', 'demo': kwargs['demo']}
    before_other = json.loads((c.home / '.cursor/mcp.json').read_text())['mcpServers']['other']
    result = qs.apply_install(installation(c), c.settings, home=c.home, run=c.run, probe=probe,
                             doctor_run=lambda s: [Check('server', 'PASS', 'synthetic')], demo=True)
    assert result['ok'] and [p['demo'] for p in probes] == [False, True]
    assert result['real_client_usage'] == 'unmeasured'
    assert c.settings.token_file.stat().st_mode & 0o777 == 0o600
    token_bytes = c.settings.token_file.read_bytes()
    assert token_bytes.strip().decode() not in json.dumps(result)
    assert sum(a[1] == 'bootstrap' for a in c.calls) == 1
    # Second explicit apply: no token replacement, no second bootstrap, unique journal.
    again = qs.apply_install(installation(c), c.settings, home=c.home, run=c.run, probe=probe,
                            doctor_run=lambda s: [])
    assert again['ok'] and again['stamp'] != result['stamp']
    assert sum(a[1] == 'bootstrap' for a in c.calls) == 1
    assert c.settings.token_file.read_bytes() == token_bytes
    out = qs.undo(result['stamp'], home=c.home)
    assert out['results'][0]['undo'] == 'restored'
    assert json.loads((c.home / '.cursor/mcp.json').read_text())['mcpServers']['other'] == before_other
    assert c.settings.token_file.exists() and c.settings.store_dir.exists()
    assert not any(a[1] == 'bootout' for a in c.calls)


@pytest.mark.parametrize('phase', ['resources', 'ready', 'doctor', 'probe'])
def test_stop_retains_progress_without_demo_or_replay(install_context, monkeypatch, phase):
    from memory_os_v4.ops import provision
    from memory_os_v4.ops.doctor import Check
    c = install_context
    plan = installation(c)
    probes = []
    async def probe(*args, **kwargs):
        probes.append(kwargs)
        raise ExceptionGroup('synthetic connection', [RuntimeError('failure')])
    if phase == 'resources':
        monkeypatch.setattr(provision, 'apply', lambda *a, **k: {'ok': False, 'ready': False})
    if phase == 'ready':
        monkeypatch.setattr(qs, '_ready', lambda *a, **kw: None)
    checks = [Check('server', 'FAIL', 'synthetic')] if phase == 'doctor' else []
    result = qs.apply_install(plan, c.settings, home=c.home, run=c.run, probe=probe,
                             doctor_run=lambda s: checks, demo=True, timeout=.001)
    assert not result['ok'] and result['stage'] in ('resources', 'service', 'checks')
    journal = c.home / '.local/state/memory-os/quickstart' / result['stamp'] / 'install.json'
    assert json.loads(journal.read_text())['ok'] is False
    assert all(not p['demo'] for p in probes) and len(probes) <= 1
    if phase in ('resources', 'ready'):
        assert 'memory-os-ai' not in (c.home / '.cursor/mcp.json').read_text()


def test_unsafe_paths_and_undo_traversal_refused(home, tmp_path):
    with pytest.raises(qs.QuickstartError, match='stamp'):
        qs.undo('../../foreign', home=home)
    path = home / '.cursor/mcp.json'
    path.unlink()
    outside = tmp_path / 'foreign.json'
    outside.write_text('{}')
    path.symlink_to(outside)
    with pytest.raises(qs.QuickstartError, match='Symlink'):
        qs.preview(home=home, python=PY, env=ENV, clients=('cursor',))
    assert outside.read_text() == '{}'


def test_pending_client_journal_survives_write_failure(home, tmp_path, monkeypatch):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('cursor',))
    def fail(*args, **kwargs):
        raise OSError('synthetic failure')
    monkeypatch.setattr(qs, '_write_client', fail)
    with pytest.raises(qs.QuickstartError, match='retained'):
        qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / 'j')
    journal = next((tmp_path / 'j').glob('*/journal.json'))
    data = json.loads(journal.read_text())
    assert data['records'][0]['status'] == 'failed-or-partial'
    assert data['records'][0]['applied_entry']['command'] == PY
    assert journal.stat().st_mode & 0o777 == 0o600


def test_claude_file_changed_since_preview_is_refused(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('claude-code',))
    write(home, '.claude.json', {'mcpServers': {'other': {'command': 'keep'}}})
    def no_cli(*args, **kwargs):
        pytest.fail('Claude CLI must not run after a config conflict')
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / 'j', run=no_cli)
    assert result['records'][0]['status'] == 'conflict-file-changed-since-preview'


def test_toml_semantic_undo_preserves_unrelated_edit(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('codex',))
    applied = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / 'j')
    path = home / '.codex/config.toml'
    with path.open('a') as f:
        f.write('\n[unrelated]\nkeep = true\n')
    result = qs.undo(applied['stamp'], home=home, journal_root=tmp_path / 'j')
    assert result['results'][0]['undo'] == 'restored'
    assert 'keep = true' in path.read_text() and 'model = "gpt"' in path.read_text()
    assert 'memory-os-ai' not in path.read_text()


def test_cli_wiring_and_anonymized_preview(home, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    from memory_os_v4.ops.settings import OpsSettings
    s = OpsSettings.from_env()
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('cursor',))
    plan.update(ok=True, blockers=[])
    monkeypatch.setattr(qs, 'prepare', lambda *a, **k: plan)
    monkeypatch.setattr(cli.OpsSettings, 'from_env', lambda: s)
    assert cli.main(['quickstart', '--clients', 'cursor', '--report']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['stage'] == 'preview' and result['writes'] == 'none'
    assert 's3cret' not in json.dumps(result)
    with pytest.raises(SystemExit):
        cli.main(['quickstart', '--download-approved'])


def test_guard_stops_before_all_writes_even_with_present_resources(install_context, monkeypatch):
    from memory_os_v4.ops import provision
    c = install_context
    plan = installation(c)
    before = tree(c.home)
    def stop(*args):
        raise provision.ProvisionError('Swap measurement unavailable')
    monkeypatch.setattr(provision, 'guard', stop)
    with pytest.raises(provision.ProvisionError):
        qs.apply_install(plan, c.settings, home=c.home, run=c.run)
    assert tree(c.home) == before


def test_guard_breach_stops_only_new_attested_service(install_context, monkeypatch):
    from memory_os_v4.ops import provision
    c = install_context
    plan = installation(c)
    counts = []
    def guard(*args):
        counts.append(1)
        if len(counts) > 1:
            raise provision.ProvisionError('Swap above cap')
    monkeypatch.setattr(provision, 'guard', guard)
    result = qs.apply_install(plan, c.settings, home=c.home, run=c.run)
    assert not result['ok'] and result['guard_stop']['stopped']
    assert sum(a[1] == 'bootout' for a in c.calls) == 1
    assert 'memory-os-ai' not in (c.home / '.cursor/mcp.json').read_text()


def test_live_job_same_python_different_args_or_token_is_foreign(install_context):
    from types import SimpleNamespace
    c = install_context
    service = qs._service_plan(c.settings, c.home, c.python, c.resource['env'])
    for changed in ('args', 'token'):
        args = [*service['argv']]
        if changed == 'args':
            args[2] = 'some_other_server'
        token = str(c.settings.token_file) if changed != 'token' else '/foreign/token'
        out = f'program = {c.python}\narguments = {{\n' + '\n'.join(args) + '\n}\n'
        out += f'MEMORY_V4_TOKEN_FILE => {token}\nMEMORY_V4_DIR => {c.settings.store_dir}\n'
        def run(*a, **kw):
            return SimpleNamespace(returncode=0, stdout=out, stderr='')
        assert qs._job_state(service, run=run) == 'foreign-or-unattested'


def test_running_desktop_refused(install_context):
    c = install_context
    before = tree(c.home)
    plan = installation(c, clients=('claude-desktop',))
    assert not plan['ok'] and any('quit Claude Desktop' in b for b in plan['blockers'])
    assert tree(c.home) == before


def test_realistic_claude_cli_entry_and_undo(home, tmp_path):
    from types import SimpleNamespace
    before = {'other': {'command': 'keep'}}
    write(home, '.claude.json', {'mcpServers': before})
    def run(argv, **kw):
        assert kw['env']['HOME'] == str(home)
        path = home / '.claude.json'
        data = json.loads(path.read_text())
        if argv[2] == 'add':
            split = argv.index('--')
            env = {argv[i+1].split('=', 1)[0]: argv[i+1].split('=', 1)[1]
                   for i, val in enumerate(argv) if val == '--env'}
            data['mcpServers']['memory-os-ai'] = {'type': 'stdio', 'command': argv[split+1],
                                                'args': argv[split+2:], 'env': env}
        else:
            data['mcpServers'].pop('memory-os-ai', None)
        path.write_text(json.dumps(data))
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('claude-code',))
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / 'j', run=run)
    assert result['records'][0]['status'] == 'applied'
    undone = qs.undo(result['stamp'], home=home, journal_root=tmp_path / 'j', run=run)
    assert undone['results'][0]['undo'] == 'restored'
    assert json.loads((home / '.claude.json').read_text())['mcpServers'] == before


def test_masked_token_path_cannot_change_after_preview(home, tmp_path):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('cursor',))
    before = tree(tmp_path)
    with pytest.raises(qs.QuickstartError, match='Desired entry'):
        qs.apply_clients(plan, home=home, python=PY, env={**ENV, 'MEMORY_V4_TOKEN_FILE': '/other/token'},
                         journal_root=tmp_path / 'j')
    assert tree(tmp_path) == before


@pytest.mark.parametrize('damage', ['none', 'identity', 'work-only', 'profile', 'backend'])
def test_ready_requires_actual_retrieval_profile_and_identity(install_context, monkeypatch, damage):
    from memory_os_v4.ops import auth, provision
    c = install_context
    info = {'version': c.identity['version'], 'commit': c.identity['commit'], 'ready': True, 'reason': 'ready',
            'capabilities': {'store': {'backend': 'sqlite_s1', 'instance_id': 'synthetic',
                                       'sqlite_version': '3.53.4'},  # 4.1.0: attested by the server process
                'work_state': True,
                'encoding': {'requested_device': 'auto', 'model': provision.catalog()['model_id'],
                    'profile': {'dtype': 'native', 'batch_size': 32, 'max_seq_length': 512,
                               'model_revision': provision.catalog()['revision']}},
                'policies': {'bm25_profile': 'scoped_literal', 'lexical_guard_k': 3},
                'brief': {'tokenizer': {'ready': True}}}}
    if damage == 'identity':
        info['commit'] = 'b'*40
    elif damage == 'work-only':
        info['reason'] = 'ready_work_only'
    elif damage == 'profile':
        info['capabilities']['encoding']['profile']['dtype'] = 'float32'
    elif damage == 'backend':
        info['capabilities']['store']['backend'] = 'jsonl'
    from memory_os_v4.ops.qualified_runtime import catalog as runtime_catalog
    info['capabilities']['encoding']['profile']['runtime'] = runtime_catalog()['versions']
    # Restore the real function replaced in install_context for this pure response test.
    import importlib.util
    spec = importlib.util.spec_from_file_location('memory_os_v4.ops._qs_ready_test', Path(qs.__file__))
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    monkeypatch.setattr(auth, 'http_get', lambda *a, **kw: (200, json.dumps(info).encode()))
    if damage == 'none':
        assert original._ready(c.settings, c.identity) == info
    else:
        with pytest.raises(original.QuickstartError):
            original._ready(c.settings, c.identity)


@pytest.mark.asyncio
@pytest.mark.parametrize('demo,write_failure', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('tasks_enabled', [False, True])
async def test_relay_probe_real_contract_with_fake_transport(monkeypatch, demo, write_failure, tasks_enabled):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import mcp
    import mcp.client.stdio
    calls = []
    key = ['']
    @asynccontextmanager
    async def transport(params):
        assert params.args == ['-m', 'memory_os_v4.ops.relay']
        assert params.env['PYTHONPATH'] == ''
        yield object(), object()
    class Session:
        def __init__(self, *args):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def initialize(self):
            return SimpleNamespace(serverInfo=SimpleNamespace(version='synthetic'))
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in
                ('memory_status', 'memory_chat_save', 'memory_search', 'memory_request_status',
                 'memory_task_list', 'memory_task_create', 'memory_task_update')])
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if name == 'memory_status':
                payload = {'capabilities': {'store': {'instance_id': 'test-store'}}}
            elif name == 'memory_chat_save':
                key[0] = arguments['facts'][0]['text']
                payload = {'ok': True}
            elif name == 'memory_task_list':
                payload = {'ok': True, 'tasks': [], 'count': 0}
            else:
                payload = {'text': key[0]}
            result = SimpleNamespace(isError=write_failure and name == 'memory_chat_save',
                                     content=[SimpleNamespace(text=json.dumps(payload))])
            result.model_dump = lambda: payload
            return result
    monkeypatch.setattr(mcp, 'ClientSession', Session)
    monkeypatch.setattr(mcp.client.stdio, 'stdio_client', transport)
    expected = {'version': 'synthetic', 'capabilities': {'store': {'instance_id': 'test-store'},
                                                       'work_state': tasks_enabled}}
    if write_failure:
        with pytest.raises(qs.QuickstartError, match='never replay'):
            await qs._relay_probe(PY, ENV, demo=demo, expected=expected)
    else:
        result = await qs._relay_probe(PY, ENV, demo=demo, expected=expected)
        assert result['connected'] and result['real_client_usage'] == 'unmeasured'
        if demo:
            assert result['demo']['saved_and_found'] and result['demo']['project'].startswith('memory-os-demo-')
    assert sum(name == 'memory_chat_save' for name, _ in calls) == int(demo)
    assert sum(name == 'memory_task_list' for name, _ in calls) == int(tasks_enabled)
    assert not any(name == 'memory_search' for name, _ in calls) if write_failure else True


def test_cli_apply_passes_separate_consents_and_report_masks(home, monkeypatch, capsys):
    from memory_os_v4.ops import cli
    from memory_os_v4.ops.settings import OpsSettings
    s = OpsSettings.from_env()
    plan = qs.preview(home=home, python=PY, env=ENV, clients=('cursor',))
    plan.update(ok=True, blockers=[])
    calls = []
    monkeypatch.setattr(qs, 'prepare', lambda *a, **kw: plan)
    monkeypatch.setattr(cli.OpsSettings, 'from_env', lambda: s)
    def apply(*a, **kw):
        calls.append(kw)
        return {'ok': True, 'stage': 'complete', 'stamp': 'synthetic', 'elapsed_s': 1,
                'relay': {'connected': True}, 'private_path': str(home),
                'clients': {'records': [{'client': 'cursor', 'status': 'applied'}]}}
    monkeypatch.setattr(qs, 'apply_install', apply)
    assert cli.main(['quickstart', '--clients', 'cursor', '--apply', '--download-approved', '--demo', '--report']) == 0
    report = json.loads(capsys.readouterr().out)
    assert calls == [{'download_approved': True, 'demo': True}]
    assert 'private_path' not in report and report['real_client_usage'] == 'unmeasured'


def test_cli_undo_refusal_is_failure(monkeypatch, capsys):
    from memory_os_v4.ops import cli
    monkeypatch.setattr(qs, 'undo', lambda *a, **kw: {'results': [
        {'client': 'cursor', 'undo': 'refused-entry-changed-since-apply'}]})
    assert cli.main(['quickstart', '--undo', 'synthetic']) == 1
    assert 'refused-entry-changed-since-apply' in capsys.readouterr().out


@pytest.mark.parametrize('backend', ['sqlite', 'jsonl'])
def test_backend_choice_and_persisted_backend_no_implicit_switch(install_context, backend):
    c = install_context
    plan = installation(c, backend=backend)
    assert plan['ok'] and plan['backend'] == backend
    env = plan['service']['env']
    assert env['MEMORY_V4_STORE_BACKEND'] == ('sqlite_s1' if backend == 'sqlite' else 'jsonl')
    assert env['MEMORY_V4_WORK_STATE'] == ('true' if backend == 'sqlite' else 'false')
    path = Path(plan['service']['path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plan['service']['plist'])
    assert qs._selected_backend(c.settings, c.home) == backend
    # Asking to switch an existing service cannot match ownership and never migrates.
    from memory_os_v4.ops.auth import create_token
    create_token(c.settings.token_file)
    other = installation(c, backend='jsonl' if backend == 'sqlite' else 'sqlite')
    assert not other['ok'] and other['service']['state'] == 'foreign-or-different'
    before = tree(c.home)
    with pytest.raises(qs.QuickstartError):
        qs.apply_install(other, c.settings, home=c.home, run=c.run)
    assert tree(c.home) == before


def test_new_default_sqlite_directory_avoids_reserved_legacy_path(install_context, monkeypatch):
    from dataclasses import replace
    c = install_context
    monkeypatch.delenv('MEMORY_V4_DIR', raising=False)
    original = replace(c.settings, store_dir=c.home / '.memory-os-v4')
    before = tree(c.home)
    chosen, backend = qs.settings_for_install(original, home=c.home)
    assert backend == 'sqlite' and chosen.store_dir == c.home / '.local/share/memory-os/store'
    assert tree(c.home) == before
    jsonl, backend = qs.settings_for_install(original, backend='jsonl', home=c.home)
    assert backend == 'jsonl' and jsonl.store_dir == original.store_dir
    original.store_dir.mkdir()
    (original.store_dir / 'items.jsonl').write_text('preserve legacy history')
    preserved, backend = qs.settings_for_install(original, home=c.home)
    assert preserved == original and backend == 'sqlite'  # preflight will refuse, never silently redirect history
    assert (original.store_dir / 'items.jsonl').read_text() == 'preserve legacy history'


def test_runtime_drift_blocks_preview_apply_before_all_mutations(install_context, monkeypatch):
    from memory_os_v4.ops import qualified_runtime
    c = install_context
    expected = qualified_runtime.catalog()['versions']
    drifted = {**expected, 'torch': '2.14.1', 'transformers': '5.19.0'}
    monkeypatch.setattr(qualified_runtime, 'inspect_current', lambda **kw: qualified_runtime.compare(drifted))
    before = tree(c.home)
    plan = installation(c)
    assert not plan['ok'] and any('runtime_unqualified' in b for b in plan['blockers'])
    with pytest.raises(qs.QuickstartError):
        qs.apply_install(plan, c.settings, home=c.home, run=c.run, download_approved=True)
    assert tree(c.home) == before


def test_sqlite_plist_really_enables_private_task_api(install_context, monkeypatch):
    from memory_os_v4 import Config, MemoryV4, server
    c = install_context
    plan = installation(c)
    for name, value in plan['service']['env'].items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv('MEMORY_V4_EMBED_MODEL', 'hash')  # synthetic, no neural model or download
    cfg = Config.from_env()
    assert cfg.store_backend == 'sqlite_s1' and cfg.work_state is True
    with MemoryV4(config=cfg) as engine:
        monkeypatch.setattr(server, '_engine', engine)
        result = server.dispatch('memory_task_create', {'project': 'synthetic', 'title': 'Private install proof',
                                                       'request_id': 'new-task'})
        assert result['ok'], result
        listed = server.dispatch('memory_task_list', {'project': 'synthetic', 'limit': 1})
        assert listed['ok'] and listed['count'] == 1
        assert listed['tasks'][0]['title'] == 'Private install proof'
    assert (c.settings.store_dir / 'store.sqlite3').is_file()
    assert not (c.settings.store_dir / 'items.jsonl').exists()
