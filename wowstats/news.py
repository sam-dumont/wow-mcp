"""What's up on Forever, week by week: the weekly knowledge-base pass's
digest (tools/kb/meta.py), and what is coming.

The weekly pass reads the news and announcement feeds (Wowhead's Forever
news, Blizzard's blue posts, the guides the KB cites) and writes
data/kb/research/meta-<date>.json: the statements it proposes and, under
`digest`, that week's news, one line per item in our own words with its
category, the day it was said, its source and who it concerns (classes,
levels, roles). An item that announces something to come carries an
`event`: its name, the day it starts when one was announced (`starts`, else
null: "announced, no date"), the window when only that was given ("winter
2026"), what it changes, and the preparation the knowledge base can source
(`prep`: statement ids, a quest to start with). One pipeline: the digests
are the history, the upcoming events and the prep gaps derive from them, and
the class statements of the same lane feed the class meta (wowstats.classmeta).

The weekly job (tools/kb/lifecycle.py) then folds that file: its statements
into their lanes, its digest into data/kb/news.json (`digests`), which keeps
the newest 8 weeks; an older week keeps only its items announcing an event
still to come (live_event), the rest goes to one line in
data/kb/digest-history.json. Git keeps every week in full. An event
drops off by itself: a dated one once its day is past, an undated one once
its window ends, or UNDATED_DAYS after it was last mentioned. A newer mention
of the same event (its `key`) replaces the older one.

Nothing here is ours but the wording: no date is guessed (the weekly pass
keeps a `starts` only when the item names that day), and the pages show
actions and dates only; sources stay in the data and the MCP.

Apart from the news, `community`: what players are saying that week
(data/kb/community.json, tools/kb/community.py): the official forums' and
r/wowforever's busiest threads, each a title, a link, a day, counts and one
line in our own words. Opinions only: they never feed the events, their
dates, the prep or the class meta.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from . import classguide
from .links import wowhead_url

CATEGORIES = ("opening", "phase", "event", "patch", "hotfix", "class", "meta", "known-issue", "pvp", "economy",
              "announcement")
ROLES = ("tank", "healer", "dps")
PREP_KINDS = ("attunement", "resistance", "consumables", "gear", "reputation", "other")
# Prep gaps (the full view's "What to change", the MCP) for events this
# close; the card's one line (also on /zogzog/) for events closer still.
NEAR_DAYS = 28
SOON_DAYS = 14
# How far below an event's level a character still gets its prep: an
# attunement chain starts in the 50s for a level-60 raid.
PREP_LEVELS = 10
UNDATED_DAYS = 120
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# When an announced window ends, to drop an undated event once it is past
# (never shown as a date).
WINDOW_END = {"winter": (3, 20), "spring": (6, 20), "summer": (9, 22), "autumn": (12, 20), "fall": (12, 20),
              "early": (6, 30), "late": (12, 31)}
CITE = ("source_url", "sources", "source_type", "author", "date", "confidence", "last_verified", "statements")


def _today(today: date | None) -> date:
    return today or datetime.now(UTC).date()


def _day(v) -> date | None:
    v = str(v or "")[:10]
    try:
        return date.fromisoformat(v) if DAY.match(v) else None
    except ValueError:
        return None


def window_end(window: str | None) -> date | None:
    """The last day of an announced window ("winter 2026": the next March),
    None when it names none we can read."""
    m = re.search(r"\b(winter|spring|summer|autumn|fall|early|late)\b\D*(\d{4})", str(window or ""), re.I)
    if m:
        word, year = m.group(1).lower(), int(m.group(2))
        mo, d = WINDOW_END[word]
        return date(year + (1 if word == "winter" else 0), mo, d)
    m = re.fullmatch(r"\s*(\d{4})\s*", str(window or ""))
    return date(int(m.group(1)), 12, 31) if m else None


# ------------------------------------------------------------------ the digests


def weeks() -> list[dict]:
    """Every week's digest, newest first: {week, from, items}."""
    from . import classmeta

    out = {}
    for lane in classmeta.lanes()["weekly"]:
        for d in [lane.get("digest") or {}] + list(lane.get("digests") or []):
            if d.get("items") and _day(d.get("week")):
                out[d["week"]] = {"week": d["week"], "from": d.get("from"), "items": list(d["items"])}
    return sorted(out.values(), key=lambda w: w["week"], reverse=True)


