"""The class guide over MCP (wowstats.classguide): how to level the
character's class at its level, from the knowledge base's `classes` lane
and Forever's own trainer and talent data, every statement with its source.
"""

from __future__ import annotations

from . import tools as T

CHAR = T.CHAR


def _who(ctx: T.Ctx, char: str) -> dict:
    """The character's class, race, side and level, and its talents as
    Battle.net reads them now (T.current_talents: live from vm-wow, never
    kept; None without them)."""
    st = ctx.store
    snap = st.logs(f'feed:="wow-character" {T.cf(char)} -kind:=played | fields _time, level, class, race, faction '
                   f"| sort by (_time desc) | limit 1", limit=1)
    last = st.logs(f"{T.rec(char)} kind:in(xp, level) | fields _time, level | sort by (_time desc) | limit 1",
                   limit=1)
    sheet = st.logs(f'feed:="wow-gear" {T.cf(char)} | fields _time, class, race, faction | sort by (_time desc) '
                    f"| limit 1", limit=1)
    s, r, g = (snap or [{}])[0], (last or [{}])[0], (sheet or [{}])[0]
    who = {**T.who(ctx, char), **{k: g[k] for k in ("class", "race", "faction") if g.get(k)},
           **{k: s[k] for k in ("class", "race", "faction") if s.get(k)}}
    newer = r if T.parse_t(r.get("_time")) >= T.parse_t(s.get("_time")) else s
    who["level"] = T.opt_num(newer.get("level")) or T.opt_num(s.get("level")) or T.opt_num(r.get("level"))
    who["talents"], _ = T.current_talents(ctx, char, who.get("class"))
    return who


def _no_class(char: str | None) -> dict:
    """No class known for the character (only level splits arrived): an
    answer that says so and how to ask, never an error."""
    from wowstats import classguide

    return {"char": char, "answer": None, "why": "the character's class isn't known yet",
            "classes": [x.title() for x in classguide.CLASSES],
            "unlock": "Log in once with Polestar on, then log out; or ask again with `class`."}


def t_class_guide(ctx: T.Ctx, args: dict) -> dict:
    from wowstats import classguide

    cls = str(args.get("class") or "").strip()
    who: dict = {}
    char = None
    if not cls or args.get("char") or ctx.default_char:
        try:
            char = T.resolve_char(ctx, args)
            who = _who(ctx, char)
        except T.ToolError:
            if not cls:
                raise
    asked = cls or who.get("class")
    if not asked:
        return _no_class(char)
    if cls and classguide.token(cls) != classguide.token(who.get("class")):
        # Another class: the character's race, level and talents don't apply;
        # its side still says which trainers it can reach.
        who = {"faction": who.get("faction")} if who.get("faction") else {}
    level = T.opt_num(args.get("level")) or who.get("level") or 1
    try:
        g = classguide.guide(asked, int(level), who.get("race"), who.get("faction"), who.get("talents"))
    except ValueError as e:
        raise T.ToolError(str(e)) from None
    # The next bracket repeats what still holds from this one: say so instead
    # of sending it twice (the answer stays under tools.KB_CHARS).
    now, nxt = g.get("now") or {}, g.get("next") or {}
    same = [k for k in list(nxt) if k != "levels" and nxt[k] and nxt[k] == now.get(k)]
    for k in same:
        nxt.pop(k)
    if same:
        nxt["same_as_now"] = same
    # A list that only partly repeats: the statements already in `now` go
    # by id (same_ids_as_now), so the answer's room goes to its citations.
    kept_ids = []
    for k, v in list(nxt.items()):
        if k == "levels" or not isinstance(v, list) or not isinstance(now.get(k), list):
            continue
        seen = {x.get("id") for x in now[k] if isinstance(x, dict) and x.get("id")}
        rest = [x for x in v if not (isinstance(x, dict) and x.get("id") in seen and x in now[k])]
        if len(rest) < len(v):
            kept_ids += [x["id"] for x in v if x not in rest]
            nxt[k] = rest
    if kept_ids:
        nxt["same_ids_as_now"] = kept_ids
    if g.get("stats") and g["stats"] == now.get("stats"):
        g.pop("stats")
    # The class-wide lists, kept to the two brackets asked about.
    lo = (now.get("levels") or [int(level)])[0]
    hi = (nxt.get("levels") or now.get("levels") or [int(level)])[-1]
    for k in ("talents", "spells", "contradictions"):
        if isinstance(g.get(k), list):
            g[k] = [s for s in g[k] if not isinstance(s, dict) or not s.get("levels")
                    or (s["levels"][0] <= hi and s["levels"][-1] >= lo)]
    return {"char": char if who.get("class") else None, **g}


