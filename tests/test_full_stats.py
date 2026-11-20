"""Plausible on the live full view (owner, 2026-11-20): every HTML page of
the full view sends one pageview with its path alone, into the same
Plausible site as the static pages (PLAUSIBLE_DOMAIN), and nothing at all
when PLAUSIBLE_DOMAIN is unset. The browser side (Plausible's own script on
the full view) is tests/test_full_stats_browser.py."""

import json
import re

import pytest

from tests.test_public_view import fake_extras, make  # noqa: F401 - fake_extras is a fixture
from tests.test_tenancy import OWNER, as_user, env, friend_ready, server, vl  # noqa: F401 - fixtures
from wowstats import ui

DOMAIN = "wow.m.example"
TAGS = re.compile(rb'<script src="([^"]+)" data-path="([^"]*)" defer></script>\n'
                  rb'<script src="/js/script.js" data-domain="([^"]+)" data-api="/api/event" defer></script>\n'
                  rb"</head>")
HTML = {"Accept": "text/html", "User-Agent": "Mozilla/5.0"}


@pytest.fixture
def counted(env, run_app):  # noqa: F811 - the tenancy fixture, with Plausible on
    env = {**env, "PLAUSIBLE_DOMAIN": DOMAIN}
    app = ui.App(env)
    t = ui.tenancy_of(env, app)
    r = run_app(app)
    r.srv.RequestHandlerClass = ui.make_handler(app, t)
    return r


def pageview(body: bytes) -> tuple[str, str]:
    """(path, domain) of the page's one pageview."""
    found = TAGS.findall(body)
    assert len(found) == 1, body[:400]
    src, path, domain = found[0]
    assert src == ui.STATS_JS.encode()
    assert body.count(b"stats.js") == 1 and body.count(b"data-domain") == 1
    return path.decode(), domain.decode()


def test_every_full_view_page_counts_with_its_path(counted):
    h = friend_ready(counted)
    for path in ("/", "/start", "/start/print", "/settings", "/help/", "/privacy"):
        code, hdr, body = counted.get(path, headers=h)
        assert code == 200 and hdr["Content-Type"].startswith("text/html"), path
        assert pageview(body) == (path, DOMAIN), path
    # The query names the character and the tab: never in the pageview.
    assert pageview(counted.get("/?char=Hero-Test&tab=gear", headers=h)[2]) == ("/", DOMAIN)
    # The styled 404 counts too, under the path asked for.
    code, _, body = counted.get("/nope", headers={**h, **HTML})
    assert code == 404 and pageview(body) == ("/nope", DOMAIN)


def test_the_owner_pages_count(counted):
    code, _, body = counted.get("/admin", headers=as_user(OWNER))
    assert code == 200 and pageview(body) == ("/admin", DOMAIN)


def test_logged_out_pages_count_and_load_the_script_without_the_login(counted):
    """The privacy note and the refusal page are read before (or without) a
    login: what they load must be reachable there (the lab opens /privacy
    and /privacy/* only)."""
    assert pageview(counted.get("/privacy")[2]) == ("/privacy", DOMAIN)
    code, hdr, js = counted.get(ui.STATS_JS)
    assert code == 200 and hdr["Content-Type"].startswith("text/javascript")
    assert js == (ui.static_dir() / "stats.js").read_bytes()
    # A login the app doesn't take: the refusal page counts too.
    code, _, body = counted.get("/", headers={**as_user("stranger"), **HTML})
    assert code == 403 and pageview(body) == ("/", DOMAIN)


def test_an_install_code_never_reaches_plausible(counted):
    code, _, body = counted.get("/install/abcdefghij", headers=HTML)
    if b"</head>" in body:
        assert pageview(body)[0] == "/install/code"
    assert b'data-path="/install/abcdefghij"' not in body
    assert ui.stats_tags({"domain": DOMAIN, "script": "/js/script.js", "api": "/api/event"}, ui.STATS_JS,
                         "/install/abcdefghij/x").count("abcdefghij") == 0


def test_the_path_is_escaped():
    tags = ui.stats_tags({"domain": DOMAIN, "script": "/js/script.js", "api": "/api/event"}, ui.STATS_JS,
                         '/a"><script>x</script>')
    assert "<script>x" not in tags and '"><' not in tags and 'data-path="/a%22%3E%3Cscript%3Ex%3C/script%3E"' in tags


def test_answers_that_are_not_pages_carry_nothing(counted):
    h = friend_ready(counted)
    for path in ("/api/me", "/api/config", "/app.js", "/app.css", "/privacy/account.css"):
        code, _, body = counted.get(path, headers=h)
        assert code == 200 and b"data-domain" not in body and b"stats.js\" data-path" not in body, path


def test_nothing_without_plausible_domain(server):  # noqa: F811 - the tenancy fixture, Plausible unset
    h = friend_ready(server)
    for path in ("/", "/start", "/settings", "/help/", "/privacy"):
        body = server.get(path, headers=h)[2]
        assert b"stats.js" not in body and b"data-domain" not in body and b"/js/script.js" not in body, path
    body = server.get("/nope", headers={**h, **HTML})[2]
    assert b"data-domain" not in body
    assert server.get(ui.STATS_JS)[0] != 200


def test_a_bad_domain_stops_the_full_view_at_start():
    with pytest.raises(SystemExit):
        make("full", PLAUSIBLE_DOMAIN='wow.example" onload="x')
    with pytest.raises(SystemExit):
        make("full", PLAUSIBLE_DOMAIN=DOMAIN, PLAUSIBLE_SCRIPT="https://plausible.io/js/script.js")


@pytest.mark.usefixtures("fake_extras")
def test_the_3d_frame_never_counts(run_app, tmp_path):
    """/model3d/view is a frame inside the page, which already counted the
    visit: a second pageview would count it twice."""
    srv = run_app(make("full", PLAUSIBLE_DOMAIN=DOMAIN, MODEL3D_CACHE_DIR=str(tmp_path / "cache")))
    assert pageview(srv.get("/")[2]) == ("/", DOMAIN)
    code, _, body = srv.get("/model3d/view")
    assert code == 200 and b"data-domain" not in body and b"stats.js" not in body
    assert json.loads(srv.get("/api/config")[2])  # the API is still plain JSON


def test_the_public_live_view_never_counts(run_app):
    """The live public view (VIEW=public) stays as it was: no analytics."""
    srv = run_app(make("public", PLAUSIBLE_DOMAIN=DOMAIN, AUTH="open"))
    body = srv.get("/")[2]
    assert b"data-domain" not in body and b"stats.js" not in body