def role_of(cls, spec) -> str:
    from . import classmeta

    return classmeta.role(cls, spec) or "dps"


def who(char: dict | None) -> dict | None:
    """{class, level, role, faction} from a character header-like dict
    (class, level, spec, faction), or None."""
    if not char:
        return None
    cls = classguide.token(char.get("class"))
    lv = char.get("level")
    lv = int(lv) if isinstance(lv, int | float) else None
    from . import classmeta

    spec = classmeta.spec_name(cls, char.get("spec")) if cls else None
    return {"class": cls, "level": lv, "spec": spec, "role": classmeta.role(cls, spec) if cls else None,
            "faction": str(char.get("faction") or "").title() or None}


def relevance(item: dict, w: dict | None) -> int:
    """How much an item (or an event) concerns a character: 0 when it doesn't,
    else higher for its class, its level range, its role. An item for
    everyone counts 1."""
    if not w:
        return 0
    classes = [classguide.token(c) for c in item.get("classes") or [] if classguide.token(c)]
    roles = [r for r in item.get("roles") or [] if r in ROLES]
    lv = item.get("levels")
    score = 1
    if classes:
        if w.get("class") not in classes:
            return 0
        score += 3
    if roles:
        if (w.get("role") or "dps") not in roles:
            return 0
        score += 2
    if lv and w.get("level") is not None:
        lo, hi = lv[0], lv[1]
        if not lo - (PREP_LEVELS if item.get("event") else 0) <= w["level"] <= hi:
            return 0
        score += 2
    faction = str(item.get("faction") or "").title()
    if faction and w.get("faction") and faction != w["faction"]:
        return 0
    return score + (1 if item.get("event") else 0)


def _public(it: dict, cite: bool) -> dict:
    keep = ("id", "category", "title", "text", "classes", "levels", "roles", "faction")
    out = {k: it[k] for k in keep if it.get(k) not in (None, "", [])}
    out["date"] = (_day(it.get("date")) or None) and str(it["date"])[:10]
    if it.get("event"):
        out["event"] = {k: it["event"].get(k) for k in ("key", "name", "starts", "window")}
    if cite:
        out.update({k: it[k] for k in CITE if it.get(k) not in (None, "", [])})
    return out


