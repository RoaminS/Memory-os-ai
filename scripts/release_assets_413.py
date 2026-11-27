#!/usr/bin/env python3
"""4.1.3 freeze (Q1 of decision D13): the asset set of a GitHub Release, checked, and its ``SHA256SUMS``.

A Release has six assets: five files that ``SHA256SUMS`` covers, and ``SHA256SUMS`` itself. Standard library only;
it reads the five files and writes ONE new file, ``<directory>/SHA256SUMS``. Run at publication (never by the test
suite on real assets), once the five files are in one directory:

    python3 -I -B scripts/release_assets_413.py <directory>            # checks, then writes SHA256SUMS
    python3 -I -B scripts/release_assets_413.py <directory> --check    # checks, prints SHA256SUMS, writes nothing

The five files of version V (read from the wheel and sdist names, which must agree):

- ``memory_os_ai-V-py3-none-any.whl`` and ``memory_os_ai-V.tar.gz``: the qualified wheel and sdist;
- ``memory_os_bootstrap.py``: the bootstrap whose release pin is filled for THAT wheel;
- ``memory-os-ai-V.p9-manifest.json``: the P9 manifest of that wheel;
- ``install.sh``: the one-command install, whose single ``RELEASE_TAG`` line must be ``RELEASE_TAG="vV"``.

Two pieces of code are told apart. The bootstrap ASSET is untrusted input: it is read as text, its pin block is cut
out as text, and it is never imported or run. The bootstrap TEMPLATE of this repository (``memory_os_bootstrap.py``
next to this script, from the same commit) is trusted code: its SOURCE bytes are compiled and executed (no import
loader, so no ``__pycache__`` is read), and its own readers judge the asset, with no parallel reimplementation:

- ``template.load_pin(<pin text of the asset>)`` must be ``ok``, in state ``release``, of version V, and pin exactly
  the wheel present (filename, SHA-256, size);
- the pin must name exactly the retained pairs of the template: each cell of ``CELLS`` in ``lexical`` and each cell
  of ``SEMANTIC_CELLS`` in ``semantic`` (4.1.3: five lexical, one semantic); a missing or extra pair is refused, since
  an install on an omitted cell would stop on ``cell_lock_missing``;
- for EACH (cell, profile) of that pin, ``template.proof_state(<P9 manifest>, pin=..., cell_id=..., profile=...)``
  must give ``PASS`` with ``manifest_matches_artifact_cell_and_lock`` (the 4.1.2 publication rule); any other state or
  reason is refused with the cell, the profile and the reason.

``SHA256SUMS`` is in ``sha256sum`` format (``<64 hex>  <name>``), one line per file above, sorted by name, and never
a line for itself; ``install.sh`` requires exactly one line for ``memory_os_bootstrap.py``. Anything else in the
directory (a missing, duplicated or extra file, a link, a directory, an existing ``SHA256SUMS``) is refused and
nothing is written. The proof complement of a Release has its own checksum file and is not handled here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import types

SUMS = "SHA256SUMS"
BOOTSTRAP = "memory_os_bootstrap.py"
INSTALL = "install.sh"
TEMPLATE = Path(__file__).resolve().parent / BOOTSTRAP
TEMPLATE_MODULE = "memory_os_bootstrap_template_413"
PROVEN = ("PASS", "manifest_matches_artifact_cell_and_lock")
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


def load_template(path: Path = TEMPLATE):
    """The bootstrap TEMPLATE of this repository (trusted code of the same commit), loaded without bytecode.

    Never called on the bootstrap ASSET, which is only ever read as text (``pin_text``).
    The SOURCE bytes are compiled and executed into a fresh module: no import loader, so no ``__pycache__`` is ever
    read (``sys.dont_write_bytecode`` only stops writing one) nor written."""
    path = Path(path)
    source = path.read_bytes()
    module = types.ModuleType(TEMPLATE_MODULE)
    module.__file__ = str(path)
    sys.modules[TEMPLATE_MODULE] = module          # dataclasses and typing resolve the module by name
    exec(compile(source, str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def retained_pairs(template) -> list:
    """Every (cell, profile) a release must pin: each retained cell in lexical, the semantic cells in semantic."""
    return sorted({(cell_id, "lexical") for cell_id in template.CELLS}
                  | {(cell_id, "semantic") for cell_id in template.SEMANTIC_CELLS})


def pin_text(text: str, template) -> str:
    """The JSON text of the pin block of the bootstrap ASSET, cut out as text with the template's markers."""
    head = template.PIN_BEGIN + '\nRELEASE_PIN = r"""\n'
    tail = '\n"""\n' + template.PIN_END + "\n"
    if text.count(head) != 1 or text.count(tail) != 1 or text.index(head) > text.index(tail):
        raise AssetError(f"{BOOTSTRAP}: the release pin block is not found exactly once")
    return text[text.index(head) + len(head):text.index(tail)]


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


