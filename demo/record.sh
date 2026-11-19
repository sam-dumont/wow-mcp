#!/usr/bin/env bash
# Record the manual's footage on a throwaway two-person lab (stack/up.sh):
# VHS for the terminal, Playwright (Chromium) for the browser, both themes.
# The composition is demo/video (Remotion); the clips land in docs/videos/media.
#
#   VL_BIN=... demo/record.sh            # demo/video/public/<take>-{dark,light}.{mp4,webm} + marks
#   MODES=dark VL_BIN=... demo/record.sh # one theme
#   KEEP=1 ...                           # leave the lab running afterwards
#
# Needs: uv, vhs, ffprobe, the Inconsolata font, and a VictoriaLogs binary
# (VL_BIN, v1.54.0: the release tarball's victoria-logs-prod). Run it on an
# idle machine: VHS drops frames when the CPU is busy.
#
# Each theme is one friend's first days: the wizard and the install, a run
# of the shipper by hand, the decoy character's evenings, the tour, the zogzog link,
# the update, Settings (which resets the upload password), the uninstall.
# The terminal runs in a throwaway HOME with a fake WoW folder. launchd is
# never touched: `launchctl` is a no-op shim first on the takes' PATH (the
# real one is per macOS user: the uninstall line would stop this Mac's own
# shipper), and the takes' shell exits before any command if `launchctl`
# resolves to anything else. The installers run as for a friend (no
# WOW_STATS_NO_LOAD), so their checklist shows the background program.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
out="$here/video/public"
# Short on purpose: the installer and ls print these paths in the takes
# (macOS's $TMPDIR is 50 characters of /var/folders). Deleted on exit.
state=$(mktemp -d /tmp/wow.XXXXXX)
mkdir -p "$out"
cd "$here/.."

pids=()
finish() {
  if [[ ${KEEP:-0} == 1 ]]; then echo "KEEP=1: lab left running, state in $state"; return; fi
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  rm -rf "$state" "$here/tapes/.settings.tape"
}
trap finish EXIT

echo "== the lab"
SITES=1 source "$here/stack/up.sh"
source "$here/stack/friend.sh"
mkdir -p "$state/bin"
printf '#!/bin/sh\n# demo/record.sh: launchd stays untouched in the takes.\nexit 0\n' > "$state/bin/launchctl"
chmod 755 "$state/bin/launchctl"

theme() {
  if [[ $1 == dark ]]; then
    echo '{"name":"wow-dark","background":"#16110C","foreground":"#EFE4CA","cursor":"#C99A3B","selection":"#4A3826","black":"#16110C","red":"#D9604A","green":"#7DC160","yellow":"#C99A3B","blue":"#6FA8E8","magenta":"#C792EA","cyan":"#7CC4F0","white":"#EFE4CA","brightBlack":"#B09C7A","brightRed":"#D9604A","brightGreen":"#7DC160","brightYellow":"#F0CF7A","brightBlue":"#6FA8E8","brightMagenta":"#C792EA","brightCyan":"#7CC4F0","brightWhite":"#FFFFFF"}'
  else
    echo '{"name":"wow-light","background":"#F7EEDA","foreground":"#2E2012","cursor":"#8A5A00","selection":"#E6D5AE","black":"#2E2012","red":"#A33A26","green":"#2F7A16","yellow":"#8A5A00","blue":"#2F6FB8","magenta":"#8A4FB8","cyan":"#1F7FA8","white":"#F7EEDA","brightBlack":"#6A5233","brightRed":"#A33A26","brightGreen":"#2F7A16","brightYellow":"#6B4400","brightBlue":"#2F6FB8","brightMagenta":"#8A4FB8","brightCyan":"#1F7FA8","brightWhite":"#2E2012"}'
  fi
}

