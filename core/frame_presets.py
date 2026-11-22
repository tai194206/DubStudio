"""Cấu hình tập trung các preset khung hình thích ứng (frame_preset) cho Story Mode.

Hỗ trợ 4 chế độ khung hình chuẩn:
- portrait_9_16  : 1080x1920 tràn viền (mặc định, tương thích ngược 100%)
- framed_4_5     : 1080x1920 nền đen, vùng ảnh 1080x1350 tại (x=0, y=210)
- framed_1_1     : 1080x1920 nền đen, vùng ảnh 1080x1080 tại (x=0, y=360)
- landscape_16_9 : 1920x1080 màn hình ngang tràn viền

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

from .errors import DubberError

DEFAULT_FRAME_PRESET = "portrait_9_16"

VALID_FRAME_PRESETS: Tuple[str, ...] = (
    "portrait_9_16",
    "framed_4_5",
    "framed_1_1",
    "landscape_16_9",
)

VALID_FRAME_PRESET_SETTINGS: Tuple[str, ...] = (
    "auto",
    "portrait_9_16",
    "framed_4_5",
    "framed_1_1",
    "landscape_16_9",
)

FRAME_PRESET_GUI_LABELS: Dict[str, str] = {
    "auto": "Tự động theo kịch bản",
    "portrait_9_16": "9:16 Tràn viền",
    "framed_4_5": "4:5 Khung điện ảnh",
    "framed_1_1": "1:1 Khung tư liệu",
    "landscape_16_9": "16:9 Ngang",
}


@dataclass(frozen=True)
class FramePreset:
    name: str
    out_w: int
    out_h: int
    inner_w: int
    inner_h: int
    pad_x: int
    pad_y: int
    aspect_label: str
    filename_suffix: str
    default_sub_margin_v: int
    min_sub_margin_v: int
    sub_margin_l: int
    sub_margin_r: int
    title_font_size: int
    title_margin_v: int
    wm_font_size: int
    wm_margin_v_top: int
    wm_margin_v_bottom: int

    @property
    def needs_pad(self) -> bool:
        return (self.inner_w != self.out_w) or (self.inner_h != self.out_h)

    @property
    def pad_filter(self) -> str:
        if not self.needs_pad:
            return ""
        return f"pad={self.out_w}:{self.out_h}:{self.pad_x}:{self.pad_y}:black"

    @property
    def is_landscape(self) -> bool:
        return self.out_w > self.out_h

    @property
    def bottom_bar_px(self) -> int:
        return max(0, self.out_h - (self.pad_y + self.inner_h))


FRAME_PRESETS: Dict[str, FramePreset] = {
    "portrait_9_16": FramePreset(
        name="portrait_9_16",
        out_w=1080,
        out_h=1920,
        inner_w=1080,
        inner_h=1920,
        pad_x=0,
        pad_y=0,
        aspect_label="9:16",
        filename_suffix="916",
        default_sub_margin_v=400,
        min_sub_margin_v=0,
        sub_margin_l=80,
        sub_margin_r=160,
        title_font_size=44,
        title_margin_v=250,
        wm_font_size=34,
        wm_margin_v_top=160,
        wm_margin_v_bottom=560,
    ),
    "framed_4_5": FramePreset(
        name="framed_4_5",
        out_w=1080,
        out_h=1920,
        inner_w=1080,
        inner_h=1350,
        pad_x=0,
        pad_y=210,
        aspect_label="4:5",
        filename_suffix="45",
        default_sub_margin_v=405,
        min_sub_margin_v=390,
        sub_margin_l=70,
        sub_margin_r=110,
        title_font_size=42,
        title_margin_v=240,
        wm_font_size=32,
        wm_margin_v_top=150,
        wm_margin_v_bottom=560,
    ),
    "framed_1_1": FramePreset(
        name="framed_1_1",
        out_w=1080,
        out_h=1920,
        inner_w=1080,
        inner_h=1080,
        pad_x=0,
        pad_y=360,
        aspect_label="1:1",
        filename_suffix="11",
        default_sub_margin_v=510,
        min_sub_margin_v=500,
        sub_margin_l=70,
        sub_margin_r=110,
        title_font_size=40,
        title_margin_v=390,
        wm_font_size=32,
        wm_margin_v_top=160,
        wm_margin_v_bottom=560,
    ),
    "landscape_16_9": FramePreset(
        name="landscape_16_9",
        out_w=1920,
        out_h=1080,
        inner_w=1920,
        inner_h=1080,
        pad_x=0,
        pad_y=0,
        aspect_label="16:9",
        filename_suffix="169",
        default_sub_margin_v=72,
        min_sub_margin_v=0,
        sub_margin_l=120,
        sub_margin_r=120,
        title_font_size=38,
        title_margin_v=56,
        wm_font_size=28,
        wm_margin_v_top=36,
        wm_margin_v_bottom=48,
    ),
}


def normalize_frame_preset(raw: Any, *, allow_auto: bool = False) -> str:
    """Chuẩn hóa tên frame_preset; báo DubberError nếu giá trị không hợp lệ."""
    if raw is None:
        return "auto" if allow_auto else DEFAULT_FRAME_PRESET
    val = str(raw).strip().lower()
    if not val:
        return "auto" if allow_auto else DEFAULT_FRAME_PRESET
    if val == "auto":
        if allow_auto:
            return "auto"
        return DEFAULT_FRAME_PRESET
    if val in FRAME_PRESETS:
        return val
    accepted = VALID_FRAME_PRESET_SETTINGS if allow_auto else VALID_FRAME_PRESETS
    raise DubberError(
        f"frame_preset '{raw}' không hợp lệ (chấp nhận: {', '.join(accepted)})."
    )


def get_frame_preset(preset: Union[str, FramePreset, None] = None) -> FramePreset:
    """Lấy đối tượng FramePreset từ tên hoặc trả lại chính đối tượng đó."""
    if isinstance(preset, FramePreset):
        return preset
    key = normalize_frame_preset(preset, allow_auto=False)
    return FRAME_PRESETS[key]


def resolve_frame_preset(
    override: Union[str, FramePreset, None] = None,
    storyboard_preset: Union[str, FramePreset, None] = None,
) -> FramePreset:
    """Giải quyết preset cuối cùng: ưu tiên override (nếu != None/''/'auto'), rồi tới storyboard, mặc định 9:16."""
    if isinstance(override, FramePreset):
        return override
    norm_override = normalize_frame_preset(override, allow_auto=True)
    if norm_override != "auto":
        return FRAME_PRESETS[norm_override]

    if isinstance(storyboard_preset, FramePreset):
        return storyboard_preset
    norm_sb = normalize_frame_preset(storyboard_preset, allow_auto=False)
    return FRAME_PRESETS[norm_sb]


def resolve_subtitle_layout(
    preset: Union[str, FramePreset, None],
    *,
    subtitle_style: str = "karaoke_line",
    font_size: int = 52,
    margin_v: Optional[int] = None,
) -> Dict[str, Any]:
    """Tính tọa độ PlayRes, MarginV, MarginL/R và cỡ chữ phụ đề phù hợp từng frame_preset."""
    fp = get_frame_preset(preset)
    raw_fs = int(font_size)

    if fp.is_landscape:
        if subtitle_style == "pop_2words":
            eff_fs = max(44, min(58, int(round(raw_fs * 0.75))))
            outline, shadow = 4.5, 2.5
        elif subtitle_style == "highlight_3words":
            eff_fs = max(42, min(54, int(round(raw_fs * 0.75))))
            outline, shadow = 4.5, 2.5
        else:
            eff_fs = max(34, min(48, int(round(raw_fs * 0.78))))
            outline, shadow = 3.5, 2.0

        if margin_v is None or int(margin_v) >= 200:
            eff_mv = fp.default_sub_margin_v
        else:
            eff_mv = max(24, int(margin_v))

        return {
            "preset": fp,
            "play_res_x": fp.out_w,
            "play_res_y": fp.out_h,
            "font_size": eff_fs,
            "margin_v": eff_mv,
            "margin_l": fp.sub_margin_l,
            "margin_r": fp.sub_margin_r,
            "outline": outline,
            "shadow": shadow,
        }

    # Các preset khung dọc (PlayRes 1080x1920)
    if subtitle_style == "pop_2words":
        eff_fs = max(56, raw_fs)
        def_ml, def_mr = 70, 110
        outline, shadow = 6.0, 3.0
    elif subtitle_style == "highlight_3words":
        eff_fs = max(54, min(raw_fs, 76))
        def_ml, def_mr = 55, 55
        outline, shadow = 6.5, 4.0
    else:
        eff_fs = min(raw_fs, 60)
        def_ml, def_mr = 80, 160
        outline, shadow = 4.0, 2.0

    if fp.name == "portrait_9_16":
        eff_mv = fp.default_sub_margin_v if margin_v is None else int(margin_v)
    elif fp.name == "framed_4_5":
        eff_mv = fp.default_sub_margin_v if (margin_v is None or int(margin_v) < fp.min_sub_margin_v) else int(margin_v)
    elif fp.name == "framed_1_1":
        eff_mv = fp.default_sub_margin_v if (margin_v is None or int(margin_v) < fp.min_sub_margin_v) else int(margin_v)
    else:
        eff_mv = fp.default_sub_margin_v if margin_v is None else int(margin_v)

    return {
        "preset": fp,
        "play_res_x": fp.out_w,
        "play_res_y": fp.out_h,
        "font_size": eff_fs,
        "margin_v": eff_mv,
        "margin_l": def_ml if fp.name == "portrait_9_16" else fp.sub_margin_l,
        "margin_r": def_mr if fp.name == "portrait_9_16" else fp.sub_margin_r,
        "outline": outline,
        "shadow": shadow,
    }
