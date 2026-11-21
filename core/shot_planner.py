"""Lập kế hoạch cảnh từ mạch lời kể liên tục.

Đầu vào: các beat (đoạn ý của lời kể, mỗi đoạn có địa điểm và cường độ) và mốc thời gian từng từ.
Đầu ra: danh sách Shot có mốc bắt đầu/kết thúc tính bằng giây, chuyển động, và kiểu chuyển cảnh.

Nguyên tắc:
  * Cảnh được cắt theo ranh giới câu/ý trong lời kể, không theo ảnh.
  * Cùng một địa điểm liên tiếp thì KHÔNG cắt cứng: camera tiếp tục từ trạng thái cũ (continuous).
  * Đổi địa điểm thì cắt tại đúng ranh giới ý.
  * Cường độ cao hơn thì camera tiến gần hơn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

SENTENCE_END = ".!?…"
CLAUSE_END = ",;:—"
ZOOM_BY_INTENSITY = {1: 0.06, 2: 0.14, 3: 0.24}   # biên độ phóng to trong một shot
ZOOM_BASE = 1.0


@dataclass
class Beat:
    id: int
    text: str
    location: str
    intensity: int = 1          # 1 = bình thản, 2 = tăng dần, 3 = cao trào
    intent: str = "explain"     # explain | establish | reveal | emotion | transition


@dataclass
class Shot:
    id: int
    start: float
    end: float
    location: str
    beat_ids: List[int]
    motion: str                 # push_in | pull_out | hold
    zoom_from: float
    zoom_to: float
    transition_in: str          # cut | continuous | crossfade
    token_range: Tuple[int, int] = (0, 0)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _tokens_of(beats: Sequence[Beat]) -> Tuple[List[str], List[int], List[int]]:
    """Trả về (token, chỉ số beat của token, cờ cuối câu) cho toàn bộ lời kể."""
    tokens, beat_of, sent_end = [], [], []
    for b in beats:
        for tok in b.text.split():
            tokens.append(tok)
            beat_of.append(b.id)
            sent_end.append(1 if tok[-1:] in SENTENCE_END else 0)
    return tokens, beat_of, sent_end


def plan_shots(beats: Sequence[Beat], word_times: Sequence[Tuple[float, float]],
               max_shot: float = 6.0, min_shot: float = 1.5) -> List[Shot]:
    """word_times[i] = (bắt đầu, kết thúc) của token i. Số token phải khớp với các beat."""
    if not beats:
        return []
    tokens, beat_of, sent_end = _tokens_of(beats)
    n = len(tokens)
    if len(word_times) != n:
        raise ValueError(f"Số mốc thời gian ({len(word_times)}) không khớp số từ trong lời kể ({n})")
    loc_of = {b.id: b.location for b in beats}
    intensity_of = {b.id: b.intensity for b in beats}
    intent_of = {b.id: b.intent for b in beats}

    # Ứng viên cắt: cuối beat, cuối câu. Giá trị là chỉ số token đầu của đoạn sau (cắt TRƯỚC token đó).
    beat_bound = {i for i in range(1, n) if beat_of[i] != beat_of[i - 1]}
    sent_bound = {i + 1 for i in range(n - 1) if sent_end[i]}
    clause_bound = {i + 1 for i in range(n - 1) if tokens[i][-1:] in CLAUSE_END}
    candidates = sorted((beat_bound | sent_bound | clause_bound) | {n})

    def t_start(i: int) -> float:
        return word_times[i][0]

    def t_end(i: int) -> float:
        return word_times[i - 1][1]          # cắt trước token i nghĩa là kết thúc ở token i-1

    shots: List[Shot] = []
    s = 0
    prev_loc: Optional[str] = None
    zoom_state = ZOOM_BASE
    while s < n:
        ahead = [c for c in candidates if c > s]
        best = None
        for c in ahead:
            if t_end(c) - t_start(s) > max_shot:
                break
            best = c
        if best is None:
            # Không có ranh giới ý nào đủ ngắn: cắt theo token xa nhất còn trong giới hạn.
            best = s + 1
            for i in range(s + 1, n + 1):
                if t_end(i) - t_start(s) <= max_shot:
                    best = i
                else:
                    break
        # Đổi địa điểm trong khoảng này thì phải cắt tại chỗ đổi
        for i in range(s + 1, best):
            if loc_of[beat_of[i]] != loc_of[beat_of[s]]:
                best = i
                break
        # Quá ngắn và chưa phải cuối: thử nối thêm một ứng viên nếu vẫn ở cùng địa điểm
        if t_end(best) - t_start(s) < min_shot and best < n:
            nxt = [c for c in candidates if c > best]
            for c in nxt:
                if t_end(c) - t_start(s) > max_shot:
                    break
                if loc_of[beat_of[c - 1]] != loc_of[beat_of[s]]:
                    break
                best = c
                if t_end(best) - t_start(s) >= min_shot:
                    break

        loc = loc_of[beat_of[s]]
        bid = sorted({beat_of[i] for i in range(s, best)}, key=lambda x: x)
        intensity = max(intensity_of[b] for b in bid)
        intent = intent_of[bid[0]]
        if intent == "establish":
            motion = "pull_out"
        elif intent == "reveal":
            motion = "pull_out"
        else:
            motion = "push_in" if intensity >= 2 else "hold"
        amount = ZOOM_BY_INTENSITY.get(intensity, 0.0) if motion == "push_in" else 0.0

        if loc == prev_loc and shots:
            transition = "continuous"
            zoom_from = zoom_state            # tiếp tục từ mức zoom cũ, không reset
        else:
            transition = "cut" if prev_loc is not None and loc != prev_loc else "cut"
            zoom_from = ZOOM_BASE
        zoom_to = zoom_from + amount if motion == "push_in" else (
            zoom_from - amount if motion == "pull_out" and zoom_from > ZOOM_BASE else zoom_from)
        zoom_state = zoom_to

        start = t_start(s) if not shots else shots[-1].end
        end = t_end(best)
        shots.append(Shot(
            id=len(shots) + 1, start=round(start, 3), end=round(end, 3), location=loc,
            beat_ids=bid, motion=motion, zoom_from=round(zoom_from, 4), zoom_to=round(zoom_to, 4),
            transition_in=transition, token_range=(s, best),
        ))
        prev_loc = loc
        s = best
    merge_short_tail(shots, min_shot, max_shot)
    return shots


def merge_short_tail(shots: List[Shot], min_shot: float, max_shot: float) -> None:
    """Gộp shot cuối nếu quá ngắn vào shot trước, khi cùng địa điểm và tổng không vượt max_shot. Sửa tại chỗ."""
    if len(shots) < 2:
        return
    prev, last = shots[-2], shots[-1]
    if last.duration >= min_shot or prev.location != last.location:
        return
    if last.end - prev.start > max_shot:
        return
    prev.end = last.end
    prev.beat_ids = sorted(set(prev.beat_ids) | set(last.beat_ids))
    prev.token_range = (prev.token_range[0], last.token_range[1])
    shots.pop()
