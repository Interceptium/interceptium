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
from .ui import anim, fonts, gfx
from .ui.overlay import OverlayHost, Popup
from .ui import theme as T
from .ui.widgets import (
    Button, Card, Field, Host, IconButton, IconButtonC, PowerButton, ScrollArea, Segmented, StatusPill, Toggle,
    ToggleC, install_wheel,
)

MAX_CARDS = 200
MODE_COLORS = {"id": T.ACCENT, "remove": T.DANGER, "local": T.BLUE, "cdn": T.WARN}


def asset_path(name: str) -> Path:
    here = Path(__file__).resolve()
    bases = [here.parent.parent.parent, here.parent]
    if getattr(sys, "_MEIPASS", None):
        bases.insert(0, Path(sys._MEIPASS))  # type: ignore[attr-defined]
    for base in bases:
        candidate = base / "assets" / name
        if candidate.exists():
            return candidate
    return bases[0] / "assets" / name


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


def count_rules(entries: list) -> tuple[int, int]:
    total = on = 0
    for rule in iter_rules(entries):
        total += 1
        on += bool(rule.get("enabled", True))
    return total, on


# ------------------------------------------------------------------ pages --
class Page(Popup):
    """A pop-up over a dimmed backdrop with smooth open/close animations."""

    def __init__(self, win: "MainWindow", title: str, width: int = 540) -> None:
        super().__init__(win.overlay, title, width)
        self.win = win

    def field_label(self, text: str, parent: tk.Misc | None = None, top: int = 14) -> tk.Label:
        lab = label(parent or self.body, text, 9, "bold", T.MUTED)
        lab.pack(anchor="w", pady=(T.S(top), T.S(6)))
        return lab

    def buttons(self, ok_text: str, on_ok: Callable[[], None], kind: str = "primary") -> None:
        row = tk.Frame(self.footer, bg=T.SURFACE)
        row.pack(fill="x", pady=(T.S(20), 0), padx=(0, T.S(12)))  # line up with the scroll gutter
        Button(row, ok_text, on_ok, kind=kind).pack(side="right")
        Button(row, "Cancel", self.close, kind="ghost").pack(side="right", padx=T.S(8))


class PromptPage(Page):
    def __init__(self, win: "MainWindow", title: str, on_ok: Callable[[str], None], value: str = "",
                 ok: str = "Save") -> None:
        super().__init__(win, title, width=500)
        self.on_ok = on_ok
        self.field_label("Name", top=0)
        self.field = Field(self.body, value, width=470)
        self.field.pack(fill="x")
        self.first_focus = self.field.entry
        self.buttons(ok, self.primary)

    def primary(self) -> None:
        text = self.field.get().strip()
        if text:
            self.close()
            self.on_ok(text)


class ConfirmPage(Page):
    def __init__(self, win: "MainWindow", title: str, message: str, on_confirm: Callable[[], None],
                 ok: str = "Delete") -> None:
        super().__init__(win, title, width=500)
        self.on_confirm = on_confirm
        label(self.body, message, 10, color=T.MUTED, justify="left", wraplength=T.S(470)).pack(anchor="w")
        self.buttons(ok, self.primary, kind="danger")

    def primary(self) -> None:
        self.close()
        self.on_confirm()


class AssetList(tk.Frame):
    """One field per asset. "Add asset" appends Asset 2, Asset 3, ..."""

    def __init__(self, parent: tk.Misc, values: list[str], width: int) -> None:
        super().__init__(parent, bg=parent.cget("bg"))
        self.width = width
        self.rows: list[tuple[tk.Frame, tk.Label, Field, IconButton]] = []
        self.box = tk.Frame(self, bg=self.cget("bg"))
        self.box.pack(fill="x")
        Button(self, "Add asset", self.add, kind="ghost", icon="plus", height=34, size=9).pack(
            anchor="w", pady=(T.S(2), 0))
        for value in values or [""]:
            self.add(value, focus=False)

    def add(self, value: str = "", focus: bool = True) -> Field:
        bg = self.cget("bg")
        row = tk.Frame(self.box, bg=bg)
        row.pack(fill="x", pady=(0, T.S(8)))
        name = label(row, "", 9, "bold", T.FAINT, width=7, anchor="w")
        name.pack(side="left")
        field = Field(row, value, "ID or type", width=self.width - 120)
        field.pack(side="left", fill="x", expand=True)
        remove = IconButton(row, "close", lambda: self.remove(row), kind="subtle", size=34, icon_size=14)
        self.rows.append((row, name, field, remove))
        self._renumber()
        if focus:
            field.entry.focus_set()
            self.event_generate("<<AssetAdded>>")
        return field

    def remove(self, row: tk.Frame) -> None:
        if len(self.rows) <= 1:
            return
        self.rows = [r for r in self.rows if r[0] is not row]
        row.destroy()
        self._renumber()

    def _renumber(self) -> None:
        many = len(self.rows) > 1
        for i, (_row, name, _field, remove) in enumerate(self.rows, start=1):
            name.configure(text=f"Asset {i}")
            if many and not remove.winfo_manager():
                remove.pack(side="left", padx=(T.S(6), 0))
            elif not many and remove.winfo_manager():
                remove.pack_forget()

    def values(self) -> list[tuple[int, str]]:
        return [(i, f.get().strip()) for i, (_r, _n, f, _b) in enumerate(self.rows, start=1)]


