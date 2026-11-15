"""Hệ thống cache audio thô và metadata WordTiming của DubStudio.

Khóa cache băm SHA-256 từ thông tin engine, voice, tham số phát âm và nội dung văn bản.
Không đưa speed_rate vào khóa cache vì audio lưu thô; tăng tốc áp dụng sau.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from .config import ROOT_DIR
from .ffmpeg_utils import probe
from .models import WordTiming

CACHE_SCHEMA_VERSION = "1.0"
DEFAULT_CACHE_DIR = ROOT_DIR / "cache"


def compute_cache_key(
    engine: str,
    voice_id: str,
    text: str,
    engine_params: Optional[dict] = None,
) -> str:
    """Tạo khóa hash SHA-256 từ schema version, engine, voice, engine_params và text."""
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "engine": str(engine).strip().lower(),
        "voice_id": str(voice_id).strip(),
        "engine_params": engine_params or {},
        "text": str(text).strip(),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AudioCache:
    """Quản lý đọc/ghi cache nguyên tử cho audio thô và WordTiming."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, ext: str = ".mp3") -> Optional[Tuple[Path, List[WordTiming]]]:
        """Đọc audio và WordTiming từ cache nếu hợp lệ.
        
        Trả về (audio_path, words) nếu cache hit.
        Trả về None nếu cache miss hoặc file hỏng/rỗng.
        """
        audio_path = self.cache_dir / f"{key}{ext}"
        meta_path = self.cache_dir / f"{key}.json"

        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            return None

        # Kiểm tra tính toàn vẹn của file audio qua ffprobe
        try:
            info = probe(audio_path)
            if not info.has_audio or info.duration <= 0:
                return None
        except Exception:
            return None

        words: List[WordTiming] = []
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
                raw_words = data.get("words", [])
                words = [WordTiming.from_dict(w) for w in raw_words]
            except Exception:
                words = []

        return audio_path, words

    def put(
        self,
        key: str,
        audio_source: Path,
        words: Optional[List[WordTiming]] = None,
        ext: str = ".mp3",
    ) -> Tuple[Path, Path]:
        """Ghi nguyên tử audio và metadata WordTiming vào cache bằng file .tmp và os.replace()."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:8]

        target_audio = self.cache_dir / f"{key}{ext}"
        tmp_audio = self.cache_dir / f"{key}.tmp.{token}{ext}"

        target_meta = self.cache_dir / f"{key}.json"
        tmp_meta = self.cache_dir / f"{key}.tmp.{token}.json"

        # 1. Ghi audio nguyên tử
        shutil.copyfile(str(audio_source), str(tmp_audio))
        os.replace(str(tmp_audio), str(target_audio))

        # 2. Ghi metadata nguyên tử
        meta_data = {
            "schema_version": CACHE_SCHEMA_VERSION,
            "words": [w.to_dict() for w in (words or [])],
        }
        tmp_meta.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp_meta), str(target_meta))

        return target_audio, target_meta

    def clear(self) -> int:
        """Xóa toàn bộ nội dung trong thư mục cache, ngoại trừ .gitkeep."""
        deleted = 0
        if not self.cache_dir.exists():
            return 0

        for item in self.cache_dir.iterdir():
            if item.name == ".gitkeep":
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                deleted += 1
            except Exception:
                pass
        return deleted
