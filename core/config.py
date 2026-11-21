"""Cấu hình ứng dụng: đường dẫn, biến môi trường (.env) và thiết lập người dùng."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv, set_key

from core.style_presets import FALLBACK_MODELS, STYLE_PRESETS

APP_NAME = "DubStudio"
APP_VERSION = "3.2.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
WORK_ROOT = ROOT_DIR / "temp_processing"
CACHE_DIR = ROOT_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "output"
DOWNLOAD_DIR = OUTPUT_DIR / "downloads"
SETTINGS_PATH = ROOT_DIR / "settings.json"

DEFAULT_GEMINI_MODELS = FALLBACK_MODELS

VALID_TRANSITIONS = ("none", "fade", "dissolve", "smoothleft", "wipeleft")
VALID_WATERMARK_POS = ("top_left", "top_right", "bottom_left")
VALID_ANTI_AI_LEVELS = ("off", "light", "medium", "strong")


def load_env() -> None:
    """Nạp file .env."""
    load_dotenv(ENV_PATH, override=True)


def save_key(name: str, value: str) -> None:
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), name, value, quote_mode="never")
    os.environ[name] = value


@dataclass
class Keys:
    gemini: str = ""
    elevenlabs: str = ""

    @classmethod
    def from_env(cls) -> "Keys":
        return cls(
            gemini=os.getenv("GEMINI_API_KEY", "").strip(),
            elevenlabs=os.getenv("ELEVENLABS_API_KEY", "").strip(),
        )


@dataclass
class DubSettings:
    # Ngôn ngữ
    source_lang: str = "auto"
    target_lang: str = "vi"
    # Nhận diện giọng nói
    whisper_model: str = "small"
    max_segment_seconds: float = 10.0
    # Dịch
    gemini_model: str = DEFAULT_GEMINI_MODELS[0]
    task_profile: str = "Cân bằng (hằng ngày)"
    topic: str = ""
    style_preset: str = "Tự nhiên"
    style: str = STYLE_PRESETS["Tự nhiên"]
    pronoun_rules: str = ""
    glossary: str = ""
    # Giọng đọc
    tts_engine: str = "edge"            # edge | elevenlabs | tone
    edge_voice: str = ""                # trống = giọng mặc định của ngôn ngữ đích
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    tts_workers: int = 1
    max_speed: float = 1.6
    # Trộn âm (Dub Studio)
    mix_mode: str = "duck"              # simple | duck | stems
    original_volume: float = 0.5
    ai_volume: float = 1.0
    bg_volume: float = 1.0
    # Đầu ra Dub Studio
    burn_captions: bool = False
    caption_size: int = 18
    export_srt: bool = True
    keep_temp: bool = False

    # Cấu hình Story Studio 9:16 (v3.2)
    story_shot_mode: str = "planned"            # planned | scene
    story_transition: str = "fade"              # none | fade | dissolve | smoothleft | wipeleft
    story_transition_duration: float = 0.25     # giây (0.15 - 0.50)
    story_smart_clip_fit: bool = True           # tự kéo giãn nhẹ clip 5s cho khít câu thoại, tránh lặp giật
    story_subtitle_style: str = "pop_2words"    # pop_2words | highlight_3words | karaoke_line
    story_burn_subs: bool = True
    story_show_scene_titles: bool = True        # hiện tiêu đề on_screen_text phía trên
    story_sub_font: str = "Arial"
    story_sub_font_size: int = 72               # cỡ chữ chế độ nảy 1-2 chữ
    story_sub_margin_v: int = 400               # khoảng cách từ đáy (vùng an toàn TikTok)
    story_sub_color: str = "#FFE000"            # màu chữ chính (vàng rực)
    story_watermark_enabled: bool = True
    story_watermark_text: str = "Dòng Máu Sử Việt"
    story_watermark_pos: str = "top_left"       # top_left | top_right | bottom_left
    story_watermark_opacity: int = 65           # 20..100 (%)
    story_cinematic_grade: bool = True
    story_loudness_norm: bool = True
    story_anti_ai: bool = True
    story_anti_ai_level: str = "medium"         # off | light | medium | strong
    story_anti_ai_grain: bool = True            # hạt phim & tối góc nhẹ (Film Grain / Vignette)
    story_codec: str = "h264"
    story_bgm_volume: float = 0.28
    story_sfx_volume: float = 0.85
    story_speed: float = 1.0

    def story_render_kwargs(self) -> Dict[str, Any]:
        """Xuất từ điển tham số chuẩn để truyền thẳng vào StoryPipeline.render_story(**kwargs)."""
        trans = self.story_transition if self.story_transition in VALID_TRANSITIONS else "none"
        wm_pos = self.story_watermark_pos if self.story_watermark_pos in VALID_WATERMARK_POS else "top_left"
        raw_aai = str(self.story_anti_ai_level or "").strip().lower()
        if raw_aai not in VALID_ANTI_AI_LEVELS:
            raw_aai = "medium" if self.story_anti_ai else "off"
        elif not self.story_anti_ai and raw_aai == "medium":
            raw_aai = "off"
        return {
            "burn_subtitles": bool(self.story_burn_subs),
            "codec": self.story_codec or "h264",
            "bgm_volume": max(0.0, min(1.0, float(self.story_bgm_volume))),
            "sfx_volume": max(0.0, min(1.5, float(self.story_sfx_volume))),
            "shot_mode": self.story_shot_mode if self.story_shot_mode in ("scene", "planned") else "scene",
            "cinematic_grade": bool(self.story_cinematic_grade),
            "loudness_norm": bool(self.story_loudness_norm),
            "subtitle_style": self.story_subtitle_style if self.story_subtitle_style in ("pop_2words", "highlight_3words", "karaoke_line") else "pop_2words",
            "watermark_text": self.story_watermark_text.strip() if self.story_watermark_enabled else None,
            "watermark_pos": wm_pos,
            "watermark_opacity": max(20, min(100, int(self.story_watermark_opacity))),
            "anti_ai": raw_aai != "off",
            "anti_ai_level": raw_aai,
            "anti_ai_grain": bool(self.story_anti_ai_grain),
            "transition": trans,
            "transition_duration": max(0.1, min(0.6, float(self.story_transition_duration))),
            "smart_clip_fit": bool(self.story_smart_clip_fit),
            "show_scene_titles": bool(self.story_show_scene_titles),
            "sub_font": self.story_sub_font.strip() or "Arial",
            "sub_font_size": max(32, min(110, int(self.story_sub_font_size))),
            "sub_margin_v": max(150, min(700, int(self.story_sub_margin_v))),
            "sub_color": self.story_sub_color.strip() or "#FFE000",
            "speed": max(0.5, min(2.0, float(self.story_speed))),
        }

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "DubSettings":
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            names = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in names:
                    setattr(s, k, v)
            # Đồng bộ ngược cho file settings.json cũ chỉ có story_anti_ai (bool)
            if "story_anti_ai_level" not in data and "story_anti_ai" in data:
                s.story_anti_ai_level = "medium" if bool(data["story_anti_ai"]) else "off"
            elif s.story_anti_ai_level not in VALID_ANTI_AI_LEVELS:
                s.story_anti_ai_level = "medium" if bool(s.story_anti_ai) else "off"
            s.story_anti_ai = s.story_anti_ai_level != "off"
        except (OSError, ValueError):
            pass
        return s

    def save(self, path: Path = SETTINGS_PATH) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")