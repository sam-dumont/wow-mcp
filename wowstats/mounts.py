"""Rideable appearances for mount spells this character's shipper has seen."""

from __future__ import annotations

from . import riding
from .stores import SourceError, jlist, opt_num, parse_t


def catalog(tables) -> dict[int, tuple[str, int]]:
    """Index mount spells with a single unconditional creature display in this build."""
    displays: dict[int, set[int]] = {}
    for row in tables.get("MountXDisplay", {}).values():
        if row.get("CreatureDisplayInfoID") and not row.get("PlayerConditionID"):
            displays.setdefault(row["MountID"], set()).add(row["CreatureDisplayInfoID"])
    out = {}
    for mount, row in tables.get("Mount", {}).items():
        choices = displays.get(mount, ())
        if len(choices) == 1 and row.get("SourceSpellID") and row.get("Name_lang"):
            out[row["SourceSpellID"]] = (row["Name_lang"], next(iter(choices)))
    return out


def resolve(by_spell, known) -> list[dict]:
    appearances = {appearance for spell in known if (appearance := by_spell.get(spell))}
    return [{"name": name, "display_id": display} for name, display in sorted(appearances)]


def owned(app, name: str, tables) -> list[dict]:
    """The current spellbook and Recorder learns. A future training or an unused
    item that teaches a mount does not put a character in its saddle."""
    from . import character3d

    if not tables or not getattr(app, "views", None) or not getattr(app, "store", None):
        return []
    if not (by_spell := character3d.index(tables).mounts):
        return []
    cf = app.views.char_filter(name)
    store = app.store
    try:
        books = store.vl(f"{store.stream} {riding.Q_BOOK.format(cf=cf)}")
    except SourceError:
        books = []
    book = max(books, key=lambda row: parse_t(row.get("_time")), default={})
    at = parse_t(book.get("_time"))
    ranks = {opt_num(pair[0]): opt_num(pair[1]) for pair in jlist(book.get("spells"))
             if isinstance(pair, list) and len(pair) == 2}
    known = {spell for spell, rank in ranks.items() if rank == 0}
    ids = ", ".join(map(str, sorted(by_spell)))
    try:
        learns = store.vl(f'{store.stream} feed:="wow-recorder" {cf} kind:=learn spell_id:in({ids}) '
                          '| stats by (spell_id) max(_time) as last', ("spell_id",), {"last": "maxtime"})
    except SourceError:
        learns = []
    for row in learns:
        spell = opt_num(row.get("spell_id"))
        if spell not in ranks or parse_t(row.get("last")) > at:
            known.add(spell)
    return resolve(by_spell, known)
