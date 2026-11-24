"""Interceptium desktop window."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable

from . import APP_NAME, __version__, roblox
from .app import Controller
from .logbus import log
from .rules import MODE_LABELS, describe, iter_rules, parse_target_text, rule_mode
from .ui import theme as T
from .ui.shapes import rounded
from .ui.widgets import (
    Card, IconButton, PowerButton, RoundButton, RoundEntry, ScrollArea, Segmented, StatusPill, Toggle,
    Tooltip, chip_image,
)

MAX_CARDS = 200
MODE_COLORS = {"id": T.ACCENT, "remove": T.DANGER, "local": "#7aa7ff", "cdn": T.WARN}


def asset_path(name: str) -> Path:
    here = Path(__file__).resolve()
    for base in (Path(getattr(sys, "_MEIPASS", "")), here.parent.parent.parent, here.parent):
        candidate = base / "assets" / name
        if candidate.exists():
            return candidate
    return here.parent / "assets" / name


def open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        log.emit("App", f"could not open {path}: {exc}")


def label(parent: tk.Misc, text: str, size: int = 10, weight: str = "normal", color: str = T.TEXT,
          **kw) -> tk.Label:
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=T.font(size, weight), **kw)


# ---------------------------------------------------------------- dialogs --
class Dialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, width: int = 460) -> None:
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=T.SURFACE)
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.body = tk.Frame(self, bg=T.SURFACE, padx=T.S(24), pady=T.S(22))
        self.body.pack(fill="both", expand=True)
        self._width = T.S(width)
        label(self.body, title, 15, "bold").pack(anchor="w", pady=(0, T.S(14)))
        self.bind("<Escape>", lambda _e: self.destroy())

    def show(self) -> None:
        self.update_idletasks()
        top = self.master.winfo_toplevel()
        w = max(self._width, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        x = top.winfo_rootx() + (top.winfo_width() - w) // 2
        y = top.winfo_rooty() + (top.winfo_height() - h) // 3
        self.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.grab_set()
        self.focus_set()
        self.wait_window()

    def buttons(self, ok_text: str, on_ok: Callable[[], None], kind: str = "primary") -> None:
        row = tk.Frame(self.body, bg=T.SURFACE)
        row.pack(fill="x", pady=(T.S(18), 0))
        RoundButton(row, ok_text, on_ok, kind=kind, height=38).pack(side="right")
        RoundButton(row, "Cancel", self.destroy, kind="ghost", height=38).pack(side="right", padx=T.S(8))
        self.bind("<Return>", lambda _e: on_ok())


class PromptDialog(Dialog):
    def __init__(self, parent: tk.Misc, title: str, message: str, value: str = "") -> None:
        super().__init__(parent, title)
        self.result: str | None = None
        label(self.body, message, 10, color=T.MUTED, justify="left").pack(anchor="w", pady=(0, T.S(8)))
        self.field = RoundEntry(self.body, value, width=400)
        self.field.pack(fill="x")
        self.buttons("Save", self._ok)
        self.after(50, self.field.entry.focus_set)
        self.field.entry.select_range(0, "end")

    def _ok(self) -> None:
        if self.field.get().strip():
            self.result = self.field.get().strip()
            self.destroy()


class ConfirmDialog(Dialog):
    def __init__(self, parent: tk.Misc, title: str, message: str, ok: str = "Delete") -> None:
        super().__init__(parent, title)
        self.result = False
        label(self.body, message, 10, color=T.MUTED, justify="left", wraplength=T.S(400)).pack(anchor="w")
        self.buttons(ok, self._ok, kind="danger")

    def _ok(self) -> None:
        self.result = True
        self.destroy()


class RuleDialog(Dialog):
    def __init__(self, parent: tk.Misc, rule: dict | None, configs_folder: Path) -> None:
        super().__init__(parent, "Edit rule" if rule else "New rule", width=520)
        self.rule = rule
        self.configs_folder = configs_folder
        self.result: dict | None = None
        rule = rule or {}
        mode = rule_mode(rule) if rule else "id"

        def field_label(text: str) -> None:
            label(self.body, text, 9, "bold", T.MUTED).pack(anchor="w", pady=(T.S(10), T.S(5)))

        field_label("NAME")
        self.name = RoundEntry(self.body, str(rule.get("name", "")), "e.g. Purple sky", width=470)
        self.name.pack(fill="x")

        field_label("ASSETS TO CHANGE")
        targets = " ".join(str(t) for t in rule.get("replace_ids", []) or [])
        self.targets = RoundEntry(self.body, targets, "Asset IDs or types, e.g. 1234567 89012345 Image", width=470)
        self.targets.pack(fill="x")

        field_label("ACTION")
        self.mode = Segmented(self.body, [("id", "Replace"), ("remove", "Remove"), ("local", "Local file")],
                              mode if mode != "cdn" else "remove", self._mode_changed, width=470)
        self.mode.pack(anchor="w")
        if mode == "cdn":
            label(self.body, "This rule used a CDN URL. Interceptium never contacts other servers, "
                  "so pick another action.", 9, color=T.WARN, wraplength=T.S(460), justify="left").pack(
                anchor="w", pady=(T.S(6), 0))

        self.value_label = label(self.body, "", 9, "bold", T.MUTED)
        self.value_label.pack(anchor="w", pady=(T.S(10), T.S(5)))
        row = tk.Frame(self.body, bg=T.SURFACE)
        row.pack(fill="x")
        start = ""
        if mode == "id" and rule.get("with_id") not in (None, ""):
            start = str(rule.get("with_id"))
        elif mode == "local":
            start = str(rule.get("local_path") or "")
        self.value = RoundEntry(row, start, width=360)
        self.value.pack(side="left", fill="x", expand=True)
        self.browse = RoundButton(row, "Browse", self._browse, kind="ghost", height=40)
        self.browse.pack(side="left", padx=(T.S(8), 0))

        en_row = tk.Frame(self.body, bg=T.SURFACE)
        en_row.pack(fill="x", pady=(T.S(16), 0))
        self.enabled = Toggle(en_row, bool(rule.get("enabled", True)))
        self.enabled.pack(side="left")
        label(en_row, "Rule is on", 10).pack(side="left", padx=T.S(10))

        self.error = label(self.body, "", 9, color=T.DANGER, wraplength=T.S(460), justify="left")
        self.error.pack(anchor="w", pady=(T.S(8), 0))
        self.buttons("Save rule", self._save)
        self._mode_changed(self.mode.value)
        self.after(50, self.name.entry.focus_set)

    def _mode_changed(self, mode: str) -> None:
        if mode == "remove":
            self.value_label.configure(text="NOTHING ELSE NEEDED: THE ASSET WON'T LOAD")
            self.value.set_enabled(False)
            self.browse.set_enabled(False)
        elif mode == "local":
            self.value_label.configure(text="FILE TO USE INSTEAD")
            self.value.set_enabled(True)
            self.browse.set_enabled(True)
        else:
            self.value_label.configure(text="REPLACEMENT ASSET ID")
            self.value.set_enabled(True)
            self.browse.set_enabled(False)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose replacement file")
        if path:
            self.value.var.set(path)

    def _save(self) -> None:
        try:
            targets = parse_target_text(self.targets.get())
            if not targets:
                raise ValueError("Add at least one asset ID or type.")
            mode = self.mode.value
            value = self.value.get().strip()
            if mode == "id" and not (value.isdigit() and int(value) > 80):
                raise ValueError("Enter the numeric ID of the replacement asset.")
            if mode == "local":
                if not value:
                    raise ValueError("Choose a file.")
                p = Path(value).expanduser()
                if not value.startswith("/") or not (self.configs_folder / value.lstrip("/")).is_file():
                    if not p.is_file():
                        raise ValueError("That file doesn't exist.")
                    try:  # store files inside the configs folder in portable "/Folder/file" form
                        value = "/" + p.resolve().relative_to(self.configs_folder.resolve()).as_posix()
                    except ValueError:
                        value = str(p)
        except ValueError as exc:
            self.error.configure(text=str(exc))
            return
        rule = self.rule if self.rule is not None else {}
        for key in ("with_id", "local_path", "cdn_url", "remove"):
            rule.pop(key, None)
        rule.update({"name": self.name.get().strip() or "Rule", "enabled": self.enabled.value,
                     "replace_ids": targets, "mode": mode})
        if mode == "id":
            rule["with_id"] = int(value)
        elif mode == "local":
            rule["local_path"] = value
        self.result = rule
        self.destroy()


class SettingsDialog(Dialog):
    def __init__(self, parent: tk.Misc, ctl: Controller) -> None:
        super().__init__(parent, "Settings", width=500)
        self.ctl = ctl
        s = ctl.config.settings

        def toggle_row(title: str, sub: str, value: bool, cmd: Callable[[bool], None]) -> None:
            card = Card(self.body, fill=T.CARD, radius=14, pad=14)
            card.pack(fill="x", pady=T.S(4))
            text = tk.Frame(card.body, bg=T.CARD)
            text.pack(side="left", fill="x", expand=True)
            label(text, title, 10, "bold").pack(anchor="w")
            label(text, sub, 9, color=T.MUTED, wraplength=T.S(340), justify="left").pack(anchor="w")
            Toggle(card.body, value, cmd).pack(side="right")

        toggle_row("Clear cache when launching", "Roblox keeps downloaded assets. Clearing makes it fetch "
                   "them again so your rules apply.", s.clear_cache_on_launch, self._set_clear)
        toggle_row("Reroute website launches", "When you join a game from the website, restart Roblox "
                   "through the proxy automatically.", s.auto_capture_launches, self._set_capture)

        port_card = Card(self.body, fill=T.CARD, radius=14, pad=14)
        port_card.pack(fill="x", pady=T.S(4))
        text = tk.Frame(port_card.body, bg=T.CARD)
        text.pack(side="left", fill="x", expand=True)
        label(text, "Proxy port", 10, "bold").pack(anchor="w")
        label(text, "Used on 127.0.0.1 only. Applies next start.", 9, color=T.MUTED).pack(anchor="w")
        self.port = RoundEntry(port_card.body, str(s.port), width=90, height=36)
        self.port.pack(side="right")
        self.port.var.trace_add("write", lambda *_: self._set_port())

        row = tk.Frame(self.body, bg=T.SURFACE)
        row.pack(fill="x", pady=(T.S(14), 0))
        RoundButton(row, "Open data folder", lambda: open_path(ctl.config.root), kind="ghost").pack(side="left")
        RoundButton(row, "Remove from Roblox", self._uninstall, kind="danger").pack(side="left", padx=T.S(8))

        safety = ("Safety: the proxy listens on 127.0.0.1 only, decrypts nothing except Roblox asset "
                  "servers, never reads accounts, cookies or tokens, and never contacts other servers. "
                  "Its certificate is limited to roblox.com and rbxcdn.com.")
        label(self.body, safety, 9, color=T.FAINT, wraplength=T.S(450), justify="left").pack(
            anchor="w", pady=(T.S(16), 0))
        label(self.body, f"{APP_NAME} {__version__}", 9, color=T.FAINT).pack(anchor="w", pady=(T.S(6), 0))
        close = tk.Frame(self.body, bg=T.SURFACE)
        close.pack(fill="x", pady=(T.S(12), 0))
        RoundButton(close, "Done", self.destroy, kind="primary").pack(side="right")

    def _set_clear(self, v: bool) -> None:
        self.ctl.config.settings.clear_cache_on_launch = v
        self.ctl.config.save()

    def _set_capture(self, v: bool) -> None:
        self.ctl.config.settings.auto_capture_launches = v
        self.ctl.config.save()
        if self.ctl.running:
            if v and (roblox.IS_WINDOWS or roblox.IS_MACOS):
                self.ctl.watcher.start()
            else:
                self.ctl.watcher.stop()

    def _set_port(self) -> None:
        text = self.port.get().strip()
        if text.isdigit() and 1024 <= int(text) <= 65535:
            self.ctl.config.settings.port = int(text)
            self.ctl.config.save()

    def _uninstall(self) -> None:
        dlg = ConfirmDialog(self, "Remove from Roblox?", "This takes Interceptium's certificate and setting "
                            "back out of every Roblox install. The proxy will stop working until you start it "
                            "again.", ok="Remove")
        dlg.show()
        if dlg.result:
            threading.Thread(target=self.ctl.remove_from_roblox, daemon=True).start()


# ------------------------------------------------------------ list items --
class ConfigRow(tk.Canvas):
    def __init__(self, parent: tk.Misc, name: str, count: int, enabled: bool, selected: bool,
                 on_select: Callable[[str], None], on_toggle: Callable[[str, bool], None]) -> None:
        self._bg = parent.cget("bg")
        self.h = T.S(54)
        super().__init__(parent, height=self.h, bg=self._bg, highlightthickness=0, bd=0, cursor="hand2")
        self.name, self.selected = name, selected
        self._hover = False
        self._img = self.create_image(0, 0, anchor="nw")
        self._dot = self.create_image(T.S(14), self.h / 2 - T.S(4), anchor="nw")
        self._title = self.create_text(T.S(30), T.S(19), anchor="w", text=name, font=T.font(10, "bold"))
        noun = "rule" if count == 1 else "rules"
        self._sub = self.create_text(T.S(30), T.S(36), anchor="w", text=f"{count} {noun}", font=T.font(8),
                                     fill=T.MUTED)
        self.toggle = Toggle(self, enabled, lambda v: on_toggle(name, v), width=40, height=22)
        self._win = self.create_window(0, self.h / 2, window=self.toggle, anchor="e")
        self.enabled = enabled
        for seq, fn in (("<Enter>", lambda _e: self._set_hover(True)), ("<Leave>", lambda _e: self._set_hover(False)),
                        ("<ButtonRelease-1>", lambda _e: on_select(name))):
            self.bind(seq, fn)
        self.bind("<Configure>", lambda _e: self._paint())

    def _set_hover(self, v: bool) -> None:
        self._hover = v
        self._paint()

    def _paint(self) -> None:
        w = self.winfo_width()
        if w < 10:
            return
        fill = T.CARD_SELECTED if self.selected else (T.CARD_HOVER if self._hover else self._bg)
        self._p = rounded(w, self.h, T.S(14), fill, self._bg, T.mix(T.ACCENT, fill, 0.55) if self.selected else None,
                          T.S(1) if self.selected else 0)
        self.itemconfigure(self._img, image=self._p)
        dot_color = T.ACCENT if self.toggle.value else T.FAINT
        self._d = rounded(T.S(8), T.S(8), T.S(4), dot_color, fill)
        self.itemconfigure(self._dot, image=self._d)
        self.itemconfigure(self._title, fill=T.TEXT)
        self.toggle.configure(bg=fill)
        self.toggle._bg = fill
        self.toggle._paint()
        self.coords(self._win, w - T.S(12), self.h / 2)
        max_w = w - T.S(30) - T.S(70)
        f = T.font(10, "bold")
        text = self.name
        while f.measure(text) > max_w and len(text) > 3:
            text = text[:-2]
        self.itemconfigure(self._title, text=text if text == self.name else text + "…")


class RuleCard(tk.Canvas):
    def __init__(self, parent: tk.Misc, rule: dict, on_toggle: Callable[[dict, bool], None],
                 on_edit: Callable[[dict], None], on_delete: Callable[[dict], None], indent: int = 0) -> None:
        self._bg = parent.cget("bg")
        self.h = T.S(76)
        super().__init__(parent, height=self.h, bg=self._bg, highlightthickness=0, bd=0)
        self.rule = rule
        self.indent = indent
        self._hover = False
        self._img = self.create_image(indent, 0, anchor="nw")
        self.toggle = Toggle(self, bool(rule.get("enabled", True)), lambda v: on_toggle(rule, v))
        self._t_win = self.create_window(indent + T.S(18), self.h / 2, window=self.toggle, anchor="w")
        self.edit_btn = IconButton(self, "✎", lambda: on_edit(rule), kind="subtle", size=34)
        self.del_btn = IconButton(self, "✕", lambda: on_delete(rule), kind="subtle", size=34, font_size=10)
        Tooltip(self.edit_btn, "Edit rule")
        Tooltip(self.del_btn, "Delete rule")
        self._e_win = self.create_window(0, self.h / 2, window=self.edit_btn, anchor="e")
        self._d_win = self.create_window(0, self.h / 2, window=self.del_btn, anchor="e")
        self._items: list[int] = []
        self._chips: list[tk.PhotoImage] = []
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Double-Button-1>", lambda _e: on_edit(rule))

    def _set_hover(self, v: bool) -> None:
        self._hover = v
        self._paint()

    def _paint(self) -> None:
        w = self.winfo_width()
        if w < 50:
            return
        x0 = self.indent
        cw = w - x0
        fill = T.CARD_HOVER if self._hover else T.CARD
        self._p = rounded(cw, self.h - T.S(8), T.S(16), fill, self._bg)
        self.itemconfigure(self._img, image=self._p)
        self.coords(self._img, x0, T.S(4))
        for widget in (self.toggle, self.edit_btn, self.del_btn):
            widget.configure(bg=fill)
            widget._bg = fill
            widget._paint()
        self.coords(self._t_win, x0 + T.S(18), self.h / 2)
        self.coords(self._d_win, w - T.S(14), self.h / 2)
        self.coords(self._e_win, w - T.S(14) - T.S(40), self.h / 2)
        for item in self._items:
            self.delete_item(item)
        self._items.clear()
        self._chips.clear()

        enabled = bool(self.rule.get("enabled", True))
        mode = rule_mode(self.rule)
        text_x = x0 + T.S(76)
        right_limit = w - T.S(110)
        # mode badge on the right
        badge_text = MODE_LABELS[mode]
        color = MODE_COLORS[mode]
        img, bw, bh = chip_image(badge_text, T.mix(color, fill, 0.82), fill, 9)
        self._chips.append(img)
        bx = right_limit - bw
        self._items.append(self.create_image(bx, T.S(16), image=img, anchor="nw"))
        self._items.append(self.create_text(bx + bw / 2, T.S(16) + bh / 2, text=badge_text,
                                            font=T.font(9, "bold"), fill=color))
        desc = describe(self.rule) if mode != "cdn" else "skipped"
        self._items.append(self.create_text(right_limit, T.S(52), text=desc, anchor="e", font=T.font(9),
                                            fill=T.MUTED))
        # name
        name = str(self.rule.get("name") or "Untitled rule")
        f = T.font(11, "bold")
        max_name = bx - text_x - T.S(12)
        shown = name
        while f.measure(shown) > max_name and len(shown) > 3:
            shown = shown[:-2]
        if shown != name:
            shown += "…"
        self._items.append(self.create_text(text_x, T.S(27), text=shown, anchor="w", font=f,
                                            fill=T.TEXT if enabled else T.FAINT))
        # target chips
        targets = [str(t) for t in self.rule.get("replace_ids", []) or []]
        x = text_x
        limit = right_limit - T.S(150)
        for i, t in enumerate(targets):
            rest = len(targets) - i
            img, cw2, ch = chip_image(t, T.CHIP, fill)
            if x + cw2 > limit and rest > 0:
                more = f"+{rest}"
                img, cw2, ch = chip_image(more, T.CHIP, fill)
                self._chips.append(img)
                self._items.append(self.create_image(x, T.S(41), image=img, anchor="nw"))
                self._items.append(self.create_text(x + cw2 / 2, T.S(41) + ch / 2, text=more,
                                                    font=T.font(9), fill=T.MUTED))
                break
            self._chips.append(img)
            self._items.append(self.create_image(x, T.S(41), image=img, anchor="nw"))
            self._items.append(self.create_text(x + cw2 / 2, T.S(41) + ch / 2, text=t, font=T.font(9),
                                                fill=T.MUTED if enabled else T.FAINT))
            x += cw2 + T.S(6)

    def delete_item(self, item: int) -> None:
        tk.Canvas.delete(self, item)


# ------------------------------------------------------------ main window --
class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.ctl = Controller()
        self.store = self.ctl.config
        self.lines: queue.Queue[str] = queue.Queue()
        log.subscribe(self.lines.put)
        names = self.store.names()
        enabled = [n for n in self.store.settings.enabled_configs if n in names]
        self.selected: str = enabled[0] if enabled else names[0]
        self.data: dict = self.store.load_config(self.selected)
        self.show_all = False

        root.title(APP_NAME)
        root.configure(bg=T.BG)
        root.geometry(f"{T.S(1140)}x{T.S(800)}")
        root.minsize(T.S(940), T.S(620))
        self._set_icons()

        shell = tk.Frame(root, bg=T.BG, padx=T.S(20), pady=T.S(16))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        self._build_header(shell)
        self._build_sidebar(shell)
        self._build_rules(shell)
        self._build_log(shell)

        self.refresh_configs()
        self.refresh_rules()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(150, self._pump_log)
        log.emit("App", f"{APP_NAME} {__version__} ready. Press the round button to start")

    # -- layout -----------------------------------------------------------
    def _set_icons(self) -> None:
        try:
            self._logo_big = tk.PhotoImage(file=str(asset_path("logo.png")))
            self.root.iconphoto(True, self._logo_big)
            self._logo = tk.PhotoImage(file=str(asset_path("logo_48.png")))
        except tk.TclError:
            self._logo = None
        if sys.platform == "win32":
            try:
                self.root.iconbitmap(default=str(asset_path("logo.ico")))
            except tk.TclError:
                pass

    def _build_header(self, shell: tk.Frame) -> None:
        head = tk.Frame(shell, bg=T.BG)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, T.S(14)))
        if self._logo is not None:
            tk.Label(head, image=self._logo, bg=T.BG).pack(side="left", padx=(0, T.S(12)))
        titles = tk.Frame(head, bg=T.BG)
        titles.pack(side="left")
        label(titles, APP_NAME, 19, "bold").pack(anchor="w")
        label(titles, "Local Roblox asset proxy · nothing leaves your PC", 9, color=T.MUTED).pack(anchor="w")
        gear = IconButton(head, "⚙", self.open_settings, kind="ghost", size=38, font_size=13)
        gear.pack(side="right")
        Tooltip(gear, "Settings")
        self.status = StatusPill(head)
        self.status.pack(side="right", padx=T.S(12))

    def _build_sidebar(self, shell: tk.Frame) -> None:
        side = tk.Frame(shell, bg=T.BG, width=T.S(300))
        side.grid(row=1, column=0, sticky="nsw", padx=(0, T.S(16)))
        side.grid_propagate(False)
        side.pack_propagate(False)

        proxy = Card(side, fill=T.SURFACE, radius=22, pad=18)
        proxy.pack(fill="x")
        self.power = PowerButton(proxy.body, self.toggle_proxy, size=124)
        self.power.pack(pady=(T.S(2), T.S(8)))
        self.proxy_line = label(proxy.body, "Proxy is off", 9, color=T.MUTED)
        self.proxy_line.pack()
        btns = tk.Frame(proxy.body, bg=T.SURFACE)
        btns.pack(fill="x", pady=(T.S(12), 0))
        RoundButton(btns, "▶  Launch Roblox", self.launch, kind="primary", width=250, height=40).pack()
        RoundButton(btns, "Clear Roblox cache", self.clear_cache, kind="ghost", width=250, height=36).pack(
            pady=(T.S(8), 0))

        configs = Card(side, fill=T.SURFACE, radius=22, pad=14, fill_height=True)
        configs.pack(fill="both", expand=True, pady=(T.S(14), 0))
        top = tk.Frame(configs.body, bg=T.SURFACE)
        top.pack(fill="x", pady=(0, T.S(8)))
        label(top, "Configs", 12, "bold").pack(side="left", padx=(T.S(4), 0))
        for glyph, tip, cmd, kind in (("+", "New config", self.new_config, "primary"),
                                      ("↓", "Import Fleasion / Interceptium configs", self.import_config, "ghost"),
                                      ("⋯", "Open configs folder", lambda: open_path(self.store.folder), "ghost")):
            btn = IconButton(top, glyph, cmd, kind=kind, size=30, font_size=12)
            btn.pack(side="right", padx=(T.S(6), 0))
            Tooltip(btn, tip)
        label(configs.body, "Switch on every config you want to run.", 8, color=T.FAINT).pack(
            anchor="w", padx=T.S(4), pady=(0, T.S(6)))
        self.config_list = ScrollArea(configs.body, T.SURFACE)
        self.config_list.pack(fill="both", expand=True)

    def _build_rules(self, shell: tk.Frame) -> None:
        panel = Card(shell, fill=T.SURFACE, radius=22, pad=20, fill_height=True)
        panel.grid(row=1, column=1, sticky="nsew")
        head = tk.Frame(panel.body, bg=T.SURFACE)
        head.pack(fill="x")
        titles = tk.Frame(head, bg=T.SURFACE)
        titles.pack(side="left", fill="x", expand=True)
        self.config_title = label(titles, "", 17, "bold", anchor="w")
        self.config_title.pack(anchor="w", fill="x")
        self.config_sub = label(titles, "", 9, color=T.MUTED, anchor="w")
        self.config_sub.pack(anchor="w")
        RoundButton(head, "Delete", self.delete_config, kind="danger", height=32, size=9).pack(side="right")
        RoundButton(head, "Export", self.export_config, kind="ghost", height=32, size=9).pack(
            side="right", padx=T.S(6))
        RoundButton(head, "Rename", self.rename_config, kind="ghost", height=32, size=9).pack(side="right")
        RoundButton(head, "+  Add rule", self.add_rule, kind="primary", height=38).pack(side="right", padx=T.S(12))

        self.search = RoundEntry(panel.body, "", "Search rules by name, ID or type…", width=500, height=38)
        self.search.pack(fill="x", pady=(T.S(14), T.S(10)))
        self.search.var.trace_add("write", lambda *_: self._search_changed())
        self.rule_list = ScrollArea(panel.body, T.SURFACE)
        self.rule_list.pack(fill="both", expand=True)

    def _build_log(self, shell: tk.Frame) -> None:
        card = Card(shell, fill=T.SURFACE, radius=18, pad=14)
        card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(T.S(14), 0))
        top = tk.Frame(card.body, bg=T.SURFACE)
        top.pack(fill="x")
        label(top, "Activity", 10, "bold").pack(side="left", padx=(T.S(4), 0))
        self.log_toggle = RoundButton(top, "Hide", self._toggle_log, kind="subtle", height=26, size=8)
        self.log_toggle.pack(side="right")
        self.log_wrap = tk.Frame(card.body, bg=T.SURFACE)
        self.log_wrap.pack(fill="x", pady=(T.S(8), 0))
        self.log_text = tk.Text(self.log_wrap, height=4, bg=T.SURFACE, fg=T.MUTED, bd=0, highlightthickness=0,
                                font=("Consolas" if sys.platform == "win32" else "Menlo" if sys.platform == "darwin"
                                      else "DejaVu Sans Mono", 9), wrap="word", state="disabled",
                                padx=T.S(4), selectbackground=T.CARD_HOVER)
        self.log_text.pack(fill="x")
        for tag, color in (("Replace", T.ACCENT), ("Remove", T.DANGER), ("Error", T.DANGER),
                           ("File", "#7aa7ff"), ("Proxy", T.TEXT), ("Watcher", T.WARN), ("time", T.FAINT)):
            self.log_text.tag_configure(tag, foreground=color)

    def _toggle_log(self) -> None:
        if self.log_wrap.winfo_ismapped():
            self.log_wrap.pack_forget()
            self.log_toggle.configure_text("Show")
        else:
            self.log_wrap.pack(fill="x", pady=(T.S(8), 0))
            self.log_toggle.configure_text("Hide")

    # -- background work -------------------------------------------------
    def _bg(self, fn: Callable[[], object], done: Callable[[], None] | None = None) -> None:
        def work() -> None:
            try:
                fn()
            except Exception as exc:
                log.emit("Error", str(exc))
            if done:
                self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _pump_log(self) -> None:
        lines = []
        while not self.lines.empty():
            lines.append(self.lines.get_nowait())
        if lines:
            self.log_text.configure(state="normal")
            for line in lines:
                stamp, _, rest = line.partition(" ")
                topic = rest[1:rest.find("]")] if rest.startswith("[") else ""
                self.log_text.insert("end", stamp + "  ", "time")
                self.log_text.insert("end", rest + "\n", topic)
            if int(self.log_text.index("end-1c").split(".")[0]) > 2000:
                self.log_text.delete("1.0", "500.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(150, self._pump_log)

    # -- proxy -------------------------------------------------------------
    def _update_status(self) -> None:
        running = self.ctl.running
        self.power.set_state(running)
        if running:
            self.status.set(f"Running · {self.ctl.proxy_url.removeprefix('http://')}", T.ACCENT)
            self.proxy_line.configure(text=f"Listening on {self.ctl.proxy_url.removeprefix('http://')}")
        else:
            self.status.set("Stopped", T.DANGER)
            self.proxy_line.configure(text="Proxy is off")

    def toggle_proxy(self) -> None:
        self.power.set_state(self.ctl.running, busy=True)
        self._bg(self.ctl.stop if self.ctl.running else self.ctl.start, self._update_status)

    def launch(self) -> None:
        self.power.set_state(self.ctl.running, busy=True)
        self._bg(self.ctl.launch_roblox, self._update_status)

    def clear_cache(self) -> None:
        self._bg(roblox.clear_cache)

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.ctl).show()

    # -- configs -----------------------------------------------------------
    def refresh_configs(self) -> None:
        for child in self.config_list.inner.winfo_children():
            child.destroy()
        for name in self.store.names():
            data = self.data if name == self.selected else self.store.load_config(name)
            count = sum(1 for _ in iter_rules(data["replacement_rules"]))
            row = ConfigRow(self.config_list.inner, name, count, self.store.is_enabled(name), name == self.selected,
                            self.select_config, self.toggle_config)
            row.pack(fill="x", pady=T.S(2))
            self.config_list.bind_all_wheel(row)
        self._update_header()

    def _update_header(self) -> None:
        total = sum(1 for _ in iter_rules(self.data["replacement_rules"]))
        on = sum(1 for r in iter_rules(self.data["replacement_rules"]) if r.get("enabled", True))
        state = "running" if self.store.is_enabled(self.selected) else "switched off"
        self.config_title.configure(text=self.selected)
        self.config_sub.configure(text=f"{total} rules · {on} on · config is {state}")

    def select_config(self, name: str) -> None:
        if name == self.selected:
            return
        self.selected = name
        self.data = self.store.load_config(name)
        self.show_all = False
        self.search.var.set("")
        self.refresh_configs()
        self.refresh_rules()
        self.rule_list.to_top()

    def toggle_config(self, name: str, value: bool) -> None:
        self.store.set_enabled(name, value)
        log.emit("Configs", f"{name} {'on' if value else 'off'}. Clear the Roblox cache to see changes")
        self.root.after(220, self.refresh_configs)

    def new_config(self) -> None:
        dlg = PromptDialog(self.root, "New config", "Give your config a name.")
        dlg.show()
        if dlg.result:
            try:
                name = self.store.create(dlg.result)
            except ValueError as exc:
                log.emit("Error", str(exc))
                return
            self.store.set_enabled(name, True)
            self.selected = ""
            self.select_config(name)

    def import_config(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, title="Import Fleasion / Interceptium configs",
                                            filetypes=[("JSON config", "*.json"), ("All files", "*.*")])
        last = None
        for path in paths:
            try:
                last = self.store.import_file(Path(path))
                log.emit("Configs", f"imported {last}")
            except (OSError, ValueError) as exc:
                log.emit("Error", f"{Path(path).name}: {exc}")
        if last:
            self.selected = ""
            self.select_config(last)

    def rename_config(self) -> None:
        dlg = PromptDialog(self.root, "Rename config", "New name:", self.selected)
        dlg.show()
        if dlg.result:
            try:
                self.selected = self.store.rename(self.selected, dlg.result)
            except (OSError, ValueError) as exc:
                log.emit("Error", str(exc))
            self.refresh_configs()

    def export_config(self) -> None:
        dest = filedialog.asksaveasfilename(parent=self.root, title="Export config", defaultextension=".json",
                                            initialfile=f"{self.selected}.json", filetypes=[("JSON", "*.json")])
        if dest:
            self.store.export(self.selected, Path(dest))
            log.emit("Configs", f"exported {self.selected}")

    def delete_config(self) -> None:
        dlg = ConfirmDialog(self.root, f"Delete “{self.selected}”?",
                            "The config file and all its rules will be deleted. This can't be undone.")
        dlg.show()
        if not dlg.result:
            return
        self.store.delete(self.selected)
        if not self.store.names():
            self.store.create("Default")
            self.store.set_enabled("Default", True)
        self.selected = ""
        self.select_config(self.store.names()[0])

    # -- rules ---------------------------------------------------------------
    def _save(self) -> None:
        self.store.save_config(self.selected, self.data)
        self._update_header()

    def _search_changed(self) -> None:
        if hasattr(self, "_search_job"):
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(180, self.refresh_rules)

    def _visible_items(self) -> list[tuple[int, str, object, list]]:
        query = self.search.get().strip().lower()
        items: list[tuple[int, str, object, list]] = []

        def matches(rule: dict) -> bool:
            if not query:
                return True
            hay = " ".join([str(rule.get("name", ""))] + [str(t) for t in rule.get("replace_ids", []) or []]
                           + [str(rule.get("with_id", "")), str(rule.get("local_path", ""))]).lower()
            return query in hay

        def walk(entries: list, depth: int) -> None:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "group":
                    children = entry.get("children") or []
                    before = len(items)
                    items.append((depth, "group", entry, entries))
                    walk(children, depth + 1)
                    if len(items) == before + 1 and query:
                        items.pop()
                elif matches(entry):
                    items.append((depth, "rule", entry, entries))

        walk(self.data["replacement_rules"], 0)
        return items

    def refresh_rules(self) -> None:
        inner = self.rule_list.inner
        for child in inner.winfo_children():
            child.destroy()
        items = self._visible_items()
        if not items:
            self._empty_state(inner)
            return
        shown = items if self.show_all else items[:MAX_CARDS]
        for depth, kind, entry, _parent in shown:
            indent = T.S(22) * depth
            if kind == "group":
                row = tk.Frame(inner, bg=T.SURFACE)
                row.pack(fill="x", pady=(T.S(10), T.S(2)), padx=(indent + T.S(6), 0))
                label(row, "▾  " + str(entry.get("name") or "Group"), 10, "bold", T.MUTED).pack(side="left")
                count = sum(1 for _ in iter_rules(entry.get("children") or []))
                label(row, f"  {count}", 9, color=T.FAINT).pack(side="left")
                continue
            card = RuleCard(inner, entry, self._rule_toggled, self.edit_rule, self.delete_rule, indent=indent)
            card.pack(fill="x")
            self.rule_list.bind_all_wheel(card)
        if len(items) > len(shown):
            more = RoundButton(inner, f"Show all {len(items)} items", self._show_all, kind="ghost")
            more.pack(pady=T.S(10))

    def _show_all(self) -> None:
        self.show_all = True
        self.refresh_rules()

    def _empty_state(self, inner: tk.Frame) -> None:
        box = tk.Frame(inner, bg=T.SURFACE)
        box.pack(fill="x", pady=T.S(60))
        ring = tk.Canvas(box, width=T.S(84), height=T.S(84), bg=T.SURFACE, highlightthickness=0)
        self._empty_img = rounded(T.S(84), T.S(84), T.S(42), T.CARD, T.SURFACE, T.LINE, T.S(1.5))
        ring.create_image(0, 0, image=self._empty_img, anchor="nw")
        ring.create_text(T.S(42), T.S(42), text="✦", font=T.font(24), fill=T.ACCENT)
        ring.pack()
        searching = bool(self.search.get().strip())
        label(box, "No matching rules" if searching else "No rules yet", 13, "bold").pack(pady=(T.S(12), T.S(2)))
        label(box, "Try a different search." if searching else
              "Add a rule, or import a Fleasion config from the sidebar.", 9, color=T.MUTED).pack()
        if not searching:
            RoundButton(box, "+  Add your first rule", self.add_rule, kind="primary", height=40).pack(pady=T.S(14))

    def _rule_toggled(self, rule: dict, value: bool) -> None:
        rule["enabled"] = value
        self._save()
        self.root.after(200, self._repaint_cards)

    def _repaint_cards(self) -> None:
        for child in self.rule_list.inner.winfo_children():
            if isinstance(child, RuleCard):
                child._paint()

    def add_rule(self) -> None:
        dlg = RuleDialog(self.root, None, self.store.folder)
        dlg.show()
        if dlg.result:
            self.data["replacement_rules"].append(dlg.result)
            self._save()
            self.refresh_rules()
            self.refresh_configs()
            self.root.after(50, lambda: self.rule_list.canvas.yview_moveto(1.0))
            log.emit("Rules", f"added “{dlg.result['name']}”. Clear the Roblox cache to see it in game")

    def edit_rule(self, rule: dict) -> None:
        dlg = RuleDialog(self.root, rule, self.store.folder)
        dlg.show()
        if dlg.result:
            self._save()
            self.refresh_rules()

    def delete_rule(self, rule: dict) -> None:
        for _d, _k, entry, parent in self._visible_items():
            if entry is rule:
                parent.remove(rule)
                break
        self._save()
        self.refresh_rules()
        self.refresh_configs()

    def on_close(self) -> None:
        self.ctl.shutdown()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except (AttributeError, OSError):
            pass


def run() -> int:
    _enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    T.setup(root)
    MainWindow(root)
    root.deiconify()
    root.mainloop()
    return 0
