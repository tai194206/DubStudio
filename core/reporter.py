"""Kênh báo cáo tiến độ/log dùng chung cho GUI và CLI, kèm cơ chế hủy."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from .errors import Cancelled


class Reporter:
    """log(msg, level) và progress(pct, msg). `scope()` ánh xạ 0-100% vào một đoạn của tiến độ tổng."""

    def __init__(
        self,
        on_log: Optional[Callable[[str, str], None]] = None,
        on_progress: Optional[Callable[[float, Optional[str]], None]] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        self._on_log = on_log
        self._on_progress = on_progress
        self.cancel_event = cancel_event or threading.Event()
        self._lo = 0.0
        self._hi = 100.0

    def log(self, msg: str, level: str = "info") -> None:
        if self._on_log:
            self._on_log(str(msg), level)
        else:
            print(msg)

    def progress(self, pct: float, msg: Optional[str] = None) -> None:
        pct = max(0.0, min(100.0, pct))
        if self._on_progress:
            self._on_progress(self._lo + (self._hi - self._lo) * pct / 100.0, msg)

    def scope(self, lo: float, hi: float) -> "Reporter":
        child = Reporter(self._on_log, self._on_progress, self.cancel_event)
        span = self._hi - self._lo
        child._lo = self._lo + span * lo / 100.0
        child._hi = self._lo + span * hi / 100.0
        return child

    def cancel(self) -> None:
        self.cancel_event.set()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("Đã hủy theo yêu cầu.")
