#!/usr/bin/env python3
"""Explicit bounded acquisition only, after operator GO. Never imported by runtime."""

import argparse
import hashlib
from pathlib import Path
import shutil
import urllib.request

URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
SHA = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CAP = 2_000_000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--download-approved", action="store_true")
    args = parser.parse_args()
    if not args.download_approved:
        parser.error("explicit operator GO required; no acquisition performed")
    target = args.output.resolve()
    root = Path(__file__).resolve().parents[1] / ".local-qualification"
    if not target.is_relative_to(root):
        parser.error("target must be inside clone .local-qualification")
    if shutil.disk_usage(root).free < 5_000_000_000:
        parser.error("disk guard < 5 GB")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Exclusive .part stays on failure. No redirect/proxy to an unapproved source.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    if target.exists():
        parser.error("target already exists")
    partial = target.with_suffix(target.suffix + ".part")
    with partial.open("xb") as out, opener.open(URL, timeout=30) as source:
        length = source.headers.get("Content-Length")
        if length is None or not 0 < int(length) <= CAP:
            raise ValueError("missing/oversized declared content length")
        data = source.read(CAP + 1)
        out.write(data)
        if len(data) > CAP or hashlib.sha256(data).hexdigest() != SHA:
            raise ValueError("table size/hash mismatch; failed evidence preserved")
    partial.rename(target)
    print(SHA + "  " + str(target))


if __name__ == "__main__":
    main()
