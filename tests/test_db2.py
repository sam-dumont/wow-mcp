"""wowstats.db2 on a small WDC5 file written here, field by field: no game
file. One field of each storage kind (plain, bitpacked, signed bitpacked,
common data, pallet, pallet array), strings, the id list, the copy table,
the relationship map, and a second, encrypted section that reads as zeros."""

import struct

import pytest

from tools import model3d_dbd
from wowstats import db2

LAYOUT = 0x1234ABCD
DBD = """COLUMNS
int ID
string Name
int Plain
float Ratio
int Packed
int Neg
int Common
int Pal
int PalArr
int<Parent::ID> ParentID

LAYOUT 0BADF00D
BUILD 1.15.0.1
$noninline,id$ID<32>
Name

LAYOUT 1234ABCD
BUILD 1.60.1.70959
$noninline,id$ID<32>
Name
Plain<32>
Ratio
Packed<u16>
Neg<32>
Common<32>
Pal<32>
PalArr<32>[3]
$noninline,relation$ParentID<32>
"""

NONE, BITPACKED, COMMON, INDEXED, INDEXED_ARRAY, SIGNED = 0, 1, 2, 3, 4, 5
RECORD = 16  # bytes: 116 bits of fields, padded

# (id, name, plain, ratio, packed, neg, common, pal value, pal array, parent)
ROWS = [
    (10, "Hogger", 70000, 0.5, 1000, -20, 7, 300, (1, 2, 3), 501),
    (11, "", -1, -2.25, 0, 31, 99, 77, (4, 5, 6), 0),
    (15, "Gnome", 3, 1.0, 1023, -32, 7, 300, (4, 5, 6), 502),
]
COPIES = [(20, 10)]  # (new id, the id it copies)
PAL = [77, 300]
PAL_ARR = [(1, 2, 3), (4, 5, 6)]
COMMON_DEFAULT = 7


def _bits(fields: list[tuple[int, int, int]], size: int) -> bytes:
    """(bit offset, bit count, value) -> a record of `size` bytes."""
    v = 0
    for off, n, x in fields:
        v |= (x & ((1 << n) - 1)) << off
    return v.to_bytes(size, "little")


