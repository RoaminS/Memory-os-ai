"""Paired ingestion-only measurement, two sequential source-isolated jobs."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tarfile
import time
import uuid

from bench.e2e.beam import DATA_SHA, conversations, identity
from bench.e2e.client import offline_env
from bench.e2e.data import sha256
from bench.e2e.guard import Limits, ResourceStop, ensure_priority, sample
from bench.e2e.run import code_record, kill_group, model_record

VERSION = "indexing-batch-v1"
BASE = "07795f28ba6ecbde4f4cfbd21e023d017bdefa55"
DATA = Path("/Users/romain/Memory-os-ai/v4/bench/data/beam/beam_100k.jsonl")
MODEL = Path("/Users/romain/.cache/huggingface/hub/models--ibm-granite--"
             "granite-embedding-97m-multilingual-r2/snapshots/835ad14087e140460703cf0fae09f97d469d65c2")
PROBES = ("current city and preferences", "project configuration", "events in chronological order",
          "user instructions", "recent decision")


def write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def git(clone, *args):
    commands = []
    for name in ("git-state", "lexical-dev-git"):
        metadata = clone / ".local-qualification" / name
        if metadata.is_dir():
            commands.append(["git", "--git-dir", str(metadata)])
    commands.append(["git", "-C", str(clone)])
    # Bundles may be imported in another local metadata directory. An older
    # directory must not shadow an exact commit available in the next one.
    for command in commands:
        try:
            return subprocess.run([*command, *args], cwd=clone, capture_output=True,
                                  check=True, timeout=30).stdout
        except subprocess.CalledProcessError as exc:
            failure = exc
    raise ValueError("Cannot attest exact source commit in local Git metadata") from failure


def archive_records(payload):
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        return {m.name.removeprefix("v4/"): hashlib.sha256(archive.extractfile(m).read()).hexdigest()
                for m in archive if m.isfile() and m.name.endswith(".py")}


def select(items):
    return sorted(items, key=lambda c: identity(VERSION, c.source_id))[:3]


def prepare(clone, directory, revision):
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Exact 40-character candidate commit required")
    if directory.exists():
        raise ValueError("Fresh plan directory required")
    ensure_priority(Limits())
    sample(clone)
    if sha256(DATA) != DATA_SHA:
        raise ValueError("BEAM checksum changed")
    archives = {arm: git(clone, "archive", rev, "v4/src", "v4/bench/e2e")
                for arm, rev in (("before", BASE), ("after", revision))}
    if archive_records(archives["after"]) != code_record():
        raise ValueError("Candidate source differs from declared commit")
    selected = select(list(conversations(DATA)))
    record = model_record(str(MODEL))
    record["max_seq_length"] = 512
    plan = {"version": VERSION, "before": BASE, "after": revision,
            "archive_sha256": {k: hashlib.sha256(v).hexdigest() for k, v in archives.items()},
            "harness_sha256": sha256(Path(__file__)), "data": str(DATA), "data_sha256": DATA_SHA,
            "selected": [{"id": c.id, "source_id": c.source_id} for c in selected],
            "model": record, "machine": {"node": platform.node(), "machine": platform.machine(),
                                         "platform": platform.platform(), "python": sys.version},
            "order": ["before", "after"], "jobs": 2, "batch_size": 4,
            "diagnostic_probes": list(PROBES),
            "wall_seconds_per_job": 5400, "nice": 15, "concurrency": 1,
            "swap_bytes": 8_500_000_000, "free_bytes": 5_000_000_000,
            "targets": {"p50": 300, "p95_nearest_rank": 450, "max": 600},
            "embedding_tolerance": {"atol": 2e-6, "rtol": 1e-5},
            "criterion": "all three pairs complete; every after ingest strictly faster; targets met; "
                         "same canonical items/receipts; vectors within tolerance; diagnostic ranks exact",
            "claim": "three-conversation feasibility only, no population p95 or answer/retrieval evaluation"}
    directory.mkdir(mode=0o700, parents=True)
    for arm, payload in archives.items():
        (directory / f"{arm}.tar").write_bytes(payload)
        root = directory / arm
        root.mkdir()
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for member in archive:
                if not member.isdir() and not member.isfile():
                    raise ValueError("Only source files/directories allowed")
                if not (root / member.name).resolve().is_relative_to(root):
                    raise ValueError("Unsafe archive path")
            archive.extractall(root, filter="data")
    write(directory / "plan.json", plan)
    write(directory / "receipt.json", {"plan_sha256": sha256(directory / "plan.json")})
    return plan


def frozen(directory):
    plan = json.loads((directory / "plan.json").read_text())
    if (plan["version"] != VERSION or
            json.loads((directory / "receipt.json").read_text())["plan_sha256"] !=
            sha256(directory / "plan.json") or plan["harness_sha256"] != sha256(Path(__file__))):
        raise ValueError("Plan/harness changed")
    if sha256(Path(plan["data"])) != plan["data_sha256"]:
        raise ValueError("Dataset changed")
    observed = model_record(plan["model"]["id"])
    observed["max_seq_length"] = 512
    if observed != plan["model"]:
        raise ValueError("Local model changed")
    for arm in plan["order"]:
        tar = directory / f"{arm}.tar"
        if sha256(tar) != plan["archive_sha256"][arm]:
            raise ValueError("Source archive changed")
        root = directory / arm / "v4"
        found = {str(p.relative_to(root)): sha256(p) for parent in (root / "src", root / "bench/e2e")
                 for p in parent.rglob("*.py")}
        if found != archive_records(tar.read_bytes()):
            raise ValueError("Extracted source changed")
    machine = plan["machine"]
    if machine != {"node": platform.node(), "machine": platform.machine(),
                   "platform": platform.platform(), "python": sys.version}:
        raise ValueError("Paired measurements require identical machine/runtime")
    return plan


def snapshot(store, output):
    # The server is the sole writer; inspect only its already-flushed private files.
    import numpy as np
    items = {}
    for line in (store / "items.jsonl").read_text().splitlines():
        row = json.loads(line)
        items[row["id"]] = row
    names = {iid: (iid if row["kind"] != "episode" else f"episode:{i}")
             for i, (iid, row) in enumerate(items.items())}
    normalized = []
    for iid, original in items.items():
        row = dict(original)
        row["id"] = names[iid]
        row["parent_ids"] = [names.get(x, x) for x in row["parent_ids"]]
        for key in ("created_at", "access_count", "last_accessed"):
            row.pop(key, None)
        normalized.append(row)
    ids = json.loads((store / "ids.json").read_text())
    receipts = [json.loads(line) for line in (store / "requests.jsonl").read_text().splitlines()]
    write(output.with_suffix(".json"), {"items": normalized, "ids": [names[i] for i in ids],
                                        "receipts": receipts})
    np.save(output.with_suffix(".npy"), np.load(store / "embeddings.npy", allow_pickle=False))


async def worker(plan, arm, output):
    from bench.e2e.network import deny_ip_network
    deny_ip_network()
    from bench.e2e.client import ToolClient, project_for
    from bench.e2e.leaderboard import AddSearchAdapter
    from bench.e2e.pipeline import extract
    root = output / "memory"
    t = time.perf_counter()
    rows = []
    async with ToolClient(root, plan["model"]["id"], resident=True,
                          config_profile="beam-c4-4.0.2") as client:
        startup = time.perf_counter() - t
        adapter = AddSearchAdapter(client, "beam-c4-v1-capture")
        chosen = {c["id"] for c in plan["selected"]}
        for c in conversations(Path(plan["data"])):
            if c.id not in chosen:
                continue
            opened = await client.call("eval_open_conversation", {"conversation_id": c.id})
            if not opened["store_empty"] or not opened["encoder_reused"]:
                raise ValueError("Resident isolation failed")
            before = json.loads((root / "usage.json").read_text())
            t = time.perf_counter()
            quotes = extract(project_for(adapter.namespace, c.id), c.turns, 1024)
            result = await adapter._capture(c.id, c.turns, quotes)
            elapsed = time.perf_counter() - t
            if result["deduped"] or result["added"] != len(quotes):
                raise ValueError("Lossless capture failed")
            usage = json.loads((root / "usage.json").read_text())
            # Outside the timed ingestion: fixed, question-blind parity probes,
            # no BEAM probing question, reference, answer or retrieval score.
            ranks = [[hit["id"] for hit in (await adapter.search(c.id, query, 10))["results"]]
                     for query in plan["diagnostic_probes"]]
            snapshot(root / "conversations" / c.id, output / c.id)
            rows.append({"id": c.id, "source_id": c.source_id, "ingest_seconds": elapsed,
                         "capture": result, "open": opened, "diagnostic_ranks": ranks,
                         "usage": {k: usage[k] - before[k] for k in
                                   ("encoder_calls", "encoder_texts", "encoder_ms", "llm_calls")}})
            write(output / "rows.json", rows)
    write(output / "complete.json", {"arm": arm, "startup_seconds": startup, "rows": rows})


def rss_bytes(child, registry):
    pids = [child.pid]
    for path in registry.glob("*.json"):
        pids.append(json.loads(path.read_text())["pid"])
    result = subprocess.run(["/bin/ps", "-p", ",".join(map(str, pids)), "-o", "rss="],
                            capture_output=True, text=True, timeout=3)
    # A disappearing worker is an ordinary process-exit race. While it is
    # alive, denied/unknown measurements must never count as zero memory.
    if result.returncode or not result.stdout.strip():
        if child.poll() is not None:
            return 0
        raise ResourceStop("RSS measurement denied/unavailable")
    try:
        values = [int(x) for x in result.stdout.split()]
    except ValueError as exc:
        raise ResourceStop("Unknown RSS measurement format") from exc
    if any(x < 0 for x in values):
        raise ResourceStop("Negative RSS measurement refused")
    return sum(x * 1024 for x in values)


def run_job(directory, plan, arm):
    from bench.e2e.processes import cleanup
    stage = directory / arm
    output = stage / ".local-qualification/measurement"
    output.mkdir(parents=True, exist_ok=False)
    registry = output / "server-registry"
    env = offline_env()
    env.update(EVAL_PROCESS_OWNER=uuid.uuid4().hex, EVAL_SERVER_REGISTRY=str(registry),
               PYTHONPATH=os.pathsep.join([str(stage / "v4/src"), str(stage / "v4")]))
    command = [sys.executable, str(Path(__file__).resolve()), "_worker",
               "--plan-dir", str(directory), "--arm", arm]
    samples = []
    with (output / "worker.log").open("x") as log:
        child = subprocess.Popen(command, env=env, cwd=output, stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + plan["wall_seconds_per_job"]
        try:
            while child.poll() is None:
                resources = sample(directory)
                resources["rss_bytes"] = rss_bytes(child, registry)
                samples.append(resources)
                if resources["rss_bytes"] > 4 * 1024 ** 3 or time.monotonic() > deadline:
                    raise ResourceStop("RSS >4 GiB or preregistered wall budget exceeded")
                time.sleep(.5)
            if child.returncode or not (output / "complete.json").exists():
                raise RuntimeError("Measurement failed; no automatic restart")
        finally:
            try:
                cleanup(registry, env["EVAL_PROCESS_OWNER"])
            finally:
                kill_group(child)
                write(output / "resource-samples.json", samples)


def compare(directory, plan):
    import numpy as np
    arms = {a: directory / a / ".local-qualification/measurement" for a in plan["order"]}
    results = {a: json.loads((p / "complete.json").read_text())["rows"] for a, p in arms.items()}
    by = {a: {r["id"]: r for r in rs} for a, rs in results.items()}
    expected = {c["id"] for c in plan["selected"]}
    if any(set(rs) != expected for rs in by.values()):
        raise ValueError("Missing/extra conversation")
    pairs = []
    for cid in sorted(expected):
        a, b = (json.loads((arms[s] / f"{cid}.json").read_text()) for s in plan["order"])
        if a != b or by["before"][cid]["capture"] != by["after"][cid]["capture"]:
            raise ValueError("Items/provenance/capture results changed")
        if by["before"][cid]["diagnostic_ranks"] != by["after"][cid]["diagnostic_ranks"]:
            raise ValueError("Hybrid diagnostic ranks changed")
        va, vb = (np.load(arms[s] / f"{cid}.npy", allow_pickle=False) for s in plan["order"])
        if va.shape != vb.shape or not np.allclose(va, vb, **plan["embedding_tolerance"]):
            raise ValueError("Embedding tolerance exceeded")
        # No query encoding or BEAM question: use stored vectors as deterministic
        # diagnostic probes. Unit tests cover exact hybrid ranks with hash vectors.
        indices = list(range(0, len(va), max(1, len(va) // 32)))[:32]
        for i in indices:
            sa, sb = va @ va[i], vb @ vb[i]
            ra = sorted(range(len(sa)), key=lambda j: (-float(sa[j]), a["ids"][j]))[:10]
            rb = sorted(range(len(sb)), key=lambda j: (-float(sb[j]), b["ids"][j]))[:10]
            if ra != rb:
                raise ValueError("Diagnostic dense ranks changed; do not relax the gate")
        before, after = (by[s][cid]["ingest_seconds"] for s in plan["order"])
        pairs.append({"id": cid, "before_seconds": before, "after_seconds": after,
                      "speedup": before / after, "max_embedding_difference": float(np.max(np.abs(va - vb)))})
    times = sorted(p["after_seconds"] for p in pairs)
    p50, p95 = times[len(times) // 2], times[math.ceil(.95 * len(times)) - 1]
    met = all(p["after_seconds"] < p["before_seconds"] for p in pairs)
    met = met and p50 <= 300 and p95 <= 450 and max(times) <= 600
    return {"complete": True, "pairs": pairs, "p50": p50, "p95_nearest_rank": p95,
            "max": max(times), "criterion_met": met, "claim": plan["claim"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "run", "compare", "_worker"])
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--arm", choices=["before", "after"])
    args = parser.parse_args(argv)
    clone = Path(__file__).resolve().parents[3]
    directory = args.plan_dir.resolve()
    if not directory.is_relative_to(clone / ".local-qualification"):
        raise ValueError("Plan must be in this clone's private qualification directory")
    if args.operation == "prepare":
        if not args.source_commit:
            raise ValueError("Source commit required")
        prepare(clone, directory, args.source_commit)
    elif args.operation == "_worker":
        # Archives/model are verified by the supervisor before both workers.
        plan = json.loads((directory / "plan.json").read_text())
        asyncio.run(worker(plan, args.arm, directory / args.arm / ".local-qualification/measurement"))
    else:
        plan = frozen(directory)
        if args.operation == "run":
            def terminate(signum, frame):
                raise ResourceStop("Operator interrupted measurement")
            signal.signal(signal.SIGTERM, terminate)
            ensure_priority(Limits())
            sample(directory)
            with (directory / "started.json").open("x") as f:
                json.dump({"jobs_authorized": 2}, f)
            try:
                for arm in plan["order"]:
                    run_job(directory, plan, arm)
            except BaseException as exc:
                write(directory / "stopped.json", {"reason": str(exc), "complete": False})
                raise
        write(directory / "report.json", compare(directory, plan))


if __name__ == "__main__":
    main()
