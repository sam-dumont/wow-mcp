#!/usr/bin/env bash
# The watch mode's versions lane (.github/workflows/kb-refresh.yaml): every
# upstream version that moved becomes a commit on HEAD, with no model:
# addon releases (their contracts checked), a Polestar release (checked, the
# tag moved), a forever-db release (checked, its tag moved), a client build,
# ForeverDB.net's data (downloaded, checked). Each kind reruns the exports of
# tools/kb/rebuild.py that read it and regenerates the test data.
# tools/kb/bump.py has the checks and their reasons; a refused kind gets its
# reason in $WHY and no commit.
#
# Then, every day whether a version moved or not, every daily export of
# tools/kb/rebuild.py reruns on the pinned inputs (owner, 2026-11-25: "a
# daily rebuild of static files"): a file that drifted from its exporter
# (an exporter changed on main, an export a kind didn't rerun) is rebuilt
# in its own commit. Deterministic: a day where nothing moved commits
# nothing, and the run opens no PR.
#
#   WHY=why.json GH_TOKEN=... tools/kb/versions.sh
#   KINDS="polestar" TAG=v0.4.2.2 FDB_TAG=data-v2026.11.2 ...   # the run job's Watch step already read them
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
# Commit what the tree holds now, when it holds anything: `commit <subject>`.
commit() {
  git add -A
  git diff --cached --quiet || git commit -qm "$1"
}
# The checkout every export reads: Polestar at tools/polestar-tag, forever-db
# at tools/forever-db-tag (tools/kb/checkout.py). --keep reuses the clone
# when the Polestar tag didn't move; forever-db is laid over it again.
checkout() { uvp tools/kb/checkout.py "$ps" --keep; }
exports() {  # exports <kind>...: the exports of tools/kb/rebuild.py that read them
  uvp tools/kb/rebuild.py run --polestar "$ps" --lua "$lua" --work "$tmp/rebuild" --inputs "$@"
}

if [ "${1:-check}" = check ]; then
  if [ -z "${KINDS+x}" ]; then
    seen=$(env -u GITHUB_OUTPUT uv run --frozen python tools/kb/watch.py --kinds versions) || exit 1
    KINDS=$(jq -r '.kinds | join(" ")' <<<"$seen")
    TAG=$(jq -r '.polestar_tag // ""' <<<"$seen")
    FDB_TAG=$(jq -r '.forever_db_tag // ""' <<<"$seen")
  fi
  kinds=" $KINDS "
  tag=${TAG:-}
  echo "versions moved: ${KINDS:-none}${tag:+ (Polestar $tag)}${FDB_TAG:+ (forever-db $FDB_TAG)}"
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
    -u ACTIONS_RUNTIME_TOKEN bash "$0" build "$ok" "$tag" "$KINDS" "${FDB_TAG:-}"
fi

[ "$1" = build ] || { echo "usage: $0 [check]" >&2; exit 2; }
ok=$2 tag=$3 kinds=" $4 " fdb_tag=${5:-}
command -v "$lua" >/dev/null || { sudo apt-get update -q && sudo apt-get install -y -q --no-install-recommends lua5.4; } \
  || exit 1
# Every run builds the checkout: the daily rebuild below reads it whatever moved.
checkout || exit 1
# Exported for the gates' tests that read a Polestar checkout.
[ -n "${GITHUB_ENV:-}" ] && printf 'POLESTAR_CHECKOUT=%s\nLUA=%s\n' "$ps" "$lua" >> "$GITHUB_ENV"

# A refusal puts the tree back as the last commit left it, and the checkout
# with it: the gates read POLESTAR_CHECKOUT, and a checkout left at a
# refused tag failed them (2026-11-22), which would refuse every other kind.
back() {
  git reset -q --hard HEAD && git clean -qfd
  checkout
}

