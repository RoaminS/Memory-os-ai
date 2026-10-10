"""Dense embedders: sentence-transformers wrapper and a dependency-free hashing
bag-of-words embedder used by unit tests (and available via ``embed_model="hash"``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import importlib.metadata
from pathlib import Path

import numpy as np

from .text import tokenize

logger = logging.getLogger("memory_os_v4")

# Process-wide cache of loaded SentenceTransformer models, keyed by
# (model_name, resolved device, max_seq_length, compute dtype). 2026-09-13 resource
# constraint: MemoryV4 (and therefore a fresh Embedder) is recreated per
# conversation by bench/retrievers.py's V4Retriever.reset() -- for
# LongMemEval oracle (one question per "conversation") that is up to 150
# full model reloads in a single run. Each un-cached load kept its own
# model resident on mps until Python happened to garbage-collect the
# previous Embedder, which is what caused an actual `MPS backend out of
# memory` crash reloading ibm-granite/granite-embedding-97m-multilingual-r2
# under a tightened PYTORCH_MPS_HIGH_WATERMARK_RATIO. Caching the model
# object itself (not its outputs -- every encode() call is still a real,
# identical forward pass) fixes both the reload latency and the memory
# pressure, with zero effect on any embedding value.
_MODEL_CACHE: dict[tuple[str, str, int, str], object] = {}


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        return "mps" if torch.backends.mps.is_available() else "cpu"
    except Exception:  # pragma: no cover - torch always present here
        return "cpu"


class Embedder:
    """Lazy wrapper; float32 output arrays do not imply float32 model computation."""

    def __init__(
        self,
        model_name: str,
        device: str = "auto",
        max_seq_length: int = 512,
        batch_size: int = 32,
        dtype: str = "native",
    ) -> None:
        self.model_name = model_name
        self.device = _resolve_device(device)
        self.batch_size = batch_size
        if dtype not in {"native", "float32"}:
            raise ValueError("Unsupported model compute dtype")
        self.dtype = dtype
        # Palier 2-3 (E10, Config.embed_max_seq_length): some cached models
        # (e.g. ibm-granite/granite-embedding-97m-multilingual-r2, ModernBERT-
        # based) report a native max_seq_length far longer (32768) than any
        # turn/question this codebase actually encodes -- capping it keeps
        # encoding fast without truncating real inputs.
        self.max_seq_length = max_seq_length
        self._model = None
        self._dim: int | None = None

    def _ensure_model(self):
        if self._model is None:
            key = (self.model_name, self.device, self.max_seq_length, self.dtype)
            cached = _MODEL_CACHE.get(key)
            if cached is None:
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
                os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
                import torch
                from sentence_transformers import SentenceTransformer

                torch.set_num_threads(1)  # see memory_os_v4/__init__.py: avoids a faiss/torch segfault
                if self.device == "mps" and not torch.backends.mps.is_available():
                    raise RuntimeError("Explicit MPS device unavailable; no CPU fallback")
                kwargs = {"model_kwargs": {"torch_dtype": torch.float32}} if self.dtype == "float32" else {}
                cached = SentenceTransformer(self.model_name, device=self.device, **kwargs)
                if self.dtype == "float32" and any(p.dtype != torch.float32 for p in cached.parameters()):
                    raise RuntimeError("Model did not honor explicit float32 precision")
                cached.max_seq_length = self.max_seq_length
                cached.encode(["_warmup_"], show_progress_bar=False)  # force one real forward pass
                _MODEL_CACHE[key] = cached
            self._model = cached
            self._dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._ensure_model()
        return self._dim  # type: ignore[return-value]

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        model = self._ensure_model()
        vecs = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        # 2026-09-13 resource constraint (16 GB RAM, MPS-wired memory
        # pressure): release cached MPS allocator blocks after every encode()
        # call so a long bench run's peak wired memory doesn't grow
        # unbounded across many small batches. No-op on cpu/older torch.
        if self.device == "mps":
            import torch

            if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                torch.mps.empty_cache()
        return np.asarray(vecs, dtype=np.float32)

    def encoding_profile(self) -> dict:
        """Attest loaded parameters, not the dtype of exported numpy arrays.

        No additional forward pass. A missing model revision is explicitly
        reported: this receipt attests local runtime, not remote weights.
        """
        model = self._ensure_model()
        parameters = list(model.parameters())
        revision = None
        try:
            revision = getattr(model[0].auto_model.config, "_commit_hash", None)
        except (AttributeError, KeyError, TypeError, IndexError):
            pass
        path = Path(self.model_name)
        if path.is_dir() and path.parent.name == "snapshots":
            revision = path.resolve().name
        runtime = {}
        for name in ("torch", "sentence-transformers", "transformers", "numpy"):
            try:
                runtime[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                runtime[name] = None
        return {"parameter_dtypes": sorted({str(p.dtype) for p in parameters}),
                "parameter_devices": sorted({p.device.type for p in parameters}),
                "model_revision": revision, "runtime": runtime,
                "encoder_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "normalized": True, "output_dtype": "float32"}


class HashEmbedder:
    """Deterministic hashing-trick bag-of-words embedder (no ML, for fast tests)."""

    model_name = "hash"

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in tokenize(text):
                digest = hashlib.md5(tok.encode("utf-8")).hexdigest()
                idx = int(digest[:8], 16) % self.dim
                sign = 1.0 if int(digest[8], 16) % 2 == 0 else -1.0
                vecs[i, idx] += sign
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (vecs / norms).astype(np.float32)
