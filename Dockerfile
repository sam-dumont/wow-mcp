# One image, wow-mcp in the registry (docs/deploy.md, "The container"),
# plus the renderer's (target renderer, wow-mcp-render; names in release.yml).
# No model runs in it: the play analysis, plans and coaching run in the
# user's own MCP client (docs/mcp.md). Four modes: `wow-stats ui` (the default),
# `exporter`, `publish` and `static`. The runtime is the standard library plus cryptography
# (publish's sealed bundle), the MCP server and DuckDB with its semantic
# layer (the analysis' queries); uv builds the venv in a first
# stage and the second stage copies it, nothing else. Runs as uid 10001 with
# a read-only root filesystem: bytecode is compiled at build time and the
# processes write nothing (HOME=/tmp for whatever insists).

# The images' OCI labels. SOURCE_URL is wowstats.SOURCE_URL (GHCR links the
# package to that repository); release.yml passes all three.
ARG SOURCE_URL=https://github.com/sam-dumont/wow-mcp
ARG VERSION=dev
ARG REVISION=unknown

# The bases, pinned by tag and digest on literal FROM lines so Dependabot can
# read and bump them (it doesn't follow ARGs).
FROM ghcr.io/astral-sh/uv:0.13@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv
# The main image on Docker Hardened Images' Python since 0.7 (Debian 13,
# Apache 2.0): glibc, which DuckDB needs (wowstats/analysis/duck.py: its
# wheels are manylinux only, none for Alpine's musl since 1.3), and no shell
# or system package manager in the runtime variant. Debian slim, the first
# glibc base, carried 53 HIGH/CRITICAL findings, 47 of them with no Debian
# fix (2026-11-20). Chainguard's image was the other candidate: free only as
# `latest`, with no clear terms for publishing images built on it. dhi.io
# takes no anonymous pulls: `docker login dhi.io` with a free Docker Hub
# account (docs/development.md). The -dev variant (bash, apt) only builds the
# venv; both carry the same python-3.15 package at /usr/bin/python3.15.
FROM dhi.io/python:3.15-debian13-dev@sha256:e52e8a325d11080c04be94b02f9e7df64370ca8a866b5a75fcb56e17bcf11c0d AS build-base
FROM dhi.io/python:3.15-debian13@sha256:76e89381500bad12fdaba7333bca42a96e4f1a72e3bf5ef753bbe5258cc77ac8 AS base
# The renderer stays on Debian slim: Playwright's Chromium wants Debian's
# libraries, pinned below by a snapshot date. The Python minor moves on
# purpose, not with the weekly digest bumps (.github/dependabot.yml).
FROM docker.io/library/python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS render-base

FROM build-base AS builder
USER root
COPY --from=uv /uv /usr/local/bin/uv
# The venv's python links to /usr/bin/python3.15, which the runtime base has too.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/bin/python3.15
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY wowstats ./wowstats
COPY web ./web
COPY data/route-reference.json ./data/route-reference.json
COPY data/gear-reference.json ./data/gear-reference.json
COPY data/foreverdb-weights.json ./data/foreverdb-weights.json
COPY data/kb-reference.json.gz ./data/kb-reference.json.gz
COPY data/kb ./data/kb
COPY data/forever-reference.json ./data/forever-reference.json
COPY data/trainers-reference.json ./data/trainers-reference.json
COPY data/class-reference.json ./data/class-reference.json
COPY data/dbd ./data/dbd
COPY data/wowsims-talents.json ./data/wowsims-talents.json
COPY data/battlenet-look.json ./data/battlenet-look.json
COPY data/dungeons ./data/dungeons
COPY THIRD_PARTY.md ./THIRD_PARTY.md
COPY shipper ./shipper
COPY docs/videos/steps.json ./docs/videos/steps.json
COPY docs/videos/media ./docs/videos/media
RUN uv sync --frozen --no-dev --no-editable --extra publish --extra mcp --extra analysis --extra analysis-openai && chmod -R a+rX,go-w /app/.venv \
    && mkdir -p /empty/data

# The renderer (`wow-stats render`, wowstats/renderer.py): a past level's
# picture, drawn by the 3D view's own viewer in a headless Chromium, in a
# pod that holds no key and no store. Its own target and image
# (wow-mcp-render), so Chromium and its libraries never
# reach the main image above. Debian (Playwright's
# Chromium is a glibc build). Only the headless shell and the libraries it links (no fonts,
# no X server: the picture has no text). Pinned: the base by digest, Chromium
# by the Playwright version in uv.lock (its browsers.json names the build),
# its libraries from Debian's snapshot archive at one date, so a rebuild
# installs the same packages. Build it with `--target renderer`; the default build is the
# runtime stage, last in this file.
FROM render-base AS render-builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY wowstats ./wowstats
COPY web ./web
COPY data/route-reference.json ./data/route-reference.json
COPY data/gear-reference.json ./data/gear-reference.json
COPY data/foreverdb-weights.json ./data/foreverdb-weights.json
COPY data/kb-reference.json.gz ./data/kb-reference.json.gz
COPY data/kb ./data/kb
COPY data/forever-reference.json ./data/forever-reference.json
COPY data/trainers-reference.json ./data/trainers-reference.json
COPY data/class-reference.json ./data/class-reference.json
COPY data/dbd ./data/dbd
COPY data/wowsims-talents.json ./data/wowsims-talents.json
COPY data/battlenet-look.json ./data/battlenet-look.json
COPY data/dungeons ./data/dungeons
COPY THIRD_PARTY.md ./THIRD_PARTY.md
COPY shipper ./shipper
COPY docs/videos/steps.json ./docs/videos/steps.json
# The wheel force-includes the welcome clips, which the renderer never serves: an empty directory keeps
# the build passing without 24 MB in the image. The semantic layer is a core dependency (the pages'
# numbers, pyproject.toml) that the renderer never imports: DuckDB, the layer and what only they pull in
# stay out (tests/test_docker_context.py checks the list against uv.lock).
RUN mkdir -p docs/videos/media \
 && uv sync --frozen --no-dev --no-editable --extra render \
      --no-install-package duckdb --no-install-package boring-semantic-layer --no-install-package ibis-framework \
      --no-install-package sqlglot --no-install-package atpublic --no-install-package attrs \
      --no-install-package parsy --no-install-package returns --no-install-package toolz \
      --no-install-package pyyaml --no-install-package python-dateutil \
      --no-install-package tzdata --no-install-package packaging --no-install-package typing-extensions \
 && chmod -R a+rX,go-w /app/.venv

