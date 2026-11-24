"""Pop-up windows over a dimmed backdrop, with eased open/close animations.

Tk widgets can't be see-through, so the dimming is a borderless black window
whose overall opacity is animated. The pop-up itself is another borderless
window whose opacity and vertical offset are animated. Its rounded corners
are cut out with a transparent key colour (Windows) or a transparent window
(macOS). Height changes (e.g. adding a field) are animated too.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable

from . import anim, gfx
from . import theme as T
from .widgets import IconButton, ScrollArea

KEY = "#010203"            # transparent key colour for the pop-up corners (Windows)
DIM = 0.55                 # backdrop opacity
OPEN_TIME = 0.30
CLOSE_TIME = 0.18
RESIZE_TIME = 0.22
RISE = 22                  # pixels the pop-up glides up while opening


def ease_out_quint(t: float) -> float:
    return 1 - (1 - t) ** 5


def ease_in_cubic(t: float) -> float:
    return t * t * t


def _borderless(win: tk.Toplevel) -> None:
    if sys.platform == "darwin":
        try:  # borderless but still able to take keyboard focus
            win.tk.call("::tk::unsupported::MacWindowStyle", "style", win._w, "plain", "none")
            return
        except tk.TclError:
            pass
    win.overrideredirect(True)


def _set_alpha(win: tk.Toplevel, value: float) -> None:
    try:
        win.attributes("-alpha", max(0.0, min(1.0, value)))
    except tk.TclError:
        pass


class Backdrop(tk.Toplevel):
    def __init__(self, root: tk.Tk, on_click: Callable[[], None]) -> None:
        super().__init__(root, bg="#000000")
        self.withdraw()
        _borderless(self)
        self.transient(root)
        self.root = root
        self.level = 0.0
        _set_alpha(self, 0.0)
        self.bind("<ButtonRelease-1>", lambda _e: on_click())

    def cover(self) -> None:
        r = self.root
        self.geometry(f"{r.winfo_width()}x{r.winfo_height()}+{r.winfo_rootx()}+{r.winfo_rooty()}")

    def fade(self, target: float, duration: float, done: Callable[[], None] | None = None) -> None:
        start = self.level

        def step(v: float) -> None:
            self.level = start + (target - start) * v
            _set_alpha(self, self.level)

        anim.get().tween((self, "fade"), duration, step, ease=anim.ease_out if target > start else ease_in_cubic,
                         done=done)


class Popup(tk.Toplevel):
    """A rounded, animated pop-up. Put content in ``self.body``."""

    def __init__(self, host: "OverlayHost", title: str, width: int = 540) -> None:
        root = host.root
        super().__init__(root, bg=KEY)
        self.withdraw()
        _borderless(self)
        self.transient(root)
        self.host = host
        if sys.platform == "win32":
            try:
                self.attributes("-transparentcolor", KEY)
            except tk.TclError:
                pass
        elif sys.platform == "darwin":
            try:
                self.attributes("-transparent", True)
                self.configure(bg="systemTransparent")
            except tk.TclError:
                pass
        self.W = T.S(width)
        self.h = T.S(200)
        self.pad = T.S(24)
        self.r = T.S(22)
        self.offset = float(RISE)
        self.opacity = 0.0
        self.closing = False
        self.first_focus: tk.Widget | None = None
        self._fit_job: str | None = None

        corner_bg = KEY if sys.platform in ("win32", "darwin") else gfx.mix(T.BG, "#000000", 0.6)
        self.canvas = tk.Canvas(self, bg=corner_bg, highlightthickness=0, bd=0, width=self.W, height=self.h)
        if sys.platform == "darwin" and self.cget("bg") == "systemTransparent":
            self.canvas.configure(bg="systemTransparent")
        self.canvas.pack(fill="both", expand=True)
        self._shape: list[int] = []
        self._build_shape()

        self.content = tk.Frame(self.canvas, bg=T.SURFACE)
        self._win = self.canvas.create_window(self.pad, self.pad, window=self.content, anchor="nw",
                                              width=self.W - 2 * self.pad)
        head = tk.Frame(self.content, bg=T.SURFACE)
        head.pack(fill="x", pady=(0, T.S(14)))
        tk.Label(head, text=title, bg=T.SURFACE, fg=T.TEXT, font=T.font(15, "bold")).pack(side="left")
        IconButton(head, "close", self.close, kind="subtle", size=32, icon_size=15).pack(side="right")
        self.head = head
        self.footer = tk.Frame(self.content, bg=T.SURFACE)  # stays visible; only the body scrolls
        self.footer.pack(side="bottom", fill="x")
        self.footer.bind("<Configure>", lambda _e: self._schedule_fit(), add="+")
        self.scroll = ScrollArea(self.content, T.SURFACE)
        self.scroll.pack(fill="both", expand=True)
        self.body = tk.Frame(self.scroll.inner, bg=T.SURFACE)
        self.body.pack(fill="x")
        self.scroll.inner.bind("<Configure>", lambda _e: self._schedule_fit(), add="+")
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<Return>", lambda _e: self.primary())

    # -- shape: a rounded card built from corner discs + rectangles (cheap to resize every frame)
    def _build_shape(self) -> None:
        c = self.canvas
        r, b = self.r, max(1, T.S(1))
        outer = gfx.image(gfx.circle(2 * r), T.LINE)
        inner = gfx.image(gfx.circle(2 * (r - b)), T.SURFACE)
        self._outer_disc = [c.create_image(0, 0, anchor="nw", image=outer) for _ in range(4)]
        self._outer_rect = [c.create_rectangle(0, 0, 0, 0, fill=T.LINE, width=0) for _ in range(2)]
        self._inner_disc = [c.create_image(0, 0, anchor="nw", image=inner) for _ in range(4)]
        self._inner_rect = [c.create_rectangle(0, 0, 0, 0, fill=T.SURFACE, width=0) for _ in range(2)]

    def _layout_shape(self) -> None:
        c, W, h, r = self.canvas, self.W, int(self.h), self.r
        b = max(1, T.S(1))

        def place(discs: list[int], rects: list[int], x0: int, y0: int, x1: int, y1: int, rad: int) -> None:
            d = 2 * rad
            for item, (x, y) in zip(discs, ((x0, y0), (x1 - d, y0), (x0, y1 - d), (x1 - d, y1 - d))):
                c.coords(item, x, y)
            c.coords(rects[0], x0 + rad, y0, x1 - rad, y1)
            c.coords(rects[1], x0, y0 + rad, x1, y1 - rad)

        place(self._outer_disc, self._outer_rect, 0, 0, W, h, r)
        place(self._inner_disc, self._inner_rect, b, b, W - b, h - b, r - b)
        c.itemconfigure(self._win, height=max(10, h - 2 * self.pad))

    # -- sizing ------------------------------------------------------------
    def wanted_height(self) -> int:
        content = (self.head.winfo_reqheight() + T.S(14) + self.scroll.inner.winfo_reqheight()
                   + self.footer.winfo_reqheight())
        limit = int(self.host.root.winfo_height() * 0.88)
        return max(T.S(120), min(limit, content + 2 * self.pad))

    def _schedule_fit(self) -> None:
        if self._fit_job is None and not self.closing:
            self._fit_job = self.after_idle(self._fit)

    def _fit(self) -> None:
        self._fit_job = None
        if not self.winfo_viewable():
            return
        target = self.wanted_height()
        if abs(target - self.h) < 1:
            return
        start = self.h

        def step(v: float) -> None:
            self.h = start + (target - start) * v
            self.place_window()

        anim.get().tween((self, "size"), RESIZE_TIME, step, ease=anim.ease_out)

    def place_window(self) -> None:
        root = self.host.root
        x = root.winfo_rootx() + (root.winfo_width() - self.W) // 2
        y = root.winfo_rooty() + int((root.winfo_height() - self.h) / 2 + self.offset)
        self.geometry(f"{self.W}x{int(self.h)}+{x}+{y}")
        self._layout_shape()

    # -- open / close ----------------------------------------------------------
    def open(self) -> None:
        self.update_idletasks()
        self.h = self.wanted_height()
        _set_alpha(self, 0.0)
        self.place_window()
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass
        if self.first_focus is not None:
            self.first_focus.focus_set()

        def step(v: float) -> None:
            self.opacity = v
            self.offset = RISE * (1 - v)
            _set_alpha(self, v)
            self.place_window()

        anim.get().tween((self, "show"), OPEN_TIME, step, ease=ease_out_quint)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        start_alpha = self.opacity

        def step(v: float) -> None:
            self.opacity = start_alpha * (1 - v)
            self.offset = RISE * 0.5 * v
            _set_alpha(self, self.opacity)
            self.place_window()

        self.host.popup_closing(self)
        anim.get().tween((self, "show"), CLOSE_TIME, step, ease=ease_in_cubic, done=self._finish)

    def _finish(self) -> None:
        self.host.popup_closed(self)
        self.destroy()

    def primary(self) -> None:
        """Return-key action; subclasses override."""


class OverlayHost:
    """Owns the backdrop and the stack of open pop-ups for a root window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.popups: list[Popup] = []
        self.backdrop: Backdrop | None = None
        root.bind("<Configure>", self._follow, add="+")
        root.bind("<Unmap>", self._hide, add="+")
        root.bind("<Map>", self._show, add="+")

    def open(self, popup: Popup) -> None:
        if self.backdrop is None or not self.backdrop.winfo_exists():
            self.backdrop = Backdrop(self.root, self.close_top)
        bd = self.backdrop
        if not self.popups:
            bd.cover()
            bd.deiconify()
            bd.lift()
            bd.fade(DIM, OPEN_TIME * 0.8)
        self.popups.append(popup)
        popup.open()

    def close_top(self) -> None:
        if self.popups:
            self.popups[-1].close()

    def popup_closing(self, popup: Popup) -> None:
        open_ones = [p for p in self.popups if not p.closing]
        if not open_ones and self.backdrop is not None:
            bd = self.backdrop
            bd.fade(0.0, CLOSE_TIME + 0.04,
                    done=lambda: not [p for p in self.popups if not p.closing] and bd.withdraw())

    def popup_closed(self, popup: Popup) -> None:
        if popup in self.popups:
            self.popups.remove(popup)
        if self.popups:
            top = self.popups[-1]
            try:
                top.focus_force()
            except tk.TclError:
                pass
        else:
            self.root.focus_force()

    def _follow(self, e: tk.Event) -> None:
        if e.widget is not self.root:
            return
        if self.backdrop is not None and self.popups:
            self.backdrop.cover()
        for p in self.popups:
            p.place_window()

    def _hide(self, e: tk.Event) -> None:
        if e.widget is self.root and self.backdrop is not None:
            self.backdrop.withdraw()
            for p in self.popups:
                p.withdraw()

    def _show(self, e: tk.Event) -> None:
        if e.widget is self.root and self.popups and self.backdrop is not None:
            self.backdrop.cover()
            self.backdrop.deiconify()
            for p in self.popups:
                p.deiconify()
                p.place_window()

    def close_all(self) -> None:
        for p in list(self.popups):
            p.destroy()
        self.popups.clear()
        if self.backdrop is not None:
            self.backdrop.destroy()
            self.backdrop = None
