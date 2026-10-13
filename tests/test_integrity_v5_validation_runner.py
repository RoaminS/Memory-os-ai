from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_integrity_v5_validation as runner


def case():
    return {
        "id": "PB-001",
        "language": "fr",
        "family": "correction",
        "now": "2026-10-13T10:00:00Z",
        "backend": "sqlite_s1",
        "seed": {"items": [], "blocks": [], "tasks": []},
        "client": {"name": "synthetic-codex", "version": "1"},
        "paths": ["server", "relay"],
        "steps": [
            {
                "id": "seed",
                "tool": "memory_add_facts",
                "arguments": {
                    "project": "P",
                    "request_id": "seed",
                    "facts": [
                        {
                            "item_id": "old",
                            "text": "Synthetic original assertion.",
                            "event_time": "2026-10-12T10:00:00Z",
                        }
                    ],
                },
            },
            {
                "id": "preview",
                "tool": "memory_add_facts",
                "arguments": {
                    "project": "P",
                    "mode": "preview",
                    "reason": "Synthetic correction.",
                    "facts": [
                        {
                            "item_id": "new",
                            "text": "Synthetic new assertion.",
                            "replaces": ["old"],
                        }
                    ],
                },
            },
            {
                "id": "bound",
                "bind_preview": {
                    "step": "preview",
                    "confirmed_impacts": ["old"],
                    "request_id": "apply",
                },
            },
            {
                "id": "apply",
                "tool": "memory_add_facts",
                "arguments": {"$ref": "bound.arguments"},
            },
            {
                "id": "changes",
                "tool": "memory_timeline",
                "arguments": {"view": "changes", "project": "P"},
            },
        ],
    }


def test_binding_does_not_implicitly_confirm_or_evaluate():
    p = {
        "ok": True,
        "prepared_facts": [],
        "impacts": [{"target_id": "old"}],
        "expected_revisions": {"old": "a" * 64},
        "preview_digest": "b" * 64,
    }
    b = {"step": "preview", "confirmed_impacts": ["old"], "request_id": "apply"}
    r = runner.bind_preview(
        b,
        {"preview": p},
        {
            "preview": {
                "mode": "preview",
                "project": "P",
                "facts": [],
                "reason": "Change.",
            }
        },
    )
    assert (
        r["arguments"]["confirmed_impacts"] == ["old"]
        and r["arguments"]["mode"] == "apply"
    )
    with pytest.raises(ValueError):
        runner.bind_preview(
            {**b, "confirmed_impacts": []},
            {"preview": p},
            {"preview": {"mode": "preview"}},
        )
    with pytest.raises(ValueError):
        runner.refs({"$ref": "__import__(x)"}, {})
    assert (
        runner.refs(
            {"$ref": "s.hits.0.revision"}, {"s": {"hits": [{"revision": "a" * 64}]}}
        )
        == "a" * 64
    )


@pytest.mark.parametrize("path", ["server", "relay"])
def test_independent_development_case_determinism(tmp_path, path):
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem

    c = case()
    runner.validate(c, models, MemoryItem)
    results = []
    for repeat in (1, 2):
        directory = tmp_path / str(repeat)
        directory.mkdir()
        results.append(runner.collect_case(c, directory, path))
    assert results[0]["deterministic_sha256"] == results[1]["deterministic_sha256"]
    assert len(results[0]["records"]) == 5
    assert results[0]["records"][-1]["result"]["events"]
    if path == "relay":
        assert (
            results[0]["records"][3]["result"]["changed_by"]["client_id"]
            == "synthetic-codex"
        )


@pytest.mark.parametrize("mutate", ["gold", "unknown", "forward", "unsupported_fault"])
def test_compatibility_refuses_without_candidate_execution(mutate):
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem

    c = case()
    if mutate == "gold":
        c["expect"] = {}
        with pytest.raises(ValueError):
            runner.no_gold(c)
    elif mutate == "unknown":
        c["secret_file"] = "do-not-read"
        with pytest.raises(ValueError):
            runner.validate(c, models, MemoryItem)
    elif mutate == "forward":
        c["steps"][0]["arguments"] = {"$ref": "changes.events"}
        with pytest.raises(ValueError):
            runner.validate(c, models, MemoryItem)
    else:
        c["fault"] = {
            "step": "apply",
            "point": "after_commit",
            "occurrence": 1,
            "kind": "process_exit",
        }
        with pytest.raises(ValueError):
            runner.validate(c, models, MemoryItem)


def test_exception_after_commit_is_reopened_without_replay(tmp_path):
    c = case()
    c["steps"] = c["steps"][:4]
    c["fault"] = {
        "step": "apply",
        "point": "after_commit",
        "occurrence": 1,
        "kind": "exception",
    }
    r = runner.collect_case(c, tmp_path, "server")
    last = r["records"][-1]
    assert last["private_reopen_after_uncertain"] and last["result"]["ok"] is False
    assert len([h for h in last["history"].get("new", [])]) >= 1
    assert (
        sum(1 for receipt in last["receipts"] if receipt["request_id"] == "apply") == 1
    )


def test_journal_measurement_harness_on_synthetic_store(tmp_path, monkeypatch):
    import qualify_integrity_v5_journal as campaign

    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    monkeypatch.setattr(
        campaign,
        "resource_sample",
        lambda require_swap: {"synthetic_unit_resources": True},
    )
    out = tmp_path / ".local-qualification" / "measurement"
    monkeypatch.setattr(sys, "argv", ["qualify", "--output", str(out)])
    rc = campaign.main()
    import json

    report = json.loads((out / "report.json").read_text())
    assert report["events"] == 10000 and report["neural_calls"] == 0
    assert rc == (0 if report["ok"] else 1)


def test_raw_seed_and_cursor_pagination_have_deterministic_projection(tmp_path):
    c = case()
    c["seed"]["items"] = [
        {"id": "raw", "kind": "fact", "text": "Independent raw seed.", "project": "P"}
    ]
    c["steps"].append(
        {
            "id": "later",
            "tool": "memory_timeline",
            "arguments": {
                "view": "changes",
                "project": "P",
                "since_cursor": {"$ref": "changes.next_cursor"},
            },
        }
    )
    results = []
    for i in (1, 2):
        directory = tmp_path / str(i)
        directory.mkdir()
        results.append(runner.collect_case(c, directory, "server"))
    assert results[0]["deterministic_sha256"] == results[1]["deterministic_sha256"]
    assert results[0]["records"][-1]["result"]["events"] == []
