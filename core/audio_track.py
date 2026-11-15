"""Tạo giọng cho từng câu, khớp thời gian rồi ghép thành một track âm thanh dài bằng video."""
from __future__ import annotations

import shutil
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from .cache import AudioCache, compute_cache_key
from .errors import DubberError
from .ffmpeg_utils import change_speed, to_wav
from .models import WordTiming, scale_word_timings
from .reporter import Reporter
from .subtitles import Segment
from .timefit import compute_speed, fit_window
from .tts import TTSEngine

SR = 24000  # tần số làm việc của track giọng (mono)


# ------------------------------------------------------------------ wav I/O
def read_wav(path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise DubberError(f"WAV không phải 16-bit: {path}")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            data = data.reshape(-1, w.getnchannels()).mean(axis=1)
    return data


def write_wav(path, samples: np.ndarray, sr: int = SR) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def trim_silence(x: np.ndarray, threshold: float = 0.012, keep_ms: float = 25.0) -> np.ndarray:
    """Cắt khoảng lặng đầu/cuối do TTS sinh ra để câu bắt đầu đúng mốc thời gian."""
    if x.size == 0:
        return x
    idx = np.flatnonzero(np.abs(x) > threshold)
    if idx.size == 0:
        return x[:0]
    keep = int(SR * keep_ms / 1000)
    return x[max(idx[0] - keep, 0): idx[-1] + keep + 1]


def fade_edges(x: np.ndarray, ms: float = 8.0) -> np.ndarray:
    n = min(int(SR * ms / 1000), x.size // 2)
    if n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        x = x.copy()
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


# ------------------------------------------------------------ tạo từng câu
@dataclass
class ClipResult:
    path: Optional[Path] = None
    speed: float = 1.0
    overflow: bool = False
    error: Optional[str] = None
    words: list[WordTiming] = field(default_factory=list)


def _render_one(i: int, seg: Segment, segments: Sequence[Segment], engine: TTSEngine,
                clip_dir: Path, max_speed: float, total_duration: float,
                cache: Optional[AudioCache] = None) -> ClipResult:
    text = seg.text.strip()
    if not text:
        return ClipResult(error="Câu rỗng")
    raw = clip_dir / f"raw_{i:04d}{engine.ext}"
    wav = clip_dir / f"clip_{i:04d}.wav"
    try:
        words: List[WordTiming] = []
        voice_id = getattr(engine, "voice_id", getattr(engine, "voice", ""))
        engine_params = getattr(engine, "params", {})

        cached = None
        key = None
        if cache is not None:
            key = compute_cache_key(engine.name, voice_id, text, engine_params)
            cached = cache.get(key, engine.ext)

        if cached is not None:
            cached_audio, words = cached
            shutil.copyfile(str(cached_audio), str(raw))
        else:
            words = engine.synthesize(text, raw) or []
            if cache is not None and key is not None:
                cache.put(key, raw, words, engine.ext)

        to_wav(raw, wav, SR, 1)
        x = trim_silence(read_wav(wav))
        if x.size == 0:
            return ClipResult(error="Giọng đọc rỗng")
        write_wav(wav, x)
        duration = x.size / SR
        window = fit_window(segments, i, total_duration)
        speed = compute_speed(duration, window, max_speed)
        if speed > 1.005:
            fast = clip_dir / f"fast_{i:04d}.wav"
            change_speed(wav, fast, speed)
            wav = fast
        overflow = duration / speed > window + 0.02
        return ClipResult(path=wav, speed=speed, overflow=overflow, words=words)
    except Exception as e:  # một câu lỗi (hết quota, rớt mạng) → để im lặng, không làm hỏng cả video
        return ClipResult(error=str(e) or type(e).__name__)


def render_clips(segments: Sequence[Segment], engine: TTSEngine, work_dir, *, max_speed: float,
                 total_duration: float, workers: int = 3,
                 reporter: Optional[Reporter] = None,
                 cache: Optional[AudioCache] = None) -> Dict[int, ClipResult]:
    reporter = reporter or Reporter()
    clip_dir = Path(work_dir) / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    results: Dict[int, ClipResult] = {}
    total = len(segments)
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = {pool.submit(_render_one, i, s, segments, engine, clip_dir, max_speed, total_duration, cache): i
                   for i, s in enumerate(segments)}
        for done, fut in enumerate(as_completed(futures), 1):
            reporter.check_cancel()
            i = futures[fut]
            results[i] = fut.result()
            if results[i].error:
                preview = segments[i].text[:50].replace("\n", " ")
                reporter.log(f"⚠️ Câu {i + 1} lỗi ({results[i].error}) → thay bằng im lặng: \"{preview}\"", "warn")
            reporter.progress(done * 100 / total, f"Tạo giọng đọc {done}/{total}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    sped = sum(1 for r in results.values() if r.speed > 1.005)
    over = sum(1 for r in results.values() if r.overflow)
    bad = sum(1 for r in results.values() if r.error)
    reporter.log(f"✓ {total} câu: {sped} câu được tăng tốc, {over} câu vẫn tràn khung, {bad} câu lỗi", "ok")
    if over:
        reporter.log("   💡 Câu tràn khung: nên rút gọn bản dịch hoặc tăng 'tốc độ tối đa'.", "warn")
    return results


# ------------------------------------------------------------------ ghép
def build_track(segments: Sequence[Segment], clips: Dict[int, ClipResult], total_duration: float,
                out_path) -> Path:
    """Đặt từng câu vào đúng mốc bắt đầu trên một track im lặng dài bằng video.

    Nếu câu trước tràn sang câu sau thì câu sau được đẩy lùi, không bao giờ chồng tiếng lên nhau.
    """
    total = int(round(total_duration * SR))
    buf = np.zeros(total, dtype=np.float32)
    cursor = 0
    for i, seg in enumerate(segments):
        r = clips.get(i)
        if not r or not r.path:
            continue
        x = fade_edges(read_wav(r.path))
        start = max(int(round(seg.start * SR)), cursor)
        if start >= total:
            break
        end = min(start + x.size, total)  # không vượt quá độ dài video
        buf[start:end] = x[: end - start]

        # Cập nhật mốc từ WordTiming thực tế sau khi co giãn tốc độ và dịch chuyển theo timeline
        actual_offset = start / SR
        if r.words:
            seg.words = scale_word_timings(r.words, speed=r.speed, offset=actual_offset)

        cursor = end
    out_path = Path(out_path)
    write_wav(out_path, buf)
    return out_path
