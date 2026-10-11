"""L13a: opt-in, bounded process-local qualification aggregates, no payload log."""
from bisect import bisect_left
from copy import deepcopy
import json
import os
from threading import Lock


LATENCY_UPPER_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 30000)


def enabled_from_env() -> bool:
    value = os.environ.get("MEMORY_V4_QUALIFICATION_METRICS", "0")
    if value not in {"0", "1"}:
        raise ValueError("MEMORY_V4_QUALIFICATION_METRICS must be 0 or 1")
    return value == "1"


def result_text(result: dict) -> str:
    """Exactly the MCP TextContent.text serialization (not the full wire envelope)."""
    return json.dumps(result, ensure_ascii=False)


class QualificationMetrics:
    def __init__(self, tool_names, *, enabled=False):
        self.enabled = enabled
        self._names = frozenset(tool_names)
        self._lock = Lock()
        self._rows = {}
        self._failures = 0

    def record(self, name, arguments, result, elapsed_ms):
        if not self.enabled:
            return
        name = name if name in self._names else "_unknown"
        text = result_text(result)
        size = len(text.encode("utf-8"))
        elapsed_ms = max(0.0, elapsed_ms)
        success = result.get("ok") is not False
        denied = result.get("code") == "maintenance_denied"
        # Never retain queries, projects, request IDs, paths, errors or response text.
        with self._lock:
            row = self._rows.setdefault(name, {
                "calls": 0, "successes": 0, "errors": 0, "maintenance_denied": 0,
                "elapsed_ms_sum": 0.0, "elapsed_ms_max": 0.0,
                "latency_bins": [0] * (len(LATENCY_UPPER_MS) + 1),
                "content_text_utf8_bytes_sum": 0, "content_text_utf8_bytes_max": 0,
                "brief_requested_tokens_sum": 0, "brief_budget_requests": 0,
                "brief_rendered_utf8_bytes_sum": 0,
            })
            row["calls"] += 1
            row["successes" if success else "errors"] += 1
            row["maintenance_denied"] += int(denied)
            row["elapsed_ms_sum"] += elapsed_ms
            row["elapsed_ms_max"] = max(row["elapsed_ms_max"], elapsed_ms)
            row["latency_bins"][bisect_left(LATENCY_UPPER_MS, elapsed_ms)] += 1
            row["content_text_utf8_bytes_sum"] += size
            row["content_text_utf8_bytes_max"] = max(row["content_text_utf8_bytes_max"], size)
            if name == "memory_session_brief" and success:
                budget = (arguments or {}).get("max_tokens", 3000)
                if type(budget) is int and 200 <= budget <= 20000:
                    row["brief_requested_tokens_sum"] += budget
                    row["brief_budget_requests"] += 1
                rendered = result.get("text")
                if isinstance(rendered, str):
                    row["brief_rendered_utf8_bytes_sum"] += len(rendered.encode("utf-8"))

    def failed_observation(self):
        with self._lock:
            self._failures += 1

    def snapshot(self):
        with self._lock:
            return {
                "schema_version": 1, "enabled": self.enabled,
                "scope": "current_server_process", "retention": "until_process_exit",
                "latency_scope": "synchronous_dispatch_including_engine_wait_excluding_transport",
                "latency_bin_upper_ms": [*LATENCY_UPPER_MS, None],
                "byte_scope": "mcp_text_content_text_only_not_full_wire",
                "measured_tokens": None, "token_budget_verdict": "not_measured",
                "snapshot_excludes_current_status_call": True,
                "observation_failures": self._failures, "tools": deepcopy(self._rows),
            }
