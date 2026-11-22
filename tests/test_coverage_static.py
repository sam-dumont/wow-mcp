"""Coverage on the static sites (owner, 2026-11-22, option 1):

- /zogzog/ carries the full view's coverage, cut to what its lines read, so
  it says the same lines (the Recorder's gap by level and length, the
  equipment's last read);
- the public site gets one state per source its page draws a line for
  (Battle.net, under the equipment), never a time, a gap or a level;
- unknown stays unknown on both: no deaths count without the Recorder, no
  "complete" for a Battle.net store that didn't answer.

The bundles are built by the real publisher over the docs demo
(tests/degraded.py), the Recorder off for an hour of level 28 and no
Syndicator sheet, so the worn items come from Battle.net, read 5 hours ago
from the last complete read. The page itself: tests/test_coverage_browser.py."""

from __future__ import annotations

import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tests import degraded as D
from wowstats import coverage, publish, redact, ui
from wowstats import gear as gearmod
from wowstats.mcp import tools as T

pytestmark = pytest.mark.usefixtures("demo_clock")

WORN = {"head": {"item_id": 10504, "stats": {}}, "chest": {"item_id": 14152, "stats": {}}}
# Keys that carry a time, a length or a level of a gap: never in the public site.
TIMED = {"as_of", "gaps", "off_s", "recorder_off", "from", "to", "fix"}


def gap_rows() -> list[dict]:
    return [r for r in D.rows_for("recorder-gap") if r["feed"] not in ("wow-gear", "wow-bags")]


def stale_read():
    """Battle.net's state: the last complete read, 5 hours before the build."""
    read = {"as_of": time.time() - 5 * 3600, "fallback": True}
    return lambda: dict(read)


class Built:
    """One publisher run over `rows` with `bnet` as Battle.net's state, its
    sites served by nothing: read the files, or serve them (browser tests)."""

    def __init__(self, tmp: Path, rows: list[dict], bnet):
        self.mp = pytest.MonkeyPatch()
        self.mp.setenv("WOW_ITEMS", "off")
        self.mp.setattr(gearmod, "armory_worn", lambda store, char: gearmod.worn_stats(WORN))
        self.mp.setattr(coverage, "bnet_of", lambda store, char: bnet())
        T._MISSING.clear()
        self.vl = D.VL(rows)
        self.tmp = tmp
        self.env = {"VL_URL": self.vl.url, "MODEL3D": "off", "PUBLISH_DIR": str(tmp / "site"),
                    "ZOGZOG_KEY_FILE": str(tmp / "zogzog.key"), "MODEL3D_CACHE_DIR": str(tmp / "cache"),
                    "HOME": str(tmp), "ANALYSIS_STATE_DIR": str(tmp), "DEFAULT_CHAR": D.CHAR}
        publish.new_key(self.env["ZOGZOG_KEY_FILE"])  # a throwaway key, never the lab's
        with redirect_stdout(io.StringIO()):
            assert publish.publish(self.env)["built"]
        self.key = Path(self.env["ZOGZOG_KEY_FILE"]).read_text().strip()
        self.cur = tmp / "site" / "current"
        # The full view over the same store, for the lines to compare with.
        self.full = ui.App({**self.env, "VIEW": "full", "AUTH": "none", "BIND": "127.0.0.1"})

    def public(self, rel: str) -> dict:
        return json.loads((self.cur / "public" / rel).read_bytes())

    def zogzog(self, rel: str) -> dict:
        k = publish.unb64u(self.key)
        blob = (self.cur / "zogzog" / publish.sealed_name(k, rel)).read_bytes()
        return json.loads(publish.unseal(k, rel, blob))

    def public_answers(self) -> dict[str, dict]:
        root = self.cur / "public"
        return {p.relative_to(root).as_posix(): json.loads(p.read_bytes()) for p in (root / "data").rglob("*.json")}

    def close(self) -> None:
        self.vl.close()
        self.mp.undo()
        T._MISSING.clear()


@pytest.fixture(scope="module")
def gap(tmp_path_factory):
    b = Built(tmp_path_factory.mktemp("covgap"), gap_rows(), stale_read())
    yield b
    b.close()


@pytest.fixture(scope="module")
def blind(tmp_path_factory):
    """No Recorder at all, and a Battle.net store that didn't answer."""
    rows = [r for r in D.rows_for("no-recorder") if r["feed"] not in ("wow-gear", "wow-bags")]
    b = Built(tmp_path_factory.mktemp("covblind"), rows, lambda: "unknown")
    yield b
    b.close()


