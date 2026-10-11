"""Warm-start refusals prove only this attempt was not sent, never replay."""
import json

import anyio
import mcp.types as types
import pytest

from memory_os_v4.ops.durable_relay import DurableRelay, PreflightRefusal, response
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.idempotency import raw_args_sha256


class Session:
    def __init__(self, fault=None):
        self.effects, self.fault = 0, fault

    async def call_tool(self, name, args):
        if name == 'memory_request_status':
            return response({'ok': True, 'contract': 'l3-v2', 'store_instance_id': 'store',
                             'replay_permitted': False, 'scope': '["project","p"]',
                             'operation': args['operation'], 'raw_args_sha256': raw_args_sha256(args['arguments'])})
        self.effects += 1
        if self.fault == 'during':
            raise ConnectionError('lost after effect')
        return response({'effect': self.effects})


class Transport:
    def __init__(self, session):
        self.session = session

    async def submit(self, op, *, replay):
        assert replay is False
        return await op(self.session)


def settings(tmp_path):
    return OpsSettings(url='http://127.0.0.1:9/mcp/', token_file=tmp_path / 'token',
                       store_dir=tmp_path / 'store', backup_dir=tmp_path / 'backups',
                       backup_key=tmp_path / 'key', ops_dir=tmp_path / 'ops', service_label=None)


def call(relay, name='memory_block_set', args=None):
    return anyio.run(relay.call, name, {'name': 'working_rules', 'text': 'data', 'request_id': 'id'}
                     if args is None else args)


@pytest.mark.parametrize('failure,code,retryable', [
    ('timeout', 'preflight_unavailable', True),
    ('tool_error', 'preflight_unavailable', True),
    ('malformed', 'preflight_unavailable', True),
    ('warming', 'upstream_not_ready', True),
    ('warming_ok', 'upstream_not_ready', True),
    ('legacy', 'legacy_unattested', False),
    ('wrong_operation', 'preflight_invalid', False),
    ('authentication', 'authentication_rejected', False),
])
def test_preflight_failure_has_no_mutation_or_pending_entry(tmp_path, monkeypatch, failure, code, retryable):
    monkeypatch.setenv('MEMORY_V4_RELAY_JOURNAL_DIR', str(tmp_path / 'relay'))

    class Failing(Session):
        async def call_tool(self, name, args):
            if name == 'memory_request_status' and 'arguments' in args:
                if failure in {'timeout', 'authentication'}:
                    raise TimeoutError('no reply')
                if failure == 'tool_error':
                    return types.CallToolResult(isError=True, content=[types.TextContent(type='text', text='warming')])
                if failure == 'malformed':
                    return types.CallToolResult(content=[types.TextContent(type='text', text='{')])
                if failure in {'warming', 'warming_ok'}:
                    return response({'ok': failure == 'warming_ok', 'code': 'engine_not_ready'})
                if failure == 'legacy':
                    return response({'ok': True, 'contract': 'l3-v1'})
                if failure == 'wrong_operation':
                    result = await super().call_tool(name, args)
                    result.structuredContent['operation'] = 'memory_forget'
                    return result
            return await super().call_tool(name, args)

    session = Failing()
    transport = Transport(session)
    if failure == 'authentication':
        transport.last_error = {'code': 'authentication_rejected'}
    relay = DurableRelay(transport, settings(tmp_path))
    try:
        with pytest.raises(PreflightRefusal) as caught:
            call(relay)
        details = json.loads(str(caught.value))
        assert details['code'] == code and details['retryable'] is retryable
        assert details['mutation_sent'] is False and details['automatic_retry'] is False
        assert details['attempt_outcome'] == 'not_sent'
        assert session.effects == 0 and relay.journal.records == {}
        local = call(relay, 'memory_request_status', {}).structuredContent
        assert local['unresolved_count'] == 0
        assert local['connection']['preflight_failure']['code'] == code
    finally:
        relay.close()


def test_explicit_retry_after_ready_runs_once_with_same_logical_id(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORY_V4_RELAY_JOURNAL_DIR', str(tmp_path / 'relay'))

    class Warm(Session):
        warming = True

        async def call_tool(self, name, args):
            if self.warming:
                return response({'ok': False, 'code': 'engine_not_ready'})
            return await super().call_tool(name, args)

    session = Warm()
    relay = DurableRelay(Transport(session), settings(tmp_path))
    try:
        with pytest.raises(PreflightRefusal):
            call(relay)
        assert session.effects == 0
        session.warming = False
        assert call(relay).structuredContent == {'effect': 1}
        assert session.effects == 1
        assert call(relay, 'memory_request_status', {}).structuredContent['connection']['preflight_failure'] is None
    finally:
        relay.close()


def test_prior_uncertain_logical_request_disables_preflight_retry_hint(tmp_path, monkeypatch):
    monkeypatch.setenv('MEMORY_V4_RELAY_JOURNAL_DIR', str(tmp_path / 'relay'))
    session = Session('during')
    relay = DurableRelay(Transport(session), settings(tmp_path))
    try:
        with pytest.raises(RuntimeError):
            call(relay)
        assert session.effects == 1
        original = relay.journal.path.read_bytes()

        async def unavailable(name, args):
            raise TimeoutError('preflight unavailable')

        session.call_tool = unavailable
        with pytest.raises(PreflightRefusal) as caught:
            call(relay)
        assert caught.value.details['retryable'] is False
        assert caught.value.details['logical_status_required'] is True
        assert relay.journal.path.read_bytes() == original
        assert session.effects == 1
    finally:
        relay.close()
