"""Private MCP stdio server entry point; never opens an HTTP listener."""
from .network import deny_ip_network

deny_ip_network()

import os  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from .usage import install  # noqa: E402

owner = os.environ["EVAL_PROCESS_OWNER"]
if not re.fullmatch(r"[0-9a-f]{32}", owner) or f"--eval-owner={owner}" not in sys.argv:
    raise RuntimeError("Private evaluation owner nonce required")
registry = Path(os.environ["EVAL_SERVER_REGISTRY"]).resolve()
clone = Path(__file__).resolve().parents[3]
if not registry.is_relative_to(clone / ".local-qualification"):
    raise RuntimeError("Server registry outside private clone refused")
registry.mkdir(parents=True, exist_ok=True)

private = clone / ".local-qualification"
for key in ("MEMORY_EVAL_USAGE_FILE", "MEMORY_EVAL_CONFIG_FILE"):
    if not Path(os.environ[key]).resolve().is_relative_to(private):
        raise RuntimeError("Evaluation files outside private clone refused")
raw_cfg = json.loads(Path(os.environ["MEMORY_EVAL_CONFIG_FILE"]).read_text())
if not Path(raw_cfg["data_dir"]).resolve().is_relative_to(private) or raw_cfg["store_backend"] != "jsonl":
    raise RuntimeError("Only a private JSONL evaluation store is allowed")

install(Path(os.environ["MEMORY_EVAL_USAGE_FILE"]), os.environ["MEMORY_V4_EMBED_MODEL"])

from memory_os_v4 import Config, MemoryV4  # noqa: E402
from memory_os_v4.server import run, set_engine  # noqa: E402

if __name__ == "__main__":
    sys.argv.remove(f"--eval-owner={owner}")
    cfg = Config(**raw_cfg)
    if os.environ.get('MEMORY_EVAL_REQUIRE_MPS') == '1':
        from memory_os_v4.embed import _resolve_device
        if _resolve_device(cfg.device) != 'mps':
            raise RuntimeError('Production-profile pilot requires MPS before model load; no CPU fallback')
    if os.environ.get("MEMORY_EVAL_RESIDENT") == "1":
        from memory_os_v4 import memory as memory_module, server as server_module
        from .resident import ResidentPool
        pool = ResidentPool(memory_module, server_module, cfg)
        pool.install()
    else:
        set_engine(MemoryV4(config=cfg))
    run()
