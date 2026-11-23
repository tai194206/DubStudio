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
    """Thông số V5.8 Unified Cold Slate-Charcoal Bleach-Bypass cho riêng từng cảnh tạo bằng AI."""

    scale_factor: float
    saturation: float
    curves: str
    noise: str
    vignette: str = ""
    denoise: str = ""
    colorbalance: str = ""


# Chuẩn V5.8 Unified Cold Slate-Charcoal House Look (CHỈ áp cho cảnh AI, miễn ảnh thật):
# - Tiền khử nhiễu rất nhẹ (hqdn3d=0.6..1.0) trước hạt phim để giữ nguyên độ gai góc vải/da, không bệt nhựa.
# - Curves low-key kết hợp colorbalance nghiêng lạnh slate-blue ở shadow/midtone để khóa chung 1 house look.
DEPOLISH_SPECS = {
    "light": AIDepolishSpec(
        scale_factor=1.03,
        saturation=0.62,
        denoise="hqdn3d=0.6:0.5:1.2:1.0",
        curves="curves=all='0/0 0.25/0.22 0.5/0.47 0.85/0.84 1/0.96'",
        colorbalance="colorbalance=rs=-0.03:gs=0.00:bs=0.045:rm=-0.025:gm=0.00:bm=0.04:rh=0.015:gh=0.005:bh=-0.01",
        noise="noise=alls=5:allf=t+u",
        vignette="vignette=PI/7",
    ),
    "medium": AIDepolishSpec(
        scale_factor=1.03,
        saturation=0.52,
        denoise="hqdn3d=0.8:0.6:1.5:1.2",
        curves="curves=all='0/0 0.25/0.20 0.5/0.45 0.85/0.82 1/0.95'",
        colorbalance="colorbalance=rs=-0.04:gs=0.00:bs=0.06:rm=-0.03:gm=0.00:bm=0.05:rh=0.02:gh=0.01:bh=-0.01",
        noise="noise=alls=7:allf=t+u",
        vignette="vignette=PI/6",
    ),
    "strong": AIDepolishSpec(
        scale_factor=1.04,
        saturation=0.44,
        denoise="hqdn3d=1.0:0.8:1.8:1.4",
        curves="curves=all='0/0 0.25/0.18 0.5/0.42 0.85/0.79 1/0.93'",
        colorbalance="colorbalance=rs=-0.05:gs=0.00:bs=0.075:rm=-0.035:gm=0.00:bm=0.06:rh=0.02:gh=0.01:bh=-0.01",
        noise="noise=alls=9:allf=t+u",
        vignette="vignette=PI/5",
    ),
}

# Lớp hòa tông cực nhẹ ~15% lúc render cho ảnh tư liệu thật (non-destructive 100%, giữ nguyên file gốc & SHA-256)
ARCHIVAL_HARMONIZE_VF = (
    "eq=contrast=1.04:brightness=-0.02,"
    "colorbalance=rs=-0.018:gs=0.00:bs=0.026:rm=-0.015:gm=0.00:bm=0.022:rh=-0.005:gh=0.00:bh=0.008"
)


def build_archival_harmonize_vf(enabled: bool = True) -> str:
    """Trả về lớp hòa tông ~15% non-destructive áp lúc render cho cảnh tư liệu thật trong archive_sources.json."""
    return ARCHIVAL_HARMONIZE_VF if enabled else ""

DEFAULT_FLOW_WATERMARK_ZOOM = 1.04


def is_archival_scene(
    scene_id: Optional[int],
    archival_scene_ids: Optional[set[int]],
) -> bool:
    """Kiểm tra scene_id có nằm trong danh sách ảnh tư liệu thật được bảo vệ miễn lọc/miễn crop."""
    if scene_id is None or not archival_scene_ids:
        return False
    return int(scene_id) in archival_scene_ids


