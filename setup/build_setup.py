"""Builds the Windows installer: dist/Interceptium-Setup-<version>.exe

Run tools/build.py first (it produces dist/Interceptium.exe, which is packed
inside the installer).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from interceptium import __version__  # noqa: E402


def main() -> int:
    import PyInstaller.__main__ as pyi
    import winversion

    payload = ROOT / "dist" / "Interceptium.exe"
    if not payload.is_file():
        print("dist/Interceptium.exe is missing: run tools/build.py first", file=sys.stderr)
        return 1
    name = f"Interceptium-Setup-{__version__}"
    args = [
        str(HERE / "run_setup.py"),
        "--name", name,
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--paths", str(ROOT / "src"),
        "--paths", str(HERE),
        "--add-data", f"{payload}:payload",
        "--add-data", f"{ROOT / 'assets'}:assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "setup"),
        "--specpath", str(ROOT / "build" / "setup"),
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if sys.platform == "win32":
        info = winversion.write(ROOT / "build" / "version_setup.txt", __version__, name, "Interceptium Setup")
        args += ["--icon", str(ROOT / "assets" / "logo.ico"), "--version-file", str(info)]
    pyi.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
