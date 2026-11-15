"""Cấu hình ứng dụng: đường dẫn, biến môi trường (.env) và thiết lập người dùng."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dotenv import load_dotenv, set_key

from core.style_presets import FALLBACK_MODELS, STYLE_PRESETS

APP_NAME = "DubStudio"
APP_VERSION = "3.0.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"
WORK_ROOT = ROOT_DIR / "temp_processing"
CACHE_DIR = ROOT_DIR / "cache"
OUTPUT_DIR = ROOT_DIR / "output"
DOWNLOAD_DIR = OUTPUT_DIR / "downloads"
SETTINGS_PATH = ROOT_DIR / "settings.json"

# Tên model có thể đổi bất cứ lúc nào, app tự thử lần lượt các model này nếu model chọn không còn.
DEFAULT_GEMINI_MODELS = FALLBACK_MODELS


def load_env() -> None:
    """Nạp file .env (bản cũ đọc GEMINI_API_KEY nhưng quên gọi bước này)."""
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
    pronoun_rules: str = ""   # ví dụ: "Tom gọi Jerry là 'mày', Jerry xưng 'tao'"
    glossary: str = ""
    # Giọng đọc
    tts_engine: str = "edge"            # edge | elevenlabs | tone (thử nghiệm, không gọi API)
    edge_voice: str = ""                # trống = giọng mặc định của ngôn ngữ đích
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_multilingual_v2"
    tts_workers: int = 3
    max_speed: float = 1.6              # tăng tốc tối đa khi câu đọc dài hơn khung thời gian
    # Trộn âm
    mix_mode: str = "duck"              # simple | duck | stems
    original_volume: float = 0.5
    ai_volume: float = 1.0
    bg_volume: float = 1.0              # chỉ dùng ở chế độ stems
    # Đầu ra
    burn_captions: bool = False
    caption_size: int = 18
    export_srt: bool = True
    keep_temp: bool = False

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "DubSettings":
        s = cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            names = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in names:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self, path: Path = SETTINGS_PATH) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
