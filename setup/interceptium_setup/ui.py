"""Interceptium Setup window, built on the same widgets and animations as the app."""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable

from interceptium.ui import anim, boot, gfx
from interceptium.ui import theme as T
from interceptium.ui.widgets import Button, Card, Field, Host, ProgressBar, Stage, Toggle

from .core import APP_EXE, APP_NAME, Installer, Options, SetupError, default_directory

HERE = Path(__file__).resolve().parent


def asset(name: str) -> Path:
    bases = [HERE.parent.parent / "assets", HERE.parent / "assets"]
    if getattr(sys, "_MEIPASS", None):
        bases.insert(0, Path(sys._MEIPASS) / "assets")  # type: ignore[attr-defined]
    for base in bases:
        if (base / name).exists():
            return base / name
    return bases[0] / name


def label(parent: tk.Misc, text: str, size: int = 10, weight: str = "normal", color: str = T.TEXT,
          **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=T.font(size, weight), **kw)


def ease_out_back(t: float) -> float:
    c = 1.7
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


class Badge(Host):
    """A round icon that pops in: the disc grows with a slight overshoot, then the icon fades in."""

    def __init__(self, parent: tk.Misc, icon: str, color: str, size: int = 84, animate: bool = True) -> None:
        self.d = T.S(size)
        super().__init__(parent, self.d, self.d)
        self.icon, self.color = icon, color
        self._ring = self.create_image(self.d / 2, self.d / 2, anchor="center")
        self._disc = self.create_image(self.d / 2, self.d / 2, anchor="center")
        self._ic = self.create_image(self.d / 2, self.d / 2, anchor="center")
        if animate:
            self._draw(0.0, 0.0)
            anim.get().tween((self, "pop"), 0.5, lambda v: self._draw(v, max(0.0, (v - 0.45) / 0.55)),
                             ease=anim.linear)
        else:
            self._draw(1.0, 1.0)

    def _draw(self, scale: float, icon: float) -> None:
        def even(v: float) -> int:  # even sizes render through the fast path
            return max(2, min(self.d - self.d % 2, int(v) // 2 * 2))

        ring_d = even(self.d * min(1.0, scale))
        disc_d = even((self.d - T.S(14)) * max(0.0, ease_out_back(min(1.0, scale))))
        self.itemconfigure(self._ring, image=gfx.image(gfx.circle(ring_d), gfx.mix(self.color, T.BG, 0.85)))
        self.itemconfigure(self._disc, image=gfx.image(gfx.circle(disc_d), self.color))
        self.itemconfigure(self._ic, image=gfx.icon(self.icon, T.S(34), T.ACCENT_TEXT if self.color == T.ACCENT
                                                    else "#ffffff", round(min(1.0, icon) * 32) / 32))


class OptionCard(Host):
    """Clickable card with an icon, title and one line of detail."""

    def __init__(self, parent: tk.Misc, icon: str, title: str, detail: str, color: str,
                 command: Callable[[], None]) -> None:
        self.h = T.S(76)
        super().__init__(parent, T.S(520), self.h, cursor="hand2")
        self.icon, self.title, self.detail, self.color, self.command = icon, title, detail, color, command
        self.hover = 0.0
        self.w = 0
        self._bg = self.create_image(0, 0, anchor="nw")
        self._disc = self.create_image(T.S(20), self.h / 2, anchor="w")
        self._ic = self.create_image(T.S(20) + T.S(22), self.h / 2, anchor="center")
        self._title = self.create_text(T.S(80), T.S(28), anchor="w", text=title, font=T.font(11, "bold"),
                                       fill=T.TEXT)
        self.create_text(T.S(80), T.S(50), anchor="w", text=detail, font=T.font(9), fill=T.MUTED)
        self._chev = self.create_image(0, self.h / 2, anchor="e")
        self.bind("<Configure>", self._resize)
        self.bind("<Enter>", lambda _e: self._fade(1.0))
        self.bind("<Leave>", lambda _e: self._fade(0.0))
        self.bind("<ButtonRelease-1>", lambda e: 0 <= e.x <= self.w and 0 <= e.y <= self.h and self.command())

    def _fade(self, target: float) -> None:
        start = self.hover

        def step(v: float) -> None:
            self.hover = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "h"), 0.16, step)

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self._draw()

    def _draw(self) -> None:
        if self.w < 40:
            return
        fill = gfx.mix(T.CARD, T.CARD_HOVER, self.hover)
        self.itemconfigure(self._bg, image=gfx.image(gfx.rrect(self.w, self.h, T.S(18)), fill))
        self.itemconfigure(self._disc, image=gfx.image(gfx.circle(T.S(44)), gfx.mix(self.color, fill, 0.82)))
        self.itemconfigure(self._ic, image=gfx.icon(self.icon, T.S(20), self.color))
        self.itemconfigure(self._chev, image=gfx.icon("chevron_right", T.S(18), gfx.mix(T.FAINT, T.TEXT, self.hover)))
        self.coords(self._chev, self.w - T.S(18) + T.S(4) * self.hover, self.h / 2)


