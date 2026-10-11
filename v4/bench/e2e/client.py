"""Real MCP stdio tool client; dispatch mode is an explicitly distinct diagnostic."""
from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys

from .configuration import resolved_config


@asynccontextmanager
async def owned_stdio(params, errlog):
    """Register SDK's separately sessioned server immediately after spawn.

    anyio.open_process is a public API. This temporary wrapper is confined to
    the sequential evaluation worker; it is always restored on context exit.
    """
    import anyio
    from mcp.client.stdio import stdio_client
    original = anyio.open_process
    registry = Path(params.env["EVAL_SERVER_REGISTRY"])
    owner = params.env["EVAL_PROCESS_OWNER"]
    registry.mkdir(parents=True, exist_ok=True)

    async def opened(*args, **kwargs):
        process = await original(*args, **kwargs)
        try:
            record = registry / f"{process.pid}-{owner}.json"
            temporary = record.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": process.pid, "owner": owner}))
            temporary.replace(record)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        return process

    anyio.open_process = opened
    try:
        async with stdio_client(params, errlog=errlog) as streams:
            yield streams
    finally:
        anyio.open_process = original


def offline_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MEMORY_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "GROQ_", "OLLAMA_"))}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTHONOPTIMIZE="0",
               OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               VECLIB_MAXIMUM_THREADS="1", TOKENIZERS_PARALLELISM="false")
    return env


class ToolClient:
    def __init__(self, root: Path, model: str, transport: str = "stdio",
                 overrides: dict | None = None, reranker: str | None = None, resident: bool = False,
                 config_profile: str = 'historical', encoding: dict | None = None):
        self.root, self.model, self.transport = root, model, transport
        self.overrides, self.reranker, self.resident = overrides, reranker, resident
        self.config_profile = config_profile
        self.encoding = encoding
        if resident and transport != "stdio":
            raise ValueError("Resident campaigns require real private MCP stdio")
        self.stack = AsyncExitStack()

    async def __aenter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.transport == "dispatch":
            from .usage import install
            install(self.root / "usage.json", self.model)
            from memory_os_v4 import Config, MemoryV4
            from memory_os_v4 import server
            cfg = Config(**resolved_config(self.model, str(self.root / "store"), self.overrides,
                                           self.reranker, profile=self.config_profile, encoding=self.encoding))
            self.engine = MemoryV4(config=cfg)
            server.set_engine(self.engine)
        elif self.transport == "stdio":
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters
            self.root.mkdir(parents=True, exist_ok=True)
            self.stderr = self.stack.enter_context((self.root / "server.stderr").open("w"))
            env = offline_env()
            env.update(MEMORY_V4_DIR=str(self.root / "store"), MEMORY_V4_EMBED_MODEL=self.model,
                       MEMORY_V4_DEVICE="cpu", MEMORY_V4_EMBED_BATCH_SIZE="4",
                       MEMORY_V4_EMBED_MAX_SEQ_LENGTH="256",
                       MEMORY_V4_USE_RECENCY="0", MEMORY_V4_STORE_BACKEND="jsonl")
            env["MEMORY_EVAL_RESIDENT"] = "1" if self.resident else "0"
            env["MEMORY_EVAL_USAGE_FILE"] = str(self.root / "usage.json")
            config_path = self.root / "engine-config.json"
            config_path.write_text(json.dumps(resolved_config(self.model, str(self.root / "store"),
                                                              self.overrides, self.reranker,
                                                              profile=self.config_profile,
                                                              encoding=self.encoding)))
            if self.encoding and self.encoding['device'] == 'mps':
                env.update(PYTORCH_ENABLE_MPS_FALLBACK='0', PYTORCH_MPS_HIGH_WATERMARK_RATIO='0.25',
                           PYTORCH_MPS_LOW_WATERMARK_RATIO='0.20')
            if self.config_profile in {'beam-c4-production-4.0.3', 'lme-s-production-4.0.3'}:
                env.update(MEMORY_EVAL_REQUIRE_MPS='1', PYTORCH_ENABLE_MPS_FALLBACK='0', PYTORCH_MPS_HIGH_WATERMARK_RATIO='0.4',
                           PYTORCH_MPS_LOW_WATERMARK_RATIO='0.3')
            env["MEMORY_EVAL_CONFIG_FILE"] = str(config_path)
            # Absolute source roots, no production editable-package dependency.
            v4 = Path(__file__).resolve().parents[2]
            source = os.environ.get('MEMORY_EVAL_ENGINE_SOURCE')
            if source is not None:
                source = Path(source).resolve()
                if not source.is_relative_to(v4.parent / '.local-qualification') or not (source / 'memory_os_v4/config.py').is_file():
                    raise ValueError('Frozen engine must be an extracted private source tree')
            env["PYTHONPATH"] = os.pathsep.join([str(source or v4 / 'src'), str(v4)])
            params = StdioServerParameters(command=sys.executable,
                                          args=["-m", "bench.e2e.offline_server",
                                                f"--eval-owner={env['EVAL_PROCESS_OWNER']}",
                                                "--transport", "stdio"],
                                          env=env, cwd=str(self.root))
            try:
                read, write = await self.stack.enter_async_context(owned_stdio(params, errlog=self.stderr))
                self.session = await self.stack.enter_async_context(ClientSession(read, write))
                await self.session.initialize()
            except BaseException:
                await self.stack.aclose()
                raise
        else:
            raise ValueError("Unknown transport")
        return self

    async def call(self, name: str, payload: dict) -> dict:
        if self.transport == "dispatch":
            from memory_os_v4.server import dispatch
            out = dispatch(name, payload)
        else:
            result = await self.session.call_tool(name, payload)
            if result.isError:
                raise RuntimeError(f"MCP tool failed: {name}")
            out = getattr(result, "structuredContent", None)
            if out is None:
                blocks = [c.text for c in result.content if getattr(c, "type", "") == "text"]
                out = json.loads("\n".join(blocks))
        if not isinstance(out, dict) or out.get("ok") is False:
            raise RuntimeError(f"Tool {name} returned an error")
        return out

    async def __aexit__(self, *exc):
        if self.transport == "dispatch":
            from memory_os_v4 import server
            server.set_engine(None)
        await self.stack.aclose()


def project_for(namespace: str, user_id: str) -> str:
    if not namespace or not user_id:
        raise ValueError("Explicit namespace and user required")
    return "eval-" + hashlib.sha256(json.dumps([namespace, user_id]).encode()).hexdigest()
