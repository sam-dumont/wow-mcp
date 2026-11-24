"""One computation path for the page's numbers, and populations that say what they count.

External review, 2026-11-22 (pinned to a856e2d):

1. When the semantic layer raised, pages.numbers() logged it, returned None,
   and the views counted the numbers again with their own formulas: the
   path changed without the player or the operator knowing. The owner's
   standing decision is "use the semantic model all the time": a failure is
   now the page's standard failure state (the last good answer, marked
   stale), one JSON log line and a field on /healthz, never other numbers.
2. On the seeded demo the page said 1,188 kills and 94 quests (the completed
   levels) and the MCP's character_summary 1,262 and 99 (the current level
   too; that demo was Testchar, tests/testchar.py now). Both are right; neither said so. One definition now (pages.climb),
   each population named, the same numbers on both sides.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
import warnings
from pathlib import Path

import pytest

from tests import testchar
from tests.test_layer_pages import CHAR, SeedVL, seed_rows
from wowstats import eventcache, insights, stores, ui, views
from wowstats.analysis import copies, pages, semantic
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore
from wowstats.stores import SemanticError

ROOT = Path(__file__).resolve().parents[1]
warnings.filterwarnings("ignore", module="boring_semantic_layer")


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    fake = SeedVL(seed_rows(tmp_path))
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(MStore, "_get", lambda self, url, params, extra=None: fake(url, params))
    monkeypatch.setattr(eventcache, "_SHARED", {})
    # The layer's memory and health are per process: each test starts clean.
    for name, empty in (("_memo", type(pages._memo)()), ("_failing", {}), ("_last_ok", None)):
        monkeypatch.setattr(pages, name, empty)
    copies.clear()  # the characters' copies are per process too
    return tmp_path


def app(home: Path, view: str = "full") -> ui.App:
    return ui.App({"VIEW": view, "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://vl.invalid",
                   "MODEL3D": "off", "WOW_ITEMS": "off", "DEFAULT_CHAR": CHAR, "HOME": str(home / view)})


def broken(monkeypatch):
    """The layer fails from now on, whatever it is asked."""
    def boom(*a, **k):
        raise RuntimeError("the layer broke")

    monkeypatch.setattr(semantic, "run", boom)
    monkeypatch.setattr(pages, "_memo", type(pages._memo)())


# ------------------------------------------------------------- 1. one path


def test_a_semantic_failure_keeps_the_last_good_answer_stale_and_says_so(seeded, monkeypatch, capsys, run_app):
    a = app(seeded)
    assert a.prebuilt.check() == "first build"
    good = a.api_char(CHAR)
    assert good["header"]["counts"]["completed_levels"]["kills"] == 2255 and not good.get("stale")
    assert pages.health() == {"semantic": "ok"}

    broken(monkeypatch)
    a.prebuilt.changed()
    assert a.prebuilt.check() is None  # the build failed: the last set stays
    got = a.api_char(CHAR)
    assert got["stale"] is True and "couldn't be computed" in got["error"]
    # The last good numbers, not numbers counted another way.
    assert got["levels"] == good["levels"] and got["header"]["counts"] == good["header"]["counts"]
    h = pages.health()
    assert h["semantic"] == "failing" and h["semantic_failing_chars"] >= 1 and h["semantic_failed_at"]
    assert CHAR not in json.dumps(h)  # /healthz answers without a login: no names
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if '"semantic_failed"' in x]
    assert lines and lines[0]["event"] == "semantic_failed" and lines[0]["level"] == "error"
    assert lines[0]["char"] and "the layer broke" in lines[0]["error"]

    status, headers, body = run_app(a).get("/healthz")
    assert status == 200 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["semantic"] == "failing" and json.loads(body)["status"] == "ok"


def test_without_a_last_good_answer_a_semantic_failure_is_an_error_never_other_numbers(seeded, monkeypatch, run_app):
    a = app(seeded)
    broken(monkeypatch)
    with pytest.raises(SemanticError):
        a.views.char(CHAR)
    with pytest.raises(SemanticError):
        a.views.overview(CHAR)
    status, _, body = run_app(a).get(f"/api/char?name={CHAR}")
    assert status == 502 and "couldn't be computed" in json.loads(body)["error"]
    # The MCP's tools that read the same layer fail the same way: a tool error, not a guess.
    ctx = T.Ctx(store=MStore(vl_url="http://vl.invalid"), default_char=CHAR)
    with pytest.raises(T.ToolError):
        T.call(ctx, "deaths", {"char": CHAR})


def test_health_is_ok_again_once_the_layer_answers(seeded, monkeypatch):
    real = semantic.run
    broken(monkeypatch)
    with pytest.raises(SemanticError):
        pages.numbers(CHAR, None)
    assert pages.health()["semantic"] == "failing"
    monkeypatch.setattr(semantic, "run", real)
    pages.numbers(CHAR, None)
    assert pages.health() == {"semantic": "ok"}


def test_a_missing_source_still_gets_partial_answers_from_the_layer(seeded, monkeypatch):
    """Degraded mode stays: a character without Polestar's splits (Testgun,
    tests/testchar.py) gets the layer's answer, with the numbers nobody
    recorded left unknown."""
    fake = SeedVL([r for r in testchar.rows() if r.get("char") == "Testgun-Demo"])
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(MStore, "_get", lambda self, url, params, extra=None: fake(url, params))
    d = app(seeded).api_char("Testgun-Demo")
    assert not d.get("stale") and d["levels"] == []
    assert d["header"]["counts"]["completed_levels"] is None
    assert d["header"]["avg_level_s"] is None and d["header"]["xp_per_hour"] is None


def _defs(path: Path) -> dict[str, ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def test_the_fallback_formulas_do_not_come_back():
    """The second computation path is gone; this fails if it returns."""
    v = _defs(ROOT / "wowstats" / "views.py")
    p = _defs(ROOT / "wowstats" / "analysis" / "pages.py")
    # The views' own sums and rates, and the "is the layer installed?" switch.
    assert "split_total" not in v and "available" not in p and "_ready" not in p
    src = (ROOT / "wowstats" / "views.py").read_text(encoding="utf-8")
    assert not re.search(r"\bper_hour\(", src), "views count a rate themselves again"
    # build_header takes the layer's numbers; it never sums the level rows.
    for name in ("build_header", "build_levels", "build_dungeons", "build_deaths"):
        body = ast.unparse(v[name])
        assert "sum(" not in body and "len(rows)" not in body, f"{name} counts again"
    # pages.numbers and asker never answer None: a failure raises.
    for name in ("numbers", "asker"):
        for node in ast.walk(p[name]):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
                assert node.value.value is not None, f"pages.{name} returns None again"
            if isinstance(node, ast.ExceptHandler):
                assert isinstance(node.body[-1], ast.Raise), f"pages.{name} swallows a failure again"
    # The callers don't test for a missing layer any more.
    for fn in (views.Views.char, views.Views.layer_ask, views.Views.overview, insights.on_track,
               insights.route_levels, insights.death_clusters, T.layer_ask, T.t_character_summary):
        code = textwrap.dedent(inspect.getsource(fn))
        assert "layer is None" not in code and "layer is not None" not in code and "ask is None" not in code, \
            fn.__name__


# ------------------------------------------------------------- 2. populations


def test_both_consumers_name_each_population_and_agree(seeded):
    page = app(seeded).api_char(CHAR)["header"]["counts"]
    ctx = T.Ctx(store=MStore(vl_url="http://vl.invalid"), default_char=CHAR)
    mcp = T.call(ctx, "character_summary", {"char": CHAR})
    assert "kills" not in mcp and "quests_turned_in" not in mcp  # no bare total to confuse
    assert mcp["counts"] == page
    assert page == {
        "completed_levels": {"from_level": 22, "to_level": 45, "kills": 2255, "quests": 141},
        # Troll Temper, handed in at Gadgetzan after Zul'Farrak, is level 46's sixth.
        "current_level": {"level": 46, "kills": 101, "quests": 6},
        "including_current_level": {"kills": 2356, "quests": 147},
    }
    # The parts add up: one definition, two populations.
    for k in ("kills", "quests"):
        assert page["completed_levels"][k] + page["current_level"][k] == page["including_current_level"][k]


def test_the_friends_page_keeps_the_populations_and_the_public_one_none(seeded):
    from wowstats import redact

    full = app(seeded).api_char(CHAR)
    z = redact.zogzog("char", full)["header"]
    assert z["counts"] == full["header"]["counts"] and "kills" not in z
    assert "counts" not in app(seeded, "public").api_char(CHAR)["header"]