def wdc5(encrypted_section: bool = True) -> bytes:
    strings = b"\0Hogger\0Gnome\0"
    str_at = {"": 0, "Hogger": 1, "Gnome": 8}
    total = len(ROWS) + (1 if encrypted_section else 0)
    total_records = total * RECORD
    records = b""
    for i, (_id, name, plain, ratio, packed, neg, _common, pal, arr, _parent) in enumerate(ROWS):
        # A string's offset is counted from where it is stored in the record, as if
        # every section's records came before every section's strings.
        name_ofs = (str_at[name] + total_records - i * RECORD) if name else 0
        head = struct.pack("<Iif", name_ofs, plain, ratio)
        packed_bits = _bits([(0, 10, packed), (10, 6, neg), (16, 1, PAL.index(pal)), (17, 1, PAL_ARR.index(arr))], 4)
        records += head + packed_bits
    infos = [  # offset bits, size bits, extra bytes, compression, p0, p1, p2
        (0, 32, 0, NONE, 0, 0, 0),
        (32, 32, 0, NONE, 0, 0, 0),
        (64, 32, 0, NONE, 0, 0, 0),
        (96, 10, 0, BITPACKED, 0, 10, 0),
        (106, 6, 0, SIGNED, 10, 6, 1),
        (112, 0, 8, COMMON, COMMON_DEFAULT, 0, 0),
        (112, 1, 4 * len(PAL), INDEXED, 16, 1, 0),
        (113, 1, 4 * 3 * len(PAL_ARR), INDEXED_ARRAY, 17, 1, 3),
    ]
    pallet = struct.pack(f"<{len(PAL)}I", *PAL) + struct.pack(f"<{3 * len(PAL_ARR)}I", *(x for a in PAL_ARR for x in a))
    common = struct.pack("<II", 11, 99)  # only the row that differs from the default
    ids = struct.pack(f"<{len(ROWS)}I", *(r[0] for r in ROWS))
    copy = b"".join(struct.pack("<II", d, s) for d, s in COPIES)
    rel_entries = [(r[9], i) for i, r in enumerate(ROWS) if r[9]]
    rel = struct.pack("<III", len(rel_entries), 0, 0) + b"".join(struct.pack("<II", f, i) for f, i in rel_entries)
    sections = [(0, len(ROWS), records, strings, ids, copy, rel)]
    if encrypted_section:  # one row whose key the client lacks: all zeros
        sections.append((0xDEADBEEF, 1, b"\0" * RECORD, b"", struct.pack("<I", 0), b"", b""))
    head_size = 4 + 4 + 128 + 4 * 9 + 2 * 2 + 4 * 7
    fsi = b"".join(struct.pack("<HHIIIII", *f) for f in infos)
    field_struct = b"".join(struct.pack("<hH", 0, f[0] // 8) for f in infos)
    encrypted_lists = struct.pack("<I", 0) * (len(sections) - 1)
    pos = head_size + 40 * len(sections) + len(field_struct) + len(fsi) + len(pallet) + len(common) + len(encrypted_lists)
    heads, body = b"", b""
    for tact, count, recs, strs, idl, cp, rl in sections:
        heads += struct.pack("<QIIIIIIII", tact, pos, count, len(strs), 0, len(idl), len(rl), 0, len(cp) // 8)
        chunk = recs + strs + idl + cp + rl
        body += chunk
        pos += len(chunk)
    header = struct.pack("<IIIIIIIIIHHIIIIIII", total, len(infos), RECORD, len(strings), 0xC0FFEE, LAYOUT,
                         10, 20, 0, 0, 0, len(infos), 12, 0, len(fsi), len(common), len(pallet), len(sections))
    return (b"WDC5" + struct.pack("<I", 5) + b"test".ljust(128, b"\0") + header + heads + field_struct + fsi
            + pallet + common + encrypted_lists + body)


def test_reads_every_kind_of_field():
    t = db2.Table(wdc5(), "Thing", "1.60.1.70959", DBD)
    assert t.layout == "1234ABCD"
    assert set(t.rows) == {10, 11, 15, 20}  # the encrypted section's row is left out
    r = t.rows[10]
    assert r == {"ID": 10, "Name": "Hogger", "Plain": 70000, "Ratio": 0.5, "Packed": 1000, "Neg": -20,
                 "Common": 7, "Pal": 300, "PalArr": [1, 2, 3], "ParentID": 501}
    assert t.rows[11] == {"ID": 11, "Name": "", "Plain": -1, "Ratio": -2.25, "Packed": 0, "Neg": 31,
                          "Common": 99, "Pal": 77, "PalArr": [4, 5, 6], "ParentID": 0}
    assert t.rows[15]["Name"] == "Gnome" and t.rows[15]["Neg"] == -32 and t.rows[15]["Packed"] == 1023
    assert t.rows[15]["ParentID"] == 502


def test_copy_table_rows_keep_their_own_id():
    rows = db2.Table(wdc5(), "Thing", "", DBD).rows
    assert rows[20] == {**rows[10], "ID": 20}


def test_columns_keeps_only_those_and_the_id():
    rows = db2.Table(wdc5(), "Thing", "", DBD, columns=("Pal", "ParentID")).rows
    assert rows[15] == {"ID": 15, "Pal": 300, "ParentID": 502}
    assert rows[20] == {"ID": 20, "Pal": 300, "ParentID": 501}


def test_one_section_reads_the_same():
    assert db2.Table(wdc5(encrypted_section=False), "Thing", "", DBD).rows == db2.Table(wdc5(), "Thing", "", DBD).rows


def test_layout_picks_the_definition_and_a_new_one_says_so():
    data = bytearray(wdc5())
    at = 4 + 4 + 128 + 4 * 5
    assert struct.unpack_from("<I", data, at)[0] == LAYOUT
    struct.pack_into("<I", data, at, 0x99999999)
    with pytest.raises(db2.DB2Error, match="layout 99999999"):
        db2.Table(bytes(data), "Thing", "1.60.2.1", DBD)
    # An unknown layout still reads by its build when the definition lists it.
    assert db2.Table(bytes(data), "Thing", "1.60.1.70959", DBD).rows[10]["Name"] == "Hogger"


def test_not_a_table():
    with pytest.raises(db2.DB2Error):
        db2.Table(b"WDC2" + b"\0" * 300, "Thing", "", DBD)


def test_shipped_definitions_cover_the_assembly():
    from wowstats import character3d

    for name, columns in character3d.TABLES.items():
        cols, blocks = db2._shipped(name)
        assert blocks, name
        fields = {f["name"] for b in blocks for f in b["fields"]}
        assert set(columns) <= fields, (name, set(columns) - fields)
    assert set(character3d.TABLES) <= set(model3d_dbd.TABLES)


def test_trim_keeps_columns_and_forever_blocks_only():
    out = model3d_dbd.trim(DBD.replace("BUILD 1.15.0.1", "BUILD 1.15.0.1\nCOMMENT old one"))
    assert out.startswith("COLUMNS\nint ID\n")
    assert "0BADF00D" not in out and "LAYOUT 1234ABCD" in out
    assert model3d_dbd.trim(DBD.replace("1.60.1.70959", "1.15.8.1")) is None
    ranged = DBD.replace("BUILD 1.60.1.70959", "BUILD 1.15.0.1-1.60.9.99999")
    assert "1234ABCD" in model3d_dbd.trim(ranged)
