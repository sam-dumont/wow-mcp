"""The installers put Polestar Recorder in the AddOns folder
(wow_stats.py --install-recorder): the pinned official release, checked
against its SHA-256, at the right folder depth, never over a newer copy, and
never near the game's saved data (WTF/). A fake release zip stands in for
the download."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def release_zip(version: str, extra: dict[str, str] | None = None) -> bytes:
    """A zip shaped like the release's: everything under Polestar_Recorder/."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Polestar_Recorder/", "")
        z.writestr("Polestar_Recorder/Polestar_Recorder.toc",
                   f"## Interface: 16001\n## Title: Polestar Recorder\n## Version: {version}\n"
                   "## SavedVariables: PolestarRecorderDB\nRecorder.lua\n")
        z.writestr("Polestar_Recorder/Recorder.lua", f"-- {version}\n")
        for name, body in (extra or {}).items():
            z.writestr(name, body)
    return buf.getvalue()


@pytest.fixture
def wow(tmp_path):
    """A _forever_ folder with a character's saved data."""
    root = tmp_path / "_forever_"
    sv = root / "WTF" / "Account" / "ACC" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "Polestar_Recorder.lua").write_text("PolestarRecorderDB = { enabled = true }\n")
    return root


def pin(shipper, monkeypatch, data: bytes, version: str = "9.9.9"):
    monkeypatch.setattr(shipper, "RECORDER_VERSION", version)
    monkeypatch.setattr(shipper, "RECORDER_SHA256", hashlib.sha256(data).hexdigest())
    calls = []

    def fetch(url):
        calls.append(url)
        return data

    return fetch, calls


def saved(wow: Path) -> str:
    return (wow / "WTF" / "Account" / "ACC" / "SavedVariables" / "Polestar_Recorder.lua").read_text()


def test_installs_at_the_right_depth_and_leaves_the_saved_data(shipper, monkeypatch, wow):
    data = release_zip("9.9.9")
    fetch, calls = pin(shipper, monkeypatch, data)
    said = shipper.install_recorder(wow, fetch)
    addons = wow / "Interface" / "AddOns"
    assert (addons / "Polestar_Recorder" / "Polestar_Recorder.toc").is_file()  # not .../Polestar_Recorder/Polestar_Recorder/
    assert shipper.recorder_version(addons) == "9.9.9"
    assert "9.9.9 installed" in said and len(calls) == 1
    assert sorted(p.name for p in addons.iterdir()) == ["Polestar_Recorder"]  # no temporary folder left
    assert saved(wow) == "PolestarRecorderDB = { enabled = true }\n"


def test_a_checksum_mismatch_changes_nothing(shipper, monkeypatch, wow):
    old = wow / "Interface" / "AddOns" / "Polestar_Recorder"
    old.mkdir(parents=True)
    (old / "Polestar_Recorder.toc").write_text("## Version: 0.1\n")
    fetch, _ = pin(shipper, monkeypatch, release_zip("9.9.9"))
    tampered = release_zip("9.9.9", {"Polestar_Recorder/evil.lua": "x"})
    with pytest.raises(shipper.RecorderError, match="checksum"):
        shipper.install_recorder(wow, lambda url: tampered)
    assert shipper.recorder_version(old.parent) == "0.1"
    assert sorted(p.name for p in old.parent.iterdir()) == ["Polestar_Recorder"]
    assert saved(wow) == "PolestarRecorderDB = { enabled = true }\n"


@pytest.mark.parametrize("have", ["9.9.9", "9.10.0", "10.0"])
def test_a_same_or_newer_copy_is_kept(shipper, monkeypatch, wow, have):
    mine = wow / "Interface" / "AddOns" / "Polestar_Recorder"
    mine.mkdir(parents=True)
    (mine / "Polestar_Recorder.toc").write_text(f"## Title: Polestar Recorder\n## Version: {have}\n")
    (mine / "Mine.lua").write_text("-- the person's own copy\n")
    fetch, calls = pin(shipper, monkeypatch, release_zip("9.9.9"))
    assert "kept" in shipper.install_recorder(wow, fetch)
    assert calls == []  # not even downloaded
    assert (mine / "Mine.lua").is_file() and shipper.recorder_version(mine.parent) == have


