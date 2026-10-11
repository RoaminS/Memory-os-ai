"""Read-only aggregation of client-exported relay observations, never a live probe."""
import hashlib
import json
import os
from pathlib import Path
import time

from .clients import CLIENTS


def artifact():
    from .. import __version__
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("relay.py", "durable_relay.py", "request_journal.py", "connection_proof.py"):
        digest.update(name.encode() + b"\0" + (root / name).read_bytes())
    return {"version": __version__, "source_sha256": digest.hexdigest()}


def inspect_proofs(settings):
    """Operator saves connection fields returned by each actual client. No inferred installation."""
    path = os.environ.get("MEMORY_V4_CLIENT_PROOFS")
    records = []
    if path:
        try:
            raw = Path(path).read_bytes()
            if len(raw) > 262144:
                raise ValueError("oversize")
            data = json.loads(raw)
            if data.get("schema_version") != 1 or not isinstance(data.get("connections"), list):
                raise ValueError("format")
            records = data["connections"]
        except (OSError, ValueError, TypeError, AttributeError):
            return [{"area": "client-proofs", "status": "FAIL", "detail": "invalid client proof file"}]
    endpoint = hashlib.sha256(settings.url.encode()).hexdigest()
    result = []
    for client in CLIENTS:
        candidates = [r for r in records if isinstance(r, dict) and r.get("client_id") == client.key]
        valid = []
        for r in candidates:
            try:
                age = time.time() - float(r["observed_at"])
                if (0 <= age <= 300 and r["endpoint_sha256"] == endpoint and r["successful_control"] is True
                        and r["relay"] == artifact() and isinstance(r["server"]["version"], str)
                        and r["server"]["version"] == r["relay"]["version"]):
                    valid.append(r)
            except (KeyError, ValueError, TypeError):
                pass
        result.append({"area": "connection:" + client.key, "status": "PASS" if valid else "WARN",
                       "detail": "recent client-exported control observation; not a live probe" if valid else
                       "unknown: no fresh matching client/endpoint/artifact observation"})
    return result
