"""Load the stdlib-only Config file without importing the ML package facade."""
from dataclasses import asdict
import importlib.util
from pathlib import Path
import sys


ALLOWED = {"fusion", "w_dense", "w_bm25", "w_entity", "candidates", "dense_norm",
           "full_candidate_scoring", "use_entities", "use_dense", "use_stemming",
           "rerank_top_n", "rerank_weight", "bm25_profile", "lexical_first", "lexical_guard_k"}


def resolved_config(model: str, data_dir: str, overrides: dict | None = None,
                    reranker: str | None = None, *, profile: str = 'historical',
                    encoding: dict | None = None) -> dict:
    path = Path(__file__).resolve().parents[2] / "src/memory_os_v4/config.py"
    name = "_memory_eval_stdlib_config"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    if profile not in {'historical', 'beam-c4-4.0.2', 'beam-c4-production-4.0.3'}:
        raise ValueError('Unknown frozen evaluation configuration profile')
    cfg = module.Config(data_dir=data_dir, embed_model=model, device="cpu",
                        embed_batch_size=4, embed_max_seq_length=256 if profile == 'historical' else 512,
                        use_recency=False, rerank_model=None, store_backend="jsonl")
    if profile == 'historical':
        cfg.bm25_profile, cfg.lexical_guard_k = 'legacy', 0
    elif cfg.bm25_profile != 'scoped_literal' or cfg.lexical_guard_k != 3:
        raise ValueError('BEAM requires approved 4.0.2 C4 retrieval defaults')
    if profile == 'beam-c4-production-4.0.3':
        cfg.device, cfg.embed_dtype, cfg.embed_batch_size = 'auto', 'native', 32
    result = asdict(cfg)
    if encoding is not None:
        allowed = {'device', 'embed_dtype', 'embed_batch_size', 'batch_facts'}
        if profile != 'beam-c4-4.0.2' or not isinstance(encoding, dict) or set(encoding) != allowed:
            raise ValueError('Explicit complete encoding profile requires frozen BEAM C4')
        if (encoding['device'] not in {'cpu', 'mps'} or
                encoding['embed_dtype'] not in {'native', 'float32'} or
                type(encoding['embed_batch_size']) is not int or encoding['embed_batch_size'] != 4 or
                encoding['batch_facts'] is not False):
            raise ValueError('Unsupported preregistered encoding profile')
        result.update(encoding)
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
