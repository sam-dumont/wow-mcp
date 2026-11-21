"""One friend can't grow the shared process without limit (review 0.6.0, S5),
and a proxy can't put a no-login full view on the internet (S4)."""

import pytest

from tests.test_public_view import CHAR, fake_extras, make  # noqa: F401 - fixtures and helpers
from wowstats import guard, plan, ui
from wowstats.ui import ConfigError

# ---------------------------------------------------------------- S5


def test_model3d_refuses_a_free_form_sex(run_app, tmp_path, monkeypatch, fake_extras):  # noqa: F811
    app = make("full", MODEL3D_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setattr(app, "known", lambda name: True)
    srv = run_app(app)
    before = len(app._cache)
    for sex in ("x" * 2000, "Male", "other"):
        st, _, _ = srv.get(f"/api/model3d?name={CHAR}&sex={sex}")
        assert st == 400
    assert len(app._cache) == before and not any("model3d" in k for k in app._cache)


def test_plan_get_checks_the_character(run_app, monkeypatch):
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "none", "VIEW": "full"})
    monkeypatch.setattr(app, "known", lambda name: name == CHAR)
    asked = []
    monkeypatch.setattr(app, "plan", lambda name: asked.append(name) or {"available": False})
    srv = run_app(app)
    assert srv.get("/api/plan?char=Nobody-Here")[0] == 404
    assert srv.get("/api/plan?char=" + "%22" * 3)[0] == 400
    assert srv.get(f"/api/plan?char={CHAR}")[0] == 200
    assert asked == [CHAR]


def test_the_lock_maps_stay_bounded(monkeypatch):
    app = ui.App({"BIND": "127.0.0.1", "AUTH": "none", "VIEW": "full"})
    for i in range(500):
        app.cached(f"plan:name{i}", lambda: {})
    assert app._locks == {}
    # A build that asks for another key (api_char asks for the roster) must not wait on itself.
    assert app.cached("a", lambda: app.cached("b", lambda: {"x": 1}))["x"] == 1

    from wowstats.plan import facts

    monkeypatch.setattr(facts, "gather", lambda env, char, store=None, battlenet=None: {})
    plan._CACHE.clear()
    k = len(plan._KEY_LOCKS)
    for i in range(500):
        plan.inputs({"DEFAULT_CHAR": "x"}, f"name{i}")
    assert len(plan._KEY_LOCKS) == k
    assert len(plan._CACHE) <= plan.MAX_CACHED
    plan._CACHE.clear()


# ---------------------------------------------------------------- S4

PROXY_HEADERS = guard.PROXY_HEADERS
BASIC = {"AUTH": "basic", "BASIC_AUTH_USER": "me", "BASIC_AUTH_PASSWORD": "a-long-enough-password"}


@pytest.mark.parametrize("header", PROXY_HEADERS)
def test_a_proxied_request_is_refused_without_a_login(run_app, header):
    srv = run_app(ui.App({"BIND": "127.0.0.1", "AUTH": "open", "VIEW": "full"}))
    assert srv.get("/api/config")[0] == 200
    st, _, body = srv.get("/api/config", headers={header: "203.0.113.9"})
    assert st == 403 and b"AUTH=basic" in body
    assert srv.get("/healthz", headers={header: "x"})[0] == 200


@pytest.mark.parametrize("header", PROXY_HEADERS)
def test_a_proxied_request_is_fine_with_a_login(run_app, header):
    import base64

    srv = run_app(ui.App({"BIND": "127.0.0.1", "VIEW": "full", **BASIC}))
    auth = {"Authorization": "Basic " + base64.b64encode(b"me:a-long-enough-password").decode()}
    assert srv.get("/api/config", headers={**auth, header: "203.0.113.9"})[0] == 200


def test_the_public_view_may_sit_behind_a_proxy(run_app):
    srv = run_app(ui.App({"BIND": "127.0.0.1", "AUTH": "open", "VIEW": "public", "ALLOWED_HOSTS": "wow.example.org"}))
    assert srv.get("/api/config", headers={"X-Forwarded-For": "203.0.113.9"})[0] == 200


def test_open_with_allowed_hosts_refuses_to_start():
    with pytest.raises(ConfigError, match="ALLOWED_HOSTS"):
        ui.App({"BIND": "127.0.0.1", "AUTH": "open", "VIEW": "full", "ALLOWED_HOSTS": "wow.example.org"})
    assert ui.App({"BIND": "127.0.0.1", "AUTH": "basic", "VIEW": "full", "ALLOWED_HOSTS": "wow.example.org",
                   **{k: v for k, v in BASIC.items() if k != "AUTH"}}).auth_mode == "basic"


def test_the_421_page_says_to_set_a_login():
    assert b"set a login" in guard.Hosts({}).refusal("evil.example")
