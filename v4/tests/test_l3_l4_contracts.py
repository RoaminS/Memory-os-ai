"""Contract-only validation: does not claim L3/L4 runtime implementation."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

SCHEMA = json.loads((Path(__file__).parents[1] / "docs/contracts/l3-l4-v1.schema.json").read_text())
COMMON = {"request_id": "synthetic-id", "scope": {"kind": "project", "target": "A"},
          "operation": "memory_chat_save", "payload_sha256": "a" * 64, "store_instance_id": "store-A"}


def validate(definition, payload):
    validator = Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{definition}"})
    validator.validate(payload)


def test_valid_schema_and_status_input():
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["x-implementation-status"] == "contract-only"
    validate("request_status_input", COMMON)


@pytest.mark.parametrize("state,reason", [
    ("completed", "receipt_complete"), ("pending", "outcome_uncertain"),
    ("not_found", "absence_is_not_nonexecution"), ("unknown", "different_store"),
    ("conflict", "fingerprint_conflict"),
])
def test_states_never_grant_replay(state, reason):
    payload = {**COMMON, "state": state, "reason": reason, "backend": "jsonl", "replay_permitted": False}
    if state == "completed":
        payload["result"] = {"added": ["item"]}
    validate("request_status_output", payload)
    with pytest.raises(ValidationError, match="False was expected"):
        validate("request_status_output", {**payload, "replay_permitted": True})


def test_no_false_certainty_or_result_from_absence():
    payload = {**COMMON, "state": "not_found", "reason": "absence_is_not_nonexecution",
               "backend": "sqlite_s1", "replay_permitted": False}
    with pytest.raises(ValidationError):
        validate("request_status_output", {**payload, "result": {}})
    with pytest.raises(ValidationError):
        validate("request_status_output", {**payload, "state": "not_sent"})
    with pytest.raises(ValidationError):
        validate("request_status_input", {**COMMON, "token": "forbidden"})
    with pytest.raises(ValidationError):
        validate("request_status_input", {**COMMON, "scope": {"kind": "global", "target": "A"}})


def test_client_and_relay_contracts():
    entry = {**COMMON, "schema_version": 1, "client_instance_id": "client-A", "state": "sending", "revision": 2}
    validate("client_ledger_record", entry)
    with pytest.raises(ValidationError):
        validate("client_ledger_record", {**entry, "state": "completed"})
    validate("client_ledger_record", {**entry, "state": "completed", "result_sha256": "b" * 64})
    validate("relay_diagnostic", {"schema_version": 1, "relay_package_version": "4.0.4-candidate",
        "announced_server_version": "4.0.3", "observed_upstream_version": "4.0.4-candidate",
        "connection_state": "identity_changed", "inflight_outcome": "uncertain",
        "retry_mutation_automatically": False})
