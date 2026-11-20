"""The gear fit model against published gear tables (tools/gearfit_check.py,
tests/data/gearfit-refs.json: item IDs, ranks and source URLs only).

The floors are today's agreement less a margin: a model change that drops
below them is read against the misses (`uv run python
tools/gearfit_check.py --misses`), never tuned to the tables. The known
disagreements and the decision on each are in docs/gear-reference.md
("Checked against published gear tables")."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import gearfit_check  # noqa: E402

DOC = json.loads((ROOT / "tests" / "data" / "gearfit-refs.json").read_text(encoding="utf-8"))


def test_the_fixture_holds_ids_ranks_and_urls_only():
    keys = {"item_id", "slot", "bracket", "rank", "source_url", "scope", "variant"}
    for specs in DOC["specs"].values():
        for rows in specs.values():
            for r in rows:
                assert set(r) <= keys and r["source_url"].startswith("https://") and r["scope"] in ("forever", "era")


def test_agreement_with_the_reference_tables():
    res = gearfit_check.check(DOC)
    specs = res["specs"]
    assert len(specs) == 27
    n = sum(s["n"] for s in specs.values())
    assert n > 700
    assert sum(s["built"] for s in specs.values()) / n >= 0.80
    assert sum(s["top3"] for s in specs.values()) / n >= 0.90
    for name, s in specs.items():
        assert s["n"] >= 10, name
        assert s["built"] / s["n"] >= 0.55, name
        assert s["top3"] / s["n"] >= 0.60, name
    assert all(t["flagged"] for t in res["traps"]), [t for t in res["traps"] if not t["flagged"]]