def check(directory, *, template=None) -> dict:
    """Check ``directory`` against the asset set; returns ``{"version", "tag", "assets", "proofs", "sums"}``.

    Never writes. ``template`` is the loaded bootstrap template (default: the one next to this script)."""
    directory = Path(directory)
    template = load_template() if template is None else template
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
    pin = template.load_pin(pin_text(found[BOOTSTRAP].read_text(encoding="utf-8"), template))
    if not pin["ok"]:
        raise AssetError(f"{BOOTSTRAP}: the bootstrap refuses its own pin: {pin['code']}: {pin['message']}")
    if (pin["state"], pin["version"]) != ("release", version):
        raise AssetError(f"{BOOTSTRAP}: the pin is not a release pin of {version} "
                         f"(state {pin['state']!r}, version {pin['version']!r})")
    pinned = pin["wheel"]
    if (pinned["filename"], pinned["sha256"], pinned["size"]) != (wheel.name, wheel_sha, wheel_size):
        raise AssetError(f"{BOOTSTRAP}: the pinned wheel is not {wheel.name} (SHA-256 {wheel_sha}, {wheel_size} bytes)")
    pairs = sorted((cell_id, profile) for cell_id, profiles in pin["cells"].items() for profile in profiles)
    wanted = retained_pairs(template)
    if pairs != wanted:
        missing = [f"{c}/{p}" for c, p in wanted if (c, p) not in pairs]
        extra = [f"{c}/{p}" for c, p in pairs if (c, p) not in wanted]
        raise AssetError(f"{BOOTSTRAP}: the release pin must name exactly the {len(wanted)} retained cell/profile "
                         f"pairs: missing {missing}, extra {extra}")

    proofs = []
    for cell_id, profile in pairs:
        proof = template.proof_state(str(found[manifests[0]]), pin=pin, cell_id=cell_id, profile=profile)
        if (proof["state"], proof["reason"]) != PROVEN:
            raise AssetError(f"{manifests[0]}: {cell_id}/{profile} is not proven for this wheel and lock "
                             f"(state {proof['state']!r}, reason {proof['reason']})")
        proofs.append({"cell_id": cell_id, "profile": profile, "state": proof["state"], "reason": proof["reason"]})

    digests = {name: sha256(found[name]) for name in names}
    return {"version": version, "tag": tag, "assets": names, "proofs": proofs,
            "sums": "".join(f"{digests[name]}  {name}\n" for name in names)}


def write(directory, *, template=None) -> dict:
    """``check`` then create ``SHA256SUMS`` (exclusive creation: an existing file is never replaced)."""
    result = check(directory, template=template)
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
    proven = ", ".join(f"{p['cell_id']}/{p['profile']}" for p in result["proofs"])
    print(f"memory-os-ai {result['version']} ({result['tag']}): {len(result['assets'])} files covered "
          f"({len(result['assets']) + 1} assets with {SUMS}), PASS for {proven}; {SUMS} {action}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
