"""Game data is kept for good: every query reads the whole history, a range
that fills the row limit is read again in pages, and VL_ARCHIVE_URL is
optional."""

from datetime import UTC, datetime, timedelta

from wowstats import logsql, stores
from wowstats.stores import HISTORY_START, Store

# Ten years of events, one a day.
NOW = datetime.now(UTC)
EVENTS = [NOW - timedelta(days=d, hours=1) for d in range(3650)]


class Fake(Store):
    def __init__(self, archive=""):
        super().__init__("http://vl.invalid", archive)
        self.calls = []

    def vl_raw(self, base, query, start, end, limit=stores.ROW_LIMIT):
        self.calls.append((base, start, end))
        if "| limit 1" in query:
            return []
        rows = [{"_time": stores.iso_us(t)} for t in EVENTS if start <= t <= end]
        return logsql.parse(query).run(rows)[:limit]


def test_whole_history_comes_back_past_the_row_limit():
    s = Fake()
    rows = s.vl_paged(s.vl_url, "*", HISTORY_START, NOW, limit=500)
    assert len(rows) == len(EVENTS)
    assert len({r["_time"] for r in rows}) == len(EVENTS), "no row twice at a split"


def test_one_store_reads_from_the_start():
    s = Fake()
    assert len(s.vl("*")) == len(EVENTS)
    assert {c[0] for c in s.calls} == {"http://vl.invalid"}
    assert min(c[1] for c in s.calls) == HISTORY_START
    assert s.sources()["archive"] == "off"


def test_mcp_store_has_no_window_either():
    from wowstats.mcp.store import Store as McpStore

    st = McpStore.from_env({"VL_URL": "http://vl.invalid", "HISTORY_DAYS": "30"})
    assert st.window()[0] == HISTORY_START
