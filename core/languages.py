"""Danh sách ngôn ngữ hỗ trợ, giọng Edge-TTS mặc định và tốc độ đọc ước tính."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str            # tên hiển thị (tiếng Việt)
    english: str         # tên đưa vào prompt cho LLM
    edge_voices: tuple   # (nữ, nam)
    cps: float           # số ký tự đọc được mỗi giây (ước lượng, để giới hạn độ dài câu dịch)

    @property
    def edge_voice(self) -> str:
        return self.edge_voices[0]


LANGUAGES = {
    l.code: l
    for l in [
        Language("vi", "Tiếng Việt", "Vietnamese", ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"), 15),
        Language("en", "Tiếng Anh", "English", ("en-US-AriaNeural", "en-US-GuyNeural"), 15),
        Language("zh", "Tiếng Trung", "Simplified Chinese", ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"), 5),
        Language("ja", "Tiếng Nhật", "Japanese", ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"), 7),
        Language("ko", "Tiếng Hàn", "Korean", ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"), 8),
        Language("th", "Tiếng Thái", "Thai", ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural"), 11),
        Language("id", "Tiếng Indonesia", "Indonesian", ("id-ID-GadisNeural", "id-ID-ArdiNeural"), 14),
        Language("es", "Tiếng Tây Ban Nha", "Spanish", ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"), 15),
        Language("fr", "Tiếng Pháp", "French", ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"), 15),
        Language("de", "Tiếng Đức", "German", ("de-DE-KatjaNeural", "de-DE-ConradNeural"), 14),
    ]
}


def get_language(code: str) -> Language:
    code = (code or "").split("-")[0].lower()
    if code in LANGUAGES:
        return LANGUAGES[code]
    # Ngôn ngữ lạ: vẫn chạy được, chỉ thiếu giọng mặc định
    return Language(code, code, code, ("en-US-AriaNeural", "en-US-GuyNeural"), 14)


def choices(include_auto: bool = False) -> list:
    items = [f"{c} - {l.name}" for c, l in LANGUAGES.items()]
    return (["auto - Tự nhận diện"] if include_auto else []) + items


def code_from_choice(text: str) -> str:
    return (text or "").split(" - ")[0].strip().lower()
