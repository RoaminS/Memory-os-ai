"""S4 historical-copy recovery. Current authority is read-only; only a copy changes.

Requires a same-lineage live authority, complete immutable-prefix comparison,
explicit tombstone proof, one recovery transaction and a new store identity.
There is no production pointer replacement or automatic replay of client calls.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import time
import uuid

from ..sqlite_engine import SQLiteRequests
from ..sqlite_store import SQLiteStore, _json
from ..store import MemoryItem
from .backup import BackupError
from .sqlite_backup import inventory, plain_path, readonly
from .storecheck import loads_strict


def digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def rows(con, table):
    return con.execute(f'SELECT * FROM "{table}" ORDER BY 1,2').fetchall()


def prefix(source, original, guard):
    """Immutable snapshot records must still exist exactly in current authority."""
    for table, keys in (('item_versions', ('item_id', 'revision')),
                        ('block_versions', ('sequence',)), ('legacy_files', ('path',)),
                        ('legacy_request_log', ('ordinal',)),
                        ('legacy_pending_requests', ('scope', 'request_id'))):
        columns = [r[1] for r in original.execute(f'PRAGMA table_info("{table}")')]
        positions = [columns.index(k) for k in keys]
        where = ' AND '.join('"' + key + '"=?' for key in keys)
        for row in original.execute(f'SELECT * FROM "{table}"'):
            guard()
            current = source.execute(f'SELECT * FROM "{table}" WHERE {where}', tuple(row[i] for i in positions)).fetchone()
            if current != row:
                raise BackupError('Authority does not contain the immutable snapshot history')
    # Implicit receipts can be purged. Explicit completed receipts cannot.
    for row in original.execute("SELECT * FROM request_receipts WHERE COALESCE(json_extract(record_json,'$.explicit'),1)<>0"):
        guard()
        if source.execute('SELECT * FROM request_receipts WHERE sequence=?', (row[0],)).fetchone() != row:
            raise BackupError('Explicit snapshot receipt missing/divergent in authority')


def authority_cut(source_db, expected, guard=lambda: None):
    source = readonly(source_db)
    try:
        source.execute('BEGIN')
        row = source.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()
        meta = loads_strict(row[0].encode()) if row else {}
        sequence = source.execute('SELECT value FROM receipt_counter WHERE id=1').fetchone()[0]
        guard()
        if (meta.get('store_instance_id'), sequence) != (expected['source_store_instance_id'], expected['source_receipt_sequence']):
            raise BackupError('Authority changed after reconciliation; no reopening')
    finally:
        source.close()


def prepare_recovery(target_db, source_db, manifest, *, guard=lambda: None, fault=lambda point: None):
    target_db, source_db = plain_path(target_db), plain_path(source_db)
    if (target_db == source_db or target_db.parent == source_db.parent
            or target_db.is_relative_to(source_db.parent) or source_db.is_relative_to(target_db.parent)):
        raise BackupError('Recovery target must be outside current authority')
    source = readonly(source_db)
    original = readonly(target_db, sealed=True)
    try:
        source.execute('BEGIN')
        current = inventory(source, guard)
        snapshot = inventory(original, guard)
        if snapshot != manifest['logical_inventory']:
            raise BackupError('Unverified snapshot cannot be recovered')
        if (current['store_instance_id'] != snapshot['store_instance_id']
                or current['snapshot_receipt_sequence'] < snapshot['snapshot_receipt_sequence']):
            raise BackupError('Missing or incompatible authority lineage/cut; quarantine retained')
        prefix(source, original, guard)
        tombstones = {}
        for item_id, revision, record, checksum in source.execute('SELECT item_id,revision,record_json,record_sha256 FROM item_versions ORDER BY item_id,revision'):
            guard()
            value = loads_strict(record.encode())
            if value.get('deleted') is True:
                tombstones[item_id] = {'item_id': item_id, 'project': value['project'],
                                       'source_revision': revision, 'source_record_sha256': checksum}
        for item_id in tombstones:
            live = source.execute('SELECT v.record_json FROM items i JOIN item_versions v '
                                  'ON v.item_id=i.id AND v.revision=i.current_revision WHERE i.id=?', (item_id,)).fetchone()
            if not live or loads_strict(live[0].encode()).get('deleted') is not True:
                raise BackupError('Ambiguous tombstone history; explicit policy required')
        proof = {'source_store_instance_id': current['store_instance_id'],
                 'source_receipt_sequence': current['snapshot_receipt_sequence'],
                 'source_inventory_sha256': digest(current), 'tombstones_sha256': digest(tombstones),
                 'tombstones_count': len(tombstones), 'snapshot_inventory_sha256': digest(snapshot),
                 'snapshot_receipt_sequence': snapshot['snapshot_receipt_sequence'],
                 'captured_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                 'empty_list_proof': 'complete immutable history scanned' if not tombstones else None}
        saved = {table: rows(original, table) for table in
                 ('item_versions', 'block_versions', 'request_receipts', 'embedding_cache',
                  'legacy_files', 'legacy_request_log', 'legacy_pending_requests', 'schema_migrations')}
        old_meta = loads_strict(original.execute("SELECT value_json FROM store_metadata WHERE key='metadata'").fetchone()[0].encode())
    finally:
        original.close()
        source.close()
    fault('after_reconciliation_proof')
    authority_cut(source_db, proof, guard)
    store = SQLiteStore(target_db.parent)  # Only the verified private target, never authority.
    try:
        store.fault_hook = fault
        requests = SQLiteRequests(store)
        requests.implicit_limit = len(requests.records) + 1  # Preserve every snapshot receipt in recovery.
        new_id, recovery_id = uuid.uuid4().hex, 's4-recovery-' + uuid.uuid4().hex
        replayed = []
        with store.transaction():
            for item_id, tombstone in sorted(tombstones.items()):
                guard()
                item = store.get(item_id)
                if item is None:
                    outcome = 'absent_from_snapshot'
                else:
                    if item.project != tombstone['project']:
                        raise BackupError('Tombstone project mismatch')
                    outcome = 'already_deleted' if item.deleted else 'deleted'
                    if not item.deleted:
                        # Preserve old known fields; append() also retains unknown legacy fields.
                        item = MemoryItem.from_dict(item.to_dict())
                        item.deleted = True
                        store.append(item)
                replayed.append({**tombstone, 'outcome': outcome})
            store.save_chunk_map({k: v for k, v in store.chunk_owner.items()
                                  if v not in tombstones and k not in tombstones})
            event = {'type': 's4_restore_recovery', 'request_id': recovery_id,
                     'restored_from_instance_id': snapshot['store_instance_id'],
                     'store_instance_id': new_id, 'reconciliation': proof,
                     'forgets_replayed': replayed, 'committed_at': time.time()}
            meta = {**store.meta, 'store_instance_id': new_id,
                    'restored_from_instance_id': snapshot['store_instance_id'],
                    'recovery_history': [*store.meta.get('recovery_history', []), event]}
            store.save_meta(meta)
            result = {'reconciliation': proof, 'forgets_replayed': replayed,
                      'store_instance_id': new_id, 'restored_from_instance_id': snapshot['store_instance_id'],
                      'recovery_request_id': recovery_id, 'recovery_event_sha256': digest(event),
                      'forget_reconciliation': 'complete', 'quarantined': True,
                      'reopening_allowed': False, 'model': old_meta.get('embed_model'),
                      'dim': old_meta.get('dim'), 'encoding_profile': snapshot['encoding_profile']}
            requests.complete('operator:s4', recovery_id, 's4_restore_recovery', proof, result,
                              attestation={'store_instance_id': new_id, 'raw_args_sha256': digest(proof)})
        fault('before_recovery_ack')
        # Verify old histories/receipts/cache survived and the only new records are control/tombstones.
        for table, before in saved.items():
            after = rows(store._connection, table)
            if table in {'item_versions', 'request_receipts'}:
                if not set(before).issubset(after):
                    raise BackupError('Historical records lost during recovery')
                additions = len(after) - len(before)
                expected = sum(r['outcome'] == 'deleted' for r in replayed) if table == 'item_versions' else 1
                if additions != expected:
                    raise BackupError('Unexpected recovery records')
            elif after != before:
                raise BackupError('Recovery changed unrelated records')
        actual_meta = dict(store.meta)
        for key in ('store_instance_id', 'restored_from_instance_id', 'recovery_history'):
            actual_meta.pop(key, None)
        baseline = {k: v for k, v in old_meta.items() if k not in {'store_instance_id', 'restored_from_instance_id', 'recovery_history'}}
        if actual_meta != baseline:
            raise BackupError('Recovery changed unrelated metadata')
        inventory(store._connection, guard)
        return result
    finally:
        store.fault_hook = None
        store.close()
