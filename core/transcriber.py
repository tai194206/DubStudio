"""Nhận diện giọng nói bằng faster-whisper (nhanh hơn Whisper gốc, chạy được trên CPU/GPU)."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

from .errors import MissingDependency
from .ffmpeg_utils import extract_audio, probe
from .reporter import Reporter
from .subtitles import Segment, split_long_segments

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3", "turbo"]


def transcribe(media_path, work_dir, *, model_size: str = "small", language: Optional[str] = None,
               max_segment_seconds: float = 10.0, reporter: Optional[Reporter] = None,
               device: str = "auto") -> Tuple[List[Segment], str]:
    """Trả về (danh sách câu, mã ngôn ngữ nhận diện được)."""
    reporter = reporter or Reporter()
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise MissingDependency(
            "Chưa cài faster-whisper (cần cho bước nhận diện giọng nói).\n"
            "Chạy: pip install faster-whisper\n"
            "Hoặc bỏ qua bước này bằng cách nạp sẵn file kịch bản/SRT."
        ) from e

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav = work_dir / "whisper_input.wav"
    reporter.progress(2, "Đang tách âm thanh...")
    extract_audio(media_path, wav, sample_rate=16000, channels=1)
    total = max(probe(wav).duration, 0.1)

    reporter.log(f"🧠 Tải model Whisper '{model_size}' (lần đầu sẽ tự tải về)...")
    model = WhisperModel(model_size, device=device, compute_type="auto")
    seg_iter, info = model.transcribe(
        str(wav), language=None if language in (None, "", "auto") else language,
        vad_filter=True, beam_size=5,
    )
    reporter.log(f"🌐 Ngôn ngữ nhận diện: {info.language} (độ tin cậy {info.language_probability:.0%})")

    segs: List[Segment] = []
    for s in seg_iter:
        reporter.check_cancel()
        text = s.text.strip()
        if text:
            segs.append(Segment(float(s.start), float(s.end), text, text))
        reporter.progress(5 + 95 * min(s.end / total, 1.0), f"Nhận diện giọng nói {min(s.end / total, 1.0):.0%}")

    segs = split_long_segments(segs, max_segment_seconds)
    reporter.log(f"✓ Nhận diện được {len(segs)} câu thoại", "ok")
    return segs, info.language