class RulePage(Page):
    MODES = [("id", "Replace"), ("remove", "Remove"), ("local", "Local file")]

    def __init__(self, win: "MainWindow", rule: dict | None, on_save: Callable[[dict], None]) -> None:
        super().__init__(win, "Edit rule" if rule else "New rule")
        self.rule = rule
        self.on_save = on_save
        self.configs_folder = win.store.folder
        rule = rule or {}
        mode = rule_mode(rule) if rule else "id"
        width = 480

        self.field_label("Name", top=0)
        self.name = Field(self.body, str(rule.get("name", "")), width=width)
        self.name.pack(fill="x")
        self.first_focus = self.name.entry

        self.field_label("Assets")
        self.assets = AssetList(self.body, [str(t) for t in rule.get("replace_ids", []) or []], width)
        self.assets.pack(fill="x")
        self.assets.bind("<<AssetAdded>>", lambda _e: self.after_idle(self._reveal_last_asset))

        self.field_label("Action")
        self.mode = Segmented(self.body, self.MODES, mode if mode != "cdn" else "remove", self._mode_changed,
                              width=width)
        self.mode.pack(anchor="w")
        if mode == "cdn":
            label(self.body, "CDN rules are not supported.", 9, color=T.WARN).pack(anchor="w", pady=(T.S(8), 0))

        self.value_box = tk.Frame(self.body, bg=T.SURFACE)
        self.value_box.pack(fill="x")
        self.value_label = self.field_label("", self.value_box)
        row = tk.Frame(self.value_box, bg=T.SURFACE)
        row.pack(fill="x")
        start = ""
        if mode == "id" and rule.get("with_id") not in (None, ""):
            start = str(rule.get("with_id"))
        elif mode == "local":
            start = str(rule.get("local_path") or "")
        self.value = Field(row, start, width=width - 110)
        self.value.pack(side="left", fill="x", expand=True)
        self.browse = Button(row, "Browse", self._browse, kind="ghost", height=40)

        self.anchor = tk.Frame(self.body, bg=T.SURFACE)
        self.anchor.pack(fill="x")
        en = tk.Frame(self.anchor, bg=T.SURFACE)
        en.pack(fill="x", pady=(T.S(20), 0))
        self.enabled = Toggle(en, bool(rule.get("enabled", True)))
        self.enabled.pack(side="left")
        label(en, "Enabled", 10).pack(side="left", padx=T.S(10))
        self.error = label(self.anchor, "", 9, color=T.DANGER, wraplength=T.S(460), justify="left")
        self.error.pack(anchor="w", pady=(T.S(10), 0))
        self.buttons("Save", self.primary)
        self._mode_changed(self.mode.value)

    def _reveal_last_asset(self) -> None:
        field = self.assets.rows[-1][2]
        y = field.winfo_rooty() - self.scroll.inner.winfo_rooty()
        view = self.scroll.canvas.winfo_height()
        if y + field.winfo_height() > self.scroll.pos + view - T.S(80):
            self.scroll.scroll_by(y + field.winfo_height() - (self.scroll.pos + view) + T.S(120))

    def _mode_changed(self, mode: str) -> None:
        if mode == "remove":
            self.value_box.pack_forget()
        else:
            self.value_box.pack(fill="x", before=self.anchor)
            self.value_label.configure(text="File" if mode == "local" else "Replacement asset ID")
        if mode == "local" and not self.browse.winfo_manager():
            self.browse.pack(side="left", padx=(T.S(8), 0))
        elif mode != "local" and self.browse.winfo_manager():
            self.browse.pack_forget()
        self.error.configure(text="")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(parent=self.win.root, title="Choose file")
        if path:
            self.value.var.set(path)

    def collect_targets(self) -> list[str]:
        targets: list[str] = []
        for index, text in self.assets.values():
            if not text:
                continue
            try:
                tokens = parse_target_text(text)
            except ValueError as exc:
                raise ValueError(f"Asset {index}: {exc}") from None
            for token in tokens:
                if token not in targets:
                    targets.append(token)
        if not targets:
            raise ValueError("Enter at least one asset ID or type.")
        return targets

    def primary(self) -> None:
        try:
            targets = self.collect_targets()
            mode = self.mode.value
            value = self.value.get().strip()
            if mode == "id" and not (value.isdigit() and int(value) > 80):
                raise ValueError("Enter a numeric replacement asset ID.")
            if mode == "local":
                if not value:
                    raise ValueError("Choose a file.")
                portable = value.startswith("/") and (self.configs_folder / value.lstrip("/")).is_file()
                if not portable:
                    p = Path(value).expanduser()
                    if not p.is_file():
                        raise ValueError("File not found.")
                    try:  # files inside the configs folder are stored as portable "/Folder/file"
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
        self.close()
        self.on_save(rule)


