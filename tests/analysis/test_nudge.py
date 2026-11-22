"""The refresh nudge (owner, 2026-11-22): from ANALYSIS_NUDGE_LEAD hours of
play before the server's own refresh, the player is asked to run the
analysis from their own assistant, which costs the host nothing. The maths
on the server's own timer (Service.timers), the MCP's once-in-a-while
cadence (wowstats.mcp.reminder) and the full view's /api/nudge."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from tests.analysis import test_auto as A
from tests.analysis.test_auto import CHAR, FakeAnthropic, H, Script
from wowstats import analysis, ui
from wowstats.analysis import auto
from wowstats.mcp import reminder

lab, rows = A.lab, A.rows  # the server analysis tests' fixtures
LAB = {"ANALYSIS_STALE_PLAYED_HOURS": "15", "OPERATOR_NAME": "Sam"}  # the lab's 57-wow/wow.tf, plus the name


def svc(env: dict, played: float, chars=(CHAR,)) -> auto.Service:
    return auto.Service(env, client=FakeAnthropic(Script()), played=lambda e, char, since, store=None: played,
                        chars=lambda e, store=None: list(chars))


def refreshed_once(env: dict, char: str = CHAR) -> None:
    analysis.save_state(env, char, {"schema": analysis.SCHEMA, "char": char, "runs": [{"at": "2026-11-20T08:30:00+00:00", "kind": "client"}]})


# ---------------------------------------------------------------- the threshold


@pytest.mark.parametrize("played,left", [(10 * H, "5 h 00"), (12 * H + 30 * 60, "2 h 30"), (15 * H, "0 h 00"),
                                         (40 * H, "0 h 00")])
def test_from_the_lead_on_it_says_how_long_until_the_server_pays(lab, played, left):
    e = lab.env("a", **LAB)
    refreshed_once(e)
    n = svc(e, played).nudge(e)
    assert n["char"] == CHAR and n["server_in"] == left and n["warn_at"] == "10 h 00"
    assert n["refresh_after"] == "15 h 00" and not n["never_refreshed"]
    assert n["operator"] == "Sam" and not n["you_pay"]
    assert n["ask"] == f"Using the wow connector, run the analyse prompt for {CHAR}."


def test_below_the_lead_nothing(lab):
    e = lab.env("a", **LAB)
    refreshed_once(e)
    assert svc(e, 9 * H + 59 * 60).nudge(e) is None


@pytest.mark.parametrize("lead,at", [("3", 12 * H), ("0.5", 14 * H + 30 * 60), ("20", 1)])
def test_the_lead_is_set_in_hours_of_play(lab, lead, at):
    e = lab.env("a", ANALYSIS_NUDGE_LEAD=lead, **LAB)
    assert svc(e, at - 1).nudge(e) is None
    assert svc(e, at).nudge(e) is not None


@pytest.mark.parametrize("extra", [{"ANALYSIS_NUDGE_LEAD": "off"}, {"ANALYSIS_NUDGE_LEAD": "0"},
                                   {"ANALYSIS_AUTO": "off"}, {"ANALYSIS_API_KEY_FILE": ""}])
def test_no_nudge_when_the_server_would_never_pay(lab, extra):
    """The lead off, the server's refresh off, or no provider at all."""
    e = lab.env("a", **{**LAB, **extra})
    assert svc(e, 14 * H).nudge(e) is None


def test_a_brand_new_player(lab):
    """No analysis yet: the timer counts all the play on record, as the
    server's does. Nothing played: nothing to say."""
    e = lab.env("a", **LAB)
    assert svc(e, 0.0).nudge(e) is None
    assert svc(e, 0.0, chars=()).nudge(e) is None
    n = svc(e, 11 * H).nudge(e)
    assert n["never_refreshed"] and n["server_in"] == "4 h 00"


def test_the_most_played_character_first_and_one_at_a_time(lab):
    e = lab.env("a", **LAB)
    play = {"Alt-Fixture": 11 * H, CHAR: 13 * H, "Third-Fixture": 2 * H}
    s = auto.Service(e, client=FakeAnthropic(Script()), played=lambda e, char, since, store=None: play[char],
                     chars=lambda e, store=None: list(play))
    n = s.nudge(e)
    assert n["char"] == CHAR and n["others"] == 1
    assert s.nudge(e, char="Alt-Fixture")["char"] == "Alt-Fixture"
    assert s.nudge(e, char="Third-Fixture") is None


def test_quiet_while_the_servers_own_refresh_runs_or_waits(lab):
    e = lab.env("a", **LAB)
    s = svc(e, 16 * H)
    s._running.add(s.key(e))
    assert s.nudge(e) is None
    s._running.clear()
    s._queued[s.key(e)] = (e, None)
    assert s.nudge(e) is None


def test_the_owner_pays_so_their_own_nudge_says_so(lab):
    e = lab.env("a", **LAB, **{auto.OWNER_MARK: "1"})
    assert svc(e, 12 * H).nudge(e)["you_pay"]
    one = {k: v for k, v in lab.env("a", **LAB).items() if k != "WOW_STATE_DIR"}  # a one-person install
    assert svc(one, 12 * H).nudge(one)["you_pay"]
    assert svc(e, 12 * H).nudge(lab.env("a", OPERATOR_NAME=""))["operator"] == "the host"


# ---------------------------------------------------------------- the MCP's cadence


