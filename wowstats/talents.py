"""Talents and Legacy points, laid out like the game's frames.

Forever 1.60.1 has no classic talent API: a class's talents are one trait
tree (C_Traits) that holds the three classic trees side by side, and
Battle.net's /specializations lists what is learned as talent spell IDs and
ranks (wowstats.exporter). This module turns those ranks into the frame the
game draws, from Forever's own tables on
wago.tools (product wowf), as wowstats.items and wowstats.character3d read theirs:

  TraitTree, TraitNode (PosX, PosY: the frame's grid), TraitNodeXTraitNodeEntry,
  TraitNodeEntry (MaxRanks), TraitDefinition (the talent's SpellID),
  TraitTreeXTraitCurrency (3820: class talent points, 4225: Legacy points),
  TraitCurrencySource (what earns each Legacy point), Talent and TalentTab
  (the classic trees' names and class, matched by rank spell), SpellName,
  Spell (descriptions), SpellEffect (the numbers in them, and the tick and
  radius), SpellMisc (SpellIconFileDataID, DurationIndex), SpellDuration,
  SpellAuraOptions (stacks, proc chance, charges), SpellRadius, and
  ManifestInterfaceData (the icon's file name, as wow.zamimg.com names its
  renders). A description's numbers may be another spell's ($15271s1:
  Spirit Tap's Spirit is its buff's): those spells' rows are read too.

Legacy points (Forever's "Legacy" system) are an account-wide currency
(TraitCurrency 4225) earned by Legacy Challenges, each an achievement listed
in TraitCurrencySource (a class reaching 25, 45 and 60, a crafting profession
at 150, 225 and 300, PvP ranks and reputations, exploring, dungeons and
raids), and spent in three account-wide Legacy talent trees (TraitTree 1187,
1188, 1189: Well Rested, Thrill of Adventure, Talented...). The count comes
from Battle.net's /achievements (the exporter), every character of the
account together; without Battle.net it is unknown.

The catalog is built once per wago.tools build and kept on disk next to the
item tables. Environment: WOW_TALENTS (on | off; WOW_ITEMS=off turns it off
too), MODEL3D_CACHE_DIR, MODEL3D_WAGO_URL and MODEL3D_PRODUCT.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

DAY = 86400
CLASS_CURRENCY = "3820"
LEGACY_CURRENCY = "4225"
# TalentTab.ClassMask -> the class token the shipper and gear.py use.
CLASS_BITS = {1: "WARRIOR", 2: "PALADIN", 4: "HUNTER", 8: "ROGUE", 16: "PRIEST", 64: "SHAMAN", 128: "MAGE",
              256: "WARLOCK", 1024: "DRUID"}
ICON = re.compile(r"^[a-z0-9_\-]{1,80}$")
TABLES = ("TraitTree", "TraitNode", "TraitNodeXTraitNodeEntry", "TraitNodeEntry", "TraitDefinition",
          "TraitTreeXTraitCurrency", "TraitCurrencySource", "Talent", "TalentTab", "TraitEdge")
# The cached catalog's shape: a catalog written by an older build of this
# module is rebuilt, not reused (2: the grid from the commonest step, off-grid
# nodes dropped, the prerequisite edges; 3: each Legacy challenge's
# achievement ids, both client versions').
FORMAT = 6  # 5: the per-rank numbers (TraitDefinitionEffectPoints curves); 6: the spells a description names
# Read in a second pass, only the rows the trees need (they're large).
SPELL_TABLES = ("SpellName", "Spell", "SpellEffect", "SpellMisc", "ManifestInterfaceData",
                "TraitDefinitionEffectPoints", "CurvePoint", "SpellDuration", "SpellAuraOptions", "SpellRadius")


def _i(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def class_token(c) -> str | None:
    t = re.sub(r"[^A-Z]", "", str(c or "").upper())
    return t if t in CLASS_BITS.values() else None


# ------------------------------------------------------------------ descriptions

# What a description's $ tokens read (Spell.Description_lang):
#   $s1 $m1 $S1 $M1  an effect's number; $o1 its total over the duration
#   $d  the duration, $t1 an effect's tick, $a1 its radius
#   $u  the stacks, $h the proc chance, $n the charges
#   any of them after a spell id ($15271s1): that spell's, not this one's
#   $/10;s1 or $*2;s1 scaled, ${...} a formula of them (".1": decimals),
#   $lone:many; a plural, $gmale:female; a gendered word.
# Multi-letter names ($SPFR, $bh, $pl, $<mult>) are the caster's stats,
# which only the client knows.
_VAR = r"\$(?:(?P<op>[/*])(?P<k>\d+);)?(?P<sid>\d+)?(?P<var>[sSmMoOdDtTaAuUhHnN])(?P<idx>[1-3])?(?![a-zA-Z])"
_TOKEN = re.compile(r"\$\{(?P<expr>[^}]*)\}(?:\.(?P<prec>\d)(?!\d))?"
                    r"|\$[lLgG](?P<one>[^:;$]*):(?P<many>[^;$]*);"
                    r"|" + _VAR +
                    r"|\$<[^>]*>|\$[a-zA-Z]+\d*")
_INNER = re.compile(_VAR + r"|\$<[^>]*>|\$[a-zA-Z]+\d*")
_GONE = "\x00"  # a number we can't read, until its words are dropped
_UNIT = r"(?:%|\s+(?:sec|seconds?|min|yds?|yards?|times)\b)?"
# The words that only carry a dropped number, dropped with it:
# "by ?%" -> "", "a ?% chance" -> "a chance", "?% of" -> "part of".
_DROP = [(re.compile(p.replace("X", _GONE + _UNIT)), r) for p, r in (
    (r",?\s*stacking up to X", ""),
    (r"\s+and\s+lasts\s+X", ""),
    (r"(?:(?<=[.!])|^)\s*Lasts\s+X\s*\.", ""),
    (r"\s+(?:by|for|over|within|every|lasts|after)\s+(?:an additional\s+|up to\s+)?X", ""),
    (r"\b([Aa])\s+X\s+chance", r"\1 chance"),
    (r"X\s+of\b", "part of"),
    (r"\s*(?:up to\s+)?X", ""),
)]


def _duration(sec: float) -> str:
    if sec < 60:
        return f"{round(sec, 1):g} sec"
    if sec < 3600:
        return f"{round(sec / 60, 1):g} min"
    return f"{round(sec / 3600, 1):g} hrs"


def _number(v: float, prec: int | None = None) -> str:
    return f"{round(v, 2 if prec is None else prec):g}"


def _calc(expr: str) -> float | None:
    """A formula's arithmetic (numbers, + - * /, brackets) without eval."""
    import ast

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add):
                return a + b
            if isinstance(n.op, ast.Sub):
                return a - b
            if isinstance(n.op, ast.Mult):
                return a * b
            return a / b
        raise ValueError(expr)

    if not re.fullmatch(r"[\d.+\-*/() ]+", expr):
        return None
    try:
        return ev(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, TypeError):
        return None


