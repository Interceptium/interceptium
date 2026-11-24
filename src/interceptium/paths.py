"""Where Interceptium keeps its own files (per-user, never system-wide)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    """Return (and create) the per-user data directory."""
    override = os.environ.get("INTERCEPTIUM_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Interceptium"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Interceptium"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = (Path(xdg) if xdg else Path.home() / ".local" / "share") / "interceptium"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_file() -> Path:
    return data_dir() / "config.json"


def ca_dir() -> Path:
    path = data_dir() / "ca"
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(path, 0o700)
    return path
