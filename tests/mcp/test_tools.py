import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from unittest import mock

from tests.mcp import fakes
from wowstats.mcp import route, tools
from wowstats.mcp.store import Store, parse_when


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores()
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url, vm_url=cls.fake.url))

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def call(self, name, **args):
        return tools.call(self.ctx, name, args)

    def test_every_query_keeps_the_stream_filter_and_the_char(self):
        # The account-wide missing-sources read (wowstats.missing, every
        # character on purpose) is cached a minute; another test clearing
        # that cache on the same worker made it show up here.
        self.call("zones", char=fakes.CHAR)
        self.fake.queries.clear()
        self.call("zones", char=fakes.CHAR)
        qs = self.fake.log_queries()
        self.assertTrue(qs)
        for q in qs:
            self.assertTrue(q.startswith("feed:wow-* "), q)
            self.assertIn(f'char:="{fakes.CHAR}"', q)

    def test_char_defaults_to_the_one_seen_last(self):
        out = self.call("character_summary")
        self.assertEqual(out["char"], fakes.CHAR)
        self.assertEqual(out["other_characters"], [fakes.ALT])

    def test_character_summary(self):
        out = self.call("character_summary", char=fakes.CHAR)
        self.assertIsNone(out["title"])  # no page behind this context
        self.assertEqual(out["level"], 14)
        self.assertEqual(out["xp"], 5200)  # the snapshot is newer than the recorder row
        self.assertEqual(out["xp_pct"], 42.3)
        self.assertEqual(out["gold"], "12g 34s 56c")
        self.assertEqual(out["time_in_level"], "25m")
        self.assertEqual(out["deaths"], 2)  # the rows `deaths` counts (fakes.DEATHS)
        # pace over levels 11-13: 30300 xp in 11100 s
        self.assertEqual(out["xp_per_hour_last_3_levels"], 9827)
        self.assertIsNotNone(out["eta_next_level"])

    def test_levels(self):
        out = self.call("levels", char=fakes.CHAR, from_level=11)
        self.assertEqual([lv["level"] for lv in out["levels"]], [11, 12, 13, 14])
        self.assertTrue(out["levels"][-1]["partial"])
        self.assertEqual(out["levels"][-1]["seconds"], 1500)  # newest copy of the growing split
        self.assertEqual(out["slowest"], 12)
        self.assertEqual(out["median"], "55m")

    def test_sessions(self):
        out = self.call("sessions", char=fakes.CHAR, since="2026-11-01")
        s = out["sessions"]
        self.assertEqual(len(s), 2)
        newest, older = s
        self.assertTrue(newest["start"].startswith("2026-11-17T20:00"))
        self.assertEqual(newest["dungeons"], [{"name": "Ragefire Chasm", "dur": "30m"}])
        self.assertEqual(newest["grouped_with"], ["Thrall-Fixture"])
        self.assertEqual(newest["guide_steps"], 3)
        self.assertEqual(older["deaths"], 1)
        self.assertEqual(older["xp_gained"], 970)
        self.assertEqual(older["quests_turned_in"], 1)
        self.assertEqual(older["flights"], 1)
        self.assertEqual(older["levels"], "12->12")

    def test_zones(self):
        out = self.call("zones", char=fakes.CHAR)
        self.assertEqual(out["zones"][0]["zone"], "The Barrens")
        d = out["zones"][1]
        # 970 XP over 4980 s in Durotar (fakes.ZONE_RECS): 701.2 an hour.
        self.assertEqual((d["zone"], d["xp"], d["deaths"], d["xp_per_hour"]), ("Durotar", 970, 1, 701))

    def test_guide_steps(self):
        out = self.call("guide_steps", char=fakes.CHAR, guide="barrens")
        self.assertEqual([g["guide"] for g in out["guides"]], ["Barrens 12-15"])
        self.assertEqual(out["slowest_steps"][0]["step"], 2)
        how = {(s["how"], tuple(s["steps"])) for s in out["skipped"]}
        self.assertIn(("jumped", (3, 4, 5)), how)
        self.assertIn(("skipped", (7,)), how)
        self.assertEqual(out["guides"][0]["skipped"], 4)

    def test_deaths_dungeons_players(self):
        d = self.call("deaths", char=fakes.CHAR)
        self.assertEqual(d["total"], 2)
        self.assertEqual(d["by_zone"], [{"zone": "Durotar", "n": 2}])
        r = self.call("dungeons", char=fakes.CHAR)
        self.assertEqual(r["runs"][0]["xp_per_hour"], 9000)
        p = self.call("players", char=fakes.CHAR)
        self.assertEqual(p["players"][0]["player"], "Thrall-Fixture")
        self.assertEqual(p["players"][0]["days"], 2)
        self.assertEqual(p["players"][0]["class"], "Shaman")
        self.assertNotIn(fakes.CHAR, [x["player"] for x in p["players"]])

    def test_prices(self):
        out = self.call("prices", item="linen")
        self.assertEqual(len(out["items"]), 1)
        i = out["items"][0]
        self.assertEqual((i["latest"], i["latest_high"], i["min"], i["scan_days"], i["change_vs_first"]),
                         ("0s 22c", "0s 40c", "0s 15c", 2, "0s 7c"))

    def test_market_is_not_an_mcp_tool(self):
        self.assertNotIn("market", [t.name for t in tools.TOOLS])
        with self.assertRaises(KeyError):
            self.call("market")

    def test_market(self):
        before = len(self.fake.queries)
        out = tools.call(self.ctx, "market", {}, (tools.MARKET,))
        self.assertEqual(out["retail_token_gold"], 251235)
        self.assertEqual(out["retail_token_change_7d"], 1235)
        self.assertEqual(out["rulesets"], [{"name": "Forever", "up": True}])
        self.assertNotIn("items", out)  # no Auction House API (owner's rule, 2026-11-20)
        self.assertFalse(any("auction" in q.get("query", "") for q in self.fake.queries[before:]))

    def test_route_reference_off_says_what_instead(self):
        with mock.patch.dict(os.environ, {"ROUTE_REFERENCE_FILE": "off"}):
            out = self.call("route_reference")
        self.assertFalse(out["available"])
        self.assertIn("own previous levels", out["instead"])

    def test_route_reference_from_a_players_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"source": "forge judge", "guides": {"Barrens 12-15": {"minutes": 20, "start_level": 12,
                                                                             "end_level": 15}},
                       "levels": {"12": 2400}}, f)
        try:
            with mock.patch.dict(os.environ, {"ROUTE_REFERENCE_FILE": f.name}):
                out = self.call("route_reference", level=12, guide="barrens")
                steps = self.call("guide_steps", char=fakes.CHAR, guide="barrens")
        finally:
            os.unlink(f.name)
        self.assertEqual(out["level"]["expected_s"], 2400)
        self.assertEqual(out["guide"]["minutes"], 20)
        self.assertEqual(steps["guides"][0]["expected"], "20m")
        self.assertEqual(steps["guides"][0]["vs_expected"], "150%")
        route._cached = None

    def test_bad_arguments(self):
        with self.assertRaises(tools.ToolError):
            self.call("zones", nope=1)
        with self.assertRaises(tools.ToolError):
            self.call("sessions", since="last tuesday")
        with self.assertRaises(KeyError):
            tools.call(self.ctx, "drop_tables", {})

    def test_store_down_is_a_tool_error(self):
        ctx = tools.Ctx(store=Store(vl_url="http://127.0.0.1:9", timeout=2))
        with self.assertRaises(tools.ToolError) as e:
            tools.call(ctx, "levels", {"char": "X-Y"})
        # A request id, never the store's address (2026-11-20 server review, info 2)
        self.assertNotIn("127.0.0.1", str(e.exception))
        self.assertIn("request", str(e.exception))

    def test_parse_when(self):
        now = datetime(2026, 11, 18, 12, tzinfo=UTC)
        self.assertEqual(parse_when("36h", now, now).isoformat(), "2026-11-17T00:00:00+00:00")
        self.assertEqual(parse_when("2026-11-01", now, now).isoformat(), "2026-11-01T00:00:00+00:00")
        self.assertIs(parse_when("", now, now), now)


class GearToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores()
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url))

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def test_gear(self):
        out = tools.call(self.ctx, "gear", {"char": fakes.CHAR})
        self.assertEqual((out["level"], out["class"], out["spec"]), (14, "PRIEST", "Shadow"))
        self.assertIn("because you picked Shadow in", out["spec_from"])
        self.assertEqual(len(out["slots"]), 17)
        chest = next(s for s in out["slots"] if s["slot"] == "Chest")
        self.assertEqual(chest["worn"], "Neophyte's Robe")
        ups = out["next_upgrades"]
        self.assertTrue(ups and len(ups) <= 10)
        for u in ups:
            self.assertTrue(u["where"] and (u["gain_points"] or 0) > 0, u)
            self.assertLessEqual(u["level"], 17)
        # Horde quests only; the quest handed in gives nothing any more.
        self.assertFalse(any("1884" in w for u in ups for w in u["where"]))
        # What the bags and the bank hold comes first, an equip away (fakes.BAGS),
        # in the advice's order (the slot furthest from BiS first).
        self.assertEqual(sorted((u["item"], u["where"]) for u in ups[:2]),
                         [("Adept's Gloves", ["in your bags: equip it"]),
                          ("Seer's Cuffs", ["in your bank: take it out at a banker and equip it"])])
        # The badge: the item's share of BiS and the change from the worn one
        # (so the worn one's share is the difference).
        held = [u["bis_pct"] - u["delta_pct"] for u in ups[:2]]
        self.assertEqual(held, sorted(held))
        self.assertTrue(all(s["worn_standing"] is None or s["worn_standing"] in ("BiS", "2nd")
                            or s["worn_standing"].endswith("% of BiS") for s in out["slots"]))
        # Each weapon setup's best on its own, and where the weights come from.
        self.assertTrue(out["weapon_setups"])
        self.assertTrue({x["setup"] for x in out["weapon_setups"]} <= {"two-hander", "main hand + off hand"})
        self.assertTrue(out["weights_from"]["label"])
        # Every upgrade says who it's built for, the same label as the page
        # (wowstats.gearfit), and the list is ranked on rank_points.
        self.assertEqual(out["role"], "caster")
        for u in ups:
            self.assertIn(u["fit"]["for_you"], ("yes", "any", "spec", "class"), u)
            self.assertTrue(u["fit"]["says"], u)
            self.assertLessEqual(u["rank_points"], u["gain_points"] + 0.01)
        holy = tools.call(self.ctx, "gear", {"char": fakes.CHAR, "spec": "holy", "limit": 3})
        self.assertEqual(holy["spec"], "Holy (healer)")
        self.assertIn("you picked it on this page", holy["spec_from"])
        self.assertLessEqual(len(holy["next_upgrades"]), 3)

    def test_gear_without_a_sheet_says_so(self):
        # No Syndicator sheet: an answer, never an error (degraded mode,
        # tests/test_degraded.py): the best for the level when the class is
        # known, else why and what unlocks it.
        fake = fakes.FakeStores(logs=[r for r in fakes.LOGS if r[1] is not fakes.GEAR])
        try:
            out = tools.call(tools.Ctx(store=Store(vl_url=fake.url)), "gear", {"char": fakes.CHAR})
            self.assertIn("Syndicator", out["unlock"])
            self.assertTrue(out.get("estimated") or "no character sheet" in out.get("why", ""))
        finally:
            fake.close()


