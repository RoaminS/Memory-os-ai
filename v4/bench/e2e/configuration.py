"""Load the stdlib-only Config file without importing the ML package facade."""
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys


def resolved_config(model: str, data_dir: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "src/memory_os_v4/config.py"
    name = "_memory_eval_stdlib_config"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    cfg = module.Config(data_dir=data_dir, embed_model=model, device="cpu",
                        embed_batch_size=4, embed_max_seq_length=256, use_recency=False,
                        rerank_model=None, store_backend="jsonl")
    return asdict(cfg)
