"""A public server must never hand out other players' names, positions,
money, Battle.net market data, Auctionator prices or the MCP. The store
here is poisoned: every row it returns carries all of those, so anything
that leaks shows up in the bytes the server sends."""

import json
import sys
import types

import pytest

from wowstats import __version__, ui
from wowstats.stores import Store

SECRET_PLAYER = "Secretfriend-Otherrealm"
SECRET_MONEY = "987654321"
SECRET_X, SECRET_Y = "12.345", "67.891"
SECRET_PRICE = "4242421"
SECRET_TOKEN = "313131"
CHAR = "Hero-Test"
KINDS = ["session", "xp", "level", "zone", "step", "skip", "kill", "turnin", "death", "taxi", "land", "enter"]
SECRETS = [SECRET_PLAYER, "Secretfriend", SECRET_MONEY, SECRET_X, SECRET_Y, SECRET_PRICE, SECRET_TOKEN]


def poison_row(i: int, kind: str) -> dict:
    return {
        "_time": f"2026-11-17T15:{i:02d}:00Z", "char": CHAR, "feed": "wow-recorder", "kind": kind,
        # The session buckets' first and last record (stores.SESSION_BUCKETS).
        "t0": f"2026-11-17T15:{i:02d}:00Z", "t1": f"2026-11-17T15:{i:02d}:00Z",
        "source_id": f"s{i}", "level": "12", "xp": "100", "xp_max": "2000", "xp_gained": "50",
        "zone": "Elwynn Forest", "duration_s": "60", "secs": "60", "visits": "1", "n": "3", "events": "9",
        "last_seen": "2026-11-17T15:00:00Z", "seen": "2026-11-17T15:00:00Z",
        "latest": json.dumps({"level": 12, "zone": "Elwynn Forest"}),
        "player": SECRET_PLAYER, "context": "grouped", "classes": json.dumps(["MAGE"]), "races": json.dumps(["Human"]),
        "zones": json.dumps(["Elwynn Forest"]), "money_copper": SECRET_MONEY, "x": SECRET_X, "y": SECRET_Y,
        "map_id": "1429", "quest_id": "7", "quest_name": "Kobold Camp Cleanup", "guide": "g", "step": str(i),
        "name": "The Deadmines", "seconds": "1800", "quests": "3", "kills": "20", "partial": "false",
        "item_key": "2592", "scan_day": "2026-11-17", "lo": SECRET_PRICE, "hi": SECRET_PRICE, "av": "5",
        "price_copper": SECRET_PRICE, "realm": "Test", "race": "Gnome", "class": "PRIEST",
        # wow-gear: the sheet itself is public, anything else in it is not.
        "items": json.dumps([{"slot": 3, "slot_name": "Shoulder", "item_id": 5820, "name": "Faerie Mantle",
                              "quality": 2, "item_level": 32, "money_copper": SECRET_MONEY, "x": SECRET_X}]),
        "professions": json.dumps([{"skill_id": 185, "name": "Cooking", "rank": 130, "max_rank": 225,
                                    "player": SECRET_PLAYER}]),
        "wanted": json.dumps([{"item_id": 12998, "name": "Magician's Mantle", "slot_name": "Shoulder", "level": 25,
                               "price_copper": SECRET_PRICE}]),
        "item_level": "26.7", "guild": "Moss Order", "spec": "Shadow", "faction": "Alliance", "rested_xp": "1500",
    }


class PoisonStore(Store):
    def __init__(self):
        super().__init__("http://vl.invalid", vm_url="http://vm.invalid")

    def vl(self, query, keys=(), ops=None, limit=0):
        return [poison_row(i, k) for i, k in enumerate(KINDS)]

    def vl_raw(self, base, query, start, end, limit=0):
        return [poison_row(0, "pos")]

    def vm_query(self, q):
        return [{"metric": {"item": "Wool Cloth", "item_id": "2592", "realm": "Normal", "faction": "Alliance"},
                 "value": [1, SECRET_TOKEN]}]

    def vm_range(self, q, start, end, step):
        return [{"metric": {}, "values": [[1, SECRET_TOKEN], [2, SECRET_TOKEN]]}]

    def profile_query(self, q):
        # The character profile (render, item level, talents): none here;
        # tests that need one answer vm-wow over HTTP (test_publish).
        if getattr(self, "_http_profile", False):
            return super().profile_query(q)
        return []


