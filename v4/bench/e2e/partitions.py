"""Development only: all oracle questions were previously observed."""
from collections import defaultdict
import hashlib
import math

from .stats import cluster_ids

SEED = 20261009


def split(golds, dataset_sha):
    clusters = cluster_ids(golds)
    groups = defaultdict(list)
    for g in golds:
        groups[clusters[g.id]].append(g)
    strata = defaultdict(list)
    for group in groups.values():
        key = tuple(sorted({g.category for g in group}))
        ids = sorted(g.id for g in group)
        digest = hashlib.sha256(f"{SEED}:{','.join(ids)}".encode()).hexdigest()
        strata[key].append((digest, ids))
    dev = []
    for groups_in_stratum in strata.values():
        ordered = sorted(groups_in_stratum)
        dev.extend(qid for _, ids in ordered[:math.ceil(.2 * len(ordered))] for qid in ids)
    return {"version": "observed-oracle-dev-v1", "seed": SEED, "dataset_sha256": dataset_sha,
            "dev_ids": sorted(dev),
            "quarantine_observed_ids": sorted(g.id for g in golds if g.id not in set(dev)),
            "test_ids": [], "warning": "ALL previously observed; no untouched test available"}


def validate(selection, golds, dataset_sha):
    if selection != split(golds, dataset_sha):
        raise ValueError("Selection must be the frozen development split; test/quarantine forbidden")
    return set(selection["dev_ids"])