def digest(chars: list[dict] | None = None, since: str | None = None, week: str | None = None,
           cls=None, level: int | None = None, role: str | None = None, cite: bool = False,
           today: date | None = None, category: str | None = None) -> dict:
    """One week's news (the newest, or `week`), or every item said since a
    day: the items concerning the given characters first (class, level, role),
    then the rest, each newest first; and the earlier weeks there are.
    `community`: what players said that week, apart (never news).

    chars: header-like dicts (class, level, spec, faction); cls/level/role
    filter instead, keeping only what concerns them. category: only that
    category ("community": only what players said)."""
    if category and category not in (*CATEGORIES, "community"):
        raise ValueError(f"unknown category {category!r}")
    ws = weeks()
    if not ws:
        said = community(since=since, week=week, chars=chars, cls=cls, today=today) \
            if category in (None, "community") else []
        return {"available": bool(said), "why": "No weekly news yet: the knowledge base's weekly pass hasn't run.",
                "weeks": [], "items": said if category == "community" else [],
                "community": [] if category == "community" else said, "upcoming": []}
    if since and _day(since):
        pick = [w for w in ws if w["week"] >= since]
        items = [it for w in pick for it in w["items"] if (str(it.get("date") or "")[:10] >= since)]
        shown = {"week": None, "since": since}
    else:
        w = next((x for x in ws if x["week"] == week), ws[0])
        items = list(w["items"])
        shown = {"week": w["week"], "from": w.get("from")}
    whos = [x for x in (who(c) for c in chars or []) if x]
    if cls or level or role:
        f = {"class": classguide.token(cls), "level": level, "role": role, "faction": None}
        if cls and not f["class"]:
            raise ValueError(f"unknown class {cls!r}")
        items = [it for it in items if _filter(it, f)]
        whos = whos or [f]
    seen, uniq = set(), []
    for it in items:
        k = it.get("id") or it.get("text")
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    ranked = sorted(uniq, key=lambda it: str(it.get("date") or ""), reverse=True)
    ranked.sort(key=lambda it: -max([relevance(it, x) for x in whos] or [0]))
    rows = []
    for it in ranked:
        r = max([relevance(it, x) for x in whos] or [0])
        rows.append({**_public(it, cite), "for_you": r > 1 if whos else None})
    said = community(since=since, week=shown.get("week") if week else None, chars=chars, cls=cls, today=today) \
        if category in (None, "community") else []
    if category == "community":
        rows, said = said, []
    elif category:
        rows = [r for r in rows if r.get("category") == category]
    return {"available": True, **shown, "weeks": [{"week": x["week"], "from": x.get("from"), "n": len(x["items"])}
                                                  for x in ws],
            "items": rows, "community": said, "upcoming": upcoming(today=today, chars=chars, cite=cite)}


# ------------------------------------------------------------------ what players say

COMMUNITY_SOURCES = {"forums-us": "Blizzard forums (US)", "forums-eu": "Blizzard forums (EU)",
                     "reddit": "r/wowforever"}
COMMUNITY_MAX = 5
# A community week shown with the newest digest when it is this recent.
COMMUNITY_FRESH_DAYS = 14


def community_weeks() -> list[dict]:
    """Every stored week of what players said, newest first: {week, from, items}."""
    from . import classmeta

    d = classmeta._data_dir()
    doc = classmeta._read(d / "kb" / "community.json") if d and (d / "kb" / "community.json").exists() else {}
    out = [w for w in doc.get("weeks") or [] if _day(w.get("week")) and w.get("items")]
    return sorted(out, key=lambda w: w["week"], reverse=True)


def community(week: str | None = None, since: str | None = None, chars: list[dict] | None = None,
              cls=None, today: date | None = None) -> list[dict]:
    """What players are saying: the week's threads (the newest week when it is
    under COMMUNITY_FRESH_DAYS old, else `week`'s), or every one since a day;
    the most discussed first, those naming the characters' class marked
    for_you. At most COMMUNITY_MAX. Never a fact: no event, no date of one."""
    ws = community_weeks()
    if since:
        rows = [it for w in ws for it in w["items"] if str(it.get("date") or "") >= since]
    elif week:
        rows = next((list(w["items"]) for w in ws if w["week"] == week), [])
    else:
        newest = ws[0] if ws else None
        fresh = newest and _day(newest["week"]) >= _today(today) - timedelta(days=COMMUNITY_FRESH_DAYS)
        rows = list(newest["items"]) if fresh else []
    whos = [x for x in (who(c) for c in chars or []) if x and x.get("class")]
    want = classguide.token(cls) if cls else None
    out = []
    for it in rows:
        classes = [classguide.token(c) for c in it.get("classes") or [] if classguide.token(c)]
        if want and classes and want not in classes:
            continue
        mine = bool(classes) and any(w["class"] in classes for w in whos)
        row = {k: it[k] for k in ("id", "title", "text", "url", "date", "replies", "likes") if it.get(k) is not None}
        row.update(category="community", where=COMMUNITY_SOURCES.get(it.get("source"), "players"),
                   classes=classes, for_you=mine if whos else None)
        out.append(row)
    out.sort(key=lambda r: -int(bool(r.get("for_you"))))
    return out[:COMMUNITY_MAX]


