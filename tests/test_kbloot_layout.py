"""kbloot finds its lane in the installed layout (wowstats/data/), not only in a
checkout (data/): the image had no checkout and gave no drops, silently."""

import json
import logging

from wowstats.analysis import kb
from wowstats.plan import kbloot

LANE = {"statements": [{"title": "The Drowned City (dungeon)", "text": "Drops: • Boss One: Briny Sash"}]}


def test_lane_is_found_under_the_installed_data_dir(tmp_path, monkeypatch):
    installed = tmp_path / "site-packages" / "wowstats" / "data"
    (installed / "kb" / "research").mkdir(parents=True)
    (installed / "kb" / "research" / "dungeons.json").write_text(json.dumps(LANE))
    monkeypatch.setattr(kb, "DATA_DIRS", (installed, tmp_path / "site-packages" / "data"))
    kbloot._statements.cache_clear()
    try:
        assert kbloot._statements() == tuple(LANE["statements"])
    finally:
        kbloot._statements.cache_clear()


def test_a_missing_lane_warns(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(kb, "DATA_DIRS", (tmp_path,))
    kbloot._statements.cache_clear()
    try:
        with caplog.at_level(logging.WARNING, logger=kbloot.log.name):
            assert kbloot._statements() == ()
        assert "dungeon drops will be empty" in caplog.text
    finally:
        kbloot._statements.cache_clear()
