"""Module lõi Né quét AI (Anti-AI Filter) cho DubStudio.

Cung cấp 4 mức cấu hình (off / light / medium / strong) dùng chung cho:
  1. Luồng xuất 1-pass trong StoryPipeline (áp filter hình TRƯỚC bước đốt phụ đề ASS
     để phụ đề luôn thẳng tắp, sắc nét; áp filter tiếng + room-tone sau khi trộn âm thanh tổng).
  2. Chế độ xử lý độc lập cho bất kỳ file video .mp4/.mov/.mkv hoàn chỉnh nào
     (tab 4. Né quét AI trên giao diện).

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional, Sequence, Tuple, Union

from .errors import DubberError
from .ffmpeg_utils import _NO_WINDOW, atempo_chain, get_ffmpeg, probe, run_ffmpeg

AntiAILevel = Literal["off", "light", "medium", "strong"]
VALID_ANTI_AI_LEVELS: Tuple[str, ...] = ("off", "light", "medium", "strong")

ANTI_AI_LABELS = {
    "off": "Tắt",
    "light": "Nhẹ — Chuẩn nét",
    "medium": "Vừa — Khuyên dùng (CapCut)",
    "strong": "Mạnh — Phá sâu",
}

_LEVEL_ALIASES = {
    "off": "off",
    "none": "off",
    "false": "off",
    "0": "off",
    "tat": "off",
    "tắt": "off",
    "light": "light",
    "nhe": "light",
    "nhẹ": "light",
    "medium": "medium",
    "med": "medium",
    "true": "medium",
    "1": "medium",
    "vua": "medium",
    "vừa": "medium",
    "strong": "strong",
    "high": "strong",
    "manh": "strong",
    "mạnh": "strong",
}


@dataclass(frozen=True)
class AntiAIPreset:
    level: str
    rotate_deg: float               # góc xoay vi mô (độ, dấu ± theo seed)
    shift_px_range: Tuple[int, int] # dịch tâm vi mô (px) ở mức strong
    unsharp: str                    # bộ lọc sắc nét nhẹ
    eq: str                         # cân bằng màu nhẹ
    noise: str                      # hạt phim (chỉ áp khi film_grain=True)
    vignette: str                   # tối góc dịu (chỉ áp khi film_grain=True)
    pitch_pct: float                # dịch cao độ vi mô (% — có bù atempo giữ nguyên thời lượng)
    room_tone_db: float             # mức room-tone nền (dBFS âm)
    audio_eq: str                   # lọc cân bằng nhẹ cho luồng âm thanh


PRESETS = {
    "light": AntiAIPreset(
        level="light",
        rotate_deg=0.40,
        shift_px_range=(0, 0),
        unsharp="unsharp=3:3:0.25:3:3:0.0",
        eq="eq=contrast=1.02:saturation=1.03",
        noise="",
        vignette="",
        pitch_pct=0.0,
        room_tone_db=-48.0,
        audio_eq="highpass=f=60,lowpass=f=18500",
    ),
    "medium": AntiAIPreset(
        level="medium",
        rotate_deg=0.55,
        shift_px_range=(0, 0),
        unsharp="unsharp=3:3:0.30:3:3:0.0",
        eq="eq=contrast=1.03:brightness=0.005:saturation=1.05",
        noise="noise=alls=3:allf=t+u",
        vignette="vignette=PI/6",
        pitch_pct=0.4,
        room_tone_db=-44.0,
        audio_eq="highpass=f=65,equalizer=f=3200:t=q:w=1.5:g=0.5",
    ),
    "strong": AntiAIPreset(
        level="strong",
        rotate_deg=0.75,
        shift_px_range=(2, 4),
        unsharp="unsharp=3:3:0.35:3:3:0.0",
        eq="eq=contrast=1.04:brightness=0.006:saturation=1.06",
        noise="noise=alls=5:allf=t+u",
        vignette="vignette=PI/5",
        pitch_pct=0.6,
        room_tone_db=-40.0,
        audio_eq="highpass=f=70,equalizer=f=220:t=q:w=1.2:g=0.7,equalizer=f=3500:t=q:w=1.4:g=0.8",
    ),
}


@dataclass(frozen=True)
class AIDepolishSpec:
    """Thông số khử bóng HDR nhẹ cho riêng từng cảnh tạo bằng AI."""

    scale_factor: float
    saturation: float
    curves: str
    noise: str


DEPOLISH_SPECS = {
    "light": AIDepolishSpec(
        scale_factor=1.03,
        saturation=0.95,
        curves="curves=all='0/0 0.5/0.5 0.85/0.80 1/0.92'",
        noise="noise=alls=4:allf=t+u",
    ),
    "medium": AIDepolishSpec(
        scale_factor=1.03,
        saturation=0.95,
        curves="curves=all='0/0 0.5/0.5 0.85/0.77 1/0.88'",
        noise="noise=alls=6:allf=t+u",
    ),
    "strong": AIDepolishSpec(
        scale_factor=1.04,
        saturation=0.93,
        curves="curves=all='0/0 0.5/0.5 0.85/0.74 1/0.85'",
        noise="noise=alls=8:allf=t+u",
    ),
}


def _parse_positive_int(val: Any) -> Optional[int]:
    if isinstance(val, bool) or val is None:
        return None
    if isinstance(val, int):
        return val if val > 0 else None
    text = str(val).strip()
    if text.isdigit():
        num = int(text)
        return num if num > 0 else None
    return None


def load_archival_scene_ids(project_dir: Union[Path, str, None]) -> set[int]:
    """Đọc tập scene_id tư liệu thật từ `<project_dir>/archive_sources.json`.

    Hỗ trợ linh hoạt cấu trúc `{"assets": {"001": {"scene_id": 1}}}` của fetch_archive_media.py,
    dict theo khóa số cảnh hoặc list bản ghi có trường `scene` / `scene_id`.
    Trả về `set()` nếu file chưa tồn tại, rỗng hoặc không hợp lệ.
    """
    if not project_dir:
        return set()
    sources_path = Path(project_dir) / "archive_sources.json"
    if not sources_path.is_file():
        return set()
    try:
        raw_text = sources_path.read_text(encoding="utf-8-sig").strip()
        if not raw_text:
            return set()
        data = json.loads(raw_text)
    except (OSError, ValueError):
        return set()

    scene_ids: set[int] = set()

    def collect_from_seq(seq: Sequence[Any]) -> None:
        for item in seq:
            if isinstance(item, dict):
                for k in ("scene_id", "scene", "id"):
                    sid = _parse_positive_int(item.get(k))
                    if sid is not None:
                        scene_ids.add(sid)
                        break
            else:
                sid = _parse_positive_int(item)
                if sid is not None:
                    scene_ids.add(sid)

    def collect_from_mapping(mapping: dict) -> None:
        for key, val in mapping.items():
            sid: Optional[int] = None
            if isinstance(val, dict):
                for k in ("scene_id", "scene", "id"):
                    sid = _parse_positive_int(val.get(k))
                    if sid is not None:
                        break
            if sid is None:
                sid = _parse_positive_int(key)
            if sid is not None:
                scene_ids.add(sid)

    if isinstance(data, list):
        collect_from_seq(data)
        return scene_ids
    if not isinstance(data, dict):
        return set()

    has_container = False
    for container_key in ("assets", "items", "scenes"):
        container = data.get(container_key)
        if isinstance(container, dict):
            has_container = True
            collect_from_mapping(container)
        elif isinstance(container, list):
            has_container = True
            collect_from_seq(container)

    if not has_container:
        collect_from_mapping(data)
    return scene_ids


def build_ai_scene_depolish_vf(
    level: Union[str, bool, None],
    width: int = 1080,
    height: int = 1920,
) -> str:
    """Tạo chuỗi filter khử bóng nhựa HDR nhẹ riêng cho cảnh tạo bằng AI.

    Giữ độ nét gốc (không unsharp, không xoay toàn khung), chỉ hạ bão hòa nhẹ,
    dìm highlights qua curves, scale+crop vi mô 3–4% và rải hạt phim siêu mịn.
    """
    lv = normalize_anti_ai_level(level)
    if lv == "off":
        return ""

    spec = DEPOLISH_SPECS[lv]
    w = max(2, int(width) - (int(width) % 2))
    h = max(2, int(height) - (int(height) % 2))
    sw = max(w + 2, int(math.ceil((w * spec.scale_factor) / 2.0)) * 2)
    sh = max(h + 2, int(math.ceil((h * spec.scale_factor) / 2.0)) * 2)
    cx = (sw - w) // 2
    cy = (sh - h) // 2
    return (
        f"scale={sw}:{sh}:flags=lanczos,"
        f"crop={w}:{h}:{cx}:{cy},"
        f"setsar=1,"
        f"eq=saturation={spec.saturation:.2f},"
        f"{spec.curves},"
        f"{spec.noise}"
    )


def normalize_anti_ai_level(level_or_bool: Union[str, bool, None]) -> str:
    """Chuẩn hóa mức Anti-AI, tương thích ngược hoàn toàn với cờ bool cũ.

    - False / None / "" / "off" -> "off"
    - True -> "medium"
    - "light" / "medium" / "strong" -> giữ nguyên
    """
    if level_or_bool is None or level_or_bool is False:
        return "off"
    if level_or_bool is True:
        return "medium"
    raw = str(level_or_bool).strip().lower()
    if not raw:
        return "off"
    if raw in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[raw]
    raise ValueError(
        f"Mức anti_ai_level '{level_or_bool}' không hợp lệ (chấp nhận: {', '.join(VALID_ANTI_AI_LEVELS)})."
    )


def _make_rng(seed: Optional[Union[int, str]] = None, salt: str = "") -> random.Random:
    if seed is None:
        return random.Random()
    digest = hashlib.sha256(f"{seed}|{salt}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _compute_safe_scale(width: int, height: int, angle_deg: float, max_shift_px: int = 0) -> float:
    """Tính hệ số phóng dư tối thiểu đảm bảo 0 pixel góc đen sau khi xoay và dịch tâm.

    Công thức hình học chính xác:
        s_min = cos(|θ|) + (max(W, H) / min(W, H)) * sin(|θ|)
    Cộng thêm biên dịch tâm (2 * max_shift / min(W, H)) và lề an toàn +0.012
    (vượt ngưỡng >= +0.008 và bù làm tròn chẵn + nội suy viền Lanczos/bilinear).
    """
    w = max(2, int(width))
    h = max(2, int(height))
    theta = math.radians(abs(float(angle_deg)))
    aspect = max(w, h) / float(min(w, h))
    s_rot = math.cos(theta) + aspect * math.sin(theta)
    s_shift = (2.0 * abs(int(max_shift_px)) / float(min(w, h))) if max_shift_px > 0 else 0.0
    return s_rot + s_shift + 0.012


def build_anti_ai_video_filters(
    level: Union[str, bool, None],
    width: int = 1080,
    height: int = 1920,
    film_grain: bool = True,
    seed: Optional[Union[int, str]] = None,
) -> List[str]:
    """Tạo danh sách filter video FFmpeg phá lưới không gian + miền tần số, đảm bảo 0 góc đen.

    Lưu ý: trong luồng StoryPipeline 1-pass, danh sách filter này PHẢI được áp TRƯỚC
    bước đốt phụ đề `ass=...` để chữ phụ đề luôn thẳng tắp và sắc nét.
    """
    lv = normalize_anti_ai_level(level)
    if lv == "off":
        return []

    preset = PRESETS[lv]
    w = max(2, int(width) - (int(width) % 2))
    h = max(2, int(height) - (int(height) % 2))
    rng = _make_rng(seed, salt=f"video_{lv}_{w}x{h}")

    sign = rng.choice((-1.0, 1.0))
    angle_deg = sign * preset.rotate_deg
    angle_rad = math.radians(angle_deg)

    dx, dy = 0, 0
    lo_sh, hi_sh = preset.shift_px_range
    if hi_sh > 0:
        dx = rng.choice((-1, 1)) * rng.randint(lo_sh, hi_sh)
        dy = rng.choice((-1, 1)) * rng.randint(lo_sh, hi_sh)

    max_shift = max(abs(dx), abs(dy))
    scale_factor = _compute_safe_scale(w, h, angle_deg, max_shift_px=max_shift)

    # Làm tròn lên bội số của 2 (yêu cầu chuẩn yuv420p) và bảo đảm đủ dư cho crop + shift
    sw = int(math.ceil((w * scale_factor) / 2.0)) * 2
    sh = int(math.ceil((h * scale_factor) / 2.0)) * 2
    sw = max(sw, w + 2 * abs(dx) + 4)
    sh = max(sh, h + 2 * abs(dy) + 4)

    cx = max(0, min(sw - w, (sw - w) // 2 + dx))
    cy = max(0, min(sh - h, (sh - h) // 2 + dy))

    filters: List[str] = [
        f"scale={sw}:{sh}:flags=lanczos",
        f"rotate={angle_rad:.6f}:ow={sw}:oh={sh}:bilinear=1:fillcolor=black",
        f"crop={w}:{h}:{cx}:{cy}",
        f"scale={w}:{h}:flags=lanczos",
        "setsar=1",
    ]

    if film_grain and preset.noise:
        filters.append(preset.noise)
    if preset.unsharp:
        filters.append(preset.unsharp)
    if preset.eq:
        filters.append(preset.eq)
    if film_grain and preset.vignette:
        filters.append(preset.vignette)

    return filters


def build_anti_ai_audio_filters(
    level: Union[str, bool, None],
    sample_rate: int = 44100,
    seed: Optional[Union[int, str]] = None,
) -> str:
    """Trả về chuỗi filter audio tuyến tính giữ nguyên độ dài 100% (dùng cho -af hoặc filter_complex).

    - `light`: chuẩn hóa stereo + lọc thông dải tự nhiên (highpass/lowpass).
    - `medium`: dịch cao độ vi mô ±0.4% (`asetrate` + `aresample` + `atempo` bù ngược chính xác) + EQ nhẹ.
    - `strong`: dịch cao độ vi mô ±0.6% (bù ngược `atempo`) + EQ định hình nhẹ.
    """
    lv = normalize_anti_ai_level(level)
    if lv == "off":
        return ""

    preset = PRESETS[lv]
    sr = max(8000, int(sample_rate))
    rng = _make_rng(seed, salt=f"audio_{lv}_{sr}")

    parts: List[str] = [
        f"aresample={sr}",
        f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo",
    ]
    if preset.audio_eq:
        parts.append(preset.audio_eq)

    if preset.pitch_pct > 0.0:
        sign = rng.choice((-1.0, 1.0))
        pitch_factor = 1.0 + sign * (preset.pitch_pct / 100.0)
        shifted_sr = sr * pitch_factor
        tempo_comp = 1.0 / pitch_factor
        parts.append(f"asetrate={shifted_sr:.2f}")
        parts.append(f"aresample={sr}")
        parts.append(f"atempo={tempo_comp:.6f}")

    return ",".join(parts)


def build_anti_ai_audio_graph(
    in_label: str,
    out_label: str,
    level: Union[str, bool, None],
    sample_rate: int = 44100,
    seed: Optional[Union[int, str]] = None,
    *,
    include_normalize: bool = True,
    pre_filters: str = "",
    post_filters: str = "",
) -> str:
    """Tạo đoạn filter_complex hoàn chỉnh gồm lọc audio + dịch cao độ vi mô + room-tone hồng tự nhiên.

    Giữ nguyên tuyệt đối thời lượng của `in_label` nhờ `amix=inputs=2:duration=first`.
    """
    lv = normalize_anti_ai_level(level)
    sr = max(8000, int(sample_rate))

    if lv == "off":
        chain = ",".join(p for p in (pre_filters.strip(","), post_filters.strip(",")) if p)
        if not chain:
            chain = "anull"
        return f"{in_label}{chain}{out_label}"

    preset = PRESETS[lv]
    base_chain = build_anti_ai_audio_filters(lv, sample_rate=sr, seed=seed)
    mid_chain = ",".join(p for p in (pre_filters.strip(","), base_chain) if p)

    # Quy đổi dBFS sang biên độ tuyến tính cho anoisesrc (pink room-tone)
    amp = 10.0 ** (preset.room_tone_db / 20.0)
    rng = _make_rng(seed, salt=f"room_{lv}")
    noise_seed = rng.randint(1, 2_000_000)
    norm_opt = ":normalize=0" if include_normalize else ""
    post_part = f",{post_filters.strip(',')}" if post_filters.strip(",") else ""

    return (
        f"{in_label}{mid_chain}[_aai_base];"
        f"anoisesrc=color=pink:sample_rate={sr}:amplitude={amp:.6f}:seed={noise_seed},"
        f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo[_aai_room];"
        f"[_aai_base][_aai_room]amix=inputs=2:duration=first{norm_opt}{post_part}{out_label}"
    )


def get_clean_metadata_flags() -> List[str]:
    """Trả cờ FFmpeg xóa sạch metadata/C2PA/EXIF/XMP/chapters và đóng gói MP4 faststart."""
    return [
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        "-movflags", "+faststart",
    ]


@dataclass
class VideoDetails:
    duration: float
    has_video: bool
    has_audio: bool
    width: int
    height: int
    fps: float


def probe_video_details(path: Union[Path, str]) -> VideoDetails:
    """Đọc đầy đủ kích thước (W, H), fps, thời lượng và luồng audio/video chỉ bằng ffmpeg -i."""
    base = probe(path)
    res = subprocess.run(
        [get_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    text = res.stderr
    width, height, fps = 1080, 1920, 30.0
    for line in text.splitlines():
        if "Video:" in line:
            m_dim = re.search(r",\s*(\d{2,5})x(\d{2,5})(?:\s|\[|,|$)", line)
            if m_dim:
                width, height = int(m_dim.group(1)), int(m_dim.group(2))
            m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
            if m_fps:
                fps = float(m_fps.group(1))
            break
    return VideoDetails(
        duration=base.duration,
        has_video=base.has_video,
        has_audio=base.has_audio,
        width=width,
        height=height,
        fps=fps,
    )


def _emit_log(log_cb: Optional[Callable], msg: str, level: str = "info") -> None:
    if log_cb is None:
        return
    try:
        log_cb(msg, level)
    except TypeError:
        log_cb(msg)


def _emit_progress(progress_cb: Optional[Callable], pct: float, msg: str = "") -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(pct, msg)
    except TypeError:
        progress_cb(pct)


def process_standalone_video(
    input_path: Union[Path, str],
    output_path: Union[Path, str],
    level: Union[str, bool, None] = "medium",
    film_grain: bool = True,
    speed: float = 1.0,
    log_cb: Optional[Callable] = None,
    progress_cb: Optional[Callable] = None,
    *,
    seed: Optional[Union[int, str]] = None,
    crf: int = 18,
) -> Path:
    """Xử lý Né quét AI độc lập cho bất kỳ file video .mp4/.mov/.mkv hoàn chỉnh nào.

    - Tự động đọc kích thước W×H, fps, trạng thái luồng âm thanh (hỗ trợ cả video câm không audio).
    - Nếu `speed != 1.0`: chỉnh tốc độ đồng bộ hình (`setpts`) + tiếng (`atempo`).
    - Xuất chuẩn 30fps, H.264 High (`yuv420p`, `crf=18`), xóa sạch toàn bộ metadata/C2PA/chapters.
    """
    in_p = Path(input_path).resolve()
    if not in_p.is_file():
        raise DubberError(f"File video đầu vào không tồn tại: {in_p}")

    out_p = Path(output_path).resolve()
    if out_p == in_p:
        raise DubberError("File đầu ra không được trùng đường dẫn với file đầu vào.")
    out_p.parent.mkdir(parents=True, exist_ok=True)

    lv = normalize_anti_ai_level(level)
    spd = float(speed)
    if not (0.25 <= spd <= 4.0):
        raise DubberError(f"Tốc độ speed={spd} không hợp lệ (chấp nhận 0.25–4.0).")
    apply_speed = abs(spd - 1.0) > 1e-3

    _emit_progress(progress_cb, 8, "Đang phân tích thông số video đầu vào...")
    info = probe_video_details(in_p)
    if not info.has_video:
        raise DubberError(f"File '{in_p.name}' không có luồng hình ảnh (video).")

    _emit_log(
        log_cb,
        f"🛡️ Bắt đầu Né quét AI: '{in_p.name}' ({info.width}x{info.height}, {info.fps:.1f}fps, "
        f"{info.duration:.2f}s | mức={lv} | grain={'bật' if film_grain else 'tắt'} | tốc độ={spd:.2f}x)",
        "accent",
    )

    eff_seed = seed if seed is not None else f"{in_p.name}:{info.width}x{info.height}"
    vf_chain = build_anti_ai_video_filters(
        lv,
        width=info.width,
        height=info.height,
        film_grain=film_grain,
        seed=eff_seed,
    )
    if apply_speed:
        vf_chain.insert(0, f"setpts=PTS/{spd:.6f}")

    # Bảo đảm chẵn pixel & chuẩn 30fps
    target_w = max(2, info.width - (info.width % 2))
    target_h = max(2, info.height - (info.height % 2))
    if not vf_chain:
        vf_chain = [f"scale={target_w}:{target_h}:flags=lanczos", "setsar=1"]

    def build_cmd(include_normalize: bool = True) -> List[str]:
        fc_parts: List[str] = [f"[0:v]{','.join(vf_chain)}[vout]"]
        map_args: List[str] = ["-map", "[vout]"]
        audio_codec_args: List[str] = ["-an"]

        if info.has_audio:
            pre_a = atempo_chain(spd) if apply_speed else ""
            if lv != "off":
                fc_parts.append(
                    build_anti_ai_audio_graph(
                        "[0:a]",
                        "[aout]",
                        lv,
                        sample_rate=44100,
                        seed=eff_seed,
                        include_normalize=include_normalize,
                        pre_filters=pre_a,
                    )
                )
                map_args += ["-map", "[aout]"]
            elif pre_a:
                fc_parts.append(f"[0:a]{pre_a}[aout]")
                map_args += ["-map", "[aout]"]
            else:
                map_args += ["-map", "0:a:0"]
            audio_codec_args = ["-c:a", "aac", "-b:a", "192k"]

        return [
            "-i", str(in_p),
            "-filter_complex", ";".join(fc_parts),
            *map_args,
            "-r", "30",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-preset", "medium",
            "-crf", str(int(crf)),
            "-pix_fmt", "yuv420p",
            *audio_codec_args,
            *get_clean_metadata_flags(),
            str(out_p),
        ]

    _emit_progress(progress_cb, 30, "Đang xử lý khung hình, âm thanh và xóa sạch metadata...")
    res = run_ffmpeg(build_cmd(True), check=False)
    if res.returncode != 0 and "normalize" in res.stderr:
        res = run_ffmpeg(build_cmd(False), check=False)
    if res.returncode != 0:
        raise DubberError(f"Xử lý Né quét AI thất bại:\n{res.stderr.strip()[-900:]}")

    _emit_progress(progress_cb, 92, "Đang kiểm tra file đầu ra...")
    out_info = probe_video_details(out_p)
    size_mb = out_p.stat().st_size / (1024 * 1024)
    _emit_progress(progress_cb, 100, f"Hoàn tất Né quét AI ({size_mb:.1f}MB)")
    _emit_log(
        log_cb,
        f"✓ Hoàn thành: {out_p} ({out_info.width}x{out_info.height}, {out_info.fps:.0f}fps, "
        f"{out_info.duration:.2f}s, {size_mb:.1f}MB)",
        "ok",
    )
    return out_p