def describe(text: str, values: list[float], times: int = 1, spell: dict | None = None,
             spells: dict | None = None) -> str:
    """A tooltip line from Spell.Description_lang: $s1..$s3 (and $/1000;S1)
    become the effect's numbers (EffectBasePointsF, times the rank for a
    trait talent whose rank spell is the same at every rank); `spell` has
    this spell's duration, stacks, proc chance... and `spells` the spells a
    token names by id ($15271s1). A number none of them has (a spell
    outside the catalog, the caster's spell power) is left out with the
    words that only carry it, never shown as "?" or guessed."""
    own = {**(spell or {}), "values": [v * times for v in values]}
    spells = spells or {}

    def read(m: re.Match) -> float | str | None:
        sp = spells.get(m.group("sid")) if m.group("sid") else own
        if not sp:
            return None
        var, idx = m.group("var").lower(), int(m.group("idx") or 1) - 1
        vals = sp.get("values") or []
        v = None
        if var in "sm":
            v = vals[idx] if idx < len(vals) else None
        elif var == "o":
            per = (sp.get("period") or [0, 0, 0])[idx] if idx < 3 else 0
            if idx < len(vals) and sp.get("dur") and per:
                v = vals[idx] * sp["dur"] / (per / 1000)
        elif var == "d":
            return sp["dur"] if (sp.get("dur") or 0) > 0 else None
        elif var == "t":
            per = (sp.get("period") or [0, 0, 0])[idx] if idx < 3 else 0
            v = per / 1000 if per else None
        elif var == "a":
            v = ((sp.get("radius") or [0, 0, 0])[idx] or None) if idx < 3 else None
        else:
            v = {"u": sp.get("stacks"), "h": sp.get("chance"), "n": sp.get("charges")}[var] or None
        if v is None:
            return None
        k = int(m.group("k") or 1)
        return v / k if m.group("op") == "/" else v * k if m.group("op") == "*" else v

    def formula(m: re.Match) -> str:
        def inner(t: re.Match) -> str:
            v = read(t) if t.group("var") else None
            if v is None:
                raise LookupError
            return f"({v!r})"

        try:
            v = _calc(_INNER.sub(inner, m.group("expr")))
        except LookupError:
            v = None
        return _GONE if v is None else _number(abs(v), int(m.group("prec")) if m.group("prec") else 1)

    last: list[float | None] = [None]  # the number a plural follows ("1 spell", "2 spells")

    def sub(m: re.Match) -> str:
        if m.group("expr") is not None:
            return formula(m)
        if m.group("many") is not None:
            if m.group(0)[1] in "gG":
                return m.group("one")
            return m.group("one") if last[0] == 1 else m.group("many")
        if m.group("var"):
            v = read(m)
            last[0] = None if v is None else abs(v)
            if v is None:
                return _GONE
            return _duration(v) if m.group("var").lower() == "d" else _number(abs(v))
        return _GONE

    out = _TOKEN.sub(sub, text or "")
    for rx, rep in _DROP:
        out = rx.sub(rep, out)
    out = out.replace(_GONE, "")
    out = re.sub(r"\s+([,.;:!])", r"\1", out)
    out = re.sub(r",\s*\.", ".", out)
    return re.sub(r"\s{2,}", " ", out).strip()


# ------------------------------------------------------------------ the catalog


def _commonest(gaps: list[int], default: int) -> int:
    if not gaps:
        return default
    counts: dict[int, int] = {}
    for g in gaps:
        counts[g] = counts.get(g, 0) + 1
    return max(sorted(counts), key=lambda g: counts[g])


def _on_grid(offset: int, step: int) -> bool:
    """A node's offset from its tree's first column lands on a column (a
    tenth of a step of slack)."""
    return abs(offset / step - round(offset / step)) <= 0.1


