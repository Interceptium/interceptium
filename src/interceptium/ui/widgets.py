"""Rounded, animated widgets."""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable

from . import theme as T
from .shapes import circle, rounded


def bg_of(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("bg"))
    except tk.TclError:
        return T.BG


def _resolve(widget: tk.Misc, color: str) -> str:
    """Turn any Tk colour name into #rrggbb."""
    if color.startswith("#") and len(color) == 7:
        return color
    r, g, b = widget.winfo_rgb(color)
    return "#%02x%02x%02x" % (r >> 8, g >> 8, b >> 8)


class RoundButton(tk.Canvas):
    STYLES = {
        "primary": (T.ACCENT, T.ACCENT_HOVER, T.ACCENT_PRESS, T.ACCENT_TEXT),
        "ghost": (T.CHIP, "#2c3447", "#1d2333", T.TEXT),
        "danger": ("#3a1f2a", "#4a2533", "#2f1922", T.DANGER),
        "subtle": (T.CARD, T.CARD_HOVER, T.SURFACE, T.MUTED),
    }

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None] | None = None,
                 kind: str = "ghost", width: int | None = None, height: int = 36, size: int = 10,
                 radius: float | None = None, bold: bool = True) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        self._font = T.font(size, "bold" if bold else "normal")
        h = T.S(height)
        w = T.S(width) if width else self._font.measure(text) + T.S(36)
        super().__init__(parent, width=w, height=h, bg=self._bg, highlightthickness=0, bd=0, cursor="hand2")
        self._pw, self._ph = w, h
        self._r = h / 2 if radius is None else T.S(radius)
        self._text = text
        self.command = command
        self._kind = kind
        self._enabled = True
        self._state = "normal"
        self._img = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(w / 2, h / 2, text=text, font=self._font)
        self._paint()
        self.bind("<Enter>", lambda _e: self._set("hover"))
        self.bind("<Leave>", lambda _e: self._set("normal"))
        self.bind("<ButtonPress-1>", lambda _e: self._set("press"))
        self.bind("<ButtonRelease-1>", self._release)

    def _set(self, state: str) -> None:
        self._state = state
        self._paint()

    def _paint(self) -> None:
        base, hover, press, fg = self.STYLES[self._kind]
        fill = {"normal": base, "hover": hover, "press": press}[self._state]
        if not self._enabled:
            fill, fg = T.mix(base, self._bg, 0.55), T.FAINT
        self._photo = rounded(self._pw, self._ph, self._r, fill, self._bg)
        self.itemconfigure(self._img, image=self._photo)
        self.itemconfigure(self._label, fill=fg, text=self._text)

    def _release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self._pw and 0 <= event.y <= self._ph
        self._set("hover" if inside else "normal")
        if inside and self._enabled and self.command:
            self.command()

    def configure_text(self, text: str, kind: str | None = None) -> None:
        self._text = text
        if kind:
            self._kind = kind
        self._paint()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()


class IconButton(RoundButton):
    """A small circular button with a single glyph."""

    def __init__(self, parent: tk.Misc, glyph: str, command: Callable[[], None] | None = None,
                 kind: str = "subtle", size: int = 32, font_size: int = 11) -> None:
        super().__init__(parent, glyph, command, kind=kind, width=size, height=size, size=font_size, bold=False)


