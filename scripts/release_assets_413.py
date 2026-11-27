#!/usr/bin/env python3
"""4.1.3 freeze (Q1 of decision D13): the asset set of a GitHub Release, checked, and its ``SHA256SUMS``.

Standard library only; it reads the assets and writes ONE new file, ``<directory>/SHA256SUMS``. Run at publication
(never by the test suite on real assets), once the six other assets are in one directory:

    python3 -I -B scripts/release_assets_413.py <directory>            # checks, then writes SHA256SUMS
    python3 -I -B scripts/release_assets_413.py <directory> --check    # checks, prints SHA256SUMS, writes nothing

The assets of version V (read from the wheel and sdist names, which must agree):

- ``memory_os_ai-V-py3-none-any.whl`` and ``memory_os_ai-V.tar.gz``: the qualified wheel and sdist;
- ``memory_os_bootstrap.py``: the bootstrap whose release pin is filled for THAT wheel (state ``release``, version V,
  wheel filename, SHA-256 and size), read as text, never executed;
- ``memory-os-ai-V.p9-manifest.json``: the P9 manifest (schema 1, kind ``memory-os-ai-p9-manifest``) naming the
  wheel's SHA-256;
- ``install.sh``: the one-command install, whose single ``RELEASE_TAG`` line must be ``RELEASE_TAG="vV"``.

``SHA256SUMS`` is in ``sha256sum`` format (``<64 hex>  <name>``), one line per asset above, sorted by name, and never
a line for itself; ``install.sh`` requires exactly one line for ``memory_os_bootstrap.py``. Anything else in the
directory (a missing, duplicated or extra file, a link, a directory, an existing ``SHA256SUMS``) is refused and
nothing is written. The proof complement of a Release has its own checksum file and is not handled here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

PACKAGE = "memory-os-ai"
SUMS = "SHA256SUMS"
BOOTSTRAP = "memory_os_bootstrap.py"
INSTALL = "install.sh"
P9_KIND = "memory-os-ai-p9-manifest"
# Same markers as scripts/memory_os_bootstrap.py (PIN_BEGIN, PIN_END, render_pin_block); a test keeps them equal.
PIN_BEGIN = "# --- BEGIN RELEASE PIN (written by the release tooling: fill_pin; never edited by hand) ---"
PIN_END = "# --- END RELEASE PIN ---"
VERSION = r"[0-9]+(?:\.[0-9]+)*"
WHEEL = re.compile(rf"memory_os_ai-(?P<version>{VERSION})-py3-none-any\.whl")
SDIST = re.compile(rf"memory_os_ai-(?P<version>{VERSION})\.tar\.gz")
P9 = re.compile(rf"memory-os-ai-(?P<version>{VERSION})\.p9-manifest\.json")
TAG_LINE = re.compile(r'^  RELEASE_TAG="(?P<tag>[^"\n]*)"$', re.M)


class AssetError(ValueError):
    """A refusal: the directory is not exactly the release asset set; nothing was written."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_names(version: str) -> list[str]:
    """Every asset of ``version`` that ``SHA256SUMS`` covers, sorted as it lists them."""
    return sorted([f"memory_os_ai-{version}-py3-none-any.whl", f"memory_os_ai-{version}.tar.gz", BOOTSTRAP,
                   f"memory-os-ai-{version}.p9-manifest.json", INSTALL])


def release_tag(text: str) -> str:
    """The single ``RELEASE_TAG`` of ``install.sh``; refused unless there is exactly one assignment."""
    tags = TAG_LINE.findall(text)
    if len(tags) != 1 or text.count("RELEASE_TAG=") != 1:
        raise AssetError(f"{INSTALL}: exactly one RELEASE_TAG=\"v<version>\" line is required, found {len(tags)}")
    return tags[0]


def release_pin(text: str) -> dict:
    """The JSON pin block of the bootstrap, read as text (the bootstrap is never imported or run)."""
    head = PIN_BEGIN + '\nRELEASE_PIN = r"""\n'
    tail = '\n"""\n' + PIN_END + "\n"
    if text.count(head) != 1 or text.count(tail) != 1 or text.index(head) > text.index(tail):
        raise AssetError(f"{BOOTSTRAP}: the release pin block is not found exactly once")
    try:
        pin = json.loads(text[text.index(head) + len(head):text.index(tail)])
    except ValueError as exc:
        raise AssetError(f"{BOOTSTRAP}: the release pin is not JSON ({exc})") from None
    if not isinstance(pin, dict):
        raise AssetError(f"{BOOTSTRAP}: the release pin is not a JSON object")
    return pin