@pytest.fixture
def fake_extras(monkeypatch):
    """A stand-in for the optional MCP module."""
    calls = []
    for name in ("mcp",):
        mod = types.ModuleType(f"wowstats.{name}")

        def handle(handler, view, _name=name):
            calls.append(_name)
            handler.json(200, {"from": _name, "player": SECRET_PLAYER})

        mod.handle = handle
        mod.owns = lambda path: path == "/mcp" or path.startswith(("/oauth/", "/.well-known/oauth-"))
        mod.reset = lambda env: None
        # wowstats.mcp's seam: the endpoint runs on its own port (main()),
        # never through the UI's handler.
        mod.endpoint = lambda env, bind: types.SimpleNamespace(
            ctx=types.SimpleNamespace(title=None), path="/mcp",
            attach_tenancy=lambda t: None) if env.get("MCP_PUBLIC_URL") else None
        monkeypatch.setitem(sys.modules, f"wowstats.{name}", mod)
    return calls


def make(view: str, **env) -> ui.App:
    return ui.App({"VIEW": view, "BIND": "127.0.0.1",
                   "MCP_PUBLIC_URL": "http://127.0.0.1:8080/mcp", **env}, store=PoisonStore())


MCP_PATHS = ["/mcp", "/oauth/authorize", "/oauth/token", "/oauth/register",
             "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
             "/.well-known/oauth-authorization-server"]
PATHS = ["/", "/app.js", "/app.css", "/api/config", "/api/chars", f"/api/char?name={CHAR}",
         f"/api/journal?name={CHAR}", f"/api/dungeons?name={CHAR}", f"/api/gear?name={CHAR}", f"/api/gear?name={CHAR}&spec=holy", "/api/market", "/api/prices", "/api/logsql?q=*", "/api/coach",
         "/full/", "/full", *MCP_PATHS]


def test_public_never_leaks(run_app, fake_extras):
    srv = run_app(make("public"))
    for path in PATHS:
        for method in ("GET", "POST"):
            code, _, body = srv.get(path, method=method, data=b"{}" if method == "POST" else None)
            text = body.decode("utf-8", "replace")
            for s in SECRETS:
                assert s not in text, f"{method} {path} leaks {s!r}"
    assert fake_extras == [], "a public server must never call the MCP"


@pytest.mark.parametrize("path", ["/api/market", "/api/prices", "/api/logsql?q=*", "/api/coach", "/full/",
                                  *MCP_PATHS])
def test_public_full_only_routes_are_404(run_app, fake_extras, path):
    srv = run_app(make("public"))
    for method in ("GET", "POST", "OPTIONS"):
        code, _, _ = srv.get(path, method=method, data=b"{}" if method == "POST" else None)
        assert code == 404, f"{method} {path}"


def test_public_page_has_no_full_sections(run_app):
    srv = run_app(make("public"))
    _, _, body = srv.get("/")
    html = body.decode()
    for marker in ('id="people"', 'id="market"', 'id="prices"', 'id="askbox"', 'claude.ai/new', 'id="debug"',
                   "<!--full-->", "/full/"):
        assert marker not in html
    assert 'name="robots" content="noindex' in html
    code, headers, _ = srv.get("/api/chars")
    assert code == 200 and "noindex" in headers.get("X-Robots-Tag", "")
    _, _, js = srv.get("/app.js")
    assert b"/full/" not in js


