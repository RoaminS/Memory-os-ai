"""L3 relay policy on top of the single transport-owner queue."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import time

import mcp.types as types

from .. import __version__
from ..idempotency import _json, raw_args_sha256
from .request_journal import RelayJournal, directory, key

READS = {"memory_status", "memory_request_status", "memory_session_brief", "memory_search",
         "memory_get_context", "memory_timeline", "memory_block_get", "memory_list_documents", "memory_search_occurrences"}

MUTATIONS = {"memory_chat_save", "memory_add_facts", "memory_block_set", "memory_forget",
             "memory_ingest", "memory_migrate_v3", "memory_consolidate", "memory_compact"}
READS.update({'memory_task_get', 'memory_task_list', 'memory_task_preview'})
MUTATIONS.update({'memory_task_create', 'memory_task_update', 'memory_block_compare_and_set'})


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


class PreflightRefusal(RuntimeError):
    """A control read failed before preparing or sending this mutation."""

    def __init__(self, details):
        self.details = details
        super().__init__(json.dumps(details, ensure_ascii=False))


class DurableRelay:
    def __init__(self, transport, settings):
        self.transport, self.settings = transport, settings
        self.journal = None
        self.identity = None
        self.connection_observation = None
        self.preflight_failure = None
        self.brief_wire_formats = {"legacy"}
        self.brief_budget_supported = False

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

    async def control(self, op):
        if hasattr(self.transport, "control"):
            return await self.transport.control(op)
        return await self.transport.submit(op, replay=True)

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
                return response({**journal.status(), "connection": {
                    "last_error": getattr(self.transport, "last_error", None),
                    "observation": self.connection_observation,
                    "live_probe": False, "control_deadline_s": 4,
                    "catalog": "session_snapshot", "relay_version": __version__,
                    "preflight_failure": self.preflight_failure}})
            # Includes resolved local entries so an explicit retry is a status read, never a replay.
            from .request_journal import scan
            records = scan(journal.path.parent, exclude=journal.path)["entries"] + list(journal.records.values())
            matches = {key(r): r for r in records if r["request_id"] == args["request_id"]
                       and r["endpoint_sha256"] == journal.endpoint
                       and all(args.get(k) in (None, r[k]) for k in ("scope", "raw_args_sha256", "store_instance_id"))}
            if len(matches) != 1:
                return response({"status": "unknown", "reason": "local_identity_missing_or_ambiguous", "replay_permitted": False})
            row = next(iter(matches.values()))
            return response(await self.control(lambda s: self._status(s, row)))
        mutation = name in MUTATIONS
        compact_brief = name == "memory_session_brief" and args.get("mode", "compact") == "compact"
        brief_wire = args.get("wire_format", "compact_v2" if "compact_v2" in self.brief_wire_formats else "legacy")
        brief_budget = args.get("budget_tokens")
        def brief_error(data):
            from ..brief import compact_error, envelope
            local = journal.status()
            notices = ["unresolved_requests"] if (local["unresolved_count"] or local["errors"] or local["journal_poisoned"]) else []
            return envelope(compact_error(data, wire_format=brief_wire if brief_wire in {"legacy", "compact_v2"} else "legacy",
                                         budget_tokens=brief_budget, notices=notices))
        upstream_args = args
        if compact_brief:
            from ..brief import compact_error, envelope
            if (brief_wire not in {"legacy", "compact_v2"}
                    or (brief_budget is not None and (not isinstance(brief_budget, int)
                        or isinstance(brief_budget, bool) or not 200 <= brief_budget <= 1500))):
                return envelope(compact_error({"code": "brief_unavailable"}))
            if brief_wire == "compact_v2" and brief_wire not in self.brief_wire_formats:
                return brief_error({"code": "wire_format_unavailable"})
            upstream_args = dict(args)
            if "compact_v2" in self.brief_wire_formats:
                upstream_args["wire_format"] = brief_wire
            else:
                upstream_args.pop("wire_format", None)
            if not self.brief_budget_supported:
                upstream_args.pop("budget_tokens", None)
        if name in {"memory_consolidate", "memory_compact"} and args.get("dry_run", True) is True:
            mutation = False
        if not mutation:
            if name == "memory_status":
                result = await self.control(lambda s: s.call_tool(name, args))
                data = body(result)
                caps = data.get("capabilities")
                if isinstance(caps, dict):
                    from .connection_proof import artifact
                    self.connection_observation = {
                        "client_id": os.environ.get("MEMORY_V4_CLIENT_ID", "unknown"),
                        "endpoint_sha256": hashlib.sha256(self.settings.url.encode()).hexdigest(),
                        "observed_at": time.time(), "successful_control": True,
                        "relay": artifact(), "server": {**caps.get("engine", {}),
                            "store_instance_id": caps.get("store", {}).get("instance_id")}}

            else:
                try:
                    result = await self.transport.submit(lambda s: s.call_tool(name, upstream_args), replay=name in READS)
                except Exception:
                    if name != "memory_session_brief" or args.get("mode", "compact") == "detailed":
                        raise
                    return brief_error({"code": "upstream_unavailable"})
            if name == "memory_session_brief":
                report = journal.status()
                if args.get("mode", "compact") == "compact":
                    data = result.structuredContent
                    if data is None:
                        data = json.loads("".join(c.text for c in result.content if isinstance(c, types.TextContent)))
                    if isinstance(data, dict) and data.get("ok") is False:
                        return brief_error(data)
                    if data.get("mode") == "compact":
                        from ..brief import rebudget, envelope
                        notices = ["unresolved_requests"] if (report["unresolved_count"] or report["errors"] or report["journal_poisoned"]) else []
                        return envelope(rebudget(data, notices, wire_format=brief_wire, budget_tokens=brief_budget))
                    return brief_error({"code": "brief_unavailable"})
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
            checked_data = checked.structuredContent
            if checked_data is None:
                try:
                    checked_data = json.loads("".join(c.text for c in checked.content if isinstance(c, types.TextContent)))
                except ValueError:
                    checked_data = None
            # Preserve a server-authorized preflight refusal; no mutation was sent.
            if not checked.isError:
                if isinstance(checked_data, dict) and checked_data.get("code") in {"maintenance_denied", "project_unknown"}:
                    return checked
            # A timeout, error result or malformed body does not attest an old
            # contract. Only a successful control response can prove its absence.
            if isinstance(checked_data, dict) and checked_data.get("code") == "maintenance_window":
                captured["code"] = "maintenance_window"
                raise RuntimeError("Operator write window; mutation not started")
            if isinstance(checked_data, dict) and checked_data.get("code") == "invalid_arguments":
                captured["code"] = "invalid_arguments"
                raise RuntimeError("Mutation arguments invalid; nothing sent")
            if isinstance(checked_data, dict) and checked_data.get('code') in {'backend_unsupported', 'capability_disabled'}:
                captured['code'] = checked_data['code']
                raise RuntimeError('Work-state capability unavailable; nothing sent')
            if isinstance(checked_data, dict) and checked_data.get("code") in {
                "upstream_not_ready", "engine_not_ready", "engine_initializing"
            }:
                captured["code"] = "upstream_not_ready"
                raise RuntimeError("Upstream is not ready; mutation not sent")
            preflight = body(checked)
            if preflight.get("contract") != "l3-v2":
                captured["code"] = "legacy_unattested"
                raise RuntimeError("Server does not advertise l3-v2")
            captured["code"] = "preflight_invalid"
            operation = "memory_consolidate" if name == "memory_compact" else name
            if (not preflight.get("store_instance_id")
                    or preflight.get("raw_args_sha256") != raw or not preflight.get("scope")
                    or preflight.get("operation") != operation
                    or preflight.get("replay_permitted") is not False):
                raise RuntimeError("Invalid l3-v2 attestation; mutation not sent")
            identity = preflight["store_instance_id"]
            if self.identity is not None and identity != self.identity:
                captured["code"] = "identity_changed"
                raise RuntimeError("identity_changed: restart client after operator review; mutation not sent")
            self.identity = identity
            self.preflight_failure = None
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
                if name in {'memory_task_create', 'memory_task_update', 'memory_block_compare_and_set'}:
                    # A CAS refusal has a final receipt too. Resolve through a
                    # read, never infer completion from an error or replay it.
                    try:
                        resolved = await self._status(session, row)
                        if resolved.get('status') == 'completed':
                            return response(resolved['result'])
                    except Exception:
                        pass  # preserve uncertainty if the receipt is unavailable
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
            if captured["code"] == "preflight_unavailable":
                transport_code = (getattr(self.transport, "last_error", None) or {}).get("code")
                if transport_code in {"authentication_rejected", "token_missing", "token_invalid", "identity_changed"}:
                    captured["code"] = transport_code
            if captured["code"] in {"preflight_unavailable", "upstream_not_ready", "legacy_unattested", "preflight_invalid",
                                    "maintenance_window", "invalid_arguments", "backend_unsupported", "capability_disabled", "authentication_rejected", "token_missing", "token_invalid", "identity_changed"}:
                from .request_journal import scan
                report = scan(journal.path.parent, exclude=journal.path)
                prior = report["entries"] + list(journal.records.values())
                status_required = bool(report["errors"] or journal.poisoned or any(
                    r["request_id"] == request_id and r["endpoint_sha256"] == journal.endpoint for r in prior))
                details = {"ok": False, "request_id": request_id, "code": captured["code"],
                           "attempt_outcome": "not_sent", "mutation_sent": False,
                           "outcome": "not_started",
                           "retryable": captured["code"] in {"preflight_unavailable", "upstream_not_ready", "maintenance_window"} and not status_required,
                           "automatic_retry": False, "logical_status_required": status_required,
                           "message": "No mutation sent on this attempt. Retry manually only after readiness and any prior logical status are resolved."}
                self.preflight_failure = {**details, "observed_at": time.time()}
                raise PreflightRefusal(details) from exc
            raise RuntimeError(f"memory-os: request_id={request_id}; code={captured['code']}; outcome uncertain or refused. "
                               "Use memory_request_status; do not repeat the mutation.") from exc
