"""Measurement attestation/gates using only synthetic arrays and hash stores."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from bench.e2e import indexing_campaign as campaign
from memory_os_v4 import Config, MemoryV4


def test_selection_is_order_independent_and_not_outcome_based():
    items = [SimpleNamespace(source_id=str(i), id=str(i)) for i in range(20)]
    a = [c.id for c in campaign.select(items)]
    assert a == [c.id for c in campaign.select(items[::-1])] and len(a) == 3


def test_snapshot_canonicalizes_only_episode_ids_and_runtime_fields(tmp_path):
    with MemoryV4(config=Config(data_dir=str(tmp_path / "store"), embed_model="hash")) as engine:
        episode = engine.add_episode("Recorded message", project="p", event_time=100)
        engine.add_facts([{"text": "Recorded fact", "item_id": "fact"}],
                         project="p", parent_ids=[episode.id], event_time=100)
        engine.flush()
        (tmp_path / "store/requests.jsonl").write_text("")
        campaign.snapshot(tmp_path / "store", tmp_path / "snapshot")
        result = json.loads((tmp_path / "snapshot.json").read_text())
        assert result["ids"] == ["episode:0", "fact"]
        assert result["items"][1]["parent_ids"] == ["episode:0"]
        assert "created_at" not in result["items"][0]
        assert result["items"][0]["event_time"] == 100


def artifacts(root, times=(200, 250, 300)):
    plan = {"order": ["before", "after"], "selected": [{"id": str(i)} for i in range(3)],
            "embedding_tolerance": {"atol": 2e-6, "rtol": 1e-5}, "claim": "feasibility"}
    for arm in plan["order"]:
        output = root / arm / ".local-qualification/measurement"
        output.mkdir(parents=True)
        rows = []
        for i in range(3):
            seconds = 700 if arm == "before" else times[i]
            rows.append({"id": str(i), "ingest_seconds": seconds, "capture": {"added": 2},
                         "diagnostic_ranks": [["a", "b"]]})
            campaign.write(output / f"{i}.json", {"ids": ["a", "b"], "items": [], "receipts": []})
            np.save(output / f"{i}.npy", np.eye(2, dtype=np.float32))
        campaign.write(output / "complete.json", {"rows": rows})
    return plan


def test_three_pair_gate_reports_sample_p95_not_population_ci(tmp_path):
    report = campaign.compare(tmp_path, artifacts(tmp_path))
    assert report["criterion_met"] and report["p50"] == 250
    assert report["p95_nearest_rank"] == report["max"] == 300


def test_performance_failure_is_not_hidden_by_correct_embeddings(tmp_path):
    report = campaign.compare(tmp_path, artifacts(tmp_path, (200, 250, 500)))
    assert report["complete"] and report["criterion_met"] is False


@pytest.mark.parametrize("field", ["embeddings", "receipt"])
def test_correctness_difference_fails_closed(tmp_path, field):
    plan = artifacts(tmp_path)
    output = tmp_path / "after/.local-qualification/measurement"
    if field == "embeddings":
        np.save(output / "0.npy", np.zeros((2, 2), dtype=np.float32))
    else:
        row = json.loads((output / "0.json").read_text())
        row["receipts"] = [{"state": "pending"}]
        campaign.write(output / "0.json", row)
    with pytest.raises(ValueError):
        campaign.compare(tmp_path, plan)


def test_source_archive_rejects_worktree_assumption(tmp_path):
    import io
    import tarfile
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        member = tarfile.TarInfo("v4/src/file.py")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"pass"))
    assert set(campaign.archive_records(payload.getvalue())) == {"src/file.py"}


def test_source_attestation_skips_stale_metadata_and_sets_clone_cwd(tmp_path, monkeypatch):
    for name in ("git-state", "lexical-dev-git"):
        (tmp_path / ".local-qualification" / name).mkdir(parents=True)
    calls = []

    def run(command, **kw):
        calls.append(command)
        assert kw["cwd"] == tmp_path and kw["check"]
        if "git-state" in command[2]:
            raise subprocess.CalledProcessError(128, command, stderr=b"Unknown revision")
        return SimpleNamespace(stdout=b"archive")

    monkeypatch.setattr(campaign.subprocess, "run", run)
    assert campaign.git(tmp_path, "archive", "a" * 40) == b"archive"
    assert len(calls) == 2 and "lexical-dev-git" in calls[1][2]


def test_guard_refuses_measurements_before_any_worker(tmp_path, monkeypatch):
    clone = Path(campaign.__file__).resolve().parents[3]
    directory = clone / ".local-qualification/test-uncreated-indexing-guard"
    monkeypatch.setattr(campaign, "frozen", lambda _: {})
    monkeypatch.setattr(campaign, "ensure_priority", lambda _: None)
    monkeypatch.setattr(campaign, "sample", lambda _: (_ for _ in ()).throw(campaign.ResourceStop("swap")))
    monkeypatch.setattr(campaign, "run_job", lambda *_: pytest.fail("Guard must precede worker"))
    with pytest.raises(campaign.ResourceStop, match="swap"):
        campaign.main(["run", "--plan-dir", str(directory)])
    assert not directory.exists()


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, ""), (0, "unknown"), (0, "-1")])
def test_unknown_rss_fails_closed_for_live_worker(tmp_path, monkeypatch, returncode, stdout):
    child = SimpleNamespace(pid=123, poll=lambda: None)
    monkeypatch.setattr(campaign.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=returncode, stdout=stdout))
    with pytest.raises(campaign.ResourceStop, match="RSS"):
        campaign.rss_bytes(child, tmp_path)


@pytest.mark.asyncio
async def test_real_private_stdio_measurement_smoke_uses_hash_only(monkeypatch):
    # Private paths satisfy the same server protections as the heavy script.
    from bench.e2e.beam import CATEGORIES, conversations
    clone = Path(campaign.__file__).resolve().parents[3]
    private = clone / ".local-qualification"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="indexing-hash-test-", dir=private) as name:
        root = Path(name)
        output = root / "measurement"
        output.mkdir()
        probes = {}
        for cat in CATEGORIES:
            key = {"abstention": "ideal_response", "contradiction_resolution": "ideal_answer",
                   "instruction_following": "expected_compliance",
                   "preference_following": "expected_compliance",
                   "summarization": "ideal_summary"}.get(cat, "answer")
            probes[cat] = [{"question": f"Never sent to search {cat}", key: "SCORER_ONLY",
                            "rubric": ["SCORER_ONLY"],
                            **({} if cat == "abstention" else {"source_chat_ids": [0]})}]
        row = {"conversation_id": "synthetic", "conversation_seed": "SECRET", "narratives": "SECRET",
               "user_profile": "SECRET", "conversation_plan": "SECRET", "user_questions": "SECRET",
               "chat": [[{"content": "A recorded preference.", "role": "user", "id": 0,
                          "index": "SECRET", "question_type": "SECRET", "time_anchor": None}]],
               "probing_questions": repr(probes)}
        data = root / "data.jsonl"
        data.write_text(json.dumps(row) + "\n")
        c = next(conversations(data))
        plan = {"model": {"id": "hash"}, "selected": [{"id": c.id}], "data": str(data),
                "diagnostic_probes": ["recorded preference"]}
        monkeypatch.setenv("EVAL_PROCESS_OWNER", uuid.uuid4().hex)
        registry = output / "server-registry"
        monkeypatch.setenv("EVAL_SERVER_REGISTRY", str(registry))
        # worker() runs in its own process in production. Restore the tripwire
        # here so later tests, including TCP outside the sandbox, stay isolated.
        with monkeypatch.context() as undo:
            undo.setattr(socket, "socket", socket.socket)
            undo.setattr(socket, "getaddrinfo", socket.getaddrinfo)
            await campaign.worker(plan, "after", output)
        result = json.loads((output / "complete.json").read_text())
        assert result["rows"][0]["capture"]["added"] == 1
        assert result["rows"][0]["usage"]["llm_calls"] == 0
        assert len(result["rows"][0]["diagnostic_ranks"][0]) == 1
        assert "SCORER_ONLY" not in (output / f"{c.id}.json").read_text()
        # Context exit has terminated the private SDK-owned server.
        from bench.e2e.processes import cleanup
        cleanup(registry, os.environ["EVAL_PROCESS_OWNER"])