class Toggle(tk.Canvas):
    """iOS-style switch with a sliding circular knob."""

    def __init__(self, parent: tk.Misc, value: bool = False, command: Callable[[bool], None] | None = None,
                 width: int = 44, height: int = 24) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        self._pw, self._ph = T.S(width), T.S(height)
        super().__init__(parent, width=self._pw, height=self._ph, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.value = value
        self.command = command
        self._pos = 1.0 if value else 0.0
        self._anim: str | None = None
        self._track = self.create_image(0, 0, anchor="nw")
        self._knob = self.create_image(0, 0, anchor="nw")
        self._paint()
        self.bind("<ButtonRelease-1>", lambda _e: self.toggle())

    def _paint(self) -> None:
        color = T.mix(T.TRACK_OFF, T.ACCENT, self._pos)
        self._t_img = rounded(self._pw, self._ph, self._ph / 2, color, self._bg)
        pad = T.S(3)
        d = self._ph - 2 * pad
        self._k_img = circle(d, "#ffffff", color)
        x = pad + (self._pw - 2 * pad - d) * self._pos
        self.itemconfigure(self._track, image=self._t_img)
        self.itemconfigure(self._knob, image=self._k_img)
        self.coords(self._knob, round(x), pad)

    def set(self, value: bool, animate: bool = True) -> None:
        self.value = value
        if self._anim:
            self.after_cancel(self._anim)
            self._anim = None
        if not animate:
            self._pos = 1.0 if value else 0.0
            self._paint()
            return
        self._step(1.0 if value else 0.0)

    def _step(self, target: float) -> None:
        delta = target - self._pos
        if abs(delta) < 0.04:
            self._pos = target
            self._paint()
            self._anim = None
            return
        self._pos += delta * 0.38  # ease-out
        self._paint()
        self._anim = self.after(14, self._step, target)

    def toggle(self) -> None:
        self.set(not self.value)
        if self.command:
            self.command(self.value)


class PowerButton(tk.Canvas):
    """Big round start/stop button with a soft pulsing ring while running."""

    def __init__(self, parent: tk.Misc, command: Callable[[], None], size: int = 128) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        self._d = T.S(size)
        super().__init__(parent, width=self._d, height=self._d, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.command = command
        self.running = False
        self.busy = False
        self._hover = False
        self._phase = 0.0
        self._ring = self.create_image(0, 0, anchor="nw")
        self._icon = self.create_text(self._d / 2, self._d / 2 - T.S(9), text="⏻", font=T.font(26))
        self._label = self.create_text(self._d / 2, self._d / 2 + T.S(22), text="START", font=T.font(9, "bold"))
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", lambda _e: (not self.busy) and self.command())
        self._paint()
        self._tick()

    def _set_hover(self, value: bool) -> None:
        self._hover = value
        self._paint()

    def set_state(self, running: bool, busy: bool = False) -> None:
        self.running, self.busy = running, busy
        self._paint()

    def _paint(self) -> None:
        import math
        d = self._d
        if self.busy:
            ring_color, core, fg, label = T.mix(T.WARN, self._bg, 0.7), T.CARD_HOVER, T.WARN, "WAIT"
        elif self.running:
            glow = round((math.sin(self._phase) + 1) / 2 * 12) / 12  # few steps -> cached images
            ring_color = T.mix(T.mix(T.ACCENT, self._bg, 0.75), T.mix(T.ACCENT, self._bg, 0.45), glow)
            core = T.ACCENT_HOVER if self._hover else T.ACCENT
            fg, label = T.ACCENT_TEXT, "RUNNING"
        else:
            ring_color = T.mix(T.LINE, self._bg, 0.3)
            core = T.CARD_HOVER if self._hover else T.CARD
            fg, label = T.TEXT, "START"
        # one image: a ring of ``ring_color`` around a ``core`` disc
        self._r_img = circle(d, core, self._bg, ring_color, T.S(10))
        self.itemconfigure(self._ring, image=self._r_img)
        self.itemconfigure(self._icon, fill=fg)
        self.itemconfigure(self._label, fill=fg, text=label)

    def _tick(self) -> None:
        if self.running and not self.busy:
            self._phase += 0.12
            self._paint()
        self.after(50, self._tick)


class Card(tk.Frame):
    """Rounded panel. Put children in ``card.body``.

    The rounded background is an image placed behind the body, so normal
    pack/grid geometry decides the size (the card grows with its content, or
    stretches when packed/gridded to expand).
    """

    def __init__(self, parent: tk.Misc, fill: str = T.CARD, radius: int = 18, pad: int = 16,
                 fill_height: bool = False, outline: str | None = None) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        super().__init__(parent, bg=self._bg)
        self.fill = fill
        self.outline = outline
        self._r = T.S(radius)
        self._back = tk.Label(self, bg=self._bg, bd=0, highlightthickness=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=T.S(pad), pady=T.S(pad))
        self._size = (0, 0)
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, e: tk.Event) -> None:
        if (e.width, e.height) != self._size and e.width > 4 and e.height > 4:
            self._size = (e.width, e.height)
            self._draw()

    def _draw(self) -> None:
        w, h = self._size
        if w < 4 or h < 4:
            return
        self._photo = rounded(w, h, self._r, self.fill, self._bg, self.outline, T.S(1) if self.outline else 0)
        self._back.configure(image=self._photo)

    def set_fill(self, fill: str, outline: str | None = None) -> None:
        self.fill, self.outline = fill, outline
        self.body.configure(bg=fill)
        self._draw()


class ScrollArea(tk.Frame):
    """Vertically scrolling frame with a slim rounded scrollbar."""

    def __init__(self, parent: tk.Misc, bg: str) -> None:
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.bar = tk.Canvas(self, width=T.S(8), bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self._thumb = self.bar.create_image(0, 0, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y", padx=(T.S(4), 0))
        self.inner.bind("<Configure>", lambda _e: self._update())
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.configure(yscrollcommand=lambda *_: self._draw_bar())
        self._drag: float | None = None
        self.bar.bind("<ButtonPress-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        self.bind_all_wheel(self.canvas)
        self.bind_all_wheel(self.inner)

    def bind_all_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<Enter>", lambda _e: self._wheel(True), add="+")
        widget.bind("<Leave>", lambda _e: self._wheel(False), add="+")

    def _wheel(self, on: bool) -> None:
        if on:
            if sys.platform.startswith("linux"):
                self.bind_all("<Button-4>", lambda _e: self._scroll(-3))
                self.bind_all("<Button-5>", lambda _e: self._scroll(3))
            else:
                self.bind_all("<MouseWheel>", self._on_wheel)
        else:
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.unbind_all(seq)

    def _on_wheel(self, e: tk.Event) -> None:
        step = -e.delta // 120 * 3 if sys.platform == "win32" else -e.delta
        self._scroll(step or (-1 if e.delta > 0 else 1))

    def _scroll(self, units: int) -> None:
        if self.inner.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(units, "units")

    def _resize(self, e: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)
        self._update()

    def _update(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, max(self.inner.winfo_reqheight(), 1)),
                              yscrollincrement=T.S(12))
        self._draw_bar()

    def _draw_bar(self) -> None:
        top, bottom = self.canvas.yview()
        h = self.bar.winfo_height()
        if bottom - top >= 0.999 or h < 10:
            self.bar.itemconfigure(self._thumb, state="hidden")
            return
        th = max(T.S(28), int((bottom - top) * h))
        y = int(top * h)
        self._thumb_img = rounded(T.S(6), th, T.S(3), T.LINE, self._bg)
        self.bar.itemconfigure(self._thumb, image=self._thumb_img, state="normal")
        self.bar.coords(self._thumb, T.S(1), min(y, h - th))

    def _bar_press(self, e: tk.Event) -> None:
        self._drag = e.y

    def _bar_drag(self, e: tk.Event) -> None:
        if self._drag is None:
            return
        h = max(1, self.bar.winfo_height())
        top, _ = self.canvas.yview()
        self.canvas.yview_moveto(top + (e.y - self._drag) / h)
        self._drag = e.y

    def to_top(self) -> None:
        self.canvas.yview_moveto(0)


class RoundEntry(tk.Canvas):
    """Text field inside a rounded box that lights up when focused."""

    def __init__(self, parent: tk.Misc, value: str = "", placeholder: str = "", width: int = 320,
                 height: int = 40) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        self._pw, self._ph = T.S(width), T.S(height)
        super().__init__(parent, width=self._pw, height=self._ph, bg=self._bg, highlightthickness=0, bd=0)
        self._img = self.create_image(0, 0, anchor="nw")
        self.var = tk.StringVar(value=value)
        self.placeholder = placeholder
        self.entry = tk.Entry(self, textvariable=self.var, bd=0, relief="flat", bg=T.FIELD, fg=T.TEXT,
                              insertbackground=T.ACCENT, font=T.font(10), highlightthickness=0,
                              disabledbackground=T.FIELD, disabledforeground=T.FAINT,
                              selectbackground=T.mix(T.ACCENT, T.FIELD, 0.5))
        self._win = self.create_window(T.S(14), self._ph / 2, window=self.entry, anchor="w")
        # Placeholder is a label laid over the entry (canvas text would sit underneath it).
        self._hint = tk.Label(self, text=placeholder, bg=T.FIELD, fg=T.FAINT, font=T.font(10), bd=0,
                              cursor="xterm")
        self._hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _e: self._paint(True))
        self.entry.bind("<FocusOut>", lambda _e: self._paint(False))
        self.var.trace_add("write", lambda *_: self._hint_state())
        self.bind("<Configure>", lambda e: self._layout(e.width))
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self._focus = False
        self._layout(self._pw)

    def _layout(self, width: int) -> None:
        self._pw = max(width, T.S(60))
        self.itemconfigure(self._win, width=self._pw - T.S(28))
        self._paint(self._focus)

    def _paint(self, focus: bool) -> None:
        self._focus = focus
        outline = T.ACCENT if focus else T.LINE
        self._photo = rounded(self._pw, self._ph, T.S(12), T.FIELD, self._bg, outline, T.S(1.3))
        self.itemconfigure(self._img, image=self._photo)
        self._hint_state()

    def _hint_state(self) -> None:
        if self.var.get() or not self.placeholder or str(self.entry.cget("state")) == "disabled":
            self._hint.place_forget()
        else:
            self._hint.place(in_=self.entry, x=0, rely=0.5, anchor="w")

    def get(self) -> str:
        return self.var.get()

    def set_enabled(self, enabled: bool) -> None:
        self.entry.configure(state="normal" if enabled else "disabled")
        self._hint_state()


class Segmented(tk.Canvas):
    """A pill containing several options; the chosen one slides into place."""

    def __init__(self, parent: tk.Misc, options: list[tuple[str, str]], value: str,
                 command: Callable[[str], None] | None = None, width: int = 360, height: int = 38) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        self._pw, self._ph = T.S(width), T.S(height)
        super().__init__(parent, width=self._pw, height=self._ph, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.options = options
        self.value = value
        self.command = command
        self._seg = self._pw / len(options)
        self._pos = float(self._index(value))
        self._track = self.create_image(0, 0, anchor="nw")
        self._pill = self.create_image(0, 0, anchor="nw")
        self._labels = [
            self.create_text(self._seg * i + self._seg / 2, self._ph / 2, text=label, font=T.font(9, "bold"))
            for i, (_key, label) in enumerate(options)
        ]
        self.bind("<ButtonRelease-1>", self._click)
        self._anim: str | None = None
        self._paint()

    def _index(self, key: str) -> int:
        return next((i for i, (k, _l) in enumerate(self.options) if k == key), 0)

    def _paint(self) -> None:
        self._t_img = rounded(self._pw, self._ph, self._ph / 2, T.FIELD, self._bg, T.LINE, T.S(1))
        pad = T.S(3)
        self._p_img = rounded(int(self._seg - 2 * pad), self._ph - 2 * pad, (self._ph - 2 * pad) / 2, T.ACCENT, T.FIELD)
        self.itemconfigure(self._track, image=self._t_img)
        self.itemconfigure(self._pill, image=self._p_img)
        self.coords(self._pill, round(self._pos * self._seg + pad), pad)
        current = self._index(self.value)
        for i, item in enumerate(self._labels):
            self.itemconfigure(item, fill=T.ACCENT_TEXT if i == current else T.MUTED)
            self.tag_raise(item)

    def _click(self, e: tk.Event) -> None:
        index = min(len(self.options) - 1, max(0, int(e.x // self._seg)))
        self.set(self.options[index][0])
        if self.command:
            self.command(self.value)

    def set(self, key: str) -> None:
        self.value = key
        if self._anim:
            self.after_cancel(self._anim)
        self._slide(float(self._index(key)))

    def _slide(self, target: float) -> None:
        delta = target - self._pos
        if abs(delta) < 0.02:
            self._pos = target
            self._paint()
            self._anim = None
            return
        self._pos += delta * 0.35
        self._paint()
        self._anim = self.after(14, self._slide, target)


class StatusPill(tk.Canvas):
    def __init__(self, parent: tk.Misc) -> None:
        self._bg = _resolve(parent, bg_of(parent))
        super().__init__(parent, height=T.S(30), width=T.S(200), bg=self._bg, highlightthickness=0, bd=0)
        self._img = self.create_image(0, 0, anchor="nw")
        self._dot = self.create_image(T.S(12), T.S(10), anchor="nw")
        self._text = self.create_text(T.S(28), T.S(15), anchor="w", font=T.font(9, "bold"))
        self.set("Stopped", T.DANGER)

    def set(self, text: str, color: str) -> None:
        f = T.font(9, "bold")
        w = f.measure(text) + T.S(42)
        h = T.S(30)
        self.configure(width=w)
        fill = T.mix(color, self._bg, 0.86)
        self._p = rounded(w, h, h / 2, fill, self._bg)
        self._d = circle(T.S(10), color, fill)
        self.itemconfigure(self._img, image=self._p)
        self.itemconfigure(self._dot, image=self._d)
        self.itemconfigure(self._text, text=text, fill=color)


def chip_image(text: str, fill: str, bg: str, size: int = 9) -> tuple[tk.PhotoImage, int, int]:
    f = T.font(size)
    w = f.measure(text) + T.S(18)
    h = T.S(22)
    return rounded(w, h, h / 2, fill, bg), w, h


class Tooltip:
    """Small rounded hint shown after hovering a widget."""

    def __init__(self, widget: tk.Misc, text: str) -> None:
        self.widget, self.text = widget, text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e: tk.Event) -> None:
        self._job = self.widget.after(450, self._show)

    def _show(self) -> None:
        if self._tip is not None:
            return
        f = T.font(9)
        w, h = f.measure(self.text) + T.S(20), T.S(28)
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=T.BG)
        canvas = tk.Canvas(tip, width=w, height=h, bg=T.BG, highlightthickness=0, bd=0)
        canvas.pack()
        self._img = rounded(w, h, h / 2, T.CARD_HOVER, T.BG, T.LINE, T.S(1))
        canvas.create_image(0, 0, image=self._img, anchor="nw")
        canvas.create_text(w / 2, h / 2, text=self.text, font=f, fill=T.TEXT)
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - w // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + T.S(6)
        tip.geometry(f"+{max(0, x)}+{y}")
        self._tip = tip

    def _hide(self, _e: tk.Event | None = None) -> None:
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
