"""Trộn giọng AI với âm thanh gốc và xuất MP4 (có thể kèm phụ đề cứng)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .errors import DubberError
from .ffmpeg_utils import probe, run_ffmpeg
from .reporter import Reporter

# "Force Mix": ép mọi nguồn về stereo 44.1kHz để xử lý được video lạ (mono, 48kHz, 5.1...).
_FMT = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
MIX_MODES = ("simple", "duck", "stems")


def _filter_graph(mode: str, has_orig_audio: bool, orig_vol: float, ai_vol: float, bg_vol: float,
                  normalize_opt: bool) -> str:
    norm = ":normalize=0" if normalize_opt else ""
    ai = f"[1:a]{_FMT},volume={ai_vol}"
    if mode == "stems":
        return f"[2:a]{_FMT},volume={bg_vol}[bg];{ai}[ai];[bg][ai]amix=inputs=2:duration=longest{norm}[aout]"
    if not has_orig_audio:  # video câm: chỉ có giọng AI
        return f"{ai}[aout]"
    orig = f"[0:a]{_FMT},volume={orig_vol}[orig]"
    if mode == "duck":
        # Âm gốc tự nhỏ lại đúng lúc AI đang nói rồi to trở lại (sidechain compression)
        return (f"{orig};{ai},asplit=2[ai_sc][ai_mix];"
                f"[orig][ai_sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked];"
                f"[ducked][ai_mix]amix=inputs=2:duration=longest{norm}[aout]")
    return f"{orig};{ai}[ai];[orig][ai]amix=inputs=2:duration=longest{norm}[aout]"


def mix_video(video, ai_track, output, *, mode: str = "duck", original_volume: float = 0.5,
              ai_volume: float = 1.0, bg_stem=None, bg_volume: float = 1.0, srt_path=None,
              caption_size: int = 18, reporter: Optional[Reporter] = None) -> Path:
    reporter = reporter or Reporter()
    if mode not in MIX_MODES:
        raise DubberError(f"Chế độ trộn không hợp lệ: {mode}")
    if mode == "stems" and not bg_stem:
        mode = "duck"
    info = probe(video)
    if not info.has_video:
        raise DubberError("File đầu vào không có hình ảnh")
    if not info.has_audio and mode in ("simple", "duck"):
        reporter.log("ℹ️ Video không có âm thanh gốc → chỉ dùng giọng AI", "warn")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="mix_"))
    try:
        vcodec = ["-c:v", "copy"]
        if srt_path:  # đốt phụ đề: chạy ffmpeg trong thư mục tạm để tránh lỗi đường dẫn Windows (C:\...)
            shutil.copy(srt_path, tmp / "subs.srt")
            style = (f"FontName=Arial,FontSize={caption_size},Bold=1,PrimaryColour=&H00FFFFFF,"
                     "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=30")
            vcodec = ["-vf", f"subtitles=subs.srt:force_style='{style}'",
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

        def build(normalize_opt: bool):
            args = ["-i", Path(video).resolve(), "-i", Path(ai_track).resolve()]
            if mode == "stems":
                args += ["-i", Path(bg_stem).resolve()]
            fc = _filter_graph(mode, info.has_audio, original_volume, ai_volume, bg_volume, normalize_opt)
            args += ["-filter_complex", fc, "-map", "0:v:0", "-map", "[aout]", *vcodec,
                     "-c:a", "aac", "-b:a", "192k", "-t", f"{info.duration:.3f}",
                     "-movflags", "+faststart", output.resolve()]
            return args

        # `normalize=0` (giữ nguyên âm lượng khi trộn) cần FFmpeg >= 4.4; bản cũ hơn thì thử lại không có.
        res = run_ffmpeg(build(True), cwd=tmp, check=False)
        if res.returncode != 0 and "normalize" in res.stderr:
            res = run_ffmpeg(build(False), cwd=tmp, check=False)
        if res.returncode != 0:
            raise DubberError(f"Trộn video thất bại:\n{res.stderr.strip()[-900:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reporter.log(f"✓ Đã xuất: {output}", "ok")
    return output
