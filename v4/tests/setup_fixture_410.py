"""Shared support for the 4.1.0 setup tests (docs/SETUP_4_1_0.md): temporary HOME only.

Nothing here reaches the real HOME, launchd, the network or a real client: every subprocess
goes through ``FakeSystem`` (``run=``), which refuses any command it does not know.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from memory_os_v4.ops import quickstart as qs

# Planted secrets: must never appear in a preview, report, journal, error or doctor output.
FOREIGN_SECRET = "sk-FOREIGN-SENTINEL-7f3a9c1e5b2d4f60"
BEARER_SECRET = "PREVIOUS-BEARER-SENTINEL-0a1b2c3d4e5f"
ACCOUNT_SECRET = "oauth-ACCOUNT-SENTINEL-99887766"
SECRETS = (FOREIGN_SECRET, BEARER_SECRET, ACCOUNT_SECRET)

SIX = ("claude-code", "claude-desktop", "codex", "cursor", "vscode", "antigravity")
FILE_CLIENTS = ("claude-desktop", "codex", "cursor", "vscode", "antigravity")
JSON_CLIENTS = ("claude-desktop", "cursor", "vscode", "antigravity")

PATHS = {
    "claude-code": ".claude.json",
    "claude-desktop": "Library/Application Support/Claude/claude_desktop_config.json",
    "codex": ".codex/config.toml",
    "cursor": ".cursor/mcp.json",
    "vscode": "Library/Application Support/Code/User/mcp.json",
    "antigravity": ".gemini/config/mcp_config.json",
}

CODEX_TOML = (
    "# Codex settings kept by the user\n"
    'model = "gpt-5"   # inline comment\n'
    "\n"
    "[mcp_servers.other]\n"
    'command = "other-server"\n'
    'args = ["--flag"]  # keep me\n'
    "\n"
    "[mcp_servers.other.env]\n"
    f'API_KEY = "{FOREIGN_SECRET}"\n'
    "\n"
    "[profiles.work]\n"
    'approval_policy = "never"\n'
)


def _dump(data, indent) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def client_files() -> dict[str, str]:
    """Canonical fixture configs, each with unrelated entries and its own indentation."""
    other = {"command": "other-server", "args": ["--flag"], "env": {"API_KEY": FOREIGN_SECRET}}
    return {
        "claude-code": _dump({"numStartups": 3, "oauthAccount": {"accessToken": ACCOUNT_SECRET},
                              "mcpServers": {"other": other}}, 2),
        "claude-desktop": _dump({"globalShortcut": "Alt+Space", "mcpServers": {"filesystem": other}}, 4),
        "codex": CODEX_TOML,
        "cursor": _dump({"mcpServers": {"other": other}}, "\t"),
        "vscode": _dump({"servers": {"other": {"type": "stdio", **other}}, "inputs": []}, 2),
        "antigravity": _dump({"mcpServers": {"other": other}, "théme": "sombre"}, 2),
    }


def write(home: Path, rel: str, text: str, mode: int = 0o600) -> Path:
    path = home / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    return path


def make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for key, text in client_files().items():
        write(home, PATHS[key], text)
    return home


def tree(root: Path) -> dict:
    """Full inventory: every path with type, mode, mtime and bytes (symlinks not followed)."""
    out = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        out[str(path.relative_to(root))] = (
            info.st_mode, info.st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else b"")
    return out


def contents(root: Path) -> dict:
    """Bytes only (for data that may be re-stat'ed but must never change)."""
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def servers(home: Path, key: str) -> dict:
    """The MCP server table of a client fixture, parsed."""
    text = (home / PATHS[key]).read_text(encoding="utf-8")
    if key == "codex":
        import tomllib
        return tomllib.loads(text).get("mcp_servers", {})
    return json.loads(text)["servers" if key == "vscode" else "mcpServers"]


def set_entry(home: Path, key: str, entry: dict) -> None:
    """Plant an existing ``memory-os-ai`` entry in a JSON client fixture."""
    path = home / PATHS[key]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["servers" if key == "vscode" else "mcpServers"]["memory-os-ai"] = entry
    path.write_text(_dump(data, 2), encoding="utf-8")


EXISTING = {
    "direct-http": {"type": "http", "url": "http://127.0.0.1:8765/mcp/",
                    "headers": {"Authorization": "Bearer " + BEARER_SECRET}},
    "legacy-v3": {"command": "/old/python", "args": ["-m", "memory_os_ai.server"]},
    "other": {"command": "some-wrapper", "args": ["memory"]},
}


class FakeSystem:
    """Injected ``run``: launchctl, pgrep and the ``claude`` CLI, nothing else."""

    def __init__(self, home: Path):
        self.home = home
        self.calls: list[list[str]] = []
        self.loaded = False
        self.service: dict | None = None  # the planned service, once a test has one
        self.desktop_running = False
        self.claude_fails = False

    def __call__(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if argv[:2] == ["launchctl", "print"]:
            if not self.loaded or self.service is None:
                return SimpleNamespace(returncode=113, stdout="", stderr="Could not find service")
            text = f"program = {self.service['argv'][0]}\narguments = {{\n" + "\n".join(self.service["argv"]) + "\n}\n"
            text += "\n".join(f"{k} => {v}" for k, v in self.service["env"].items())
            return SimpleNamespace(returncode=0, stdout=text, stderr="")
        if argv[:2] == ["launchctl", "bootstrap"]:
            self.loaded = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["launchctl", "bootout"]:
            self.loaded = False
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["pgrep", "-x"]:
            return SimpleNamespace(returncode=0 if self.desktop_running else 1, stdout="", stderr="")
        if argv[:2] == ["claude", "mcp"]:
            assert kwargs["env"]["HOME"] == str(self.home), "claude CLI must run against the selected HOME"
            if self.claude_fails:
                return SimpleNamespace(returncode=1, stdout="", stderr="refused")
            path = self.home / ".claude.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            table = data.setdefault("mcpServers", {})
            if argv[2] == "add":
                split = argv.index("--")
                env = {argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
                       for i, value in enumerate(argv[:split]) if value == "--env"}
                table["memory-os-ai"] = {"type": "stdio", "command": argv[split + 1],
                                         "args": argv[split + 2:], "env": env}
            elif argv[2] == "remove":
                table.pop("memory-os-ai", None)
            else:
                raise AssertionError(f"unexpected claude command: {argv}")
            path.write_text(_dump(data, 2), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected system command: {argv}")

    def verbs(self, tool: str = "launchctl") -> list[str]:
        return [argv[1] for argv in self.calls if argv[0] == tool]


def resource_plan(home: Path, resources) -> dict:
    """Synthetic ``provision.plan`` result: resources present, nothing to download."""
    env = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    if "granite" in resources:
        env["HF_HUB_CACHE"] = str(home / "cache")
    if "cl100k" in resources:
        env["MEMORY_V4_CL100K_PATH"] = str(home / "table")
    return {"ok": True, "ready": True, "download_bytes": 0, "peak_additional_bytes_upper_bound": 1_000_000,
            "resources": list(resources), "env": env}


def setup_context(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """A whole fake machine: HOME with the six clients, settings below HOME, fake system."""
    from memory_os_v4 import __version__, readiness
    from memory_os_v4.ops import provision, qualified_runtime
    from memory_os_v4.ops.settings import OpsSettings

    home = make_home(tmp_path)
    python = home / "runtime/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("# synthetic executable, never executed")
    python.chmod(0o700)
    settings = OpsSettings("http://127.0.0.1:43219/mcp/", home / ".config/memory-os/http.token",
                           home / ".local/share/memory-os/store", home / "memory-os-backups",
                           home / ".config/memory-os/backup.key", home / ".local/state/memory-os/ops",
                           "test.memory-os")
    identity = {"version": __version__, "commit": "a" * 40, "source_dirty": False,
                "identity_source": "embedded_descriptor"}
    system = FakeSystem(home)
    seen = SimpleNamespace(plan_calls=[], apply_calls=[], runtime_calls=[], ready_calls=[])

    def fake_plan(**kwargs):
        seen.plan_calls.append(kwargs)
        return resource_plan(home, tuple(kwargs.get("resources", ("granite", "cl100k"))))

    def fake_apply(preview, **kwargs):
        seen.apply_calls.append((preview, kwargs))
        return dict(preview)

    def fake_runtime(**kwargs):
        seen.runtime_calls.append(kwargs)
        return {"ok": True, "state": "qualified", "mismatches": {}, "profile_id": "synthetic"}

    ready = {"version": __version__, "capabilities": {"store": {"instance_id": "synthetic"}}}

    def fake_ready(*args, **kwargs):
        seen.ready_calls.append(kwargs)
        return ready

    monkeypatch.setattr(qs.sys, "platform", "darwin")
    monkeypatch.setattr(qs.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(qs.shutil, "disk_usage", lambda *a: SimpleNamespace(free=20_000_000_000))
    monkeypatch.setattr(qs, "_port_state", lambda port: "free")
    monkeypatch.setattr(qs, "_ready", fake_ready)
    monkeypatch.setattr(readiness, "build_identity", lambda: dict(identity))
    monkeypatch.setattr(provision, "plan", fake_plan)
    monkeypatch.setattr(provision, "apply", fake_apply)
    monkeypatch.setattr(provision, "guard", lambda *a: None)
    monkeypatch.setattr(qualified_runtime, "inspect_current", fake_runtime)
    return SimpleNamespace(root=tmp_path, home=home, python=str(python), settings=settings,
                           identity=identity, system=system, seen=seen, ready=ready)


def prepare(c, *, profile, clients=("cursor",), **kwargs) -> dict:
    """``quickstart.prepare`` with the explicit profile required by the 4.1.0 setup contract."""
    plan = qs.prepare(c.settings, home=c.home, python=c.python, clients=clients, run=c.system,
                      profile=profile, **kwargs)
    c.system.service = plan.get("service")
    return plan


async def fake_probe(python, env, **kwargs):
    return {"connected": True, "real_client_usage": "unmeasured", "demo": kwargs.get("demo", False)}


def install(c, *, profile, clients=("cursor",), **kwargs) -> dict:
    """Preview then explicit apply through the fakes; returns the apply journal document."""
    from memory_os_v4.ops.doctor import Check
    plan = prepare(c, profile=profile, clients=clients, **kwargs)
    assert plan["ok"], plan.get("blockers")
    return qs.apply_install(plan, c.settings, home=c.home, run=c.system, probe=fake_probe,
                            doctor_run=lambda settings: [Check("server", "PASS", "synthetic")])


def plant_data(c) -> None:
    """Memory written *after* the install: store, backups and key must survive every exit."""
    c.settings.store_dir.mkdir(parents=True, exist_ok=True)
    (c.settings.store_dir / "store.sqlite3").write_bytes(b"SQLite format 3\x00 new writes since install")
    (c.settings.store_dir / "store.sqlite3-wal").write_bytes(b"uncheckpointed new write")
    c.settings.backup_dir.mkdir(parents=True, exist_ok=True)
    (c.settings.backup_dir / "backup-0001.enc").write_bytes(b"encrypted backup")
    c.settings.backup_key.parent.mkdir(parents=True, exist_ok=True)
    if not c.settings.backup_key.exists():
        c.settings.backup_key.write_bytes(b"k" * 32)
        c.settings.backup_key.chmod(0o600)


def data_inventory(c) -> dict:
    """Bytes of everything the user owns: store, backups, backup key, token."""
    out = {}
    for root in (c.settings.store_dir, c.settings.backup_dir):
        if root.exists():
            out.update({str(root / name): data for name, data in contents(root).items()})
    for path in (c.settings.backup_key, c.settings.token_file):
        if path.exists():
            out[str(path)] = path.read_bytes()
    return out


def all_text(root: Path, *, skip_suffixes=(".bak",)) -> str:
    """Concatenated text of every journal/report file below ``root`` (verbatim backups excluded)."""
    parts = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix not in skip_suffixes:
            parts.append(path.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(parts)
