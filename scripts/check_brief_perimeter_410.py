#!/usr/bin/env python3
"""G13 (QUALIFY_4_1_0 §4): brief perimeter of P-A, computed exclusively from Git objects.

Union of the static relative-import closures of ``brief*.py`` at three commits: the commit of
the P-A proof, the intermediate reference and the final candidate, files that left the
closure included. SHA-256 of every file comes from the Git blob. The callers ``server.py``
and ``memory.py``, the dependencies and the resources are inventoried separately.

This tool reads no sealed input, runs no brief, replays nothing and NEVER concludes a
transfer: an identical static closure is a conservative filter, not a proof of equivalence.
The report is deterministic (no clock), so its SHA-256 can be quoted in a decision.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import posixpath
import re
import subprocess

PACKAGE = "v4/src/memory_os_v4"
CALLERS = (PACKAGE + "/server.py", PACKAGE + "/memory.py")
DEPENDENCY_FILES = ("pyproject.toml", "v4/pyproject.toml")
RESOURCE_PREFIX = PACKAGE + "/resources/"
P_A_PROOF = "ceedb5cfdfd0d61929276dbcd1417ea077044412"
INTERMEDIATE = "9e41ea40141f8ed4d7ed12a92a1900d2409272c3"
ROLES = ("p_a_proof", "intermediate", "candidate")
FORBIDDEN_NAMES = ("brief_v5_val", "brient_v5_val")  # sealed folders: named only to be refused


class Tree:
    """Read-only view of one commit, through ``git ls-tree`` / ``git cat-file`` only."""

    def __init__(self, root, commit, run=subprocess.check_output):
        self.root, self.commit, self.run = root, commit, run
        listing = run(["git", "ls-tree", "-r", "--name-only", commit, "--", PACKAGE, *DEPENDENCY_FILES],
                      cwd=root, text=True)
        self.paths = set(listing.splitlines())
        self._blobs = {}

    def blob(self, path):
        if path not in self._blobs:
            self._blobs[path] = self.run(["git", "cat-file", "blob", self.commit + ":" + path], cwd=self.root)
        return self._blobs[path]

    def sha256(self, path):
        return hashlib.sha256(self.blob(path)).hexdigest() if path in self.paths else None


def module_file(tree, module_path):
    """``pkg/a/b`` -> the file that defines it at this commit, if any."""
    for candidate in (module_path + ".py", module_path + "/__init__.py"):
        if candidate in tree.paths:
            return candidate
    return None


def relative_imports(tree, path):
    """Files reached by the static relative imports of ``path`` (any nesting depth in the code)."""
    found = set()
    package = posixpath.dirname(path)
    for node in ast.walk(ast.parse(tree.blob(path))):
        if not isinstance(node, ast.ImportFrom) or node.level < 1:
            continue
        base = package
        for _ in range(node.level - 1):
            base = posixpath.dirname(base)
        if not (base == PACKAGE or base.startswith(PACKAGE + "/")):
            continue  # leaves the package: not part of the engine closure
        target = base + ("/" + node.module.replace(".", "/") if node.module else "")
        # ``from . import x`` names x only: the package __init__ is not added (rule of the
        # contract's own count, 14 / 22 / 23 files at ceedb5c / 9e41ea4 / 453a893).
        defined = module_file(tree, target) if node.module else None
        if defined:
            found.add(defined)
        for alias in node.names:  # ``from . import x`` / ``from .pkg import submodule``
            sub = module_file(tree, target + "/" + alias.name)
            if sub:
                found.add(sub)
    return found


def closure(tree):
    roots = sorted(p for p in tree.paths if re.fullmatch(re.escape(PACKAGE) + r"/brief[^/]*\.py", p))
    if not roots:
        raise ValueError("No brief*.py at " + tree.commit)
    seen, frontier = set(roots), list(roots)
    while frontier:
        for reached in sorted(relative_imports(tree, frontier.pop())):
            if reached not in seen:
                seen.add(reached)
                frontier.append(reached)
    return {"roots": roots, "files": {path: tree.sha256(path) for path in sorted(seen)}}


def compare(older, newer):
    a, b = older["files"], newer["files"]
    common = sorted(set(a) & set(b))
    return {"joined": sorted(set(b) - set(a)), "left": sorted(set(a) - set(b)),
            "changed_among_common": [p for p in common if a[p] != b[p]],
            "identical_among_common": [p for p in common if a[p] == b[p]]}


def report(root, candidate, run=subprocess.check_output):
    if re.fullmatch(r"[0-9a-f]{40}", candidate) is None:
        raise ValueError("Complete candidate SHA required")
    commits = dict(zip(ROLES, (P_A_PROOF, INTERMEDIATE, candidate)))
    trees = {role: Tree(root, commit, run) for role, commit in commits.items()}
    closures = {role: closure(tree) for role, tree in trees.items()}
    union = sorted({path for row in closures.values() for path in row["files"]})
    # Every file of the union at every commit, including files outside (or gone from) a closure.
    union_table = {path: {role: {"sha256": trees[role].sha256(path), "in_closure": path in closures[role]["files"]}
                          for role in ROLES} for path in union}

    def across(path):
        hashes = {role: trees[role].sha256(path) for role in ROLES}
        return {"sha256": hashes,
                "changed_p_a_proof_to_intermediate": hashes["p_a_proof"] != hashes["intermediate"],
                "changed_intermediate_to_candidate": hashes["intermediate"] != hashes["candidate"]}

    resources = sorted({p for tree in trees.values() for p in tree.paths if p.startswith(RESOURCE_PREFIX)})
    return {
        "format": "qualify-410-brief-perimeter-v1",
        "git_only": True,
        "sealed_inputs_read": False,
        "brief_executed": False,
        "commits": commits,
        "closure_rule": "static relative imports (ast.ImportFrom, level >= 1) from " + PACKAGE + "/brief*.py",
        "closures": {role: {**row, "count": len(row["files"])} for role, row in closures.items()},
        "union": union_table,
        "p_a_proof_to_intermediate": compare(closures["p_a_proof"], closures["intermediate"]),
        "intermediate_to_candidate": compare(closures["intermediate"], closures["candidate"]),
        "callers": {path: across(path) for path in CALLERS},
        "dependencies": {path: across(path) for path in DEPENDENCY_FILES},
        "resources": {path: across(path) for path in resources},
        "limits": [
            "the 40/40 result stays attributed to " + P_A_PROOF[:7] + "; nothing is replayed",
            "an identical static closure is a conservative filter, not a demonstration of equivalence",
            "the callers server.py and memory.py are outside the closure and are inventoried separately",
            "dynamic imports, third-party dependencies and runtime configuration are not covered",
        ],
        "automatic_transfer": False,
        "conclusion": "decision_of_romain_required",
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="complete SHA of the final commit")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    resolved = str(args.output.resolve())
    if any(name in resolved or name in str(args.output) for name in FORBIDDEN_NAMES):
        parser.error("REFUS: sealed validation folder (never read, listed or written)")
    if args.output.exists():
        parser.error("Fresh report required; earlier evidence is kept")
    root = Path(__file__).resolve().parents[1]
    result = report(root, args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"closures": {role: row["count"] for role, row in result["closures"].items()},
                      "automatic_transfer": False, "conclusion": result["conclusion"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
