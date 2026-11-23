"""Every outbound link wow-stats writes, offline: the Wowhead builders give
the Forever form, a dungeon links Wowhead's guide when one was checked and
its zone page when not, and the fixed lists (dungeon guides, addon pages, the
Ask links) are the ones checked by hand on 2026-11-19 (each fetched: a 200
whose title names the thing; CurseForge answers scripts with a Cloudflare
challenge, so its pages were checked by their search listing)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from wowstats import forever, insights, links
from wowstats.analysis import kb
from wowstats.mcp import wowhead

ROOT = Path(__file__).resolve().parent.parent
FOREVER_DB = re.compile(r"^https://www\.wowhead\.com/forever/(item|quest|npc|spell|zone|object)=[1-9]\d*$")
GUIDE = re.compile(r"^https://www\.wowhead\.com/forever/guide/[a-z0-9-]+-dungeon-overview-location-rewards$")

# The dungeon guides, as fetched on 2026-11-19 (Wowhead's own Forever
# dungeon guide menu; each a 200 titled with the dungeon). Razorfen Kraul's
# slug is Wowhead's own typo.
CHECKED_GUIDES = {
    f"https://www.wowhead.com/forever/guide/{slug}-dungeon-overview-location-rewards" for slug in (
        "alcaz-prison", "blackfathom-deeps", "blackrock-depths", "city-of-dalaran",
        "dire-maul-capital-gardens-west", "dire-maul-gordoks-commons-north", "dire-maul-warpwood-quarter-east",
        "excavation-site-wetlands", "gnomeregan", "hall-of-thanes", "lower-blackrock-spire", "maraudon",
        "ragefire-chasm", "razerfen-kraul", "razorfen-downs", "ruins-of-lordaeron", "scarlet-monastery-armory",
        "scarlet-monastery-cathedral", "scarlet-monastery-graveyard", "scarlet-monastery-library", "scholomance",
        "shadowfang-keep", "stratholme-live-side", "stratholme-undead-side", "the-deadmines", "the-drowned-city",
        "the-stockade", "the-temple-of-atalhakkar", "uldaman", "upper-blackrock-spire", "wailing-caverns",
        "zulfarrak")}
# Forever's new dungeons with no Wowhead guide on 2026-11-19.
NO_GUIDE = ("Krol'dok Stronghold", "Blackmaw Hold", "Shaper's Terrace")

# The manual's addon pages (docs/videos/steps.json), checked 2026-11-19.
CHECKED_ADDONS = {
    "polestar": "https://www.curseforge.com/wow/addons/polestar",
    "recorder": "https://github.com/fitzforge/polestar/releases/latest",
    "syndicator": "https://www.curseforge.com/wow/addons/syndicator",
    "auctionator": "https://www.curseforge.com/wow/addons/auctionator",
    "altoholic": "https://www.curseforge.com/wow/addons/altoholic",
    "questie": "https://www.curseforge.com/wow/addons/questie",
    # Checked 2026-11-20 in WebKit (page title "WoWSims Exporter"), project 590920.
    "wowsims": "https://www.curseforge.com/wow/addons/wowsims-exporter",
}
# Every other https link the page code and the manual write (Ask links get
# the prompt appended; the 3D viewer's credit is Wowhead's home page).
CHECKED_FIXED = {
    "https://www.wowhead.com/forever", "https://www.wowhead.com", "https://claude.ai/new?q=",
    "https://chatgpt.com/?prompt=", "https://www.curseforge.com/download/app", "https://polestar.guide/client",
    "https://docs.astral.sh/uv/",
    # The weights' credit (web/app.js WEIGHTS_CREDIT): the site tools/foreverdb_weights reads.
    "https://foreverdb.net",
    # wowstats/account.py's pinned uv, UV_URL: fetched and checked against UV_SHA256.
    "https://github.com/astral-sh/uv/releases/download/",
    *CHECKED_ADDONS.values(),
}
URL = re.compile(r"https://[^\s\"'`<>)\\]+")


def guide_map() -> dict:
    return kb.static_rule("dungeon.wowhead_guides")["value"]


def guide_urls() -> list[str]:
    return [u for v in guide_map().values() for u in ([v] if isinstance(v, str) else v.values())]


def test_every_guide_is_a_checked_forever_dungeon_guide():
    urls = guide_urls()
    assert len(urls) == len(set(urls))
    for u in urls:
        assert GUIDE.match(u), u
    assert set(urls) == CHECKED_GUIDES


def test_the_guide_map_is_dated_and_names_real_dungeons():
    r = kb.static_rule("dungeon.wowhead_guides")
    assert r["last_verified"] == "2026-11-19" and r["scope"] == "forever"
    game = {a["name"] for a in (forever.data() or {}).get("areas", {}).values() if a.get("instance") == "dungeon"}
    new = set(kb.static_rule("dungeon.forever_new")["value"])
    assert set(guide_map()) <= game | new
    assert not set(NO_GUIDE) & set(guide_map())


@pytest.mark.parametrize("name, want", [
    ("City of Dalaran", "city-of-dalaran"),
    ("The Stockade", "the-stockade"),
    ("Stormwind Stockade", "the-stockade"),  # Polestar's name for it
    ("Excavation Site: Wetlands", "excavation-site-wetlands"),  # a colon, not a wing
    ("Scarlet Monastery: Library", "scarlet-monastery-library"),  # a wing: its own guide
    ("Blackrock Spire: Upper", "upper-blackrock-spire"),
])
def test_a_dungeon_links_its_guide(name, want):
    assert links.dungeon_url(name, 1) == f"https://www.wowhead.com/forever/guide/{want}-dungeon-overview-location-rewards"


@pytest.mark.parametrize("name", [*NO_GUIDE, "Scarlet Monastery", "Dire Maul", "Stratholme", "Blackrock Spire"])
def test_without_one_guide_a_dungeon_keeps_its_zone_link(name):
    assert links.dungeon_url(name, 796) == "https://www.wowhead.com/forever/zone=796"
    assert links.dungeon_url(name) is None
    # a dungeon split in wings lists each wing's guide instead
    assert len(links.dungeon_guides(name)) == {"Scarlet Monastery": 4, "Dire Maul": 3}.get(
        name, 2 if name in ("Stratholme", "Blackrock Spire") else 0)


def test_a_zone_that_is_no_dungeon_keeps_its_zone_link():
    zid = forever.area_id("Wetlands")
    assert insights.zone_ref("Wetlands")["url"] == f"https://www.wowhead.com/forever/zone={zid}"
    assert insights.zone_ref("City of Dalaran")["url"].startswith("https://www.wowhead.com/forever/guide/")


def test_every_wowhead_builder_gives_the_forever_form():
    for kind in links.KINDS:
        assert FOREVER_DB.match(links.wowhead_url(kind, 123)), kind
    with pytest.raises(ValueError):
        links.wowhead_url("guide", 1)
    out = wowhead.annotate({"item_id": 1, "quest_id": 2, "npc_id": 3, "spell_id": 4, "object_id": 5, "zone_id": 6,
                            "area_id": 7, "quest_ids": [8, 9]})
    for k, v in out.items():
        for u in v if isinstance(v, list) else [v]:
            if isinstance(u, str) and u.startswith("http"):
                assert FOREVER_DB.match(u), (k, u)
    # the page's own builder (web/items.js) writes the same address
    js = (ROOT / "web" / "items.js").read_text()
    assert re.search(r"const WOWHEAD = '" + re.escape(links.WOWHEAD_BASE) + "';", js)


def test_the_manual_links_the_checked_addon_pages():
    steps = json.loads((ROOT / "docs" / "videos" / "steps.json").read_text())
    assert {a["id"]: a.get("url") for a in steps["addons"]} == CHECKED_ADDONS


def test_every_fixed_link_in_the_page_and_the_manual_was_checked():
    found = set()
    for p in [*(ROOT / "web").glob("*.js"), ROOT / "web" / "model3d" / "model3d.js",
              ROOT / "docs" / "videos" / "steps.json"]:
        found |= {u for u in URL.findall(p.read_text()) if "${" not in u}
    for p in (ROOT / "wowstats" / "account.py", ROOT / "shipper" / "install-mac.sh",
              ROOT / "shipper" / "install-windows.ps1"):
        found |= {u for u in URL.findall(p.read_text()) if "{" not in u}
    assert found <= CHECKED_FIXED, found - CHECKED_FIXED


def test_the_mcp_dungeon_rows_link_the_guide_or_the_wings():
    from wowstats.mcp import plantools

    assert plantools._dungeon_links("City of Dalaran") == {
        "url": "https://www.wowhead.com/forever/guide/city-of-dalaran-dungeon-overview-location-rewards"}
    sm = plantools._dungeon_links("Scarlet Monastery")
    assert sm["url"] == f"https://www.wowhead.com/forever/zone={forever.area_id('Scarlet Monastery')}"
    assert [g["name"] for g in sm["guides"]] == ["Graveyard", "Library", "Armory", "Cathedral"]
    assert plantools._dungeon_links("Blackmaw Hold") == {
        "url": "https://www.wowhead.com/forever/search?q=Blackmaw%20Hold"}
