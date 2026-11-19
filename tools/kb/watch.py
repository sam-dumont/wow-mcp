"""The knowledge base's daily watchers: deterministic, no model.

    uv run python tools/kb/watch.py            # print what changed
    uv run python tools/kb/watch.py --github   # and keep one issue per change kind
    uv run python tools/kb/watch.py --accept   # write today's values into data/kb/watch.json

Compares what data/kb/watch.json knows (the values the KB was last checked
against) with today's:

- client-build: Forever's current client build on wago.tools;
- official: Blizzard's hotfix and patch-note pages (a page's normalised
  text hash; a forum thread's post count and last post, from Discourse's
  JSON);
- polestar: the Polestar addon's latest release on GitHub;
- addons: the latest releases of the install docs' other addons that
  publish on GitHub (the CurseForge- and Wago-only ones have no keyless
  feed: listed, not watched).

Each kind with a change keeps one GitHub issue (label kb-watch) open with
what changed and the links; a kind back to its known value closes its
issue; nothing changes on a no-op. The refresh job (tools/kb/refresh.py)
reads `changed_official` to propose KB statements from the changed pages.
Once the KB is updated, `--accept` records the new values in the same PR.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import web  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data" / "kb" / "watch.json"
LABEL = "kb-watch"
TITLES = {"client-build": "KB watch: new Forever client build", "official": "KB watch: official patch notes changed",
          "polestar": "KB watch: new Polestar release", "addons": "KB watch: addon releases"}


def current(state: dict, fetch, gh) -> dict:
    """Today's value of every watched thing ({kind: {key: value}}); a value
    that can't be read is None (said in the issue, never a change)."""
    out: dict[str, dict] = {"client-build": {}, "official": {}, "polestar": {}, "addons": {}}
    b = state["client_build"]
    status, body = fetch.get(b["url"])
    try:
        out["client-build"]["build"] = json.loads(body)["version"] if status == 200 else None
    except (ValueError, KeyError):
        out["client-build"]["build"] = None
    for p in state["official"]:
        status, body = fetch.get(p["url"])
        if status != 200:
            out["official"][p["key"]] = None
        elif p["kind"] == "discourse":
            try:
                d = json.loads(body)
                out["official"][p["key"]] = f"{d['posts_count']} posts, last {d['last_posted_at']}"
            except (ValueError, KeyError):
                out["official"][p["key"]] = None
        else:
            text = web.text_of(body)
            out["official"][p["key"]] = web.digest(text) if len(text) > 200 else None
    out["polestar"]["release"] = gh.latest_release(state["polestar"]["repo"]) if gh else None
    for a in state["addons"]:
        if a.get("repo"):
            out["addons"][a["name"]] = gh.latest_release(a["repo"]) if gh else None
    return out


def known(state: dict) -> dict:
    return {"client-build": {"build": state["client_build"]["known"]},
            "official": {p["key"]: p["known"] for p in state["official"]},
            "polestar": {"release": state["polestar"]["known"]},
            "addons": {a["name"]: a["known"] for a in state["addons"] if a.get("repo")}}


def links(state: dict, kind: str, key: str) -> str:
    if kind == "official":
        p = next(p for p in state["official"] if p["key"] == key)
        return p.get("page") or p["url"]
    if kind == "polestar":
        return f"https://github.com/{state['polestar']['repo']}/releases"
    if kind == "addons":
        a = next(a for a in state["addons"] if a["name"] == key)
        return f"https://github.com/{a['repo']}/releases"
    return state["client_build"].get("page") or state["client_build"]["url"]


NEXT = {
    "client-build": "Re-run the client-table exports against the new build (tools/forever_reference/export.py, "
                    "tools/kb/export.py; docs/forever-reference.md) and open a PR: a diff that only touches the "
                    "generated tables can merge once CI is green.",
    "official": "The refresh job (kb-refresh, mode watch) proposes KB statement changes from the changed pages in a "
                "PR, never merged on its own: review it against the page.",
    "polestar": "Bump POLESTAR_TAG (.github/workflows/route-reference.yml), re-run the exports "
                "(docs/route-reference.md) and check the KB rules that cite Polestar (data/kb/rules.json).",
    "addons": "Read the release notes against docs/install.md: a change in what an addon saves can break an import.",
}


def report(state: dict, now: dict, was: dict) -> dict[str, str | None]:
    """{kind: issue body, or None when nothing of that kind changed}."""
    out: dict[str, str | None] = {}
    day = datetime.now(UTC).date().isoformat()
    for kind in TITLES:
        changed = [(k, was[kind].get(k), v) for k, v in now[kind].items() if v is not None and v != was[kind].get(k)]
        unread = [k for k, v in now[kind].items() if v is None]
        if not changed:
            out[kind] = None
            continue
        rows = "\n".join(f"| {k} | `{o}` | `{n}` | {links(state, kind, k)} |" for k, o, n in changed)
        body = (f"Checked {day} by `tools/kb/watch.py` (workflow kb-refresh). What the KB was last checked "
                f"against (`data/kb/watch.json`) changed:\n\n| What | Known | Now | Link |\n|---|---|---|---|\n{rows}\n"
                + (f"\nCouldn't read: {', '.join(unread)} (not counted as a change).\n" if unread else "")
                + f"\n## Next\n- [ ] {NEXT[kind]}\n- [ ] Record the new values: `uv run python tools/kb/watch.py "
                  f"--accept {kind}` in the same PR.\n\nThis issue closes itself once `data/kb/watch.json` matches "
                  "again.\n")
        out[kind] = body
    return out


def accept(state: dict, now: dict, kinds: list[str]) -> dict:
    if "client-build" in kinds and now["client-build"]["build"]:
        state["client_build"]["known"] = now["client-build"]["build"]
    for p in state["official"]:
        if "official" in kinds and now["official"].get(p["key"]):
            p["known"] = now["official"][p["key"]]
    if "polestar" in kinds and now["polestar"]["release"]:
        state["polestar"]["known"] = now["polestar"]["release"]
    for a in state["addons"]:
        if "addons" in kinds and now["addons"].get(a["name"]):
            a["known"] = now["addons"][a["name"]]
    state["checked"] = datetime.now(UTC).date().isoformat()
    return state


def run(state: dict, fetch, gh, github: bool) -> dict:
    now = current(state, fetch, gh)
    bodies = report(state, now, known(state))
    did = {}
    for kind, body in bodies.items():
        did[kind] = web.keep_issue(gh, kind, LABEL, TITLES[kind], body) if github else (
            "changed" if body else "none")
    changed_official = [k for k, v in now["official"].items() if v and v != known(state)["official"].get(k)]
    return {"now": now, "did": did, "changed_official": changed_official}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--github", action="store_true", help="open, edit or close the issues (GH_TOKEN)")
    ap.add_argument("--accept", nargs="*", metavar="KIND", help="record today's values (all kinds if none given)")
    a = ap.parse_args(argv)
    path = Path(a.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    gh = web.GitHub(os.environ.get("GITHUB_REPOSITORY"))
    if a.accept is not None:
        now = current(state, web.Fetcher(), gh)
        path.write_text(json.dumps(accept(state, now, a.accept or list(TITLES)), indent=2) + "\n", encoding="utf-8")
        return 0
    out = run(state, web.Fetcher(), gh, a.github)
    print(json.dumps({"did": out["did"], "changed_official": out["changed_official"]}, indent=1))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed_official={json.dumps(out['changed_official'])}\n")
    unwatched = [a["name"] for a in state["addons"] if not a.get("repo")]
    if unwatched:
        print(f"not watched (no keyless release feed): {', '.join(unwatched)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
