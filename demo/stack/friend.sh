# A friend through the API instead of the wizard (`demo/lab friend`, and the
# tour takes in record.sh): its name comes from the friends list (stack/up.sh),
# as a real install's does from its identity provider. Gets an install command, runs the
# Mac installer in a throwaway HOME with a fake WoW folder (launchd left
# alone), then sends the evenings. Sourced; $state and $here set.
source "$here/stack/env.sh"

# Run the installer, VHS and the shipper with the same disposable paths.
# In particular, git, uv and the Polestar client mustn't inherit the
# operator's working tree, cache or data directory.
as_friend() {
  local home=$1 wow=$2 python
  shift 2
  python=$(uv python find '>=3.11') || return 1
  env -i HOME="$home" PATH="$PATH" LANG="${LANG:-en_US.UTF-8}" TERM="${TERM:-xterm-256color}" \
    TMPDIR="$state" WOW_STATS_CONFIG="$home/.config/wow-stats/config.toml" WOW_STATS_WOW_DIR="$wow" \
    NO_REAL_WOW=1 UV_PYTHON="$python" UV_PYTHON_DOWNLOADS=never \
    DEMO_FRIEND_SHELL="$here/stack/friend-shell.sh" \
    /bin/bash -c 'source "$DEMO_FRIEND_SHELL" || exit 1; exec "$@"' bash "$@"
}

# friend <dark|light> <first|all>
friend() {
  friend_of "$1"
  local home="$state/home-$1" wow="$state/wow-$1/_forever_" api="http://127.0.0.1:$port/api/me"
  mkdir -p "$home" "$wow/WTF/Account/DEMO/SavedVariables" "$wow/Logs" "$wow/Interface/AddOns"
  if [[ ! -f "$home/.config/wow-stats/config.toml" ]]; then
    local url
    url=$(curl -sf -H 'Content-Type: application/json' -d '{}' "$api/install-code" \
      | uv run --no-project python -c 'import json,sys; print(json.load(sys.stdin)["mac"].split()[2])')
    as_friend "$home" "$wow" env WOW_STATS_NO_LOAD=1 /bin/bash -o pipefail \
      -c 'curl -fsSL "$1" | /bin/bash' bash "$url" >/dev/null
  fi
  uv run --no-project python "$here/stack/seed_friend.py" "$home/.config/wow-stats/config.toml" "$2"
}
