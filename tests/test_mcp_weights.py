"""The site's WEIGHTS reach the MCP as they reach the page (review-3, item 1).

The MCP built its Views with an empty Settings, so `weights` stayed empty
there: once the planner scored from the doll's inputs (Views.gear_inputs,
#64), the MCP's gear_options, dungeon_options and next_step scored the gear
for the add-on's spec while the page scored it for WEIGHTS. And the planner's
cache didn't key on the spec, so whichever caller filled it first decided
what the other one got. The demo priest here, with WEIGHTS asking for a spec
the add-on's sheet doesn't pick, on the stand-ins of tests/degraded.py."""

from __future__ import annotations

import pytest

from tests import degraded as D
from wowstats import gear as gearmod
from wowstats import plan as planmod
from wowstats import ui, views
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore


def _addon_spec() -> str:
    """The spec the demo's add-on sheet scores for with no WEIGHTS."""
    return gearmod.advise_rows(prep_rows(), None)["spec"]["key"]


def prep_rows() -> list[dict]:
    return [r for r in D.rows_for("full") if r.get("char") == D.CHAR]


def _other_spec() -> str:
    """A spec of the demo's class that isn't the add-on's pick."""
    ref = gearmod.reference()
    picked = _addon_spec()
    return next(s["key"] for s in gearmod.specs_of(ref, "PRIEST") if s["key"] != picked)


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("WOW_ITEMS", "off")
    vl = D.VL(D.rows_for("full"))
    tmp = tmp_path_factory.mktemp("weights")
    spec = _other_spec()
    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl.url, "VM_URL": vl.url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp),
           "WOW_ITEMS": "off", "WEIGHTS": f"{D.CHAR}:{spec}"}
    yield env, spec
    vl.close()
    planmod._CACHE.clear()
    T._VIEWS.clear()
    mp.undo()


def _fresh():
    planmod._CACHE.clear()
    T._VIEWS.clear()


def _mcp(env: dict) -> dict:
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"], vm_url=env["VM_URL"]), default_char=D.CHAR, env=env)
    return {name: T.call(ctx, name, {"char": D.CHAR})
            for name in ("gear_options", "dungeon_options", "next_step", "gear")}


def _ui(env: dict) -> dict:
    app = ui.App(env)
    return {"doll": app.gear(D.CHAR, None), "dungeons": app.dungeons(D.CHAR)}


@pytest.mark.parametrize("first", ["ui", "mcp"])
def test_page_and_mcp_score_the_weights_spec(setup, first):
    env, spec = setup
    _fresh()
    if first == "ui":
        page = _ui(env)
        mcp = _mcp(env)
    else:
        mcp = _mcp(env)
        page = _ui(env)
    label = page["doll"]["spec"]["label"]
    assert page["doll"]["spec"]["key"] == spec, "the doll scores for WEIGHTS"
    assert page["dungeons"]["spec"] == label
    assert mcp["gear_options"]["spec"] == label, (first, mcp["gear_options"]["spec"], label)
    # The gear tool too: it scored the add-on's spec while everything else
    # scored WEIGHTS (decision-data review, 2026-11-22).
    assert mcp["gear"]["spec"] == label and "WEIGHTS" in mcp["gear"]["spec_from"], mcp["gear"]["spec_from"]
    # The same answers whichever caller filled the planner's cache first.
    _fresh()
    alone = _mcp(env)
    for name in ("gear_options", "dungeon_options", "next_step"):
        assert mcp[name] == alone[name], (first, name)


def test_one_settings_builder():
    """The page and the MCP read their Settings from one function."""
    s = views.settings_from_env({"WEIGHTS": f"{D.CHAR}:holy", "DEFAULT_CHAR": D.CHAR, "PRICE_DAYS": "30"})
    assert s.weights == {D.CHAR: "holy"} and s.view == "full" and s.price_days == 30
    assert ui.parse_weights is views.parse_weights


def test_planner_cache_keys_on_the_spec(setup):
    """Two callers that score for different specs never share an entry."""
    env, spec = setup
    _fresh()
    a = planmod.inputs(env, D.CHAR)
    b = planmod.inputs({**env, "WEIGHTS": ""}, D.CHAR)
    assert a["gear_spec"] != b["gear_spec"]
