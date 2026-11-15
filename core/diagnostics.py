"""Kiểm tra môi trường: thư viện/phần mềm nào đã có, thiếu thì cài thế nào."""
from __future__ import annotations

import importlib.util
from typing import List, Tuple

from .ffmpeg_utils import find_binary


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def check_environment() -> List[Tuple[str, bool, str]]:
    """Trả về [(tên, đã có?, ghi chú/cách cài)]."""
    ffmpeg = find_binary("ffmpeg")
    return [
        ("FFmpeg", bool(ffmpeg), ffmpeg or "Bắt buộc. Cài từ ffmpeg.org hoặc: pip install imageio-ffmpeg"),
        ("numpy", _has("numpy"), "Bắt buộc: pip install numpy"),
        ("edge-tts", _has("edge_tts"), "Giọng miễn phí: pip install edge-tts"),
        ("google-genai", _has("google.genai"), "Dịch bằng Gemini: pip install google-genai"),
        ("deep-translator", _has("deep_translator"), "Dịch dự phòng: pip install deep-translator"),
        ("yt-dlp", _has("yt_dlp"), "Tải video: pip install yt-dlp"),
        ("faster-whisper", _has("faster_whisper"), "Tùy chọn, tự nhận diện giọng nói: pip install faster-whisper"),
        ("demucs", _has("demucs"), "Tùy chọn, tách nhạc nền: pip install demucs"),
    ]
