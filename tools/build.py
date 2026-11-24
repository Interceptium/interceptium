"""Builds a single-file Interceptium executable with PyInstaller.

Usage:  python tools/build.py          -> dist/Interceptium[.exe] (or .app on macOS)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    import PyInstaller.__main__ as pyi

    args = [
        str(ROOT / "run_interceptium.py"),
        "--name", "Interceptium",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--noupx",  # packed executables are a common antivirus false-positive trigger
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ROOT / 'assets'}:assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        # Leave out big optional stdlib parts we never use.
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if sys.platform == "win32":
        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT / "src"))
        import winversion
        from interceptium import __version__

        info = winversion.write(ROOT / "build" / "version_app.txt", __version__, "Interceptium", "Interceptium")
        args += ["--icon", str(ROOT / "assets" / "logo.ico"), "--version-file", str(info)]
    elif sys.platform == "darwin":
        args += ["--icon", str(ROOT / "assets" / "logo.icns")]
    if sys.platform == "darwin":
        args += ["--osx-bundle-identifier", "io.github.interceptium"]
    pyi.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
