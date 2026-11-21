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

    def spy(path, text):
        if Path(path).name == "config.toml":
            import tomllib
            tomllib.loads(text)  # must already be valid when it reaches the write
            written.append(text)
        return real(path, text)
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


# ---- an existing relay entry: ours only when it designates this installation ----

def relay_entry(**env):
    return {"command": "/old/venv/bin/python", "args": ["-m", "memory_os_v4.ops.relay"], "env": env}


OUR_SERVICE = {"MEMORY_V4_URL": ENV["MEMORY_V4_URL"], "MEMORY_V4_TOKEN_FILE": ENV["MEMORY_V4_TOKEN_FILE"]}
FOREIGN_RELAYS = {
    "no-environment": {"command": "/old/venv/bin/python", "args": ["-m", "memory_os_v4.ops.relay"]},
    "other-token-file": relay_entry(**{**OUR_SERVICE, "MEMORY_V4_TOKEN_FILE": "/other/http.token"}),
    "other-url": relay_entry(**{**OUR_SERVICE, "MEMORY_V4_URL": "http://127.0.0.1:9999/mcp/"}),
    "token-file-only": relay_entry(MEMORY_V4_TOKEN_FILE=ENV["MEMORY_V4_TOKEN_FILE"]),
    "relay-script": {"command": "memory-os-relay"},
}


def test_installation_identity_is_the_service_a_relay_entry_designates():
    ours = qs.installation_identity(ENV)
    assert len(ours) == 64 and ours == qs.installation_identity(dict(reversed(list(ENV.items()))))
    assert qs.installation_identity({**OUR_SERVICE, "MEMORY_V4_OPS_DIR": "/elsewhere", "MEMORY_V4_CLIENT_ID": "x"}) == ours
    assert qs.entry_identity(relay_entry(**OUR_SERVICE)) == ours
    for entry in FOREIGN_RELAYS.values():
        assert qs.entry_identity(entry) != ours
    assert qs.entry_identity(FOREIGN_RELAYS["no-environment"]) is None
    assert qs.entry_identity(FOREIGN_RELAYS["token-file-only"]) is None


def test_relay_entry_of_this_installation_is_updated_without_extra_consent(home, tmp_path):
    """Same service (URL and token file), another interpreter: shown as ``replace``, applied, backed up."""
    set_entry(home, "cursor", relay_entry(**OUR_SERVICE, MEMORY_V4_CLIENT_ID="cursor"))
    plan, result = apply(home, tmp_path, ("cursor",))
    assert plan["clients"][0]["current_class"] == "relay" and plan["clients"][0]["action"] == "replace"
    assert status(result) == {"cursor": "applied"}
    assert servers(home, "cursor")["memory-os-ai"]["command"] == PY


def test_plan_names_the_identity_it_compared(home):
    set_entry(home, "cursor", relay_entry(**OUR_SERVICE))
    item, = qs.preview(home=home, python=PY, env=ENV, clients=("cursor",))["clients"]
    assert item["current_identity"] == item["installation_identity"] == qs.installation_identity(ENV)


@pytest.mark.parametrize("key", ["cursor", "claude-desktop", "claude-code"])
@pytest.mark.parametrize("case", sorted(FOREIGN_RELAYS))
def test_relay_entry_of_unknown_or_other_identity_is_not_replaced_without_consent(home, tmp_path, key, case):
    set_entry(home, key, FOREIGN_RELAYS[case])
    system = FakeSystem(home)
    before = tree(home)
    plan, result = apply(home, tmp_path, (key,), run=system)
    item, = plan["clients"]
    assert item["current_class"] == "relay" and item["action"] == "refuse-existing-relay-other-installation"
    assert item["current_identity"] != item["installation_identity"]
    assert status(result) == {key: "refuse-existing-relay-other-installation"}
    assert tree(home) == before and system.calls == []


@pytest.mark.parametrize("case", sorted(FOREIGN_RELAYS))
def test_relay_entry_of_another_installation_is_replaced_only_with_named_consent(home, tmp_path, case):
    set_entry(home, "cursor", FOREIGN_RELAYS[case])
    original = (home / PATHS["cursor"]).read_bytes()
    plan, result = apply(home, tmp_path, ("cursor",), replace_existing=("cursor",))
    assert plan["clients"][0]["action"] == "replace" and status(result) == {"cursor": "applied"}
    assert qs.entry_identity(servers(home, "cursor")["memory-os-ai"]) == qs.installation_identity(ENV)
    assert Path(result["records"][0]["backup"]).read_bytes() == original


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
