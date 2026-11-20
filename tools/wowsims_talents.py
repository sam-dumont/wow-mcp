"""Regenerate data/wowsims-talents.json from WowSimsExporter's Forever table.

WowSimsExporter (wowsims/exporter, MIT) saves a character's talents as the
wowsims sim's positional string: digit i of tree t is the i-th talent of tree
t in wowsims/forever's ui/sim/talents/trees/<class>.json, which the addon
maps to Forever's TraitDefinition IDs in Flavors/Forever/Talents.lua
(between its GENERATED markers). The shipper's talent events carry that
string; the server reads it back to talents with this table.

Usage: uv run python tools/wowsims_talents.py <path to a wowsims/exporter checkout>
Rewrites data/wowsims-talents.json. Rerun it when the addon's table moves
(its "Source: wowsims/forever@..." line changes): a string saved by a newer
addon whose trees grew is read up to what this table knows, and a digit past
the end makes the reading unreadable rather than wrong (wowstats.wowsims).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "wowsims-talents.json"


def parse(lua: str) -> tuple[str, dict[str, list[list[int]]]]:
    block = lua.split("-- BEGIN GENERATED TALENTS", 1)[1].split("-- END GENERATED TALENTS", 1)[0]
    source = re.search(r"-- Source: (\S+)", block)
    classes: dict[str, list[list[int]]] = {}
    for m in re.finditer(r"\n    ([A-Z]+) = \{(.*?)\n    \},", block, re.S):
        classes[m.group(1)] = [[int(x) for x in re.findall(r"\d+", row)]
                               for row in re.findall(r"\{([\d,\s]+)\}", m.group(2))]
    return (source.group(1) if source else "unknown"), classes


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    repo = Path(argv[1])
    source, classes = parse((repo / "Flavors" / "Forever" / "Talents.lua").read_text(encoding="utf-8"))
    commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip() or "unknown"
    if len(classes) != 9 or any(len(t) != 3 for t in classes.values()):
        print(f"unexpected table: {sorted(classes)}", file=sys.stderr)
        return 1
    OUT.write_text(json.dumps({"about": "WowSimsExporter's Forever talent order (Flavors/Forever/Talents.lua): "
                                        "class -> three trees -> TraitDefinition IDs, digit order of its "
                                        "talent string. MIT, THIRD_PARTY.md.",
                               "exporter": f"wowsims/exporter@{commit}", "sim": source, "classes": classes},
                              indent=1) + "\n", encoding="utf-8")
    print(f"{OUT}: {sum(len(r) for t in classes.values() for r in t)} talents, {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