class SettingsPage(Page):
    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win, "Settings")
        ctl = win.ctl
        self.ctl = ctl
        s = ctl.config.settings
        self._row("Clear cache on launch", Toggle, s.clear_cache_on_launch, self._set_clear, first=True)
        self._row("Reroute website launches", Toggle, s.auto_capture_launches, self._set_capture)
        port_body = self._row("Port", None, None, None)
        self.port = Field(port_body, str(s.port), width=96, height=34)
        self.port.pack(side="right")
        self.port.var.trace_add("write", lambda *_: self._set_port())

        row = tk.Frame(self.body, bg=T.SURFACE)
        row.pack(fill="x", pady=(T.S(18), 0))
        Button(row, "Open data folder", lambda: open_path(ctl.config.root), kind="ghost").pack(side="left")
        Button(row, "Remove from Roblox", self._uninstall, kind="danger").pack(side="left", padx=T.S(8))
        label(self.body, f"Version {__version__}", 9, color=T.FAINT).pack(anchor="w", pady=(T.S(22), 0))

    def _row(self, title: str, widget, value, command, first: bool = False) -> tk.Frame:
        card = Card(self.body, fill=T.CARD, radius=14, pad=14)
        card.pack(fill="x", pady=(0 if first else T.S(8), 0))
        label(card.body, title, 10, "bold").pack(side="left")
        if widget is not None:
            widget(card.body, value, command).pack(side="right")
        return card.body

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
        self.win.push_page(ConfirmPage(
            self.win, "Remove from Roblox",
            "The Interceptium certificate and setting will be removed from all Roblox installs.",
            lambda: threading.Thread(target=self.ctl.remove_from_roblox, daemon=True).start(), ok="Remove"))


# ------------------------------------------------------------ list items --
class ConfigRow(Host):
    def __init__(self, parent: tk.Misc, name: str, count: int, enabled: bool, selected: bool,
                 on_select: Callable[[str], None], on_toggle: Callable[[str, bool], None]) -> None:
        self.h = T.S(52)
        super().__init__(parent, T.S(240), self.h)
        self.name, self.count, self.selected = name, count, selected
        self.on_select, self.on_toggle = on_select, on_toggle
        self.hover = 0.0
        self.w = 0
        self._outline = self.create_image(0, 0, anchor="nw")
        self._bg = self.create_image(0, 0, anchor="nw")
        self._title = self.create_text(T.S(16), T.S(18), anchor="w", font=T.font(10, "bold"), fill=T.TEXT)
        noun = "rule" if count == 1 else "rules"
        self.create_text(T.S(16), T.S(35), anchor="w", font=T.font(9), fill=T.MUTED, text=f"{count} {noun}")
        self.toggle = ToggleC(self, 0, (self.h - T.S(22)) // 2, enabled, self._toggled)
        self.bind("<Enter>", lambda _e: self._fade(1.0))
        self.bind("<Leave>", lambda _e: self._fade(0.0))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Configure>", self._resize)

    def _toggled(self, value: bool) -> None:
        self.on_toggle(self.name, value)

    def _click(self, e: tk.Event) -> None:
        over = self.find_overlapping(e.x, e.y, e.x, e.y)
        if any(self.toggle.tag in self.gettags(item) for item in over):
            return
        self.on_select(self.name)

    def _fade(self, target: float) -> None:
        start = self.hover

        def step(v: float) -> None:
            self.hover = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "h"), 0.14, step)

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self.toggle.move(self.w - self.toggle.w - T.S(14), self.toggle.y)
            self._draw()

    def _draw(self) -> None:
        if self.w < 20:
            return
        r = T.S(14)
        mask = gfx.rrect(self.w, self.h, r)
        if self.selected:
            b = max(1, T.S(1))
            self.itemconfigure(self._outline, image=gfx.image(mask, gfx.mix(T.ACCENT, T.SURFACE, 0.55)),
                               state="normal")
            self.itemconfigure(self._bg, image=gfx.image(gfx.rrect(self.w - 2 * b, self.h - 2 * b, r - b),
                                                         T.CARD_SELECTED))
            self.coords(self._bg, b, b)
        else:
            self.itemconfigure(self._outline, state="hidden")
            self.itemconfigure(self._bg, image=gfx.image(mask, T.CARD_HOVER, self.hover))
            self.coords(self._bg, 0, 0)
        width = self.w - T.S(16) - self.toggle.w - T.S(26)
        self.itemconfigure(self._title, text=T.elide(self.name, T.font(10, "bold"), width))


