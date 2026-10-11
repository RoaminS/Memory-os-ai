"""``memory-os-admin`` — operate a local memory-os-v4 ALPHA (see docs/OPERATIONS.md).

token create                 create the 0600 bearer-token file if missing (never printed)
health [--confined]          daily health report (exit 1 on anomaly)
doctor [--json|--store-only]  read-only installation checks or local store counters
backup [--no-stop]           encrypted, consistent backup (stops/restarts the launchd service)
restore-drill [--no-server]  decrypt latest backup in a temp dir, verify manifest, serve it on a temp port
hook session-start|stop-nudge   Claude Code hooks (stdin JSON → stdout JSON)
setup [--write-launchd DIR]  print client configs (Claude Code, Claude Desktop, Codex, hooks) and launchd jobs
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from urllib.parse import urlsplit
from pathlib import Path

from .settings import OpsSettings


def _print_json(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def cmd_token(args, s: OpsSettings) -> int:
    from .auth import create_token

    try:
        created = create_token(s.token_file)
    except (ValueError, OSError) as exc:  # unsafe/malformed existing file: never overwritten, never printed
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"{'created' if created else 'exists and is valid'}: {s.token_file} (mode 0600, content never printed)")
    return 0


def cmd_provision(args, s: OpsSettings) -> int:
    from .provision import ProvisionError, apply, plan
    try:
        preview = plan(resources=tuple(args.resources.split(",")), cache_dir=args.cache_dir,
                       table_path=args.table_path)
        result = apply(preview, download_approved=args.download_approved) if args.apply else preview
        _print_json(result)
        return 0 if result["ok"] else 1
    except (OSError, ValueError, ProvisionError) as exc:
        _print_json({"ok": False, "code": "provision_refused",
                     "error": str(exc) if isinstance(exc, ProvisionError) else type(exc).__name__})
        return 1


def cmd_health(args, s: OpsSettings) -> int:
    from .health import notify, render, report

    r = report(s, confined=True if args.confined else None)
    print(render(r), end="")
    notify(r)
    return 1 if r["anomalies"] else 0


def cmd_store_doctor(args, s: OpsSettings) -> int:
    import sqlite3
    from .health import scan_store
    try:
        _print_json(scan_store(s.store_dir))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 1


def cmd_backup(args, s: OpsSettings) -> int:
    if args.backend == "sqlite":
        from .sqlite_backup import backup as sqlite_backup
        if not args.online or args.no_stop:
            _print_json({"ok": False, "code": "invalid_options", "error": "SQLite requires --online, never --no-stop"})
            return 2
        r = sqlite_backup(s, apply=args.apply)
        _print_json(r)
        return 0 if r["ok"] else 1
    if args.online or args.apply:
        _print_json({"ok": False, "code": "invalid_options", "error": "--online/--apply require SQLite S4"})
        return 2
    from .backup import backup

    r = backup(s, stop_service=not args.no_stop)
    _print_json(r)
    return 0 if r["ok"] else 1


def cmd_restore_drill(args, s: OpsSettings) -> int:
    if args.backend == "sqlite":
        from .sqlite_backup import restore_drill as sqlite_drill
        r = sqlite_drill(s, Path(args.archive) if args.archive else None,
                         apply=args.apply, start_server=not args.no_server, reconcile=args.reconcile_forgets)
        _print_json(r)
        return 0 if r["ok"] else 1
    if args.apply or args.reconcile_forgets:
        _print_json({"ok": False, "code": "invalid_options", "error": "--apply requires SQLite S4"})
        return 2
    from .backup import restore_drill

    r = restore_drill(s, Path(args.archive) if args.archive else None, start_server=not args.no_server)
    _print_json(r)
    return 0 if r["ok"] else 1


def cmd_hook(args, s: OpsSettings) -> int:
    from .hooks import HOOKS

    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    try:
        out = HOOKS[args.name](payload, s)
    except Exception:
        return 0  # a hook must never break the client session
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


def _plist(
    label: str,
    argv: list[str],
    *,
    env: dict | None = None,
    keepalive: bool = False,
    hour: int | None = None,
    minute: int = 0,
    weekday: int | None = None,
    nice: int | None = None,
    log: Path,
) -> str:
    def esc(x: str) -> str:
        return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [
        f"<key>Label</key><string>{label}</string>",
        "<key>ProgramArguments</key><array>"
        + "".join(f"<string>{esc(a)}</string>" for a in argv)
        + "</array>",
    ]
    if env:
        parts.append(
            "<key>EnvironmentVariables</key><dict>"
            + "".join(f"<key>{esc(k)}</key><string>{esc(v)}</string>" for k, v in env.items())
            + "</dict>"
        )
    if keepalive:
        parts.append("<key>KeepAlive</key><true/><key>RunAtLoad</key><true/>")
    if hour is not None:
        parts.append(
            f"<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer>"
            f"<key>Minute</key><integer>{minute}</integer>"
            + (f"<key>Weekday</key><integer>{weekday}</integer>" if weekday is not None else "")
            + "</dict>"
        )
    if nice is not None:
        parts.append(f"<key>Nice</key><integer>{nice}</integer>")
    parts.append(f"<key>StandardErrorPath</key><string>{esc(str(log))}</string>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>\n '
        + "\n ".join(parts)
        + "\n</dict></plist>\n"
    )


def cmd_setup(args, s: OpsSettings) -> int:
    from .auth import create_token
    from .clients import CLIENTS, snippet

    sqlite_jobs = getattr(args, 'sqlite_s4_jobs', False)
    if sqlite_jobs:
        import re
        from ..config import Config
        from ..readiness import build_identity
        cfg = Config.from_env()
        declared = build_identity()
        commit = declared.get('commit') or ''
        if cfg.store_backend != 'sqlite_s1' or declared.get('source_dirty') or not re.fullmatch(r'[0-9a-f]{40}', commit):
            _print_json({'ok': False, 'code': 'sqlite_s4_not_configured',
                         'error': 'Inactive SQLite templates require explicit sqlite_s1 and qualified build commit; token unchanged'})
            return 2

    try:  # one-command installation: the token is created when missing (never printed, never overwritten)
        created = create_token(s.token_file)
    except (ValueError, OSError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"# token: {'created' if created else 'exists and is valid'} — {s.token_file} (0600, never printed)")
    py = sys.executable
    # Every NON-secret setting is propagated; the token stays in its 0600 file (only its path is written).
    env = {
        "MEMORY_V4_DIR": str(s.store_dir),
        "MEMORY_V4_URL": s.url,
        "MEMORY_V4_TOKEN_FILE": str(s.token_file),
        "MEMORY_V4_BACKUP_DIR": str(s.backup_dir),
        "MEMORY_V4_BACKUP_KEY": str(s.backup_key),
        "MEMORY_V4_OPS_DIR": str(s.ops_dir),
        "MEMORY_V4_SUPERSEDE_COS": "1.01",  # INC-001 confinement
        "MEMORY_V4_DEDUP_COS": "1.01",  # INC-002 confinement
        "HF_HUB_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    if sqlite_jobs:
        env.update(MEMORY_V4_STORE_BACKEND='sqlite_s1', MEMORY_V4_BUILD_COMMIT=commit,
                   MEMORY_V4_EMBED_MODEL=cfg.embed_model, MEMORY_V4_DEVICE=cfg.device,
                   MEMORY_V4_HASH_DIM=str(cfg.hash_dim), MEMORY_V4_EMBED_DTYPE=cfg.embed_dtype,
                   MEMORY_V4_EMBED_BATCH_SIZE=str(cfg.embed_batch_size),
                   MEMORY_V4_EMBED_MAX_SEQ_LENGTH=str(cfg.embed_max_seq_length),
                   MEMORY_V4_SQLITE_EMBEDDING_NAMESPACE=cfg.sqlite_embedding_namespace,
                   MEMORY_V4_BM25_PROFILE=cfg.bm25_profile,
                   MEMORY_V4_LEXICAL_GUARD_K=str(cfg.lexical_guard_k))
    label = s.service_label or "local.memory-os-v4"
    host = urlsplit(s.url).hostname or "127.0.0.1"
    try:
        from ..readiness import build_identity

        identity = build_identity()
    except Exception as exc:  # pragma: no cover - defensive
        identity = {"commit": None, "error": type(exc).__name__}
    qualified_build = bool(identity.get("commit")) and not identity.get("source_dirty")
    if qualified_build and identity.get("identity_source") == "embedded_descriptor":
        env.pop("MEMORY_V4_BUILD_COMMIT", None)
        print("# Qualified embedded descriptor: MEMORY_V4_BUILD_COMMIT is unnecessary (remove old declarations).")
    elif qualified_build:
        env["MEMORY_V4_BUILD_COMMIT"] = identity["commit"]  # non-secret; launchd does not inherit this process env
    else:
        print("# WARNING: no qualified build identity (commit unknown or modified checkout). The generated server "
              "job is NOT qualified: /ready will answer 503 build_unqualified. Install a qualified artifact; "
              "MEMORY_V4_BUILD_COMMIT is only a declarative option for source checkouts.")
    table = os.environ.get("MEMORY_V4_CL100K_PATH")
    if table:
        env["MEMORY_V4_CL100K_PATH"] = table
    for name in ("HF_HOME", "HF_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    relay_env = {k: env[k] for k in ("MEMORY_V4_URL", "MEMORY_V4_TOKEN_FILE")}
    hook_env = "env " + " ".join(
        shlex.quote(f"{k}={env[k]}") for k in ("MEMORY_V4_URL", "MEMORY_V4_TOKEN_FILE", "MEMORY_V4_OPS_DIR")
    )
    print("# 1. Connect each AI client through the relay (the relay reads the token file; no secret in configs):")
    for client in CLIENTS:
        note = f" — {client.note}" if client.note else ""
        print(f"\n## {client.name} (~/{client.path}){note}")
        print(snippet(client, py, relay_env))
    print("\n# 2. Claude Code hooks (~/.claude/settings.json, merge into 'hooks'):")
    _print_json(
        {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "timeout": 15,
                            "command": f"{hook_env} {shlex.quote(py)} -m memory_os_v4.ops.cli hook session-start",
                        }
                    ]
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "timeout": 10,
                            "command": f"{hook_env} {shlex.quote(py)} -m memory_os_v4.ops.cli hook stop-nudge",
                        }
                    ]
                }
            ],
        }
    )
    jobs = {
        label: _plist(
            label,
            [
                py,
                "-m",
                "memory_os_v4.server",
                "--transport",
                "http",
                "--host",
                host,
                "--port",
                str(s.port),
                "--warm",
            ],
            env=env,
            keepalive=True,
            log=s.store_dir / "server.log",
        ),
        f"{label}.backup": _plist(
            f"{label}.backup",
            [py, "-m", "memory_os_v4.ops.cli", "backup"],
            hour=4,
            env={**env, "MEMORY_V4_SERVICE_LABEL": label},
            log=s.ops_dir / "logs/backup.err",
        ),
        f"{label}.health": _plist(
            f"{label}.health",
            [py, "-m", "memory_os_v4.ops.cli", "health"],
            hour=8,
            minute=30,
            env=env,
            log=s.ops_dir / "logs/health.err",
        ),
    }
    if getattr(args, "sqlite_s4_jobs", False):
        jobs[f"{label}.backup"] = _plist(f"{label}.backup",
            [py, "-m", "memory_os_v4.ops.cli", "backup", "--backend", "sqlite", "--online", "--apply"],
            hour=4, nice=15, env=env, log=s.ops_dir / "logs/backup.err")
        jobs[f"{label}.restore-drill"] = _plist(f"{label}.restore-drill",
            [py, "-m", "memory_os_v4.ops.cli", "restore-drill", "--backend", "sqlite", "--apply", "--reconcile-forgets"],
            hour=3, minute=30, weekday=1, nice=15, env=env, log=s.ops_dir / "logs/restore-drill.err")
        print("# SQLite S4 templates only: no activation/migration. Weekly private drill requires current "
              "authority, qualified build commit and existing offline encoder; no production promotion.")
    if args.write_launchd:
        out = Path(args.write_launchd).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        for name, xml in jobs.items():
            (out / f"{name}.plist").write_text(xml)
        print(
            f"\n# 3. launchd jobs written to {out} — copy to ~/Library/LaunchAgents, then "
            f"`launchctl bootstrap gui/$(id -u) <plist>` (after editing a plist: bootout + bootstrap)."
        )
    else:
        print(f"\n# 3. launchd jobs: rerun with --write-launchd DIR ({', '.join(jobs)})")
    print("\n# 4. Then check everything: memory-os-admin doctor")
    return 0


def cmd_doctor(args, s: OpsSettings) -> int:
    from dataclasses import asdict

    from .doctor import render, run

    if args.store_only:
        return cmd_store_doctor(args, s)
    checks = run(s)
    if args.json:
        _print_json([asdict(c) for c in checks])
    else:
        print(render(checks))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="memory-os-admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    pv = sub.add_parser("provision", help="preview pinned resources; no download without two explicit flags")
    pv.add_argument("--resources", default="granite,cl100k")
    pv.add_argument("--cache-dir", type=Path)
    pv.add_argument("--table-path", type=Path)
    pv.add_argument("--apply", action="store_true")
    pv.add_argument("--download-approved", action="store_true")
    t = sub.add_parser("token")
    t.add_argument("action", choices=["create"])
    h = sub.add_parser("health")
    h.add_argument("--confined", action="store_true")
    b = sub.add_parser("backup")
    b.add_argument("--backend", choices=("jsonl", "sqlite"), default="jsonl")
    b.add_argument("--online", action="store_true")
    b.add_argument("--apply", action="store_true", help="SQLite only: execute rather than simulate")
    b.add_argument(
        "--no-stop", action="store_true", help="copy without stopping the service (not consistent)"
    )
    r = sub.add_parser("restore-drill")
    r.add_argument("--backend", choices=("jsonl", "sqlite"), default="jsonl")
    r.add_argument("--apply", action="store_true", help="SQLite only: execute a quarantined file drill")
    r.add_argument("--reconcile-forgets", action="store_true", help="SQLite only: current-authority reconciliation and new copy identity; required for private server")
    r.add_argument("--archive")
    r.add_argument("--no-server", action="store_true")
    k = sub.add_parser("hook")
    k.add_argument("name", choices=["session-start", "stop-nudge"])
    dr = sub.add_parser("doctor", help="read-only check of the whole installation and of every AI client")
    dr.add_argument("--json", action="store_true")
    dr.add_argument("--store-only", action="store_true", help="local store counters only (no HTTP or client reads)")
    st = sub.add_parser("setup")
    st.add_argument("--write-launchd", metavar="DIR")
    st.add_argument("--sqlite-s4-jobs", action="store_true", help="Generate inactive SQLite backup + weekly file-drill templates")
    args = p.parse_args(argv)
    s = OpsSettings.from_env()
    handler = {
        "provision": cmd_provision,
        "token": cmd_token,
        "health": cmd_health,
        "backup": cmd_backup,
        "restore-drill": cmd_restore_drill,
        "hook": cmd_hook,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
    }[args.cmd]
    return handler(args, s)


if __name__ == "__main__":
    sys.exit(main())
