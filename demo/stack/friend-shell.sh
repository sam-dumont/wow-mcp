# Sourced before VHS starts, again inside its terminal (login shells can
# reorder PATH), and before the non-interactive demo/lab installer.
shim=${DEMO_FRIEND_SHELL%/*}/shims
export PATH="$shim:$PATH"
[[ -x "$shim/launchctl" && $(command -v launchctl) == "$shim/launchctl" &&
   $WOW_STATS_CONFIG == "$HOME/.config/wow-stats/config.toml" &&
   -d "$WOW_STATS_WOW_DIR/WTF" ]] || { echo "invalid demo installer environment" >&2; exit 1; }
cd "$HOME" || exit 1
