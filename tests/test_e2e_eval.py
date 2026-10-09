"""Lightweight protocol regressions; no model, server, network or dataset loaded."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bench.e2e import baselines, data, guard, pipeline, run, stats, worker
from bench.e2e.configuration import resolved_config
from bench.e2e import processes
from bench.e2e.client import project_for
from bench.e2e.leaderboard import AddSearchAdapter


def entry(**overrides):
    out = {"question_id": "opaque-q", "question_type": "knowledge-update", "question": "What city?",
           "answer": "FUTURE-GOLD-SECRET", "question_date": "2023/04/10 (Mon) 23:07",
           "haystack_sessions": [[{"role": "user", "content": "I visited Paris.", "has_answer": True}]],
           "haystack_session_ids": ["s1"], "haystack_dates": ["2023/04/09 (Sun) 23:07"],
           "answer_session_ids": ["s1"]}
    out.update(overrides)
    return out


def source(tmp_path, entries):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(entries))
    return path


def test_annotations_cannot_reach_capture_or_response(tmp_path):
    path = source(tmp_path, [entry()])
    inputs, gold = next(data.cases(path))
    assert "answer" not in asdict(inputs)
    assert "category" not in asdict(inputs)
    assert "meta" not in asdict(inputs.turns[0])
    quotes = pipeline.extract(inputs.id, inputs.turns)
    assert "FUTURE-GOLD-SECRET" not in json.dumps([asdict(q) for q in quotes])
    assert gold.answers == ("FUTURE-GOLD-SECRET",)
    assert "FUTURE-GOLD-SECRET" not in pipeline.respond(inputs.question, quotes)
    changed = source(tmp_path, [entry(answer="DIFFERENT", question_type="different",
                                    haystack_sessions=[[{"role": "user", "content": "I visited Paris.",
                                                        "has_answer": False}]])])
    assert next(data.cases(changed))[0] == inputs


def test_future_observations_excluded_but_gold_not_rewritten(tmp_path):
    p = source(tmp_path, [entry(haystack_dates=["2024/04/09 (Tue) 23:07"])])
    inputs, gold = next(data.cases(p))
    assert inputs.turns == ()
    assert gold.evidence == ("s1:0",)
    assert gold.excluded_future_turns == 1


@pytest.mark.parametrize("raw", ['[{},]', '[{} {}]', '[{}', '[] trailing', '{}'])
def test_streaming_json_rejects_malformed(tmp_path, raw):
    p = tmp_path / "bad.json"
    p.write_text(raw)
    with pytest.raises(ValueError):
        list(data.array_entries(p))


def test_streaming_unicode_and_size_limit(tmp_path):
    p = source(tmp_path, [{"text": "é" * 40000}, {"text": "done"}])
    assert len(list(data.array_entries(p))) == 2
    with pytest.raises(ValueError, match="memory budget"):
        list(data.array_entries(p, max_entry_bytes=200))


def test_alignment_and_duplicate_questions_rejected(tmp_path):
    with pytest.raises(ValueError, match="alignment"):
        list(data.cases(source(tmp_path, [entry(haystack_dates=[])])))
    with pytest.raises(ValueError, match="Duplicate"):
        list(data.cases(source(tmp_path, [entry(), entry()])))


def test_oracle_is_never_s_and_empty_gold_kept(tmp_path):
    p = source(tmp_path, [entry(haystack_sessions=[[{"role": "user", "content": "Paris",
                                                    "has_answer": False}]])])
    assert len(list(data.cases(p))) == 1
    assert data.inventory(p, "oracle")["empty_evidence_questions"] == 1
    with pytest.raises(ValueError, match="Oracle-only"):
        data.inventory(p, "s")


def test_quote_losslessness_and_source_identity():
    t = data.Turn("s1:0", "s1", "assistant", "é" * 13, "2023-01-01T00:00:00+00:00")
    qs = pipeline.extract("c", (t,), 4)
    assert len(qs) == 4
    assert all(q.turn_id == t.id for q in qs)
    assert len({q.id for q in qs}) == 4
    assert all("assistant said:" in q.text for q in qs)
    assert pipeline.extract("another", (t,), 4)[0].id != qs[0].id
    changed = data.Turn(t.id, t.session, t.role, "changed", t.date)
    assert pipeline.extract("c", (changed,), 4)[0].id != qs[0].id


def test_shared_utf8_cap_and_chunk_limit():
    qs = [pipeline.Quote(str(i), str(i), "éé", "date") for i in range(3)]
    assert len(pipeline.pack(qs, 2, 5)) == 1
    assert len(pipeline.pack(qs, 2, 20)) == 2
    assert len(pipeline.pack(qs, 2, 20, full=True)) == 3


def test_baselines_identical_quotes_and_persisted_files(tmp_path):
    qs = [pipeline.Quote("b", "t1", "Paris cat", "d"), pipeline.Quote("a", "t2", "Rome dog", "d")]
    for cls in (baselines.BM25, baselines.Files):
        b = cls(qs, tmp_path, "hash")
        assert b.search("cat", 10) == [qs[0]]
        assert b.search("absent", 10) == []
    assert baselines.FullContext(qs, tmp_path, "hash").search("cat", 1) == qs
    with pytest.raises(ValueError, match="not a dense"):
        baselines.Dense(qs, tmp_path, "hash")


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call(self, name, payload):
        self.calls.append((name, payload))
        if name == "memory_chat_save":
            return {"facts": {"added": [q["item_id"] for q in payload["facts"]], "deduped": [], "superseded": []}}
        return {"hits": []}


def test_adapter_raw_add_search_scope_requestid_and_no_annotation():
    async def check():
        fake = FakeClient()
        a = AddSearchAdapter(fake, "run")
        message = {"id": "t0", "session": "s1", "role": "user", "content": "Paris",
                   "date": "2023-01-01T00:00:00+00:00"}
        raw = {"op": "Add", "user_id": "*", "messages": [message]}
        await a.request(raw)
        await a.request(raw)
        assert fake.calls[0][1] == fake.calls[1][1]
        assert fake.calls[0][1]["project_label"] != "*"
        await a.request({"op": "Search", "user_id": "*", "query": "Paris"})
        assert fake.calls[-1][1]["project"] == fake.calls[0][1]["project_label"]
        assert project_for("run", "different") != project_for("run", "*")
        assert project_for("another-run", "*") != project_for("run", "*")
        with pytest.raises(ValueError, match="annotations"):
            await a.request({**raw, "answer": "cheat"})
        with pytest.raises(ValueError, match="schema"):
            await a.request({**raw, "messages": [{**message, "has_answer": True}]})
        with pytest.raises(ValueError, match="timezone"):
            await a.request({**raw, "messages": [{**message, "date": "2023-01-01"}]})
        with pytest.raises(ValueError, match="integer"):
            await a.request({"op": "Search", "user_id": "*", "query": "Paris", "limit": True})
    asyncio.run(check())


def test_adapter_uncertain_add_never_retried():
    fake = FakeClient()
    fake.call = Mock(side_effect=RuntimeError("uncertain"))
    turns = (data.Turn("t", "s", "user", "Paris", "2023-01-01T00:00:00+00:00"),)
    with pytest.raises(RuntimeError, match="uncertain"):
        asyncio.run(AddSearchAdapter(fake, "run").add("u", turns))
    assert fake.call.call_count == 1


def gold(qid, sessions):
    return data.Gold(qid, ("t1",), ("Paris",), "cat", tuple(sessions), 0)


def test_metrics_dedup_and_empty_evidence():
    assert stats.score(["t1", "t1"], "PARIS!", gold("q", ["s"]), 10)["recall"] == 1
    empty = data.Gold("q", (), ("Paris",), "cat", (), 0)
    out = stats.score([], None, empty, 10)
    assert out["recall"] is None
    assert out["answer_em"] == 0


def test_cluster_transitivity_and_insufficient_independence():
    gs = [gold("q1", ["a"]), gold("q2", ["a", "b"]), gold("q3", ["b"])]
    mapping = stats.cluster_ids(gs)
    assert len(set(mapping.values())) == 1
    out = stats.estimate([(mapping[g.id], 1) for g in gs], 17)
    assert out["n"] == 3 and out["clusters"] == 1 and out["ci95"] is None


def test_paired_bootstrap_reproducible_and_failure_in_denominator():
    gs = [gold("q1", ["a"]), gold("q2", ["b"])]
    rows = [{"qid": g.id, "system": s, "status": "failed" if s == "bm25" else "ok",
             "recall": 0.0 if s == "bm25" else 1.0, "mrr": 0.0, "ndcg": 0.0, "answer_em": 0.0}
            for g in gs for s in ("memory", "bm25")]
    out = stats.summarize(rows, gs, ["memory", "bm25"], 17)
    assert out == stats.summarize(rows, gs, ["memory", "bm25"], 17)
    assert out["systems"]["bm25"]["failures"] == 2
    assert out["paired_delta_memory"]["bm25"]["ci95"] == [1.0, 1.0]


def test_guard_thresholds_decimal_go_and_unknown_swap(monkeypatch, tmp_path):
    reader = guard.swap_used
    monkeypatch.setattr(guard, "swap_used", lambda: 8_500_000_000)
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda p: SimpleNamespace(free=5_000_000_000))
    assert guard.sample(tmp_path)["swap_bytes"] == 8_500_000_000
    monkeypatch.setattr(guard, "swap_used", lambda: 8_500_000_001)
    with pytest.raises(guard.ResourceStop, match="swap"):
        guard.sample(tmp_path)
    monkeypatch.setattr(guard, "swap_used", lambda: 0)
    monkeypatch.setattr(guard.shutil, "disk_usage", lambda p: SimpleNamespace(free=4_999_999_999))
    with pytest.raises(guard.ResourceStop, match="disk"):
        guard.sample(tmp_path)
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    with pytest.raises(guard.ResourceStop, match="denied"):
        reader()


def test_priority_permission_denied_fails_closed(monkeypatch):
    monkeypatch.setattr(guard.os, "getpriority", lambda *a: 0)
    monkeypatch.setattr(guard.os, "setpriority", Mock(side_effect=PermissionError()))
    with pytest.raises(guard.ResourceStop, match="nice"):
        guard.ensure_priority(guard.Limits())


def test_model_name_cannot_trigger_download(tmp_path):
    with pytest.raises(ValueError, match="EXISTING"):
        run.model_record("sentence-transformers/not-downloaded")
    assert run.model_record("hash")["downloads"] == 0


def test_full_context_over_budget_is_not_scored(tmp_path):
    inputs, g = next(data.cases(source(tmp_path, [entry()])))
    cfg = {"run_id": "run", "ingest_bytes": 10000, "model": "hash", "transport": "dispatch",
           "top_k": 10, "context_bytes": 1}
    out = asyncio.run(worker.evaluate(inputs, "full-context", cfg, tmp_path))
    assert out["status"] == "not_applicable"


def test_observation_namespace_and_k_matched_in_worker(tmp_path):
    inputs, g = next(data.cases(source(tmp_path, [entry(question="What did I visit?")])))
    cfg = {"run_id": "run", "ingest_bytes": 10000, "model": "hash", "transport": "dispatch",
           "top_k": 10, "context_bytes": 10000}
    out = asyncio.run(worker.evaluate(inputs, "bm25", cfg, tmp_path))
    assert out["status"] == "ok"
    assert out["retrieved_turn_ids"] == ["s1:0"]
    assert out["llm_calls"] == 0 and out["api_usd"] == 0
    assert out["tokens"] is None


def test_explicit_config_independent_of_live_environment(monkeypatch):
    monkeypatch.setenv("MEMORY_V4_DIR", "/forbidden/live")
    monkeypatch.setenv("MEMORY_V4_FUSION", "poison")
    cfg = resolved_config("hash", "/private/eval")
    assert cfg["data_dir"] == "/private/eval"
    assert cfg["fusion"] == "cc"
    assert cfg["embed_max_seq_length"] == 256
    assert cfg["store_backend"] == "jsonl" and cfg["use_recency"] is False


def test_supervisor_resource_stop_kills_only_its_child(monkeypatch, tmp_path):
    child = SimpleNamespace(pid=333333, poll=lambda: None)
    killed = []
    monkeypatch.setattr(run.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(run, "kill_group", lambda c: killed.append(c.pid))
    monkeypatch.setattr(run, "sample", Mock(side_effect=guard.ResourceStop("swap")))
    with pytest.raises(guard.ResourceStop, match="swap"):
        run.supervise(["fake"], tmp_path / "log", tmp_path, {}, float("inf"),
                      guard.Limits(), [], tmp_path / "out", 120)
    assert killed == [333333]


def test_parent_catches_blocking_case_timeout(monkeypatch, tmp_path):
    child = SimpleNamespace(pid=333333, poll=lambda: None)
    killed = []
    monkeypatch.setattr(run.subprocess, "Popen", lambda *a, **k: child)
    monkeypatch.setattr(run, "kill_group", lambda c: killed.append(c.pid))
    times = iter([0, 0, 20, 20, 40, 40])
    monkeypatch.setattr(run.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    monkeypatch.setattr(run, "sample", lambda *a: {})
    with pytest.raises(guard.ResourceStop, match="Case exceeded"):
        run.supervise(["fake"], tmp_path / "log", tmp_path, {}, 1000,
                      guard.Limits(), [], tmp_path / "out", 10)
    assert killed == [333333]


def test_missing_prediction_stays_in_frozen_denominator(monkeypatch, tmp_path):
    from pathlib import Path
    clone = tmp_path / "clone"
    fake_file = clone / "v4/bench/e2e/run.py"
    fake_file.parent.mkdir(parents=True)
    monkeypatch.setattr(run, "__file__", str(fake_file))
    monkeypatch.setattr(run, "ensure_priority", lambda *a: None)
    monkeypatch.setattr(run, "sample", lambda *a: {"swap_bytes": 0, "free_bytes": 10_000_000_000})
    monkeypatch.setattr(run, "verify_source", lambda *a: None)
    monkeypatch.setattr(run, "code_record", lambda: {"fake.py": "a" * 64})
    monkeypatch.setattr(run, "resolved_config", lambda *a: {"embed_model": "hash"})
    dataset = source(tmp_path, [entry(), entry(question_id="q2", haystack_session_ids=["s2"],
                                             answer_session_ids=["s2"])])

    def supervise(*args):
        prediction_path = args[7]
        row = {"qid": "opaque-q", "system": "bm25", "status": "ok", "recall": 1.0,
               "mrr": 1.0, "ndcg": 1.0, "answer_em": 0.0, "end_to_end_ms": 1.0}
        prediction_path.write_text(json.dumps(row) + "\n")
        return 0
    monkeypatch.setattr(run, "supervise", supervise)
    output = clone / ".local-qualification/campaign"
    status = run.main(["--data", str(dataset), "--variant", "oracle", "--output", str(output),
                       "--systems", "bm25", "--model", "hash", "--source-commit", "a" * 40])
    assert status == 1
    report = json.loads((output / "report.json").read_text())
    assert report["selected_count"] == 2
    assert report["systems"]["bm25"]["n"] == 2
    assert report["systems"]["bm25"]["failures"] == 1
    assert report["systems"]["bm25"]["metrics"]["recall"]["mean"] == .5
    assert report["systems"]["bm25"]["metrics"]["recall"]["ci95"] == [0.0, 1.0]
    assert Path(output / "frozen.json").is_file()


def test_cleanup_does_not_signal_foreign_or_stale_pids(monkeypatch, tmp_path):
    (tmp_path / "foreign.json").write_text(json.dumps({"pid": 123, "owner": "foreign"}))
    (tmp_path / "stale.json").write_text(json.dumps({"pid": 124, "owner": "our-owner"}))
    signal = Mock()
    monkeypatch.setattr(processes, "verified_pid", lambda *a: False)
    monkeypatch.setattr(processes.os, "killpg", signal)
    processes.cleanup(tmp_path, "our-owner")
    signal.assert_not_called()


def test_cleanup_verifies_nonce_and_escalates_only_owned_server(monkeypatch, tmp_path):
    (tmp_path / "ours.json").write_text(json.dumps({"pid": 333333, "owner": "our-owner"}))
    checks = iter([True, False, False, False])
    monkeypatch.setattr(processes, "verified_pid", lambda *a: next(checks))
    signal = Mock()
    monkeypatch.setattr(processes.os, "killpg", signal)
    processes.cleanup(tmp_path, "our-owner")
    signal.assert_called_once_with(333333, processes.signal.SIGTERM)


def test_ip_tripwire_preserves_unix_socket_family(monkeypatch):
    import socket
    from bench.e2e.network import deny_ip_network
    original, dns = socket.socket, socket.getaddrinfo
    try:
        deny_ip_network()
        with pytest.raises(RuntimeError, match="IP sockets"):
            socket.socket(socket.AF_INET)
        with pytest.raises(RuntimeError, match="DNS"):
            socket.getaddrinfo("example.com", 80)
        # Type compatibility only, do not open any socket in this test.
        assert issubclass(socket.socket, original)
    finally:
        monkeypatch.setattr(socket, "socket", original)
        monkeypatch.setattr(socket, "getaddrinfo", dns)


def test_stdio_tool_contract_uses_current_sdk_api_without_starting_server(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    import mcp
    import mcp.client.stdio
    from bench.e2e.client import ToolClient
    captured = {}
    monkeypatch.setenv("EVAL_PROCESS_OWNER", "a" * 32)
    monkeypatch.setenv("EVAL_SERVER_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setenv("MEMORY_V4_DIR", "/forbidden/live")

    @asynccontextmanager
    async def stdio(params, errlog):
        captured["params"] = params
        yield None, None

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            captured["initialized"] = True

        async def call_tool(self, name, payload):
            captured["call"] = (name, payload)
            return SimpleNamespace(isError=False, structuredContent={"hits": []})

    monkeypatch.setattr(mcp, "ClientSession", Session)
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", stdio)

    async def check():
        async with ToolClient(tmp_path, "hash") as client:
            assert await client.call("memory_search", {"query": "Paris"}) == {"hits": []}
        params = captured["params"]
        assert params.args[:2] == ["-m", "bench.e2e.offline_server"]
        assert params.env["MEMORY_V4_DIR"] == str(tmp_path / "store")
        cfg = json.loads((tmp_path / "engine-config.json").read_text())
        assert cfg["embed_model"] == "hash" and cfg["data_dir"] == str(tmp_path / "store")
    asyncio.run(check())