def build_catalog(t: dict[str, list[dict]]) -> dict:
    """The frames from the tables (each a list of CSV rows). Pure: the tests
    feed it small tables."""
    tabs = {r["ID"]: r for r in t["TalentTab"]}
    by_spell: dict[str, tuple[dict, int]] = {}
    for r in t["Talent"]:
        for k in range(9):
            s = r.get(f"SpellRank_{k}") or "0"
            if s != "0":
                by_spell[s] = (r, k + 1)
    entries = {r["ID"]: r for r in t["TraitNodeEntry"]}
    defs = {r["ID"]: r for r in t["TraitDefinition"]}
    node_entries: dict[str, list[str]] = {}
    for r in sorted(t["TraitNodeXTraitNodeEntry"], key=lambda r: _i(r.get("_Index"))):
        node_entries.setdefault(r["TraitNodeID"], []).append(r["TraitNodeEntryID"])
    currency = {r["TraitTreeID"]: r["TraitCurrencyID"] for r in t["TraitTreeXTraitCurrency"]}

    trees: dict[str, dict] = {}
    for n in t["TraitNode"]:
        tree = n["TraitTreeID"]
        kind = {CLASS_CURRENCY: "class", LEGACY_CURRENCY: "legacy"}.get(currency.get(tree, ""))
        if not kind:
            continue
        spells = []
        dids = []
        maxr = 0
        for e in node_entries.get(n["ID"], []):
            en = entries.get(e)
            d = defs.get(en["TraitDefinitionID"]) if en else None
            if d and _i(d.get("SpellID")) > 0:
                spells.append(str(_i(d["SpellID"])))
                dids.append(_i(d["ID"]))
                maxr = max(maxr, _i(en.get("MaxRanks")))
        if not spells:
            continue
        cls_tal = by_spell.get(spells[0])
        trees.setdefault(tree, {"kind": kind, "nodes": []})["nodes"].append({
            "id": _i(n["ID"]), "x": _i(n["PosX"]), "y": _i(n["PosY"]), "spells": list(dict.fromkeys(spells)),
            # TraitDefinition IDs: what C_Traits (and so WowSimsExporter's string) names a talent by.
            "defs": list(dict.fromkeys(dids)),
            "max": maxr or 1,
            "tab": cls_tal[0]["TabID"] if cls_tal else None,
            # The classic talent's own spell per rank, when it has one.
            "ranks": [str(_i(cls_tal[0].get(f"SpellRank_{k}"))) for k in range(9)
                      if _i(cls_tal[0].get(f"SpellRank_{k}"))] if cls_tal else [],
        })

    edge_pairs = sorted({(_i(r.get("LeftTraitNodeID")), _i(r.get("RightTraitNodeID")))
                         for r in t.get("TraitEdge") or []})
    out_trees = []
    best: dict[str, tuple[int, str]] = {}  # class -> (node count, tree): its biggest tree is the frame
    for tid, tr in trees.items():
        nodes = tr["nodes"]
        if tr["kind"] == "class":
            masks = [_i(tabs[nd["tab"]]["ClassMask"]) for nd in nodes if nd["tab"] in tabs]
            cls = CLASS_BITS.get(max(set(masks), key=masks.count)) if masks else None
            if not cls:
                continue
            tr["class"] = cls
            if len(nodes) > best.get(cls, (0, ""))[0]:
                best[cls] = (len(nodes), tid)
    for tid, tr in trees.items():
        nodes = tr["nodes"]
        if tr["kind"] == "class" and best.get(tr.get("class"), (0, ""))[1] != tid:
            continue  # a class's starter or test tree (a handful of nodes), not the frame
        cls = tr.get("class")
        # The frame's grid: nodes sit on PosX/PosY steps, a class's three
        # spec columns side by side with a wide gap between them. Forever
        # moved talents between the classic trees, so a column's name isn't
        # its talents' TalentTab: the columns are the class's tabs in their
        # OrderIndex order, left to right, as Polestar's generated
        # Talents/Builds.lua numbers them ("spec column" 1-3, specs listed
        # in that order). A node far off the grid (one Hunter node sits at X
        # 102800, a duplicate the game never ranks) is left out.
        # The step is the commonest gap between neighbouring columns (rows),
        # never the smallest: Priest's tree 1114 holds a stray duplicate of
        # Holy Specialization at X 9280, Y 21300, 200 off its neighbour, and
        # the smallest gap made the step 400 instead of 600, which put every
        # talent in the wrong column (live on 0.2.2).
        xs = sorted({nd["x"] for nd in nodes})
        ys = sorted({nd["y"] for nd in nodes})
        step = _commonest([b - a for a, b in zip(xs, xs[1:], strict=False) if 300 <= b - a <= 1200], 600)
        ystep = _commonest([b - a for a, b in zip(ys, ys[1:], strict=False) if 300 <= b - a <= 1200], step)
        spans: list[list[int]] = []
        for x in xs:
            if spans and x - spans[-1][-1] <= step * 1.5:
                spans[-1].append(x)
            else:
                spans.append([x])
        want = 3 if tr["kind"] == "class" else 1
        keep = sorted(sorted(spans, key=lambda sp: -sum(1 for nd in nodes if sp[0] <= nd["x"] <= sp[-1]))[:want])
        names = sorted((r for r in tabs.values() if CLASS_BITS.get(_i(r["ClassMask"])) == cls),
                       key=lambda r: _i(r["OrderIndex"])) if cls else []
        # A row far below the rest (Hunter's Improved Serpent Sting sits 56
        # steps down, Priest's Holy Specialization 25) is drawn one row
        # under the last: the frame keeps its height.
        rows = sorted({round((nd["y"] - ys[0]) / ystep) for nd in nodes})
        row_at: dict[int, int] = {}
        last_raw, last_out = None, -1
        for r in rows:
            last_out = r if last_raw is None else last_out + (r - last_raw if r - last_raw <= 2 else 1)
            row_at[r], last_raw = last_out, r
        sections = []
        for i, sp in enumerate(keep):
            placed = []
            for nd in sorted((nd for nd in nodes if sp[0] <= nd["x"] <= sp[-1]), key=lambda nd: (nd["y"], nd["x"])):
                if not _on_grid(nd["x"] - sp[0], step):
                    continue  # off the grid: a duplicate the game never draws (the stray above)
                placed.append({"id": nd["id"], "col": round((nd["x"] - sp[0]) / step),
                               "row": row_at[round((nd["y"] - ys[0]) / ystep)], "spells": nd["spells"],
                               "defs": nd["defs"], "max": nd["max"],
                               "ranks": nd["ranks"]})
            # The game's prerequisite arrows (TraitEdge: left node -> right
            # node), between two talents of this tree.
            at = {nd["id"]: (nd["col"], nd["row"]) for nd in placed}
            edges = [{"fc": at[a][0], "fr": at[a][1], "tc": at[b][0], "tr": at[b][1]}
                     for a, b in edge_pairs if a in at and b in at]
            sections.append({"name": names[i]["Name_lang"] if i < len(names) else None, "order": i,
                             "nodes": placed, "edges": edges})
        out_trees.append({"id": _i(tid), "kind": tr["kind"], "class": cls, "sections": sections})

    legacy = []
    by_req: dict[str, dict] = {}
    for r in t["TraitCurrencySource"]:
        if r.get("TraitCurrencyID") != LEGACY_CURRENCY or _i(r.get("Amount")) <= 0:
            continue
        req = (r.get("Requirement_lang") or "").strip()
        if not req:
            continue
        # Each challenge is listed twice (two versions in the client), with
        # two achievement ids: Battle.net may answer either.
        cur = by_req.get(req)
        if cur is None:
            cur = by_req[req] = {"requirement": req, "amount": _i(r["Amount"]),
                                 "achievement": _i(r.get("AchievementID")), "achievements": []}
            legacy.append(cur)
        if _i(r.get("AchievementID")) > 0 and _i(r.get("AchievementID")) not in cur["achievements"]:
            cur["achievements"].append(_i(r.get("AchievementID")))
    return {"trees": out_trees, "legacy_sources": legacy}


