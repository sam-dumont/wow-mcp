# One image, wow-mcp in the registry (docs/deploy.md, "The container"),
# plus the renderer's (target renderer, wow-mcp-render; names in release.yml).
# No model runs in it: the play analysis, plans and coaching run in the
# user's own MCP client (docs/mcp.md). Four modes: `wow-stats ui` (the default),
# `exporter`, `publish` and `static`. The runtime is the standard library only (plus cryptography
# for publish's sealed bundle); uv builds the venv in a first
# stage and the second stage copies it, nothing else. Runs as uid 10001 with
# a read-only root filesystem: bytecode is compiled at build time and the
# processes write nothing (HOME=/tmp for whatever insists).

# The images' OCI labels. SOURCE_URL is wowstats.SOURCE_URL (GHCR links the
# package to that repository); release.yml passes all three.
ARG SOURCE_URL=https://github.com/sam-dumont/wow-mcp
ARG VERSION=dev
ARG REVISION=unknown

# The bases, pinned by tag and digest on literal FROM lines so Dependabot can
# read and bump them (it doesn't follow ARGs). The Python minor moves on
# purpose, not with the weekly digest bumps (.github/dependabot.yml).
FROM ghcr.io/astral-sh/uv:0.13@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv
FROM docker.io/library/python:3.15-alpine@sha256:0f9552bb28711333da16915ec146f5e6bb44a7fdac9ced15dc17c2e0206346dd AS base
# The renderer's base (target renderer, below): glibc, for Playwright's Chromium.
FROM docker.io/library/python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS render-base

FROM base AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PYTHON=/usr/local/bin/python3
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY wowstats ./wowstats
COPY web ./web
COPY data/route-reference.json ./data/route-reference.json
COPY data/gear-reference.json ./data/gear-reference.json
COPY data/kb-reference.json.gz ./data/kb-reference.json.gz
COPY data/kb ./data/kb
COPY data/forever-reference.json ./data/forever-reference.json
COPY data/trainers-reference.json ./data/trainers-reference.json
COPY data/class-reference.json ./data/class-reference.json
COPY THIRD_PARTY.md ./THIRD_PARTY.md
COPY shipper ./shipper
COPY docs/videos/steps.json ./docs/videos/steps.json
COPY docs/videos/media ./docs/videos/media
# The analysis' knowledge base needs SQLite's FTS5: fail the build, not the first nightly run.
RUN uv sync --frozen --no-dev --no-editable --extra publish --extra mcp && chmod -R a+rX,go-w /app/.venv \
    && mkdir -p /empty/data \
    && /app/.venv/bin/python -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(a)')" \
    && FASTMCP_CHECK_FOR_UPDATES=off /app/.venv/bin/python -c "import wowstats.mcp.server"

# The renderer (`wow-stats render`, wowstats/renderer.py): a past level's
# picture, drawn by Wowhead's viewer in a headless Chromium, in a pod that
# holds no key and no store. Its own target and image
# (wow-mcp-render), so Chromium and its libraries never
# reach the main image above. Debian, not Alpine: Playwright's Chromium is a
# glibc build. Only the headless shell and the libraries it links (no fonts,
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
COPY data/kb-reference.json.gz ./data/kb-reference.json.gz
COPY data/kb ./data/kb
COPY data/forever-reference.json ./data/forever-reference.json
COPY data/trainers-reference.json ./data/trainers-reference.json
COPY data/class-reference.json ./data/class-reference.json
COPY THIRD_PARTY.md ./THIRD_PARTY.md
COPY shipper ./shipper
COPY docs/videos/steps.json ./docs/videos/steps.json
# The wheel force-includes the welcome clips, which the renderer never serves: an empty directory keeps
# the build passing without 24 MB in the image.
RUN mkdir -p docs/videos/media \
 && uv sync --frozen --no-dev --no-editable --extra render && chmod -R a+rX,go-w /app/.venv

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
# 8090: the MCP endpoint (FastMCP), only when MCP_PUBLIC_URL is set.
EXPOSE 8080 8090 9464
# docker ps and compose read this; Kubernetes uses its own probes. The mode
# picks the port (wowstats/healthcheck.py); exec form, no shell.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-m", "wowstats.healthcheck"]
ENTRYPOINT ["wow-stats"]
CMD ["ui"]
