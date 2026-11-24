"""Install, uninstall, repair and reinstall logic for Interceptium on Windows.

Design rules (safety):
  * Per-user install (default %LOCALAPPDATA%\\Programs\\Interceptium); no
    administrator rights, no system-wide changes.
  * Only files this installer wrote (recorded in ``install.json``) are ever
    deleted. A folder is removed only if it is empty afterwards, so choosing an
    unusual install folder can never delete other files.
  * The Windows "Apps" entry lives under HKEY_CURRENT_USER.
  * No network access of any kind.

Everything that touches the operating system goes through a ``SystemOps``
object, so the logic can be tested with a fake one.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

APP_NAME = "Interceptium"
PUBLISHER = "Interceptium"
APP_EXE = "Interceptium.exe"
SETUP_EXE = "Interceptium Setup.exe"
MANIFEST = "install.json"
SHORTCUT = "Interceptium.lnk"

Progress = Callable[[float, str], None]


def _no_progress(_value: float, _text: str) -> None:
    pass


class SetupError(Exception):
    """A problem we can explain to the user."""


@dataclass
class Options:
    directory: Path
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True


@dataclass
class Manifest:
    version: str
    directory: str
    files: list[str] = field(default_factory=list)
    shortcuts: list[str] = field(default_factory=list)
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True

    def options(self) -> Options:
        return Options(Path(self.directory), self.desktop_shortcut, self.start_menu_shortcut)

    @classmethod
    def load(cls, directory: Path) -> "Manifest | None":
        try:
            raw = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        files = [f for f in raw.get("files", []) if isinstance(f, str) and _safe_relative(f)]
        shortcuts = [s for s in raw.get("shortcuts", []) if isinstance(s, str) and s.lower().endswith(".lnk")]
        return cls(
            version=str(raw.get("version", "")),
            directory=str(directory),
            files=files,
            shortcuts=shortcuts,
            desktop_shortcut=bool(raw.get("desktop_shortcut", True)),
            start_menu_shortcut=bool(raw.get("start_menu_shortcut", True)),
        )

    def save(self) -> None:
        path = Path(self.directory) / MANIFEST
        tmp = path.with_name(MANIFEST + ".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        os.replace(tmp, path)


def _safe_relative(name: str) -> bool:
    """Manifest entries must be relative paths that stay inside the install folder.

    Forward slashes separate folders; absolute paths, drive letters, backslashes
    and "." / ".." segments are refused.
    """
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


def _remove_empty_dirs(directory: Path, names: list[str]) -> None:
    """Remove folders (inside `directory`) that are empty after deleting `names`; deepest first."""
    folders: set[Path] = set()
    for name in names:
        parts = name.split("/")[:-1]
        for i in range(len(parts), 0, -1):
            folders.add(directory.joinpath(*parts[:i]))
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()  # only succeeds when empty
        except OSError:
            pass


def payload_files(payload: Path) -> list[tuple[str, Path]]:
    """(manifest name, source file) pairs for a payload folder, or a single app exe."""
    if payload.is_file():
        return [(APP_EXE, payload)]
    out = []
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            name = path.relative_to(payload).as_posix()
            if _safe_relative(name):
                out.append((name, path))
    return out


class SystemOps(Protocol):
    def stop_app(self) -> None: ...
    def remove_roblox_changes(self, app_exe: Path) -> None: ...
    def start_menu_dir(self) -> Path: ...
    def desktop_dir(self) -> Path: ...
    def create_shortcut(self, link: Path, target: Path, icon: Path) -> None: ...
    def write_uninstall_entry(self, values: dict[str, object]) -> None: ...
    def read_install_location(self) -> Path | None: ...
    def delete_uninstall_entry(self) -> None: ...
    def user_data_dir(self) -> Path: ...
    def launch(self, exe: Path) -> None: ...


def _retry(action: Callable[[], None], attempts: int = 40, delay: float = 0.25) -> None:
    """Windows keeps files locked briefly after a process exits; try again for a while."""
    for attempt in range(attempts):
        try:
            action()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def copy_with_progress(src: Path, dst: Path, report: Callable[[float], None]) -> None:
    total = max(1, src.stat().st_size)
    tmp = dst.with_name(dst.name + ".partial")
    done = 0
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        while True:
            chunk = fin.read(1024 * 512)
            if not chunk:
                break
            fout.write(chunk)
            done += len(chunk)
            report(done / total)
    _retry(lambda: os.replace(tmp, dst))


def default_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_NAME


class Installer:
    def __init__(self, ops: SystemOps, payload: Path | None, setup_exe: Path | None, version: str) -> None:
        self.ops = ops
        self.payload = payload        # folder with the files to install (or a single Interceptium.exe)
        self.setup_exe = setup_exe    # this installer, copied in for repair/uninstall later
        self.version = version

    # -- queries -----------------------------------------------------------
    def installed(self) -> Manifest | None:
        location = self.ops.read_install_location()
        if location is None:
            return None
        manifest = Manifest.load(location)
        if manifest is None and (location / APP_EXE).exists():
            # entry without a manifest: treat as installed with default options
            manifest = Manifest(self.version, str(location), [APP_EXE, SETUP_EXE])
        return manifest

    # -- install -------------------------------------------------------------
    def install(self, options: Options, progress: Progress = _no_progress) -> Manifest:
        if self.payload is None or not self.payload.exists() or not payload_files(self.payload):
            raise SetupError("This setup file is incomplete. Download it again.")
        directory = Path(options.directory).expanduser()
        if not directory.is_absolute():
            raise SetupError("Choose a full folder path.")
        progress(0.02, "Closing Interceptium")
        self.ops.stop_app()
        progress(0.06, "Creating folder")
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as exc:
            raise SetupError(f"Can't write to {directory}. Choose another folder.") from exc

        previous = Manifest.load(directory)
        files: list[str] = []
        sources = payload_files(self.payload)
        total = max(1, sum(src.stat().st_size for _name, src in sources))
        done = 0
        progress(0.08, "Copying Interceptium")
        for name, src in sources:
            target = directory.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            size = src.stat().st_size
            base = done
            copy_with_progress(src, target,
                               lambda f, b=base, n=size: progress(0.08 + 0.62 * (b + f * n) / total,
                                                                  "Copying Interceptium"))
            done += size
            files.append(name)
        if self.setup_exe is not None and self.setup_exe.is_file():
            target = directory / SETUP_EXE
            if self.setup_exe.resolve() != target.resolve():
                copy_with_progress(self.setup_exe, target,
                                   lambda f: progress(0.70 + 0.15 * f, "Copying setup"))
            files.append(SETUP_EXE)

        progress(0.87, "Creating shortcuts")
        shortcuts: list[str] = []
        app = directory / APP_EXE
        if options.start_menu_shortcut:
            link = self.ops.start_menu_dir() / SHORTCUT
            self.ops.create_shortcut(link, app, app)
            shortcuts.append(str(link))
        if options.desktop_shortcut:
            link = self.ops.desktop_dir() / SHORTCUT
            self.ops.create_shortcut(link, app, app)
            shortcuts.append(str(link))

        if previous is not None:  # files an older version installed that this one no longer has
            stale = [n for n in previous.files if n not in files and n not in (SETUP_EXE, MANIFEST)]
            for name in stale:
                _retry(lambda p=directory.joinpath(*name.split("/")): p.unlink())
            _remove_empty_dirs(directory, stale)

        progress(0.93, "Registering with Windows")
        manifest = Manifest(self.version, str(directory), files + [MANIFEST], shortcuts,
                            options.desktop_shortcut, options.start_menu_shortcut)
        manifest.save()
        self.ops.write_uninstall_entry(self._entry(directory, manifest.files))
        progress(1.0, "Done")
        return manifest

    def _entry(self, directory: Path, files: list[str]) -> dict[str, object]:
        setup = directory / SETUP_EXE
        size = 0
        for name in files:
            try:
                size += directory.joinpath(*name.split("/")).stat().st_size
            except OSError:
                pass
        size_kb = size // 1024
        return {
            "DisplayName": APP_NAME,
            "DisplayVersion": self.version,
            "Publisher": PUBLISHER,
            "DisplayIcon": f"{directory / APP_EXE},0",
            "InstallLocation": str(directory),
            "InstallDate": date.today().strftime("%Y%m%d"),
            "UninstallString": f'"{setup}" --uninstall',
            "QuietUninstallString": f'"{setup}" --uninstall --quiet',
            "ModifyPath": f'"{setup}" --maintenance',
            "EstimatedSize": int(size_kb),
            "NoRepair": 1,
        }

    # -- uninstall -------------------------------------------------------------
    def uninstall(self, directory: Path, remove_data: bool = False, progress: Progress = _no_progress) -> None:
        directory = Path(directory)
        manifest = Manifest.load(directory)
        progress(0.05, "Closing Interceptium")
        self.ops.stop_app()
        app = directory / APP_EXE
        if app.exists():
            progress(0.15, "Removing changes made to Roblox")
            self.ops.remove_roblox_changes(app)
        progress(0.35, "Removing shortcuts")
        # Only the two shortcuts this installer creates, never paths read from a file.
        for link in (self.ops.start_menu_dir() / SHORTCUT, self.ops.desktop_dir() / SHORTCUT):
            _retry(link.unlink)
        progress(0.5, "Removing files")
        files = manifest.files if manifest else [APP_EXE, SETUP_EXE, MANIFEST]
        files = [n for n in files if _safe_relative(n)]
        step = max(1, len(files) // 40)
        for i, name in enumerate(files):
            _retry(lambda p=directory.joinpath(*name.split("/")): p.unlink())
            if i % step == 0 or i == len(files) - 1:
                progress(0.5 + 0.3 * (i + 1) / max(1, len(files)), "Removing files")
        _remove_empty_dirs(directory, files)
        for leftover in (directory / (APP_EXE + ".partial"), directory / (SETUP_EXE + ".partial")):
            _retry(leftover.unlink)
        try:
            directory.rmdir()  # only succeeds when nothing else is left in it
        except OSError:
            pass
        progress(0.88, "Removing from Windows")
        self.ops.delete_uninstall_entry()
        if remove_data:
            progress(0.94, "Removing configs and settings")
            data = self.ops.user_data_dir()
            if data.name == APP_NAME and data.is_dir():
                shutil.rmtree(data, ignore_errors=True)
        progress(1.0, "Done")

    # -- maintenance -------------------------------------------------------------
    def repair(self, progress: Progress = _no_progress) -> Manifest:
        manifest = self.installed()
        if manifest is None:
            raise SetupError("Interceptium is not installed.")
        return self.install(manifest.options(), progress)

    def reinstall(self, progress: Progress = _no_progress) -> Manifest:
        manifest = self.installed()
        if manifest is None:
            raise SetupError("Interceptium is not installed.")
        options = manifest.options()
        self.uninstall(options.directory, remove_data=False,
                       progress=lambda v, t: progress(v * 0.45, t))
        return self.install(options, lambda v, t: progress(0.45 + v * 0.55, t))
