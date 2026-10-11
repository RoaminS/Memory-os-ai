"""Exact, local-only cl100k_base. No registry loader/network/cache download path."""

import base64
from functools import lru_cache
import hashlib
import importlib.metadata
import os
from pathlib import Path

TABLE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
# cl100k_base pattern from MIT-licensed tiktoken openai_public.py.
PATTERN = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"


class TokenizerUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load(path):
    try:
        import tiktoken

        if importlib.metadata.version("tiktoken") != "0.12.0":
            raise ValueError("unqualified tokenizer implementation version")

        if not path:
            raise ValueError("explicit table path required")
        with Path(path).expanduser().open("rb") as source:
            raw = source.read(2_000_001)
        if hashlib.sha256(raw).hexdigest() != TABLE_SHA256:
            raise ValueError("unattested table")
        ranks = {
            base64.b64decode(token): int(rank) for token, rank in (line.split() for line in raw.splitlines())
        }
        return tiktoken.Encoding(
            name="cl100k_base",
            pat_str=PATTERN,
            mergeable_ranks=ranks,
            special_tokens={
                "<|endoftext|>": 100257,
                "<|fim_prefix|>": 100258,
                "<|fim_middle|>": 100259,
                "<|fim_suffix|>": 100260,
                "<|endofprompt|>": 100276,
            },
        )
    except (ImportError, OSError, ValueError) as exc:
        raise TokenizerUnavailable(
            "tokenizer_unavailable: install brief extra and provision the attested local table explicitly"
        ) from exc


def counter():
    encoding = _load(os.environ.get("MEMORY_V4_CL100K_PATH"))
    return lambda text: len(encoding.encode_ordinary(text))


def status():
    try:
        counter()
        return {
            "ready": True,
            "encoding": "cl100k_base",
            "table_sha256": TABLE_SHA256,
            "implementation_version": importlib.metadata.version("tiktoken"),
        }
    except TokenizerUnavailable:
        return {
            "ready": False,
            "encoding": "cl100k_base",
            "table_sha256": TABLE_SHA256,
            "reason": "tokenizer_unavailable",
        }
