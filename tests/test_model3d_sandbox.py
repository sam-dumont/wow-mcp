"""Wowhead's viewer is a third party's script fetched from its CDN: it runs
boxed in (pre-publication review M2, 2026-11-19).

Before: /model3d/view was a same-origin frame with `script-src 'self'` and
`connect-src 'self'`, so a hostile viewer build could call /api/* with the
visitor's login, read the page around it, and in /zogzog/ read the key from
its own fragment. Now the frame is sandboxed (an opaque origin, no network),
its files come from the page around it through an allow-list, and only one
reviewed build of the viewer is relayed. The browser half of this (the
attacks from inside the frame, Chromium and WebKit) is
tests/test_model3d_sandbox_browser.py."""

import base64
import hashlib
import re

import pytest

from tests.test_model3d import Upstream
from tests.test_public_view import (
    CHAR,
    fake_extras,  # noqa: F401 - a fixture
    make,
)
from wowstats import model3d, ui

VIEW = ui.static_dir() / "model3d"


@pytest.fixture
def upstream():
    u = Upstream()
    yield u
    u.srv.shutdown()
    u.srv.server_close()


def directives(csp: str) -> dict[str, str]:
    return {d.split()[0]: d.split(" ", 1)[1] if " " in d else "" for d in (x.strip() for x in csp.split(";")) if d}


@pytest.mark.usefixtures("fake_extras")
def test_the_view_is_a_sandbox_with_no_network(run_app, upstream, tmp_path):
    srv = run_app(make("full", **upstream.env(tmp_path)))
    code, headers, body = srv.get("/model3d/view?name=" + CHAR)
    d = directives(headers["Content-Security-Policy"])
    assert code == 200
    assert d["sandbox"] == "allow-scripts", "an opaque origin: no cookies, storage or same-origin reads"
    assert d["connect-src"] == "'none'" and d["frame-ancestors"] == "'self'"
    assert "'self'" not in d["script-src"] and "'self'" not in d["img-src"]
    # Nothing by URL: every script and style is inline and allowed by its hash.
    html = body.decode()
    assert not re.search(r"<(script|link|img)[^>]+(src|href)=", html), "a sandboxed frame loads nothing by URL"
    for tag, directive in (("script", "script-src"), ("style", "style-src")):
        blocks = re.findall(rf"<{tag}>(.*?)</{tag}>", html, re.S)
        assert blocks, tag
        for text in blocks:
            h = "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"
            assert h in d[directive], tag
    # The same policy travels with the page, for hosts that send no headers.
    meta = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html).group(1)
    assert directives(meta)["connect-src"] == "'none'"


def test_the_page_frames_it_sandboxed_and_keeps_the_key():
    js = (VIEW / "model3d.js").read_text()
    frames = re.findall(r"h\('iframe', \{[^}]*\}", js)
    assert frames, "model3d.js makes the 3D frame"
    for f in frames:
        assert "sandbox: 'allow-scripts'" in f and "allow-same-origin" not in f, f
    # The /zogzog/ key never goes into the frame's address.
    q = js[js.index("function frameQuery"):js.index("function frameEl")]
    assert "wowKey" not in q and "#" not in q
    # It answers only from our own frames, only while they hold the sandboxed page.
    assert "ev.origin !== 'null'" in js and "x.contentWindow === ev.source" in js
    assert "wowRelayPath(d.path)" in js


def test_the_renderer_copy_reads_its_own_files():
    """The renderer loads the page on its own in a headless browser that
    holds nothing: no sandbox, its own server only."""
    body = model3d.view_page(VIEW, render=True)
    d = directives(model3d.view_csp(body, render=True))
    assert d["connect-src"] == "'self'" and "sandbox" not in d and "frame-ancestors" not in d


def test_an_old_bundle_keeps_the_old_policy():
    old = b"<!doctype html><script src='view.js'></script>"
    assert model3d.view_csp(old) == model3d.CSP_VIEW_LEGACY


def test_only_the_reviewed_viewer_is_relayed(upstream, tmp_path, capsys):
    good = upstream.files["/modelviewer/live/viewer/viewer.min.js"]
    pin = hashlib.sha256(good).hexdigest()
    relay = model3d.Relay(upstream.base + "/modelviewer/", tmp_path / "c", 64, pin)
    assert relay.get("viewer/viewer.min.js") == good
    # Wowhead ships something else: refused, the last good copy stays.
    upstream.files["/modelviewer/live/viewer/viewer.min.js"] = b"fetch('/api/me')"
    for f in (tmp_path / "c").rglob("viewer.min.js"):
        f.touch()
        import os
        os.utime(f, (0, 0))  # a day old: due for a fresh fetch
    assert relay.get("viewer/viewer.min.js") == good
    assert "viewer changed" in capsys.readouterr().out
    # With no good copy at all: an error, never the new script.
    fresh = model3d.Relay(upstream.base + "/modelviewer/", tmp_path / "d", 64, pin)
    with pytest.raises(model3d.Upstream, match="waiting for a review"):
        fresh.get("viewer/viewer.min.js")
    # The default pin is the build reviewed on 2026-11-19, not whatever is live.
    assert model3d.Relay(upstream.base, None, 1).viewer_sha256 == model3d.VIEWER_SHA256


def test_a_changed_copy_on_disk_is_not_served(upstream, tmp_path):
    pin = hashlib.sha256(upstream.files["/modelviewer/live/viewer/viewer.min.js"]).hexdigest()
    relay = model3d.Relay(upstream.base + "/modelviewer/", tmp_path / "c", 64, pin)
    relay.get("viewer/viewer.min.js")
    for f in (tmp_path / "c").rglob("viewer.min.js"):
        f.write_bytes(b"alert(1)")
    upstream.files["/modelviewer/live/viewer/viewer.min.js"] = b"alert(2)"
    with pytest.raises(model3d.Upstream):
        relay.get("viewer/viewer.min.js")


def test_the_relay_remembers_a_bounded_number_of_misses(upstream, tmp_path):
    """Review, info 2: a miss and a lock per path asked for, never freed."""
    relay = model3d.Relay(upstream.base + "/modelviewer/", None, 1)
    for i in range(relay.MAX_MISSING + 500):
        relay._miss(f"m2/{i}.m2")
    assert len(relay.missing) <= relay.MAX_MISSING
    assert len(relay.locks) == relay.LOCKS
    assert relay.get("m2/999999.m2") is None and len(relay.locks) == relay.LOCKS