class View(tk.Frame):
    _stage_dispose = True

    def __init__(self, app: "SetupApp") -> None:
        super().__init__(app.stage, bg=T.BG, padx=T.S(36), pady=T.S(10))
        self.app = app

    def title(self, text: str, sub: str = "") -> None:
        label(self, text, 18, "bold").pack(anchor="w")
        if sub:
            label(self, sub, 10, color=T.MUTED, justify="left", wraplength=T.S(560)).pack(anchor="w", pady=(T.S(4), 0))

    def footer(self) -> tk.Frame:
        row = tk.Frame(self, bg=T.BG)
        row.pack(side="bottom", fill="x", pady=(T.S(10), T.S(18)))
        return row


class InstallView(View):
    def __init__(self, app: "SetupApp") -> None:
        super().__init__(app)
        self.title(f"Install {APP_NAME}", f"Version {app.installer.version}")
        label(self, "Location", 9, "bold", T.MUTED).pack(anchor="w", pady=(T.S(22), T.S(6)))
        row = tk.Frame(self, bg=T.BG)
        row.pack(fill="x")
        self.location = Field(row, str(app.args.dir or default_directory()), width=440)
        self.location.pack(side="left", fill="x", expand=True)
        Button(row, "Browse", self._browse, kind="ghost", icon="folder", height=40).pack(side="left", padx=(T.S(8), 0))

        self.desktop = self._option("Desktop shortcut", not app.args.no_desktop)
        self.start_menu = self._option("Start menu shortcut", not app.args.no_start_menu)
        self.error = label(self, "", 9, color=T.DANGER)
        self.error.pack(anchor="w", pady=(T.S(8), 0))

        foot = self.footer()
        Button(foot, "Install", self._install, kind="primary", icon="import", width=150, height=42).pack(side="right")
        Button(foot, "Cancel", app.close, kind="ghost", height=42).pack(side="right", padx=T.S(8))

    def _option(self, text: str, value: bool) -> Toggle:
        card = Card(self, fill=T.SURFACE, radius=14, pad=14)
        card.pack(fill="x", pady=(T.S(10), 0))
        label(card.body, text, 10, "bold").pack(side="left")
        toggle = Toggle(card.body, value)
        toggle.pack(side="right")
        return toggle

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self.app.root, title="Install location")
        if chosen:
            path = Path(chosen)
            if path.name.lower() != APP_NAME.lower():
                path = path / APP_NAME
            self.location.var.set(str(path))

    def _install(self) -> None:
        text = self.location.get().strip()
        if not text or not Path(text).is_absolute():
            self.error.configure(text="Choose a full folder path.")
            return
        options = Options(Path(text), self.desktop.value, self.start_menu.value)
        self.app.run_task("install", lambda p: self.app.installer.install(options, p))


class ProgressView(View):
    TITLES = {"install": "Installing", "repair": "Repairing", "reinstall": "Reinstalling",
              "uninstall": "Uninstalling"}

    def __init__(self, app: "SetupApp", kind: str) -> None:
        super().__init__(app)
        spacer = tk.Frame(self, bg=T.BG)
        spacer.pack(expand=True)
        self.spinner = Spinner(self)
        self.spinner.pack(pady=(0, T.S(18)))
        label(self, f"{self.TITLES[kind]} {APP_NAME}", 18, "bold").pack()
        self.status = label(self, "Starting", 10, color=T.MUTED)
        self.status.pack(pady=(T.S(6), T.S(20)))
        self.bar = ProgressBar(self, width=460, height=8)
        self.bar.pack(fill="x", padx=T.S(40))
        self.bar.set_busy(True)
        tk.Frame(self, bg=T.BG).pack(expand=True)

    def update_progress(self, value: float, text: str) -> None:
        self.bar.set(value)
        if self.status.cget("text") != text:
            self.status.configure(text=text)


