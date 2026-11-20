"""The shipper's CASC reader (shipper/wow_stats.py Casc, casc_files,
--casc-extract) on a made-up install (tests/casc_fixture.py). Never a real
WoW folder."""

import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tests import casc_fixture as cf

ROOT = Path(__file__).resolve().parent.parent

CHAR, SKIN0, SKIN1, TEX, SKEL, PARENT = 1000, 1001, 1002, 1003, 1004, 1005
STAND, WALK, PSTAND = 1006, 1007, 1008
BODY = 2000  # a texture the server asks for by id
ITEM_DB2 = 3000


def install(tmp_path, shipper, write=None, extra=None, **kw) -> Path:
    inst = cf.Install(shipper.jenkins96, **kw)
    if extra:
        extra(inst)
    inst.file(CHAR, cf.m2(skins=(SKIN0, SKIN1), textures=(TEX,), skel=SKEL, anims=((0, 0, 9999), (4, 0, 9998))))
    inst.file(SKIN0, b"SKIN" + b"lod0" * 200)
    inst.file(SKIN1, b"SKIN" + b"lod1" * 200)
    inst.file(TEX, b"BLP2" + bytes(range(256)) * 8)
    inst.file(SKEL, cf.skel(parent=PARENT, anims=((0, 0, STAND), (4, 0, WALK), (0, 1, STAND + 100))))
    inst.file(PARENT, cf.skel(anims=((0, 0, PSTAND),)))
    inst.file(STAND, b"AFM2" + b"stand" * 100)
    inst.file(WALK, b"AFM2" + b"walk" * 100)
    inst.file(PSTAND, b"AFM2" + b"pstand" * 100)
    inst.file(BODY, b"BLP2" + b"body" * 4000)
    inst.file(ITEM_DB2, b"WDC5" + b"rows" * 50, name="DBFilesClient/Item.db2")
    # Another locale's copy of the body texture, and a low-violence one: skipped.
    inst.file(BODY, b"BLP2 french", locale=cf.FRFR)
    inst.file(BODY, b"BLP2 gentle", content=cf.LOW_VIOLENCE)
    inst.file(4000, b"BLP2 encrypted", encoded=b"BLTE" + struct.pack(">I", 0) + b"E" + bytes(40))
    inst.file(4001, b"BLP2 not downloaded", stored=False)
    inst.file(4002, b"BLP2 nameless", content=cf.NO_NAME_HASH)
    inst.file(4003, b"BLP2 four", encoded=cf.blte(b"BLP2 four", blocks=0))
    return inst.write(tmp_path / "World of Warcraft", **(write or {}))


def test_hashlittle2_vectors(shipper):
    # lookup3.c's own self-test (driver5), seeds 0.
    assert shipper.hashlittle2(b"") == (0xDEADBEEF, 0xDEADBEEF)
    assert shipper.hashlittle2(b"Four score and seven years ago") == (0x17770551, 0xCE7226E6)
    # Upper case and backslashes, so both spellings of a path hash the same.
    assert shipper.jenkins96("DBFilesClient/Item.db2") == shipper.jenkins96("DBFILESCLIENT\\ITEM.DB2")


@pytest.mark.parametrize("root_version", [1, 2, "classic"])
def test_reads_files_and_follows_the_model(tmp_path, shipper, root_version):
    casc = shipper.Casc(install(tmp_path, shipper, root_version=root_version))
    assert casc.version == "1.60.1.70959" and casc.locale_name == "enUS"
    got, tables, missing = shipper.casc_files(casc, [CHAR, BODY], ["Item", "NoSuchTable"])
    # The model, its first skin, texture, skeleton, parent skeleton and the
    # standing animations: never the second skin or the walk.
    assert set(got) == {CHAR, SKIN0, TEX, SKEL, PARENT, STAND, PSTAND, BODY, ITEM_DB2}
    assert got[BODY].startswith(b"BLP2body")  # this locale's copy, not the French or low-violence one
    assert tables == {"Item": ITEM_DB2}
    assert missing == {"NoSuchTable": "not in this build", 9999: "not in this build",
                       STAND + 100: "not in this build"}


