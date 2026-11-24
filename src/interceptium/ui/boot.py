"""Create a Tk root with the bundled font, theme and animation engine ready."""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

from . import anim, fonts, gfx
from . import theme as T


def enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp rendering on high-DPI screens
        except (AttributeError, OSError):
            pass


def create_root(font_dir: Path) -> tk.Tk:
    enable_dpi_awareness()
    fonts.register(font_dir)
    root = tk.Tk()
    root.withdraw()
    gfx.reset()
    T.setup(root)
    anim.setup(root)
    return root
