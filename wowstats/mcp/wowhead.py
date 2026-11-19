"""Wowhead links on MCP tool results.

Every object of an answer that carries a game id gets the Wowhead Forever
page for it (wowstats.links), so a client can link what it names instead of
guessing a URL. One walk over the finished answer rather than a field in
each tool: a tool that starts returning an id gets its link with no change.

The id fields and what they point at:

    item_id, with_item_id      item
    quest_id, quest_ids        quest
    npc_id                     npc
    spell_id                   spell
    object_id                  object
    zone_id, area_id           zone (AreaTable ids, as Wowhead's zone=)

An object with one of them gets `wowhead`; with several, `wowhead` is the
first in that order (the row's own thing: the item of a quest reward) and
each other one `<field without _id>_wowhead` (`quest_wowhead`). A list of
ids (`quest_ids`) gets a list (`quests_wowhead`). Ids that aren't positive
whole numbers get nothing.
"""

from __future__ import annotations

from wowstats.links import wowhead_url

FIELDS = (("item_id", "item"), ("with_item_id", "item"), ("quest_id", "quest"), ("npc_id", "npc"),
          ("spell_id", "spell"), ("object_id", "object"), ("zone_id", "zone"), ("area_id", "zone"))
LISTS = (("quest_ids", "quest", "quests_wowhead"), ("item_ids", "item", "items_wowhead"))


def _id(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, str) and v.isdigit():
        v = int(v)
    return v if isinstance(v, int) and v > 0 else None


def annotate(obj):
    """`obj` with the links added (a new structure; the input is left as is)."""
    if isinstance(obj, list):
        return [annotate(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {k: annotate(v) for k, v in obj.items()}
    primary = True
    for field, kind in FIELDS:
        i = _id(obj.get(field))
        if i is None:
            continue
        name = "wowhead" if primary else field[:-3] + "_wowhead"
        if name not in out:
            out[name] = wowhead_url(kind, i)
        primary = False
    for field, kind, name in LISTS:
        ids = obj.get(field)
        if isinstance(ids, list) and name not in out:
            urls = [wowhead_url(kind, i) for i in map(_id, ids) if i is not None]
            if urls:
                out[name] = urls
    return out
