"""Find the installed Interceptium Setup so the app can offer Repair / Reinstall / Uninstall."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SETUP_EXE = "Interceptium Setup.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Interceptium"
ACTIONS = ("repair", "reinstall", "uninstall")


def install_location() -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "InstallLocation")
        return Path(value) if value else None
    except OSError:
        return None


def find_setup() -> Path | None:
    """The maintenance copy of the installer, if this app was installed with it."""
    if sys.platform != "win32":
        return None
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / SETUP_EXE)
    location = install_location()
    if location is not None:
        candidates.append(location / SETUP_EXE)
    return next((c for c in candidates if c.is_file()), None)


def launch_setup(action: str, setup: Path | None = None) -> bool:
    if action not in ACTIONS:
        raise ValueError(action)
    setup = setup or find_setup()
    if setup is None:
        return False
    try:
        subprocess.Popen([str(setup), f"--{action}"], close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x00000008 if sys.platform == "win32" else 0)  # DETACHED_PROCESS
    except OSError:
        return False
    return True
