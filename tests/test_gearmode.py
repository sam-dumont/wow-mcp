"""The gear mode kept per character on the server (wowstats.gearmode)."""

import json

import pytest

from wowstats import gear, gearmode


def test_saved_mode_per_character(tmp_path):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    assert gearmode.saved(env, "Nibbet-Gearholt") == gear.DEFAULT_MODE
    assert gearmode.save(env, "Nibbet-Gearholt", "PvP") == "pvp"
    assert gearmode.save(env, "Other Name-Gearholt", "raid") == "pve"
    assert gearmode.saved(env, "Nibbet-Gearholt") == "pvp"
    assert gearmode.saved(env, "Other-Name-Gearholt") == "pve"  # a space reads as a hyphen
    assert gearmode.saved(env, "Someone-Else") == "lvl"
    # Leveling is the default: saving it clears the entry.
    assert gearmode.save(env, "Nibbet-Gearholt", "lvl") == "lvl"
    kept = json.loads((tmp_path / gearmode.FILE).read_text())
    assert kept == {"schema": 1, "chars": {"Other-Name-Gearholt": "pve"}}


def test_wow_state_dir_also_keeps_it(tmp_path):
    env = {"WOW_STATE_DIR": str(tmp_path)}
    gearmode.save(env, "Nibbet-Gearholt", "pve")
    assert gearmode.saved(env, "Nibbet-Gearholt") == "pve"


def test_without_a_state_folder_nothing_is_kept():
    with pytest.raises(gearmode.ModeError, match="keeps no settings"):
        gearmode.save({}, "Nibbet-Gearholt", "pvp")
    assert gearmode.saved({}, "Nibbet-Gearholt") == "lvl"


def test_a_bad_mode_or_file_is_refused_or_ignored(tmp_path):
    env = {"ANALYSIS_STATE_DIR": str(tmp_path)}
    with pytest.raises(gearmode.ModeError, match="lvl, pvp or pve"):
        gearmode.save(env, "Nibbet-Gearholt", "arena")
    (tmp_path / gearmode.FILE).write_text("not json")
    assert gearmode.saved(env, "Nibbet-Gearholt") == "lvl"
