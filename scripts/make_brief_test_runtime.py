#!/usr/bin/env python3
"""Make a disposable local-only Python 3.11 test overlay; never installs/downloads anything."""

import argparse
from pathlib import Path
import shutil
import sys
import venv


def create_runtime(output, base, tiktoken_site):
    sources = [tiktoken_site / name for name in
               ("tiktoken", "tiktoken_ext", "tiktoken-0.12.0.dist-info")]
    if not base.is_dir() or not all(p.is_dir() for p in sources):
        raise ValueError("preinstalled qualified base or complete tiktoken 0.12.0 missing; no download fallback")
    venv.EnvBuilder(with_pip=False).create(output)
    site = next((output / "lib").glob("python*/site-packages"))
    (site / "qualified-base.pth").write_text(str(base) + "\n")
    for source in sources:
        (site / source.name).symlink_to(source.resolve(), target_is_directory=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--base-site",
        type=Path,
        default=Path("/Users/romain/Memory-os-ai/.venv/lib/python3.11/site-packages"),
    )
    parser.add_argument(
        "--tiktoken-site",
        type=Path,
        default=Path("/opt/homebrew/lib/python3.11/site-packages"),
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / ".local-qualification"
    output = args.output.resolve()
    if (
        sys.version_info[:2] != (3, 11)
        or not output.is_relative_to(root)
        or output.exists()
    ):
        parser.error(
            "Python 3.11 and fresh output in clone .local-qualification required"
        )
    if shutil.disk_usage(root).free < 5_000_000_000:
        parser.error("disk guard < 5 GB")
    base = args.base_site.resolve()
    try:
        create_runtime(output, base, args.tiktoken_site.resolve())
    except ValueError as exc:
        parser.error(str(exc))
    print(output / "bin/python")


if __name__ == "__main__":
    main()
