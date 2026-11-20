"""WOW_ITEMS=off (the compose kit's small preset): the knowledge base asks
Wago for nothing, like the item and talent catalogs."""

from wowstats.analysis import kb
from wowstats.upstream import Upstream


def test_items_off_means_no_game_table_download(monkeypatch, tmp_path):
    def no_network(*a, **k):
        raise AssertionError("fetched from Wago with WOW_ITEMS=off")

    monkeypatch.setattr("wowstats.upstream.fetch", no_network)
    k = kb.build({"WOW_ITEMS": "off", "MODEL3D_CACHE_DIR": str(tmp_path)})
    assert k.meta["game_build"] is None and list(tmp_path.iterdir()) == []


def test_upstream_down_is_not_fatal(monkeypatch, tmp_path):
    def down(*a, **k):
        raise Upstream("no route")

    monkeypatch.setattr("wowstats.upstream.fetch", down)
    assert kb.build({"MODEL3D_CACHE_DIR": str(tmp_path)}).meta["game_build"] is None
