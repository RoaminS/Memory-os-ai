"""Authenticated private read-only HTTP reopen after S4 recovery, never promotion."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

from .. import __version__
from ..store import MemoryItem
from ..temporal import is_valid_at
from .auth import create_token, http_get, call_tool
from .backup import BackupError
from .settings import OpsSettings
from .sqlite_backup import readonly
from .sqlite_recovery import authority_cut


def probe(root: Path, settings, recovery, *, guard=lambda: None):
    import anyio

    commit = os.environ.get('MEMORY_V4_BUILD_COMMIT', '')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise BackupError('Private ready requires an explicit qualified MEMORY_V4_BUILD_COMMIT')
    model, dim, profile = recovery.get('model'), recovery.get('dim'), recovery.get('encoding_profile')
    if not isinstance(model, str) or not isinstance(dim, int) or dim <= 0 or not isinstance(profile, dict):
        raise BackupError('Encoding profile unavailable; private reopen refused')
    authority_cut(settings.store_dir / 'store.sqlite3', recovery['reconciliation'], guard)
    con = readonly(root / 'store.sqlite3', sealed=True)
    try:
        live = [MemoryItem.from_dict(json.loads(row[0])) for row in con.execute(
            "SELECT v.record_json FROM items i JOIN item_versions v ON v.item_id=i.id AND v.revision=i.current_revision WHERE COALESCE(json_extract(v.record_json,'$.deleted'),0)=0")]
    finally:
        con.close()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    token = root.parent / 'private-http.token'
    create_token(token)  # New private token, never the production token.
    private = OpsSettings(**{**settings.__dict__, 'store_dir': root, 'token_file': token,
                             'url': f'http://127.0.0.1:{port}/mcp/', 'service_label': None})
    env = {**os.environ, 'MEMORY_V4_DIR': str(root), 'MEMORY_V4_TOKEN_FILE': str(token),
           'MEMORY_V4_URL': private.url,
           'MEMORY_V4_STORE_BACKEND': 'sqlite_s1', 'MEMORY_V4_EMBED_MODEL': model,
           'MEMORY_V4_HASH_DIM': str(dim), 'MEMORY_V4_PROJECT': '*',
           'MEMORY_V4_WRITE_MODE': 'read_only', 'MEMORY_V4_MAINTENANCE_MODE': 'disabled',
           'MEMORY_V4_DEVICE': profile['device'] if profile.get('device') != 'hash' else 'auto',
           'MEMORY_V4_EMBED_DTYPE': str(profile['dtype']),
           'MEMORY_V4_EMBED_BATCH_SIZE': str(profile['batch_size']),
           'MEMORY_V4_EMBED_MAX_SEQ_LENGTH': str(profile['max_seq_length']),
           'PYTHONDONTWRITEBYTECODE': '1', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
           'MEMORY_V4_OPS_DIR': str(root.parent / 'private-ops'),
           'MEMORY_V4_RELAY_JOURNAL_DIR': str(root.parent / 'private-relay')}
    env.pop('MEMORY_V4_PROJECT_REGISTRY', None)  # Current production registry is inactive; no external alias rewrite.
    start = time.monotonic()
    log_path = root.parent / 'private-server.log'
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as log:
        proc = subprocess.Popen([sys.executable, '-B', '-m', 'memory_os_v4.server', '--transport', 'http',
                                 '--host', '127.0.0.1', '--port', str(port), '--warm', '--read-only'],
                                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        while time.monotonic() - start < 240:
            guard()
            if proc.poll() is not None:
                raise BackupError('Private restore server exited before ready; log preserved')
            status, body = http_get(private.ready_url, private, timeout=1)
            if status == 200:
                ready = json.loads(body)
                break
            time.sleep(0.1)
        else:
            raise BackupError('Private restore ready exceeded 240 s')
        capabilities = ready['capabilities']
        if (ready.get('version'), ready.get('commit'), capabilities['store']['instance_id'],
                capabilities['encoding']['profile'], capabilities['policies']['writes']['mode']) != (
                __version__, commit, recovery['store_instance_id'], profile, 'read_only'):
            raise BackupError('Private ready identity/profile/policy differs from recovery proof')
        async def checks():
            with anyio.fail_after(30):
                status = await call_tool('memory_status', {'project': '*'}, private)
                if status.get('total') != len(live) or status.get('embedding_cache', {}).get('misses') != 0:
                    raise BackupError('Private restore count/cache mismatch; rebuild requires a separate qualification')
                witnesses = []
                for item in (i for i in live if is_valid_at(i, time.time()) and not i.invalidated_by):
                    guard()
                    result = await call_tool('memory_search', {'project': item.project,
                                             'query': ' '.join(item.text.split()[:24]), 'top_k': 10}, private)
                    if item.id not in {h.get('id') for h in result.get('hits', [])}:
                        raise BackupError('Restored witness absent from search')
                    witnesses.append(item.id)
                    if len(witnesses) == 3:
                        break
                old = await call_tool('memory_request_status', {'request_id': recovery['recovery_request_id'],
                                      'store_instance_id': recovery['restored_from_instance_id']}, private)
                if old.get('reason') != 'store_identity_missing_or_changed':
                    raise BackupError('Historical client identity was accepted unexpectedly')
                return status, witnesses
        status, witnesses = anyio.run(checks)
        authority_cut(settings.store_dir / 'store.sqlite3', recovery['reconciliation'], guard)
        return {'match': True, 'ready_status': 200, 'version': __version__, 'commit': commit,
                'store_instance_id': recovery['store_instance_id'], 'expected_total': len(live),
                'status_total': status['total'], 'corpus_reencodings': status['embedding_cache']['misses'],
                'witnesses': witnesses, 'duration_s': round(time.monotonic() - start, 3),
                'production_modified': False, 'private_only': True}
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
