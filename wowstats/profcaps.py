"""A profession's cap (its max rank), without CharacterExport-Forever.

The owner dropped CharacterExport-Forever on 2026-11-20 (API first, an
add-on only when there is no other way). Battle.net's API has no
professions for Classic or Forever: a character's resources there are its
profile, appearance, equipment, specializations, media, statistics,
achievements, reputations, PvP and hunter pets (checked against Blizzard's
Classic profile API list, 2026-11-20). So the cap comes from what is
already read, in this order:

1. Polestar Recorder's skill records (`kind=skill`: rank and max_rank, each
   time the game's skill list changes, training included), when the newest
   one still covers the rank Polestar saved ("recorder");
2. else the trainer tiers in the knowledge base (`profession.tiers`): the
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


def cap_of(rank: int | None, rec: dict | None) -> tuple[int, int | None, str | None]:
    """(rank, cap, where the cap comes from: "recorder", "tiers" or None) for
    one profession: Polestar's rank and the Recorder's newest record of that
    skill ({rank, max_rank, t}). Skills only go up, so the higher rank wins;
    a recorded cap below it is from before a training the Recorder missed."""
    rank = max(int(rank or 0), int(num((rec or {}).get("rank"))))
    rcap = int(num((rec or {}).get("max_rank")))
    if rcap and rcap >= rank:
        return rank, rcap, "recorder"
    cap = infer(rank)
    return rank, cap, "tiers" if cap else None


def fill(profs: list[dict], skills: dict[int, dict] | None) -> list[dict]:
    """The professions with `max_rank` and `max_rank_from` set (cap_of),
    whatever cap an old sheet carried."""
    out = []
    for p in profs or []:
        sid = opt_num(p.get("skill_id"))
        rank, cap, src = cap_of(opt_num(p.get("rank")), (skills or {}).get(int(sid)) if sid is not None else None)
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
