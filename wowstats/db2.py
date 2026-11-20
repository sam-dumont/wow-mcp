"""Forever's client tables (.db2, WDC3 to WDC5), read from the files each
player's own shipper sends (wowstats.assets).

A .db2 file holds rows but not their columns' names or types: those come
from WoWDBDefs' definitions (github.com/wowdev/WoWDBDefs, CC BY-SA 4.0,
THIRD_PARTY.md), trimmed to Forever's builds in data/dbd/
(tools/model3d_dbd). The layout hash in the file's header picks the
definition, so a new client build with the same layout reads without a
change here; one with a new layout says so (DB2Error) until data/dbd/ is
remade.

The reader follows wow.export's (github.com/Kruithne/wow.export, MIT,
db/WDCReader.js): sections, the field storage info (plain, bitpacked,
common data, pallets), the copy table, the relationship map and the id
lists. Encrypted sections are skipped. Only what the 3D view reads is
needed, so strings are read but never relied on.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path

_HERE = Path(__file__).resolve().parent
DBD_DIRS = (_HERE / "data" / "dbd", _HERE.parent / "data" / "dbd")

NONE, BITPACKED, COMMON, INDEXED, INDEXED_ARRAY, SIGNED = 0, 1, 2, 3, 4, 5


class DB2Error(ValueError):
    pass


# --- definitions (WoWDBDefs .dbd) -----------------------------------------------

class Field:
    __slots__ = ("name", "kind", "size", "signed", "count", "inline", "is_id", "relation")

    def __init__(self, name, kind, size, signed, count, inline, is_id, relation):
        self.name, self.kind, self.size, self.signed, self.count = name, kind, size, signed, count
        self.inline, self.is_id, self.relation = inline, is_id, relation


_FIELD = re.compile(r"^(?:\$(?P<ann>[a-z,]+)\$)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
                    r"(?:<(?P<u>u?)(?P<size>\d+)>)?(?:\[(?P<count>\d+)\])?")


def parse_dbd(text: str) -> tuple[dict[str, str], list[dict]]:
    """(column -> base type, the definition blocks: {layouts, builds, fields})."""
    cols: dict[str, str] = {}
    blocks: list[dict] = []
    lines = [ln.split("//", 1)[0].rstrip() for ln in text.splitlines()]
    i = 0
    if lines and lines[0].strip() == "COLUMNS":
        i = 1
        while i < len(lines) and lines[i].strip():
            parts = lines[i].split()
            if len(parts) >= 2:
                cols[parts[1].rstrip("?")] = parts[0].split("<", 1)[0]
            i += 1
    cur: dict | None = None
    for ln in lines[i:]:
        s = ln.strip()
        if not s:
            if cur and cur["fields"]:
                blocks.append(cur)
            cur = None
            continue
        cur = cur or {"layouts": set(), "builds": [], "fields": []}
        if s.startswith("LAYOUT "):
            cur["layouts"].update(x.strip().upper() for x in s[7:].split(","))
        elif s.startswith("BUILD "):
            cur["builds"].extend(x.strip() for x in s[6:].split(","))
        elif s.startswith("COMMENT"):
            continue
        else:
            m = _FIELD.match(s)
            if m:
                cur["fields"].append(m.groupdict())
    if cur and cur["fields"]:
        blocks.append(cur)
    return cols, blocks


@lru_cache(maxsize=64)
def _shipped(table: str) -> tuple[dict[str, str], list[dict]]:
    for d in DBD_DIRS:
        p = d / f"{table}.dbd"
        if p.is_file():
            return parse_dbd(p.read_text(encoding="utf-8"))
    raise DB2Error(f"no definition for {table}")


def definition(table: str, layout: str, build: str = "", text: str | None = None) -> list[Field]:
    """The fields of `table` for a file of this layout hash (or, failing
    that, this build)."""
    cols, blocks = parse_dbd(text) if text is not None else _shipped(table)
    block = next((b for b in blocks if layout.upper() in b["layouts"]), None)
    if block is None and build:
        block = next((b for b in blocks if build in b["builds"]), None)
    if block is None:
        raise DB2Error(f"{table}: no definition for layout {layout} (build {build or '?'}): remake data/dbd/")
    out = []
    for f in block["fields"]:
        ann = set((f["ann"] or "").split(","))
        kind = cols.get(f["name"], "int")
        if kind == "locstring":
            kind = "string"
        size = int(f["size"]) if f["size"] else 32
        out.append(Field(f["name"], kind, size, not f["u"], int(f["count"]) if f["count"] else 0,
                         "noninline" not in ann, "id" in ann, "relation" in ann))
    return out


# --- the file --------------------------------------------------------------------

_INT = {(8, True): "b", (8, False): "B", (16, True): "h", (16, False): "H", (32, True): "i", (32, False): "I",
        (64, True): "q", (64, False): "Q"}


def _code(f: Field) -> str:
    return "f" if f.kind == "float" else _INT.get((f.size, f.signed), "i")


def _caster(f: Field):
    """Raw bits (as read from a bitpacked field, a pallet or the common
    data) -> the field's value: a float, or an integer of the field's size
    and sign."""
    if f.kind == "float":
        return lambda v: struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    size = f.size if f.size in (8, 16, 32, 64) else 32
    mask, top = (1 << size) - 1, 1 << (size - 1)
    if f.signed:
        return lambda v: (v & mask) - ((v & top) << 1)
    return lambda v: v & mask


class Table:
    """One .db2 file: `rows` is id -> {column: value}; arrays are lists.

    `columns` keeps only those columns in each row (the id column always
    stays): a big table read for two of its columns costs a fraction of the
    time and memory."""

    def __init__(self, data: bytes, table: str, build: str = "", dbd: str | None = None,
                 columns: Iterable[str] | None = None):
        self.name = table
        magic = data[:4]
        if magic not in (b"WDC3", b"WDC4", b"WDC5"):
            raise DB2Error(f"{table}: not a WDC3-5 file")
        ver = int(magic[3:4])
        pos = 4
        if ver == 5:
            pos += 4 + 128  # schema version, schema build string
        (self.record_count, _fields, self.record_size, _strings, _thash, layout, self.min_id, self.max_id,
         _locale, self.flags, self.id_index, total_fields, _bitpacked, _lookup, fsi_size, common_size,
         pallet_size, sections) = struct.unpack_from("<IIIIIIIIIHHIIIIIII", data, pos)
        pos += 4 * 9 + 2 * 2 + 4 * 7
        self.layout = f"{layout:08X}"
        self.fields = definition(table, self.layout, build, dbd)
        self._id_name = self._inline_id()
        self.rows: dict[int, dict] = {}
        self.data = data
        self._strings: list[tuple[int, int, int]] = []  # (offset, size, index of its first byte) per section
        inline = [f for f in self.fields if f.inline]
        heads = []
        for _ in range(sections):
            heads.append(struct.unpack_from("<QIIIIIIII", data, pos))
            pos += 40
        pos += 4 * total_fields  # (size, position) per field: field_info says the same
        info = []
        for _ in range(fsi_size // 24):
            off, size, extra, comp, p0, p1, p2 = struct.unpack_from("<HHIIIII", data, pos)
            info.append((off, size, extra, comp, (p0, p1, p2)))
            pos += 24
        if not info and self.record_count == 0:  # an empty table lists no fields at all
            self.data = b""
            return
        if len(info) < len(inline):
            raise DB2Error(f"{table}: the file has {len(info)} fields, the definition {len(inline)}")
        start = pos
        self.pallet: list[list[int]] = [[] for _ in info]
        for i, (_, _, extra, comp, _) in enumerate(info):
            if comp in (INDEXED, INDEXED_ARRAY):
                self.pallet[i] = list(struct.unpack_from(f"<{extra // 4}I", data, pos))
                pos += extra
        if pos != start + pallet_size:
            raise DB2Error(f"{table}: pallet data doesn't add up")
        start = pos
        self.common: list[dict[int, int]] = [{} for _ in info]
        for i, (_, _, extra, comp, _) in enumerate(info):
            if comp == COMMON:
                vals = struct.unpack_from(f"<{extra // 4}I", data, pos)
                self.common[i] = dict(zip(vals[0::2], vals[1::2], strict=True))
                pos += extra
        if pos != start + common_size:
            raise DB2Error(f"{table}: common data doesn't add up")
        if ver > 3:  # wow.export: one encrypted-id list per section but the last, skipped
            for _ in range(sections - 1):
                n = struct.unpack_from("<I", data, pos)[0]
                pos += 4 + 4 * n
        self.info = info
        keep = None if columns is None else set(columns) | {f.name for f in self.fields if f.is_id} | {self._id_name}
        normal = not self.flags & 1
        total_records = self.record_count * self.record_size
        outside = 0
        string_base = 0
        parsed = []
        for h in heads:
            tact, file_off, count, str_size, records_end, idl_size, rel_size, omap_count, copy_count = h
            rec_ofs = pos
            rec_size = self.record_size * count if normal else records_end - file_off
            str_ofs = rec_ofs + rec_size
            pos = str_ofs + str_size
            ids = list(struct.unpack_from(f"<{idl_size // 4}I", data, pos))
            pos += idl_size
            copies = [struct.unpack_from("<II", data, pos + 8 * k) for k in range(copy_count)]
            pos += 8 * copy_count
            omap = [struct.unpack_from("<IH", data, pos + 6 * k) for k in range(omap_count)]
            pos += 6 * omap_count
            rel: dict[int, int] = {}
            if rel_size:
                n = struct.unpack_from("<I", data, pos)[0]
                for k in range(n):
                    foreign, index = struct.unpack_from("<II", data, pos + 12 + 8 * k)
                    rel[index] = foreign
                pos += rel_size
            pos += 4 * omap_count
            # An encrypted block the client has no key for reads as zeros: skipped.
            encrypted = tact != 0 and not data[rec_ofs:rec_ofs + rec_size].strip(b"\0")
            parsed.append((count, rec_ofs, outside, ids, copies, omap, rel, encrypted))
            outside += rec_size
            string_base += str_size
            self._strings.append((str_ofs, str_size, string_base - str_size))
        readers = self._readers(keep) if normal else None
        id_name = self._id_name
        id_cols = [f.name for f in self.fields if f.is_id and not f.inline]
        rel_cols = [f.name for f in self.fields if f.relation and not f.inline and (keep is None or f.name in keep)]
        copy_rows: list[tuple[int, int]] = []
        for count, rec_ofs, outside_size, ids, copies, omap, rel, encrypted in parsed:
            copy_rows += [(d, s) for d, s in copies if d != s]
            if encrypted:
                continue
            if ids and not any(ids):  # wow.export: an all-zero id list means "the index is the id"
                ids = list(range(count))
            for i in range(count):
                rid = ids[i] if ids else None
                if normal:
                    at = rec_ofs + i * self.record_size
                    row = {}
                    for name, read in readers:
                        row[name] = read(at, rid, outside_size + i * self.record_size - total_records)
                        if rid is None and name == id_name:
                            rid = row[name]
                else:
                    row = self._sparse(omap[i][0], rid)
                    if keep is not None:
                        row = {k: v for k, v in row.items() if k in keep}
                if rid is None:
                    rid = row.get(id_name, 0)
                for name in id_cols:
                    row[name] = rid
                for name in rel_cols:
                    row[name] = rel.get(i, 0)
                self.rows[int(rid)] = row
        for dst, src in copy_rows:
            if src in self.rows:
                self.rows[dst] = {**self.rows[src], **{f.name: dst for f in self.fields if f.is_id}}
        self.data = b""  # rows are read: the bytes can go

    def _inline_id(self) -> str:
        """The id column: the id list's (non-inline) when the definition has
        one, else the inline field the header points at."""
        outside = next((f for f in self.fields if f.is_id and not f.inline), None)
        if outside:
            return outside.name
        inline = [f for f in self.fields if f.inline]
        if 0 <= self.id_index < len(inline):
            return inline[self.id_index].name
        idf = next((f for f in inline if f.is_id), None)
        return idf.name if idf else "ID"

    def _string(self, index: int) -> str:
        for ofs, size, base in self._strings:
            if base <= index < base + size:
                at = ofs + index - base
                end = self.data.index(b"\0", at)
                return self.data[at:end].decode("utf-8", "replace")
        return ""

    def _readers(self, keep: set[str] | None) -> list[tuple[str, Callable]]:
        """One reader per inline column to keep, for fixed-size records:
        read(record offset, record id or None, the record's offset for
        strings) -> the value. The id column comes first when it is inline,
        so the common data (keyed by id) can use it."""
        data = self.data
        out = []
        inline = [f for f in self.fields if f.inline]
        for k, f in enumerate(inline):
            if keep is not None and f.name not in keep:
                continue
            off_bits, size_bits, _extra, comp, packing = self.info[k]
            n = f.count or 1
            byte = off_bits >> 3
            cast = _caster(f)
            if comp == NONE and f.kind == "string":
                def read(at, rid, abs_ofs, byte=byte, n=n, f=f):
                    vals = []
                    for j in range(n):
                        rel_pos = byte + 4 * j
                        ofs = struct.unpack_from("<I", data, at + rel_pos)[0]
                        vals.append(self._string(abs_ofs + rel_pos + ofs) if ofs else "")
                    return vals if f.count else vals[0]
            elif comp == NONE:
                st = struct.Struct("<" + _code(f) * n)
                if f.count:
                    def read(at, rid, abs_ofs, st=st, byte=byte):
                        return list(st.unpack_from(data, at + byte))
                else:
                    def read(at, rid, abs_ofs, st=st, byte=byte):
                        return st.unpack_from(data, at + byte)[0]
            elif comp == COMMON:
                common, default = self.common[k], packing[0]
                id_name = self._id_name

                def read(at, rid, abs_ofs, common=common, default=default, cast=cast, id_name=id_name):
                    return cast(common.get(rid if rid is not None else 0, default))
            else:
                shift, mask = off_bits & 7, (1 << size_bits) - 1
                nbytes = (shift + size_bits + 7) >> 3
                pallet = self.pallet[k]
                if comp == INDEXED_ARRAY:
                    m = packing[2]

                    def read(at, rid, abs_ofs, byte=byte, nbytes=nbytes, shift=shift, mask=mask, m=m,
                             pallet=pallet, cast=cast):
                        v = (int.from_bytes(data[at + byte:at + byte + nbytes], "little") >> shift) & mask
                        return [cast(x) for x in pallet[v * m:v * m + m]]
                elif comp == INDEXED:
                    def read(at, rid, abs_ofs, byte=byte, nbytes=nbytes, shift=shift, mask=mask, pallet=pallet,
                             cast=cast, f=f):
                        v = cast(pallet[(int.from_bytes(data[at + byte:at + byte + nbytes], "little") >> shift)
                                        & mask])
                        return [v] if f.count else v
                else:
                    signed = comp == SIGNED
                    top = 1 << (size_bits - 1)

                    def read(at, rid, abs_ofs, byte=byte, nbytes=nbytes, shift=shift, mask=mask, cast=cast,
                             signed=signed, top=top, f=f):
                        v = (int.from_bytes(data[at + byte:at + byte + nbytes], "little") >> shift) & mask
                        if signed and v & top:
                            v -= top << 1
                        v = cast(v)
                        return [v] if f.count else v
            out.append((f.name, read))
        out.sort(key=lambda r: r[0] != self._id_name)
        return out

    def _sparse(self, at: int, rid: int | None) -> dict:
        """A variable-size record (the offset map's tables): every field
        stored as is, one after the other, strings inline."""
        data = self.data
        row: dict = {}
        inline = [f for f in self.fields if f.inline]
        for k, f in enumerate(inline):
            _off, _size, _extra, comp, packing = self.info[k]
            if comp == COMMON:
                v = _caster(f)(self.common[k].get(rid if rid is not None else row.get(self._id_name, 0),
                                                   packing[0]))
                row[f.name] = [v] if f.count else v
                continue
            vals = []
            for _ in range(f.count or 1):
                if f.kind == "string":
                    end = data.index(b"\0", at)
                    vals.append(data[at:end].decode("utf-8", "replace"))
                    at = end + 1
                else:
                    fmt = "<" + _code(f)
                    vals.append(struct.unpack_from(fmt, data, at)[0])
                    at += struct.calcsize(fmt)
            row[f.name] = vals if f.count else vals[0]
        return row