def test_public_config_and_payload_shape(run_app):
    srv = run_app(make("public"))
    cfg = json.loads(srv.get("/api/config")[2])
    versions = cfg.pop("data_versions")
    assert cfg == {"view": "public", "static": None, "version": __version__}
    # What the bundled data was built from: public sources' versions, no more.
    assert set(versions) <= {
        "foreverdb", "forever_db", "polestar", "client_build", "wowsims", "checked_addons", "checked",
        "watched_client_build", "spell_hotfix_push", "kb_digest_week", "kb_statement_reviews"} and versions
    d = json.loads(srv.get(f"/api/char?name={CHAR}")[2])
    # The card, the climb and the dungeons; nothing else (redact.PUBLIC).
    assert set(d) == {"char", "generated_at", "stale", "route_source", "header", "levels", "dungeons", "gear",
                      "spec", "talents", "legacy", "class_guide", "coverage"}
    # The coverage state is judged when a bundle is built (publish.static_coverage); the live view has none.
    assert d["coverage"] is None
    for gone in ("money_copper", "zone", "seen", "guild", "spec", "rested_xp", "kills", "quests", "eta_s"):
        assert gone not in d["header"], gone
    assert d["header"]["level"] == 12 and d["header"]["race"] == "Gnome"
    assert all(set(lv) == {"level", "seconds", "partial", "route_s"} for lv in d["levels"])
    assert d["dungeons"] and all(set(r) == {"name", "level", "t"} for r in d["dungeons"])
    # Gear and professions are public (no gold in them); tracked upgrades aren't.
    g = d["gear"]
    assert set(g) == {"item_level", "armory_item_level", "armory_item_level_source", "render", "items", "professions",
                      "items_from", "items_note", "standing"}
    # The doll's badges (owner, 2026-11-23): rank, percent and label per worn slot.
    assert isinstance(g["standing"], list)
    assert all(set(st) == {"slot", "rank", "pct", "label"} for st in g["standing"])
    assert g["items_from"] is None and g["items_note"] is None  # Syndicator's items: Battle.net isn't asked
    # No vm-wow here: the armory's count is ours; no static site, no picture.
    assert g["armory_item_level_source"] == "computed" and g["render"] is None
    assert g["items"][0]["name"] == "Faerie Mantle" and g["items"][0]["quality"] == 2
    # The cap: no Recorder skill record here, so the trainer tier over 130
    # (150); the sheet's 225 came from CharacterExport-Forever, dropped
    # 2026-11-20, and is not read.
    assert g["professions"][0] == {"skill_id": 185, "name": "Cooking", "rank": 130, "max_rank": 150,
                                   "max_rank_from": "tiers"}
    chars = json.loads(srv.get("/api/chars")[2])
    assert chars == {"chars": [{"char": CHAR, "level": 12, "demo": True}]}


@pytest.mark.parametrize("what", ["journal", "gear", "overview", "people"])
def test_full_only_answers_are_404_in_public(run_app, what):
    """Advice, the journal, gear upgrades and people: full view only."""
    srv = run_app(make("public"))
    assert srv.get(f"/api/{what}?name={CHAR}")[0] == 404
    assert run_app(make("full", MCP_PUBLIC_URL="")).get(f"/api/{what}?name={CHAR}")[0] == 200


def test_public_timeline_is_the_level_slider_only(run_app):
    srv = run_app(make("public"))
    t = json.loads(srv.get(f"/api/timeline?name={CHAR}")[2])
    assert set(t) == {"char", "generated_at", "levels"}
    sn = json.loads(srv.get(f"/api/snapshot?name={CHAR}&level=12")[2])
    assert set(sn) <= {"char", "level", "available", "why", "generated_at", "gear", "gear_reconstructed", "picture"}
    # A past level's items say how sure they are, and how they were got only
    # by kind: never the quest, the zone or the creature.
    for it in (sn.get("gear") or {}).get("items") or []:
        assert it["certainty"] in ("exact", "likely")
        assert set(it.get("source") or {}) <= {"kind"}


