#!/usr/bin/env bash
# The demo for the docs site (site/pages/demo.md): an empty VictoriaLogs and
# VictoriaMetrics and the picture renderer for the length of the build, the
# nine characters' events, simulated armory series and real client models,
# the publisher, and the two
# bundles in site/public/demo/ (Astro copies public/ as is).
#
#   site/scripts/build-demo.sh [--mcp-session] [YYYY-MM-DD]
#
# YYYY-MM-DD: the character's last evening (default: yesterday, UTC).
# --mcp-session: also record site/pages/mcp-session.md against the same
# store (site/scripts/mcp_session.py); commit the result.
#
# VictoriaLogs: VL_BIN (a victoria-logs-prod binary) if set, else Docker with
# the image the compose kit pins (deploy/compose/compose.yaml), so the demo
# runs the store the product ships with. VictoriaMetrics the same way (VM_BIN,
# else the compose kit's image): vm-wow, the Battle.net exporter's store. The
# renderer is `wow-stats render` with Playwright's Chromium (the `render`
# extra; `uv run --extra render playwright install chromium` once). All three
# listen on 127.0.0.1 only and their data goes with the temporary folder.
set -euo pipefail

session=0
if [[ ${1:-} == --mcp-session ]]; then session=1; shift; fi

repo=$(cd "$(dirname "$0")/../.." && pwd)
out=${DEMO_OUT:-$repo/site/public/demo}
port=${DEMO_VL_PORT:-19460}
vm_port=${DEMO_VM_PORT:-19461}
render_port=${DEMO_RENDER_PORT:-19462}
state=$(mktemp -d)
cleanup() {
  [[ -n ${vl_pid:-} ]] && kill "$vl_pid" 2>/dev/null || true
  [[ -n ${vl_ctr:-} ]] && docker rm -f "$vl_ctr" >/dev/null 2>&1 || true
  [[ -n ${vm_pid:-} ]] && kill "$vm_pid" 2>/dev/null || true
  [[ -n ${vm_ctr:-} ]] && docker rm -f "$vm_ctr" >/dev/null 2>&1 || true
  [[ -n ${render_pid:-} ]] && kill "$render_pid" 2>/dev/null || true
  # The demo is built by then: a leftover temporary folder is a warning,
  # not a failed build.
  rm -rf "$state" || echo "build-demo.sh: couldn't remove $state" >&2
}
trap cleanup EXIT

cd "$repo"
# Resolve the public archive before starting Docker. A local build-roster store
# also works offline. Only an explicitly requested test build uses box models.
if [[ ${DEMO_3D_TEST_FIXTURES:-} == 1 ]]; then
  : "${DEMO_OUT:?set DEMO_OUT to the temporary directory for this fixture build}"
elif [[ -z ${DEMO_3D_STORE:-} ]]; then
  DEMO_3D_STORE=$(site/scripts/demo3d-asset.sh "$state/demo3d")
  export DEMO_3D_STORE
elif [[ ! -f "$DEMO_3D_STORE/roster.json" ]]; then
  echo "build-demo.sh: DEMO_3D_STORE needs the roster.json from demo3d.py build-roster: $DEMO_3D_STORE" >&2
  exit 1
fi

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
if [[ -n ${VM_BIN:-} ]]; then
  "$VM_BIN" -storageDataPath="$state/vm" -httpListenAddr="127.0.0.1:$vm_port" -retentionPeriod=100y >"$state/vm.log" 2>&1 &
  vm_pid=$!
else
  vm_image=$(sed -n 's/^ *image: *\(docker\.io\/victoriametrics\/victoria-metrics:[^ ]*\).*/\1/p' "$repo/deploy/compose/compose.yaml" | head -1)
  [[ -n $vm_image ]] || { echo "no victoria-metrics image in deploy/compose/compose.yaml" >&2; exit 1; }
  mkdir -p "$state/vm"
  vm_ctr=$(docker run -d --rm --user "$(id -u):$(id -g)" -p "127.0.0.1:$vm_port:8428" -v "$state/vm:/vm" "$vm_image" \
    -storageDataPath=/vm -retentionPeriod=100y)
fi

# The renderer's token: a file the publisher reads too (RENDER_TOKEN_FILE).
head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' >"$state/render.token"
BIND=127.0.0.1 PORT=$render_port RENDER_TOKEN_FILE="$state/render.token" RENDER_CACHE_DIR="$state/render" \
  uv run --frozen --extra render wow-stats render >"$state/render.log" 2>&1 &
render_pid=$!

up() { for _ in $(seq 1 90); do curl -sf "$1" >/dev/null && return 0; sleep 1; done; return 1; }
up "http://127.0.0.1:$port/health" || { echo "VictoriaLogs didn't start" >&2; exit 1; }
up "http://127.0.0.1:$vm_port/health" || { echo "VictoriaMetrics didn't start" >&2; exit 1; }
up "http://127.0.0.1:$render_port/healthz" || { echo "the renderer didn't start:" >&2; cat "$state/render.log" >&2; exit 1; }

VM_URL="http://127.0.0.1:$vm_port" RENDER_URL="http://127.0.0.1:$render_port" RENDER_TOKEN_FILE="$state/render.token" \
  uv run --frozen --extra publish --extra render python site/scripts/demo.py "http://127.0.0.1:$port" "$out" "$@"
if [[ $session == 1 ]]; then
  VM_URL="http://127.0.0.1:$vm_port" \
    uv run --frozen --extra publish --extra mcp python site/scripts/mcp_session.py "http://127.0.0.1:$port"
fi
