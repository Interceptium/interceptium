"""Widgets drawn as anti-aliased alpha layers on Tk canvases.

Interactive pieces (toggles, icon buttons) are *components*: they draw onto
any host canvas, so a card can contain them without background mismatches.
Thin ``tk.Canvas`` wrappers make standalone versions.
"""

from __future__ import annotations

import itertools
import math
import sys
import tkinter as tk
from typing import Callable

from . import anim, gfx
from . import theme as T

_ids = itertools.count()


def _tag(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


def parent_bg(widget: tk.Misc) -> str:
    try:
        color = str(widget.cget("bg"))
    except tk.TclError:
        return T.BG
    if color.startswith("#") and len(color) == 7:
        return color
    r, g, b = widget.winfo_rgb(color)
    return "#%02x%02x%02x" % (r >> 8, g >> 8, b >> 8)


class Host(tk.Canvas):
    """A borderless canvas that blends into its parent."""

    def __init__(self, parent: tk.Misc, width: int, height: int, **kw) -> None:
        super().__init__(parent, width=width, height=height, bg=parent_bg(parent), highlightthickness=0, bd=0,
                         **kw)


def _hover_cursor(host: tk.Canvas, tag: str) -> None:
    host.tag_bind(tag, "<Enter>", lambda _e: host.configure(cursor="hand2"), add="+")
    host.tag_bind(tag, "<Leave>", lambda _e: host.configure(cursor=""), add="+")


# --------------------------------------------------------------- toggle --
class ToggleC:
    def __init__(self, host: tk.Canvas, x: int, y: int, value: bool,
                 command: Callable[[bool], None] | None = None, width: int = 40, height: int = 22) -> None:
        self.host, self.command = host, command
        self.w, self.h = T.S(width), T.S(height)
        self.x, self.y = x, y
        self.value = value
        self.pos = 1.0 if value else 0.0
        self.tag = _tag("tg")
        self._track = host.create_image(0, 0, anchor="nw", tags=self.tag)
        self._shadow = host.create_image(0, 0, anchor="nw", tags=self.tag)
        self._knob = host.create_image(0, 0, anchor="nw", tags=self.tag)
        host.tag_bind(self.tag, "<ButtonRelease-1>", lambda _e: self.toggle())
        _hover_cursor(host, self.tag)
        self._draw()

    def move(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        self._draw()

    def _draw(self) -> None:
        pad = max(2, T.S(3))
        d = self.h - 2 * pad
        color = gfx.mix(T.TRACK_OFF, T.ACCENT, self.pos)
        self.host.itemconfigure(self._track, image=gfx.image(gfx.rrect(self.w, self.h, self.h / 2), color))
        self.host.coords(self._track, self.x, self.y)
        kx = self.x + pad + (self.w - 2 * pad - d) * self.pos
        sh = d + T.S(6)
        self.host.itemconfigure(self._shadow, image=gfx.image(gfx.glow(sh, d / 2 - 1, T.S(3)), "#000000", 0.3))
        self.host.coords(self._shadow, round(kx - T.S(3)), self.y + pad - T.S(3) + 1)
        self.host.itemconfigure(self._knob, image=gfx.image(gfx.circle(d), "#ffffff"))
        self.host.coords(self._knob, round(kx), self.y + pad)

    def set(self, value: bool, animate: bool = True) -> None:
        self.value = value
        target = 1.0 if value else 0.0
        if not animate:
            anim.get().stop((self, "t"))
            self.pos = target
            self._draw()
            return
        start = self.pos

        def step(v: float) -> None:
            self.pos = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "t"), 0.2, step)

    def toggle(self) -> None:
        self.set(not self.value)
        if self.command:
            self.command(self.value)


class Toggle(Host):
    def __init__(self, parent: tk.Misc, value: bool = False, command: Callable[[bool], None] | None = None,
                 width: int = 40, height: int = 22) -> None:
        super().__init__(parent, T.S(width), T.S(height))
        self.c = ToggleC(self, 0, 0, value, command, width, height)

    @property
    def value(self) -> bool:
        return self.c.value


