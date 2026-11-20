"""Compare our render of a character with Blizzard's: stdlib only.

Both images come as straight RGBA on a transparent background, ours
already scaled so its body is as tall as the reference's (render.py does
that in the browser). Then:

- align: the feet on the same row, the torso's middle on the same column,
  then the best shift within a few pixels (masks are one Python int per row,
  so an IoU is a few hundred AND/OR/popcounts);
- the silhouette's IoU;
- regions of the reference's body (head, torso, legs, feet, hands, weapon),
  each with its own IoU, mean colour and a 4x4x4 colour histogram over the
  pixels both images cover, ours brightness-matched first (the two renderers
  light differently);
- where the weapon mass sits (the character's right or left);
- a diff heatmap and the contact sheet (reference | ours | diff), as PNG.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

ALPHA = 40  # a pixel belongs to the silhouette from this alpha up
REGIONS = ("head", "torso", "legs", "feet", "hands", "weapon")
# Bands of the body's height, from the top of the head to the soles.
BANDS = {"head": (0.0, 0.2), "torso": (0.2, 0.5), "legs": (0.5, 0.88), "feet": (0.88, 1.01)}
HANDS_BAND = (0.40, 0.64)


@dataclass
class Img:
    w: int
    h: int
    px: bytearray = field(repr=False)

    @classmethod
    def blank(cls, w: int, h: int, rgba=(0, 0, 0, 0)) -> Img:
        return cls(w, h, bytearray(bytes(rgba) * (w * h)))

    def at(self, x: int, y: int) -> tuple[int, int, int, int]:
        i = (y * self.w + x) * 4
        return self.px[i], self.px[i + 1], self.px[i + 2], self.px[i + 3]

    def put(self, x: int, y: int, rgba) -> None:
        i = (y * self.w + x) * 4
        self.px[i:i + 4] = bytes(rgba)


# ------------------------------------------------------------------ PNG

def png_encode(img: Img) -> bytes:
    raw = bytearray()
    stride = img.w * 4
    for y in range(img.h):
        raw.append(0)
        raw += img.px[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", img.w, img.h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def png_decode(data: bytes) -> Img:
    """8-bit RGBA or RGB, not interlaced (our own sheets and the tests';
    the references go through the browser, render.py)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w = 8, bytearray(), 0
    h = depth = ctype = 0
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h, depth, ctype, _c, _f, inter = struct.unpack(">IIBBBBB", body)
            if depth != 8 or ctype not in (2, 6) or inter:
                raise ValueError("only 8-bit RGB(A), not interlaced")
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    bpp = 4 if ctype == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = w * bpp
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _y in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += line
        prev = line
    if bpp == 3:
        rgba = bytearray(w * h * 4)
        rgba[0::4], rgba[1::4], rgba[2::4] = out[0::3], out[1::3], out[2::3]
        rgba[3::4] = b"\xff" * (w * h)
        out = rgba
    return Img(w, h, out)


# ------------------------------------------------------------------ masks

def mask_rows(img: Img, alpha: int = ALPHA) -> list[int]:
    """One int per row, bit x set where the pixel is opaque enough."""
    rows = []
    a = img.px[3::4]
    for y in range(img.h):
        line = a[y * img.w:(y + 1) * img.w]
        bits = 0
        for x, v in enumerate(line):
            if v >= alpha:
                bits |= 1 << x
        rows.append(bits)
    return rows


def extent(rows: list[int], min_px: int = 3) -> tuple[int, int] | None:
    """The body's top and bottom row: the first and last with min_px pixels."""
    full = [y for y, r in enumerate(rows) if r.bit_count() >= min_px]
    return (full[0], full[-1]) if full else None


def span(bits: int) -> tuple[int, int] | None:
    if not bits:
        return None
    return (bits & -bits).bit_length() - 1, bits.bit_length() - 1


def axis(rows: list[int], top: int, bottom: int) -> int:
    """The body's vertical axis: the median column of its pixels below the
    head and above the feet (a weapon is thin next to the body's mass)."""
    counts: dict[int, int] = {}
    h = bottom - top
    for y in range(top + int(0.15 * h), top + int(0.9 * h) + 1):
        bits = rows[y]
        while bits:
            low = bits & -bits
            x = low.bit_length() - 1
            counts[x] = counts.get(x, 0) + 1
            bits ^= low
    total = sum(counts.values())
    seen = 0
    for x in sorted(counts):
        seen += counts[x]
        if seen * 2 >= total:
            return x
    return 0


