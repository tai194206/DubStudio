"""Module Adapter ElevenLabs /with-timestamps cho Story Mode 9:16.

Cung cấp các hàm:
- generate_voice_with_timestamps: Gọi API /with-timestamps của ElevenLabs cho toàn bộ kịch bản.
- chars_to_words: Gom mảng ký tự kèm timestamp thành mảng từ.
- align_scenes_with_words: Ánh xạ timestamp vào từng cảnh của Storyboard (tính duration_sec, start_time, end_time).
- export_karaoke_ass: Xuất file phụ đề Karaoke .ass chuẩn TikTok 9:16 với thẻ \\kf.

Tuân thủ nghiêm ngặt nguyên tắc không import tkinter hay gui/ trong core/.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import requests


def generate_voice_with_timestamps(
    api_key: str,
    voice_id: str,
    full_text: str,
    output_mp3_path: Union[str, Path],
    model_id: str = "eleven_multilingual_v2",
) -> Dict[str, Any]:
    """Gọi endpoint /with-timestamps của ElevenLabs 1 lần duy nhất cho toàn bộ kịch bản.
    
    Lưu file MP3 và trả về dictionary chứa alignment từng ký tự.
    """
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": full_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()

    # Giải mã và lưu file audio MP3
    audio_bytes = base64.b64decode(data["audio_base64"])
    out_p = Path(output_mp3_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "wb") as f:
        f.write(audio_bytes)

    return data.get("alignment", {})


def chars_to_words(alignment: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Gom mảng ký tự (characters) của ElevenLabs thành mảng từng từ (words)
    với start_time và end_time chính xác.
    """
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    words = []
    current_word = ""
    word_start = None
    word_end = None

    for ch, st, et in zip(chars, starts, ends):
        if ch.isspace():
            if current_word:
                words.append({
                    "word": current_word,
                    "start": word_start,
                    "end": word_end,
                })
                current_word = ""
                word_start = None
        else:
            if not current_word:
                word_start = st
            current_word += ch
            word_end = et

    if current_word:
        words.append({
            "word": current_word,
            "start": word_start,
            "end": word_end,
        })

    return words


def align_scenes_with_words(
    scenes: Sequence[Union[Dict[str, Any], Any]],
    words: List[Dict[str, Any]],
) -> Sequence[Union[Dict[str, Any], Any]]:
    """Ánh xạ danh sách từ đã có timestamp vào từng cảnh trong storyboard_v2.json.
    
    Tính ra start_time, end_time và duration_sec chính xác cho mỗi cảnh để cắt video.
    Hỗ trợ cả dict và StoryScene object.
    """
    word_idx = 0
    total_words = len(words)

    for i, scene in enumerate(scenes):
        is_dict = isinstance(scene, dict)
        speech_val = scene.get("speech", "") if is_dict else getattr(scene, "speech", "")
        # Đếm số từ trong câu thoại của cảnh hiện tại
        scene_tokens = [t for t in re.split(r"\s+", speech_val.strip()) if t]
        token_count = len(scene_tokens)

        if word_idx >= total_words or token_count == 0:
            prev_end = 0.0
            if i > 0:
                prev_s = scenes[i - 1]
                prev_end = prev_s.get("end_time", 0.0) if isinstance(prev_s, dict) else getattr(prev_s, "end_time", 0.0)
            st_val = prev_end
            et_val = st_val + 2.0
            dur_val = 2.0
            if is_dict:
                scene["start_time"] = round(st_val, 3)
                scene["end_time"] = round(et_val, 3)
                scene["duration_sec"] = dur_val
                scene["words"] = []
            else:
                scene.start_time = round(st_val, 3)
                scene.end_time = round(et_val, 3)
                scene.duration_sec = dur_val
                scene.words = []
            continue

        matched_words = words[word_idx : min(word_idx + token_count, total_words)]
        word_idx += len(matched_words)

        # Cảnh đầu tiên bắt đầu từ 0.0s, các cảnh sau nối khít từ end_time cảnh trước
        prev_end = 0.0
        if i > 0:
            prev_s = scenes[i - 1]
            prev_end = prev_s.get("end_time", 0.0) if isinstance(prev_s, dict) else getattr(prev_s, "end_time", 0.0)
        start_t = 0.0 if i == 0 else prev_end

        last_w = matched_words[-1]
        end_t = last_w.get("end", 0.0) if isinstance(last_w, dict) else getattr(last_w, "end", 0.0)

        # Nếu có khoảng nghỉ giữa 2 câu, cộng nửa khoảng nghỉ vào cảnh hiện tại cho tự nhiên
        if word_idx < total_words:
            next_w = words[word_idx]
            next_word_start = next_w.get("start", end_t) if isinstance(next_w, dict) else getattr(next_w, "start", end_t)
            end_t = round((end_t + next_word_start) / 2.0, 3)

        st_r = round(start_t, 3)
        et_r = round(end_t, 3)
        dur_r = round(max(0.5, et_r - st_r), 3)

        if is_dict:
            scene["start_time"] = st_r
            scene["end_time"] = et_r
            scene["duration_sec"] = dur_r
            scene["words"] = matched_words
        else:
            scene.start_time = st_r
            scene.end_time = et_r
            scene.duration_sec = dur_r
            scene.words = matched_words

    return scenes


