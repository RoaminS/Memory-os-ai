#!/bin/sh
# Memory OS AI: one-command install (4.1.3), published as a Release asset next to memory_os_bootstrap.py and
# SHA256SUMS, and run as:
#
#     curl -fsSL https://github.com/RoaminS/Memory-os-ai/releases/download/<tag>/install.sh | sh
#     curl -fsSL .../install.sh | sh -s -- --cpython 3.13        # options are passed on to the bootstrap
#
# What it does, in order (POSIX sh; nothing is written before the bootstrap's single [y/N] confirmation):
#   1. checks its prerequisites: curl, a SHA-256 tool (sha256sum or shasum), python3, and uv >= 0.13.9, which it
#      NEVER downloads (when uv is absent it says how to install it and exits);
#   2. downloads memory_os_bootstrap.py and SHA256SUMS of THIS release (the tag below, never "latest", so the script,
#      the bootstrap and the checksums always belong together) into a fresh private temporary directory, removed at
#      exit; a file left by an earlier attempt is never used;
#   3. refuses on any download failure, a missing or repeated checksum line, or a SHA-256 mismatch: the bootstrap is
#      then never run;
#   4. runs the verified bootstrap with --interactive and --uv "$(command -v uv)". The bootstrap shows the complete
#      preview and asks ONE question on /dev/tty (stdin is this script); without a terminal it stops after the
#      preview and writes nothing.
#
# Declared limits: this script itself is fetched over HTTPS without a checksum (the manual route of the README checks
# every file); SHA256SUMS comes from the same release and is not signed.
# There is no override of the download location: tests rewrite the RELEASE_BASE line of a copy.

main() {
  RELEASE_TAG="v4.1.3"
  RELEASE_BASE="https://github.com/RoaminS/Memory-os-ai/releases/download/$RELEASE_TAG"
  UV_MINIMUM="0.13.9"

  say() { printf '%s\n' "$*"; }
  refuse() { printf 'memory-os-ai install: %s\n' "$*" >&2; exit 1; }

  say "Memory OS AI $RELEASE_TAG: one-command install (preview first; nothing is written before your confirmation)."

  command -v curl >/dev/null 2>&1 || refuse "curl is required; nothing downloaded, nothing written"
  if command -v sha256sum >/dev/null 2>&1; then
    SHA_TOOL="sha256sum"
  elif command -v shasum >/dev/null 2>&1; then
    SHA_TOOL="shasum"
  else
    refuse "a SHA-256 tool (sha256sum or shasum) is required; nothing downloaded, nothing written"
  fi

  UV=$(command -v uv 2>/dev/null || true)
  case "$UV" in
    /*) ;;
    *)
      printf '%s\n' \
        "memory-os-ai install: uv >= $UV_MINIMUM is required, and this script never downloads it." \
        "  Install uv from its official instructions (https://docs.astral.sh/uv/getting-started/installation/)," \
        "  preferably a release binary checked against its published SHA-256, make sure 'uv' is on your PATH," \
        "  then run this command again. Nothing was downloaded or written." >&2
      exit 1
      ;;
  esac

  PY=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    found=$(command -v "$candidate" 2>/dev/null || true)
    case "$found" in /*) PY=$found; break ;; esac
  done
  [ -n "$PY" ] || refuse "python3 is required to run the bootstrap (standard library only); nothing downloaded"

  # Route of the target interpreter: on Linux (Ubuntu 24.04 arm64), whose system Python loads a SQLite below the
  # minimum, the standalone CPython 3.11 named for the cell (listed in the preview, downloaded only after y). An
  # explicit --python or --cpython given after "sh -s --" is kept as is.
  ROUTE_GIVEN=no
  for argument in "$@"; do
    case "$argument" in --python|--python=*|--cpython|--cpython=*) ROUTE_GIVEN=yes ;; esac
  done
  if [ "$ROUTE_GIVEN" = no ] && [ "$(uname -s 2>/dev/null)" = Linux ]; then
    set -- --cpython 3.11 "$@"
  fi

  WORK=$(mktemp -d "${TMPDIR:-/tmp}/memory-os-install.XXXXXX") || refuse "cannot create a temporary directory"
  case "$WORK" in /*) ;; *) refuse "unexpected temporary directory '$WORK'" ;; esac
  trap 'rm -f "$WORK/memory_os_bootstrap.py" "$WORK/SHA256SUMS"; rmdir "$WORK" 2>/dev/null || true' EXIT
  trap 'exit 130' INT TERM HUP

  for name in memory_os_bootstrap.py SHA256SUMS; do
    curl -fsSL --proto-redir '=https' -o "$WORK/$name" "$RELEASE_BASE/$name" \
      || refuse "download of $name from $RELEASE_BASE failed; nothing was run, nothing written"
  done

  LINES=$(awk '$2 == "memory_os_bootstrap.py" || $2 == "*memory_os_bootstrap.py"' "$WORK/SHA256SUMS" | wc -l | tr -d ' ')
  [ "$LINES" = 1 ] || refuse "SHA256SUMS of $RELEASE_TAG has $LINES lines for memory_os_bootstrap.py (exactly one is required); nothing was run"
  EXPECTED=$(awk '$2 == "memory_os_bootstrap.py" || $2 == "*memory_os_bootstrap.py" { print $1 }' "$WORK/SHA256SUMS")
  case "$EXPECTED" in
    *[!0-9a-f]*|"") refuse "SHA256SUMS holds no valid SHA-256 for memory_os_bootstrap.py; nothing was run" ;;
  esac
  [ "${#EXPECTED}" -eq 64 ] || refuse "SHA256SUMS holds no valid SHA-256 for memory_os_bootstrap.py; nothing was run"
  if [ "$SHA_TOOL" = sha256sum ]; then
    ACTUAL=$(sha256sum "$WORK/memory_os_bootstrap.py" | awk '{ print $1 }')
  else
    ACTUAL=$(shasum -a 256 "$WORK/memory_os_bootstrap.py" | awk '{ print $1 }')
  fi
  [ "$ACTUAL" = "$EXPECTED" ] \
    || refuse "checksum mismatch for memory_os_bootstrap.py (SHA256SUMS of $RELEASE_TAG: $EXPECTED, downloaded: $ACTUAL); nothing was run"
  say "memory_os_bootstrap.py verified: SHA-256 $ACTUAL (SHA256SUMS of $RELEASE_TAG)."

  # stdin is this script under "curl | sh": the bootstrap reads its answer from /dev/tty, never from stdin.
  STATUS=0
  "$PY" -I -B "$WORK/memory_os_bootstrap.py" --interactive --uv "$UV" "$@" </dev/null || STATUS=$?
  exit "$STATUS"
}

main "$@"
