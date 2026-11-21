"""``memory-os-constraints {lexical|semantic}``: path of the constraints file of one install profile.

Standard library only, and it never imports ``memory_os_v4``: it must work right after
``pip install --no-deps memory-os-ai``, before any dependency exists, so that the second command
can install under the qualified constraints (docs/SETUP_4_1_0.md §9):

    pip install --no-deps "memory-os-ai==X"
    pip install -c "$(memory-os-constraints lexical)" "memory-os-ai[ops,brief]==X"
"""
from __future__ import annotations

import sys
from pathlib import Path

FILES = {
    "lexical": "qualified_runtime_constraints_lexical_v1.txt",
    "semantic": "qualified_runtime_constraints_v1.txt",
}
USAGE = "usage: memory-os-constraints {lexical|semantic}"


def path(profile: str) -> str:
    """Absolute path of the packaged constraints file of ``profile`` (``lexical`` or ``semantic``)."""
    if profile not in FILES:
        raise ValueError("Unknown profile: choose lexical or semantic")
    return str(Path(__file__).resolve().parent / "memory_os_v4" / "resources" / FILES[profile])


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1 or arguments[0] not in FILES:
        print(USAGE, file=sys.stderr)
        return 2
    found = path(arguments[0])
    if not Path(found).is_file():
        print("constraints file missing from this installation: " + FILES[arguments[0]], file=sys.stderr)
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
