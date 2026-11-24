"""Anti-aliased shapes and icons as true-alpha Tk images.

A shape is first rendered once into a coverage mask (one byte per pixel,
0..255) using signed-distance maths. Turning a mask into an image of any
colour or opacity is then pure byte manipulation plus PNG encoding, which is
fast enough to do per animation frame. Images carry real alpha, so they blend
correctly over anything drawn underneath them on a canvas.
"""

from __future__ import annotations

import base64
import math
import struct
import tkinter as tk
import zlib
from collections import OrderedDict
from typing import Callable, Iterable

Mask = tuple[int, int, bytes]  # width, height, coverage bytes

_masks: dict[tuple, Mask] = {}
_photos: OrderedDict[tuple, tk.PhotoImage] = OrderedDict()
MAX_PHOTOS = 1500


def _cover(d: float) -> int:
    c = 0.5 - d
    if c <= 0:
        return 0
    if c >= 1:
        return 255
    return int(c * 255 + 0.5)


# ------------------------------------------------------------------ masks --
def _corner(r: float) -> list[bytes]:
    """Coverage of the top-left c x c block of any rounded rect with radius r."""
    c = int(math.ceil(r))
    rows = []
    for y in range(c):
        qy = r - y - 0.5
        row = bytearray(c)
        for x in range(c):
            qx = r - x - 0.5
            d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
            row[x] = _cover(d)
        rows.append(bytes(row))
    return rows


def _generic(w: int, h: int, sdf: Callable[[float, float], float]) -> bytes:
    out = bytearray(w * h)
    i = 0
    for y in range(h):
        py = y + 0.5
        for x in range(w):
            out[i] = _cover(sdf(x + 0.5, py))
            i += 1
    return bytes(out)


def rrect(w: int, h: int, r: float) -> Mask:
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0.0, min(float(r), w / 2, h / 2))
    key = ("rr", w, h, round(r, 2))
    m = _masks.get(key)
    if m:
        return m
    c = int(math.ceil(r))
    if 2 * c > w or 2 * c > h:
        hw, hh = w / 2, h / 2

        def sdf(px: float, py: float) -> float:
            qx = abs(px - hw) - (hw - r)
            qy = abs(py - hh) - (hh - r)
            return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r

        data = _generic(w, h, sdf)
    else:
        corner = _corner(r)
        full = b"\xff" * w
        mid = b"\xff" * (w - 2 * c)
        top = [row + mid + row[::-1] for row in corner]
        data = b"".join(top) + full * (h - 2 * c) + b"".join(reversed(top))
    m = (w, h, data)
    _masks[key] = m
    return m


def circle(d: int) -> Mask:
    return rrect(d, d, d / 2)


def ring(d: int, width: float) -> Mask:
    key = ("ring", d, width)
    m = _masks.get(key)
    if m:
        return m
    c = d / 2
    r_mid = c - width / 2

    def sdf(px: float, py: float) -> float:
        return abs(math.hypot(px - c, py - c) - r_mid) - width / 2

    m = (d, d, _generic(d, d, sdf))
    _masks[key] = m
    return m


def glow(d: int, inner: float, spread: float) -> Mask:
    """Soft radial halo: opaque at radius ``inner`` fading out over ``spread``."""
    key = ("glow", d, inner, spread)
    m = _masks.get(key)
    if m:
        return m
    c = d / 2
    out = bytearray(d * d)
    i = 0
    for y in range(d):
        for x in range(d):
            dist = math.hypot(x + 0.5 - c, y + 0.5 - c) - inner
            if dist <= 0:
                v = 1.0
            elif dist >= spread:
                v = 0.0
            else:
                t = 1 - dist / spread
                v = t * t * (3 - 2 * t)
            out[i] = int(v * 255)
            i += 1
    m = (d, d, bytes(out))
    _masks[key] = m
    return m


# ------------------------------------------------------------------ images --
def _png(w: int, h: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    stride = w * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


_tables: dict[int, bytes] = {}


def _opacity_table(level: int) -> bytes:
    t = _tables.get(level)
    if t is None:
        t = bytes(int(v * level / 255 + 0.5) for v in range(256))
        _tables[level] = t
    return t


def image(mask: Mask, color: str, opacity: float = 1.0) -> tk.PhotoImage:
    """Coloured, alpha-blended PhotoImage for a mask. Cached."""
    w, h, cov = mask
    level = max(0, min(255, int(opacity * 255 + 0.5)))
    key = (id(cov), w, h, color, level)
    img = _photos.get(key)
    if img is not None:
        _photos.move_to_end(key)
        return img
    alpha = cov if level == 255 else cov.translate(_opacity_table(level))
    buf = bytearray(bytes.fromhex(color[1:7]) + b"\x00") * (w * h)
    buf[3::4] = alpha
    img = tk.PhotoImage(width=w, height=h, data=base64.b64encode(_png(w, h, bytes(buf))).decode("ascii"),
                        format="png")
    _photos[key] = img
    if len(_photos) > MAX_PHOTOS:
        _photos.popitem(last=False)
    return img


# ------------------------------------------------------------------- icons --
# Icons are described on a 24x24 grid as strokes (polylines), circles and arcs.
Seg = tuple[float, float, float, float]


def _seg_dist(px: float, py: float, s: Seg) -> float:
    ax, ay, bx, by = s
    dx, dy = bx - ax, by - ay
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy or 1)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _poly(points: Iterable[tuple[float, float]]) -> list[Seg]:
    pts = list(points)
    return [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]) for i in range(len(pts) - 1)]


