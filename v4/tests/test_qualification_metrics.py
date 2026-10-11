import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from memory_os_v4 import Config, MemoryV4, server
from memory_os_v4.qualification import QualificationMetrics, enabled_from_env, result_text


def test_default_off_and_strict_opt_in(monkeypatch):
    monkeypatch.delenv("MEMORY_V4_QUALIFICATION_METRICS", raising=False)
    assert not enabled_from_env()
    monkeypatch.setenv("MEMORY_V4_QUALIFICATION_METRICS", "1")
    assert enabled_from_env()
    monkeypatch.setenv("MEMORY_V4_QUALIFICATION_METRICS", "true")
    with pytest.raises(ValueError):
        enabled_from_env()


def test_bounded_private_aggregates_and_real_utf8_size():
    m = QualificationMetrics(["memory_session_brief"], enabled=True)
    payload = {"text": "é SECRET", "facts": ["SECRET" * 500]}
    m.record("memory_session_brief", {"max_tokens": 1000, "project": "PRIVATE"}, payload, 5)
    for i in range(100):
        m.record(f"untrusted-{i}", {"token": "SECRET"}, {"ok": False, "error": "SECRET"}, 10)
    snap = m.snapshot()
    row = snap["tools"]["memory_session_brief"]
    assert row["content_text_utf8_bytes_sum"] == len(result_text(payload).encode("utf-8"))
    assert row["brief_rendered_utf8_bytes_sum"] == len(payload["text"].encode("utf-8"))
    assert row["brief_requested_tokens_sum"] == 1000 and row["latency_bins"][1] == 1
    assert set(snap["tools"]) == {"memory_session_brief", "_unknown"}
    assert snap["tools"]["_unknown"]["errors"] == 100
    assert "SECRET" not in json.dumps(snap) and "PRIVATE" not in json.dumps(snap)
    assert snap["measured_tokens"] is None and snap["token_budget_verdict"] == "not_measured"
    row["calls"] = -1
    assert m.snapshot()["tools"]["memory_session_brief"]["calls"] == 1


def test_thread_safe_and_disabled_no_serialization():
    off = QualificationMetrics([], enabled=False)
    off.record("x", {}, {"not_json": object()}, 1)
    assert off.snapshot()["tools"] == {}
    m = QualificationMetrics(["x"], enabled=True)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: m.record("x", {}, {}, 0.5), range(400)))
    assert m.snapshot()["tools"]["x"]["calls"] == 400


def test_dispatch_outcomes_metrics_do_not_write_store_or_change_ack(tmp_path, monkeypatch):
    m = QualificationMetrics(server.TOOL_HANDLERS, enabled=True)
    monkeypatch.setattr(server, "_qualification", m)
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model="hash")) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        server.dispatch("memory_search", {"query": ""})
        server.dispatch("memory_compact", {})
        server.dispatch("memory_session_brief", {"mode": "detailed", "max_tokens": 1000})
        snap = server.dispatch("memory_status", {})["qualification"]
        assert snap["tools"]["memory_search"]["errors"] == 1
        assert snap["tools"]["memory_compact"]["maintenance_denied"] == 1
        assert "memory_status" not in snap["tools"]
        assert not list(tmp_path.glob("*qualification*"))
        monkeypatch.setattr(m, "record", lambda *a: (_ for _ in ()).throw(ValueError("metrics failed")))
        result = server.dispatch("memory_add_facts", {
            "facts": [{"text": "Synthetic acknowledged fact"}], "request_id": "ack"})
        assert len(result["added"]) == 1 and "error" not in result
        assert m.snapshot()["observation_failures"] == 1


@pytest.mark.asyncio
async def test_transport_text_matches_byte_counter(tmp_path, monkeypatch):
    m = QualificationMetrics(server.TOOL_HANDLERS, enabled=True)
    monkeypatch.setattr(server, "_qualification", m)
    with MemoryV4(config=Config(data_dir=str(tmp_path), embed_model="hash")) as engine:
        monkeypatch.setattr(server, "_engine", engine)
        content = await server.call_tool("memory_session_brief", {"mode": "detailed", "max_tokens": 1000})
        row = m.snapshot()["tools"]["memory_session_brief"]
        assert row["content_text_utf8_bytes_sum"] == len(content[0].text.encode("utf-8"))
