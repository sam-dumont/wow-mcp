"""Synthetic game files for the 3D viewer's tests (web/model3d/m3d).

No real game file is used anywhere in the tests: this writes small files in
the formats the viewer reads (wowdev.wiki, and wow.export's loaders, whose
field order the viewer ports), with known content, so a test can say which
colour must show where.

- BLP2 textures: DXT1 (4-colour and 1-bit alpha blocks), DXT3, DXT5,
  uncompressed BGRA, palettized with 0, 1, 4 and 8-bit alpha.
- A chunked character model (MD21, SFID, TXID, SKID): boxes for a body, a
  head on its own bone and geosets 301, 401, 402, 501, 503, 1701; its skin.
- Its skeleton (SKL1, SKPD to a parent, SKS1, SKB1, AFID) and the parent
  (SKB1, SKS1, SKA1 with attachment 1, AFID); a Stand .anim for each
  (AFM2+AFSB for the child, AFM2 for the parent) that tilt the head 30
  degrees one way (child) or the other (parent).
- An item for the right hand (M2 bones, attachments 1 and 2, AFID with a raw
  .anim that lifts it), a glow model for its attachment point, a belt
  (collection, geosets 1801/1802, bones in the reverse order of the
  character's) and a skinned model (geosets 0 and 3).
- Emitters (the M2 page of wowdev.wiki's M2Particle and M2Ribbon, 0x1EC and
  0xB0 bytes): a mesh-less glow (one bone, one additive particle emitter
  that bursts 3 particles at 33 ms, red to blue over a 1 s life), the same
  with its enabled track at 0, a sphere + plane pair for the simulation's
  unit tests, and a ribbon on a bone that turns once a second.

`scene()` returns {"files": {id: bytes}, "recipe": {...}, "expect": {...}}.
`python tools/model3d_fixtures.py <dir>` writes them as <id>.<ext> plus
recipe.json, for a look in other tools.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

# ---------------------------------------------------------------- file ids

CHAR_M2, CHAR_SKIN, CHILD_SKEL, PARENT_SKEL, CHILD_ANIM, PARENT_ANIM = 100001, 100002, 100003, 100004, 100005, 100006
ITEM_M2, ITEM_SKIN, ITEM_ANIM = 100011, 100012, 100013
VISUAL_M2, VISUAL_SKIN = 100021, 100022
BELT_M2, BELT_SKIN = 100031, 100032
SKINNED_M2, SKINNED_SKIN = 100041, 100042

TEX_RED = 200001       # DXT1, the head and the right foot
TEX_GREEN = 200002     # DXT1, the item (recipe texture type 2)
TEX_CYAN = 200003      # DXT1, the belt (recipe texture type 2)
TEX_BASE = 200004      # BGRA, the body material's base layer
TEX_MULT = 200005      # BGRA, the multiply layer
TEX_OVERLAY = 200006   # palettized, 8-bit alpha: the alpha-blended layer
TEX_PURPLE = 200007    # palettized, 4-bit alpha: geoset 402 (recipe texture type 6, hair)
TEX_ORANGE = 200008    # palettized, no alpha: the glow model (its own TXID)
TEX_ALPHA = 200009     # BGRA with an alpha ramp: geoset 401
TEX_DXT1_MIX = 200101  # unit test only: four DXT1 blocks
TEX_DXT3 = 200102
TEX_DXT5 = 200103
TEX_PAL1 = 200104

TEX_WHITE = 200010     # BGRA white: particles and ribbons take their track colours

GLOW_M2, GLOW_OFF_M2, SPREAD_M2, RIBBON_M2 = 100051, 100052, 100053, 100054

MISSING_ITEM = 424242
MISSING_LAYER = 999001

YELLOW = (200, 180, 40)
BLUE = (40, 40, 220)
MULT = (128, 255, 255)
MULTIPLIED = (100, 180, 40)  # YELLOW * MULT / 255, truncated
RED = (255, 0, 0)
GREEN = (0, 255, 0)
CYAN = (0, 255, 255)
PURPLE = (160, 0, 160)
ORANGE = (255, 128, 0)
TEAL = (10, 200, 120)

UNLIT_TWO_SIDED = 0x1 | 0x4  # M2 material flags: no lighting, no culling
NO_SEQ = 0xFFFF


# ---------------------------------------------------------------- BLP2

def _blp(encoding: int, alpha_depth: int, alpha_encoding: int, w: int, h: int, mip0: bytes,
         palette: list[tuple[int, int, int]] | None = None) -> bytes:
    header_len = 4 + 4 + 4 + 8 + 16 * 4 * 2 + 256 * 4
    offsets = [header_len] + [0] * 15
    sizes = [len(mip0)] + [0] * 15
    pal = bytearray(1024)
    for i, (r, g, b) in enumerate(palette or []):
        pal[i * 4:i * 4 + 4] = bytes((b, g, r, 0))
    return (b"BLP2" + struct.pack("<I4B2I", 1, encoding, alpha_depth, alpha_encoding, 0, w, h)
            + struct.pack("<16I", *offsets) + struct.pack("<16I", *sizes) + bytes(pal) + mip0)


def blp_bgra(w: int, h: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Uncompressed (encoding 3); pixels RGBA, row 0 at the top."""
    raw = b"".join(bytes((b, g, r, a)) for r, g, b, a in pixels)
    return _blp(3, 8, 8, w, h, raw)


