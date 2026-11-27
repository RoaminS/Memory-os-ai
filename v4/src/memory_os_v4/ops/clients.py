"""Known MCP clients: where each one reads its configuration, how its ``memory-os-ai`` entry is classified, and the
snippet that connects it through the token-reading relay (so no secret ever lands in a client configuration).

Classification of an entry: ``relay`` (stdio relay: OK), ``direct-http`` (URL without the bearer token: 401 on a
secured server), ``legacy-v3`` (old ``memory_os_ai.server``: separate, fragmented memory), ``other``, ``absent``.

Per-OS paths (4.1.1, contract §5, lot L5):
- macOS (``darwin``): all 11 documented user-level locations (``Client.path``).
- Linux (``linux``): 9 clients established from official documentation (``claude-code``, ``codex``,
  ``cursor``, ``antigravity``, ``gemini-cli``, ``zed``, ``devin``, ``grok`` and, since 4.1.3, ``vscode``).
  ``windsurf`` and ``claude-desktop`` are not supported on Linux and receive an actionable refusal without
  guessing any path.
- VS Code on Linux (4.1.3, decision of Romain, 2026-11-26): the official VS Code documentation states no user-level
  ``mcp.json`` path and names, for a server shared across workspaces, the Copilot user configuration
  ``$COPILOT_HOME/mcp-config.json`` or ``~/.copilot/mcp-config.json`` (top-level ``mcpServers``; stdio entries with
  ``type``, ``command``, ``args``, ``env``, ``tools``); https://code.visualstudio.com/docs/copilot/reference/mcp-configuration.
  ``--clients vscode`` writes that file on Linux (format ``copilot-json``); ``COPILOT_HOME`` set, even empty, refuses
  it (``client_env_override``). macOS keeps ``~/Library/Application Support/Code/User/mcp.json`` unchanged.
- ``XDG_CONFIG_HOME`` applies ONLY to Zed on Linux, and ONLY when it is an absolute path under HOME
  (no ``..`` traversal, no symlink escape); otherwise an explicit refusal is raised, consistent with
  ``quickstart._safe_path`` and the L4 systemd XDG limit. Since 4.1.3, ``scripts/memory_os_bootstrap.py`` forwards
  ``XDG_CONFIG_HOME`` (and no other ``XDG_*``) to its child quickstart preview, which validates it with these rules.
- Relocating environment variables (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, ``GEMINI_CLI_HOME``, ``GROK_HOME`` and,
  for VS Code on Linux only, ``COPILOT_HOME``) refuse automatic configuration for the affected client without
  guessing a path, even when they are set to an empty value.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shlex
import sys

SERVER = "memory-os-ai"
RELAY_MARKERS = ("memory_os_v4.ops.relay", "memory-os-relay", "stdio_relay.py")
GEMINI_SYSTEM_SETTINGS = Path("/etc/gemini-cli/settings.json")
LINUX_UNSUPPORTED_REASONS = {
    "windsurf": (
        "Windsurf is not supported on Linux in 4.1.3 (official documentation cites conflicting user-level "
        "paths; re-evaluated in 4.1.3, still refused); no path guessed, nothing written"
    ),
    "claude-desktop": (
        "Claude Desktop is not supported on Linux in 4.1.3 (official Linux version is in beta; "
        "re-evaluated in 4.1.3, still refused); no path guessed, nothing written"
    ),
}


class ClientPathError(ValueError):
    """Actionable refusal when a client's user-level config path is unsupported, relocated or unsafe."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def normalize_platform(platform: str | None = None) -> str:
    """``'darwin'``, ``'linux'``, or the raw platform string."""
    raw = host_platform() if platform is None else platform
    if raw == "darwin":
        return "darwin"
    if isinstance(raw, str) and raw.startswith("linux"):
        return "linux"
    return str(raw)


def host_platform() -> str:
    """Platform of the detected host cell (``darwin``, ``linux``, …); follows ``cell.detect()``."""
    from . import cell
    detected = cell.detect()
    return detected.get("sys_platform") or detected.get("os") or sys.platform


def is_macos(platform: str | None = None) -> bool:
    return normalize_platform(platform) == "darwin"


