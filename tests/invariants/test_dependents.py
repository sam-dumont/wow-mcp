"""Revalidation reaches the advice built on a finding (review 6).

A nudge or a pattern line stands on the findings it cites. When one of them
is "to revalidate" (judged under other definitions, property 10), the line
it backs isn't advice anyone checked under today's definitions either. The
review's case: verify a finding, keep a nudge and a pattern citing it, bump
PREP_VERSION, run the nightly recheck. The finding went to revalidate; the
full view's nudge (verdict "now"), the pattern and the /zogzog/ nudge stayed
up, because the line screen only refused findings missing or doubtful.

Now every reader takes the lines through one rule (analysis.standing): a
line resting on a finding that awaits revalidation, is doubtful or is gone
is on hold. It leaves "What to do" on the page, the MCP's play_analysis and
whats_new, the plan's facts and the /zogzog/ bundle, and comes back once an
analysis judges its findings again. The nightly pipeline marks it too
(`on_hold`), and the readers check again at output time, so a state written
before (or a finding marked since) never shows held advice.
"""

from __future__ import annotations

import json

import pytest

from tests.analysis import test_analysis as A
from wowstats import analysis
from wowstats.analysis import prep

driven, rows, knowledge = A.driven, A.rows, A.knowledge
CHAR, finding, walk = A.CHAR, A.finding, A.walk
NUDGE = "Keep the long sessions."  # SYNTH's shareable nudge, on sessions-online


def _texts(lines) -> list[str]:
    return [x.get("text") for x in lines or []]


def _readers(env, monkeypatch, knowledge, rows) -> dict:
    """What each reader shows as advice: the page and the MCP's
    play_analysis (answer, full), /zogzog/ (answer zogzog, and the share
    port's shape), the MCP's whats_new, the plan's facts."""
    from wowstats.mcp import plantools, route, tools
    from wowstats.plan import facts

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ctx = tools.Ctx(store=None, default_char=CHAR)
    full = analysis.answer(env, CHAR)
    mcp = tools.call(ctx, "play_analysis", {"char": CHAR})
    db = prep.build([dict(r) for r in rows], CHAR, route.reference())
    plan = facts.extract(db, knowledge, route.reference(), cards=False, state=analysis.load_state(env, CHAR))
    return {"full": _texts(full["nudges"]), "full_patterns": _texts(full["patterns"]),
            "mcp": _texts(mcp["nudges"]), "mcp_patterns": _texts(mcp["patterns"]),
            "zogzog": _texts(analysis.answer(env, CHAR, "zogzog")["nudges"]),
            "shared": _texts(analysis.shared(env)["chars"].get(CHAR, {}).get("nudges")),
            "whats_new": _texts(plantools.t_whats_new(ctx, {"char": CHAR})["nudges"]),
            "plan": _texts(plan["nudges"]), "plan_findings": [f["key"] for f in plan["findings"]],
            "on_hold": full.get("on_hold") or []}



@pytest.fixture()
def kept(driven):
    """A whole run: the nudge and the pattern lines cite sessions-online."""
    env, drive = driven
    walk(env, drive)
    return env, drive


def _standing(got: dict) -> None:
    for k in ("full", "mcp", "zogzog", "shared", "whats_new", "plan"):
        assert NUDGE in got[k], (k, got[k])
    assert "Sessions are long and steady." in got["full_patterns"]
    assert not any("sessions-online" in x["waits_on"] for x in got["on_hold"])
    assert "sessions-online" in got["plan_findings"]


def _held(got: dict) -> None:
    for k in ("full", "mcp", "zogzog", "shared", "whats_new", "plan"):
        assert NUDGE not in got[k], (k, got[k])
    for k in ("full_patterns", "mcp_patterns"):
        assert "Sessions are long and steady." not in got[k], (k, got[k])
    assert "sessions-online" not in got["plan_findings"]
    held = {x["text"]: x for x in got["on_hold"]}
    assert held[NUDGE]["waits_on"] == ["sessions-online"] and held[NUDGE]["kind"] == "nudge"
    assert held["Sessions are long and steady."]["kind"] == "pattern"


def test_a_definition_change_holds_every_line_on_the_finding(kept, rows, knowledge, monkeypatch):
    env, drive = kept
    _standing(_readers(env, monkeypatch, knowledge, rows))
    monkeypatch.setattr(prep, "PREP_VERSION", prep.PREP_VERSION + 1)
    out = A.recheck_night(env, rows, knowledge)
    assert "revalidate" in out["findings"]["sessions-online"]
    # The nightly pipeline marks the lines it keeps.
    assert [n["on_hold"] for n in out["nudges"] if n["text"] == NUDGE] == [["sessions-online"]]
    assert out["runs"][-1]["on_hold"] >= 2
    _held(_readers(env, monkeypatch, knowledge, rows))
    # Judged again under today's definitions: the advice is back, with no rerun of the night.
    drive._WS.clear()
    drive.brief(env, CHAR, restart=True)
    assert drive.propose(env, CHAR, finding())["stored"]
    _standing(_readers(env, monkeypatch, knowledge, rows))


@pytest.mark.parametrize("mark", ["revalidate", "doubtful", "gone"])
def test_the_readers_check_at_output_time(kept, rows, knowledge, monkeypatch, mark):
    """A state written before the finding was marked (no `on_hold` on its
    lines): the readers hold the lines themselves."""
    env, _drive = kept
    st_file = analysis.char_dir(env, CHAR) / analysis.STATE_FILE
    st = json.loads(st_file.read_text())
    assert not any(n.get("on_hold") for n in st["nudges"])
    if mark == "gone":
        st["findings"].pop("sessions-online")
    else:
        st["findings"]["sessions-online"][mark] = (
            {"since": "2026-11-22T04:00:00+00:00", "why": ["prep version 4 -> 5"]} if mark == "revalidate" else True)
    st_file.write_text(json.dumps(st))
    got = _readers(env, monkeypatch, knowledge, rows)
    for k in ("full", "mcp", "zogzog", "shared", "whats_new", "plan"):
        assert NUDGE not in got[k], (mark, k, got[k])
    assert NUDGE in {x["text"] for x in got["on_hold"]}
