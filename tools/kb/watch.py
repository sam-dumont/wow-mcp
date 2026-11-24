"""The knowledge base's watchers: deterministic, no model.

    uv run python tools/kb/watch.py                      # print what changed
    uv run python tools/kb/watch.py --kinds versions     # the upstream versions only
    uv run python tools/kb/watch.py --accept polestar --release v1.2  # the tag the bump checked
    uv run python tools/kb/watch.py --accept official --handoff read.json  # what a step processed
    uv run python tools/kb/watch.py --github --why why.json   # the issues, at the end of a run

Compares what data/kb/watch.json knows (the values the KB was last checked
against) with today's:

- client-build: Forever's current client build on wago.tools;
- polestar: the Polestar addon's newest final release on GitHub, its tag a
  plain version (the repository also releases its desktop client,
  `client-v*`, and pre-releases: neither is the addon);
- addons: the latest releases of the install docs' other addons that
  publish on GitHub (the CurseForge- and Wago-only ones have no keyless
  feed: listed, not watched);
- foreverdb: ForeverDB.net's addon data, where gear comes from and the stat
  weights (a short hash of its manifest's SHA-256s for the files read,
  tools/foreverdb_sources/export.py `fingerprint`), at most once a week
  (owner, 2026-11-24): for FOREVERDB_DAYS after the last accepted bump
  (watch.json `foreverdb.taken`) it reads as its known value, so the daily
  watch neither bumps nor opens an issue, and the first run after that
  takes whatever the data is then;
- official: Blizzard's hotfix and patch-note pages (a page's normalised
  text hash; a forum thread's post count and last post, from Discourse's
  JSON).

The first four are upstream versions (`--kinds versions`): the refresh
job's daily `watch` mode (.github/workflows/kb-refresh.yaml) lands each one
like Dependabot would, with no model at all: the Polestar bump and its
exports, the addons' contract check (tools/kb/bump.py), the class reference
for a new build. `official` is prose a model has to read: the weekly
`review` mode checks it here (`--kinds official`, free) and runs the model
pass (tools/kb/refresh.py) only on the pages that changed (owner,
2026-11-22: the deterministic part as often as needed, the model once a
week). `--accept` records what went through, at the value the step that
processed it saw, never a fresh fetch: a fresh read could acknowledge a post
that came in while the model read the thread, or a release newer than the
one the bump checked (Codex review, 2026-11-22). `official` comes from the
model pass's `--handoff` file (tools/kb/refresh.py), `addons` from the
bump's (tools/kb/bump.py addons), `client-build` from the class-reference
export's (tools/class_reference/export.py), `foreverdb` from its bump's
(tools/kb/bump.py foreverdb), `polestar` from `--release`.
`--github` runs last: a kind
back to its known value (the PR merged) closes its issue; a kind still
changed keeps one issue (label kb-watch) that says why it wasn't merged
(`--why`: {kind: reason}, written by the run's steps), or that the run
stopped before a PR. Each mode keeps only its own kinds' issues.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "kb" / "watch.json"
LABEL = "kb-watch"
TAG = re.compile(r"^v\d+(\.\d+){1,4}$")
TITLES = {"client-build": "KB watch: new Forever client build", "official": "KB watch: official patch notes changed",
          "polestar": "KB watch: new Polestar release", "addons": "KB watch: addon releases",
          "foreverdb": "KB watch: new ForeverDB.net data"}
# Upstream versions, landed without a model; the rest is prose for the weekly review.
VERSIONS = ("client-build", "polestar", "addons", "foreverdb")
GROUPS = {"versions": list(VERSIONS), "official": ["official"]}


def kinds_of(names: list[str] | None) -> list[str]:
    """Kind names, `versions` standing for the upstream ones; none given: all."""
    out = [k for n in names or list(TITLES) for k in GROUPS.get(n, [n])]
    bad = [k for k in out if k not in TITLES]
    if bad:
        raise SystemExit(f"unknown kind: {', '.join(bad)}")
    return [k for k in TITLES if k in out]


def current(state: dict, fetch, gh, kinds: list[str] | None = None) -> dict:
    """Today's value of every watched thing of `kinds` (default all), as
    {kind: {key: value}}; a value that can't be read is None (said in the
    issue, never a change). A kind not asked for is {} and nothing is fetched
    for it."""
    kinds = list(TITLES) if kinds is None else kinds
    out: dict[str, dict] = {k: {} for k in TITLES}
    if "client-build" in kinds:
        b = state["client_build"]
        status, body = fetch.get(b["url"])
        try:
            out["client-build"]["build"] = json.loads(body)["version"] if status == 200 else None
        except (ValueError, KeyError):
            out["client-build"]["build"] = None
    for p in state["official"] if "official" in kinds else []:
        status, body = fetch.get(p["url"])
        out["official"][p["key"]] = web.official_version(p["kind"], body) if status == 200 else None
    if "polestar" in kinds:
        ps = state["polestar"]
        out["polestar"]["release"] = (forward(gh, ps["repo"], ps["known"], polestar_release(gh, ps["repo"]))
                                      if gh else None)
    for a in state["addons"] if "addons" in kinds else []:
        if a.get("repo"):
            out["addons"][a["name"]] = addon_release(gh, a) if gh else None
    if "foreverdb" in kinds and taken_recently(state["foreverdb"]):
        out["foreverdb"]["data"] = state["foreverdb"]["known"]
    elif "foreverdb" in kinds:
        status, body = fetch.get(state["foreverdb"]["manifest"])
        try:
            out["foreverdb"]["data"] = foreverdb().fingerprint(json.loads(body)) if status == 200 else None
        except (ValueError, AttributeError, TypeError, foreverdb().Refused):
            out["foreverdb"]["data"] = None
    return out


def version_key(tag: str | None) -> tuple[int, ...]:
    """A tag's numbers in order (`bundle/v12.13.0+v1.1.5` is 12, 13, 0, 1, 1, 5)."""
    return tuple(int(n) for n in re.findall(r"\d+", tag or ""))


