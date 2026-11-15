"""Mô hình dữ liệu câu thoại và đọc/ghi kịch bản.

Đọc được 3 định dạng (tự nhận diện):
  1. SRT chuẩn
  2. `[0.00s -> 3.74s] Nội dung`  hoặc  `[00:00:01.200 -> 00:00:03.400] Nội dung`
  3. `1.2, 3.4, Nội dung`  (định dạng cũ của tool)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable, List


@dataclass
class Segment:
    start: float
    end: float
    text: str          # nội dung dùng để lồng tiếng (bản dịch)
    src: str = ""      # lời gốc (nếu có)

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------- thời gian
def parse_time(value: str) -> float:
    s = value.strip().lower().rstrip("s").strip()
    if ":" in s:
        parts = s.replace(",", ".").split(":")
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    return float(s.replace(",", "."))


def _split_ms(t: float):
    ms = int(round(max(t, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return h, m, s, ms


def fmt_srt(t: float) -> str:
    h, m, s, ms = _split_ms(t)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def fmt_editor(t: float) -> str:
    h, m, s, ms = _split_ms(t)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


# ------------------------------------------------------------------ đọc
_T = r"\d+(?::\d+){0,2}(?:[.,]\d+)?s?"
_ARROW = re.compile(rf"^\s*\[?\s*({_T})\s*(?:-->|->)\s*({_T})\s*\]?\s*(.*)$")
_CSV = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(.+)$")


def parse_script(text: str) -> List[Segment]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").split("\n")
    segs: List[Segment] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = _ARROW.match(line)
        if m:
            start, end, rest = parse_time(m[1]), parse_time(m[2]), m[3].strip()
            if rest:  # dạng [a -> b] nội dung
                segs.append(Segment(start, end, rest))
            else:     # dạng SRT: nội dung nằm ở các dòng sau
                buf, j = [], i + 1
                while j < len(lines) and lines[j].strip() and not _ARROW.match(lines[j].strip()):
                    buf.append(lines[j].strip())
                    j += 1
                segs.append(Segment(start, end, " ".join(buf)))
                i = j
                continue
        else:
            m = _CSV.match(line)
            if m:
                segs.append(Segment(float(m[1]), float(m[2]), m[3].strip()))
        i += 1
    return normalize(segs)


def normalize(segs: Iterable[Segment]) -> List[Segment]:
    """Sắp xếp, sửa câu có thời lượng <= 0 và chống chồng lấn."""
    out = sorted((Segment(s.start, s.end, s.text, s.src) for s in segs), key=lambda s: s.start)
    for i, s in enumerate(out):
        if s.end <= s.start:
            s.end = s.start + 0.5
        if i + 1 < len(out) and s.end > out[i + 1].start > s.start:
            s.end = out[i + 1].start
    return out


# ------------------------------------------------------------------ ghi
def format_srt(segs: Iterable[Segment]) -> str:
    blocks = []
    for i, s in enumerate(segs, 1):
        blocks.append(f"{i}\n{fmt_srt(s.start)} --> {fmt_srt(s.end)}\n{s.text.strip()}\n")
    return "\n".join(blocks)


def format_editor(segs: Iterable[Segment]) -> str:
    """Định dạng dễ sửa tay trong ô soạn kịch bản."""
    return "\n".join(f"[{fmt_editor(s.start)} -> {fmt_editor(s.end)}] {s.text.strip()}" for s in segs) + "\n"


def to_json(segs: Iterable[Segment]) -> str:
    return json.dumps([asdict(s) for s in segs], ensure_ascii=False, indent=2)


def from_json(text: str) -> List[Segment]:
    return [Segment(**d) for d in json.loads(text)]


# ------------------------------------------------------ tách câu quá dài
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？;；])\s*")


def split_long_segments(segs: Iterable[Segment], max_seconds: float) -> List[Segment]:
    """Tách câu Whisper dài (vd 25 giây) thành các câu ngắn theo dấu câu, chia thời gian theo độ dài chữ."""
    out: List[Segment] = []
    for s in segs:
        if s.duration <= max_seconds:
            out.append(s)
            continue
        pieces = [p.strip() for p in _SENT_SPLIT.split(s.text) if p.strip()]
        if len(pieces) < 2:
            out.append(s)
            continue
        total = sum(len(p) for p in pieces)
        limit = total * max_seconds / s.duration
        chunks, cur, cur_len = [], [], 0
        for p in pieces:
            if cur and cur_len + len(p) > limit:
                chunks.append(cur)
                cur, cur_len = [], 0
            cur.append(p)
            cur_len += len(p)
        if cur:
            chunks.append(cur)
        t = s.start
        for ch in chunks:
            text = " ".join(ch)
            d = s.duration * len(text) / max(total, 1)
            out.append(Segment(t, t + d, text, text))
            t += d
    return out
