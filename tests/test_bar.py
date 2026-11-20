"""Every HTML page carries the same header (wowstats.bar) and loads the same
stylesheet (web/base.css): the full view's pages for each kind of reader,
and the static site's (the public page, /zogzog/, the help page, the empty
page and the 404). The header's home link leads to a page that answers.

Owner, 2026-11-20: "each page has a different header, like go on privacy,
you get no link back; also each page has its own styling, it should be
coherent across the app"."""

from __future__ import annotations

import urllib.parse
from html.parser import HTMLParser

import pytest

from tests import test_tenancy
from tests.test_tenancy import OWNER, as_user, friend_ready
from wowstats import bar, notice, publish, static

vl, env, server = test_tenancy.vl, test_tenancy.env, test_tenancy.server  # the multi-user server's fixtures

HTML = {"Accept": "text/html"}


class Page(HTMLParser):
    def __init__(self, body: bytes):
        super().__init__()
        self.bars, self.homes, self.navs, self.sheets, self.scripts, self.icons, self.in_bar = 0, [], [], [], [], [], 0
        self.feed(body.decode())

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "header" and "bar" in cls:
            self.bars += 1
            self.in_bar = 1
        elif tag == "a" and "bar-home" in cls and self.in_bar:
            self.homes.append(a.get("href"))
        elif tag == "nav" and "bar-nav" in cls and self.in_bar:
            self.navs.append(a)
        elif tag == "link" and a.get("rel") == "stylesheet":
            self.sheets.append(a["href"])
        elif tag == "link" and a.get("rel") == "icon":
            self.icons.append(a["href"])
        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])

    def handle_endtag(self, tag):
        if tag == "header":
            self.in_bar = 0


def shared(p: Page, path: str) -> tuple[str, str, str]:
    """The header once, with its home link; base.css the first stylesheet;
    one tab icon. Returns the three, resolved against the page's path."""
    assert p.bars == 1, (path, p.bars)
    assert len(p.homes) == 1 and p.homes[0], (path, p.homes)
    assert p.sheets and p.sheets[0].endswith("base.css"), (path, p.sheets)
    assert len(p.icons) == 1 and p.icons[0].endswith("favicon.svg"), (path, p.icons)
    return tuple(urllib.parse.urljoin(path, x) for x in (p.homes[0], p.sheets[0], p.icons[0]))


# ---------------------------------------------------------------- the full view

def readers(server):
    friend = friend_ready(server)
    return {
        "friend": {**friend, **HTML},
        "owner": {**as_user(OWNER), **HTML},
        "refused": {**as_user("someone"), **HTML},
        "logged out": dict(HTML),
    }


# (reader, path, status): every page the full view renders as HTML.
FULL = [
    ("friend", "/", 200), ("friend", "/start", 200), ("friend", "/start/print", 200),
    ("friend", "/settings", 200), ("friend", "/help/", 200), ("friend", "/privacy", 200),
    ("friend", "/no-such-page", 404), ("owner", "/admin", 200), ("owner", "/", 200),
    ("refused", "/", 403), ("refused", "/privacy", 200), ("logged out", "/privacy", 200),
]


def test_every_full_view_page_has_the_bar_and_the_stylesheet(server):
    who = readers(server)
    for reader, path, status in FULL:
        h = who[reader]
        code, hdr, body = server.get(path, headers=h)
        assert code == status and hdr["Content-Type"].startswith("text/html"), (reader, path, code)
        home, css, icon = shared(Page(body), path)
        assert home == "/", (reader, path, home)
        # The icon answers without a login (a login proxy lets /privacy/ through).
        code, hdr, _ = server.get(icon)
        assert icon == bar.ICON and code == 200 and hdr["Content-Type"] == "image/svg+xml", (reader, path, icon)
        code, hdr, _ = server.get(css, headers=h)
        assert code == 200 and hdr["Content-Type"].startswith("text/css"), (reader, path, css, code)
        for src in Page(body).scripts:
            assert server.get(urllib.parse.urljoin(path, src), headers=h)[0] == 200, (reader, path, src)
        # Home answers: the reader's page, or for those the login hasn't let
        # in, the same way in (the refusal, which says what to do).
        code, _, page = server.get(home, headers=h)
        assert Page(page).bars == 1 and (code == 200 or reader in ("refused", "logged out")), (reader, path, code)


def test_the_icon_needs_no_login(server):
    """Browsers ask for /favicon.ico by themselves: a 401 there was noise on
    every page (2026-11-20)."""
    for path in ("/favicon.ico", "/favicon.svg", bar.ICON):
        code, hdr, body = server.get(path)
        assert code == 200 and hdr["Content-Type"] == "image/svg+xml" and body.startswith(b"<svg"), path


def test_the_readers_pages_are_in_every_logged_in_bar(server):
    h = readers(server)["friend"]
    for path in ("/", "/start", "/settings", "/help/", "/privacy", "/start/print", "/no-such-page"):
        p = Page(server.get(path, headers=h)[2])
        assert len(p.navs) == 1, path
    body = server.get("/privacy", headers=HTML)[2].decode()
    assert 'href="/settings"' not in body and 'href="/help/"' not in body  # logged out: the way in, and the note


# ---------------------------------------------------------------- the static site

@pytest.fixture
def bundles(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from tests.mcp.test_helppage import URL, publish_with

    kf = tmp_path / "help.key"
    publish.new_key(str(kf))
    publish_with(tmp_path, monkeypatch, HELP_KEY_FILE=str(kf), HELP_MCP_URL=URL)
    return static.Site({"SITE_DIR": str(tmp_path / "site"), "ZOGZOG_PATH": "/zogzog/"})


def test_every_static_page_has_the_bar_and_nothing_behind_the_login(bundles):
    for path in ("/", "/zogzog/", "/help/"):
        f, _ = bundles.resolve(path)
        p = Page(f.read_bytes())
        home, css, icon = shared(p, path)
        # Home is the bundle's own page; no link to the full view's pages.
        assert bundles.resolve(home) is not None and not p.navs, (path, home)
        for ref in [css, icon, *(urllib.parse.urljoin(path, s) for s in p.scripts)]:
            assert bundles.resolve(ref) is not None, (path, ref)
    assert "help/" not in bundles.resolve("/")[0].read_text()


def test_the_empty_page_and_the_404_have_the_bar(tmp_path):
    b = publish.Bundle(tmp_path / "b")
    notice.write_empty(b)
    p = Page(notice.empty_page())
    home, css, icon = shared(p, "/")
    assert home == "/" and not p.scripts
    for ref in (css, icon):
        assert (tmp_path / "b" / ref.lstrip("/")).is_file(), ref
    for rel in bar.FILES:
        assert (tmp_path / "b" / rel).is_file(), rel
    p = Page(notice.not_found_page())
    home, css, icon = shared(p, "/anything")
    assert home == "/" and css == notice.ASSETS + "base.css" and icon == notice.ASSETS + "favicon.svg"
    assert notice.asset("base.css") and notice.asset("favicon.svg")