def test_fonts_are_served_from_the_page_itself(run_app):
    srv = run_app(make("public"))
    code, headers, body = srv.get("/fonts/cinzel.woff2")
    assert code == 200 and headers["Content-Type"] == "font/woff2" and body[:4] == b"wOF2"
    assert "font-src 'self'" in headers["Content-Security-Policy"]
    assert srv.get("/fonts/../ui.py")[0] == 404
    css = srv.get("/app.css")[2].decode()
    assert "url(" in css and "http" not in css.split("@font-face", 1)[-1].split("}")[0]


def test_full_view_shows_everything(run_app, fake_extras):
    """The same poisoned store through a full server: the secrets do come out,
    so the public test above would catch a leak."""
    srv = run_app(make("full"))
    d = srv.get(f"/api/char?name={CHAR}")[2].decode()
    assert SECRET_PLAYER in d and SECRET_MONEY in d
    assert SECRET_PRICE in srv.get("/api/prices")[2].decode()
    assert SECRET_TOKEN in srv.get("/api/market")[2].decode()
    code, headers, _ = srv.get("/full/")
    assert code == 302 and headers["Location"] == "/"
    # /mcp and the OAuth paths live on MCP_PORT (FastMCP), not here.
    assert srv.get("/mcp", method="POST", data=b"{}")[0] == 404
    # No model on the server: the Coach button's route is gone.
    assert srv.get("/api/coach", method="POST", data=b"{}")[0] == 404
    assert fake_extras == []
    assert srv.get("/.well-known/oauth-authorization-server")[0] == 404
    cfg = json.loads(srv.get("/api/config")[2])
    assert cfg["view"] == "full" and "coach" not in cfg and cfg["mcp"]


def test_public_server_never_gets_metrics_url():
    app = ui.App({"VIEW": "public", "BIND": "127.0.0.1", "VM_URL": "http://vm:8428"})
    assert app.store.vm_url == ""


def test_mcp_paths_are_not_on_the_ui_port(run_app, fake_extras):
    """FastMCP serves /mcp and the OAuth paths on its own port (a reverse
    proxy routes them there, docs/mcp.md): on the UI's port they are pages
    like any other, behind the page login, and nothing answers them."""
    srv = run_app(make("full", AUTH="basic", BASIC_AUTH_USER="me", BASIC_AUTH_PASSWORD="a long password"))
    for path in MCP_PATHS:
        assert srv.get(path, method="POST", data=b"{}")[0] == 401, path
    assert srv.get("/api/chars")[0] == 401
    assert fake_extras == []


def test_mcp_off_until_public_url(run_app, fake_extras):
    app = make("full", MCP_PUBLIC_URL="")
    assert app.mcp is None
    srv = run_app(app)
    assert srv.get("/mcp", method="POST", data=b"{}")[0] == 404
    assert srv.get("/.well-known/oauth-authorization-server")[0] == 404
    assert json.loads(srv.get("/api/config")[2])["mcp"] is False
    assert fake_extras == []


def test_bad_mcp_settings_stop_the_server():
    """The real module: a full view with an MCP URL but no identity provider
    refuses to start, instead of failing inside a request."""
    with pytest.raises(ui.ConfigError, match="OIDC_ISSUER"):
        ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": "http://127.0.0.1:8090/mcp"},
               store=PoisonStore())
    with pytest.raises(ui.ConfigError, match="passphrase login went"):
        ui.App({"VIEW": "full", "BIND": "127.0.0.1", "MCP_PUBLIC_URL": "http://127.0.0.1:8090/mcp",
                "MCP_LOGIN": "password"}, store=PoisonStore())


# ------------------------------------------------------------- Settings on the live public view (review LOW-2)

HIDDEN = "Hidden-Test"


class TwoCharStore(PoisonStore):
    """Hero-Test and Hidden-Test, both with events."""

    def vl(self, query, keys=(), ops=None, limit=0):
        rows = super().vl(query, keys, ops, limit)
        return rows + [{**r, "char": HIDDEN} for r in rows]


