"""The nine demo models, as served by the checkout's Compose UI and the published viewer."""

import json
import sys
import time
from urllib.request import urlopen

from tools.fixtures.generate import DEMO
from tools.fixtures.roster import CHARACTERS

ROSTER = {DEMO: ("PRIEST", "Gnome", "male"),
          **{c.name: (c.cls, c.race, "female" if c.sex == 3 else "male") for c in CHARACTERS}}
PET = {"HUNTER": (1, "Show pet"), "WARLOCK": (2, "Show demon")}


def check_answer(name, answer):
    cls, race, sex = ROSTER[name]
    assert (answer["class"], answer["race"], answer["sex"]) == (cls, race, sex), (name, answer.get("why"))
    recipe = answer["recipe"]
    assert recipe and recipe["model"], (name, answer.get("why"))
    pet = recipe.get("companion") or {}
    assert pet.get("kind") == (PET[cls][0] if cls in PET else None), (name, pet)
    if cls == "PRIEST":
        assert recipe["ranged"]["kind"] == 19 and any(a["slot"] == 18 for a in recipe["attachments"]), name
    return recipe


def check_live(base):
    # The seed imports profiles after the events. Give the live UI time to
    # see those samples and replace any answers it built during ingestion.
    pending, models = dict.fromkeys(ROSTER), set()
    deadline = time.monotonic() + 120
    while pending:
        for name in list(pending):
            try:
                with urlopen(f"{base}/api/model3d?name={name}", timeout=30) as response:  # noqa: S310
                    recipe = check_answer(name, json.load(response))
                for part in (recipe, recipe.get("companion")):
                    if part:
                        with urlopen(f"{base}/model3d/file/{part['model']}", timeout=30) as response:  # noqa: S310
                            assert response.read(4) == b"MD21", (name, part["model"])
            except (AssertionError, KeyError, OSError, TypeError) as exc:
                pending[name] = str(exc)
                continue
            models.add(recipe["model"])
            print(f"{name}: model {recipe['model']}", flush=True)
            del pending[name]
        if pending:
            assert time.monotonic() < deadline, pending
            time.sleep(2)
    assert len(models) == len(ROSTER) == 9, models


if __name__ == "__main__":
    check_live(sys.argv[1])