# --------------------------------------------------------------- buttons --
_ICON_KINDS = {
    # kind: (base colour or None for transparent, hover colour, icon colour, icon hover colour)
    "subtle": (None, T.GHOST_HOVER, T.MUTED, T.TEXT),
    "ghost": (T.GHOST, T.GHOST_HOVER, T.MUTED, T.TEXT),
    "primary": (T.ACCENT, T.ACCENT_HOVER, T.ACCENT_TEXT, T.ACCENT_TEXT),
}


class IconButtonC:
    def __init__(self, host: tk.Canvas, x: int, y: int, icon: str, command: Callable[[], None] | None,
                 size: int = 32, kind: str = "subtle", icon_size: int = 16) -> None:
        self.host, self.command, self.kind = host, command, kind
        self.d = T.S(size)
        self.icon = icon
        self.icon_px = T.S(icon_size)
        self.x, self.y = x, y
        self.hover = 0.0
        self.pressed = False
        self.tag = _tag("ib")
        self._bg = host.create_image(0, 0, anchor="nw", tags=self.tag)
        self._ic = host.create_image(0, 0, anchor="center", tags=self.tag)
        host.tag_bind(self.tag, "<Enter>", lambda _e: self._fade(1.0), add="+")
        host.tag_bind(self.tag, "<Leave>", lambda _e: self._leave(), add="+")
        host.tag_bind(self.tag, "<ButtonPress-1>", lambda _e: self._press(True))
        host.tag_bind(self.tag, "<ButtonRelease-1>", self._release)
        _hover_cursor(host, self.tag)
        self._draw()

    def move(self, x: int, y: int) -> None:
        self.x, self.y = x, y
        self._draw()

    def _leave(self) -> None:
        self.pressed = False
        self._fade(0.0)

    def _press(self, value: bool) -> None:
        self.pressed = value
        self._draw()

    def _release(self, e: tk.Event) -> None:
        inside = self.x <= e.x <= self.x + self.d and self.y <= e.y <= self.y + self.d
        was = self.pressed
        self._press(False)
        if inside and was and self.command:
            self.command()

    def _fade(self, target: float) -> None:
        start = self.hover

        def step(v: float) -> None:
            self.hover = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "h"), 0.14, step)

    def _draw(self) -> None:
        base, hover, ic, ic_hover = _ICON_KINDS[self.kind]
        mask = gfx.circle(self.d)
        if base is None:
            color, opacity = hover, self.hover
        else:
            color, opacity = gfx.mix(base, hover, self.hover), 1.0
        if self.pressed:
            color, opacity = gfx.mix(color, "#000000", 0.18), max(opacity, 0.8)
        self.host.itemconfigure(self._bg, image=gfx.image(mask, color, opacity))
        self.host.coords(self._bg, self.x, self.y)
        self.host.itemconfigure(self._ic, image=gfx.icon(self.icon, self.icon_px, gfx.mix(ic, ic_hover, self.hover)))
        self.host.coords(self._ic, self.x + self.d / 2, self.y + self.d / 2)


class IconButton(Host):
    def __init__(self, parent: tk.Misc, icon: str, command: Callable[[], None] | None = None,
                 kind: str = "subtle", size: int = 32, icon_size: int = 16) -> None:
        super().__init__(parent, T.S(size), T.S(size))
        self.c = IconButtonC(self, 0, 0, icon, command, size, kind, icon_size)


_BUTTON_KINDS = {
    # kind: (base, hover, text)
    "primary": (T.ACCENT, T.ACCENT_HOVER, T.ACCENT_TEXT),
    "ghost": (T.GHOST, T.GHOST_HOVER, T.TEXT),
    "danger": ("#2a1820", "#3a1f2a", T.DANGER),
}


