"""Colours, fonts and DPI scaling."""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont

BG = "#0b0d14"
SURFACE = "#121620"
CARD = "#181d2a"
CARD_HOVER = "#1f2536"
CARD_SELECTED = "#1d2b37"
FIELD = "#0f131c"
LINE = "#2a3145"
TEXT = "#eef1f7"
MUTED = "#8b94a8"
FAINT = "#5c6478"
ACCENT = "#2fd3c7"
ACCENT_HOVER = "#4de0d5"
ACCENT_PRESS = "#22b3a8"
ACCENT_TEXT = "#06221f"
DANGER = "#ff5d7a"
DANGER_HOVER = "#ff7a91"
WARN = "#f5b84b"
TRACK_OFF = "#323a4f"
CHIP = "#232a3b"

_scale = 1.0
FONT_FAMILY = "TkDefaultFont"


def setup(root: tk.Tk) -> None:
    """Pick fonts and work out the UI scale from the screen DPI."""
    global _scale, FONT_FAMILY
    try:
        _scale = max(1.0, min(3.0, root.winfo_fpixels("1i") / 96.0))
    except tk.TclError:
        _scale = 1.0
    families = set(tkfont.families(root))
    preferred = (
        ["Segoe UI Variable Text", "Segoe UI"] if sys.platform == "win32"
        else ["SF Pro Text", "Helvetica Neue"] if sys.platform == "darwin"
        else ["Inter", "Cantarell", "Ubuntu", "Noto Sans", "DejaVu Sans"]
    )
    FONT_FAMILY = next((f for f in preferred if f in families), "TkDefaultFont")


def S(value: float) -> int:
    """Scale a design-pixel value to screen pixels."""
    return max(1, int(round(value * _scale)))


_fonts: dict[tuple[int, str], tkfont.Font] = {}


def font(size: int = 10, weight: str = "normal") -> tkfont.Font:
    key = (size, weight)
    if key not in _fonts:
        _fonts[key] = tkfont.Font(family=FONT_FAMILY, size=size, weight=weight)
    return _fonts[key]


def mix(a: str, b: str, t: float) -> str:
    """Blend colour a towards b by t (0..1)."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (round(ar + (br - ar) * t), round(ag + (bg - ag) * t), round(ab + (bb - ab) * t))
