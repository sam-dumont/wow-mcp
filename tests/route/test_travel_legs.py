"""Polestar 0.4.3 moved the race packs into two faction directories."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

DUMP = Path(__file__).resolve().parents[2] / "tools/route_reference/dump.lua"
RACES = {"Alliance": ["Dwarf", "Gnome", "Human", "NightElf", "Skyborne"],
         "Horde": ["Orc", "Scourge", "Skyborne", "Tauren", "Troll"]}


@pytest.mark.parametrize("modern", [False, True])
def test_complete_leg_layout_and_missing_pack(tmp_path, modern):
    lua = shutil.which("lua5.4") or shutil.which("lua")
    if not lua:
        pytest.skip("no Lua interpreter")
    for side, races in RACES.items():
        for race in races:
            path = tmp_path / (f"Polestar_Guides_{side}/Legs_{race}.lua" if modern else f"Polestar_Guides_{race}/Legs.lua")
            path.parent.mkdir(exist_ok=True)
            with path.open("a") as f:
                if modern:
                    f.write(f'if not Polestar.IsPlayerRace("{race}") then return end\n')
                f.write(f'Polestar.RegisterLegs("guides", "", {{["{side}-{race}"]={{[1]="step"}}}})\n')
    args = [lua, str(DUMP), str(tmp_path), "legs"]
    legs = json.loads(subprocess.check_output(args))["legs"]
    assert len(legs) == 10 and {"Horde-Skyborne", "Alliance-Skyborne"} <= legs.keys()
    path.write_text("-- empty")
    assert "registered no travel legs" in subprocess.run(args, capture_output=True, text=True).stderr
    path.unlink()
    failed = subprocess.run(args, capture_output=True, text=True)
    assert failed.returncode and str(path) in failed.stderr
