"""Lõi dựng phim Story Mode 9:16 cho DubStudio (v3.2 - Smart Clip Fit, Xfade, TikTok Pop & Watermark).

Quy trình:
1. Khớp tài nguyên ảnh tĩnh/clip theo id cảnh hoặc tên địa điểm (locations).
2. Sinh TTS & đo thời lượng (có AudioCache hash), căn mốc WordTiming.
3. Sinh phụ đề ASS (nhảy 1-2 chữ nổi bật / Karaoke câu) + Tiêu đề cảnh (on_screen_text) + Watermark kênh.
4. Render từng cảnh/run 9:16 với Smart Clip Fit (kéo giãn mượt clip MP4 ngắn) hoặc Continuous Zoom.
5. Ghép nối video (cắt cứng hoặc xfade mượt có bù thời lượng giữ khớp 100% âm thanh).
6. Hậu kỳ màu điện ảnh, tối ưu giảm dấu vết AI, trộn Voice + BGM fade/ducking + SFX + Loudnorm (-11 LUFS).

Tuyệt đối KHÔNG import tkinter hay gui/ trong module này.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from pysubs2 import Alignment, Color, SSAEvent, SSAFile, SSAStyle

from .anti_ai_filter import (
    build_ai_scene_depolish_vf,
    get_clean_metadata_flags,
    load_archival_scene_ids,
    normalize_anti_ai_level,
)
from .audio_track import SR, fade_edges, read_wav, write_wav
from .cache import AudioCache, compute_cache_key
from .config import OUTPUT_DIR, ROOT_DIR, WORK_ROOT, DubSettings, Keys
from .elevenlabs_alignment import (
    align_scenes_with_words,
    chars_to_words,
    export_karaoke_ass,
    generate_voice_with_timestamps,
)
from .errors import DubberError
from .ffmpeg_utils import change_speed, probe, run_ffmpeg, to_wav
from .frame_presets import (
    FramePreset,
    get_frame_preset,
    resolve_frame_preset,
    resolve_subtitle_layout,
)
from .models import Segment, WordTiming
from .reporter import Reporter
from .sfx_engine import find_sfx_file, load_or_synthesize_sfx
from .story_schema import Storyboard, load_storyboard
from .tts import TTSEngine, ToneTTS, make_engine

_FMT = "aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
ASSET_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
ASSET_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")

VALID_TRANSITIONS = {"none", "fade", "dissolve", "smoothleft", "wipeleft"}
VALID_WATERMARK_POS = {"top_left", "top_right", "bottom_left"}

CINEMATIC_GRADE_FILTER = (
    "colorbalance=rs=0.05:gs=0.01:bs=-0.05,"
    "noise=alls=3:allf=t,"
    "vignette=PI/6"
)


def _parse_hex_color(hex_str: str, fallback: Tuple[int, int, int] = (255, 224, 0), alpha: int = 0) -> Color:
    """Chuyển mã màu #RRGGBB sang pysubs2.Color an toàn."""
    raw = (hex_str or "").strip().lstrip("#")
    if len(raw) == 6:
        try:
            r = int(raw[0:2], 16)
            g = int(raw[2:4], 16)
            b = int(raw[4:6], 16)
            return Color(r, g, b, alpha)
        except ValueError:
            pass
    return Color(fallback[0], fallback[1], fallback[2], alpha)


def find_scene_asset(assets_dir: Path | str, scene_id: int) -> Optional[Path]:
    """Tìm tài nguyên cho cảnh theo thứ tự ưu tiên:
    1. Clip {id:03d}.mp4 / {id}.mp4
    2. Ảnh {id:03d}.png / .jpg / .jpeg / ...
    3. Ảnh {id}.png / .jpg / .jpeg / ...
    """
    assets_dir = Path(assets_dir)
    if not assets_dir.is_dir():
        return None

    for ext in ASSET_VIDEO_EXTS:
        for pattern in (f"{scene_id:03d}{ext}", f"{scene_id}{ext}", f"{scene_id:03d}{ext.upper()}", f"{scene_id}{ext.upper()}"):
            p = assets_dir / pattern
            if p.is_file():
                return p

    for ext in ASSET_IMAGE_EXTS:
        for pattern in (f"{scene_id:03d}{ext}", f"{scene_id}{ext}", f"{scene_id:03d}{ext.upper()}", f"{scene_id}{ext.upper()}"):
            p = assets_dir / pattern
            if p.is_file():
                return p

    return None


def find_location_asset(
    sb: Storyboard,
    assets_dir: Path | str,
    loc_name: str,
    fallback_scene_id: Optional[int] = None,
    asset_map: Optional[Dict[int, Path]] = None,
) -> Optional[Path]:
    """Tìm file ảnh/clip cho một địa điểm (location) trong chế độ shot_mode='planned'."""
    assets_dir = Path(assets_dir)

    mapped_filename = sb.resolve_location_filename(loc_name)
    if mapped_filename:
        candidate = assets_dir / mapped_filename
        if candidate.is_file():
            return candidate

    for ext in (*ASSET_VIDEO_EXTS, *ASSET_IMAGE_EXTS):
        for name_pat in (f"{loc_name}{ext}", f"{loc_name}{ext.upper()}"):
            candidate = assets_dir / name_pat
            if candidate.is_file():
                return candidate

    if asset_map:
        if fallback_scene_id is not None and fallback_scene_id in asset_map:
            return asset_map[fallback_scene_id]
        return next(iter(asset_map.values()), None)

    if fallback_scene_id is not None:
        return find_scene_asset(assets_dir, fallback_scene_id)

    return None


def validate_story_assets(storyboard: Storyboard, assets_dir: Path | str) -> Tuple[Dict[int, Path], List[int]]:
    """Kiểm tra sự tồn tại tài nguyên cho toàn bộ các cảnh trong storyboard."""
    assets_dir = Path(assets_dir)
    storyboard.ensure_scenes_from_beats()

    found: Dict[int, Path] = {}
    missing: List[int] = []

    beat_loc_map: Dict[int, str] = {}
    for idx, b in enumerate(storyboard.beats or [], start=1):
        if isinstance(b, dict):
            b_id = int(b.get("id", idx))
            beat_loc_map[b_id] = str(b.get("location", "")).strip()

    for sc in storyboard.scenes:
        asset = find_scene_asset(assets_dir, sc.id)
        if asset is None and sc.id in beat_loc_map:
            asset = find_location_asset(storyboard, assets_dir, beat_loc_map[sc.id])
        if asset is not None:
            found[sc.id] = asset
        else:
            missing.append(sc.id)

    return found, missing


def create_tiktok_karaoke_style(
    font_name: str = "Arial",
    font_size: int = 52,
    margin_v: int = 400,
    primary_hex: str = "#FFE600",
    *,
    margin_l: int = 80,
    margin_r: int = 160,
    outline: float = 4.0,
    shadow: float = 2.0,
) -> SSAStyle:
    """Tạo kiểu phụ đề Karaoke .ass chuẩn vùng an toàn."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    style.primarycolor = _parse_hex_color(primary_hex, (255, 230, 0), alpha=0)
    style.secondarycolor = Color(255, 255, 255, 0)
    style.outlinecolor = Color(0, 0, 0, 0)
    style.backcolor = Color(0, 0, 0, 128)

    style.borderstyle = 1
    style.outline = float(outline)
    style.shadow = float(shadow)
    style.alignment = Alignment.BOTTOM_CENTER
    style.marginv = margin_v
    style.marginl = margin_l
    style.marginr = margin_r
    return style


def create_tiktok_pop_style(
    font_name: str = "Arial",
    font_size: int = 72,
    margin_v: int = 390,
    primary_hex: str = "#FFE000",
    *,
    margin_l: int = 70,
    margin_r: int = 110,
    outline: float = 6.0,
    shadow: float = 3.0,
) -> SSAStyle:
    """Kiểu phụ đề nảy 1-2 chữ nổi bật ở vùng dưới an toàn."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    style.primarycolor = _parse_hex_color(primary_hex, (255, 224, 0), alpha=0)
    style.secondarycolor = Color(255, 255, 255, 0)
    style.outlinecolor = Color(0, 0, 0, 0)
    style.backcolor = Color(0, 0, 0, 165)

    style.borderstyle = 1
    style.outline = float(outline)
    style.shadow = float(shadow)
    style.alignment = Alignment.BOTTOM_CENTER
    style.marginv = margin_v
    style.marginl = margin_l
    style.marginr = margin_r
    return style


def _color_to_ass_bgr(hex_str: str, fallback: Tuple[int, int, int] = (255, 224, 0)) -> str:
    """Chuyển mã màu #RRGGBB sang chuỗi màu ASS &HBBGGRR&."""
    c = _parse_hex_color(hex_str, fallback, alpha=0)
    return f"&H{c.b:02X}{c.g:02X}{c.r:02X}&"


def create_tiktok_highlight3_style(
    font_name: str = "Arial",
    font_size: int = 66,
    margin_v: int = 400,
    *,
    margin_l: int = 55,
    margin_r: int = 55,
    outline: float = 6.5,
    shadow: float = 4.0,
) -> SSAStyle:
    """Kiểu phụ đề cụm tối đa 3 chữ IN HOA (trắng viền đen đậm + bóng sâu, từ đang đọc sáng màu nổi bật)."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True
    style.italic = True

    style.primarycolor = Color(255, 255, 255, 0)
    style.secondarycolor = Color(255, 255, 255, 0)
    style.outlinecolor = Color(0, 0, 0, 0)
    style.backcolor = Color(0, 0, 0, 120)

    style.borderstyle = 1
    style.outline = float(outline)
    style.shadow = float(shadow)
    style.alignment = Alignment.BOTTOM_CENTER
    style.marginv = margin_v
    style.marginl = margin_l
    style.marginr = margin_r
    return style


def _group_words_into_highlight_chunks(
    words: Sequence[Any],
    max_words: int = 3,
) -> List[List[Tuple[str, int, int]]]:
    """Chia các từ trong CÙNG 1 câu/cảnh thành cụm tối đa 3 chữ.

    Luôn đóng cụm tại cuối câu/cảnh (nếu cuối câu lẻ 1 hoặc 2 chữ thì giữ nguyên cụm 1-2 chữ,
    tuyệt đối không mượn chữ của cảnh/câu kế tiếp).
    """
    items: List[Tuple[str, int, int, bool]] = []
    for w in words:
        raw_txt, st, en = _extract_word_tuple(w)
        if not raw_txt:
            continue
        is_sent_end = raw_txt.endswith((".", "!", "?"))
        # Dọn dấu phẩy/chấm cuối từ để cụm IN HOA sắc gọn như CapCut, giữ lại ? !
        cleaned = raw_txt.rstrip(",.;:")
        disp = (cleaned if cleaned else raw_txt).upper()
        st_ms = max(0, int(round(st * 1000)))
        en_ms = max(st_ms + 80, int(round(en * 1000)))
        items.append((disp, st_ms, en_ms, is_sent_end))

    chunks: List[List[Tuple[str, int, int]]] = []
    cur: List[Tuple[str, int, int]] = []
    for disp, st_ms, en_ms, is_sent_end in items:
        cur.append((disp, st_ms, en_ms))
        if len(cur) >= max_words or is_sent_end:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def create_scene_title_style(
    font_name: str = "Arial",
    font_size: int = 44,
    *,
    margin_v: int = 250,
    margin_l: int = 60,
    margin_r: int = 60,
    outline: float = 4.0,
    shadow: float = 2.5,
) -> SSAStyle:
    """Kiểu tiêu đề sử thi (on_screen_text) đặt nổi bật ở phần trên khung hình."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    style.primarycolor = Color(255, 245, 210, 0)
    style.secondarycolor = Color(255, 245, 210, 0)
    style.outlinecolor = Color(15, 10, 5, 0)
    style.backcolor = Color(0, 0, 0, 150)

    style.borderstyle = 1
    style.outline = float(outline)
    style.shadow = float(shadow)
    style.alignment = Alignment.TOP_CENTER
    style.marginv = margin_v
    style.marginl = margin_l
    style.marginr = margin_r
    return style


