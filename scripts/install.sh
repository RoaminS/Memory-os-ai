#!/bin/sh
# Truncation guard: until the last line of this file has run main, any exit is a failure (a partial download of
# this script never runs anything and never exits 0).
trap 'printf "%s\n" "memory-os-ai install: this script is incomplete (truncated download); nothing was run" >&2; exit 1' EXIT
#
# Memory OS AI: one-command install (4.1.3), published as a Release asset next to memory_os_bootstrap.py and
# SHA256SUMS. Run it so that a failed download is a failure (pipefail), with curl ignoring any curlrc (-q) and
# speaking HTTPS only, redirects included:
#
#   bash -o pipefail -c "curl -q -fsSL --proto '=https' --proto-redir '=https' <release-asset-url>/install.sh | sh"
#   (options for the bootstrap go after "sh -s --", for example: ... | sh -s -- --clients codex,cursor)
#
# WRITES BEFORE THE CONFIRMATION: nothing is written in $HOME, in the store or in any client configuration, and no
# service is installed, before your "y". The only writes before it are the two verified files (memory_os_bootstrap.py
# and SHA256SUMS of this release) in a private temporary directory (mktemp -d), removed at exit.
#
# What it does, in order:
#   1. checks its prerequisites: curl, a SHA-256 tool (sha256sum or shasum), python3, and uv >= 0.13.9, which it
#      NEVER downloads (when uv is absent it says how to install it and exits, before any download or write);
#   2. chooses the interpreter route unless --python or --cpython is given: on Linux (Ubuntu 24.04 arm64, whose
#      system Python loads a SQLite below the minimum) the standalone CPython 3.11; on macOS the python3 found when it
#      is of a retained cell (3.11, 3.12 or 3.13 with SQLite >= 3.51.3), otherwise the standalone CPython 3.13. A
#      standalone CPython is announced in the preview with its source and size, and downloaded only after "y";
#   3. downloads memory_os_bootstrap.py and SHA256SUMS of THIS release (the tag below, never "latest", so the script,
#      the bootstrap and the checksums always belong together) into that temporary directory; a file left by an
#      earlier attempt is never used;
#   4. refuses on any download failure, a missing or repeated checksum line, or a SHA-256 mismatch: the bootstrap is
#      then never run;
#   5. runs the verified bootstrap with --interactive and --uv "$(command -v uv)". The bootstrap shows the complete
#      preview and asks ONE question on /dev/tty (stdin is this script); without a terminal it stops after the
#      preview.
#
# Declared limits: this script itself is fetched over HTTPS without a checksum (the manual route of the README checks
# every file); SHA256SUMS comes from the same release and is not signed.
# There is no override of the download location or of the protocols: tests rewrite the RELEASE_BASE and CURL_PROTO
# lines of a copy.

END_MARKER="--memory-os-install-script-complete"

main() {
  # The last argument must be the end marker written on the last line of this file: a truncated last line is refused.
  if [ "$#" -lt 1 ]; then exit 1; fi
  eval "LAST=\${$#}"
  [ "$LAST" = "$END_MARKER" ] || exit 1
  COUNT=$#
  INDEX=0
  for ARGUMENT do
    INDEX=$((INDEX + 1))
    [ "$INDEX" -eq 1 ] && set --
    [ "$INDEX" -lt "$COUNT" ] && set -- "$@" "$ARGUMENT"
  done
  trap - EXIT

  RELEASE_TAG="v4.1.3"
  RELEASE_BASE="https://github.com/RoaminS/Memory-os-ai/releases/download/$RELEASE_TAG"
  CURL_PROTO="=https"
  UV_MINIMUM="0.13.9"
  CELL_PROBE='import sqlite3, sys; print("memory-os-cell:" + ("retained" if sys.version_info[:2] in ((3, 11), (3, 12), (3, 13)) and sqlite3.sqlite_version_info >= (3, 51, 3) else "other"))'

  say() { printf '%s\n' "$*"; }
  refuse() { printf 'memory-os-ai install: %s\n' "$*" >&2; exit 1; }

  say "Memory OS AI $RELEASE_TAG: one-command install. Nothing is written in your home, store or client configuration,"
  say "and no service is installed, before your answer to its single question; the only writes before it are the"
  say "bootstrap and SHA256SUMS of this release, verified, in a private temporary directory removed at exit."

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
  for CANDIDATE in python3.13 python3.12 python3.11 python3; do
    FOUND=$(command -v "$CANDIDATE" 2>/dev/null || true)
    case "$FOUND" in /*) PY=$FOUND; break ;; esac
  done
  [ -n "$PY" ] || refuse "python3 is required to run the bootstrap (standard library only); nothing downloaded"

  ROUTE_GIVEN=no
  for ARGUMENT in "$@"; do
    case "$ARGUMENT" in --python|--python=*|--cpython|--cpython=*) ROUTE_GIVEN=yes ;; esac
  done
  if [ "$ROUTE_GIVEN" = no ]; then
    case "$(uname -s 2>/dev/null)" in
      Linux)
        set -- --cpython 3.11 "$@"
        say "Route: the standalone CPython 3.11 named for Ubuntu 24.04 (announced with its size; downloaded only after y)."
        ;;
      Darwin)
        if [ "$("$PY" -I -c "$CELL_PROBE" 2>/dev/null)" = "memory-os-cell:retained" ]; then
          say "Route: $PY (a Python of a retained cell, SQLite >= 3.51.3)."
        else
          set -- --cpython 3.13 "$@"
          say "Route: $PY is not a Python of a retained cell (version or SQLite): the standalone CPython 3.13"
          say "(announced with its source and size; downloaded only after y)."
        fi
        ;;
    esac
  fi

  WORK=$(mktemp -d "${TMPDIR:-/tmp}/memory-os-install.XXXXXX") || refuse "cannot create a temporary directory"
  case "$WORK" in /*) ;; *) refuse "unexpected temporary directory '$WORK'" ;; esac
  trap 'rm -f "$WORK/memory_os_bootstrap.py" "$WORK/SHA256SUMS"; rmdir "$WORK" 2>/dev/null || true' EXIT
  trap 'exit 130' INT TERM HUP

  for NAME in memory_os_bootstrap.py SHA256SUMS; do
    curl -q -fsSL --proto "$CURL_PROTO" --proto-redir "$CURL_PROTO" -o "$WORK/$NAME" "$RELEASE_BASE/$NAME" \
      || refuse "download of $NAME from $RELEASE_BASE failed; nothing was run (the temporary directory is removed)"
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

main "$@" "--memory-os-install-script-complete"