def _erase_flow_corner_sparkle(rgb_img: Any) -> Any:
    """Tự động phát hiện và làm sạch biểu tượng thoi 4 cánh nhỏ ở góc phải dưới của Google Flow.

    Dùng khớp mẫu hình sao 4 cánh chuẩn (zero-mean astroid correlation) trong góc phải dưới
    để khóa đúng tâm ±1px kể cả trên nền đá/lá phức tạp, sau đó lấp đa tầng (coarse-to-fine)
    từ viền sạch xung quanh rồi hòa hạt phim vi mô.
    """
    import numpy as np
    from PIL import Image

    w, h = rgb_img.size
    if w < 240 or h < 240:
        return rgb_img

    arr_u8 = np.asarray(rgb_img, dtype=np.uint8)
    gray = np.asarray(rgb_img.convert("L"), dtype=np.float32)

    # Vùng đọc rộng hơn bán kính kernel (0.76..0.99) nhưng tâm sao phải nằm hẳn trong góc [0.84*w.., 0.83*h..]
    rx0 = int(w * 0.76)
    ry0 = int(h * 0.75)
    rx1 = w - 2
    ry1 = h - 2
    if rx1 - rx0 < 28 or ry1 - ry0 < 28:
        return rgb_img

    # Kích thước bán kính điển hình của ngôi sao Google Flow / Gemini (~0.042 * min(w, h))
    base_r = max(12.0, min(42.0, min(w, h) * 0.042))
    sub = gray[ry0:ry1, rx0:rx1]
    sh, sw = sub.shape
    min_cx = int(w * 0.84)
    min_cy = int(h * 0.83)

    best_score = 0.0
    best_pt: Optional[Tuple[int, int, float]] = None

    for r_try in (base_r * 0.72, base_r * 0.88, base_r, base_r * 1.18):
        kr = int(math.ceil(r_try * 1.45))
        if 2 * kr + 1 >= sh or 2 * kr + 1 >= sw:
            continue
        ky, kx = np.mgrid[-kr : kr + 1, -kr : kr + 1].astype(np.float32)
        d = np.power(np.abs(kx) / r_try, 0.54) + np.power(np.abs(ky) / r_try, 0.54)
        star_in = d <= 1.0
        ring_out = (d > 1.08) & (d <= 1.55)
        support = star_in | ring_out
        n_in = int(star_in.sum())
        n_out = int(ring_out.sum())
        if n_in < 12 or n_out < 12:
            continue

        kern = np.zeros_like(ky, dtype=np.float32)
        kern[star_in] = 1.0 / n_in
        kern[ring_out] = -1.0 / n_out
        k_zero = np.zeros_like(ky, dtype=np.float32)
        k_zero[support] = np.where(star_in[support], 1.0, 0.0)
        k_zero[support] -= k_zero[support].mean()
        k_norm = float(np.sqrt((k_zero[support] ** 2).sum())) + 1e-6

        win = np.lib.stride_tricks.sliding_window_view(sub, kern.shape)[::2, ::2]
        diff_resp = np.einsum("ijkl,kl->ij", win, kern, optimize=True)
        dot_resp = np.einsum("ijkl,kl->ij", win, k_zero, optimize=True)
        win_sup = win[:, :, support]
        sup_std = np.std(win_sup, axis=2) * math.sqrt(float(support.sum())) + 1e-6
        pearson = dot_resp / (sup_std * k_norm)

        yy_c, xx_c = np.mgrid[0 : pearson.shape[0], 0 : pearson.shape[1]]
        cx_cand = rx0 + (xx_c * 2) + kr
        cy_cand = ry0 + (yy_c * 2) + kr
        in_corner = (cx_cand >= min_cx) & (cy_cand >= min_cy)
        mask_cand = in_corner & (pearson >= 0.45) & (diff_resp >= 15.0)
        if not np.any(mask_cand):
            continue
        score_map = np.where(mask_cand, pearson * np.minimum(diff_resp, 60.0), 0.0)
        iy, ix = np.unravel_index(int(np.argmax(score_map)), score_map.shape)
        peak_score = float(score_map[iy, ix])
        if peak_score > best_score:
            best_score = peak_score
            best_pt = (rx0 + (ix * 2) + kr, ry0 + (iy * 2) + kr, float(r_try))

    if best_pt is None or best_score <= 0.0:
        return rgb_img

    cx_best, cy_best, r_found = best_pt
    r_pad = int(math.ceil(r_found * 1.65))
    bx0 = max(0, cx_best - r_pad)
    by0 = max(0, cy_best - r_pad)
    bx1 = min(w, cx_best + r_pad + 1)
    by1 = min(h, cy_best + r_pad + 1)

    patch = arr_u8[by0:by1, bx0:bx1].astype(np.float32)
    ph, pw = patch.shape[:2]
    yy, xx = np.mgrid[by0:by1, bx0:bx1].astype(np.float32)
    dx = np.abs(xx - cx_best)
    dy = np.abs(yy - cy_best)

    # Nới nhẹ mặt nạ sao ~20% + 2px để bao phủ sạch 100% mép khử răng cưa ở cả 4 chóp nhọn
    r_mask = r_found * 1.24 + 2.0
    astroid = (np.power(dx / r_mask, 0.54) + np.power(dy / r_mask, 0.54)) <= 1.0
    inner_disc = (dx * dx + dy * dy) <= float((r_found * 0.45) ** 2)
    mask = astroid | inner_disc

    mask[:2, :] = False
    mask[-2:, :] = False
    mask[:, :2] = False
    mask[:, -2:] = False

    def _box_blur(arr2d: np.ndarray, rad: int) -> np.ndarray:
        if rad <= 0:
            return arr2d
        p = np.pad(arr2d, ((rad, rad), (rad, rad)), mode="edge")
        csx = np.pad(p.cumsum(axis=1), ((0, 0), (1, 0)), mode="constant")
        hx = (csx[:, 2 * rad + 1 :] - csx[:, : -2 * rad - 1]) / float(2 * rad + 1)
        csy = np.pad(hx.cumsum(axis=0), ((1, 0), (0, 0)), mode="constant")
        return (csy[2 * rad + 1 :, :] - csy[: -2 * rad - 1, :]) / float(2 * rad + 1)

    work = patch.copy()
    valid_vals = work[~mask]
    init_fill = np.median(valid_vals, axis=0) if valid_vals.size else np.array([20.0, 20.0, 20.0], dtype=np.float32)
    work[mask] = init_fill

    # Lấp đa tầng thô -> mịn (10 -> 5 -> 3 -> 1) giúp hội tụ tức thì trên toàn bộ lỗ sao không để lại quầng
    for rad, iters in ((10, 8), (5, 8), (3, 8), (1, 6)):
        for _ in range(iters):
            for ch_idx in range(3):
                blurred = _box_blur(work[:, :, ch_idx], rad)
                work[:, :, ch_idx][mask] = blurred[mask]

    # Bù hạt phim vi mô hòa theo kết cấu nền sạch xung quanh
    if valid_vals.shape[0] > 20:
        lum_orig = patch.mean(axis=2)
        bg_smooth = _box_blur(lum_orig, 2)
        hi_freq = (lum_orig - bg_smooth)[~mask]
        grain_sigma = float(min(4.0, max(0.4, np.std(hi_freq) * 0.45)))
        rng = np.random.default_rng(seed=(w * 10007 + h * 97 + cx_best))
        grain = rng.normal(0.0, grain_sigma, size=(ph, pw, 1)).astype(np.float32)
        work[mask] = np.clip(work[mask] + grain[mask], 0.0, 255.0)

    out_arr = arr_u8.copy()
    out_arr[by0:by1, bx0:bx1] = np.clip(np.rint(work), 0, 255).astype(np.uint8)
    return Image.fromarray(out_arr, mode="RGB")


