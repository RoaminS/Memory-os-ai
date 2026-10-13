"""Write-only guards for caller-declared identities; historical data stays opaque."""
from __future__ import annotations

import unicodedata

GENERIC_SINGLE_PREDICATES = frozenset({"other", "unknown", "default"})


def validate_new_identity(identity: dict) -> None:
    """Reject generic single properties without rewriting the caller's identity.

    This control is never applied on Store.load/from_dict: old assertions remain
    readable. Identity predicates are extensible, not the facet vocabulary.
    Shape/type validation belongs to the input model/normalizer.
    """
    if identity.get("cardinality") != "single":
        return
    predicate = unicodedata.normalize("NFKC", identity["predicate"]).casefold().strip()
    if predicate in GENERIC_SINGLE_PREDICATES:
        raise ValueError("generic_single_identity: single-valued identities require a specific predicate; "
                         "other, unknown and default are not allowed")
