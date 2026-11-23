"""The privacy table (docs/explanation/privacy.md) says what each tier shows: it must name the
same answers as wowstats/redact.py's allow-lists, so a tier that gains or
loses an answer can't leave the table behind. A row may name one field of an
answer (`char.gear.standing`): the public tier's shape must hold it when the
table says Anyone sees it, and must not otherwise."""

import re
from pathlib import Path

from wowstats import redact

README = Path(__file__).resolve().parent.parent / "docs" / "explanation" / "privacy.md"


def _rows(fields: bool = False) -> dict[str, dict[str, str]]:
    """The answers' rows, or with `fields` the rows that name one field."""
    text = README.read_text(encoding="utf-8")
    m = re.search(r"<!-- privacy-table -->\n(.*?)<!-- /privacy-table -->", text, re.S)
    assert m, "docs/explanation/privacy.md lost its privacy table markers"
    rows = {}
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or not (key := re.fullmatch(r"`(\w+(?:\.\w+)*)`", cells[1])):
            continue
        if ("." in key.group(1)) != fields:
            continue
        assert key.group(1) not in rows, f"{key.group(1)} is in the table twice"
        rows[key.group(1)] = {"full": cells[2], "zogzog": cells[3], "public": cells[4]}
    return rows


def _shown(cell: str) -> bool:
    return cell.lower() != "no"


def test_the_privacy_table_matches_the_allow_lists():
    rows = _rows()
    assert {k for k, r in rows.items() if _shown(r["public"])} == set(redact.PUBLIC)
    assert {k for k, r in rows.items() if _shown(r["zogzog"])} == set(redact.ZOGZOG)
    assert all(_shown(r["full"]) for r in rows.values())


def test_the_field_rows_match_the_public_shape():
    rows = _rows(fields=True)
    assert "char.gear.standing" in rows
    for path, r in rows.items():
        what, *keys = path.split(".")
        spec = redact.PUBLIC.get(what)
        for k in keys:
            spec = spec.get(k) if isinstance(spec, dict) else None
        assert (spec is not None) == _shown(r["public"]), path
