#!/usr/bin/env bash
# Fetch the versioned roster artwork, verify its SHA-256 and print the store path.
# DEMO_3D_URL can name a mirror of the same archive.
set -euo pipefail
dest=${1:?usage: demo3d-asset.sh <cache dir>}
repo=$(cd "$(dirname "$0")/../.." && pwd)
exec python3 "$repo/tools/fixtures/roster_asset.py" "$dest"
