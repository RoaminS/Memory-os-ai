"""SQLite source adapters. Only MemoryV4 owns the transaction/acknowledgement.

There is no durable pending reservation: either effects and the final receipt
commit together, or neither exists. An ambiguous COMMIT requires reopening.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
import time

from .blocks import Block, BlockStore
from .idempotency import RequestConflictError, RequestIncompleteError
from .sqlite_store import _json
from .sqlite_stats import IMPLICIT_RECEIPT_LIMIT


def json_value(value):
    if hasattr(value, "to_dict"):
        return json_value(value.to_dict())
    if is_dataclass(value):
        return json_value(asdict(value))
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    return value


def snapshot(value):
    """Validate and detach a request payload before its action can mutate it."""
    return json.loads(_json(json_value(value)))


def fingerprint(operation, payload):
    return hashlib.sha256(
        _json({"operation": operation, "payload": json_value(payload)}).encode()
    ).hexdigest()


class SQLiteBlocks(BlockStore):
    def __init__(self, store, char_limit):
        self.store = store
        self.mutate = None
        super().__init__(store.db_path, char_limit)

    def load(self):
        self._blocks.clear()
        self._history.clear()
        for block_key, record, checksum in self.store._connection.execute(
            "SELECT block_key,record_json,record_sha256 FROM block_versions ORDER BY sequence"
        ):
            if hashlib.sha256(record.encode()).hexdigest() != checksum:
                raise RuntimeError("SQLite block checksum mismatch")
            block = Block(**{key: value for key, value in json.loads(record).items() if key in Block.__dataclass_fields__})
            if (
                block.scope not in {"global", "project"}
                or not isinstance(block.name, str)
                or not isinstance(block.text, str)
                or (block.scope == "global" and block.project is not None)
                or (
                    block.scope == "project"
                    and (not isinstance(block.project, str) or not block.project or block.project == "*")
                )
            ):
                raise RuntimeError("Invalid SQLite block record")
            key = (block.project, block.name)
            if _json(key) != block_key:
                raise RuntimeError("SQLite block identity mismatch")
            self._history[key].append(block)
            self._blocks[key] = block

    def set(self, name, text, project="default", scope="project"):
        def apply():
            target = self._target(project, scope)
            if (
                not isinstance(name, str)
                or not name
                or not isinstance(text, str)
                or (target is not None and (not isinstance(target, str) or not target))
            ):
                raise ValueError("Invalid block name/text/project")
            block = Block(name, text[: self.char_limit], time.time(), target, scope)
            key = (target, name)
            previous = self.store._connection.execute(
                "SELECT record_json FROM block_versions WHERE block_key=? ORDER BY sequence DESC LIMIT 1", (_json(key),)).fetchone()
            record = _json({**(json.loads(previous[0]) if previous else {}), **asdict(block)})
            self.store.write(
                "block_version",
                "INSERT INTO block_versions(block_key,record_json,record_sha256) VALUES(?,?,?)",
                (_json(key), record, hashlib.sha256(record.encode()).hexdigest()),
            )
            self._blocks[key] = block
            self._history[key].append(block)

        if self.mutate is None:
            raise RuntimeError("SQLite blocks must be bound to a memory engine")
        return self.mutate(
            "block_set", {"name": name, "text": text, "project": project, "scope": scope}, apply
        )

    def append(self, name, text, project="default", scope="project"):
        # Obtain the current text under the engine lock, before concatenating.
        def apply():
            current = self._blocks.get((self._target(project, scope), name))
            combined = f"{current.text}\n{text}".strip() if current and current.text else text
            return self.set(name, combined, project, scope)

        return self.mutate(
            "block_append", {"name": name, "text": text, "project": project, "scope": scope}, apply
        )

    def check_integrity(self):
        expected = SQLiteBlocks(self.store, self.char_limit)
        return expected._blocks == self._blocks and expected._history == self._history


class SQLiteRequests:
    implicit_limit = IMPLICIT_RECEIPT_LIMIT
    def __init__(self, store):
        self.store = store
        self.path = store.db_path
        self.load()

    @property
    def _poisoned(self):
        return self.store._poisoned

    def load(self):
        self.records = {}
        for sequence, scope, request_id, record, checksum in self.store._connection.execute(
            "SELECT sequence,scope,request_id,record_json,record_sha256 FROM request_receipts ORDER BY sequence"
        ):
            if hashlib.sha256(record.encode()).hexdigest() != checksum:
                raise RuntimeError("SQLite receipt checksum mismatch")
            value = json.loads(record)
            if (
                value.get("state") != "complete"
                or (value.get("explicit", True) and "result" not in value)
                or not isinstance(value.get("fingerprint"), str)
                or (value.get("sequence"), value.get("scope"), value.get("request_id"))
                != (sequence, scope, request_id)
            ):
                raise RuntimeError("Invalid SQLite completed receipt")
            self.records[(scope, request_id)] = value
        for scope, request_id, record, checksum in self.store._connection.execute(
            "SELECT scope,request_id,record_json,record_sha256 FROM legacy_pending_requests"
        ):
            value = json.loads(record)
            if (hashlib.sha256(record.encode()).hexdigest() != checksum or value.get("state") != "pending"
                    or (value.get("scope"), value.get("request_id")) != (scope, request_id)
                    or not isinstance(value.get("fingerprint"), str)
                    or (scope, request_id) in self.records):
                raise RuntimeError("Invalid legacy uncertain receipt")
            self.records[(scope, request_id)] = value

    def lookup(self, scope, request_id, operation, payload):
        previous = self.records.get((scope, request_id))
        if previous is None:
            return False, None
        if previous["fingerprint"] != fingerprint(operation, payload):
            raise RequestConflictError(
                "request_id already used with a different operation or payload in this scope"
            )
        if previous.get("state") != "complete":
            raise RequestIncompleteError("Legacy request has an uncertain outcome; reconcile it before any retry")
        if not previous.get("explicit", True):
            raise RequestConflictError("Implicit receipt has no replay result; clients must use explicit request_id")
        return True, json.loads(_json(previous["result"]))

    def complete(self, scope, request_id, operation, payload, result, *, implicit=False, attestation=None):
        # Retention precedes the final receipt; it rolls back with the request.
        retain = self.implicit_limit - int(implicit)
        removed = self.store.write("receipt_retention", "DELETE FROM request_receipts WHERE "
                                   "json_extract(record_json,'$.explicit')=0 AND sequence NOT IN "
                                   "(SELECT sequence FROM request_receipts WHERE json_extract(record_json,'$.explicit')=0 "
                                   "ORDER BY sequence DESC LIMIT ?) RETURNING scope,request_id", (retain,)).fetchall()
        for key in removed:
            self.records.pop(tuple(key), None)
        sequence = self.store.write("receipt_sequence", "UPDATE receipt_counter SET value=value+1 WHERE id=1 RETURNING value").fetchone()[0]
        value = {
            "scope": scope,
            "request_id": request_id,
            "operation": operation,
            "fingerprint": fingerprint(operation, payload),
            "state": "complete",
            "sequence": sequence,
            "committed_at": time.time(),
            "explicit": not implicit,
            **(attestation or {}),
        }
        if not implicit:
            value["result"] = json_value(result)
        record = _json(value)
        self.store.write(
            "receipt",
            "INSERT INTO request_receipts VALUES(?,?,?,?,?)",
            (sequence, scope, request_id, record, hashlib.sha256(record.encode()).hexdigest()),
        )
        self.records[(scope, request_id)] = value

    def check_integrity(self):
        return not self._poisoned and SQLiteRequests(self.store).records == self.records