class SchemaTest(unittest.TestCase):
    def test_schemas_are_closed_objects_and_names_unique(self):
        names = [t.name for t in tools.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        for t in tools.TOOLS:
            s = t.input_schema
            self.assertEqual(s["type"], "object")
            self.assertFalse(s["additionalProperties"])
            self.assertLessEqual(len(t.description), 400)


if __name__ == "__main__":
    unittest.main()


class TitleTest(unittest.TestCase):
    def test_the_summary_says_what_the_page_says(self):
        """character_summary's title is the page's own line (ui.App.title),
        not a second rendering of it."""
        fake = fakes.FakeStores()
        try:
            asked = []
            ctx = tools.Ctx(store=Store(vl_url=fake.url, vm_url=fake.url),
                            title=lambda c: asked.append(c) or "Priest · Discipline 16/7/0 (levelling Shadow)")
            out = tools.call(ctx, "character_summary", {"char": fakes.CHAR})
            self.assertEqual(out["title"], "Priest · Discipline 16/7/0 (levelling Shadow)")
            self.assertEqual(asked, [fakes.CHAR])
        finally:
            fake.close()


class EmptyAccountTest(unittest.TestCase):
    """A session signed in as an account with no data says which account."""

    class Empty:
        def logs(self, query, start=None, end=None, limit=None):
            return []

    def test_the_message_names_who_is_signed_in(self):
        ctx = tools.Ctx(store=self.Empty(), who="sample-slug")
        with self.assertRaises(tools.ToolError) as e:
            tools.resolve_char(ctx, {})
        self.assertEqual(str(e.exception), "No character has any events yet for sample-slug. If that isn't you, "
                                           "sign in again with the account that ships your data.")

    def test_a_one_person_install_keeps_the_shipper_hint(self):
        with self.assertRaises(tools.ToolError) as e:
            tools.resolve_char(tools.Ctx(store=self.Empty()), {})
        self.assertIn("is the shipper sending?", str(e.exception))
