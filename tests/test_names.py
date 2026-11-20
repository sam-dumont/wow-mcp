"""Character names become file names in the static bundles: a name from an
event must stay one file name, and every bundle write stays in its bundle
(outside review, 2026-11-19: a crafted name wrote outside the player's
bundle)."""

import importlib.util
import json
from pathlib import Path

import pytest

from wowstats import ingest, names, publish

ROOT = Path(__file__).resolve().parents[1]

GOOD = ["Testpriest", "Testpriest-Fixture", "Testpriest Fixture", "Zoë", "Ñandú", "Kel'Thuzad", "Testhunter-Living Flame",
        "Ægir", "Øystein", "charid-3f2a0"]
BAD = ["", " ", "..", ".", "../victim", "..%2fvictim", "%2e%2e%2fvictim", "%2E%2E%2F", "/etc/passwd",
       "C:\\Windows", "a\\..\\b", "Grim\x00ble", "Grim/ble", "Grim\nble", "\u2215victim",  # division slash
       "\uff0e\uff0e\uff0fvictim",  # fullwidth ../
       "\u2024\u2024\u2215victim",  # one dot leaders + division slash
       "Grim\u202eelb",  # right-to-left override
       "Testpriest.json", "x" * 65, "1Testpriest", "\uff11\uff12", "-Testpriest", "'Testpriest", "Test%priest", None, 12, ["Testpriest"]]


@pytest.mark.parametrize("name", GOOD)
def test_real_names_are_valid(name):
    assert names.valid_char(name)


@pytest.mark.parametrize("name", BAD)
def test_crafted_names_are_refused(name):
    assert not names.valid_char(name)


def load_shipper():
    spec = importlib.util.spec_from_file_location("wow_stats_shipper", ROOT / "shipper" / "wow_stats.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", GOOD + BAD)
def test_the_shipper_applies_the_same_rule(name):
    # The shipper is one standalone file (no wowstats import): its copy of
    # the rule must agree with the app's.
    assert load_shipper().valid_char(name) is names.valid_char(name)


@pytest.mark.parametrize("name", BAD[:-3])
def test_ingest_refuses_an_event_with_a_crafted_name(name):
    ev = {"_time": "2026-11-17T15:00:00Z", "feed": "wow-recorder", "char": name, "kind": "xp"}
    lines, bad = ingest.parse(json.dumps(ev).encode())
    assert (lines, bad) == ([], 1)


def test_ingest_keeps_a_real_name_and_events_without_one():
    evs = [{"_time": "2026-11-17T15:00:00Z", "feed": "wow-recorder", "char": "Zoë", "kind": "xp"},
           {"_time": "2026-11-17T15:00:00Z", "feed": "wow-prices", "item": "Wool Cloth"}]
    lines, bad = ingest.parse(b"\n".join(json.dumps(e).encode() for e in evs))
    assert bad == 0 and len(lines) == 2


def test_the_shipper_never_spools_a_crafted_name(capsys):
    mod = load_shipper()
    out = mod.Out(dry=True)
    out.emit({"_time": "2026-11-17T15:00:00Z", "feed": "wow-recorder", "char": "../victim", "kind": "xp"})
    out.emit({"_time": "2026-11-17T15:00:00Z", "feed": "wow-recorder", "char": "Testpriest", "kind": "xp"})
    printed = capsys.readouterr().out
    assert "victim" not in printed and "Testpriest" in printed


ESCAPES = ["../x.json", "data/../../x.json", "data/char/..%2F..%2F..%2Fx.json", "data/char/%2e%2e%2f%2e%2e%2fx.json",
           "/etc/x", "%2Fetc%2Fx", "data/a\\..\\..\\x", "data/a%5C..%5C..%5Cx", "data/x\x00.json", "data/x%00.json",
           "data/char/%2e%2e", ""]


@pytest.mark.parametrize("rel", ESCAPES)
def test_no_bundle_write_leaves_the_bundle(tmp_path, rel):
    root = tmp_path / "site" / "b1" / "public"
    b = publish.Bundle(root)
    with pytest.raises(publish.PublishError):
        b.put(rel, b"owned")
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_bundle_writes_inside_still_work(tmp_path):
    b = publish.Bundle(tmp_path / "public")
    b.put("data/char/Zo%C3%AB.json", b"{}")
    b.put("model3d/file/100001", b"x")
    assert (tmp_path / "public" / "data" / "char" / "Zoë.json").read_bytes() == b"{}"