def create_watermark_style(
    font_name: str = "Arial",
    font_size: int = 34,
    pos: str = "top_left",
    opacity_pct: int = 65,
    *,
    margin_v_top: int = 160,
    margin_v_bottom: int = 560,
    margin_side: int = 44,
) -> SSAStyle:
    """Kiểu Watermark tên kênh (hỗ trợ top_left, top_right, bottom_left và độ mờ tùy chỉnh)."""
    style = SSAStyle()
    style.fontname = font_name
    style.fontsize = font_size
    style.bold = True

    clamped_op = max(20, min(100, int(opacity_pct)))
    alpha = int(round((100 - clamped_op) * 2.55))
    outline_alpha = min(255, alpha + 30)

    style.primarycolor = Color(255, 255, 255, alpha)
    style.secondarycolor = Color(255, 255, 255, alpha)
    style.outlinecolor = Color(0, 0, 0, outline_alpha)
    style.backcolor = Color(0, 0, 0, min(255, alpha + 70))

    style.borderstyle = 1
    style.outline = 2.5
    style.shadow = 1.5

    if pos == "top_right":
        style.alignment = Alignment.TOP_RIGHT
        style.marginl = 40
        style.marginr = margin_side
        style.marginv = margin_v_top
    elif pos == "bottom_left":
        style.alignment = Alignment.BOTTOM_LEFT
        style.marginl = margin_side
        style.marginr = 40
        style.marginv = margin_v_bottom  # né cụm caption của TikTok
    else:
        style.alignment = Alignment.TOP_LEFT
        style.marginl = margin_side
        style.marginr = 40
        style.marginv = margin_v_top
    return style


def _extract_word_tuple(w: Any) -> Tuple[str, float, float]:
    """Trích (word, start, end) an toàn từ cả WordTiming object lẫn dict."""
    if isinstance(w, dict):
        text = str(w.get("word") or w.get("text") or "").strip()
        st = float(w.get("start", 0.0))
        en = float(w.get("end", st + float(w.get("duration", 0.15))))
        return text, st, max(en, st + 0.05)
    text = str(getattr(w, "word", "")).strip()
    st = float(getattr(w, "start", 0.0))
    en = float(getattr(w, "end", st + float(getattr(w, "duration", 0.15))))
    return text, st, max(en, st + 0.05)


