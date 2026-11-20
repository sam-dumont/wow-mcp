"""The knowledge base's daily watchers: deterministic, no model.

    uv run python tools/kb/watch.py            # print what changed
    uv run python tools/kb/watch.py --accept official [--only hotfixes]  # record today's values
    uv run python tools/kb/watch.py --github --why why.json   # the issues, at the end of a run

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

The refresh job (.github/workflows/kb-refresh.yaml) handles each change
itself and lands it in one PR that merges when the gates pass (owner,
2026-11-20: "the goal is automated refreshes"): the model pass on the
changed official pages, the Polestar bump and its exports, the addons'
contract check (tools/kb/bump.py), and `--accept` for what went through.
`--github` runs last: a kind back to its known value (the PR merged) closes
its issue; a kind still changed keeps one issue (label kb-watch) that says
why it wasn't merged (`--why`: {kind: reason}, written by the run's steps),
or that the run stopped before a PR.
"""

from __future__ import annotations

import argparse
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


RUN_URL = "{server}/{repo}/actions/runs/{run}"

# What a person does when the job couldn't land a kind itself.
NEXT = {
    "client-build": "Re-run the client-table export (tools/class_reference/export.py, with Polestar at "
                    "tools/polestar-tag), check the diff and the tests, then `watch.py --accept client-build`.",
    "official": "Read the refused PR's statements against the page, fix or drop what the gates name, merge; or "
                "run kb-refresh with mode refresh and the page keys once the cause is fixed.",
    "polestar": "Bump tools/polestar-tag, re-run the exports (docs/route-reference.md), check what the gates or "
                "the contract check name, then `watch.py --accept polestar`.",
    "addons": "Check what the addon saves against shipper/wow_stats.py (docs/reference/addons.md), then "
              "`watch.py --accept addons`.",
}


def run_link() -> str:
    env = os.environ
    if env.get("GITHUB_RUN_ID") and env.get("GITHUB_REPOSITORY"):
        return RUN_URL.format(server=env.get("GITHUB_SERVER_URL") or "https://github.com",
                              repo=env["GITHUB_REPOSITORY"], run=env["GITHUB_RUN_ID"])
    return "the refresh job's run"


def report(state: dict, now: dict, was: dict, why: dict | None = None) -> dict[str, str | None]:
    """{kind: issue body, or None when nothing of that kind changed}. `why`:
    {kind: why the run didn't land it}; a changed kind without one means the
    run stopped before its PR (a failed step)."""
    out: dict[str, str | None] = {}
    why = why or {}
    day = datetime.now(UTC).date().isoformat()
    for kind in TITLES:
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


def accept(state: dict, now: dict, kinds: list[str], only: list[str] | None = None) -> dict:
    """Today's values into the state, for `kinds`; `only`: these official
    page keys, not every page (the model pass read only those)."""
    if "client-build" in kinds and now["client-build"]["build"]:
        state["client_build"]["known"] = now["client-build"]["build"]
    for p in state["official"]:
        if only is not None and p["key"] not in only:
            continue
        if "official" in kinds and now["official"].get(p["key"]):
            p["known"] = now["official"][p["key"]]
    if "polestar" in kinds and now["polestar"]["release"]:
        state["polestar"]["known"] = now["polestar"]["release"]
    for a in state["addons"]:
        if "addons" in kinds and now["addons"].get(a["name"]):
            a["known"] = now["addons"][a["name"]]
    state["checked"] = datetime.now(UTC).date().isoformat()
    return state


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


def run(state: dict, fetch, gh, github: bool, why: dict | None = None) -> dict:
    now = current(state, fetch, gh)
    bodies = report(state, now, known(state), why)
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
    ap.add_argument("--only", nargs="*", metavar="KEY", help="--accept official: only these page keys")
    ap.add_argument("--release", help="--accept polestar: this tag, not a fresh read")
    a = ap.parse_args(argv)
    path = Path(a.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    gh = web.GitHub(web.repo())
    if a.accept is not None:
        kinds = a.accept or list(TITLES)
        now = current(state, web.Fetcher(), gh)
        if a.release:
            # The tag the job checked and exported (a step without GH_TOKEN can't read it again).
            now["polestar"]["release"] = a.release if TAG.match(a.release) else None
        unread = [k for k in kinds if k != "addons" and not any(
            v for n, v in now[k].items() if k != "official" or a.only is None or n in a.only)]
        if unread:
            # 2026-11-20: an accept that silently kept the old Polestar value
            # left the bump's PR red on a tag-file test.
            print(f"couldn't read today's value of: {', '.join(unread)}; nothing accepted", file=sys.stderr)
            return 3
        new = accept(state, now, kinds, a.only)
        path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        return 0
    why = json.loads(Path(a.why).read_text(encoding="utf-8")) if a.why and Path(a.why).exists() else {}
    out = run(state, web.Fetcher(), gh, a.github, why)
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