FROM render-base AS renderer
ARG DEBIAN_SNAPSHOT=20261115T000000Z
ARG SOURCE_URL VERSION REVISION
LABEL org.opencontainers.image.title="wow-mcp-render" \
      org.opencontainers.image.description="wow-stats renderer: past levels' pictures in a headless Chromium" \
      org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="GPL-3.0-only" org.opencontainers.image.source="${SOURCE_URL}"
COPY --from=render-builder /app/.venv /app/.venv
# The GPL's text and the third-party notices, beside the program (the wheel
# carries them too, under wowstats/data/).
COPY --from=render-builder /app/LICENSE /app/THIRD_PARTY.md /app/
ENV PATH=/app/.venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    BIND=0.0.0.0 \
    PORT=8083 \
    RENDER_CACHE_DIR=/tmp/wow-stats-render
RUN sed -i -e "s|http://deb.debian.org/debian-security|http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|" \
           -e "s|http://deb.debian.org/debian|http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" \
           /etc/apt/sources.list.d/debian.sources \
    && echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0t64 libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 \
       libatspi2.0-0t64 libdbus-1-3 libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
       libxrandr2 libxkbcommon0 libgbm1 libasound2t64 \
    && playwright install --only-shell chromium \
    && rm -rf /opt/playwright/ffmpeg-* /var/lib/apt/lists/* /var/cache/apt/* \
    && chmod -R a+rX /opt/playwright
USER 10001:10001
EXPOSE 8083
# docker ps and compose read this; Kubernetes uses its own probes. The mode
# picks the port (wowstats/healthcheck.py); exec form, no shell.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-m", "wowstats.healthcheck"]
ENTRYPOINT ["wow-stats"]
CMD ["render"]

FROM base AS runtime
ARG SOURCE_URL VERSION REVISION
LABEL org.opencontainers.image.title="wow-mcp" \
      org.opencontainers.image.description="wow-stats: a WoW Forever leveling log, its web page and its MCP server" \
      org.opencontainers.image.version="${VERSION}" org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="GPL-3.0-only" org.opencontainers.image.source="${SOURCE_URL}"
COPY --from=builder /app/.venv /app/.venv
# The GPL's text and the third-party notices, beside the program (the wheel
# carries them too, under wowstats/data/).
COPY --from=builder /app/LICENSE /app/THIRD_PARTY.md /app/
# /data owned by the runtime user: a fresh named volume mounted there takes
# this ownership, so `publish` can write its builds and key into it.
COPY --from=builder --chown=10001:10001 /empty/data /data
# The same for the SQLite event store's folder (EVENTS_STORE=sqlite:/events,
# deploy/compose/compose.sqlite.yaml): its volume is shared by ui and publish.
COPY --from=builder --chown=10001:10001 /empty/data /events
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    BIND=0.0.0.0 \
    PORT=8080 \
    MCP_PORT=8090 \
    FASTMCP_CHECK_FOR_UPDATES=off
# A container needs a non-loopback bind to be reachable at all, so VIEW and
# AUTH have no defaults here: the server refuses to start until both are set.
USER 10001:10001
# Checked on the runtime base itself, as the runtime user, in exec form (the
# base has no shell): fail the build, not the first nightly run. The Python
# minor, SQLite's FTS5 for the knowledge base, the IANA zones for
# WOW_TIMEZONE, DuckDB and the MCP server's imports.
RUN ["python", "-c", "import sys, sqlite3, zoneinfo, duckdb, wowstats.mcp.server; assert sys.version_info[:2] == (3, 15), sys.version; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(a)'); zoneinfo.ZoneInfo('Europe/Brussels')"]
# 8090: the MCP endpoint (FastMCP), only when MCP_PUBLIC_URL is set.
EXPOSE 8080 8090 9464
# docker ps and compose read this; Kubernetes uses its own probes. The mode
# picks the port (wowstats/healthcheck.py); exec form, no shell.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-m", "wowstats.healthcheck"]
ENTRYPOINT ["wow-stats"]
CMD ["ui"]