def _group_words_for_pop(words: Sequence[Any], max_words: int = 1) -> List[Tuple[str, int, int]]:
    """Gom danh sách từ thành các cụm 1-2 chữ ngắn gọn bật nảy theo đúng nhịp đọc."""
    tuples = [_extract_word_tuple(w) for w in words]
    tuples = [t for t in tuples if t[0]]
    if not tuples:
        return []

    chunks: List[Tuple[str, int, int]] = []
    i = 0
    n = len(tuples)
    punct_ends = (".", ",", "!", "?", ";", ":", "—", "…")

    while i < n:
        w1, s1, e1 = tuples[i]
        take_two = False
        if max_words >= 2 and i + 1 < n:
            w2, s2, e2 = tuples[i + 1]
            if (
                not w1.endswith(punct_ends)
                and len(w1) <= 7
                and (len(w1) + len(w2) + 1) <= 13
                and (s2 - e1) <= 0.35
            ):
                take_two = True

        if take_two:
            w2, s2, e2 = tuples[i + 1]
            label = f"{w1} {w2}"
            start_ms = int(round(s1 * 1000))
            end_ms = max(start_ms + 90, int(round(e2 * 1000)))
            chunks.append((label, start_ms, end_ms))
            i += 2
        else:
            start_ms = int(round(s1 * 1000))
            end_ms = max(start_ms + 90, int(round(e1 * 1000)))
            chunks.append((w1, start_ms, end_ms))
            i += 1

    return chunks


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def generate_story_karaoke_ass(
    segments: Sequence[Segment],
    out_path: Path | str,
    *,
    font_size: int = 52,
    font_name: str = "Arial",
    use_kf: bool = True,
    subtitle_style: str = "karaoke_line",
    sub_margin_v: Optional[int] = None,
    sub_color: str = "#FFE000",
    watermark_text: Optional[str] = None,
    watermark_pos: str = "top_left",
    watermark_opacity: int = 65,
    scene_titles: Optional[Sequence[Tuple[float, float, str]]] = None,
    total_duration: float = 0.0,
    burn_subtitles: bool = True,
    frame_preset: Union[str, FramePreset] = "portrait_9_16",
) -> Path:
    """Sinh file .ass gồm phụ đề, tiêu đề cảnh (on_screen_text) và Watermark theo frame_preset."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    layout = resolve_subtitle_layout(
        frame_preset,
        subtitle_style=subtitle_style,
        font_size=font_size,
        margin_v=sub_margin_v,
    )
    fp: FramePreset = layout["preset"]

    subs = SSAFile()
    subs.info["PlayResX"] = layout["play_res_x"]
    subs.info["PlayResY"] = layout["play_res_y"]

    karaoke_style_name = "KaraokeTikTok"
    pop_style_name = "PopWordTikTok"
    hl3_style_name = "Highlight3TikTok"
    title_style_name = "SceneTitleTikTok"
    wm_style_name = "ChannelWatermark"

    karaoke_layout = (
        layout
        if subtitle_style == "karaoke_line"
        else resolve_subtitle_layout(
            fp,
            subtitle_style="karaoke_line",
            font_size=52,
            margin_v=sub_margin_v,
        )
    )
    subs.styles[karaoke_style_name] = create_tiktok_karaoke_style(
        font_name=font_name,
        font_size=karaoke_layout["font_size"],
        margin_v=karaoke_layout["margin_v"],
        primary_hex=sub_color,
        margin_l=karaoke_layout["margin_l"],
        margin_r=karaoke_layout["margin_r"],
        outline=karaoke_layout["outline"],
        shadow=karaoke_layout["shadow"],
    )
    if subtitle_style == "pop_2words":
        subs.styles[pop_style_name] = create_tiktok_pop_style(
            font_name=font_name,
            font_size=layout["font_size"],
            margin_v=layout["margin_v"],
            primary_hex=sub_color,
            margin_l=layout["margin_l"],
            margin_r=layout["margin_r"],
            outline=layout["outline"],
            shadow=layout["shadow"],
        )
    elif subtitle_style == "highlight_3words":
        subs.styles[hl3_style_name] = create_tiktok_highlight3_style(
            font_name=font_name,
            font_size=layout["font_size"],
            margin_v=layout["margin_v"],
            margin_l=layout["margin_l"],
            margin_r=layout["margin_r"],
            outline=layout["outline"],
            shadow=layout["shadow"],
        )

    max_end_ms = int(round(total_duration * 1000)) if total_duration > 0 else 0

    if burn_subtitles:
        if subtitle_style == "pop_2words":
            # Đẩy sớm 70ms để bù độ trễ nhận âm của Whisper & độ trễ mắt nhìn (giống nhịp CapCut/TikTok)
            LEAD_MS = 70
            all_pop_events: List[SSAEvent] = []
            for seg in segments:
                if not seg.text.strip():
                    continue
                seg_words = list(seg.words) if seg.words else []
                if not seg_words:
                    from .tts import estimate_word_timings
                    dur = max(0.2, seg.end - seg.start)
                    est = estimate_word_timings(seg.text, dur)
                    seg_words = [w.scale_and_shift(speed=1.0, offset=seg.start) for w in est]

                chunks = _group_words_for_pop(seg_words, max_words=1)
                for label, st_ms, en_ms in chunks:
                    adj_st = max(0, st_ms - LEAD_MS)
                    adj_en = max(adj_st + 60, en_ms - LEAD_MS)
                    # Bật nảy siêu nhanh đúng 1 frame (30ms) để bám sát giọng đọc nhanh
                    pop_tag = r"{\fscx108\fscy108\t(0,30,\fscx100\fscy100)}"
                    ev_text = f"{pop_tag}{_escape_ass_text(label)}"
                    all_pop_events.append(
                        SSAEvent(start=adj_st, end=adj_en, text=ev_text, style=pop_style_name, layer=2)
                    )
                    if adj_en > max_end_ms:
                        max_end_ms = adj_en

            # Cắt nối mượt: TUYỆT ĐỐI giữ nguyên mốc bắt đầu của từ sau, chỉ cắt đuôi từ trước
            all_pop_events.sort(key=lambda ev: (ev.start, ev.end))
            for idx in range(len(all_pop_events) - 1):
                cur_ev = all_pop_events[idx]
                nxt_ev = all_pop_events[idx + 1]
                if nxt_ev.start <= cur_ev.start:
                    nxt_ev.start = cur_ev.start + 50
                    nxt_ev.end = max(nxt_ev.start + 60, nxt_ev.end)
                gap = nxt_ev.start - cur_ev.end
                if gap < 0 or gap <= 160:
                    # Nếu trùng nhau hoặc khoảng hở rất nhỏ (<160ms), nối sát để chữ đổi tức thì không chớp tắt
                    cur_ev.end = nxt_ev.start

            subs.events.extend(all_pop_events)
        elif subtitle_style == "highlight_3words":
            # Cụm tối đa 3 chữ IN HOA (mỗi câu/cảnh tách cụm độc lập, lẻ 1-2 chữ cuối giữ nguyên không mượn cảnh sau)
            LEAD_MS = 70
            active_bgr = _color_to_ass_bgr(sub_color, (255, 224, 0))
            white_bgr = "&HFFFFFF&"
            all_hl3_events: List[Tuple[SSAEvent, int]] = []  # (event, chunk_uid)
            chunk_uid = 0

            for seg in segments:
                if not seg.text.strip():
                    continue
                seg_words = list(seg.words) if seg.words else []
                if not seg_words:
                    from .tts import estimate_word_timings
                    dur = max(0.2, seg.end - seg.start)
                    est = estimate_word_timings(seg.text, dur)
                    seg_words = [w.scale_and_shift(speed=1.0, offset=seg.start) for w in est]

                chunks = _group_words_into_highlight_chunks(seg_words, max_words=3)
                for chunk in chunks:
                    chunk_uid += 1
                    labels = [_escape_ass_text(tok) for tok, _, _ in chunk]
                    for k, (_, st_ms, en_ms) in enumerate(chunk):
                        adj_st = max(0, st_ms - LEAD_MS)
                        adj_en = max(adj_st + 60, en_ms - LEAD_MS)
                        # Chữ đang đọc sáng rực màu nổi bật, các chữ khác trong cụm giữ màu trắng
                        rendered_words: List[str] = []
                        for wi, lbl in enumerate(labels):
                            if wi == k:
                                rendered_words.append(f"{{\\1c{active_bgr}}}{lbl}{{\\1c{white_bgr}}}")
                            else:
                                rendered_words.append(lbl)
                        # Thêm blur nhẹ viền bóng để tạo quầng tối nổi chữ giống phong cách CapCut
                        ev_text = r"{\blur1.0}" + " ".join(rendered_words)
                        ev = SSAEvent(start=adj_st, end=adj_en, text=ev_text, style=hl3_style_name, layer=2)
                        all_hl3_events.append((ev, chunk_uid))
                        if adj_en > max_end_ms:
                            max_end_ms = adj_en

            all_hl3_events.sort(key=lambda pair: (pair[0].start, pair[0].end))
            for idx in range(len(all_hl3_events) - 1):
                cur_ev, cur_cid = all_hl3_events[idx]
                nxt_ev, nxt_cid = all_hl3_events[idx + 1]
                if nxt_ev.start <= cur_ev.start:
                    nxt_ev.start = cur_ev.start + 50
                    nxt_ev.end = max(nxt_ev.start + 60, nxt_ev.end)
                gap = nxt_ev.start - cur_ev.end
                if cur_cid == nxt_cid:
                    # Cùng 1 cụm 3 chữ: luôn nối sát để cụm đứng yên không chớp tắt khi đổi từ sáng
                    cur_ev.end = nxt_ev.start
                elif gap < 0 or gap <= 160:
                    # Khác cụm nhưng sát nhau: nối mượt không chớp; nếu hết câu nghỉ dài (>160ms) thì tắt gọn
                    cur_ev.end = nxt_ev.start

            subs.events.extend(ev for ev, _ in all_hl3_events)
        else:
            k_tag = r"\kf" if use_kf else r"\k"
            for seg in segments:
                if not seg.text.strip():
                    continue

                start_ms = int(round(seg.start * 1000))
                end_ms = int(round(seg.end * 1000))

                if seg.words:
                    _, w0_s, _ = _extract_word_tuple(seg.words[0])
                    _, _, wn_e = _extract_word_tuple(seg.words[-1])
                    start_ms = min(start_ms, int(round(w0_s * 1000)))
                    end_ms = max(end_ms, int(round(wn_e * 1000)))

                    parts: List[str] = []
                    for w in seg.words:
                        w_txt, ws, we = _extract_word_tuple(w)
                        dur_cs = max(1, int(round((we - ws) * 100)))
                        parts.append(f"{{{k_tag}{dur_cs}}}{_escape_ass_text(w_txt)}")
                    event_text = " ".join(parts)
                else:
                    event_text = _escape_ass_text(seg.text)

                if end_ms > max_end_ms:
                    max_end_ms = end_ms
                subs.events.append(SSAEvent(start=start_ms, end=end_ms, text=event_text, style=karaoke_style_name, layer=2))

    # Gắn tiêu đề cảnh on_screen_text (nếu bật)
    if scene_titles:
        subs.styles[title_style_name] = create_scene_title_style(
            font_name=font_name,
            font_size=fp.title_font_size,
            margin_v=fp.title_margin_v,
            margin_l=100 if fp.is_landscape else 60,
            margin_r=100 if fp.is_landscape else 60,
            outline=3.2 if fp.is_landscape else 4.0,
            shadow=2.0 if fp.is_landscape else 2.5,
        )
        for st_s, en_s, raw_title in scene_titles:
            title_txt = (raw_title or "").strip()
            if not title_txt:
                continue
            st_ms = max(0, int(round(st_s * 1000)))
            en_ms = max(st_ms + 400, int(round(en_s * 1000)))
            if en_ms > max_end_ms:
                max_end_ms = en_ms
            styled = r"{\fad(180,220)}" + _escape_ass_text(title_txt.upper())
            subs.events.append(SSAEvent(start=st_ms, end=en_ms, text=styled, style=title_style_name, layer=1))

    # Gắn Watermark tên kênh (hoạt động độc lập kể cả khi tắt phụ đề)
    clean_wm = (watermark_text or "").strip()
    if clean_wm:
        subs.styles[wm_style_name] = create_watermark_style(
            font_name=font_name,
            font_size=fp.wm_font_size,
            pos=watermark_pos,
            opacity_pct=watermark_opacity,
            margin_v_top=fp.wm_margin_v_top,
            margin_v_bottom=fp.wm_margin_v_bottom,
        )
        wm_end_ms = max(max_end_ms, 1000)
        subs.events.append(
            SSAEvent(
                start=0,
                end=wm_end_ms,
                text=_escape_ass_text(clean_wm),
                style=wm_style_name,
                layer=0,
            )
        )

    subs.save(str(out_path), encoding="utf-8")
    return out_path


def _add_subtle_room_tone(wav_path: Path) -> None:
    """Chèn lớp âm nền phòng thu cực nhỏ (~ -60 dB) giúp xóa các khoảng lặng số 0.000 đặc trưng của TTS."""
    try:
        x = read_wav(wav_path)
        if x.size < 16:
            return
        rng = np.random.default_rng()
        raw_noise = rng.normal(0.0, 0.00065, size=x.size).astype(np.float32)
        smooth = np.convolve(raw_noise, np.array([0.25, 0.5, 0.25], dtype=np.float32), mode="same")
        out = np.clip(x + smooth, -1.0, 1.0)
        write_wav(wav_path, out)
    except Exception:
        pass


def _build_video_clip_cmd(
    asset_file: Path,
    target_dur: float,
    out_clip: Path,
    *,
    smart_clip_fit: bool = True,
    extra_vf: str = "",
    frame_preset: Union[str, FramePreset] = "portrait_9_16",
) -> List[str]:
    """Xây dựng lệnh FFmpeg render clip MP4 theo frame_preset.

    Nếu clip gốc ngắn hơn target_dur không quá 45% (ví dụ clip Flow 5s gặp câu 6.5s),
    tự động kéo giãn nhẹ tốc độ bằng setpts thay vì lặp lại từ đầu gây giật hình.
    Mọi extra_vf (như khử bóng Anti-AI) được chạy ở kích thước vùng ảnh hoạt động TRƯỚC bước pad.
    """
    fp = get_frame_preset(frame_preset)
    vf_parts = [
        f"scale={fp.inner_w}:{fp.inner_h}:force_original_aspect_ratio=increase",
        f"crop={fp.inner_w}:{fp.inner_h}",
        "setsar=1",
        "fps=30",
    ]
    clean_extra = (extra_vf or "").strip().strip(",")
    if clean_extra:
        vf_parts.append(clean_extra)
    if fp.needs_pad:
        vf_parts.append(fp.pad_filter)
        vf_parts.append("setsar=1")
    vf_parts.append("format=yuv420p")
    base_vf = ",".join(vf_parts)
    use_stretch = False
    stretch_ratio = 1.0

    if smart_clip_fit:
        try:
            src_dur = probe(asset_file).duration
            if src_dur > 0.2 and src_dur < target_dur:
                ratio = target_dur / src_dur
                if ratio <= 1.45:
                    use_stretch = True
                    stretch_ratio = ratio
        except Exception:
            pass

    if use_stretch:
        vf = f"setpts={stretch_ratio:.5f}*PTS,{base_vf}"
        return [
            "-i", str(asset_file),
            "-t", f"{target_dur:.3f}",
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            str(out_clip),
        ]

    return [
        "-stream_loop", "-1",
        "-i", str(asset_file),
        "-t", f"{target_dur:.3f}",
        "-vf", base_vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_clip),
    ]


def _concat_with_xfade(
    clips: Sequence[Path],
    base_durations: Sequence[float],
    out_video: Path,
    work: Path,
    transition: str,
    trans_dur: float,
) -> bool:
    """Ghép chuỗi clip bằng FFmpeg xfade có giữ chuẩn tổng thời lượng khớp âm thanh.
    Trả về True nếu thành công, False để tự lùi an toàn về concat -c copy.
    """
    if len(clips) < 2 or len(clips) != len(base_durations):
        return False

    inputs: List[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    fc_parts: List[str] = []
    for idx in range(len(clips)):
        fc_parts.append(f"[{idx}:v]settb=AVTB,fps=30,format=yuv420p[v{idx}]")

    acc_label = "[v0]"
    running_offset = 0.0
    for idx in range(len(clips) - 1):
        running_offset += base_durations[idx]
        nxt_label = f"[v{idx + 1}]"
        out_label = "[vout]" if idx == len(clips) - 2 else f"[xf{idx + 1}]"
        fc_parts.append(
            f"{acc_label}{nxt_label}xfade=transition={transition}:duration={trans_dur:.3f}:offset={running_offset:.3f}{out_label}"
        )
        acc_label = out_label

    cmd = [
        *inputs,
        "-filter_complex", ";".join(fc_parts),
        "-map", "[vout]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-an",
        str(out_video),
    ]
    res = run_ffmpeg(cmd, cwd=work, check=False)
    return res.returncode == 0 and out_video.is_file()


def _build_zoompan_filter(
    motion: str,
    duration: float,
    *,
    extra_vf: str = "",
    frame_preset: Union[str, FramePreset] = "portrait_9_16",
) -> str:
    """Xây dựng chuỗi bộ lọc zoompan cho hiệu ứng Ken Burns đúng kích thước vùng ảnh hoạt động (trước pad)."""
    fp = get_frame_preset(frame_preset)
    iw, ih = fp.inner_w, fp.inner_h
    d = max(1, int(math.ceil(duration * 30)))
    step = 0.15 / max(d, 1)

    if motion == "zoom_in":
        vf_zoom = (
            f"zoompan=z='min(pzoom+{step:.6f},1.25)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={iw}x{ih}:fps=30"
        )
    elif motion == "zoom_out":
        vf_zoom = (
            f"zoompan=z='if(eq(on,0),1.15,max(1.0,pzoom-{step:.6f}))':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={iw}x{ih}:fps=30"
        )
    elif motion == "pan_left":
        vf_zoom = (
            f"zoompan=z='1.15':"
            f"x='(1.0-on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={iw}x{ih}:fps=30"
        )
    elif motion == "pan_right":
        vf_zoom = (
            f"zoompan=z='1.15':"
            f"x='(on/{d})*(iw-iw/zoom)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={iw}x{ih}:fps=30"
        )
    else:
        vf_zoom = (
            f"zoompan=z='1.0':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={d}:s={iw}x{ih}:fps=30"
        )

    parts = [
        f"scale={iw}:{ih}:force_original_aspect_ratio=increase",
        f"crop={iw}:{ih}",
        "setsar=1",
        vf_zoom,
        "setsar=1",
    ]
    clean_extra = (extra_vf or "").strip().strip(",")
    if clean_extra:
        parts.append(clean_extra)
    if fp.needs_pad:
        parts.append(fp.pad_filter)
        parts.append("setsar=1")
    parts.append("format=yuv420p")
    return ",".join(parts)


def _norm_vi_token(text: str) -> str:
    """Chuẩn hóa không dấu + bỏ dấu câu chỉ để so khớp mốc từ (không làm đổi chữ gốc)."""
    s = text.lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def _distribute_span(words: Sequence[str], t_start: float, t_end: float) -> List[Tuple[float, float]]:
    """Chia khoảng thời gian [t_start, t_end] cho danh sách từ theo tỉ lệ độ dài ký tự."""
    if not words:
        return []
    span = max(0.05 * len(words), t_end - t_start)
    weights = [max(1, len(w)) for w in words]
    total_w = sum(weights)
    out: List[Tuple[float, float]] = []
    cur = t_start
    for w_len in weights:
        dur = span * (w_len / total_w)
        out.append((cur, cur + dur))
        cur += dur
    return out


_VI_DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")


def _int_to_vi_norms(n: int, thousand_word: str = "ngan") -> List[str]:
    """Đổi số nguyên (0..99999) sang danh sách âm tiết tiếng Việt không dấu để khớp Whisper."""
    if n < 0:
        return []
    if n < 10:
        return [_norm_vi_token(_VI_DIGITS[n])]
    if n < 100:
        tens, ones = divmod(n, 10)
        res = ["muoi"] if tens == 1 else [_norm_vi_token(_VI_DIGITS[tens]), "muoi"]
        if ones == 5:
            res.append("lam")
        elif ones > 0:
            res.append(_norm_vi_token(_VI_DIGITS[ones]))
        return res
    if n < 1000:
        hundreds, rem = divmod(n, 100)
        res = [_norm_vi_token(_VI_DIGITS[hundreds]), "tram"]
        if 0 < rem < 10:
            res.extend(["le", _norm_vi_token(_VI_DIGITS[rem])])
        elif rem >= 10:
            res.extend(_int_to_vi_norms(rem, thousand_word))
        return res
    thousands, rem = divmod(n, 1000)
    res = _int_to_vi_norms(thousands, thousand_word) + [thousand_word]
    if rem > 0:
        if rem < 100:
            res.extend(["khong", "tram"])
            if rem < 10:
                res.append("le")
        res.extend(_int_to_vi_norms(rem, thousand_word))
    return res


def _expand_whisper_words(
    raw_words: Sequence[Tuple[str, float, float]],
    clean_norm_set: set[str],
) -> List[Tuple[str, float, float]]:
    """Tách các token dạng số của Whisper (vd: '73%', '9.000', '49') sang âm tiết Việt
    nếu trong kịch bản gốc viết bằng chữ, giúp khớp từng từ chính xác 100%.
    """
    thousand_w = "ngan" if "ngan" in clean_norm_set else "nghin"
    expanded: List[Tuple[str, float, float]] = []

    for raw_txt, st, en in raw_words:
        txt = raw_txt.strip()
        norm = _norm_vi_token(txt)
        if not norm:
            continue

        # Nếu kịch bản gốc cũng viết bằng số (vd: '1953', '20', '11') thì giữ nguyên để khớp 1-1
        if norm in clean_norm_set:
            expanded.append((norm, st, en))
            continue

        has_pct = "%" in txt
        digits_only = re.sub(r"[.,%]", "", txt)
        if digits_only.isdigit():
            val = int(digits_only)
            parts = _int_to_vi_norms(val, thousand_w) if val <= 99999 else [digits_only]
            if has_pct:
                parts.extend(["phan", "tram"])
            if len(parts) > 1:
                spans = _distribute_span(parts, st, en)
                for p, (ps, pe) in zip(parts, spans):
                    expanded.append((p, ps, pe))
                continue
            elif parts:
                expanded.append((parts[0], st, en))
                continue

        expanded.append((norm, st, en))

    return expanded


def _align_full_audio_with_whisper(
    audio_wav: Path,
    scenes: Sequence[Any],
    total_duration: float,
    model_size: str = "small",
    reporter: Optional[Reporter] = None,
    cache_dir: Optional[Path] = None,
) -> bool:
    """Dùng faster-whisper nghe trực tiếp mảng âm thanh 16kHz (không lỗi PyAV)
    và căn mốc từng chữ siêu sát với giọng đọc gốc. Hỗ trợ cache theo hash audio+kịch bản.
    """
    import wave

    clean_items: List[Tuple[int, str, str]] = []
    for s_idx, sc in enumerate(scenes):
        for tok in sc.speech.split():
            if tok.strip():
                clean_items.append((s_idx, tok, _norm_vi_token(tok)))

    if not clean_items:
        return False

    clean_norm_set = {it[2] for it in clean_items if it[2]}
    full_prompt = " ".join(sc.speech for sc in scenes if sc.speech.strip())

    # Kiểm tra xem máy có CUDA không; nếu chạy CPU mà người dùng chọn large* thì tự chuyển về 'small'
    # để vừa nhanh gấp 5 lần vừa giữ nguyên độ chính xác căn mốc theo initial_prompt.
    has_cuda = False
    try:
        import ctranslate2
        has_cuda = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        has_cuda = False

    effective_model = (model_size or "small").strip()
    if not has_cuda and effective_model.lower().startswith("large"):
        if reporter:
            reporter.log(
                f"ℹ️ Máy không có GPU CUDA: dùng Whisper 'small' (CPU beam=1) thay cho '{effective_model}' để căn mốc nhanh & chuẩn.",
                "info",
            )
        effective_model = "small"

    # Kiểm tra cache căn mốc (nếu cùng audio + kịch bản thì nạp trong 0.01s)
    cache_file: Optional[Path] = None
    if cache_dir is not None and audio_wav.is_file():
        try:
            h = hashlib.sha256()
            h.update(b"whisper_align_v2|")
            h.update(effective_model.encode("utf-8"))
            h.update(b"|")
            h.update(full_prompt.encode("utf-8"))
            h.update(b"|")
            with audio_wav.open("rb") as af:
                for chunk in iter(lambda: af.read(65536), b""):
                    h.update(chunk)
            cache_file = Path(cache_dir) / f"align_{h.hexdigest()}.json"
            if cache_file.is_file():
                cached_payload = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_scenes = cached_payload.get("scenes", [])
                if len(cached_scenes) == len(scenes):
                    for sc, c_words in zip(scenes, cached_scenes):
                        sw = [
                            WordTiming(word=str(w["word"]), start=float(w["start"]), end=float(w["end"]))
                            for w in c_words
                        ]
                        if sw:
                            sc.words = sw
                            sc.start_time = sw[0].start
                            sc.end_time = sw[-1].end
                            sc.duration_sec = round(max(0.4, sc.end_time - sc.start_time), 3)
                    if scenes:
                        scenes[0].start_time = 0.0
                    if reporter:
                        reporter.log("✓ Đã nạp mốc từng từ từ cache Whisper (0.01s).", "ok")
                    return True
        except Exception:
            cache_file = None

    raw_w_words: List[Tuple[str, float, float]] = []
    try:
        from faster_whisper import WhisperModel

        model = None
        used_device = "cpu"
        device_candidates = (("cuda", "float16"), ("cpu", "int8")) if has_cuda else (("cpu", "int8"),)
        for device, compute in device_candidates:
            try:
                model = WhisperModel(effective_model, device=device, compute_type=compute)
                used_device = device
                break
            except Exception:
                continue

        if model is not None:
            # Nạp thẳng WAV 16kHz dạng numpy để bỏ qua hoàn toàn lỗi PyAV (av.open)
            wav_16k = audio_wav.with_name(f"{audio_wav.stem}_16k.wav")
            to_wav(audio_wav, wav_16k, 16000, 1)
            with wave.open(str(wav_16k), "rb") as wf:
                if wf.getsampwidth() == 2:
                    audio_input = (
                        np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
                        / 32768.0
                    )
                else:
                    audio_input = np.asarray(read_wav(wav_16k), dtype=np.float32)

            segs_iter, _ = model.transcribe(
                audio_input,
                language="vi",
                word_timestamps=True,
                vad_filter=False,
                beam_size=1 if used_device == "cpu" else 5,
                initial_prompt=full_prompt[:900],
            )
            for seg in segs_iter:
                for w in getattr(seg, "words", None) or []:
                    txt = str(getattr(w, "word", "")).strip()
                    if txt:
                        raw_w_words.append((txt, float(w.start), float(w.end)))
    except Exception as e:
        if reporter:
            reporter.log(f"⚠️ Whisper căn mốc gặp lỗi ({e}) → chia đều theo tỉ lệ độ dài câu.", "warn")

    w_words = _expand_whisper_words(raw_w_words, clean_norm_set) if raw_w_words else []

    n = len(clean_items)
    assigned: List[Optional[Tuple[float, float]]] = [None] * n

    if w_words:
        clean_norms = [it[2] for it in clean_items]
        whisper_norms = [w[0] for w in w_words]
        matcher = difflib.SequenceMatcher(None, clean_norms, whisper_norms, autojunk=False)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    assigned[i1 + k] = (w_words[j1 + k][1], w_words[j1 + k][2])
            elif tag == "replace":
                # Nếu cùng số âm tiết (vd: 'Henri Navarre' vs 'Henry Nava', 'Piroth' vs 'Pirot') thì gán thẳng 1-1
                if (i2 - i1) == (j2 - j1):
                    for k in range(i2 - i1):
                        assigned[i1 + k] = (w_words[j1 + k][1], w_words[j1 + k][2])
                else:
                    t_st = w_words[j1][1]
                    t_en = w_words[j2 - 1][2]
                    spans = _distribute_span([clean_items[idx][1] for idx in range(i1, i2)], t_st, t_en)
                    for k, sp in enumerate(spans):
                        assigned[i1 + k] = sp

        # Nội suy mượt cho các từ bị sót nằm giữa 2 mốc đã bắt được
        idx = 0
        while idx < n:
            if assigned[idx] is not None:
                idx += 1
                continue
            j = idx
            while j < n and assigned[j] is None:
                j += 1
            prev_end = assigned[idx - 1][1] if idx > 0 and assigned[idx - 1] is not None else 0.0
            next_start = assigned[j][0] if j < n and assigned[j] is not None else total_duration
            if next_start <= prev_end:
                next_start = min(total_duration, prev_end + 0.16 * (j - idx))
            spans = _distribute_span([clean_items[k][1] for k in range(idx, j)], prev_end, next_start)
            for k, sp in enumerate(spans):
                assigned[idx + k] = sp
            idx = j
    else:
        spans = _distribute_span([it[1] for it in clean_items], 0.0, total_duration)
        assigned = list(spans)

    per_scene: Dict[int, List[WordTiming]] = {i: [] for i in range(len(scenes))}
    prev_st = 0.0
    prev_scene_idx = -1
    prev_raw_word = ""
    for (s_idx, raw_word, norm_tok), span in zip(clean_items, assigned):
        st, en = span if span is not None else (prev_st, prev_st + 0.15)
        st = max(prev_st, min(st, total_duration))
        en = max(st + 0.04, min(en, total_duration))

        # Chống Whisper gộp khoảng lặng giữa 2 câu vào đầu từ đầu câu sau (vd: 'Kỷ(52.92-53.56)')
        is_sentence_head = (s_idx != prev_scene_idx) or prev_raw_word.endswith((".", "!", "?", ","))
        if is_sentence_head and not any(ch.isdigit() for ch in raw_word) and len(norm_tok) <= 6 and (en - st) > 0.55:
            st = max(prev_st, en - 0.30)

        per_scene[s_idx].append(WordTiming(word=raw_word, start=round(st, 3), end=round(en, 3)))
        prev_st = st
        prev_scene_idx = s_idx
        prev_raw_word = raw_word

    for s_idx, sc in enumerate(scenes):
        sw = per_scene.get(s_idx, [])
        if sw:
            sc.words = sw
            sc.start_time = sw[0].start
            sc.end_time = sw[-1].end
            sc.duration_sec = round(max(0.4, sc.end_time - sc.start_time), 3)

    if scenes:
        scenes[0].start_time = 0.0

    # Lưu cache căn mốc nếu Whisper thành công
    if w_words and cache_file is not None:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "model": effective_model,
                "scenes": [
                    [{"word": w.word, "start": w.start, "end": w.end} for w in getattr(sc, "words", [])]
                    for sc in scenes
                ],
            }
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    return bool(w_words)


def _scale_scenes_timings(scenes: Sequence[Any], scale: float, max_duration: float) -> None:
    """Quy đổi toàn bộ mốc thời gian của từng cảnh và từng từ theo hệ số tốc độ `scale` (t_new = t / scale)."""
    s = max(float(scale), 0.01)
    max_dur = max(0.1, float(max_duration))
    for sc in scenes:
        old_words = getattr(sc, "words", None) or []
        if old_words:
            new_words: List[WordTiming] = []
            prev_st = 0.0
            for w in old_words:
                st = round(max(prev_st, min(max_dur, float(w.start) / s)), 3)
                en = round(max(st + 0.03, min(max_dur, float(w.end) / s)), 3)
                if en < st:
                    en = st
                new_words.append(WordTiming(word=w.word, start=st, end=en))
                prev_st = st
            sc.words = new_words

        if sc.start_time > 0:
            sc.start_time = round(min(max_dur, float(sc.start_time) / s), 3)
        if sc.end_time > 0:
            sc.end_time = round(min(max_dur, float(sc.end_time) / s), 3)
        if sc.duration_sec > 0:
            sc.duration_sec = round(max(0.2, float(sc.duration_sec) / s), 3)
        if getattr(sc, "words", None):
            sc.start_time = sc.words[0].start
            sc.end_time = sc.words[-1].end
            sc.duration_sec = round(max(0.2, sc.end_time - sc.start_time), 3)
    if scenes:
        scenes[0].start_time = 0.0


def _prepare_bgm_audio(
    bgm_file: Path,
    work_dir: Path,
    target_duration: float,
    reporter: Optional[Reporter] = None,
) -> Path:
    """Chuẩn bị luồng nhạc nền BGM sạch (WAV stereo 44.1kHz) ở đúng tốc độ gốc 1.00x, khớp tuyệt đối `target_duration`.

    - Hỗ trợ cả file audio (.mp3/.wav/.m4a/.aac/.ogg/.flac) lẫn file video chứa nhạc (.mp4/.mov/.mkv/.webm).
    - Nếu file không có luồng âm thanh -> ném DubberError rõ ràng thay vì crash FFmpeg.
    - Nếu BGM dài hơn video: lấy từ 00:00 cắt gọn đúng tới `target_duration`.
    - Nếu BGM ngắn hơn video: tự lặp (`-stream_loop -1`) rồi cắt gọn đúng tới `target_duration`.
    - TUYỆT ĐỐI không áp dụng `atempo` lên BGM (khóa cứng tốc độ gốc 1.00x).
    """
    bgm_file = Path(bgm_file).resolve()
    bgm_info = probe(bgm_file)
    if not bgm_info.has_audio:
        raise DubberError(
            f"File nhạc nền (BGM) '{bgm_file.name}' không có luồng âm thanh (audio). "
            "Vui lòng chọn file âm thanh hoặc file video có kèm nhạc."
        )

    target_dur = max(0.1, float(target_duration))
    is_video_container = bgm_file.suffix.lower() in ASSET_VIDEO_EXTS or bgm_info.has_video
    bgm_prepared = work_dir / "bgm_prepared_1x.wav"

    if bgm_info.duration >= target_dur - 0.05:
        run_ffmpeg([
            "-i", bgm_file,
            "-vn",
            "-ss", "0",
            "-t", f"{target_dur:.3f}",
            "-ac", "2",
            "-ar", "44100",
            "-c:a", "pcm_s16le",
            bgm_prepared,
        ])
        action_note = f"cắt từ 00:00 → {target_dur:.2f}s (gốc {bgm_info.duration:.2f}s)"
    else:
        loop_src = bgm_file
        if is_video_container:
            # Rút audio ra WAV trước khi lặp để tránh lỗi -stream_loop với container MP4/MKV có edit list
            bgm_extracted = work_dir / "bgm_extracted_raw.wav"
            run_ffmpeg([
                "-i", bgm_file,
                "-vn",
                "-ac", "2",
                "-ar", "44100",
                "-c:a", "pcm_s16le",
                bgm_extracted,
            ])
            loop_src = bgm_extracted
        run_ffmpeg([
            "-stream_loop", "-1",
            "-i", loop_src,
            "-vn",
            "-t", f"{target_dur:.3f}",
            "-ac", "2",
            "-ar", "44100",
            "-c:a", "pcm_s16le",
            bgm_prepared,
        ])
        action_note = f"tự lặp khít {target_dur:.2f}s (gốc {bgm_info.duration:.2f}s)"

    if reporter:
        kind = "rút nhạc từ video" if is_video_container else "nhạc nền"
        reporter.log(f"🎵 Đã chuẩn bị BGM ({kind} '{bgm_file.name}', khóa 1.00x, {action_note}).", "ok")

    return bgm_prepared


class StoryPipeline:
    """Lõi điều phối dựng video Story 9:16 hoàn chỉnh."""

    def __init__(
        self,
        settings: Optional[DubSettings] = None,
        keys: Optional[Keys] = None,
        reporter: Optional[Reporter] = None,
        *,
        tts_engine: Optional[TTSEngine] = None,
        work_root: Optional[Path] = None,
        cache: Optional[AudioCache] = None,
        sfx_dir: Optional[Path] = None,
    ):
        self.s = settings or DubSettings()
        self.keys = keys or Keys.from_env()
        self.rep = reporter or Reporter()
        self._engine = tts_engine
        self.work_root = Path(work_root) if work_root else WORK_ROOT
        self.cache = cache or AudioCache()
        self.sfx_dir = Path(sfx_dir) if sfx_dir else None
        self._workdir: Optional[Path] = None

    def workdir(self) -> Path:
        if self._workdir is None:
            self._workdir = self.work_root / time.strftime("story_%Y%m%d_%H%M%S")
            self._workdir.mkdir(parents=True, exist_ok=True)
        return self._workdir

    def cleanup(self) -> None:
        if self._workdir and not getattr(self.s, "keep_temp", False):
            shutil.rmtree(self._workdir, ignore_errors=True)
        self._workdir = None

    def render_story(
        self,
        storyboard_path_or_obj: Union[Path, str, dict, Storyboard],
        assets_dir: Path | str,
        output_path: Optional[Path | str] = None,
        *,
        bgm_path: Optional[Path | str] = None,
        burn_subtitles: bool = True,
        codec: str = "h264",
        scene_pad: float = 0.25,
        bgm_volume: float = 0.35,
        sfx_volume: float = 0.85,
        alignment_data: Optional[Dict[str, Any]] = None,
        full_audio_path: Optional[Path | str] = None,
        shot_mode: str = "scene",
        cinematic_grade: bool = False,
        loudness_norm: bool = False,
        subtitle_style: str = "karaoke_line",
        watermark_text: Optional[str] = None,
        watermark_pos: str = "top_left",
        watermark_opacity: int = 65,
        anti_ai: bool = False,
        anti_ai_level: Optional[str] = None,
        anti_ai_grain: bool = True,
        transition: str = "none",
        transition_duration: float = 0.25,
        smart_clip_fit: bool = True,
        show_scene_titles: bool = False,
        sub_font: str = "Arial",
        sub_font_size: int = 52,
        sub_margin_v: Optional[int] = None,
        sub_color: str = "#FFE000",
        speed: float = 1.0,
        frame_preset: Optional[str] = None,
    ) -> Path:
        """Thực hiện toàn bộ quy trình dựng video Story theo preset khung hình thích ứng."""
        if shot_mode not in ("scene", "planned"):
            raise DubberError(f"shot_mode '{shot_mode}' không hợp lệ; chỉ chấp nhận 'scene' hoặc 'planned'")
        if subtitle_style not in ("karaoke_line", "pop_2words", "highlight_3words"):
            raise DubberError(f"subtitle_style '{subtitle_style}' không hợp lệ")
        if transition not in VALID_TRANSITIONS:
            raise DubberError(f"transition '{transition}' không hợp lệ (chấp nhận: {sorted(VALID_TRANSITIONS)})")
        if watermark_pos not in VALID_WATERMARK_POS:
            watermark_pos = "top_left"
        try:
            eff_anti_ai_level = (
                normalize_anti_ai_level(anti_ai_level)
                if anti_ai_level is not None
                else normalize_anti_ai_level(bool(anti_ai))
            )
        except ValueError as e:
            raise DubberError(str(e)) from e
        anti_ai_active = eff_anti_ai_level != "off"
        speed = float(speed)
        if not (0.25 <= speed <= 4.0):
            raise DubberError(f"Tốc độ đầu ra speed={speed} không hợp lệ (chấp nhận 0.25–4.0)")
        apply_speed = abs(speed - 1.0) > 1e-3
        eff_sfx_volume = max(0.0, min(1.5, float(sfx_volume)))

        assets_dir = Path(assets_dir)
        if not assets_dir.is_dir():
            raise DubberError(f"Thư mục tài nguyên không tồn tại: {assets_dir}")

        if isinstance(storyboard_path_or_obj, Storyboard):
            sb = storyboard_path_or_obj
        else:
            sb = load_storyboard(storyboard_path_or_obj)

        sb.ensure_scenes_from_beats()
        fp = resolve_frame_preset(override=frame_preset, storyboard_preset=sb.frame_preset)

        if not output_path:
            clean_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in sb.title).strip("_")
            output_path = OUTPUT_DIR / f"{clean_title or 'story'}_{fp.filename_suffix}.mp4"
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        work = self.workdir()
        self.rep.log(
            f"📖 Bắt đầu dựng phim: '{sb.title}' ({len(sb.scenes)} phân đoạn | khung={fp.name} {fp.out_w}x{fp.out_h} | chế độ={shot_mode} | chuyển cảnh={transition})"
        )

        # ------------------------------------------------------------- 1. Khớp tài nguyên
        self.rep.progress(5, "Đang kiểm tra tài nguyên ảnh/clip...")
        asset_map, missing = validate_story_assets(sb, assets_dir)
        if missing:
            raise DubberError(f"Thiếu tài nguyên cho {len(missing)} cảnh (ID: {missing}) trong thư mục {assets_dir.name}")
        self.rep.log(f"✓ Đã khớp đủ {len(asset_map)} tài nguyên media.", "ok")

        archival_ids: set[int] = set()
        if anti_ai_active:
            candidate_dirs: List[Path] = []
            if isinstance(storyboard_path_or_obj, (Path, str)):
                sb_p = Path(storyboard_path_or_obj)
                if sb_p.is_file():
                    candidate_dirs.append(sb_p.resolve().parent)
            resolved_assets = assets_dir.resolve()
            candidate_dirs.extend([resolved_assets.parent, resolved_assets])
            for pdir in candidate_dirs:
                archival_ids = load_archival_scene_ids(pdir)
                if archival_ids:
                    break

        per_scene_anti_ai = anti_ai_active
        ai_depolish_vf = (
            build_ai_scene_depolish_vf(eff_anti_ai_level, width=fp.inner_w, height=fp.inner_h)
            if per_scene_anti_ai
            else ""
        )
        if per_scene_anti_ai:
            n_archival = sum(1 for sc in sb.scenes if sc.id in archival_ids)
            n_ai = len(sb.scenes) - n_archival
            if archival_ids:
                self.rep.log(
                    f"🛡️ [Anti-AI Selective] Giữ nét gốc {n_archival} cảnh tư liệu thật "
                    f"| Áp lọc khử bóng nhẹ (mức={eff_anti_ai_level}, {fp.inner_w}x{fp.inner_h}) cho {n_ai} cảnh AI.",
                    "info",
                )
            else:
                self.rep.log(
                    f"🛡️ [Anti-AI Per-Scene] Áp lọc khử bóng nhẹ từng cảnh (mức={eff_anti_ai_level}, {fp.inner_w}x{fp.inner_h}) "
                    f"cho {n_ai} cảnh AI (0 ảnh tư liệu thật; giữ sạch giọng đọc & phụ đề).",
                    "info",
                )
        self.rep.check_cancel()

        # ------------------------------------------------------------- 2. Xử lý Alignment & Nguồn Âm thanh
        if alignment_data is not None:
            words_list = chars_to_words(alignment_data)
            align_scenes_with_words(sb.scenes, words_list)
        elif (assets_dir / "alignment.json").is_file():
            try:
                align_dict = json.loads((assets_dir / "alignment.json").read_text(encoding="utf-8-sig"))
                words_list = chars_to_words(align_dict)
                align_scenes_with_words(sb.scenes, words_list)
            except Exception as e:
                self.rep.log(f"⚠️ Bỏ qua file alignment.json lỗi: {e}", "warn")

        engine_allows_el = (self._engine is None) or (getattr(self._engine, "name", "") == "elevenlabs")

        resolved_full_audio: Optional[Path] = None
        if full_audio_path and Path(full_audio_path).is_file():
            resolved_full_audio = Path(full_audio_path).resolve()
        elif (assets_dir / "full_audio.mp3").is_file():
            resolved_full_audio = (assets_dir / "full_audio.mp3").resolve()
        elif (assets_dir / "full_audio.wav").is_file():
            resolved_full_audio = (assets_dir / "full_audio.wav").resolve()
        elif (
            sb.full_script_for_elevenlabs
            and getattr(self.keys, "elevenlabs", None)
            and engine_allows_el
            and not isinstance(self._engine, ToneTTS)
            and not any(sc.duration_sec > 0 for sc in sb.scenes)
        ):
            try:
                self.rep.progress(10, "Đang gọi ElevenLabs /with-timestamps cho toàn bộ kịch bản...")
                el_mp3 = work / "elevenlabs_full.mp3"
                voice_id_str = (
                    getattr(self._engine, "voice_id", None)
                    or getattr(self.s, "elevenlabs_voice_id", None)
                    or getattr(self.s, "tts_voice", None)
                    or "21m00Tcm4TlvDq8ikWAM"
                )
                align_dict = generate_voice_with_timestamps(
                    self.keys.elevenlabs,
                    voice_id_str,
                    sb.get_full_script(),
                    el_mp3,
                )
                words_list = chars_to_words(align_dict)
                align_scenes_with_words(sb.scenes, words_list)
                resolved_full_audio = el_mp3
                self.rep.log(f"✓ ElevenLabs Voice ID '{voice_id_str}' đọc liền mạch thành công.", "ok")
            except Exception as e:
                self.rep.log(f"⚠️ ElevenLabs /with-timestamps lỗi ({e}) -> chuyển per-scene TTS", "warn")

        scene_durations: List[float] = []
        scene_audio_files: List[Path] = []
        timeline_segments: List[Segment] = []
        timeline_starts: List[float] = []
        total_scenes = len(sb.scenes)

        voice_track_path = work / "voice_track.wav"

        if resolved_full_audio is not None:
            raw_voice_path = (work / "voice_track_raw.wav") if apply_speed else voice_track_path
            to_wav(resolved_full_audio, raw_voice_path, SR, 1)
            audio_info = probe(raw_voice_path)
            raw_total_duration = audio_info.duration

            # Tự chạy Whisper căn mốc từng chữ trên bản gốc 1.00x (tận dụng cache align_*.json 0.01s & độ chính xác cao nhất),
            # kể cả khi storyboard.json đã có sẵn start_time/duration_sec ước lượng.
            if not any(getattr(sc, "words", []) for sc in sb.scenes):
                self.rep.progress(18, "Đang dùng Whisper căn mốc từng chữ cho file giọng đọc...")
                self.rep.log(f"🎙️ Phát hiện file giọng ngoài ({resolved_full_audio.name}) → đang căn mốc từng chữ bằng Whisper...")
                ok_align = _align_full_audio_with_whisper(
                    raw_voice_path,
                    sb.scenes,
                    raw_total_duration,
                    model_size=getattr(self.s, "whisper_model", "small") or "small",
                    reporter=self.rep,
                    cache_dir=getattr(self.cache, "cache_dir", None),
                )
                if ok_align:
                    self.rep.log("✓ Đã căn mốc từng từ chuẩn 100% theo kịch bản gốc (Whisper).", "ok")
                else:
                    self.rep.log("⚠️ Whisper không khả dụng → đã chia đều mốc từ theo tỉ lệ độ dài câu.", "warn")

            if sb.scenes and sb.scenes[0].start_time > 0.0:
                sb.scenes[0].start_time = 0.0

            if apply_speed:
                self.rep.progress(24, f"Đang đồng bộ tốc độ giọng nói {speed:.2f}x (FFmpeg atempo)...")
                change_speed(raw_voice_path, voice_track_path, speed)
                sped_info = probe(voice_track_path)
                total_duration = sped_info.duration
                eff_scale = (raw_total_duration / total_duration) if (raw_total_duration > 0 and total_duration > 0) else speed
                _scale_scenes_timings(sb.scenes, eff_scale, total_duration)
                self.rep.log(
                    f"⚡ Đã đồng bộ tốc độ đầu ra {speed:.2f}x (Video + Giọng + Phụ đề): "
                    f"{raw_total_duration:.2f}s → {total_duration:.2f}s (BGM giữ nguyên 1.00x).",
                    "ok",
                )
            else:
                total_duration = raw_total_duration

            for i, sc in enumerate(sb.scenes):
                timeline_starts.append(sc.start_time)
                if i < total_scenes - 1:
                    next_start = sb.scenes[i + 1].start_time
                    if next_start > sc.start_time:
                        dur_val = round(next_start - sc.start_time, 3)
                    elif sc.duration_sec > 0:
                        dur_val = sc.duration_sec
                    else:
                        dur_val = round(sc.end_time - sc.start_time, 3) if sc.end_time > sc.start_time else 1.0
                else:
                    rem = round(total_duration - sc.start_time, 3)
                    dur_val = max(0.5, rem) if rem > 0 else (sc.duration_sec or 1.0)

                scene_durations.append(dur_val)

                s_words = getattr(sc, "words", [])
                if not s_words:
                    from .tts import estimate_word_timings
                    s_words = estimate_word_timings(sc.speech, max(0.1, dur_val))
                    s_words = [w.scale_and_shift(speed=1.0, offset=sc.start_time) for w in s_words]
                timeline_segments.append(
                    Segment(
                        start=sc.start_time,
                        end=sc.start_time + dur_val,
                        text=sc.speech,
                        words=s_words,
                    )
                )

            self.rep.log(f"✓ Đã nạp track giọng nói tổng: {total_duration:.2f}s", "ok")
        else:
            engine = self._engine or make_engine(
                self.s,
                self.keys,
                on_fallback=lambda name, e: self.rep.log(f"⚠️ {name} lỗi ({e}) → chuyển engine dự phòng", "warn"),
            )
            voice_id = getattr(engine, "voice_id", getattr(engine, "voice", ""))
            engine_params = getattr(engine, "params", {})

            # Tránh tăng tốc 2 lần nếu EdgeTTS đã được khởi tạo trực tiếp với rate khác +0%
            engine_already_scaled = (
                getattr(engine, "name", "") == "edge"
                and str(getattr(engine, "rate", "+0%")).strip() not in ("", "+0%", "0%", "-0%")
            )
            apply_per_scene_speed = apply_speed and not engine_already_scaled
            if apply_speed and engine_already_scaled:
                self.rep.log(
                    f"ℹ️ Engine Edge-TTS đã có rate={getattr(engine, 'rate', '')} → bỏ qua atempo bổ sung để tránh tăng tốc 2 lần.",
                    "warn",
                )

            clips_dir = work / "audio_scenes"
            clips_dir.mkdir(parents=True, exist_ok=True)

            timeline_cursor = 0.0

            for i, sc in enumerate(sb.scenes):
                self.rep.check_cancel()
                self.rep.progress(10 + int(i * 35 / max(1, total_scenes)), f"Tạo giọng đọc phân đoạn {i + 1}/{total_scenes}")

                raw_audio = clips_dir / f"raw_scene_{sc.id:03d}{engine.ext}"
                wav_audio = clips_dir / f"scene_{sc.id:03d}.wav"

                key = compute_cache_key(engine.name, voice_id, sc.speech, engine_params)
                cached = self.cache.get(key, engine.ext)

                words: List[WordTiming] = []
                if cached is not None:
                    cached_audio, words = cached
                    shutil.copyfile(str(cached_audio), str(raw_audio))
                else:
                    words = engine.synthesize(sc.speech, raw_audio) or []
                    self.cache.put(key, raw_audio, words, engine.ext)
                    if getattr(engine, "name", "") == "edge":
                        time.sleep(0.35)

                to_wav(raw_audio, wav_audio, SR, 1)
                audio_info = probe(wav_audio)
                audio_dur = audio_info.duration

                # Vá lỗi Edge-TTS bỏ sót mốc từ ở số (1953, 20) hoặc tên riêng nước ngoài
                if getattr(engine, "name", "") == "edge" and len(words) != len(sc.speech.split()):
                    from .tts import estimate_word_timings
                    words = estimate_word_timings(sc.speech, max(0.1, audio_dur))

                if apply_per_scene_speed:
                    wav_sped = clips_dir / f"scene_{sc.id:03d}_spd.wav"
                    change_speed(wav_audio, wav_sped, speed)
                    sped_dur = probe(wav_sped).duration
                    eff_scale = (audio_dur / sped_dur) if (audio_dur > 0 and sped_dur > 0) else speed
                    wav_audio = wav_sped
                    audio_dur = sped_dur
                    words = [w.scale_and_shift(speed=eff_scale, offset=0.0) for w in words]

                base_pad = 0.08 if shot_mode == "planned" else scene_pad
                effective_pad = (base_pad / speed) if apply_per_scene_speed else base_pad
                base_sc_dur = (sc.duration_sec / speed) if (apply_per_scene_speed and sc.duration_sec > 0) else sc.duration_sec
                scene_video_dur = base_sc_dur if base_sc_dur > 0 else (audio_dur + effective_pad)
                scene_durations.append(scene_video_dur)
                scene_audio_files.append(wav_audio)
                timeline_starts.append(timeline_cursor)

                scene_words = [w.scale_and_shift(speed=1.0, offset=timeline_cursor) for w in words]
                timeline_segments.append(
                    Segment(
                        start=timeline_cursor,
                        end=timeline_cursor + audio_dur,
                        text=sc.speech,
                        words=scene_words,
                    )
                )

                timeline_cursor += scene_video_dur

            total_duration = timeline_cursor
            spd_note = f" | tốc độ {speed:.2f}x" if apply_per_scene_speed else ""
            self.rep.log(f"✓ Đã tạo xong giọng đọc ({engine.name}{spd_note}). Tổng thời lượng: {total_duration:.2f}s", "ok")

        # ------------------------------------------------------------- 3. Sinh phụ đề .ass, Tiêu đề cảnh & Watermark
        ass_path = work / f"story_karaoke_{fp.filename_suffix}.ass"
        has_aligned_words = any(len(getattr(sc, "words", [])) > 0 for sc in sb.scenes)
        clean_wm = (watermark_text or "").strip()

        scene_title_events: List[Tuple[float, float, str]] = []
        if show_scene_titles:
            for i, sc in enumerate(sb.scenes):
                if sc.on_screen_text and i < len(timeline_starts) and i < len(scene_durations):
                    st_t = timeline_starts[i]
                    en_t = st_t + min(2.8, max(0.8, scene_durations[i] - 0.15))
                    scene_title_events.append((st_t, en_t, sc.on_screen_text))

        has_ass_overlay = burn_subtitles or bool(clean_wm) or bool(scene_title_events)

        if has_aligned_words and subtitle_style == "karaoke_line" and not clean_wm and not scene_title_events:
            export_karaoke_ass(
                sb.scenes,
                ass_path,
                max_words_per_line=6,
                frame_preset=fp,
                margin_v=sub_margin_v,
            )
        elif timeline_segments or clean_wm or scene_title_events:
            effective_segments = timeline_segments
            if has_aligned_words and not any(seg.words for seg in timeline_segments):
                effective_segments = [
                    Segment(
                        start=sc.start_time,
                        end=sc.end_time or (sc.start_time + sc.duration_sec),
                        text=sc.speech,
                        words=getattr(sc, "words", []),
                    )
                    for sc in sb.scenes
                ]
            generate_story_karaoke_ass(
                effective_segments,
                ass_path,
                font_size=sub_font_size,
                font_name=sub_font,
                subtitle_style=subtitle_style,
                sub_margin_v=sub_margin_v,
                sub_color=sub_color,
                watermark_text=clean_wm or None,
                watermark_pos=watermark_pos,
                watermark_opacity=watermark_opacity,
                scene_titles=scene_title_events or None,
                total_duration=total_duration,
                burn_subtitles=burn_subtitles,
                frame_preset=fp,
            )
        else:
            self.rep.log("⚠️ Không có phân đoạn nào cho phụ đề.", "warn")
            ass_path.write_text("[Script Info]\nTitle: Empty\n[Events]\n", encoding="utf-8")

        exported_ass = output_path.with_suffix(".ass")
        shutil.copyfile(str(ass_path), str(exported_ass))

        # ------------------------------------------------------------- 4. Render video theo frame_preset (scene hoặc planned)
        rendered_scene_videos: List[Path] = []
        base_clip_durations: List[float] = []

        per_scene_grade_vf = (
            ("colorbalance=rs=0.03:gs=0.005:bs=-0.03" if per_scene_anti_ai else CINEMATIC_GRADE_FILTER)
            if (cinematic_grade and fp.needs_pad)
            else ""
        )

        # Chỉ bật xfade nếu có từ 2 phân đoạn trở lên và thời lượng tối thiểu đủ lớn
        use_xfade = transition != "none"
        eff_trans_dur = max(0.10, min(0.50, float(transition_duration)))

        if shot_mode == "planned":
            if not sb.beats:
                raise DubberError("storyboard chưa có beats; cần sinh beats trước khi chạy shot_mode='planned'")

            from .shot_plan_adapter import beats_from_storyboard, word_times_from_words
            from .shot_planner import plan_shots
            from .continuous_motion import group_continuous_runs, build_run_filter

            normalized_beats = []
            for b in sb.beats:
                if isinstance(b, dict):
                    b_copy = dict(b)
                    if "text" not in b_copy and "text_anchor" in b_copy:
                        b_copy["text"] = b_copy["text_anchor"]
                    normalized_beats.append(b_copy)

            beats = beats_from_storyboard({"beats": normalized_beats})

            all_words = []
            if has_aligned_words:
                all_words = [w for sc in sb.scenes for w in getattr(sc, "words", [])]
            elif timeline_segments:
                all_words = [w for seg in timeline_segments for w in seg.words]

            expected_tokens = sum(len(str(b.get("text") or "").split()) for b in normalized_beats)
            if expected_tokens > 0 and len(all_words) != expected_tokens:
                from .tts import estimate_word_timings
                self.rep.log(
                    f"⚠️ Tự căn lại mốc từ cho chế độ planned ({len(all_words)} → {expected_tokens} từ).",
                    "warn",
                )
                if len(timeline_segments) == len(normalized_beats):
                    rebuilt_words: List[WordTiming] = []
                    for seg, b in zip(timeline_segments, normalized_beats):
                        b_txt = str(b.get("text") or "").strip()
                        toks = b_txt.split()
                        if not toks:
                            continue
                        if len(seg.words) == len(toks):
                            rebuilt_words.extend(seg.words)
                        else:
                            est = estimate_word_timings(b_txt, max(0.1, seg.end - seg.start))
                            rebuilt_words.extend(w.scale_and_shift(speed=1.0, offset=seg.start) for w in est)
                    all_words = rebuilt_words
                else:
                    full_txt = " ".join(str(b.get("text") or "").strip() for b in normalized_beats if b.get("text"))
                    all_words = estimate_word_timings(full_txt, max(0.5, total_duration))

            word_times = word_times_from_words(all_words, beats=beats)
            shots = plan_shots(beats, word_times)
            runs = group_continuous_runs(shots)

            for i, r in enumerate(runs):
                if i < len(runs) - 1:
                    next_run_start = runs[i + 1].start
                    dur = max(0.5, round(next_run_start - r.start, 3)) if next_run_start > r.start else r.duration
                else:
                    rem_dur = round(total_duration - r.start, 3)
                    dur = max(0.5, rem_dur) if rem_dur > 0 else r.duration
                base_clip_durations.append(dur)

            if len(runs) < 2 or min(base_clip_durations) <= eff_trans_dur * 2.2:
                use_xfade = False

            runs_video_dir = work / "runs_video"
            runs_video_dir.mkdir(parents=True, exist_ok=True)

            for i, r in enumerate(runs):
                self.rep.check_cancel()
                self.rep.progress(45 + int(i * 35 / max(1, len(runs))), f"Render continuous run {fp.aspect_label} ({i + 1}/{len(runs)})")

                loc_name = r.location
                first_shot_id = r.shot_ids[0] if r.shot_ids else 1
                asset_file = find_location_asset(sb, assets_dir, loc_name, first_shot_id, asset_map)

                if asset_file is None:
                    raise DubberError(f"Không tìm thấy tài nguyên hình ảnh/clip cho địa điểm '{loc_name}'")

                # Bù đúng eff_trans_dur cho các clip trước clip cuối để xfade không làm ngắn tổng video
                tail_pad = eff_trans_dur if (use_xfade and i < len(runs) - 1) else 0.0
                render_dur = round(base_clip_durations[i] + tail_pad, 3)
                out_clip = runs_video_dir / f"run_{i:03d}.mp4"

                is_video = asset_file.suffix.lower() in ASSET_VIDEO_EXTS
                run_has_archival = any(sid in archival_ids for sid in (r.shot_ids or []))
                run_extra_parts = []
                if per_scene_anti_ai and not run_has_archival and ai_depolish_vf:
                    run_extra_parts.append(ai_depolish_vf)
                if per_scene_grade_vf:
                    run_extra_parts.append(per_scene_grade_vf)
                run_extra_vf = ",".join(run_extra_parts)
                if is_video:
                    cmd = _build_video_clip_cmd(
                        asset_file,
                        render_dur,
                        out_clip,
                        smart_clip_fit=smart_clip_fit,
                        extra_vf=run_extra_vf,
                        frame_preset=fp,
                    )
                else:
                    vf = build_run_filter(r, fps=30, frame_preset=fp, extra_vf=run_extra_vf)
                    cmd = [
                        "-loop", "1",
                        "-t", f"{render_dur:.3f}",
                        "-i", str(asset_file),
                        "-vf", vf,
                        "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-pix_fmt", "yuv420p",
                        "-an",
                        str(out_clip),
                    ]

                res = run_ffmpeg(cmd, check=False)
                if res.returncode != 0:
                    raise DubberError(f"Render run {i} ({loc_name}) thất bại:\n{res.stderr.strip()[-800:]}")

                rendered_scene_videos.append(out_clip)
        else:
            base_clip_durations = list(scene_durations)
            if len(sb.scenes) < 2 or min(base_clip_durations) <= eff_trans_dur * 2.2:
                use_xfade = False

            scenes_video_dir = work / "scenes_video"
            scenes_video_dir.mkdir(parents=True, exist_ok=True)

            for i, sc in enumerate(sb.scenes):
                self.rep.check_cancel()
                self.rep.progress(45 + int(i * 35 / max(1, total_scenes)), f"Render cảnh {fp.aspect_label} ({i + 1}/{total_scenes})")

                asset_file = asset_map[sc.id]
                tail_pad = eff_trans_dur if (use_xfade and i < total_scenes - 1) else 0.0
                render_dur = round(base_clip_durations[i] + tail_pad, 3)
                out_clip = scenes_video_dir / f"clip_{sc.id:03d}.mp4"

                is_video = (sc.asset_type == "flow_video") or (asset_file.suffix.lower() in ASSET_VIDEO_EXTS)
                scene_extra_parts = []
                if per_scene_anti_ai and (sc.id not in archival_ids) and ai_depolish_vf:
                    scene_extra_parts.append(ai_depolish_vf)
                if per_scene_grade_vf:
                    scene_extra_parts.append(per_scene_grade_vf)
                scene_extra_vf = ",".join(scene_extra_parts)
                if is_video:
                    cmd = _build_video_clip_cmd(
                        asset_file,
                        render_dur,
                        out_clip,
                        smart_clip_fit=smart_clip_fit,
                        extra_vf=scene_extra_vf,
                        frame_preset=fp,
                    )
                else:
                    effective_motion = sc.ken_burns or sc.motion
                    vf = _build_zoompan_filter(
                        effective_motion,
                        render_dur,
                        extra_vf=scene_extra_vf,
                        frame_preset=fp,
                    )
                    cmd = [
                        "-loop", "1",
                        "-t", f"{render_dur:.3f}",
                        "-i", str(asset_file),
                        "-vf", vf,
                        "-t", f"{render_dur:.3f}",
                        "-c:v", "libx264",
                        "-preset", "veryfast",
                        "-crf", "20",
                        "-pix_fmt", "yuv420p",
                        "-an",
                        str(out_clip),
                    ]

                res = run_ffmpeg(cmd, check=False)
                if res.returncode != 0:
                    raise DubberError(f"Render cảnh {sc.id} thất bại:\n{res.stderr.strip()[-800:]}")

                rendered_scene_videos.append(out_clip)

        self.rep.check_cancel()

        # ------------------------------------------------------------- 5. Ghép nối video (Xfade hoặc Concat)
        self.rep.progress(82, f"Đang ghép nối chuỗi cảnh video {fp.aspect_label}...")
        raw_full_video = work / "full_video_raw.mp4"

        xfade_done = False
        if use_xfade and len(rendered_scene_videos) >= 2:
            self.rep.log(f"✨ Đang áp chuyển cảnh mượt '{transition}' ({eff_trans_dur:.2f}s)...")
            xfade_done = _concat_with_xfade(
                rendered_scene_videos,
                base_clip_durations,
                raw_full_video,
                work,
                transition,
                eff_trans_dur,
            )
            if not xfade_done:
                self.rep.log("⚠️ Xfade không khả dụng trên cấu hình này -> tự lùi về nối trực tiếp.", "warn")

        if not xfade_done:
            concat_txt = work / "concat_list.txt"
            with concat_txt.open("w", encoding="utf-8") as f:
                for v_path in rendered_scene_videos:
                    rel = v_path.relative_to(work).as_posix()
                    f.write(f"file '{rel}'\n")

            cmd_concat = [
                "-f", "concat",
                "-safe", "0",
                "-i", "concat_list.txt",
                "-c", "copy",
                str(raw_full_video),
            ]
            res = run_ffmpeg(cmd_concat, cwd=work, check=False)
            if res.returncode != 0:
                raise DubberError(f"Nối video thất bại:\n{res.stderr.strip()[-800:]}")

        # ------------------------------------------------------------- 6. Ghép Voice Track & SFX Track
        self.rep.progress(85, "Đang dựng track âm thanh tổng & SFX...")
        total_samples = int(round(total_duration * SR))

        if resolved_full_audio is None:
            voice_buf = np.zeros(total_samples, dtype=np.float32)
            for i, wav_path in enumerate(scene_audio_files):
                x = fade_edges(read_wav(wav_path))
                start_s = timeline_starts[i]
                idx_start = int(round(start_s * SR))
                if idx_start >= total_samples:
                    break
                idx_end = min(idx_start + x.size, total_samples)
                voice_buf[idx_start:idx_end] = x[: idx_end - idx_start]

            write_wav(voice_track_path, voice_buf)

        sfx_search_dirs = [
            d for d in [
                self.sfx_dir,
                assets_dir / "sfx",
                ROOT_DIR / "assets" / "sfx",
                Path("assets/sfx"),
            ] if d is not None
        ]

        has_sfx = False
        sfx_track_path = None
        if eff_sfx_volume > 1e-4:
            sfx_buf = np.zeros(total_samples, dtype=np.float32)
            sfx_sources: List[str] = []

            for i, sc in enumerate(sb.scenes):
                if sc.sfx and sc.sfx.lower() != "none":
                    try:
                        sfx_data, source_tag = load_or_synthesize_sfx(
                            sc.sfx,
                            sfx_search_dirs,
                            work,
                            sample_rate=SR,
                            index=i,
                        )
                        if sfx_data is None or sfx_data.size == 0:
                            continue
                        start_s = timeline_starts[i] if i < len(timeline_starts) else sc.start_time
                        idx_start = max(0, int(round(start_s * SR)))
                        if idx_start >= total_samples:
                            continue
                        idx_end = min(idx_start + sfx_data.size, total_samples)
                        if idx_end > idx_start:
                            seg = sfx_data[: idx_end - idx_start].copy()
                            # Nếu SFX chạm mốc kết thúc video, làm mượt đuôi 20ms tránh tiếng tách và giữ nguyên độ dài
                            if idx_end - idx_start < sfx_data.size:
                                n_tail = min(int(round(SR * 0.020)), seg.size // 2)
                                if n_tail > 0:
                                    seg[-n_tail:] *= np.linspace(1.0, 0.0, n_tail, dtype=np.float32)
                            sfx_buf[idx_start:idx_end] += seg
                            has_sfx = True
                            sfx_sources.append(f"{sc.sfx}({source_tag})@{start_s:.2f}s")
                    except Exception as e:
                        self.rep.log(f"⚠️ Bỏ qua lỗi đọc/tạo SFX '{sc.sfx}': {e}", "warn")

            if has_sfx:
                sfx_peak = float(np.max(np.abs(sfx_buf)))
                if sfx_peak > 0.92:
                    # Nén mềm khi nhiều SFX chồng nhau tránh hard-clip trước khi đưa sang FFmpeg
                    sfx_buf = (
                        np.tanh(sfx_buf * (1.25 / sfx_peak)) / math.tanh(1.25)
                    ).astype(np.float32) * 0.92
                sfx_track_path = work / "sfx_track.wav"
                write_wav(sfx_track_path, sfx_buf)
                preview = ", ".join(sfx_sources[:6]) + ("..." if len(sfx_sources) > 6 else "")
                self.rep.log(
                    f"🔊 Đã tạo track SFX ({len(sfx_sources)} hiệu ứng | âm lượng {eff_sfx_volume:.0%}): {preview}",
                    "info",
                )
        else:
            self.rep.log("🔇 Âm lượng SFX = 0% → bỏ qua hiệu ứng âm thanh.", "info")

        # ------------------------------------------------------------- 7. Trộn BGM Fade/Ducking, SFX, Hậu kỳ & Xuất File
        self.rep.progress(90, f"Đang trộn âm thanh, áp hậu kỳ và xuất video {fp.aspect_label}...")

        resolved_bgm = None
        if bgm_path and Path(bgm_path).is_file():
            resolved_bgm = Path(bgm_path).resolve()
        elif sb.bgm:
            candidate_bgm = Path(sb.bgm)
            if not candidate_bgm.is_file():
                candidate_bgm = assets_dir / sb.bgm
            if candidate_bgm.is_file():
                resolved_bgm = candidate_bgm.resolve()

        prepared_bgm: Optional[Path] = None
        if resolved_bgm is not None:
            prepared_bgm = _prepare_bgm_audio(resolved_bgm, work, total_duration, reporter=self.rep)

        ass_work_file = work / "subs.ass"
        shutil.copyfile(str(ass_path), str(ass_work_file))

        is_h265 = str(codec).lower() in ("h265", "libx265", "hevc")
        if is_h265:
            vcodec_args = ["-c:v", "libx265", "-crf", "26", "-preset", "medium", "-pix_fmt", "yuv420p"]
        else:
            vcodec_args = ["-c:v", "libx264", "-profile:v", "high", "-crf", "24", "-preset", "medium", "-pix_fmt", "yuv420p"]

        metadata_args = get_clean_metadata_flags() if anti_ai_active else ["-movflags", "+faststart"]
        bgm_fade_in_d = min(1.0, max(0.1, total_duration * 0.2))
        bgm_fade_out_d = min(1.8, max(0.2, total_duration * 0.35))
        bgm_fade_out_st = max(0.0, total_duration - bgm_fade_out_d)
        bgm_fade = (
            f"atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={bgm_fade_in_d:.3f},"
            f"afade=t=out:st={bgm_fade_out_st:.3f}:d={bgm_fade_out_d:.3f}"
        )

        def build_mix_cmd(include_normalize: bool = True) -> List[str]:
            norm = ":normalize=0" if include_normalize else ""
            inputs = ["-i", str(raw_full_video), "-i", str(voice_track_path)]
            next_idx = 2

            bgm_idx = None
            if prepared_bgm is not None:
                inputs += ["-i", str(prepared_bgm)]
                bgm_idx = next_idx
                next_idx += 1

            sfx_idx = None
            if sfx_track_path:
                inputs += ["-i", str(sfx_track_path)]
                sfx_idx = next_idx
                next_idx += 1

            fc_parts = []
            vf_chain: List[str] = []
            # Chỉ giữ lớp màu nhẹ toàn cục ở bước cuối khi khung không có dải đen pad (còn khung có dải đen đã áp trước pad).
            if cinematic_grade and not fp.needs_pad:
                if per_scene_anti_ai:
                    vf_chain.append("colorbalance=rs=0.03:gs=0.005:bs=-0.03")
                else:
                    vf_chain.append(CINEMATIC_GRADE_FILTER)

            if has_ass_overlay:
                vf_chain.append("ass=subs.ass")

            if vf_chain:
                fc_parts.append(f"[0:v]{','.join(vf_chain)}[vout]")
                v_map = "[vout]"
            else:
                v_map = "0:v:0"

            post_parts: List[str] = []
            if loudness_norm:
                post_parts.append("loudnorm=I=-11:TP=-1.0:LRA=7")
            # Bộ giới hạn mềm bảo toàn 0 dBFS tránh rè/clipping ngay cả khi tăng SFX tới 150%
            post_parts.append("alimiter=limit=0.95:attack=5:release=50:level=0")
            master_post = ",".join(post_parts)

            mix_out_label = "[aout]"
            direct_post = f",{master_post}"
            sfx_bus = (
                f"[{sfx_idx}:a]{_FMT},"
                f"equalizer=f=100:t=q:w=1.0:g=3.5,"
                f"equalizer=f=2200:t=q:w=1.2:g=-3.5,"
                f"volume={eff_sfx_volume:.3f}[sfx];"
                if sfx_idx is not None
                else ""
            )

            if bgm_idx is not None and sfx_idx is not None:
                fc_parts.append(
                    f"[1:a]{_FMT},asplit=2[voice_sc][voice_mix];"
                    f"[{bgm_idx}:a]{_FMT},volume={bgm_volume},{bgm_fade}[bg];"
                    f"[bg][voice_sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked_bg];"
                    f"{sfx_bus}"
                    f"[voice_mix][ducked_bg][sfx]amix=inputs=3:duration=first{norm}{direct_post}{mix_out_label}"
                )
            elif bgm_idx is not None:
                fc_parts.append(
                    f"[1:a]{_FMT},asplit=2[voice_sc][voice_mix];"
                    f"[{bgm_idx}:a]{_FMT},volume={bgm_volume},{bgm_fade}[bg];"
                    f"[bg][voice_sc]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[ducked_bg];"
                    f"[voice_mix][ducked_bg]amix=inputs=2:duration=first{norm}{direct_post}{mix_out_label}"
                )
            elif sfx_idx is not None:
                fc_parts.append(
                    f"[1:a]{_FMT}[voice];"
                    f"{sfx_bus}"
                    f"[voice][sfx]amix=inputs=2:duration=first{norm}{direct_post}{mix_out_label}"
                )
            else:
                fc_parts.append(f"[1:a]{_FMT}{direct_post}{mix_out_label}")

            filter_complex_str = ";".join(fc_parts)
            return [
                *inputs,
                "-filter_complex", filter_complex_str,
                "-map", v_map,
                "-map", "[aout]",
                "-t", f"{total_duration:.3f}",
                "-r", "30",
                *vcodec_args,
                "-c:a", "aac",
                "-b:a", "128k",
                *metadata_args,
                str(output_path),
            ]

        res = run_ffmpeg(build_mix_cmd(True), cwd=work, check=False)
        if res.returncode != 0 and "normalize" in res.stderr:
            res = run_ffmpeg(build_mix_cmd(False), cwd=work, check=False)

        if res.returncode != 0:
            raise DubberError(f"Xuất video Story cuối thất bại:\n{res.stderr.strip()[-900:]}")

        try:
            out_info = probe(output_path)
            if abs(out_info.duration - total_duration) > 0.5:
                self.rep.log(
                    f"⚠️ Độ dài video ({out_info.duration:.2f}s) lệch so với âm thanh ({total_duration:.2f}s)",
                    "warn",
                )
        except Exception:
            pass

        file_size_mb = output_path.stat().st_size / (1024 * 1024)
        self.rep.progress(100, f"Hoàn tất xuất video {fp.aspect_label} ({file_size_mb:.1f}MB)")
        self.rep.log(f"🎉 Hoàn thành video Story {fp.aspect_label} ({fp.out_w}x{fp.out_h}): {output_path} ({file_size_mb:.1f}MB)", "ok")
        return output_path