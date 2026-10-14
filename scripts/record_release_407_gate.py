"""Bind a new RC collection/scorer result; never scores gold, modifies scorer, or infers anything."""

import argparse
import json
from pathlib import Path
from release_407_checks import digest


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--collection", type=Path, required=True)
    p.add_argument("--scorer", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError("Fresh proof required")
    c = json.loads(a.collection.read_text())
    c["scorer_result"] = json.loads(a.scorer.read_text())
    c["original_collection_sha256"] = digest(a.collection)
    c["original_scorer_sha256"] = digest(a.scorer)
    a.output.write_text(json.dumps(c, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
