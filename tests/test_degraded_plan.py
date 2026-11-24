"""The session plan with a source missing (tests/degraded.py): never a
refusal while there is anything to plan from. Without the Recorder there is
no guide step to plan, so the plan goes by level from Polestar's level
splits, says it is an estimate, and ends with the one line that unlocks the
step-by-step plan (owner, 2026-11-20: "everything needs to be able to run in
a degraded mode")."""

from __future__ import annotations

import re

import pytest

from tests import degraded as D
from wowstats import plan as planmod
from wowstats.mcp import tools as T
from wowstats.mcp.store import Store as MStore
from wowstats.plan import write

# The demo days are fixed: read them on the demo's clock, not the wall clock.
pytestmark = pytest.mark.usefixtures("demo_clock")

NO_RECORDER = ("no-recorder", "recorder-off", "polestar-only", "only-level-splits", "recorder-later-before")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    """One stand-in store per scenario, and the plan made on each."""
    out = {}
    for sc in D.SCENARIOS:
        vl = D.VL(D.rows_for(sc))
        tmp = tmp_path_factory.mktemp(sc)
        env = {"VL_URL": vl.url, "VM_URL": vl.url, "ANALYSIS_STATE_DIR": str(tmp), "DEFAULT_CHAR": D.CHAR}
        planmod._CACHE.clear()
        out[sc] = {"vl": vl, "env": env, "plan": planmod.plan(env, D.CHAR, 2),
                   "inputs": planmod.inputs(env, D.CHAR)}
    yield out
    for v in out.values():
        v["vl"].close()
    planmod._CACHE.clear()


@pytest.mark.parametrize("sc", list(D.SCENARIOS))
def test_a_plan_whatever_is_missing(served, sc):
    out = served[sc]["plan"]
    e = out["estimate"]
    assert e["supported"], e.get("message")
    assert e["blocks"] and e["end"]["level"] > e["start"]["level"]
    assert write.rendered_ok(out["markdown"], e["facts"]) == []  # every number from the facts
    if sc in NO_RECORDER:
        assert e["estimated"] and e["mode"] == "by_level"
        assert "(estimated)" in out["markdown"]
        assert out["markdown"].rstrip().endswith("for a step-by-step plan.")
        assert "/psrec on" in out["markdown"]
    else:
        assert not e["estimated"] and e["mode"] == "route" and "/psrec" not in out["markdown"]


def test_the_friend_gets_a_plan_by_level(served):
    """The case that started it: Polestar on, its Recorder never switched
    on. His level splits give the pace, the route the guide per level, the
    sheet the gold."""
    e = served["recorder-off"]["plan"]["estimate"]
    md = served["recorder-off"]["plan"]["markdown"]
    p = e["pace"]
    assert p["xp_per_hour"]["n"] == 5 and p["xp_per_hour"]["value"] > 0
    # His own pace against the route's: near it on the demo's last levels (0.96), never the route's 1.
    assert p["level_factor"]["from"] == "your level times against Polestar's" and p["level_factor"]["factor"] != 1
    # Polestar's guide for each level, a checkpoint at each level-up.
    assert all("Polestar's" in b["title"] and "guide" in b["title"] for b in e["blocks"])
    planmod._CACHE.clear()  # kept per person and name: every scenario here is the same character
    four = planmod.plan(served["recorder-off"]["env"], D.CHAR, 4)["estimate"]
    dings = [c for c in four["checkpoints"] if c.get("ding")]
    assert dings and all(float(c["level"]).is_integer() for c in dings)
    assert re.search(r"- Level \d+ at about \+\dh\d\d", write.fallback(four))
    # The gold line: a rate from the splits, the riding goal with an ETA.
    gr = e["mount"]["gold_rate"]
    assert gr["estimated"] and gr["copper_per_hour"] and "no gold rate" not in md
    assert e["unlock"] == "Switch on Polestar's Recorder (/psrec on, then /reload) for a step-by-step plan."


def test_the_unlock_line_follows_the_setup(served):
    assert served["recorder-off"]["inputs"]["recorder"] == "off"
    assert served["no-recorder"]["inputs"]["recorder"] == "missing"
    assert served["no-recorder"]["plan"]["estimate"]["unlock"].startswith("Add Polestar's Recorder")
    assert served["full"]["inputs"]["recorder"] == "on"
    assert served["only-level-splits"]["inputs"]["recorder"] is None  # no setup report: the plain line


def test_the_estimated_gold_rate_matches_the_recorded_one(served):
    """The splits' played time between two sheets against the Recorder's
    timeline on the same play: within a tenth."""
    rec = served["full"]["inputs"]["gold_rate"]["copper_per_hour"]
    est = served["recorder-off"]["inputs"]["gold_rate"]["copper_per_hour"]
    assert rec and est and abs(est - rec) / rec < 0.1
    # The same sheets every day: the 14 days count back from the demo's
    # clock. On the wall clock they lost about one sheet a day (16 on the
    # 18th, 12 from the 21st at 19:16 UTC, the two rates then 22% apart).
    assert served["full"]["inputs"]["gold_rate"]["n"] == served["recorder-off"]["inputs"]["gold_rate"]["n"] == 14


def test_level_from_the_splits_alone(served):
    inp = served["only-level-splits"]["inputs"]
    splits = [r for r in D.rows_for("only-level-splits")]
    assert inp["level"] == max(int(r["level"]) for r in splits) + 1  # the last split is a finished level
    e = served["only-level-splits"]["plan"]["estimate"]
    assert e["start"]["level"] == inp["level"] and e["supported"]


def test_progress_from_sheets_lets_a_plan_be_reviewed(served):
    prog = served["polestar-only"]["inputs"]["progress"]
    assert len(prog) > 2 and all(p.get("estimated") for p in prog)
    on = [p["online_s"] for p in prog]
    lv = [p["level"] for p in prog]
    assert on == sorted(on) and lv == sorted(lv) and on[-1] > 0


@pytest.mark.parametrize("sc", ["recorder-off", "only-level-splits"])
def test_the_mcp_answers_by_level(served, sc):
    env = served[sc]["env"]
    ctx = T.Ctx(store=MStore(vl_url=env["VL_URL"], vm_url=env["VM_URL"]), default_char=D.CHAR, env=env)
    planmod._CACHE.clear()
    out = T.call(ctx, "plan_session", {"char": D.CHAR, "hours": 2})
    assert out["supported"] and out["estimated"] and "/psrec on" in out["unlock"]
    assert "estimated" in out["brief"] and out["facts"]["unlock"] == out["unlock"]
    assert out["blocks"] and out["markdown"]
    step = T.call(ctx, "next_step", {"char": D.CHAR})
    assert step["estimated"] and step["blocks"] and step["unlock"] == out["unlock"]
    mount = T.call(ctx, "mount_goal", {"char": D.CHAR})
    assert mount["tiers"]
