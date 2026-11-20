"""A made-up WoW install in CASC form, for the shipper's reader
(shipper/wow_stats.py Casc). Written from the format as wow.export reads it
(casc-source-local.js, casc-source.js, blte-reader.js): .build.info, a build
config, local indexes (Data/data/*.idx), data.NNN archives of BLTE files, an
encoding table and a root. Every file in it is invented bytes."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

ENUS, FRFR = 0x2, 0x10
LOW_VIOLENCE = 0x80
NO_NAME_HASH = 0x10000000


def blte(data: bytes, blocks: int = 2, mode: bytes = b"Z", encrypted: tuple[int, ...] = ()) -> bytes:
    """BLTE with a block table: `blocks` blocks, zlib ('Z') or raw ('N');
    the blocks numbered in `encrypted` are 'E' blocks of made-up bytes
    (a key nobody here has)."""
    if blocks == 0:  # no table: one raw block
        return b"BLTE" + struct.pack(">I", 0) + b"N" + data
    step = max(1, -(-len(data) // blocks))
    parts = [data[i:i + step] for i in range(0, max(len(data), 1), step)] or [b""]
    raw = []
    for k, p in enumerate(parts):
        m = mode if k % 2 == 0 else b"N"
        if k in encrypted:
            raw.append(b"E" + b"\x08" + b"k" * 8 + b"\x04" + b"iviv" + b"S" + hashlib.sha256(p).digest() * 4)
            continue
        raw.append(m + (zlib.compress(p) if m == b"Z" else p))
    table = b"".join(struct.pack(">II", len(r), len(p)) + hashlib.md5(r).digest() for r, p in zip(raw, parts, strict=True))
    header = 12 + 24 * len(raw)
    return b"BLTE" + struct.pack(">I", header) + b"\x0f" + len(raw).to_bytes(3, "big") + table + b"".join(raw)


def bucket(key9: bytes) -> int:
    x = 0
    for b in key9:
        x ^= b
    return (x & 0x0F) ^ (x >> 4)


class Install:
    """Collects files, then writes the install (`write`)."""

    def __init__(self, hasher, product: str = "wowf", locale: str = "enUS", root_version: int | str = 1):
        self.hasher = hasher  # the root's name hash (shipper jenkins96, checked on its own)
        self.product, self.locale, self.root_version = product, locale, root_version
        self.blobs: list[tuple[bytes, bytes]] = []  # (ekey, encoded)
        self.ckeys: dict[bytes, list[bytes]] = {}  # ckey -> its ekeys
        self.root_blocks: list[tuple[int, int, list[tuple[int, bytes, int]]]] = []  # (locale, content, [(fdid, ckey, hash)])
        self.unstored: set[bytes] = set()  # ekeys left out of the indexes

    def add(self, data: bytes, encoded: bytes | None = None, stored: bool = True,
            also: bytes | None = None) -> bytes:
        """Store one file; its content key. `also`: a second encoding of
        it, listed after the first in the encoding table and always stored
        (one content key, two encoding keys)."""
        ckey = hashlib.md5(data).digest()
        for enc, keep in ((encoded if encoded is not None else blte(data), stored), (also, True)):
            if enc is None:
                continue
            ekey = hashlib.md5(enc).digest()
            self.ckeys.setdefault(ckey, []).append(ekey)
            self.blobs.append((ekey, enc))
            if not keep:
                self.unstored.add(ekey)
        return ckey

    def file(self, fdid: int, data: bytes, name: str | None = None, locale: int = ENUS, content: int = 0,
             **kw) -> bytes:
        ckey = self.add(data, **kw)
        self.root_blocks.append((locale, content, [(fdid, ckey, self.hasher(name) if name else 0)]))
        return ckey

    def root(self) -> bytes:
        named = sum(len(e) for _, c, e in self.root_blocks if not c & NO_NAME_HASH)
        total = sum(len(e) for _, _, e in self.root_blocks)
        if self.root_version == "classic":
            out = b""
            for loc, content, entries in self.root_blocks:
                entries = sorted(entries)
                out += struct.pack("<III", len(entries), content, loc)
                prev = -1
                for fdid, _, _ in entries:
                    out += struct.pack("<i", fdid - prev - 1)
                    prev = fdid
                for _, ckey, h in entries:
                    out += ckey + struct.pack("<Q", h)
            return out
        out = struct.pack("<IIIIII", 0x4D465354, 0x18, self.root_version, total, named, 0)
        for loc, content, entries in self.root_blocks:
            entries = sorted(entries)
            if self.root_version == 2:
                out += struct.pack("<IIII", len(entries), loc, content & 0x1FFFF, content & ~0x1FFFF & 0xFFFFFFFF)
                out += b"\0"
            else:
                out += struct.pack("<III", len(entries), content, loc)
            prev = -1
            for fdid, _, _ in entries:
                out += struct.pack("<i", fdid - prev - 1)
                prev = fdid
            out += b"".join(ck for _, ck, _ in entries)
            if not (total != named and content & NO_NAME_HASH):
                out += b"".join(struct.pack("<Q", h) for _, _, h in entries)
        return out

    def encoding(self, page_kb: int = 1) -> bytes:
        page = page_kb * 1024
        espec = b"z\0"
        entries = sorted(self.ckeys.items())
        pages, cur = [], b""
        firsts = []
        for ck, eks in entries:
            e = bytes([len(eks)]) + (1234).to_bytes(5, "big") + ck + b"".join(eks)
            if len(cur) + len(e) > page:
                pages.append(cur)
                cur = b""
            if not cur:
                firsts.append(ck)
            cur += e
        if cur:
            pages.append(cur)
        index = b"".join(f + hashlib.md5(p).digest() for f, p in zip(firsts, pages, strict=True))
        body = b"".join(p + bytes(page - len(p)) for p in pages)
        head = b"EN" + bytes([1, 16, 16]) + struct.pack(">HHII", page_kb, page_kb, len(pages), 0) + b"\0"
        head += struct.pack(">I", len(espec))
        return head + espec + index + body

    def write(self, root: Path, build_key: str = "0123456789abcdef0123456789abcdef", tags: str | None = None,
              extra_rows: str = "", offset_bits: int = 30, encoding_in_older: bool = False) -> Path:
        """`offset_bits`: the index header's split of an entry's position
        between archive and offset. `encoding_in_older`: the encoding
        table's entry only in its bucket's older .idx."""
        root_data = self.root()
        root_ckey = self.add(root_data, blte(root_data, blocks=3))
        enc = self.encoding()
        enc_blte = blte(enc, blocks=4)
        enc_ekey = hashlib.md5(enc_blte).digest()
        self.blobs.append((enc_ekey, enc_blte))
        enc_ckey = hashlib.md5(enc).digest()
        tags = tags if tags is not None else f"Windows x86_64 EU? {self.locale} speech?:Windows x86_64 EU? {self.locale} text?"
        (root / "Data" / "data").mkdir(parents=True, exist_ok=True)
        header = "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Tags!STRING:0|Version!STRING:0|Product!STRING:0"
        rows = [f"eu|1|{'f' * 32}|{'e' * 32}|{tags}|1.15.0.1|wow_classic_era",
                f"eu|1|{build_key}|{'e' * 32}|{tags}|1.60.1.70959|{self.product}"]
        (root / ".build.info").write_text("\n".join([header, *rows]) + "\n" + extra_rows, encoding="utf-8")
        cfg = root / "Data" / "config" / build_key[:2] / build_key[2:4] / build_key
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(f"# Build Configuration\n\nroot = {root_ckey.hex()}\nencoding = {enc_ckey.hex()} {enc_ekey.hex()}\n",
                       encoding="utf-8")
        # Two archives, so the archive number's split across bytes is read.
        archives: dict[int, bytearray] = {0: bytearray(), 1: bytearray()}
        idx: dict[int, list[bytes]] = {}
        older: dict[int, list[bytes]] = {}
        for n, (ekey, enc) in enumerate(self.blobs):
            a = n % 2
            off = len(archives[a])
            archives[a] += ekey[::-1] + struct.pack("<I", len(enc) + 30) + bytes(10) + enc
            if ekey in self.unstored:
                continue
            pos = (a << offset_bits) | off
            into = older if encoding_in_older and ekey == enc_ekey else idx
            into.setdefault(bucket(ekey[:9]), []).append(ekey[:9] + pos.to_bytes(5, "big")
                                                         + struct.pack("<I", len(enc) + 30))
        for a, data in archives.items():
            (root / "Data" / "data" / f"data.{a:03d}").write_bytes(bytes(data))
        for b in set(idx) | set(older):
            head = struct.pack("<II", 0x10, 0) + struct.pack("<HBBBBBBQ", 7, b, 0, 4, 5, 9, offset_bits, 0x4000000000)
            head += bytes(((8 + 0x10 + 0x0F) & ~0x0F) - len(head))
            for ver, ents in ((2, idx.get(b, [])), (1, older.get(b, []))):
                # The older version: usually nothing useful, looked at only
                # for what the newest one lacks.
                data = b"".join(sorted(ents))
                (root / "Data" / "data" / f"{b:02x}0000000{ver}.idx").write_bytes(
                    head + struct.pack("<II", len(data), 0) + data)
        return root


# --- made-up model files --------------------------------------------------------

def chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack("<I", len(body)) + body


def m2(skins=(), textures=(), skel=0, anims=()) -> bytes:
    """A chunked model naming its files: SFID skins, TXID textures, SKID,
    AFID (anim id, sub id, file)."""
    out = chunk(b"MD21", b"MD20" + bytes(60))
    if skins:
        out += chunk(b"SFID", struct.pack(f"<{len(skins)}I", *skins))
    if textures:
        out += chunk(b"TXID", struct.pack(f"<{len(textures)}I", *textures))
    if skel:
        out += chunk(b"SKID", struct.pack("<I", skel))
    if anims:
        out += chunk(b"AFID", b"".join(struct.pack("<HHI", a, s, f) for a, s, f in anims))
    return out


def skel(parent=0, anims=()) -> bytes:
    out = chunk(b"SKL1", bytes(16))
    if parent:
        out += chunk(b"SKPD", bytes(8) + struct.pack("<I", parent) + bytes(4))
    if anims:
        out += chunk(b"AFID", b"".join(struct.pack("<HHI", a, s, f) for a, s, f in anims))
    return out
