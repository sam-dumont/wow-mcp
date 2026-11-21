"""Consumer agreement (property 8): the page and the MCP give the same
numbers for the same history, under a non-default configuration (WEIGHTS
for another spec, another timezone), and a friend's account gives the same
numbers for the same play as the owner's, each reading only their own.

The numbers compared: deaths, every zone's time and visits, every level's
split, every dungeon run, every session's span, the kills; and the
analysis' own counts (query_metric) against the page's. The friend's
history is the demo's, renamed, in their own VictoriaLogs tenant.

Builds on tests/test_zone_time.py (page, journal and MCP zone facts),
tests/test_mcp_weights.py (the WEIGHTS spec) and tests/test_tenancy.py
(nothing of one person reaches another).
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests import degraded as D
from wowstats import logsql, ui
from wowstats.analysis import prep
from wowstats.stores import parse_t

pytestmark = pytest.mark.usefixtures("demo_clock")

FRIEND_CHAR = "Pipwick-Otherhall"
OWNER, FRIEND = "owner-sub-1", "friend-sub-2"


def renamed(rows: list[dict]) -> list[dict]:
    """The demo's play under the friend's character."""
    a, b = D.CHAR, FRIEND_CHAR
    out = []
    for r in rows:
        r = {k: (v.replace(a, b).replace(a.replace("-", " "), b.replace("-", " ")) if isinstance(v, str) else v)
             for k, v in r.items()}
        out.append(r)
    return out


class TenantVL:
    """VictoriaLogs answering each tenant (AccountID) from its own rows,
    wowstats.logsql on each, and a VictoriaMetrics that holds nothing."""

    def __init__(self, tenants: dict[str, list[dict]]):
        self.rows = {k: sorted(v, key=lambda r: parse_t(r.get("_time"))) for k, v in tenants.items()}
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if self.command == "POST" and self.headers.get("Content-Length"):
                    q |= dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                if u.path == "/select/logsql/query":
                    rows = fake.rows.get(self.headers.get("AccountID") or "0", [])
                    try:
                        parsed = logsql.parse(q.get("query", ""))
                    except logsql.Unsupported:
                        return self._send(400, b"unsupported in the stand-in")
                    lo = parse_t(q["start"]) if q.get("start") else None
                    hi = parse_t(q["end"]) if q.get("end") else None
                    rows = [r for r in rows if (lo is None or lo <= parse_t(r["_time"]))
                            and (hi is None or parse_t(r["_time"]) <= hi)]
                    out = parsed.run(rows)[:int(q.get("limit") or 10 ** 9)]
                    return self._send(200, "".join(json.dumps(r) + "\n" for r in out).encode())
                if u.path.startswith("/api/v1/"):
                    return self._send(200, b'{"status": "success", "data": {"result": []}}')
                self._send(404, b"")

            do_POST = do_GET

        srv = type("S", (ThreadingHTTPServer,), {"request_queue_size": 64, "daemon_threads": True})
        self.httpd = srv(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- the numbers


def page_numbers(app, char: str) -> dict:
    c = app.api_char(char)
    j = app.views.journal(char)
    return {
        "deaths": c["deaths"]["total"],
        "zones": sorted((z["zone"], z["secs"], z["visits"]) for z in c["zones"]),
        "levels": [(lv["level"], lv["seconds"], lv["xp"], lv["quests"], lv["kills"], bool(lv["partial"]))
                   for lv in c["levels"]],
        "dungeons": sorted((d["name"], d["level"], d["seconds"], d["xp"]) for d in c["dungeons"]),
        "sessions": sorted((s["start"], s["end"]) for s in j["sessions"]),
        "spec": c["spec"]["scored_for"]["label"],
    }


def mcp_numbers(call, char: str) -> dict:
    zones = call("zones", char=char, since="2000-01-01", limit=200)
    assert zones["zones_total"] == len(zones["zones"])
    zones = zones["zones"]
    got = call("sessions", char=char, since="2000-01-01", limit=50)
    assert got["more"] is False
    sessions = got["sessions"]
    return {
        "deaths": call("deaths", char=char)["total"],
        # The MCP also lists places that only have kills or quests (no time): the page's are the visited ones.
        "zones": sorted((z["zone"], z["secs"], z["visits"]) for z in zones if z["visits"]),
        "levels": [(lv["level"], lv["seconds"], lv["xp"], lv["quests"], lv["kills"], bool(lv["partial"]))
                   for lv in call("levels", char=char)["levels"]],
        "dungeons": sorted((d["name"], d["level"], d["seconds"], d["xp"]) for d in call("dungeons", char=char)["runs"]),
        "sessions": sorted((s["start"], s["end"]) for s in sessions),
        "spec": call("gear_options", char=char)["spec"],
    }


def analysis_numbers(call, char: str) -> dict:
    """The analysis' counts of the same things (query_metric), and by day:
    the days follow WOW_TIMEZONE, their sum doesn't."""
    total = call("query_metric", char=char, metrics=["deaths", "kills", "session_count"])
    row = dict(zip(total["query"]["metrics"], [int(x) for x in total["result"].splitlines()[1].split("\t")],
                   strict=True))
    days = call("query_metric", char=char, metrics=["deaths"], group_by=["time"], grain="day", limit=500)
    by_day = [line.split("\t") for line in days["result"].splitlines()[1:] if "\t" in line]
    return {**row, "deaths_by_day_sum": sum(int(n or 0) for _, n in by_day), "days": [d for d, _ in by_day]}


