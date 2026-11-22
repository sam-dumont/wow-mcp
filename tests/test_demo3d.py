"""tools/fixtures/demo3d.py and site/scripts/demo3d-asset.sh: the demo's real
model, trimmed to Nibbet's rows and fetched from the `demo-3d` release. No
real game file can be in a test: the trim runs on golden3d's tables."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from wowstats import character3d

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "fixtures"))

import demo3d  # noqa: E402
import golden3d  # noqa: E402

OUTFIT = [{"slot": 1, "item_id": 7048}, {"slot": 3, "item_id": 6685}, {"slot": 5, "item_id": 273044},
          {"slot": 6, "item_id": 7000}, {"slot": 15, "item_id": 273045}, {"slot": 16, "item_id": 2816},
          {"slot": 17, "item_id": 279838}, {"slot": 18, "item_id": 7001}]


def _dump(x) -> str:
    return json.dumps(x, sort_keys=True, default=list)


def test_trim_keeps_the_recipes(tmp_path):
    g = golden3d.build()
    rows = {n: g["tables"].get(n, {}) for n in character3d.TABLES}
    sheets = [OUTFIT] + [[i] for i in OUTFIT]
    full = demo3d.answers(character3d.Tables(rows, golden3d.BUILD), g["look"], sheets)
    small = demo3d.trim(rows, g["look"], sheets)
    # Fewer rows (the test helm and shield, the items he doesn't wear here)...
    assert sum(map(len, small.values())) < sum(map(len, rows.values()))
    assert golden3d.TEST_HELM not in small["Item"]
    # ... and the same answers, through the WDC5 files the store keeps.
    (tmp_path / "build.json").write_text(json.dumps({"build": golden3d.BUILD}))
    for name, r in small.items():
        (tmp_path / f"{name}.db2").write_bytes(golden3d.wdc5(name, r))
    again = demo3d.answers(character3d.load_tables(tmp_path), g["look"], sheets)
    assert _dump(again) == _dump(full)


def test_pack_is_the_same_bytes(tmp_path):
    store = tmp_path / "store"
    (store / "files").mkdir(parents=True)
    (store / "files" / "1.blp").write_bytes(b"BLP2" + bytes(12))
    (store / "look.json").write_text("[]\n")
    a = demo3d.pack(store, tmp_path / "a.tar.gz")
    (store / "files" / "1.blp").touch()  # a newer time changes nothing
    b = demo3d.pack(store, tmp_path / "b.tar.gz")
    assert a == b
    assert (tmp_path / "a.tar.gz.sha256").read_text().split()[0] == a


def _fetch(tmp_path, url: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "site" / "scripts" / "demo3d-asset.sh"), str(tmp_path / "dest")],
                          env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "DEMO_3D_URL": url},
                          capture_output=True, text=True, timeout=60)


def test_no_asset_keeps_the_made_up_figure(tmp_path):
    r = _fetch(tmp_path, f"file://{tmp_path}/none.tar.gz")
    assert r.returncode == 0 and r.stdout == ""
    assert "made-up figure" in r.stderr


def test_another_archive_is_refused(tmp_path):
    store = tmp_path / "store"
    (store / "tables").mkdir(parents=True)
    (store / "tables" / "build.json").write_text("{}")
    (store / "look.json").write_text("[]")
    demo3d.pack(store, tmp_path / "other.tar.gz")
    r = _fetch(tmp_path, f"file://{tmp_path}/other.tar.gz")
    assert r.returncode == 0 and r.stdout == ""
    assert "SHA-256" in r.stderr
    assert not (tmp_path / "dest" / "store").exists()


def test_the_pin_is_a_sha256():
    line = (ROOT / "site" / "demo3d.sha256").read_text().split()
    assert len(line[0]) == 64 and int(line[0], 16) >= 0 and line[1] == demo3d.ARCHIVE