def _filter(it: dict, f: dict) -> bool:
    """A filter keeps what concerns it, and the items for everyone."""
    return relevance(it, {k: v for k, v in f.items() if v is not None}) > 0


# ------------------------------------------------------------------ what's coming


def events(today: date | None = None) -> list[dict]:
    """Every event still to come, soonest first (undated ones last), each
    from its newest mention: {key, name, starts, window, when, in_days,
    changes, effects, prep, levels, roles, classes, category} plus its
    source fields."""
    today = _today(today)
    latest: dict[str, tuple[str, dict]] = {}
    for w in sorted(weeks(), key=lambda w: w["week"]):
        for it in w["items"]:
            ev = it.get("event")
            if not ev or not ev.get("key") or not ev.get("name"):
                continue
            latest[ev["key"]] = (w["week"], it)
    out = []
    for key, (wk, it) in latest.items():
        ev = it["event"]
        if ev.get("status") in ("cancelled", "done"):
            continue
        starts = _day(ev.get("starts"))
        if starts:
            if starts < today:
                continue
            n = (starts - today).days
            when = _short(starts)
        else:
            end = window_end(ev.get("window"))
            said = _day(it.get("date")) or _day(wk) or today
            if (end and end < today) or (not end and said + timedelta(days=UNDATED_DAYS) < today):
                continue
            n = None
            when = f"announced for {ev['window']}, no date" if ev.get("window") else "announced, no date"
        out.append({"key": key, "name": ev["name"], "starts": starts.isoformat() if starts else None,
                    "window": ev.get("window") or None, "when": when, "in_days": n,
                    "changes": ev.get("changes") or it.get("text"), "effects": ev.get("effects") or [],
                    "prep": ev.get("prep") or [], "category": it.get("category"),
                    "levels": ev.get("levels") or it.get("levels"), "roles": it.get("roles") or [],
                    "classes": it.get("classes") or [], "event": True,
                    **{k: it[k] for k in CITE if it.get(k) not in (None, "", [])}})
    return sorted(out, key=lambda e: (e["in_days"] is None, e["in_days"] or 0, e["name"]))


def live_event(item: dict, week, today: date | None = None) -> bool:
    """A digest item announcing an event still to come, by events()'s rules:
    a dated one until its day, an undated one until its window ends or
    UNDATED_DAYS after it was said. The weekly job keeps these past the
    digest window (tools/kb/lifecycle.py)."""
    ev = item.get("event") or {}
    if not ev.get("key") or not ev.get("name") or ev.get("status") in ("cancelled", "done"):
        return False
    today = _today(today)
    starts = _day(ev.get("starts"))
    if starts:
        return starts >= today
    end = window_end(ev.get("window"))
    said = _day(item.get("date")) or _day(week) or today
    return (end >= today) if end else said + timedelta(days=UNDATED_DAYS) >= today