ICONS: dict[str, dict] = {
    "power": {"segs": [(12, 3, 12, 11)], "arcs": [(12, 13, 8, -55, 235)]},
    "settings": {"segs": [(4, 7, 20, 7), (4, 17, 20, 17)], "dots": [(9, 7, 2.6), (15, 17, 2.6)]},
    "edit": {"segs": _poly([(4, 20), (4.6, 16), (15.5, 5.1), (18.9, 8.5), (8, 19.4), (4, 20)]) + [(13.3, 7.3, 16.7, 10.7)]},
    "close": {"segs": [(6.5, 6.5, 17.5, 17.5), (17.5, 6.5, 6.5, 17.5)]},
    "plus": {"segs": [(12, 5, 12, 19), (5, 12, 19, 12)]},
    "import": {"segs": [(12, 4, 12, 14)] + _poly([(7.5, 9.5), (12, 14), (16.5, 9.5)]) + _poly([(4, 15), (4, 20), (20, 20), (20, 15)])},
    "search": {"segs": [(15.5, 15.5, 20, 20)], "arcs": [(10.5, 10.5, 6.5, 0, 360)]},
    "chevron": {"segs": _poly([(7, 10), (12, 15), (17, 10)])},
    "chevron_right": {"segs": _poly([(10, 7), (15, 12), (10, 17)])},
    "back": {"segs": [(5, 12, 19, 12)] + _poly([(11, 6), (5, 12), (11, 18)])},
    "play": {"fill": [(8, 5), (19, 12), (8, 19)]},
    "check": {"segs": _poly([(5, 12.5), (10, 17.5), (19, 7)])},
    "trash": {"segs": [(4, 7, 20, 7), (10, 11, 10, 16), (14, 11, 14, 16)]
              + _poly([(9, 7), (9, 4.5), (15, 4.5), (15, 7)]) + _poly([(6, 7), (7, 20), (17, 20), (18, 7)])},
    "refresh": {"arcs": [(12, 12, 7.5, 30, 330)], "segs": _poly([(18.5, 3.5), (18.5, 8.3), (13.8, 8.3)])},
    "wrench": {"arcs": [(15.5, 8.5, 4.5, 0, 270)], "segs": [(12.3, 11.7, 5, 19)]},
    "folder": {"segs": _poly([(3, 6), (9, 6), (11, 8.5), (21, 8.5), (21, 19), (3, 19), (3, 6)])},
    "box": {"segs": _poly([(4, 8), (12, 4), (20, 8), (20, 16), (12, 20), (4, 16), (4, 8)])
            + _poly([(4, 8), (12, 12), (20, 8)]) + [(12, 12, 12, 20)]},
}


def _arc_dist(px: float, py: float, arc: tuple) -> float:
    cx, cy, r, a0, a1 = arc
    ang = math.degrees(math.atan2(py - cy, px - cx))  # 0 = right, 90 = down
    sweep = (a1 - a0) % 360 or 360
    rel = (ang - a0) % 360
    if rel <= sweep:
        return abs(math.hypot(px - cx, py - cy) - r)
    best = float("inf")
    for a in (a0, a1):
        ex = cx + r * math.cos(math.radians(a))
        ey = cy + r * math.sin(math.radians(a))
        best = min(best, math.hypot(px - ex, py - ey))
    return best


def _inside(px: float, py: float, pts: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def icon_mask(name: str, size: int, stroke: float = 2.0) -> Mask:
    key = ("icon", name, size, stroke)
    m = _masks.get(key)
    if m:
        return m
    spec = ICONS[name]
    scale = 24.0 / size
    half = stroke / 2

    def sdf(x: float, y: float) -> float:
        px, py = x * scale, y * scale
        d = float("inf")
        for s in spec.get("segs", []):
            d = min(d, _seg_dist(px, py, s) - half)
        for a in spec.get("arcs", []):
            d = min(d, _arc_dist(px, py, a) - half)
        for cx, cy, r in spec.get("dots", []):
            d = min(d, math.hypot(px - cx, py - cy) - r)
        poly = spec.get("fill")
        if poly:
            edge = min(_seg_dist(px, py, s) for s in _poly(poly + [poly[0]]))
            d = min(d, -edge if _inside(px, py, poly) else edge)
        return d / scale

    m = (size, size, _generic(size, size, sdf))
    _masks[key] = m
    return m


def icon(name: str, size: int, color: str, opacity: float = 1.0) -> tk.PhotoImage:
    return image(icon_mask(name, size), color, opacity)


# ---------------------------------------------------------------- colours --
def mix(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t))


def alpha_image(w: int, h: int, alpha: bytes, color: str) -> tk.PhotoImage:
    """Uncached image from a ready-made alpha channel (used for animation frames)."""
    buf = bytearray(bytes.fromhex(color[1:7]) + b"\x00") * (w * h)
    buf[3::4] = alpha
    return tk.PhotoImage(width=w, height=h, data=base64.b64encode(_png(w, h, bytes(buf))).decode("ascii"),
                         format="png")


def reset() -> None:
    """Forget cached images (they belong to a Tk interpreter that may be gone)."""
    _photos.clear()