if [ "$ok" = 1 ]; then
  was=$(jq -r .polestar.known data/kb/watch.json)
  if checkout && exports polestar; then
    if uvp tools/kb/bump.py regen --old "$was" --new "$tag" --why "$WHY" \
      && uvp tools/kb/watch.py --accept polestar --release "$tag"; then
      commit "Polestar $tag: the tag, the exports and the test data"
    else
      back
    fi
  else
    back
    why '.polestar = $w' "The exports failed at $tag (the versions lane, tools/kb/versions.sh): nothing of the release is in the PR."
  fi
fi

# forever-db at its own release, ahead of the Polestar tag that bundles it
# (tools/kb/checkout.py). Its data is taken as it is, like Polestar's; the
# golden characters' moved upgrades go with it, nothing else they lose.
if has forever-db && [ -n "$fdb_tag" ]; then
  if uvp tools/kb/bump.py forever-db "$fdb_tag" --why "$WHY" \
    && checkout && exports forever-db \
    && uvp tools/kb/bump.py regen --as forever-db --why "$WHY" \
    && uvp tools/kb/bump.py golden --as forever-db --why "$WHY" \
    && uvp tools/kb/watch.py --accept forever-db --release "$fdb_tag"; then
    commit "forever-db ${fdb_tag#data-v}: the quest data and the exports that read it"
  else
    back
    why '."forever-db" //= $w' "The exports failed on forever-db $fdb_tag (tools/kb/versions.sh)."
  fi
fi

if has client-build; then
  # The newest build, read once and accepted; the exports then read it as the known one.
  if uvp tools/class_reference/export.py --polestar "$ps" --build latest --handoff "$tmp/kb-handoff-client.json" \
    && uvp tools/kb/watch.py --accept client-build --handoff "$tmp/kb-handoff-client.json" \
    && exports client-build \
    && uvp tools/kb/bump.py regen --as client-build --why "$WHY"; then
    commit "Client build: the exports that read the client tables rerun"
  else
    git reset -q --hard HEAD && git clean -qfd
    # bump.py regen wrote its own reason; this one only when it didn't.
    why '."client-build" //= $w' "An export that reads the client tables failed against the new build (tools/kb/versions.sh)."
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
    commit "ForeverDB.net data: item sources and stat weights"
  else
    git reset -q --hard HEAD && git clean -qfd
    : > "$tmp/kb-foreverdb.md"
    why '.foreverdb //= $w' "The ForeverDB export failed on the new data (tools/kb/versions.sh)."
  fi
fi

# The daily rebuild: every daily export on what the commits above pinned. A
# file that comes out different is main's own exporter rerun at inputs main
# already names, so its test data moves with it; the golden characters
# still stop it. A failure is the `rebuild` entry of $WHY: the land job
# turns the run red on it (no watch kind has an issue for it).
if exports; then
  moved=$(git status --porcelain -- data | sed 's/^...//' | paste -sd ' ' - | cut -c1-150)
  if [ -n "$moved" ]; then
    if uvp tools/kb/bump.py regen --as rebuild --why "$WHY" \
      && uvp tools/kb/bump.py golden --as rebuild --why "$WHY"; then
      commit "Data rebuild: $moved rebuilt from the pinned sources"
    else
      git reset -q --hard HEAD && git clean -qfd
    fi
  fi
else
  git reset -q --hard HEAD && git clean -qfd
  why '.rebuild //= $w' "An export failed in the daily rebuild (tools/kb/rebuild.py, the run's \"Versions\" step log says which)."
fi

# The land job's second try: the gates on what was made again, still with no token.
if [ -n "${GATES_OUT:-}" ] && [ "$(git rev-parse HEAD)" != "${GATES_BASE:?GATES_BASE: the commit the lane started from}" ]; then
  export POLESTAR_CHECKOUT="$ps" LUA="$lua"
  uvp tools/kb/gates.py --base "$GATES_BASE" --out "$GATES_OUT" --markdown "${GATES_OUT%.json}.md" || true
fi
exit 0
