#!/usr/bin/env python3
"""Exact offline A1 envelope proof, synthetic/hash only, no TCP or release claim."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "v4/src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path, required=True, help="Already provisioned cl100k file; never downloaded")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / ".local-qualification"):
        parser.error("Fresh output under this checkout's .local-qualification required")
    output.mkdir(parents=True, exist_ok=False)
    os.environ["MEMORY_V4_CL100K_PATH"] = str(args.table.resolve())
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    report = {"ok": False, "inference": False, "production_modified": False,
              "tcp_qualified": False, "release_qualified": False, "cases": []}
    try:
        import anyio
        from memory_os_v4 import Config, MemoryV4, server
        from memory_os_v4.brief_tokens import counter, status
        from memory_os_v4.ops.durable_relay import DurableRelay
        from memory_os_v4.ops.relay import SessionToolCatalog
        from memory_os_v4.ops.qualified_runtime import inspect_current

        report["runtime"] = inspect_current(extras=("ops", "brief"))
        if not report["runtime"]["ok"]:
            raise ValueError("Qualified dependency runtime required; no override")
        count = counter()
        report["tokenizer"] = status()
        source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                text=True, capture_output=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                               text=True, capture_output=True, check=True).stdout.strip()
        report.update(source_commit=source, source_dirty=bool(dirty))
        files = ["brief.py", "brief_selection.py", "brief_value.py", "brief_coverage.py",
                 "models.py", "server.py", "ops/durable_relay.py", "ops/relay.py"]
        report["source_sha256"] = {name: hashlib.sha256((ROOT / "v4/src/memory_os_v4" / name).read_bytes()).hexdigest()
                                   for name in files}
        class Ledger:
            def status(self):
                return {"unresolved_count": 1, "errors": [], "journal_poisoned": False}
        class Transport:
            async def submit(self, operation, *, replay):
                class Session:
                    async def call_tool(self, name, args):
                        return await server.call_tool(name, args)
                return await operation(Session())
        with tempfile.TemporaryDirectory(prefix="a1-private-", dir=output) as temporary:
            with MemoryV4(config=Config(data_dir=temporary, store_backend="sqlite_s1",
                                       embed_model="hash", hash_dim=16)) as engine:
                engine.add_facts([{"text": f"Synthetic A1 marker {i}: keep the pinned operational decision."}
                                  for i in range(20)], project="a1-private")
                previous, previous_get = server._engine, server.get_engine
                server._engine, server.get_engine = engine, lambda: engine
                try:
                    catalog = SessionToolCatalog()
                    catalog._tools = anyio.run(server.list_tools)
                    relay = DurableRelay(Transport(), None)
                    relay.journal = Ledger()
                    relay.brief_wire_formats, relay.brief_budget_supported = catalog.brief_contract()
                    for fmt in ("legacy", "compact_v2"):
                        for budget in (200, 600, 1500):
                            for path in ("direct", "relay"):
                                arguments = {"project": "a1-private", "wire_format": fmt, "budget_tokens": budget}
                                if path == "direct":
                                    result = anyio.run(server.call_tool, "memory_session_brief", arguments)
                                else:
                                    result = anyio.run(relay.call, "memory_session_brief", arguments)
                                data = json.loads(result.content[0].text)
                                from memory_os_v4.brief_coverage import COLUMNS
                                from memory_os_v4.brief_selection import read_units
                                if 'coverage' not in data:
                                    raise ValueError('Scoped counts lost')
                                totals = data['coverage']['totals']
                                totals = dict(zip(COLUMNS, totals)) if isinstance(totals, list) else totals
                                units = read_units(data) or []
                                if (totals['eligible'] != 20 or totals['pool'] != 20
                                        or totals['content'] != sum(u.representation != 'reference' for u in units)
                                        or totals['reference_only'] != sum(u.representation == 'reference' for u in units)
                                        or totals['omitted'] != 20 - len(units)):
                                    raise ValueError('Final coverage accounting failed')
                                wire = json.dumps(result.model_dump(mode="json", by_alias=True, exclude_none=True),
                                                  ensure_ascii=False, separators=(",", ":"))
                                size, tokens = len(wire.encode()), count(wire)
                                if size > 6000 or tokens > budget or result.isError:
                                    raise ValueError("Final envelope budget failed")
                                if (result.structuredContent is None) != (fmt == "compact_v2"):
                                    raise ValueError("Wire format failed")
                                if path == "relay" and "unresolved_requests" not in data.get("notices", []):
                                    raise ValueError("Local unresolved notice lost")
                                report["cases"].append({"wire_format": fmt, "budget_tokens": budget,
                                                        "path": path, "bytes": size, "tokens": tokens})
                finally:
                    server._engine, server.get_engine = previous, previous_get
        report["ok"] = len(report["cases"]) == 12 and not report["source_dirty"]
        if report["source_dirty"]:
            raise ValueError("Dirty source: measurements retained but not qualified")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"ok": report["ok"], "cases": len(report["cases"]), "error": report.get("error")}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
