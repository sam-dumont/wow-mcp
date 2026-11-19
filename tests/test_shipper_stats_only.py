"""`wtf_archive = false`: stats only. No git copy of the WTF folder; the
events are still extracted from the live folder, spooled and kept."""

import shutil

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="no git")

CHAR = (
    'PolestarDB = {\n["alts"] = {\n["chars"] = {\n["Demo-Player"] = {\n["name"] = "Demo Player",\n'
    '["seen"] = 1794900000,\n["level"] = 12,\n["xp"] = 10,\n["xpMax"] = 100,\n["class"] = "PRIEST",\n'
    '["race"] = "Gnome",\n["faction"] = "Alliance",\n},\n},\n},\n}\n'
)


def setup(shipper, tmp_path, monkeypatch, conf: str):
    wow = tmp_path / "wow"
    sv = wow / "WTF" / "Account" / "DEMO" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Polestar.lua").write_text(CHAR)
    (wow / "WTF" / "Config.wtf").write_text("SET x 1\n")
    (tmp_path / "config.toml").write_text(f"wow_dir = '{wow}'\narchive_dir = '{tmp_path / 'arch'}'\n"
                                          f"log_file = '{tmp_path / 'log'}'\n{conf}")
    monkeypatch.setenv("WOW_STATS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(shipper, "QUIET_S", 0)
    monkeypatch.setattr(shipper.sys, "argv", ["wow_stats.py", "--no-ship"])
    return tmp_path / "arch"


def test_default_keeps_the_wtf_history(shipper, tmp_path, monkeypatch):
    arch = setup(shipper, tmp_path, monkeypatch, "")
    assert shipper.main() == 0
    assert (arch / "wtf" / ".git").is_dir() and (arch / "wtf" / "Config.wtf").is_file()
    assert list((arch / "spool").glob("*.ndjson"))


@pytest.mark.parametrize("value", ["false", '"off"'])
def test_stats_only_extracts_without_copying_the_folder(shipper, tmp_path, monkeypatch, value):
    arch = setup(shipper, tmp_path, monkeypatch, f"wtf_archive = {value}\n")
    assert shipper.main() == 0
    assert not (arch / "wtf" / ".git").exists() and not (arch / "wtf" / "Config.wtf").exists()
    spooled = list((arch / "spool").glob("*.ndjson"))
    assert spooled and '"char":"Demo-Player"' in spooled[0].read_text().replace(" ", "")