def crop_flow_watermark(
    img: Any,
    *,
    zoom_factor: float = DEFAULT_FLOW_WATERMARK_ZOOM,
    remove_corner_sparkle: bool = True,
) -> Any:
    """Tự động crop zoom 1.04x (cắt mép phải/dưới ~3.85%) và dọn dấu thoi góc phải dưới cho ảnh AI từ Google Flow.

    LƯU Ý AN TOÀN: CHỈ gọi trên ảnh AI mới tải từ Flow; tuyệt đối KHÔNG gọi trên ảnh tư liệu thật
    trong `archive_sources.json`.
    """
    out = img
    if remove_corner_sparkle:
        out = _erase_flow_corner_sparkle(out)
    z = float(zoom_factor)
    w, h = out.size
    if z > 1.0 and w >= 16 and h >= 16:
        keep_w = max(2, min(w, int(round(w / z))))
        keep_h = max(2, min(h, int(round(h / z))))
        out = out.crop((0, 0, keep_w, keep_h))
    return out


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


def load_house_look_scene_ids(
    project_dir: Union[Path, str, None],
    *,
    prefer_16x9: bool = False,
) -> set[int]:
    """Đọc tập scene_id đã được áp House Look V5.8 từ `pipeline_state.json` (hoặc `pipeline_state_16x9.json`).

    Dùng để chống Double-Grading lúc render: cảnh AI đã có `house_look_applied: true` sẽ chạy chế độ
    pass-through nhẹ (không giảm saturation, không dìm curves, không phủ thêm colorbalance hay noise lần 2).
    """
    if not project_dir:
        return set()
    base = Path(project_dir)
    if base.is_file():
        candidate_files = [base]
    else:
        is_16x9_dir = prefer_16x9 or (base.name == "assets_16x9")
        names = (
            ("pipeline_state_16x9.json", "pipeline_state.json", "house_look_scenes.json")
            if is_16x9_dir
            else ("pipeline_state.json", "house_look_scenes.json", "pipeline_state_16x9.json")
        )
        dirs = [base]
        if base.name in ("assets", "assets_16x9", "raw_images", "raw_images_16x9") and base.parent != base:
            dirs.append(base.parent)
        candidate_files = [d / fn for d in dirs for fn in names]

    scene_ids: set[int] = set()
    for state_path in candidate_files:
        if not state_path.is_file():
            continue
        try:
            raw_text = state_path.read_text(encoding="utf-8-sig").strip()
            if not raw_text:
                continue
            data = json.loads(raw_text)
        except (OSError, ValueError):
            continue

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    if item.get("house_look_applied") is True:
                        sid = _parse_positive_int(item.get("id") or item.get("scene_id") or item.get("scene"))
                        if sid is not None:
                            scene_ids.add(sid)
                else:
                    sid = _parse_positive_int(item)
                    if sid is not None:
                        scene_ids.add(sid)
        elif isinstance(data, dict):
            hl_list = data.get("house_look_scenes")
            if isinstance(hl_list, list):
                for val in hl_list:
                    sid = _parse_positive_int(val)
                    if sid is not None:
                        scene_ids.add(sid)
            scenes_seq = data.get("scenes")
            if isinstance(scenes_seq, list):
                for item in scenes_seq:
                    if isinstance(item, dict) and item.get("house_look_applied") is True:
                        sid = _parse_positive_int(item.get("id") or item.get("scene_id") or item.get("scene"))
                        if sid is not None:
                            scene_ids.add(sid)
        if scene_ids:
            break
    return scene_ids


