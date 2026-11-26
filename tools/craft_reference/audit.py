"""Compare gear recipe gates to a local ForeverLevelingBiS Data/Tables.lua.

Run on the release checkout to repeat the #283 comparison. Each differing
spell gets a row with the acquisition method and the two independent gates
our advice can substantiate. The addon's data stays on the machine.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wowstats import crafting, gear  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tables", type=Path)
    args = parser.parse_args()
    spells = {s["spell"]: s for sources in gear.reference().data["sources"].values() for s in sources
              if s.get("kind") == "craft" and s.get("spell")}
    out = csv.writer(sys.stdout)
    out.writerow(("spell", "profession", "addon_method", "gear_skill", "pattern_skills", "verified_trainer_skill"))
    checked = changed = 0
    for line in args.tables.read_text().splitlines():
        m = re.match(r"\{spell=(\d+),item=\d+,skill=(\d+)(?:,(\w+)=|})", line)
        if not m or not (src := spells.get(int(m[1]))):
            continue
        checked += 1
        if src.get("skill") == int(m[2]):
            continue
        changed += 1
        state = crafting.recipe_state(crafting.line_of(src["profession"]), int(m[1]), crafting.Who())
        pats = crafting.reference()["patterns"]
        out.writerow((m[1], src["profession"], m[3] or "unknown", src.get("skill"),
                      "/".join(str(pats[str(i)][1]) for i in state["patterns"]), state.get("trainer_skill")))
    print(f"{checked} matched spells, {changed} differences", file=sys.stderr)


if __name__ == "__main__":
    main()