class Button(Host):
    """Pill button with optional leading icon and a smooth hover fade."""

    def __init__(self, parent: tk.Misc, text: str, command: Callable[[], None] | None = None,
                 kind: str = "ghost", icon: str | None = None, width: int | None = None, height: int = 38,
                 size: int = 10) -> None:
        self.font = T.font(size, "bold")
        self.h = T.S(height)
        self.icon = icon
        self.icon_px = T.S(15)
        self.gap = T.S(8)
        content = self.font.measure(text) + (self.icon_px + self.gap if icon else 0)
        self.w = T.S(width) if width else content + T.S(32)
        super().__init__(parent, self.w, self.h)
        self.text, self.kind, self.command = text, kind, command
        self.enabled = True
        self.hover = 0.0
        self.pressed = False
        self._bg = self.create_image(0, 0, anchor="nw")
        self._ic = self.create_image(0, 0, anchor="w")
        self._tx = self.create_text(0, self.h / 2, anchor="w", font=self.font)
        self.bind("<Enter>", lambda _e: self.enabled and self._fade(1.0))
        self.bind("<Leave>", lambda _e: self._leave())
        self.bind("<ButtonPress-1>", lambda _e: self._press(True))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", self._resize)
        self._draw()

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self._draw()

    def _leave(self) -> None:
        self.pressed = False
        self._fade(0.0)

    def _press(self, value: bool) -> None:
        self.pressed = value and self.enabled
        self._draw()

    def _release(self, e: tk.Event) -> None:
        inside = 0 <= e.x <= self.w and 0 <= e.y <= self.h
        was = self.pressed
        self._press(False)
        if inside and was and self.enabled and self.command:
            self.command()

    def _fade(self, target: float) -> None:
        start = self.hover

        def step(v: float) -> None:
            self.hover = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "h"), 0.14, step)

    def _draw(self) -> None:
        base, hover, fg = _BUTTON_KINDS[self.kind]
        color = gfx.mix(base, hover, self.hover)
        if self.pressed:
            color = gfx.mix(color, "#000000", 0.15)
        if not self.enabled:
            color, fg = gfx.mix(base, parent_bg(self.master), 0.5), T.FAINT
        self.itemconfigure(self._bg, image=gfx.image(gfx.rrect(self.w, self.h, self.h / 2), color))
        content = self.font.measure(self.text) + (self.icon_px + self.gap if self.icon else 0)
        x = (self.w - content) / 2
        if self.icon:
            self.itemconfigure(self._ic, image=gfx.icon(self.icon, self.icon_px, fg), state="normal")
            self.coords(self._ic, round(x), self.h / 2)
            x += self.icon_px + self.gap
        else:
            self.itemconfigure(self._ic, state="hidden")
        self.itemconfigure(self._tx, text=self.text, fill=fg)
        self.coords(self._tx, round(x), self.h / 2)
        self.configure(cursor="hand2" if self.enabled else "")

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self._draw()

    def set_text(self, text: str) -> None:
        self.text = text
        self._draw()


