"""Snapshot parts (property 18): every part of a snapshot source comes from
the newest snapshot that carries it, however many partial ones came after.

A gear sheet without its items (Syndicator didn't know the character on
that run), bags without the bank (its cache lost), a logout sheet without
the money: each newer partial snapshot keeps the part the older complete
one said (evidence property (d), tests/invariants/test_evidence.py). Review
6 found the readers looked at a fixed window of the newest 10 snapshots: an
eleventh partial one pushed the complete one out and the page and the MCP
lost the equipment, the bank and the money while the analysis still had
them. Here: one complete snapshot then 9, 10 and 15 partial ones give the
same equipment, bank and money through the page, the MCP and the
analysis' prep, each part with its own observation time (not the newer
row's), also in the answers' coverage.

Made-up characters and items only: the docs demo's character, its
snapshots replaced.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests import degraded as D
from tests.invariants import histories as H
from tests.invariants.test_consumers import TenantVL, _fresh, _single
from wowstats import coverage

pytestmark = pytest.mark.usefixtures("demo_clock")

CHAR = D.CHAR
SNAP_FEEDS = ("wow-gear", "wow-bags", "wow-character")
# The complete snapshots, then the partial ones every minute after (the
# demo's own "now" is 2026-11-18T10:05:52Z, its last sheet 09:05:52).
T_FULL = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC).timestamp()
T_PART = T_FULL + 300
WORN = [7048, 4320]
BAGS, BANK = [2589, 6948], [4306, 2996]
MONEY = 199230


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _items(ids: list[int]) -> str:
    return json.dumps([{"slot": s, "slot_name": "Head" if s == 1 else "Chest", "item_id": i}
                       for s, i in zip((1, 5), ids, strict=True)])


def history(n_partial: int) -> list[dict]:
    """The demo with its snapshots replaced: one complete of each source at
    T_FULL, then n_partial of each lacking the part (items, bank, money)."""
    rows = [r for r in D.rows_for("full") if r["feed"] not in SNAP_FEEDS or r.get("kind") == "played"]
    sheet = next(r for r in reversed(D.rows_for("full")) if r["feed"] == "wow-character" and r.get("kind") != "played")
    gear = next(r for r in reversed(D.rows_for("full")) if r["feed"] == "wow-gear")
    common = {"char": CHAR, "component": "wow", "layer": "app"}
    rows.append({**gear, "_time": iso(T_FULL), "items": _items(WORN), "source_id": f"gear:{CHAR}:full"})
    rows.append({**common, "_time": iso(T_FULL), "feed": "wow-bags", "bags": json.dumps(BAGS),
                 "bank": json.dumps(BANK), "source_id": f"bags:{CHAR}:full"})
    rows.append({**sheet, "_time": iso(T_FULL), "money_copper": str(MONEY), "source_id": f"character:{CHAR}:full"})
    for i in range(n_partial):
        t = iso(T_PART + 60 * i)
        g = {k: v for k, v in gear.items() if k != "items"}
        rows.append({**g, "_time": t, "source_id": f"gear:{CHAR}:part{i}"})
        rows.append({**common, "_time": t, "feed": "wow-bags", "bags": json.dumps(BAGS),
                     "source_id": f"bags:{CHAR}:part{i}"})
        s = {k: v for k, v in sheet.items() if k != "money_copper"}
        rows.append({**s, "_time": t, "source_id": f"character:{CHAR}:part{i}"})
    return rows


def cov_iso(t: float) -> str:
    """Coverage's own time format (wowstats.coverage)."""
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parts(cov: dict | None) -> dict:
    return {s: ((cov or {}).get(s) or {}).get("parts") for s in ("gear", "polestar")}


def _worn(items) -> list[int]:
    return sorted(int(i["item_id"]) for i in items or [])


def answers(rows: list[dict], tmp_path) -> dict:
    """What the page, the MCP and the analysis say of the equipment, the
    bank and the money, with each part's time."""
    _fresh()
    vl = TenantVL({"0": rows})
    try:
        app, call = _single(vl.url, tmp_path, {})
        gi = app.views.gear_inputs(CHAR)
        header = app.api_char(CHAR)["header"]
        cov = app.coverage(CHAR)
        summary = call("character_summary", char=CHAR)
        gear = call("gear", char=CHAR)
    finally:
        vl.close()
    facts = H.facts(H.build(rows, CHAR))
    held = gi["held"] or {}
    return {
        "page": {"worn": _worn(gi["sheet"]["items"]), "worn_from": gi["worn_from"],
                 "bank": held.get("bank"), "bags": held.get("bags"),
                 "bank_at": (held.get("at") or {}).get("bank"), "money": header.get("money_copper"),
                 "money_at": header.get("money_at")},
        "mcp": {"money": summary.get("money_copper"), "worn_from": "sheet" if "unlock" not in gear else "fallback",
                "coverage": _parts(summary.get("coverage"))},
        "prep": {"worn": sorted(r[2] for r in facts["gear"])},
        "coverage": _parts(cov),
        "prep_coverage": _parts(coverage.from_rows(rows, CHAR)),
    }


@pytest.fixture(scope="module")
def complete(tmp_path_factory):
    return answers(history(0), tmp_path_factory.mktemp("complete"))


def test_the_complete_snapshot_alone(complete):
    """The baseline: every part read, each at T_FULL."""
    assert complete["page"]["worn"] == sorted(WORN) == complete["prep"]["worn"]
    assert complete["page"]["bank"] == sorted(BANK) and complete["page"]["money"] == MONEY == complete["mcp"]["money"]
    assert complete["page"]["money_at"] == iso(T_FULL)


@pytest.mark.parametrize("n", [9, 10, 15])
def test_partial_snapshots_keep_the_complete_parts(complete, tmp_path, n):
    got = answers(history(n), tmp_path)
    assert got["page"]["worn"] == complete["page"]["worn"], "the page lost the equipment"
    assert got["page"]["worn_from"] == "sheet"
    assert got["mcp"]["worn_from"] == "sheet", "the MCP lost the equipment"
    assert got["prep"] == complete["prep"]
    assert got["page"]["bank"] == sorted(BANK), "the page lost the bank"
    assert got["page"]["money"] == MONEY == got["mcp"]["money"], "the page or the MCP lost the money"
    # Each inherited part keeps its own observation time, not the newer row's.
    assert got["page"]["money_at"] == iso(T_FULL)
    assert got["page"]["bank_at"] == iso(T_FULL)
    last = cov_iso(T_PART + 60 * (n - 1))
    want = {"gear": {"items": cov_iso(T_FULL), "bags": last, "bank": cov_iso(T_FULL)},
            "polestar": {"money_copper": cov_iso(T_FULL)}}
    assert got["coverage"] == want, "the page's coverage"
    assert got["mcp"]["coverage"] == want, "the MCP's coverage"
    assert got["prep_coverage"] == want, "the analysis' coverage"
