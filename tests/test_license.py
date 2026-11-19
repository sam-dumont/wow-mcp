"""What a binary recipient gets: the GPL's text and the way to the matching source.

The images copy LICENSE (Dockerfile), the wheel carries it, and the one-line
installers (wowstats.account) install it next to the shipper and name the
source tag of the version they carry.
"""

import base64
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

import wowstats
from wowstats import account
from wowstats import tenancy as T

ROOT = Path(__file__).resolve().parent.parent
GPL = (ROOT / "LICENSE").read_bytes()


def test_license_is_the_gpl3():
    assert b"GNU GENERAL PUBLIC LICENSE" in GPL and b"Version 3, 29 June 2007" in GPL


def test_source_url_matches_pyproject():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["project"]["urls"]["Source"] == wowstats.SOURCE_URL
    assert cfg["project"]["license"] == "GPL-3.0-only"
    assert wowstats.source_tag_url() == f"{wowstats.SOURCE_URL}/tree/v{cfg['project']['version']}"


def test_wheel_and_both_builder_stages_carry_the_license():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert cfg["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]["LICENSE"] == "wowstats/data/LICENSE"
    stages = {m.group(1): m.group(2) for m in re.finditer(r"(?ms)^FROM \S+ AS (\S+)\n(.*?)(?=^FROM |\Z)",
                                                          (ROOT / "Dockerfile").read_text())}
    for builder in ("builder", "render-builder"):
        assert re.search(r"(?m)^COPY [^\n]*\bLICENSE\b[^\n]* \./$", stages[builder]), builder
    for final in ("runtime", "renderer"):
        assert re.search(r"(?m)^COPY --from=\S+ /app/LICENSE /app/THIRD_PARTY\.md /app/$", stages[final]), final
        assert 'org.opencontainers.image.licenses="GPL-3.0-only"' in stages[final], final


@pytest.fixture()
def people(tmp_path):
    t = T.Tenancy({"WOW_STATE_DIR": str(tmp_path / "state"), "WOW_OWNER_SUB": "owner-sub",
                   "WOW_OWNER_SLUG": "demo", "PUBLIC_URL": "https://wow.example.org"})
    return t, t.registry.get("owner-sub")


def test_mac_installer_and_update_install_the_license(people, tmp_path):
    t, u = people
    for script in (account.mac_installer(t, u, "x" * 24), account.mac_installer(t, u, None)):
        text = script.decode()
        assert account.source_tag_url() in "\n".join(text.splitlines()[:4])
        body = re.search(r"cat > \"\$tmp/LICENSE\" <<'WOWSTATS_EOF'\n(.*?)\nWOWSTATS_EOF", text, re.S)
        assert body and body.group(1).encode() == GPL.rstrip(b"\n")
    # The stock installer copies it next to the program.
    assert 'install -m 644 "$HERE/LICENSE" "$DEST_DIR/LICENSE"' in (ROOT / "shipper" / "install-mac.sh").read_text()


def test_windows_installer_and_update_install_the_license(people):
    t, u = people
    for script in (account.windows_installer(t, u, "x" * 24), account.windows_installer(t, u, None)):
        text = script.decode()
        assert account.source_tag_url() in "\n".join(text.splitlines()[:4])
        m = re.search(r"Put 'LICENSE' '([A-Za-z0-9+/=]+)'", text)
        assert m and base64.b64decode(m.group(1)) == GPL
    ps = (ROOT / "shipper" / "install-windows.ps1").read_text()
    assert "(Join-Path $Dest 'LICENSE')" in ps


def test_mac_installer_writes_the_license_beside_the_program(people, tmp_path):
    """Run the stock installer from the generated bundle under a throwaway HOME."""
    t, u = people
    text = account.mac_installer(t, u, "x" * 24).decode()
    tmp = tmp_path / "bundle"
    tmp.mkdir()
    for name, body in re.findall(r"cat > \"\$tmp/([^\"]+)\" <<'WOWSTATS_EOF'\n(.*?)\nWOWSTATS_EOF", text, re.S):
        (tmp / name).write_text(body + "\n")
    home = tmp_path / "home"
    wow = home / "wow"
    (wow / "WTF" / "Account" / "DEMO" / "SavedVariables").mkdir(parents=True)
    conf = home / "wow-stats.toml"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"wow_dir = '{wow}'\n")
    if subprocess.run(["bash", "-c", "command -v plutil"], capture_output=True).returncode:
        pytest.skip("needs macOS's plutil")
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "WOW_STATS_CONFIG": str(conf),
           "WOW_STATS_NO_LOAD": "1", "WOW_STATS_RECORDER_URL": "http://127.0.0.1:9/none",
           "WOW_STATS_RECORDER_SHA256": "0" * 64}
    py = shutil.which("python3")
    if py:
        env["PATH"] = str(Path(py).parent) + ":" + env["PATH"]
    r = subprocess.run(["bash", str(tmp / "install-mac.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (home / ".local" / "share" / "wow-stats" / "LICENSE").read_bytes() == GPL
