#!/usr/bin/env python3
"""Run outside sandbox, approved acquisition only; no model calls."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v4'))
from bench.e2e.mab_acquisition import main  # noqa: E402

if __name__ == '__main__':
    main()
