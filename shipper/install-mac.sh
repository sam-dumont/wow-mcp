#!/bin/bash
# Installs wow-stats for the current macOS user: a launchd agent that runs
# when WoW saves its SavedVariables and every 5 minutes, and Polestar
# Recorder in the AddOns folder (wow_stats.py --install-recorder). No admin
# needed.
# Safe to rerun: after updating wow_stats.py, after a new character's first
# logout (its SavedVariables folder joins the watch list), after editing
# wow_dir in the config.
# WOW_STATS_NO_LOAD=1 writes everything but leaves launchd alone (to try the
# installer under a throwaway HOME without touching the real agent).
# WOW_STATS_QUIET=1 (the one-line installers) prints only the closing
# checklist (wow_stats.py --preflight): what works, and the one fix for each
# thing that doesn't.
set -euo pipefail

LABEL=local.wow-stats
HERE=$(cd "$(dirname "$0")" && pwd)
DEST_DIR="$HOME/.local/share/wow-stats"
SCRIPT="$DEST_DIR/wow_stats.py"
CONF="${WOW_STATS_CONFIG:-$HOME/.config/wow-stats/config.toml}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { [ "${WOW_STATS_QUIET:-0}" = 1 ] || printf '%s\n' "$*"; }

xml_escape() {
  local s=$1
  s=${s//&/&amp;}
  s=${s//</&lt;}
  s=${s//>/&gt;}
  printf '%s' "$s"
}

# Python 3.11+: through uv when it is installed, else python3.
prog=()
for c in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv "$(command -v uv || true)"; do
  if [ -n "$c" ] && [ -x "$c" ]; then prog=("$c" run --quiet --script); break; fi
done
if [ ${#prog[@]} -eq 0 ]; then
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
    if [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      prog=("$c"); break
    fi
  done
fi
[ ${#prog[@]} -gt 0 ] || { echo "needs uv (https://docs.astral.sh/uv/) or Python 3.11+" >&2; exit 1; }

# A copy, so moving or updating the download doesn't break the agent.
mkdir -p "$DEST_DIR" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
install -m 755 "$HERE/wow_stats.py" "$SCRIPT"
# The GPL's text (the one-line installer carries it): the program's license, next to it.
if [ -f "$HERE/LICENSE" ]; then install -m 644 "$HERE/LICENSE" "$DEST_DIR/LICENSE"; fi
say "script: $SCRIPT (runs with: ${prog[*]})"

if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  (umask 077; cp "$HERE/config.example.toml" "$CONF")
  say "config: $CONF created from the example; fill in [ship] to send events"
fi
chmod 600 "$CONF"

WOW=$("${prog[@]}" "$SCRIPT" --print-wow-dir)
# Polestar Recorder (not on CurseForge): the pinned release, checked. A
# failed download doesn't stop the install; it says why on stderr.
if [ "${WOW_STATS_QUIET:-0}" = 1 ]; then
  "${prog[@]}" "$SCRIPT" --install-recorder 2>/dev/null || true  # the checklist says if it's missing
else
  "${prog[@]}" "$SCRIPT" --install-recorder || true
fi
watch=""
shopt -s nullglob
for d in "$WOW"/WTF/Account/*/SavedVariables "$WOW"/WTF/Account/*/*/*/SavedVariables; do
  [ -d "$d" ] || continue
  watch+="    <string>$(xml_escape "$d")</string>"$'\n'
done
shopt -u nullglob
if [ -z "$watch" ]; then
  echo "no SavedVariables folder under $WOW/WTF/Account: log in once in WoW, or set wow_dir in $CONF" >&2
  exit 1
fi

program=""
for a in "${prog[@]}" "$SCRIPT"; do
  program+="    <string>$(xml_escape "$a")</string>"$'\n'
done

tmp=$(mktemp)
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    @WATCH_PATHS@) printf '%s' "$watch"; continue ;;
    @PROGRAM@) printf '%s' "$program"; continue ;;
  esac
  line=${line//@HOME@/"$(xml_escape "$HOME")"}
  printf '%s\n' "$line"
done < "$HERE/wow-stats.plist" > "$tmp"
plutil -lint -s "$tmp"
mv "$tmp" "$PLIST"
chmod 644 "$PLIST"
say "agent: $PLIST ($(printf '%s' "$watch" | grep -c '<string>') watched folders in $WOW)"

if [ "${WOW_STATS_NO_LOAD:-0}" = 1 ]; then
  say "agent: not loaded (WOW_STATS_NO_LOAD=1)"
else
  # Reload: bootout is asynchronous, so bootstrap may need a few tries.
  domain="gui/$(id -u)"
  launchctl bootout "$domain/$LABEL" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    if launchctl bootstrap "$domain" "$PLIST" 2>/dev/null; then break; fi
    sleep 1
  done
  launchctl print "$domain/$LABEL" >/dev/null
  launchctl kickstart "$domain/$LABEL"
  say "agent: loaded and started; log in ~/Library/Logs/wow-stats.log"
fi

# The checklist: exits non-zero only when the install can't work as it is.
say ""
"${prog[@]}" "$SCRIPT" --preflight
