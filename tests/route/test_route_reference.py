"""The bundled route clock (data/route-reference.json) and how the tools use it."""

import json
import os
import tempfile
import unittest
from unittest import mock

from tests.mcp import fakes
from wowstats.mcp import route, tools
from wowstats.mcp.store import Store

HILLSBRAD = "forever/alliance/30-40/01-hillsbrad-foothills"
SESSION = [{"_time": "2026-11-17T20:00:00.000Z", "kind": "session", "race": "Gnome", "class": "PRIEST",
            "faction": "Alliance"}]
# The player's own levels on the route's ids, as the recorder writes them.
LEVELS = [{"_time": "2026-11-16T20:00:00.000Z", "level": 28, "seconds": 15028, "xp": 41400, "partial": False,
           "source_id": f"level:{fakes.CHAR}:28"},
          {"_time": "2026-11-17T20:00:00.000Z", "level": 29, "seconds": 600, "xp": 2000, "partial": True,
           "source_id": f"level:{fakes.CHAR}:29"}]
STEPS = [{"_time": "2026-11-16T19:14:45.300Z", "kind": "step", "guide": HILLSBRAD, "step": 7, "duration_s": 673.0,
          "zone": "Wetlands", "source_id": "rec:1:248:53"}]


def bundled() -> route.Reference:
    route._cached = None
    with mock.patch.dict(os.environ, {"ROUTE_REFERENCE_FILE": ""}):
        return route.reference()


class BundledFileTest(unittest.TestCase):
    def test_loads_by_default(self):
        ref = bundled()
        self.assertTrue(ref.available, ref.why)
        self.assertTrue(ref.polestar.get("tag", "").startswith("v"))
        self.assertEqual(ref.polestar.get("license"), "GPL-3.0-only")
        for side in route.SIDES:
            self.assertEqual(sorted(int(k) for k in ref.levels[side])[:1], [1])
            self.assertIn("59", ref.levels[side])

    def test_every_level_and_guide_has_a_clock(self):
        ref = bundled()
        for side in route.SIDES:
            for lv, row in ref.levels[side].items():
                self.assertGreater(row["seconds"], 0, (side, lv))
                self.assertLessEqual(row["min_s"], row["seconds"])
                self.assertLessEqual(row["seconds"], row["max_s"])
        g = ref.guides[HILLSBRAD]
        self.assertGreater(g["seconds"], 0)
        self.assertEqual([s[0] for s in g["steps"]], list(range(1, len(g["steps"]) + 1)))

    def test_off_and_unreadable(self):
        self.assertFalse(route.reference("off").available)
        self.assertIn("can't be read", route.reference("/nonexistent/route.json").why)


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.ref = bundled()

    def test_level_for_class_then_side_then_both(self):
        # One definition with the semantic layer's planned_s: the class's
        # median over the side's starting races, whatever the race.
        own = self.ref.level(28, "Alliance", "PRIEST")
        self.assertEqual(own["expected_s"], self.ref.levels["Alliance"]["28"]["by_class"]["PRIEST"])
        self.assertEqual(own["basis"], "Alliance Priest, median over the starting races")
        side = self.ref.level(28, "Alliance")
        self.assertEqual(side["expected_s"], self.ref.levels["Alliance"]["28"]["seconds"])
        self.assertIn("median", side["basis"])
        cls = self.ref.level(28, "alliance", "priest")
        self.assertIn("Priest", cls["basis"])
        self.assertIsNotNone(self.ref.level(28))
        self.assertIsNone(self.ref.level(70))

    def test_guide_ids_as_the_recorder_writes_them(self):
        self.assertEqual(self.ref.guide(HILLSBRAD)[0], HILLSBRAD)
        self.assertEqual(self.ref.guide("alliance/30-40/01-hillsbrad-foothills")[0], HILLSBRAD)
        self.assertEqual(self.ref.guide("05-duskwood")[0], "forever/alliance/05-duskwood")
        self.assertIsNone(self.ref.guide("lookup/handin/stormwind-stockade"))
        self.assertEqual(self.ref.step_seconds(HILLSBRAD, 7), self.ref.guides[HILLSBRAD]["steps"][6][1])
        self.assertIsNone(self.ref.step_seconds(HILLSBRAD, 999))

    def test_compare(self):
        out = self.ref.compare([{"level": 28, "seconds": 15028, "partial": False},
                                {"level": 29, "seconds": 600, "partial": True}],
                               {"faction": "Alliance", "race": "Gnome", "class": "PRIEST"})
        exp = self.ref.level(28, "Alliance", "PRIEST")["expected_s"]
        self.assertEqual(out[0]["route_s"], exp)
        self.assertEqual(out[0]["vs_route"], f"{15028 / exp:.1f}x")
        self.assertNotIn("vs_route", out[1])  # the current level isn't finished

    def test_hand_made_file_still_reads(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"source": "mine", "guides": {"Barrens 12-15": {"minutes": 20}}, "levels": {"12": 2400}}, f)
        try:
            route._cached = None
            ref = route.reference(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(ref.level(12)["expected_s"], 2400)
        self.assertEqual(ref.guide_seconds("barrens"), 1200)


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from wowstats.analysis import drive

        drive._WS.clear()  # the prep of another test's fixture character
        rules = [("kind:=session", SESSION), ('feed:="wow-level"', LEVELS), ("kind:in(step, skip)", STEPS),
                 *fakes.prep_rules(SESSION + STEPS)]
        cls.fake = fakes.FakeStores(logs=rules + fakes.LOGS)
        cls.ctx = tools.Ctx(store=Store(vl_url=cls.fake.url))
        route._cached = None

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def call(self, name, **args):
        with mock.patch.dict(os.environ, {"ROUTE_REFERENCE_FILE": ""}):
            return tools.call(self.ctx, name, args)

    def test_levels_carry_the_route(self):
        out = self.call("levels", char=fakes.CHAR)
        lv28 = next(lv for lv in out["levels"] if lv["level"] == 28)
        exp = bundled().level(28, "Alliance", "PRIEST")
        self.assertEqual(lv28["route_s"], exp["expected_s"])
        self.assertTrue(lv28["vs_route"].endswith("x"))
        self.assertIn("route_note", out)

    def test_route_reference_uses_the_characters_side_and_class(self):
        out = self.call("route_reference", char=fakes.CHAR, level=28)
        self.assertEqual(out["for"], {"faction": "Alliance", "class": "PRIEST"})
        self.assertEqual(out["level"]["basis"], "Alliance Priest, median over the starting races")
        g = self.call("route_reference", char=fakes.CHAR, guide=HILLSBRAD, step=7)
        self.assertEqual(g["guide"]["guide"], HILLSBRAD)
        self.assertGreater(g["guide"]["step"]["expected_s"], 0)
        listing = self.call("route_reference", char=fakes.CHAR)
        self.assertEqual(list(listing["levels"]), ["Alliance"])
        self.assertTrue(all(v["levels"] for v in listing["guides"].values()))

    def test_guide_steps_show_the_expected_step_time(self):
        out = self.call("guide_steps", char=fakes.CHAR)
        g = next(g for g in out["guides"] if g["guide"] == HILLSBRAD)
        self.assertIn("expected", g)
        step = next(s for s in out["slowest_steps"] if s["guide"] == HILLSBRAD)
        self.assertIn("expected", step)


if __name__ == "__main__":
    unittest.main()