@pytest.fixture
def users_port(tmp_path):
    """The full view's share port, as far as /users.json goes: the token
    asked on every request, the settings line the test sets."""
    import http.server
    import threading

    token = "t" * 40
    (tmp_path / "share-token").write_text(token)
    line = {"slug": "owner", "account_id": 0, "status": "active", "public_on": True,
            "hidden_public": [HIDDEN], "hidden_zogzog": []}
    state = {"line": line, "up": True}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if not state["up"]:
                self.send_response(500)
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {token}" or self.path != "/users.json":
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"users": [state["line"]]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {"USERS_URL": f"http://127.0.0.1:{srv.server_address[1]}/users.json",
           "ANALYSIS_SHARE_TOKEN_FILE": str(tmp_path / "share-token")}
    yield state, env
    srv.shutdown()
    srv.server_close()


def public_with_settings(env) -> ui.App:
    app = ui.App({"VIEW": "public", "BIND": "127.0.0.1", **env}, store=TwoCharStore())
    app.page_settings.refresh_s = 0  # every request reads the settings again
    return app


def test_live_public_view_hides_what_settings_hide(run_app, users_port):
    """A character hidden from the public page in Settings doesn't exist on
    the live public view either: not listed, its answers 404, not in the
    link preview. The other one still shows."""
    state, env = users_port
    srv = run_app(public_with_settings({**env, "DEFAULT_CHAR": HIDDEN}))
    code, _, body = srv.get("/api/chars")
    assert code == 200 and [c["char"] for c in json.loads(body)["chars"]] == [CHAR]
    for path in (f"/api/char?name={HIDDEN}", f"/api/timeline?name={HIDDEN}",
                 f"/api/snapshot?name={HIDDEN}&level=5", f"/render/{HIDDEN}.webp"):
        assert srv.get(path)[0] == 404, path
    assert srv.get(f"/api/char?name={CHAR}")[0] == 200
    page = srv.get("/")[2].decode()
    assert HIDDEN not in page and "Hidden" not in page
    # Shown again once Settings stop hiding it.
    state["line"] = {**state["line"], "hidden_public": []}
    assert sorted(c["char"] for c in json.loads(srv.get("/api/chars")[2])["chars"]) == sorted([CHAR, HIDDEN])


def test_live_public_view_off_answers_like_the_static_site(run_app, users_port):
    """Public page off: every path is a 404, as the static server answers
    for a site built with it off; /healthz still says the process is up."""
    state, env = users_port
    state["line"] = {**state["line"], "public_on": False}
    srv = run_app(public_with_settings(env))
    for path in ("/", "/api/chars", f"/api/char?name={CHAR}", "/api/config", "/app.js"):
        code, _, body = srv.get(path)
        assert code == 404 and CHAR.encode() not in body, path
    assert srv.get("/healthz")[0] == 200
    state["line"] = {**state["line"], "public_on": True}
    assert srv.get("/api/chars")[0] == 200


def test_live_public_view_fails_closed_without_settings(run_app, users_port):
    """The share port down before the first answer: page off, not every
    character shown; a person missing from the list: off too."""
    state, env = users_port
    state["up"] = False
    srv = run_app(public_with_settings(env))
    assert srv.get("/api/chars")[0] == 404
    state["up"] = True
    assert srv.get("/api/chars")[0] == 200
    state["up"] = False  # down after an answer: the last settings stay
    assert [c["char"] for c in json.loads(srv.get("/api/chars")[2])["chars"]] == [CHAR]
    state["up"] = True
    state["line"] = {**state["line"], "account_id": 7}
    assert srv.get("/api/chars")[0] == 404


def test_live_public_view_without_users_url_shows_everyone(run_app):
    """One person, no Settings: as before."""
    srv = run_app(ui.App({"VIEW": "public", "BIND": "127.0.0.1"}, store=TwoCharStore()))
    assert sorted(c["char"] for c in json.loads(srv.get("/api/chars")[2])["chars"]) == sorted([CHAR, HIDDEN])


