"""``memory-os-constraints {lexical|semantic} [--cell CELL]``: path of the cell constraints file.

Standard library only, and it never imports ``memory_os_v4``: it must work right after
``pip install --no-deps memory-os-ai``, before any dependency exists, so that the second command
can install under the qualified constraints of the detected platform cell (docs/SETUP_4_1_0.md §9,
4.1.1 contract §6 / lot L5):

    pip install --no-deps "memory-os-ai==X"
    pip install -c "$(memory-os-constraints lexical)" "memory-os-ai[ops,brief]==X"
"""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import platform
import re
import stat
import sys

# Retained reference filenames (kept for backwards compatibility).
FILES = {
    "lexical": "qualified_runtime_constraints_lexical_v1.txt",
    "semantic": "qualified_runtime_constraints_v1.txt",
}
# Exact per-cell constraints files, flat under memory_os_v4/resources/.
CELL_FILES = {
    ("semantic", "macos-arm64-py311"): "qualified_runtime_constraints_v1.txt",
    ("lexical", "macos-arm64-py311"): "qualified_runtime_constraints_lexical_v1.txt",
    ("lexical", "macos-arm64-py312"): "qualified_runtime_constraints_lexical_macos-arm64-py312_v1.txt",
    ("lexical", "macos-arm64-py313"): "qualified_runtime_constraints_lexical_macos-arm64-py313_v1.txt",
    ("lexical", "ubuntu2404-arm64-py311"): "qualified_runtime_constraints_lexical_ubuntu2404-arm64-py311_v1.txt",
    ("lexical", "ubuntu2404-x86_64-py311"): "qualified_runtime_constraints_lexical_ubuntu2404-x86_64-py311_v1.txt",
}
# Anchored SHA-256 digests of the packaged constraints files (generated and verified by
# scripts/generate_cell_locks_411.py --check; stdlib-only, never imports memory_os_v4).
# BEGIN GENERATED CELL_CONSTRAINTS_DIGESTS
CELL_CONSTRAINTS_DIGESTS: dict[tuple[str, str], str] = {
    ('semantic', 'macos-arm64-py311'): 'eeffd1c60060a2c4a2279f48e07818095914f6de34f5638c4bbeaa524055220d',
    ('lexical', 'macos-arm64-py311'): '17967671d2f143083c56d241fb6ad1c0ce5cb8a8ecd5b92ad706257e21860321',
    ('lexical', 'macos-arm64-py312'): '769ba2f9a4020549a976a06406e2a387772c50d33531de966a27110eea8db5cc',
    ('lexical', 'macos-arm64-py313'): 'f7068c427ab1de814fa891afa8875e092d82a842e6532b28f532ee257fd9d8eb',
    ('lexical', 'ubuntu2404-arm64-py311'): 'ce22752cb9ee6ac527c12aa87264db8f0b8c2437ebcae9983f7e20f21efe194d',
    ('lexical', 'ubuntu2404-x86_64-py311'): '9c4b36b32fde100e5419e3cda7632f0b59ea9ca223dbc6b4c099e63ee68aa0b8',
}
# END GENERATED CELL_CONSTRAINTS_DIGESTS
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# Same exact facts as ops/cell.py, the architecture included (stdlib copy, never imported from it).
_ARCH_MACHINES = {"arm64": frozenset({"arm64", "aarch64"}), "x86_64": frozenset({"x86_64", "amd64"})}
_CELL_FACTS = {
    "macos-arm64-py311": {"os": "darwin", "arch": "arm64", "python": (3, 11)},
    "macos-arm64-py312": {"os": "darwin", "arch": "arm64", "python": (3, 12)},
    "macos-arm64-py313": {"os": "darwin", "arch": "arm64", "python": (3, 13)},
    "ubuntu2404-arm64-py311": {"os": "linux", "arch": "arm64", "distribution_id": "ubuntu", "version_id": "24.04",
                               "python": (3, 11)},
    "ubuntu2404-x86_64-py311": {"os": "linux", "arch": "x86_64", "distribution_id": "ubuntu", "version_id": "24.04",
                                "python": (3, 11)},
}
_OS_RELEASE_PATHS = (Path("/etc/os-release"), Path("/usr/lib/os-release"))
_KERNEL_RELEASE_PATH = Path("/proc/sys/kernel/osrelease")
_READ = object()

USAGE = "usage: memory-os-constraints {lexical|semantic} [--cell CELL_ID]"


def _parse_os_release(text: str | None) -> tuple[str | None, str | None, bool]:
    """Return ``(id, version_id, has_conflict)`` verbatim (same exact rule as ops/cell.py)."""
    values: dict[str, str] = {}
    conflicts: set[str] = set()
    for line in (text or "").splitlines():
        match = re.fullmatch(r"\s*([A-Z0-9_]+)=(.*)", line)
        if not match:
            continue
        value = match[2].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if values.setdefault(match[1], value) != value:
            conflicts.add(match[1])
    conflict = bool(conflicts & {"ID", "VERSION_ID"})
    return (None if "ID" in conflicts else (values.get("ID") or None),
            None if "VERSION_ID" in conflicts else (values.get("VERSION_ID") or None),
            conflict)