def needed_spells(cat: dict) -> set[str]:
    s: set[str] = set()
    for tr in cat["trees"]:
        for sec in tr["sections"]:
            for nd in sec["nodes"]:
                s.update(nd["spells"])
                s.update(nd["ranks"])
    return s


def named_spells(descs: dict) -> set[str]:
    """The spells the descriptions take a number from ($15271s1, $/10;12536s1)."""
    return {m.group(1) for d in descs.values() for m in re.finditer(r"\$(?:[/*]\d+;)?(\d+)[a-zA-Z]", d or "")}


def add_spell_text(cat: dict, names: dict, descs: dict, effects: dict, icons: dict,
                   curves: dict | None = None, extra: dict | None = None) -> None:
    """Names, descriptions, effect values and icon names onto the catalog;
    `curves`: each trait definition's per-rank numbers (rank_curves);
    `extra`: {spell: {dur, stacks, chance, charges, period, radius}}, for
    the trees' spells and the ones their descriptions name (values only)."""
    extra = extra or {}
    want = needed_spells(cat)
    cat["spells"] = {s: {"name": names.get(s), "desc": descs.get(s) or "", "values": effects.get(s) or [],
                         "icon": icons.get(s), **extra.get(s, {})} for s in sorted(want, key=int)}
    for s in sorted(named_spells({k: descs.get(k) for k in want}) - want, key=int):
        if s in effects or s in extra:
            cat["spells"][s] = {"name": None, "desc": "", "values": effects.get(s) or [], "icon": None,
                                **extra.get(s, {})}
    cat["curves"] = curves or {}


def rank_curves(points: list[dict], curve_points: list[dict]) -> dict[str, dict[str, list]]:
    """{TraitDefinitionID: {effect index: [[rank, value], ...]}}: a trait
    talent whose number grows with its rank keeps it in a curve
    (TraitDefinitionEffectPoints -> CurvePoint), not in its spell's effect,
    which says 0 (Spirit Tap's chance: 20, 40 ... 100). Forever's 664 rows
    are all OperationType 0, "set": the curve's value is the number."""
    by_curve: dict[str, list] = {}
    for r in curve_points:
        by_curve.setdefault(r["CurveID"], []).append((_i(r.get("OrderIndex")), float(r["Pos_0"]), float(r["Pos_1"])))
    out: dict[str, dict[str, list]] = {}
    for r in points:
        pts = sorted(by_curve.get(r["CurveID"]) or [])
        if pts and _i(r.get("OperationType")) == 0:
            out.setdefault(r["TraitDefinitionID"], {})[str(_i(r["EffectIndex"]))] = [[x, y] for _, x, y in pts]
    return out


