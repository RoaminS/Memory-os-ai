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
    Client("claude-code", "Claude Code", ".mcp.json", "mcpServers"),
    Client("claude-desktop", "Claude Desktop", "Library/Application Support/Claude/claude_desktop_config.json",
           "mcpServers", "edit with the app closed: it rewrites this file from memory"),
    Client("codex", "Codex (CLI/app)", ".codex/config.toml", "codex-toml"),
    Client("vscode", "VS Code / GitHub Copilot", "Library/Application Support/Code/User/mcp.json", "vscode"),
    Client("cursor", "Cursor", ".cursor/mcp.json", "mcpServers"),
    Client("antigravity", "Antigravity", ".gemini/config/mcp_config.json", "mcpServers"),
    Client("gemini-cli", "Gemini CLI", ".gemini/settings.json", "mcpServers"),
    Client("grok", "Grok CLI", ".grok/config.toml", "grok-toml"),
    Client("windsurf", "Windsurf", ".codeium/windsurf/mcp_config.json", "mcpServers"),
)


def classify(entry: dict | None) -> str:
    if not entry:
        return "absent"
    command = " ".join(str(x) for x in [entry.get("command", ""), *(entry.get("args") or [])])
    url = str(entry.get("url") or entry.get("serverUrl") or entry.get("httpUrl") or "")
    if any(marker in command for marker in RELAY_MARKERS):
        return "relay"
    if "memory_os_ai.server" in command or "memory-os-ai-v3" in command:
        return "legacy-v3"
    if url:
        return "direct-http"
    return "other"


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
    if not path.is_file():
        return "missing-config", None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return "ok", None
    try:
        if client.fmt.endswith("toml"):
            return "ok", _toml_entry(raw)
        data = json.loads(raw)
    except Exception:
        return "unreadable", None
    key = "servers" if client.fmt == "vscode" else "mcpServers"
    return "ok", (data.get(key) or {}).get(SERVER)


def relay_command(python: str) -> tuple[str, list[str]]:
    return python, ["-m", "memory_os_v4.ops.relay"]


def snippet(client: Client, python: str, env: dict[str, str]) -> str:
    command, args = relay_command(python)
    if client.fmt == "vscode":
        return json.dumps({"servers": {SERVER: {"type": "stdio", "command": command, "args": args, "env": env}}},
                          indent=1)
    if client.fmt == "codex-toml":
        flags = " ".join(f"--env {shlex.quote(f'{k}={v}')}" for k, v in env.items())
        return f"codex mcp add {SERVER} {flags} -- " + " ".join(shlex.quote(a) for a in [command, *args])
    if client.fmt == "grok-toml":
        return f"grok mcp add {SERVER} " + " ".join(shlex.quote(a) for a in [command, "--", *args])
    return json.dumps({"mcpServers": {SERVER: {"command": command, "args": args, "env": env}}}, indent=1)

