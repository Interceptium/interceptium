"""Colours, fonts and DPI scaling."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont

from .gfx import mix  # noqa: F401  (re-exported for convenience)

BG = "#0b0d13"
SURFACE = "#11141c"
CARD = "#171b26"
CARD_HOVER = "#1d2230"
CARD_SELECTED = "#18262c"
FIELD = "#0d1017"
LINE = "#262c3b"
TEXT = "#eceff5"
MUTED = "#8a92a5"
FAINT = "#5a6275"
ACCENT = "#2fd3c7"
ACCENT_HOVER = "#52ddd3"
ACCENT_PRESS = "#25b5aa"
ACCENT_TEXT = "#04201d"
DANGER = "#ff5d7a"
WARN = "#f2b54a"
BLUE = "#7ea6ff"
TRACK_OFF = "#2d3446"
CHIP = "#212736"
GHOST = "#1e2330"
GHOST_HOVER = "#272d3d"

_scale = 1.0
_fonts: dict[tuple, tkfont.Font] = {}
FONT_FAMILY = "TkDefaultFont"
MONO_FAMILY = "TkFixedFont"


def setup(root: tk.Tk) -> None:
    """Pick fonts and work out the UI scale from the screen DPI."""
    global _scale, FONT_FAMILY, MONO_FAMILY
    _fonts.clear()  # fonts belong to the previous interpreter, if any
    try:
        _scale = max(1.0, min(3.0, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        _scale = 1.0
    families = set(tkfont.families(root))
    preferred = ["Inter"] + (
        ["Segoe UI Variable Text", "Segoe UI"] if sys.platform == "win32"
        else ["SF Pro Text", "Helvetica Neue"] if sys.platform == "darwin"
        else ["Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans"]
    )
    FONT_FAMILY = next((f for f in preferred if f in families), "TkDefaultFont")
    mono = ["Cascadia Mono", "Consolas"] if sys.platform == "win32" else ["SF Mono", "Menlo"] \
        if sys.platform == "darwin" else ["JetBrains Mono", "DejaVu Sans Mono"]
    MONO_FAMILY = next((f for f in mono if f in families), "TkFixedFont")


def S(value: float) -> int:
    """Scale a design-pixel value to screen pixels."""
    return max(1, int(round(value * _scale)))


def font(size: int = 10, weight: str = "normal", mono: bool = False) -> tkfont.Font:
    key = (size, weight, mono)
    if key not in _fonts:
        _fonts[key] = tkfont.Font(family=MONO_FAMILY if mono else FONT_FAMILY, size=size, weight=weight)
    return _fonts[key]


def elide(text: str, f: tkfont.Font, width: int) -> str:
    """Shorten ``text`` with an ellipsis so it fits in ``width`` pixels."""
    if f.measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"
