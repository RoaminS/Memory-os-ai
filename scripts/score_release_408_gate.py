"""Rebind the reviewed external P-B scorer to a frozen RCv2. Claude runs this."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from release_408_checks import verify_engine_version_only

ROOT = Path(__file__).resolve().parents[1]
SCORER_SHA = "489e6e93812580e990c64d3ab7195f658746b829198fcdf1101c6b1763907752"
REPLAY_SHA = "de3fc9506ff582f8e5b24635f427cb8a6e0836bde1fb154367cd73fbfb194906"


def main():
    p = argparse.ArgumentParser()
    for key in ("manifest", "scorer", "cases", "collection", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    a = p.parse_args()
    if a.output.exists():
        raise ValueError("Fresh scorer result required")
    m = json.loads(a.manifest.read_text())
    verify_engine_version_only(ROOT, m["source_commit"], m["version"])
    replay = a.scorer.parent / "replay/replay_pb_v1.py"
    for f, expected in ((a.scorer, SCORER_SHA), (replay, REPLAY_SHA)):
        if hashlib.sha256(f.read_bytes()).hexdigest() != expected:
            raise ValueError("Reviewed scorer/helper changed")
    from memory_os_v4.ops.provision import guard

    guard(a.output.parent)
    # Rebinding changes no expectation, threshold or scoring rule. The external
    # originals stay unchanged; replay temporaries are owned by this output.
    with tempfile.TemporaryDirectory(
        prefix="rc-pb-scorer-", dir=a.output.parent
    ) as tmp:
        private = Path(tmp)
        original = private / a.scorer.name
        shutil.copyfile(a.scorer, original)
        (private / "replay").mkdir()
        shutil.copyfile(replay, private / "replay/replay_pb_v1.py")
        code = """import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('reviewed_rc_scorer',sys.argv[1])
mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)
mod.CANDIDATE=sys.argv[2]; mod.CLONE=Path(sys.argv[3])
r=mod.main(sys.argv[4],sys.argv[5],sys.argv[6])
raise SystemExit(0 if r['verdict'] else 1)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                code,
                str(original),
                m["source_commit"],
                str(ROOT),
                str(a.cases.resolve()),
                str(a.collection.resolve()),
                str(a.output.resolve()),
            ],
            check=False,
        )
        guard(a.output.parent)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