def run_at(bits: int, x: int, gap: int = 0) -> tuple[int, int] | None:
    """The run of set bits holding column x, joined with the runs beside it
    that are at most `gap` pixels away (an arm hanging by the torso)."""
    if not bits >> x & 1:
        return None
    lo = x
    while True:
        while lo > 0 and bits >> (lo - 1) & 1:
            lo -= 1
        nxt = next((k for k in range(lo - 2, max(-1, lo - 2 - gap), -1) if bits >> k & 1), None)
        if nxt is None:
            break
        lo = nxt
    hi = x
    top = bits.bit_length()
    while True:
        while bits >> (hi + 1) & 1:
            hi += 1
        nxt = next((k for k in range(hi + 2, min(top, hi + 2 + gap)) if bits >> k & 1), None)
        if nxt is None:
            break
        hi = nxt
    return lo, hi


def torso_core(rows: list[int], top: int, bottom: int) -> tuple[int, int]:
    """The torso's left and right edge: the median of each upper-body row's
    run through the body's axis (arms included: they hang along it; a
    weapon held apart is not)."""
    h = bottom - top
    c = axis(rows, top, bottom)
    gap = max(2, int(0.08 * h))
    ls, rs = [], []
    for y in range(top + int(0.25 * h), top + int(0.45 * h) + 1):
        s = run_at(rows[y], c, gap) if 0 <= y < len(rows) else None
        if s:
            ls.append(s[0])
            rs.append(s[1])
    if not ls:
        return c, c
    ls.sort()
    rs.sort()
    return ls[len(ls) // 2], rs[len(rs) // 2]


def shifted(rows: list[int], dx: int, dy: int, n: int) -> list[int]:
    out = [0] * n
    for y, r in enumerate(rows):
        t = y + dy
        if 0 <= t < n:
            out[t] = (r << dx) if dx >= 0 else (r >> -dx)
    return out


def iou(a: list[int], b: list[int]) -> float:
    inter = union = 0
    for x, y in zip(a, b, strict=False):
        inter += (x & y).bit_count()
        union += (x | y).bit_count()
    return inter / union if union else 0.0


def align(ref: list[int], ours: list[int], search: int = 8) -> tuple[int, int, float]:
    """(dx, dy, IoU) that put ours on the reference: feet on feet, torso
    middle on torso middle, then the best shift within `search` pixels."""
    er, eo = extent(ref), extent(ours)
    if not er or not eo:
        return 0, 0, 0.0
    cr, co = torso_core(ref, *er), torso_core(ours, *eo)
    dx0 = (cr[0] + cr[1]) // 2 - (co[0] + co[1]) // 2
    dy0 = er[1] - eo[1]
    best = (dx0, dy0, -1.0)
    n = len(ref)
    for dy in range(dy0 - 3, dy0 + 4):
        for dx in range(dx0 - search, dx0 + search + 1):
            v = iou(ref, shifted(ours, dx, dy, n))
            if v > best[2]:
                best = (dx, dy, v)
    return best


def place(img: Img, dx: int, dy: int, w: int, h: int) -> Img:
    """`img` moved by (dx, dy) on a w x h transparent canvas."""
    out = Img.blank(w, h)
    for y in range(img.h):
        t = y + dy
        if not 0 <= t < h:
            continue
        x0, x1 = max(0, -dx), min(img.w, w - dx)
        if x0 >= x1:
            continue
        out.px[(t * w + x0 + dx) * 4:(t * w + x1 + dx) * 4] = img.px[(y * img.w + x0) * 4:(y * img.w + x1) * 4]
    return out


# ------------------------------------------------------------------ regions

def dilate(rows: list[int], r: int) -> list[int]:
    out = []
    for y in range(len(rows)):
        m = 0
        for k in range(max(0, y - r), min(len(rows), y + r + 1)):
            v = rows[k]
            for d in range(r + 1):
                m |= (v << d) | (v >> d)
        out.append(m)
    return out


def region_masks(ref: list[int], ours: list[int], w: int, weapon: list[int] | None = None) -> dict[str, list[int]]:
    """Each region as row masks over the reference's frame. The weapon is
    `weapon` when given (what our weapons cover: our picture with them less
    the one without), else whatever either image has outside the body's
    column; hands are the arms' ends; the rest is cut in bands of the
    reference body's height."""
    e = extent(ref)
    n = len(ref)
    if not e:
        return {r: [0] * n for r in REGIONS}
    top, bottom = e
    h = max(1, bottom - top)
    left, right = torso_core(ref, top, bottom)
    cw = max(1, right - left)
    full = (1 << w) - 1
    lo_out = max(0, left - int(0.08 * cw))
    hi_out = min(w - 1, right + int(0.08 * cw))
    inside = full & ~((1 << lo_out) - 1) & ((1 << (hi_out + 1)) - 1)
    out = {r: [0] * n for r in REGIONS}
    hand_lo, hand_hi = top + int(HANDS_BAND[0] * h), top + int(HANDS_BAND[1] * h)
    lane = int(0.22 * cw)
    hand_cols = (((1 << (left + lane)) - 1) & ~((1 << max(0, lo_out)) - 1)) | \
                (((1 << (hi_out + 1)) - 1) & ~((1 << max(0, right - lane)) - 1))
    for y in range(n):
        any_px = ref[y] | ours[y]
        if not any_px:
            continue
        if weapon is not None:
            wp = weapon[y] & full
        else:
            wp = any_px & ~inside if top <= y <= bottom else (any_px if y < top else 0)
        out["weapon"][y] = wp
        weapon_y = wp
        rest = full & ~weapon_y
        if hand_lo <= y <= hand_hi:
            out["hands"][y] = rest & hand_cols
            rest &= ~hand_cols
        f = (y - top) / h
        for name, (a, b) in BANDS.items():
            if a <= f < b:
                out[name][y] = rest
                break
    return out


def _luma(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def colour_stats(ref: Img, ours: Img, both: list[int], gain: float = 1.0) -> dict | None:
    """Mean colours and the histogram intersection over the pixels of
    `both` (row masks), ours times `gain`."""
    n = 0
    sr = [0, 0, 0]
    so = [0, 0, 0]
    hr: dict[int, int] = {}
    ho: dict[int, int] = {}
    for y, bits in enumerate(both):
        while bits:
            low = bits & -bits
            x = low.bit_length() - 1
            bits ^= low
            i = (y * ref.w + x) * 4
            a = ref.px[i:i + 3]
            b = [min(255, int(v * gain)) for v in ours.px[i:i + 3]]
            for k in range(3):
                sr[k] += a[k]
                so[k] += b[k]
            ka = (a[0] >> 6) << 4 | (a[1] >> 6) << 2 | (a[2] >> 6)
            kb = (b[0] >> 6) << 4 | (b[1] >> 6) << 2 | (b[2] >> 6)
            hr[ka] = hr.get(ka, 0) + 1
            ho[kb] = ho.get(kb, 0) + 1
            n += 1
    if not n:
        return None
    mr = [v / n for v in sr]
    mo = [v / n for v in so]
    inter = sum(min(c, ho.get(k, 0)) for k, c in hr.items()) / n
    dist = sum((p - q) ** 2 for p, q in zip(mr, mo, strict=True)) ** 0.5
    return {"ref": [round(v) for v in mr], "ours": [round(v) for v in mo], "distance": round(dist, 1),
            "histogram": round(inter, 3), "pixels": n}


def gain_of(ref: Img, ours: Img, both: list[int]) -> float:
    """Ours' brightness to the reference's, over the pixels both cover."""
    lr = lo = 0.0
    for y, bits in enumerate(both):
        while bits:
            low = bits & -bits
            x = low.bit_length() - 1
            bits ^= low
            i = (y * ref.w + x) * 4
            lr += _luma(*ref.px[i:i + 3])
            lo += _luma(*ours.px[i:i + 3])
    return max(0.5, min(2.0, lr / lo)) if lo > 0 else 1.0


def side_mass(rows: list[int], weapon: list[int], centre: int) -> tuple[int, int]:
    """Weapon pixels of one image left and right of the body's middle."""
    left = right = 0
    lmask = (1 << centre) - 1
    for r, wp in zip(rows, weapon, strict=False):
        m = r & wp
        left += (m & lmask).bit_count()
        right += (m & ~lmask).bit_count()
    return left, right


def compare(ref: Img, ours: Img, bare: Img | None = None) -> dict:
    """Every number the grade reads, plus where ours went (dx, dy). `bare`:
    our picture without the held items, on the same pixels as `ours`."""
    rr, ro = mask_rows(ref), mask_rows(ours)
    rr += [0] * max(0, ours.h - ref.h)
    dx, dy, v = align(rr, ro)
    n = max(ref.h, ours.h)
    placed = place(ours, dx, dy, ref.w, ref.h)
    po = mask_rows(placed)
    rr = rr[:ref.h]
    weapon = None
    pb = None
    if bare is not None:
        pb = mask_rows(place(bare, dx, dy, ref.w, ref.h))
        weapon = dilate([a & ~b for a, b in zip(po, pb, strict=True)], 3)
    regions = region_masks(rr, po, ref.w, weapon)
    both_all = [a & b for a, b in zip(rr, po, strict=True)]
    gain = gain_of(ref, placed, both_all)
    e = extent(rr)
    centre = axis(rr, *e) if e else ref.w // 2
    out = {"iou": round(iou(rr, po), 3), "dx": dx, "dy": dy, "gain": round(gain, 3), "regions": {},
           "height": {"ref": (e[1] - e[0]) if e else 0}, "size": [ref.w, n]}
    for name, m in regions.items():
        a = [x & y for x, y in zip(rr, m, strict=True)]
        b = [x & y for x, y in zip(po, m, strict=True)]
        ra, rb = sum(x.bit_count() for x in a), sum(x.bit_count() for x in b)
        stats = colour_stats(ref, placed, [x & y for x, y in zip(a, b, strict=True)], gain)
        out["regions"][name] = {"iou": round(iou(a, b), 3) if ra or rb else None, "ref_px": ra, "our_px": rb,
                                "colour": stats}
    if pb is not None:
        # theirs: what the reference has beyond our body without weapons
        body = dilate(pb, 4)
        extra = [a & ~b for a, b in zip(rr, body, strict=True)]
        full = (1 << ref.w) - 1
        out["weapon_side"] = {"ref": side_mass(extra, [full] * len(extra), centre),
                              "ours": side_mass(po, regions["weapon"], centre)}
    else:
        out["weapon_side"] = {"ref": side_mass(rr, regions["weapon"], centre),
                              "ours": side_mass(po, regions["weapon"], centre)}
    out["_placed"] = placed
    return out


# ------------------------------------------------------------------ pictures

def heatmap(ref: Img, ours: Img, gain: float = 1.0) -> Img:
    """White where neither, red where only the reference, blue where only
    ours, and grey to orange by colour difference where both."""
    out = Img.blank(ref.w, ref.h, (255, 255, 255, 255))
    for y in range(ref.h):
        for x in range(ref.w):
            i = (y * ref.w + x) * 4
            a, b = ref.px[i + 3] >= ALPHA, ours.px[i + 3] >= ALPHA
            if a and b:
                d = sum(abs(ref.px[i + k] - min(255, ours.px[i + k] * gain)) for k in range(3)) / 3 / 255
                t = min(1.0, max(0.0, d - 0.08) * 2.5)
                out.px[i:i + 4] = bytes((int(200 + 55 * t), int(200 - 90 * t), int(200 - 200 * t), 255))
            elif a:
                out.px[i:i + 4] = b"\xd0\x20\x20\xff"
            elif b:
                out.px[i:i + 4] = b"\x20\x50\xd0\xff"
    return out


def over_white(img: Img) -> Img:
    out = Img.blank(img.w, img.h, (255, 255, 255, 255))
    p, q = img.px, out.px
    for i in range(0, len(p), 4):
        a = p[i + 3]
        if a:
            for k in range(3):
                q[i + k] = (p[i + k] * a + 255 * (255 - a)) // 255
    return out


def crop_box(*imgs: Img, pad: int = 12) -> tuple[int, int, int, int]:
    """The box holding every image's silhouette, padded."""
    x0 = y0 = 1 << 30
    x1 = y1 = -1
    for img in imgs:
        rows = mask_rows(img, 1)
        for y, r in enumerate(rows):
            s = span(r)
            if s:
                x0, x1 = min(x0, s[0]), max(x1, s[1])
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return 0, 0, imgs[0].w, imgs[0].h
    return max(0, x0 - pad), max(0, y0 - pad), min(imgs[0].w, x1 + pad + 1), min(imgs[0].h, y1 + pad + 1)


def crop(img: Img, box: tuple[int, int, int, int]) -> Img:
    x0, y0, x1, y1 = box
    out = Img.blank(x1 - x0, y1 - y0)
    for y in range(y0, y1):
        out.px[(y - y0) * out.w * 4:(y - y0 + 1) * out.w * 4] = img.px[(y * img.w + x0) * 4:(y * img.w + x1) * 4]
    return out


def sheet(panels: list[Img], gap: int = 8) -> Img:
    """Panels side by side on white."""
    h = max(p.h for p in panels)
    w = sum(p.w for p in panels) + gap * (len(panels) - 1)
    out = Img.blank(w, h, (255, 255, 255, 255))
    x = 0
    for p in panels:
        for y in range(p.h):
            out.px[(y * w + x) * 4:(y * w + x + p.w) * 4] = p.px[y * p.w * 4:(y + 1) * p.w * 4]
        x += p.w + gap
    return out


def contact_sheet(ref: Img, placed: Img, gain: float = 1.0) -> bytes:
    box = crop_box(ref, placed)
    r, o = crop(ref, box), crop(placed, box)
    return png_encode(sheet([over_white(r), over_white(o), heatmap(r, o, gain)]))
