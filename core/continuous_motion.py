"""Chuyển động camera liên tục cho một chuỗi shot cùng địa điểm.

Thay vì render mỗi cảnh thành một clip với zoom bắt đầu lại từ 1.0 (zoompan kiểu pzoom cũ),
module này gom các shot liên tiếp cùng địa điểm thành MỘT clip và nội suy zoom theo từng đoạn
đúng theo zoom_from/zoom_to của planner.

Chỉ dùng FFmpeg zoompan, không phụ thuộc GUI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from core.shot_planner import Shot

SRC_W, SRC_H = 1620, 2880      # phóng nguồn trước để zoompan ít rung
OUT_W, OUT_H = 1080, 1920


@dataclass
class Run:
    """Một clip liên tục: các shot cùng địa điểm, nối tiếp không cắt."""
    location: str
    start: float
    end: float
    pieces: List[Tuple[float, float, float, float]]   # (bắt đầu s, kết thúc s, zoom_from, zoom_to), tương đối run
    shot_ids: List[int]

    @property
    def duration(self) -> float:
        return self.end - self.start


def group_continuous_runs(shots: Sequence[Shot]) -> List[Run]:
    """Gom shot thành run. Shot mở đầu run luôn là 'cut' hoặc shot đầu tiên."""
    runs: List[Run] = []
    for sh in shots:
        new_run = (not runs) or sh.transition_in != "continuous" or sh.location != runs[-1].location
        if new_run:
            runs.append(Run(sh.location, sh.start, sh.end, [], []))
        run = runs[-1]
        run.end = sh.end
        run.shot_ids.append(sh.id)
        run.pieces.append((sh.start, sh.end, sh.zoom_from, sh.zoom_to))
    for run in runs:
        base = run.start
        run.pieces = [(round(a - base, 4), round(b - base, 4), za, zb) for a, b, za, zb in run.pieces]
    return runs


def zoom_expression(pieces: Sequence[Tuple[float, float, float, float]], fps: int = 30) -> str:
    """Biểu thức z theo số khung `on`, nội suy tuyến tính trong từng đoạn; ngoài đoạn cuối giữ nguyên."""
    if not pieces:
        return "1.0"
    last_z = pieces[-1][3]
    expr = f"{last_z:.4f}"
    for a, b, za, zb in reversed(pieces):
        f0 = int(round(a * fps))
        f1 = int(round(b * fps))
        length = max(1, f1 - f0)
        seg = f"{za:.4f}+({zb - za:.4f})*(on-{f0})/{length}"
        expr = f"if(lt(on,{f1}),{seg},{expr})"
    return expr


def build_run_filter(run: Run, fps: int = 30) -> str:
    """Chuỗi filter FFmpeg cho một run (ảnh tĩnh nguồn)."""
    n = max(1, round(run.duration * fps))
    z = zoom_expression(run.pieces, fps)
    return (
        f"scale={SRC_W}:{SRC_H}:force_original_aspect_ratio=increase,crop={SRC_W}:{SRC_H},"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={n}:s={OUT_W}x{OUT_H}:fps={fps},setsar=1,format=yuv420p"
    )