# ------------------------------------------------------------ power button --
class PowerButton(Host):
    """Round start/stop control.

    Stopped: dark disc. Running: accent disc, a light arc orbiting the ring and
    a breathing halo. Starting/stopping: a faster, muted orbit. State changes
    cross-fade.
    """

    PERIOD = 1.6  # seconds per orbit while running
    FRAMES = 96   # orbit frames (cached after the first lap)
    TAIL = 210.0  # degrees of fading tail behind the head

    def __init__(self, parent: tk.Misc, command: Callable[[], None], size: int = 132) -> None:
        self.D = T.S(size)
        self.M = T.S(18)
        self.W = self.D + 2 * self.M
        super().__init__(parent, self.W, self.W, cursor="hand2")
        self.command = command
        self.running = False
        self.busy = False
        self.on = 0.0      # 0 stopped .. 1 running (cross-fade)
        self.orbit = 0.0   # visibility of the orbiting arc
        self.hover = 0.0
        self._angle = 0.0
        self._last = 0.0
        self._frames: dict[tuple, tk.PhotoImage] = {}
        self.ring_w = T.S(3.5)
        self.core_d = self.D - 2 * T.S(15)
        self._ring_pixels = self._prepare_ring()

        m, c = self.M, self.W / 2
        off = (self.W - self.core_d) / 2
        self._halo = self.create_image(0, 0, anchor="nw")
        self._track = self.create_image(m, m, anchor="nw")
        self._arc = self.create_image(m, m, anchor="nw")
        self._head = self.create_image(0, 0, anchor="center")
        self._core_off = self.create_image(off, off, anchor="nw")
        self._core_on = self.create_image(off, off, anchor="nw")
        self._shine = self.create_image(off, off, anchor="nw")
        self._icon_off = self.create_image(c, c, anchor="center")
        self._icon_on = self.create_image(c, c, anchor="center")
        self.bind("<Enter>", lambda _e: self._tween_attr("hover", 1.0, 0.16))
        self.bind("<Leave>", lambda _e: self._tween_attr("hover", 0.0, 0.16))
        self.bind("<ButtonRelease-1>", self._click)
        self.itemconfigure(self._core_off, image=gfx.image(gfx.circle(self.core_d), T.CARD_HOVER))
        self._draw(0.0)

    def _prepare_ring(self) -> list[tuple[int, int, float]]:
        d = self.D
        _w, _h, cov = gfx.ring(d, self.ring_w)
        c = d / 2
        pixels = []
        for i, a in enumerate(cov):
            if a:
                y, x = divmod(i, d)
                ang = math.degrees(math.atan2(x + 0.5 - c, -(y + 0.5 - c))) % 360  # clockwise from top
                pixels.append((i, a, ang))
        return pixels

    def _arc_image(self, frame: int, color: str, level: int) -> tk.PhotoImage:
        key = (frame, color, level)
        img = self._frames.get(key)
        if img is None:
            head = frame * 360.0 / self.FRAMES
            alpha = bytearray(self.D * self.D)
            scale = level / 16.0
            for i, a, ang in self._ring_pixels:
                rel = (head - ang) % 360.0
                if rel < self.TAIL:
                    f = 1.0 - rel / self.TAIL
                    alpha[i] = int(a * (f ** 1.35) * scale)
            img = gfx.alpha_image(self.D, self.D, bytes(alpha), color)
            if len(self._frames) > 900:
                self._frames.clear()
            self._frames[key] = img
        return img

    def _draw(self, now: float) -> None:
        on, hover = self.on, self.hover
        track = gfx.mix(T.LINE, T.MUTED, hover * 0.35)
        self.itemconfigure(self._track, image=gfx.image(gfx.ring(self.D, self.ring_w), track))
        circle = gfx.circle(self.core_d)
        self.itemconfigure(self._core_on, image=gfx.image(circle, T.ACCENT, on))
        self.itemconfigure(self._shine, image=gfx.image(circle, "#ffffff", 0.06 * hover))
        isz = T.S(34)
        self.itemconfigure(self._icon_off, image=gfx.icon("power", isz, T.TEXT, 1.0 - on))
        self.itemconfigure(self._icon_on, image=gfx.icon("power", isz, T.ACCENT_TEXT, on))
        breath = (math.sin(now * 2 * math.pi / 2.4) + 1) / 2
        halo = on * (0.10 + 0.14 * breath)
        halo_mask = gfx.glow(self.W, self.core_d / 2, self.M + T.S(12))
        self.itemconfigure(self._halo, image=gfx.image(halo_mask, T.ACCENT, round(halo * 48) / 48))
        level = int(round(self.orbit * 16))
        if level <= 0:
            self.itemconfigure(self._arc, state="hidden")
            self.itemconfigure(self._head, state="hidden")
            return
        color = T.MUTED if self.busy else T.ACCENT
        frame = int(self._angle / 360.0 * self.FRAMES) % self.FRAMES
        self.itemconfigure(self._arc, image=self._arc_image(frame, color, level), state="normal")
        head = math.radians(frame * 360.0 / self.FRAMES)
        r = self.D / 2 - self.ring_w / 2
        self.itemconfigure(self._head, image=gfx.image(gfx.circle(T.S(7)), color, level / 16), state="normal")
        self.coords(self._head, self.W / 2 + r * math.sin(head), self.W / 2 - r * math.cos(head))

    def _tick(self, now: float) -> None:
        dt = min(0.1, now - self._last) if self._last else 0.0
        self._last = now
        speed = 360.0 / (self.PERIOD / 2.2 if self.busy else self.PERIOD)
        self._angle = (self._angle + speed * dt) % 360.0
        self._draw(now)
        idle = not self.running and not self.busy
        if idle and self.orbit <= 0.0 and self.on <= 0.0 and not anim.get().active((self, "hover")):
            anim.get().stop((self, "loop"))
            self._last = 0.0

    def _tween_attr(self, attr: str, target: float, duration: float) -> None:
        start = getattr(self, attr)

        def step(v: float) -> None:
            setattr(self, attr, start + (target - start) * v)

        anim.get().tween((self, attr), duration, step, ease=anim.ease_in_out)
        if not anim.get().active((self, "loop")):
            anim.get().loop((self, "loop"), self._tick)

    def set_state(self, running: bool, busy: bool = False) -> None:
        if (running, busy) == (self.running, self.busy):
            return
        self.running, self.busy = running, busy
        self._tween_attr("on", 1.0 if running else 0.0, 0.35)
        self._tween_attr("orbit", 1.0 if (running or busy) else 0.0, 0.35)

    def _click(self, e: tk.Event) -> None:
        c = self.W / 2
        if math.hypot(e.x - c, e.y - c) <= self.D / 2 and not self.busy:
            self.command()