def curve_at(pts: list, x: float) -> float:
    """A curve's value at `x`: linear between its points, flat past its ends."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y1
    return pts[-1][1]


def read_rows(body: bytes, keep=None):
    for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        if keep is None or keep(r):
            yield r


class Catalog:
    def __init__(self, wago_url: str, product: str, cache: Path | None, on: bool = True):
        self.wago = wago_url.rstrip("/")
        self.product = product
        self.file = cache / "talent-catalog.json" if cache else None
        self.on = on
        self.data: dict | None = None
        self.build = ""
        self.checked = 0.0
        self.lock = threading.Lock()

    def get(self) -> dict | None:
        self._load()
        return self.data

    def _load(self) -> None:
        if not self.on or (self.data and time.time() - self.checked < DAY):
            return
        with self.lock:
            if self.data and time.time() - self.checked < DAY:
                return
            if not self.data and self.file and self.file.is_file():
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    d = json.loads(self.file.read_text(encoding="utf-8"))
                    if d.get("format") == FORMAT:
                        self.data, self.build, self.checked = d["catalog"], d["build"], float(d["checked"])
                if self.data and time.time() - self.checked < DAY:
                    return
            from wowstats.upstream import Upstream, fetch
            from wowstats.upstream import write_file as _write

            try:
                latest = json.loads(fetch(f"{self.wago}/api/builds/{urllib.parse.quote(self.product)}/latest")
                                    or b"{}")
                build = str(latest["version"])
                if build != self.build or not self.data:
                    self.data = self._fetch(build, fetch, Upstream)
                    self.build = build
            except (Upstream, ValueError, KeyError, TypeError):
                pass  # keep what we have; try again tomorrow
            self.checked = time.time()
            if self.file and self.data:
                with contextlib.suppress(OSError):
                    _write(self.file, json.dumps({"format": FORMAT, "build": self.build, "checked": self.checked,
                                                  "catalog": self.data}, separators=(",", ":")).encode())

    def _fetch(self, build: str, fetch, Upstream) -> dict:  # noqa: N803 - the exception class
        def table(name: str) -> bytes:
            body = fetch(f"{self.wago}/db2/{name}/csv?build={urllib.parse.quote(build)}", timeout=120)
            if not body:
                raise Upstream(f"wago.tools has no {name} table for {build}")
            return body

        return catalog_from(lambda name: read_rows(table(name)))


def catalog_from(rows) -> dict:
    """The catalog from rows(table name) -> that table's CSV rows (dicts):
    wago.tools in production, a saved slice of the same tables in the tests."""
    cat = build_catalog({n: list(rows(n)) for n in TABLES})
    want = needed_spells(cat)
    names = {r["ID"]: r["Name_lang"] for r in rows("SpellName") if r["ID"] in want}
    descs = {r["ID"]: r["Description_lang"] for r in rows("Spell") if r["ID"] in want}
    # The spells whose numbers the descriptions name ($15271s1) are read too.
    every = want | named_spells(descs)
    effects: dict[str, list[float]] = {}
    extra: dict[str, dict] = {}
    radius_ix: dict[tuple[str, int], str] = {}
    for r in rows("SpellEffect"):
        if r["SpellID"] not in every or r.get("DifficultyID") != "0":
            continue
        vals = effects.setdefault(r["SpellID"], [0.0, 0.0, 0.0])
        k = _i(r["EffectIndex"])
        if 0 <= k < 3:
            with contextlib.suppress(ValueError):
                vals[k] = float(r["EffectBasePointsF"])
            if _i(r.get("EffectAuraPeriod")) > 0:
                extra.setdefault(r["SpellID"], {}).setdefault("period", [0, 0, 0])[k] = _i(r["EffectAuraPeriod"])
            if _i(r.get("EffectRadiusIndex_0")) > 0:
                radius_ix[(r["SpellID"], k)] = r["EffectRadiusIndex_0"]
    # SpellMisc is keyed by its own ID; SpellID names the spell.
    fdids: dict[str, str] = {}
    dur_ix: dict[str, str] = {}
    for r in rows("SpellMisc"):
        if r.get("SpellID") not in every or r.get("DifficultyID") not in ("0", ""):
            continue
        if r["SpellID"] in want:
            fdids[r["SpellID"]] = r["SpellIconFileDataID"]
        if _i(r.get("DurationIndex")) > 0:
            dur_ix[r["SpellID"]] = r["DurationIndex"]
    if dur_ix:
        durs = {r["ID"]: _i(r.get("Duration")) for r in rows("SpellDuration") if r.get("ID") in set(dur_ix.values())}
        for s, ix in dur_ix.items():
            if durs.get(ix, 0) > 0:
                extra.setdefault(s, {})["dur"] = durs[ix] / 1000
    for r in rows("SpellAuraOptions"):
        if r.get("SpellID") in every and r.get("DifficultyID") in ("0", ""):
            e = extra.setdefault(r["SpellID"], {})
            for key, col in (("stacks", "CumulativeAura"), ("chance", "ProcChance"), ("charges", "ProcCharges")):
                if _i(r.get(col)) > 0:
                    e[key] = min(_i(r[col]), 100) if key == "chance" else _i(r[col])
    if radius_ix:
        radii = {r["ID"]: float(r["Radius"]) for r in rows("SpellRadius") if r.get("ID") in set(radius_ix.values())}
        for (s, k), ix in radius_ix.items():
            if radii.get(ix):
                extra.setdefault(s, {}).setdefault("radius", [0, 0, 0])[k] = radii[ix]
    files = set(fdids.values())
    # rstrip("."): the game has a few doubled dots ("Spell_Shadow_DevouringPlague..blp",
    # Devouring Contagion's), which left a trailing dot the icon check refused.
    icon_names = {r["ID"]: r["FileName"].rsplit(".", 1)[0].rstrip(".").lower() for r in rows("ManifestInterfaceData")
                  if r["ID"] in files}
    icons = {s: icon_names.get(f) for s, f in fdids.items() if ICON.match(icon_names.get(f) or "")}
    defs = {str(d) for tr in cat["trees"] for sec in tr["sections"] for nd in sec["nodes"]
            for d in nd.get("defs") or []}
    points = [r for r in rows("TraitDefinitionEffectPoints") if r.get("TraitDefinitionID") in defs]
    want_curves = {r["CurveID"] for r in points}
    curve_points = [r for r in rows("CurvePoint") if r.get("CurveID") in want_curves]
    add_spell_text(cat, names, descs, effects, icons, rank_curves(points, curve_points), extra)
    return cat


_shared: Catalog | None = None
_guard = threading.Lock()


def shared() -> Catalog:
    global _shared
    with _guard:
        if _shared is None:
            env = os.environ
            cache = Path(env.get("MODEL3D_CACHE_DIR") or Path(tempfile.gettempdir()) / "wow-stats-model3d")
            try:
                cache.mkdir(parents=True, exist_ok=True)
            except OSError:
                cache = None
            from wowstats.upstream import PRODUCT, WAGO_URL

            off = ("off", "0", "false", "no")
            on = (env.get("WOW_TALENTS", "on").strip().lower() not in off
                  and env.get("WOW_ITEMS", "on").strip().lower() not in off)
            _shared = Catalog(env.get("MODEL3D_WAGO_URL", WAGO_URL), env.get("MODEL3D_PRODUCT", PRODUCT), cache, on)
        return _shared


# ------------------------------------------------------------------ answers


def frame(cat: dict | None, cls, ranks: dict[str, int], kind: str = "class") -> dict | None:
    """The talent frame of `cls` (or the Legacy trees) with `ranks` filled
    in: three sections (the classic trees) of nodes on a grid, each with its
    name, icon, rank of max and tooltip text. None when the catalog has no
    such tree."""
    if not cat:
        return None
    token = class_token(cls)
    trees = [tr for tr in cat["trees"] if tr["kind"] == kind and (kind != "class" or tr["class"] == token)]
    if not trees:
        return None
    spells = cat.get("spells") or {}
    curves = cat.get("curves") or {}
    used: set[str] = set()
    sections = []
    for tr in trees:
        for sec in sorted(tr["sections"], key=lambda s: s["order"]):
            pts = 0
            nodes = []
            for nd in sec["nodes"]:
                rank = max((ranks.get(s, 0) for s in nd["spells"]), default=0)
                used.update(s for s in nd["spells"] if s in ranks)
                pts += rank
                # The tooltip: the classic talent's spell for this rank when
                # it has one (its own numbers), else the trait's spell: its
                # rank curve where it has one (Spirit Tap's chance), else
                # its effect times the rank. Unlearned: rank 1's numbers.
                at = max(rank, 1)
                rs = nd["ranks"][min(at, len(nd["ranks"])) - 1] if nd["ranks"] else None
                own = rs is not None and bool((spells.get(rs) or {}).get("desc"))
                sp = spells[rs] if own else spells.get(nd["spells"][0]) or {}
                values = [v * (1 if own else at) for v in sp.get("values") or []]
                if not own or rs == nd["spells"][0]:  # the trait's own spell (rank 1's is, often)
                    for k, cv in (curves.get(str((nd.get("defs") or [None])[0])) or {}).items():
                        if cv and int(k) < 3:
                            values += [0.0] * (int(k) + 1 - len(values))
                            values[int(k)] = curve_at(cv, at)
                base = spells.get(nd["spells"][0]) or {}
                nodes.append({"col": nd["col"], "row": nd["row"], "name": base.get("name") or sp.get("name"),
                              "icon": base.get("icon") or sp.get("icon"), "rank": rank, "max": nd["max"],
                              "desc": describe(sp.get("desc") or "", values, spell=sp, spells=spells)})
            sections.append({"name": sec["name"], "points": pts, "nodes": nodes, "edges": sec.get("edges") or []})
    return {"sections": sections, "unmatched": len(set(ranks) - used)}


def answer(cat: dict | None, cls, ranks: dict[str, int], source: str | None, at: str | None,
           level: int | None = None) -> dict:
    """The character card's talents: spec (the tree with the most points, as
    Polestar's Gear.lua DetectSpec picks it), points per tree and the frame.
    available False with a why when nothing saved them."""
    if not ranks:
        return {"available": False, "source": None, "at": None, "level": None, "spec": None, "points": [],
                "sections": [],
                "why": NO_TALENTS}
    f = frame(cat, cls, ranks)
    if not f:
        return {"available": False, "source": source, "at": at, "level": level, "spec": None, "points": [],
                "sections": [], "why": "The talent tables for this class aren't loaded."}
    pts = [{"tree": s["name"], "points": s["points"]} for s in f["sections"]]
    top = max(pts, key=lambda p: p["points"]) if pts else None
    return {"available": True, "source": source, "at": at, "level": level,
            "spec": top["tree"] if top and top["points"] else None, "points": pts, "sections": f["sections"],
            "unmatched": f["unmatched"], "why": None}


LEGACY_UNKNOWN = ("Unknown: Legacy points come from Battle.net's achievements (Legacy challenges are "
                  "achievements), and Battle.net hasn't read this account's characters (a site without a "
                  "Battle.net API client has none).")


def legacy(cat: dict | None, achievements: dict | None) -> dict:
    """Legacy points earned, from Battle.net's achievements (owner,
    2026-11-20: the API, not a guess from levels and skills): a Legacy
    challenge counts when any of the account's characters completed its
    achievement. achievements: {id: when} (stores.account_achievements),
    None without Battle.net: then the count is unknown. What was spent in
    the Legacy trees isn't in the API."""
    sources = (cat or {}).get("legacy_sources") or []
    total = sum(s["amount"] for s in sources) if sources else None
    if not sources or achievements is None:
        return {"available": False, "earned_points": None, "earned": [], "total": total, "spent": None,
                "why": LEGACY_UNKNOWN if sources else "Forever's Legacy challenges aren't loaded yet."}
    got = {int(k) for k in achievements}
    earned = [s for s in sources if got & set(s.get("achievements") or [s.get("achievement")])]
    return {"available": True, "earned_points": sum(s["amount"] for s in earned),
            "earned": [s["requirement"] for s in earned], "total": total, "spent": None,
            "why": "From Battle.net's achievements, as its last read saw them: each Legacy challenge is "
                   "one. What was spent in the Legacy trees isn't in the API."}


