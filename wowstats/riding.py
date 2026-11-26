"""The riding rank a character has, from every read that can see it.

Polestar Recorder writes a skill record only when the skill list changes
while it records (its login baseline is silent), so a riding training it
didn't see never shows: the same blind spot as the profession caps
(wowstats.profcaps, 2026-11-24). DataStore_Crafts has no riding (it keeps
the professions only). What does see it:

1. Altoholic's spellbook (DataStore_Spells, feed wow-altoholic kind spells,
   rescanned at login): the riding trainings are spells, and DataStore
   itself picks them out of the scan (DataStore_Spells.lua: a spell of
   Enum.RidingSkills with level 0 becomes `ridingSkill`). Forever's spell
   data teaches Apprentice Riding as 33388 (kb-reference: the class mount
   quests' spells teach it too). A full reading of the book ("game");
2. the Recorder's learn records of those spells and its skill records of
   the Riding skill line 762 ("recorder"): true when there, silent about
   what it didn't see.

Ranks only go up: the highest wins, the game's on a tie. A rank of 0 is
never a fact past the tier's level: nothing proves the Forever client
lists riding in the spellbook until one of its spells has been seen there,
so a move built on 0 says "if not trained yet" with the day of the read.
"""

from __future__ import annotations

import json

from .stores import num, opt_num, parse_t

SKILL = 762
# DataStore_Spells Enum.RidingSkills, with the skill rank each trains.
SPELL_RANK = {33388: 75, 33391: 150, 34090: 225, 34091: 300, 90265: 375}
_IDS = ", ".join(str(s) for s in SPELL_RANK)

# The LogsQL reads, after the stream filter: the newest spellbook only (each
# save is the whole book), the Recorder's learn records of a riding spell.
Q_BOOK = 'feed:="wow-altoholic" {cf} kind:=spells | sort by (_time desc) | limit 1 | fields _time, spells'
Q_LEARN = "{rec} kind:=learn spell_id:in(" + _IDS + ") | fields _time, spell_id"


def queries(cf: str, rec: str) -> dict[str, str]:
    return {"riding_book": Q_BOOK.format(cf=cf), "riding_learn": Q_LEARN.format(rec=rec)}


def _pairs(v) -> list:
    """The spellbook's [[spell id, level]] as an event carries it: a list,
    or the JSON string the store makes of a nested value."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def book_rank(spells) -> int:
    """The highest riding rank a spellbook lists known (level 0), 0 for none."""
    got = [SPELL_RANK.get(int(num(p[0])), 0) for p in _pairs(spells)
           if isinstance(p, list) and len(p) == 2 and opt_num(p[1]) == 0]
    return max(got, default=0)


def _ts(t) -> float:
    if isinstance(t, int | float):
        return float(t)
    return parse_t(t).timestamp() if t else 0.0


def state(skill: dict | None, book: list[dict] | None = None, learned: list[dict] | None = None) -> dict:
    """{rank, max_rank, t, from, book_t} for the Riding skill: `skill` the
    Recorder's newest skill record of line 762 ({rank, max_rank, t}),
    `book` the newest Altoholic spellbook rows ({_time, spells}), `learned`
    the Recorder's learn records of a riding spell ({_time, spell_id}).
    from: "game" (the spellbook), "recorder", or None (no read saw riding);
    t: the winning read's time; book_t: when the spellbook was read (the
    hedge's "as of"), else None. Times as the rows carry them."""
    newest = max(book or [], key=lambda r: _ts(r.get("_time")), default=None)
    seen = [(book_rank(newest.get("spells")), newest.get("_time"), "game")] if newest else []
    rec = skill or {}
    seen.append((int(num(rec.get("rank"))), rec.get("t"), "recorder"))
    seen += [(SPELL_RANK.get(int(num(r.get("spell_id"))), 0), r.get("_time"), "recorder") for r in learned or []]
    rank, t, src = max(seen, key=lambda s: (s[0], s[2] == "game", _ts(s[1])))
    return {"rank": rank, "max_rank": max(rank, int(num(rec.get("max_rank")))), "t": t if rank else None,
            "from": src if rank else None, "book_t": newest.get("_time") if newest else None}


def merge(skills: dict[int, dict] | None, book: list[dict] | None, learned: list[dict] | None) -> dict[int, dict]:
    """The Recorder's skills ({skill id: {rank, max_rank, t}}) with Riding's
    entry made from every read (state): what riding_claims and the mount
    reads take."""
    out = dict(skills or {})
    st = state(out.get(SKILL), book, learned)
    if st["rank"] or st["book_t"] is not None or SKILL in out:
        out[SKILL] = st
    return out

def class_route(skill: int, who: dict, done=(), log=()) -> dict | None:
    """A mount quest this character can use, including the post-hotfix catch-up."""
    from wowstats.analysis import kb

    cls, side = (who.get("class") or "").upper(), who.get("faction")
    if cls not in ("PALADIN", "WARLOCK") or side not in ("Alliance", "Horde"):
        return None
    rule = kb.static_rule("riding.class_quests") or {}
    for row in (rule.get("value") or {}).get("routes", []):
        if row["cls"] != cls or row["skill"] != skill or row.get("faction", side) != side:
            continue
        if row.get("races") and (who.get("race") or "").upper() not in row["races"]:
            continue
        qid = row["quest_id"]
        state = "done" if qid in done else "open" if qid in log else "new"
        after = row.get("after") or (row.get("after_by_side") or {}).get(side)
        return {"quest_id": qid, "name": row["name"], "state": state,
                "action": after if after and state == "done" else row["action"],
                "fulfilled": state == "done" and after is None,
                "remaining_copper": 0 if state == "done" else row.get("remaining_copper"),
                "source_url": row["source"], "riding_source_url": rule["source_url"]}
    return None
