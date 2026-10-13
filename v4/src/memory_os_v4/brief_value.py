"""Frozen P-A value order. Provenance is declarative, never authentication."""
import unicodedata


def client(value):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize('NFKC', value).strip().casefold()
    return value if value and value != 'unknown' else None


def origin(item):
    values = [tag[7:] for tag in item.tags if tag.startswith('client:')]
    if len(values) > 1:
        return None
    if item.source and item.source.startswith('client:'):
        values.append(item.source[7:])
    # Conflicting / unknown declarations do not establish another client.
    normalized = {client(value) for value in values}
    return next(iter(normalized)) if len(normalized) == 1 else None


def memory_order(item, expanded, scores, caller):
    tags = {tag.casefold() for tag in item.tags}
    if item.id in expanded:
        tier = 0
    elif {'constraint', 'critical'} <= tags:
        tier = 1
    elif {'decision', 'constraint'} & tags:
        tier = 4
    elif item.kind == 'procedure':
        tier = 5
    elif {'change', 'update'} & tags and client(caller) and origin(item) and origin(item) != client(caller):
        tier = 6
    else:
        tier = 7
    return (tier, -scores.get(item.id, 0), -item.importance, -item.event_time, item.id)


def task_order(task, requested):
    reference = 'task:' + task['task_id']
    tier = (0 if reference in requested else 8 if task['stale'] or task['status'] == 'done'
            else 2 if task['status'] == 'blocked' else 3)
    from datetime import datetime
    return (tier, 0, 0, -datetime.fromisoformat(task['updated_at']).timestamp(), reference)
