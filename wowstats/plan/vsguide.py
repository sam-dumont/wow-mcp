"""Our dungeon advice against Polestar's, and the measured reason when they
differ (owner, 2026-11-19: "we need a REASON why it deviates from the
guide": the advice said Scarlet Monastery: Library at level 32 while
Polestar had Razorfen Kraul).

Polestar is the default. Its Dungeons hub lists a dungeon "For your level"
from its floor to its top: the floor is the range's bottom or the level
players who killed a boss there entered at (their 25th percentile),
whichever is higher (data/forever-reference.json `floor`, exported from
Polestar's Codex by tools/forever_reference/export.py). Scarlet Monastery
opens at 30 in the Group Finder, but players start it at 35.1, so Polestar
lists it from 36; Razorfen Kraul from 32.

A dungeon we suggest outside that list carries one line: Polestar's
dungeons for the level, ours, and the reason, from numbers measured on the
character only:

- under the floor, his own quests there (ready to hand in first) or his own
  runs there paying more XP an hour than his questing. Gear alone never
  sends him under a floor: three players in four start the dungeon there or
  later;
- over the top (he outlevels it), the panel's verdict and its numbers
  (quests left, upgrades at their drop chance, XP or gold an hour).

Without such a reason the advice follows Polestar: the dungeon waits for
its floor (the Dungeons panel's verdict says from which level), and the
session plan and dungeon_options rank it with what doesn't fit now.

The same answer, `vs_guide`, sits on the Dungeons panel's blocks, the
Overview's dungeon move (dungeonview.with_gain), dungeon_options and the
session plan's dungeon list (estimate.dungeons):
{agrees, guide_says, we_say, reason, numbers, text}.
"""

from __future__ import annotations

import math

SHOWN = 3  # dungeons named in the line (the page's rule: the best plus two)


def floor(area: dict) -> float:
    """Polestar's floor: the export's, else the range's bottom (a dungeon
    Polestar's Codex has no recording for, one from the knowledge base)."""
    f = area.get("floor")
    lo = (area.get("levels") or [0, 0])[0]
    return float(f) if isinstance(f, (int, float)) and f >= lo else float(lo)


def from_level(area: dict) -> int:
    """The first whole level Polestar lists it at (its floor, rounded up)."""
    return math.ceil(floor(area) - 1e-9)


def fits(area: dict, level: int) -> bool:
    """On Polestar's "For your level" at this level: floor to top."""
    lv = area.get("levels") or []
    return len(lv) == 2 and floor(area) <= level <= lv[1]


def for_level(level: int, areas: dict[str, dict], skip: set[str] | None = None) -> list[str]:
    """Polestar's dungeons for this level, as names: the ones that fit,
    those whose run opened last first (the newest for the level), without
    `skip` (the other side's, the ones with nothing left for him) unless
    nothing else is left."""
    on = [(a, aid) for aid, a in areas.items() if fits(a, level)]
    keep = [x for x in on if x[1] not in (skip or set())] or on
    keep.sort(key=lambda x: (-floor(x[0]), x[0]["name"]))
    return [a["name"] for a, _ in keep]


def k_xp(n: float) -> str:
    """XP the way the line writes it: 3,400 under ten thousand, else 14k."""
    n = round(n or 0)
    return f"{n:,}" if n < 10000 else f"{round(n / 1000)}k"


def _names(names: list[str]) -> str:
    shown = names[:SHOWN]
    more = len(names) - len(shown)
    if more:
        return ", ".join(shown) + f" and {more} more"
    return shown[0] if len(shown) == 1 else ", ".join(shown[:-1]) + " and " + shown[-1]


def measured(quests: list[dict], gain: dict | None) -> tuple[str | None, dict]:
    """The reason to go under a dungeon's floor, from the character's own
    numbers: quests of it ready to hand in, quests of it in his log, his
    own runs there beating his questing XP an hour. (text, numbers); text
    None when nothing measured beats following Polestar."""
    ready = [q for q in quests if q.get("state") == "open" and q.get("complete")]
    # A wing's "all" quest its trip doesn't finish is no reason to go there.
    log = [q for q in quests if q.get("state") == "open" and not (q.get("wings") or {}).get("other_wings")]
    xp_log = sum(q.get("xp_now") or 0 for q in log)
    g = gain or {}
    runs = g.get("own_runs") or 0
    xph, qxph = g.get("xp_per_hour"), g.get("questing_xp_per_hour")
    ratio = round(xph / qxph, 1) if runs and xph and qxph and not g.get("kills_unknown") else None
    nums = {"quests_ready": len(ready), "quests_in_log": len(log), "quest_xp_in_log": round(xp_log),
            "own_runs": runs, "run_xp_per_hour": round(xph) if ratio else None,
            "questing_xp_per_hour": round(qxph) if qxph else None, "vs_questing": ratio}
    bits = []
    if ready:
        bits.append(f"{len(ready)} of your quests there {'is' if len(ready) == 1 else 'are'} ready to hand in"
                    + (f" ({k_xp(sum(q.get('xp_now') or 0 for q in ready))} XP)" if any(
                        q.get("xp_now") for q in ready) else ""))
    elif log:
        bits.append(f"{len(log)} of your quests {'is' if len(log) == 1 else 'are'} there"
                    + (f" ({k_xp(xp_log)} XP)" if xp_log else ""))
    if ratio and xph >= qxph:
        bits.append(f"your runs there give {ratio:.1f}× your questing XP per hour")
    return (", and ".join(bits) if bits else None), nums


def compare(level: int, name: str, area: dict, guide: list[str], *, recommended: bool, why: str | None,
            quests: list[dict], gain: dict | None) -> dict:
    """`vs_guide` for one dungeon at `level`. `recommended`: whether our
    advice sends him there now (the panel's high value); `why`: the
    panel's reason for it (used for a dungeon he outlevels)."""
    lv = area.get("levels") or [0, 0]
    reason, nums = measured(quests, gain)
    nums = {"level": level, "floor": floor(area), "from_level": from_level(area), "max": lv[1], **nums}
    agrees = fits(area, level)
    under = level < floor(area)
    if agrees:
        reason = None
    elif not under:  # he outlevels it: the panel's own numbers are the reason
        reason = why if recommended else None
    out = {"agrees": agrees, "guide_says": guide, "we_say": name, "reason": reason, "numbers": nums,
           "under_floor": under, "text": None}
    if not agrees and recommended and reason and guide:
        out["text"] = f"Polestar's dungeons for level {level}: {_names(guide)}. {name} instead: {reason}."
    elif not agrees and recommended and reason:
        out["text"] = f"Polestar has no dungeon for level {level}. {name}: {reason}."
    return out


def named(v: dict | None, name: str | None) -> dict | None:
    """The same answer for a wing or a move that names the dungeon its own
    way ("Scarlet Monastery: Library" for Scarlet Monastery's block)."""
    if not v or not name or v["we_say"] == name:
        return v
    text = v.get("text")
    we = v["we_say"]
    if text and f"{we} instead:" in text:
        text = text.replace(f"{we} instead:", f"{name} instead:", 1)
    elif text:
        text = text.replace(f". {we}: ", f". {name}: ", 1)
    return {**v, "we_say": name, "text": text}


def agree_text(v: dict | None) -> str | None:
    """The short line under a move that agrees: on Polestar's list too."""
    if not v or not v.get("agrees"):
        return None
    return f"On Polestar's list for level {v['numbers']['level']} too."
