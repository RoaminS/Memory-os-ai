"""L3 relay policy on top of the single transport-owner queue."""
from __future__ import annotations

import hashlib
import json
import os
import uuid

import mcp.types as types

from ..idempotency import _json, raw_args_sha256
from .request_journal import RelayJournal, directory, key

READS = {"memory_status", "memory_request_status", "memory_session_brief", "memory_search",
         "memory_get_context", "memory_timeline", "memory_block_get", "memory_list_documents", "memory_search_occurrences"}

MUTATIONS = {"memory_chat_save", "memory_add_facts", "memory_block_set", "memory_forget",
             "memory_ingest", "memory_migrate_v3", "memory_consolidate", "memory_compact"}


def body(result):
    if result.isError:
        raise RuntimeError("Upstream control call failed")
    data = result.structuredContent
    if data is None:
        data = json.loads("".join(c.text for c in result.content if isinstance(c, types.TextContent)))
    if not isinstance(data, dict) or data.get("ok") is False:
        raise RuntimeError("Upstream control contract unavailable")
    return data


def response(data):
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False))],
                                structuredContent=data)


class DurableRelay:
    def __init__(self, transport, settings):
        self.transport, self.settings = transport, settings
        self.journal = None
        self.identity = None

    def ledger(self):
        if self.journal is None:
            self.journal = RelayJournal(directory(self.settings), self.settings.url,
                                        os.environ.get("MEMORY_V4_CLIENT_ID", "unknown"),
                                        max_bytes=int(os.environ.get("MEMORY_V4_RELAY_JOURNAL_MAX_BYTES", 10 * 1024**2)),
                                        max_days=float(os.environ.get("MEMORY_V4_RELAY_JOURNAL_MAX_DAYS", 30)))
        return self.journal

    def close(self):
        if self.journal is not None:
            self.journal.close()

    async def _status(self, session, row):
        arguments = {k: row[k] for k in ("request_id", "scope", "operation", "raw_args_sha256", "store_instance_id")}
        data = body(await session.call_tool("memory_request_status", arguments))
        if (data.get("contract") != "l3-v2" or data.get("store_instance_id") != row["store_instance_id"]
                or data.get("request_id") != row["request_id"] or data.get("replay_permitted") is not False):
            raise RuntimeError("identity_changed or receipt contract unavailable; no replay")
        if data.get("status") == "completed":
            if "result" not in data:
                raise RuntimeError("Invalid completed receipt; no replay")
            self.ledger().append({**row, "result_sha256": hashlib.sha256(_json(data["result"]).encode()).hexdigest()}, "completed")
        return data

    async def call(self, name, arguments):
        args = dict(arguments or {})
        journal = self.ledger()
        if name == "memory_request_status":
            if not args.get("request_id"):
                return response(journal.status())
            # Includes resolved local entries so an explicit retry is a status read, never a replay.
            from .request_journal import scan
            records = scan(journal.path.parent, exclude=journal.path)["entries"] + list(journal.records.values())
            matches = {key(r): r for r in records if r["request_id"] == args["request_id"]
                       and r["endpoint_sha256"] == journal.endpoint
                       and all(args.get(k) in (None, r[k]) for k in ("scope", "raw_args_sha256", "store_instance_id"))}
            if len(matches) != 1:
                return response({"status": "unknown", "reason": "local_identity_missing_or_ambiguous", "replay_permitted": False})
            row = next(iter(matches.values()))
            return response(await self.transport.submit(lambda s: self._status(s, row), replay=True))
        mutation = name in MUTATIONS
        if name in {"memory_consolidate", "memory_compact"} and args.get("dry_run", True) is True:
            mutation = False
        if not mutation:
            result = await self.transport.submit(lambda s: s.call_tool(name, args), replay=name in READS)
            if name == "memory_session_brief":
                report = journal.status()
                if report["unresolved_count"] or report["errors"] or report["journal_poisoned"]:
                    data = body(result)
                    notice = (f"{report['unresolved_count']} uncertain operations; see memory_request_status. "
                              f"Journal errors: {len(report['errors'])}; never repeat unresolved mutations.")
                    data["request_status_notice"] = notice
                    data["text"] = str(data.get("text", "")) + "\n" + notice
                    result = response(data)
            return result
        request_id = args.get("request_id") or uuid.uuid4().hex
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ValueError("Invalid logical request_id; nothing sent")
        args["request_id"] = request_id
        raw = raw_args_sha256(args)
        captured = {"code": "preflight_unavailable"}

        async def send(session):
            # Server resolves defaults/scope. This is READ ONLY and is never the mutation itself.
            checked = await session.call_tool("memory_request_status", {"operation": name, "arguments": args})
            # Preserve a server-authorized preflight refusal; no mutation was sent.
            if not checked.isError:
                checked_data = checked.structuredContent
                if checked_data is None:
                    try:
                        checked_data = json.loads("".join(c.text for c in checked.content if isinstance(c, types.TextContent)))
                    except ValueError:
                        checked_data = None
                if isinstance(checked_data, dict) and checked_data.get("code") == "maintenance_denied":
                    return checked
            captured["code"] = "legacy_unattested"
            preflight = body(checked)
            if (preflight.get("contract") != "l3-v2" or not preflight.get("store_instance_id")
                    or preflight.get("raw_args_sha256") != raw or not preflight.get("scope")
                    or preflight.get("replay_permitted") is not False):
                raise RuntimeError("legacy_unattested: mutation not sent; upgrade the server contract")
            identity = preflight["store_instance_id"]
            if self.identity is not None and identity != self.identity:
                captured["code"] = "identity_changed"
                raise RuntimeError("identity_changed: restart client after operator review; mutation not sent")
            self.identity = identity
            row = {k: preflight[k] for k in ("scope", "operation", "raw_args_sha256", "store_instance_id")}
            row["request_id"] = request_id
            captured.update(row)
            from .request_journal import scan
            report = scan(journal.path.parent, exclude=journal.path)
            if report["errors"] or journal.poisoned:
                captured["code"] = "journal_integrity_uncertain"
                raise RuntimeError("Journal integrity uncertain: mutation not sent")
            records = report["entries"] + list(journal.records.values())
            prior = [r for r in records if r["endpoint_sha256"] == journal.endpoint
                     and r["request_id"] == request_id and r["scope"] == row["scope"]]
            if prior:
                if any(r["raw_args_sha256"] != raw or r["store_instance_id"] != identity
                       or r["operation"] != row["operation"] for r in prior):
                    captured["code"] = "request_conflict"
                    raise RuntimeError("Logical request identity conflict; mutation not sent")
                status = await self._status(session, row)
                if status["status"] == "completed":
                    return response(status["result"])
                captured["code"] = "unresolved_request"
                raise RuntimeError("Unresolved logical request; mutation not sent, no replay")
            captured["code"] = "journal_not_durable"
            journal.append(row, "prepared")
            journal.append(row, "sending")  # fsync must succeed BEFORE entering call_tool.
            captured["code"] = "outcome_uncertain"
            result = await session.call_tool(name, args)
            # Tool errors may follow partial writes: only a durable receipt can resolve them.
            try:
                data = body(result)
            except (ValueError, RuntimeError):
                journal.append(row, "blocked")
                return result
            captured["code"] = "completion_not_durable"
            journal.append({**row, "result_sha256": hashlib.sha256(_json(data).encode()).hexdigest()}, "completed")
            return result

        try:
            return await self.transport.submit(send, replay=False)
        except BaseException as exc:
            # Cancellation keeps the last durable sending record. Never swallow cancellation.
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError(f"memory-os: request_id={request_id}; code={captured['code']}; outcome uncertain or refused. "
                               "Use memory_request_status; do not repeat the mutation.") from exc