def icon_names(*answers) -> set[str]:
    """Every talent icon an answer draws (the publisher copies them in)."""
    out: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get("icon"), str) and ICON.match(v["icon"]) and "col" in v and "row" in v:
                out.add(v["icon"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for a in answers:
        walk(a)
    return out


NO_TALENTS = ("No talents known yet. They come from Battle.net's profile read, every 2 hours, once it knows "
              "the character (a site without a Battle.net API client has none).")


def match_ranks(cat: dict | None, cls, readings) -> dict[str, int]:
    """Battle.net's [{spell, rank, name}] -> {node spell id: rank} in the
    catalog's ids: by the trait's spell, a classic rank spell, then the
    talent's name (Battle.net's ids are the game's, which may be either).
    What matches nothing keeps its own id (the frame counts it unmatched)."""
    by_id: dict[str, str] = {}
    by_name: dict[str, str] = {}
    spells = (cat or {}).get("spells") or {}
    token = class_token(cls)
    for tr in (cat or {}).get("trees") or []:
        if tr.get("kind") != "class" or tr.get("class") != token:
            continue
        for sec in tr["sections"]:
            for nd in sec["nodes"]:
                if not nd["spells"]:
                    continue
                first = nd["spells"][0]
                for sp in [*nd["spells"], *nd["ranks"]]:
                    by_id.setdefault(str(sp), first)
                name = (spells.get(first) or {}).get("name")
                if name:
                    by_name.setdefault(name.lower(), first)
    out: dict[str, int] = {}
    for x in readings if isinstance(readings, list) else []:
        if not isinstance(x, dict) or _i(x.get("rank")) <= 0:
            continue
        key = by_id.get(str(_i(x.get("spell")))) or by_name.get(str(x.get("name") or "").lower()) or str(
            _i(x.get("spell")))
        out[key] = max(out.get(key, 0), _i(x["rank"]))
    return out


def top_tree(points: list[dict]) -> str | None:
    """The tree with the most points, the first one on a tie (Gear.lua
    mostPoints); None before the first point."""
    best, top = None, 0
    for t in points:
        if _i(t.get("points")) > top:
            best, top = t.get("tree"), _i(t["points"])
    return best


def capstone(cat: dict | None, cls) -> dict[str, str]:
    """Each class tree's last talent (tree name -> talent name): the one-rank
    node of the deepest row that has one (Shadowform for Shadow)."""
    out: dict[str, str] = {}
    spells = (cat or {}).get("spells") or {}
    token = class_token(cls)
    for tr in (cat or {}).get("trees") or []:
        if tr.get("kind") != "class" or tr.get("class") != token:
            continue
        for sec in tr["sections"]:
            ones = [nd for nd in sec["nodes"] if nd["max"] == 1 and nd["spells"]]
            if ones and sec["name"]:
                nd = max(ones, key=lambda n: (n["row"], -n["col"]))
                name = (spells.get(nd["spells"][0]) or {}).get("name")
                if name:
                    out[sec["name"]] = name
    return out


def _letters(v) -> str:
    return "".join(c for c in str(v or "").lower() if c.isalpha())


def in_tab_order(trees: list[dict], tabs: list[str]) -> list[dict]:
    """Battle.net's trees in the frame's tab order, one entry per tab (a tab
    it didn't list: 0 points). A tree matches a tab by name, Battle.net's
    longer name included ("Shadow Magic" is Shadow). Without the frame's
    tabs, Battle.net's own order."""
    if not trees or not tabs:
        return trees
    out, left = [], list(trees)
    for tab in tabs:
        k = _letters(tab)
        hit = next((t for t in left if _letters(t["tree"]) == k), None) or next(
            (t for t in left if k and (_letters(t["tree"]).startswith(k) or k.startswith(_letters(t["tree"])))), None)
        if hit:
            left.remove(hit)
            out.append(hit)
        else:
            out.append({"tree": tab, "points": 0})
    return out + left


def from_battlenet(cat: dict | None, cls, bnet: dict | None = None) -> tuple[dict, dict]:
    """The card's talents and its spec line, from Battle.net's readings
    (stores.character_talents: trees, ranks, refreshed), the only source
    since 2026-11-20 (owner: the API first, an add-on only when there is no
    other way). A past level's build is unknown: Battle.net's readings can't
    be kept past 30 days and the game saves no talents itself; the last 3
    weeks' changes are history(), read from vm-wow as it holds them.

    Talents are shown as the game has them (owner, 2026-11-18). The spec
    line is the tree with the most points and the split, "Discipline
    16/0/7", never relabelled by a gear setting: what the gear is scored for
    is its own line (gear.scored_for)."""
    b_ranks = match_ranks(cat, cls, (bnet or {}).get("ranks"))
    b_trees = [t for t in (bnet or {}).get("trees") or [] if isinstance(t, dict) and t.get("tree")]
    if b_ranks or b_trees:
        ans = answer(cat, cls, b_ranks, "Battle.net", (bnet or {}).get("refreshed"), None)
    else:
        ans = answer(cat, cls, {}, None, None)
    # The split in the game's tab order (Discipline / Holy / Shadow for a
    # priest: the frame's sections, TalentTab.OrderIndex), every tree shown,
    # 0 too; Battle.net's own tree names and points when it gave them (it
    # lists them in another order, Discipline / Shadow Magic / Holy).
    split = in_tab_order([{"tree": t["tree"], "points": _i(t.get("points"))} for t in b_trees],
                         [s["name"] for s in (frame(cat, cls, {}) or {}).get("sections") or []]) \
        or ans.get("points") or []
    name = top_tree(split)
    spec = {"name": name, "source": ans.get("source") if name else None,
            "split": "/".join(str(t["points"]) for t in split) if name else None,
            "points": split if name else []}
    return ans, spec


def _node_names(cat: dict | None, cls) -> dict[str, str]:
    """Every rank spell of the class's talents -> the talent's name (the
    catalog's), for readings Battle.net sent without one."""
    out: dict[str, str] = {}
    spells = (cat or {}).get("spells") or {}
    token = class_token(cls)
    for tr in (cat or {}).get("trees") or []:
        if tr.get("kind") != "class" or tr.get("class") != token:
            continue
        for sec in tr["sections"]:
            for nd in sec["nodes"]:
                name = (spells.get(nd["spells"][0]) or {}).get("name") if nd["spells"] else None
                for sp in [*nd["spells"], *nd["ranks"]] if name else []:
                    out.setdefault(str(sp), name)
    return out


def history(cat: dict | None, cls, readings: list[dict]) -> list[dict]:
    """What changed between the daily readings (stores.talent_readings),
    newest first: [{"date", "t", "before": [{tree, points}], "after": [...],
    "gained": [{name, spell, from, to}], "dropped": [...]}]. Trees in the
    frame's tab order, like the card; a rank up is gained, a rank down
    dropped. A day the same as the one before is no change."""
    tabs = [s["name"] for s in (frame(cat, cls, {}) or {}).get("sections") or []]
    names = _node_names(cat, cls)

    def split(r: dict) -> list[dict]:
        return in_tab_order([{"tree": t["tree"], "points": _i(t.get("points"))} for t in r.get("trees") or []], tabs)

    out = []
    for a, b in zip(readings, readings[1:], strict=False):
        ra, rb = a.get("ranks") or {}, b.get("ranks") or {}
        before, after = split(a), split(b)
        gained, dropped = [], []
        for k in sorted(set(ra) | set(rb)):
            was, now = _i((ra.get(k) or {}).get("rank")), _i((rb.get(k) or {}).get("rank"))
            if was == now:
                continue
            x = rb.get(k) or ra.get(k)
            row = {"name": x.get("name") or names.get(str(x.get("spell"))) or None,
                   "spell": _i(x.get("spell")) or None, "from": was, "to": now}
            (gained if now > was else dropped).append(row)
        if gained or dropped or before != after:
            out.append({"date": str(b.get("t") or "")[:10], "t": b.get("t"), "before": before, "after": after,
                        "gained": gained, "dropped": dropped})
    return out[::-1]


ICON_URL = "https://wow.zamimg.com/images/wow/icons/{size}/{name}.jpg"
ICON_SIZES = ("small", "medium", "large")
_icons: dict[tuple[str, str], bytes | None] = {}


def icon_path(name: str, size: str = "medium") -> str:
    """Where a bundle keeps an icon (the page's window.wowIcon reads it)."""
    return f"data/icon/{size}/{name}.jpg"


def icon_bytes(name: str, size: str = "medium") -> bytes | None:
    """A talent icon from wow.zamimg.com (the renders Wowhead serves, named
    as Forever's ManifestInterfaceData names the file), kept on disk next to
    the catalog. None for a bad name or a 404; Upstream when it's down."""
    if not ICON.match(name or "") or size not in ICON_SIZES:
        return None
    if (size, name) in _icons:
        return _icons[(size, name)]
    from wowstats.upstream import fetch
    from wowstats.upstream import write_file as _write

    cat = shared()
    f = cat.file.parent / "icons" / size / f"{name}.jpg" if cat.file else None
    body = None
    if f and f.is_file():
        with contextlib.suppress(OSError):
            body = f.read_bytes()
    if body is None:
        body = fetch(ICON_URL.format(size=size, name=name))
        if body is not None and f:
            with contextlib.suppress(OSError):
                _write(f, body)
    _icons[(size, name)] = body
    return body
