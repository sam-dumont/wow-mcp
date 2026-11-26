# The demo lab's ports and shared helpers (sourced by stack/up.sh, lab and
# record.sh; $state and $here set by the caller).
APP=19470 VL=19471 PROXY_DARK=19472 PROXY_LIGHT=19473 IDP=19474 MCP=19475 SHARE=19476 SITE=19477 OWNER_PROXY=19478
BASE=http://localhost:$PROXY_DARK
repo=$(cd "$here/.." && pwd)

# The friends' sites, built from what the store holds now: the publisher's
# own code, with this lab's throwaway keys.
publish_sites() {
  (cd "$repo" && env USERS_URL=http://127.0.0.1:$SHARE/users.json ANALYSIS_SHARE_URL=http://127.0.0.1:$SHARE/analysis.json \
    ANALYSIS_SHARE_TOKEN_FILE="$state/share-token" \
    PUBLISH_DIR="$state/site" FRIENDS_VL_URL=http://127.0.0.1:$VL VL_URL=http://127.0.0.1:$VL \
    ZOGZOG_MASTER_KEY_FILE="$state/master.key" ZOGZOG_KEY_FILE="$state/owner-zogzog.key" \
    HELP_KEY_FILE="$state/help.key" HELP_MCP_URL=$BASE/mcp MODEL3D=off \
    uv run --frozen --extra publish --extra mcp wow-stats publish --force) >>"$state/publish.log" 2>&1
}

# The proxy port that logs in as this mode's friend, the name the friends
# list gives them (stack/up.sh) and their main: the docs site's decoy
# character in both themes (tools/fixtures/generate.py --demo).
friend_of() {
  case "$1" in
    dark) port=$PROXY_DARK name=frostbolt ;;
    light) port=$PROXY_LIGHT name=moonfire ;;
    *) echo "demo mode must be dark or light" >&2; return 1 ;;
  esac
  char=Nibbet-Gearholt
}
