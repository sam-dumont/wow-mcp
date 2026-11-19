"""The class guide over MCP (wowstats.classguide): how to level the
character's class at its level, from the knowledge base's `classes` lane
and Forever's own trainer and talent data, every statement with its source.
"""

from __future__ import annotations

from . import tools as T

CHAR = T.CHAR


def _who(ctx: T.Ctx, char: str) -> dict:
    """The character's class, race, side and level, and its talent exports
    (the newest sheets that carry one)."""
    from wowstats import talents

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
    rows = st.logs(f'feed:="wow-gear" {T.cf(char)} (talent_ranks:* or talent_nodes:*) | fields _time, talent_ranks, '
                   f"talent_nodes, talents_level, talents_date | sort by (_time desc) | limit 50", limit=50)
    tal = None
    if rows and who.get("class"):
        tal, _ = talents.from_rows(talents.shared().get(), rows, who["class"])
    who["talents"] = tal
    return who


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
        raise T.ToolError("the character's class isn't known yet: name one with `class`")
    if cls and classguide.token(cls) != classguide.token(who.get("class")):
        # Another class: the character's race, level and talents don't apply;
        # its side still says which trainers it can reach.
        who = {"faction": who.get("faction")} if who.get("faction") else {}
    level = T.opt_num(args.get("level")) or who.get("level") or 1
    try:
        g = classguide.guide(asked, int(level), who.get("race"), who.get("faction"), who.get("talents"))
    except ValueError as e:
        raise T.ToolError(str(e)) from None
    return {"char": char if who.get("class") else None, **g}


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
)