def build_ai_scene_depolish_vf(
    level: Union[str, bool, None],
    width: int = 1080,
    height: int = 1920,
    *,
    house_look_applied: bool = False,
) -> str:
    """Tạo chuỗi filter V5.8 cho cảnh tạo bằng AI (tích hợp Double-Grading Guard).

    - Khi `house_look_applied=False` (ảnh AI cũ chưa qua `house_look` V5.8): áp đầy đủ `DEPOLISH_SPECS`
      (hqdn3d nhẹ, hạ bão hòa 0.44–0.62, curves low-key, colorbalance slate-blue, noise 35mm, vignette).
    - Khi `house_look_applied=True` (ảnh AI mới đã qua `apply_house_look_to_image` V5.8 sau khi tải):
      chuyển sang chế độ pass-through siêu nhẹ (chỉ scale+crop vi mô & vignette nhẹ, KHÔNG giảm saturation,
      KHÔNG dìm curves, KHÔNG phủ thêm colorbalance hay noise lần 2).
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
    parts = [
        f"scale={sw}:{sh}:flags=lanczos",
        f"crop={w}:{h}:{cx}:{cy}",
        "setsar=1",
    ]
    if house_look_applied:
        if spec.vignette:
            parts.append(spec.vignette)
        return ",".join(parts)

    if spec.denoise:
        parts.append(spec.denoise)
    parts.append(f"eq=saturation={spec.saturation:.2f}")
    parts.append(spec.curves)
    if spec.colorbalance:
        parts.append(spec.colorbalance)
    parts.append(spec.noise)
    if spec.vignette:
        parts.append(spec.vignette)
    return ",".join(parts)


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
