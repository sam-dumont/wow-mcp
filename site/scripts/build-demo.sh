#!/usr/bin/env bash
# The demo for the docs site (site/pages/demo.md): an empty VictoriaLogs for
# the length of the build, the invented character's events, the publisher,
# and the two bundles in site/public/demo/ (Astro copies public/ as is).
#
#   site/scripts/build-demo.sh [--mcp-session] [YYYY-MM-DD]
#
# YYYY-MM-DD: the character's last evening (default: yesterday, UTC).
# --mcp-session: also record site/pages/mcp-session.md against the same
# store (site/scripts/mcp_session.py); commit the result.
#
# VictoriaLogs: VL_BIN (a victoria-logs-prod binary) if set, else Docker with
# the image the compose kit pins (deploy/compose/compose.yaml), so the demo
# runs the store the product ships with. It listens on 127.0.0.1 only and
# its data goes with the temporary folder.
set -euo pipefail

session=0
if [[ ${1:-} == --mcp-session ]]; then session=1; shift; fi

repo=$(cd "$(dirname "$0")/../.." && pwd)
out="$repo/site/public/demo"
port=${DEMO_VL_PORT:-19460}
state=$(mktemp -d)
cleanup() {
  [[ -n ${vl_pid:-} ]] && kill "$vl_pid" 2>/dev/null || true
  [[ -n ${vl_ctr:-} ]] && docker rm -f "$vl_ctr" >/dev/null 2>&1 || true
  # The demo is built by then: a leftover temporary folder is a warning,
  # not a failed build.
  rm -rf "$state" || echo "build-demo.sh: couldn't remove $state" >&2
}
trap cleanup EXIT

if [[ -n ${VL_BIN:-} ]]; then
  "$VL_BIN" -storageDataPath="$state/vl" -httpListenAddr="127.0.0.1:$port" -retentionPeriod=100y >"$state/vl.log" 2>&1 &
  vl_pid=$!
else
  image=$(sed -n 's/^ *image: *\(docker\.io\/victoriametrics\/victoria-logs:[^ ]*\).*/\1/p' "$repo/deploy/compose/compose.yaml" | head -1)
  [[ -n $image ]] || { echo "no victoria-logs image in deploy/compose/compose.yaml" >&2; exit 1; }
  # The data on the host's temporary folder, not in the container: a full
  # Docker disk turns VictoriaLogs read-only (it refuses every insert).
  # It runs as our own user: as the image's root, on Linux, it left files
  # the cleanup couldn't remove (Permission denied failed the pages build).
  mkdir -p "$state/vl"
  vl_ctr=$(docker run -d --rm --user "$(id -u):$(id -g)" -p "127.0.0.1:$port:9428" -v "$state/vl:/vl" "$image" \
    -storageDataPath=/vl -retentionPeriod=100y)
fi
for _ in $(seq 1 60); do curl -sf "http://127.0.0.1:$port/health" >/dev/null && break; sleep 1; done
curl -sf "http://127.0.0.1:$port/health" >/dev/null || { echo "VictoriaLogs didn't start" >&2; exit 1; }

cd "$repo"
uv run --frozen --extra publish python site/scripts/demo.py "http://127.0.0.1:$port" "$out" "$@"
if [[ $session == 1 ]]; then
  uv run --frozen --extra publish --extra mcp python site/scripts/mcp_session.py "http://127.0.0.1:$port"
fi