def _format_ass_time(seconds: float) -> str:
    """Format giây thành chuỗi thời gian ASS H:MM:SS.cs (xử lý tràn centisec -> giây -> phút -> giờ)."""
    total_cs = max(0, int(round(seconds * 100)))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def export_karaoke_ass(
    aligned_scenes: Sequence[Union[Dict[str, Any], Any]],
    output_ass_path: Union[str, Path],
    max_words_per_line: int = 6,
) -> None:
    """Xuất file phụ đề .ass hiệu ứng tô màu Karaoke (\\kf) từ mốc thời gian ElevenLabs.
    
    Chia mỗi câu dài thành các cụm ngắn (mặc định 6 từ/dòng) chuẩn hiển thị dọc 9:16.
    Hỗ trợ cả danh sách dict và danh sách StoryScene.
    """
    ass_header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,68,&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,380,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogue_lines = []

    for scene in aligned_scenes:
        if isinstance(scene, dict):
            w_list = scene.get("words", [])
        else:
            w_list = getattr(scene, "words", [])

        if not w_list:
            continue

        # Chia thành các cụm ngắn dễ đọc trên màn hình 9:16
        for idx in range(0, len(w_list), max_words_per_line):
            chunk = w_list[idx : idx + max_words_per_line]
            first_w = chunk[0]
            last_w = chunk[-1]
            chunk_start = first_w.get("start", 0.0) if isinstance(first_w, dict) else getattr(first_w, "start", 0.0)
            chunk_end = last_w.get("end", 0.0) if isinstance(last_w, dict) else getattr(last_w, "end", 0.0)

            start_str = _format_ass_time(chunk_start)
            end_str = _format_ass_time(chunk_end)

            karaoke_parts = []
            cursor = chunk_start
            for w in chunk:
                w_word = w.get("word", "") if isinstance(w, dict) else getattr(w, "word", "")
                w_end = w.get("end", cursor) if isinstance(w, dict) else getattr(w, "end", cursor)
                # Tính thời lượng (đơn vị centiseconds = 1/100 giây) cho thẻ \kf
                dur_cs = max(5, int(round((w_end - cursor) * 100)))
                karaoke_parts.append(f"{{\\kf{dur_cs}}}{w_word}")
                cursor = w_end

            line_text = " ".join(karaoke_parts)
            dialogue_lines.append(
                f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{line_text}"
            )

    out_p = Path(output_ass_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w", encoding="utf-8") as f:
        f.write(ass_header + "\n".join(dialogue_lines) + "\n")
