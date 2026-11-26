"""Exact, local-only cl100k_base. No registry loader/network/cache download path.

4.1.3 (decision D3): the attested table ships in the package (``resources/cl100k_base.tiktoken``, MIT, from OpenAI
tiktoken; see THIRD_PARTY_NOTICES.md), so the service AND the relay count tokens with no configuration.
``MEMORY_V4_CL100K_PATH`` stays an explicit override: when it is set, only that file is used (never a silent
fallback to the embedded copy). Either way the SHA-256 is verified at load.

Decision D4: ``tiktoken>=0.12.0,<1`` is accepted (exact versions stay pinned in the qualified locks); a self-test
encodes known vectors at load and any mismatch makes the tokenizer unavailable, never a wrong count.
"""

import base64
from functools import lru_cache
import hashlib
import importlib.metadata
import importlib.resources
import os
from pathlib import Path
import re

TABLE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
TABLE_BYTES = 1_681_126
EMBEDDED_TABLE = "cl100k_base.tiktoken"
# cl100k_base pattern from MIT-licensed tiktoken openai_public.py.
PATTERN = r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"
# Reference encodings computed with the qualified tiktoken 0.12.0 and the attested table (exact token ids).
SELF_TEST = (
    ("hello world", (15339, 1917)),
    ("Mémoire partagée entre IA : décision du 26 novembre 2026.",
     (44, 978, 97495, 961, 351, 8047, 9465, 44190, 551, 35881, 1854, 3930, 220, 1627, 83615, 220, 2366, 21, 13)),
    ("def f(x):\n    return x**2  # 🚀\n", (755, 282, 2120, 997, 262, 471, 865, 334, 17, 220, 674, 11410, 248, 222, 198)),
    ("  spaces\t\ttabs\r\nnewlines\n\n", (220, 12908, 197, 3324, 3518, 319, 943, 8128, 271)),
    ("東京と大阪 1234567 3.14159", (14276, 109, 47653, 19732, 27384, 33443, 103, 220, 4513, 10961, 22, 220, 18, 13, 9335, 2946)),
    ("I'll they've we're DON'T", (40, 3358, 814, 3077, 584, 2351, 45373, 17773)),
)


class TokenizerUnavailable(RuntimeError):
    pass


_PEP440 = re.compile(r"v?(?:(?P<epoch>\d+)!)?(?P<release>\d+(?:\.\d+)*)(?P<pre>[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?\d*)?"
                     r"(?P<post>(?:-\d+)|(?:[-_.]?(?:post|rev|r)[-_.]?\d*))?(?P<dev>[-_.]?dev[-_.]?\d*)?"
                     r"(?:\+[a-z0-9]+(?:[-_.][a-z0-9]+)*)?", re.IGNORECASE)


def _supported(version: str) -> bool:
    """PEP 440 ``>=0.12.0,<1`` (D4) without depending on ``packaging``.

    Post-releases and local versions are admitted, as the specifier admits them. Pre-releases and dev releases are
    excluded, as pip excludes them by default; ``<1`` also excludes every pre-release of 1. The self-test remains the
    guard against an implementation that counts differently."""
    match = _PEP440.fullmatch(version.strip())
    if match is None or match.group("pre") or match.group("dev"):
        return False
    if int(match.group("epoch") or 0) != 0:
        return False                              # a non-zero epoch sorts after every epoch-0 version: >= 1
    release = [int(piece) for piece in match.group("release").split(".")]
    release += [0] * (3 - len(release))
    return (0, 12, 0) <= tuple(release[:3]) and tuple(release) < (1,)


def embedded_table() -> Path | None:
    """The table shipped in the package, or None if the installation does not contain it."""
    try:
        path = Path(str(importlib.resources.files("memory_os_v4") / "resources" / EMBEDDED_TABLE))
    except (ModuleNotFoundError, TypeError, ValueError):
        return None
    return path if path.is_file() else None


def table_source() -> tuple[str, str | None]:
    """("explicit", value) when ``MEMORY_V4_CL100K_PATH`` is SET (an empty value is an explicit, invalid table:
    refused, never replaced by the embedded copy), else ("embedded", path or None)."""
    explicit = os.environ.get("MEMORY_V4_CL100K_PATH")
    if explicit is not None:
        return "explicit", explicit
    embedded = embedded_table()
    return "embedded", str(embedded) if embedded else None


@lru_cache(maxsize=4)
def _load(path):
    try:
        import tiktoken

        if not _supported(importlib.metadata.version("tiktoken")):
            raise ValueError("unsupported tokenizer implementation version")
        if not path:
            raise ValueError("no attested table: neither MEMORY_V4_CL100K_PATH nor the embedded copy")
        with Path(path).expanduser().open("rb") as source:
            raw = source.read(2_000_001)
        if hashlib.sha256(raw).hexdigest() != TABLE_SHA256:
            raise ValueError("unattested table")
        ranks = {
            base64.b64decode(token): int(rank) for token, rank in (line.split() for line in raw.splitlines())
        }
        encoding = tiktoken.Encoding(
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
        for text, expected in SELF_TEST:
            if tuple(encoding.encode_ordinary(text)) != expected:
                raise ValueError("tokenizer self-test failed: this implementation does not count like the reference")
        return encoding
    except (ImportError, OSError, ValueError) as exc:
        raise TokenizerUnavailable(
            "tokenizer_unavailable: install the brief extra; the attested table ships with the package "
            "(MEMORY_V4_CL100K_PATH, when set, must name the attested table)"
        ) from exc


def counter():
    encoding = _load(table_source()[1])
    return lambda text: len(encoding.encode_ordinary(text))


def status():
    source = table_source()[0]
    try:
        counter()
        return {
            "ready": True,
            "encoding": "cl100k_base",
            "table_sha256": TABLE_SHA256,
            "table_source": source,
            "implementation_version": importlib.metadata.version("tiktoken"),
        }
    except TokenizerUnavailable:
        return {
            "ready": False,
            "encoding": "cl100k_base",
            "table_sha256": TABLE_SHA256,
            "table_source": source,
            "reason": "tokenizer_unavailable",
        }
