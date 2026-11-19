"""Links to Wowhead's WoW Forever database."""

WOWHEAD_BASE = "https://www.wowhead.com/forever"
KINDS = frozenset({"item", "quest", "npc", "spell", "zone", "object"})


def wowhead_url(kind: str, id_: int | str) -> str:
    """The Wowhead Forever page for a game object."""
    if kind not in KINDS:
        raise ValueError(f"unknown wowhead kind: {kind}")
    return f"{WOWHEAD_BASE}/{kind}={int(id_)}"


# ---------------------------------------------------------------- dungeons
#
# A dungeon links its Wowhead Forever guide, which says how to get in, what
# drops and which quests to take; Wowhead's zone page for a dungeon is a map
# and a list (owner, 2026-11-19). The guides are a static, checked list in
# the knowledge base (data/kb/rules.json `dungeon.wowhead_guides`, one URL
# per dungeon or per wing); a dungeon without one keeps its zone link.

GUIDE_BASE = f"{WOWHEAD_BASE}/guide/"


def _key(name: str) -> str:
    """One name per dungeon whatever wrote it ("The Stockade", "Stormwind
    Stockade"; wowstats.plan.dungeonview.key without the wing split)."""
    import re

    n = re.sub(r"^(the |stormwind )", "", (name or "").strip().lower())
    return re.sub(r"[^a-z]", "", n)


def _guides() -> dict[str, str | dict[str, str]]:
    from wowstats.analysis import kb

    value = (kb.static_rule("dungeon.wowhead_guides") or {}).get("value") or {}
    return {_key(k): v for k, v in value.items()}


def dungeon_guides(name: str | None) -> list[dict]:
    """The Wowhead Forever guides of a dungeon, [{"name", "url"}]: one for a
    dungeon with a single guide, one per wing for a dungeon split in wings
    (Scarlet Monastery), the wing's own for a wing ("Scarlet Monastery:
    Library"). [] when Wowhead has none."""
    if not name:
        return []
    g = _guides()
    whole = g.get(_key(name))
    if whole is None and ":" in name:
        base, wing = (s.strip() for s in name.split(":", 1))
        wings = g.get(_key(base))
        if isinstance(wings, dict):
            url = {_key(w): u for w, u in wings.items()}.get(_key(wing))
            return [{"name": f"{base}: {wing}", "url": url}] if url else []
        whole = wings if isinstance(wings, str) else None
    if isinstance(whole, str):
        return [{"name": name, "url": whole}]
    if isinstance(whole, dict):
        return [{"name": w, "url": u} for w, u in whole.items()]
    return []


def dungeon_url(name: str | None, area_id: int | str | None = None) -> str | None:
    """Where a dungeon's name links: its guide when Wowhead has exactly one,
    else its zone page (AreaTable id), else None."""
    gs = dungeon_guides(name)
    if len(gs) == 1:
        return gs[0]["url"]
    return wowhead_url("zone", area_id) if area_id else None
