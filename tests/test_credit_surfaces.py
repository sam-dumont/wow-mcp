"""Every place the page shows a gear score links ForeverDB.net (owner,
2026-11-24: "Their link every time as we reverse engineered the tables").

A static read of web/app.js: a function that writes a score (an upgrade's
gain or BiS share, a standing, a dungeon's gain) either shows the credit
itself (creditEl, or dollBadges, whose legend carries it) or is only used by
functions that do, all the way up. A new surface that forgets the link fails
here. The MCP side is tests/test_degraded.py
test_every_gear_score_credits_foreverdb."""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.js"

# What writes a score on the page.
SCORES = re.compile(r"\bupText\(|\bupLine\(|\bgainText\(|\bgainLine\(|\.bis_pct\b|\.bis_unknown\b|\bst\.pct\b")
# What shows the credit.
CREDITS = re.compile(r"\bcreditEl\(|\bdollBadges\(")
# The formatters themselves: they make the words, their callers show them.
# badgeFor and itemChip draw an item's badge only when its row carries
# bis_pct, which only the scored answers' rows do (the advice, dungeons,
# moves: credited above); loot and snapshot rows never carry one.
FORMATTERS = {"upText", "upLine", "upShort", "gainText", "gainLine", "bisTier", "badgeFor", "creditEl"}
DECL = re.compile(r"^(?:async\s+)?function\s+(\w+)|^const\s+(\w+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>", re.M)


def functions(src: str) -> dict[str, str]:
    """Top-level function name -> its text (up to the next declaration)."""
    marks = [(m.start(), m.group(1) or m.group(2)) for m in DECL.finditer(src)]
    out = {}
    for (start, name), (end, _) in zip(marks, [*marks[1:], (len(src), "")], strict=True):
        out[name] = src[start:end]
    return out


def bare(src: str) -> list[str]:
    """The functions that show a score with no credit on any path to them."""
    fns = functions(src)
    users: dict[str, set[str]] = {n: set() for n in fns}
    for caller, body in fns.items():
        for name in fns:
            if name != caller and re.search(rf"\b{name}\b", body):
                users[name].add(caller)

    def credited(name: str, seen: frozenset = frozenset()) -> bool:
        if CREDITS.search(fns[name]):
            return True
        callers = users[name] - seen
        return bool(callers) and all(credited(c, seen | {name}) for c in callers)

    return sorted(n for n, body in fns.items() if n not in FORMATTERS and SCORES.search(body) and not credited(n))


def test_every_score_on_the_page_credits_foreverdb():
    src = APP.read_text(encoding="utf-8")
    assert {"renderAdvice", "moveEl", "dungeonBlock", "dropRow", "creditEl"} <= set(functions(src))
    assert bare(src) == [], "shows a gear score without the ForeverDB.net link"


def test_the_scan_sees_a_bare_score():
    """A new surface that forgets the link is caught."""
    src = APP.read_text(encoding="utf-8") + "\nfunction forgetful(u) {\n  return h('p', { text: upText(u) });\n}\n"
    assert bare(src) == ["forgetful"]


def test_every_foreverdb_source_on_the_page_links_it():
    """ForeverDB's item sources (wowstats/gear.py, via "foreverdb") show how
    sure they are and the link, wherever a source shows: basisEl, in every
    branch of sourceEl and in sourceLine."""
    src = APP.read_text(encoding="utf-8")
    fns = functions(src)
    assert re.search(r"const SOURCE_CREDIT = \{[^}]*https://foreverdb\.net", src)
    assert "SOURCE_CREDIT" in fns["basisEl"]
    body = fns["sourceEl"]
    branches = [b for b in body.split("return ")[1:] if b.startswith("h('span', { class: 'src' }")]
    assert len(branches) >= 9
    # The auction house's own listing (no source) is the one branch without it.
    assert sum("basisEl(sr)" not in b for b in branches) <= 1, branches
    assert "basisEl(src)" in fns["sourceLine"]