class Spinner(Host):
    """Rotating arc used while work is running."""

    def __init__(self, parent: tk.Misc, size: int = 64) -> None:
        self.d = T.S(size)
        super().__init__(parent, self.d, self.d)
        self._track = self.create_image(0, 0, anchor="nw", image=gfx.image(gfx.ring(self.d, T.S(4)), T.TRACK_OFF))
        self._arc = self.create_image(0, 0, anchor="nw")
        self._frames: dict[int, tk.PhotoImage] = {}
        _w, _h, cov = gfx.ring(self.d, T.S(4))
        c = self.d / 2
        self._pixels = []
        for i, a in enumerate(cov):
            if a:
                y, x = divmod(i, self.d)
                self._pixels.append((i, a, math.degrees(math.atan2(x + 0.5 - c, -(y + 0.5 - c))) % 360))
        anim.get().loop((self, "spin"), self._tick)

    def _frame(self, index: int) -> tk.PhotoImage:
        img = self._frames.get(index)
        if img is None:
            head = index * 360 / 72
            alpha = bytearray(self.d * self.d)
            for i, a, ang in self._pixels:
                rel = (head - ang) % 360
                if rel < 120:
                    alpha[i] = int(a * (1 - rel / 120) ** 1.2)
            img = gfx.alpha_image(self.d, self.d, bytes(alpha), T.ACCENT)
            self._frames[index] = img
        return img

    def _tick(self, now: float) -> None:
        self.itemconfigure(self._arc, image=self._frame(int(now * 72 / 1.1) % 72))


class DoneView(View):
    def __init__(self, app: "SetupApp", kind: str, directory: Path | None) -> None:
        super().__init__(app)
        titles = {"install": f"{APP_NAME} is installed", "repair": f"{APP_NAME} is repaired",
                  "reinstall": f"{APP_NAME} is reinstalled", "uninstall": f"{APP_NAME} was removed"}
        tk.Frame(self, bg=T.BG).pack(expand=True)
        Badge(self, "check", T.ACCENT).pack(pady=(0, T.S(18)))
        label(self, titles[kind], 18, "bold").pack()
        if directory is not None and kind != "uninstall":
            label(self, T.elide(str(directory), T.font(9), T.S(560)), 9, color=T.FAINT).pack(pady=(T.S(6), 0))
        tk.Frame(self, bg=T.BG).pack(expand=True)
        foot = self.footer()
        self.launch: Toggle | None = None
        if kind != "uninstall" and directory is not None:
            self.launch = Toggle(foot, True)
            self.launch.pack(side="left")
            label(foot, f"Open {APP_NAME}", 10).pack(side="left", padx=T.S(10))
            self.app_exe = directory / APP_EXE
        Button(foot, "Finish" if kind != "uninstall" else "Close", self._finish, kind="primary", width=130,
               height=42).pack(side="right")

    def _finish(self) -> None:
        if self.launch is not None and self.launch.value:
            self.app.installer.ops.launch(self.app_exe)
        self.app.close()


class ErrorView(View):
    def __init__(self, app: "SetupApp", message: str) -> None:
        super().__init__(app)
        tk.Frame(self, bg=T.BG).pack(expand=True)
        Badge(self, "close", T.DANGER).pack(pady=(0, T.S(18)))
        label(self, "Something went wrong", 18, "bold").pack()
        label(self, message, 10, color=T.MUTED, wraplength=T.S(520), justify="center").pack(pady=(T.S(8), 0))
        tk.Frame(self, bg=T.BG).pack(expand=True)
        foot = self.footer()
        Button(foot, "Close", app.close, kind="ghost", width=120, height=42).pack(side="right")


class MaintenanceView(View):
    def __init__(self, app: "SetupApp") -> None:
        super().__init__(app)
        manifest = app.installer.installed()
        where = T.elide(manifest.directory, T.font(10), T.S(560)) if manifest else ""
        self.title(f"{APP_NAME} is installed", f"Version {manifest.version if manifest else ''}\n{where}")
        box = tk.Frame(self, bg=T.BG)
        box.pack(fill="x", pady=(T.S(22), 0))
        for icon, title, detail, color, cmd in (
            ("wrench", "Repair", "Restore missing or damaged files", T.ACCENT, self._repair),
            ("refresh", "Reinstall", "Remove and install again; configs are kept", T.BLUE, self._reinstall),
            ("trash", "Uninstall", f"Remove {APP_NAME} from this PC", T.DANGER, self._uninstall),
        ):
            OptionCard(box, icon, title, detail, color, cmd).pack(fill="x", pady=(0, T.S(10)))
        foot = self.footer()
        Button(foot, "Close", app.close, kind="ghost", height=42).pack(side="right")

    def _repair(self) -> None:
        self.app.run_task("repair", self.app.installer.repair)

    def _reinstall(self) -> None:
        self.app.run_task("reinstall", self.app.installer.reinstall)

    def _uninstall(self) -> None:
        self.app.go(UninstallView(self.app, back=True))


