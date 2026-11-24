"""Creates the release source archives in dist/release/:

  Interceptium-<v>-source-windows.zip   app + installer source, Windows build script
  Interceptium-<v>-source-linux.zip     app source, Linux build script
  Interceptium-<v>-source-macos.zip     app source, macOS build script
  Interceptium-Setup-<v>-source.zip     installer source + the UI toolkit it uses
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from interceptium import __version__  # noqa: E402

PLATFORM_SCRIPTS = {
    "windows": ["scripts/build-windows.bat", "scripts/build-setup.bat"],
    "linux": ["scripts/build-linux.sh"],
    "macos": ["scripts/build-macos.sh"],
}


def tracked_files() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return sorted(line for line in out.splitlines() if line and (ROOT / line).is_file())
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", "build", "dist", ".venv", "__pycache__"}
        return sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in ROOT.rglob("*")
                      if p.is_file() and not (skip & set(p.relative_to(ROOT).parts)))


def write_zip(path: Path, prefix: str, files: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in files:
            info = zipfile.ZipInfo.from_file(ROOT / name, f"{prefix}/{name}")
            if name.endswith(".sh"):
                info.external_attr = 0o100755 << 16
            with open(ROOT / name, "rb") as fh:
                zf.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    return path


def main(out: Path = ROOT / "dist" / "release") -> list[Path]:
    files = tracked_files()
    all_scripts = {s for scripts in PLATFORM_SCRIPTS.values() for s in scripts}
    windows_only = ("windows/", "tools/build_windows.py", "tools/pack_setup.py")
    common = [f for f in files if f not in all_scripts and not f.startswith("setup/")
              and not f.startswith(windows_only)]
    common_windows = [f for f in files if f.startswith(("tools/build_windows.py", "tools/pack_setup.py"))]
    setup_files = [f for f in files if f.startswith("setup/")]
    made = []
    for platform, scripts in PLATFORM_SCRIPTS.items():
        chosen = common + [s for s in scripts if s in files]
        if platform == "windows":
            chosen += common_windows
        if platform == "windows":
            chosen += setup_files + [f for f in files if f.startswith("windows/")]
        made.append(write_zip(out / f"Interceptium-{__version__}-source-{platform}.zip",
                              f"Interceptium-{__version__}-{platform}", sorted(chosen)))
    toolkit = [f for f in files if f.startswith("src/interceptium/")]  # the installer packs the whole app
    setup_zip = setup_files + toolkit + [f for f in files if f.startswith(("assets/", "windows/"))] + [
        "tools/build_windows.py", "tools/pack_setup.py", "scripts/build-setup.bat", "run_interceptium.py",
        "requirements.txt", "LICENSE"]
    made.append(write_zip(out / f"Interceptium-Setup-{__version__}-source.zip",
                          f"Interceptium-Setup-{__version__}", sorted(set(f for f in setup_zip if f in files))))
    for path in made:
        print(path.name, path.stat().st_size)
    return made


if __name__ == "__main__":
    main()
