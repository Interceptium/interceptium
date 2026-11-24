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
import time
import traceback
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
    subprocess.Popen([str(temp), *argv, "--from-temp"], close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    p.add_argument("--log", type=Path, help="write progress and errors to this file (quiet mode)")
    return p.parse_args(argv)


class Log:
    """Quiet mode has no window and no console, so it reports to a file."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def __call__(self, text: str) -> None:
        if self.path is None:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} [{os.getpid()}] {text}\n")
        except OSError:
            pass


def run_quiet(installer: Installer, args: argparse.Namespace, log: "Log | None" = None) -> int:
    log = log or Log(args.log)
    last: list[str] = [""]

    def progress(value: float, text: str) -> None:
        if text != last[0]:
            last[0] = text
            log(f"{value:.0%} {text}")

    try:
        if args.uninstall:
            manifest = installer.installed()
            if manifest is None:
                return 0
            installer.uninstall(Path(manifest.directory), remove_data=args.remove_data, progress=progress)
        elif args.repair:
            installer.repair(progress)
        elif args.reinstall:
            installer.reinstall(progress)
        else:
            options = Options(args.dir or default_directory(), not args.no_desktop, not args.no_start_menu)
            manifest = installer.install(options, progress)
            if args.launch:
                installer.ops.launch(Path(manifest.directory) / APP_EXE)
    except (SetupError, OSError) as exc:
        log(f"failed: {exc}")
        print(f"Interceptium Setup: {exc}", file=sys.stderr)
        return 1
    log("finished")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse(argv)
    log = Log(args.log)
    log(f"start {' '.join(argv)}")
    try:
        ops = make_ops()
        if relaunch_from_temp(ops, argv):
            log("continuing from a temporary copy")
            return 0
    except Exception:
        log(traceback.format_exc())
        if args.quiet:
            return 3
        raise
    installer = Installer(ops, payload_path(), setup_path(), __version__)
    try:
        if args.quiet:
            try:
                return run_quiet(installer, args, log)
            except Exception:  # never leave an unattended run stuck behind an error dialog
                log(traceback.format_exc())
                return 3
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