def test_an_older_copy_is_replaced_whole(shipper, monkeypatch, wow):
    old = wow / "Interface" / "AddOns" / "Polestar_Recorder"
    old.mkdir(parents=True)
    (old / "Polestar_Recorder.toc").write_text("## Version: 9.9.8\n")
    (old / "Gone.lua").write_text("-- a file the new release dropped\n")
    fetch, _ = pin(shipper, monkeypatch, release_zip("9.9.9"))
    assert "(was 9.9.8)" in shipper.install_recorder(wow, fetch)
    assert shipper.recorder_version(old.parent) == "9.9.9" and not (old / "Gone.lua").exists()
    assert sorted(p.name for p in old.parent.iterdir()) == ["Polestar_Recorder"]
    assert saved(wow) == "PolestarRecorderDB = { enabled = true }\n"


@pytest.mark.parametrize("bad", ["../escape.lua", "Polestar_Recorder/../../x.lua", "/abs.lua", "Other/x.lua"])
def test_a_zip_reaching_outside_its_folder_is_refused(shipper, monkeypatch, wow, bad):
    data = release_zip("9.9.9", {bad: "x"})
    fetch, _ = pin(shipper, monkeypatch, data)
    with pytest.raises(shipper.RecorderError, match="unexpected path"):
        shipper.install_recorder(wow, fetch)
    assert not (wow / "Interface" / "AddOns" / "Polestar_Recorder").exists()
    assert not (wow.parent / "escape.lua").exists()


def test_no_wow_folder_is_an_error_not_a_new_folder(shipper, tmp_path):
    with pytest.raises(shipper.RecorderError, match="no WoW folder"):
        shipper.install_recorder(tmp_path / "missing", lambda url: b"")
    assert not (tmp_path / "missing").exists()


def test_the_pin_is_one_official_release():
    s = (ROOT / "shipper" / "wow_stats.py").read_text()
    version = re.search(r'^RECORDER_VERSION = "([\d.]+)"$', s, re.M).group(1)
    assert re.search(r'^RECORDER_SHA256 = "[0-9a-f]{64}"$', s, re.M)
    assert "https://github.com/fitzforge/polestar/releases/download/" in s
    assert re.search(r'f"v\{RECORDER_VERSION\}/Polestar_Recorder-\{RECORDER_VERSION\}\.zip"', s)
    assert version.count(".") >= 2


def test_both_installers_add_it_and_go_on_without_it():
    """Install and update both run the stock installers (account.py), so both
    get the Recorder; a failed download never stops the shipper's install."""
    mac = (ROOT / "shipper" / "install-mac.sh").read_text()
    assert '"${prog[@]}" "$SCRIPT" --install-recorder || true' in mac
    win = (ROOT / "shipper" / "install-windows.ps1").read_text()
    call = win.index("& $python $Script --install-recorder")
    assert "$ErrorActionPreference = 'Continue'" in win[call - 80:call]
    from wowstats import account

    assert account.shipper_file("install-mac.sh") == (ROOT / "shipper" / "install-mac.sh").read_bytes()


def test_the_test_only_override_points_at_another_zip(shipper, wow):
    """WOW_STATS_RECORDER_URL/_SHA256 (the Windows end-to-end's fake release)
    reach install_recorder's url and checksum."""
    data = release_zip("0.4.1.1")
    seen = []

    def fetch(url):
        seen.append(url)
        return data

    shipper.install_recorder(wow, fetch, url="http://127.0.0.1/x.zip", sha256=hashlib.sha256(data).hexdigest())
    assert seen == ["http://127.0.0.1/x.zip"]
    assert 'os.environ.get("WOW_STATS_RECORDER_URL")' in (ROOT / "shipper" / "wow_stats.py").read_text()