class GroupRow(Host):
    def __init__(self, parent: tk.Misc, name: str, count: int, collapsed: bool, indent: int,
                 on_click: Callable[[], None]) -> None:
        self.h = T.S(38)
        super().__init__(parent, T.S(300), self.h, cursor="hand2")
        x = indent + T.S(8)
        icon = "chevron_right" if collapsed else "chevron"
        self.create_image(x + T.S(8), self.h / 2, anchor="center", image=gfx.icon(icon, T.S(16), T.MUTED))
        title = self.create_text(x + T.S(24), self.h / 2, anchor="w", text=name, font=T.font(10, "bold"),
                                 fill=T.MUTED)
        bbox = self.bbox(title)
        self.create_text((bbox[2] if bbox else x + T.S(80)) + T.S(10), self.h / 2, anchor="w", text=str(count),
                         font=T.font(9), fill=T.FAINT)
        self.bind("<ButtonRelease-1>", lambda _e: on_click())


class RuleCard(Host):
    def __init__(self, parent: tk.Misc, rule: dict, on_toggle: Callable[[dict, bool], None],
                 on_edit: Callable[[dict], None], on_delete: Callable[[dict], None], indent: int = 0) -> None:
        self.h = T.S(74)
        super().__init__(parent, T.S(600), self.h)
        self.rule = rule
        self.indent = indent
        self.hover = 0.0
        self.w = 0
        self.gap = T.S(4)
        self._bg = self.create_image(indent, self.gap, anchor="nw")
        self.toggle = ToggleC(self, indent + T.S(18), (self.h - T.S(22)) // 2, bool(rule.get("enabled", True)),
                              lambda v: on_toggle(rule, v))
        size = 34
        y = (self.h - T.S(size)) // 2
        self.edit_btn = IconButtonC(self, 0, y, "edit", lambda: on_edit(rule), size=size)
        self.del_btn = IconButtonC(self, 0, y, "close", lambda: on_delete(rule), size=size)
        self._items: list[int] = []
        self.bind("<Configure>", self._resize)
        self.bind("<Enter>", lambda _e: self._fade(1.0))
        self.bind("<Leave>", lambda _e: self._fade(0.0))
        self.on_edit = on_edit
        self.bind("<Double-Button-1>", self._double)

    def _double(self, e: tk.Event) -> None:
        tags = {self.toggle.tag, self.edit_btn.tag, self.del_btn.tag}
        for item in self.find_overlapping(e.x, e.y, e.x, e.y):
            if tags & set(self.gettags(item)):
                return
        self.on_edit(self.rule)

    def _fade(self, target: float) -> None:
        start = self.hover

        def step(v: float) -> None:
            self.hover = start + (target - start) * v
            self._draw_bg()

        anim.get().tween((self, "h"), 0.14, step)

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self._layout()

    def _draw_bg(self) -> None:
        if self.w < 60:
            return
        fill = gfx.mix(T.CARD, T.CARD_HOVER, self.hover)
        mask = gfx.rrect(self.w - self.indent, self.h - 2 * self.gap, T.S(16))
        self.itemconfigure(self._bg, image=gfx.image(mask, fill))

    def _chip(self, x: float, y: float, text: str, fill: str, fg: str, bold: bool = False) -> int:
        f = T.font(9, "bold" if bold else "normal")
        w, h = f.measure(text) + T.S(18), T.S(22)
        self._items.append(self.create_image(round(x), round(y), anchor="nw",
                                             image=gfx.image(gfx.rrect(w, h, h / 2), fill)))
        self._items.append(self.create_text(round(x + w / 2), round(y + h / 2), text=text, font=f, fill=fg))
        return w

    def _layout(self) -> None:
        if self.w < 60:
            return
        self._draw_bg()
        for item in self._items:
            self.delete(item)
        self._items.clear()
        enabled = bool(self.rule.get("enabled", True))
        mode = rule_mode(self.rule)

        right = self.w - T.S(14)
        self.del_btn.move(right - self.del_btn.d, self.del_btn.y)
        self.edit_btn.move(right - self.del_btn.d - T.S(4) - self.edit_btn.d, self.edit_btn.y)
        right = right - 2 * self.del_btn.d - T.S(20)

        color = MODE_COLORS[mode]
        badge = MODE_LABELS[mode]
        bw = T.font(9, "bold").measure(badge) + T.S(18)
        self._chip(right - bw, T.S(14), badge, gfx.mix(color, T.CARD, 0.84), color, bold=True)
        detail = describe(self.rule)
        if detail:
            df = T.font(9)
            self._items.append(self.create_text(right, T.S(52), text=T.elide(detail, df, T.S(180)), anchor="e",
                                                font=df, fill=T.MUTED))

        x = self.indent + T.S(74)
        nf = T.font(11, "bold")
        name = str(self.rule.get("name") or "Untitled")
        self._items.append(self.create_text(x, T.S(26), anchor="w", font=nf, fill=T.TEXT if enabled else T.FAINT,
                                            text=T.elide(name, nf, max(40, right - bw - x - T.S(16)))))
        targets = [str(t) for t in self.rule.get("replace_ids", []) or []]
        limit = right - T.S(200)
        cf = T.font(9)
        for i, t in enumerate(targets):
            w = cf.measure(t) + T.S(18)
            more = f"+{len(targets) - i}"
            reserve = cf.measure(more) + T.S(24) if i < len(targets) - 1 else 0
            if x + w + reserve > limit and i > 0:
                self._chip(x, T.S(40), more, T.CHIP, T.MUTED)
                break
            x += self._chip(x, T.S(40), t, T.CHIP, T.MUTED if enabled else T.FAINT) + T.S(6)
        for comp in (self.toggle, self.edit_btn, self.del_btn):
            self.tag_raise(comp.tag)


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
        self.collapsed: set[int] = set()

        root.title(APP_NAME)
        root.configure(bg=T.BG)
        root.geometry(f"{T.S(1140)}x{T.S(800)}")
        root.minsize(T.S(960), T.S(640))
        self._set_icons()
        install_wheel(root)

        shell = tk.Frame(root, bg=T.BG, padx=T.S(20), pady=T.S(16))
        shell.pack(fill="both", expand=True)
        self.shell = shell
        self.overlay = OverlayHost(root)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)
        self._build_header(shell)
        self._build_sidebar(shell)
        self._build_rules(shell)
        self._build_log(shell)

        self.refresh_configs()
        self.refresh_rules()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.ui_calls: queue.Queue[Callable[[], None]] = queue.Queue()

        self._pump_job: str | None = root.after(50, self._pump_log)
        log.emit("App", f"{APP_NAME} {__version__}")

    # -- layout -----------------------------------------------------------
    def _set_icons(self) -> None:
        self._logo = None
        try:
            self._logo_big = tk.PhotoImage(file=str(asset_path("logo.png")))
            self.root.iconphoto(True, self._logo_big)
            self._logo = tk.PhotoImage(file=str(asset_path("logo_48.png" if T.S(1) < 2 else "logo_96.png")))
        except tk.TclError:
            pass
        if sys.platform == "win32":
            try:
                self.root.iconbitmap(default=str(asset_path("logo.ico")))
            except tk.TclError:
                pass

    def _build_header(self, shell: tk.Frame) -> None:
        head = tk.Frame(shell, bg=T.BG)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, T.S(16)))
        if self._logo is not None:
            tk.Label(head, image=self._logo, bg=T.BG).pack(side="left", padx=(0, T.S(12)))
        label(head, APP_NAME, 18, "bold").pack(side="left")
        IconButton(head, "settings", self.open_settings, kind="ghost", size=38, icon_size=18).pack(side="right")
        self.status = StatusPill(head)
        self.status.pack(side="right", padx=T.S(12))

    def _build_sidebar(self, shell: tk.Frame) -> None:
        side = tk.Frame(shell, bg=T.BG, width=T.S(300))
        side.grid(row=1, column=0, sticky="nsw", padx=(0, T.S(16)))
        side.grid_propagate(False)
        side.pack_propagate(False)

        proxy = Card(side, fill=T.SURFACE, radius=22, pad=16)
        proxy.pack(fill="x")
        self.power = PowerButton(proxy.body, self.toggle_proxy, size=132)
        self.power.pack()
        self.address = label(proxy.body, self.ctl.proxy_url.removeprefix("http://"), 9, color=T.FAINT)
        self.address.pack(pady=(0, T.S(14)))
        Button(proxy.body, "Launch Roblox", self.launch, kind="primary", icon="play", width=252, height=40).pack()
        Button(proxy.body, "Clear cache", self.clear_cache, kind="ghost", width=252, height=38).pack(pady=(T.S(8), 0))

        configs = Card(side, fill=T.SURFACE, radius=22, pad=14)
        configs.pack(fill="both", expand=True, pady=(T.S(16), 0))
        top = tk.Frame(configs.body, bg=T.SURFACE)
        top.pack(fill="x", pady=(0, T.S(10)))
        label(top, "Configs", 12, "bold").pack(side="left", padx=(T.S(4), 0))
        IconButton(top, "plus", self.new_config, kind="primary", size=30, icon_size=16).pack(side="right")
        IconButton(top, "import", self.import_config, kind="ghost", size=30, icon_size=16).pack(
            side="right", padx=T.S(6))
        self.config_list = ScrollArea(configs.body, T.SURFACE)
        self.config_list.pack(fill="both", expand=True)

    def _build_rules(self, shell: tk.Frame) -> None:
        panel = Card(shell, fill=T.SURFACE, radius=22, pad=20)
        panel.grid(row=1, column=1, sticky="nsew")
        head = tk.Frame(panel.body, bg=T.SURFACE)
        head.pack(fill="x")
        titles = tk.Frame(head, bg=T.SURFACE)
        titles.pack(side="left", fill="x", expand=True)
        self.config_title = label(titles, "", 17, "bold", anchor="w")
        self.config_title.pack(anchor="w", fill="x")
        self.config_sub = label(titles, "", 9, color=T.MUTED, anchor="w")
        self.config_sub.pack(anchor="w")
        Button(head, "Delete", self.delete_config, kind="danger", height=34, size=9).pack(side="right")
        Button(head, "Export", self.export_config, kind="ghost", height=34, size=9).pack(side="right", padx=T.S(6))
        Button(head, "Rename", self.rename_config, kind="ghost", height=34, size=9).pack(side="right")
        Button(head, "Add rule", self.add_rule, kind="primary", icon="plus", height=38).pack(
            side="right", padx=T.S(12))

        self.search = Field(panel.body, "", "Search", width=500, height=40, icon="search")
        self.search.pack(fill="x", pady=(T.S(16), T.S(10)))
        self.search.var.trace_add("write", lambda *_: self._search_changed())
        self.rule_list = ScrollArea(panel.body, T.SURFACE)
        self.rule_list.pack(fill="both", expand=True)

    def _build_log(self, shell: tk.Frame) -> None:
        card = Card(shell, fill=T.SURFACE, radius=18, pad=14)
        card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(T.S(16), 0))
        top = tk.Frame(card.body, bg=T.SURFACE)
        top.pack(fill="x")
        label(top, "Log", 10, "bold").pack(side="left", padx=(T.S(4), 0))
        self.log_toggle = Button(top, "Hide", self._toggle_log, kind="ghost", height=26, size=8, width=64)
        self.log_toggle.pack(side="right")
        self.log_wrap = tk.Frame(card.body, bg=T.SURFACE)
        self.log_wrap.pack(fill="x", pady=(T.S(8), 0))
        self.log_text = tk.Text(self.log_wrap, height=4, bg=T.SURFACE, fg=T.MUTED, bd=0, highlightthickness=0,
                                font=T.font(9, mono=True), wrap="word", state="disabled", padx=T.S(4),
                                selectbackground=T.CARD_HOVER, cursor="arrow")
        self.log_text.pack(fill="x")
        for tag, color in (("Replace", T.ACCENT), ("Remove", T.DANGER), ("Error", T.DANGER), ("File", T.BLUE),
                           ("Proxy", T.TEXT), ("Watcher", T.WARN), ("time", T.FAINT)):
            self.log_text.tag_configure(tag, foreground=color)

    def _toggle_log(self) -> None:
        if self.log_wrap.winfo_ismapped():
            self.log_wrap.pack_forget()
            self.log_toggle.set_text("Show")
        else:
            self.log_wrap.pack(fill="x", pady=(T.S(8), 0))
            self.log_toggle.set_text("Hide")

    # -- pop-ups -----------------------------------------------------------
    @property
    def pages(self) -> list[Popup]:
        return self.overlay.popups

    def push_page(self, page: Popup) -> None:
        self.overlay.open(page)

    def pop_page(self) -> None:
        self.overlay.close_top()

    # -- background work -------------------------------------------------
    def _bg(self, fn: Callable[[], object], done: Callable[[], None] | None = None) -> None:
        """Run ``fn`` on a worker thread; ``done`` then runs on the UI thread."""
        def work() -> None:
            try:
                fn()
            except Exception as exc:
                log.emit("Error", str(exc))
            if done:
                self.ui_calls.put(done)  # never touch Tk from a worker thread
        threading.Thread(target=work, daemon=True).start()

    def _pump_log(self) -> None:
        self._pump_job = None
        while not self.ui_calls.empty():
            self.ui_calls.get_nowait()()
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
        self._pump_job = self.root.after(50, self._pump_log)

    # -- proxy -------------------------------------------------------------
    def _busy(self, text: str) -> None:
        self.power.set_state(self.ctl.running, busy=True)
        self.status.set(text, T.WARN, pulse=True)

    def _update_status(self) -> None:
        running = self.ctl.running
        self.power.set_state(running)
        self.address.configure(text=self.ctl.proxy_url.removeprefix("http://"))
        if running:
            self.status.set("Running", T.ACCENT, pulse=True)
        else:
            self.status.set("Stopped", T.MUTED)

    def status_text(self) -> str:
        return str(self.status.itemcget(self.status._tx, "text"))

    def toggle_proxy(self) -> None:
        if self.ctl.running:
            self._busy("Stopping")
            self._bg(self.ctl.stop, self._update_status)
        else:
            self._busy("Starting")
            self._bg(self.ctl.start, self._update_status)

    def launch(self) -> None:
        self._busy("Launching")
        self._bg(self.ctl.launch_roblox, self._update_status)

    def clear_cache(self) -> None:
        self._bg(roblox.clear_cache)

    def open_settings(self) -> None:
        page = SettingsPage(self)
        page.bind("<Destroy>", lambda e: e.widget is page and self.address.configure(
            text=self.ctl.proxy_url.removeprefix("http://")), add="+")
        self.push_page(page)

    # -- configs -----------------------------------------------------------
    def refresh_configs(self) -> None:
        y = self.config_list.pos
        for child in self.config_list.inner.winfo_children():
            child.destroy()
        for name in self.store.names():
            data = self.data if name == self.selected else self.store.load_config(name)
            total, _on = count_rules(data["replacement_rules"])
            ConfigRow(self.config_list.inner, name, total, self.store.is_enabled(name), name == self.selected,
                      self.select_config, self.toggle_config).pack(fill="x", pady=T.S(2))
        self.root.update_idletasks()
        self.config_list.to(y)
        self._update_header()

    def _update_header(self) -> None:
        total, on = count_rules(self.data["replacement_rules"])
        self.config_title.configure(text=self.selected)
        noun = "rule" if total == 1 else "rules"
        self.config_sub.configure(text=f"{total} {noun}, {on} enabled")

    def select_config(self, name: str) -> None:
        if name == self.selected:
            return
        self.selected = name
        self.data = self.store.load_config(name)
        self.show_all = False
        self.collapsed.clear()
        self.search.var.set("")
        self.refresh_configs()
        self.refresh_rules()
        self.rule_list.to(0)

    def toggle_config(self, name: str, value: bool) -> None:
        self.store.set_enabled(name, value)
        log.emit("Configs", f"{name} {'enabled' if value else 'disabled'}")

    def new_config(self) -> None:
        def create(text: str) -> None:
            try:
                name = self.store.create(text)
            except ValueError as exc:
                log.emit("Error", str(exc))
                return
            self.store.set_enabled(name, True)
            self.selected = ""
            self.select_config(name)

        self.push_page(PromptPage(self, "New config", create, ok="Create"))

    def import_config(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, title="Import configs",
                                            filetypes=[("JSON", "*.json"), ("All files", "*.*")])
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
        old = self.selected

        def rename(text: str) -> None:
            try:
                self.selected = self.store.rename(old, text)
            except (OSError, ValueError) as exc:
                log.emit("Error", str(exc))
            self.refresh_configs()

        self.push_page(PromptPage(self, "Rename config", rename, old))

    def export_config(self) -> None:
        dest = filedialog.asksaveasfilename(parent=self.root, title="Export config", defaultextension=".json",
                                            initialfile=f"{self.selected}.json", filetypes=[("JSON", "*.json")])
        if dest:
            try:
                self.store.export(self.selected, Path(dest))
                log.emit("Configs", f"exported {self.selected}")
            except OSError as exc:
                log.emit("Error", str(exc))

    def delete_config(self) -> None:
        name = self.selected

        def delete() -> None:
            self.store.delete(name)
            if not self.store.names():
                self.store.create("Default")
                self.store.set_enabled("Default", True)
            self.selected = ""
            self.select_config(self.store.names()[0])

        self.push_page(ConfirmPage(self, "Delete config", f'"{name}" will be permanently deleted.', delete))

    # -- rules ---------------------------------------------------------------
    def _save(self) -> None:
        try:
            self.store.save_config(self.selected, self.data)
        except OSError as exc:
            log.emit("Error", f"could not save: {exc}")
        self._update_header()

    def _search_changed(self) -> None:
        job = getattr(self, "_search_job", None)
        if job:
            self.root.after_cancel(job)
        self._search_job = self.root.after(160, self._search_now)

    def _search_now(self) -> None:
        self._search_job = None
        self.refresh_rules()
        self.rule_list.to(0)

    def _visible_items(self) -> list[tuple[int, str, dict, list]]:
        query = self.search.get().strip().lower()
        items: list[tuple[int, str, dict, list]] = []

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
                    before = len(items)
                    items.append((depth, "group", entry, entries))
                    if id(entry) not in self.collapsed or query:
                        walk(entry.get("children") or [], depth + 1)
                    if query and len(items) == before + 1:
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
            indent = T.S(24) * depth
            if kind == "group":
                total, _on = count_rules(entry.get("children") or [])
                GroupRow(inner, str(entry.get("name") or "Group"), total, id(entry) in self.collapsed, indent,
                         lambda e=entry: self._toggle_group(e)).pack(fill="x", pady=(T.S(6), 0))
                continue
            RuleCard(inner, entry, self._rule_toggled, self.edit_rule, self.delete_rule, indent=indent).pack(fill="x")
        if len(items) > len(shown):
            Button(inner, f"Show all {len(items)}", self._show_all, kind="ghost").pack(pady=T.S(10))

    def _keep_scroll(self, fn: Callable[[], None]) -> None:
        y = self.rule_list.pos
        fn()
        self.root.update_idletasks()
        self.rule_list.to(y)

    def _toggle_group(self, group: dict) -> None:
        if id(group) in self.collapsed:
            self.collapsed.discard(id(group))
        else:
            self.collapsed.add(id(group))
        self._keep_scroll(self.refresh_rules)

    def _show_all(self) -> None:
        self.show_all = True
        self._keep_scroll(self.refresh_rules)

    def _empty_state(self, inner: tk.Frame) -> None:
        box = tk.Frame(inner, bg=T.SURFACE)
        box.pack(fill="x", pady=T.S(90))
        searching = bool(self.search.get().strip())
        label(box, "No results" if searching else "No rules", 12, "bold", T.MUTED).pack()
        if not searching:
            Button(box, "Add rule", self.add_rule, kind="primary", icon="plus").pack(pady=T.S(16))

    def _rule_toggled(self, rule: dict, value: bool) -> None:
        rule["enabled"] = value
        self._save()
        self._relayout_job = self.root.after(210, self._relayout_cards)  # after the switch animation

    def _relayout_cards(self) -> None:
        self._relayout_job = None
        for child in self.rule_list.inner.winfo_children():
            if isinstance(child, RuleCard):
                child._layout()

    def add_rule(self) -> None:
        def save(rule: dict) -> None:
            self.data["replacement_rules"].append(rule)
            self._save()
            self.search.var.set("")
            self.refresh_rules()
            self.refresh_configs()
            self.root.update_idletasks()
            self.rule_list.to(self.rule_list._max())
            log.emit("Rules", f"added {rule['name']}")

        self.push_page(RulePage(self, None, save))

    def edit_rule(self, rule: dict) -> None:
        def save(_rule: dict) -> None:
            self._save()
            self._keep_scroll(self.refresh_rules)

        self.push_page(RulePage(self, rule, save))

    def delete_rule(self, rule: dict) -> None:
        for _d, _k, entry, parent in self._visible_items():
            if entry is rule:
                parent.remove(rule)
                break
        else:
            return
        self._save()
        self._keep_scroll(self.refresh_rules)
        self.refresh_configs()
        log.emit("Rules", f"deleted {rule.get('name', 'rule')}")

    def on_close(self) -> None:
        for job in (self._pump_job, getattr(self, "_search_job", None), getattr(self, "_relayout_job", None)):
            if job:
                self.root.after_cancel(job)
        self.ctl.shutdown()
        anim.get().stop_all()
        self.overlay.close_all()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp rendering on high-DPI screens
        except (AttributeError, OSError):
            pass


def create_root() -> tk.Tk:
    _enable_dpi_awareness()
    fonts.register(asset_path("fonts"))
    root = tk.Tk()
    root.withdraw()
    gfx.reset()
    T.setup(root)
    anim.setup(root)
    return root


def run() -> int:
    root = create_root()
    MainWindow(root)
    root.deiconify()
    root.mainloop()
    return 0
