"""B3 development regressions: disposable stores, hash encoder, no maintenance/TCP."""
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.extract import normalize_client_facts
from memory_os_v4.fact_updates import validate_replacements
from memory_os_v4.models import FactIdentityIn
from memory_os_v4.sqlite_store import MIN_SQLITE
from memory_os_v4.store import MemoryItem, Store

GENERIC = ['other', 'unknown', 'default', ' OTHER ', '\tUnknown\n', '\u3000ＤＥＦＡＵＬＴ\u3000', 'ＯＴＨＥＲ', 'ＵＮＫＮＯＷＮ', 'DeFaUlT']
BACKENDS = ['jsonl', pytest.param('sqlite_s1', marks=pytest.mark.skipif(sqlite3.sqlite_version_info < MIN_SQLITE, reason='Unqualified SQLite runtime'))]


def identity(predicate='residence', cardinality='single', subject='person:alice:1'):
    return {'subject_id': subject, 'predicate': predicate, 'cardinality': cardinality}


def spec(text='Alice lives in Paris.', **kw):
    return {'text': text, 'kind': 'fact', 'entities': ['Alice'], **kw}


def config(path, backend):
    return Config(data_dir=str(path), store_backend=backend, embed_model='hash', hash_dim=16)


def sources(engine, *, receipts=True):
    if engine.config.store_backend == 'sqlite_s1':
        tables = ('items', 'item_versions', 'request_receipts') if receipts else ('items', 'item_versions')
        return {table: engine.store._connection.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                for table in tables}
    return engine.store.items_path.read_bytes() if engine.store.items_path.exists() else b''


@pytest.mark.parametrize('predicate', GENERIC)
def test_generic_single_refused_by_model_normalizer_and_write_boundary(tmp_path, predicate):
    value = identity(predicate)
    with pytest.raises(ValidationError, match='generic_single_identity'):
        FactIdentityIn(**value)
    with pytest.raises(ValueError, match='generic_single_identity'):
        normalize_client_facts([spec(identity=value)])
    # Defense in depth if an internal caller supplies a built prospective item.
    with pytest.raises(ValueError, match='generic_single_identity'):
        validate_replacements(MemoryItem(id='new', kind='fact', text='New value.', identity=value), Store(tmp_path))


@pytest.mark.parametrize('predicate', ['other', 'unknown', 'default'])
def test_generic_multi_remains_additive(predicate):
    value = identity(predicate, 'multi')
    assert FactIdentityIn(**value).model_dump() == value
    assert normalize_client_facts([spec(identity=value)])[0]['identity'] == value


@pytest.mark.parametrize('predicate', ['db.port', 'deployment.current_region', 'résidence', 'SpecificProperty', 'custom_property_not_a_facet'])
def test_precise_predicates_are_extensible_not_facet_normalized(predicate):
    value = identity(predicate)
    assert FactIdentityIn(**value).model_dump() == value
    assert normalize_client_facts([spec(identity=value)])[0]['identity'] == value


@pytest.mark.parametrize('tool', ['memory_add_facts', 'memory_chat_save'])
def test_api_refuses_before_constructing_engine_or_receipt(monkeypatch, tool):
    def forbidden():
        raise AssertionError('Rejected API identity must not load the model/store')
    monkeypatch.setattr(server, 'get_engine', forbidden)
    payload = {'facts': [spec(identity=identity('OTHER'))]}
    if tool == 'memory_chat_save':
        payload.update(project_label='P', messages=[{'role': 'assistant', 'content': 'Synthetic summary.'}])
    else:
        payload['project'] = 'P'
    result = server.dispatch(tool, payload)
    assert result['ok'] is False and 'generic_single_identity' in result['error']


@pytest.mark.parametrize('backend', BACKENDS)
@pytest.mark.parametrize('predicate', GENERIC)
def test_invalid_identity_in_batch_keeps_all_sources_unchanged(tmp_path, backend, predicate):
    with MemoryV4(config=config(tmp_path, backend)) as engine:
        engine.add_facts([spec(item_id='old', identity=identity())], project='P', event_time=100)
        before = sources(engine)
        with pytest.raises(ValueError, match='generic_single_identity'):
            engine.add_facts([spec('First new fact.', item_id='first'),
                              spec('Invalid replacement.', identity=identity(predicate), replaces=['old'])],
                             project='P', event_time=200)
        assert sources(engine) == before
        assert engine.store.get('first') is None
        assert engine.store.get('old').valid_until is None


@pytest.mark.parametrize('backend', BACKENDS)
def test_homonyms_multi_and_equal_date_specific_states_coexist(tmp_path, backend):
    with MemoryV4(config=config(tmp_path, backend)) as engine:
        first = engine.add_facts([spec(identity=identity(subject='alice:1')),
                                  spec(identity=identity(subject='alice:2'))], project='P', event_time=100)
        assert len(first['added']) == 2 and first['superseded'] == []
        same_date = engine.add_facts([spec('Alice lives in Lyon.', identity=identity(subject='alice:1'))], project='P', event_time=100)
        assert same_date['added'] and not same_date['superseded']
        for t, place in ((101, 'Paris'), (102, 'Lyon')):
            result = engine.add_facts([spec(f'Alice visits {place}.', identity=identity('other', 'multi'))], project='P', event_time=t)
            assert result['added'] and result['superseded'] == []
        assert all(it.valid_until is None for it in engine.store.all(False))


@pytest.mark.parametrize('backend', BACKENDS)
def test_legacy_generic_identity_is_readable_without_renaming_or_history_update(tmp_path, backend):
    from memory_os_v4.sqlite_store import SQLiteStore
    # Raw fixture is historical DATA, intentionally bypassing incoming validation.
    legacy = MemoryItem(id='legacy', kind='fact', text='Historical assertion.', project='P',
                        event_time=100, identity=identity(' Other '))
    store = SQLiteStore(tmp_path) if backend == 'sqlite_s1' else Store(tmp_path)
    store.append(legacy)
    # Engine bootstrap/close may record implicit flush receipts. Item authority
    # and every historical item revision must remain exactly unchanged.
    before = sources(SimpleNamespace(store=store, config=config(tmp_path, backend)), receipts=False)
    if backend == 'sqlite_s1':
        store.close()
    with MemoryV4(config=config(tmp_path, backend)) as engine:
        assert engine.store.get('legacy').to_dict() == legacy.to_dict()
        assert engine.store.get('legacy').identity['predicate'] == ' Other '
        assert sources(engine, receipts=False) == before
    with MemoryV4(config=config(tmp_path, backend)) as reopened:
        assert reopened.store.get('legacy').identity == identity(' Other ')
        assert sources(reopened, receipts=False) == before


def test_integrity_protocol_seal_unchanged():
    root = Path(__file__).parent / 'fixtures' / 'integrity_v5'
    raw = (root / 'protocol_v1.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (root / 'protocol_v1.sha256').read_text().strip()
    protocol = json.loads(raw)
    assert protocol['b3']['generic_single_predicates'] == ['default', 'other', 'unknown']
    assert protocol['b3']['persistent_identity_rewrite'] is False
    assert protocol['validation']['cases'] == 40
