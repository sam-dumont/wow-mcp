"""What a character's pages can't show yet, and the one thing that fixes it.

One list drives it: steps.json's `data` (each kind of data, the addons that
give it, the signals that show it arrived, the page sections it fills, the
fix) next to its `addons` (names, links, AddOns folders). /start, /help/,
the installers' preflight (the shipper's copy of the list) and this module
read the same file, so a page never hardcodes "add Questie".

A kind is missing for a character when none of its signals arrived for it
(for the account, for `scope: account` kinds such as prices). The fix comes
from the shipper's own setup report (feed `wow-setup`: which addons are
installed, which ones each character doesn't load, whether the Recorder's
switch is on), never from the missing data alone:
- none of the kind's addons installed: "Add X";
- installed, but unticked for this character: say so, and where to tick it;
- installed and ticked: the data just hasn't come yet, the kind's `wait`
  (play to your next level, log out or /reload once);
- no report (a shipper older than the report): both, the wait first, then
  what to check; a gap on its own can't tell "not installed" from "not yet".
A character with nothing at all gets one line (NOTHING), never a list of
addons; so does an account without a character.

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
SETUP_Q = (f'feed:="{SETUP_FEED}" | fields _time, addons, recorder_on, saved_chars, off, shipper '
           '| sort by (_time desc) | limit 1')


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


def _json(v) -> object:
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v or "{}")
    except (TypeError, ValueError):
        return {}


def setup(q: Query) -> dict | None:
    """The shipper's newest setup report: {at, addons: {id: version},
    recorder_on, saved_chars, off: {char: [addon ids it doesn't load]},
    shipper: its version}. `off` is None from a shipper that doesn't say
    (older than 0.5), `shipper` from one that can't update itself."""
    rows = sorted(q(SETUP_Q), key=lambda r: str(r.get("_time") or ""), reverse=True)
    if not rows:
        return None
    r = rows[0]
    got = _json(r.get("addons"))
    raw_off = _json(r.get("off")) if r.get("off") not in (None, "") else None
    off = ({norm(c): sorted(str(i) for i in ids) for c, ids in raw_off.items() if isinstance(ids, list)}
           if isinstance(raw_off, dict) else None)
    on = r.get("recorder_on")
    try:
        saved = int(float(r.get("saved_chars"))) if r.get("saved_chars") not in (None, "") else None
    except (TypeError, ValueError):
        saved = None
    return {"at": r.get("_time"), "addons": {str(k): str(v) for k, v in got.items()} if isinstance(got, dict) else {},
            "recorder_on": {"true": True, "false": False}.get(str(on).lower()), "saved_chars": saved,
            "off": off, "shipper": str(r.get("shipper") or "") or None}


def _names(ids: list[str], known: dict[str, dict], last: str = "or") -> str:
    names = [known.get(i, {}).get("name", i) for i in ids]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" {last} " + names[-1]


def _links(ids: list[str], known: dict[str, dict]) -> list[dict]:
    return [known[i] for i in ids if i in known]


WAIT = "Log in with this character, then log out or type /reload once."


def line_for(kind: dict, rep: dict | None, known: dict[str, dict] | None = None,
             char: str | None = None) -> tuple[str, list[dict]]:
    """The fix for one missing kind on `char`, and the addons it names (for
    links). See the module's docstring for the four cases."""
    known = known or addons()
    ids = kind["addons"]
    wait = kind.get("wait") or WAIT
    if rep is None:
        return (f"{wait} Still nothing after that? Check that {_names(ids, known)} is installed "
                "and ticked in AddOns at character select."), _links(ids, known)
    have = [i for i in ids if i in rep["addons"]]
    if not have:
        return f"Add {_names(ids, known)}.", _links(ids, known)
    if "recorder" in have and rep.get("recorder_on") is False:
        return kind["fix"], []
    # Which characters skip an addon only means something for a character's
    # own kinds; an account-wide one (prices) comes from whoever has it on.
    off = set(((rep.get("off") or {}).get(norm(char)) or ()) if char and kind.get("scope") != "account" else ())
    if all(i in off for i in have):
        verb = "is" if len(have) == 1 else "are"
        return (f"{_names(have, known, 'and')} {verb} installed but not ticked for this character: "
                f"tick {'it' if len(have) == 1 else 'one'} in AddOns at character select, then log in."), []
    return wait, []


def missing(signals: set[str], acct: set[str], rep: dict | None, char: str | None = None) -> list[dict]:
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
        fix, named = line_for(k, rep, known, char)
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
    return missing(sig, set(snap["acct"]), snap["setup"], char)


def _who(chars: list[str]) -> str:
    return chars[0] if len(chars) == 1 else ", ".join(chars[:-1]) + " and " + chars[-1]


def for_account(snap: dict) -> dict:
    """/api/me/status' part: the setup report and the gaps, once per kind
    and fix, each with the characters it concerns (`chars`; `who` and the
    `line`, "Missing for Alt-Realm: ...", name them unless the kind is the
    account's): on the account page a line that doesn't say whose it is
    reads as the main character's."""
    out: dict[tuple[str, str], dict] = {}
    for char, sig in sorted(snap["per"].items()):
        for m in missing(set(sig), set(snap["acct"]), snap["setup"], char):
            e = out.setdefault((m["id"], m["fix"]), {**{k: m[k] for k in ("id", "what", "fix", "addons")}, "chars": []})
            e["chars"].append(char)
    scope = {k["id"]: k.get("scope") for k in kinds()}
    for e in out.values():
        # An account-wide kind (prices) is no one character's.
        e["who"] = "" if scope.get(e["id"]) == "account" else _who(e["chars"])
        e["line"] = f"Missing{' for ' + e['who'] if e['who'] else ''}: {e['what']}. {e['fix']}"
    gaps = list(out.values())
    if not snap["per"]:
        gaps = [{**{k: v for k, v in NOTHING.items() if k != "sections"}, "chars": []}]
    return {"setup": snap["setup"], "missing": gaps}
