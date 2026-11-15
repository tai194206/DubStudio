"""Mô hình dữ liệu chuẩn của DubStudio.

Bao gồm Segment và WordTiming phục vụ việc căn mốc từng từ cho phụ đề động Karaoke.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class WordTiming:
    word: str
    start: float  # tính bằng giây, tương đối so với đầu câu
    end: float    # tính bằng giây, tương đối so với đầu câu

    @property
    def duration(self) -> float:
        return self.end - self.start

    def scale_and_shift(self, speed: float = 1.0, offset: float = 0.0) -> WordTiming:
        """Quy đổi mốc thời gian khi câu thoại bị đổi tốc độ và ghép vào mốc thời gian của video.
        
        formula: (t / speed) + offset
        """
        s = max(speed, 0.01)
        return WordTiming(
            word=self.word,
            start=round(self.start / s + offset, 4),
            end=round(self.end / s + offset, 4),
        )

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "start": round(self.start, 4),
            "end": round(self.end, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> WordTiming:
        return cls(
            word=str(data.get("word", "")),
            start=float(data.get("start", 0.0)),
            end=float(data.get("end", 0.0)),
        )


@dataclass
class Segment:
    start: float
    end: float
    text: str                                  # nội dung câu thoại dùng để lồng tiếng
    src: str = ""                              # nội dung gốc (nếu có)
    words: list[WordTiming] = field(default_factory=list)  # mốc thời gian từng từ

    @property
    def duration(self) -> float:
        return self.end - self.start


def scale_word_timings(words: Sequence[WordTiming], speed: float = 1.0, offset: float = 0.0) -> list[WordTiming]:
    """Quy đổi danh sách WordTiming khi câu được tăng tốc speed và ghép tại mốc offset."""
    s = max(speed, 0.01)
    return [w.scale_and_shift(s, offset) for w in words]
