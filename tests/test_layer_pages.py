"""The page's numbers from the semantic layer: the same answers before and after.

Since the dashboard reads its time per level, XP an hour, kill and quest
totals, dungeon runs and deaths from the semantic layer (one definition for
the pages and the MCP's query_metric), every answer a plain open of the page
asks for must still say what it said when the views counted those numbers
themselves. tests/data/layer-pages-demo.json holds those answers on the
demo seed (dev/seed.py), first taken from the views before the move (main at 3d1afb5, with
this demo seed), then retaken once for the decided change of the session and
route-time definitions (journal and timeline session ends, a few seconds
each), and once for the Journal's XP worked out from the bar's readings
(prep.xp_gains: the history's first reading gains nothing, -2,990 on the
first evening). When the local demo became the docs site's character (the
Testchar seed moved to tests/testchar.py) the snapshot was retaken on him,
from the code that had just passed on the old one, and once more for the
dungeon trip's line (owner, 2026-11-23: the trip from where he stands, the
kill estimate counted; only the Overview's dungeon moves' `gain` and text
changed), and once for the trip at his pace (owner, 2026-11-23: the route's legs
labelled `your pace` and `gain.trip.pace` added; the demo's trips are quicker than
the rules, so the minutes stay), and once for the legs seen or not (2026-11-25, an outside
review: Razorfen Downs' run, logged at the door, read its pickups as 0 s measured, "~35 min
trip"; they now come from the route at his pace, "~1h00 trip (20 min pickups · 35 min inside)",
and `gain.trip.unknown_legs` is added). This test builds them again and compares, number for number.

The snapshot leaves out what isn't the layer's (NOT_LAYER): the gear advice
(each level snapshot's `bis` and `gear`, the character's `gear`) and the
plan's picks (the Overview's `moves`, `next_moves`, `upgrades`). Those moved
with every gear or data change: 45 commits rewrote the snapshot from
2026-11-20 to 11-25, 17 of them for those parts alone, and its gzip conflicted
on every PR. They have their own tests (tests/test_gear*.py, tests/slices).
It is plain JSON, one key per line, so git merges two changes to it.

Rewrite the snapshot only when a change of definition is decided:
`WOW_UPDATE_SNAPSHOT=1 uv run pytest tests/test_layer_pages.py`, then read the
diff.
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from tools.fixtures.generate import build_demo
from wowstats import eventcache, logsql, stores, ui
from wowstats.stores import parse_t

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests" / "data" / "layer-pages-demo.json"
warnings.filterwarnings("ignore", module="boring_semantic_layer")
# Moments, not numbers: when an answer was built, and the cache's freshness.
VOLATILE = {"generated_at", "stale"}
# Not the layer's numbers, per answer kind (the module's doc): left out of the snapshot.
NOT_LAYER = {"snapshot": {"bis", "gear"}, "char": {"gear"}, "overview": {"moves", "next_moves", "upgrades"}}


# The single-character history used for the snapshot, before dev/seed.py adds the rest of the roster.
CHAR = "Nibbet-Gearholt"
END = "2026-11-18"


def seed_rows(_tmp: Path) -> list[dict]:
    return [{**r, "demo": True} for r in build_demo(END)]


class SeedVL:
    """VictoriaLogs over the seed's rows: wowstats.logsql answers each query."""

    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: parse_t(r.get("_time")))

    def __call__(self, url: str, params: dict | None = None, *a, **k) -> bytes:
        if "/select/logsql/query" not in url:
            return json.dumps({"data": {"result": []}}).encode()  # vm-wow: nothing
        q = logsql.parse(params["query"])
        lo, hi, limit = parse_t(params["start"]), parse_t(params["end"]), int(params["limit"])
        out = q.run([r for r in self.rows if lo <= parse_t(r["_time"]) <= hi])[:limit]
        return "\n".join(json.dumps(r) for r in out).encode()


