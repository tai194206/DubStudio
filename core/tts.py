"""Các engine tạo giọng đọc và trích xuất mốc thời gian từng từ (WordTiming).

Mỗi engine cài đặt `synthesize(text, out_path) -> List[WordTiming]`.
"""
from __future__ import annotations

import asyncio
import math
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Sequence

from .config import DubSettings, Keys
from .errors import DubberError, MissingDependency, TTSError
from .languages import get_language
from .models import WordTiming


class TTSEngine(ABC):
    name = "base"
    ext = ".mp3"

    @property
    def voice_id(self) -> str:
        return ""

    @property
    def params(self) -> dict:
        return {}

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
        """Tạo audio tại out_path và trả về danh sách WordTiming nếu hỗ trợ."""
        ...


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

    def __init__(self, voice: str, rate: str = "+0%", pitch: str = "+0Hz", volume: str = "+0%"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self.volume = volume

    @property
    def voice_id(self) -> str:
        return self.voice

    @property
    def params(self) -> dict:
        return {"rate": self.rate, "pitch": self.pitch, "volume": self.volume}

    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
        try:
            import edge_tts
        except ImportError as e:
            raise MissingDependency("Thiếu edge-tts. Chạy: pip install edge-tts") from e

        words: List[WordTiming] = []
        audio_chunks: List[bytes] = []

        async def go():
            communicate = edge_tts.Communicate(
                text,
                self.voice,
                rate=self.rate,
                pitch=self.pitch,
                volume=self.volume,
                boundary="WordBoundary",
            )
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # Quy đổi từ đơn vị 100ns sang giây (chia cho 1e7)
                    start_sec = chunk["offset"] / 1e7
                    dur_sec = chunk["duration"] / 1e7
                    words.append(WordTiming(
                        word=chunk["text"],
                        start=round(start_sec, 4),
                        end=round(start_sec + dur_sec, 4),
                    ))

        _retry(lambda: asyncio.run(go()))
        out_path.write_bytes(b"".join(audio_chunks))
        return words


def _chars_to_words(chars: Sequence[str], starts: Sequence[float], ends: Sequence[float]) -> List[WordTiming]:
    """Gom các ký tự kèm timestamp của ElevenLabs thành từng từ phân cách bởi khoảng trắng."""
    words: List[WordTiming] = []
    curr_chars: List[str] = []
    curr_start: Optional[float] = None
    curr_end: Optional[float] = None

    for ch, s, e in zip(chars, starts, ends):
        if ch.isspace():
            if curr_chars and curr_start is not None and curr_end is not None:
                words.append(WordTiming(
                    word="".join(curr_chars),
                    start=round(curr_start, 4),
                    end=round(curr_end, 4),
                ))
                curr_chars = []
                curr_start = None
                curr_end = None
        else:
            if curr_start is None:
                curr_start = s
            curr_end = e
            curr_chars.append(ch)

    if curr_chars and curr_start is not None and curr_end is not None:
        words.append(WordTiming(
            word="".join(curr_chars),
            start=round(curr_start, 4),
            end=round(curr_end, 4),
        ))
    return words


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
        self.key, self.voice_id_str, self.model_id = api_key, voice_id, model_id
        self.stability, self.similarity = stability, similarity

    @property
    def voice_id(self) -> str:
        return self.voice_id_str

    @property
    def params(self) -> dict:
        return {
            "model_id": self.model_id,
            "stability": self.stability,
            "similarity": self.similarity,
        }

    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
        """[KIỂM THỬ THỦ CÔNG - Manual Test Only] Gọi endpoint /with-timestamps của ElevenLabs."""
        import base64
        import requests

        words: List[WordTiming] = []

        def go():
            r = requests.post(
                f"{self.API}/text-to-speech/{self.voice_id_str}/with-timestamps",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": self.model_id,
                    "voice_settings": {
                        "stability": self.stability,
                        "similarity_boost": self.similarity,
                    },
                },
                timeout=90,
            )
            if r.status_code != 200:
                raise TTSError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            audio_b64 = data.get("audio_base64", "")
            out_path.write_bytes(base64.b64decode(audio_b64))

            alignment = data.get("alignment") or {}
            chars = alignment.get("characters", [])
            starts = alignment.get("character_start_times_seconds", [])
            ends = alignment.get("character_end_times_seconds", [])
            words.extend(_chars_to_words(chars, starts, ends))

        _retry(go)
        return words


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

    def __init__(self, voice_id: str = "tone_beep"):
        self._voice_id = voice_id

    @property
    def voice_id(self) -> str:
        return self._voice_id

    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
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

        # Tự sinh WordTiming chia đều theo số từ và thời lượng câu
        words_list = text.strip().split()
        if not words_list:
            return []
        word_dur = dur / len(words_list)
        return [
            WordTiming(
                word=w,
                start=round(idx * word_dur, 4),
                end=round((idx + 1) * word_dur, 4),
            )
            for idx, w in enumerate(words_list)
        ]


class FallbackTTS(TTSEngine):
    """Thử lần lượt nhiều engine (vd ElevenLabs hết quota thì chuyển sang Edge)."""

    def __init__(self, engines: List[TTSEngine], on_fallback=None):
        self.engines, self.on_fallback = engines, on_fallback
        self.name = "+".join(e.name for e in engines)
        self.ext = ".mp3"

    @property
    def voice_id(self) -> str:
        return self.engines[0].voice_id if self.engines else ""

    @property
    def params(self) -> dict:
        return self.engines[0].params if self.engines else {}

    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
        last: Optional[Exception] = None
        for k, eng in enumerate(self.engines):
            target = out_path.with_suffix(eng.ext)
            try:
                words = eng.synthesize(text, target) or []
                if target != out_path:
                    target.replace(out_path)
                return words
            except Exception as e:
                last = e
                if self.on_fallback and k + 1 < len(self.engines):
                    self.on_fallback(eng.name, e)
        raise TTSError(str(last))


def align_words_with_whisper(audio_path: Path, text: str, language: Optional[str] = None) -> List[WordTiming]:
    """[CHƯA KIỂM CHỨNG TỰ ĐỘNG - Manual Test Only] Fallback căn mốc từ bằng faster-whisper.
    
    Chỉ dùng khi TTS engine không trả timestamp.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MissingDependency("Thiếu faster-whisper để căn mốc từ: pip install faster-whisper") from e

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True, language=language)
    words = []
    for s in segments:
        if hasattr(s, "words") and s.words:
            for w in s.words:
                words.append(WordTiming(
                    word=w.word.strip(),
                    start=round(w.start, 4),
                    end=round(w.end, 4),
                ))
    return words


def make_engine(settings: DubSettings, keys: Keys, on_fallback=None) -> TTSEngine:
    lang = get_language(settings.target_lang)
    edge = EdgeTTS(settings.edge_voice or lang.edge_voice)
    if settings.tts_engine == "tone":
        return ToneTTS()
    if settings.tts_engine == "elevenlabs":
        eleven = ElevenLabsTTS(keys.elevenlabs, settings.elevenlabs_voice_id, settings.elevenlabs_model)
        return FallbackTTS([eleven, edge], on_fallback)
    return edge