META_HOW = ("score: 0-100, the weighted mean of the parts measured (talents 40, gear 40, enchants 20; a part "
            "with no data is left out, never counted as 0). talents: the share of the levelling build's points "
            "(the class path at the character's level) the character has where the build puts them. gear: worn "
            "points over worn plus each slot's best gain now, with Polestar's stat weights for the spec (the gear "
            "advice's scoring). enchants: the enchantable slots the meta names an enchant for at this level, "
            "enchanted or not (only when the item links say). gaps: the moves that cost the score most, each with "
            "what, where and what blocks it. meta: the knowledge base's statements with their source, date, "
            "confidence and scope; as_of is the newest day they were checked or changed, stale past 21 days. "
            "Consumables and professions are described, not measured. A tank or healer spec's talents score against "
            "the levelling build before 60 and against the role's endgame build at 60 (meta.build.target); with no "
            "sourced build the part is 'no target yet' and left out. meta.coming_up: what is coming on Forever "
            "(raid or dungeon openings, phases, rulesets) with its day, or 'announced, no date' when none was "
            "announced (never guess one), what it changes for this class, its source; adherence.prep: what to "
            "get ready for an event within 4 weeks that concerns the character (advice, not part of the score).")


def t_class_meta(ctx: T.Ctx, args: dict) -> dict:
    """The state of the meta for a class and spec (wowstats.classmeta), and,
    for the character, how close it is to it."""
    from wowstats import classguide, classmeta

    cls = str(args.get("class") or "").strip()
    spec = str(args.get("spec") or "").strip() or None
    char = None
    if not cls or args.get("char") or ctx.default_char:
        try:
            char = T.resolve_char(ctx, args)
        except T.ToolError:
            if not cls:
                raise
    if char:
        v = T._views(ctx)
        v.chars()  # the character's aliases (Name-Realm and Name Realm are one)
        data = v.char(char)
        hd = dict(data.get("header") or {})
        if not hd.get("class") or not hd.get("level"):
            # The page's answer reads the newest sheet; the class guide's
            # reads (_who) also take the Recorder's and the snapshots'.
            who = _who(ctx, char)
            hd.update({k: who[k] for k in ("class", "race", "faction", "level") if who.get(k) and not hd.get(k)})
            if isinstance(hd.get("level"), float):
                hd["level"] = int(hd["level"])
            tal = data.get("talents") if (data.get("talents") or {}).get("available") else who.get("talents")
            data = {**data, "header": hd, "talents": tal}
        mine = hd.get("class")
        if not cls and not mine:
            return _no_class(char)
        if not cls or classguide.token(cls) == classguide.token(mine):
            if spec:
                data = {**data, "spec": {**(data.get("spec") or {}), "name": spec}}
            a = v.meta(char, data, cite=True)
            if not a.get("available"):
                raise T.ToolError(a.get("why") or "no meta for this class yet")
            return {**a, "how": META_HOW}
    if not cls:
        raise T.ToolError("the character's class isn't known yet: name one with `class`")
    if not classguide.token(cls):
        raise T.ToolError(f"unknown class {cls!r}: one of {', '.join(x.title() for x in classguide.CLASSES)}")
    level = T.opt_num(args.get("level"))
    d = classmeta.describe(cls, spec, int(level) if level else None, cite=True)
    if not d:
        raise T.ToolError(f"the knowledge base has no meta for {cls} yet")
    return {"char": None, "meta": d, "adherence": None,
            "how": "Another class than the character's: the meta only, no adherence. " + META_HOW}


NEWS_HOW = ("Each item is one line in our own words from the weekly knowledge-base pass over Forever's news "
            "feeds (Wowhead's Forever news, Blizzard's blue posts, the guides the KB cites), with its category, "
            "the day it was said, its source and who it concerns (classes, levels, roles). for_you: it concerns "
            "the character (class, level, role). upcoming: events to come, soonest first; `starts` is the "
            "announced day, null when none was announced ('announced, no date'): never guess one. Past events "
            "are left out. community: what players are saying that week on the official forums and "
            "r/wowforever, each thread a title, a link, a day, its counts and one line in our own words: opinions, "
            "never a fact or a date; cite them as what players say. category=community returns only those.")


def t_news(ctx: T.Ctx, args: dict) -> dict:
    """What's up on Forever this week (or since a day), for the character
    or a class/level/role."""
    from wowstats import classguide, news

    since = str(args.get("since") or "").strip() or None
    if since and not news.DAY.match(since):
        raise T.ToolError("since is YYYY-MM-DD")
    week = str(args.get("week") or "").strip() or None
    if week and not news.DAY.match(week):
        raise T.ToolError("week is YYYY-MM-DD")
    cls = str(args.get("class") or "").strip() or None
    if cls and not classguide.token(cls):
        raise T.ToolError(f"unknown class {cls!r}: one of {', '.join(x.title() for x in classguide.CLASSES)}")
    level = T.opt_num(args.get("level"))
    role = args.get("role") or None
    category = str(args.get("category") or "").strip().lower() or None
    if category and category not in (*news.CATEGORIES, "community"):
        raise T.ToolError(f"unknown category {category!r}: one of {', '.join((*news.CATEGORIES, 'community'))}")
    chars = []
    char = None
    if not (cls or level or role) and (args.get("char") or ctx.default_char):
        try:
            char = T.resolve_char(ctx, args)
        except T.ToolError:
            char = None
        if char:
            who = _who(ctx, char)
            spec = (who.get("talents") or {}).get("spec") if isinstance(who.get("talents"), dict) else None
            chars = [{**who, "spec": spec}]
    out = news.digest(chars=chars, since=since, week=week, cls=cls, level=int(level) if level else None,
                      role=role, cite=True, category=category)
    return {"char": char, **out, "how": NEWS_HOW}


