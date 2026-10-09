"""Sequential, nice-15, fail-closed supervisor; no download/network/submission."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
from itertools import islice
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tarfile
import time
import uuid

from .client import offline_env
from .configuration import resolved_config
from .data import cases, inventory, sha256
from .partitions import validate as validate_selection
from .guard import Limits, ResourceStop, ensure_priority, sample
from .stats import EVIDENCE_POLICY, bootstrap_preflight, score, summarize
from .processes import cleanup

SYSTEMS = ["memory", "bm25", "dense", "files", "full-context"]


def capture_namespace(seed: int) -> str:
    # Stores/processes are private per case. Keep citation ids and tie-breaking
    # identical across treatments. Dataset checksums include gold annotations:
    # NEVER feed them into ids/tie-breaking; retain checksums for proof only.
    return hashlib.sha256(json.dumps(["e2e-quotes-v1", seed]).encode()).hexdigest()


def model_record(model: str, check=None) -> dict:
    if model == "hash":
        return {"id": "hash", "revision": "deterministic-core-source", "neural": False,
                "downloads": 0}
    path = Path(model).expanduser().resolve()
    if not path.is_dir() or not (path / "config.json").is_file():
        raise ValueError("Use an EXISTING local model snapshot directory, never a Hub model name")
    records = {str(p.relative_to(path)): sha256(p, check=check) for p in sorted(path.rglob("*")) if p.is_file()}
    if not any(name.endswith((".safetensors", ".bin")) for name in records):
        raise ValueError("Local snapshot has no weights")
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    if total > 300_000_000:
        raise ValueError("This constrained profile only admits cached models <= 300 MB")
    return {"id": str(path), "snapshot_files": records, "bytes": total,
            "revision": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
            "neural": True, "downloads": 0, "device": "cpu", "batch_size": 4,
            "max_seq_length": 256, "precision": "float32"}


def code_record() -> dict:
    v4 = Path(__file__).resolve().parents[2]
    return {str(p.relative_to(v4)): sha256(p) for root in (v4 / "src", v4 / "bench" / "e2e")
            for p in sorted(root.rglob("*.py"))}


def verify_source(clone: Path, revision: str, code: dict) -> None:
    metadata = clone / ".local-qualification/git-state"
    git = ["git", "--git-dir", str(metadata)] if metadata.is_dir() else ["git", "-C", str(clone)]
    result = subprocess.run([*git, "archive", revision, "v4/src", "v4/bench/e2e"],
                            capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError("Cannot attest declared source commit in local Git metadata")
    records = {}
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith(".py"):
                records[member.name.removeprefix("v4/")] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    if records != code:
        raise ValueError("Declared source commit does not match evaluator/core working tree")


def kill_group(child) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=3)


def supervise(command, log, cwd, env, deadline, limits, measurements,
              prediction_path: Path, case_seconds: int):
    """No second worker starts until the first process group has ended."""
    with log.open("x") as f:
        child = subprocess.Popen(command, stdout=f, stderr=subprocess.STDOUT,
                                 cwd=cwd, env=env, start_new_session=True)
        progress_time, last_size = time.monotonic(), -1
        try:
            while child.poll() is None:
                if time.monotonic() >= deadline:
                    raise ResourceStop("Frozen wall-time budget exceeded")
                size = prediction_path.stat().st_size if prediction_path.exists() else 0
                if size != last_size:
                    last_size, progress_time = size, time.monotonic()
                if time.monotonic() - progress_time > case_seconds:
                    raise ResourceStop("Case exceeded wall timeout (including blocking encoder calls)")
                measurements.append(sample(cwd, limits))
                time.sleep(.5)
            return child.returncode
        finally:
            try:
                cleanup(cwd / "server-registry", env.get("EVAL_PROCESS_OWNER", ""))
            finally:
                kill_group(child)


def termination_class(reason, statuses=None):
    if not reason:
        return None
    if reason.startswith(("Frozen wall-time budget exceeded", "Case exceeded wall timeout")):
        return "budget"
    if reason.startswith("Worker "):
        return "budget" if 3 in (statuses or {}).values() else "correctness"
    return "resource" if any(word in reason.lower() for word in ("swap", "free disk", "nice")) else "correctness"


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", required=True, type=Path)
    p.add_argument("--variant", required=True, choices=["s", "oracle"])
    p.add_argument('--bootstrap-policy', choices=['haystack', EVIDENCE_POLICY], default='haystack')
    p.add_argument('--bootstrap-min-clusters', type=int, default=2)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--model", default="hash", help="hash or existing <=300 MB local snapshot directory")
    p.add_argument("--systems", default=",".join(SYSTEMS))
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--context-bytes", type=int, default=12000)
    p.add_argument("--ingest-bytes", type=int, default=16_000_000)
    p.add_argument("--run-seconds", type=int, default=3600)
    p.add_argument("--case-seconds", type=int, default=120)
    p.add_argument("--seed", type=int, default=20261009)
    p.add_argument("--transport", default="stdio", choices=["stdio", "dispatch"])
    p.add_argument("--source-commit", required=True)
    p.add_argument("--engine-overrides", default="{}", help="JSON retrieval overrides only")
    p.add_argument("--reranker", help="existing local <=300 MB cross-encoder snapshot")
    p.add_argument("--selection", type=Path, help="frozen DEVELOPMENT split, never test")
    p.add_argument("--chunk-chars", type=int, default=1024)
    p.add_argument("--unique-turns", action="store_true")
    p.add_argument("--query-mode", choices=["none", "lexical"], default="none")
    p.add_argument("--trace-k", type=int, default=0)
    p.add_argument("--warm-searches", type=int, default=0)
    p.add_argument("--resident", action="store_true")
    args = p.parse_args(argv)
    if args.variant == 'oracle' and (args.bootstrap_policy != 'haystack' or args.bootstrap_min_clusters != 2):
        p.error('S bootstrap amendment forbidden for oracle development')
    if not 128 <= args.chunk_chars <= 2048 or not 0 <= args.trace_k <= 200 or not 0 <= args.warm_searches <= 3:
        p.error("Chunk/trace budget invalid")
    try:
        overrides = json.loads(args.engine_overrides)
        resolved_config(args.model, "<private>", overrides, args.reranker)
    except (ValueError, TypeError) as exc:
        p.error(str(exc))
    if args.resident and args.transport != "stdio":
        p.error("Resident protocol requires real MCP stdio")
    # Graceful operator interruption must also clean SDK-owned server sessions.
    def terminate(signum, frame):
        raise ResourceStop("Evaluation supervisor interrupted by SIGTERM")
    signal.signal(signal.SIGTERM, terminate)
    if any(v < 1 for v in (args.limit, args.top_k, args.context_bytes, args.ingest_bytes,
                           args.run_seconds, args.case_seconds)) or args.top_k > 200:
        p.error("Invalid budget")
    systems = args.systems.split(",")
    if len(systems) != len(set(systems)) or not set(systems) <= set(SYSTEMS) or not systems:
        p.error("Unknown/duplicate system")
    if args.model == "hash" and "dense" in systems:
        p.error("Dense neural baseline requires a cached local snapshot; hash is diagnostic only")
    if len(args.source_commit) != 40 or any(c not in "0123456789abcdef" for c in args.source_commit):
        p.error("An exact source Git commit is required")
    if args.output.exists():
        p.error("Output must be a NEW directory; never overwrite a prior campaign")
    # Data stores/logs live only inside this clone's ignored private directory.
    clone = Path(__file__).resolve().parents[3]
    output = args.output.resolve()
    if not output.is_relative_to(clone / ".local-qualification"):
        p.error("Output must be under this clone/.local-qualification")
    output.parent.mkdir(parents=True, exist_ok=True)
    limits = Limits()
    try:
        ensure_priority(limits)
        first = sample(output.parent, limits)
    except ResourceStop as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 2
    output.mkdir(mode=0o700)
    deadline = time.monotonic() + args.run_seconds
    measurements = [first]
    last_check = 0.0

    def check():
        nonlocal last_check
        if time.monotonic() >= deadline:
            raise ResourceStop("Frozen wall-time budget exceeded during preparation")
        if time.monotonic() - last_check >= .5:
            measurements.append(sample(output, limits))
            last_check = time.monotonic()

    try:
        data = inventory(args.data, args.variant, check=check)
        model = model_record(args.model, check=check)
        model_path = model["id"]
        selection_record = None
        if args.selection:
            all_golds = [g for _, g in cases(args.data, check=check)]
            selection_record = json.loads(args.selection.read_text())
            allowed = validate_selection(selection_record, all_golds, data["sha256"])
            golds = [g for g in all_golds if g.id in allowed][:args.limit]
        else:
            golds = [g for _, g in islice(cases(args.data, check=check), args.limit)]
        reranker = model_record(args.reranker, check=check) if args.reranker else None
        if reranker is not None and not reranker["neural"]:
            raise ValueError("Reranker must be a cached neural snapshot")
        if not golds:
            raise ValueError("Empty dataset")
        bootstrap_record = None
        if args.bootstrap_policy != 'haystack' or args.bootstrap_min_clusters != 2:
            if args.variant != 's':
                raise ValueError('S bootstrap amendment forbidden for oracle development')
            try:
                bootstrap_record = bootstrap_preflight(golds, args.bootstrap_policy, args.bootstrap_min_clusters)
            except ValueError as exc:
                write_json(output / 'bootstrap-preflight.json', {'available': False,
                           'policy': args.bootstrap_policy, 'minimum_clusters': args.bootstrap_min_clusters,
                           'reason': str(exc), 'model_workers_started': 0})
                raise
            write_json(output / 'bootstrap-preflight.json', bootstrap_record)
        selection = [g.id for g in golds]
        code = code_record()
        verify_source(clone, args.source_commit, code)
        cfg = {"stage": "oracle development" if args.variant == "oracle" else "S fixed-candidate confirmation; independence checked by amendment plan",
               "protocol": "e2e-resident-v2" if args.resident else "e2e-quotes-v1", "resident": args.resident, "run_id": capture_namespace(
                   args.seed),
               "campaign_id": hashlib.sha256(str(output).encode()).hexdigest(),
               "source_commit": args.source_commit, "code_files": code, "data": str(args.data.resolve()),
               "process_owner": uuid.uuid4().hex,
               "dataset": data, "selected_ids": selection, "limit": len(golds), "systems": systems,
               "model": model_path, "model_record": model, "transport": args.transport,
               "resolved_engine_config": resolved_config(model_path, "<private fresh store per question>", overrides, args.reranker),
               "engine_overrides": overrides, "reranker": args.reranker, "reranker_record": reranker,
               "selection_filter": bool(args.selection), "selection_record": selection_record,
               "selection_sha256": sha256(args.selection) if args.selection else None,
               "chunk_chars": args.chunk_chars, "unique_turns": args.unique_turns,
               "query_mode": args.query_mode, "trace_k": args.trace_k,
               "warm_searches": args.warm_searches,
               "top_k": args.top_k, "context_bytes": args.context_bytes, "ingest_bytes": args.ingest_bytes,
               "run_seconds": args.run_seconds, "case_seconds": args.case_seconds,
               "seed": args.seed, "bootstrap_replicates": 2000, "confidence": .95,
               "extraction": f"quoted observable spans, {args.chunk_chars} chars, no LLM/no future annotations",
               "response": "highest query-term overlap sentence; answer-proxies-v2 EM/F1/containment (nonofficial)",
               "budgets": "shared UTF-8 bytes; chars/4 estimates are NOT tokenizer budgets",
               "concurrency": 1, "threads": 1, "device": "cpu", "limits": asdict(limits),
               "cache": "memory: resident model per arm, isolated fresh conversation stores" if args.resident else "memory stdio: model cold per case; dense: process-cold then warm",
               "python": sys.version, "platform": platform.platform(),
               "dependencies": sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())}
        config_path = output / "frozen.json"
        if bootstrap_record is not None:
            cfg['bootstrap_policy'] = args.bootstrap_policy
            cfg['bootstrap_min_clusters'] = args.bootstrap_min_clusters
        write_json(config_path, cfg)
        freeze_sha = sha256(config_path)
        write_json(output / "freeze-receipt.json", {"sha256": freeze_sha,
                   "time": datetime.now(timezone.utc).isoformat()})
        work = output / "work"
        work.mkdir(mode=0o700)
        env = offline_env()
        env["EVAL_PROCESS_OWNER"] = cfg["process_owner"]
        env["EVAL_SERVER_REGISTRY"] = str(output / "server-registry")
        v4 = clone / "v4"
        env["PYTHONPATH"] = os.pathsep.join([str(v4), str(v4 / "src")])
        statuses, stopped = {}, None
        for system in systems:
            try:
                # Repeat hash checks immediately before every sequential worker.
                sample(output, limits)
                if sha256(config_path) != freeze_sha or sha256(args.data, check=check) != data["sha256"] or code_record() != code:
                    raise ResourceStop("Frozen inputs changed")
                if args.selection and sha256(args.selection) != cfg["selection_sha256"]:
                    raise ResourceStop("Frozen development selection changed")
                if reranker and model_record(args.reranker, check=check) != reranker:
                    raise ResourceStop("Frozen reranker changed")
                if model_record(model_path, check=check) != model:
                    raise ResourceStop("Frozen model changed")
                cmd = [sys.executable, "-m", "bench.e2e.worker", "--config", str(config_path),
                       "--system", system, "--output", str(output / f"{system}.jsonl"), "--work", str(work)]
                statuses[system] = supervise(cmd, output / f"{system}.log", output, env,
                                             deadline, limits, measurements,
                                             output / f"{system}.jsonl", args.case_seconds)
                sample(output, limits)
                if sha256(config_path) != freeze_sha or sha256(args.data, check=check) != data["sha256"] or code_record() != code:
                    raise ResourceStop("Frozen inputs changed during worker")
                if args.selection and sha256(args.selection) != cfg["selection_sha256"]:
                    raise ResourceStop("Frozen development selection changed")
                if reranker and model_record(args.reranker, check=check) != reranker:
                    raise ResourceStop("Frozen reranker changed")
                if model_record(model_path, check=check) != model:
                    raise ResourceStop("Frozen model changed during worker")
                print(f"{system}: exit {statuses[system]}", flush=True)
                if statuses[system] != 0:
                    stopped = f"Worker {system} failed"
                    break
            except ResourceStop as e:
                stopped = str(e)
                break
        rows = []
        for system in systems:
            path = output / f"{system}.jsonl"
            actual = {}
            if path.exists():
                for line in path.read_text().splitlines():
                    try:
                        r = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # interrupted last row; recorded below as failed
                    actual[r["qid"]] = r
            for g in golds:
                if g.id in actual:
                    rows.append(actual[g.id])
                else:
                    rows.append({"qid": g.id, "system": system, "status": "failed",
                                 "reason": stopped or "Missing prediction", **score([], None, g, args.top_k)})
        # Preserve denominators even if a later guarded bootstrap is interrupted.
        with (output / "predictions.jsonl").open("x") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        report = summarize(rows, golds, systems, args.seed,
                           bootstrap_replicates=0 if stopped else 2000, check=None if stopped else check,
                           cluster_policy=args.bootstrap_policy, minimum_clusters=args.bootstrap_min_clusters)
        report.update(frozen_sha256=freeze_sha, complete=stopped is None,
                      stopped=stopped, termination_class=termination_class(stopped, statuses), selected_count=len(golds), variant=args.variant,
                      all_cases_executed_without_failure=all(len([r for r in rows if r["system"] == s and
                                                       r["status"] != "failed"]) == len(golds) for s in systems),
                      source_commit=args.source_commit, resource_samples=len(measurements),
                      max_swap_bytes=max(m["swap_bytes"] for m in measurements),
                      min_free_bytes=min(m["free_bytes"] for m in measurements),
                      response_judge="answer-proxies-v2 EM/F1/containment, NOT official LME judge",
                      agent_tasks="not evaluated", leaderboard_rank="not evaluated")
        lifecycle = output / "memory-lifecycle.json"
        if lifecycle.exists():
            report["memory_lifecycle"] = json.loads(lifecycle.read_text())
        if not report["all_cases_executed_without_failure"] and report["termination_class"] is None:
            report["termination_class"] = "correctness"
        write_json(output / "report.json", report)
        write_json(output / "resource-samples.json", measurements)
        write_json(output / "proofs.json", {p.name: sha256(p) for p in sorted(output.iterdir()) if p.is_file()})
        print(json.dumps({"report": str(output / "report.json"), "complete": report["complete"]}))
        return 0 if report["complete"] and report["all_cases_executed_without_failure"] else 1
    except (ValueError, OSError, ResourceStop) as e:
        write_json(output / "aborted.json", {"reason": str(e), "complete": False, "termination_class": termination_class(str(e))})
        print(f"STOP: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
