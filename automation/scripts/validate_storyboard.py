#!/usr/bin/env python3
"""Kiểm định và tự đồng bộ Storyboard V5 cho DubStudio v3.2 + Google Flow.

Giao diện CLI:
    python automation/scripts/validate_storyboard.py <path_to_storyboard.json> [--sync-files] [--strict]

Mặc định chạy ở chế độ chỉ đọc (read-only). Không phụ thuộc thư viện bên ngoài,
import trực tiếp `core.story_schema` từ thư mục gốc repo.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Đảm bảo in tiếng Việt không lỗi cp1252/cp1258 trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Thêm repo root vào sys.path để import trực tiếp core.story_schema
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.story_schema import (  # noqa: E402
    VALID_MOTIONS,
    VALID_SFX,
    Storyboard,
    load_storyboard,
)

VALID_INTENTS = {"establish", "explain", "reveal", "emotion"}
VALID_ASSET_TYPES = {"flow_video", "static_image"}

# Bảng 4 Hạng tỉ lệ ưu tiên Tư liệu thật (V5.2)
ASSET_RATIO_TIERS: Dict[str, Dict[str, Any]] = {
    "0_100": {
        "label": "0_100 (Hạng 1 — Ưu tiên 100% tư liệu thật)",
        "target_ai": 0.0,
        "min_ai": 0.0,
        "max_ai": 15.0,
        "max_consec_static": None,
    },
    "30_70": {
        "label": "30_70 (Hạng 2 — Kết hợp tư liệu & clip AI)",
        "target_ai": 30.0,
        "min_ai": 25.0,
        "max_ai": 38.0,
        "max_consec_static": 4,
    },
    "50_50": {
        "label": "50_50 (Hạng 3 — Cân bằng AI & Tư liệu)",
        "target_ai": 50.0,
        "min_ai": 45.0,
        "max_ai": 55.0,
        "max_consec_static": 3,
    },
    "70_30": {
        "label": "70_30 (Hạng 4 — Dự phòng cổ đại/ít tư liệu)",
        "target_ai": 70.0,
        "min_ai": 65.0,
        "max_ai": 75.0,
        "max_consec_static": 2,
    },
}

HOOK_FORBIDDEN_PREFIXES: Tuple[str, ...] = (
    "ở tập trước",
    "tập trước",
    "hôm nay",
    "chào mừng",
    "xin chào",
)

FORBIDDEN_WORDS = [
    "blood",
    "gore",
    "kill",
    "killing",
    "dead",
    "corpse",
    "die",
    "suicide",
    "shoot",
    "shooting",
    "firing gun",
    "gunshot",
    "stab",
    "slash",
    "bomb",
    "bombing",
    "explode",
    "explosion",
    "blast",
    "detonate",
    "weapon",
    "rifle",
    "grenade",
    "screaming",
    "carnage",
    "warzone",
    "attack",
    "assault",
    "decapitation",
    "brutal wounds",
]

FORBIDDEN_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (w, re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE))
    for w in FORBIDDEN_WORDS
]

# Tốc độ đọc ước lượng theo chuẩn V5 (~3.2 từ/giây + 0.35s nghỉ mỗi cảnh)
WORDS_PER_SEC = 3.2
SCENE_PAUSE_SEC = 0.35


@dataclass
class TierReport:
    name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _fmt_ts(sec: float) -> str:
    """Chuyển giây (float) sang định dạng HH:MM:SS.mmm."""
    total_ms = max(0, int(round(sec * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{ms:03d}"


def _fmt_mmss(sec: float) -> str:
    """Chuyển giây sang MM:SS để hiển thị thống kê."""
    total_s = max(0, int(round(sec)))
    m, s = divmod(total_s, 60)
    return f"{m:02d}:{s:02d}"


def _resolve_location_file(locations: Dict[str, Any], loc_name: str, sb: Optional[Storyboard]) -> Optional[str]:
    """Lấy tên file ánh xạ trong locations (hỗ trợ cả string và dict)."""
    if sb is not None:
        resolved = sb.resolve_location_filename(loc_name)
        if resolved is not None:
            return resolved
    if not isinstance(locations, dict) or loc_name not in locations:
        return None
    val = locations[loc_name]
    if isinstance(val, str) and val.strip():
        return val.strip()
    if isinstance(val, dict):
        for k in ("file", "asset", "path", "filename", "image", "video"):
            v = val.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _estimate_scene_duration(speech: str, duration_sec: Any = None) -> float:
    """Ước lượng thời lượng 1 cảnh nếu thiếu mốc thời gian."""
    try:
        d = float(duration_sec)
        if d > 0:
            return d
    except (TypeError, ValueError):
        pass
    word_count = len(speech.split())
    return round(max(1.0, word_count / WORDS_PER_SEC + SCENE_PAUSE_SEC), 3)


def _compute_timeline(scenes: List[Dict[str, Any]]) -> Tuple[List[Tuple[float, float]], bool]:
    """Lấy danh sách (start, end) cho từng cảnh; trả về (timings, is_estimated)."""
    has_valid_times = True
    timings: List[Tuple[float, float]] = []
    for sc in scenes:
        try:
            st = float(sc.get("start_time", 0.0))
            en = float(sc.get("end_time", 0.0))
        except (TypeError, ValueError):
            has_valid_times = False
            break
        if en <= st:
            has_valid_times = False
            break
        timings.append((st, en))

    if has_valid_times and len(timings) == len(scenes) and scenes:
        return timings, False

    # Tự ước lượng liên tiếp nếu thiếu hoặc chưa có mốc start_time/end_time hợp lệ
    est_timings: List[Tuple[float, float]] = []
    cursor = 0.0
    for sc in scenes:
        speech = str(sc.get("speech", "")).strip()
        dur = _estimate_scene_duration(speech, sc.get("duration_sec"))
        st = round(cursor, 3)
        en = round(cursor + dur, 3)
        est_timings.append((st, en))
        cursor = en
    return est_timings, True


def validate_storyboard(path: Path) -> Tuple[List[TierReport], Optional[Dict[str, Any]], Dict[str, Any]]:
    """Chạy đầy đủ 5 tầng kiểm định. Trả về (reports, raw_data, stats)."""
    t1 = TierReport("Tầng 1 — Core Schema DubStudio")
    t2 = TierReport("Tầng 2 — Đồng bộ Whisper & ElevenLabs")
    t3 = TierReport("Tầng 3 — Ánh xạ locations, asset_type & Enum DubStudio")
    t4 = TierReport("Tầng 4 — Bộ lọc An toàn Google Flow")
    t5 = TierReport("Tầng 5 — Nhịp điệu, Hook 3s & Tỉ lệ V5.2")
    reports = [t1, t2, t3, t4, t5]
    stats: Dict[str, Any] = {}

    if not path.is_file():
        t1.errors.append(f"Không tìm thấy file storyboard: {path}")
        return reports, None, stats

    try:
        raw_text = path.read_text(encoding="utf-8-sig")
    except Exception as e:
        t1.errors.append(f"Không thể đọc file UTF-8 ({path.name}): {e}")
        return reports, None, stats

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        t1.errors.append(f"Cú pháp JSON không hợp lệ: {e}")
        return reports, None, stats

    if not isinstance(data, dict):
        t1.errors.append("Root của storyboard.json phải là JSON object (dict).")
        return reports, None, stats

    # =========================================================================
    # TẦNG 1: Core Schema DubStudio (ERROR)
    # =========================================================================
    sb: Optional[Storyboard] = None
    try:
        sb = load_storyboard(path)
    except Exception as e:
        t1.errors.append(f"core.story_schema.load_storyboard() báo lỗi: {e}")

    if "project_title" in data:
        t1.errors.append("Không được dùng khóa 'project_title' — phải dùng 'title'.")
    title_val = data.get("title")
    if not isinstance(title_val, str) or not title_val.strip():
        t1.errors.append("Thiếu khóa bắt buộc 'title' hoặc giá trị rỗng.")

    scenes_raw = data.get("scenes")
    if not isinstance(scenes_raw, list) or len(scenes_raw) == 0:
        t1.errors.append("Danh sách 'scenes' bị thiếu hoặc rỗng.")
        return reports, data, stats

    scenes: List[Dict[str, Any]] = []
    seen_ids: List[int] = []
    for idx, sc in enumerate(scenes_raw):
        if not isinstance(sc, dict):
            t1.errors.append(f"scenes[{idx}] không phải JSON object (dict).")
            continue
        scenes.append(sc)
        if "scene_id" in sc:
            t1.errors.append(f"scenes[{idx}] dùng 'scene_id' — phải đổi thành 'id'.")
        if "id" not in sc:
            t1.errors.append(f"scenes[{idx}] thiếu trường bắt buộc 'id'.")
        else:
            raw_id = sc["id"]
            if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                t1.errors.append(f"scenes[{idx}] có 'id'={raw_id!r} không phải số nguyên.")
            else:
                seen_ids.append(raw_id)

    expected_ids = list(range(1, len(scenes_raw) + 1))
    if seen_ids and seen_ids != expected_ids:
        t1.errors.append(
            f"Danh sách 'id' trong scenes phải liên tiếp 1..{len(scenes_raw)}, hiện tại: {seen_ids}."
        )

    # =========================================================================
    # TẦNG 2: Đồng bộ Whisper & ElevenLabs (ERROR)
    # =========================================================================
    beats_raw = data.get("beats")
    beats: List[Dict[str, Any]] = []
    if not isinstance(beats_raw, list):
        t2.errors.append("Thiếu danh sách 'beats' (hoặc không phải list).")
    else:
        for idx, b in enumerate(beats_raw):
            if isinstance(b, dict):
                beats.append(b)
            else:
                t2.errors.append(f"beats[{idx}] không phải JSON object (dict).")

        if len(beats_raw) != len(scenes_raw):
            t2.errors.append(
                f"Số lượng beats ({len(beats_raw)}) khác số lượng scenes ({len(scenes_raw)})."
            )

    pair_len = min(len(beats), len(scenes))
    for i in range(pair_len):
        sc = scenes[i]
        bt = beats[i]
        sid = sc.get("id", i + 1)
        sc_speech = sc.get("speech")
        bt_text = bt.get("text")
        if bt_text != sc_speech:
            t2.errors.append(
                f"Cảnh {sid}: beats[{i}].text không khớp 100% với scenes[{i}].speech."
            )

    expected_full_script = " ".join(str(sc.get("speech", "")) for sc in scenes)
    actual_full_script = data.get("full_script_for_elevenlabs")
    if actual_full_script is None:
        t2.errors.append("Thiếu trường bắt buộc 'full_script_for_elevenlabs'.")
    elif actual_full_script != expected_full_script:
        t2.errors.append(
            "'full_script_for_elevenlabs' không khớp 100% với chuỗi ghép các scenes[i].speech (cách nhau đúng 1 dấu cách)."
        )

    for i, sc in enumerate(scenes):
        sid = sc.get("id", i + 1)
        speech = str(sc.get("speech", ""))
        if "..." in speech or "…" in speech:
            t2.errors.append(f"Cảnh {sid}: 'speech' chứa dấu ba chấm ('...' hoặc '…').")
        if "(" in speech or ")" in speech:
            t2.errors.append(f"Cảnh {sid}: 'speech' chứa ngoặc đơn '(...)'.")

    # =========================================================================
    # TẦNG 3: Ánh xạ locations, asset_type & Enum DubStudio (ERROR)
    # =========================================================================
    locations = data.get("locations")
    if not isinstance(locations, dict):
        t3.errors.append("Thiếu bảng ánh xạ 'locations' (hoặc không phải dict).")
        locations = {}

    for i, sc in enumerate(scenes):
        sid = sc.get("id", i + 1)
        sid_int = sid if isinstance(sid, int) and not isinstance(sid, bool) else (i + 1)

        asset_type = sc.get("asset_type")
        if asset_type not in VALID_ASSET_TYPES:
            t3.errors.append(
                f"Cảnh {sid}: 'asset_type'={asset_type!r} không hợp lệ (chấp nhận: {sorted(VALID_ASSET_TYPES)})."
            )

        # Kiểm tra motion & ken_burns đều phải có mặt và hợp lệ
        if "motion" not in sc or not str(sc.get("motion", "")).strip():
            t3.errors.append(f"Cảnh {sid}: thiếu trường bắt buộc 'motion'.")
        elif sc["motion"] not in VALID_MOTIONS:
            t3.errors.append(
                f"Cảnh {sid}: 'motion'={sc['motion']!r} không hợp lệ (chấp nhận: {sorted(VALID_MOTIONS)})."
            )

        if "ken_burns" not in sc or not str(sc.get("ken_burns", "")).strip():
            t3.errors.append(f"Cảnh {sid}: thiếu trường bắt buộc 'ken_burns'.")
        elif sc["ken_burns"] not in VALID_MOTIONS:
            t3.errors.append(
                f"Cảnh {sid}: 'ken_burns'={sc['ken_burns']!r} không hợp lệ (chấp nhận: {sorted(VALID_MOTIONS)})."
            )

        # Kiểm tra sfx trong scene
        sc_sfx = sc.get("sfx")
        if sc_sfx not in VALID_SFX:
            t3.errors.append(
                f"Cảnh {sid}: scenes[{i}].sfx={sc_sfx!r} không hợp lệ (chấp nhận: {sorted(VALID_SFX)})."
            )

        if i < len(beats):
            bt = beats[i]
            loc = bt.get("location")
            if not isinstance(loc, str) or not loc.strip():
                t3.errors.append(f"Cảnh {sid}: beats[{i}] thiếu trường 'location'.")
            elif loc not in locations:
                t3.errors.append(f"Cảnh {sid}: location '{loc}' không tồn tại trong bảng 'locations'.")
            else:
                mapped_file = _resolve_location_file(locations, loc, sb)
                if asset_type == "flow_video":
                    expected_file = f"{sid_int:03d}.mp4"
                    if mapped_file != expected_file:
                        t3.errors.append(
                            f"Cảnh {sid} (flow_video): locations['{loc}']={mapped_file!r}, yêu cầu '{expected_file}'."
                        )
                elif asset_type == "static_image":
                    expected_file = f"{sid_int:03d}.jpg"
                    if mapped_file != expected_file:
                        t3.errors.append(
                            f"Cảnh {sid} (static_image): locations['{loc}']={mapped_file!r}, yêu cầu '{expected_file}'."
                        )

            bt_sfx = bt.get("sfx")
            if bt_sfx not in VALID_SFX:
                t3.errors.append(
                    f"Cảnh {sid}: beats[{i}].sfx={bt_sfx!r} không hợp lệ (chấp nhận: {sorted(VALID_SFX)})."
                )
            elif sc_sfx in VALID_SFX and bt_sfx != sc_sfx:
                t3.errors.append(
                    f"Cảnh {sid}: sfx không khớp giữa beats ({bt_sfx!r}) và scenes ({sc_sfx!r})."
                )

            intent = bt.get("intent")
            if intent not in VALID_INTENTS:
                t3.errors.append(
                    f"Cảnh {sid}: beats[{i}].intent={intent!r} không hợp lệ (chấp nhận: {sorted(VALID_INTENTS)})."
                )

            intensity = bt.get("intensity")
            if isinstance(intensity, bool) or not isinstance(intensity, int) or not (1 <= intensity <= 3):
                t3.errors.append(
                    f"Cảnh {sid}: beats[{i}].intensity={intensity!r} phải là số nguyên trong khoảng 1..3."
                )

    # =========================================================================
    # TẦNG 4: Bộ lọc An toàn Google Flow (ERROR)
    # =========================================================================
    for i, sc in enumerate(scenes):
        sid = sc.get("id", i + 1)
        asset_type = sc.get("asset_type")
        img_prompt = str(sc.get("image_prompt") or "")
        mot_prompt = str(sc.get("motion_prompt") or "")

        # Quét từ cấm trên cả image_prompt và motion_prompt
        for field_name, prompt_val in (("image_prompt", img_prompt), ("motion_prompt", mot_prompt)):
            if not prompt_val:
                continue
            for word, pat in FORBIDDEN_PATTERNS:
                if pat.search(prompt_val):
                    t4.errors.append(
                        f"Cảnh {sid} | Trường {field_name} | Dính từ cấm: '{word}'"
                    )

        # Kiểm tra tính đầy đủ của prompt
        if not img_prompt.strip():
            t4.errors.append(f"Cảnh {sid}: 'image_prompt' bị rỗng.")
        else:
            img_lower = img_prompt.lower()
            if "9:16" not in img_lower:
                t4.errors.append(f"Cảnh {sid}: 'image_prompt' thiếu '9:16'.")
            if "no text" not in img_lower:
                t4.errors.append(f"Cảnh {sid}: 'image_prompt' thiếu 'no text'.")

        if asset_type == "flow_video":
            if not mot_prompt.strip():
                t4.errors.append(f"Cảnh {sid} (flow_video): 'motion_prompt' bị rỗng.")
        elif asset_type == "static_image":
            if mot_prompt != "":
                t4.errors.append(
                    f"Cảnh {sid} (static_image): 'motion_prompt' phải để rỗng \"\" (tránh bot chạy nhầm tốn 10 tín dụng)."
                )

    # =========================================================================
    # TẦNG 5: Nhịp điệu, Hook 3s & Tỉ lệ V5.2 (WARNING)
    # =========================================================================
    total_scenes = len(scenes)
    flow_count = sum(1 for sc in scenes if sc.get("asset_type") == "flow_video")
    static_count = sum(1 for sc in scenes if sc.get("asset_type") == "static_image")
    pct_flow = (flow_count / total_scenes * 100.0) if total_scenes else 0.0
    pct_static = (static_count / total_scenes * 100.0) if total_scenes else 0.0
    total_words = sum(len(str(sc.get("speech", "")).split()) for sc in scenes)
    sfx_scene_count = sum(
        1 for sc in scenes if str(sc.get("sfx") or "").strip().lower() not in ("", "none")
    )

    # Xác định Hạng tỉ lệ V5.2 (đọc từ JSON nếu có; nếu thiếu thì tự suy luận theo % flow_video)
    raw_tier = data.get("asset_ratio_tier")
    declared_tier: Optional[str] = None
    if raw_tier is not None and str(raw_tier).strip() != "":
        norm_tier = str(raw_tier).strip()
        if norm_tier in ASSET_RATIO_TIERS:
            declared_tier = norm_tier
        else:
            t5.warnings.append(
                f"Giá trị 'asset_ratio_tier'={raw_tier!r} không hợp lệ (chấp nhận: {sorted(ASSET_RATIO_TIERS)})."
            )

    if declared_tier is not None:
        effective_tier = declared_tier
        tier_inferred = False
    else:
        if pct_flow <= 15.0:
            effective_tier = "0_100"
        else:
            effective_tier = min(
                ("30_70", "50_50", "70_30"),
                key=lambda k: abs(pct_flow - ASSET_RATIO_TIERS[k]["target_ai"]),
            )
        tier_inferred = True

    tier_cfg = ASSET_RATIO_TIERS[effective_tier]
    raw_max_consec = tier_cfg["max_consec_static"]
    max_consec_static: Optional[int] = int(raw_max_consec) if raw_max_consec is not None else None

    # Nếu JSON có khai báo asset_ratio_tier thì kiểm tra biên độ tỉ lệ flow_video
    if declared_tier is not None and total_scenes > 0:
        min_ai = float(tier_cfg["min_ai"])
        max_ai = float(tier_cfg["max_ai"])
        if not (min_ai <= pct_flow <= max_ai):
            t5.warnings.append(
                f"Tỉ lệ flow_video thực tế là {pct_flow:.1f}% ({flow_count}/{total_scenes} cảnh), "
                f"lệch ngoài biên độ hạng '{declared_tier}' ({min_ai:.0f}%–{max_ai:.0f}%)."
            )

    timings, is_est_time = _compute_timeline(scenes)
    total_duration = timings[-1][1] if timings else 0.0

    if not (20 <= total_scenes <= 30):
        t5.warnings.append(
            f"Tổng số cảnh là {total_scenes} (khuyến nghị V5.2: 20–22 cảnh, tối đa 30)."
        )

    if not (280 <= total_words <= 460):
        t5.warnings.append(
            f"Tổng số từ là {total_words} từ (khuyến nghị V5.2: 300–350 từ hoặc 380–460 từ tập dài)."
        )

    dur_label = "ước lượng" if is_est_time else "end_time cảnh cuối"
    if not (90.0 <= total_duration <= 145.0):
        t5.warnings.append(
            f"Tổng thời lượng ({dur_label}) là {total_duration:.1f}s ({_fmt_mmss(total_duration)}) "
            f"(khuyến nghị V5.2: 96–120s gốc / 80–100s ở 1.2x, tối đa 145s)."
        )

    # Ở các hạng có dùng video AI (30_70, 50_50, 70_30), Cảnh 1 ưu tiên flow_video; hạng 0_100 được phép dùng static_image
    if effective_tier != "0_100" and scenes and scenes[0].get("asset_type") == "static_image":
        t5.warnings.append("Cảnh 1 (Hook) đang là 'static_image' (với hạng có AI nên ưu tiên 'flow_video').")

    if max_consec_static is not None:
        consec_static: List[Any] = []
        for i, sc in enumerate(scenes):
            sid = sc.get("id", i + 1)
            if sc.get("asset_type") == "static_image":
                consec_static.append(sid)
            else:
                if len(consec_static) > max_consec_static:
                    t5.warnings.append(
                        f"Có {len(consec_static)} cảnh 'static_image' đứng liền nhau: {consec_static} "
                        f"(hạng '{effective_tier}' tối đa {max_consec_static})."
                    )
                consec_static = []
        if len(consec_static) > max_consec_static:
            t5.warnings.append(
                f"Có {len(consec_static)} cảnh 'static_image' đứng liền nhau: {consec_static} "
                f"(hạng '{effective_tier}' tối đa {max_consec_static})."
            )

    # Luật chống nhàm chán cho static_image: cấm ken_burns == 'static' và cấm 2 cảnh liền kề trùng ken_burns
    for i, sc in enumerate(scenes):
        if sc.get("asset_type") != "static_image":
            continue
        sid = sc.get("id", i + 1)
        kb = str(sc.get("ken_burns") or "").strip().lower()
        if kb == "static":
            t5.warnings.append(
                f"Cảnh {sid} (static_image): 'ken_burns' đang là 'static' (cần dùng zoom_in/zoom_out/pan_left/pan_right để khung hình luôn chuyển động)."
            )
        if i > 0 and scenes[i - 1].get("asset_type") == "static_image":
            prev_sid = scenes[i - 1].get("id", i)
            prev_kb = str(scenes[i - 1].get("ken_burns") or "").strip().lower()
            if kb and kb == prev_kb:
                t5.warnings.append(
                    f"Cảnh {prev_sid} & {sid} (2 cảnh static_image liền kề): bị trùng 'ken_burns'='{kb}' (phải đổi chiều chuyển động)."
                )

    # Kiểm tra Hook 3 giây Cảnh 1 + độ dài câu speech & on_screen_text từng cảnh
    hook_words = 0
    hook_sfx = "none"
    for i, sc in enumerate(scenes):
        sid = sc.get("id", i + 1)
        raw_speech = str(sc.get("speech", "")).strip()
        speech_words = len(raw_speech.split())
        sc_sfx = str(sc.get("sfx") or "none").strip() or "none"

        if i == 0:
            hook_words = speech_words
            hook_sfx = sc_sfx
            if speech_words < 8:
                t5.warnings.append(f"Cảnh {sid} (Hook 3s): câu 'speech' quá ngắn ({speech_words} từ, chuẩn V5.2: 8–13 từ).")
            elif speech_words > 14:
                t5.warnings.append(
                    f"Cảnh {sid} (Hook 3s): câu 'speech' dài {speech_words} từ (> 14 từ, dễ vượt 3.5 giây gây loãng Hook; chuẩn V5.2: 8–13 từ)."
                )
            clean_hook = raw_speech.lower().lstrip("\"'“”‘’—–- ")
            for pf in HOOK_FORBIDDEN_PREFIXES:
                if re.match(r"^" + re.escape(pf) + r"(?:\b|[,\.\-:!?])", clean_hook):
                    t5.warnings.append(
                        f"Cảnh {sid} (Hook 3s): mở đầu bằng cụm từ cấm '{pf}' (hãy mở bằng câu hỏi/con số gây sốc; nối tập trước đẩy sang Cảnh 2)."
                    )
                    break
            if sc_sfx.lower() in ("", "none"):
                t5.warnings.append(
                    f"Cảnh {sid} (Hook 3s): chưa gắn 'sfx' mở màn tại 0.00s (khuyên dùng 'boom', 'cinematic_impact', 'artillery_fire', 'whoosh' hoặc 'alarm')."
                )
        else:
            if speech_words < 10:
                t5.warnings.append(f"Cảnh {sid}: câu 'speech' quá ngắn ({speech_words} từ, khuyến nghị 10–22 từ).")
            elif speech_words > 22:
                t5.warnings.append(f"Cảnh {sid}: câu 'speech' quá dài ({speech_words} từ, khuyến nghị 10–22 từ).")

        ost = str(sc.get("on_screen_text") or "").strip()
        if ost:
            ost_words = len(ost.split())
            if ost_words > 7:
                t5.warnings.append(
                    f"Cảnh {sid}: 'on_screen_text' dài {ost_words} từ (> 7 từ): {ost!r}."
                )
            if any(ch.isalpha() for ch in ost) and ost != ost.upper():
                t5.warnings.append(
                    f"Cảnh {sid}: 'on_screen_text' chưa viết IN HOA: {ost!r}."
                )

    stats = {
        "title": str(data.get("title") or ""),
        "asset_ratio_tier": effective_tier,
        "tier_label": tier_cfg["label"],
        "tier_inferred": tier_inferred,
        "pct_flow": pct_flow,
        "pct_static": pct_static,
        "total_scenes": total_scenes,
        "flow_video": flow_count,
        "static_image": static_count,
        "veo_credits": flow_count * 10,
        "total_words": total_words,
        "hook_words": hook_words,
        "hook_sfx": hook_sfx,
        "sfx_scene_count": sfx_scene_count,
        "total_duration": total_duration,
        "duration_1_2x": round(total_duration / 1.2, 2) if total_duration > 0 else 0.0,
        "is_estimated_duration": is_est_time,
    }
    return reports, data, stats


def sync_episode_files(storyboard_path: Path, data: Dict[str, Any]) -> None:
    """Tạo/cập nhật assets/, raw_images/, elevenlabs.txt, timeline_script.txt, pipeline_state.json."""
    ep_dir = storyboard_path.parent
    (ep_dir / "assets").mkdir(parents=True, exist_ok=True)
    (ep_dir / "raw_images").mkdir(parents=True, exist_ok=True)

    scenes: List[Dict[str, Any]] = data.get("scenes", [])

    # 1. Ghi elevenlabs.txt (mỗi câu thoại 1 dòng sạch)
    el_lines = [str(sc.get("speech", "")).strip() for sc in scenes]
    (ep_dir / "elevenlabs.txt").write_text("\n".join(el_lines) + "\n", encoding="utf-8")

    # 2. Ghi timeline_script.txt
    timings, _ = _compute_timeline(scenes)
    tl_lines = [
        f"[{_fmt_ts(st)} -> {_fmt_ts(en)}] {str(sc.get('speech', '')).strip()}"
        for sc, (st, en) in zip(scenes, timings)
    ]
    (ep_dir / "timeline_script.txt").write_text("\n".join(tl_lines) + "\n", encoding="utf-8")

    # 3. Tạo hoặc cập nhật pipeline_state.json (Idempotent)
    state_path = ep_dir / "pipeline_state.json"
    existing_by_id: Dict[int, Dict[str, Any]] = {}
    if state_path.is_file():
        try:
            old_state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if isinstance(old_state, dict) and isinstance(old_state.get("scenes"), list):
                for item in old_state["scenes"]:
                    if isinstance(item, dict) and isinstance(item.get("id"), int):
                        existing_by_id[item["id"]] = item
        except Exception:
            # Nếu file state cũ bị hỏng, khởi tạo lại an toàn
            existing_by_id = {}

    new_state_scenes: List[Dict[str, Any]] = []
    for sc in scenes:
        sid = int(sc["id"])
        asset_type = str(sc.get("asset_type", "static_image"))
        target_image = f"raw_images/{sid:03d}.png"
        if asset_type == "flow_video":
            target_asset = f"assets/{sid:03d}.mp4"
            default_video_status = "pending"
        else:
            target_asset = f"assets/{sid:03d}.jpg"
            default_video_status = "skipped"

        old = existing_by_id.get(sid)
        if old is not None and old.get("asset_type") == asset_type:
            img_status = str(old.get("image_status", "pending"))
            vid_status = str(old.get("video_status", default_video_status))
            err_msg = str(old.get("error", ""))
        else:
            img_status = "pending"
            vid_status = default_video_status
            err_msg = ""

        new_state_scenes.append(
            {
                "id": sid,
                "asset_type": asset_type,
                "target_image": target_image,
                "target_asset": target_asset,
                "image_status": img_status,
                "video_status": vid_status,
                "error": err_msg,
            }
        )

    state_payload = {
        "title": str(data.get("title", "")),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scenes": new_state_scenes,
    }
    state_path.write_text(
        json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def print_report(
    storyboard_path: Path,
    reports: List[TierReport],
    stats: Dict[str, Any],
    *,
    strict: bool,
) -> Tuple[int, int]:
    """In báo cáo kiểm định đầy đủ và trả về (total_errors, total_warnings)."""
    total_errors = 0
    total_warnings = 0

    print("=" * 76)
    print(f"KIỂM ĐỊNH STORYBOARD V5: {storyboard_path}")
    if strict:
        print("Chế độ: STRICT (mọi WARNING được nâng lên thành ERROR)")
    print("=" * 76)

    for rep in reports:
        eff_errors = list(rep.errors)
        eff_warnings = list(rep.warnings)
        if strict and eff_warnings:
            eff_errors.extend(f"[STRICT] {w}" for w in eff_warnings)
            eff_warnings = []

        total_errors += len(eff_errors)
        total_warnings += len(eff_warnings)

        if eff_errors:
            badge = f"FAIL ({len(eff_errors)} ERROR" + (f", {len(eff_warnings)} WARN)" if eff_warnings else ")")
        elif eff_warnings:
            badge = f"PASS WITH WARNINGS ({len(eff_warnings)} WARN)"
        else:
            badge = "PASS"

        print(f"\n[{badge}] {rep.name}")
        for msg in eff_errors:
            print(f"  [ERROR] {msg}")
        for msg in eff_warnings:
            print(f"  [WARN]  {msg}")

    if stats:
        n_scenes = stats["total_scenes"]
        n_flow = stats["flow_video"]
        n_static = stats["static_image"]
        pct_flow = (n_flow / n_scenes * 100.0) if n_scenes else 0.0
        pct_static = (n_static / n_scenes * 100.0) if n_scenes else 0.0
        dur_orig = stats["total_duration"]
        dur_12x = stats["duration_1_2x"]
        dur_note = " (ước lượng)" if stats.get("is_estimated_duration") else ""

        tier_code = stats.get("asset_ratio_tier", "")
        tier_src = "tự suy luận" if stats.get("tier_inferred") else "khai báo"
        print("\n" + "-" * 76)
        print("THỐNG KÊ TỔNG QUAN TẬP")
        print("-" * 76)
        print(f"  Tiêu đề (title)       : {stats.get('title') or '(trống)'}")
        print(
            f"  Hạng tỉ lệ (tier)     : {tier_code} ({tier_src}) | "
            f"AI {pct_flow:.1f}% / Thật {pct_static:.1f}%"
        )
        print(f"  Tổng số cảnh          : {n_scenes}")
        print(f"  Cảnh flow_video       : {n_flow} ({pct_flow:.1f}%)  | Ước tính Veo 3.1: {stats['veo_credits']} tín dụng")
        print(f"  Cảnh static_image     : {n_static} ({pct_static:.1f}%)  | 0 tín dụng (Ken Burns)")
        print(f"  Tổng số từ            : {stats['total_words']} từ")
        print(f"  Hook Cảnh 1 (3s)      : {stats.get('hook_words', 0)} từ | SFX: {stats.get('hook_sfx', 'none')}")
        print(f"  Số cảnh có SFX        : {stats.get('sfx_scene_count', 0)}/{n_scenes} cảnh")
        dur_label_str = f"Thời lượng gốc{dur_note}"
        print(f"  {dur_label_str:<22}: {dur_orig:.1f}s ({_fmt_mmss(dur_orig)})")
        print(f"  Thời lượng ở 1.2x     : {dur_12x:.1f}s ({_fmt_mmss(dur_12x)})")

    print("\n" + "=" * 76)
    if total_errors == 0:
        print(f"KẾT LUẬN: PASS (0 ERROR, {total_warnings} WARNING)")
    else:
        print(f"KẾT LUẬN: FAIL ({total_errors} ERROR, {total_warnings} WARNING)")
    print("=" * 76)
    return total_errors, total_warnings


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm định Storyboard V5 cho DubStudio v3.2 + Google Flow."
    )
    parser.add_argument(
        "storyboard",
        type=Path,
        help="Đường dẫn tới file storyboard.json cần kiểm định.",
    )
    parser.add_argument(
        "--sync-files",
        action="store_true",
        help="Nếu PASS (0 ERROR), tự tạo/cập nhật assets/, raw_images/, elevenlabs.txt, timeline_script.txt, pipeline_state.json.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Nâng mọi WARNING thành ERROR.",
    )
    args = parser.parse_args(argv)

    storyboard_path = args.storyboard.resolve()
    reports, data, stats = validate_storyboard(storyboard_path)
    total_errors, _ = print_report(storyboard_path, reports, stats, strict=args.strict)

    if total_errors > 0:
        if args.sync_files:
            print("[SYNC] Bỏ qua --sync-files vì storyboard chưa PASS kiểm định.")
        return 1

    if args.sync_files and data is not None:
        sync_episode_files(storyboard_path, data)
        ep_dir = storyboard_path.parent
        print(f"[SYNC] Đã đồng bộ thành công các file sản xuất trong: {ep_dir}")
        print("       - assets/ & raw_images/")
        print("       - elevenlabs.txt")
        print("       - timeline_script.txt")
        print("       - pipeline_state.json (idempotent)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