# ------------------------------------------------------------- status pill --
class StatusPill(Host):
    def __init__(self, parent: tk.Misc) -> None:
        self.h = T.S(30)
        super().__init__(parent, T.S(120), self.h)
        self._bg = self.create_image(0, 0, anchor="nw")
        self._dot = self.create_image(T.S(15), self.h / 2, anchor="center")
        self._tx = self.create_text(T.S(27), self.h / 2, anchor="w", font=T.font(9, "bold"))
        self.color = T.MUTED
        self.set("Stopped", T.MUTED)

    def set(self, text: str, color: str, pulse: bool = False) -> None:
        self.color = color
        w = T.font(9, "bold").measure(text) + T.S(42)
        self.configure(width=w)
        self.itemconfigure(self._bg, image=gfx.image(gfx.rrect(w, self.h, self.h / 2), gfx.mix(color, T.BG, 0.86)))
        self.itemconfigure(self._tx, text=text, fill=color)
        if pulse:
            anim.get().loop((self, "pulse"), self._beat)
        else:
            anim.get().stop((self, "pulse"))
            self._dot_draw(1.0)

    def _dot_draw(self, opacity: float) -> None:
        self.itemconfigure(self._dot, image=gfx.image(gfx.circle(T.S(8)), self.color, round(opacity * 48) / 48))

    def _beat(self, now: float) -> None:
        self._dot_draw(0.35 + 0.65 * (math.sin(now * 2 * math.pi / 1.8) + 1) / 2)


# -------------------------------------------------------------------- card --
class Card(tk.Frame):
    """Rounded panel. Put children in ``card.body``; normal geometry sizes it."""

    def __init__(self, parent: tk.Misc, fill: str = T.CARD, radius: int = 18, pad: int = 16) -> None:
        super().__init__(parent, bg=parent_bg(parent))
        self.fill = fill
        self._r = T.S(radius)
        self._back = tk.Label(self, bg=parent_bg(parent), bd=0, highlightthickness=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=T.S(pad), pady=T.S(pad))
        self._size = (0, 0)
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, e: tk.Event) -> None:
        if (e.width, e.height) != self._size and e.width > 4 and e.height > 4:
            self._size = (e.width, e.height)
            self._back.configure(image=gfx.image(gfx.rrect(e.width, e.height, self._r), self.fill))


