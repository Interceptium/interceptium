"""Roblox client integration: find installs, trust our CA, launch through the proxy.

Nothing here needs administrator rights, reads accounts/cookies, or contacts
any server. Changes are limited to:
  * a marked block appended to each Roblox install's own ``ssl/cacert.pem``
  * one FastFlag (``DFFlagHttpUseEmbeddedCaCerts=False``) in the install's
    ``ClientSettings/ClientAppSettings.json`` so Roblox reads that bundle
  * deleting Roblox's asset cache database when you ask for it
All of these are undone by "Remove from Roblox".
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .logbus import log

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

BEGIN_MARK = "# >>> Interceptium local CA (safe to delete) >>>"
END_MARK = "# <<< Interceptium local CA <<<"
CA_FLAG = "DFFlagHttpUseEmbeddedCaCerts"
PLAYER_EXE = "RobloxPlayerBeta.exe" if IS_WINDOWS else "RobloxPlayer"
_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0  # CREATE_NO_WINDOW


@dataclass(frozen=True)
class Install:
    exe: Path
    resources: Path  # folder holding ssl/cacert.pem
    settings_dir: Path  # folder holding ClientSettings/


def _local_appdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def find_installs() -> list[Install]:
    found: list[Install] = []
    if IS_WINDOWS:
        roots = [
            _local_appdata() / "Roblox" / "Versions",
            _local_appdata() / "Bloxstrap" / "Versions",
            _local_appdata() / "Fishstrap" / "Versions",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Roblox" / "Versions",
        ]
        for root in roots:
            try:
                children = list(root.iterdir())
            except OSError:
                continue
            for child in children:
                exe = child / PLAYER_EXE
                if exe.is_file():
                    found.append(Install(exe, child, child))
    elif IS_MACOS:
        for app in (Path("/Applications/Roblox.app"), Path.home() / "Applications" / "Roblox.app"):
            exe = app / "Contents" / "MacOS" / PLAYER_EXE
            if exe.is_file():
                found.append(Install(exe, app / "Contents" / "Resources", app / "Contents" / "MacOS"))
    found.sort(key=lambda i: i.exe.stat().st_mtime if i.exe.exists() else 0, reverse=True)
    return found


def cache_files() -> list[Path]:
    if IS_WINDOWS:
        bases = [_local_appdata() / "Roblox", _local_appdata() / "RobloxPCGDK"]
    elif IS_MACOS:
        bases = [Path.home() / "Library" / "Roblox"]
    else:
        bases = [Path.home() / ".var" / "app" / "org.vinegarhq.Sober" / "data" / "sober" / "appData"]
    out = []
    for base in bases:
        for name in ("rbx-storage.db", "rbx-storage.db-wal", "rbx-storage.db-shm"):
            out.append(base / name)
    return out


# --- certificate bundle -------------------------------------------------
def _strip_block(text: str) -> str:
    while BEGIN_MARK in text:
        start = text.index(BEGIN_MARK)
        end = text.find(END_MARK, start)
        if end == -1:
            text = text[:start]
            break
        text = text[:start] + text[end + len(END_MARK):]
    return text.rstrip() + "\n" if text.strip() else ""


def _system_bundle() -> str:
    """Build a PEM bundle from the OS trust store (fallback if Roblox has none)."""
    ctx = ssl.create_default_context()
    pems = [ssl.DER_cert_to_PEM_cert(der) for der in ctx.get_ca_certs(binary_form=True)]
    return "".join(pems)


def _install_ca_one(inst: Install, ca_pem: str) -> None:
    bundle = inst.resources / "ssl" / "cacert.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = bundle.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        current = _system_bundle()
    base = _strip_block(current)
    bundle.write_text(f"{base}\n{BEGIN_MARK}\n{ca_pem.strip()}\n{END_MARK}\n", encoding="utf-8")

    settings = inst.settings_dir / "ClientSettings" / "ClientAppSettings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    try:
        loaded = json.loads(settings.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        pass
    data[CA_FLAG] = "False"
    settings.write_text(json.dumps(data, indent=2), encoding="utf-8")


def install_ca(ca_pem: str) -> int:
    count = 0
    for inst in find_installs():
        try:
            _install_ca_one(inst, ca_pem)
            count += 1
        except OSError as exc:
            log.emit("Roblox", f"could not prepare {inst.resources}: {exc}")
    log.emit("Roblox", f"certificate set up in {count} Roblox install(s)")
    return count


def uninstall_ca() -> int:
    count = 0
    for inst in find_installs():
        bundle = inst.resources / "ssl" / "cacert.pem"
        try:
            text = bundle.read_text(encoding="utf-8", errors="replace")
            if BEGIN_MARK in text:
                bundle.write_text(_strip_block(text), encoding="utf-8")
                count += 1
        except OSError:
            pass
        settings = inst.settings_dir / "ClientSettings" / "ClientAppSettings.json"
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            if isinstance(data, dict) and CA_FLAG in data:
                del data[CA_FLAG]
                settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            pass
    log.emit("Roblox", f"certificate removed from {count} Roblox install(s)")
    return count


# --- processes ------------------------------------------------------------
@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    exe: str
    args: list[str]


def list_players() -> list[Proc]:
    procs: list[Proc] = []
    if IS_WINDOWS:
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name='RobloxPlayerBeta.exe'\" | "
            "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
        )
        try:
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return procs
        if not out:
            return procs
        try:
            rows = json.loads(out)
        except ValueError:
            return procs
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            cmd = row.get("CommandLine") or ""
            exe = row.get("ExecutablePath") or ""
            procs.append(Proc(int(row["ProcessId"]), int(row.get("ParentProcessId") or 0), exe, _split_args(cmd)))
    elif IS_MACOS:
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            return procs
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and f"/MacOS/{PLAYER_EXE}" in parts[2]:
                exe, _, rest = parts[2].partition(f"/MacOS/{PLAYER_EXE}")
                procs.append(Proc(int(parts[0]), int(parts[1]), exe + f"/MacOS/{PLAYER_EXE}", rest.split()))
    return procs


def _split_args(cmdline: str) -> list[str]:
    """Split a Windows command line and drop argv[0]."""
    if not cmdline:
        return []
    if cmdline.startswith('"'):
        rest = cmdline[1:].split('"', 1)[1] if '"' in cmdline[1:] else ""
    else:
        rest = cmdline.split(" ", 1)[1] if " " in cmdline else ""
    return rest.split()


def is_player_running() -> bool:
    return bool(list_players())


def kill(pid: int) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        else:
            os.kill(pid, 9)
    except (OSError, subprocess.TimeoutExpired):
        pass


def clear_cache() -> int:
    if is_player_running():
        log.emit("Cache", "not cleared: Roblox is running")
        return 0
    removed = 0
    for path in cache_files():
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.emit("Cache", f"could not delete {path.name}: {exc}")
    log.emit("Cache", f"Roblox cache cleared ({removed} file(s))")
    return removed


def proxy_env(proxy_url: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        env[key] = proxy_url
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    return env


def launch(proxy_url: str, args: list[str] | None = None, exe: Path | None = None) -> int | None:
    if exe is None:
        installs = find_installs()
        if not installs:
            log.emit("Roblox", "no Roblox Player install found")
            return None
        exe = installs[0].exe
    kwargs: dict[str, object] = {"env": proxy_env(proxy_url), "cwd": str(exe.parent)}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen([str(exe), *(args or [])], **kwargs)  # noqa: S603 - fixed exe path
    except OSError as exc:
        log.emit("Roblox", f"launch failed: {exc}")
        return None
    log.emit("Roblox", "launched")
    return proc.pid


class LaunchWatcher:
    """Re-routes Roblox started from the website so it goes through the proxy.

    When a Player process appears that Interceptium did not start, it is closed
    and started again with the same arguments plus the proxy settings. The
    arguments (which include a one-time join ticket) are never logged.
    """

    def __init__(self, proxy_url: Callable[[], str], ca_pem: Callable[[], str], clear_first: Callable[[], bool]) -> None:
        self._proxy_url = proxy_url
        self._ca_pem = ca_pem
        self._clear_first = clear_first
        self._ours: set[int] = set()
        self._handled: set[int] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def mark_ours(self, pid: int | None) -> None:
        if pid:
            self._ours.add(pid)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="interceptium-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self._tick()
            except Exception as exc:
                log.emit("Watcher", f"{type(exc).__name__}: {exc}")

    def _tick(self) -> None:
        procs = list_players()
        alive = {p.pid for p in procs}
        self._ours &= alive | {os.getpid()}
        changed = True
        while changed:  # anything spawned by a proxied player is also proxied
            changed = False
            for p in procs:
                if p.pid not in self._ours and p.ppid in self._ours:
                    self._ours.add(p.pid)
                    changed = True
        for p in procs:
            if p.pid in self._ours or p.pid in self._handled:
                continue
            self._handled.add(p.pid)
            log.emit("Watcher", "restarting Roblox through the proxy")
            kill(p.pid)
            time.sleep(1.0)
            install_ca(self._ca_pem())
            if self._clear_first():
                clear_cache()
            exe = Path(p.exe) if p.exe else None
            self.mark_ours(launch(self._proxy_url(), p.args, exe))
