"""/help/ on the full view: the help page for whoever the login let in,
the same content as the static site's sealed copy, with no key; Help in
every logged-in page's nav; and a 404 page a person can leave."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from tests import test_tenancy
from tests.test_tenancy import OWNER, as_user, friend_ready
from wowstats import account
from wowstats.mcp import helppage

vl, env, server = test_tenancy.vl, test_tenancy.env, test_tenancy.server  # the multi-user server's fixtures

BASE = "https://wow-full.m.example"  # the fixture's PUBLIC_URL
HTML = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.jumps, self.hrefs = set(), [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
        if a.get("data-jump"):
            self.jumps.append(a["data-jump"])
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])


def parsed(body: bytes) -> _Ids:
    p = _Ids()
    p.feed(body.decode())
    return p


def test_logged_in_gets_the_help_page_with_its_content(server):
    h = friend_ready(server)
    code, hdr, body = server.get("/help/", headers=h)
    assert code == 200 and hdr["Content-Type"].startswith("text/html")
    # One source: the content is the static copy's render, here unsealed,
    # showing the full view's own MCP address.
    content = helppage.render(f"{BASE}/mcp", here=True)
    assert content.encode() in body
    assert f"{BASE}/mcp".encode() in body and b"data-media-base=\"/start/media/\"" in body
    assert b"Opening\xe2\x80\xa6" not in body and b"content.enc" not in body
    # The reader's own pages stay in the tab; the static copy opens them on the full view.
    p = parsed(body)
    assert "/start" in p.hrefs and "/settings" in p.hrefs and "/" in p.hrefs
    assert not any(x.startswith(BASE + "/start") for x in p.hrefs)
    static = helppage.render(f"{BASE}/mcp")
    assert f"href='{BASE}/start' target='_blank'" in static
    # Its script and stylesheet come from the same place.
    for path, ctype in (("/help/help.js", "text/javascript"), ("/help/help.css", "text/css"),
                        ("/base.css", "text/css"), ("/theme.js", "text/javascript")):
        code, hdr, asset = server.get(path, headers=h)
        assert code == 200 and hdr["Content-Type"].startswith(ctype), path
    assert b"mediaBase" in server.get("/help/help.js", headers=h)[2]
    # The owner too.
    assert server.get("/help/", headers=as_user(OWNER))[0] == 200


def test_help_without_the_slash_redirects(server):
    h = friend_ready(server)
    code, hdr, _ = server.get("/help", headers=h)
    assert code in (301, 302, 308) and hdr["Location"] == "/help/"


@pytest.mark.parametrize("who", [{}, as_user("someone")])
def test_logged_out_or_not_admitted_is_refused_like_other_pages(server, who):
    for path in ("/help/", "/help", "/help/help.js"):
        code, hdr, body = server.get(path, headers=who)
        ref, _, _ = server.get("/start", headers=who)
        assert code == ref and code in (401, 403), (path, code)
        assert b"Your AI" not in body and b"/mcp" not in body


def test_every_anchor_resolves(server):
    """The header's jumps, the anchors other pages can link to (#start,
    #ai, #trouble, a question like #tr-no-data, a fold like #x-update)."""
    h = friend_ready(server)
    p = parsed(server.get("/help/", headers=h)[2])
    assert p.jumps and set(p.jumps) <= p.ids
    assert {"start", "page", "ai", "trouble", "else", "tr-no-data", "x-update", "x-uninstall"} <= p.ids
    in_page = [x[1:] for x in p.hrefs if x.startswith("#")]
    assert in_page and set(in_page) <= p.ids


def test_help_is_in_every_logged_in_pages_nav(server):
    h = friend_ready(server)
    for path in ("/", "/start", "/settings", "/privacy", "/help/"):
        code, _, body = server.get(path, headers=h)
        assert code == 200, path
        assert re.search(rb'<nav[^>]*>.*?<a href="/help/"[^>]*>Help</a>', body, re.S), path
    # The help page marks itself; the others mark theirs.
    assert b'<a href="/help/" aria-current="page">Help</a>' in server.get("/help/", headers=h)[2]
    assert b'<a href="/start" aria-current="page">Start</a>' in server.get("/start", headers=h)[2]
    # Logged out, Privacy stays the only link (Help needs the login).
    body = server.get("/privacy")[2]
    assert b'href="/help/"' not in body


def test_unknown_page_is_a_styled_404_with_the_way_back(server):
    h = friend_ready(server)
    code, hdr, body = server.get("/nope", headers={**h, **HTML})
    assert code == 404 and hdr["Content-Type"].startswith("text/html")
    p = parsed(body)
    assert {"/", "/start", "/help/"} <= set(p.hrefs)
    assert b'href="/privacy/account.css"' in body
    assert re.findall(rb'<script src="([^"]+)"', body) == [b"/privacy/theme.js"]
    # Scripts, the API and the MCP clients keep the plain answer.
    assert server.get("/nope", headers=h)[:1] == (404,)
    assert server.get("/nope", headers=h)[2] == b"not found\n"
    code, hdr, body = server.get("/api/nope", headers={**h, **HTML})
    assert code == 404 and not hdr["Content-Type"].startswith("text/html")


def test_the_404_page_loads_nothing_behind_the_login():
    body = account.not_found_page().decode()
    refs = re.findall(r'(?:src|href)="(/[^"]*)"', body)
    loads = [r for r in refs if r.endswith((".css", ".js", ".woff2"))]
    assert loads and all(r.startswith("/privacy/") for r in loads)
