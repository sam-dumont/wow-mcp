"""What a request or an upstream answer can put in an outbound URL (CodeQL
#42, py/partial-ssrf). An icon name comes from the request path, a build from
wago.tools' own JSON: each is checked against a strict shape before it is
quoted into a URL, and fetch() reaches only the allowed origins."""

from __future__ import annotations

import json

import pytest

from wowstats import itemcard, talents, upstream
from wowstats.analysis import kb

HOSTILE = ["../x", "..%2fx", "%2e%2e", "a?x=1", "a#frag", "a@evil.example", "a/b", "a\\b", "x" * 500, "épée",
           "inv\n", "", " inv", "INV_UPPER", "a:80", "a%00"]


def no_request(*a, **kw):
    raise AssertionError(f"a request was made: {a}")


@pytest.mark.parametrize("bad", HOSTILE)
def test_a_hostile_icon_name_never_reaches_a_url(bad):
    with pytest.raises(ValueError):
        upstream.icon_url("https://wow.zamimg.com/images/wow/icons/", "large", bad)


@pytest.mark.parametrize("bad", HOSTILE + ["1.2.3", "1.60.1.71037?x=1", "1.60.1.71037#", "1.60.1.71037/..",
                                           "١.٦٠.١.٧١٠٣٧"])
def test_a_hostile_build_never_reaches_a_url(bad):
    with pytest.raises(ValueError):
        upstream.table_url("https://wago.tools", "ItemSparse", bad)
    with pytest.raises(ValueError):
        upstream.build_of(json.dumps({"version": bad}).encode())


@pytest.mark.parametrize("bad", HOSTILE + ["ItemSparse/../x", "Secret"])
def test_only_listed_tables_and_products(bad):
    with pytest.raises(ValueError):
        upstream.table_url("https://wago.tools", bad, "1.60.1.71037")
    with pytest.raises(ValueError):
        upstream.builds_url("https://wago.tools", bad)


def test_good_values_build_the_same_urls_as_before():
    assert upstream.builds_url("https://wago.tools", "wowf") == "https://wago.tools/api/builds/wowf/latest"
    assert (upstream.table_url("https://wago.tools", "ItemSparse", "1.60.1.71037")
            == "https://wago.tools/db2/ItemSparse/csv?build=1.60.1.71037")
    assert (upstream.icon_url("https://wow.zamimg.com/images/wow/icons/", "large", "inv_sword_39")
            == "https://wow.zamimg.com/images/wow/icons/large/inv_sword_39.jpg")
    # Real Forever icon names carry hyphens (193 of 30,319 on 2026-11-22).
    assert upstream.icon_url("https://wow.zamimg.com/images/wow/icons", "medium",
                             "ui-hud-actionbar-keyring").endswith("/medium/ui-hud-actionbar-keyring.jpg")
    assert upstream.build_of(b'{"version": "1.60.1.71037"}') == "1.60.1.71037"


def test_every_table_the_server_reads_is_listed():
    assert set(talents.TABLES) | set(itemcard.TABLES) | set(kb.GAME_TABLES) <= upstream.WAGO_TABLES


@pytest.mark.parametrize("bad", HOSTILE)
def test_the_item_icon_route_refuses_before_any_request(bad, tmp_path, monkeypatch):
    monkeypatch.setattr(upstream, "fetch", no_request)
    cat = itemcard.Catalog("https://wago.tools", "wowf", tmp_path)
    assert cat.icon(bad, "large") is None


@pytest.mark.parametrize("bad", HOSTILE)
def test_talent_icons_refuse_before_any_request(bad, monkeypatch):
    monkeypatch.setattr(upstream, "fetch", no_request)
    assert talents.icon_bytes(bad, "medium") is None


def test_a_hostile_build_from_wago_stops_before_the_tables(tmp_path, monkeypatch):
    """wago.tools' answer is data too: a version that isn't a build is not
    quoted into the next URL."""
    asked = []

    def wago(url, timeout=30):
        asked.append(url)
        return json.dumps({"version": "1.60.1.71037?x=../../admin"}).encode()

    monkeypatch.setattr(upstream, "fetch", wago)
    cat = itemcard.Catalog("https://wago.tools", "wowf", tmp_path)
    assert cat.card(19019) is None
    assert asked == ["https://wago.tools/api/builds/wowf/latest"]


@pytest.mark.parametrize("url", ["https://evil.example/x", "http://wago.tools/api", "https://wago.tools.evil.example/",
                                 "https://user@wago.tools/x", "https://wago.tools:8443/x", "file:///etc/passwd",
                                 "ftp://wago.tools/x"])
def test_fetch_reaches_only_the_allowed_origins(url, monkeypatch):
    monkeypatch.setattr(upstream, "urlopen", no_request)
    with pytest.raises(upstream.Upstream, match="not an allowed upstream"):
        upstream.fetch(url)


def test_allow_adds_one_origin(monkeypatch):
    monkeypatch.setattr(upstream, "ALLOWED", set(upstream.ALLOWED))
    assert not upstream.allowed("http://127.0.0.1:9001/x")
    upstream.allow("http://127.0.0.1:9001")
    assert upstream.allowed("http://127.0.0.1:9001/db2/x")
    assert not upstream.allowed("http://127.0.0.1:9002/db2/x"), "the port is part of the origin"
    assert upstream.allowed("https://wago.tools/api") and upstream.allowed("https://wow.zamimg.com/images/x.jpg")