class UninstallView(View):
    def __init__(self, app: "SetupApp", back: bool = False) -> None:
        super().__init__(app)
        self.title(f"Uninstall {APP_NAME}", "Interceptium, its shortcuts and its changes to Roblox will be removed.")
        card = Card(self, fill=T.SURFACE, radius=14, pad=14)
        card.pack(fill="x", pady=(T.S(24), 0))
        label(card.body, "Also delete configs and settings", 10, "bold").pack(side="left")
        self.remove_data = Toggle(card.body, app.args.remove_data)
        self.remove_data.pack(side="right")
        foot = self.footer()
        Button(foot, "Uninstall", self._go, kind="danger", icon="trash", width=150, height=42).pack(side="right")
        if back:
            Button(foot, "Back", lambda: app.go(MaintenanceView(app), direction=-1), kind="ghost",
                   height=42).pack(side="right", padx=T.S(8))
        else:
            Button(foot, "Cancel", app.close, kind="ghost", height=42).pack(side="right", padx=T.S(8))

    def _go(self) -> None:
        manifest = self.app.installer.installed()
        if manifest is None:
            self.app.go(DoneView(self.app, "uninstall", None))
            return
        remove = self.remove_data.value
        self.app.run_task("uninstall",
                          lambda p: self.app.installer.uninstall(Path(manifest.directory), remove, p))


class SetupApp:
    def __init__(self, root: tk.Tk, installer: Installer, args: argparse.Namespace) -> None:
        self.root, self.installer, self.args = root, installer, args
        self.busy = False
        self.events: queue.Queue = queue.Queue()
        root.title(f"{APP_NAME} Setup")
        root.configure(bg=T.BG)
        w, h = T.S(720), T.S(580)
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 3
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.resizable(False, False)
        try:
            self._icon = tk.PhotoImage(file=str(asset("logo.png")))
            root.iconphoto(True, self._icon)
            self._logo = tk.PhotoImage(file=str(asset("logo_48.png")))
        except tk.TclError:
            self._logo = None
        if sys.platform == "win32":
            try:
                root.iconbitmap(default=str(asset("logo.ico")))
            except tk.TclError:
                pass
        head = tk.Frame(root, bg=T.BG, padx=T.S(28), pady=T.S(20))
        head.pack(fill="x")
        if self._logo is not None:
            tk.Label(head, image=self._logo, bg=T.BG).pack(side="left", padx=(0, T.S(12)))
        label(head, f"{APP_NAME} Setup", 14, "bold").pack(side="left")
        label(head, f"Version {installer.version}", 9, color=T.FAINT).pack(side="right")
        self.stage = Stage(root, T.BG)
        self.stage.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(16, self._pump)

    def go(self, view: View, direction: int = 1) -> None:
        self.stage.show(view, direction=direction, animate=self.stage.current is not None)

    def run_task(self, kind: str, work: Callable[[Callable[[float, str], None]], object]) -> None:
        if self.busy:
            return
        self.busy = True
        view = ProgressView(self, kind)
        self.go(view)

        def report(value: float, text: str) -> None:
            self.events.put(("progress", value, text))

        def worker() -> None:
            try:
                result = work(report)
                directory = Path(result.directory) if hasattr(result, "directory") else None
                self.events.put(("done", kind, directory))
            except SetupError as exc:
                self.events.put(("error", str(exc)))
            except OSError as exc:
                self.events.put(("error", f"{exc.strerror or exc}. Close {APP_NAME} and try again."))
            except Exception as exc:  # unexpected: still show it instead of hanging
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))

        self._progress_view = view
        threading.Thread(target=worker, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self._progress_view.update_progress(event[1], event[2])
                elif event[0] == "done":
                    self._progress_view.update_progress(1.0, "Done")
                    self.busy = False
                    kind, directory = event[1], event[2]
                    self.root.after(420, lambda k=kind, d=directory: self.go(DoneView(self, k, d)))
                elif event[0] == "error":
                    self.busy = False
                    self.go(ErrorView(self, event[1]))
        except queue.Empty:
            pass
        self._pump_job = self.root.after(16, self._pump)

    def close(self) -> None:
        if self.busy:
            return  # never stop half-way through changing files
        anim.get().stop_all()
        try:
            self.root.after_cancel(self._pump_job)
        except (AttributeError, tk.TclError):
            pass
        self.root.destroy()


def run_ui(installer: Installer, mode: str, args: argparse.Namespace) -> int:
    root = boot.create_root(asset("fonts"))
    app = SetupApp(root, installer, args)
    if mode == "install":
        app.go(InstallView(app))
    elif mode == "maintenance":
        app.go(MaintenanceView(app))
    elif mode == "uninstall":
        app.go(UninstallView(app))
    elif mode == "repair":
        app.run_task("repair", installer.repair)
    elif mode == "reinstall":
        app.run_task("reinstall", installer.reinstall)
    root.deiconify()
    root.mainloop()
    return 0
