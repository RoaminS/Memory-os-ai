#!/usr/bin/env python3
"""Offline S3 adapter for qualified 4.0.5; preserve modern L3 receipt attestations.

Normal migration preserves identity; this is NOT restore/recovery. Uses the
installed S3 converter, inserts attested translated receipts in the original S3 transaction,
verifies exhaustive original records, then publishes a fresh directory once.
No launchctl, live-store engine, backend activation or direct production write.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
import time
import uuid


def migrate_with_attestation(source, destination, *, apply=False, fault=lambda point: None):
    from memory_os_v4 import __version__
    from memory_os_v4 import sqlite_migrate as s3
    from memory_os_v4.sqlite_store import SQLiteStore, _json
    from memory_os_v4.writer_lock import WriterLock
    if __version__ != '4.0.5':
        raise ValueError('Adapter qualified for installed 4.0.5 only')
    source, destination = s3._paths(source, destination)
    if destination.exists():
        raise ValueError('Fresh destination required; never reuse a prepared or active authority')
    simulation = s3.migrate(source, destination)
    with s3.offline_lease(source):
        files = s3._files(source)
        expected = s3._inventory(files)
    before = s3._file_manifest(files)
    identity = expected['meta'].get('store_instance_id')
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
        raise ValueError('Existing enrolled source identity required; no restore/enrolment')
    if not apply:
        return {**simulation, 'l3_attestations_preserved': True, 'store_instance_id': identity}
    stage = destination.parent / ('.' + destination.name + '.activation-' + uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    target = stage / 'authority'
    started = time.monotonic()
    try:
        # Scope this store factory to the single offline converter only. Runtime
        # engine classes/files remain unchanged. Transform the FIRST INSERT;
        # never UPDATE/DELETE an immutable receipt or disable a schema trigger.
        preserved = 0
        original_factory = s3.SQLiteStore
        class AttestedImportStore(SQLiteStore):
            def write(self, label, sql, parameters=()):
                nonlocal preserved
                attested = False
                if label == 'legacy_complete':
                    sequence, scope, request_id, record, checksum = parameters
                    value = json.loads(record)
                    legacy = value.get('legacy_record', {})
                    fields = {k: legacy[k] for k in ('raw_args_sha256', 'store_instance_id') if k in legacy}
                    if fields:
                        value.update(fields)
                        record = _json(value)
                        parameters = (sequence, scope, request_id, record, hashlib.sha256(record.encode()).hexdigest())
                        preserved += 1
                        attested = True
                result = super().write(label, sql, parameters)
                if attested:
                    fault('after_receipt_attestation')
                return result
        try:
            s3.SQLiteStore = AttestedImportStore
            migration = s3.migrate(source, target, apply=True)
        finally:
            s3.SQLiteStore = original_factory
        if migration['files'] != before:
            raise ValueError('Source changed between simulation and import')
        fault('after_s3_import')
        fault('after_attestation_commit')
        with closing(WriterLock(target)):
            store = SQLiteStore(target)
            try:
                comparison = s3._compare(store, files, expected)
                from memory_os_v4.sqlite_engine import SQLiteRequests
                receipts = SQLiteRequests(store).records
                for key, original in expected['last_requests'].items():
                    for field in ('raw_args_sha256', 'store_instance_id'):
                        if original.get(field) != receipts[key].get(field):
                            raise ValueError('L3 receipt attestation lost during migration')
                if store.meta.get('store_instance_id') != identity:
                    raise ValueError('Normal migration changed source identity')
                migration.update(comparison=comparison, authority_sha256=s3._authority_digest(store._connection),
                    activation_adapter={'version': 1, 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        'l3_receipts_preserved': preserved, 'normal_migration_identity_preserved': True})
                store._connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:
                store.close()
        with (target / 'migration.json').open('w') as fh:
            fh.write(_json(migration) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        s3._fsync_directory(target)
        with s3.offline_lease(source):
            if s3._file_manifest(s3._files(source)) != before:
                raise ValueError('Source changed before final publication')
            fault('before_activation_publish')
            s3._publish(target, destination)
        fault('after_activation_publish')
        stage.rmdir()  # Empty successful staging directory only.
        return {**migration, 'migration_seconds': time.monotonic() - started}
    except Exception:
        # Retain bounded failed staging evidence. Never activate or reuse it.
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from source_release_checks import resources
    resources(args.destination.parent)
    if os.getpriority(os.PRIO_PROCESS, 0) < 15:
        raise ValueError('nice 15 required')
    result = migrate_with_attestation(args.source, args.destination, apply=args.apply)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
