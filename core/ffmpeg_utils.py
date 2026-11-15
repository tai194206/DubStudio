"""Tiện ích FFmpeg. Không cần ffprobe: thông tin media lấy từ đầu ra của `ffmpeg -i`."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import ROOT_DIR
from .errors import DubberError

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # không bật cửa sổ console trên Windows
_cached_ffmpeg: Optional[str] = None


def find_binary(name: str) -> Optional[str]:
    env = os.environ.get(f"{name.upper()}_PATH")
    if env and Path(env).exists():
        return env
    local = ROOT_DIR / (name + (".exe" if os.name == "nt" else ""))
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for p in (
            rf"C:\ffmpeg\bin\{name}.exe",
            rf"D:\ffmpeg\bin\{name}.exe",
            os.path.expandvars(rf"%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe"),
        ):
            if os.path.exists(p):
                return p
    if name == "ffmpeg":  # bản đóng gói sẵn trong pip (imageio-ffmpeg) nếu có
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
    return None


def get_ffmpeg() -> str:
    global _cached_ffmpeg
    if _cached_ffmpeg is None:
        path = find_binary("ffmpeg")
        if not path:
            raise DubberError(
                "Không tìm thấy FFmpeg. Cài FFmpeg (https://ffmpeg.org) và thêm vào PATH, "
                "hoặc đặt ffmpeg.exe cạnh run.py, hoặc chạy: pip install imageio-ffmpeg"
            )
        _cached_ffmpeg = path
    return _cached_ffmpeg


def run_ffmpeg(args, *, cwd=None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [get_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *[str(a) for a in args]]
    res = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd, creationflags=_NO_WINDOW,
    )
    if check and res.returncode != 0:
        raise DubberError(f"FFmpeg báo lỗi:\n{res.stderr.strip()[-900:]}")
    return res


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool


def probe(path) -> MediaInfo:
    res = subprocess.run(
        [get_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    text = res.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise DubberError(f"Không đọc được file media: {path}")
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    return MediaInfo(
        duration=duration,
        has_audio=bool(re.search(r"Stream #\d+:\d+.*: Audio:", text)),
        has_video=bool(re.search(r"Stream #\d+:\d+.*: Video:", text)),
    )


def extract_audio(src, dst, sample_rate: int = 16000, channels: int = 1) -> None:
    run_ffmpeg(["-i", src, "-vn", "-ac", channels, "-ar", sample_rate, "-c:a", "pcm_s16le", dst])


def to_wav(src, dst, sample_rate: int = 24000, channels: int = 1) -> None:
    run_ffmpeg(["-i", src, "-vn", "-ac", channels, "-ar", sample_rate, "-c:a", "pcm_s16le", dst])


def atempo_chain(speed: float) -> str:
    """`atempo` chỉ nhận 0.5-2.0 ở bản FFmpeg cũ, nên tự nối chuỗi khi vượt giới hạn."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def change_speed(src, dst, speed: float) -> None:
    """Đổi tốc độ nhưng giữ nguyên cao độ giọng (không bị méo tiếng)."""
    run_ffmpeg(["-i", src, "-filter:a", atempo_chain(speed), "-c:a", "pcm_s16le", dst])
