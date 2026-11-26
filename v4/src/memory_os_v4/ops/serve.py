"""4.1.3 Lot 3: ``memory-os-admin serve``, the shared service in the FOREGROUND, without launchd or systemd.

For machines and environments without a supported service manager (a container, WSL2, a terminal session), and the
only mode allowed on an unqualified platform (decision D7).

* The environment is the one quickstart writes into a service definition (``quickstart._service_plan`` without a
  manager): store, token file, loopback URL, backend, profile and resources. Nothing is written by ``serve``.
* What quickstart checks before installing is checked before starting: the cell (or the explicit
  ``--allow-unqualified-platform``), the qualified runtime and build, the existing private token (never created
  here), the provisioned resources of the profile.
* The process is then REPLACED by the server (``os.execve``): it listens on 127.0.0.1 only, handles SIGINT and SIGTERM
  itself (clean shutdown, engine closed, writer lease released) and its exit code is the command's.
* Inherited ``MEMORY_V4_*`` variables are dropped: only the installation's own environment reaches the server.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

UNQUALIFIED_ENV = "MEMORY_V4_PLATFORM_UNQUALIFIED"
UNQUALIFIED_WARNING = ("WARNING: unqualified platform ({label}): Memory OS AI makes no qualification claim here; "
                       "lexical profile, foreground `serve` only, no service installed. Report problems with the "
                       "platform named.")


class ServeRefused(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def plan(settings, *, profile=None, allow_unqualified=False, home=None, environ=None, detected=None,
         python=None, container_listen_all=False, container=None) -> dict:
    """The exact foreground command (argv, env) of this installation, or ``ServeRefused``. Writes nothing."""
    from . import cell, provision, qualified_runtime, quickstart
    from .auth import read_token
    from .. import readiness
    from ..sqlite_store import MIN_SQLITE

    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    python = python or str(Path(sys.executable).absolute())
    notices: list[str] = []
    if sys.platform.startswith("win"):
        raise ServeRefused("platform_windows_unsupported", "native Windows is not supported (planned for 4.2.0)")
    if container_listen_all:
        from ..http_security import in_container
        if not (in_container() if container is None else container):
            raise ServeRefused("container_listen_outside_container", "--container-listen-all is accepted only inside "
                               "a container (/.dockerenv or /run/.containerenv); the loopback rule is unchanged here")
    detected = cell.detect() if detected is None else detected
    installed = quickstart._installed_profile(settings, home)
    effective = profile or installed
    if effective is None:
        raise ServeRefused("setup_profile_required", "pass --profile lexical or --profile semantic; no default")
    if installed and profile and installed != profile:
        raise ServeRefused("setup_profile_conflict", f"this installation is {installed}; --profile {profile} refused")
    unqualified = not detected.get("retained")
    if unqualified:
        if not allow_unqualified:
            raise ServeRefused("runtime_unqualified", f"{detected.get('refusal') or 'unretained platform cell'}; "
                               "add --allow-unqualified-platform to run the lexical profile in the foreground anyway")
        if effective != "lexical":
            raise ServeRefused("platform_unqualified_lexical_only",
                               "--allow-unqualified-platform accepts the lexical profile only")
        if not detected.get("sqlite_ok"):
            raise ServeRefused("sqlite_too_old", f"SQLite >= {'.'.join(map(str, MIN_SQLITE))} is required, even on "
                               f"an unqualified platform (loaded: {detected.get('sqlite_version')})")
        if sys.version_info < (3, 11):
            raise ServeRefused("python_too_old", "Python >= 3.11 is required")
        if detected.get("implementation") != "cpython":
            raise ServeRefused("python_not_cpython", "CPython is required (sqlite3 extension and locks)")
        notices.append(UNQUALIFIED_WARNING.format(label=detected.get("label") or "unknown"))
    else:
        acceptance = cell.acceptance(detected, profile=effective)
        if not acceptance["ok"]:
            raise ServeRefused(acceptance["code"], acceptance["message"])
        runtime = qualified_runtime.inspect_current(profile=effective, extras=("ops", "brief"),
                                                    cell_id=detected["cell_id"])
        if not runtime["ok"]:
            raise ServeRefused("runtime_unqualified", "installed package versions differ from the cell lock: "
                               + ", ".join(sorted(runtime.get("mismatches") or {})))
        identity = readiness.build_identity()
        if identity.get("identity_source") != "embedded_descriptor" or not identity.get("commit") \
                or identity.get("source_dirty"):
            raise ServeRefused("build_unqualified", "install a qualified wheel; source/env identity is insufficient")
    try:
        token = read_token(settings.token_file)
    except (OSError, ValueError) as exc:
        raise ServeRefused("token_unsafe", f"{settings.token_file}: {exc}") from None
    if token is None:
        raise ServeRefused("token_missing", f"no token file at {settings.token_file}; create it first with "
                           "`memory-os-admin token create` (serve never creates one)")
    resources = provision.plan(resources=("cl100k",)) if effective == "lexical" else provision.plan()
    if not resources["ready"]:
        raise ServeRefused("resources_missing", "provision the resources of the profile first "
                           "(`memory-os-admin provision`); serve downloads nothing")
    try:
        service = quickstart._service_plan(settings, home, python, resources["env"], backend=None,
                                           profile=effective, manager=None)
    except quickstart.QuickstartError as exc:
        raise ServeRefused(getattr(exc, "code", None) or "serve_refused", str(exc)) from None
    env = {key: value for key, value in environ.items() if not key.startswith("MEMORY_V4_")}
    env.update(service["env"])
    argv = list(service["argv"])
    if unqualified:
        env[UNQUALIFIED_ENV] = "1"
    if container_listen_all:
        # D8: listen on every interface of the CONTAINER; clients still use the loopback URL, the host publishes
        # 127.0.0.1:<same port>, Host/Origin stay restricted to loopback authorities, the token file stays 0600.
        from ..http_security import CONTAINER_LISTEN_ENV
        env[CONTAINER_LISTEN_ENV] = "1"
        argv[argv.index("--host") + 1] = "0.0.0.0"
        notices.append("container: listening on 0.0.0.0 inside the container only; publish it on the host as "
                       f"127.0.0.1:{settings.port}:{settings.port} (Host/Origin stay restricted to loopback)")
    return {"ok": True, "profile": effective, "cell": detected.get("cell_id"), "cell_label": detected.get("label"),
            "unqualified": unqualified, "argv": argv, "env": env,
            "server_env": {k: env[k] for k in (*service["env"], *([UNQUALIFIED_ENV] if unqualified else []),
                                               *(["MEMORY_V4_CONTAINER_LISTEN_ALL"] if container_listen_all else []))},
            "notices": notices}


def main(args, settings) -> int:
    try:
        result = plan(settings, profile=args.profile, allow_unqualified=args.allow_unqualified_platform,
                      container_listen_all=args.container_listen_all)
    except ServeRefused as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}), file=sys.stderr)
        return 2
    for notice in result["notices"]:
        print(notice, file=sys.stderr)
    if args.dry_run:
        # Paths and flags only: the token value is never read into the plan.
        print(json.dumps({key: result[key] for key in ("ok", "profile", "cell", "cell_label", "unqualified",
                                                       "argv", "server_env")}, indent=1, sort_keys=True))
        return 0
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(result["argv"][0], result["argv"], result["env"])   # never returns
    return 1  # pragma: no cover
