"""Known MCP clients: where each one reads its configuration, how its ``memory-os-ai`` entry is classified, and the
snippet that connects it through the token-reading relay (so no secret ever lands in a client configuration).

Classification of an entry: ``relay`` (stdio relay: OK), ``direct-http`` (URL without the bearer token: 401 on a
secured server), ``legacy-v3`` (old ``memory_os_ai.server``: separate, fragmented memory), ``other``, ``absent``.
Paths are the documented user-level locations on macOS (Antigravity's was confirmed in its language server).
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

SERVER = "memory-os-ai"
RELAY_MARKERS = ("memory_os_v4.ops.relay", "memory-os-relay", "stdio_relay.py")


@dataclass(frozen=True)
class Client:
    key: str
    name: str
    path: str  # relative to $HOME
    fmt: str  # "mcpServers" | "vscode" | "codex-toml" | "grok-toml"
    note: str = ""

    def config_path(self, home: Path) -> Path:
        return home / self.path


CLIENTS: tuple[Client, ...] = (
    Client("claude-code", "Claude Code", ".claude.json", "claude-user",
           "user scope; project .mcp.json can override it; use claude mcp add --scope user"),
    Client("claude-desktop", "Claude Desktop", "Library/Application Support/Claude/claude_desktop_config.json",
           "mcpServers", "edit with the app closed: it rewrites this file from memory"),
    Client("codex", "Codex (CLI/app)", ".codex/config.toml", "codex-toml"),
    Client("vscode", "VS Code / GitHub Copilot", "Library/Application Support/Code/User/mcp.json", "vscode"),
    Client("cursor", "Cursor", ".cursor/mcp.json", "mcpServers"),
    Client("antigravity", "Antigravity", ".gemini/config/mcp_config.json", "mcpServers"),
    Client("gemini-cli", "Gemini CLI", ".gemini/settings.json", "mcpServers"),
    Client("grok", "Grok CLI", ".grok/config.toml", "grok-toml"),
    Client("zed", "Zed", ".config/zed/settings.json", "zed"),
    Client("devin", "Devin Desktop", ".config/devin/mcp_config.json", "mcpServers"),
    Client("windsurf", "Windsurf", ".codeium/windsurf/mcp_config.json", "mcpServers"),
)


def classify(entry: dict | None) -> str:
    if entry is not None and not isinstance(entry, dict):
        return "other"
    if not entry:
        return "absent"
    if not isinstance(entry.get("args", []), list):
        return "other"
    command = " ".join(str(x) for x in [entry.get("command", ""), *(entry.get("args") or [])])
    url = str(entry.get("url") or entry.get("serverUrl") or entry.get("httpUrl") or "")
    if any(marker in command for marker in RELAY_MARKERS):
        return "relay"
    if "memory_os_ai.server" in command or "memory-os-ai-v3" in command:
        return "legacy-v3"
    if url:
        return "direct-http"
    return "other"


class DuplicateKey(ValueError):
    """A JSON object holding the same key twice: which value a client uses is not ours to guess."""


def _no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise DuplicateKey("duplicate key")
    return dict(pairs)


def strict_loads(text: str):
    """``json.loads`` that REFUSES duplicate keys at every level (``DuplicateKey``, a ``ValueError``).

    Used for every JSON read a decision is taken on: a duplicated table or a duplicated server
    name could otherwise mask an entry behind the last value.
    """
    return json.loads(text, object_pairs_hook=_no_duplicates)


def _toml_entry(text: str) -> dict | None:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        return None
    data = tomllib.loads(text)
    servers = data.get("mcp_servers") or data.get("mcp", {}).get("servers") or {}
    return servers.get(SERVER)


def read_entry(client: Client, home: Path) -> tuple[str, dict | None]:
    """(state, entry): state is ``missing-config`` when the client has no configuration file at all."""
    path = client.config_path(home)
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return "ok", None
        if client.fmt.endswith("toml"):
            entry = _toml_entry(raw)
        else:
            data = _jsonc(raw) if client.fmt == "zed" else strict_loads(raw)
            key = {"vscode": "servers", "zed": "context_servers"}.get(client.fmt, "mcpServers")
            servers = data.get(key, {})
            if not isinstance(servers, dict):
                return "unreadable", None
            if SERVER in servers and servers[SERVER] is None:
                return "unreadable", None  # our name IS there (key membership), with a null value: not "absent"
            entry = servers.get(SERVER)
        if entry is not None and not isinstance(entry, dict):
            return "unreadable", None
        if client.fmt == "zed" and entry is not None and entry.get("source") != "custom":
            return "unreadable", None
        return "ok", entry
    except FileNotFoundError:
        return "missing-config", None
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return "unreadable", None


def relay_command(python: str) -> tuple[str, list[str]]:
    return python, ["-m", "memory_os_v4.ops.relay"]


def snippet(client: Client, python: str, env: dict[str, str]) -> str:
    command, args = relay_command(python)
    env = {"MEMORY_V4_CLIENT_ID": client.key, **env}
    if client.fmt == "zed":
        return json.dumps({"context_servers": {SERVER: {"source": "custom", "command": command, "args": args, "env": env}}}, indent=1)
    if client.fmt == "claude-user":
        flags = " ".join(f"--env {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        return f"claude mcp add --scope user --transport stdio {flags} {SERVER} -- " + " ".join(
            shlex.quote(a) for a in [command, *args])
    if client.fmt == "vscode":
        return json.dumps({"servers": {SERVER: {"type": "stdio", "command": command, "args": args, "env": env}}},
                          indent=1)
    if client.fmt == "codex-toml":
        flags = " ".join(f"--env {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        return f"codex mcp add {SERVER} {flags} -- " + " ".join(shlex.quote(a) for a in [command, *args])
    if client.fmt == "grok-toml":
        # TOML is mergeable and propagates all non-secret settings, unlike an unqualified CLI flag guess.
        return f"[mcp_servers.{SERVER}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n" + (
            f"[mcp_servers.{SERVER}.env]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in env.items()))
    return json.dumps({"mcpServers": {SERVER: {"command": command, "args": args, "env": env}}}, indent=1)



def _jsonc(text: str):
    """Zed JSONC: remove comments/trailing commas outside quoted JSON strings only."""
    import re
    # Tokenization preserves quoted URLs, escaped quotes and comment-like text.
    token = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
    clean = token.sub(lambda m: m.group() if m.group().startswith('"') else ' ', text)
    token = re.compile(r'"(?:\\.|[^"\\])*"|,\s*(?=[}\]])')
    return strict_loads(token.sub(lambda m: m.group() if m.group().startswith('"') else '', clean))
