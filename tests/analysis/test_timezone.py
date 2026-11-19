"""WOW_TIMEZONE: the analysis' local hours follow the player's timezone."""

import subprocess
import sys


def zone(value: str | None) -> str:
    env = {"PATH": "/usr/bin:/bin"} | ({"WOW_TIMEZONE": value} if value is not None else {})
    code = "from wowstats.analysis import prep; print(prep.TZ_NAME, prep.TZ.key)"
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip() + ("|warned" if "not a known timezone" in r.stderr else "")


def test_default_set_and_unknown():
    assert zone(None) == "Europe/Brussels Europe/Brussels"
    assert zone("America/New_York") == "America/New_York America/New_York"
    assert zone("Not/AZone") == "UTC UTC|warned"
