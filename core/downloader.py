"""Tải video bằng yt-dlp. Chỉ dùng với video bạn sở hữu hoặc được phép sử dụng."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from .errors import DubberError, MissingDependency
from .ffmpeg_utils import get_ffmpeg
from .reporter import Reporter

_URL = re.compile(r"https?://\S+")


def is_url(text: str) -> bool:
    return bool(_URL.match((text or "").strip()))


def _yt_dlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError as e:
        raise MissingDependency("Thiếu yt-dlp. Chạy: pip install yt-dlp") from e


def parse_url_list(text: str) -> List[dict]:
    """Mỗi dòng một link. Chấp nhận cả định dạng cũ `1. Tên video - https://...`."""
    items = []
    for line in text.splitlines():
        m = _URL.search(line)
        if not m:
            continue
        title = re.sub(r"^\s*\d+[.)]\s*", "", line[: m.start()]).strip(" -–\t")
        items.append({"title": title, "url": m.group(0).strip()})
    return items


def list_entries(url: str) -> List[dict]:
    """Mở rộng playlist/collection thành danh sách video (thay cho bản Selenium cũ)."""
    yt_dlp = _yt_dlp()
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        return []
    if "entries" not in info:
        return [{"title": info.get("title", ""), "url": info.get("webpage_url") or url}]
    out = []
    for e in info["entries"] or []:
        if not e:
            continue
        u = e.get("url") or e.get("webpage_url")
        if u:
            out.append({"title": e.get("title", ""), "url": u})
    return out


def download_video(url: str, out_dir, reporter: Optional[Reporter] = None,
                   max_height: int = 1080, filename: Optional[str] = None) -> Path:
    yt_dlp = _yt_dlp()
    reporter = reporter or Reporter()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    template = (filename or "%(title).120B [%(id)s]") + ".%(ext)s"

    def hook(d):
        reporter.check_cancel()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                reporter.progress(d.get("downloaded_bytes", 0) * 100 / total, "Đang tải video...")

    opts = {
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / template),
        "windowsfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": get_ffmpeg(),
        "progress_hooks": [hook],
    }
    reporter.log(f"⬇️ Đang tải: {url}")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            downloads = info.get("requested_downloads") or []
            path = downloads[0]["filepath"] if downloads else ydl.prepare_filename(info)
    except Exception as e:
        if type(e).__name__ == "Cancelled":
            raise
        raise DubberError(f"Tải video thất bại: {e}") from e
    reporter.progress(100, "Đã tải xong")
    reporter.log(f"✓ Đã lưu: {path}", "ok")
    return Path(path)
