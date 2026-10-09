"""Count real encoder API calls/texts/processed tokens, including warmup."""
from __future__ import annotations

import json
from pathlib import Path
import time

_path: Path | None = None
_counts: dict = {}
_installed = False


def install(path: Path, model: str) -> None:
    global _path, _counts, _installed
    _path = path
    _counts = {"encoder_calls": 0, "encoder_texts": 0, "encoder_input_tokens": 0,
               "reranker_calls": 0, "reranker_pairs": 0, "reranker_ms": 0.0,
               "reranker_input_tokens": 0, "reranker_batch_size": 4,
               "encoder_ms": 0.0, "llm_calls": 0, "api_usd": 0.0,
               "tokenizer": "encoder tokenizer after max_seq_length truncation",
               "warmup_included": True}
    if model == "hash":
        _counts["tokenizer"] = "no neural encoder; no LLM"
        path.write_text(json.dumps(_counts))
        return
    if _installed:
        return
    from sentence_transformers import CrossEncoder, SentenceTransformer
    original = SentenceTransformer.encode

    def encode(self, sentences, *args, **kwargs):
        values = [sentences] if isinstance(sentences, str) else list(sentences)
        # Bounded token counting, matching the model's actual preprocessing.
        count = 0
        for start in range(0, len(values), 4):
            batch = self.tokenize(values[start:start + 4])
            count += int(batch["attention_mask"].sum())
        t = time.perf_counter()
        result = original(self, sentences, *args, **kwargs)
        _counts["encoder_calls"] += 1
        _counts["encoder_texts"] += len(values)
        _counts["encoder_input_tokens"] += count
        _counts["encoder_ms"] += (time.perf_counter() - t) * 1000
        if _path is not None:
            _path.write_text(json.dumps(_counts, allow_nan=False))
        return result

    SentenceTransformer.encode = encode
    predict_original = CrossEncoder.predict

    def predict(self, sentences, *args, **kwargs):
        values = list(sentences)
        if args:
            raise ValueError("Evaluation reranker accepts keyword options only")
        kwargs["batch_size"] = 4
        self.max_length = 256
        tokens = 0
        for start in range(0, len(values), 4):
            pairs = values[start:start + 4]
            batch = self.tokenizer([p[0] for p in pairs], [p[1] for p in pairs],
                                   padding=True, truncation=True, max_length=256,
                                   return_tensors="pt")
            tokens += int(batch["attention_mask"].sum())
        t = time.perf_counter()
        result = predict_original(self, values, **kwargs)
        _counts["reranker_calls"] += 1
        _counts["reranker_pairs"] += len(values)
        _counts["reranker_input_tokens"] += tokens
        _counts["reranker_ms"] += (time.perf_counter() - t) * 1000
        if _path is not None:
            _path.write_text(json.dumps(_counts, allow_nan=False))
        return result

    CrossEncoder.predict = predict
    _installed = True


def read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {
        "encoder_calls": 0, "encoder_texts": 0, "encoder_input_tokens": 0,
        "llm_calls": 0, "api_usd": 0.0, "tokenizer": "none", "warmup_included": True}