# tape <name> <mode> [@KEY@=value ...]: one VHS take into $out/<name>-<mode>.mp4.
tape() {
  local name=$1 mode=$2; shift 2
  uv run --no-project python - "$here/tapes/$name.tape" "$state/$name.tape" "$out/$name-$mode.mp4" "$@" <<'PY'
import sys
src, dst, mp4, *subs = sys.argv[1:]
text = open(src, encoding="utf-8").read()
for kv in subs:
    k, v = kv.split("=", 1)
    text = text.replace(k, v)
open(dst, "w", encoding="utf-8").write(f'Output "{mp4}"\n' + text)
PY
  vhs "$state/$name.tape" >/dev/null
  echo "  $name-$mode.mp4 ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$out/$name-$mode.mp4" | cut -c1-5) s)"
}

# The shipper reads the WoW folder its config names, and only that one: never
# run it in a take whose config doesn't point into this lab.
guard() {
  grep -q "^wow_dir = '$state/" "$1" || { echo "the take's config has no lab wow_dir: $1" >&2; exit 1; }
}

browser() {  # browser <scene> <mode> [ENV=value ...]: one Playwright take
  local scene=$1 mode=$2; shift 2
  (cd "$here/browser" && env DEMO_STATE="$state" DEMO_THEME="$mode" "$@" uv run --frozen python "$scene.py")
  cp "$state/$scene-$mode.webm" "$state/$scene-marks-$mode.json" "$out/"
  echo "  $scene-$mode.webm"
}

for mode in ${MODES:-dark light}; do
  echo "== $mode"
  friend_of "$mode"
  # Laid out like a Mac's, so the paths on screen read like a friend's.
  home="$state/Users/$name"; wow="$state/$mode/World of Warcraft/_forever_"; conf="$home/.config/wow-stats/config.toml"
  mkdir -p "$home"
  uv run --frozen python demo/stack/fake_wow.py "$wow" "$char" "Pellwick-${char#*-}"
  cat > "$state/shell.sh" <<SH
export HOME='$home' WOW_STATS_WOW_DIR='$wow' PATH='$state/bin':"\$PATH"
export UV_CACHE_DIR='$(uv cache dir)' UV_PYTHON_INSTALL_DIR='$(uv python dir)'
[ "\$(command -v launchctl)" = '$state/bin/launchctl' ] || exit 1  # never the real launchd
cd "\$HOME"
SH
  [[ $(bash -c "source '$state/shell.sh' >/dev/null && command -v launchctl") == "$state/bin/launchctl" ]] \
    || { echo "the takes' launchctl isn't the shim: stopping before any take" >&2; exit 1; }
  sed -e "s|@THEME@|$(theme "$mode")|" -e "s|@STATE@|$state|" -e "s|@MODE@|$mode|" \
    "$here/tapes/settings.tape.in" > "$here/tapes/.settings.tape"

  # The start page and the install, side by side; the first evening arrives
  # once the installer wrote the config.
  rm -f "$state/install-url"
  browser wizard "$mode" DEMO_URL="http://localhost:$port" & wiz=$!
  for _ in $(seq 1 120); do [[ -s "$state/install-url" ]] && break; kill -0 "$wiz" 2>/dev/null || break; sleep 0.5; done
  [[ -s "$state/install-url" ]] || { echo "the wizard gave no install command (see above)" >&2; exit 1; }
  tape install-mac "$mode" "@INSTALL_URL@=$(cat "$state/install-url")"
  guard "$conf"
  uv run --no-project python "$here/stack/seed_friend.py" "$conf" first
  wait "$wiz"

  tape status-mac "$mode"
  uv run --no-project python "$here/stack/seed_friend.py" "$conf" all
  sleep 90  # the page's answers are rebuilt once new data is seen (wowstats.prebuilt, every 60 s)
  browser tour "$mode" DEMO_URL="http://localhost:$port"
  publish_sites
  browser private "$mode" DEMO_URL="http://localhost:$port" DEMO_SITE_PORT="$SITE"
  tape update-mac "$mode" "@UPDATE_URL@=$BASE/install/update"
  browser settings "$mode" DEMO_URL="http://localhost:$port"
  uninstall=$(uv run --no-project python -c 'import json; print(json.load(open("docs/videos/steps.json"))["uninstall"]["os"]["mac"]["command"])')
  tape uninstall-mac "$mode" "@UNINSTALL@=$uninstall"
done
echo "== done: footage in demo/video/public; next: npm --prefix demo/video run render"
