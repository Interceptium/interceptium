"""Anti-aliased rounded rectangles and circles rendered into Tk PhotoImages.

Tk's canvas draws shapes without anti-aliasing, so curves look jagged.
Here every pixel gets a coverage value from a signed-distance function and is
blended against the colour behind it, giving smooth edges. Images are cached,
so identical shapes (e.g. many same-sized cards) are rendered only once.
"""

from __future__ import annotations

import math
import tkinter as tk

_cache: dict[tuple, tk.PhotoImage] = {}
_MAX_CACHE = 600


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _sdf(x: float, y: float, hw: float, hh: float, r: float) -> float:
    qx = abs(x) - (hw - r)
    qy = abs(y) - (hh - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def _row(y: int, w: int, h: int, r: float, fill, bg, outline, ow: float) -> str:
    hw, hh = w / 2.0, h / 2.0
    cy = y + 0.5 - hh
    out = []
    fr, fg, fb = fill
    br, bgg, bb = bg
    orr, og, ob = outline if outline else fill
    for x in range(w):
        d = _sdf(x + 0.5 - hw, cy, hw, hh, r)
        cover = 0.5 - d
        if cover <= 0:
            out.append("#%02x%02x%02x" % bg)
            continue
        if outline and ow > 0:
            inner = min(1.0, max(0.0, 0.5 - (d + ow)))
            cr = orr + (fr - orr) * inner
            cg = og + (fg - og) * inner
            cb = ob + (fb - ob) * inner
        else:
            cr, cg, cb = fr, fg, fb
        if cover >= 1:
            out.append("#%02x%02x%02x" % (int(cr), int(cg), int(cb)))
        else:
            out.append("#%02x%02x%02x" % (
                int(br + (cr - br) * cover), int(bgg + (cg - bgg) * cover), int(bb + (cb - bb) * cover)))
    return "{" + " ".join(out) + "}"


def rounded(w: int, h: int, r: float, fill: str, bg: str, outline: str | None = None, ow: float = 0) -> tk.PhotoImage:
    """A w x h image of a rounded rectangle on a solid ``bg`` colour."""
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0.0, min(float(r), w / 2.0, h / 2.0))
    key = (w, h, round(r, 2), fill, bg, outline, ow)
    img = _cache.get(key)
    if img is not None:
        return img
    if len(_cache) > _MAX_CACHE:
        _cache.clear()
    f, b = _rgb(fill), _rgb(bg)
    o = _rgb(outline) if outline else None
    img = tk.PhotoImage(width=w, height=h)
    edge = int(math.ceil(r + ow)) + 1
    rows: list[str] = []
    middle: str | None = None
    for y in range(h):
        if edge <= y < h - edge:
            if middle is None:
                middle = _row(y, w, h, r, f, b, o, ow)
            rows.append(middle)
        else:
            rows.append(_row(y, w, h, r, f, b, o, ow))
    img.put(" ".join(rows))
    _cache[key] = img
    return img


def circle(d: int, fill: str, bg: str, outline: str | None = None, ow: float = 0) -> tk.PhotoImage:
    return rounded(d, d, d / 2.0, fill, bg, outline, ow)
