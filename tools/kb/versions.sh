#!/usr/bin/env bash
# The watch mode's versions lane (.github/workflows/kb-refresh.yaml): every
# upstream version that moved becomes a commit on HEAD, with no model:
# addon releases (their contracts checked), a Polestar release (checked, the
# tag moved, the exports rerun, the test data regenerated), a client build
# (the class and forever references rerun), ForeverDB.net's data (downloaded, checked,
# both exports rerun). tools/kb/bump.py has the checks and their
# reasons; a refused kind gets its reason in $WHY and no commit.
#
#   WHY=why.json GH_TOKEN=... tools/kb/versions.sh
#   KINDS="polestar" TAG=v0.4.2.2 ...   # the run job's Watch step already read them
#   GATES_BASE=<sha> GATES_OUT=gates.json ...   # then the gates, on what it made
#
# Used twice: by the run job, and by the land job when main moved under the
# PR and the change conflicts with it. Everything here is deterministic, so
# the land job makes it again on the current main rather than refusing
# (2026-11-22: #140, Polestar v0.4.2.2, conflicted with semantic wave 1's
# regenerated plan inputs and was refused).
#
# Two phases. `check` reads GitHub with GH_TOKEN (a read-only token): which
# versions moved, Polestar's licence terms and saved tables, the addons'
# contracts. It then execs `build` with no token in its environment: the
# exports run Polestar's own planner and Lua at the new tag, and exec leaves
# no parent process holding the token either.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
: "${WHY:?WHY: the file of reasons, kind to why it was left out}"
[ -s "$WHY" ] || echo '{}' > "$WHY"
tmp=${RUNNER_TEMP:-${TMPDIR:-/tmp}}
ps="$tmp/polestar"
lua=${LUA:-lua5.4}
uvp() { uv run --frozen python "$@"; }
has() { case " $kinds " in *" $1 "*) return 0 ;; esac; return 1; }
why() {  # why <jq filter> <reason>
  jq --arg w "$2" "$1" "$WHY" > "$WHY.new" && mv "$WHY.new" "$WHY"
}

if [ "${1:-check}" = check ]; then
  if [ -z "${KINDS+x}" ]; then
    seen=$(env -u GITHUB_OUTPUT uv run --frozen python tools/kb/watch.py --kinds versions) || exit 1
    KINDS=$(jq -r '.kinds | join(" ")' <<<"$seen")
    TAG=$(jq -r '.polestar_tag // ""' <<<"$seen")
  fi
  kinds=" $KINDS "
  tag=${TAG:-}
  echo "versions moved: ${KINDS:-none}${tag:+ (Polestar $tag)}"
  # Addons first: their commit takes the whole tree, before the Polestar tag moves.
  # Each accept records what its step processed (a handoff), never a fresh read.
  if has addons && uvp tools/kb/bump.py addons --why "$WHY" --handoff "$tmp/kb-handoff-addons.json"; then
    uvp tools/kb/watch.py --accept addons --handoff "$tmp/kb-handoff-addons.json" || exit 1
    git add -A && git commit -qm "$(jq -r '.message // "Addon releases: what the shipper reads is unchanged"' \
      "$tmp/kb-handoff-addons.json")"
  fi
  ok=0
  if has polestar && [ -n "$tag" ] && uvp tools/kb/bump.py polestar "$tag" --why "$WHY"; then
    ok=1
  fi
  exec env -u GH_TOKEN -u GITHUB_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_URL \
    -u ACTIONS_RUNTIME_TOKEN bash "$0" build "$ok" "$tag" "$KINDS"
fi

[ "$1" = build ] || { echo "usage: $0 [check]" >&2; exit 2; }
ok=$2 tag=$3 kinds=" $4 "
cloned=0
if [ "$ok" = 1 ] || has client-build; then
  command -v "$lua" >/dev/null || { sudo apt-get update -q && sudo apt-get install -y -q --no-install-recommends lua5.4; } \
    || exit 1
  rm -rf "$ps"
  git clone -q --depth 1 --branch "$(tr -d '[:space:]' < tools/polestar-tag)" https://github.com/fitzforge/polestar "$ps" \
    || exit 1
  cloned=1
  # Exported for the gates' tests that read a Polestar checkout.
  [ -n "${GITHUB_ENV:-}" ] && printf 'POLESTAR_CHECKOUT=%s\nLUA=%s\n' "$ps" "$lua" >> "$GITHUB_ENV"