def older(tag: str | None, than: str | None) -> bool:
    a, b = version_key(tag), version_key(than)
    return bool(a and b) and a < b


def forward(gh, repo: str, known: str | None, latest: str | None) -> str | None:
    """`latest`, never a version back from `known` while known's release still
    exists: then `known` (no change). 2026-11-24: wowsims/exporter's v3.2.11
    release went away (the tag stayed), GitHub's Latest fell back to v3.2.10
    and the watch moved `known` back without a word (#211). A known release
    that is gone (404) lets the older one through: it is what people can
    install now, and the bump says so in its commit (`moved_back`)."""
    if older(latest, known) and not gh.release_gone(repo, known):
        return known
    return latest


def addon_release(gh, addon: dict) -> str | None:
    """The release of an addon the watch and the bump compare with its `known`."""
    return forward(gh, addon["repo"], addon.get("known"), gh.latest_release(addon["repo"]))


def moved_back(state: dict, checked: dict[str, str]) -> list[str]:
    """What a step takes that is older than what the KB knew, said in its commit."""
    return [f"{a['name']} back to {checked[a['name']]} ({a['known']}'s release is gone upstream)"
            for a in state["addons"] if a["name"] in checked and older(checked[a["name"]], a.get("known"))]


# ForeverDB.net's data is rebuilt every few hours; one bump a week is enough
# for item sources and weights, and keeps the PRs to one a week.
FOREVERDB_DAYS = 7


def today():
    return datetime.now(UTC).date()


def taken_recently(fdb: dict) -> bool:
    """Whether ForeverDB's data was last taken under FOREVERDB_DAYS ago."""
    try:
        taken = datetime.fromisoformat(str(fdb.get("taken"))).date()
    except ValueError:
        return False
    return (today() - taken).days < FOREVERDB_DAYS


def foreverdb():
    """tools/foreverdb_sources/export.py, by path (tools/kb has an export.py of its own)."""
    if "foreverdb_export" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "foreverdb_export", ROOT / "tools" / "foreverdb_sources" / "export.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["foreverdb_export"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["foreverdb_export"]


def polestar_release(gh, repo: str) -> str | None:
    """Polestar's newest final addon release: GitHub's Latest when its tag is a
    plain version, else the newest plain-version release that is neither a
    draft nor a pre-release. None when there is none (not a change)."""
    tag = gh.latest_release(repo)
    if tag and TAG.match(tag):
        return tag
    return next((r["tag_name"] for r in gh.list_releases(repo)
                 if not r.get("draft") and not r.get("prerelease") and TAG.match(str(r.get("tag_name") or ""))),
                None)


def known(state: dict) -> dict:
    return {"client-build": {"build": state["client_build"]["known"]},
            "official": {p["key"]: p["known"] for p in state["official"]},
            "polestar": {"release": state["polestar"]["known"]},
            "addons": {a["name"]: a["known"] for a in state["addons"] if a.get("repo")},
            "foreverdb": {"data": state["foreverdb"]["known"]}}


def links(state: dict, kind: str, key: str) -> str:
    if kind == "official":
        p = next(p for p in state["official"] if p["key"] == key)
        return p.get("page") or p["url"]
    if kind == "polestar":
        return f"https://github.com/{state['polestar']['repo']}/releases"
    if kind == "addons":
        a = next(a for a in state["addons"] if a["name"] == key)
        return f"https://github.com/{a['repo']}/releases"
    if kind == "foreverdb":
        return state["foreverdb"].get("page") or state["foreverdb"]["manifest"]
    return state["client_build"].get("page") or state["client_build"]["url"]


