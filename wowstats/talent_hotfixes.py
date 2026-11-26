"""Balance changes in Forever client 1.60.1.71139, DBCache push 114344 (2026-11-25).
The public DB2 files predate the push. Only identifiers, numerical observations
and our descriptions travel here. The game cache and Blizzard's prose stay local.
"""

from dataclasses import dataclass

BUILD = "1.60.1.71139"
SOURCE = "https://us.forums.blizzard.com/en/wow/t/2403040/16"
PUSH = 114344
STARFIRE_REFUND = ("Blizzard refunded every talent point for druids with at least three points in Improved Starfire. "
                   "This Battle.net reading predates the refund. The next allocation is unknown.")


@dataclass(frozen=True)
class Talent:
    spell: str
    name: str
    classic: str | None
    text: str
    # Each column supplies a number in text, one value per rank. A single-rank talent needs no column.
    values: tuple[tuple[float, ...], ...] = ()
    previous_name: str = ""

    @property
    def max_rank(self) -> int:
        return len(self.values[0]) if self.values else 1

    def description(self, rank: int | None = None) -> str:
        return self.text.format(*(f"{col[rank - 1]:g}" if rank else "/".join(f"{v:g}" for v in col)
                                  for col in self.values))


# TraitDefinition IDs stay stable across the renames and even the replacement at 134397.
# Curves 85324, 108163 (Wrath); 85327, 129045 (Moon and Stars); 109039 (Starfire);
# 129042, 108156 (Eclipse); 85335 (Dreamstate); 85325 (Genesis).
BALANCE = {
    134384: Talent("16814", "Starlight Wrath", "762",
                   "Wrath and Starfire cast {} seconds faster. Wrath costs {}% less mana.",
                   ((.1, .2, .3, .4, .5), (10, 20, 30, 40, 50)), "Improved Wrath"),
    134392: Talent("16821", "Moon and Stars", "763",
                   "Moonfire gains {}% damage and critical strike chance. When hit while casting Starfire, "
                   "the druid has a {}% chance to avoid pushback.", ((5, 10), (50, 100)), "Improved Moonfire"),
    134394: Talent("16850", "Improved Starfire", "784",
                   "Starfire has a {}% chance to stun the target for 3 seconds.", ((8, 15),)),
    134396: Talent("408248", "Eclipse", None,
                   "Wrath gains {}% critical strike chance. A Wrath cast shortens the next two Starfires "
                   "by {} seconds. Up to four charges last 15 seconds.", ((7, 13, 20), (.17, .33, .5))),
    134397: Talent("408258", "Dreamstate", "790", "Casting Innervate on another target gives the druid "
                   "{}% of its effect.", ((20, 40, 60, 80, 100),)),
    134398: Talent("24858", "Moonkin Form", "793",
                   "Moonkin gains 10% Arcane and Nature damage, 360% armour from gear and a 100% increase "
                   "in Omen of Clarity proc chance. Allies within 45 yards gain 3% critical strike chance. "
                   "The form prevents healing spells and Polymorph. Shifting breaks roots, slows and Polymorph."),
    134385: Talent("1223081", "Genesis", None,
                   "Periodic spell and ability damage and healing increase by {}%. Hurricane damage and "
                   "Tranquility healing both receive the bonus.", ((2, 4, 6, 8, 10),)),
}


def apply_nodes(cat: dict) -> None:
    """Patch the trait identities before the catalog looks up the spells and icons."""
    for tree in cat["trees"]:
        if tree.get("class") != "DRUID":
            continue
        for section in tree["sections"]:
            for node in section["nodes"]:
                definition = next((d for d in node["defs"] if d in BALANCE), None)
                if definition is None:
                    continue
                fix = BALANCE[definition]
                if fix.spell not in node["spells"]:
                    # Moonfury was replaced, not renamed. A record of buying Moonfury is not a Dreamstate purchase.
                    node["ranks"] = []
                node.update(spells=[fix.spell], max=fix.max_rank, hotfix=definition)


def upgrade_cache(cat: dict) -> None:
    """Keep a pre-hotfix catalog usable offline, including the replacement spell's icon."""
    apply_nodes(cat)
    spells = cat["spells"]
    if any(t.get("class") == "DRUID" for t in cat["trees"]):
        spells.setdefault("408258", {"icon": "ability_druid_dreamstate"})
        for fix in BALANCE.values():
            if fix.spell in spells:
                spells[fix.spell]["name"] = fix.name


def game_docs(docs: list[dict]) -> list[dict]:
    """Correct the spell/legacy Talent records and add the Forever-only trait records."""
    replacements = {}
    for definition, fix in BALANCE.items():
        talent_id = f"talent:{fix.classic}" if fix.classic else f"talent:trait-{definition}"
        former_name = f" Formerly {fix.previous_name}." if fix.previous_name else ""
        body = f"Druid Balance, {fix.max_rank} rank{'s' if fix.max_rank != 1 else ''}.{former_name} {fix.description()}"
        for key, kind in ((talent_id, "talent"), (f"spell:{fix.spell}", "spell")):
            replacements[key] = (kind, fix.name, body)
    # The Classic Talent rows still identify the old individual rank spells. Other live spells
    # (including item bonuses) share some of their names, so only join by spell ID.
    for definition, spells in ((134384, (16815, 16816, 16817, 16818)), (134392, (16822, 16823, 16824, 16825)),
                               (134394, (16923, 16924, 16925, 16926))):
        fix = BALANCE[definition]
        for rank, spell in enumerate(spells, 2):
            if rank <= fix.max_rank:
                replacements[f"spell:{spell}"] = ("spell", fix.name, f"Rank {rank}. {fix.description(rank)}")
            else:
                replacements[f"spell:{spell}"] = ("spell", f"{fix.name} (retired rank)",
                                                  f"Former rank {rank}. {fix.name} now has {fix.max_rank} ranks.")
    for spell in (16896, 16897, 16899, 16900, 16901):
        replacements[f"spell:{spell}"] = ("spell", "Moonfury (retired)", "Dreamstate replaced Moonfury in Balance.")
    out = [doc for doc in docs if doc["id"] not in replacements]
    return out + [{"id": k, "kind": kind, "title": name, "body": text, "source": SOURCE, "url": SOURCE,
                   "date": "2026-11-25", "label": "game data"} for k, (kind, name, text) in replacements.items()]