def test_no_follow_and_missing(tmp_path, shipper):
    casc = shipper.Casc(install(tmp_path, shipper))
    got, _, missing = shipper.casc_files(casc, [CHAR, 4000, 4001, 4002, 4003, 5555], follow=False)
    assert set(got) == {CHAR, 4002, 4003}
    assert got[4003] == b"BLP2 four"
    assert missing == {4000: "encrypted", 4001: "not stored on this computer", 5555: "not in this build"}


def test_limits(tmp_path, shipper):
    casc = shipper.Casc(install(tmp_path, shipper))
    got, _, missing = shipper.casc_files(casc, [CHAR], max_files=2)
    assert len(got) == 2
    assert "over the run's limit" in missing.values()


def test_a_damaged_file_is_missing_not_wrong(tmp_path, shipper):
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    archive, offset, size = casc._locate(casc.ekey(keys[TEX]))
    p = root / "Data" / "data" / f"data.{archive:03d}"
    raw = bytearray(p.read_bytes())
    raw[offset + size - 1] ^= 0xFF  # the last byte of its last block
    p.write_bytes(bytes(raw))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX not in got and "checksum" in missing[TEX]


def test_locale_from_build_info(tmp_path, shipper):
    root = install(tmp_path, shipper, locale="frFR")
    casc = shipper.Casc(root)
    assert casc.locale_name == "frFR"
    got, _, _ = shipper.casc_files(casc, [BODY])
    assert got[BODY] == b"BLP2 french"


