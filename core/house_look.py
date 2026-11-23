"""Module chuẩn hóa House Look toàn hệ thống V5.8 (Unified Cold Slate-Charcoal + Soft LAB Match).

Cung cấp cơ chế đồng bộ tông màu phim tài liệu chiến trường cho mọi cảnh AI gen từ Google Flow:
  1. Cơ chế tham chiếu 2 tầng:
     - Tầng 1 (Global Default): profile từ `core/assets/house_look_default.jpg` (khung chuẩn Cảnh 8 V5.7).
     - Tầng 2 (Project Override): trường tùy chọn `"look_reference"` trong `storyboard.json`.
  2. Khớp màu mềm trên không gian CIE LAB (`strength=0.62`, nằm trong `0.55–0.70`) giữ nguyên
     chi tiết vùng tối và đốm lửa thực, chống ám vàng giả lên đạo cụ/quân trang.
  3. Gradient ND nhẹ phần trời mép trên (`top_nd_strength=0.20`, trong `0.15–0.25`) ưu tiên dìm trời
     trắng đục thành xám lam giông bão mà không làm tối sầm khuôn mặt nhân vật.
  4. Khử nhiễu siêu nhẹ trước khi phủ lớp hạt phim 35mm đồng nhất (giữ 90% kết cấu thô sắc nét,
     tuyệt đối không làm bệt nhựa AI).
  5. Kiểm định độ đồng bộ tự động (`audit_image_against_house_look`) tính điểm 0–100 và gắn cờ cảnh lệch.

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from PIL import Image, ImageOps

DEFAULT_HOUSE_LOOK_IMAGE = Path(__file__).resolve().parent / "assets" / "house_look_default.jpg"
DEFAULT_HOUSE_LOOK_JSON = Path(__file__).resolve().parent / "assets" / "house_look_default.json"

DEFAULT_HOUSE_LOOK_STRENGTH = 0.64
DEFAULT_TOP_ND_STRENGTH = 0.20

_M_RGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float32,
)

_M_XYZ_TO_RGB = np.array(
    [
        [3.2404542, -1.5371385, -0.4985314],
        [-0.9692660, 1.8760108, 0.0415560],
        [0.0556434, -0.2040259, 1.0572252],
    ],
    dtype=np.float32,
)

_D65_WHITE = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


@dataclass(frozen=True)
class HouseLookProfile:
    """Chữ ký tông màu & độ sáng chuẩn trong không gian CIE LAB / RGB."""

    luma_mean: float
    luma_std: float
    a_mean: float
    a_std: float
    b_mean: float
    b_std: float
    top_sky_luma: float
    sat_mean: float
    cool_index: float
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for k, v in list(data.items()):
            if isinstance(v, float):
                data[k] = round(v, 4)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HouseLookProfile":
        return cls(
            luma_mean=float(data["luma_mean"]),
            luma_std=float(data["luma_std"]),
            a_mean=float(data["a_mean"]),
            a_std=float(data["a_std"]),
            b_mean=float(data["b_mean"]),
            b_std=float(data["b_std"]),
            top_sky_luma=float(data["top_sky_luma"]),
            sat_mean=float(data["sat_mean"]),
            cool_index=float(data["cool_index"]),
            source=str(data.get("source") or ""),
        )


@dataclass(frozen=True)
class HouseLookAuditResult:
    """Kết quả đo kiểm định mức độ đồng bộ House Look của 1 khung hình."""

    luma_mean: float
    luma_std: float
    a_mean: float
    b_mean: float
    top_sky_luma: float
    sat_mean: float
    cool_index: float
    delta_luma: float
    delta_b: float
    delta_sat: float
    consistency_score: float
    passed: bool
    flags: List[str] = field(default_factory=list)


_FALLBACK_PROFILE = HouseLookProfile(
    luma_mean=33.70,
    luma_std=24.38,
    a_mean=-2.22,
    a_std=1.53,
    b_mean=-6.48,
    b_std=4.11,
    top_sky_luma=56.84,
    sat_mean=6.96,
    cool_index=21.04,
    source="fallback_008_v57",
)


def rgb_to_lab(arr_rgb: np.ndarray) -> np.ndarray:
    """Chuyển mảng ảnh RGB (0..255) sang không gian màu CIE LAB (float32)."""
    rgb = np.clip(np.asarray(arr_rgb, dtype=np.float32) / 255.0, 0.0, 1.0)
    lin = np.where(
        rgb <= 0.04045,
        rgb / 12.92,
        np.power(np.clip((rgb + 0.055) / 1.055, 0.0, None), 2.4),
    )
    xyz = (lin @ _M_RGB_TO_XYZ.T) / _D65_WHITE
    eps = 0.008856
    kappa = 903.3
    f = np.where(xyz > eps, np.cbrt(np.clip(xyz, 0.0, None)), (kappa * xyz + 16.0) / 116.0)
    l_ch = 116.0 * f[..., 1] - 16.0
    a_ch = 500.0 * (f[..., 0] - f[..., 1])
    b_ch = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([l_ch, a_ch, b_ch], axis=-1).astype(np.float32)


def _lab_to_rgb_float(arr_lab: np.ndarray) -> np.ndarray:
    """Chuyển mảng CIE LAB (float32) về không gian RGB dạng float32 (0..255)."""
    lab = np.asarray(arr_lab, dtype=np.float32)
    l_ch = lab[..., 0]
    a_ch = lab[..., 1]
    b_ch = lab[..., 2]

    fy = (l_ch + 16.0) / 116.0
    fx = a_ch / 500.0 + fy
    fz = fy - b_ch / 200.0

    eps = 0.008856
    kappa = 903.3
    fx3 = fx * fx * fx
    fz3 = fz * fz * fz

    xr = np.where(fx3 > eps, fx3, (116.0 * fx - 16.0) / kappa)
    yr = np.where(l_ch > kappa * eps, np.power(np.clip((l_ch + 16.0) / 116.0, 0.0, None), 3), l_ch / kappa)
    zr = np.where(fz3 > eps, fz3, (116.0 * fz - 16.0) / kappa)

    xyz = np.stack(
        [xr * _D65_WHITE[0], yr * _D65_WHITE[1], zr * _D65_WHITE[2]],
        axis=-1,
    )
    lin = np.clip(xyz @ _M_XYZ_TO_RGB.T, 0.0, 1.0)
    srgb = np.where(
        lin <= 0.0031308,
        lin * 12.92,
        1.055 * np.power(np.clip(lin, 0.0, None), 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb * 255.0, 0.0, 255.0).astype(np.float32)


def lab_to_rgb(arr_lab: np.ndarray) -> np.ndarray:
    """Chuyển mảng CIE LAB (float32) về mảng uint8 RGB (0..255)."""
    return np.clip(np.rint(_lab_to_rgb_float(arr_lab)), 0, 255).astype(np.uint8)


def _as_rgb_array(img_or_path: Any) -> tuple[np.ndarray, str]:
    if isinstance(img_or_path, (Path, str)):
        p = Path(img_or_path)
        with Image.open(p) as im:
            rgb = ImageOps.exif_transpose(im).convert("RGB")
            return np.asarray(rgb, dtype=np.uint8), p.name
    if isinstance(img_or_path, np.ndarray):
        arr = np.asarray(img_or_path, dtype=np.uint8)
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        return arr[..., :3], ""
    return np.asarray(img_or_path.convert("RGB"), dtype=np.uint8), ""


def measure_house_look_profile(
    img_or_path: Any,
    source: str = "",
) -> HouseLookProfile:
    """Đo chữ ký màu CIE LAB, độ sáng vùng trời 25% trên cùng, chỉ số bão hòa C*ab và cool_index (B - R)."""
    arr_rgb, auto_src = _as_rgb_array(img_or_path)
    lab = rgb_to_lab(arr_rgb)
    h = max(1, lab.shape[0])
    top_h = max(1, int(round(h * 0.25)))

    rgb_f = arr_rgb.astype(np.float32)
    # Dùng CIE LAB Chroma C* = sqrt(a^2 + b^2) để tránh ảo giác nhiễu chia cho 0 trên vùng đen sâu
    chroma_ab = np.sqrt(np.square(lab[..., 1]) + np.square(lab[..., 2]))
    cool_index = float((rgb_f[..., 2] - rgb_f[..., 0]).mean())

    return HouseLookProfile(
        luma_mean=float(lab[..., 0].mean()),
        luma_std=float(lab[..., 0].std()),
        a_mean=float(lab[..., 1].mean()),
        a_std=float(lab[..., 1].std()),
        b_mean=float(lab[..., 2].mean()),
        b_std=float(lab[..., 2].std()),
        top_sky_luma=float(lab[:top_h, :, 0].mean()),
        sat_mean=float(chroma_ab.mean()),
        cool_index=cool_index,
        source=source or auto_src,
    )


def load_default_house_look_profile() -> HouseLookProfile:
    """Nạp House Look mặc định toàn hệ thống (JSON -> ảnh `core/assets/house_look_default.jpg` -> fallback)."""
    if DEFAULT_HOUSE_LOOK_JSON.is_file():
        try:
            raw = json.loads(DEFAULT_HOUSE_LOOK_JSON.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                return HouseLookProfile.from_dict(raw)
        except Exception:
            pass
    if DEFAULT_HOUSE_LOOK_IMAGE.is_file():
        try:
            return measure_house_look_profile(DEFAULT_HOUSE_LOOK_IMAGE, source=DEFAULT_HOUSE_LOOK_IMAGE.name)
        except Exception:
            pass
    return _FALLBACK_PROFILE


def resolve_look_reference(
    storyboard_data: Optional[Dict[str, Any]] = None,
    ep_dir: Optional[Union[Path, str]] = None,
    assets_dir: Optional[Union[Path, str]] = None,
) -> Optional[HouseLookProfile]:
    """Cơ chế House Look 2 tầng:
    - Tầng 2 (Override từng dự án): đọc `storyboard_data["look_reference"]`.
      Nếu `"none"`/`"off"`/`"false"` -> trả `None` (tắt House Look).
      Nếu số cảnh (vd `"008"`, `"8"`) hoặc đường dẫn file -> đo profile từ ảnh đó.
    - Tầng 1 (Global Default): dùng `load_default_house_look_profile()`.
    """
    raw_ref = ""
    if isinstance(storyboard_data, dict):
        val = storyboard_data.get("look_reference")
        if val is False:
            return None
        if isinstance(val, str):
            raw_ref = val.strip()
        elif isinstance(val, int) and val > 0:
            raw_ref = f"{val:03d}"

    low_ref = raw_ref.lower()
    if low_ref in ("none", "off", "false"):
        return None

    if raw_ref:
        ep_p = Path(ep_dir) if ep_dir else None
        as_p = Path(assets_dir) if assets_dir else (ep_p / "assets" if ep_p else None)
        candidates: List[Path] = []
        if raw_ref.isdigit():
            sid = int(raw_ref)
            for d in (as_p, ep_p / "assets" if ep_p else None, ep_p / "raw_images" if ep_p else None):
                if d is not None:
                    for ext in (".jpg", ".jpeg", ".png", ".webp"):
                        candidates.append(d / f"{sid:03d}{ext}")
                        candidates.append(d / f"{sid}{ext}")
        else:
            p_cand = Path(raw_ref)
            if p_cand.is_absolute():
                candidates.append(p_cand)
            else:
                if ep_p is not None:
                    candidates.append(ep_p / p_cand)
                if as_p is not None:
                    candidates.append(as_p / p_cand)
                candidates.append(p_cand)

        for cand in candidates:
            if cand.is_file():
                return measure_house_look_profile(cand, source=f"override:{cand.name}")

    return load_default_house_look_profile()


def _make_deterministic_seed(seed: Optional[Union[int, str]], width: int, height: int) -> int:
    raw = f"{seed if seed is not None else 'house_look'}|{width}x{height}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], "big")


def apply_house_look_to_image(
    img: Any,
    *,
    profile: Optional[HouseLookProfile] = None,
    strength: float = DEFAULT_HOUSE_LOOK_STRENGTH,
    top_nd_strength: float = DEFAULT_TOP_ND_STRENGTH,
    unify_grain: bool = True,
    seed: Optional[Union[int, str]] = None,
) -> Any:
    """Áp House Look V5.8 cho ảnh AI mới tải từ Flow (giữ 100% chi tiết thô, không làm bệt nhựa):
      1. Khớp màu mềm trong không gian CIE LAB (`strength` mặc định 0.62 trong dải 0.55–0.70).
      2. Dìm nhẹ phần trời mép trên (`top_nd_strength` mặc định 0.20 trong dải 0.15–0.25) theo độ sáng trời.
      3. Tiền làm mịn cực nhẹ 10% + phủ hạt phim 35mm Tri-X đơn sắc & chấm bụi lưu trữ đồng bộ.
    """
    ref = profile or load_default_house_look_profile()
    s = max(0.0, min(0.85, float(strength)))
    nd = max(0.0, min(0.35, float(top_nd_strength)))

    if isinstance(img, Image.Image):
        rgb_img = ImageOps.exif_transpose(img).convert("RGB")
    else:
        arr_in, _ = _as_rgb_array(img)
        rgb_img = Image.fromarray(arr_in, mode="RGB")

    arr_u8 = np.asarray(rgb_img, dtype=np.uint8)
    h, w = arr_u8.shape[:2]
    if h < 4 or w < 4:
        return rgb_img

    lab = rgb_to_lab(arr_u8)
    l_ch = lab[..., 0]
    a_ch = lab[..., 1]
    b_ch = lab[..., 2]

    if s > 1e-6:
        src_l_mean = float(l_ch.mean())
        src_l_std = float(l_ch.std())
        src_a_mean = float(a_ch.mean())
        src_a_std = float(a_ch.std())
        src_b_mean = float(b_ch.mean())
        src_b_std = float(b_ch.std())

        # Reinhard chuẩn hóa kèm sàn std tối thiểu tránh khuếch đại nhiễu trên nền gần trung tính
        scale_l = float(np.clip(ref.luma_std / max(src_l_std, 6.0), 0.75, 1.30))
        scale_a = float(np.clip(ref.a_std / max(src_a_std, 2.0), 0.55, 1.30))
        scale_b = float(np.clip(ref.b_std / max(src_b_std, 2.5), 0.55, 1.30))

        l_target = np.clip((l_ch - src_l_mean) * scale_l + ref.luma_mean, 0.0, 100.0)
        a_target = (a_ch - src_a_mean) * scale_a + ref.a_mean
        b_target = (b_ch - src_b_mean) * scale_b + ref.b_mean

        # Hòa trộn mềm giữ chi tiết vùng tối (L), sắc lạnh slate vùng mid/highlight và đốm lửa thực (b lớn)
        l_out = l_ch + (s * 0.65) * (l_target - l_ch)
        w_lum = np.clip((l_ch - 1.0) / 6.0, 0.50, 1.0)
        w_warm = np.clip((b_ch - 10.0) / 18.0, 0.0, 1.0)
        eff_chroma_s = s * w_lum * (1.0 - 0.65 * w_warm)

        a_out = a_ch + eff_chroma_s * (a_target - a_ch)
        b_out = b_ch + eff_chroma_s * (b_target - b_ch)

        # Khử nhẹ sắc vàng tô giả lên đạo cụ/vật liệu trung tính (không đụng lửa/tia sáng thật)
        yellow_mask = (l_out < 72.0) & (b_out > 1.5) & (b_out < 13.0)
        cool_weight = min(1.0, s / 0.64)
        if np.any(yellow_mask):
            b_cooled = b_out * 0.45 - 1.0
            b_out = np.where(yellow_mask, b_out + cool_weight * (b_cooled - b_out), b_out)
    else:
        l_out = l_ch.copy()
        a_out = a_ch.copy()
        b_out = b_ch.copy()

    # Gradient ND nhẹ mép trên (top 30% chiều cao, trọng số độ sáng giữ nguyên khuôn mặt tối ở cận cảnh)
    if nd > 1e-6:
        band_h = max(1, int(round(h * 0.30)))
        y_coords = np.arange(band_h, dtype=np.float32)
        ramp = np.power((band_h - y_coords) / float(band_h), 1.6)[:, None]
        l_top = l_out[:band_h, :]
        sky_w = 0.55 + 0.45 * np.clip((l_top - 30.0) / 45.0, 0.0, 1.0)
        attenuation = nd * ramp * sky_w
        l_out[:band_h, :] = np.clip(l_top * (1.0 - attenuation), 0.0, 100.0)
        b_out[:band_h, :] = b_out[:band_h, :] - 2.2 * attenuation

    lab_out = np.stack([np.clip(l_out, 0.0, 100.0), a_out, b_out], axis=-1)
    rgb_f = _lab_to_rgb_float(lab_out)

    if unify_grain:
        # Tiền làm mịn cực nhẹ 10% (giữ trọn 90% độ gai góc kết cấu vải/da/đất)
        padded = np.pad(rgb_f, ((1, 1), (1, 1), (0, 0)), mode="edge")
        blur3 = (
            padded[:-2, :-2]
            + padded[:-2, 1:-1]
            + padded[:-2, 2:]
            + padded[1:-1, :-2]
            + padded[1:-1, 1:-1]
            + padded[1:-1, 2:]
            + padded[2:, :-2]
            + padded[2:, 1:-1]
            + padded[2:, 2:]
        ) / 9.0
        rgb_f = 0.90 * rgb_f + 0.10 * blur3

        # Hạt phim 35mm Tri-X đơn sắc đồng nhất theo trọng số midtone
        rng = np.random.default_rng(_make_deterministic_seed(seed, w, h))
        grain = rng.normal(0.0, 4.0, size=(h, w)).astype(np.float32)
        w_mid = 1.0 - 0.45 * np.square(np.clip((l_out / 100.0 - 0.45) / 0.55, -1.0, 1.0))
        grain_3d = (grain * w_mid)[:, :, None]
        rgb_f = np.clip(rgb_f + grain_3d, 0.0, 255.0)

        # Vài vết bụi/xước lưu trữ siêu mảnh (1px rộng, 1..6px cao, mờ 14%) đồng bộ hạt phim
        n_specks = 12
        xs = rng.integers(8, max(9, w - 8), size=n_specks)
        ys = rng.integers(8, max(9, h - 14), size=n_specks)
        lens = rng.integers(1, 7, size=n_specks)
        bright = rng.random(size=n_specks) > 0.35
        for x_pt, y_pt, tick_len, is_bright in zip(xs, ys, lens, bright):
            val = 215.0 if is_bright else 25.0
            rgb_f[y_pt : y_pt + tick_len, x_pt, :] = (
                0.86 * rgb_f[y_pt : y_pt + tick_len, x_pt, :] + 0.14 * val
            )

    out_u8 = np.clip(np.rint(rgb_f), 0, 255).astype(np.uint8)
    return Image.fromarray(out_u8, mode="RGB")


def audit_image_against_house_look(
    img_or_path: Any,
    *,
    profile: Optional[HouseLookProfile] = None,
) -> HouseLookAuditResult:
    """Đo định lượng mức độ đồng bộ House Look (0–100 điểm) và tự gắn cờ nếu lệch chuẩn."""
    ref = profile or load_default_house_look_profile()
    cur = measure_house_look_profile(img_or_path)

    delta_luma = cur.luma_mean - ref.luma_mean
    delta_b = cur.b_mean - ref.b_mean
    delta_sat = cur.sat_mean - ref.sat_mean

    flags: List[str] = []
    if abs(delta_luma) > 14.0:
        flags.append(f"Độ sáng lệch chuẩn ({cur.luma_mean:.1f} vs {ref.luma_mean:.1f})")
    if abs(delta_b) > 4.5 or (ref.b_mean <= -3.0 and cur.b_mean > -1.0):
        flags.append(f"Tông màu chưa nghiêng lạnh slate-blue (b={cur.b_mean:+.1f} vs {ref.b_mean:+.1f})")
    if abs(delta_sat) > 12.0:
        flags.append(f"Độ bão hòa lệch chuẩn (C*={cur.sat_mean:.1f} vs {ref.sat_mean:.1f})")
    if cur.top_sky_luma > 68.0:
        flags.append(f"Trời mép trên quá sáng (topL={cur.top_sky_luma:.1f} > 68)")

    raw_score = (
        100.0
        - 1.8 * abs(delta_luma)
        - 4.2 * abs(delta_b)
        - 1.1 * abs(delta_sat)
        - 1.5 * max(0.0, cur.top_sky_luma - 62.0)
    )
    score = round(max(0.0, min(100.0, raw_score)), 1)
    passed = (len(flags) == 0) and (score >= 70.0)

    return HouseLookAuditResult(
        luma_mean=round(cur.luma_mean, 2),
        luma_std=round(cur.luma_std, 2),
        a_mean=round(cur.a_mean, 2),
        b_mean=round(cur.b_mean, 2),
        top_sky_luma=round(cur.top_sky_luma, 2),
        sat_mean=round(cur.sat_mean, 2),
        cool_index=round(cur.cool_index, 2),
        delta_luma=round(delta_luma, 2),
        delta_b=round(delta_b, 2),
        delta_sat=round(delta_sat, 2),
        consistency_score=score,
        passed=passed,
        flags=flags,
    )