TALENT_WHY = {
    "no_battlenet": "Battle.net hasn't read this character's talents in the last 3 weeks (it reads each "
                    "character once a day once it knows it; a site without a Battle.net client has none).",
    "unreachable": "The talent history can't be read right now (the Battle.net store didn't answer).",
    "no_client": "No Battle.net API client on this site: no talents, so no talent history.",
}


def t_talent_changes(ctx: T.Ctx, args: dict) -> dict:
    """How the talents changed (wowstats.views.talent_history), for the
    caller's own account: WoWSims Exporter's saves when the player has the
    addon (every change, their own events), else Battle.net's last 3 weeks,
    read live from vm-wow and never kept, behind the Battle.net switch.
    The same answer as the character page."""
    char = T.resolve_char(ctx, args)
    h = T._views(ctx).talent_history(char, _who(ctx, char).get("class"))
    if h["available"]:
        why = None if h["changes"] else f"No talent change since {h['since']}."
    elif not T.bnet_vm_url(ctx) and ctx.store.vm_url:
        why = T.BNET_OFF
    else:
        why = TALENT_WHY.get(h.get("why") or "") or TALENT_WHY["no_battlenet"]
    full = h.get("source") == "addon"
    return {"char": char, "days": h["days"], "source": "WoWSims Exporter" if full else (
                "Battle.net" if h["available"] else None),
            "changes": h["changes"] if h["available"] else None, "why": why,
            "not_recorded": h.get("not_recorded") or [],
            "full_history": None if full else "Install WoWSims Exporter and tick it in AddOns for every change, "
                                               "not only the last 3 weeks.",
            "how": "Each change: the day, points per tree before and after, talents gained or dropped with their "
                   "rank before and after. Newest first. not_recorded: talents learned that the addon can't save."}


CLASS_TOOLS = (
    T.Tool("class_guide", "Class guide",
           "How to level the character's class at its level on Forever: spec and talent picks for this bracket "
           "and the next, trainer spells to buy now and next and where, stats, solo play, dungeon role, class "
           "quests, mistakes, the respec moment. Each statement sourced (date, confidence, scope); contradictions "
           "listed. `class` and `level` ask about another.",
           T.schema({"char": CHAR,
                     "class": {"type": "string", "description": "A class to ask about instead of the character's, "
                                                                "e.g. mage."},
                     "level": {"type": "integer", "minimum": 1, "maximum": 60,
                               "description": "The level to ask about. Default: the character's."}}),
           t_class_guide),
    T.Tool("class_meta", "State of the meta",
           "How close am I to the meta? The Forever meta for the character's class and spec (specs, build, "
           "stats, gear, enchants, consumables, professions, recent changes; sourced and dated) and a 0-100 "
           "score with its parts and the top gaps, each with where to fix it. `class` asks about another "
           "class, without the score.",
           T.schema({"char": CHAR,
                     "class": {"type": "string", "description": "A class to ask about instead of the character's."},
                     "spec": {"type": "string", "description": "A spec (talent tree) to ask about, e.g. Fury. "
                                                               "Default: the character's, else the levelling one."},
                     "level": {"type": "integer", "minimum": 1, "maximum": 60,
                               "description": "Another class's level to ask about."}}),
           t_class_meta),
    T.Tool("talent_changes", "Talent changes",
           "How the character's talents changed: every change when the player has WoWSims Exporter, else "
           "the last 3 weeks of Battle.net's reads. Each: the day, the points per tree before and after, "
           "the talents gained or dropped (rank before and after). Newest first.",
           T.schema({"char": CHAR}), t_talent_changes),
    T.Tool("news", "This week on Forever",
           "New on Forever: this week's news digest (patches, hotfixes, class changes, openings, phases, "
           "known issues), or everything since a day, what concerns the character first; what is coming, with "
           "its announced day or 'announced, no date'; and what players say (community: opinions). "
           "Sourced, dated. `class`, `level`, `role` filter instead; `category` keeps one.",
           T.schema({"char": CHAR,
                     "since": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                               "description": "Everything said since this day (YYYY-MM-DD) instead of one week."},
                     "week": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                              "description": "An earlier week's digest, by its day (from `weeks`)."},
                     "class": {"type": "string", "description": "Only what concerns this class (and everyone)."},
                     "level": {"type": "integer", "minimum": 1, "maximum": 60,
                               "description": "Only what concerns this level (and everyone)."},
                     "role": {"type": "string", "enum": ["tank", "healer", "dps"],
                              "description": "Only what concerns this role (and everyone)."},
                     "category": {"type": "string",
                                  "enum": ["opening", "phase", "event", "patch", "hotfix", "class", "meta",
                                           "known-issue", "pvp", "economy", "announcement", "community"],
                                  "description": "Only this category. community: what players are saying "
                                                 "(forums, r/wowforever), opinions, never facts."}}),
           t_news),
)