def test_no_install(tmp_path, shipper):
    with pytest.raises(shipper.CascError, match="no .build.info"):
        shipper.Casc(tmp_path)
    (tmp_path / ".build.info").write_text("Product!STRING:0\nwow\n", encoding="utf-8")
    with pytest.raises(shipper.CascError, match="no wowf build"):
        shipper.Casc(tmp_path)


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_extract_cli_is_read_only(tmp_path, shipper):
    root = install(tmp_path, shipper)
    before = snapshot(root)
    out = tmp_path / "out"
    ids = tmp_path / "ids.txt"
    ids.write_text(f"{CHAR},\n{BODY}\n")
    env = {**os.environ, "WOW_STATS_CONFIG": str(tmp_path / "none.toml")}
    r = subprocess.run([sys.executable, str(ROOT / "shipper" / "wow_stats.py"), "--casc-extract", str(out),
                        "--wow-root", str(root), "--fdids", f"@{ids}", "--tables", "Item"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert snapshot(root) == before
    man = json.loads((out / "manifest.json").read_text())
    assert man["product"] == "wowf" and man["tables"] == {"Item": ITEM_DB2}
    names = sorted(f["name"] for f in man["files"].values())
    assert "Item.db2" in names and f"{CHAR}.m2" in names and f"{SKIN0}.skin" in names and f"{BODY}.blp" in names
    assert (out / f"{STAND}.anim").read_bytes().startswith(b"AFM2")
    assert "9 files" in r.stderr


@pytest.mark.parametrize("write", [{"offset_bits": 28}, {"encoding_in_older": True}])
def test_index_shapes(tmp_path, shipper, write):
    """The owner's first proof run (2026-11-20) stopped at the encoding
    table's index entry. The entry's split comes from each .idx header, and
    an entry the newest .idx lacks is found in an older one."""
    casc = shipper.Casc(install(tmp_path, shipper, write=write))
    got, _, _ = shipper.casc_files(casc, [CHAR, BODY], follow=False)
    assert set(got) == {CHAR, BODY}


def test_storage_tables_may_pass_the_file_limit(tmp_path, shipper, monkeypatch):
    """The encoding table and the root are far larger than any file the
    view reads (a full client's encoding passed 64 MB): their own limit."""
    monkeypatch.setattr(shipper, "CASC_MAX_FILE", 1000)  # the made-up encoding is 1.1 KB
    casc = shipper.Casc(install(tmp_path, shipper))
    got, _, missing = shipper.casc_files(casc, [SKIN0, BODY], follow=False)
    assert set(got) == {SKIN0} and "too large" in missing[BODY]


def test_diagnose_prints_structure_only(tmp_path, shipper):
    root = install(tmp_path, shipper)
    lines = shipper.casc_diagnose(root)
    text = "\n".join(lines)
    assert lines[0] == f"install: wowf 1.60.1.70959, locale enUS, platform {sys.platform}"
    assert "size/pos/key bytes 4/5/9 offset bits 30" in text and "NOT sorted" not in text
    assert "encoding table: reads" in text and "root: reads" in text
    hit = next(ln for ln in lines if ln.startswith("  in bucket"))
    assert "fits" in hit and "key matches" in hit and "size field" in hit and "matches)" in hit and "BLTE after it: yes" in hit
    assert b"body" not in text.encode() and "BLP2" not in text  # never a file's contents
    r = subprocess.run([sys.executable, str(ROOT / "shipper" / "wow_stats.py"), "--casc-diagnose", "--wow-root", str(root)],
                       capture_output=True, text=True, timeout=60,
                       env={**os.environ, "WOW_STATS_CONFIG": str(tmp_path / "none.toml")})
    assert r.returncode == 0 and "encoding table: reads" in r.stdout, r.stderr


def test_tables_by_file_id_with_encrypted_rows(tmp_path, shipper):
    """Forever's root has no names (the owner's proof run, 2026-11-20: no
    table found by name), and its item tables keep hidden rows in
    encrypted blocks of an otherwise plain file. Tables come by file id;
    their encrypted blocks read as zeros, the rest is still checked."""
    inst = cf.Install(shipper.jenkins96)
    rows = b"WDC5" + bytes(range(256)) * 12
    inst.file(5000, rows, encoded=cf.blte(rows, blocks=4, encrypted=(1,)), content=cf.NO_NAME_HASH)
    inst.file(5001, b"BLP2" + bytes(range(256)) * 4, encoded=cf.blte(b"BLP2" + bytes(range(256)) * 4, blocks=2,
                                                                    encrypted=(1,)))
    casc = shipper.Casc(inst.write(tmp_path / "World of Warcraft"))
    got, tables, missing = shipper.casc_files(casc, [5001], {"Item": 5000, "ChrModel": 0, "Gone": 6000})
    assert tables == {"Item": 5000}
    step = -(-len(rows) // 4)
    assert got[5000] == rows[:step] + bytes(step) + rows[2 * step:]  # the encrypted quarter blank
    assert missing == {5000: "partly encrypted: those rows left blank", 5001: "encrypted",
                       "ChrModel": "not in this build", "Gone": "not in this build", 6000: "not in this build"}


def test_diagnose_one_file(tmp_path, shipper):
    root = install(tmp_path, shipper)
    lines = shipper.casc_diagnose(root, [TEX, 4000, 4001, 7777])
    by = {ln.split(":")[0].replace(" root 1/1 locale 0x00000002 flags 0x00000000", ""): ln
          for ln in lines if ln.startswith("file ")}
    assert "header key matches, size field matches; body: BLTE" in by[f"file {TEX} encoding 1/1"]
    assert "blocks" in by[f"file {TEX} encoding 1/1"]
    assert by["file 4000 encoding 1/1"].endswith("blocks Ex1")
    assert by["file 4001 encoding 1/1"].endswith("in no index")
    assert by["file 7777"] == "file 7777: not in the root"


def test_a_header_key_that_isnt_the_reversed_key_still_reads(tmp_path, shipper):
    """The header's first 16 bytes don't prove anything (the owner's root
    file, 2026-11-20): right size, valid BLTE, right content key: it reads."""
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    archive, offset, _ = casc._locate(casc.ekey(keys[TEX]))
    p = root / "Data" / "data" / f"data.{archive:03d}"
    raw = bytearray(p.read_bytes())
    raw[offset:offset + 16] = bytes(range(16))
    p.write_bytes(bytes(raw))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX in got, missing


def test_a_stale_index_entry_is_not_stored(tmp_path, shipper):
    """An index entry whose space was reused for other data (the owner's
    third proof run): no BLTE file after the header, nor at the entry."""
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    archive, offset, _ = casc._locate(casc.ekey(keys[TEX]))
    p = root / "Data" / "data" / f"data.{archive:03d}"
    raw = bytearray(p.read_bytes())
    raw[offset + 30:offset + 34] = b"XXXX"
    p.write_bytes(bytes(raw))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX not in got and missing[TEX] == "not stored on this computer (its index entry is stale)"


@pytest.mark.parametrize("entries", ["with header", "without header", "both"])
def test_a_file_downloaded_on_demand_reads(tmp_path, shipper, entries):
    """The owner's install, 2026-11-20 (diagnose, file 119230): two entries
    for one file, archive 32 offset 48381554 size 20205 with a header whose
    size field is 0, and offset 48381584 size 20175, the BLTE file itself
    with no header. Either one reads."""
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    ek = casc.ekey(keys[TEX])
    archive, offset, size = casc._locate(ek)
    p = root / "Data" / "data" / f"data.{archive:03d}"
    raw = bytearray(p.read_bytes())
    raw[offset + 16:offset + 20] = bytes(4)
    p.write_bytes(bytes(raw))
    add = {"with header": [(archive, offset, size)], "without header": [(archive, offset + 30, size - 30)],
           "both": [(archive, offset, size), (archive, offset + 30, size - 30)]}[entries]
    _rewrite_idx(shipper, _idx_path(root, ek, 2), drop=ek, add=tuple(_entry(ek, *a) for a in add))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX in got, missing
    lines = shipper.casc_diagnose(root, [TEX])
    if entries != "with header":
        assert any("no header, BLTE at the entry" in ln for ln in lines if ln.startswith(f"file {TEX} root"))


def test_another_files_blob_is_refused_by_its_content_key(tmp_path, shipper):
    """An entry that points at another valid file of the right size: only
    the content key tells, and it does."""
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX, SKIN0})
    ek = casc.ekey(keys[TEX])
    a, off, size = casc._locate(casc.ekey(keys[SKIN0]))
    _rewrite_idx(shipper, _idx_path(root, ek, 2), drop=ek, add=(_entry(ek, a, off, size),))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX not in got and "content key" in missing[TEX]


def test_a_root_blob_of_another_file_is_refused(tmp_path, shipper):
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    ek, _ = _root_key(shipper, root)
    a, off, size = casc._locate(casc.ekey(keys[TEX]))
    _rewrite_idx(shipper, _idx_path(root, ek, 2), drop=ek, add=(_entry(ek, a, off, size),))
    with pytest.raises(shipper.CascMissing, match="content key"):
        shipper.Casc(root).content_keys({TEX})


def test_an_encoding_table_that_isnt_its_content_key_is_refused(tmp_path, shipper):
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    cfg = next((root / "Data" / "config").rglob(casc.build_key))
    text = cfg.read_text()
    ck = casc.config["encoding"].split()[0]
    cfg.write_text(text.replace(ck, "0" * 32))
    with pytest.raises(shipper.CascError, match="content key"):
        shipper.Casc(root)._encoding()


@pytest.mark.skipif(sys.platform == "win32", reason="a 1 GB sparse archive")
def test_the_owners_root_entry_replayed(tmp_path, shipper):
    """The owner's diagnose, 2026-11-20 11:55: one entry for the root,
    position bytes 02fc00aa7b, size bytes b5725102 (archive 11, offset
    1006676603, 38892213 bytes), header key differs, size field matches,
    BLTE after it. That reads."""
    root = install(tmp_path, shipper)
    ek, (archive, offset, size) = _root_key(shipper, root)
    pos, sz = bytes.fromhex("02fc00aa7b"), bytes.fromhex("b5725102")
    assert shipper.Casc._entry(ek[:9] + pos + sz) == (11, 1006676603, 38892213)
    blob = (root / "Data" / "data" / f"data.{archive:03d}").read_bytes()[offset:offset + size]
    big = 38892213
    with (root / "Data" / "data" / "data.011").open("wb") as f:
        f.seek(1006676603)
        f.write(bytes(range(16)) + struct.pack("<I", big) + bytes(10) + blob[30:])
        f.seek(1045568816 - 1)
        f.write(b"\0")
    _rewrite_idx(shipper, _idx_path(root, ek, 2), drop=ek, add=(ek[:9] + pos + sz,))
    line = next(ln for ln in shipper.casc_diagnose(root) if ln.startswith("  in bucket") and "archive 11" in ln)
    assert "key differs" in line and "size field 38892213 (matches)" in line and "BLTE after it: yes" in line
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX in got, missing


# --- a key with several index entries (the owner's root file, 2026-11-20) -----

def _idx_path(root: Path, key9: bytes, ver: int) -> Path:
    return root / "Data" / "data" / f"{cf.bucket(key9[:9]):02x}0000000{ver}.idx"


def _rewrite_idx(shipper, path: Path, drop: bytes = b"", add: tuple[bytes, ...] = ()) -> None:
    """Drop the entries for key `drop`, add `add`, keep them sorted."""
    raw = path.read_bytes()
    h = shipper.Casc._index_header(raw)
    ents = raw[h["start"]:h["start"] + h["length"]]
    rows = [ents[i:i + 18] for i in range(0, len(ents), 18)]
    data = b"".join(sorted([r for r in rows if not drop or r[:9] != drop[:9]] + list(add)))
    path.write_bytes(raw[:h["start"] - 8] + struct.pack("<II", len(data), 0) + data)


def _entry(key: bytes, archive: int, offset: int, size: int) -> bytes:
    return key[:9] + ((archive << 30) | offset).to_bytes(5, "big") + struct.pack("<I", size)


def _starts(root: Path) -> list[tuple[int, int]]:
    """Where each blob of the made-up archives starts: another file's
    header, what a stale entry points at."""
    out = []
    for a in (0, 1):
        raw = (root / "Data" / "data" / f"data.{a:03d}").read_bytes()
        off = 0
        while off < len(raw):
            out.append((a, off))
            off += struct.unpack_from("<I", raw, off + 16)[0]
    return out


def _decoy(root: Path) -> tuple[int, int]:
    """Another file's header after everything else in data.001: a place
    that sorts after any real one."""
    p = root / "Data" / "data" / "data.001"
    off = p.stat().st_size
    body = cf.blte(b"another file")
    p.write_bytes(p.read_bytes() + bytes(range(16)) + struct.pack("<I", len(body) + 30) + bytes(10) + body)
    return 1, off


def _root_key(shipper, root: Path) -> tuple[bytes, tuple[int, int, int]]:
    casc = shipper.Casc(root)
    ek = casc.ekey(bytes.fromhex(casc.config["root"].split()[0]))
    return ek, casc._locate(ek)


@pytest.mark.parametrize("stale", ["lower", "higher"])
def test_a_stale_duplicate_in_the_same_index_is_skipped(tmp_path, shipper, stale):
    """Two entries for the root's key in one .idx, the stale one sorted
    before or after the current one: the root still reads."""
    root = install(tmp_path, shipper)
    ek, (archive, offset, size) = _root_key(shipper, root)
    a, off = (0, 0) if stale == "lower" else _decoy(root)
    assert (((a << 30) | off) < ((archive << 30) | offset)) == (stale == "lower")
    _rewrite_idx(shipper, _idx_path(root, ek, 2), add=(_entry(ek, a, off, size),))
    casc = shipper.Casc(root)
    assert len(casc._locations(ek)) == 2
    got, _, missing = shipper.casc_files(casc, [TEX])
    assert TEX in got, missing
    assert any("2 entries:" in ln for ln in shipper.casc_diagnose(root, [TEX])) is False  # TEX has one
    assert sum(ln.startswith("  in bucket") for ln in shipper.casc_diagnose(root)) >= 3  # encoding + root's two


@pytest.mark.parametrize("current", ["newer", "older"])
def test_a_stale_entry_in_one_index_version_and_a_current_one_in_the_other(tmp_path, shipper, current):
    root = install(tmp_path, shipper)
    ek, (archive, offset, size) = _root_key(shipper, root)
    a, off = next(s for s in _starts(root) if s != (archive, offset))
    good, bad = _entry(ek, archive, offset, size), _entry(ek, a, off, size)
    newer, older = _idx_path(root, ek, 2), _idx_path(root, ek, 1)
    _rewrite_idx(shipper, newer, drop=ek, add=(good if current == "newer" else bad,))
    _rewrite_idx(shipper, older, drop=ek, add=(bad if current == "newer" else good,))
    got, _, missing = shipper.casc_files(shipper.Casc(root), [TEX])
    assert TEX in got, missing


def test_indexes_rewritten_during_the_run_are_read_again(tmp_path, shipper):
    """The game moved a file and rewrote its index after the shipper read
    the indexes: the old entry is stale, the new index has it."""
    root = install(tmp_path, shipper)
    casc = shipper.Casc(root)
    keys, _ = casc.content_keys({TEX})
    ek = casc.ekey(keys[TEX])
    archive, offset, size = casc._locate(ek)
    a, off = next(s for s in _starts(root) if s != (archive, offset))
    path = _idx_path(root, ek, 2)
    current = path.read_bytes()
    _rewrite_idx(shipper, path, drop=ek, add=(_entry(ek, a, off, size),))
    casc = shipper.Casc(root)
    casc.content_keys({TEX})  # the indexes are read now, with the stale entry
    path.write_bytes(current)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    got, _, missing = shipper.casc_files(casc, [TEX])
    assert TEX in got, missing


def test_every_entry_stale_is_still_not_stored(tmp_path, shipper):
    root = install(tmp_path, shipper)
    ek, (archive, offset, size) = _root_key(shipper, root)
    starts = [s for s in _starts(root) if s != (archive, offset)]
    _rewrite_idx(shipper, _idx_path(root, ek, 2), drop=ek,
                 add=tuple(_entry(ek, a, off, size) for a, off in starts[:2]))
    with pytest.raises(shipper.CascMissing, match="content key"):  # other files' blobs, refused
        shipper.Casc(root).content_keys({TEX})


# --- several root entries, several encodings (the owner's extract, 2026-11-20) --

def test_a_file_whose_preferred_root_entry_isnt_stored_reads_another(tmp_path, shipper):
    """Two root entries for one file id, this platform's first in the root
    and not on this computer, the other platform's stored: it reads."""
    here = shipper.CASC_MACOS if sys.platform == "darwin" else shipper.CASC_WINDOWS
    there = shipper.CASC_WINDOWS if here == shipper.CASC_MACOS else shipper.CASC_MACOS

    def write(inst):
        inst.file(5000, b"BLP2 this platform, never downloaded", content=here, stored=False)
        inst.file(5000, b"BLP2 the other platform's copy", content=there)
        inst.file(5001, b"BLP2 enUS", stored=False)
        inst.file(5001, b"BLP2 every locale", locale=0xFFFFFFFF)
    root = install(tmp_path, shipper, extra=write)
    casc = shipper.Casc(root)
    got, _, missing = shipper.casc_files(casc, [5000, 5001], follow=False)
    assert got == {5000: b"BLP2 the other platform's copy", 5001: b"BLP2 every locale"}, missing
    assert casc.options(5000)[0] != hashlib.md5(b"BLP2 the other platform's copy").digest()  # tried second
    lines = shipper.casc_diagnose(root, [5000])
    assert "file 5000: 2 root entries, 2 worth trying" in lines
    assert any(ln.startswith(f"file 5000 root 1/2 locale 0x00000002 flags 0x{here:08x} encoding 1/1: in no index")
               for ln in lines)
    assert any(ln.startswith("folder Data/data:") and "data.NNN 2" in ln and ".idx" in ln for ln in lines)


def test_a_content_key_with_two_encodings_reads_the_stored_one(tmp_path, shipper):
    def write(inst):
        inst.file(5002, b"BLP2 two encodings", encoded=cf.blte(b"BLP2 two encodings", blocks=3), stored=False,
                  also=cf.blte(b"BLP2 two encodings", blocks=1))
    root = install(tmp_path, shipper, extra=write)
    casc = shipper.Casc(root)
    got, _, missing = shipper.casc_files(casc, [5002], follow=False)
    assert got == {5002: b"BLP2 two encodings"}, missing
    lines = shipper.casc_diagnose(root, [5002])
    assert any(ln.startswith("file 5002 root 1/1") and "encoding 1/2: in no index" in ln for ln in lines)
    assert any(ln.startswith("file 5002 root 1/1") and "encoding 2/2: archive" in ln for ln in lines)


def test_a_low_violence_copy_is_never_read(tmp_path, shipper):
    def write(inst):
        inst.file(5003, b"BLP2 gentle only", content=cf.LOW_VIOLENCE)
    root = install(tmp_path, shipper, extra=write)
    got, _, missing = shipper.casc_files(shipper.Casc(root), [5003], follow=False)
    assert 5003 not in got and missing[5003] == "not in this build"
