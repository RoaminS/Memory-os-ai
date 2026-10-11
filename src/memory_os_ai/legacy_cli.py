"""Compatibility entrypoint: explain the optional document engine before loading it."""
from __future__ import annotations

import importlib.util
import sys

DEPENDENCIES = ("fitz", "pytesseract", "pptx", "docx", "PIL", "requests")


def require_v3() -> None:
    missing = [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]
    if missing:
        from . import __version__
        raise ImportError(f"Legacy v3 dependencies missing: {', '.join(missing)}. "
                          f"Install 'memory-os-ai[legacy-v3]=={__version__}' in this environment. "
                          "For shared v4 memory use memory-os-ai or memory-os-relay.")


def main() -> int:
    if any(value in {"--help", "-h"} for value in sys.argv[1:]):
        print("memory-os-ai-v3: optional legacy document server (stdio; --sse / --http).\n"
              "Install memory-os-ai[legacy-v3] for legacy ingestion. For shared v4 memory use "
              "memory-os-ai; clients connect with memory-os-relay.")
        return 0
    try:
        require_v3()
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    from .server import run
    return run()
