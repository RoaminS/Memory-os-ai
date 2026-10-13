#!/usr/bin/env python3
"""Gold-free P-B collector: sealed inputs, immutable source, no scoring/inference."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_brief_v5_validation import (  # noqa: E402
    timestamp,
    wall_clock,
    bound_engine,
    forbid_neural,
    unique_object,
    no_gold,
)
from qualify_brief_v5_performance import export_arm, guarded, run_guarded  # noqa: E402


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_inputs(args):
    if args.inputs.name.startswith("cases_") or any(
        p.startswith("sealed_v") for p in args.inputs.parts
    ):
        raise ValueError("Gold source path forbidden; use an inputs-only copy")
    raw = args.inputs.read_bytes()
    manifest = json.loads(args.manifest.read_text(), object_pairs_hook=unique_object)
    if (
        sha(raw) != args.inputs_sha256
        or manifest["inputs_sha256"] != args.inputs_sha256
    ):
        raise ValueError("P-B inputs seal mismatch")
    cases = [
        json.loads(line, object_pairs_hook=unique_object)
        for line in raw.splitlines()
        if line.strip()
    ]
    no_gold(cases)
    if (
        len(cases) != 40
        or manifest["cases"] != 40
        or [c["id"] for c in cases] != [f"PB-{i:03}" for i in range(1, 41)]
    ):
        raise ValueError("All 40 ordered independent inputs required")
    if any(sum(c["language"] == lang for c in cases) != 20 for lang in ("fr", "en")):
        raise ValueError("Expected 20 FR/20 EN")
    for family in ("identity", "correction", "compensation", "journal/crash"):
        if sum(c["family"] == family for c in cases) != 10:
            raise ValueError("Expected frozen 10/10/10/10 families")
    return cases, manifest


def refs(value, outputs):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("Invalid reference syntax")
            parts = value["$ref"].split(".")
            if (
                not parts
                or parts[0] not in outputs
                or any(
                    not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9-]*|[0-9]+", p)
                    for p in parts
                )
            ):
                raise ValueError("Reference must target an earlier step, data only")
            target = outputs[parts[0]]
            for key in parts[1:]:
                target = (
                    target[int(key)]
                    if isinstance(target, list) and key.isdecimal()
                    else target[key]
                )
            return deepcopy(target)
        return {k: refs(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [refs(v, outputs) for v in value]
    return value


def bind_preview(step, prior, original):
    if (
        set(step) != {"step", "confirmed_impacts", "request_id"}
        or step["step"] not in prior
    ):
        raise ValueError("Invalid explicit preview binding")
    p = prior[step["step"]]
    request = original[step["step"]]
    if p.get("ok") is not True or request.get("mode") != "preview":
        raise ValueError("Binding requires a successful explicit preview")
    confirmed = step["confirmed_impacts"]
    expected = sorted({i["target_id"] for i in p["impacts"]})
    if (
        not isinstance(confirmed, list)
        or not all(isinstance(i, str) for i in confirmed)
        or sorted(confirmed) != expected
    ):
        raise ValueError("Case must explicitly confirm exact impacts")
    if not isinstance(step["request_id"], str) or not step["request_id"]:
        raise ValueError("Binding requires an explicit request id")
    return {
        "arguments": {
            **deepcopy(request),
            "mode": "apply",
            "facts": p["prepared_facts"],
            "expected_revisions": p["expected_revisions"],
            "preview_digest": p["preview_digest"],
            "confirmed_impacts": confirmed,
            "request_id": step["request_id"],
        }
    }


def validate(case, models, MemoryItem):
    from memory_os_v4 import server

    required = {
        "id",
        "language",
        "family",
        "now",
        "backend",
        "seed",
        "steps",
        "client",
        "paths",
    }
    if set(case) - required - {"fault"} or required - set(case):
        raise ValueError("Unknown/missing P-B case fields")
    timestamp(case["now"])
    if (
        case["backend"] not in {"sqlite_s1", "jsonl"}
        or not case["paths"]
        or len(set(case["paths"])) != len(case["paths"])
        or set(case["paths"]) - {"server", "relay"}
    ):
        raise ValueError("Explicit backend/path required")
    if set(case["client"]) - {"name", "version"} or not all(
        isinstance(case["client"].get(k), str) and case["client"][k]
        for k in ("name", "version")
    ):
        raise ValueError("Synthetic clientInfo required")
    seed = case["seed"]
    if set(seed) != {"items", "blocks", "tasks"} or not all(
        isinstance(v, list) for v in seed.values()
    ):
        raise ValueError("Seed schema mismatch")
    if case["backend"] == "jsonl" and (seed["tasks"] or case.get("fault")):
        raise ValueError("JSONL cannot seed tasks or receive SQLite fault labels")
    ids = []
    for source in seed["items"]:
        if set(source) - {f.name for f in fields(MemoryItem)}:
            raise ValueError("Unknown MemoryItem field")
        value = dict(source)
        for key in (
            "event_time",
            "created_at",
            "valid_from",
            "valid_until",
            "last_accessed",
        ):
            if value.get(key) is not None:
                value[key] = timestamp(value[key])
        item = MemoryItem(**value)
        if (
            not isinstance(item.tags, list)
            or not all(isinstance(t, str) for t in item.tags)
            or not item.project
            or item.project == "*"
        ):
            raise ValueError("Invalid seed identity/tags")
        ids.append(item.id)
    if len(set(ids)) != len(ids):
        raise ValueError(
            "Duplicate seed ids; use declared steps for historical changes"
        )
    for block in seed["blocks"]:
        if set(block) - {"name", "text", "project", "scope"}:
            raise ValueError("Unknown seed block fields")
        models.BlockSetIn.model_validate(block)
    for task in seed["tasks"]:
        payload = {
            k: v for k, v in task.items() if k not in {"dated_fixture", "update"}
        }
        models.TaskCreateIn.model_validate(payload)
        timestamp(task.get("dated_fixture", case["now"]))
        if "update" in task:
            models.TaskUpdateIn.model_validate(task["update"])
    prior = {}
    for step in case["steps"]:
        if (
            not isinstance(step.get("id"), str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9-]*", step["id"])
            or step["id"] in prior
        ):
            raise ValueError("Unique safe step id required")
        if "bind_preview" in step:
            if (
                set(step) != {"id", "bind_preview"}
                or set(step["bind_preview"])
                != {"step", "confirmed_impacts", "request_id"}
                or step["bind_preview"]["step"] not in prior
            ):
                raise ValueError("Binding must target an earlier step")
        else:
            if (
                set(step) != {"id", "tool", "arguments"}
                or step["tool"] not in server.TOOL_MODELS
            ):
                raise ValueError("Only existing MCP tools allowed")
            if step["tool"] in {
                "memory_compact",
                "memory_consolidate",
                "memory_ingest",
                "memory_migrate_v3",
            }:
                raise ValueError("Maintenance/download outside this synthetic protocol")

            # Validate concrete arguments now; references validate syntax/order
            # here and real models immediately before the actual API call.
            def audit_ref(v):
                if isinstance(v, dict):
                    if "$ref" in v:
                        if (
                            set(v) != {"$ref"}
                            or not isinstance(v["$ref"], str)
                            or v["$ref"].split(".")[0] not in prior
                        ):
                            raise ValueError("Dangling/forward reference")
                        if any(
                            not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9-]*|[0-9]+", p)
                            for p in v["$ref"].split(".")
                        ):
                            raise ValueError("Invalid reference path")
                        return True
                    return any([audit_ref(x) for x in v.values()])
                if isinstance(v, list):
                    return any([audit_ref(x) for x in v])
                return False

            if not audit_ref(step["arguments"]):
                server.TOOL_MODELS[step["tool"]].model_validate(step["arguments"])
        prior[step["id"]] = step
    if "fault" in case:
        f = case["fault"]
        if (
            set(f) != {"step", "point", "occurrence", "kind"}
            or f["step"] not in prior
            or f["kind"] != "exception"
            or type(f["occurrence"]) is not int
            or f["occurrence"] < 1
        ):
            raise ValueError(
                "P-B collector fault=exception only; process_exit uses the separate B9 crash matrix"
            )
        if not re.fullmatch(r"(before|after)_[a-z_]+", f["point"]):
            raise ValueError("Unknown boundary syntax")


@contextmanager
def deterministic_ids(case_id):
    previous = uuid.uuid4
    count = 0

    def fixed():
        nonlocal count
        count += 1
        return uuid.uuid5(uuid.NAMESPACE_URL, case_id + ":" + str(count))

    uuid.uuid4 = fixed
    try:
        yield
    finally:
        uuid.uuid4 = previous


def normalized(value):
    if isinstance(value, dict):
        result = {k: normalized(v) for k, v in value.items()}
        for cursor_name in ("next_cursor", "since_cursor"):
            if isinstance(result.get(cursor_name), str):
                raw = base64.urlsafe_b64decode(
                    result[cursor_name] + "=" * ((-len(result[cursor_name])) % 4)
                )
                result[cursor_name] = json.loads(
                    raw[:-32]
                )  # omit private-key-dependent MAC only
        return result
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def collect_case(case, directory, path):
    import anyio
    from memory_os_v4 import Config, MemoryV4, server
    from memory_os_v4.store import MemoryItem
    from memory_os_v4.ops.settings import OpsSettings
    from memory_os_v4.ops.durable_relay import DurableRelay
    from memory_os_v4.ops.relay import SessionToolCatalog, task_owner_arguments
    from mcp.types import Implementation
    from memory_os_v4.brief_tokens import counter

    now = timestamp(case["now"])
    cfg = Config(
        data_dir=str(directory / "store"),
        store_backend=case["backend"],
        work_state=bool(case["seed"]["tasks"])
        or any(s.get("tool", "").startswith("memory_task_") for s in case["steps"]),
        embed_model="hash",
        hash_dim=16,
        project_registry=None,
    )
    sent = []

    async def tool_result(name, args):
        from mcp.types import CallToolResult

        result = await server.call_tool(name, args)
        return CallToolResult(content=result) if isinstance(result, list) else result

    class Session:
        async def call_tool(self, name, args):
            sent.append({"tool": name, "arguments": deepcopy(args)})
            return await tool_result(name, args)

    class Transport:
        async def submit(self, op, *, replay):
            return await op(Session())

        async def control(self, op):
            return await op(Session())

    settings = OpsSettings(
        url="http://127.0.0.1:9/mcp/",
        store_dir=directory / "store",
        token_file=directory / "token",
        backup_dir=directory / "backups",
        backup_key=directory / "key",
        ops_dir=directory / "ops",
        service_label=None,
    )
    outputs = {}
    original = {}
    records = []
    reopened = False
    unresolved_after_fault = False
    with wall_clock(now), forbid_neural(), deterministic_ids(case["id"] + ":" + path):
        with MemoryV4(config=cfg) as seed, bound_engine(server, seed):
            for raw in case["seed"]["items"]:
                item = dict(raw)
                item.setdefault("created_at", case["now"])
                item.setdefault("event_time", case["now"])
                for key in (
                    "event_time",
                    "created_at",
                    "valid_from",
                    "valid_until",
                    "last_accessed",
                ):
                    if item.get(key) is not None:
                        item[key] = timestamp(item[key])
                seed.store.append(MemoryItem(**item))
            for b in case["seed"]["blocks"]:
                seed.blocks.set(**b)
            for t in case["seed"]["tasks"]:
                args = {
                    k: v for k, v in t.items() if k not in {"dated_fixture", "update"}
                }
                with wall_clock(timestamp(t.get("dated_fixture", case["now"]))):
                    made = server.dispatch("memory_task_create", args)
                    if made.get("ok") is not True:
                        raise ValueError("Task seed refused")
                    if "update" in t:
                        updated = server.dispatch(
                            "memory_task_update",
                            {
                                **t["update"],
                                "expected_etag": made["etag"],
                                "task_id": made["task_id"],
                                "project": args["project"],
                            },
                        )
                        if updated.get("ok") is not True:
                            raise ValueError("Task seed update refused")
        with MemoryV4(config=cfg) as engine, bound_engine(server, engine):
            relay = DurableRelay(Transport(), settings)
            catalog = SessionToolCatalog()
            catalog._tools = anyio.run(server.list_tools)
            client = Implementation(**case["client"])
            fault_count = 0
            try:
                for step in case["steps"]:
                    if "bind_preview" in step:
                        outputs[step["id"]] = bind_preview(
                            step["bind_preview"], outputs, original
                        )
                        records.append(
                            {
                                "step_id": step["id"],
                                "binding": deepcopy(outputs[step["id"]]),
                            }
                        )
                        continue
                    if unresolved_after_fault and step["tool"] not in {
                        "memory_request_status",
                        "memory_search",
                        "memory_get_context",
                        "memory_timeline",
                        "memory_status",
                        "memory_task_get",
                        "memory_task_list",
                        "memory_block_get",
                    }:
                        raise ValueError(
                            "Fault outcome unresolved: no mutation replay permitted"
                        )
                    args = refs(step["arguments"], outputs)
                    server.TOOL_MODELS[step["tool"]].model_validate(args)
                    original[step["id"]] = deepcopy(args)

                    def fault(point):
                        nonlocal fault_count
                        rule = case.get("fault", {})
                        if (
                            rule.get("step") == step["id"]
                            and rule.get("point") == point
                        ):
                            fault_count += 1
                            if fault_count == rule["occurrence"]:
                                raise OSError("P-B synthetic injected interruption")

                    if hasattr(engine.store, "fault_hook"):
                        engine.store.fault_hook = fault
                    if path == "relay":
                        if step["tool"] in {
                            "memory_add_facts",
                            "memory_chat_save",
                        } and catalog.fact_client_supported(step["tool"]):
                            args = {
                                **args,
                                "client_provenance": {
                                    "client_id": client.name,
                                    "client_version": client.version,
                                },
                            }
                        if step["tool"] in {"memory_task_create", "memory_task_update"}:
                            args = task_owner_arguments(
                                args,
                                client,
                                creation=step["tool"] == "memory_task_create",
                            )
                        from memory_os_v4.ops.durable_relay import PreflightRefusal
                        from mcp.types import CallToolResult, TextContent

                        try:
                            result = anyio.run(relay.call, step["tool"], args)
                        except PreflightRefusal as exc:
                            result = CallToolResult(
                                isError=True,
                                structuredContent=exc.details,
                                content=[TextContent(type="text", text=str(exc))],
                            )
                    else:
                        result = anyio.run(tool_result, step["tool"], args)
                    if hasattr(engine.store, "fault_hook"):
                        engine.store.fault_hook = None
                    wire = json.dumps(
                        result.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    data = result.structuredContent or json.loads(
                        result.content[0].text
                    )
                    outputs[step["id"]] = data
                    if (
                        step["tool"] == "memory_request_status"
                        and data.get("status") == "completed"
                    ):
                        unresolved_after_fault = False
                    history = {
                        i.id: engine.store.raw_history(i.id)
                        if hasattr(engine.store, "raw_history")
                        else [i.to_dict()]
                        for i in engine.store.all(True)
                    }
                    if not hasattr(engine.store, "raw_history"):
                        history = {}
                        for line in engine.store.items_path.read_bytes().splitlines():
                            if line.strip():
                                record = json.loads(line)
                                history.setdefault(record["id"], []).append(record)
                    # No journal MAC key or bearer token ever enters the collected evidence.
                    blocks = {
                        str(k): [dict(vars(v)) for v in versions]
                        for k, versions in engine.blocks._history.items()
                    }
                    tasks = (
                        [
                            json.loads(r[0])
                            for r in engine.store._connection.execute(
                                "SELECT record_json FROM task_versions ORDER BY task_id,revision"
                            )
                        ]
                        if hasattr(engine.store, "_connection")
                        else []
                    )
                    receipts = [
                        {
                            k: v
                            for k, v in row.items()
                            if k not in {"fingerprint", "raw_args_sha256"}
                        }
                        for row in engine.requests.records.values()
                    ]
                    records.append(
                        {
                            "step_id": step["id"],
                            "arguments": args,
                            "result": data,
                            "serialized_call_tool_result": wire,
                            "sha256": sha(wire.encode()),
                            "bytes": len(wire.encode()),
                            "tokens": counter()(wire),
                            "history": history,
                            "blocks": blocks,
                            "tasks": tasks,
                            "receipts": receipts,
                        }
                    )
                    if getattr(engine.store, "_poisoned", False):
                        # Inspect on a new private engine; NEVER replay the failed mutation.
                        try:
                            engine.close()
                        except RuntimeError:
                            pass
                        engine = MemoryV4(config=cfg)
                        server._engine = engine
                        reopened = True
                        unresolved_after_fault = True
                        records[-1]["private_reopen_after_uncertain"] = True
                if case.get("fault") and fault_count < case["fault"]["occurrence"]:
                    raise ValueError(
                        "Declared fault did not trigger; incompatible case, never silently counted"
                    )
            finally:
                relay.close()
                if reopened:
                    engine.close()
                if hasattr(engine.store, "fault_hook"):
                    engine.store.fault_hook = None

    def paths(v):
        if isinstance(v, str):
            return v.replace(str(directory), "<private-trial>")
        if isinstance(v, dict):
            return {k: paths(x) for k, x in v.items()}
        if isinstance(v, list):
            return [paths(x) for x in v]
        return v

    deterministic = [
        {
            k: normalized(paths(v))
            for k, v in r.items()
            if k not in {"serialized_call_tool_result", "sha256", "bytes", "tokens"}
        }
        for r in records
    ]
    return {
        "records": records,
        "deterministic_sha256": sha(
            json.dumps(
                deterministic, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ),
        "calls_sent": sent,
        "neural_calls": 0,
    }


def worker(args):
    sys.path.insert(0, str(args.worker_source / "v4/src"))
    from memory_os_v4 import models
    from memory_os_v4.store import MemoryItem
    from memory_os_v4.brief_tokens import counter

    counter()  # exact tokenizer must be usable BEFORE any case is seeded
    from memory_os_v4.readiness import build_identity

    if build_identity().get("commit") != args.candidate_commit:
        raise ValueError("Worker source identity mismatch")
    cases, _ = read_inputs(args)
    for case in cases:
        validate(case, models, MemoryItem)
    (args.output / "audit.json").write_text(
        json.dumps({"cases": 40, "compatible": True, "evaluation": False}) + "\n"
    )
    if not args.run:
        return
    rows = []
    for case in cases:
        for path in case["paths"]:
            repeats = []
            for repeat in (1, 2):
                guarded()
                with tempfile.TemporaryDirectory(
                    prefix=case["id"] + "-", dir=args.output
                ) as tmp:
                    result = collect_case(case, Path(tmp), path)
                repeats.append(result["deterministic_sha256"])
                row = {"case_id": case["id"], "path": path, "repeat": repeat, **result}
                with (args.output / "outputs.jsonl").open("a") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
            if repeats[0] != repeats[1]:
                raise ValueError("Determinism refused: " + case["id"] + "/" + path)
    (args.output / "worker.json").write_text(
        json.dumps(
            {
                "complete": True,
                "records": len(rows),
                "cases": 40,
                "neural_calls": 0,
                "evaluation": False,
            }
        )
        + "\n"
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("inputs", "manifest", "binding", "output", "table"):
        p.add_argument("--" + key, type=Path, required=True)
    p.add_argument("--inputs-sha256", required=True)
    p.add_argument("--candidate-commit", required=True)
    p.add_argument("--run", action="store_true")
    p.add_argument("--worker-source", type=Path, help=argparse.SUPPRESS)
    args = p.parse_args()
    for k in ("inputs", "manifest", "binding", "output", "table", "worker_source"):
        if getattr(args, k) is not None:
            setattr(args, k, getattr(args, k).resolve())
    if args.worker_source:
        worker(args)
        return 0
    if not args.output.is_relative_to(ROOT / ".local-qualification"):
        p.error("Private output required")
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "complete": False,
        "evaluation": False,
        "production_modified": False,
        "neural_calls": 0,
    }
    try:
        cases, _ = read_inputs(args)
        if not re.fullmatch(r"[0-9a-f]{40}", args.candidate_commit):
            raise ValueError("Full immutable candidate SHA required")
        binding = json.loads(args.binding.read_text(), object_pairs_hook=unique_object)
        if (
            set(binding)
            != {
                "candidate_commit",
                "inputs_sha256",
                "manifest_sha256",
                "bound_at",
                "bound_by",
            }
            or binding["candidate_commit"] != args.candidate_commit
            or binding["inputs_sha256"] != args.inputs_sha256
            or binding["manifest_sha256"] != sha(args.manifest.read_bytes())
            or not binding["bound_by"]
        ):
            raise ValueError("Frozen binding mismatch")
        timestamp(binding["bound_at"])
        if subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            text=True,
        ).strip():
            raise ValueError("Dirty runner source refused")
        report.update(
            candidate_commit=args.candidate_commit,
            inputs_sha256=args.inputs_sha256,
            manifest_sha256=sha(args.manifest.read_bytes()),
            binding=binding,
            tokenizer_sha256=sha(args.table.read_bytes()),
            resources=guarded(),
        )
        report["source_archive_sha256"] = sha(
            subprocess.check_output(
                ["git", "archive", args.candidate_commit, "v4/src", "pyproject.toml"],
                cwd=ROOT,
            )
        )
        with tempfile.TemporaryDirectory(prefix="pb-private-", dir=args.output) as tmp:
            source = Path(tmp) / "source"
            export_arm(args.candidate_commit, source)
            home = Path(tmp) / "home"
            home.mkdir()
            env = {
                k: v for k, v in os.environ.items() if not k.startswith("MEMORY_V4_")
            }
            env.update(
                HOME=str(home),
                PYTHONPATH=str(source / "v4/src"),
                MEMORY_V4_BUILD_COMMIT=args.candidate_commit,
                MEMORY_V4_EMBED_MODEL="hash",
                MEMORY_V4_QUALIFICATION_METRICS="0",
                MEMORY_V4_CL100K_PATH=str(args.table),
                MEMORY_V4_MIN_FREE_BYTES=os.environ.get(
                    "MEMORY_V4_MIN_FREE_BYTES", "5000000000"
                ),
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                PYTHONDONTWRITEBYTECODE="1",
            )
            base = [
                sys.executable,
                "-B",
                str(Path(__file__).resolve()),
                "--worker-source",
                str(source),
                "--inputs",
                str(args.inputs),
                "--manifest",
                str(args.manifest),
                "--binding",
                str(args.binding),
                "--table",
                str(args.table),
                "--inputs-sha256",
                args.inputs_sha256,
                "--candidate-commit",
                args.candidate_commit,
                "--output",
                str(args.output),
            ]
            for execute in (False, True) if args.run else (False,):
                with (
                    args.output / ("worker.log" if execute else "audit-worker.log")
                ).open("x") as log:
                    rc = run_guarded(
                        base + (["--run"] if execute else []), env=env, log=log
                    )
                if rc:
                    raise ValueError(
                        "Worker refused; keep evidence, no restart in this output"
                    )
        report["complete"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
