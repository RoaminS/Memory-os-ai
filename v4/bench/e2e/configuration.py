"""Load the stdlib-only Config file without importing the ML package facade."""
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys


ALLOWED = {"fusion", "w_dense", "w_bm25", "w_entity", "candidates", "dense_norm",
           "full_candidate_scoring", "use_entities", "use_dense", "use_stemming",
           "rerank_top_n", "rerank_weight", "bm25_profile", "lexical_first", "lexical_guard_k"}


def resolved_config(model: str, data_dir: str, overrides: dict | None = None,
                    reranker: str | None = None) -> dict:
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
    result = asdict(cfg)
    overrides = overrides or {}
    if not isinstance(overrides, dict) or not set(overrides) <= ALLOWED:
        raise ValueError("Only preregistered retrieval fields may be overridden")
    for key, value in overrides.items():
        if type(value) is not type(result[key]):
            # JSON integer weights are safe to promote; booleans are not numbers.
            if type(result[key]) is float and type(value) is int:
                value = float(value)
            else:
                raise ValueError(f"Invalid override type: {key}")
        result[key] = value
    if result["fusion"] not in {"cc", "rrf", "dbsf"} or result["dense_norm"] not in {"minmax", "fixed"}:
        raise ValueError("Unsupported fusion/normalization")
    module.Config(**result).validate_lexical_policy()
    import math
    if any(not math.isfinite(result[k]) or result[k] < 0 for k in ("w_dense", "w_bm25", "w_entity")):
        raise ValueError("Nonnegative finite weights required")
    if not 1 <= result["candidates"] <= 200 or not 1 <= result["rerank_top_n"] <= 50:
        raise ValueError("Candidate/reranking budgets exceed constrained profile")
    if not 0 <= result["rerank_weight"] <= 1:
        raise ValueError("Invalid rerank weight")
    result.update(rerank_model=reranker, rerank_device="cpu")
    return result