def strip(v):
    if isinstance(v, dict):
        return {k: strip(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [strip(x) for x in v]
    return v


def answers(home: Path) -> dict:
    """Every answer with a number the layer now gives, full view and public."""
    out: dict = {}
    for view in ("full", "public"):
        app = ui.App({"VIEW": view, "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                      "MODEL3D": "off", "WOW_ITEMS": "off", "DEFAULT_CHAR": CHAR, "HOME": str(home / view)})
        names = [c["char"] for c in app.views.chars()]
        for name in sorted(names):
            out[f"{view}:char:{name}"] = app.api_char(name)
            tl = app.timeline(name)
            out[f"{view}:timeline:{name}"] = tl
            for lv in tl.get("levels") or []:
                out[f"{view}:snapshot:{name}:{lv}"] = app.snapshot(name, lv)
            if view == "full":
                out[f"{view}:overview:{name}"] = app.overview(name)
                out[f"{view}:journal:{name}"] = app.api_journal(name)
    out = {k: {f: x for f, x in v.items() if f not in NOT_LAYER.get(k.split(":")[1], ())} if isinstance(v, dict)
           else v for k, v in out.items()}
    return strip(json.loads(json.dumps(out, default=str)))


@pytest.fixture
def seed_vl(monkeypatch, tmp_path):
    monkeypatch.setattr(stores, "http_get", SeedVL(seed_rows(tmp_path)))
    monkeypatch.setattr(eventcache, "_SHARED", {})
    return tmp_path


def diff(a, b, path="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}.{k}: only before" for k in a.keys() - b.keys()]
        out += [f"{path}.{k}: only after" for k in b.keys() - a.keys()]
        for k in a.keys() & b.keys():
            out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items before, {len(b)} after"]
        return [x for i, (p, q) in enumerate(zip(a, b, strict=True)) for x in diff(p, q, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} before, {b!r} after"]


def test_the_pages_say_the_same_numbers_from_the_layer(seed_vl, monkeypatch):
    from wowstats.analysis import pages

    built = []
    compute = pages._compute
    monkeypatch.setattr(pages, "_memo", type(pages._memo)())
    monkeypatch.setattr(pages, "_compute", lambda ask: built.append(ask.char) or compute(ask))
    got = answers(seed_vl)
    # The layer ran, once per character and data however many answers read it (both views, every level's snapshot).
    assert sorted(built) == sorted({k.split(":")[2] for k in got if ":char:" in k})
    if os.environ.get("WOW_UPDATE_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(got, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    want = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert set(got) == set(want)
    problems = diff(want, got)
    assert not problems, "\n".join(problems[:40])
    # The answers hold real numbers, not an empty page that matches an empty page.
    char = got[f"full:char:{CHAR}"]
    assert char["levels"] and char["header"]["xp_per_hour"] and char["deaths"]["total"] == 25
    assert char["dungeons"] and got[f"public:char:{CHAR}"]["levels"]


def test_the_pages_take_the_layers_numbers(seed_vl, monkeypatch):
    """Not a copy that happens to agree: a number changed in the layer shows on the page."""
    from wowstats.analysis import pages

    compute = pages._compute
    base: dict[int, int] = {}

    def doubled(ask):
        n = compute(ask)
        # The layer's own number, never one typed here: the seed's route
        # times move with Polestar and forever-db (tools/kb/rebuild.py).
        base.update({lv: r["played_s"] for lv, r in n["levels"].items()})
        for r in n["levels"].values():
            r["played_s"] = (r["played_s"] or 0) * 2
        n["deaths"]["total"] += 100
        return n

    monkeypatch.setattr(pages, "_memo", type(pages._memo)())
    monkeypatch.setattr(pages, "_compute", doubled)
    app = ui.App({"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                  "MODEL3D": "off", "WOW_ITEMS": "off", "DEFAULT_CHAR": CHAR, "HOME": str(seed_vl)})
    d = app.api_char(CHAR)
    lv30 = next(lv for lv in d["levels"] if lv["level"] == 30)
    assert base[30] and lv30["seconds"] == 2 * base[30] and d["deaths"]["total"] == 125


def test_the_kept_numbers_never_cross_people(tmp_path, monkeypatch):
    """Two people with a character of the same name: the numbers are kept per
    character AND per rows, so one never gets the other's (wowstats.tenancy)."""
    from wowstats.analysis import pages

    monkeypatch.setattr(pages, "_memo", type(pages._memo)())
    rows = [r for r in seed_rows(tmp_path) if r.get("char") == CHAR
            and (r["feed"] in ("wow-level", "wow-dungeon") or r.get("kind") in ("level", "session", "zone", "death"))]
    from wowstats.analysis import copies

    mine_cp = copies.build(rows, CHAR, ("store-a", CHAR))
    mine = pages.numbers(CHAR, mine_cp)
    theirs = pages.numbers(CHAR, copies.build([r for r in rows if not (r["feed"] == "wow-level"
                                                                        and int(r["level"]) == 30)],
                                              CHAR, ("store-b", CHAR)))
    split = next(int(r["seconds"]) for r in rows if r["feed"] == "wow-level" and int(r["level"]) == 30)
    assert mine["levels"][30]["played_s"] == split and 30 not in theirs["levels"]
    assert pages.numbers(CHAR, mine_cp) is mine and len(pages._memo) == 2
