"""Exhaustive no-migration evidence. No authority/cache/journal exemption."""
import json
from schema8_inventory import fingerprint


def verify_unchanged(before, after):
    if before['schema'] != 8 or after['schema'] != 8 or before != after:
        raise ValueError('Schema8 authority, cache, journal or SQL objects changed')
    metadata = json.loads(dict(before['tables']['store_metadata'])['metadata'])
    if 'change_events' not in before['tables']:
        raise ValueError('Schema8 change journal missing')
    return {'ok':True, 'from_schema':8, 'to_schema':8, 'migration_required':False,
            'store_instance_id':metadata['store_instance_id'], 'inventory_sha256':fingerprint(before),
            'tables_preserved':{name:{'rows':len(rows),'sha256':fingerprint(rows)}
                                for name,rows in before['tables'].items()}}
