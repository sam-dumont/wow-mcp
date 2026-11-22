#!/usr/bin/env bash
# The demo's real 3D model: Nibbet's game files from the `demo-3d` release
# (tag demo-3d-1, built by tools/fixtures/demo3d.py), never from git.
#
#   site/scripts/demo3d-asset.sh <dest dir>
#
# Downloads the archive, checks its SHA-256 against site/demo3d.sha256 (the
# pin in git), unpacks it in <dest dir> and prints the store's folder. A
# missing asset, a download error or a checksum that doesn't match prints
# nothing and exits 0: the demo then draws golden3d's figure, and the build
# goes on (a warning in the run). Removing the release is the off switch.
#
# DEMO_3D_URL: another archive (tests use a file:// URL).
set -euo pipefail

dest=${1:?usage: demo3d-asset.sh <dest dir>}
repo=$(cd "$(dirname "$0")/../.." && pwd)
url=${DEMO_3D_URL:-https://github.com/${GITHUB_REPOSITORY:-sam-dumont/wow-mcp}/releases/download/demo-3d-1/nibbet-3d.tar.gz}
pin=$(awk 'NR==1{print $1}' "$repo/site/demo3d.sha256")

warn() {
  if [[ -n ${GITHUB_ACTIONS:-} ]]; then echo "::warning::demo 3D: $*, the demo draws the made-up figure" >&2
  else echo "demo3d-asset.sh: $*, the demo draws the made-up figure" >&2; fi
  exit 0
}

mkdir -p "$dest"
archive="$dest/nibbet-3d.tar.gz"
curl -fsSL --retry 2 --max-filesize 104857600 -o "$archive" "$url" 2>/dev/null || warn "no archive at $url"
got=$( (sha256sum "$archive" 2>/dev/null || shasum -a 256 "$archive") | awk '{print $1}')
[[ $got == "$pin" ]] || { rm -f "$archive"; warn "the archive's SHA-256 $got isn't site/demo3d.sha256's $pin"; }
tar -xzf "$archive" -C "$dest" --no-same-owner --no-same-permissions
rm -f "$archive"
[[ -f $dest/store/look.json && -f $dest/store/tables/build.json ]] || warn "the archive holds no store"
echo "$dest/store"