@dataclass(frozen=True)
class Client:
    key: str
    name: str
    path: str  # relative to $HOME on macOS (kept intact for registry/qualification contracts)
    fmt: str  # "mcpServers" | "vscode" | "codex-toml" | "grok-toml" | "claude-user" | "zed"
    note: str = ""
    linux_path: str | None = None  # relative to $HOME on Linux, or None when unsupported in 4.1.1
    override_env: tuple[str, ...] = ()
    xdg_subpath: str | None = None  # Linux XDG_CONFIG_HOME subpath (Zed only)
    linux_fmt: str | None = None  # Linux-only format when it differs (VS Code: "copilot-json")
    linux_override_env: tuple[str, ...] = ()  # Linux-only relocating variables (VS Code: COPILOT_HOME)

    def on(self, platform: str | None = None) -> "Client":
        """This client as it is configured on ``platform`` (format and relocating variables of that OS)."""
        if normalize_platform(platform) == "linux" and (self.linux_fmt or self.linux_override_env):
            return replace(self, fmt=self.linux_fmt or self.fmt,
                           override_env=self.override_env + self.linux_override_env)
        return self

    def supported_on(self, platform: str | None = None) -> bool:
        os_kind = normalize_platform(platform)
        if os_kind == "darwin":
            return True
        if os_kind == "linux":
            return self.linux_path is not None
        return False

    def config_path(
        self,
        home: Path,
        *,
        platform: str | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Path:
        """Resolve the user-level configuration path for ``(self, platform)``, or raise ``ClientPathError``.

        Never guesses a path when a client is unsupported on the OS, relocated by an environment
        variable (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, ``GEMINI_CLI_HOME``, ``GROK_HOME``), or
        directed outside ``home`` by ``XDG_CONFIG_HOME`` (Zed on Linux only).
        """
        os_kind = normalize_platform(platform)
        if os_kind == "darwin":
            rel_path = self.path
        elif os_kind == "linux":
            if self.linux_path is None:
                reason = LINUX_UNSUPPORTED_REASONS.get(
                    self.key,
                    f"{self.name} ({self.key}) is not supported on Linux in 4.1.3; no path guessed, nothing written",
                )
                raise ClientPathError(reason, code="client_unsupported_on_os")
            rel_path = self.linux_path
        else:
            raise ClientPathError(
                f"{self.name} ({self.key}) has no established user-level config path on platform {os_kind!r}; "
                "no path guessed, nothing written",
                code="client_unsupported_on_os",
            )

        env = os.environ if environ is None else environ
        for var in self.override_env:
            if var in env:
                raise ClientPathError(
                    f"{self.name} ({self.key}) config location is overridden by {var}; automatic configuration "
                    f"is refused without guessing a path (unset {var} or configure {self.name} manually)",
                    code="client_env_override",
                )

        if os_kind == "linux" and self.xdg_subpath:
            xdg = env.get("XDG_CONFIG_HOME")
            if xdg:
                if any(ord(ch) < 32 or ord(ch) == 127 for ch in xdg):
                    raise ClientPathError(
                        f"{self.name} ({self.key}): XDG_CONFIG_HOME contains control characters; "
                        "refusing to guess or write",
                        code="client_xdg_invalid",
                    )
                ambient_default = False
                if environ is None and env.get("HOME"):
                    own_home = Path(os.path.normpath(str(Path(env["HOME"]).absolute())))
                    norm_home_check = Path(os.path.normpath(str(home.absolute())))
                    if norm_home_check != own_home and os.path.isabs(xdg):
                        if Path(os.path.normpath(xdg)) == own_home / ".config":
                            ambient_default = True
                if not ambient_default:
                    if not os.path.isabs(xdg):
                        raise ClientPathError(
                            f"{self.name} ({self.key}): XDG_CONFIG_HOME must be an absolute path under HOME "
                            "(relative XDG_CONFIG_HOME is refused); unset XDG_CONFIG_HOME or set an absolute path under HOME",
                            code="client_xdg_invalid",
                        )
                    xdg_path = Path(xdg)
                    if ".." in xdg_path.parts:
                        raise ClientPathError(
                            f"{self.name} ({self.key}): XDG_CONFIG_HOME contains parent traversal ('..'); "
                            "refusing to write outside HOME",
                            code="client_xdg_outside_home",
                        )
                    norm_home = Path(os.path.normpath(str(home.absolute())))
                    norm_xdg = Path(os.path.normpath(xdg))
                    if not norm_xdg.is_relative_to(norm_home):
                        raise ClientPathError(
                            f"{self.name} ({self.key}): XDG_CONFIG_HOME is outside the selected HOME "
                            "(XDG_CONFIG_HOME outside HOME is not supported, consistent with _safe_path and "
                            "L4 systemd unit rules); move XDG_CONFIG_HOME under HOME or configure Zed manually",
                            code="client_xdg_outside_home",
                        )
                    cursor = norm_home
                    for part in norm_xdg.relative_to(norm_home).parts:
                        cursor = cursor / part
                        if cursor.is_symlink():
                            raise ClientPathError(
                                f"{self.name} ({self.key}): XDG_CONFIG_HOME traverses a symbolic link under HOME; "
                                "refusing to write",
                                code="client_xdg_outside_home",
                            )
                    return norm_xdg / self.xdg_subpath

        return home / rel_path


CLIENTS: tuple[Client, ...] = (
    Client("claude-code", "Claude Code", ".claude.json", "claude-user",
           "user scope; project .mcp.json can override it; use claude mcp add --scope user",
           linux_path=".claude.json", override_env=("CLAUDE_CONFIG_DIR",)),
    Client("claude-desktop", "Claude Desktop", "Library/Application Support/Claude/claude_desktop_config.json",
           "mcpServers", "edit with the app closed: it rewrites this file from memory",
           linux_path=None),
    Client("codex", "Codex (CLI/app)", ".codex/config.toml", "codex-toml",
           linux_path=".codex/config.toml", override_env=("CODEX_HOME",)),
    Client("vscode", "VS Code / GitHub Copilot", "Library/Application Support/Code/User/mcp.json", "vscode",
           linux_path=".copilot/mcp-config.json", linux_fmt="copilot-json", linux_override_env=("COPILOT_HOME",)),
    Client("cursor", "Cursor", ".cursor/mcp.json", "mcpServers",
           linux_path=".cursor/mcp.json"),
    Client("antigravity", "Antigravity", ".gemini/config/mcp_config.json", "mcpServers",
           linux_path=".gemini/config/mcp_config.json"),
    Client("gemini-cli", "Gemini CLI", ".gemini/settings.json", "mcpServers",
           linux_path=".gemini/settings.json", override_env=("GEMINI_CLI_HOME",)),
    Client("grok", "Grok CLI", ".grok/config.toml", "grok-toml",
           linux_path=".grok/config.toml", override_env=("GROK_HOME",)),
    Client("zed", "Zed", ".config/zed/settings.json", "zed",
           linux_path=".config/zed/settings.json", xdg_subpath="zed/settings.json"),
    Client("devin", "Devin Desktop", ".config/devin/mcp_config.json", "mcpServers",
           linux_path=".config/devin/mcp_config.json"),
    Client("windsurf", "Windsurf", ".codeium/windsurf/mcp_config.json", "mcpServers",
           linux_path=None),
)


def platform_clients(platform: str | None = None) -> tuple[Client, ...]:
    """Every known client as configured on ``platform`` (supported or not)."""
    return tuple(c.on(platform) for c in CLIENTS)


def clients_for_platform(platform: str | None = None) -> tuple[Client, ...]:
    """Clients with an established user-level path on ``platform`` (11 on macOS, 9 on Linux)."""
    return tuple(c for c in platform_clients(platform) if c.supported_on(platform))


def unsupported_clients_for_platform(platform: str | None = None) -> tuple[Client, ...]:
    """Known clients without an established user-level path on ``platform``."""
    return tuple(c for c in platform_clients(platform) if not c.supported_on(platform))


def gemini_system_settings_notice(
    *,
    environ: Mapping[str, str] | None = None,
    system_path: Path | None = None,
) -> str | None:
    """Warning when Gemini CLI's system settings file exists (takes precedence over user settings)."""
    env = os.environ if environ is None else environ
    if "GEMINI_CLI_SYSTEM_SETTINGS_PATH" in env:
        override = env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]
        if override:
            target = Path(override)
            exists_note = "exists and " if os.path.lexists(target) else ""
            return (
                f"Gemini CLI system settings override GEMINI_CLI_SYSTEM_SETTINGS_PATH is set ({target}: "
                f"{exists_note}takes precedence over ~/.gemini/settings.json); verify it does not override {SERVER}"
            )
        return (
            "Gemini CLI system settings override GEMINI_CLI_SYSTEM_SETTINGS_PATH is set (empty value); "
            f"verify it does not override {SERVER}"
        )
    target = GEMINI_SYSTEM_SETTINGS if system_path is None else system_path
    if os.path.lexists(target):
        return (
            f"Gemini CLI system settings file {target} exists and takes precedence over "
            f"~/.gemini/settings.json; verify it does not override {SERVER}"
        )
    return None