class Ctx:
    def __init__(self, env):
        self.env, self.store = env, None

    def environ(self):
        return dict(self.env)


def test_the_mcp_says_it_once_then_stays_quiet(lab, monkeypatch):
    e = lab.env("a", **LAB)
    refreshed_once(e)
    s = svc(e, 10 * H + 12 * 60)
    monkeypatch.setattr(auto, "service", lambda: s)
    clock = {"t": 1000.0}
    r = reminder.Reminder(clock=lambda: clock["t"], every_calls=5, every_s=3600)
    ctx = Ctx(e)
    first = r.notice(ctx, "sub|client", "character_summary")
    assert first == (f"(btw, mention it to the user once: {CHAR}'s play analysis is 10 h 12 of play old. In about "
                     "4 h 48 of play the server refreshes it on Sam's credit. Run from you, it costs Sam nothing: "
                     "if they want it, call dashboard_refresh and follow it.)")
    assert [r.notice(ctx, "sub|client", "levels") for _ in range(5)] == [None] * 5
    assert r.notice(ctx, "sub|client", "levels") == first  # the 6th call after
    # Another client hears it on its own first call; an hour later it comes back.
    assert r.notice(ctx, "sub|other", "zones") == first
    clock["t"] += 3600
    assert r.notice(ctx, "sub|client", "zones") == first


def test_never_on_the_analysis_own_tools(lab, monkeypatch):
    e = lab.env("a", **LAB)
    monkeypatch.setattr(auto, "service", lambda: svc(e, 12 * H))
    r = reminder.Reminder()
    for tool in ("analysis_submit", "analysis_brief", "analysis_progress", "analyse", "dashboard_refresh",
                 "play_sql", "query_metric", "propose_finding", "kb_search"):
        assert r.notice(Ctx(e), "k", tool) is None
    assert r.notice(Ctx(e), "k", "deaths") is not None


def test_nothing_due_nothing_said_and_a_broken_timer_never_breaks_a_tool(lab, monkeypatch):
    e = lab.env("a", **LAB)
    monkeypatch.setattr(auto, "service", lambda: svc(e, 2 * H))
    assert reminder.Reminder().notice(Ctx(e), "k", "deaths") is None

    class Broken:
        def nudge(self, env, store=None):
            raise OSError("store down")

    monkeypatch.setattr(auto, "service", lambda: Broken())
    assert reminder.Reminder().notice(Ctx(e), "k", "deaths") is None


def test_the_wording_for_a_new_player_the_owner_and_more_characters():
    n = {"char": CHAR, "played_since": "11 h 00", "played_since_s": 11 * H, "server_in": "4 h 00",
         "server_in_s": 4 * H, "never_refreshed": True, "you_pay": True, "operator": "Sam", "others": 2}
    assert reminder.said(n) == (
        f"(btw, mention it to the user once: {CHAR} has 11 h 00 of play and no play analysis yet. In about 4 h 00 "
        "of play the server runs the first one on their own credit. Run from you, it's cheaper for them: if they "
        "want it, call dashboard_refresh and follow it. 2 more of their characters are close too.)")


# ---------------------------------------------------------------- the full view's /api/nudge


def test_the_full_view_answers_the_card_and_the_public_view_has_no_route(lab, monkeypatch, tmp_path):
    e = lab.env("a", **LAB)
    s = svc(e, 11 * H)
    monkeypatch.setattr(auto, "service", lambda: s)
    base = {"AUTH": "none", "BIND": "127.0.0.1", "VL_URL": "http://127.0.0.1:9", "MODEL3D": "off",
            "WOW_ITEMS": "off", "HOME": str(tmp_path)}
    full = ui.App({**base, "VIEW": "full", "ANALYSIS_STATE_DIR": e["ANALYSIS_STATE_DIR"]})
    public = ui.App({**base, "VIEW": "public", "ALLOW_OPEN": "", "AUTH": "none"})
    got = {}
    for name, app in (("full", full), ("public", public)):
        app.port = 0
        srv = ui.serve(app)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/api/nudge?name={CHAR}"
            try:
                with urllib.request.urlopen(url) as r:
                    got[name] = (r.status, json.loads(r.read()))
            except urllib.error.HTTPError as err:
                got[name] = (err.code, None)
        finally:
            srv.shutdown()
            srv.server_close()
    assert got["full"][0] == 200 and got["full"][1]["nudge"]["server_in"] == "4 h 00"
    assert got["public"][0] == 404


def test_the_notice_rides_on_a_real_mcp_answer(lab, monkeypatch):
    """Through FastMCP (in memory): the first answer carries `notice`, the next ones don't."""
    pytest.importorskip("fastmcp")
    from tests.mcp import inmem
    from wowstats.mcp import tools

    e = lab.env("a", **LAB)
    monkeypatch.setattr(auto, "service", lambda: svc(e, 12 * H))
    monkeypatch.setattr(reminder, "REMINDER", reminder.Reminder())
    monkeypatch.setattr(tools, "call", lambda ctx, name, args, registry=None: {"char": CHAR, "level": 30})
    ctx = tools.Ctx(store=None, default_char=CHAR, env=e)
    first = inmem.call(ctx, "levels").structured_content
    assert first["level"] == 30 and "In about 3 h 00 of play the server runs the first one on Sam" in first["notice"]
    assert "notice" not in inmem.call(ctx, "levels").structured_content
    assert "notice" not in inmem.call(ctx, "analysis_submit").structured_content