def keys_of(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from keys_of(x)
    elif isinstance(v, list):
        for x in v:
            yield from keys_of(x)


def strings_of(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from strings_of(x)
    elif isinstance(v, list):
        for x in v:
            yield from strings_of(x)
    elif isinstance(v, str):
        yield v


# ---------------------------------------------------------------- /zogzog/


def test_zogzog_carries_what_the_full_views_lines_read(gap):
    full = gap.full.api_char(D.CHAR)["coverage"]
    zz = gap.zogzog(publish.data_path("char", D.CHAR))["coverage"]
    rec, bn = full["recorder"], full["battlenet"]
    assert rec["state"] == "partial" and [g["level"] for g in rec["gaps"]] == [28]
    assert zz["recorder"] == {"state": rec["state"], "fix": rec["fix"],
                              "gaps": [{"level": g["level"], "off_s": g["off_s"]} for g in rec["gaps"]]}
    assert zz["battlenet"] == {"state": bn["state"], "as_of": bn["as_of"]} and bn["state"] == "partial"
    # The gap's own times stay on the server: the lines don't read them.
    assert "from" not in zz["recorder"]["gaps"][0] and "to" not in zz["recorder"]["gaps"][0]
    # The journal's line, on the newest session of level 28, as the full view's.
    marked = [s for s in gap.zogzog(publish.data_path("journal", D.CHAR))["sessions"] if s.get("recorder_off")]
    full_marked = [s for s in gap.full.api_journal(D.CHAR)["sessions"] if s.get("recorder_off")]
    assert [s["recorder_off"] for s in marked] == [s["recorder_off"] for s in full_marked] != []


# ---------------------------------------------------------------- the public site


def test_public_gets_a_state_never_a_time(gap):
    char = gap.public(publish.data_path("char", D.CHAR))
    assert char["coverage"] == {"battlenet": "partial"}
    assert char["gear"]["items_from"] == "battle.net"  # the line's place on the page
    as_of = gap.full.api_char(D.CHAR)["coverage"]["battlenet"]["as_of"]
    for rel, d in gap.public_answers().items():
        assert not TIMED & set(keys_of(d)), f"{rel}: {sorted(TIMED & set(keys_of(d)))}"
        for s in strings_of(d):
            assert as_of not in s and not s.startswith(D.GAP[0][:13]), f"{rel}: {s}"


def test_public_shape_drops_the_full_coverage():
    """Handed the full view's coverage by mistake, the public shape keeps
    no time: a source's value is a plain state or nothing."""
    full = {"recorder": coverage.entry("partial", "2026-11-18T09:00:00Z", [{"level": 28, "off_s": 4200}], "x"),
            "battlenet": coverage.entry("complete", "2026-11-18T09:00:00Z")}
    got = redact.public("char", {"coverage": full})["coverage"]
    assert got == {"battlenet": None}


def test_public_state_per_battlenet_read():
    now = coverage._ts("2026-11-18T12:00:00Z")

    def at(state, as_of=None):
        return coverage.public({"battlenet": coverage.entry(state, as_of)}, now)["battlenet"]

    assert at("complete", "2026-11-18T10:00:00Z") == "complete"  # 2 h: one refresh
    assert at("complete", "2026-11-18T08:00:00Z") == "partial"   # 4 h: the full view's line
    assert at("partial", "2026-11-18T11:30:00Z") == "partial"    # an older complete read
    assert at("unavailable") == "unavailable" and at("unknown") == "unknown"
    assert coverage.public(None, now) == {"battlenet": "unknown"} == coverage.public(coverage.unknown(), now)
    assert set(coverage.public(coverage.unknown(), now)) == {"battlenet"}  # no Recorder: no count to qualify


# ---------------------------------------------------------------- unknown is not zero, on both


def test_unknown_stays_unknown_on_both_sites(blind):
    zz = blind.zogzog(publish.data_path("char", D.CHAR))
    assert zz["deaths"]["total"] is None
    assert zz["coverage"]["recorder"]["state"] == "unavailable" and zz["coverage"]["battlenet"]["state"] == "unknown"
    pub = blind.public(publish.data_path("char", D.CHAR))
    assert pub["coverage"] == {"battlenet": "unknown"} and "deaths" not in pub
    for rel, d in blind.public_answers().items():
        assert not TIMED & set(keys_of(d)), rel
