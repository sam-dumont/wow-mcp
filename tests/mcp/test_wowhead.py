"""Wowhead Forever links on MCP tool results (wowstats.mcp.wowhead)."""

from __future__ import annotations

import json
import unittest

from tests.mcp import fakes, inmem
from wowstats.links import wowhead_url
from wowstats.mcp import server, tools, wowhead
from wowstats.mcp.store import Store

W = "https://www.wowhead.com/forever"


class AnnotateTest(unittest.TestCase):
    def test_pattern(self):
        # Checked in a browser on 2026-11-18: quest=91285 (a Forever-only quest), item=287975, npc=1569,
        # spell=2050, zone=85, object=1617 all open their page (Wowhead adds a slug by redirect).
        self.assertEqual(wowhead_url("quest", 91285), f"{W}/quest=91285")
        self.assertEqual(wowhead_url("item", "287975"), f"{W}/item=287975")
        with self.assertRaises(ValueError):
            wowhead_url("player", 1)

    def test_every_kind_and_the_primary(self):
        out = wowhead.annotate({"rows": [
            {"item_id": 5356, "quest_id": 873},
            {"npc_id": 1569}, {"spell_id": 2050}, {"area_id": 1581}, {"zone_id": 85}, {"object_id": 1617},
            {"with_item_id": 2589},
            {"quest_ids": [873, "863", None, -1]},
        ]})["rows"]
        self.assertEqual(out[0], {"item_id": 5356, "quest_id": 873, "wowhead": f"{W}/item=5356",
                                  "quest_wowhead": f"{W}/quest=873"})
        self.assertEqual([r["wowhead"] for r in out[1:7]],
                         [f"{W}/npc=1569", f"{W}/spell=2050", f"{W}/zone=1581", f"{W}/zone=85", f"{W}/object=1617",
                          f"{W}/item=2589"])
        self.assertEqual(out[7]["quests_wowhead"], [f"{W}/quest=873", f"{W}/quest=863"])

    def test_no_id_no_link_and_nothing_overwritten(self):
        src = {"item_id": None, "quest_id": "abc", "npc_id": 0, "spell_id": True, "zone": "Elwynn Forest",
               "nested": {"item_id": 2589.0, "wowhead": "kept"}}
        out = wowhead.annotate(src)
        self.assertNotIn("wowhead", out)
        self.assertEqual(out["nested"]["wowhead"], "kept")
        self.assertEqual(src["nested"], {"item_id": 2589.0, "wowhead": "kept"})  # input untouched
        self.assertEqual(wowhead.annotate({"item_id": 2589.0})["wowhead"], f"{W}/item=2589")


class OnTheWireTest(unittest.TestCase):
    """Through tools/call, as a client gets them."""

    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores()
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url))

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def call(self, name, **args):
        r = inmem.call(self.ctx, name, args)
        self.assertFalse(r.is_error, r)
        self.assertEqual(json.loads(r.content[0].text), r.structured_content)
        return r.structured_content

    def test_prices(self):
        i = self.call("prices", item="linen")["items"][0]
        self.assertEqual(i["wowhead"], f"{W}/item=2589")

    def test_gear_items_and_their_sources(self):
        out = self.call("gear", char=fakes.CHAR, limit=3)
        for u in out["next_upgrades"]:
            self.assertEqual(u["wowhead"], f"{W}/item={u['item_id']}")
            if u["where"][0].startswith("in your "):  # held: the item's own page is the link
                self.assertNotIn("where_wowhead", u)
                continue
            self.assertEqual(len(u["where_wowhead"]), len(u["where"]))
            for w, link in zip(u["where"], u["where_wowhead"], strict=True):
                if w.startswith("quest "):
                    self.assertRegex(link, rf"^{W}/quest=\d+$")

    def test_the_server_says_so(self):
        self.assertIn("wowhead", server.INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
