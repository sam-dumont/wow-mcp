"""Battle.net `market` data reaches a model only through the MCP, and only
for a person whose "Battle.net data to my AI" setting is on
(wowstats.tenancy; owner ruling 2026-11-18: their own data to their own
assistant). Without that setting (a one-person install) the MCP never
lists or runs it, whatever the env. No model runs on the server."""

import unittest

from tests.mcp import fakes, inmem
from wowstats import mcp
from wowstats.mcp import tools
from wowstats.mcp.store import Store


class McpNeverServesMarket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = fakes.FakeStores()

    @classmethod
    def tearDownClass(cls):
        cls.fake.close()

    def test_not_listed_not_callable_whatever_the_env(self):
        # The old switch does nothing now.
        ctx = mcp.context({"VL_URL": self.fake.url, "VM_URL": self.fake.url, "MCP_MARKET": "1"})
        before = len(self.fake.queries)
        self.assertNotIn("market", inmem.listed(ctx))
        r = inmem.call(ctx, "market")
        self.assertTrue(r.is_error)
        self.assertIn("unknown tool", r.content[0].text)
        self.assertFalse(any("wow_auction" in q.get("query", "") for q in self.fake.queries[before:]))

    def test_listed_and_callable_when_the_person_opted_in(self):
        ctx = mcp.context({"VL_URL": self.fake.url, "VM_URL": self.fake.url})
        ctx.bnet = True
        self.assertIn("market", inmem.listed(ctx))
        out = inmem.call(ctx, "market", {"faction": "horde"})
        self.assertFalse(out.is_error, out)
        ctx.bnet = False
        self.assertNotIn("market", inmem.listed(ctx))

    def test_ctx_has_no_market_switch(self):
        self.assertFalse(hasattr(tools.Ctx(store=Store(vl_url="http://x")), "market"))


if __name__ == "__main__":
    unittest.main()
