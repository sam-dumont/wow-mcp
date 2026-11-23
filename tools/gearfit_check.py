"""Check the gear fit model (wowstats.gearfit) against published gear tables.

The tables are in tests/data/gearfit-refs.json: per class and spec, the
items a guide lists as its pick for a slot and level bracket (item IDs,
ranks and source URLs only, never their text), plus `traps`, items a spec
should see labelled as built for someone else. For each reference item:

- built: does the fit label say it is built for that spec (or fits any)?
  In a levelling bracket, "not made for your spec, fine while levelling"
  (label 2: built for another spec of the class) counts too: the tables
  pick what's best while levelling, and the page shows it, labelled
  (owner, 2026-11-20). `spec` counts how many of them took label 2.
- top3: among every reference item of the same bracket and slot that the
  class can wear (all classes' lists together), does the spec's own pick
  score in the top 3 with that spec's weights?

and each trap must come out "spec" or "class", with half its stats wasted, or as a
lower armour type with the tip that its best wearers roll first.

    uv run python tools/gearfit_check.py            # the table, Markdown
    uv run python tools/gearfit_check.py --misses   # and each disagreement

tests/test_gearfit_refs.py runs it offline from the stored file. Refreshing
the tables means collecting them again (the collection notes are in the
file's `about`): only IDs, ranks and URLs go in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wowstats import gear as gm  # noqa: E402
from wowstats import gearfit  # noqa: E402

REFS = ROOT / "tests" / "data" / "gearfit-refs.json"
TOP = 3


def bracket_level(bracket: str, req: int) -> int:
    """The level an item of this bracket is judged at: its own required
    level, inside the bracket."""
    if bracket == "60":
        return 60
    lo, hi = (int(x) for x in bracket.split("-"))
    return min(hi, max(lo, req or lo))


def slot_group(info: dict) -> str:
    inv = info["inv"]
    if inv == 17:
        return "Two-hand"
    if inv in (13, 21):
        return "Main hand"
    if inv in gm.OFFHAND_INV:
        return "Off hand"
    if inv in gm.RANGED_INV:
        return "Ranged"
    return gm.SLOT_NAMES[gm.SLOTS_OF[inv][0]]


def scoreable(ref: gm.Reference, item_id: int) -> dict | None:
    """The item, unless it is missing or a random-suffix base (its stats are
    in the link, so no table can be judged against it)."""
    info = ref.item(item_id)
    if not info or gm.suffix_base(ref, item_id, info):
        return None
    return info


def check(doc: dict, ref: gm.Reference | None = None) -> dict:
    ref = ref or gm.reference()
    pool: dict[tuple, set[int]] = {}
    for specs in doc["specs"].values():
        for rows in specs.values():
            for r in rows:
                info = scoreable(ref, r["item_id"])
                if info:
                    pool.setdefault((r["bracket"], slot_group(info)), set()).add(r["item_id"])
    out: dict = {"specs": {}, "misses": [], "traps": []}
    for cls, specs in doc["specs"].items():
        for key, rows in specs.items():
            n = built = top = spec2 = 0
            scopes = set()
            for r in rows:
                info = scoreable(ref, r["item_id"])
                if not info or r.get("variant"):
                    continue
                lv = bracket_level(r["bracket"], info.get("req") or 0)
                usable, _, min_level = gm.usability(ref, info, cls, lv)
                if not usable or min_level > lv:
                    out["misses"].append({"class": cls, "spec": key, "item_id": r["item_id"], "level": lv,
                                          "why": "not wearable by the class at that level in our data"})
                    continue
                w = gearfit.spec_weights(ref, cls, key)
                f = gearfit.fit(ref, info, cls, key, "picked", lv, w)
                n += 1
                scopes.add(r["scope"])
                ok_built = f["for_you"] in ("yes", "any") or (f["for_you"] == "spec" and r["bracket"] != "60")
                spec2 += f["for_you"] == "spec"
                built += ok_built
                mine = gm.score_with(info, w, info["inv"] in gm.OFFHAND_INV)
                rivals = []
                for other in pool.get((r["bracket"], slot_group(info)), ()):
                    oi = ref.item(other)
                    u, _, ml = gm.usability(ref, oi, cls, lv)
                    if other != r["item_id"] and u and ml <= lv:
                        rivals.append(gm.score_with(oi, w, oi["inv"] in gm.OFFHAND_INV))
                rank = 1 + sum(1 for s in rivals if s > mine + 1e-9)
                top += rank <= TOP
                if not ok_built or rank > TOP:
                    out["misses"].append({"class": cls, "spec": key, "item_id": r["item_id"], "level": lv,
                                          "bracket": r["bracket"], "slot": slot_group(info), "rank": rank,
                                          "of": len(rivals) + 1, "label": gearfit.text(f), "scope": r["scope"],
                                          "built_for": f["built_for"]})
            out["specs"][f"{cls} {key}"] = {"n": n, "built": built, "top3": top, "spec": spec2,
                                            "scopes": sorted(scopes)}
    for t in doc.get("traps") or []:
        info = ref.item(t["item_id"])
        w = gearfit.spec_weights(ref, t["class"], t["spec"])
        f = gearfit.fit(ref, info, t["class"], t["spec"], "picked", t["level"], w)
        # Off-role, mostly wasted, or a lower armour type with the tip that
        # its best wearers roll first.
        flagged = f["for_you"] in ("spec", "class") or f["wasted_pct"] >= 50 or bool(f["armor"] and f["tip"])
        out["traps"].append({**t, "flagged": flagged, "for_you": f["for_you"], "label": gearfit.text(f)})
    return out


def table(res: dict) -> str:
    lines = ["| Spec | Items | Built for the spec | of which label 2 | Our top 3 | Tables |",
             "|---|---:|---:|---:|---:|---|"]
    for spec, s in res["specs"].items():
        if not s["n"]:
            lines.append(f"| {spec.lower()} | 0 | - | - | - | none usable |")
            continue
        lines.append(f"| {spec.lower()} | {s['n']} | {s['built'] / s['n']:.0%} | {s['spec']} | "
                     f"{s['top3'] / s['n']:.0%} | {', '.join(s['scopes'])} |")
    flagged = sum(t["flagged"] for t in res["traps"])
    lines.append(f"\nTraps labelled off-role or wasted: {flagged} of {len(res['traps'])}.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--misses", action="store_true", help="list every disagreement")
    ap.add_argument("--file", type=Path, default=REFS)
    a = ap.parse_args()
    res = check(json.loads(a.file.read_text(encoding="utf-8")))
    print(table(res))
    if a.misses:
        ref = gm.reference()
        for m in res["misses"]:
            name = (ref.data.get("names") or {}).get(str(m["item_id"])) or m["item_id"]
            print(f"- {m['class'].lower()} {m['spec']} {m.get('bracket', '')} {m.get('slot', '')}: {name} "
                  f"rank {m.get('rank')}/{m.get('of')} [{m.get('scope')}] {m.get('label') or m.get('why')}")
        for t in res["traps"]:
            if not t["flagged"]:
                print(f"- TRAP missed: {t['class'].lower()} {t['spec']} {t['item_id']}: {t['label']}")


if __name__ == "__main__":
    main()
