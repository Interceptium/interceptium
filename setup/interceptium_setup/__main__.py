"""Interceptium Setup entry point.

    Interceptium Setup.exe                 install (or maintenance if already installed)
    Interceptium Setup.exe --maintenance   Repair / Reinstall / Uninstall menu
    Interceptium Setup.exe --uninstall     uninstall (what Windows Settings runs)
    Interceptium Setup.exe --repair | --reinstall
    add --quiet to run without a window (exit code 0 = success)
    --dir PATH, --no-desktop, --no-start-menu, --remove-data, --launch
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not getattr(sys, "frozen", False):
    # running from a source checkout: make the app's UI toolkit importable
    sys.path.insert(0, str(HERE.parent.parent / "src"))

from interceptium import __version__  # noqa: E402

from .core import APP_EXE, Installer, Options, SetupError, default_directory  # noqa: E402


def payload_path() -> Path | None:
    if getattr(sys, "frozen", False):
        candidate = Path(getattr(sys, "_MEIPASS", HERE)) / "payload" / APP_EXE
    else:
        env = os.environ.get("INTERCEPTIUM_PAYLOAD")
        candidate = Path(env) if env else HERE.parent.parent / "dist" / APP_EXE
    return candidate if candidate.is_file() else None


def setup_path() -> Path | None:
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def make_ops():
    if sys.platform == "win32":
        from .winops import WindowsOps

        return WindowsOps()
    from .fakeops import FakeOps  # lets the UI be developed and tested off Windows

    return FakeOps(Path(os.environ.get("INTERCEPTIUM_SETUP_SANDBOX", tempfile.gettempdir())) / "setup-sandbox")


def relaunch_from_temp(ops, argv: list[str]) -> bool:
    """If this copy lives in the install folder, continue from a temporary copy.

    Windows won't let a running .exe be replaced or deleted, so repair,
    reinstall and uninstall run from a copy in %TEMP% that deletes itself.
    """
    exe = setup_path()
    location = ops.read_install_location()
    if exe is None or location is None or "--from-temp" in argv:
        return False
    try:
        if exe.resolve().parent != location.resolve():
            return False
    except OSError:
        return False
    temp = Path(tempfile.gettempdir()) / f"interceptium-setup-{os.getpid()}.exe"
    shutil.copyfile(exe, temp)
    subprocess.Popen([str(temp), *argv, "--from-temp"], close_fds=True)
    return True


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="Interceptium Setup", add_help=True)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--maintenance", action="store_true")
    group.add_argument("--uninstall", action="store_true")
    group.add_argument("--repair", action="store_true")
    group.add_argument("--reinstall", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--dir", type=Path)
    p.add_argument("--no-desktop", action="store_true")
    p.add_argument("--no-start-menu", action="store_true")
    p.add_argument("--remove-data", action="store_true")
    p.add_argument("--launch", action="store_true")
    p.add_argument("--from-temp", action="store_true")
    return p.parse_args(argv)


def run_quiet(installer: Installer, args: argparse.Namespace) -> int:
    try:
        if args.uninstall:
            manifest = installer.installed()
            if manifest is None:
                return 0
            installer.uninstall(Path(manifest.directory), remove_data=args.remove_data)
        elif args.repair:
            installer.repair()
        elif args.reinstall:
            installer.reinstall()
        else:
            options = Options(args.dir or default_directory(), not args.no_desktop, not args.no_start_menu)
            manifest = installer.install(options)
            if args.launch:
                installer.ops.launch(Path(manifest.directory) / APP_EXE)
    except (SetupError, OSError) as exc:
        print(f"Interceptium Setup: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse(argv)
    ops = make_ops()
    if relaunch_from_temp(ops, argv):
        return 0
    installer = Installer(ops, payload_path(), setup_path(), __version__)
    try:
        if args.quiet:
            return run_quiet(installer, args)
        from .ui import run_ui

        if args.uninstall:
            mode = "uninstall"
        elif args.repair:
            mode = "repair"
        elif args.reinstall:
            mode = "reinstall"
        elif args.maintenance or installer.installed() is not None:
            mode = "maintenance"
        else:
            mode = "install"
        return run_ui(installer, mode, args)
    finally:
        if args.from_temp and setup_path() is not None:
            from .winops import schedule_self_delete

            schedule_self_delete(setup_path())


if __name__ == "__main__":
    sys.exit(main())
