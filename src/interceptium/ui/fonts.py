"""Load the bundled Inter font for this process only (nothing is installed system-wide)."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from pathlib import Path

FONT_FILES = ("Inter-Regular.ttf", "Inter-SemiBold.ttf")


def _windows(paths: list[Path]) -> None:
    fr_private = 0x10
    gdi = ctypes.windll.gdi32  # type: ignore[attr-defined]
    gdi.AddFontResourceExW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    for path in paths:
        gdi.AddFontResourceExW(str(path), fr_private, None)


def _macos(paths: list[Path]) -> None:
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    ct = ctypes.CDLL("/System/Library/Frameworks/CoreText.framework/CoreText")
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                                           ctypes.c_bool]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ct.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
    ct.CTFontManagerRegisterFontsForURL.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    scope_process = 1
    for path in paths:
        raw = str(path).encode("utf-8")
        url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), False)
        if url:
            ct.CTFontManagerRegisterFontsForURL(url, scope_process, None)
            cf.CFRelease(url)


def _linux(paths: list[Path]) -> None:
    name = ctypes.util.find_library("fontconfig") or "libfontconfig.so.1"
    fc = ctypes.CDLL(name)
    fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    for path in paths:
        fc.FcConfigAppFontAddFile(None, str(path).encode("utf-8"))


def register(font_dir: Path) -> None:
    """Best effort: if anything fails, the UI falls back to a system font."""
    paths = [font_dir / name for name in FONT_FILES if (font_dir / name).is_file()]
    if not paths:
        return
    try:
        if sys.platform == "win32":
            _windows(paths)
        elif sys.platform == "darwin":
            _macos(paths)
        else:
            _linux(paths)
    except (OSError, AttributeError, ValueError):
        pass