def _fresh() -> None:
    from wowstats import plan as planmod
    from wowstats.mcp import tools as T

    planmod._CACHE.clear()
    T._VIEWS.clear()


def _mcp(ctx):
    from wowstats.mcp import tools as T

    return lambda name, **a: T.call(ctx, name, a)


def _single(vl_url: str, tmp_path, extra: dict) -> tuple:
    from wowstats.mcp import tools as T
    from wowstats.mcp.store import Store as MStore

    env = {"VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1", "VL_URL": vl_url, "VM_URL": vl_url,
           "MODEL3D": "off", "DEFAULT_CHAR": D.CHAR, "HOME": str(tmp_path / "home"), "WOW_ITEMS": "off",
           "ANALYSIS_STATE_DIR": str(tmp_path / "analysis"), **extra}
    app = ui.App(env)
    ctx = T.Ctx(store=MStore(vl_url=vl_url, vm_url=vl_url), default_char=D.CHAR, env=env)
    return app, _mcp(ctx)


@pytest.fixture(scope="module")
def demo_vl():
    vl = TenantVL({"0": D.rows_for("full")})
    yield vl
    vl.close()


def _other_spec() -> str:
    from tests.test_mcp_weights import _other_spec as other

    return other()


CONFIGS = {
    "default": ({}, None),
    "WEIGHTS": (None, None),  # filled in the test: another spec than the add-on's
    "America/Los_Angeles": ({}, "America/Los_Angeles"),
    "Pacific/Auckland": ({}, "Pacific/Auckland"),
}


