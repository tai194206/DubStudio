"""Adapter chuyển đổi dữ liệu storyboard và mốc từ của dự án sang đầu vào cho shot_planner.

Tuân thủ nguyên tắc:
  * Không phụ thuộc GUI (không import tkinter/gui).
  * Không tự suy ra beats từ scenes; nếu chưa có beats thì báo lỗi rõ ràng.
"""
from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple, Union

from .models import WordTiming
from .shot_planner import Beat

VALID_INTENTS = frozenset({"explain", "establish", "reveal", "emotion", "transition"})


def beats_from_storyboard(sb: dict) -> List[Beat]:
    """Đọc trường 'beats' trong JSON storyboard và trả về danh sách Beat.

    Ném ValueError rõ ràng nếu storyboard chưa có beats, hoặc beat thiếu/sai trường.
    """
    if not isinstance(sb, dict):
        raise ValueError("storyboard phải là dict")

    if "beats" not in sb or sb["beats"] is None:
        raise ValueError("storyboard chưa có beats; cần sinh beats trước")

    raw_beats = sb["beats"]
    if not isinstance(raw_beats, list):
        raise ValueError("Trường 'beats' trong storyboard phải là danh sách")

    beats: List[Beat] = []
    for idx, b in enumerate(raw_beats):
        if not isinstance(b, dict):
            raise ValueError(f"Beat tại vị trí {idx} phải là dictionary")

        # 1. Kiểm tra id
        b_id = b.get("id")
        if b_id is None or not isinstance(b_id, int) or isinstance(b_id, bool):
            raise ValueError(f"Beat tại vị trí {idx} thiếu hoặc sai trường 'id' (phải là int)")

        # 2. Kiểm tra text
        text = b.get("text")
        if text is None or not isinstance(text, str) or not text.strip():
            raise ValueError(f"Beat id={b_id} thiếu hoặc có trường 'text' rỗng")

        # 3. Kiểm tra location
        location = b.get("location")
        if location is None or not isinstance(location, str) or not location.strip():
            raise ValueError(f"Beat id={b_id} thiếu hoặc có trường 'location' rỗng")

        # 4. Kiểm tra intensity
        intensity = b.get("intensity", 1)
        if not isinstance(intensity, int) or isinstance(intensity, bool) or not (1 <= intensity <= 3):
            raise ValueError(f"Beat id={b_id} có intensity={intensity} không hợp lệ; phải nằm trong khoảng 1-3")

        # 5. Kiểm tra intent
        intent = b.get("intent", "explain")
        if not isinstance(intent, str) or intent not in VALID_INTENTS:
            raise ValueError(
                f"Beat id={b_id} có intent='{intent}' không hợp lệ; phải là một trong {sorted(VALID_INTENTS)}"
            )

        beats.append(
            Beat(
                id=b_id,
                text=text.strip(),
                location=location.strip(),
                intensity=intensity,
                intent=intent,
            )
        )

    return beats


def word_times_from_words(
    words: Sequence[Any],
    beats: Optional[Union[Sequence[Beat], int]] = None,
) -> List[Tuple[float, float]]:
    """Chuyển danh sách mốc từ (WordTiming, dict hoặc tuple) sang list[(start, end)].

    Nếu truyền vào `beats` (danh sách Beat hoặc số int token dự kiến), hàm sẽ kiểm tra
    số lượng mốc từ so với tổng số token; nếu lệch sẽ ném ValueError chứa cả hai số.
    """
    word_times: List[Tuple[float, float]] = []

    for idx, w in enumerate(words):
        if isinstance(w, WordTiming) or (hasattr(w, "start") and hasattr(w, "end")):
            word_times.append((float(w.start), float(w.end)))
        elif isinstance(w, dict) and "start" in w and "end" in w:
            word_times.append((float(w["start"]), float(w["end"])))
        elif isinstance(w, (tuple, list)) and len(w) >= 2:
            word_times.append((float(w[0]), float(w[1])))
        else:
            raise ValueError(f"Mốc từ tại vị trí {idx} không hợp lệ: {w}")

    # Kiểm tra số lượng với beats nếu được cung cấp
    if beats is not None:
        if isinstance(beats, int):
            expected_tokens = beats
        else:
            expected_tokens = sum(len(b.text.split()) for b in beats)

        actual_tokens = len(word_times)
        if actual_tokens != expected_tokens:
            raise ValueError(
                f"Số mốc từ ({actual_tokens}) khác số token trong các beat ({expected_tokens})"
            )

    return word_times