# The public doll's BiS badges (owner, 2026-11-23): from the gear advice,
# each worn slot's rank, percent and label, and nothing else of the advice.
ADVICE = {
    "char": CHAR, "available": True, "spec": {"key": "shadow", "label": "Shadow"}, "gear_score": 123.4,
    "upgrades": [{"item_id": 1, "name": "Secret Upgrade", "gain": 9, "sources": [{"kind": "ah", "price_copper": 1}]}],
    "ah": {"available": True, "money_copper": SECRET_MONEY},
    "slots": [
        {"slot": 1, "slot_name": "Head", "standing": {"rank": 1, "pct": 100, "label": "BiS"},
         "current": {"item_id": 10, "name": "Worn Hat", "score": 5.0}, "best": None, "next": None},
        {"slot": 5, "slot_name": "Chest", "standing": {"rank": None, "pct": 62, "label": "62% of BiS", "target": 8.0},
         "current": {"item_id": 11, "name": "Worn Robe", "score": 5.0},
         "best": {"item_id": 2, "name": "Secret Robe", "gain": 3, "bis_pct": 100, "sources": [{"kind": "drop"}]},
         "next": {"item_id": 3, "name": "Secret Later Robe", "level": 20}},
        # Nothing worn: no badge. No BiS to measure: no badge.
        {"slot": 6, "slot_name": "Waist", "standing": None, "current": None,
         "best": {"item_id": 4, "name": "Secret Belt"}, "next": None},
        {"slot": 7, "slot_name": "Legs", "standing": None, "current": {"item_id": 12, "name": "Worn Pants"},
         "best": None, "next": None},
    ],
}


def test_worn_standing_is_the_badge_and_nothing_else():
    from wowstats import redact

    got = redact.worn_standing(ADVICE)
    assert got == [{"slot": 1, "rank": 1, "pct": 100, "label": "BiS"},
                   {"slot": 5, "rank": None, "pct": 62, "label": "62% of BiS"}]
    assert redact.worn_standing(None) == [] and redact.worn_standing({"available": False, "why": "x"}) == []


def test_public_char_carries_the_badges_but_no_advice():
    from wowstats import redact

    card = {"char": CHAR, "gear": {"item_level": 20, "items": [], "standing": [
        {**s["standing"], "slot": s["slot"], "name": "Secret Robe", "best": s["best"], "gain": 3, "target": 8.0}
        for s in ADVICE["slots"] if s["standing"]]}, "upgrades": ADVICE["upgrades"], "slots": ADVICE["slots"]}
    out = redact.public("char", card)
    assert out["gear"]["standing"] == [{"slot": 1, "rank": 1, "pct": 100, "label": "BiS"},
                                       {"slot": 5, "rank": None, "pct": 62, "label": "62% of BiS"}]
    blob = json.dumps(out)
    for leak in ("Secret", "upgrades", "best", "next", "gain", "target", "sources", "bis_pct", SECRET_MONEY):
        assert leak not in blob, leak


def test_live_public_char_has_the_advice_standing(run_app, monkeypatch):
    """The live public view reads the standing from the gear advice; the
    advice answer itself stays a 404."""
    app = make("public")
    monkeypatch.setattr(app.views, "gear", lambda name, spec=None, mode=None, pvp=None: ADVICE)
    srv = run_app(app)
    d = json.loads(srv.get(f"/api/char?name={CHAR}")[2])
    assert d["gear"]["standing"] == [{"slot": 1, "rank": 1, "pct": 100, "label": "BiS"},
                                     {"slot": 5, "rank": None, "pct": 62, "label": "62% of BiS"}]
    assert b"Secret" not in srv.get(f"/api/char?name={CHAR}")[2]
    assert srv.get(f"/api/gear?name={CHAR}")[0] == 404
