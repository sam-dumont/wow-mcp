"""What a character's pages can't show yet, and the one thing that fixes it.

One list drives it: steps.json's `data` (each kind of data, the addons that
give it, the signals that show it arrived, the page sections it fills, the
fix) next to its `addons` (names, links, AddOns folders). /start, /help/,
the installers' preflight (the shipper's copy of the list) and this module
read the same file, so a page never hardcodes "add Questie".

A kind is missing for a character when none of its signals arrived for it
(for the account, for `scope: account` kinds such as prices). The fix comes
from the shipper's own setup report (feed `wow-setup`: which addons are
installed, whether the Recorder's switch is on): an addon that isn't there
gets "Add X", one that is there but sent nothing gets the kind's own fix.
No report yet (an older shipper): "Add X" for optional addons, the fix for
the required ones.

The two queries are the part after the stream filter; `q` runs one and returns
its rows (the views' vl, the MCP store's logs).
"""

from __future__ import annotations

import json
from collections.abc import Callable

Query = Callable[[str], list[dict]]
SETUP_FEED = "wow-setup"

# Every signal in one query (a page load's budget, tests/test_bounded_reads):
# feeds by character, plus the two that are a field of a feed (quests by
# source, Altoholic's /played); rows without a char are the account's.
ARRIVED_Q = "feed:* | stats by (char, feed, source, kind) count() as n"
SETUP_Q = f'feed:="{SETUP_FEED}" | fields _time, addons, recorder_on, saved_chars | sort by (_time desc) | limit 1'


def _steps() -> dict:
    from wowstats import account

    return account.steps()


def kinds() -> list[dict]:
    return list(_steps().get("data") or [])


def addons() -> dict[str, dict]:
    return {a["id"]: a for a in _steps().get("addons") or []}


def norm(c: str) -> str:
    """'Testpriest Fixture' and 'Testpriest-Fixture' are one character (views.norm_char)."""
    return str(c or "").strip().replace(" ", "-")


def arrived(q: Query) -> tuple[dict[str, set[str]], set[str]]:
    """(signals per character, signals for the whole account). A character's
    signals count for the account too: prices come with the character who
    scanned, and an account-wide kind is there once anyone has it."""
    per: dict[str, set[str]] = {}
    acct: set[str] = set()

    def add(char, sig: str) -> None:
        if char:
            per.setdefault(norm(char), set()).add(sig)
        acct.add(sig)

    for r in q(ARRIVED_Q):
        feed, char = r.get("feed"), r.get("char")
        if not feed or feed == SETUP_FEED:
            continue
        add(char, feed)
        if feed == "wow-quests" and r.get("source"):
            add(char, f"quests:{r['source']}")
        elif feed == "wow-character" and r.get("kind") == "played":
            add(char, "played")
    return per, acct


def setup(q: Query) -> dict | None:
    """The shipper's newest setup report: {at, addons: {id: version}, recorder_on, saved_chars}."""
    rows = sorted(q(SETUP_Q), key=lambda r: str(r.get("_time") or ""), reverse=True)
    if not rows:
        return None
    r = rows[0]
    try:
        got = json.loads(r.get("addons") or "{}")
    except ValueError:
        got = {}
    on = r.get("recorder_on")
    try:
        saved = int(float(r.get("saved_chars"))) if r.get("saved_chars") not in (None, "") else None
    except (TypeError, ValueError):
        saved = None
    return {"at": r.get("_time"), "addons": {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {},
            "recorder_on": {"true": True, "false": False}.get(str(on).lower()), "saved_chars": saved}


def _names(ids: list[str], known: dict[str, dict]) -> str:
    names = [known.get(i, {}).get("name", i) for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def line_for(kind: dict, rep: dict | None, known: dict[str, dict] | None = None) -> tuple[str, list[dict]]:
    """The fix for one missing kind, and the addons it names (for links)."""
    known = known or addons()
    ids = kind["addons"]
    if rep is not None:
        have = [i for i in ids if i in rep["addons"]]
        if not have:
            return f"Add {_names(ids, known)}.", [known[i] for i in ids if i in known]
        return kind["fix"], []
    if all(known.get(i, {}).get("required") for i in ids):
        return kind["fix"], []
    return f"Add {_names(ids, known)}.", [known[i] for i in ids if i in known]


def missing(signals: set[str], acct: set[str], rep: dict | None) -> list[dict]:
    """The kinds a character lacks, in the list's order: {id, what, fix,
    line, sections, partial, addons: [{id, name, url}]}. partial: the
    sections still show the rest (quests done before installing); otherwise
    they are empty without it, and the line replaces their own empty note."""
    known = addons()
    out = []
    for k in kinds():
        have = acct if k.get("scope") == "account" else signals
        if any(s in have for s in k["signals"]):
            continue
        fix, named = line_for(k, rep, known)
        out.append({"id": k["id"], "what": k["what"], "fix": fix, "line": f"Missing: {k['what']}. {fix}",
                    "sections": list(k["sections"]), "partial": bool(k.get("partial")),
                    "addons": [{"id": a["id"], "name": a["name"], "url": a.get("url")} for a in named]})
    return out


NOTHING = {"id": "nothing", "what": "everything", "fix": "Play a bit, then log out.",
           "line": "Nothing arrived yet. Play a bit, then log out.", "sections": [], "addons": []}


def snapshot(q: Query) -> dict:
    """What arrived and the setup report, once for every character (the
    callers cache it: three stats queries and one row)."""
    per, acct = arrived(q)
    return {"per": {c: sorted(s) for c, s in per.items()}, "acct": sorted(acct), "setup": setup(q)}


def for_char(snap: dict, char: str) -> list[dict]:
    """What `char`'s pages can't show. Nothing at all arrived for it: one
    line, play a bit (its sections would all say the same)."""
    sig = set(snap["per"].get(norm(char)) or ())
    if not sig:
        return [dict(NOTHING)]
    return missing(sig, set(snap["acct"]), snap["setup"])


def for_account(snap: dict) -> dict:
    """/api/me/status' part: the setup report and, per kind, the characters
    that lack it (the start page shows the kind once, with the fix)."""
    out: dict[str, dict] = {}
    for char, sig in sorted(snap["per"].items()):
        for m in missing(set(sig), set(snap["acct"]), snap["setup"]):
            e = out.setdefault(m["id"], {**{k: m[k] for k in ("id", "what", "fix", "line", "addons")}, "chars": []})
            e["chars"].append(char)
    if not snap["per"]:
        out = {"nothing": {**{k: v for k, v in NOTHING.items() if k != "sections"}, "chars": []}}
    return {"setup": snap["setup"], "missing": list(out.values())}