# -------------------------------------------------------------- text field --
class Field(Host):
    """Rounded text input; the outline lights up when focused."""

    def __init__(self, parent: tk.Misc, value: str = "", placeholder: str = "", width: int = 320,
                 height: int = 40, icon: str | None = None) -> None:
        self.w, self.h = T.S(width), T.S(height)
        super().__init__(parent, self.w, self.h)
        self.focus = 0.0
        self.icon = icon
        self._outline = self.create_image(0, 0, anchor="nw")
        self._fill = self.create_image(1, 1, anchor="nw")
        self._ic = self.create_image(T.S(14), self.h / 2, anchor="w")
        self.var = tk.StringVar(value=value)
        self.entry = tk.Entry(self, textvariable=self.var, bd=0, relief="flat", bg=T.FIELD, fg=T.TEXT,
                              insertbackground=T.ACCENT, insertwidth=T.S(2), font=T.font(10), highlightthickness=0,
                              disabledbackground=T.FIELD, disabledforeground=T.FAINT,
                              selectbackground=gfx.mix(T.ACCENT, T.FIELD, 0.55), selectforeground=T.TEXT)
        self.text_x = T.S(40) if icon else T.S(14)
        self._win = self.create_window(self.text_x, self.h / 2, window=self.entry, anchor="w")
        self.placeholder = placeholder
        self._hint = tk.Label(self, text=placeholder, bg=T.FIELD, fg=T.FAINT, font=T.font(10), bd=0, cursor="xterm")
        self._hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _e: self._fade(1.0))
        self.entry.bind("<FocusOut>", lambda _e: self._fade(0.0))
        self.var.trace_add("write", lambda *_: self._hint_state())
        self.bind("<Configure>", self._resize)
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self._draw()
        self._hint_state()

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self._draw()

    def _fade(self, target: float) -> None:
        start = self.focus

        def step(v: float) -> None:
            self.focus = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "f"), 0.16, step)

    def _draw(self) -> None:
        b = max(1, T.S(1.25))
        r = T.S(12)
        outline = gfx.mix(T.LINE, T.ACCENT, self.focus)
        self.itemconfigure(self._outline, image=gfx.image(gfx.rrect(self.w, self.h, r), outline))
        self.itemconfigure(self._fill, image=gfx.image(gfx.rrect(self.w - 2 * b, self.h - 2 * b, r - b), T.FIELD))
        self.coords(self._fill, b, b)
        if self.icon:
            self.itemconfigure(self._ic, image=gfx.icon(self.icon, T.S(16), gfx.mix(T.FAINT, T.ACCENT, self.focus)))
        self.itemconfigure(self._win, width=max(10, self.w - self.text_x - T.S(14)))

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


