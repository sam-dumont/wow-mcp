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

Old wow-gear events still carry the cap CharacterExport-Forever read
(`max_rank`, `max_rank_at`): they stay stored, and nothing reads them.
"""

from __future__ import annotations

from .stores import num, opt_num


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


def cap_of(rank: int | None, rec: dict | None,
           sheet: dict | None = None) -> tuple[int, int | None, str | None]:
    """(rank, cap, where the cap comes from: "game", "recorder", "tiers" or
    None) for one profession: Polestar's rank, the Recorder's newest record
    of that skill ({rank, max_rank, t}) and the sheet's profession entry
    (its `cap`, from DataStore_Crafts). Ranks and caps only go up, so the
    highest of each wins, whichever was read last: DataStore rescans at
    login and skill-ups, not at a trainer, so the Recorder can be the newer
    one within a session. A cap below the rank is from before a training
    nobody saw and doesn't count. Equal caps: the game's full reading."""
    sheet = sheet or {}
    rank = max(int(rank or 0), int(num((rec or {}).get("rank"))), int(num(sheet.get("rank"))))
    seen = [(int(num(sheet.get("cap"))), "game"), (int(num((rec or {}).get("max_rank"))), "recorder")]
    ok = [s for s in seen if s[0] and s[0] >= rank]
    if ok:
        cap, src = max(ok, key=lambda s: (s[0], s[1] == "game"))
        return rank, cap, src
    cap = infer(rank)
    return rank, cap, "tiers" if cap else None


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
    from .stores import parse_t

    out: dict[int, dict] = {}
    best: dict[int, float] = {}
    for r in rows or []:
        sid = opt_num(r.get("skill_id"))
        if sid is None:
            continue
        t = r.get(t_key)
        ts = t if isinstance(t, int | float) else (parse_t(t).timestamp() if t else 0.0)
        if until is not None and ts > until:
            continue
        if ts >= best.get(int(sid), -1.0):
            best[int(sid)] = ts
            out[int(sid)] = {"rank": int(num(r.get("rank"))), "max_rank": int(num(r.get("max_rank"))), "t": t}
    return out