def _read_first(paths: tuple[Path, ...]) -> str | None:
    for item in paths:
        try:
            return item.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def detect_cell_id(
    *,
    sys_platform: str | None = None,
    machine: str | None = None,
    python_version: tuple[int, ...] | None = None,
    implementation: str | None = None,
    os_release=_READ,
    kernel_release=_READ,
    environ=None,
) -> str | None:
    """Stdlib-only retained-cell detection (matches ``memory_os_v4.ops.cell.detect``)."""
    raw_platform = sys.platform if sys_platform is None else sys_platform
    raw_machine = platform.machine() if machine is None else machine
    version = tuple(sys.version_info[:2] if python_version is None else python_version[:2])
    impl = (platform.python_implementation() if implementation is None else implementation).lower()
    env = os.environ if environ is None else environ
    lowered = (raw_machine or "").lower()
    arch = next((name for name, spellings in _ARCH_MACHINES.items() if lowered in spellings), None)
    if impl != "cpython" or arch is None:
        return None
    system = "darwin" if raw_platform == "darwin" else "linux" if raw_platform.startswith("linux") else "other"
    facts: dict[str, object] = {"os": system, "arch": arch, "python": version}
    if system == "linux":
        dist_id, ver_id, conflict = _parse_os_release(
            _read_first(_OS_RELEASE_PATHS) if os_release is _READ else os_release
        )
        if conflict:
            return None
        kernel = (_read_first((_KERNEL_RELEASE_PATH,)) or platform.release()) if kernel_release is _READ else kernel_release
        if "microsoft" in (kernel or "").lower() or bool(env.get("WSL_DISTRO_NAME")) or bool(env.get("WSL_INTEROP")):
            return None
        facts.update(distribution_id=dist_id, version_id=ver_id)
    return next((name for name, expected in _CELL_FACTS.items() if expected == facts), None)


def path(profile: str, *, cell_id: str | None = None) -> str:
    """Absolute path of the packaged constraints file of ``(profile, cell_id)``."""
    if profile not in FILES:
        raise ValueError("Unknown profile: choose lexical or semantic")
    resolved = detect_cell_id() if cell_id is None else cell_id
    if resolved is None:
        raise ValueError("No retained platform cell detected for this interpreter")
    filename = CELL_FILES.get((profile, resolved))
    if filename is None:
        raise ValueError(f"No qualified constraints for profile {profile} on cell {resolved}")
    return str(Path(__file__).resolve().parent / "memory_os_v4" / "resources" / filename)


def _read_plain_regular_file(path: Path) -> bytes | None:
    """Read a regular single-link file with no symlink component anywhere along the path."""
    if not path.is_absolute() or ".." in path.parts:
        return None
    for part in (path, *path.parents):
        try:
            if part.is_symlink():
                return None
        except OSError:
            return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        fh = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    try:
        with fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                return None
            return fh.read()
    except OSError:
        return None


def _is_plain_regular_file(path: Path) -> bool:
    """Regular single-link file with no symlink component anywhere along the path."""
    return _read_plain_regular_file(path) is not None


def _verify_constraints_file(path: Path, profile: str, cell_id: str) -> bool:
    """Verify the served constraints file is plain/single-link and matches its anchored SHA-256."""
    expected = CELL_CONSTRAINTS_DIGESTS.get((profile, cell_id))
    if not isinstance(expected, str) or _HEX64.fullmatch(expected) is None:
        return False
    data = _read_plain_regular_file(path)
    if data is None:
        return False
    actual = hashlib.sha256(data).hexdigest()
    return hmac.compare_digest(actual, expected)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    profile = None
    cell_id = None
    if len(arguments) == 1 and arguments[0] in FILES:
        profile = arguments[0]
    elif len(arguments) == 2 and arguments[0] in FILES and arguments[1].startswith("--cell=") and arguments[1][7:]:
        profile, cell_id = arguments[0], arguments[1][7:]
    elif len(arguments) == 2 and arguments[0].startswith("--cell=") and arguments[0][7:] and arguments[1] in FILES:
        cell_id, profile = arguments[0][7:], arguments[1]
    elif len(arguments) == 3 and arguments[0] in FILES and arguments[1] == "--cell" and arguments[2]:
        profile, cell_id = arguments[0], arguments[2]
    elif len(arguments) == 3 and arguments[0] == "--cell" and arguments[1] and arguments[2] in FILES:
        cell_id, profile = arguments[1], arguments[2]
    else:
        print(USAGE, file=sys.stderr)
        return 2
    resolved = detect_cell_id() if cell_id is None else cell_id
    if resolved is None:
        print("No retained platform cell detected for this interpreter", file=sys.stderr)
        return 1
    try:
        found = path(profile, cell_id=resolved)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    found_path = Path(found)
    if not _verify_constraints_file(found_path, profile, resolved):
        print("constraints file missing, unsafe or digest mismatch in this installation: " + found_path.name,
              file=sys.stderr)
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
