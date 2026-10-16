"""All mutation authors are declarative; exercise committed journal rows, not mocks."""
import json
from types import SimpleNamespace

from mcp.types import Tool
import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.changes import listing
from memory_os_v4.ops.durable_relay import MUTATIONS
from memory_os_v4.ops.relay import SessionToolCatalog, mutation_client_arguments


def catalog():
    value = SessionToolCatalog()
    value._tools = [Tool(name=name, inputSchema=model.model_json_schema())
                    for name, model in server.TOOL_MODELS.items()]
    return value


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_every_mutation_advertises_and_relay_overwrites_declarative_author(name):
    c = catalog()
    assert c.mutation_client_supported(name)
    args = {'client_provenance': {'client_id': 'forged', 'authenticated': True}}
    result = mutation_client_arguments(name, args, SimpleNamespace(name='claude-code', version='1'), c)
    assert result['client_provenance'] == {'client_id': 'claude-code', 'client_version': '1'}
    assert args['client_provenance']['client_id'] == 'forged'
    c._tools = []
    assert 'client_provenance' not in mutation_client_arguments(name, args, SimpleNamespace(name='x', version='2'), c)


@pytest.mark.parametrize('path', ['relay', 'direct', 'anonymous'])
def test_committed_events_all_authors_and_idempotence(tmp_path, monkeypatch, path):
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model='hash', hash_dim=16,
                               store_backend='sqlite_s1', work_state=True)) as m:
        monkeypatch.setattr(server, '_engine', m)
        c = catalog()
        def call(name, args):
            args = dict(args)
            if path == 'relay':
                args = mutation_client_arguments(name, args, SimpleNamespace(name='claude-code', version='1'), c)
            elif path == 'direct':
                args['client_provenance'] = {'client_id': 'direct-client', 'client_version': '2'}
            result = server.dispatch(name, args)
            assert result.get('ok') is not False, result
            events = listing(m, 'P')['events']
            event = events[-1]
            assert event['operation'] == name
            assert event['changed_by']['client_id'] == {'relay':'claude-code', 'direct':'direct-client', 'anonymous':'unknown'}[path]
            assert event['changed_by']['authenticated'] is False
            count = len(events)
            assert server.dispatch(name, args) == result
            assert len(listing(m, 'P')['events']) == count
            return result
        # Chat WITHOUT facts must carry its author as well.
        call('memory_chat_save', {'project_label':'P', 'messages':[{'role':'user', 'content':'Synthetic chat.'}], 'request_id':'chat'})
        call('memory_add_facts', {'project':'P', 'facts':[{'item_id':'f', 'text':'Synthetic fact.'}], 'request_id':'fact'})
        call('memory_forget', {'project':'P', 'item_id':'f', 'reason':'Synthetic cleanup', 'request_id':'forget'})
        call('memory_block_set', {'project':'P', 'name':'a', 'text':'Synthetic rules', 'request_id':'block'})
        call('memory_block_compare_and_set', {'project':'P', 'scope':'project', 'name':'b', 'expected_etag':None,
                                            'text':'Synthetic rules', 'reason':'Synthetic creation', 'request_id':'cas'})
        task = call('memory_task_create', {'project':'P', 'title':'Synthetic task', 'request_id':'task'})
        call('memory_task_update', {'project':'P', 'task_id':task['task_id'], 'expected_etag':task['etag'],
                                    'patch':{'next_action':'Synthetic next step'}, 'reason':'Progress', 'request_id':'update'})
        assert m.store.check_integrity()
        assert all(json.loads(row[0])['changed_by']['authenticated'] is False
                   for row in m.store._connection.execute('SELECT record_json FROM change_events'))


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_no_authentication_claim_accepted_in_provenance(name):
    from pydantic import ValidationError
    # All models must reject extra fields in the declaration. The rest of the
    # payload may also be invalid; inspect the error location specifically.
    with pytest.raises(ValidationError) as exc:
        server.TOOL_MODELS[name](client_provenance={'client_id':'x', 'authenticated':True})
    assert any(e['loc'] == ('client_provenance', 'authenticated') for e in exc.value.errors())