fi

if [ "$ok" = 1 ]; then
  was=$(jq -r .polestar.known data/kb/watch.json)
  # A refusal puts the tree back at the known tag, and the checkout with it:
  # the gates read POLESTAR_CHECKOUT, and a checkout left at the refused tag
  # failed them (2026-11-22), which would refuse every other kind in the PR.
  back() {
    git reset -q --hard HEAD && git clean -qfd
    rm -rf "$ps" && git clone -q --depth 1 --branch "$was" https://github.com/fitzforge/polestar "$ps"
  }
  if node tools/route_reference/export.mjs --polestar "$ps" --lua "$lua" \
    && node tools/gear_reference/export.mjs --polestar "$ps" --lua "$lua" \
    && uvp tools/forever_reference/export.py --polestar "$ps" --forever-db-cache "$tmp/forever-db" \
    && uvp tools/dungeon_quests/export.py --polestar "$ps" --forever-db-cache "$tmp/forever-db" \
    && uvp tools/kb/export.py --polestar "$ps" \
    && uvp tools/class_reference/export.py --polestar "$ps" \
    && uvp tools/trainers/export.py --polestar "$ps" \
    && uvp tools/craft_reference/export.py --polestar "$ps" --lua "$lua"; then
    if uvp tools/kb/bump.py regen --old "$was" --new "$tag" --why "$WHY" \
      && uvp tools/kb/watch.py --accept polestar --release "$tag"; then
      git add -A && git commit -qm "Polestar $tag: the tag, the exports and the test data"
    else
      back
    fi
  else
    back
    why '.polestar = $w' "The exports failed at $tag (the versions lane, tools/kb/versions.sh): nothing of the release is in the PR."
  fi
fi

# The client build: the forever reference reads the client tables at the
# build watch.json knows, so after the accept; the dungeon quests and the
# route trips read its areas.
if has client-build; then
  if uvp tools/class_reference/export.py --polestar "$ps" --handoff "$tmp/kb-handoff-client.json" \
    && uvp tools/kb/watch.py --accept client-build --handoff "$tmp/kb-handoff-client.json" \
    && uvp tools/forever_reference/export.py --polestar "$ps" --forever-db-cache "$tmp/forever-db" \
    && uvp tools/dungeon_quests/export.py --polestar "$ps" --forever-db-cache "$tmp/forever-db" \
    && node tools/route_reference/export.mjs --polestar "$ps" --lua "$lua" \
    && uvp tools/kb/bump.py regen --as client-build --why "$WHY"; then
    git add -A && git commit -qm "Client build: the class and forever references rerun"
  else
    git reset -q --hard HEAD && git clean -qfd
    # bump.py regen wrote its own reason; this one only when it didn't.
    why '."client-build" //= $w' "The class or forever reference export failed against the new build (tools/kb/versions.sh)."
  fi
fi

# ForeverDB's data, in this phase: a download over plain HTTPS needs no token,
# and nothing in it runs (tools/foreverdb_sources/lua.py reads it as text).
# After the other kinds, so its regen sees their data too.
if has foreverdb; then
  if uvp tools/kb/bump.py foreverdb --why "$WHY" --handoff "$tmp/kb-handoff-foreverdb.json" \
      --summary "$tmp/kb-foreverdb.md" \
    && uvp tools/kb/bump.py regen --as foreverdb --why "$WHY" \
    && uvp tools/kb/bump.py golden --as foreverdb --why "$WHY" \
    && uvp tools/kb/watch.py --accept foreverdb --handoff "$tmp/kb-handoff-foreverdb.json"; then
    git add -A && git commit -qm "ForeverDB.net data: item sources and stat weights"
  else
    git reset -q --hard HEAD && git clean -qfd
    : > "$tmp/kb-foreverdb.md"
    why '.foreverdb //= $w' "The ForeverDB export failed on the new data (tools/kb/versions.sh)."
  fi
fi

# The land job's second try: the gates on what was made again, still with no token.
if [ -n "${GATES_OUT:-}" ] && [ "$(git rev-parse HEAD)" != "${GATES_BASE:?GATES_BASE: the commit the lane started from}" ]; then
  if [ "$cloned" = 1 ]; then export POLESTAR_CHECKOUT="$ps" LUA="$lua"; fi
  uvp tools/kb/gates.py --base "$GATES_BASE" --out "$GATES_OUT" --markdown "${GATES_OUT%.json}.md" || true
fi
exit 0
