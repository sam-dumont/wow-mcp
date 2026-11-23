"""The state of the meta per class and spec on Forever, and how close a
character is to it.

Facts, sourced rules and strategy, reconciled for any character:

- the meta comes from the knowledge base only: the `class-meta` research
  lane (data/kb/research/class-meta.json: per class and spec, its standing,
  a short summary, stat priority, gear, enchants per slot, consumables,
  professions and dated shifts), the `classes` lane (the levelling build:
  one talent pick per level, wowstats.classguide) and the weekly meta pass's
  lanes (data/kb/research/meta-<date>.json, tools/kb/meta.py): their class
  statements are the recent shifts, newest first. Nothing here is an opinion
  of ours; a class or spec the KB says nothing about gets nothing.
- the character's side is its own data: the talents (Battle.net's API,
  wowstats.talents), the gear advice (ForeverDB.net's weights and
  BiS-at-level, wowstats.gear: the stat weights are in its scores) and the
  enchant ids the item links carry.

`paragraph()` is the character page's short "state of the meta" (/zogzog/
and the full view): three or four sentences for the class and spec, and
when it was last checked. `describe()` is the full description and
`adherence()` the score with its gaps (the full view's section and the MCP's
class_meta). The pages show actions and numbers only; `cite=True` adds each
statement's source, date and confidence for the MCP.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, date, datetime
from pathlib import Path

from . import classguide
from .links import wowhead_url

_HERE = Path(__file__).resolve().parent
DATA_DIRS = (_HERE / "data", _HERE.parent / "data")
ASPECTS = ("summary", "standing", "stats", "gear", "enchants", "consumables", "professions", "shift")
# The knowledge is "as of" the newest day it was checked or changed; past
# this many days the page says so plainly.
STALE_DAYS = 21
# How the score weighs its parts (talents and gear carry the meta; an
# enchant is a smaller lever). A part that can't be measured is left out
# and the others are scaled up, so a missing export never reads as 0.
WEIGHTS = {"talents": 0.4, "gear": 0.4, "enchants": 0.2}
LEVELS = ((0.9, "On the meta"), (0.7, "Close to the meta"), (0.4, "Partway there"), (0.0, "Far from the meta"))
# The slots Forever's Enchanting reaches (wowstats.insights.ENCHANTABLE), by name.
ENCHANT_SLOTS = {2: "Neck", 5: "Chest", 8: "Feet", 9: "Wrist", 10: "Hands", 15: "Back", 16: "Main hand",
                 17: "Off hand"}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SPEC_ALIASES = {"feral": "Feral Combat", "bm": "Beast Mastery", "beastmastery": "Beast Mastery",
                "mm": "Marksmanship", "sv": "Survival", "ret": "Retribution", "prot": "Protection",
                "resto": "Restoration", "disc": "Discipline", "enh": "Enhancement", "ele": "Elemental",
                "destro": "Destruction", "demo": "Demonology", "affli": "Affliction", "affi": "Affliction",
                "sub": "Subtlety", "assa": "Assassination", "assassin": "Assassination"}

# The tank and healer specs (the gear reference's "(tank)"/"(healer)" specs,
# plus Discipline and the bear, which the lane's standings name as such).
# Owner, 2026-11-20: their talents score against the levelling build before
# 60 and against the role's endgame build at 60 (the lane's `builds`); a
# spec with no sourced build there shows "no target yet", left out.
ROLE_SPECS = {("WARRIOR", "Protection"): "tank", ("PALADIN", "Protection"): "tank",
              ("DRUID", "Feral Combat"): "tank", ("PALADIN", "Holy"): "healer", ("PRIEST", "Holy"): "healer",
              ("PRIEST", "Discipline"): "healer", ("DRUID", "Restoration"): "healer",
              ("SHAMAN", "Restoration"): "healer"}

_lock = threading.Lock()
_cache: dict = {}


def _data_dir() -> Path | None:
    return next((d for d in DATA_DIRS if (d / "kb" / "research").is_dir()), None)


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lanes() -> dict:
    """{"meta": the class-meta lane, "weekly": [data/kb/news.json (the
    digests), and any meta-<date> lane the weekly job hasn't folded yet]}."""
    with _lock:
        if "lanes" not in _cache:
            d = _data_dir()
            root = d / "kb" / "research" if d else None
            digests = d / "kb" / "news.json" if d else None
            _cache["lanes"] = {
                "meta": _read(root / "class-meta.json") if root and (root / "class-meta.json").exists() else {},
                "weekly": ([_read(digests)] if digests and digests.exists() else [])
                + ([_read(p) for p in sorted(root.glob("meta-*.json"))] if root else []),
            }
        return _cache["lanes"]


def reset() -> None:
    """Forget the loaded lanes (tests)."""
    with _lock:
        _cache.clear()


# ------------------------------------------------------------------ helpers


def trees(cls: str) -> list[str]:
    tal = ((classguide.reference().get("classes") or {}).get(cls) or {}).get("talents") or {}
    return sorted({t["tree"] for t in tal.values() if t.get("tree")})


def spec_name(cls: str, spec) -> str | None:
    """A talent tree of the class from a spec as anything names it ("frost",
    "Feral", "beast mastery", a gear key like "warrior_arms")."""
    if not spec:
        return None
    raw = str(spec).lower()
    letters = "".join(c for c in raw if c.isalpha())
    for t in trees(cls):
        tl = "".join(c for c in t.lower() if c.isalpha())
        if letters in (tl, tl.replace("combat", "")) or letters.endswith(tl) \
                or (len(letters) >= 3 and tl.startswith(letters)):
            return t
    for k, v in SPEC_ALIASES.items():
        if letters == k or letters.endswith(k):
            return v if v in trees(cls) else None
    return None


def role(cls: str | None, spec: str | None) -> str | None:
    """"tank" or "healer" for those specs, else None (a damage spec)."""
    return ROLE_SPECS.get((cls or "", spec or ""))


def role_build(cls: str, spec: str | None) -> dict | None:
    """The lane's sourced endgame build for a tank or healer spec:
    {class, spec, role, level, ranks, split, ...source}, or None."""
    for b in lanes()["meta"].get("builds") or []:
        if classguide.token(b.get("class")) == cls and b.get("spec") == spec and b.get("ranks"):
            return b
    return None


def _day(v) -> str | None:
    v = str(v or "")[:10]
    return v if DAY.match(v) else None


def _covers(s: dict, level: int | None) -> bool:
    if level is None:
        return True
    lv = s.get("levels") or [1, classguide.MAX_LEVEL]
    return lv[0] <= level <= lv[1]


def _first_sentence(t: str) -> str:
    t = " ".join(str(t or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    return m.group(1) if m else t


def _month_day(d: str) -> str:
    try:
        x = date.fromisoformat(d)
    except ValueError:
        return d
    return f"{x.strftime('%b')} {x.day}"


CITE = ("id", "source_url", "sources", "source_type", "date", "confidence", "scope", "last_verified")


def _item(s: dict, cite: bool, **extra) -> dict:
    out = {"text": s.get("text") or "", **({"title": s["title"]} if s.get("title") else {}), **extra}
    if cite:
        out.update({k: s[k] for k in CITE if s.get(k) not in (None, "", [])})
    return out


def _pool() -> list[dict]:
    """The class-meta lane's statements, and the weekly pass's that name an
    aspect (merged after review): a newer summary or standing replaces the
    lane's in what is shown first."""
    from .analysis import kbschema

    weekly = [s for lane in lanes()["weekly"] for s in lane.get("statements") or [] if s.get("aspect") in ASPECTS]
    today = datetime.now(UTC).date()
    return [s for s in list(lanes()["meta"].get("statements") or []) + weekly if not kbschema.expired(s, today)]


def statements(cls: str, aspect: str | None = None, spec: str | None = None, level: int | None = None) -> list[dict]:
    """The statements of a class: of one aspect, of `spec` (and the
    class-wide ones, spec ""), covering `level`. The spec's own first, then
    Forever's before a Classic fallback, then the newest, then by confidence."""
    order = {"high": 0, "medium": 1, "low": 2}
    out = [s for s in _pool()
           if classguide.token(s.get("class")) == cls and (aspect is None or s.get("aspect") == aspect)
           and (spec is None or s.get("spec") in ("", None, spec)) and _covers(s, level)]
    out.sort(key=lambda s: order.get(s.get("confidence"), 3))
    out.sort(key=lambda s: _day(s.get("date")) or "", reverse=True)
    return sorted(out, key=lambda s: (not (spec and s.get("spec") == spec), s.get("scope") != "forever"))


def shifts(cls: str, spec: str | None = None, n: int = 5) -> list[dict]:
    """What changed for the class lately, newest first: the class-meta lane's
    `shift` statements and every class statement of the weekly meta pass's
    lanes (balance, mechanics, class items). A statement superseded by
    another is left out."""
    rows = [s for s in statements(cls, "shift", spec) if s.get("aspect") == "shift"]
    for lane in lanes()["weekly"]:
        rows += [s for s in lane.get("statements") or [] if classguide.token(s.get("class")) == cls
                 and not s.get("aspect") and s.get("spec") in (None, "", spec)]
    gone = {s.get("supersedes") for s in rows if s.get("supersedes")}
    kept: list[set[str]] = []
    out = []
    for s in sorted(rows, key=lambda s: _day(s.get("date")) or "", reverse=True):
        words = _words(s.get("text"))
        if s.get("id") in gone or not _day(s.get("date")) or any(_same(words, k) for k in kept):
            continue
        kept.append(words)
        out.append(s)
    return out[:n]


def _words(text) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9%]+", str(text or "").lower()) if len(w) > 3}


def _same(a: set[str], b: set[str]) -> bool:
    """Two sources reporting one patch note: most of the shorter one's words
    are in the other. The newer one is kept."""
    small = min(len(a), len(b))
    return bool(small) and len(a & b) / small >= 0.6


def checked(records: list[dict], today: date | None = None) -> dict:
    """{checked, stale}: the newest day any of these records was checked or
    said (a shift's date moves it), and whether that is over STALE_DAYS ago."""
    days = [d for s in records for d in (_day(s.get("last_verified")), _day(s.get("date"))) if d]
    if not days:
        return {"checked": None, "stale": True}
    newest = max(days)
    today = today or datetime.now(UTC).date()
    return {"checked": newest, "stale": (today - date.fromisoformat(newest)).days > STALE_DAYS}


def meta_spec(cls: str, spec: str | None) -> str | None:
    """The spec the meta is told for: the character's own when the KB says
    something about it, else the levelling build's (classguide.path)."""
    if spec and any(s.get("spec") == spec for s in statements(cls)):
        return spec
    p = classguide.path(cls)
    return spec_name(cls, p.get("tree")) or spec_name(cls, p.get("spec")) or p.get("spec") or spec


# ------------------------------------------------------------------ the paragraph


def paragraph(cls_raw, spec_raw=None, level: int | None = None, today: date | None = None) -> dict | None:
    """The character page's "state of the meta": three or four sentences for
    the class and spec, from the lane's summary of that spec (else the
    class's), plus the newest change when it came after that summary. None
    when the KB has no summary for the class."""
    cls = classguide.token(cls_raw)
    if not cls:
        return None
    spec = spec_name(cls, spec_raw)
    told = meta_spec(cls, spec)
    sums = [s for s in statements(cls, "summary", told, level) if s.get("spec") in (told, "", None)] \
        or statements(cls, "summary", None, level)
    if not sums:
        return None
    base = sums[0]
    text = " ".join(str(base.get("text") or "").split())
    used = [base]
    news = shifts(cls, told, 1)
    if news and (_day(news[0].get("date")) or "") > (_day(base.get("date")) or "") \
            and len(re.findall(r"[.!?](\s|$)", text)) < 4:
        text += f" Since {_month_day(news[0]['date'])}: {_first_sentence(news[0].get('text'))}"
        used.append(news[0])
    from . import news

    return {"class": cls.title(), "spec": base.get("spec") or told, "text": text, **checked(used, today),
            "soon": news.soon_line(cls, spec or told, level, today)}


# ------------------------------------------------------------------ the description


def _links(s: dict) -> dict:
    if s.get("spell_id"):
        return {"url": wowhead_url("spell", s["spell_id"])}
    if s.get("item_id"):
        return {"url": wowhead_url("item", s["item_id"])}
    return {}


def describe(cls_raw, spec_raw=None, level: int | None = None, cite: bool = False,
             today: date | None = None, faction: str | None = None) -> dict | None:
    """The full description of the class's meta for a spec at a level: how
    every spec stands, the build (the levelling path's spec and its talent
    notes for this bracket), stat priority, gear, enchants by slot,
    consumables, professions and the recent shifts with their day."""
    cls = classguide.token(cls_raw)
    if not cls:
        return None
    spec = spec_name(cls, spec_raw)
    told = meta_spec(cls, spec)
    every = statements(cls)
    if not every and not classguide.path(cls):
        return None
    lv = level

    def sect(aspect: str, sp: str | None = told) -> list[dict]:
        return [_item(s, cite, **_links(s)) for s in statements(cls, aspect, sp, lv) if s.get("spec") in (sp, "", None)]

    standing = [_item(s, cite, spec=s.get("spec") or None) for s in statements(cls, "standing", None, lv)]
    standing.sort(key=lambda x: (x.get("spec") != told, x.get("spec") or ""))
    path = classguide.path(cls)
    build = [_item(s, cite) for s in classguide.statements(cls, "talents", lv)][:4]
    enchants = [_item(s, cite, slot=s.get("slot"), enchant=s.get("enchant"), **_links(s))
                for s in statements(cls, "enchants", told, lv) if s.get("spec") in (told, "", None)]
    stats = sect("stats") or [_item(s, cite) for s in classguide.statements(cls, "stats", lv)][:2]
    recent = shifts(cls, told)
    used = [s for s in every if s.get("spec") in (told, "", None)] + recent
    r = role(cls, spec or told)
    rb = role_build(cls, spec or told) if r and (lv or 0) >= classguide.MAX_LEVEL else None
    target = ({"spec": rb["spec"], "role": r, "level": classguide.MAX_LEVEL, "split": rb.get("split"),
               "note": rb.get("note"), **({k: rb[k] for k in CITE if rb.get(k)} if cite else {})} if rb else
              {"spec": spec or told, "role": r, "level": classguide.MAX_LEVEL, "none": True}
              if r and (lv or 0) >= classguide.MAX_LEVEL else None)
    from . import news

    return {
        "class": cls.title(), "spec": told, "your_spec": spec, "level": lv, **checked(used, today),
        "summary": (paragraph(cls, told, lv, today) or {}).get("text"),
        "standing": standing,
        "build": {"spec": path.get("spec"), "tree": path.get("tree"), "notes": build, "target": target},
        "stats": stats,
        "gear": sect("gear"),
        "enchants": enchants,
        "consumables": sect("consumables"),
        "professions": sect("professions"),
        "shifts": [_item(s, cite, date=_day(s.get("date"))) for s in recent],
        # What is coming on Forever (wowstats.news), what concerns this
        # class at this level first, with what it changes for it.
        "coming_up": news.upcoming(today, char={"class": cls, "level": lv, "spec": spec or told,
                                                "faction": faction}, cite=cite),
    }


# ------------------------------------------------------------------ adherence


def _want(cls: str, level: int) -> dict[str, int]:
    """The levelling build's ranks by `level`: {talent: rank}."""
    out: dict[str, int] = {}
    for p in classguide.path(cls).get("picks") or []:
        if p.get("level", 99) <= level:
            out[p["talent"]] = out.get(p["talent"], 0) + 1
    return out


def _talent_url(cls: str, name: str) -> str | None:
    t = ((classguide.reference().get("classes") or {}).get(cls) or {}).get("talents", {}).get(name) or {}
    return wowhead_url("spell", t["spell"]) if t.get("spell") else None


def _respec_cost() -> str:
    try:
        from .analysis import kb

        v = (kb.shared().rule("talents.respec_cost") or {}).get("value") or {}
    except Exception:  # noqa: BLE001 - no KB: no price
        v = {}
    if v.get("first_g") and v.get("cap_g"):
        return f"{v['first_g']}g the first time, more after (up to {v['cap_g']}g)"
    return "a little gold"


def target(cls: str, spec: str | None, level: int) -> dict:
    """What the talents are measured against: the class's levelling build up
    to `level` ({kind: "levelling"}), or at 60 for a tank or healer spec its
    role's endgame build ({kind: "role"}; `want` None when the lane has no
    sourced one: "no target yet")."""
    r = role(cls, spec)
    if r and level >= classguide.MAX_LEVEL:
        b = role_build(cls, spec)
        return {"kind": "role", "role": r, "spec": spec, "label": f"{spec} {r} build",
                "want": {k: int(v) for k, v in (b or {}).get("ranks", {}).items()} if b else None}
    path = classguide.path(cls)
    return {"kind": "levelling", "spec": path.get("spec"), "tree": path.get("tree") or path.get("spec"),
            "label": f"{path.get('spec')} build", "want": _want(cls, level)}


def _order(cls: str, t: dict) -> list[str]:
    """The build's talents in the order to take them: the levelling path's
    picks, or a role build's by row (a row opens after 5 points per row)."""
    if t["kind"] == "levelling":
        return [p["talent"] for p in classguide.picks(cls)]
    ref = ((classguide.reference().get("classes") or {}).get(cls) or {}).get("talents") or {}
    return sorted(t["want"] or {}, key=lambda n: ((ref.get(n) or {}).get("row", 9), (ref.get(n) or {}).get("col", 9)))


def talents_part(cls: str, level: int, tal: dict | None, header: dict, spec: str | None = None) -> dict | None:
    """The talents against the target build (levelling, or the role's at 60):
    the share of the build's points the character has where the build puts
    them."""
    t = target(cls, spec, level)
    want = t["want"]
    if level < 10:
        return None
    if t["kind"] == "role" and not want:
        return {"key": "talents", "score": None, "known": False, "target": "none", "spec": spec, "role": t["role"],
                "gaps": []}
    if not want:
        return None
    ranks = classguide.ranks_of(tal)
    base = {"key": "talents", "target": t["kind"], "spec": t["spec"], "role": t.get("role")}
    if ranks is None:
        return {**base, "score": None, "known": False,
                "gaps": [{"kind": "talents", "link": "#talents", "weight": 1,
                          "title": "Your talents aren't on your page yet",
                          "action": "Battle.net reads them every 2 hours: log out once, then check back "
                                    "later. A site without a Battle.net API client has none."}]}
    total = sum(want.values())
    matched = sum(min(ranks.get(n, 0), r) for n, r in want.items())
    spent = sum(ranks.values())
    free = max(0, (level - 9) - spent)
    off = {n: r - want.get(n, 0) for n, r in ranks.items() if r > want.get(n, 0)}
    missing = [(n, r - ranks.get(n, 0)) for n, r in want.items() if ranks.get(n, 0) < r]
    order = _order(cls, t)
    missing.sort(key=lambda x: order.index(x[0]) if x[0] in order else 99)
    gaps = []
    mine = (tal or {}).get("spec") if isinstance(tal, dict) else None
    trainers = classguide.trainers(cls, header.get("race"), header.get("faction"))[:3]
    firsts = missing[:3]
    refs = [{"name": n, "url": _talent_url(cls, n)} for n, _ in firsts]
    if sum(off.values()) > 0:
        names = ", ".join(n for n, _ in sorted(off.items(), key=lambda x: -x[1])[:3])
        k = sum(off.values())
        then = (f"spend them in the {t['label']}'s order" if t["kind"] == "levelling" else
                "spend them where the build puts them, starting with " + ", ".join(n for n, _ in firsts))
        gaps.append({
            "kind": "talents", "link": "#talents", "weight": 3 if k >= 5 else 2,
            "title": f"{k} talent point{'s' if k != 1 else ''} outside the {t['label']}"
                     + (f" (you play {mine})" if mine and t["kind"] == "levelling" and mine != t.get("tree") else ""),
            "detail": f"In {names}.",
            "action": f"Unlearn your talents at a {cls.title()} trainer ({_respec_cost()}), then {then}.",
            "trainers": trainers, "blocker": None, **({"talents": refs} if t["kind"] == "role" else {})})
    if free and missing:
        gaps.append({
            "kind": "talents", "link": "#talents", "weight": 3,
            "title": f"{free} free talent point{'s' if free != 1 else ''} to spend",
            "detail": "Next in the build: " + ", ".join(f"{n} ({c} point{'s' if c != 1 else ''})" for n, c in firsts)
                      + ".",
            "action": "Open your talents (N) and put them in now.",
            "talents": refs})
    return {**base, "score": round(matched / total, 3), "known": True, "have": matched, "of": total, "free": free,
            "off_build": sum(off.values()), "gaps": gaps}


def gear_part(g: dict | None, level: int) -> dict | None:
    """The gear against the best each slot can hold at this level (Polestar's
    BiS-at-level, scored with the spec's stat weights): worn points over
    worn plus each slot's best gain now."""
    if not g or not g.get("available"):
        return None
    from .insights import item_ref, source_ref

    worn = top = 0.0
    rows = []
    for sl in g.get("slots") or []:
        cur = float((sl.get("current") or {}).get("score") or 0)
        best = sl.get("best")
        gain = float(best.get("gain") or 0) if best else 0.0
        worn += cur
        top += cur + max(0.0, gain)
        if best and gain > 0:
            rows.append((gain, sl, best))
    if top <= 0:
        return None
    # The gear advice's order (wowstats.gear.bis_order): the slot furthest
    # from BiS first, then the bigger gain.
    from .gear import bis_order

    rows.sort(key=lambda x: (bis_order(x[2]), -x[0]))
    gaps = []
    for _gain, sl, best in rows[:3]:
        src = (best.get("sources") or [{}])[0]
        u = {**best, "slot": best.get("slot") or sl["slot"], "slot_name": best.get("slot_name") or sl["slot_name"]}
        gaps.append({"kind": "upgrade", "link": f"#up-{u['slot']}", "title": u.get("name"),
                     "weight": 3 if (u.get("gain_share_pct") or 0) >= 10 else 2,
                     "item_id": u.get("item_id"), "item": item_ref(u), "source": source_ref(src, level)})
    return {"key": "gear", "score": round(worn / top, 3), "known": True, "spec": (g.get("spec") or {}).get("label"),
            "worn": round(worn, 1), "best": round(top, 1), "gaps": gaps}


def enchants_part(cls: str, spec: str | None, level: int, items: list[dict] | None,
                  profs: list[dict] | None) -> dict | None:
    """The enchantable slots the meta names an enchant for at this level,
    enchanted or not. Only from a sheet whose item links say (enchant_id 0
    is none; an older shipper sends no id at all, which proves nothing)."""
    recs: dict[str, dict] = {}
    for s in statements(cls, "enchants", spec, level):
        if s.get("slot") and s.get("spec") in (spec, "", None):
            recs.setdefault(s["slot"], s)
    worn = [i for i in items or [] if i.get("slot") in ENCHANT_SLOTS and i.get("item_id")
            and ENCHANT_SLOTS[i["slot"]] in recs]
    if not worn or any(i.get("enchant_id") is None for i in worn):
        return None
    bare = [i for i in worn if not i.get("enchant_id")]
    ench = next((p for p in profs or [] if p.get("skill_id") == 333 and p.get("rank")), None)
    gaps = []
    if bare:
        who = (f"You're an Enchanter ({ench['rank']}): these are yours to do." if ench
               else "An Enchanter puts it on: ask in a capital's Trade channel and bring the materials.")
        gaps.append({
            "kind": "enchant", "link": "#gear", "weight": 2,
            "title": f"No enchant on {len(bare)} slot{'s' if len(bare) != 1 else ''} the meta enchants",
            "items": [{**{k: i.get(k) for k in ("item_id", "name", "quality", "slot")},
                       "slot_name": ENCHANT_SLOTS[i["slot"]], "url": wowhead_url("item", i["item_id"])}
                      for i in bare],
            "enchants": [{"slot": ENCHANT_SLOTS[i["slot"]], "name": recs[ENCHANT_SLOTS[i["slot"]]].get("enchant")
                          or recs[ENCHANT_SLOTS[i["slot"]]].get("title"),
                          **_links(recs[ENCHANT_SLOTS[i["slot"]]])} for i in bare],
            "action": who})
    return {"key": "enchants", "score": round((len(worn) - len(bare)) / len(worn), 3), "known": True,
            "have": len(worn) - len(bare), "of": len(worn), "gaps": gaps}


def level_of(score: float) -> str:
    return next(label for cut, label in LEVELS if score >= cut)


def adherence(char: dict, gear: dict | None, today: date | None = None) -> dict | None:
    """How close the character is to its class's meta: a score from 0 to
    100, a level in words, the parts (talents, gear, enchants: each its own
    score, or not measured) and the top gaps, each a move (what, where,
    what blocks it, next step) as the Overview draws them.

    char: views.char's answer (header, spec, talents, gear); gear: views.gear's."""
    hd = char.get("header") or {}
    cls = classguide.token(hd.get("class") or (char.get("gear") or {}).get("class"))
    level = hd.get("level")
    if not cls or not isinstance(level, int | float):
        return None
    level = int(level)
    spec = spec_name(cls, (char.get("spec") or {}).get("name") or hd.get("spec"))
    told = meta_spec(cls, spec)
    sheet = char.get("gear") or {}
    parts = [p for p in (talents_part(cls, level, char.get("talents"), hd, spec), gear_part(gear, level),
                         enchants_part(cls, told, level, sheet.get("items"), sheet.get("professions"))) if p]
    scored = [p for p in parts if p.get("score") is not None]
    # Getting ready for what is coming (wowstats.news): advice, never part of
    # the score (owner, 2026-11-20).
    from . import news

    prep = news.prep_gaps({**hd, "class": cls, "level": level}, spec or told, today)
    if not scored:
        return {"available": False, "class": cls.title(), "spec": told, "level": level, "parts": parts,
                "gaps": [g for p in parts for g in p.get("gaps") or []][:3], "prep": prep,
                "why": "Nothing to measure yet: no talents, gear sheet or enchants on your page."}
    w = sum(WEIGHTS[p["key"]] for p in scored)
    score = sum(WEIGHTS[p["key"]] * p["score"] for p in scored) / w
    # The gaps in the order they cost the score: a part's missing share
    # times its weight, then each gap's own weight inside it.
    ranked = []
    for p in parts:
        loss = WEIGHTS[p["key"]] * (1 - p["score"]) if p.get("score") is not None else WEIGHTS[p["key"]] / 2
        for g in p.get("gaps") or []:
            ranked.append((loss, g.get("weight") or 1, g))
    ranked.sort(key=lambda x: (-x[0], -x[1]))
    return {"available": True, "class": cls.title(), "spec": told, "your_spec": spec, "level": level,
            "score": round(score * 100), "label": level_of(score),
            "parts": [{k: v for k, v in p.items() if k != "gaps"} for p in parts],
            "gaps": [g for _, _, g in ranked][:5], "prep": prep}


def answer(char: dict, gear: dict | None, cite: bool = False, today: date | None = None) -> dict:
    """The full view's /api/meta and the MCP's class_meta: the description
    for the character's class and spec at its level, and its adherence."""
    hd = char.get("header") or {}
    cls_raw = hd.get("class") or (char.get("gear") or {}).get("class")
    spec_raw = (char.get("spec") or {}).get("name") or hd.get("spec")
    level = hd.get("level") if isinstance(hd.get("level"), int | float) else None
    desc = describe(cls_raw, spec_raw, int(level) if level else None, cite, today, hd.get("faction"))
    return {"char": char.get("char"), "available": desc is not None,
            "why": None if desc else "The knowledge base has no meta for this class yet." if cls_raw else
            "The class isn't known yet: log in once with Polestar on, then log out.",
            "meta": desc, "adherence": adherence(char, gear, today) if desc else None}
