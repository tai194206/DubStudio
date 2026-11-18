"""Các engine tạo giọng đọc và trích xuất mốc thời gian từng từ (WordTiming).

Mỗi engine cài đặt `synthesize(text, out_path) -> List[WordTiming]`.

Thay đổi so với bản trước:
  - Bỏ import trùng lặp và import edge_tts ở cấp module (import trễ bên trong hàm).
  - Bỏ hai hàm thừa `_stream_with_marks` và `synth_scene` (không được gọi ở đâu).
  - EdgeTTS: kiểm tra độ dài audio thật so với mốc từ cuối. Nếu audio ngắn hơn mốc cuối,
    coi là bị cắt cụt, xóa file tạm và thử lại. Không ghi file đích khi chưa kiểm tra xong.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Sequence, Union

from .config import DubSettings, Keys
from .errors import DubberError, MissingDependency, TTSError
from .languages import get_language
from .models import WordTiming

# Mốc từ cuối có thể lệch nhẹ so với độ dài mp3 do đệm cuối file. Dưới ngưỡng này mới coi là cắt cụt.
EDGE_END_TOLERANCE = 0.25


def _normalize_rate(rate: Union[str, float, int, None]) -> str:
    """Chuẩn hóa tốc độ về dạng Edge-TTS (+X% hoặc -X%). Số thực 0.05-4.0 được coi là hệ số (1.0 = +0%)."""
    if rate is None:
        return "+0%"
    if isinstance(rate, (int, float)):
        if 0.05 <= rate <= 4.0:
            pct = int(round((rate - 1.0) * 100))
        else:
            pct = int(round(rate))
        return f"{pct:+d}%"

    s = str(rate).strip()
    if not s:
        return "+0%"
    has_percent = s.endswith("%")
    clean = s.rstrip("%").strip()
    try:
        val = float(clean)
        if has_percent:
            pct = int(round(val))
        elif 0.05 <= val <= 4.0:
            pct = int(round((val - 1.0) * 100))
        else:
            pct = int(round(val))
        return f"{pct:+d}%"
    except ValueError:
        return "+0%"


def _normalize_voice(voice: Optional[str]) -> str:
    """Chuẩn hóa mã giọng Edge-TTS (loại bỏ khoảng trắng, nhãn hiển thị thừa)."""
    s = str(voice or "").strip()
    if not s:
        return "vi-VN-HoaiMyNeural"
    m = re.search(r"([a-zA-Z]{2,3}-[a-zA-Z]{2,4}-[a-zA-Z0-9]+)", s)
    if m:
        return m.group(1)
    return s


def _normalize_pitch(pitch: Union[str, float, int, None]) -> str:
    """Chuẩn hóa độ cao giọng Edge-TTS (+XHz hoặc -XHz)."""
    if pitch is None:
        return "+0Hz"
    s = str(pitch).strip()
    if not s:
        return "+0Hz"
    clean = s.rstrip("Hzhz").strip()
    try:
        val = int(round(float(clean)))
        return f"{val:+d}Hz"
    except ValueError:
        return "+0Hz"


def _normalize_volume(volume: Union[str, float, int, None]) -> str:
    """Chuẩn hóa âm lượng Edge-TTS (+X% hoặc -X%)."""
    if volume is None:
        return "+0%"
    s = str(volume).strip()
    if not s:
        return "+0%"
    clean = s.rstrip("%").strip()
    try:
        val = int(round(float(clean)))
        return f"{val:+d}%"
    except ValueError:
        return "+0%"


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


RETRYABLE_MARKERS = (
    "no audio was received",
    "truncated",
    "timeout",
    "connection",
    "503",
    "429",
    "too many requests",
    "rate limit",
    "server disconnected",
    "websocket",
    "unexpected end of stream",
    "clientpayloaderror",
    "cannot connect to host",
    "handshake",
    "reset by peer",
)


def _retry(fn, tries: int = 5, base_delay: float = 2.0):
    """Thử lại khi lỗi tạm thời: backoff 2s, 4s, 8s, 16s. Lỗi khác thì ném ngay."""
    last = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            msg = str(e).lower()
            transient = any(m in msg for m in RETRYABLE_MARKERS)
            if not transient or k == tries - 1:
                break
            time.sleep(base_delay * (2 ** k))
    raise TTSError(str(last) or type(last).__name__) from last


def check_edge_integrity(audio_seconds: float, words: Sequence[WordTiming]) -> None:
    """Chốt kiểm tra cắt cụt: audio phải dài ít nhất tới mốc từ cuối (trừ dung sai nhỏ).

    Ném TTSError có chữ 'truncated' để _retry thử lại. Không có mốc thì không kiểm tra được và bỏ qua.
    """
    if not words:
        return
    last_end = max(w.end for w in words)
    if audio_seconds + EDGE_END_TOLERANCE < last_end:
        raise TTSError(
            f"truncated audio: audio {audio_seconds:.2f}s nhưng mốc từ cuối {last_end:.2f}s"
        )


def _audio_seconds(path: Path) -> float:
    """Đo độ dài audio bằng ffprobe (qua core.ffmpeg_utils)."""
    from .ffmpeg_utils import probe

    return float(probe(path).duration)


def estimate_word_timings(text: str, seconds: float) -> List[WordTiming]:
    """Ước lượng mốc từ khi không có WordBoundary: chia độ dài audio theo số ký tự mỗi từ.

    Đây là ước lượng, không phải mốc thật. Chỉ dùng làm dự phòng và phải được đánh dấu rõ.
    """
    tokens = text.split()
    if not tokens or seconds <= 0:
        return []
    weights = [max(1, len(t)) for t in tokens]
    total = float(sum(weights))
    out: List[WordTiming] = []
    t = 0.0
    for tok, w in zip(tokens, weights):
        d = seconds * w / total
        out.append(WordTiming(word=tok, start=round(t, 4), end=round(t + d, 4)))
        t += d
    out[-1].end = round(seconds, 4)
    return out


class EdgeTTS(TTSEngine):
    """Miễn phí, nhiều giọng. Cần internet. Dịch vụ không chính thức: chỉ dùng thử nghiệm.

    Thứ tự thử:
      1. stream() với WordBoundary: có mốc từ thật. Thử STREAM_ATTEMPTS lần.
      2. Nếu vẫn lỗi, save(): có audio nhưng KHÔNG có mốc từ. Mốc được ước lượng và
         `last_timing_source` = "estimated" để bên gọi biết karaoke không chính xác.
    """
    name = "edge"
    STREAM_ATTEMPTS = 3
    SAVE_ATTEMPTS = 3

    def __init__(self, voice: str, rate: Union[str, float, int] = "+0%",
                 pitch: Union[str, float, int] = "+0Hz",
                 volume: Union[str, float, int] = "+0%"):
        self.voice = _normalize_voice(voice)
        self.rate = _normalize_rate(rate)
        self.pitch = _normalize_pitch(pitch)
        self.volume = _normalize_volume(volume)
        self.last_timing_source: str = ""   # "boundary" | "estimated"

    @property
    def voice_id(self) -> str:
        return self.voice

    @property
    def params(self) -> dict:
        return {"rate": self.rate, "pitch": self.pitch, "volume": self.volume}

    def _communicate(self, edge_tts, text: str):
        return edge_tts.Communicate(text=text, voice=self.voice, rate=self.rate,
                                    pitch=self.pitch, volume=self.volume, boundary="WordBoundary")

    def synthesize(self, text: str, out_path: Union[Path, str]) -> List[WordTiming]:
        try:
            import edge_tts
        except ImportError as e:
            raise MissingDependency("Thiếu edge-tts. Chạy: pip install edge-tts") from e

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        part = out_path.parent / (out_path.stem + ".part" + out_path.suffix)
        clean_text = re.sub(r"[\r\n\t]+", " ", text).strip() or "..."
        result: dict = {}

        async def stream_once():
            audio_chunks: List[bytes] = []
            words: List[WordTiming] = []
            async for chunk in self._communicate(edge_tts, clean_text).stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # Đơn vị offset/duration của Edge-TTS là 100 nanosecond: chia 1e7 để ra giây
                    start = chunk["offset"] / 1e7
                    dur = chunk["duration"] / 1e7
                    words.append(WordTiming(word=chunk["text"], start=round(start, 4),
                                            end=round(start + dur, 4)))
            result["audio"] = b"".join(audio_chunks)
            result["words"] = words

        def attempt_stream():
            result.clear()
            time.sleep(0.3)
            asyncio.run(stream_once())
            audio = result.get("audio", b"")
            if not audio:
                raise TTSError("No audio was received from Edge-TTS stream")
            part.write_bytes(audio)
            try:
                check_edge_integrity(_audio_seconds(part), result["words"])
            except TTSError:
                part.unlink(missing_ok=True)
                raise
            return result["words"]

        try:
            words = _retry(attempt_stream, tries=self.STREAM_ATTEMPTS, base_delay=2.0)
            os.replace(part, out_path)
            self.last_timing_source = "boundary"
            return words
        except TTSError:
            part.unlink(missing_ok=True)

        # Dự phòng: save() không có mốc từ nhưng vẫn lấy được audio trong nhiều trường hợp stream lỗi.
        async def save_once():
            await self._communicate(edge_tts, clean_text).save(str(part))

        def attempt_save():
            part.unlink(missing_ok=True)
            time.sleep(0.3)
            asyncio.run(save_once())
            if not part.exists() or part.stat().st_size == 0:
                raise TTSError("No audio was received from Edge-TTS save")

        try:
            _retry(attempt_save, tries=self.SAVE_ATTEMPTS, base_delay=2.0)
        except TTSError:
            part.unlink(missing_ok=True)
            raise TTSError("Edge-TTS thất bại cả đường stream lẫn save cho câu này") from None
        words = estimate_word_timings(clean_text, _audio_seconds(part))
        os.replace(part, out_path)
        self.last_timing_source = "estimated"
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
                words.append(WordTiming(word="".join(curr_chars),
                                        start=round(curr_start, 4), end=round(curr_end, 4)))
                curr_chars, curr_start, curr_end = [], None, None
        else:
            if curr_start is None:
                curr_start = s
            curr_end = e
            curr_chars.append(ch)

    if curr_chars and curr_start is not None and curr_end is not None:
        words.append(WordTiming(word="".join(curr_chars),
                                start=round(curr_start, 4), end=round(curr_end, 4)))
    return words


class ElevenLabsTTS(TTSEngine):
    """Giọng chất lượng cao. Dùng HTTP trực tiếp nên không phụ thuộc phiên bản SDK.
    [KIỂM THỬ THỦ CÔNG] Chưa được kiểm chứng với API thật trong test tự động."""
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
        return {"model_id": self.model_id, "stability": self.stability, "similarity": self.similarity}

    def synthesize(self, text: str, out_path: Path) -> List[WordTiming]:
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
                    "voice_settings": {"stability": self.stability, "similarity_boost": self.similarity},
                },
                timeout=90,
            )
            if r.status_code != 200:
                raise TTSError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            out_path.write_bytes(base64.b64decode(data.get("audio_base64", "")))
            alignment = data.get("alignment") or {}
            words.extend(_chars_to_words(
                alignment.get("characters", []),
                alignment.get("character_start_times_seconds", []),
                alignment.get("character_end_times_seconds", []),
            ))

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
    """Giọng giả (tiếng bíp) để thử đồng bộ thời gian mà không tốn API. Dùng trong test."""
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

        words_list = text.strip().split()
        if not words_list:
            return []
        word_dur = dur / len(words_list)
        return [WordTiming(word=w, start=round(idx * word_dur, 4), end=round((idx + 1) * word_dur, 4))
                for idx, w in enumerate(words_list)]


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
    """[CHƯA KIỂM CHỨNG TỰ ĐỘNG] Fallback căn mốc từ bằng faster-whisper, chỉ dùng khi engine không có mốc."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MissingDependency("Thiếu faster-whisper để căn mốc từ: pip install faster-whisper") from e

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True, language=language)
    words = []
    for s in segments:
        for w in (getattr(s, "words", None) or []):
            words.append(WordTiming(word=w.word.strip(), start=round(w.start, 4), end=round(w.end, 4)))
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
