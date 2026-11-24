"""Windows implementation of SystemOps (standard library only)."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .core import APP_EXE, APP_NAME

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Interceptium"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CSIDL_PROGRAMS = 0x0002
CSIDL_DESKTOPDIRECTORY = 0x0010

# Creates a .lnk through the built-in WScript.Shell COM object. Paths are passed
# through environment variables, never pasted into the script text.
_SHORTCUT_PS = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:INTERCEPTIUM_LNK);"
    "$s.TargetPath = $env:INTERCEPTIUM_TARGET;"
    "$s.WorkingDirectory = $env:INTERCEPTIUM_WORKDIR;"
    "$s.IconLocation = $env:INTERCEPTIUM_ICON + ',0';"
    "$s.Description = 'Interceptium';"
    "$s.Save()"
)


def _folder(csidl: int, fallback: Path) -> Path:
    buf = ctypes.create_unicode_buffer(260)
    try:
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:  # type: ignore[attr-defined]
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return fallback


def _run(args: list[str], timeout: float = 60, env: dict[str, str] | None = None) -> int:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, env=env,
                              creationflags=CREATE_NO_WINDOW).returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1


class WindowsOps:
    def stop_app(self) -> None:
        _run(["taskkill", "/F", "/IM", APP_EXE], timeout=20)

    def remove_roblox_changes(self, app_exe: Path) -> None:
        # The app knows how to take its certificate back out of Roblox.
        _run([str(app_exe), "--uninstall"], timeout=60)

    def start_menu_dir(self) -> Path:
        appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return _folder(CSIDL_PROGRAMS, appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs")

    def desktop_dir(self) -> Path:
        return _folder(CSIDL_DESKTOPDIRECTORY, Path.home() / "Desktop")

    def create_shortcut(self, link: Path, target: Path, icon: Path) -> None:
        link.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "INTERCEPTIUM_LNK": str(link),
            "INTERCEPTIUM_TARGET": str(target),
            "INTERCEPTIUM_WORKDIR": str(target.parent),
            "INTERCEPTIUM_ICON": str(icon),
        })
        _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
              "-Command", _SHORTCUT_PS], env=env)

    def write_uninstall_entry(self, values: dict[str, object]) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                if isinstance(value, int):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
                else:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))

    def read_install_location(self) -> Path | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                value, _kind = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            return None
        return Path(value) if value else None

    def delete_uninstall_entry(self) -> None:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
        except OSError:
            pass

    def user_data_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_NAME

    def launch(self, exe: Path) -> None:
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                             creationflags=DETACHED_PROCESS | 0x00000200)  # + NEW_PROCESS_GROUP
        except OSError:
            pass


def schedule_self_delete(path: Path) -> None:
    """Delete a temporary copy of the setup a few seconds after it exits."""
    if sys.platform != "win32":
        return
    cmd = f'ping 127.0.0.1 -n 5 > nul & del /f /q "{path}"'
    try:
        subprocess.Popen(["cmd.exe", "/c", cmd], close_fds=True,
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)
    except OSError:
        pass
