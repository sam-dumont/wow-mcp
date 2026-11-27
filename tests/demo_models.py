"""The nine demo models, as served by the checkout's Compose UI and the published viewer."""

import hashlib
import json
import sys
import time
from urllib.request import urlopen

from tools.fixtures.generate import DEMO
from tools.fixtures.golden3d import GOLDEN_FILE
from tools.fixtures.roster import CHARACTERS
from wowstats import character3d

ROSTER = {DEMO: ("PRIEST", "Gnome", "male"),
          **{c.name: (c.cls, c.race, "female" if c.sex == 3 else "male") for c in CHARACTERS}}
PET = {"HUNTER": (1, "Show pet"), "WARLOCK": (2, "Show demon")}
LEVEL = {DEMO: 46, **{c.name: c.level for c in CHARACTERS}}
# Mount and MountXDisplay in the same build. Each rider starts on foot.
MOUNT = {"PALADIN": ("Summon Warhorse", 126407, (126406,)),
         "WARRIOR": ("Swift Timber Wolf", 123410, (123435, 123390, 123436))}
# FileDataIDs from the installed Forever 1.60.1.71139 client's race/sex
# mappings and CreatureDisplayInfo (bear 822, voidwalker 1132).
BODY = {"Gnome": 900914, "Orc": 949470, "Human": 1000764, "Dwarf": 878772, "Skyborne": 7478494,
        "Tauren": 968705, "Troll": 1018060, "Undead": 959310, "Night Elf": 974343}
COMPANION = {"HUNTER": (123040, 123045), "WARLOCK": (126369, 126370)}
# Upper torso texture of the equipped chest piece, and each ranged weapon's subclass, model and skin.
CHEST = {"PRIEST": 164764, "WARRIOR": 164188, "PALADIN": 164194, "HUNTER": 163257, "ROGUE": 163198,
         "SHAMAN": 163226, "MAGE": 164574, "WARLOCK": 162901, "DRUID": 163261}
RANGED = {"PRIEST": (19, 148652, 147512), "WARRIOR": (2, 145015, 145016), "HUNTER": (3, 145323, 145327),
          "ROGUE": (2, 145105, 145104), "MAGE": (19, 148699, 148701), "WARLOCK": (19, 148652, 147644)}
# SHA-256 of each source M2. A well-formed fixture M2 at the same ID must
# fail both the Compose and published demo checks.
CLIENT_M2 = {
    900914: "d4dd4a7d0ffa28426bc55e2483a9147992f931e3a15b497c3fe237d084f888f4",
    949470: "3c0423e92d75da7fb0197239afa4b968689b6b62264c37bb4b736c5b40f81980",
    1000764: "6081cc9e09668e99b2f04e54dbdf110a8ae57c831c5d151ef620caffd60ac061",
    878772: "0b379c17271a628dbbd092d4568962c610fd4ad104d4bb11ae172dbf83b798aa",
    7478494: "2581f6fbea23a39636d056ef24c8505a871727644152faacd518449e873a9655",
    968705: "fb4d9cdbe533087bea299ad44a81f0239c5814afbe7ed933faf831339b4e40eb",
    1018060: "860b633a64e5eafc386c2b84aad3e78068f4a8f19d0017719ceaf73e26ad4eed",
    959310: "80e23753481fb44c282277a6ac6f8708c99c6551541cba87df23903dc96335bc",
    974343: "f57fd4ae36af73e22801904393186ac89d08ded9b0b733c4100c80696cc6716b",
    123040: "fb2f6d821d2e8a1861816fa8134a8c5022e948f423e14df0cda16e9b79706940",
    126369: "e8f0a71147963e3856dab00625f2e43832c4762787a89116a9cd2371f8cd1bb9",
    123410: "08729bd55e214720ee7011931d58d96988a716952b5ea76731535a8b7416820e",
    126407: "0c843692a72f43f4ed2f03fec09e526050eedab318cbff76fc4962ba620c40c9",
}


def check_m2(fdid, stream):
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest == CLIENT_M2[fdid], (fdid, digest, CLIENT_M2[fdid])


def check_answer(name, answer):
    cls, race, sex = ROSTER[name]
    assert (answer["class"], answer["race"], answer["sex"]) == (cls, race, sex), (name, answer.get("why"))
    recipe = answer["recipe"]
    assert recipe and recipe["model"] == BODY[race], (name, recipe and recipe["model"], BODY[race])
    assert recipe["level"] == LEVEL[name], name
    mounts = [(m["name"], m["model"], tuple(m["textures"][k] for k in sorted(m["textures"]) if m["textures"][k]))
              for m in recipe.get("mounts", [])]
    assert mounts == ([MOUNT[cls]] if cls in MOUNT else []), (name, mounts)
    assert not answer["missing"], (name, answer["missing"])
    assert {5, 7, 8, 16} <= {item["slot"] for item in answer["items"]}, name
    # The gear atlas uses slot layer * 100 + body section. Check the body
    # is dressed on at least the upper and lower torso and the legs.
    layers = [layer for mat in recipe["materials"] for layer in mat["layers"] if layer["target"] >= 1000]
    assert {3, 4, 5} <= {layer["target"] % 100 for layer in layers}, name
    assert any(layer["target"] == 1303 and layer["file"] == CHEST[cls] for layer in layers), (name, CHEST[cls])
    if cls == "PALADIN":
        # Shining Mithril covers her arms and waist. Centurion Legplates cover her thighs.
        for style in (recipe, answer["recipe_sd"]):
            textures = {(layer["target"], layer["file"]) for mat in style["materials"] for layer in mat["layers"]}
            assert {(1300, 152047), (1301, 150193), (1304, 161513), (1005, 158821)} <= textures, name
    assert max(character3d.needs(recipe)) < GOLDEN_FILE, (name, "synthetic model or texture")
    pet = recipe.get("companion") or {}
    assert pet.get("kind") == (PET[cls][0] if cls in PET else None), (name, pet)
    if cls in COMPANION:
        assert (pet["model"], pet["textures"]["11"]) == COMPANION[cls], (name, pet)
    assert any(a["slot"] == 16 for a in recipe["attachments"]), name
    kind = (recipe.get("ranged") or {}).get("kind")
    ranged = [(kind, a["model"], a["textures"]["2"]) for a in recipe["attachments"] if a["slot"] == 18]
    assert ranged == ([RANGED[cls]] if cls in RANGED else []), (name, ranged)
    return recipe


def open_before(url, deadline, timeout):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("character check took over 120s")
    return urlopen(url, timeout=min(timeout, remaining))  # noqa: S310 - CI's local UI


def check_live(base):
    models = set()
    for name in ROSTER:
        # A cold character page can take over 30s on the CI runner. The seed
        # also imports its profiles last. Give each character its own retry window.
        deadline = time.monotonic() + 120
        while True:
            try:
                with open_before(f"{base}/api/model3d?name={name}", deadline, 60) as response:
                    recipe = check_answer(name, json.load(response))
                for part in (recipe, recipe.get("companion"), *recipe.get("mounts", [])):
                    if part:
                        with open_before(f"{base}/model3d/file/{part['model']}", deadline, 30) as response:
                            check_m2(part["model"], response)
            except (AssertionError, KeyError, OSError, TypeError) as exc:
                if time.monotonic() + 2 >= deadline:
                    raise AssertionError(f"{name}: {exc}") from exc
                time.sleep(2)
                continue
            models.add(recipe["model"])
            print(f"{name}: model {recipe['model']}", flush=True)
            break
    assert len(models) == len(ROSTER) == 9, models


if __name__ == "__main__":
    check_live(sys.argv[1])