def blp_palette(w: int, h: int, palette: list[tuple[int, int, int]], indices: list[int], alphas: list[int],
                depth: int) -> bytes:
    """Palettized (encoding 1) with `depth`-bit alpha (0, 1, 4 or 8)."""
    raw = bytearray(indices)
    if depth == 1:
        bits = bytearray((len(alphas) + 7) // 8)
        for i, a in enumerate(alphas):
            if a:
                bits[i // 8] |= 1 << (i % 8)
        raw += bits
    elif depth == 4:
        nib = bytearray((len(alphas) + 1) // 2)
        for i, a in enumerate(alphas):
            nib[i // 2] |= (a & 0xF) << (0 if i % 2 == 0 else 4)
        raw += nib
    elif depth == 8:
        raw += bytes(alphas)
    return _blp(1, depth, 0, w, h, bytes(raw), palette)


def rgb565(r: int, g: int, b: int) -> int:
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _dxt_colour_block(c0: int, c1: int, idx: list[int]) -> bytes:
    packed = bytearray(4)
    for i, v in enumerate(idx):
        packed[i // 4] |= (v & 3) << ((i % 4) * 2)
    return struct.pack("<HH", c0, c1) + bytes(packed)


def blp_dxt(kind: int, w: int, h: int, blocks: list[bytes]) -> bytes:
    """DXT1 (kind 1), DXT3 (3) or DXT5 (5); blocks in row order."""
    depth, enc = {1: (1, 0), 3: (8, 1), 5: (8, 7)}[kind]
    return _blp(2, depth, enc, w, h, b"".join(blocks))


def dxt1_solid(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    c = rgb565(*rgb)
    # c0 > c1 keeps the 4-colour mode (index 0 is c0, exactly)
    block = _dxt_colour_block(c, 0 if c else 1, [0] * 16) if c else _dxt_colour_block(1, 0, [1] * 16)
    return blp_dxt(1, w, h, [block] * ((w // 4) * (h // 4)))


# A reference decoder, written from wowdev.wiki's description, for the unit
# test's expected pixels.

def _unpack565(v: int) -> list[int]:
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return [(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)]


def decode_dxt_block(kind: int, block: bytes) -> list[tuple[int, int, int, int]]:
    cb = block[8:] if kind != 1 else block
    c0, c1 = struct.unpack_from("<HH", cb)
    a, b = _unpack565(c0), _unpack565(c1)
    three = kind == 1 and c0 <= c1
    cols = [a + [255], b + [255]]
    if three:
        cols += [[int((x + y) / 2) for x, y in zip(a, b, strict=True)] + [255], [0, 0, 0, 0]]
    else:
        cols += [[int((2 * x + y) / 3) for x, y in zip(a, b, strict=True)] + [255],
                 [int((x + 2 * y) / 3) for x, y in zip(a, b, strict=True)] + [255]]
    idx = [(cb[4 + i // 4] >> ((i % 4) * 2)) & 3 for i in range(16)]
    px = [list(cols[i]) for i in idx]
    if kind == 3:
        for i in range(16):
            nib = (block[i // 2] >> (4 * (i % 2))) & 0xF
            px[i][3] = nib | (nib << 4)
    elif kind == 5:
        a0, a1 = block[0], block[1]
        al = [a0, a1]
        if a0 > a1:
            al += [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        else:
            al += [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
        bits = int.from_bytes(block[2:8], "little")
        for i in range(16):
            px[i][3] = al[(bits >> (3 * i)) & 7]
    return [tuple(p) for p in px]


def decode_dxt(kind: int, w: int, h: int, blocks: list[bytes]) -> list[tuple[int, int, int, int]]:
    out = [(0, 0, 0, 0)] * (w * h)
    bw = w // 4
    for n, blk in enumerate(blocks):
        bx, by = n % bw, n // bw
        for i, p in enumerate(decode_dxt_block(kind, blk)):
            out[(by * 4 + i // 4) * w + bx * 4 + i % 4] = p
    return out


def blp_unit_cases() -> dict[int, tuple[int, int, list[tuple[int, int, int, int]]]]:
    """BLPs with their expected RGBA, for the decoder's unit test (and the
    scene's textures)."""
    cases = {}

    # DXT1: a 4-colour block, a 3-colour block with a transparent texel,
    # two solid blocks.
    b1 = _dxt_colour_block(rgb565(255, 0, 0), rgb565(0, 0, 255), [0, 1, 2, 3] * 4)
    b2 = _dxt_colour_block(rgb565(0, 0, 255), rgb565(255, 0, 0), [0, 1, 2, 3] * 4)
    b3 = _dxt_colour_block(rgb565(0, 255, 0), 0, [0] * 16)
    b4 = _dxt_colour_block(rgb565(255, 255, 255), rgb565(0, 0, 0), [1] * 16)
    blocks = [b1, b2, b3, b4]
    cases[TEX_DXT1_MIX] = (8, 8, decode_dxt(1, 8, 8, blocks), blp_dxt(1, 8, 8, blocks))

    alpha3 = bytes(((2 * i) << 4 | (2 * i + 1)) & 0xFF for i in range(8))
    blocks = [alpha3 + _dxt_colour_block(rgb565(255, 128, 0), rgb565(0, 64, 255), [i % 4 for i in range(16)])]
    cases[TEX_DXT3] = (4, 4, decode_dxt(3, 4, 4, blocks), blp_dxt(3, 4, 4, blocks))

    bits = 0
    for i in range(16):
        bits |= (i % 8) << (3 * i)
    a5 = bytes((240, 16)) + bits.to_bytes(6, "little")
    a5b = bytes((16, 240)) + bits.to_bytes(6, "little")
    col = _dxt_colour_block(rgb565(0, 255, 255), rgb565(255, 0, 255), [3 - i % 4 for i in range(16)])
    blocks = [a5 + col, a5b + col]
    cases[TEX_DXT5] = (8, 4, decode_dxt(5, 8, 4, blocks), blp_dxt(5, 8, 4, blocks))

    for tid, rgb in ((TEX_RED, RED), (TEX_GREEN, GREEN), (TEX_CYAN, CYAN)):
        cases[tid] = (8, 8, [(*rgb, 255)] * 64, dxt1_solid(8, 8, rgb))

    px = [(*YELLOW, 255)] * 16
    cases[TEX_BASE] = (4, 4, px, blp_bgra(4, 4, px))
    px = [(*MULT, 255)] * 16
    cases[TEX_MULT] = (4, 4, px, blp_bgra(4, 4, px))
    px = [(*TEAL, (255, 170, 85, 0)[i // 4]) for i in range(16)]
    cases[TEX_ALPHA] = (4, 4, px, blp_bgra(4, 4, px))

    # 8-bit alpha: opaque on the left half, clear on the right
    pal = [BLUE, (9, 9, 9)]
    idx = [0] * 64
    al = [255 if i % 8 < 4 else 0 for i in range(64)]
    cases[TEX_OVERLAY] = (8, 8, [(*BLUE, a) for a in al], blp_palette(8, 8, pal, idx, al, 8))

    # 4-bit alpha: all opaque but one odd texel at half (8 -> 136)
    pal = [PURPLE, (1, 2, 3)]
    al4 = [15] * 16
    al4[5] = 8
    cases[TEX_PURPLE] = (4, 4, [(*PURPLE, a * 17) for a in al4], blp_palette(4, 4, pal, [0] * 16, al4, 4))

    px = [(255, 255, 255, 255)] * 16
    cases[TEX_WHITE] = (4, 4, px, blp_bgra(4, 4, px))

    pal = [ORANGE, (0, 0, 0)]
    cases[TEX_ORANGE] = (4, 4, [(*ORANGE, 255)] * 16, blp_palette(4, 4, pal, [0] * 16, [], 0))

    # 1-bit alpha, two palette entries in a checker
    pal = [(250, 10, 10), (10, 10, 250)]
    idx = [(i + i // 4) % 2 for i in range(16)]
    al1 = [1 if i < 8 else 0 for i in range(16)]
    px = [(*pal[k], 255 if a else 0) for k, a in zip(idx, al1, strict=True)]
    cases[TEX_PAL1] = (4, 4, px, blp_palette(4, 4, pal, idx, al1, 1))
    return {k: v for k, v in cases.items()}


# ---------------------------------------------------------------- M2 pieces

class Data:
    """The data area after a header: put() returns offsets from `start`."""

    def __init__(self, start: int):
        self.start = start
        self.buf = bytearray()

    def put(self, b: bytes) -> int:
        while (self.start + len(self.buf)) % 16:
            self.buf += b"\0"
        ofs = self.start + len(self.buf)
        self.buf += b
        return ofs

    def arr(self, count: int, payload: bytes) -> bytes:
        """An M2Array (count, offset) pointing at `payload`."""
        if count == 0:
            return struct.pack("<II", 0, 0)
        return struct.pack("<II", count, self.put(payload))


VALUE_FMT = {"float3": "<3f", "compquat": "<4H", "int16": "<h", "uint8": "<B", "float4": "<4f", "float": "<f",
             "uint16": "<H"}


def compquat(q: tuple[float, float, float, float]) -> tuple[int, ...]:
    """wow.export's reading inverted: value = (u - 32767) / 32768."""
    return tuple(max(0, min(65535, round(v * 32768 + 32767))) for v in q)


def track(d: Data, kind: str, seqs: list | None = None, interp: int = 1) -> bytes:
    """An M2Track. `seqs`, one per sequence: None (no keys), ("in", times,
    values) in this file, or ("ext", count, ts_ofs, val_ofs) in an .anim."""
    seqs = seqs or []
    fmt = VALUE_FMT[kind]
    ts_entries, val_entries = b"", b""
    for s in seqs:
        if s is None:
            ts_entries += struct.pack("<II", 0, 0)
            val_entries += struct.pack("<II", 0, 0)
        elif s[0] == "in":
            _, times, values = s
            ts_entries += d.arr(len(times), b"".join(struct.pack("<I", t) for t in times))
            val_entries += d.arr(len(values), b"".join(struct.pack(fmt, *(v if isinstance(v, tuple) else (v,)))
                                                      for v in values))
        else:
            _, count, ts_ofs, val_ofs = s
            ts_entries += struct.pack("<II", count, ts_ofs)
            val_entries += struct.pack("<II", count, val_ofs)
    n = len(seqs)
    return struct.pack("<HH", interp, NO_SEQ) + d.arr(n, ts_entries) + d.arr(n, val_entries)


def sequence(anim_id: int, duration: int, flags: int, variation: int = 0) -> bytes:
    return struct.pack("<HHIfIhHIIHH3f3ffhH", anim_id, variation, duration, 0.0, flags, 0, 0, 0, 0, 150, 150,
                       -1, -1, 0, 1, 1, 2.6, 2.0, -1, 0)


def bone(d: Data, pivot: tuple[float, float, float], parent: int, crc: int, rot=None, trans=None) -> bytes:
    return (struct.pack("<iIhHI", -1, 0, parent, 0, crc)
            + track(d, "float3", trans) + track(d, "compquat", rot) + track(d, "float3", None)
            + struct.pack("<3f", *pivot))


def box(lo, hi, bone_idx: int = 0, uv=(0.0, 0.0, 1.0, 1.0)):
    """24 vertices (WoW coords: x forward, y right as the camera sees the
    character, z up) and 36 indices. Every face maps the uv rectangle with
    u along its horizontal edge, v from its top down."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    u0, v0, u1, v1 = uv
    faces = [  # corners: top-left, top-right, bottom-right, bottom-left; normal
        ([(x1, y0, z1), (x1, y1, z1), (x1, y1, z0), (x1, y0, z0)], (1, 0, 0)),
        ([(x0, y1, z1), (x0, y0, z1), (x0, y0, z0), (x0, y1, z0)], (-1, 0, 0)),
        ([(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)], (0, 1, 0)),
        ([(x1, y0, z1), (x0, y0, z1), (x0, y0, z0), (x1, y0, z0)], (0, -1, 0)),
        ([(x0, y0, z1), (x0, y1, z1), (x1, y1, z1), (x1, y0, z1)], (0, 0, 1)),
        ([(x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z0)], (0, 0, -1)),
    ]
    verts, idx = [], []
    for corners, n in faces:
        base = len(verts)
        for p, (u, v) in zip(corners, ((u0, v0), (u1, v0), (u1, v1), (u0, v1)), strict=True):
            verts.append((p, n, (u, v), bone_idx))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return verts, idx


def skin(submeshes: list[tuple[int, list, list]], texture_units: list[tuple[int, int]]) -> tuple[bytes, list]:
    """A .skin. submeshes: (geoset id, verts, indices), vertices numbered in
    order across all submeshes; texture_units: (submesh, texture combo index).
    Returns the file and the model's vertex list."""
    all_verts, tris, subs = [], [], []
    for geoset, verts, idx in submeshes:
        vstart, tstart = len(all_verts), len(tris)
        tris += [vstart + i for i in idx]
        all_verts += verts
        subs.append(struct.pack("<10H3f3ff", geoset, 0, vstart, len(verts), tstart, len(idx), 1, 0, 1, 0,
                                0, 0, 0, 0, 0, 0, 1.0))
    units = b"".join(struct.pack("<BbHHHHHHHHHHH", 0, 0, 0, sm, 0, 0xFFFF, 0, 0, 1, combo, 0, 0xFFFF, 0xFFFF)
                     for sm, combo in texture_units)
    head = 4 + 5 * 8 + 4
    d = Data(head)
    body = (d.arr(len(all_verts), struct.pack(f"<{len(all_verts)}H", *range(len(all_verts))))
            + d.arr(len(tris), struct.pack(f"<{len(tris)}H", *tris))
            + d.arr(len(all_verts) * 4, bytes(len(all_verts) * 4))
            + d.arr(len(subs), b"".join(subs))
            + d.arr(len(texture_units), units)
            + struct.pack("<I", 2))
    return b"SKIN" + body + bytes(d.buf), all_verts


def chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack("<I", len(payload)) + payload


def m2(*, verts, textures: list[tuple[int, int]], bounds, bones=None, sequences=None, attachments=None,
       flags: int = 0, skin_id: int, skel_id: int | None = None, afid=None, ribbons=None, particles=None,
       blend: int = 0, material_flags: int = UNLIT_TWO_SIDED) -> bytes:
    """A chunked M2. verts: (position, normal, uv, bone[, second uv]);
    textures: (type, TXID file id); bones: callables d -> bytes (bone());
    sequences: sequence() bytes; attachments: (id, bone, position);
    material_flags: the one material's (unlit and two-sided for the pixel
    tests, whose colours must come out exact)."""
    bones = bones or []
    sequences = sequences or []
    attachments = attachments or []
    ribbons = ribbons or []
    particles = particles or []

    def header(d: Data) -> bytes:
        name = b"m3d-test\0"
        vb = b"".join(struct.pack("<3f4B4B3f2f2f", *v[0], 255, 0, 0, 0, v[3], 0, 0, 0, *v[1], *v[2],
                                  *(v[4] if len(v) > 4 else (0.0, 0.0))) for v in verts)
        tex = b"".join(struct.pack("<II", t, 0) + struct.pack("<II", 0, 0) for t, _ in textures)
        att_lookup = [-1] * (max((a[0] for a in attachments), default=-1) + 1)
        for i, a in enumerate(attachments):
            att_lookup[a[0]] = i
        att = b"".join(struct.pack("<IHH3f", aid, b, 0, *pos) + track(d, "uint8", None)
                       for aid, b, pos in attachments)
        (bx0, by0, bz0), (bx1, by1, bz1) = bounds
        return (b"MD20" + struct.pack("<I", 274) + d.arr(len(name), name) + struct.pack("<I", flags)
                + d.arr(0, b"")                                              # global loops
                + d.arr(len(sequences), b"".join(sequences))
                + d.arr(len(sequences), struct.pack(f"<{len(sequences)}h", *range(len(sequences))))
                + d.arr(len(bones), b"".join(f(d) for f in bones))
                + d.arr(0, b"")                                              # key bone lookup
                + d.arr(len(verts), vb)
                + struct.pack("<I", 1)                                       # skin profiles
                + d.arr(0, b"")                                              # colours
                + d.arr(len(textures), tex)
                + d.arr(0, b"")                                              # texture weights
                + d.arr(0, b"")                                              # texture transforms
                + d.arr(0, b"")                                              # replaceable texture lookup
                + d.arr(1, struct.pack("<HH", material_flags, blend))        # materials
                + d.arr(0, b"")                                              # bone combos
                + d.arr(len(textures), struct.pack(f"<{len(textures)}H", *range(len(textures))))
                + d.arr(0, b"")                                              # texture coord combos
                + d.arr(0, b"")                                              # transparency lookup
                + d.arr(0, b"")                                              # texture transform lookup
                + struct.pack("<6ff6ff", bx0, by0, bz0, bx1, by1, bz1, 1.5, bx0, by0, bz0, bx1, by1, bz1, 1.5)
                + d.arr(0, b"") + d.arr(0, b"") + d.arr(0, b"")              # collision
                + d.arr(len(attachments), att)
                + d.arr(len(att_lookup), struct.pack(f"<{len(att_lookup)}h", *att_lookup))
                + bytes(8 * 4)                                               # events .. camera lookup
                + d.arr(len(ribbons), b"".join(f(d) for f in ribbons))
                + d.arr(len(particles), b"".join(f(d) for f in particles)))

    size = len(header(Data(0)))
    d = Data(size)
    body = header(d) + bytes(d.buf)
    out = chunk(b"MD21", body) + chunk(b"SFID", struct.pack("<I", skin_id))
    out += chunk(b"TXID", b"".join(struct.pack("<I", t) for _, t in textures))
    if skel_id:
        out += chunk(b"SKID", struct.pack("<I", skel_id))
    if afid:
        out += chunk(b"AFID", b"".join(struct.pack("<HHI", a, s, f) for a, s, f in afid))
    return out


def skel_chunk_with_arrays(cid: bytes, fixed: int, build) -> bytes:
    """A .skel chunk whose arrays point from the chunk's own start."""
    d = Data(fixed)
    head = build(d)
    assert len(head) == fixed, (cid, len(head), fixed)
    return chunk(cid, head + bytes(d.buf))


def skeleton(*, bones, sequences, parent: int | None = None, attachments=None, afid=None) -> bytes:
    out = chunk(b"SKL1", struct.pack("<IIII", 0x100, 0, 0, 0))
    if parent:
        out += chunk(b"SKPD", bytes(8) + struct.pack("<I", parent) + bytes(4))
    out += skel_chunk_with_arrays(b"SKS1", 32, lambda d: (
        d.arr(0, b"") + d.arr(len(sequences), b"".join(sequences))
        + d.arr(len(sequences), struct.pack(f"<{len(sequences)}h", *range(len(sequences)))) + bytes(8)))
    out += skel_chunk_with_arrays(b"SKB1", 16, lambda d: d.arr(len(bones), b"".join(f(d) for f in bones))
                                  + d.arr(0, b""))
    if attachments:
        look = [-1] * (max(a[0] for a in attachments) + 1)
        for i, a in enumerate(attachments):
            look[a[0]] = i
        out += skel_chunk_with_arrays(b"SKA1", 16, lambda d: (
            d.arr(len(attachments), b"".join(struct.pack("<IHH3f", aid, b, 0, *pos) + track(d, "uint8", None)
                                             for aid, b, pos in attachments))
            + d.arr(len(look), struct.pack(f"<{len(look)}h", *look))))
    if afid:
        out += chunk(b"AFID", b"".join(struct.pack("<HHI", a, s, f) for a, s, f in afid))
    return out


def anim_payload(times: list[int], values: list[bytes]) -> tuple[bytes, int, int]:
    """An .anim's data for one track: (bytes, timestamps offset, values offset)."""
    d = Data(0)
    ts = d.put(b"".join(struct.pack("<I", t) for t in times))
    vs = d.put(b"".join(values))
    return bytes(d.buf), ts, vs


def tilt(deg: float) -> tuple[int, ...]:
    """A rotation about WoW's x axis (the character's forward), compressed."""
    h = math.radians(deg) / 2
    return compquat((math.sin(h), 0.0, 0.0, math.cos(h)))


# ---------------------------------------------------------------- emitters

def const(v, kind_seqs: int = 1):
    """A track with one key at 0 in each of the first sequences (inline)."""
    return [("in", [0], [v])] * kind_seqs


def fblock(d: Data, keys: list, fmt: str) -> bytes:
    """A "fake animation block": fixed16 life fractions and their values."""
    times = struct.pack(f"<{len(keys)}h", *[round(t * 32767) for t, _ in keys])
    values = b"".join(struct.pack(fmt, *(v if isinstance(v, tuple) else (v,))) for _, v in keys)
    return d.arr(len(keys), times) + d.arr(len(keys), values)


def particle(d: Data, *, flags=0, position=(0.0, 0.0, 0.0), bone=0, texture=0, blend=4, kind=1, rows=1,
             cols=1, speed=0.0, speed_var=0.0, vrange=0.0, hrange=0.0, gravity=0.0, lifespan=1.0, life_var=0.0,
             rate=None, width=0.0, length=0.0, zsource=0.0, color=((0.0, (255.0, 255.0, 255.0)),),
             alpha=((0.0, 32767),), scale=((0.0, (0.3, 0.3)),), scale_var=(0.0, 0.0), drag=0.0,
             enabled=None) -> bytes:
    """One M2Particle (version 264+, 0x1EC bytes). Track arguments are
    constants (one key) unless given as track() sequence lists."""
    def tr(kind_, v, interp=1):
        return track(d, kind_, v if isinstance(v, list) else const(v), interp)

    rate = rate if rate is not None else 10.0
    rate_interp = 0 if isinstance(rate, list) else 1
    out = (struct.pack("<iI3fHH", -1, flags, *position, bone, texture) + bytes(16)
           + struct.pack("<BBHbbhHH", blend, kind, 0, 0, 0, 0, rows, cols)
           + tr("float", speed) + tr("float", speed_var) + tr("float", vrange) + tr("float", hrange)
           + tr("float", gravity) + tr("float", lifespan) + struct.pack("<f", life_var)
           + track(d, "float", rate if isinstance(rate, list) else const(rate), rate_interp)
           + struct.pack("<f", 0.0)
           + tr("float", width) + tr("float", length) + tr("float", zsource)
           + fblock(d, list(color), "<3f") + fblock(d, list(alpha), "<h") + fblock(d, list(scale), "<2f")
           + struct.pack("<2f", *scale_var)
           + fblock(d, [], "<H") + fblock(d, [], "<H")
           + struct.pack("<3f2f2f4f", 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, drag, 0.0, 0.0, 0.0, 0.0)
           + bytes(24) + struct.pack("<3ff", 0.0, 0.0, 0.0, 0.0) + bytes(16) + bytes(8)
           + track(d, "uint8", enabled if enabled is not None else [], 0)
           + bytes(16))
    assert len(out) == 0x1EC, hex(len(out))
    return out


def ribbon(d: Data, *, bone=0, position=(0.0, 0.0, 0.0), texture=0, material=0, color=(1.0, 0.5, 0.0),
           above=0.08, below=0.08, edges_per_second=60.0, lifetime=0.5, gravity=0.0) -> bytes:
    """One M2Ribbon (0xB0 bytes)."""
    out = (struct.pack("<iI3f", -1, bone, *position)
           + d.arr(1, struct.pack("<H", texture)) + d.arr(1, struct.pack("<H", material))
           + track(d, "float3", const(color)) + track(d, "int16", const(32767))
           + track(d, "float", const(above)) + track(d, "float", const(below))
           + struct.pack("<3fHH", edges_per_second, lifetime, gravity, 1, 1)
           + track(d, "uint16", []) + track(d, "uint8", [])
           + struct.pack("<hBB", 0, 0, 0))
    assert len(out) == 0xB0, hex(len(out))
    return out


GLOW_COLOURS = ((0.0, (255.0, 0.0, 0.0)), (1.0, (0.0, 0.0, 255.0)))
# 100 a second until 50 ms, stepped, read at each step's middle: the first
# 1/30 s step spawns 3
GLOW_RATE = [("in", [0, 50], [100.0, 0.0])]


def glow(enabled=None) -> bytes:
    return m2(verts=[], textures=[(0, TEX_WHITE)], bounds=((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)), skin_id=0,
              bones=[lambda d: bone(d, PIVOT_ROOT, -1, 0)], sequences=[sequence(0, 1000, 0x20)],
              particles=[lambda d: particle(d, rate=GLOW_RATE, color=GLOW_COLOURS, enabled=enabled)])


def spread() -> bytes:
    sphere = dict(kind=2, width=0.5, length=0.3, vrange=math.pi / 4, hrange=math.pi, speed=1.0, gravity=4.0,
                  rate=200.0, lifespan=2.0)
    plane = dict(kind=1, width=1.0, length=0.4, speed=2.0, gravity=4.0, rate=200.0, lifespan=2.0)
    return m2(verts=[], textures=[(0, TEX_WHITE)], bounds=((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)), skin_id=0,
              bones=[lambda d: bone(d, PIVOT_ROOT, -1, 0)], sequences=[sequence(0, 1000, 0x20)],
              particles=[lambda d: particle(d, **sphere), lambda d: particle(d, **plane)])


def ribbon_model() -> bytes:
    # a bone at (0, 0, 1) turning about WoW x once a second; the ribbon 0.6
    # out along its z, so the trail is a ring facing the camera
    turn = [("in", [0, 250, 500, 750, 1000], [tilt(a) for a in (0, 90, 180, 270, 360)])]
    return m2(verts=[], textures=[(0, TEX_WHITE)], bounds=((-0.8, -0.8, 0.2), (0.8, 0.8, 1.8)), skin_id=0,
              bones=[lambda d: bone(d, (0.0, 0.0, 1.0), -1, 0, rot=turn)], sequences=[sequence(0, 1000, 0x20)],
              ribbons=[lambda d: ribbon(d, position=(0.0, 0.0, 1.6))], blend=4)


# ---------------------------------------------------------------- the scene

PIVOT_ROOT = (0.0, 0.0, 0.0)
PIVOT_HEAD = (0.0, 0.0, 2.0)
CRC_ROOT, CRC_HEAD = 0xA0A0A0A0, 0xA1A1A1A1
ATTACH_RIGHT = (0.0, 0.7, 0.8)


def scene() -> dict:
    blps = blp_unit_cases()
    files: dict[int, bytes] = {tid: c[3] for tid, c in blps.items()}

    # the character: textures 0 skin (composited), 1 red, 2 teal ramp, 3 hair (type 6)
    subs = [
        (0, *box((-0.3, -0.4, 0.4), (0.3, 0.4, 2.0), 0)),            # body, material 1
        (301, *box((-0.25, -0.25, 2.05), (0.25, 0.25, 2.55), 1)),     # head, on the head bone
        (401, *box((-0.2, -0.9, 1.2), (0.2, -0.5, 1.6), 0)),          # off in the recipe
        (402, *box((-0.2, 0.5, 1.2), (0.2, 0.9, 1.6), 0)),            # on in the recipe, hair texture
        (501, *box((-0.2, -0.35, 0.0), (0.2, -0.05, 0.35), 0)),       # group 5: hidden
        (503, *box((-0.2, 0.05, 0.0), (0.2, 0.35, 0.35), 0)),         # group 5 = 3: shown
        (1701, *box((-0.2, -0.9, 1.8), (0.2, -0.5, 2.2), 0)),         # "17..": off by default
    ]
    combos = [0, 1, 2, 3, 1, 1, 2]
    skin_file, verts = skin(subs, list(enumerate(combos)))
    files[CHAR_SKIN] = skin_file
    files[CHAR_M2] = m2(verts=verts, textures=[(1, 0), (0, TEX_RED), (0, TEX_ALPHA), (6, 0)],
                        bounds=((-0.35, -0.95, 0.0), (0.35, 0.95, 2.6)), skin_id=CHAR_SKIN, skel_id=CHILD_SKEL)

    # the parent skeleton: the bones, the attachment, a Stand that tilts the
    # head -30 degrees (AFM2 only); the child's +30 (AFSB) must win
    data, ts, vs = anim_payload([0, 500, 1000], [struct.pack("<4H", *tilt(a)) for a in (0, -30, 0)])
    files[PARENT_ANIM] = chunk(b"AFM2", data)
    files[PARENT_SKEL] = skeleton(
        bones=[lambda d: bone(d, PIVOT_ROOT, -1, CRC_ROOT),
               lambda d: bone(d, PIVOT_HEAD, 0, CRC_HEAD, rot=[("ext", 3, ts, vs)])],
        sequences=[sequence(0, 1000, 0)], attachments=[(1, 0, ATTACH_RIGHT)], afid=[(0, 0, PARENT_ANIM)])

    data, ts, vs = anim_payload([0, 500, 1000], [struct.pack("<4H", *tilt(a)) for a in (0, 30, 0)])
    files[CHILD_ANIM] = chunk(b"AFM2", b"\xff" * 64) + chunk(b"AFSB", data)
    files[CHILD_SKEL] = skeleton(
        bones=[lambda d: bone(d, PIVOT_ROOT, -1, CRC_ROOT),
               lambda d: bone(d, PIVOT_HEAD, 0, CRC_HEAD, rot=[("ext", 3, ts, vs)])],
        sequences=[sequence(0, 1000, 0)], parent=PARENT_SKEL, afid=[(0, 0, CHILD_ANIM)])

    # the item: its own bone lifted 0.15 at 500 ms by a raw (unchunked) .anim
    data, ts, vs = anim_payload([0, 500, 1000], [struct.pack("<3f", 0, 0, z) for z in (0, 0.15, 0)])
    files[ITEM_ANIM] = data
    sub = [(0, *box((-0.08, -0.08, -0.08), (0.08, 0.08, 0.08), 0))]
    files[ITEM_SKIN], verts = skin(sub, [(0, 0)])
    files[ITEM_M2] = m2(verts=verts, textures=[(2, 0)], bounds=((-0.1, -0.1, -0.1), (0.1, 0.1, 0.1)),
                        skin_id=ITEM_SKIN, bones=[lambda d: bone(d, PIVOT_ROOT, -1, 0, trans=[("ext", 3, ts, vs)])],
                        sequences=[sequence(0, 1000, 0)],
                        attachments=[(1, 0, (0.0, 0.0, 0.2)), (2, 0, (0.0, 0.0, -0.4))],
                        afid=[(0, 0, ITEM_ANIM)])

    # the item's glow: no bones, its own texture
    sub = [(0, *box((-0.04, -0.04, -0.04), (0.04, 0.04, 0.04), 0))]
    files[VISUAL_SKIN], verts = skin(sub, [(0, 0)])
    files[VISUAL_M2] = m2(verts=verts, textures=[(0, TEX_ORANGE)], bounds=((-0.05,) * 3, (0.05,) * 3),
                          skin_id=VISUAL_SKIN)

    # the belt: bones in the reverse order (local 1 is the character's 0),
    # so it only stays put if the bones are matched by pivot and name
    subs = [(1801, *box((-0.36, -0.45, 0.9), (0.36, 0.0, 1.1), 1)),
            (1802, *box((-0.36, 0.0, 0.9), (0.36, 0.45, 1.1), 1))]
    files[BELT_SKIN], verts = skin(subs, [(0, 0), (1, 0)])
    files[BELT_M2] = m2(verts=verts, textures=[(2, 0)], bounds=((-0.4, -0.5, 0.9), (0.4, 0.5, 1.1)),
                        skin_id=BELT_SKIN, bones=[lambda d: bone(d, PIVOT_HEAD, 1, CRC_HEAD),
                                                  lambda d: bone(d, PIVOT_ROOT, -1, CRC_ROOT)])

    # a skinned model wearing the character's composite (top-left quarter)
    q = (0.05, 0.05, 0.4, 0.4)
    subs = [(0, *box((-0.2, -0.9, 0.6), (0.2, -0.5, 1.0), 0, q)),
            (3, *box((-0.2, -0.9, 0.2), (0.2, -0.5, 0.5), 0, q))]
    files[SKINNED_SKIN], verts = skin(subs, [(0, 0), (1, 0)])
    files[SKINNED_M2] = m2(verts=verts, textures=[(1, 0)], bounds=((-0.2, -0.9, 0.2), (0.2, -0.5, 1.0)),
                           skin_id=SKINNED_SKIN, bones=[lambda d: bone(d, PIVOT_ROOT, -1, CRC_ROOT),
                                                        lambda d: bone(d, PIVOT_HEAD, 0, CRC_HEAD)])

    recipe = {
        "v": 1,
        "model": CHAR_M2,
        "geosets": {"set": {"401": False, "402": True}, "groups": {"5": 3}},
        "materials": [{"type": 1, "width": 256, "height": 256, "layers": [
            {"file": TEX_MULT, "target": 1201, "x": 128, "y": 128, "w": 128, "h": 128, "blend": 4},
            {"file": TEX_BASE, "target": 1, "x": 0, "y": 0, "w": 256, "h": 256, "blend": 0},
            {"file": TEX_OVERLAY, "target": 1103, "x": 128, "y": 0, "w": 128, "h": 128, "blend": 15},
            {"file": MISSING_LAYER, "target": 1301, "x": 0, "y": 0, "w": 64, "h": 64, "blend": 15},
        ]}],
        "textures": {"6": TEX_PURPLE},
        "skinned": [{"model": SKINNED_M2, "textures": {"1": "material:1"}, "geosets": {"set": {}, "groups": {}}}],
        "attachments": [
            {"slot": 16, "attach": 1, "model": ITEM_M2, "textures": {"2": TEX_GREEN},
             "visuals": [{"model": VISUAL_M2, "attach": 1}, {"model": GLOW_M2, "attach": 2}], "scale": 1.0},
            {"slot": 17, "attach": 2, "model": MISSING_ITEM, "textures": {}, "visuals": [], "scale": 1.0},
        ],
        "collections": [{"slot": 6, "model": BELT_M2, "textures": {"2": TEX_CYAN},
                         "geosets": {"set": {}, "groups": {"18": 2}}}],
        "animation": 0,
    }

    # Points on front faces (WoW model coordinates) and what must show
    # there; None is the clear background. "t0"/"t500": at 0 and 500 ms.
    expect = {
        "t0": [
            ((0.3, -0.2, 1.6), YELLOW, "body, material base (top-left)"),
            ((0.3, 0.08, 1.6), BLUE, "body, alpha-blended layer, opaque half (top-right)"),
            ((0.3, 0.32, 1.6), YELLOW, "body, alpha-blended layer, clear half"),
            ((0.3, 0.2, 0.7), MULTIPLIED, "body, multiply layer (bottom-right)"),
            ((0.3, -0.2, 0.7), YELLOW, "body, bottom-left"),
            ((0.25, 0.0, 2.3), RED, "head, DXT1"),
            ((0.25, 0.22, 2.5), RED, "head top-right corner, at rest"),
            ((0.25, -0.22, 2.5), RED, "head top-left corner, at rest"),
            ((0.2, 0.7, 1.4), PURPLE, "geoset 402, hair texture from the recipe"),
            ((0.2, -0.7, 1.4), None, "geoset 401, turned off"),
            ((0.2, -0.7, 2.0), None, "geoset 1701, off by default"),
            ((0.2, -0.2, 0.2), None, "geoset 501, hidden by group 5"),
            ((0.2, 0.2, 0.2), RED, "geoset 503, group 5 = 3"),
            ((0.08, 0.7, 0.78), GREEN, "item at the right hand, at rest"),
            ((0.04, 0.7, 1.0), ORANGE, "glow at the item's attachment"),
            ((0.36, 0.22, 1.0), CYAN, "belt, geoset 1802"),
            ((0.36, -0.22, 1.0), YELLOW, "belt geoset 1801 hidden: the body behind"),
            ((0.2, -0.7, 0.8), YELLOW, "skinned model, the composite"),
            ((0.2, -0.7, 0.35), None, "skinned model, geoset 3 not chosen"),
        ],
        "t500": [
            ((0.25, 0.22, 2.5), None, "head tilted by the child skeleton's Stand: corner gone"),
            ((0.25, -0.22, 2.5), RED, "head tilted by the child skeleton's Stand: other corner"),
            ((0.08, 0.7, 0.78), None, "item lifted by its own Stand"),
            ((0.08, 0.7, 0.95), GREEN, "item lifted by its own Stand"),
            ((0.36, 0.22, 1.0), CYAN, "belt still in place (bones matched)"),
        ],
        "redraw": [((0.2, -0.7, 1.4), TEAL, "geoset 401 turned on by the second recipe")],
        "missing": sorted([MISSING_ITEM, MISSING_LAYER]),
        "material": {"type": 1, "width": 256, "height": 256, "points": [
            ((64, 64), YELLOW), ((150, 64), BLUE), ((240, 64), YELLOW), ((192, 192), MULTIPLIED), ((64, 192), YELLOW)]},
    }
    files[GLOW_M2] = glow()
    files[GLOW_OFF_M2] = glow(enabled=const(0))
    files[SPREAD_M2] = spread()
    files[RIBBON_M2] = ribbon_model()
    # the glow under the item (its attachment 2), in the character's coordinates
    expect["glow_at"] = (0.0, 0.7, 0.4)
    return {"files": files, "recipe": recipe, "expect": expect, "blps": blps}


def emitter_recipe(model: int) -> dict:
    """A recipe whose "character" is one emitter model, for pixel tests."""
    return {"v": 1, "model": model, "geosets": {"set": {}, "groups": {}}, "materials": [], "textures": {},
            "skinned": [], "attachments": [], "collections": [], "animation": 0}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: model3d_fixtures.py <dir>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    s = scene()
    ext = {CHAR_M2: "m2", ITEM_M2: "m2", VISUAL_M2: "m2", BELT_M2: "m2", SKINNED_M2: "m2", GLOW_M2: "m2",
           GLOW_OFF_M2: "m2", SPREAD_M2: "m2", RIBBON_M2: "m2",
           CHAR_SKIN: "skin", ITEM_SKIN: "skin", VISUAL_SKIN: "skin", BELT_SKIN: "skin", SKINNED_SKIN: "skin",
           CHILD_SKEL: "skel", PARENT_SKEL: "skel", CHILD_ANIM: "anim", PARENT_ANIM: "anim", ITEM_ANIM: "anim"}
    for fid, data in s["files"].items():
        (out / f"{fid}.{ext.get(fid, 'blp')}").write_bytes(data)
    (out / "recipe.json").write_text(json.dumps(s["recipe"], indent=1) + "\n")
    print(f"{len(s['files'])} files in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