def grok_claude_duplicate_notice() -> str:
    """Warning when both Claude Code and Grok are configured (Grok also reads ~/.claude.json)."""
    return (
        "Grok also reads ~/.claude.json: configuring both claude-code and grok can expose duplicate "
        f"{SERVER} entries in Grok (disable compatibility import in ~/.grok/config.toml with "
        "[compat.claude] mcps = false)"
    )


def client_notices(
    selected_keys: tuple[str, ...] | list[str] | set[str],
    home: Path,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    gemini_system_path: Path | None = None,
) -> list[str]:
    """Read-only client notices for ``quickstart.prepare`` (Gemini system file, Grok+Claude duplicate)."""
    selected = set(selected_keys)
    notices: list[str] = []
    if "gemini-cli" in selected:
        warning = gemini_system_settings_notice(environ=environ, system_path=gemini_system_path)
        if warning:
            notices.append(warning)

    by_key = {c.key: c for c in platform_clients(platform)}
    both_selected = "claude-code" in selected and "grok" in selected
    grok_with_existing_claude = False
    claude_with_existing_grok = False
    if not both_selected and "grok" in selected:
        state, entry = read_entry(by_key["claude-code"], home, platform=platform, environ=environ)
        grok_with_existing_claude = state == "ok" and entry is not None
    if not both_selected and "claude-code" in selected:
        state, entry = read_entry(by_key["grok"], home, platform=platform, environ=environ)
        claude_with_existing_grok = state == "ok" and entry is not None
    if both_selected or grok_with_existing_claude or claude_with_existing_grok:
        notices.append(grok_claude_duplicate_notice())
    return notices


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
    # A table that is PRESENT but is not a table is not "no entry": it is unreadable for us (TypeError,
    # mapped to ``unreadable`` by read_entry), never an absence.
    tables = []
    if "mcp_servers" in data:
        tables.append(data["mcp_servers"])
    if "mcp" in data:
        nested = data["mcp"]
        if not isinstance(nested, dict):
            raise TypeError("mcp is not a table")
        if "servers" in nested:
            tables.append(nested["servers"])
    if any(not isinstance(table, dict) for table in tables):
        raise TypeError("server table is not a table")
    servers = next((table for table in tables if table), {})
    return servers.get(SERVER)


def read_entry(
    client: Client,
    home: Path,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, dict | None]:
    """(state, entry): state is ``missing-config`` when the client has no configuration file at all."""
    try:
        path = client.config_path(home, platform=platform, environ=environ)
    except ClientPathError as exc:
        if exc.code == "client_unsupported_on_os":
            return "unsupported-on-os", None
        if exc.code == "client_env_override":
            return "env-override", None
        return "unsafe-path", None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return "ok", None
        if client.fmt.endswith("toml"):
            entry = _toml_entry(raw)
        else:
            data = _jsonc(raw) if client.fmt == "zed" else strict_loads(raw)
            key = {"vscode": "servers", "zed": "context_servers"}.get(client.fmt, "mcpServers")
            if not isinstance(data, dict):
                return "unreadable", None
            servers = data.get(key, {})  # absent table: no entry; present table that is not an object: unreadable
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
    if client.fmt == "copilot-json":
        return json.dumps({"mcpServers": {SERVER: {"type": "stdio", "command": command, "args": args, "env": env,
                                                   "tools": ["*"]}}}, indent=1)
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