# --------------------------------------------------------------- segmented --
class Segmented(Host):
    def __init__(self, parent: tk.Misc, options: list[tuple[str, str]], value: str,
                 command: Callable[[str], None] | None = None, width: int = 360, height: int = 38) -> None:
        self.w, self.h = T.S(width), T.S(height)
        super().__init__(parent, self.w, self.h, cursor="hand2")
        self.options, self.value, self.command = options, value, command
        self.seg = self.w / len(options)
        self.pos = float(self._index(value))
        b = max(1, T.S(1.25))
        self.create_image(0, 0, anchor="nw", image=gfx.image(gfx.rrect(self.w, self.h, self.h / 2), T.LINE))
        self.create_image(b, b, anchor="nw",
                          image=gfx.image(gfx.rrect(self.w - 2 * b, self.h - 2 * b, self.h / 2 - b), T.FIELD))
        self.pad = T.S(4)
        pw, ph = int(self.seg - 2 * self.pad), self.h - 2 * self.pad
        self._pill = self.create_image(0, self.pad, anchor="nw", image=gfx.image(gfx.rrect(pw, ph, ph / 2), T.ACCENT))
        self._labels = [self.create_text(self.seg * i + self.seg / 2, self.h / 2, text=label,
                                         font=T.font(9, "bold")) for i, (_k, label) in enumerate(options)]
        self.bind("<ButtonRelease-1>", self._click)
        self._draw()

    def _index(self, key: str) -> int:
        return next((i for i, (k, _l) in enumerate(self.options) if k == key), 0)

    def _draw(self) -> None:
        self.coords(self._pill, round(self.pos * self.seg + self.pad), self.pad)
        for i, item in enumerate(self._labels):
            near = max(0.0, 1.0 - abs(self.pos - i))
            self.itemconfigure(item, fill=gfx.mix(T.MUTED, T.ACCENT_TEXT, near))

    def _click(self, e: tk.Event) -> None:
        index = min(len(self.options) - 1, max(0, int(e.x // self.seg)))
        self.set(self.options[index][0])
        if self.command:
            self.command(self.value)

    def set(self, key: str) -> None:
        self.value = key
        start, target = self.pos, float(self._index(key))

        def step(v: float) -> None:
            self.pos = start + (target - start) * v
            self._draw()

        anim.get().tween((self, "s"), 0.24, step)


# ------------------------------------------------------------- scroll area --
class ScrollArea(tk.Frame):
    """Vertical scroller with eased wheel scrolling and a slim thumb."""

    def __init__(self, parent: tk.Misc, bg: str) -> None:
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.bar = tk.Canvas(self, width=T.S(6), bg=bg, highlightthickness=0, bd=0)
        self._thumb = self.bar.create_image(0, 0, anchor="nw")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y", padx=(T.S(6), 0))
        self.pos = 0.0
        self.target = 0.0
        self._drag: tuple[float, float] | None = None
        self.inner.bind("<Configure>", lambda _e: self._content_changed())
        self.canvas.bind("<Configure>", self._resize)
        self.bar.bind("<ButtonPress-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        self.bar.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag", None))

    def _content_h(self) -> int:
        return max(1, self.inner.winfo_reqheight())

    def _max(self) -> float:
        return max(0.0, self._content_h() - self.canvas.winfo_height())

    def _resize(self, e: tk.Event) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)
        self._content_changed()

    def _content_changed(self) -> None:
        self.canvas.configure(scrollregion=(0, 0, 0, self._content_h()))
        self.pos = min(self.pos, self._max())
        self.target = min(self.target, self._max())
        self._apply()

    def _apply(self) -> None:
        self.canvas.yview_moveto(self.pos / self._content_h())
        self._draw_bar()

    def _draw_bar(self) -> None:
        view = self.canvas.winfo_height()
        total = self._content_h()
        h = self.bar.winfo_height()
        if total <= view + 1 or h < 10:
            self.bar.itemconfigure(self._thumb, state="hidden")
            return
        th = max(T.S(32), int(h * view / total))
        y = int((h - th) * (self.pos / max(1.0, self._max())))
        self.bar.itemconfigure(self._thumb, image=gfx.image(gfx.rrect(T.S(6), th, T.S(3)), T.LINE), state="normal")
        self.bar.coords(self._thumb, 0, y)

    def scroll_by(self, px: float) -> None:
        self.target = max(0.0, min(self._max(), self.target + px))
        start, target = self.pos, self.target

        def step(v: float) -> None:
            self.pos = start + (target - start) * v
            self._apply()

        anim.get().tween((self, "scroll"), 0.22, step)

    def to(self, px: float) -> None:
        anim.get().stop((self, "scroll"))
        self.pos = self.target = max(0.0, min(self._max(), px))
        self._apply()

    def rise(self, distance: int = 22, duration: float = 0.3) -> None:
        """Content glides up into place (used when the list is replaced)."""
        d = T.S(distance)

        def step(v: float) -> None:
            self.canvas.coords(self._win, 0, round(d * (1 - v)))

        self.canvas.coords(self._win, 0, d)
        anim.get().tween((self, "rise"), duration, step, ease=lambda t: 1 - (1 - t) ** 5)

    def _bar_press(self, e: tk.Event) -> None:
        self._drag = (e.y, self.pos)

    def _bar_drag(self, e: tk.Event) -> None:
        if self._drag is None:
            return
        view = max(1, self.canvas.winfo_height())
        h = max(1, self.bar.winfo_height())
        th = max(T.S(32), int(h * view / self._content_h()))
        y0, p0 = self._drag
        track = max(1, h - th)
        self.to(p0 + (e.y - y0) * self._max() / track)


def install_wheel(root: tk.Misc) -> None:
    """Route mouse-wheel events to the ScrollArea under the pointer."""

    def find(e: tk.Event) -> ScrollArea | None:
        try:
            widget = root.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            return None
        while widget is not None:
            if isinstance(widget, ScrollArea):
                return widget
            if isinstance(widget, tk.Text):
                return None  # text widgets scroll themselves
            widget = widget.master
        return None

    def wheel(e: tk.Event) -> None:
        area = find(e)
        if area is None or not e.delta:
            return
        if sys.platform == "darwin":
            area.scroll_by(-e.delta * T.S(6))
        else:
            area.scroll_by(-e.delta / 120 * T.S(90))

    def button(e: tk.Event, direction: int) -> None:
        area = find(e)
        if area is not None:
            area.scroll_by(direction * T.S(90))

    root.bind_all("<MouseWheel>", wheel, add="+")
    root.bind_all("<Button-4>", lambda e: button(e, -1), add="+")
    root.bind_all("<Button-5>", lambda e: button(e, 1), add="+")


# ------------------------------------------------------------ progress bar --
class ProgressBar(Host):
    """Rounded bar; the fill eases toward new values and a soft light sweeps across while busy."""

    def __init__(self, parent: tk.Misc, width: int = 420, height: int = 8) -> None:
        self.w, self.h = T.S(width), T.S(height)
        super().__init__(parent, self.w, self.h)
        self.value = 0.0
        self.shown = 0.0
        self.busy = False
        self._track = self.create_image(0, 0, anchor="nw")
        self._fill = self.create_image(0, 0, anchor="nw")
        self._shine = self.create_image(0, 0, anchor="nw")
        self.bind("<Configure>", self._resize)
        self._draw(0.0)

    def _resize(self, e: tk.Event) -> None:
        if e.width != self.w:
            self.w = e.width
            self._draw(0.0)

    def _draw(self, now: float) -> None:
        h = self.h
        self.itemconfigure(self._track, image=gfx.image(gfx.rrect(self.w, h, h / 2), T.TRACK_OFF))
        fw = int(round(self.w * max(0.0, min(1.0, self.shown))))
        if fw < h:
            self.itemconfigure(self._fill, state="hidden")
            self.itemconfigure(self._shine, state="hidden")
            return
        self.itemconfigure(self._fill, image=gfx.image(gfx.rrect(fw, h, h / 2), T.ACCENT), state="normal")
        if self.busy:
            sw = min(fw, T.S(90))
            x = ((now * 0.9) % 1.4 - 0.2) * fw
            x = max(0, min(fw - sw, x))
            self.itemconfigure(self._shine, image=gfx.image(gfx.glow(sw, 0, sw / 2), "#ffffff", 0.35),
                               state="normal")
            self.coords(self._shine, round(x), round(h / 2 - sw / 2))
        else:
            self.itemconfigure(self._shine, state="hidden")

    def set(self, value: float) -> None:
        self.value = max(0.0, min(1.0, value))
        start, target = self.shown, self.value

        def step(v: float) -> None:
            self.shown = start + (target - start) * v

        anim.get().tween((self, "v"), 0.35, step)
        if not anim.get().active((self, "loop")):
            anim.get().loop((self, "loop"), self._tick)

    def _tick(self, now: float) -> None:
        self._draw(now)
        if not self.busy and not anim.get().active((self, "v")):
            anim.get().stop((self, "loop"))
            self._draw(now)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy and not anim.get().active((self, "loop")):
            anim.get().loop((self, "loop"), self._tick)


# ------------------------------------------------------------------ stage --
class Stage(tk.Frame):
    """Holds one view at a time and slides between views (with a parallax push)."""

    def __init__(self, parent: tk.Misc, bg: str, duration: float = 0.34) -> None:
        super().__init__(parent, bg=bg)
        self.current: tk.Widget | None = None
        self.duration = duration
        self.sliding = False

    def show(self, view: tk.Widget, direction: int = 1, animate: bool = True,
             done: Callable[[], None] | None = None) -> None:
        """direction 1 = new view comes from the right, -1 = from the left."""
        leaving = getattr(self, "_leaving", None)
        if leaving is not None:  # a previous slide is still running: finish it at once
            anim.get().stop((self, "slide"))
            self._dispose(leaving)
            self._leaving = None
            if self.current is not None:
                self.current.place_configure(x=0)
        old = self.current
        self.current = view
        if old is None or not animate:
            if old is not None:
                self._dispose(old)
            view.place(x=0, y=0, relwidth=1, relheight=1)
            if done:
                done()
            return
        self.sliding = True
        self._leaving = old
        width = max(1, self.winfo_width())
        view.place(x=direction * width, y=0, relwidth=1, relheight=1)
        view.lift()

        def step(v: float) -> None:
            view.place_configure(x=int(direction * width * (1 - v)))
            old.place_configure(x=int(-direction * width * 0.3 * v))

        def finish() -> None:
            self.sliding = False
            self._leaving = None
            self._dispose(old)
            if done:
                done()

        anim.get().tween((self, "slide"), self.duration, step, ease=anim.ease_in_out, done=finish)

    @staticmethod
    def _dispose(view: tk.Widget) -> None:
        try:
            view.place_forget()
            if getattr(view, "_stage_dispose", False):
                view.destroy()
        except tk.TclError:
            pass
