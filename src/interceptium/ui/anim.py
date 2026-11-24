"""Frame-paced animation engine (targets 60 fps).

All motion is driven by elapsed wall-clock time, so animations keep the same
speed even when a frame is late. The ticker only runs while something is
animating.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

FRAME = 1.0 / 60.0


def ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def linear(t: float) -> float:
    return t


class Animator:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self._tweens: dict[object, tuple[float, float, Callable[[float], None], Callable[[float], float],
                                         Callable[[], None] | None]] = {}
        self._loops: dict[object, Callable[[float], None]] = {}
        self._job: str | None = None
        self._deadline = 0.0
        self.frames = 0

    def tween(self, key: object, duration: float, fn: Callable[[float], None],
              ease: Callable[[float], float] = ease_out, done: Callable[[], None] | None = None) -> None:
        """Call ``fn(progress)`` every frame for ``duration`` seconds (progress eased 0..1)."""
        self._tweens[key] = (time.perf_counter(), max(duration, 1e-3), fn, ease, done)
        self._kick()

    def loop(self, key: object, fn: Callable[[float], None]) -> None:
        """Call ``fn(now)`` every frame until ``stop(key)``."""
        self._loops[key] = fn
        self._kick()

    def stop(self, key: object) -> None:
        self._tweens.pop(key, None)
        self._loops.pop(key, None)

    def stop_all(self) -> None:
        self._tweens.clear()
        self._loops.clear()
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def active(self, key: object) -> bool:
        return key in self._tweens or key in self._loops

    def _kick(self) -> None:
        if self._job is None:
            self._job = self.root.after(1, self._tick)

    def _tick(self) -> None:
        self._job = None
        start = time.perf_counter()
        for key, (t0, dur, fn, ease, done) in list(self._tweens.items()):
            t = min(1.0, (start - t0) / dur)
            try:
                fn(ease(t))
            except tk.TclError:  # widget was destroyed mid-animation
                self._tweens.pop(key, None)
                continue
            if t >= 1.0 and self._tweens.get(key, (None,))[0] == t0:
                self._tweens.pop(key, None)
                if done:
                    try:
                        done()
                    except tk.TclError:
                        pass
        for key, fn in list(self._loops.items()):
            try:
                fn(start)
            except tk.TclError:
                self._loops.pop(key, None)
        self.frames += 1
        if self._tweens or self._loops:
            # Aim at fixed frame deadlines (not "16 ms after this frame"), so time Tk spends redrawing
            # between frames doesn't add up and drag the rate below 60 fps.
            now = time.perf_counter()
            self._deadline = (self._deadline or start) + FRAME
            if self._deadline < now - FRAME:  # fell far behind (busy UI): don't try to catch up in a burst
                self._deadline = now + FRAME
            delay = max(1, int(round((self._deadline - now) * 1000)))
            self._job = self.root.after(delay, self._tick)
        else:
            self._deadline = 0.0


_animator: Animator | None = None


def setup(root: tk.Misc) -> Animator:
    global _animator
    _animator = Animator(root)
    return _animator


def get() -> Animator:
    if _animator is None:
        raise RuntimeError("animation engine not initialised")
    return _animator
