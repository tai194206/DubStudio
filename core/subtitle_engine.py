r"""Engine sinh phụ đề động Karaoke định dạng .ass và burn vào video bằng FFmpeg.

Sử dụng pysubs2 với tag \kf hoặc \k để đổi màu highlight từng từ đồng bộ với giọng đọc.
Đảm bảo luôn lưu file UTF-8 tường minh và dùng đường dẫn tương đối khi burn để tránh lỗi Windows.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional, Sequence

import pysubs2
from pysubs2 import Alignment, Color, SSAEvent, SSAFile, SSAStyle

from .errors import DubberError
from .ffmpeg_utils import run_ffmpeg
from .models import Segment, WordTiming
from .reporter import Reporter


def create_karaoke_style(
    font_name: str = "Arial",
    font_size: int = 24,
    primary_color: Optional[Color] = None,
    secondary_color: Optional[Color] = None,
    outline_color: Optional[Color] = None,
    outline_width: float = 2.5,
    shadow_offset: float = 1.5,
    margin_v: int = 35,
) -> SSAStyle:
    """Tạo kiểu phụ đề Karaoke với màu highlight nổi bật, viền chữ và bóng đổ rõ nét."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    # PrimaryColour: Màu chữ sau khi highlight (khi đọc tới)
    style.primarycolor = primary_color or Color(255, 230, 0, 0)       # Vàng tươi
    # SecondaryColour: Màu chữ trước khi highlight (chờ đọc)
    style.secondarycolor = secondary_color or Color(220, 220, 220, 0) # Xám bạc / Trắng nhạt
    # OutlineColour: Viền đen chống lẫn vào nền video
    style.outlinecolor = outline_color or Color(0, 0, 0, 0)
    # BackColour: Bóng đổ bán trong suốt
    style.backcolor = Color(0, 0, 0, 128)

    style.borderstyle = 1  # Outline + drop shadow
    style.outline = outline_width
    style.shadow = shadow_offset
    style.alignment = Alignment.BOTTOM_CENTER
    style.marginv = margin_v
    return style


def generate_karaoke_ass(
    segments: Sequence[Segment],
    out_path: Path | str,
    *,
    font_size: int = 24,
    font_name: str = "Arial",
    use_kf: bool = True,
) -> Path:
    """Sinh file .ass karaoke từ danh sách Segment có chứa WordTiming.
    
    Lưu bắt buộc với encoding='utf-8' tường minh.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    subs = SSAFile()
    style_name = "Karaoke"
    subs.styles[style_name] = create_karaoke_style(font_name=font_name, font_size=font_size)

    k_tag = r"\kf" if use_kf else r"\k"

    for seg in segments:
        if not seg.text.strip():
            continue

        start_ms = int(round(seg.start * 1000))
        end_ms = int(round(seg.end * 1000))

        if seg.words:
            # Điều chỉnh start_ms và end_ms bao trùm mốc của các từ
            w_start_ms = int(round(seg.words[0].start * 1000))
            w_end_ms = int(round(seg.words[-1].end * 1000))
            start_ms = min(start_ms, w_start_ms)
            end_ms = max(end_ms, w_end_ms)

            # Xây dựng chuỗi text kèm tag karaoke {\kf<centiseconds>}
            parts: list[str] = []
            for w in seg.words:
                dur_cs = max(1, int(round(w.duration * 100)))
                parts.append(f"{{{k_tag}{dur_cs}}}{w.word}")

            event_text = " ".join(parts)
        else:
            # Không có mốc từng từ: hiển thị câu bình thường
            event_text = seg.text

        event = SSAEvent(
            start=start_ms,
            end=end_ms,
            text=event_text,
            style=style_name,
        )
        subs.events.append(event)

    # BẮT BUỘC lưu với encoding='utf-8' để không dính lỗi Windows locale (cp1258 / cp1252)
    subs.save(str(out_path), encoding="utf-8")
    return out_path


def burn_ass_subtitles(
    video_path: Path | str,
    ass_path: Path | str,
    output_path: Path | str,
    *,
    work_dir: Optional[Path | str] = None,
    reporter: Optional[Reporter] = None,
) -> Path:
    r"""Burn phụ đề .ass trực tiếp vào video bằng filter ass của FFmpeg.
    
    Sao chép file .ass vào thư mục tạm và truyền đường dẫn tương đối để tránh lỗi
    thoát ký tự dấu hai chấm 'C:\...' trên Windows.
    """
    reporter = reporter or Reporter()
    video_path = Path(video_path).resolve()
    ass_path = Path(ass_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    tmp = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="burn_ass_"))
    tmp.mkdir(parents=True, exist_ok=True)
    clean_tmp = work_dir is None

    try:
        ass_filename = "subs.ass"
        shutil.copyfile(str(ass_path), str(tmp / ass_filename))

        # Filter ass với tên file tương đối trong thư mục làm việc cwd=tmp
        cmd = [
            "-i", str(video_path),
            "-vf", f"ass={ass_filename}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_path),
        ]
        reporter.log(f"🎬 Đang burn phụ đề Karaoke ASS vào {output_path.name}...")
        res = run_ffmpeg(cmd, cwd=tmp, check=False)
        if res.returncode != 0:
            raise DubberError(f"Burn phụ đề ASS thất bại:\n{res.stderr.strip()[-800:]}")
    finally:
        if clean_tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    reporter.log(f"✓ Đã burn phụ đề ASS: {output_path}", "ok")
    return output_path
