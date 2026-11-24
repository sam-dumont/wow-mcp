"""ForeverDB.net's API at release time (tools/foreverdb_api/) and its data in
the gear advice (wowstats.fdbapi, wowstats.gear).

The client runs against a fake server: pagination, the rate headers, 429
with Retry-After, the daily quota, the per-build cache that makes a second
run cost one request. The export maps recorded answers
(tests/data/foreverdb-api-responses.json: real ones, trimmed, invisible
marks removed, no key anywhere). The gear advice reads the exported file:
vendors become sources, quests get their giver, and every answer that shows
either credits ForeverDB.net."""

from __future__ import annotations

import gzip
import importlib.util
import io
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

from wowstats import fdbapi, gear

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / "data"
RECORDED = json.loads((DATA / "foreverdb-api-responses.json").read_text(encoding="utf-8"))
KEY = "fdb_test_not_a_key"


def _load(name: str):
    sys.path.insert(0, str(ROOT / "tools" / "foreverdb_api"))
    spec = importlib.util.spec_from_file_location(f"fdb_{name}", ROOT / "tools" / "foreverdb_api" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


client = _load("client")
export = _load("export")


class Resp:
    def __init__(self, status: int, body: dict | bytes, headers: dict | None = None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = {"Content-Encoding": "gzip", **(headers or {})}
        self._raw = gzip.compress(raw)

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Server:
    """Answers from a table of path -> response (or a list, one per call),
    and records what was asked and with which headers."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.asked: list[tuple[str, dict]] = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split("/api/v1/", 1)[1]
        self.asked.append((path, dict(req.header_items())))
        r = self.routes.get(path)
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if r is None:
            r = Resp(404, {"error": {"code": "not_found"}})
        if r.status != 200:
            raise urllib.error.HTTPError(req.full_url, r.status, "err", r.headers, io.BytesIO(r.read()))
        return r


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.slept: list[float] = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def ok(body: dict, left: int = 4000, minute: int = 29) -> Resp:
    return Resp(200, body, {"x-daily-remaining": str(left), "x-ratelimit-remaining": str(minute),
                            "x-ratelimit-reset": "0"})


def make(routes: dict, tmp_path=None, **kw):
    srv, clk = Server(routes), Clock()
    c = client.Client(KEY, tmp_path, opener=srv, sleep=clk.sleep, clock=clk.time, **kw)
    return c, srv, clk


META = {"data": {"build": "1.60.1.71037"}, "meta": {"build": "1.60.1.71037"}}


# --- the client --------------------------------------------------------------


def test_pages_follow_next_at_the_largest_page(tmp_path):
    page = lambda rows, nxt, total: ok({"data": rows, "meta": {"total": total, "next": nxt}})  # noqa: E731
    c, srv, _ = make({"meta": ok(META),
                      "quests?limit=100&offset=0": page([{"id": i} for i in range(100)], "/x", 130),
                      "quests?limit=100&offset=100": page([{"id": i} for i in range(100, 130)], None, 130)}, tmp_path)
    c.meta()
    rows = c.pages("quests")
    assert [r["id"] for r in rows] == list(range(130))
    assert [p for p, _ in srv.asked] == ["meta", "quests?limit=100&offset=0", "quests?limit=100&offset=100"]


def test_named_user_agent_key_in_header_only(tmp_path):
    c, srv, _ = make({"meta": ok(META)}, tmp_path)
    c.meta()
    path, headers = srv.asked[0]
    assert KEY not in path
    assert headers["X-api-key"] == KEY
    assert "wow-mcp" in headers["User-agent"] and "github.com/sam-dumont/wow-mcp" in headers["User-agent"]
    assert headers["Accept-encoding"] == "gzip"
    assert KEY not in repr(c)


def test_requests_are_spaced_under_the_minute_limit(tmp_path):
    c, _, clk = make({"meta": ok(META), "dungeons?limit=100&offset=0": ok({"data": [], "meta": {}}),
                      "zones?limit=100&offset=0": ok({"data": [], "meta": {}})}, tmp_path)
    c.meta()
    c.pages("dungeons")
    c.pages("zones")
    assert clk.slept == [pytest.approx(2.0), pytest.approx(2.0)]  # 30 a minute


def test_minute_window_used_up_waits_for_the_reset(tmp_path):
    c, _, clk = make({"meta": Resp(200, META, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000030"})},
                     tmp_path)
    c.meta()
    assert clk.slept and clk.slept[-1] == pytest.approx(30)


def test_429_waits_retry_after_then_succeeds(tmp_path):
    c, srv, clk = make({"meta": [Resp(429, {}, {"Retry-After": "17"}), ok(META)]}, tmp_path)
    c.meta()
    assert c.build == "1.60.1.71037"
    assert 17 in clk.slept and len(srv.asked) == 2


def test_no_retry_storm_on_429(tmp_path):
    c, srv, _ = make({"meta": Resp(429, {}, {"Retry-After": "1"})}, tmp_path)
    with pytest.raises(client.QuotaStop):
        c.meta()
    assert len(srv.asked) == client.MAX_429 + 1


def test_daily_quota_stops_cleanly_and_the_cache_resumes(tmp_path):
    """Under the reserve the run stops before asking again; the next run (a
    new day) finds the pages it got in the cache and asks only for the rest."""
    routes = {"meta": ok(META, left=client.RESERVE + 1),
              "vendors/1": ok({"data": {"id": 1}}, left=client.RESERVE),
              "vendors/2": ok({"data": {"id": 2}}, left=4000)}
    c, srv, _ = make(routes, tmp_path)
    c.meta()
    c.get("vendors/1")
    with pytest.raises(client.QuotaStop):
        c.get("vendors/2")
    assert [p for p, _ in srv.asked] == ["meta", "vendors/1"]

    routes["meta"] = ok(META)
    c2, srv2, _ = make(routes, tmp_path)
    c2.meta()
    assert c2.get("vendors/1")["data"]["id"] == 1
    assert c2.get("vendors/2")["data"]["id"] == 2
    assert [p for p, _ in srv2.asked] == ["meta", "vendors/2"]


def test_402_is_a_quota_stop(tmp_path):
    c, _, _ = make({"meta": Resp(402, {"error": {"code": "subscription_required"}})}, tmp_path)
    with pytest.raises(client.QuotaStop):
        c.meta()


def test_errors_are_not_retried_blind(tmp_path):
    """A 404 or 400 counts against the quota too: one request, then an error."""
    c, srv, _ = make({"meta": ok(META)}, tmp_path)
    c.meta()
    with pytest.raises(client.ApiError):
        c.get("dungeons/1")
    assert len(srv.asked) == 2


def test_server_errors_retry_a_little(tmp_path):
    c, srv, _ = make({"meta": [Resp(503, {}), ok(META)]}, tmp_path)
    c.meta()
    assert len(srv.asked) == 2
    c2, srv2, _ = make({"meta": Resp(502, {})}, tmp_path)
    with pytest.raises(client.ApiError):
        c2.meta()
    assert len(srv2.asked) == client.MAX_5XX + 1


def test_same_build_costs_one_request(tmp_path):
    """No ETag or Last-Modified to revalidate with (measured 2026-11-24): the
    per-build cache is what makes an unchanged build free, /meta aside."""
    routes = routes_with_new()
    c, srv, _ = make(routes, tmp_path)
    export.build(c, log=lambda s: None)
    first = len(srv.asked)
    assert first > 5
    c2, srv2, _ = make(routes, tmp_path)
    export.build(c2, log=lambda s: None)
    assert [p for p, _ in srv2.asked] == ["meta"]
    assert c2.cached == first - 1


def test_new_build_fetches_again(tmp_path):
    routes = routes_with_new()
    c, _, _ = make(routes, tmp_path)
    export.build(c, log=lambda s: None)
    routes["meta"] = ok({"data": {"build": "1.60.1.99999"}, "meta": {"build": "1.60.1.99999"}})
    c2, srv2, _ = make(routes, tmp_path)
    export.build(c2, log=lambda s: None)
    assert len(srv2.asked) > 5


def test_no_key_no_run():
    with pytest.raises(client.ApiError):
        client.Client("", None)


# --- the export, on recorded answers ------------------------------------------


# One weapon the game-items file doesn't have: made up from Staff of the
# Shade's recorded answer, so the export fetches its detail.
NEW_ID = 9_999_901


def routes_with_new() -> dict:
    routes = {k: ok(v) for k, v in RECORDED.items()}
    page = json.loads(json.dumps(RECORDED["items?class=2&limit=100&offset=0"]))
    page["data"].append({**page["data"][0], "id": NEW_ID, "name": "Made-up Rod"})
    page["meta"]["total"] = len(page["data"])
    routes["items?class=2&limit=100&offset=0"] = ok(page)
    detail = json.loads(json.dumps(RECORDED["items/2549"]))
    detail["data"].update(id=NEW_ID, name="Made-up Rod")
    routes[f"items/{NEW_ID}"] = ok(detail)
    return routes


@pytest.fixture(scope="module")
def exported(tmp_path_factory) -> Path:
    routes = routes_with_new()
    srv, clk = Server(routes), Clock()
    c = client.Client(KEY, None, opener=srv, sleep=clk.sleep, clock=clk.time)
    out = export.build(c, log=lambda s: None)
    p = tmp_path_factory.mktemp("fdb") / "api.json"
    p.write_text(json.dumps(out), encoding="utf-8")
    return p


def test_export_maps_vendors_quests_and_items(exported):
    d = json.loads(exported.read_text())
    assert d["source"]["credit"] == "Data from ForeverDB.net" and d["source"]["url"] == "https://foreverdb.net"
    assert d["source"]["build"] == "1.60.1.71037"
    v = d["vendors"]["228"]
    assert v["name"] == "Avette Fellwood" and v["side"] == 1 and v["at"]["zone"] == "Duskwood"
    assert d["sold"]["11304"] == [{"vendor": 228, "price": 4861, "limited": 2, "seen": True}]
    q = d["quests"]["2"]
    assert q["ends"]["name"] == "Senani Thunderheart" and q["ends"]["at"] == {
        "map": 1440, "zone": "Ashenvale", "x": 73.8, "y": 61.5}
    assert q["ends"]["seen"] is True and q["starts"]["type"] == "item"
    assert d["in_forever"] == [1489, 2381, 2383, 2384, 2549, 11304, NEW_ID]
    # Only what the game-items file lacks (or has from Era) is asked for.
    assert list(d["stats"]) == [str(NEW_ID)]
    assert d["stats"][str(NEW_ID)]["stats"] == {"sta": 15, "spShadow": 37, "dps": pytest.approx(22.3077, abs=1e-3)}
    assert d["stats"][str(NEW_ID)]["speed"] == 2600 and d["stats"][str(NEW_ID)]["dmg"] == [46, 70]
    assert d["dungeons"]["33"]["levels"] == [20, 30]
    assert export.check(exported) == []


def test_check_refuses_a_file_without_credit(tmp_path, exported):
    d = json.loads(exported.read_text())
    d["source"]["credit"] = ""
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(d))
    assert export.check(p)


# --- the gear advice with the file ---------------------------------------------


@pytest.fixture
def with_api(monkeypatch, exported):
    monkeypatch.setenv("FOREVERDB_DATA_FILE", str(exported))
    fdbapi.reset()
    gear._CACHE.clear()
    yield gear.reference()
    fdbapi.reset()
    gear._CACHE.clear()


def who(side=1, **kw):
    return gear.Who(cls="HUNTER", level=20, side=side, race_id=1, professions={}, done=set(), zone=None, **kw)


def test_vendor_is_a_source(with_api):
    ref = with_api
    srcs = [s for s in ref.data["sources"]["11304"] if s["kind"] == "vendor"]
    assert srcs == [{"kind": "vendor", "vendor": 228, "price": 4861, "limited": 2, "seen": True}]
    s = gear._source(ref, srcs[0], who(), ref.item("11304"), 11304)
    assert s["name"] == "Avette Fellwood" and s["zone"] == "Duskwood" and s["price_copper"] == 4861
    assert s["data_source"] == "foreverdb"
    assert gear.source_text(s) == "vendor: Avette Fellwood in Duskwood (73.1, 44.6), 48s 61c (data: ForeverDB.net)"
    assert gear.source_link(s).endswith("/npc=228")
    assert gear._source(ref, srcs[0], who(side=2), ref.item("11304"), 11304) is None  # Alliance vendor


def test_quest_gets_giver_and_turn_in(with_api):
    ref = with_api
    # Quest 937 rewards gear in the gear data; who gives and takes it, and
    # where they stand, come from the API.
    iid = next(i for i, ss in ref.data["sources"].items() for s in ss if s["kind"] == "quest" and s["quest"] == 937)
    s = gear._source(ref, {"kind": "quest", "quest": 937}, who(), ref.item(iid), int(iid))
    assert s["giver"] == {"type": "npc", "id": 3519, "name": "Sentinel Arynia Cloudsbreak", "zone": "Teldrassil",
                          "x": 38.3, "y": 34.4, "seen_in_forever": True, "data_source": "foreverdb"}
    assert s["turn_in"]["name"] == "Sentinel Arynia Cloudsbreak"
    assert gear.source_text(s).endswith(
        "from Sentinel Arynia Cloudsbreak in Teldrassil (38.3, 34.4) (data: ForeverDB.net)")


def test_without_the_file_nothing_changes(monkeypatch):
    monkeypatch.setenv("FOREVERDB_DATA_FILE", "off")
    fdbapi.reset()
    gear._CACHE.clear()
    ref = gear.reference()
    assert not any(s["kind"] == "vendor" for ss in ref.data["sources"].values() for s in ss)
    assert ref.meta()["foreverdb_api"] is None
    s = gear._source(ref, {"kind": "quest", "quest": 937}, who(), {}, 0)
    assert s is not None and "giver" not in s


def test_api_stats_fill_an_item_the_client_files_lack(with_api):
    """An item the game-items file lacks (or fills from Classic Era) that the
    Forever client has takes the API's copy of the client row; a client row
    is never replaced."""
    ref = with_api
    assert ref.item(str(NEW_ID))["stats"]["spShadow"] == 37
    assert ref.data["names"][str(NEW_ID)] == "Made-up Rod"
    assert str(NEW_ID) not in ref.data["era_only"]
    assert ref.item("11304") == {k: v for k, v in gear.game_items()["items"]["11304"].items()
                                 if k not in ("name", "dmg", "from")}


# --- the credit ------------------------------------------------------------------


def test_answers_with_api_data_carry_the_credit():
    assert fdbapi.uses({"sources": [{"kind": "vendor", "data_source": "foreverdb"}]})
    assert fdbapi.uses({"where": ["vendor: X in Y, 1s (data: ForeverDB.net)"]})
    assert not fdbapi.uses({"where": ["quest Z (Elwynn Forest)"]})


def test_mcp_adds_data_credit():
    from wowstats.mcp import tools

    out = tools.credited({"upgrades": [{"where": ["vendor: X in Y, 1s (data: ForeverDB.net)"]}]})
    assert out["data_credit"] == {"name": "ForeverDB.net", "url": "https://foreverdb.net",
                                  "text": "Data from ForeverDB.net"}
    assert "data_credit" not in tools.credited({"upgrades": [{"where": ["quest Z (Elwynn Forest)"]}]})


APP = ROOT / "web" / "app.js"


def test_page_links_foreverdb_wherever_api_data_shows():
    """Every line of web/app.js that writes a vendor or a quest's giver ends
    with the data credit (dataCreditEl)."""
    src = APP.read_text(encoding="utf-8")
    assert "Data from ForeverDB.net" in src and "https://foreverdb.net" in src
    blocks = re.findall(r"if \((?:sr|src)\.kind === 'vendor'[^{]*\{.*?\n  \}", src, re.S)
    assert len(blocks) == 2, blocks
    assert all("dataCreditEl()" in b for b in blocks)
    giver = re.search(r"const g = sr\.giver;.*?\n  \}", src, re.S).group(0)
    assert "dataCreditEl()" in giver