def _short(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


def upcoming(today: date | None = None, chars: list[dict] | None = None, char: dict | None = None,
             spec=None, cite: bool = False) -> list[dict]:
    """The events to come, those concerning the character(s) first, each with
    what it changes for the character's class (`for_you`, the effects naming
    it). Sources only with cite."""
    whos = [x for x in (who(c) for c in ([char] if char else []) + list(chars or [])) if x]
    rows = []
    for e in events(today):
        r = max([relevance(e, x) for x in whos] or [0])
        mine = whos[0] if whos else None
        fx = [f["text"] for f in e["effects"] if mine and _effect_for(f, mine)]
        row = {k: e[k] for k in ("key", "name", "starts", "window", "when", "in_days", "changes", "category",
                                 "levels")}
        row["for_you"] = fx
        row["concerns_you"] = r > 0 if whos else None
        if cite:
            row.update({k: e[k] for k in CITE if e.get(k) not in (None, "", [])})
            row["effects"] = e["effects"]
        rows.append((r, row))
    rows.sort(key=lambda x: -(x[0] > 0))
    return [row for _, row in rows]


def _effect_for(f: dict, w: dict) -> bool:
    classes = [classguide.token(c) for c in f.get("classes") or []]
    roles = f.get("roles") or []
    return (not classes or w.get("class") in classes) and (not roles or (w.get("role") or "dps") in roles)


def soon_line(cls, spec, level, today: date | None = None) -> str | None:
    """One line for the card (and /zogzog/): the nearest event within
    SOON_DAYS that concerns this class at this level. From the class, spec
    and level only."""
    w = who({"class": cls, "spec": spec, "level": level})
    for e in events(today):
        if e["in_days"] is None or e["in_days"] > SOON_DAYS:
            continue
        if relevance(e, w) > 0:
            return f"Coming {e['when']}: {e['name']}."
    return None


# ------------------------------------------------------------------ prep gaps


def prep_gaps(char: dict, spec=None, today: date | None = None, n: int = 3) -> list[dict]:
    """What to get ready for the events within NEAR_DAYS that concern the
    character: each a move (what, where, what blocks it, the next step).
    Advice: never part of a score.

    char: header-like (class, level, spec, faction)."""
    from . import classmeta, forever

    w = who({**char, **({"spec": spec} if spec else {})})
    if not w or w.get("level") is None:
        return []
    out = []
    for e in events(today):
        if e["in_days"] is None or e["in_days"] > NEAR_DAYS or relevance(e, w) <= 0:
            continue
        need = (e.get("levels") or [None])[0]
        for p in e["prep"]:
            if p.get("faction") and w.get("faction") and p["faction"].title() != w["faction"]:
                continue
            if not _effect_for(p, w):
                continue
            gap = {"kind": "prep", "link": "#meta", "weight": 2, "advice": True, "event": e["key"],
                   "in_days": e["in_days"], "when": e["when"],
                   "title": f"Before {e['name']} ({e['when']}): {p.get('what') or p.get('kind')}"}
            texts = [t for t in (statement_text(sid) for sid in p.get("statements") or []) if t]
            blockers = []
            if need and w["level"] < need:
                blockers.append(f"level {need} (you are {w['level']})")
            q = forever.quest(p["quest_id"]) if p.get("quest_id") else None
            if q:
                gap["quest"] = {"name": q["name"], "url": wowhead_url("quest", int(p["quest_id"])),
                                "zone": q.get("zone")}
                if q.get("min_level") and w["level"] < q["min_level"]:
                    blockers.append(f"level {q['min_level']} for its first quest")
                gap["where"] = f"{q['name']}" + (f" in {q['zone']}" if q.get("zone") else "")
                gap["action"] = p.get("next") or f"Pick up {q['name']}" + (f" in {q['zone']}." if q.get("zone")
                                                                             else ".")
            else:
                gap["where"] = p.get("where")
                gap["action"] = p.get("next")
            if p.get("kind") == "consumables" and not texts:
                rows = classmeta.statements(w["class"], "consumables", classmeta.meta_spec(w["class"], w.get("spec")),
                                            max(w["level"], need or w["level"]))
                texts = [r["text"] for r in rows[:3]]
                if not texts:
                    continue
            if p.get("blocker"):
                blockers.append(p["blocker"])
            gap["detail"] = " ".join(texts[:3]) or None
            gap["blocker"] = "; ".join(blockers) or None
            out.append(gap)
    return out[:n]


_index: dict[str, dict] = {}


def statement(sid: str) -> dict | None:
    """A research statement by id (data/kb/research/*.json)."""
    from . import classmeta

    if not _index:
        d = classmeta._data_dir()
        for p in sorted((d / "kb" / "research").glob("*.json")) if d else []:
            for s in classmeta._read(p).get("statements") or []:
                if s.get("id"):
                    _index[s["id"]] = s
    return _index.get(sid)


def statement_text(sid: str) -> str | None:
    return (statement(sid) or {}).get("text")