RUN_URL = "{server}/{repo}/actions/runs/{run}"

# What a person does when the job couldn't land a kind itself.
NEXT = {
    "client-build": "Re-run the client-table export (tools/class_reference/export.py, with Polestar at "
                    "tools/polestar-tag) with `--handoff seen.json`, check the diff and the tests, then "
                    "`watch.py --accept client-build --handoff seen.json`.",
    "official": "Read the refused PR's statements against the page, fix or drop what the gates name, merge; or "
                "run kb-refresh with mode refresh and the page keys once the cause is fixed.",
    "polestar": "Bump tools/polestar-tag, re-run the exports (docs/route-reference.md), check what the gates or "
                "the contract check name, then `watch.py --accept polestar --release <tag>`.",
    "addons": "Check what the addon saves against shipper/wow_stats.py (docs/reference/addons.md), then "
              "record the release you checked: `watch.py --accept addons --handoff seen.json`, seen.json "
              "holding `{\"addons\": {\"<name>\": \"<tag>\"}}`.",
    "foreverdb": "Read what tools/kb/bump.py foreverdb refused (a format comment or a licence file that changed, "
                 "or a gate): check the new files against tools/foreverdb_sources/export.py and the licence, rerun "
                 "the bump by hand, then `watch.py --accept foreverdb --handoff seen.json`.",
}


def run_link() -> str:
    env = os.environ
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        return RUN_URL.format(server=env.get("GITHUB_SERVER_URL") or "https://github.com",
                              repo=env["GITHUB_REPOSITORY"], run=env["GITHUB_RUN_ID"])
    return "the refresh job's run"


def report(state: dict, now: dict, was: dict, why: dict | None = None,
           kinds: list[str] | None = None) -> dict[str, str | None]:
    """{kind: issue body, or None when nothing of that kind changed}, for
    `kinds` (default all). `why`: {kind: why the run didn't land it}; a
    changed kind without one means the run stopped before its PR (a failed
    step)."""
    out: dict[str, str | None] = {}
    why = why or {}
    day = datetime.now(UTC).date().isoformat()
    for kind in kinds or TITLES:
        changed = [(k, was[kind].get(k), v) for k, v in now[kind].items() if v is not None and v != was[kind].get(k)]
        unread = [k for k, v in now[kind].items() if v is None]
        if not changed:
            out[kind] = None
            continue
        rows = "\n".join(f"| {k} | `{o}` | `{n}` | {links(state, kind, k)} |" for k, o, n in changed)
        reason = why.get(kind) or (f"The run stopped before it could land this change ({run_link()}): read its "
                                   "failed step. The next run tries again.")
        body = (f"Checked {day} by `tools/kb/watch.py` (workflow kb-refresh). What the KB was last checked "
                f"against (`data/kb/watch.json`) changed:\n\n| What | Known | Now | Link |\n|---|---|---|---|\n{rows}\n"
                + (f"\nCouldn't read: {', '.join(unread)} (not counted as a change).\n" if unread else "")
                + f"\n## Why it isn't merged\n{reason}\n\n## Next\n- [ ] {NEXT[kind]}\n"
                  "\nThe refresh job lands a change itself when its gates pass (docs/kb.md, \"The refresh job\"). "
                  "This issue closes itself once `data/kb/watch.json` on main matches again.\n")
        out[kind] = body
    return out


def accept(state: dict, now: dict, kinds: list[str]) -> dict:
    """The given values into the state, for `kinds`: what a step processed
    (read_handoff, --release); a page or addon it doesn't name keeps its `known`."""
    if "client-build" in kinds and now["client-build"].get("build"):
        state["client_build"]["known"] = now["client-build"]["build"]
    for p in state["official"]:
        if "official" in kinds and now["official"].get(p["key"]):
            p["known"] = now["official"][p["key"]]
    if "polestar" in kinds and now["polestar"].get("release"):
        state["polestar"]["known"] = now["polestar"]["release"]
    for a in state["addons"]:
        if "addons" in kinds and now["addons"].get(a["name"]):
            a["known"] = now["addons"][a["name"]]
    if "foreverdb" in kinds and now["foreverdb"].get("data"):
        if now["foreverdb"]["data"] != state["foreverdb"]["known"]:
            state["foreverdb"]["taken"] = today().isoformat()
        state["foreverdb"]["known"] = now["foreverdb"]["data"]
    state["checked"] = today().isoformat()
    return state


