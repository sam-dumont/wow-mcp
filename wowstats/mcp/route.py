"""Polestar's route clock: how long each level, guide and guide step should
take on the guide route, to set the player's own times against.

The numbers come from Polestar's route planner, run on the guides of a
Polestar release by tools/route_reference/export.mjs, and ship with the kit
as data/route-reference.json (GPL-3.0, like Polestar: docs/route-reference.md
says how they are made and how far to trust them). ROUTE_REFERENCE_FILE
points at another file; `off` turns the comparison off.

Two file shapes are read:

- the bundled export (`schema: 1`): per side and level the seconds spent in
  the level, per race and class the seconds at each ding, per guide its
  seconds and each step's (steps numbered from 1, as the addon's `step`);
- a hand-made file, as before the export existed:
  {"guides": {"<guide>": {"minutes": 42, ...}}, "levels": {"12": 2400}}.

Guide ids are Polestar's (`forever/alliance/05-duskwood`), the `guide` the
recorder writes on each step and the keys of the addon's `guideEnds`.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve()
# An installed wheel carries it as wowstats/data/; a checkout has it at the root.
BUNDLED = (_HERE.parents[1] / "data" / "route-reference.json", _HERE.parents[2] / "data" / "route-reference.json")
NOTE = ("Polestar's planner on its guides, uncalibrated, with straight-line walks: a model of a quick player, "
        "not anyone's /played. Real players take longer; compare the shape (which levels are slow) more than the "
        "totals.")
SIDES = ("Alliance", "Horde")


def _dur(s) -> str | None:
    if s is None:
        return None
    s = int(s)
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def norm_side(v) -> str | None:
    v = str(v or "").strip().lower()
    return next((s for s in SIDES if s.lower() == v), None)


def norm_class(v) -> str | None:
    v = str(v or "").strip().upper()
    return v or None


@dataclass
class Reference:
    available: bool
    why: str = ""
    source: str = ""
    polestar: dict = field(default_factory=dict)
    bias: dict | None = None
    levels: dict = field(default_factory=dict)   # side -> level -> row
    guides: dict = field(default_factory=dict)   # id -> row
    routes: list = field(default_factory=list)   # [{faction, race, class, dings: [[level, s]]}]
    legacy_levels: dict = field(default_factory=dict)  # hand-made file: level -> seconds
    # Where quest givers stand and dungeons open (tools/route_reference/export.mjs `places`).
    places: dict = field(default_factory=dict)

    # ------------------------------------------------------------ guides

    def guide(self, guide: str) -> tuple[str, dict] | None:
        """Exact id first, then the id ending with it, then any id holding it."""
        g = (guide or "").strip()
        if not g:
            return None
        if g in self.guides:
            return g, self.guides[g]
        low = g.lower()
        for test in (lambda k: k.lower().endswith("/" + low), lambda k: low in k.lower()):
            hits = [k for k in self.guides if test(k)]
            if hits:
                k = min(hits, key=len)
                return k, self.guides[k]
        return None

    def guide_seconds(self, guide: str) -> int | None:
        g = self.guide(guide)
        if not g:
            return None
        row = g[1]
        if isinstance(row.get("seconds"), (int, float)):
            return int(row["seconds"])
        if isinstance(row.get("minutes"), (int, float)):
            return int(row["minutes"] * 60)
        return None

    def title(self, guide: str | None) -> str | None:
        """The guide's own title; exact ids only (the lookup and profession
        guides aren't in the reference)."""
        g = self.guides.get(guide or "")
        return g.get("title") if g else None

    def step_title(self, guide: str | None, step) -> str | None:
        g = self.guides.get(guide or "")
        try:
            k = int(step)
        except (TypeError, ValueError):
            return None
        for st in (g or {}).get("steps") or []:
            if st[0] == k and len(st) > 2:
                return st[2]
        return None

    def step_seconds(self, guide: str, step) -> int | None:
        g = self.guide(guide)
        try:
            k = int(step)
        except (TypeError, ValueError):
            return None
        if not g:
            return None
        for s in g[1].get("steps") or []:
            if s[0] == k:
                return int(s[1])
        return None

    # ------------------------------------------------------------ levels

    def level(self, level: int, side: str | None = None, cls: str | None = None) -> dict | None:
        """The expected time in `level` (from reaching it to the next ding):
        the class's median over the side's starting races, else the side's
        median over races and classes (both sides' when the side isn't
        known). One definition for the pages, the MCP and the semantic
        layer's planned_s (wowstats.analysis.prep): a race's own run isn't
        used, so every race of a class gets the same plan."""
        level = int(level)
        side, cls = norm_side(side), norm_class(cls)
        if self.legacy_levels:
            s = self.legacy_levels.get(str(level))
            return {"level": level, "expected_s": s, "expected": _dur(s), "basis": "your route file"} if s else None
        sides = [side] if side else list(SIDES)
        rows = [(s, (self.levels.get(s) or {}).get(str(level))) for s in sides]
        rows = [(s, r) for s, r in rows if r]
        if not rows:
            return None
        if cls:
            by = [r["by_class"][cls] for _, r in rows if cls in (r.get("by_class") or {})]
            if by:
                s = sorted(by)[len(by) // 2]
                return {"level": level, "expected_s": s, "expected": _dur(s),
                        "basis": f"{'/'.join(x for x, _ in rows)} {cls.title()}, median over the starting races",
                        "guide": rows[0][1].get("guide")}
        vals = sorted(r["seconds"] for _, r in rows)
        s = vals[len(vals) // 2]
        return {"level": level, "expected_s": s, "expected": _dur(s),
                "basis": f"{'/'.join(x for x, _ in rows)} median over races and classes",
                "guide": rows[0][1].get("guide"), "range_s": [min(r["min_s"] for _, r in rows),
                                                              max(r["max_s"] for _, r in rows)]}

    # ------------------------------------------------------------ the tool

    def lookup(self, args: dict, who: dict | None = None) -> dict:
        who = dict(who or {})
        for k in ("faction", "class"):
            if args.get(k):
                who[k] = args[k]
        out: dict = {"available": True, "source": self.source, "note": NOTE}
        if self.polestar:
            out["polestar"] = self.polestar.get("tag") or self.polestar.get("version")
        if who:
            out["for"] = {k: v for k, v in who.items() if v and k in ("faction", "class")}
        lvl = args.get("level")
        if lvl is not None:
            out["level"] = self.level(int(lvl), who.get("faction"), who.get("class")) or {
                "level": int(lvl), "expected_s": None}
        if args.get("guide"):
            g = self.guide(args["guide"])
            if not g:
                out["guide"] = None
            else:
                gid, row = g
                info = {"guide": gid, **{k: v for k, v in row.items() if k != "steps"}}
                if isinstance(row.get("seconds"), (int, float)):
                    info["expected"] = _dur(row["seconds"])
                steps = row.get("steps") or []
                if args.get("step") is not None:
                    s = self.step_seconds(gid, args["step"])
                    info["step"] = {"step": int(args["step"]), "expected_s": s, "expected": _dur(s)}
                else:
                    info["steps"] = [{"step": st[0], "expected_s": st[1], "title": st[2] if len(st) > 2 else None}
                                     for st in steps]
                out["guide"] = info
        if lvl is None and not args.get("guide"):
            side = norm_side(who.get("faction"))
            if self.legacy_levels:
                out["levels"] = self.legacy_levels
            else:
                for s in [side] if side else SIDES:
                    out.setdefault("levels", {})[s] = {
                        lv: {"expected": _dur(r["seconds"]), "guide": r.get("guide")}
                        for lv, r in (self.levels.get(s) or {}).items()}
            gl = [(k, v) for k, v in self.guides.items() if not side or v.get("faction") in (None, side)]
            out["guides"] = {k: {"expected": _dur(self.guide_seconds(k)), "levels": v.get("levels")}
                             for k, v in gl[:80]}
            out["guides_total"] = len(gl)
        return out


def _load(path: Path) -> Reference:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    if data.get("schema") == 1:
        return Reference(True, source=str(data.get("source", ""))[:200], polestar=data.get("polestar") or {},
                         bias=data.get("bias"), levels=data.get("levels") or {},
                         guides={str(k): v for k, v in (data.get("guides") or {}).items() if isinstance(v, dict)},
                         routes=[r for r in data.get("routes") or [] if isinstance(r, dict)],
                         places=data.get("places") if isinstance(data.get("places"), dict) else {})
    return Reference(True, source=str(data.get("source", ""))[:200],
                     guides={str(k): v for k, v in (data.get("guides") or {}).items() if isinstance(v, dict)},
                     legacy_levels={str(k): int(v) for k, v in (data.get("levels") or {}).items()
                                    if isinstance(v, (int, float))})


_lock = threading.Lock()
_cached: tuple[str, float, Reference] | None = None


def reference(path: str | None = None) -> Reference:
    global _cached
    path = path if path is not None else os.environ.get("ROUTE_REFERENCE_FILE", "")
    if path.strip().lower() == "off":
        return Reference(False, "the route comparison is off (ROUTE_REFERENCE_FILE=off)")
    if path:
        p, where = Path(path), "ROUTE_REFERENCE_FILE"
    else:
        p, where = next((b for b in BUNDLED if b.is_file()), BUNDLED[0]), "the bundled route file"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return Reference(False, f"{where} can't be read")
    with _lock:
        if _cached and _cached[0] == str(p) and _cached[1] == mtime:
            return _cached[2]
        try:
            ref = _load(p)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            ref = Reference(False, f"{where} is not the expected JSON ({type(e).__name__})")
        _cached = (str(p), mtime, ref)
        return ref
