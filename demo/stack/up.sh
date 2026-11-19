#!/usr/bin/env bash
# The demo lab: a throwaway two-person install on this machine, for the
# recordings (demo/record.sh) and for looking around (`demo/lab up`).
#
# Sourced, with $state (an empty folder) and $here (demo/) set: starts
# VictoriaLogs, a stand-in OIDC provider for the MCP, the full view with
# several people, the login proxy's stand-in (one port per identity) and,
# with SITES=1, the publisher's static server. Fills $pids.
#
# Ports: stack/env.sh. The full view is http://localhost:19472 (an http MCP
# address must be loopback); the friends' sites are <name>-wow.localhost:19477:
# browsers, curl and Python resolve *.localhost to this machine by themselves.
# Every port binds 127.0.0.1 and the keys are made
# here and deleted with $state; the characters and players are invented. The
# one call out is the item tables (wago.tools, for names and icons), cached.

: "${VL_BIN:?set VL_BIN to a victoria-logs-prod binary (v1.54.0)}"
source "$here/stack/env.sh"
pids=()

for p in $APP $VL $PROXY_DARK $PROXY_LIGHT $IDP $MCP $SHARE $SITE; do
  if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $p is taken (another demo lab running? demo/lab down)" >&2
    exit 1
  fi
done

key() { uv run --no-project python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode())"; }
key > "$state/master.key"; key > "$state/owner-zogzog.key"; key > "$state/help.key"
openssl rand -hex 32 > "$state/sign.key"
chmod 600 "$state"/*.key
# Who gets in besides the owner (a real install renders it from its identity provider).
echo '{"demo-friend-dark": "frostbolt", "demo-friend-light": "moonfire"}' > "$state/friends.json"
# The MCP logs in through an OpenID Connect provider only: the tests' local
# stand-in plays it (its fixed client secret, never a real one).
(cd "$repo" && uv run --frozen --extra mcp python -c "from tests.mcp import oidc_standin as s; print(s.CLIENT_SECRET)") > "$state/oidc.secret"

"$VL_BIN" -storageDataPath="$state/vl" -httpListenAddr="127.0.0.1:$VL" -retentionPeriod=100y >"$state/vl.log" 2>&1 & pids+=($!)
(cd "$repo" && exec uv run --frozen --extra mcp python -c "import time; from tests.mcp.oidc_standin import Standin
with Standin(user='demo-owner', org='demo-owners', port=$IDP): time.sleep(86400)") >"$state/idp.log" 2>&1 & pids+=($!)
(cd "$repo" && exec env BIND=127.0.0.1 PORT=$APP VIEW=full AUTH=proxy PROXY_TRUSTED_CIDRS=127.0.0.1/32 \
  VL_URL=http://127.0.0.1:$VL FRIENDS_VL_URL=http://127.0.0.1:$VL \
  WOW_STATE_DIR="$state/app" WOW_OWNER_SUB=demo-owner WOW_OWNER_SLUG=owner \
  WOW_FRIENDS_FILE="$state/friends.json" PUBLIC_URL=$BASE SITE_DOMAIN=localhost \
  ZOGZOG_MASTER_KEY_FILE="$state/master.key" MODEL3D=off ANALYSIS_STATE_DIR="$state/analysis" \
  ANALYSIS_SHARE_PORT=$SHARE ANALYSIS_SHARE_TOKEN_FILE="$state/share-token" \
  MCP_PUBLIC_URL=$BASE/mcp MCP_PORT=$MCP OIDC_ISSUER=http://127.0.0.1:$IDP \
  OIDC_CLIENT_ID=wow-app OIDC_CLIENT_SECRET_FILE="$state/oidc.secret" \
  MCP_SIGNING_KEY_FILE="$state/sign.key" MCP_STATE_DIR="$state/mcp" \
  uv run --frozen --extra mcp wow-stats ui) >"$state/app.log" 2>&1 & pids+=($!)
uv run --no-project python "$here/stack/idproxy.py" $APP \
  "$PROXY_DARK:demo-friend-dark:demo-friends" "$PROXY_LIGHT:demo-friend-light:demo-friends" >"$state/proxy.log" 2>&1 & pids+=($!)
if [[ ${SITES:-0} == 1 ]]; then
  # The friends' static sites, served for <slug>-wow.localhost, which
  # browsers resolve to this machine by themselves.
  (cd "$repo" && exec env BIND=127.0.0.1 PORT=$SITE SITE_DIR="$state/site" SITE_DOMAIN=localhost SITE_ALIASES=help.localhost=owner \
    uv run --frozen --extra publish wow-stats static) >"$state/static.log" 2>&1 & pids+=($!)
fi
printf '%s\n' "${pids[@]}" > "$state/pids"

for _ in $(seq 1 90); do
  curl -sf "127.0.0.1:$VL/health" >/dev/null && curl -sf "127.0.0.1:$PROXY_DARK/api/me" >/dev/null && break
  sleep 1
done
curl -sf "127.0.0.1:$PROXY_DARK/api/me" >/dev/null || { echo "the app didn't start: $state/app.log" >&2; tail -20 "$state/app.log" >&2; exit 1; }

