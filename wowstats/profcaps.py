"""A profession's cap (its max rank), without CharacterExport-Forever.

The owner dropped CharacterExport-Forever on 2026-11-20 (API first, an
add-on only when there is no other way). Battle.net's API has no
professions for Classic or Forever: a character's resources there are its
profile, appearance, equipment, specializations, media, statistics,
achievements, reputations, PvP and hunter pets (checked against Blizzard's
Classic profile API list, 2026-11-20). So the cap comes from what is
already read, the highest that still covers the rank winning (caps only go up):

1. the sheet's `cap`/`cap_at` ("game"): DataStore_Crafts' copy of the
   game's skill list (Altoholic, read by the shipper after 0.9.0), rescanned
   at every login and skill-up. A full reading of the list, so a cap from
   it is a fact;
2. Polestar Recorder's skill records (`kind=skill`: rank and max_rank)
   ("recorder"). The Recorder writes one only when the list changes while
   it records (its login baseline is silent), so a trainer visit it didn't
   see never shows: an Expert enchanter was told to train Expert for days
   on a 150/150 record (2026-11-24). A move built on it hedges;
3. else the trainer tiers in the knowledge base (`profession.tiers`): the
   lowest cap at or above the rank ("tiers"). That is a floor: a character at
   exactly 150 may have trained Expert already, so a move built on it hedges.

Older shippers stored a dated character export as max_rank/max_rank_at.
It remains a recorded observation. A later drop in positive skill ranks
starts a new learning history for the profession.
"""

from __future__ import annotations

from .stores import num, opt_num, parse_t


def tiers() -> list[dict]:
    """The KB's profession.tiers, lowest cap first; [] when the KB has none."""
    from .analysis import kb

    r = kb.static_rule("profession.tiers")
    return sorted((t for t in (r or {}).get("value") or [] if isinstance(t, dict) and t.get("cap")),
                  key=lambda t: t["cap"])


def infer(rank: int | None) -> int | None:
    """The lowest trainer cap at or above `rank`; None without tiers or above them all."""
    if rank is None:
        return None
    return next((t["cap"] for t in tiers() if t["cap"] >= rank), None)


def _stamp(value) -> float:
    if isinstance(value, (int, float)) or str(value or "").isdigit():
        return float(value)
    t = parse_t(value)
    return t.timestamp() if t.year >= 2000 else 0


def cap_of(rank: int | None, rec: dict | None,
           sheet: dict | None = None) -> tuple[int, int | None, str | None]:
    """Reconcile dated observations within the current learning of a skill."""
    sheet, rec = sheet or {}, rec or {}
    sheet_rank = int(num(sheet.get("rank") or rank))
    seen = [(_stamp(rec.get("t")), int(num(rec.get("rank"))), int(num(rec.get("max_rank"))), "recorder"),
            (_stamp(sheet.get("cap_at") or sheet.get("rank_at")), sheet_rank, int(num(sheet.get("cap"))), "game")]
    if _stamp(sheet.get("max_rank_at")):
        seen.append((_stamp(sheet["max_rank_at"]), sheet_rank, int(num(sheet.get("max_rank"))), "export"))
    elif sheet.get("max_rank_from") in ("game", "recorder", "export"):
        seen.append((_stamp(sheet.get("cap_at") or sheet.get("rank_at")), sheet_rank,
                     int(num(sheet.get("max_rank"))), sheet["max_rank_from"]))
    best_rank, best_cap, best_src, last_at = 0, 0, None, 0
    for at, r, c, src in sorted(seen, key=lambda row: row[0]):
        if at and last_at and at > last_at and 0 < r < best_rank:
            best_rank, best_cap, best_src = 0, 0, None
        best_rank = max(best_rank, r)
        if c and c >= best_rank and (c > best_cap or (c == best_cap and src == "game")):
            best_cap, best_src = c, src
        if r:
            last_at = at
    if best_cap and best_cap >= best_rank:
        return best_rank, best_cap, best_src
    cap = infer(best_rank)
    return best_rank, cap, "tiers" if cap else None


def fill(profs: list[dict], skills: dict[int, dict] | None) -> list[dict]:
    """The professions with `max_rank` and `max_rank_from` set (cap_of),
    whatever cap an old sheet carried (`cap`/`cap_at` stay: fill again
    reads the same)."""
    out = []
    for p in profs or []:
        sid = opt_num(p.get("skill_id"))
        rank, cap, src = cap_of(opt_num(p.get("rank")), (skills or {}).get(int(sid)) if sid is not None else None, p)
        q = {k: v for k, v in p.items() if k != "max_rank_at"}
        out.append({**q, "rank": rank, "max_rank": cap, "max_rank_from": src})
    return out


def newest_skills(rows: list[dict], t_key: str = "_time", until: float | None = None) -> dict[int, dict]:
    """{skill id: {rank, max_rank, t}} from the Recorder's skill records,
    the newest per skill (rows in any order); with `until` (unix seconds),
    the newest at or before it: the caps as they were then (a level's
    snapshot)."""
    out: dict[int, dict] = {}
    for r in sorted(rows or [], key=lambda row: _stamp(row.get(t_key))):
        sid = opt_num(r.get("skill_id"))
        if sid is None or (until is not None and _stamp(r.get(t_key)) > until):
            continue
        rank, cap = int(num(r.get("rank"))), int(num(r.get("max_rank")))
        prev = out.get(int(sid), {})
        if not rank and not cap:
            continue
        if not cap and (not rank or rank >= prev.get("rank", 0)):
            cap = prev.get("max_rank", 0)
        out[int(sid)] = {"rank": rank or prev.get("rank", 0), "max_rank": cap, "t": r.get(t_key)}
    return out
