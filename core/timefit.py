"""Tính toán khớp thời gian: câu đọc dài hơn khung thì cần tăng tốc bao nhiêu."""
from __future__ import annotations

from typing import Sequence

from .subtitles import Segment

GAP_GUARD = 0.05  # chừa một khoảng nhỏ trước câu kế tiếp


def fit_window(segments: Sequence[Segment], i: int, total_duration: float) -> float:
    """Thời gian tối đa câu i được phép chiếm: từ lúc bắt đầu đến lúc câu kế tiếp bắt đầu.

    Bản cũ chỉ cho phép đúng khung [start, end] nên câu nào hơi dài cũng bị tăng tốc dù phía sau là
    khoảng lặng. Cho phép "mượn" khoảng lặng giúp giọng tự nhiên hơn.
    """
    seg = segments[i]
    nxt = segments[i + 1].start if i + 1 < len(segments) else total_duration
    return max(seg.duration, nxt - seg.start - GAP_GUARD)


def compute_speed(duration: float, window: float, max_speed: float) -> float:
    """Hệ số tăng tốc (>=1.0). Không bao giờ làm chậm giọng."""
    if duration <= 0 or duration <= window:
        return 1.0
    return min(duration / window, max_speed)
