"""Tiny thread-safe log fan-out used by the proxy, the CLI and the GUI."""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

_Listener = Callable[[str], None]


class LogBus:
    def __init__(self, keep: int = 2000) -> None:
        self._lines: deque[str] = deque(maxlen=keep)
        self._listeners: list[_Listener] = []
        self._lock = threading.Lock()

    def emit(self, topic: str, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} [{topic}] {message}"
        with self._lock:
            self._lines.append(line)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(line)
            except Exception:  # a broken listener must never break the proxy
                pass

    def subscribe(self, listener: _Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def history(self) -> list[str]:
        with self._lock:
            return list(self._lines)


log = LogBus()
