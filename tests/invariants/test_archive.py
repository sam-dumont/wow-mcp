"""An archive that doesn't answer (property 19): never a shorter history.

With VL_ARCHIVE_URL set, days older than VL_ARCHIVE_AFTER_DAYS live in a
second VictoriaLogs. Review 6 found the reader swallowed a failed archive
read and answered from the recent store alone: the event cache then read
the missing old hours as gone (retention) and dropped them, and the
analysis' coverage said the Recorder was complete. 4 rows and 2 deaths
became 2 rows, 1 death and "complete".

Here, both ways it fails: the archive down on the first read, and the
archive failing after the whole history was cached. A read whose range
needs the archive fails with stores.ArchiveUnavailable (a SourceError, so
every caller shows it as the store failing); the cache keeps what it had
and gives the whole history again once the archive answers.

Made-up character and records only.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from wowstats import coverage, eventcache, logsql, stores
from wowstats.analysis import prep
from wowstats.stores import SourceError, Store, parse_t

CHAR = "Hero-Alpha"
HOT, ARCHIVE = "http://vl-hot.invalid", "http://vl-archive.invalid"
HOT_DAYS = 89


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def history(now: datetime) -> tuple[list[dict], list[dict]]:
    """(the archive's rows, the recent store's): a session and a death
    200 days ago, and the same 5 days ago."""

    def rec(t: datetime, kind: str, i: int, **kw) -> dict:
        return {"_time": iso(t), "feed": "wow-recorder", "char": CHAR, "kind": kind, "zone": "Elwynn Forest",
                "level": "12", "source_id": f"rec:{CHAR}:{i}", **kw}

    old, new = now - timedelta(days=200), now - timedelta(days=5)
    return ([rec(old, "session", 1, login="true"), rec(old + timedelta(minutes=5), "death", 2)],
            [rec(new, "session", 3, login="true"), rec(new + timedelta(minutes=5), "death", 4)])


class Stores:
    """stores.http_get for the two VictoriaLogs: each answers its own rows
    with wowstats.logsql in [start, end] up to `limit`; the archive raises
    while `down`."""

    def __init__(self, archive: list[dict], hot: list[dict]):
        self.rows = {ARCHIVE: archive, HOT: hot}
        self.down = False

    def __call__(self, url: str, params: dict, *a, **k) -> bytes:
        base = url.split("/select/")[0]
        if base == ARCHIVE and self.down:
            raise SourceError("vl-archive.invalid: connection refused")
        lo, hi = parse_t(params["start"]), parse_t(params["end"])
        rows = [r for r in self.rows[base] if lo <= parse_t(r["_time"]) <= hi]
        out = logsql.parse(params["query"]).run(rows)[:int(params["limit"])]
        return "".join(json.dumps(r) + "\n" for r in out).encode()


@pytest.fixture
def two(monkeypatch):
    archive, hot = history(datetime.now(UTC))
    fake = Stores(archive, hot)
    monkeypatch.setattr(stores, "http_get", fake)
    monkeypatch.setattr(eventcache, "_SHARED", {})  # a cache of this test's stores only
    # An archive that failed is asked again on the next read here, not 30 s later.
    monkeypatch.setattr(stores, "ARCHIVE_RETRY_S", 0)
    st = Store(HOT, ARCHIVE, hot_days=HOT_DAYS)
    return fake, st


def read(st: Store) -> list[dict]:
    """What the analysis reads: every event of the character, through the cache."""
    return prep.query(st, st.stream, CHAR)


def deaths(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("kind") == "death")


def test_healthy_reads_the_whole_history(two):
    _fake, st = two
    rows = read(st)
    assert (len(rows), deaths(rows)) == (4, 2)
    assert coverage.from_rows(rows, CHAR)["recorder"]["state"] == "complete"


def test_archive_down_on_the_first_read_is_an_error_not_a_shorter_history(two):
    fake, st = two
    fake.down = True
    with pytest.raises(stores.ArchiveUnavailable):
        read(st)
    assert st.sources()["archive"] == "unreachable"
    # A range inside the recent store's days doesn't need the archive.
    now = datetime.now(UTC)
    got = st.vl_direct(f"{st.stream} char:={json.dumps(CHAR)} kind:=death | fields _time",
                       start=now - timedelta(days=30), end=now)
    assert len(got) == 1
    # Back: the whole history, with no restart.
    fake.down = False
    rows = read(st)
    assert (len(rows), deaths(rows)) == (4, 2)


def test_archive_failing_after_a_complete_read_keeps_the_cache(two):
    fake, st = two
    assert len(read(st)) == 4
    fake.down = True
    st.cache.expire()
    with pytest.raises(stores.ArchiveUnavailable):
        read(st)
    assert st.cache.stats() == {CHAR: 4}, "a failed read evicted the cached old hours"
    fake.down = False
    st.cache.expire()
    rows = read(st)
    assert (len(rows), deaths(rows)) == (4, 2)


def test_the_error_a_person_sees_says_the_older_days_are_missing(two):
    fake, st = two
    fake.down = True
    with pytest.raises(stores.ArchiveUnavailable) as e:
        read(st)
    said = stores.public_error(e.value)
    assert "older" in said and "vl-archive" not in said
