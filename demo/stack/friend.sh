# A friend through the API instead of the wizard (`demo/lab friend`, and the
# tour takes in record.sh): picks the name, gets an install command, runs the
# Mac installer in a throwaway HOME with a fake WoW folder (launchd left
# alone), then sends the evenings. Sourced; $state and $here set.
source "$here/stack/env.sh"

# friend <dark|light> <first|all>
friend() {
  friend_of "$1"
  local home="$state/home-$1" wow="$state/wow-$1/_forever_" api="http://127.0.0.1:$port/api/me"
  mkdir -p "$home" "$wow/WTF/Account/DEMO/SavedVariables" "$wow/Logs" "$wow/Interface/AddOns"
  if ! curl -sf "$api" | grep -q "\"slug\": *\"$name\""; then
    curl -sf -H 'Content-Type: application/json' -d "{\"slug\":\"$name\"}" "$api/slug" >/dev/null
  fi
  if [[ ! -f "$home/.config/wow-stats/config.toml" ]]; then
    local url
    url=$(curl -sf -H 'Content-Type: application/json' -d '{}' "$api/install-code" \
      | uv run --no-project python -c 'import json,sys; print(json.load(sys.stdin)["mac"].split()[2])')
    HOME="$home" WOW_STATS_WOW_DIR="$wow" WOW_STATS_NO_LOAD=1 bash -c "curl -fsSL '$url' | bash" >/dev/null
  fi
  uv run --no-project python "$here/stack/seed_friend.py" "$home/.config/wow-stats/config.toml" "$char" "$2"
}