@pytest.mark.parametrize("config", list(CONFIGS))
def test_page_and_mcp_agree(demo_vl, tmp_path, monkeypatch, config):
    monkeypatch.setenv("WOW_ITEMS", "off")
    extra, tz = CONFIGS[config]
    if config == "WEIGHTS":
        extra = {"WEIGHTS": f"{D.CHAR}:{_other_spec()}"}
    if tz:
        from zoneinfo import ZoneInfo

        monkeypatch.setenv("WOW_TIMEZONE", tz)
        monkeypatch.setattr(prep, "TZ", ZoneInfo(tz))
        monkeypatch.setattr(prep, "TZ_NAME", tz)
        extra = {**extra, "WOW_TIMEZONE": tz}
    _fresh()
    app, call = _single(demo_vl.url, tmp_path, extra)
    page, mcp = page_numbers(app, D.CHAR), mcp_numbers(call, D.CHAR)
    assert page == mcp
    assert page["deaths"] and page["zones"] and page["levels"] and page["dungeons"] and page["sessions"]
    got = analysis_numbers(call, D.CHAR)
    assert got["deaths"] == got["deaths_by_day_sum"] == page["deaths"]
    assert got["session_count"] == len(page["sessions"])
    assert got["kills"] == call("character_summary", char=D.CHAR)["kills"]
    if config == "WEIGHTS":
        assert page["spec"] != app.api_char(D.CHAR)["spec"].get("name")  # sanity: not the add-on's pick
    if tz:
        # The days are the player's: a death at 23:30 UTC is the next day in Auckland.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        deaths = app.api_char(D.CHAR)["deaths"]["recent"]
        local = {datetime.fromisoformat(d["t"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date().isoformat()
                 for d in deaths}
        assert local <= set(got["days"])


# ---------------------------------------------------------------- owner and friend


@pytest.fixture(scope="module")
def two_people(tmp_path_factory):
    from wowstats import tenancy

    tmp = tmp_path_factory.mktemp("people")
    vl = TenantVL({"0": D.rows_for("full"), "1": renamed(D.rows_for("full"))})
    master = tmp / "master.key"
    master.write_text(tenancy.b64u(b"m" * 32))
    friends = tmp / "friends.json"
    friends.write_text(json.dumps({FRIEND: "mira"}))
    env = {"BIND": "127.0.0.1", "VIEW": "full", "AUTH": "proxy", "PROXY_TRUSTED_CIDRS": "127.0.0.1/32",
           "VL_URL": vl.url, "VM_URL": vl.url, "FRIENDS_VL_URL": vl.url, "WOW_STATE_DIR": str(tmp / "state"),
           "WOW_OWNER_SUB": OWNER, "WOW_OWNER_SLUG": "sam", "WOW_FRIENDS_FILE": str(friends),
           "SITE_DOMAIN": "m.example", "PUBLIC_URL": "https://wow-full.m.example",
           "ZOGZOG_MASTER_KEY_FILE": str(master), "ZOGZOG_KEY_FILE": str(tmp / "owner-zogzog.key"),
           "MODEL3D": "off", "MODEL3D_CACHE_DIR": str(tmp / "cache"), "WOW_ITEMS": "off",
           "ANALYSIS_STATE_DIR": str(tmp / "owner-analysis"), "DEFAULT_CHAR": D.CHAR}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    yield t
    vl.close()


def _person(t, sub: str):
    u = t.identify(sub).user
    return t.app_for(u), _mcp(t.ctx_for(u))


def test_friend_and_owner_get_the_same_numbers_for_the_same_play(two_people, monkeypatch):
    monkeypatch.setenv("WOW_ITEMS", "off")
    _fresh()
    owner_app, owner_mcp = _person(two_people, OWNER)
    friend_app, friend_mcp = _person(two_people, FRIEND)
    owner = page_numbers(owner_app, D.CHAR)
    friend = page_numbers(friend_app, FRIEND_CHAR)
    assert friend == owner
    assert mcp_numbers(friend_mcp, FRIEND_CHAR) == friend
    assert analysis_numbers(friend_mcp, FRIEND_CHAR) == analysis_numbers(owner_mcp, D.CHAR)


def test_neither_reads_the_others_character(two_people, monkeypatch):
    from wowstats.mcp import tools as T

    monkeypatch.setenv("WOW_ITEMS", "off")
    _fresh()
    owner_app, owner_mcp = _person(two_people, OWNER)
    friend_app, friend_mcp = _person(two_people, FRIEND)
    for app, other in ((friend_app, D.CHAR), (owner_app, FRIEND_CHAR)):
        assert other not in json.dumps([c["char"] for c in app.views.chars()])
        try:
            got = app.api_char(other)
        except LookupError:  # the HTTP layer's 404
            continue
        assert got["deaths"]["total"] == 0 and not got["levels"] and not got["zones"] and not got["dungeons"]
    for call, other in ((friend_mcp, D.CHAR), (owner_mcp, FRIEND_CHAR)):
        try:
            got = call("deaths", char=other)
        except T.ToolError:
            continue
        assert not got["total"] and not got["recent"]  # 0, or unknown (None) with no Recorder
