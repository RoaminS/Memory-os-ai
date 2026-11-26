# Memory OS AI v4: minimal lexical image (4.1.3, Lot 3, decision D8). NOT published without a distinct GO.
#
# Build from a qualified wheel of the same commit (placed in dist/ by the CI job that builds it):
#   docker build --build-arg WHEEL=memory_os_ai-<version>-py3-none-any.whl -t memory-os-ai:local .
# Run (token file 0600 owned by uid 10001 on the host; same port on both sides, published on loopback ONLY). The
# container listens on its own loopback by default; listening on 0.0.0.0 INSIDE it is the explicit opt-in that you
# append to the command (decision D8: never by default):
#   docker run --rm -p 127.0.0.1:8765:8765 -v memory-os-data:/data \
#     -v /path/to/http.token:/run/memory-os/http.token:ro memory-os-ai:local --container-listen-all
#
# * Lexical profile only, no ML stack, no model download; the cl100k_base table ships in the wheel.
# * CPython 3.11.18 standalone through uv (its SQLite is read back by `serve`, >= 3.51.3 required); the system
#   Python of Ubuntu 24.04 (SQLite 3.45.1) is never used.
# * The token comes from a mounted FILE (mode 0600), never from an environment variable; the image holds no secret.
# * Inside the container the service listens on 0.0.0.0 only behind the explicit `--container-listen-all` opt-in
#   given at `docker run`; Host/Origin stay restricted to the loopback authorities of the port, hence the same port.

ARG UV_VERSION=0.13.9
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM ubuntu:24.04
ARG WHEEL
ARG PYTHON_VERSION=3.11.18
ENV UV_PYTHON_INSTALL_DIR=/opt/memory-os/python \
    UV_NO_CACHE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=uv /uv /usr/local/bin/uv
COPY dist/${WHEEL} /tmp/dist/${WHEEL}
RUN test -n "${WHEEL}" \
 && uv python install "${PYTHON_VERSION}" \
 && uv venv --python "${PYTHON_VERSION}" /opt/memory-os/venv \
 && uv pip install --python /opt/memory-os/venv/bin/python --no-deps "/tmp/dist/${WHEEL}" \
 && uv pip install --python /opt/memory-os/venv/bin/python --only-binary :all: \
      -c "$(/opt/memory-os/venv/bin/memory-os-constraints lexical)" "memory-os-ai[ops,brief] @ file:///tmp/dist/${WHEEL}" \
 && rm -rf /tmp/dist \
 && /opt/memory-os/venv/bin/python -c "import sqlite3,sys; v=tuple(map(int,sqlite3.sqlite_version.split('.'))); sys.exit(0 if v>=(3,51,3) else 'SQLite too old: '+sqlite3.sqlite_version)" \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin memoryos \
 && mkdir -p /data /run/memory-os && chown memoryos:memoryos /data

USER 10001:10001
ENV HOME=/home/memoryos \
    MEMORY_V4_DIR=/data/store \
    MEMORY_V4_OPS_DIR=/data/ops \
    MEMORY_V4_TOKEN_FILE=/run/memory-os/http.token \
    MEMORY_V4_URL=http://127.0.0.1:8765/mcp/ \
    MEMORY_V4_SERVICE_LABEL=local.memory-os-v4-container
VOLUME ["/data"]
EXPOSE 8765

# The product's own authenticated client (no proxy, no redirect: the token never leaves the container loopback).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD \
  ["/opt/memory-os/venv/bin/python", "-I", "-c", "import sys; from memory_os_v4.ops.auth import http_get; from memory_os_v4.ops.settings import OpsSettings; s = OpsSettings.from_env(); sys.exit(0 if http_get(s.ready_url, s, timeout=4)[0] == 200 else 1)"]

ENTRYPOINT ["/opt/memory-os/venv/bin/memory-os-admin", "serve", "--profile", "lexical"]