def inventory(directory: Path) -> dict[str, Path]:
    """Regular files of ``directory`` (no recursion); a link, a directory or any other entry is refused."""
    if directory.is_symlink() or not directory.is_dir():
        raise AssetError(f"{directory}: not a directory")
    found = {}
    for entry in sorted(os.listdir(directory)):
        mode = os.lstat(directory / entry).st_mode
        if not stat.S_ISREG(mode):
            raise AssetError(f"{entry}: not a regular file (links and directories are refused)")
        found[entry] = directory / entry
    return found


def check(directory) -> dict:
    """Check ``directory`` against the asset set; returns ``{"version", "tag", "assets", "sums"}``, never writes."""
    directory = Path(directory)
    found = inventory(directory)
    if SUMS in found:
        raise AssetError(f"{SUMS} is already present: it is never overwritten (remove it to recompute)")
    wheels = [name for name in found if WHEEL.fullmatch(name)]
    sdists = [name for name in found if SDIST.fullmatch(name)]
    manifests = [name for name in found if P9.fullmatch(name)]
    for kind, names in (("wheel", wheels), ("sdist", sdists), ("P9 manifest", manifests)):
        if len(names) != 1:
            raise AssetError(f"exactly one {kind} is required, found {len(names)}: {names}")
    version = WHEEL.fullmatch(wheels[0])["version"]
    if SDIST.fullmatch(sdists[0])["version"] != version or P9.fullmatch(manifests[0])["version"] != version:
        raise AssetError(f"the wheel, the sdist and the P9 manifest name different versions: "
                         f"{wheels[0]}, {sdists[0]}, {manifests[0]}")
    names = expected_names(version)
    missing, extra = sorted(set(names) - set(found)), sorted(set(found) - set(names))
    if missing or extra:
        raise AssetError(f"the asset set of {version} differs: missing {missing}, extra {extra}")

    tag = release_tag(found[INSTALL].read_text(encoding="utf-8"))
    if tag != f"v{version}":
        raise AssetError(f"{INSTALL}: RELEASE_TAG is {tag!r}, the release is v{version}")

    wheel = found[wheels[0]]
    wheel_sha, wheel_size = sha256(wheel), wheel.stat().st_size
    pin = release_pin(found[BOOTSTRAP].read_text(encoding="utf-8"))
    pinned = pin.get("wheel") if isinstance(pin.get("wheel"), dict) else {}
    if (pin.get("schema_version"), pin.get("state"), pin.get("package"), pin.get("version")) != \
            (1, "release", PACKAGE, version) or not pin.get("cells"):
        raise AssetError(f"{BOOTSTRAP}: the pin is not a release pin of {PACKAGE} {version} "
                         f"(state {pin.get('state')!r}, version {pin.get('version')!r})")
    if (pinned.get("filename"), pinned.get("sha256"), pinned.get("size")) != (wheel.name, wheel_sha, wheel_size):
        raise AssetError(f"{BOOTSTRAP}: the pinned wheel is not {wheel.name} (SHA-256 {wheel_sha}, {wheel_size} bytes)")

    try:
        manifest = json.loads(found[manifests[0]].read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AssetError(f"{manifests[0]}: not JSON ({exc})") from None
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("kind") != P9_KIND \
            or not isinstance(artifact, dict) or artifact.get("sha256") != wheel_sha:
        raise AssetError(f"{manifests[0]}: not a P9 manifest (schema 1, kind {P9_KIND}) naming the wheel {wheel_sha}")

    digests = {name: sha256(found[name]) for name in names}
    return {"version": version, "tag": tag, "assets": names,
            "sums": "".join(f"{digests[name]}  {name}\n" for name in names)}


def write(directory) -> dict:
    """``check`` then create ``SHA256SUMS`` (exclusive creation: an existing file is never replaced)."""
    result = check(directory)
    with open(Path(directory) / SUMS, "x", encoding="ascii", newline="\n") as handle:
        handle.write(result["sums"])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check the release assets of memory-os-ai and write SHA256SUMS.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true", help="check and print SHA256SUMS, write nothing")
    args = parser.parse_args(argv)
    try:
        result = check(args.directory) if args.check else write(args.directory)
    except (AssetError, OSError, UnicodeDecodeError) as exc:
        print(f"release assets refused: {exc}; nothing written", file=sys.stderr)
        return 1
    sys.stdout.write(result["sums"])
    action = "checked (nothing written)" if args.check else f"written: {args.directory / SUMS}"
    print(f"{PACKAGE} {result['version']} ({result['tag']}): {len(result['assets'])} assets, {SUMS} {action}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
