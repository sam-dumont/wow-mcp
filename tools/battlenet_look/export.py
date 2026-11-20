"""Writes data/battlenet-look.json: the geosets each Battle.net look choice
shows, for the options Forever's own tables don't have.

Battle.net reports a character's look in the current game's ids (a gnome's
"Beard" 84 and "Mustache" 783, say), while Forever's client merged some of
them into one option of its own ("Facial Hair" 9486, each choice a beard and
a mustache). Neither the player's install nor its hotfix cache has the
current game's rows, so wowstats.character3d reads this table: a choice id
to the (geoset type, geoset id) pairs it shows, and draws the Forever choice
showing the same geosets.

Source: the current game's ChrCustomizationOption, Choice, Element and
Geoset tables, and Forever's ChrModel and ChrCustomizationOption, as
wago.tools exports them (its CSV downloads, which its robots.txt allows),
at the builds named below and in the file. Only numbers are kept: choices
of options on a model Forever has, missing from Forever, that show a
geoset. Elements tied to another choice are left out (they depend on the
rest of the look). Game data © Blizzard Entertainment, used as facts.

Stdlib only, the same file byte for byte on every run:

    uv run python tools/battlenet_look/export.py --retail 12.1.5.70961 --forever 1.60.1.70959
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path

WAGO = "https://wago.tools/db2/{table}/csv?version={build}"
OUT = Path(__file__).resolve().parents[2] / "data" / "battlenet-look.json"


def table(name: str, build: str) -> list[dict]:
    req = urllib.request.Request(WAGO.format(table=name, build=build), headers={"User-Agent": "wow-stats export"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        raise SystemExit(f"wago.tools has no {name} rows for {build}")
    return rows


def i(v) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def build_table(retail: dict[str, list[dict]], forever: dict[str, list[dict]]) -> dict[str, list[list[int]]]:
    """choice id (text, JSON keys) -> sorted [[geoset type, geoset id], ...]."""
    models = {i(r["ID"]) for r in forever["ChrModel"]}
    have = {i(r["ID"]) for r in forever["ChrCustomizationOption"]}
    options = {i(r["ID"]) for r in retail["ChrCustomizationOption"]
               if i(r["ChrModelID"]) in models and i(r["ID"]) not in have}
    choices = {i(r["ID"]) for r in retail["ChrCustomizationChoice"] if i(r["ChrCustomizationOptionID"]) in options}
    geosets = {i(r["ID"]): (i(r["GeosetType"]), i(r["GeosetID"])) for r in retail["ChrCustomizationGeoset"]}
    out: dict[int, set[tuple[int, int]]] = {}
    for r in retail["ChrCustomizationElement"]:
        c, g = i(r["ChrCustomizationChoiceID"]), i(r["ChrCustomizationGeosetID"])
        if c in choices and g in geosets and not i(r["RelatedChrCustomizationChoiceID"]):
            out.setdefault(c, set()).add(geosets[g])
    return {str(c): [list(g) for g in sorted(gs)] for c, gs in sorted(out.items())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--retail", required=True, help="the current game's build, as wago.tools names it")
    ap.add_argument("--forever", required=True, help="Forever's build")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    retail = {n: table(n, a.retail) for n in ("ChrCustomizationOption", "ChrCustomizationChoice",
                                               "ChrCustomizationElement", "ChrCustomizationGeoset")}
    forever = {n: table(n, a.forever) for n in ("ChrModel", "ChrCustomizationOption")}
    doc = {"source": "wago.tools CSV exports of the current game's and Forever's client tables",
           "retail_build": a.retail, "forever_build": a.forever, "choices": build_table(retail, forever)}
    a.out.write_text(json.dumps(doc, indent=0, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"{a.out}: {len(doc['choices'])} choices")


if __name__ == "__main__":
    main()