def read_handoff(path: str, state: dict) -> dict[str, dict[str, str]]:
    """{kind: {key: value}} from a step's handoff file: what it processed, at
    the value it saw. `official` {page key: version} (tools/kb/refresh.py),
    `addons` {name: release tag} (tools/kb/bump.py addons), `client-build`
    {"build": build} (tools/class_reference/export.py), `foreverdb` {"data":
    fingerprint} (tools/kb/bump.py foreverdb). Kinds and keys not
    watched, and values that aren't one line of text, are left out."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    allowed = {"official": {p["key"] for p in state["official"]},
               "addons": {a["name"] for a in state["addons"] if a.get("repo")}, "client-build": {"build"},
               "foreverdb": {"data"}}
    out: dict[str, dict[str, str]] = {}
    for kind, keys in allowed.items():
        got = doc.get(kind) if isinstance(doc, dict) else None
        if isinstance(got, dict):
            out[kind] = {k: v for k, v in got.items()
                         if k in keys and isinstance(v, str) and v and len(v) < 200 and "\n" not in v}
    return out


def changes(state: dict, now: dict) -> dict:
    """What the job's steps need: the changed kinds, the changed official
    pages, and the new values they act on."""
    was = known(state)
    kinds = [k for k in TITLES if any(v is not None and v != was[k].get(n) for n, v in now[k].items())]
    tag = now["polestar"]["release"] if "polestar" in kinds else ""
    # A release tag goes into a git command and a file: only a plain version passes.
    if tag and not TAG.match(tag):
        tag = ""
    official = [k for k, v in now["official"].items() if v and v != was["official"].get(k)]
    return {"kinds": kinds, "changed_official": official, "polestar_tag": tag}


def run(state: dict, fetch, gh, github: bool, why: dict | None = None, kinds: list[str] | None = None) -> dict:
    kinds = list(TITLES) if kinds is None else kinds
    now = current(state, fetch, gh, kinds)
    bodies = report(state, now, known(state), why, kinds)
    did = {}
    for kind, body in bodies.items():
        did[kind] = web.keep_issue(gh, kind, LABEL, TITLES[kind], body,
                                   "Landed: `data/kb/watch.json` on main matches again.") if github else (
            "changed" if body else "none")
    return {"now": now, "did": did, **changes(state, now)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--github", action="store_true", help="open, edit or close the issues (GH_TOKEN)")
    ap.add_argument("--why", help="--github: a JSON file {kind: why the run didn't land it}")
    ap.add_argument("--accept", nargs="*", metavar="KIND", help="record today's values (all kinds if none given)")
    ap.add_argument("--handoff", help="--accept official, addons, client-build, foreverdb: the file of the step that "
                    "processed them, each value as it saw it")
    ap.add_argument("--release", help="--accept polestar: this tag, not a fresh read")
    ap.add_argument("--kinds", nargs="*", metavar="KIND",
                    help="only these kinds (`versions`: client-build, polestar, addons, foreverdb); default all")
    a = ap.parse_args(argv)
    path = Path(a.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    gh = web.GitHub(web.repo())
    watched = kinds_of(a.kinds)
    if a.accept is not None:
        kinds = kinds_of(a.accept) if a.accept else watched
        # 2026-11-22 (Codex review): a fresh read here acknowledged posts that
        # came in after the model read the thread, or that it never got; the
        # same for a release or build newer than the one the bump checked.
        missing = [k for k in kinds if not (a.release if k == "polestar" else a.handoff)]
        if missing:
            print(f"{', '.join(missing)}: accept what the step processed (--handoff, or --release for polestar), "
                  "never a fresh read", file=sys.stderr)
            return 2
        got = read_handoff(a.handoff, state) if a.handoff else {}
        now = {k: got.get(k, {}) for k in ("client-build", "official", "addons", "foreverdb")}
        # The tag the job checked and exported.
        now["polestar"] = {"release": a.release if a.release and TAG.match(a.release) else None}
        unread = [k for k in kinds if k != "addons" and not any(now[k].values())]
        if unread:
            # 2026-11-20: an accept that silently kept the old Polestar value
            # left the bump's PR red on a tag-file test.
            print(f"no processed value for: {', '.join(unread)}; nothing accepted", file=sys.stderr)
            return 3
        new = accept(state, now, kinds)
        path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        return 0
    why = json.loads(Path(a.why).read_text(encoding="utf-8")) if a.why and Path(a.why).exists() else {}
    out = run(state, web.Fetcher(), gh, a.github, why, watched)
    print(json.dumps({"did": out["did"], "kinds": out["kinds"], "changed_official": out["changed_official"],
                      "polestar_tag": out["polestar_tag"]}, indent=1))
    if os.environ.get("GITHUB_OUTPUT") and not a.github:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed_official={json.dumps(out['changed_official'])}\nkinds={' '.join(out['kinds'])}\n"
                    f"polestar_tag={out['polestar_tag']}\n")
    unwatched = [a["name"] for a in state["addons"] if not a.get("repo")]
    if unwatched:
        print(f"not watched (no keyless release feed): {', '.join(unwatched)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
