"""Các engine tạo giọng đọc. Mỗi engine chỉ cần cài đặt `synthesize(text, out_path)`."""
from __future__ import annotations

import asyncio
import math
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from .config import DubSettings, Keys
from .errors import DubberError, MissingDependency, TTSError
from .languages import get_language


class TTSEngine(ABC):
    name = "base"
    ext = ".mp3"

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> None: ...


def _retry(fn, tries: int = 3, delay: float = 1.5):
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            if k < tries - 1:
                time.sleep(delay * (k + 1))
    raise TTSError(str(last) or type(last).__name__) from last


class EdgeTTS(TTSEngine):
    """Miễn phí, nhiều giọng. Cần internet."""
    name = "edge"

    def __init__(self, voice: str, rate: str = "+0%"):
        self.voice, self.rate = voice, rate

    def synthesize(self, text: str, out_path: Path) -> None:
        try:
            import edge_tts
        except ImportError as e:
            raise MissingDependency("Thiếu edge-tts. Chạy: pip install edge-tts") from e

        async def go():
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(str(out_path))

        # asyncio.run tạo event loop riêng cho từng luồng nên chạy song song được, không cần nest_asyncio
        _retry(lambda: asyncio.run(go()))


class ElevenLabsTTS(TTSEngine):
    """Giọng chất lượng cao, hỗ trợ giọng nhân bản. Dùng HTTP trực tiếp nên không phụ thuộc phiên bản SDK."""
    name = "elevenlabs"
    API = "https://api.elevenlabs.io/v1"

    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_multilingual_v2",
                 stability: float = 0.5, similarity: float = 0.75):
        if not api_key:
            raise DubberError("Chưa có ELEVENLABS_API_KEY")
        if not voice_id:
            raise DubberError("Chưa chọn Voice ID của ElevenLabs")
        self.key, self.voice_id, self.model_id = api_key, voice_id, model_id
        self.stability, self.similarity = stability, similarity

    def synthesize(self, text: str, out_path: Path) -> None:
        import requests

        def go():
            r = requests.post(
                f"{self.API}/text-to-speech/{self.voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                json={"text": text, "model_id": self.model_id,
                      "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity}},
                timeout=90,
            )
            if r.status_code != 200:
                raise TTSError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
            out_path.write_bytes(r.content)

        _retry(go)


def list_elevenlabs_voices(api_key: str) -> List[dict]:
    import requests
    r = requests.get(f"{ElevenLabsTTS.API}/voices", headers={"xi-api-key": api_key}, timeout=30)
    if r.status_code != 200:
        raise DubberError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
    return [{"id": v["voice_id"], "name": v.get("name", "")} for v in r.json().get("voices", [])]


def clone_elevenlabs_voice(api_key: str, name: str, sample_paths: List[str]) -> str:
    """Nhân bản giọng từ file mẫu. CHỈ dùng với giọng của chính bạn hoặc người đã đồng ý."""
    import requests
    files = [("files", (Path(p).name, open(p, "rb"))) for p in sample_paths]
    try:
        r = requests.post(f"{ElevenLabsTTS.API}/voices/add", headers={"xi-api-key": api_key},
                          data={"name": name}, files=files, timeout=180)
    finally:
        for _, (_, fh) in files:
            fh.close()
    if r.status_code != 200:
        raise DubberError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["voice_id"]


class ToneTTS(TTSEngine):
    """Giọng giả (tiếng bíp) để thử đồng bộ thời gian mà không tốn API. Dùng trong test và chế độ thử nghiệm."""
    name = "tone"
    ext = ".wav"

    def synthesize(self, text: str, out_path: Path) -> None:
        sr = 24000
        dur = max(0.4, len(text) / 14.0)
        n = int(sr * dur)
        frames = bytearray()
        for i in range(n):
            v = int(9000 * math.sin(2 * math.pi * 330 * i / sr))
            frames += v.to_bytes(2, "little", signed=True)
        with wave.open(str(out_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(frames))


class FallbackTTS(TTSEngine):
    """Thử lần lượt nhiều engine (vd ElevenLabs hết quota thì chuyển sang Edge)."""

    def __init__(self, engines: List[TTSEngine], on_fallback=None):
        self.engines, self.on_fallback = engines, on_fallback
        self.name = "+".join(e.name for e in engines)
        self.ext = ".mp3"

    def synthesize(self, text: str, out_path: Path) -> None:
        last: Optional[Exception] = None
        for k, eng in enumerate(self.engines):
            target = out_path.with_suffix(eng.ext)
            try:
                eng.synthesize(text, target)
                if target != out_path:
                    target.replace(out_path)
                return
            except Exception as e:
                last = e
                if self.on_fallback and k + 1 < len(self.engines):
                    self.on_fallback(eng.name, e)
        raise TTSError(str(last))


def make_engine(settings: DubSettings, keys: Keys, on_fallback=None) -> TTSEngine:
    lang = get_language(settings.target_lang)
    edge = EdgeTTS(settings.edge_voice or lang.edge_voice)
    if settings.tts_engine == "tone":
        return ToneTTS()
    if settings.tts_engine == "elevenlabs":
        eleven = ElevenLabsTTS(keys.elevenlabs, settings.elevenlabs_voice_id, settings.elevenlabs_model)
        return FallbackTTS([eleven, edge], on_fallback)
    return edge
