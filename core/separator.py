"""Tách giọng nói gốc khỏi nhạc nền/hiệu ứng bằng Demucs (tùy chọn, nặng, nên có GPU).

Kết quả `no_vocals.wav` giữ nguyên nhạc và tiếng động, chỉ bỏ lời thoại, nên giọng AI không bị
chồng lên giọng gốc.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Optional

from .errors import DubberError, MissingDependency
from .ffmpeg_utils import extract_audio
from .reporter import Reporter


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def separate_background(media_path, work_dir, reporter: Optional[Reporter] = None) -> Path:
    reporter = reporter or Reporter()
    if not demucs_available():
        raise MissingDependency("Chưa cài demucs. Chạy: pip install demucs (cần cài kèm PyTorch)")
    work_dir = Path(work_dir)
    wav = work_dir / "original_audio.wav"
    out_dir = work_dir / "demucs"
    reporter.progress(5, "Đang tách âm thanh gốc...")
    extract_audio(media_path, wav, sample_rate=44100, channels=2)
    reporter.log("🎼 Đang tách nhạc nền khỏi giọng nói (có thể mất vài phút)...")
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs", "-o", str(out_dir), str(wav)]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    reporter.check_cancel()
    found = list(out_dir.rglob("no_vocals.wav"))
    if res.returncode != 0 or not found:
        raise DubberError(f"Demucs thất bại: {(res.stderr or res.stdout).strip()[-500:]}")
    reporter.progress(100, "Đã tách xong nhạc nền")
    return found[0]
