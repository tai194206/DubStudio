#!/usr/bin/env python3
"""Bot Playwright điều khiển Google Flow qua Chrome thật (CDP) cho Pipeline V5.

Giao diện CLI:
    python automation/scripts/flow_bot.py <path_to_storyboard.json> \
        --mode <probe|images|videos> \
        [--scene ID | --scenes 1,2,5] \
        [--limit N] [--dry-run] [--force] [--yes] [--cdp-url URL]

Nguyên tắc an toàn:
- Tiền kiểm storyboard bằng `validate_storyboard.py` trước khi chạy.
- HARD ASSERTION kiểm tra đúng mức tín dụng (`0 tín dụng` với Nano Banana Pro,
  `10 tín dụng` với Veo 3.1 - Lite chế độ Khung hình) trước mỗi lần bấm Tạo.
- Idempotent: tự bỏ qua cảnh đã có file đạt chuẩn dung lượng (trừ khi truyền --force).
- Ghi `pipeline_state.json` theo cơ chế atomic sau từng cảnh.
- Lột sạch âm thanh gốc video tải về bằng FFmpeg (`-c:v copy -an -movflags +faststart`).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

# Đảm bảo xuất tiếng Việt UTF-8 sạch trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[1]
DEBUG_DIR = REPO_ROOT / "automation" / "debug"
DEFAULT_SELECTORS_PATH = SCRIPTS_DIR / "flow_selectors.json"

for _p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.frame_presets import (  # noqa: E402
    DEFAULT_FRAME_PRESET,
    VALID_FRAME_PRESETS,
    FramePreset,
    get_frame_preset,
    resolve_frame_preset,
)
from validate_storyboard import (  # noqa: E402
    print_report,
    sync_episode_files,
    validate_storyboard,
)

MIN_IMAGE_BYTES = 10 * 1024       # 10 KB cho raw_images/{id:03d}.png
MIN_JPG_BYTES = 10 * 1024         # 10 KB cho assets/{id:03d}.jpg
MIN_VIDEO_BYTES = 100 * 1024      # 100 KB cho assets/{id:03d}.mp4

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class PolicyBlockedError(Exception):
    """Ném ra khi Google Flow từ chối cảnh vì vi phạm chính sách nội dung."""


class SafetyAssertionError(Exception):
    """Ném ra khi cấu hình UI/tín dụng trên Google Flow KHÔNG khớp luật an toàn."""


@dataclass
class ScenePlanItem:
    scene_id: int
    asset_type: str
    action: str           # RUN | CONVERT_JPG | SKIP | ERROR_MISSING_RAW
    reason: str
    estimated_credits: int
    scene_data: Dict[str, Any]


def _resolve_ffmpeg_binary() -> str:
    """Lấy đường dẫn FFmpeg qua core/ffmpeg_utils.py, fallback về 'ffmpeg'."""
    try:
        from core.ffmpeg_utils import get_ffmpeg

        return get_ffmpeg()
    except Exception:
        return "ffmpeg"


def _load_selectors_config(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file cấu hình selector: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_scene_ids(scene_arg: Optional[int], scenes_arg: Optional[str]) -> Optional[Set[int]]:
    selected: Set[int] = set()
    if scene_arg is not None:
        selected.add(scene_arg)
    if scenes_arg:
        for part in scenes_arg.split(","):
            tok = part.strip()
            if not tok:
                continue
            if "-" in tok:
                a_str, b_str = tok.split("-", 1)
                a, b = int(a_str.strip()), int(b_str.strip())
                if a > b:
                    a, b = b, a
                selected.update(range(a, b + 1))
            else:
                selected.add(int(tok))
    return selected if selected else None


def _load_pipeline_state_readonly(state_path: Path) -> Dict[int, Dict[str, Any]]:
    """Đọc trạng thái hiện có từ pipeline_state.json (không ghi file)."""
    if not state_path.is_file():
        return {}
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict) and isinstance(raw.get("scenes"), list):
            return {
                int(item["id"]): item
                for item in raw["scenes"]
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            }
    except Exception:
        pass
    return {}


def _load_full_pipeline_state(state_path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    """Đọc toàn bộ dict pipeline_state.json (sau khi đã sync_episode_files)."""
    if state_path.is_file():
        try:
            loaded = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict) and isinstance(loaded.get("scenes"), list):
                return loaded
        except Exception:
            pass
    return {
        "title": str(data.get("title", "")),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scenes": [],
    }


def _save_pipeline_state_atomic(state_path: Path, state: Dict[str, Any]) -> None:
    """Ghi pipeline_state.json bằng cơ chế atomic (ghi file tạm rồi os.replace)."""
    state["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    tmp_path = state_path.with_name(f"{state_path.name}.tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, state_path)


def _update_scene_state(
    state: Dict[str, Any],
    scene_id: int,
    *,
    image_status: Optional[str] = None,
    video_status: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    for item in state.get("scenes", []):
        if isinstance(item, dict) and item.get("id") == scene_id:
            if image_status is not None:
                item["image_status"] = image_status
            if video_status is not None:
                item["video_status"] = video_status
            if error is not None:
                item["error"] = error
            return


def _file_ok(path: Path, min_bytes: int) -> bool:
    return path.is_file() and path.stat().st_size > min_bytes


@dataclass(frozen=True)
class FlowAspectPlan:
    """Kế hoạch chọn tỉ lệ khung hình trên Google Flow và kích thước xuất assets/*.jpg."""

    frame_preset: str
    mode: str
    preferred: Tuple[str, ...]
    fallback: Tuple[str, ...]
    target_size: Tuple[int, int]
    fallback_prompt_cue: str = ""

    @property
    def all_candidates(self) -> Tuple[str, ...]:
        return self.preferred + self.fallback


def resolve_flow_aspect_plan(
    frame_preset: Union[str, FramePreset, None] = DEFAULT_FRAME_PRESET,
    *,
    mode: str = "images",
) -> FlowAspectPlan:
    """Ánh xạ frame_preset sang danh sách nút tỉ lệ trên Google Flow + kích thước xuất assets/*.jpg."""
    fp = get_frame_preset(frame_preset)
    if fp.name == "landscape_16_9":
        return FlowAspectPlan(
            frame_preset=fp.name,
            mode=mode,
            preferred=("16:9", "Ngang", "Landscape"),
            fallback=(),
            target_size=(fp.inner_w, fp.inner_h),
            fallback_prompt_cue="",
        )
    if fp.name == "framed_1_1":
        return FlowAspectPlan(
            frame_preset=fp.name,
            mode=mode,
            preferred=("1:1", "Vuông", "Square"),
            fallback=("16:9", "Ngang", "Landscape"),
            target_size=(fp.inner_w, fp.inner_h),
            fallback_prompt_cue="1:1 square archival framing, centered subject, edge-to-edge full-bleed, no white borders",
        )
    if fp.name == "framed_4_5":
        return FlowAspectPlan(
            frame_preset=fp.name,
            mode=mode,
            preferred=("4:5", "3:4"),
            fallback=("9:16", "Dọc", "Portrait"),
            target_size=(fp.inner_w, fp.inner_h),
            fallback_prompt_cue="4:5 vertical framing, balanced headroom, subject centered within 4:5 safe zone, edge-to-edge full-bleed, no white borders",
        )
    return FlowAspectPlan(
        frame_preset="portrait_9_16",
        mode=mode,
        preferred=("9:16", "Dọc", "Portrait"),
        fallback=(),
        target_size=(fp.inner_w, fp.inner_h),
        fallback_prompt_cue="",
    )


def _augment_prompt_for_flow_aspect(
    prompt: str,
    aspect_plan: FlowAspectPlan,
    selected_ui_aspect: str,
) -> str:
    """Khi Flow fallback về khung 9:16/16:9 cho preset 4:5 hoặc 1:1, chèn cue khóa bố cục trung tâm."""
    cue = (aspect_plan.fallback_prompt_cue or "").strip()
    if not cue or not selected_ui_aspect or selected_ui_aspect not in aspect_plan.fallback:
        return prompt
    low = prompt.lower()
    if "safe zone" in low or "centered subject" in low or "4:5 vertical framing" in low or "1:1 square" in low:
        return prompt
    m_notext = re.search(r",?\s*\bno\s+text\b", prompt, flags=re.IGNORECASE)
    if m_notext:
        head = prompt[: m_notext.start()].rstrip(" ,")
        tail = prompt[m_notext.start() :].lstrip(" ,")
        return f"{head}, {cue}, {tail}" if head else f"{cue}, {tail}"
    return f"{prompt.rstrip(' ,')}, {cue}" if prompt.strip() else cue


def _convert_image_to_png(src_path: Path, dst_png: Path) -> None:
    """Đảm bảo ảnh gốc lưu tại raw_images/{id:03d}.png đúng định dạng PNG (giữ nguyên tỉ lệ gốc)."""
    from PIL import Image, ImageOps

    dst_png.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as img:
        ImageOps.exif_transpose(img).convert("RGB").save(dst_png, format="PNG")


def _convert_png_to_jpg(
    raw_png: Path,
    dst_jpg: Path,
    *,
    frame_preset: Union[str, FramePreset] = DEFAULT_FRAME_PRESET,
) -> Tuple[int, int]:
    """Chuyển raw PNG sang RGB JPG đúng kích thước vùng ảnh hoạt động của frame_preset (không kéo méo)."""
    from PIL import Image, ImageOps

    fp = get_frame_preset(frame_preset)
    target_size = (fp.inner_w, fp.inner_h)
    centering = (0.5, 0.45) if fp.name in ("framed_4_5", "portrait_9_16") else (0.5, 0.5)

    dst_jpg.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(raw_png) as img:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        fitted = ImageOps.fit(rgb, target_size, method=Image.Resampling.LANCZOS, centering=centering)
        fitted.save(dst_jpg, format="JPEG", quality=95, subsampling=0)
        return fitted.size


def _strip_audio_ffmpeg(tmp_mp4: Path, dst_mp4: Path) -> None:
    """Lột sạch âm thanh gốc từ clip Veo bằng FFmpeg và tối ưu faststart."""
    ffmpeg_bin = _resolve_ffmpeg_binary()
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", str(tmp_mp4),
        "-c:v", "copy",
        "-an",
        "-movflags", "+faststart",
        str(dst_mp4),
    ]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg lột âm thanh thất bại:\n{res.stderr.strip()[-800:]}")
    if not _file_ok(dst_mp4, MIN_VIDEO_BYTES):
        sz = dst_mp4.stat().st_size if dst_mp4.is_file() else 0
        raise RuntimeError(
            f"File video đích quá nhỏ sau khi xử lý FFmpeg ({dst_mp4.name}: {sz} bytes <= {MIN_VIDEO_BYTES} bytes)."
        )


def _resolve_target_asset_filename(
    data: Dict[str, Any],
    scene_idx: int,
    sc: Dict[str, Any],
) -> str:
    """Đọc tên file đích chính xác của cảnh từ storyboard.json (beats -> locations)."""
    sid = int(sc["id"])
    asset_type = str(sc.get("asset_type", "static_image"))
    fallback = f"{sid:03d}.mp4" if asset_type == "flow_video" else f"{sid:03d}.jpg"

    beats = data.get("beats") if isinstance(data.get("beats"), list) else []
    locations = data.get("locations") if isinstance(data.get("locations"), dict) else {}

    loc_name = ""
    if scene_idx < len(beats) and isinstance(beats[scene_idx], dict):
        loc_name = str(beats[scene_idx].get("location") or "").strip()
    if not loc_name:
        for bt in beats:
            if isinstance(bt, dict) and bt.get("id") == sid:
                loc_name = str(bt.get("location") or "").strip()
                break

    if loc_name and loc_name in locations:
        val = locations[loc_name]
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for k in ("file", "asset", "path", "filename", "image", "video"):
                v = val.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    return fallback


def _build_execution_plan(
    ep_dir: Path,
    data: Dict[str, Any],
    mode: str,
    *,
    selected_ids: Optional[Set[int]],
    limit: Optional[int],
    force: bool,
) -> List[ScenePlanItem]:
    """Lập kế hoạch chi tiết cho từng cảnh (dùng chung cho --dry-run và chạy thật)."""
    raw_dir = ep_dir / "raw_images"
    assets_dir = ep_dir / "assets"
    state_by_id = _load_pipeline_state_readonly(ep_dir / "pipeline_state.json")
    scenes: List[Dict[str, Any]] = data.get("scenes", [])

    plan: List[ScenePlanItem] = []
    run_counter = 0

    for idx, sc in enumerate(scenes):
        sid = int(sc["id"])
        if selected_ids is not None and sid not in selected_ids:
            continue

        asset_type = str(sc.get("asset_type", "static_image"))
        target_filename = _resolve_target_asset_filename(data, idx, sc)
        target_asset = assets_dir / target_filename
        raw_png = raw_dir / f"{sid:03d}.png"
        dst_jpg = assets_dir / f"{sid:03d}.jpg"
        st_rec = state_by_id.get(sid, {})

        # Chốt bảo vệ thành phẩm có sẵn trong assets/ (trừ khi truyền --force)
        if not force:
            if asset_type == "flow_video" and _file_ok(target_asset, MIN_VIDEO_BYTES):
                sz_mb = target_asset.stat().st_size / (1024 * 1024)
                plan.append(
                    ScenePlanItem(
                        scene_id=sid,
                        asset_type=asset_type,
                        action="SKIP_EXISTING_ASSET",
                        reason=f"Đã có thành phẩm assets/{target_filename} ({sz_mb:.2f} MB) → bảo vệ, không gen đè",
                        estimated_credits=0,
                        scene_data=sc,
                    )
                )
                continue
            if asset_type == "static_image" and mode == "images" and _file_ok(target_asset, MIN_JPG_BYTES):
                sz_kb = target_asset.stat().st_size / 1024
                plan.append(
                    ScenePlanItem(
                        scene_id=sid,
                        asset_type=asset_type,
                        action="SKIP_EXISTING_ASSET",
                        reason=f"Đã có thành phẩm assets/{target_filename} ({sz_kb:.1f} KB) → bảo vệ, không gen đè",
                        estimated_credits=0,
                        scene_data=sc,
                    )
                )
                continue

        if mode == "images":
            has_raw = _file_ok(raw_png, MIN_IMAGE_BYTES)
            has_jpg = _file_ok(dst_jpg, MIN_JPG_BYTES)
            if has_raw and not force:
                if asset_type == "static_image" and not has_jpg:
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="CONVERT_JPG",
                            reason="Đã có PNG gốc (>10KB) → chỉ convert sang assets/*.jpg",
                            estimated_credits=0,
                            scene_data=sc,
                        )
                    )
                else:
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="SKIP",
                            reason="SKIP (đã có file ảnh gốc >10KB)",
                            estimated_credits=0,
                            scene_data=sc,
                        )
                    )
            else:
                if limit is not None and run_counter >= limit:
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="SKIP",
                            reason=f"SKIP (vượt giới hạn --limit {limit})",
                            estimated_credits=0,
                            scene_data=sc,
                        )
                    )
                else:
                    run_counter += 1
                    extra = " + xuất JPG" if asset_type == "static_image" else ""
                    prev_flag = f" [state cũ: {st_rec.get('image_status')}]" if st_rec.get("image_status") else ""
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="RUN",
                            reason=f"Gen ảnh Nano Banana Pro{extra}{prev_flag}",
                            estimated_credits=0,
                            scene_data=sc,
                        )
                    )

        elif mode == "videos":
            if asset_type == "static_image":
                plan.append(
                    ScenePlanItem(
                        scene_id=sid,
                        asset_type=asset_type,
                        action="SKIP",
                        reason="SKIP (static_image không tạo video)",
                        estimated_credits=0,
                        scene_data=sc,
                    )
                )
            elif not _file_ok(raw_png, MIN_IMAGE_BYTES):
                plan.append(
                    ScenePlanItem(
                        scene_id=sid,
                        asset_type=asset_type,
                        action="ERROR_MISSING_RAW",
                        reason=f"THIẾU ảnh gốc raw_images/{sid:03d}.png (>10KB) → bỏ qua, không gen mù",
                        estimated_credits=0,
                        scene_data=sc,
                    )
                )
            else:
                if limit is not None and run_counter >= limit:
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="SKIP",
                            reason=f"SKIP (vượt giới hạn --limit {limit})",
                            estimated_credits=0,
                            scene_data=sc,
                        )
                    )
                else:
                    run_counter += 1
                    prev_flag = f" [state cũ: {st_rec.get('video_status')}]" if st_rec.get("video_status") else ""
                    plan.append(
                        ScenePlanItem(
                            scene_id=sid,
                            asset_type=asset_type,
                            action="RUN",
                            reason=f"Gen video Veo 3.1 - Lite (Khung hình) + FFmpeg mute{prev_flag}",
                            estimated_credits=10,
                            scene_data=sc,
                        )
                    )

    return plan


def _print_plan_table(
    storyboard_path: Path,
    mode: str,
    plan: List[ScenePlanItem],
    *,
    dry_run: bool,
    frame_preset: Union[str, FramePreset] = DEFAULT_FRAME_PRESET,
) -> Tuple[int, int]:
    """In bảng kế hoạch chạy và trả về (run_count, total_credits)."""
    fp = get_frame_preset(frame_preset)
    aspect_plan = resolve_flow_aspect_plan(fp, mode=mode)
    tag = " [DRY-RUN — KHÔNG MỞ CHROME, KHÔNG TỐN TÍN DỤNG]" if dry_run else ""
    print("\n" + "=" * 96)
    print(f"KẾ HOẠCH FLOW BOT | MODE: {mode.upper()}{tag}")
    print(f"Tập phim: {storyboard_path.parent}")
    print(
        f"Khung hình: {fp.name} (Flow ưu tiên {aspect_plan.preferred[0]} | "
        f"assets/*.jpg: {fp.inner_w}x{fp.inner_h} → xuất {fp.out_w}x{fp.out_h})"
    )
    print("=" * 96)
    print(f"{'ID':>4} | {'Loại cảnh':<13} | {'Hành động':<19} | {'Tín dụng':>8} | Lý do / Chi tiết")
    print("-" * 96)

    counts = {
        "RUN": 0,
        "CONVERT_JPG": 0,
        "SKIP_EXISTING_ASSET": 0,
        "SKIP": 0,
        "ERROR_MISSING_RAW": 0,
    }
    total_credits = 0
    for item in plan:
        counts[item.action] = counts.get(item.action, 0) + 1
        total_credits += item.estimated_credits
        print(
            f"{item.scene_id:>4} | {item.asset_type:<13} | {item.action:<19} | "
            f"{item.estimated_credits:>8} | {item.reason}"
        )

    print("-" * 96)
    print(
        f"TỔNG KẾT: {len(plan)} cảnh xét duyệt | "
        f"RUN: {counts['RUN']} | CONVERT_JPG: {counts['CONVERT_JPG']} | "
        f"SKIP_EXISTING_ASSET: {counts['SKIP_EXISTING_ASSET']} | "
        f"SKIP: {counts['SKIP']} | THIẾU ẢNH GỐC: {counts['ERROR_MISSING_RAW']}"
    )
    print(f"TỔNG TÍN DỤNG DỰ KIẾN TIÊU THỤ: {total_credits} tín dụng")
    print("=" * 96)
    return counts["RUN"], total_credits


# =============================================================================
# TƯƠNG TÁC CHROME CDP & GOOGLE FLOW (PLAYWRIGHT)
# =============================================================================

def _get_flow_url_keywords(cfg: Dict[str, Any]) -> List[str]:
    kws = cfg.get("flow_url_keywords")
    if isinstance(kws, list) and kws:
        return [str(k) for k in kws if str(k).strip()]
    single = cfg.get("flow_url_keyword")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    return ["flow.google.com", "labs.google"]


def _connect_flow_page(playwright: Any, cdp_url: str, url_keywords: Any):
    """Kết nối vào Chrome thật qua CDP và tìm đúng tab đang mở Google Flow."""
    if isinstance(url_keywords, str):
        keywords = [url_keywords]
    else:
        keywords = [str(k) for k in (url_keywords or ["flow.google.com", "labs.google"])]

    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        raise RuntimeError(
            f"Không thể kết nối Chrome CDP tại {cdp_url}.\n"
            f"Hãy chạy `automation\\scripts\\launch_chrome_cdp.bat` trước và giữ nguyên cửa sổ Chrome!\n"
            f"Chi tiết lỗi: {e}"
        ) from e

    all_pages = []
    for ctx in browser.contexts:
        for pg in ctx.pages:
            all_pages.append(pg)

    for pg in all_pages:
        pg_url_low = (pg.url or "").lower()
        if any(kw.lower() in pg_url_low for kw in keywords):
            pg.bring_to_front()
            return browser, pg

    urls = [pg.url for pg in all_pages]
    raise RuntimeError(
        f"Đã kết nối Chrome CDP ({cdp_url}) nhưng không tìm thấy tab nào khớp {keywords}.\n"
        f"Các tab đang mở: {urls}\n"
        f"Hãy mở https://flow.google.com (hoặc https://labs.google/fx/tools/flow) và mở sẵn dự án Flow cần chạy."
    )


def _first_visible_locator(page: Any, selectors: Sequence[str], timeout_ms: int = 1500):
    """Trả về locator đầu tiên hiển thị được trong danh sách fallback selector."""
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()
            for idx in range(count):
                cand = loc.nth(idx)
                if cand.is_visible():
                    return cand, sel
        except Exception:
            continue
    # Thử chờ ngắn cho selector đầu tiên nếu chưa kịp render
    for sel in selectors:
        try:
            cand = page.locator(sel).first
            cand.wait_for(state="visible", timeout=timeout_ms)
            return cand, sel
        except Exception:
            continue
    return None, None


def _click_by_label(page: Any, label: str, *, exact: bool = False, timeout_ms: int = 4000) -> bool:
    """Tìm và click nút/tab/menuitem/option theo nhãn tiếng Việt trên Google Flow."""
    candidates = [
        page.get_by_role("tab", name=label, exact=exact),
        page.get_by_role("button", name=label, exact=exact),
        page.get_by_role("menuitem", name=label, exact=exact),
        page.get_by_role("option", name=label, exact=exact),
        page.get_by_role("radio", name=label, exact=exact),
        page.get_by_text(label, exact=exact),
    ]
    for loc in candidates:
        try:
            cnt = loc.count()
            for idx in range(cnt):
                el = loc.nth(idx)
                if el.is_visible():
                    el.click(timeout=timeout_ms)
                    time.sleep(0.4)
                    return True
        except Exception:
            continue
    return False


def _save_debug_screenshot(page: Any, filename: str) -> Path:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    out = DEBUG_DIR / filename
    try:
        page.screenshot(path=str(out), full_page=False)
    except Exception:
        pass
    return out


def _get_visible_body_text(page: Any) -> str:
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def _extract_credit_mentions(body_text: str) -> List[str]:
    """Trích xuất tất cả cụm '<số> tín dụng' hoặc '<số> credits' đang hiển thị trên trang."""
    return re.findall(r"\b\d+\s*(?:tín dụng|credits?)\b", body_text, flags=re.IGNORECASE)


def _assert_expected_credits(
    page: Any,
    expected_credit_text: str,
    *,
    forbidden_credit_numbers: Sequence[int],
    scene_id: int,
    mode_tag: str,
) -> None:
    """HARD ASSERTION: Bắt buộc xác nhận đúng số tín dụng (khi bảng cài đặt đang mở) trước khi bấm Tạo."""
    body_text = _get_visible_body_text(page)
    mentions = _extract_credit_mentions(body_text)
    lower_body = body_text.lower()
    exp_lower = expected_credit_text.lower()

    if exp_lower not in lower_body:
        shot = _save_debug_screenshot(page, f"assert_credit_fail_{mode_tag}_{scene_id:03d}.png")
        raise SafetyAssertionError(
            f"[HARD ASSERTION THẤT BẠI] Không tìm thấy chữ '{expected_credit_text}' trên bảng cài đặt Google Flow "
            f"trước khi tạo Cảnh {scene_id} ({mode_tag}).\n"
            f"Các mức tín dụng tìm thấy trên trang: {mentions}\n"
            f"Ảnh chụp debug: {shot}"
        )

    m_exp = re.search(r"\d+", expected_credit_text)
    exp_num = int(m_exp.group(0)) if m_exp else None
    found_nums = [int(m) for m in re.findall(r"(\d+)\s*(?:tín dụng|credits?)", body_text, flags=re.IGNORECASE)]
    unexpected = [n for n in found_nums if (exp_num is not None and n != exp_num) or (n in forbidden_credit_numbers)]
    if unexpected:
        shot = _save_debug_screenshot(page, f"assert_forbidden_credit_{mode_tag}_{scene_id:03d}.png")
        raise SafetyAssertionError(
            f"[HARD ASSERTION THẤT BẠI] Phát hiện mức tín dụng khác chuẩn ({unexpected} tín dụng, yêu cầu '{expected_credit_text}') "
            f"trước khi tạo Cảnh {scene_id} ({mode_tag})! Đã DỪNG KHẨN CẤP để bảo vệ tín dụng.\n"
            f"Các mức tín dụng tìm thấy: {mentions}\n"
            f"Ảnh chụp debug: {shot}"
        )


def _detect_policy_error(body_text: str, baseline_text: str, keywords: Sequence[str]) -> Optional[str]:
    """Phát hiện thông báo lỗi Policy mới xuất hiện trên trang."""
    base_lower = baseline_text.lower()
    cur_lower = body_text.lower()
    for kw in keywords:
        kw_low = kw.lower()
        if kw_low in cur_lower and cur_lower.count(kw_low) > base_lower.count(kw_low):
            # Trích câu chứa từ khóa để lưu log rõ ràng
            for line in body_text.splitlines():
                if kw_low in line.lower():
                    return line.strip()[:300]
            return f"Phát hiện từ khóa lỗi policy: '{kw}'"
    return None


def _fill_prompt(page: Any, selectors: Sequence[str], prompt_text: str) -> None:
    el, matched = _first_visible_locator(page, selectors, timeout_ms=4000)
    if el is None:
        raise RuntimeError(
            f"Không tìm thấy ô nhập prompt trên Google Flow (đã thử: {list(selectors)}). "
            f"Hãy chạy `--mode probe` để cập nhật `flow_selectors.json`."
        )
    tag = (el.evaluate("e => e.tagName.toLowerCase()") or "").lower()
    el.click()
    time.sleep(0.2)
    if tag in ("textarea", "input"):
        el.fill("")
        el.fill(prompt_text)
    else:
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.insert_text(prompt_text)
    time.sleep(0.3)


def _ensure_settings_open(page: Any, cfg: Dict[str, Any]) -> None:
    """Mở bảng cài đặt (Điều kiện kích hoạt cài đặt) nếu chưa mở."""
    sels = cfg["selectors"]
    model_sels = sels.get("model_dropdown", ["button[aria-label='Chọn nhóm mô hình']"])
    mbtn, _ = _first_visible_locator(page, model_sels, timeout_ms=400)
    if mbtn is not None:
        return
    # Đóng các modal/overlay cũ còn kẹt từ bước trước nếu có
    if page.locator(".cdk-overlay-backdrop-showing, .cdk-overlay-container button[aria-label='Đóng']").count() > 0:
        page.keyboard.press("Escape")
        time.sleep(0.4)
        page.keyboard.press("Escape")
        time.sleep(0.3)
    trig, _ = _first_visible_locator(page, sels.get("settings_trigger", []), timeout_ms=3000)
    if trig is None:
        raise RuntimeError("Không tìm thấy nút 'Điều kiện kích hoạt cài đặt' trên Google Flow.")
    trig.click(timeout=4000)
    time.sleep(0.6)


def _close_settings_popover(page: Any, cfg: Dict[str, Any]) -> None:
    """Đóng bảng cài đặt gọn gàng bằng Escape nếu đang mở."""
    sels = cfg["selectors"]
    model_sels = sels.get("model_dropdown", ["button[aria-label='Chọn nhóm mô hình']"])
    mbtn, _ = _first_visible_locator(page, model_sels, timeout_ms=300)
    if mbtn is not None:
        page.keyboard.press("Escape")
        time.sleep(0.35)


def _click_radio_option(page: Any, label: str) -> bool:
    """Click nút button[role='radio'] khớp nhãn (ưu tiên khớp chính xác cho x1/x2/x3/x4)."""
    radios = page.locator("button[role='radio']")
    cnt = radios.count()
    exact_short = label.strip().lower() in ("x1", "x2", "x3", "x4")
    for idx in range(cnt):
        el = radios.nth(idx)
        try:
            if not el.is_visible():
                continue
            txt = (el.inner_text() or "").strip()
            matched = (txt.lower() == label.strip().lower()) if exact_short else (label.lower() in txt.lower())
            if matched:
                el.click(timeout=4000)
                time.sleep(0.35)
                return True
        except Exception:
            continue
    return _click_by_label(page, label, exact=exact_short)


def _select_model_from_dropdown(
    page: Any,
    cfg: Dict[str, Any],
    model_label: str,
    scene_id: int,
    mode_tag: str,
) -> None:
    """Mở dropdown 'Chọn nhóm mô hình' và chọn đúng model yêu cầu."""
    sels = cfg["selectors"]
    model_sels = sels.get("model_dropdown", ["button[aria-label='Chọn nhóm mô hình']"])
    mbtn, _ = _first_visible_locator(page, model_sels, timeout_ms=3000)
    if mbtn is None:
        shot = _save_debug_screenshot(page, f"assert_model_btn_missing_{mode_tag}_{scene_id:03d}.png")
        raise SafetyAssertionError(
            f"[HARD ASSERTION THẤT BẠI] Không thấy nút 'Chọn nhóm mô hình' (Cảnh {scene_id}). Ảnh debug: {shot}"
        )

    cur_txt = (mbtn.inner_text() or "").strip()
    if model_label.lower() not in cur_txt.lower():
        mbtn.click(timeout=4000)
        time.sleep(0.5)
        picked = False
        for role_sel in ("[role='menuitem']", "[role='option']", "[role='menuitemradio']"):
            loc = page.locator(role_sel)
            for idx in range(loc.count()):
                cand = loc.nth(idx)
                try:
                    if cand.is_visible() and model_label.lower() in (cand.inner_text() or "").lower():
                        cand.click(timeout=4000)
                        time.sleep(0.45)
                        picked = True
                        break
                except Exception:
                    continue
            if picked:
                break
        if not picked:
            shot = _save_debug_screenshot(page, f"assert_model_pick_fail_{mode_tag}_{scene_id:03d}.png")
            raise SafetyAssertionError(
                f"[HARD ASSERTION THẤT BẠI] Không chọn được model '{model_label}' trong menu mô hình "
                f"(Cảnh {scene_id}). Ảnh debug: {shot}"
            )

    # Xác nhận lại nhãn model sau khi chọn
    mbtn_after, _ = _first_visible_locator(page, model_sels, timeout_ms=2000)
    after_txt = (mbtn_after.inner_text() or "").strip() if mbtn_after is not None else ""
    if model_label.lower() not in after_txt.lower():
        shot = _save_debug_screenshot(page, f"assert_model_verify_fail_{mode_tag}_{scene_id:03d}.png")
        raise SafetyAssertionError(
            f"[HARD ASSERTION THẤT BẠI] Model hiện tại là '{after_txt}', không khớp '{model_label}' "
            f"(Cảnh {scene_id}). Ảnh debug: {shot}"
        )


def _assert_settings_pill_summary(
    page: Any,
    cfg: Dict[str, Any],
    required_substrings: Sequence[str],
    scene_id: int,
    mode_tag: str,
) -> None:
    """Kiểm tra nhanh nút tóm tắt cấu hình ngay trước khi bấm Tạo (khi popover đã đóng)."""
    trig, _ = _first_visible_locator(page, cfg["selectors"].get("settings_trigger", []), timeout_ms=2000)
    pill_text = (trig.inner_text() or "").strip() if trig is not None else ""
    pill_low = pill_text.lower()
    missing = [s for s in required_substrings if s.lower() not in pill_low]
    if missing:
        shot = _save_debug_screenshot(page, f"assert_pill_fail_{mode_tag}_{scene_id:03d}.png")
        raise SafetyAssertionError(
            f"[HARD ASSERTION THẤT BẠI] Nút cấu hình trước khi bấm Tạo là '{pill_text}', thiếu {missing} "
            f"(Cảnh {scene_id}, {mode_tag})! Ảnh debug: {shot}"
        )


def _select_aspect_ratio_option(
    page: Any,
    aspect_plan: FlowAspectPlan,
    *,
    fallback_label: str = "",
) -> str:
    """Chọn tỉ lệ khung hình trên bảng cài đặt Google Flow theo thứ tự ưu tiên của FlowAspectPlan.

    Chỉ tìm và bấm các control lựa chọn thật (radio/option/menuitemradio/tab),
    tuyệt đối không fallback sang get_by_text để tránh click nhầm vào ô prompt.
    """
    option_selectors = (
        "button[role='radio']",
        "[role='option']",
        "[role='menuitemradio']",
        "[role='tab']",
    )

    def _try_pick(label_cand: str) -> bool:
        target = label_cand.strip().lower()
        if not target:
            return False
        for sel in option_selectors:
            try:
                loc = page.locator(sel)
                cnt = loc.count()
            except Exception:
                continue
            for idx in range(cnt):
                el = loc.nth(idx)
                try:
                    if not el.is_visible():
                        continue
                    txt = (el.inner_text() or "").strip().lower()
                    aria = (el.get_attribute("aria-label") or "").strip().lower()
                    if target in txt or target in aria:
                        el.click(timeout=4000)
                        time.sleep(0.35)
                        return True
                except Exception:
                    continue
        return False

    for cand in aspect_plan.all_candidates:
        if _try_pick(cand):
            if cand in aspect_plan.fallback:
                print(
                    f"  [ASPECT] {aspect_plan.frame_preset}: UI Flow không có '{aspect_plan.preferred[0]}' "
                    f"→ chọn fallback '{cand}' kèm khóa bố cục trung tâm.",
                    flush=True,
                )
            else:
                print(
                    f"  [ASPECT] {aspect_plan.frame_preset}: Đã chọn tỉ lệ '{cand}' trên Google Flow.",
                    flush=True,
                )
            return cand

    if fallback_label and _try_pick(fallback_label):
        print(
            f"  [ASPECT] {aspect_plan.frame_preset}: Fallback mặc định sang '{fallback_label}'.",
            flush=True,
        )
        return fallback_label

    print(
        f"  [WARN] Không tìm thấy nút chọn tỉ lệ {list(aspect_plan.all_candidates)} trên UI Flow — giữ nguyên cài đặt hiện tại.",
        flush=True,
    )
    return ""


def _configure_image_mode(
    page: Any,
    cfg: Dict[str, Any],
    scene_id: int,
    *,
    aspect_plan: Optional[FlowAspectPlan] = None,
) -> str:
    im = cfg["image_mode"]
    eff_plan = aspect_plan or resolve_flow_aspect_plan(DEFAULT_FRAME_PRESET, mode="images")
    _ensure_settings_open(page, cfg)
    _click_radio_option(page, im["tab_label"])
    selected_aspect = _select_aspect_ratio_option(
        page, eff_plan, fallback_label=im.get("aspect_label", "9:16")
    )
    _select_model_from_dropdown(page, cfg, im["model_label"], scene_id, "images")
    _click_radio_option(page, im["batch_label"])
    time.sleep(0.3)

    # HARD ASSERTION KHI BẢNG CÀI ĐẶT ĐANG MỞ: Phải thấy đúng "0 tín dụng"
    _assert_expected_credits(
        page,
        im["expected_credit_text"],
        forbidden_credit_numbers=(10, 20, 30, 40, 48, 50, 100),
        scene_id=scene_id,
        mode_tag="images",
    )
    _close_settings_popover(page, cfg)
    return selected_aspect


def _configure_video_mode(
    page: Any,
    cfg: Dict[str, Any],
    scene_id: int,
    *,
    aspect_plan: Optional[FlowAspectPlan] = None,
) -> str:
    vm = cfg["video_mode"]
    eff_plan = aspect_plan or resolve_flow_aspect_plan(DEFAULT_FRAME_PRESET, mode="videos")
    _ensure_settings_open(page, cfg)
    _click_radio_option(page, vm["tab_label"])
    _click_radio_option(page, vm["submode_label"])
    selected_aspect = _select_aspect_ratio_option(
        page, eff_plan, fallback_label=vm.get("aspect_label", "9:16")
    )
    _select_model_from_dropdown(page, cfg, vm["model_label"], scene_id, "videos")
    _click_radio_option(page, vm["batch_label"])
    time.sleep(0.3)

    # HARD ASSERTION KHI BẢNG CÀI ĐẶT ĐANG MỞ: Phải đang ở 'Khung hình' (KHÔNG phải 'Thành phần')
    try:
        ing_checked = page.evaluate(
            """() => {
                const els = Array.from(document.querySelectorAll('button[role="radio"][aria-checked="true"], [aria-selected="true"], [data-state="on"], [data-state="active"]'));
                return els.some(e => (e.innerText || '').toLowerCase().includes('thành phần'));
            }"""
        )
        if ing_checked:
            shot = _save_debug_screenshot(page, f"assert_thanhphan_fail_{scene_id:03d}.png")
            raise SafetyAssertionError(
                f"[HARD ASSERTION THẤT BẠI] Giao diện đang chọn nhầm chế độ 'Thành phần' thay vì 'Khung hình' "
                f"(Cảnh {scene_id})! Ảnh debug: {shot}"
            )
    except SafetyAssertionError:
        raise
    except Exception:
        pass

    # HARD ASSERTION KHI BẢNG CÀI ĐẶT ĐANG MỞ: Phải thấy đúng "10 tín dụng" (cấm 20/30/40/48...)
    _assert_expected_credits(
        page,
        vm["expected_credit_text"],
        forbidden_credit_numbers=(20, 24, 30, 40, 48, 50, 80, 100),
        scene_id=scene_id,
        mode_tag="videos",
    )
    _close_settings_popover(page, cfg)
    return selected_aspect


def _norm_nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").lower()


def _scroll_grid_to_top(page: Any) -> None:
    """Cuộn vùng lưới chính của Google Flow về đầu trang (scrollTop = 0)."""
    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('*').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 50 && el.getBoundingClientRect().x >= 180) {
                        el.scrollTop = 0;
                    }
                });
            }"""
        )
        time.sleep(0.3)
    except Exception:
        pass


def _click_sidebar_tab(page: Any, label: str, *, force: bool = False) -> None:
    """Chuyển tab cột trái ('Hình ảnh' / 'Video') khi chưa active; nếu đã active thì chỉ cuộn đầu trang."""
    try:
        switched = page.evaluate(
            """([lbl, forceClick]) => {
                const btns = Array.from(document.querySelectorAll('button'));
                const b = btns.find(x => x.getBoundingClientRect().x < 220 && (x.innerText || '').includes(lbl));
                if (!b) return false;
                if (!forceClick) {
                    const cls = (b.className || '').toLowerCase();
                    const ariaSel = (b.getAttribute('aria-selected') || '').toLowerCase();
                    const ariaCur = (b.getAttribute('aria-current') || '').toLowerCase();
                    const ariaPre = (b.getAttribute('aria-pressed') || '').toLowerCase();
                    const dataSt = (b.getAttribute('data-state') || '').toLowerCase();
                    const bg = window.getComputedStyle(b).backgroundColor || '';
                    const isAlreadyActive = (
                        ariaSel === 'true' ||
                        ariaCur === 'page' || ariaCur === 'true' ||
                        ariaPre === 'true' ||
                        dataSt === 'active' || dataSt === 'on' ||
                        cls.includes('active') || cls.includes('selected') ||
                        (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent')
                    );
                    if (isAlreadyActive) return false;
                }
                b.click();
                return true;
            }""",
            [label, bool(force)],
        )
        if switched:
            time.sleep(0.9)
        _scroll_grid_to_top(page)
        page.mouse.move(10, 10)
        time.sleep(0.15)
    except Exception:
        pass


_FLOW_STOPWORDS = frozenset({
    "the", "and", "with", "from", "into", "over", "slow", "steady", "gentle",
    "through", "under", "near", "above", "below", "along", "across", "around",
    "toward", "towards", "against", "beside", "between", "within", "beneath",
    "for", "are", "was", "were", "that", "this", "its", "their", "while",
})

_QUEUE_LIMIT_KEYWORDS = (
    "giới hạn",
    "quá nhiều",
    "đang xử lý tối đa",
    "too many",
    "queue full",
    "concurrent",
    "limit reached",
    "rate limit",
)


def _norm_tokens(text: str) -> List[str]:
    low = unicodedata.normalize("NFC", text or "").lower()
    # Chuẩn hóa ký tự ba chấm gắn liền chữ thành dấu hiệu riêng ở token
    low = low.replace("...", "…")
    raw = re.findall(r"[a-z0-9]{2,}…?", low)
    return [t for t in raw if t.rstrip("…") not in _FLOW_STOPWORDS]


def _token_match_weight(a_tok: str, p_tokens: Sequence[str]) -> float:
    is_trunc = a_tok.endswith("…")
    clean_a = a_tok.rstrip("…")
    if len(clean_a) < 2:
        return 0.0
    if is_trunc and len(clean_a) >= 3:
        for pt in p_tokens:
            clean_p = pt.rstrip("…")
            if clean_p.startswith(clean_a):
                return 0.85
        return 0.0
    for pt in p_tokens:
        clean_p = pt.rstrip("…")
        if clean_a == clean_p:
            return 1.0
        if len(clean_a) >= 4 and len(clean_p) >= 4 and clean_a[:4] == clean_p[:4]:
            return 0.85
    return 0.0


def _text_overlap_score(aria_label: str, prompt_text: str) -> float:
    """Tính điểm khớp ngữ nghĩa [0.0, 1.0] giữa aria-label ngắn trên Google Flow và prompt đầy đủ."""
    if not aria_label or not prompt_text:
        return 0.0
    a_tokens = _norm_tokens(aria_label)
    if not a_tokens:
        # Fallback giữ lại mọi token chữ cái >= 2 ký tự nếu aria-label toàn từ nối
        a_tokens = re.findall(r"[a-z0-9]{2,}…?", (aria_label or "").lower())
    if not a_tokens:
        return 0.0
    p_tokens = _norm_tokens(prompt_text)
    if not p_tokens:
        p_tokens = re.findall(r"[a-z0-9]{2,}", (prompt_text or "").lower())
    if not p_tokens:
        return 0.0

    total_w = 0.0
    for a_tok in a_tokens:
        total_w += _token_match_weight(a_tok, p_tokens)
    return min(1.0, total_w / float(len(a_tokens)))


def _scene_match_prompt(scene_entry: Dict[str, Any]) -> str:
    parts = [
        str(scene_entry.get("prompt") or "").strip(),
        str(scene_entry.get("image_prompt") or "").strip(),
        str(scene_entry.get("motion_prompt") or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def _match_tiles_to_batch_scenes(
    submitted_scenes: Sequence[Dict[str, Any]],
    candidate_tiles: Sequence[Dict[str, Any]],
) -> Tuple[Dict[int, Dict[str, Any]], List[int]]:
    """Khớp 1-1 danh sách cảnh đã nộp với các thẻ mới quét được trên lưới Google Flow.

    3 tầng khớp an toàn (không bao giờ gán nhầm khi số thẻ bị thiếu):
      - Tầng 1: Khớp trực tiếp theo `data-flowbot-scene-id` (`scene_tag`) hoặc `uid`.
      - Tầng 2a (đủ thẻ `rem_tiles == rem_scenes`): ưu tiên thứ tự ngược chuẩn Flow `[mới nhất -> cũ nhất]`,
        chỉ đảo nếu tổ hợp tối ưu điểm ngữ nghĩa `_text_overlap_score` vượt trội rõ rệt (>= +0.15).
      - Tầng 2b (thiếu thẻ do Flow xóa Policy `rem_tiles < rem_scenes` hoặc dư thẻ):
        CHỈ gán các cặp có điểm tương đồng ngữ nghĩa rõ ràng (score >= 0.25 và hơn nhì >= 0.10);
        cảnh không khớp trả về trong `unmatched_ids`.
    """
    matched: Dict[int, Dict[str, Any]] = {}
    used_tile_indices: Set[int] = set()

    # Đếm tần suất scene_tag để chỉ tin tưởng các tag duy nhất không bị trùng
    tag_counts: Dict[int, int] = {}
    for t in candidate_tiles:
        st = t.get("scene_tag")
        if isinstance(st, int):
            tag_counts[st] = tag_counts.get(st, 0) + 1

    submitted_by_id: Dict[int, Dict[str, Any]] = {int(sc["id"]): dict(sc) for sc in submitted_scenes}

    # Tầng 1: Direct DOM scene_tag hoặc tile_uid
    for t_idx, t in enumerate(candidate_tiles):
        st = t.get("scene_tag")
        if isinstance(st, int) and tag_counts.get(st, 0) == 1 and st in submitted_by_id and st not in matched:
            matched[st] = dict(t)
            used_tile_indices.add(t_idx)

    for sc in submitted_scenes:
        sid = int(sc["id"])
        if sid in matched:
            continue
        wanted_uid = str(sc.get("tile_uid") or "").strip()
        if not wanted_uid:
            continue
        for t_idx, t in enumerate(candidate_tiles):
            if t_idx not in used_tile_indices and str(t.get("uid") or "").strip() == wanted_uid:
                matched[sid] = dict(t)
                used_tile_indices.add(t_idx)
                break

    rem_scenes = [dict(sc) for sc in submitted_scenes if int(sc["id"]) not in matched]
    rem_tiles = [dict(t) for idx, t in enumerate(candidate_tiles) if idx not in used_tile_indices]

    if not rem_scenes:
        return matched, []
    if not rem_tiles:
        return matched, [int(sc["id"]) for sc in rem_scenes]

    # Sắp xếp thẻ còn lại theo thứ tự lưới từ trên xuống dưới, trái sang phải
    rem_tiles.sort(key=lambda t: (round(float(t.get("y", 0)) / 30.0), float(t.get("x", 0))))
    prompts = [_scene_match_prompt(sc) for sc in rem_scenes]
    score_mat: List[List[float]] = [
        [_text_overlap_score(str(t.get("aria_label") or ""), p) for t in rem_tiles]
        for p in prompts
    ]

    if len(rem_tiles) == len(rem_scenes):
        k = len(rem_scenes)
        # Flow chèn thẻ mới vào đầu lưới -> nộp [0..k-1] sẽ đứng theo thứ tự [k-1..0]
        rev_perm = tuple(range(k - 1, -1, -1))
        rev_total = sum(score_mat[i][rev_perm[i]] for i in range(k))

        if k <= 7:
            best_perm = rev_perm
            best_total = rev_total
            for perm in itertools.permutations(range(k)):
                tot = sum(score_mat[i][perm[i]] for i in range(k))
                if tot > best_total + 1e-6:
                    best_total = tot
                    best_perm = perm
            chosen_perm = best_perm if (best_total - rev_total >= 0.15) else rev_perm
        else:
            chosen_perm = rev_perm

        for i, sc in enumerate(rem_scenes):
            matched[int(sc["id"])] = rem_tiles[chosen_perm[i]]
        return matched, []

    # Tầng 2b: Thiếu hoặc lệch số thẻ -> chỉ gán các thẻ có độ tin cậy ngữ nghĩa cao
    n_s, n_t = len(rem_scenes), len(rem_tiles)
    confident_edges: Dict[Tuple[int, int], float] = {}
    for t_idx in range(n_t):
        col_scores = sorted(
            [(score_mat[s_idx][t_idx], s_idx) for s_idx in range(n_s)],
            reverse=True,
        )
        best_sc, best_s = col_scores[0]
        second_sc = col_scores[1][0] if len(col_scores) > 1 else 0.0
        if best_sc >= 0.25 and (n_s == 1 or (best_sc - second_sc) >= 0.10):
            confident_edges[(best_s, t_idx)] = best_sc

    # Chọn tập cặp khớp 1-1 (mỗi scene tối đa 1 tile, mỗi tile tối đa 1 scene) có tổng điểm cao nhất
    best_assign: Dict[int, int] = {}
    if len(confident_edges) <= 20 and n_s <= 6 and n_t <= 6:
        best_sum = -1.0
        edges_by_s: Dict[int, List[Tuple[int, float]]] = {}
        for (s_i, t_i), sc_val in confident_edges.items():
            edges_by_s.setdefault(s_i, []).append((t_i, sc_val))

        def _search(s_pos: int, used_t: Set[int], cur: Dict[int, int], cur_sum: float) -> None:
            nonlocal best_sum, best_assign
            if s_pos == n_s:
                if cur_sum > best_sum:
                    best_sum = cur_sum
                    best_assign = dict(cur)
                return
            _search(s_pos + 1, used_t, cur, cur_sum)
            for t_i, sc_val in edges_by_s.get(s_pos, []):
                if t_i not in used_t:
                    used_t.add(t_i)
                    cur[s_pos] = t_i
                    _search(s_pos + 1, used_t, cur, cur_sum + sc_val)
                    cur.pop(s_pos)
                    used_t.remove(t_i)

        _search(0, set(), {}, 0.0)
    else:
        for (s_i, t_i), sc_val in sorted(confident_edges.items(), key=lambda kv: kv[1], reverse=True):
            if s_i not in best_assign and t_i not in best_assign.values():
                best_assign[s_i] = t_i

    for s_i, t_i in best_assign.items():
        matched[int(rem_scenes[s_i]["id"])] = rem_tiles[t_i]

    unmatched = [int(sc["id"]) for sc in rem_scenes if int(sc["id"]) not in matched]
    return matched, unmatched


def _compute_image_similarity(img_path_a: Path, img_path_b: Path) -> float:
    """Tính độ tương đồng pixel [0.0, 1.0] giữa 2 ảnh ở kích thước chuẩn 32x32 RGB."""
    from PIL import Image

    with Image.open(img_path_a) as ia, Image.open(img_path_b) as ib:
        ra = ia.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
        rb = ib.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
        get_px_a = getattr(ra, "get_flattened_data", ra.getdata)
        get_px_b = getattr(rb, "get_flattened_data", rb.getdata)
        pa = list(get_px_a())
        pb = list(get_px_b())
    total_diff = 0
    for (r1, g1, b1), (r2, g2, b2) in zip(pa, pb):
        total_diff += abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
    mae = total_diff / float(len(pa) * 3)
    return max(0.0, min(1.0, 1.0 - mae / 255.0))


def _verify_and_assign_video_files_by_first_frame(
    scene_raw_pngs: Dict[int, Path],
    downloaded_tmp_videos: Sequence[Path],
    work_dir: Path,
) -> Dict[int, Path]:
    """Đối chiếu vật lý từng video tải về bằng khung hình đầu tiên so với raw_images/{id:03d}.png."""
    if not scene_raw_pngs or not downloaded_tmp_videos:
        return {}

    sids = list(scene_raw_pngs.keys())
    vids = list(downloaded_tmp_videos)
    if len(sids) == 1 and len(vids) == 1:
        return {sids[0]: vids[0]}

    ffmpeg_bin = _resolve_ffmpeg_binary()
    frames_dir = Path(work_dir) / "_first_frames_verify"
    frames_dir.mkdir(parents=True, exist_ok=True)
    vid_frames: List[Optional[Path]] = []
    for idx, vp in enumerate(vids):
        frame_out = frames_dir / f"frame_{idx:03d}.png"
        res = subprocess.run(
            [ffmpeg_bin, "-y", "-i", str(vp), "-vframes", "1", str(frame_out)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
        )
        if res.returncode == 0 and frame_out.is_file() and frame_out.stat().st_size > 100:
            vid_frames.append(frame_out)
        else:
            vid_frames.append(None)

    try:
        n_s = len(sids)
        n_v = len(vids)
        sim_mat: List[List[float]] = []
        for sid in sids:
            raw_png = scene_raw_pngs[sid]
            row_sims: List[float] = []
            for fp in vid_frames:
                if fp is not None and raw_png.is_file():
                    try:
                        row_sims.append(_compute_image_similarity(raw_png, fp))
                    except Exception:
                        row_sims.append(0.0)
                else:
                    row_sims.append(0.0)
            sim_mat.append(row_sims)

        assigned: Dict[int, Path] = {}
        if n_s == n_v and n_s <= 7:
            best_perm = tuple(range(n_s))
            best_score = -1.0
            for perm in itertools.permutations(range(n_v)):
                tot = sum(sim_mat[i][perm[i]] for i in range(n_s))
                if tot > best_score:
                    best_score = tot
                    best_perm = perm
            for i, sid in enumerate(sids):
                assigned[sid] = vids[best_perm[i]]
            return assigned

        # Fallback tổng quát nếu số lượng khác nhau hoặc > 7
        pairs = sorted(
            ((sim_mat[i][j], i, j) for i in range(n_s) for j in range(n_v)),
            reverse=True,
        )
        used_v: Set[int] = set()
        for _, i, j in pairs:
            sid = sids[i]
            if sid not in assigned and j not in used_v:
                assigned[sid] = vids[j]
                used_v.add(j)
        return assigned
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)


def _scan_grid_tiles(
    page: Any,
    *,
    want_video: Optional[bool] = None,
    policy_keywords: Sequence[str] = (),
) -> List[Dict[str, Any]]:
    """Quét toàn bộ thẻ trên lưới Google Flow theo DOM mới (`flow-grid-tile-container`) & sắp tọa độ."""
    kws = [str(k).lower() for k in policy_keywords if str(k).strip()]
    raw_tiles = page.evaluate(
        """(policyKws) => {
            if (!window.__flowbotNextUid) window.__flowbotNextUid = 1;
            let containers = Array.from(document.querySelectorAll('flow-grid-tile-container'));
            if (!containers.length) {
                containers = Array.from(document.querySelectorAll('flow-video-tile, flow-image-tile, [role="group"]'))
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.x >= 180 && r.width >= 90 && r.height >= 120;
                    });
            }
            const items = [];
            for (const el of containers) {
                const r = el.getBoundingClientRect();
                if (r.x < 180 || r.width < 90 || r.height < 120) continue;
                let uid = el.getAttribute('data-flowbot-uid');
                if (!uid) {
                    uid = 'fb_tile_' + (window.__flowbotNextUid++);
                    el.setAttribute('data-flowbot-uid', uid);
                }
                const preexisting = el.getAttribute('data-flowbot-preexisting') === '1';
                const sceneAttr = el.getAttribute('data-flowbot-scene-id');
                const sceneTag = (sceneAttr && /^\\d+$/.test(sceneAttr)) ? parseInt(sceneAttr, 10) : null;
                const ariaLabel = (el.getAttribute('aria-label') || '').trim();
                const text = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                const textLow = text.toLowerCase();

                const vid = el.querySelector('video');
                const imgs = Array.from(el.querySelectorAll('img'));
                const bestImg = imgs.find(im => im.getBoundingClientRect().width >= 80) || imgs[0] || null;
                const imgAlt = bestImg ? (bestImg.getAttribute('alt') || '').toLowerCase() : '';

                let mediaTag = 'NONE';
                let src = '';
                let hasLoadedMedia = false;
                if (vid) {
                    const vsrc = vid.getAttribute('src') || vid.currentSrc || (vid.querySelector('source') ? vid.querySelector('source').getAttribute('src') || '' : '');
                    if (vsrc) {
                        mediaTag = 'VIDEO';
                        src = vsrc;
                        hasLoadedMedia = (vid.readyState >= 1) || vsrc.startsWith('http') || vsrc.startsWith('blob:');
                    }
                }
                if (!src && bestImg) {
                    const isrc = bestImg.getAttribute('src') || bestImg.currentSrc || '';
                    if (isrc) {
                        mediaTag = 'IMG';
                        src = isrc;
                        hasLoadedMedia = (bestImg.naturalWidth >= 100) || isrc.startsWith('http');
                    }
                }

                const isVideoTile = Boolean(
                    vid ||
                    el.querySelector('flow-video-tile') ||
                    textLow.includes('play_circle') ||
                    imgAlt.includes('video')
                );

                const hasPct = /\\b\\d{1,3}\\s*%/.test(textLow);
                const hasProgEl = Boolean(
                    el.querySelector('[role="progressbar"], mat-progress-spinner, mat-spinner, [class*="progress" i], [class*="spinner" i]')
                );
                const hasGenText = (
                    textLow.includes('đang tạo') ||
                    textLow.includes('generating') ||
                    textLow.includes('in progress') ||
                    textLow.includes('queuing')
                );
                const isError = policyKws.some(kw => textLow.includes(kw)) ||
                    textLow.includes('không thể tạo') ||
                    textLow.includes('unable to generate') ||
                    textLow.includes('failed');

                const isLoading = !isError && (hasPct || hasProgEl || hasGenText || !hasLoadedMedia);

                items.push({
                    uid,
                    preexisting,
                    scene_tag: sceneTag,
                    aria_label: ariaLabel,
                    text,
                    media_tag: mediaTag,
                    src,
                    is_video_tile: isVideoTile,
                    is_loading: isLoading,
                    is_error: isError,
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                });
            }
            items.sort((a, b) => (Math.round(a.y / 30) - Math.round(b.y / 30)) || (a.x - b.x));
            items.forEach((it, idx) => { it.dom_index = idx; });
            return items;
        }""",
        kws,
    ) or []

    if want_video is None:
        return raw_tiles
    if want_video:
        return [t for t in raw_tiles if t.get("is_video_tile") or t.get("scene_tag") is not None or not t.get("preexisting")]
    return [t for t in raw_tiles if not t.get("is_video_tile")]


def _mark_preexisting_tiles(page: Any) -> Set[Tuple[str, str]]:
    """Đánh dấu toàn bộ thẻ đang có trước đợt nộp mới và trả tập (aria_label, src) dự phòng."""
    _scroll_grid_to_top(page)
    sigs = page.evaluate(
        """() => {
            if (!window.__flowbotNextUid) window.__flowbotNextUid = 1;
            let containers = Array.from(document.querySelectorAll('flow-grid-tile-container'));
            if (!containers.length) {
                containers = Array.from(document.querySelectorAll('flow-video-tile, flow-image-tile, [role="group"]'))
                    .filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.x >= 180 && r.width >= 90 && r.height >= 120;
                    });
            }
            const out = [];
            for (const el of containers) {
                if (!el.getAttribute('data-flowbot-uid')) {
                    el.setAttribute('data-flowbot-uid', 'fb_tile_' + (window.__flowbotNextUid++));
                }
                el.setAttribute('data-flowbot-preexisting', '1');
                el.removeAttribute('data-flowbot-scene-id');
                const aria = (el.getAttribute('aria-label') || '').trim();
                const vid = el.querySelector('video');
                const img = el.querySelector('img');
                const src = (vid ? (vid.getAttribute('src') || vid.currentSrc || '') : '') ||
                            (img ? (img.getAttribute('src') || img.currentSrc || '') : '');
                out.push([aria, src]);
            }
            return out;
        }"""
    ) or []
    return {(str(a), str(s)) for a, s in sigs}


def _wait_for_new_tile_slot(
    page: Any,
    scene_id: int,
    known_uids: Set[str],
    preexisting_sigs: Set[Tuple[str, str]],
    timeout_sec: float = 12.0,
) -> Optional[str]:
    """Chờ thẻ mới (placeholder hoặc đã render) vừa sinh sau khi bấm Tạo và gắn ngay scene_id."""
    deadline = time.time() + max(2.0, float(timeout_sec))
    while time.time() < deadline:
        assigned_uid = page.evaluate(
            """([sid, knownList, preSigs]) => {
                if (!window.__flowbotNextUid) window.__flowbotNextUid = 1;
                const known = new Set(knownList);
                const sigSet = new Set(preSigs.map(p => p[0] + '||' + p[1]));
                let containers = Array.from(document.querySelectorAll('flow-grid-tile-container'));
                if (!containers.length) {
                    containers = Array.from(document.querySelectorAll('flow-video-tile, flow-image-tile, [role="group"]'))
                        .filter(el => {
                            const r = el.getBoundingClientRect();
                            return r.x >= 180 && r.width >= 90 && r.height >= 120;
                        });
                }
                containers.sort((a, b) => {
                    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
                    return (Math.round(ra.y / 30) - Math.round(rb.y / 30)) || (ra.x - rb.x);
                });
                for (const el of containers) {
                    const r = el.getBoundingClientRect();
                    if (r.x < 180 || r.width < 90 || r.height < 120) continue;
                    let uid = el.getAttribute('data-flowbot-uid');
                    if (!uid) {
                        uid = 'fb_tile_' + (window.__flowbotNextUid++);
                        el.setAttribute('data-flowbot-uid', uid);
                    }
                    if (el.getAttribute('data-flowbot-preexisting') === '1') continue;
                    if (known.has(uid)) continue;
                    const aria = (el.getAttribute('aria-label') || '').trim();
                    const vid = el.querySelector('video');
                    const img = el.querySelector('img');
                    const src = (vid ? (vid.getAttribute('src') || vid.currentSrc || '') : '') ||
                                (img ? (img.getAttribute('src') || img.currentSrc || '') : '');
                    if (!aria && !src && sigSet.size > 0) {
                        // Placeholder rỗng mới sinh -> chắc chắn là slot vừa nộp
                    } else if ((aria || src) && sigSet.has(aria + '||' + src)) {
                        continue;
                    }
                    el.setAttribute('data-flowbot-scene-id', String(sid));
                    return uid;
                }
                return null;
            }""",
            [scene_id, list(known_uids), [list(p) for p in preexisting_sigs]],
        )
        if assigned_uid:
            return str(assigned_uid)
        time.sleep(0.5)
    return None


def _download_tile_by_uid(
    page: Any,
    tile_uid: str,
    dst_download_path: Path,
    *,
    is_video: bool,
    fallback_aria: str = "",
) -> Path:
    """Tải bản gốc full-res trực tiếp từ `flow-grid-tile-container` qua UID hoặc fallback aria-label.

    Hỗ trợ cả nút `more_vert` khi hover lẫn chuột phải vào `mat-context-menu-trigger`.
    """
    tile_loc = None
    if tile_uid:
        cand = page.locator(f"[data-flowbot-uid='{tile_uid}']").first
        try:
            if cand.count() > 0:
                tile_loc = cand
        except Exception:
            tile_loc = None

    if tile_loc is None and fallback_aria:
        escaped_aria = fallback_aria.replace("'", "\\'")
        cand = page.locator(f"flow-grid-tile-container[aria-label='{escaped_aria}']").first
        try:
            if cand.count() > 0:
                tile_loc = cand
        except Exception:
            tile_loc = None

    if tile_loc is None:
        raise RuntimeError(f"Không định vị được thẻ media (uid={tile_uid!r}, aria={fallback_aria!r}) để tải xuống.")

    tile_loc.evaluate("e => e.scrollIntoView({block: 'center'})")
    time.sleep(0.35)
    try:
        tile_loc.hover()
    except Exception:
        pass
    box = tile_loc.bounding_box()
    if not box:
        raise RuntimeError("Thẻ media chưa hiển thị bounding box để tải xuống.")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    time.sleep(0.55)

    opened = False
    for _ in range(5):
        opened = tile_loc.evaluate(
            """e => {
                const btn = Array.from(e.querySelectorAll('button')).find(b => (b.innerText || '').includes('more_vert'));
                if (btn) { btn.click(); return true; }
                return false;
            }"""
        )
        if opened:
            break
        box_now = tile_loc.bounding_box()
        if box_now:
            page.mouse.move(box_now["x"] + box_now["width"] - 20, box_now["y"] + 20)
        time.sleep(0.5)

    if not opened:
        # Fallback: mở context menu bằng chuột phải vào mat-context-menu-trigger
        box_now = tile_loc.bounding_box() or box
        page.mouse.click(box_now["x"] + box_now["width"] / 2, box_now["y"] + box_now["height"] / 2, button="right")
        time.sleep(0.6)

    dl_item = page.locator("[role='menuitem']:has-text('Tải xuống'), [role='menuitem']:has-text('Download')").first
    dl_item.wait_for(state="visible", timeout=5000)
    has_popup = dl_item.get_attribute("aria-haspopup")
    dst_download_path.parent.mkdir(parents=True, exist_ok=True)

    if is_video or has_popup == "menu":
        dl_item.hover()
        time.sleep(0.6)
        sub_loc = page.locator("[role='menuitem'], [role='option']")
        chosen = None
        for i in range(sub_loc.count()):
            cand = sub_loc.nth(i)
            try:
                if not cand.is_visible():
                    continue
                txt_n = _norm_nfc(cand.inner_text())
                if "gif" in txt_n:
                    continue
                if any(k in txt_n for k in ("kích thước gốc", "original", "4k", "2k", "1080p", "720p", "1k")):
                    chosen = cand
                    break
            except Exception:
                continue
        if chosen is not None:
            with page.expect_download(timeout=90_000) as dl_info:
                chosen.click()
            dl_info.value.save_as(str(dst_download_path))
            page.keyboard.press("Escape")
            page.mouse.move(10, 10)
            time.sleep(0.25)
            return dst_download_path

    with page.expect_download(timeout=90_000) as dl_info:
        dl_item.click()
    dl_info.value.save_as(str(dst_download_path))
    page.keyboard.press("Escape")
    page.mouse.move(10, 10)
    time.sleep(0.25)
    return dst_download_path


def _upload_start_frame(page: Any, raw_png: Path) -> None:
    """Nạp ảnh gốc raw_images/{id:03d}.png vào ô 'Bắt đầu' trong chế độ Video -> Khung hình."""
    # Đóng modal cũ nếu đang kẹt mở
    old_close = page.locator(".cdk-overlay-container button[aria-label='Đóng']").first
    if old_close.is_visible():
        page.keyboard.press("Escape")
        time.sleep(0.4)

    # Nếu đã có khung hình cũ dính lại từ cảnh trước, thử bấm nút gỡ (close) gần swap_horiz ở thanh prompt
    start_btn = page.locator("button:has-text('Bắt đầu')").first
    if not start_btn.is_visible():
        try:
            page.evaluate(
                """() => {
                    const swap = document.querySelector("button[aria-label*='Hoán đổi']");
                    if (!swap) return;
                    const sr = swap.getBoundingClientRect();
                    const btns = Array.from(document.querySelectorAll('button')).filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.y >= sr.y - 60 && r.y <= sr.y + 60 && r.x < sr.x && r.x > 200;
                    });
                    const closeB = btns.find(b => (b.innerText || '').includes('close') || (b.getAttribute('aria-label') || '').toLowerCase().includes('xóa') || (b.getAttribute('aria-label') || '').toLowerCase().includes('gỡ'));
                    if (closeB) closeB.click();
                    else if (btns.length) btns[0].click();
                }"""
            )
            time.sleep(0.6)
        except Exception:
            pass

    start_btn = page.locator("button:has-text('Bắt đầu')").first
    if start_btn.is_visible():
        start_btn.click(timeout=5000)
        time.sleep(0.7)

    # Tìm đúng nút 'Tải nội dung nghe nhìn lên' BÊN TRONG modal .cdk-overlay-container (không nhầm sidebar)
    upload_btn = page.locator(
        ".cdk-overlay-container button:has-text('Tải nội dung'), "
        ".cdk-overlay-container button:has-text('Upload')"
    ).first
    if upload_btn.is_visible():
        # Ghi nhận tên mục đầu tiên trong modal trước khi upload để biết khi nào ảnh mới đã tải xong
        stem_low = raw_png.stem.lower()
        name_low = raw_png.name.lower()
        with page.expect_file_chooser(timeout=12_000) as fc_info:
            upload_btn.click(timeout=5000)
        fc_info.value.set_files(str(raw_png))

        # Chờ tối đa 30s để upload hoàn tất (tự gắn vào khung Bắt đầu HOẶC hiện sẵn để bấm 'Thêm vào câu lệnh')
        wait_end = time.time() + 30.0
        clicked_add = False
        while time.time() < wait_end:
            time.sleep(1.0)
            close_modal = page.locator(".cdk-overlay-container button[aria-label='Đóng']").first
            # Trường hợp 1: Modal tự đóng và nút 'Bắt đầu' đã biến thành thumbnail ảnh
            if not close_modal.is_visible() and not page.locator("button:has-text('Bắt đầu')").first.is_visible():
                time.sleep(0.6)
                return

            # Trường hợp 2: Modal vẫn mở nhưng đã xuất hiện mục khớp tên file vừa upload -> chọn và bấm 'Thêm vào câu lệnh'
            if close_modal.is_visible() and not clicked_add:
                found_new = page.evaluate(
                    """([nm, st]) => {
                        const ov = document.querySelector('.cdk-overlay-container');
                        if (!ov) return false;
                        const btns = Array.from(ov.querySelectorAll('button')).filter(b => {
                            const t = (b.innerText || '').trim().toLowerCase();
                            return t.includes(nm) || t.includes(st + '.png');
                        });
                        if (btns.length) {
                            btns[0].click();
                            return true;
                        }
                        return false;
                    }""",
                    [name_low, stem_low],
                )
                if found_new:
                    time.sleep(0.6)
                    add_btn = page.locator(".cdk-overlay-container button:has-text('Thêm vào câu lệnh')").first
                    if add_btn.is_visible() and not add_btn.is_disabled():
                        add_btn.click(timeout=4000)
                        clicked_add = True
                        time.sleep(0.8)

        # Kiểm tra chốt: không được để nút 'Bắt đầu' trống (tránh tạo video không có khung hình gốc!)
        if page.locator("button:has-text('Bắt đầu')").first.is_visible():
            shot = _save_debug_screenshot(page, f"upload_frame_fail_{raw_png.stem}.png")
            raise RuntimeError(
                f"Đã tải lên {raw_png.name} nhưng khung 'Bắt đầu' vẫn trống (chưa gắn được ảnh). Ảnh debug: {shot}"
            )
        return

    # Fallback: input[type='file'] trực tiếp nếu giao diện đổi lại dạng input
    finp = page.locator("input[type='file']")
    if finp.count() > 0:
        finp.first.set_input_files(str(raw_png))
        time.sleep(1.5)
        return

    raise RuntimeError(
        "Không tìm thấy nút 'Bắt đầu' / 'Tải nội dung nghe nhìn lên' hoặc input[type='file'] để nạp ảnh gốc."
    )


def _detect_queue_limit(body_text: str, baseline_text: str) -> Optional[str]:
    """Phát hiện thông báo giới hạn hàng đợi / gửi đồng thời mới xuất hiện trên Google Flow."""
    base_low = baseline_text.lower()
    cur_low = body_text.lower()
    for kw in _QUEUE_LIMIT_KEYWORDS:
        if kw in cur_low and cur_low.count(kw) > base_low.count(kw):
            for line in body_text.splitlines():
                if kw in line.lower():
                    return line.strip()[:250]
            return f"Đạt giới hạn hàng đợi ({kw})"
    return None


def _wait_for_batch_tiles(
    page: Any,
    submitted: Sequence[Dict[str, Any]],
    preexisting_sigs: Set[Tuple[str, str]],
    baseline_text: str,
    cfg: Dict[str, Any],
    *,
    want_video: bool,
    timeout_sec: int,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Chờ toàn bộ thẻ trong đợt nộp song song render xong (KHÔNG click lặp sidebar tab)."""
    sels = cfg["selectors"]
    policy_kws = sels.get("policy_error_keywords", [])
    media_kind = "video" if want_video else "ảnh"
    min_wait = 12.0 if want_video else 5.0
    deadline = time.time() + max(timeout_sec, int(min_wait) + 15)
    start_ts = time.time()
    submitted_ids = {int(s["id"]) for s in submitted}

    time.sleep(8.0 if want_video else 4.0)

    last_status: Optional[Tuple[int, int, int]] = None
    settled_empty_polls = 0
    latest_policy_msg: Optional[str] = None

    while time.time() < deadline:
        cur_text = _get_visible_body_text(page)
        p_msg = _detect_policy_error(cur_text, baseline_text, policy_kws)
        if p_msg:
            latest_policy_msg = p_msg
            if len(submitted) == 1:
                raise PolicyBlockedError(p_msg)

        all_tiles = _scan_grid_tiles(page, want_video=want_video, policy_keywords=policy_kws)
        new_tiles = [
            t
            for t in all_tiles
            if (not t.get("preexisting") and (t.get("aria_label", ""), t.get("src", "")) not in preexisting_sigs)
            or (t.get("scene_tag") in submitted_ids)
        ]

        err_cnt = sum(1 for t in new_tiles if t.get("is_error"))
        load_cnt = sum(1 for t in new_tiles if t.get("is_loading") and not t.get("is_error"))
        ready_cnt = sum(1 for t in new_tiles if not t.get("is_loading") and not t.get("is_error"))

        status_tup = (ready_cnt, err_cnt, load_cnt)
        if status_tup != last_status:
            print(
                f"  [BATCH] Tiến độ {media_kind}: xong {ready_cnt}/{len(submitted)} | "
                f"lỗi {err_cnt} | đang render {load_cnt}...",
                flush=True,
            )
            last_status = status_tup

        elapsed = time.time() - start_ts
        if load_cnt == 0 and len(new_tiles) >= len(submitted) and (ready_cnt + err_cnt) >= len(submitted):
            time.sleep(1.5)
            all_tiles = _scan_grid_tiles(page, want_video=want_video, policy_keywords=policy_kws)
            final_tiles = [
                t
                for t in all_tiles
                if (not t.get("preexisting") and (t.get("aria_label", ""), t.get("src", "")) not in preexisting_sigs)
                or (t.get("scene_tag") in submitted_ids)
            ]
            return final_tiles, latest_policy_msg

        if load_cnt == 0 and elapsed >= min_wait and (ready_cnt + err_cnt > 0 or latest_policy_msg):
            settled_empty_polls += 1
            if settled_empty_polls >= 2:
                all_tiles = _scan_grid_tiles(page, want_video=want_video, policy_keywords=policy_kws)
                final_tiles = [
                    t
                    for t in all_tiles
                    if (not t.get("preexisting") and (t.get("aria_label", ""), t.get("src", "")) not in preexisting_sigs)
                    or (t.get("scene_tag") in submitted_ids)
                ]
                return final_tiles, latest_policy_msg
        else:
            settled_empty_polls = 0

        time.sleep(3.0)

    # Hết timeout tổng: vẫn trả về các thẻ đã kịp xong để cứu tối đa thay vì bỏ trắng cả mẻ
    all_tiles = _scan_grid_tiles(page, want_video=want_video, policy_keywords=policy_kws)
    partial_tiles = [
        t
        for t in all_tiles
        if not t.get("is_loading")
        and (
            (not t.get("preexisting") and (t.get("aria_label", ""), t.get("src", "")) not in preexisting_sigs)
            or (t.get("scene_tag") in submitted_ids)
        )
    ]
    return partial_tiles, latest_policy_msg


def _resolve_concurrency(cfg: Dict[str, Any], mode: str, override: Optional[int]) -> int:
    if override is not None:
        return max(1, int(override))
    c_cfg = cfg.get("concurrency", {})
    key = "images" if mode == "images" else "videos"
    default_val = 4 if mode == "images" else 3
    try:
        return max(1, int(c_cfg.get(key, default_val)))
    except Exception:
        return default_val


def run_probe_mode(cdp_url: str, cfg: Dict[str, Any]) -> int:
    """Chế độ --mode probe: soi DOM thật của Google Flow, KHÔNG bấm nút Tạo."""
    from playwright.sync_api import sync_playwright

    print(f"[PROBE] Đang kết nối Chrome CDP tại {cdp_url} ...")
    with sync_playwright() as p:
        browser, page = _connect_flow_page(p, cdp_url, _get_flow_url_keywords(cfg))
        try:
            url = page.url
            title = page.title()
            body_text = _get_visible_body_text(page)
            credit_mentions = _extract_credit_mentions(body_text)

            dom_dump = page.evaluate(
                """() => {
                    const query = [
                        'button',
                        'textarea',
                        '[contenteditable]',
                        'input[type="file"]',
                        '[role="textbox"]',
                        '[role="tab"]',
                        '[role="button"]',
                        '[role="combobox"]'
                    ].join(',');
                    const nodes = Array.from(document.querySelectorAll(query));
                    return nodes.map((el, idx) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        const isFile = el.tagName.toLowerCase() === 'input' && el.type === 'file';
                        const visible = isFile || (
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0
                        );
                        return {
                            index: idx,
                            tag: el.tagName.toLowerCase(),
                            type: el.getAttribute('type') || '',
                            role: el.getAttribute('role') || '',
                            text: (el.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                            aria_label: el.getAttribute('aria-label') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            contenteditable: el.getAttribute('contenteditable') || '',
                            accept: el.getAttribute('accept') || '',
                            id: el.id || '',
                            data_testid: el.getAttribute('data-testid') || '',
                            aria_selected: el.getAttribute('aria-selected') || '',
                            disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                            visible: visible,
                            bbox: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                w: Math.round(rect.width),
                                h: Math.round(rect.height),
                            }
                        };
                    }).filter(item => item.visible);
                }"""
            )

            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            dump_path = DEBUG_DIR / "flow_probe_dump.json"
            payload = {
                "probed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "url": url,
                "title": title,
                "credit_mentions": credit_mentions,
                "element_count": len(dom_dump),
                "elements": dom_dump,
            }
            dump_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            print("=" * 84)
            print(f"[PROBE] URL   : {url}")
            print(f"[PROBE] Title : {title}")
            print(f"[PROBE] Mức tín dụng tìm thấy trên trang: {credit_mentions}")
            print(f"[PROBE] Tổng số phần tử tương tác đang hiển thị: {len(dom_dump)}")
            print("-" * 84)
            for item in dom_dump:
                lbl = item["text"] or item["aria_label"] or item["placeholder"] or item["accept"] or "(no label)"
                print(
                    f"  <{item['tag']} role='{item['role']}' type='{item['type']}'> "
                    f"| label={lbl!r} | aria={item['aria_label']!r} | sel={item['aria_selected']!r}"
                )
            print("=" * 84)
            print(f"[PROBE] Đã lưu chi tiết DOM tại: {dump_path}")
            print("[PROBE] An toàn: KHÔNG bấm bất kỳ nút Tạo nào.")
            return 0
        finally:
            browser.close()


def run_images_mode(
    storyboard_path: Path,
    data: Dict[str, Any],
    plan: List[ScenePlanItem],
    cfg: Dict[str, Any],
    cdp_url: str,
    *,
    concurrency: Optional[int] = None,
    frame_preset: Optional[str] = None,
) -> int:
    """Chế độ --mode images: gen toàn bộ ảnh gốc (0 tín dụng) theo từng đợt song song + xuất JPG đúng frame_preset."""
    ep_dir = storyboard_path.parent
    raw_dir = ep_dir / "raw_images"
    assets_dir = ep_dir / "assets"
    state_path = ep_dir / "pipeline_state.json"
    state = _load_full_pipeline_state(state_path, data)
    eff_fp = resolve_frame_preset(override=frame_preset, storyboard_preset=data.get("frame_preset"))
    aspect_plan = resolve_flow_aspect_plan(eff_fp, mode="images")

    # Bước A: Đồng bộ pipeline_state cho các cảnh đã có sẵn thành phẩm trong assets/
    state_dirty = False
    for item in plan:
        if item.action == "SKIP_EXISTING_ASSET":
            if item.asset_type == "flow_video":
                _update_scene_state(state, item.scene_id, image_status="done", video_status="done", error="")
            else:
                _update_scene_state(state, item.scene_id, image_status="done", video_status="skipped", error="")
            state_dirty = True
    if state_dirty:
        _save_pipeline_state_atomic(state_path, state)

    # Bước B: Xử lý ngay các cảnh chỉ cần convert PNG -> JPG (không cần mở Chrome)
    for item in plan:
        if item.action == "CONVERT_JPG":
            sid = item.scene_id
            raw_png = raw_dir / f"{sid:03d}.png"
            dst_jpg = assets_dir / f"{sid:03d}.jpg"
            out_w, out_h = _convert_png_to_jpg(raw_png, dst_jpg, frame_preset=eff_fp.name)
            _update_scene_state(state, sid, image_status="done", video_status="skipped", error="")
            _save_pipeline_state_atomic(state_path, state)
            print(f"[Cảnh {sid:03d}] Đã convert ảnh gốc sẵn có -> assets/{sid:03d}.jpg ({out_w}x{out_h}, 0 tín dụng).")

    runnable = [it for it in plan if it.action == "RUN"]
    if not runnable:
        print("[IMAGES] Không có cảnh nào cần gen mới trên Google Flow. Hoàn tất!")
        return 0

    from playwright.sync_api import sync_playwright

    eff_conc = _resolve_concurrency(cfg, "images", concurrency)
    c_cfg = cfg.get("concurrency", {})
    submit_gap = float(c_cfg.get("submit_gap_sec", 2.5))
    slot_wait_sec = float(c_cfg.get("placeholder_wait_sec", 12.0))
    timeout_sec = int(cfg.get("timeouts", {}).get("image_timeout_sec", 120))
    delay_sec = float(cfg.get("timeouts", {}).get("delay_between_scenes_sec", 4))
    sels = cfg["selectors"]

    print(
        f"[IMAGES] Chế độ đồng loạt: {eff_conc} ảnh/đợt (tổng {len(runnable)} ảnh, "
        f"khung {eff_fp.name} → assets {eff_fp.inner_w}x{eff_fp.inner_h}, 0 tín dụng).",
        flush=True,
    )

    failed_count = 0
    queue: List[ScenePlanItem] = list(runnable)
    wave_idx = 0

    with sync_playwright() as p:
        browser, page = _connect_flow_page(p, cdp_url, _get_flow_url_keywords(cfg))
        try:
            _click_sidebar_tab(page, cfg["image_mode"]["tab_label"], force=True)
            while queue:
                wave_idx += 1
                chunk = queue[:eff_conc]
                queue = queue[eff_conc:]
                chunk_ids = [it.scene_id for it in chunk]
                print(f"\n[IMAGES | ĐỢT {wave_idx}] Nộp {len(chunk)} cảnh {chunk_ids}...", flush=True)

                try:
                    selected_ui_aspect = _configure_image_mode(
                        page, cfg, chunk[0].scene_id, aspect_plan=aspect_plan
                    )
                except SafetyAssertionError:
                    raise

                baseline_text = _get_visible_body_text(page)
                preexisting_sigs = _mark_preexisting_tiles(page)
                known_uids: Set[str] = {
                    str(t.get("uid"))
                    for t in _scan_grid_tiles(page, want_video=None)
                    if t.get("uid")
                }

                submitted: List[Dict[str, Any]] = []
                deferred: List[ScenePlanItem] = []

                # Giai đoạn A: Nộp nối tiếp nhanh từng cảnh trong đợt
                for pos, item in enumerate(chunk):
                    sid = item.scene_id
                    sc = item.scene_data
                    raw_prompt = str(sc.get("image_prompt", "")).strip()
                    prompt = _augment_prompt_for_flow_aspect(raw_prompt, aspect_plan, selected_ui_aspect)

                    cur_txt = _get_visible_body_text(page)
                    q_lim = _detect_queue_limit(cur_txt, baseline_text)
                    if q_lim and submitted:
                        print(f"  [QUEUE] {q_lim} → chuyển các cảnh còn lại sang đợt sau.", flush=True)
                        deferred.extend(chunk[pos:])
                        break

                    try:
                        _fill_prompt(page, sels.get("prompt_input", []), prompt)
                        _assert_settings_pill_summary(
                            page,
                            cfg,
                            (cfg["image_mode"]["model_label"], cfg["image_mode"]["batch_label"]),
                            scene_id=sid,
                            mode_tag="images",
                        )
                        gen_btn, _ = _first_visible_locator(page, sels.get("generate_button", []), timeout_ms=4000)
                        if gen_btn is None:
                            raise RuntimeError("Không tìm thấy nút Tạo trên Google Flow. Hãy chạy `--mode probe`.")
                        if gen_btn.is_disabled():
                            time.sleep(2.5)
                        if gen_btn.is_disabled():
                            if submitted:
                                print(f"  [QUEUE] Nút Tạo đang khóa (đầy hàng đợi) → chuyển Cảnh {sid:03d}+ sang đợt sau.", flush=True)
                                deferred.extend(chunk[pos:])
                                break
                            raise RuntimeError("Nút Tạo bị vô hiệu hóa khi nộp cảnh đầu tiên.")

                        gen_btn.click()
                        slot_uid = _wait_for_new_tile_slot(
                            page, sid, known_uids, preexisting_sigs, timeout_sec=slot_wait_sec
                        )
                        if slot_uid:
                            known_uids.add(slot_uid)
                        submitted.append({
                            "id": sid,
                            "item": item,
                            "prompt": prompt,
                            "tile_uid": slot_uid,
                        })
                        print(f"  -> Đã gửi lệnh ảnh Cảnh {sid:03d} (slot={slot_uid or 'pending'})", flush=True)
                        if pos < len(chunk) - 1:
                            time.sleep(submit_gap)
                    except SafetyAssertionError:
                        raise
                    except Exception as exc:
                        failed_count += 1
                        shot = _save_debug_screenshot(page, f"error_scene_{sid:03d}_images.png")
                        _update_scene_state(state, sid, image_status="error", error=str(exc))
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: LỖI khi gửi lệnh ({exc}) | Ảnh debug: {shot}", flush=True)

                if deferred:
                    queue = deferred + queue

                if not submitted:
                    continue

                # Giai đoạn B: Chờ toàn bộ đợt render xong (KHÔNG click lặp sidebar tab)
                try:
                    final_tiles, policy_msg = _wait_for_batch_tiles(
                        page,
                        submitted,
                        preexisting_sigs,
                        baseline_text,
                        cfg,
                        want_video=False,
                        timeout_sec=timeout_sec,
                    )
                except PolicyBlockedError as pe:
                    failed_count += len(submitted)
                    for sub in submitted:
                        sid = sub["id"]
                        _save_debug_screenshot(page, f"policy_scene_{sid:03d}_images.png")
                        _update_scene_state(state, sid, image_status="policy_error", error=str(pe))
                    _save_pipeline_state_atomic(state_path, state)
                    print(f"  ✗ Cảnh {[s['id'] for s in submitted]}: BỊ CHẶN POLICY ({pe})", flush=True)
                    continue

                # Giai đoạn C: Khớp thẻ & tải xuống
                matched, unmatched_ids = _match_tiles_to_batch_scenes(submitted, final_tiles)
                sub_by_id = {int(s["id"]): s for s in submitted}

                for sid, tile in matched.items():
                    item = sub_by_id[sid]["item"]
                    asset_type = item.asset_type
                    raw_png = raw_dir / f"{sid:03d}.png"
                    tmp_img = raw_dir / f"{sid:03d}.tmp.download"
                    dst_jpg = assets_dir / f"{sid:03d}.jpg"

                    if tile.get("is_error"):
                        failed_count += 1
                        err_txt = tile.get("text") or policy_msg or "Google Flow từ chối tạo ảnh (Policy)"
                        shot = _save_debug_screenshot(page, f"policy_scene_{sid:03d}_images.png")
                        _update_scene_state(state, sid, image_status="policy_error", error=err_txt[:300])
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: BỊ CHẶN POLICY ({err_txt[:120]}) | Ảnh debug: {shot}", flush=True)
                        continue

                    try:
                        dl_path = _download_tile_by_uid(
                            page,
                            str(tile.get("uid") or ""),
                            tmp_img,
                            is_video=False,
                            fallback_aria=str(tile.get("aria_label") or ""),
                        )
                        _convert_image_to_png(dl_path, raw_png)
                        if tmp_img.is_file():
                            tmp_img.unlink(missing_ok=True)
                        if not _file_ok(raw_png, MIN_IMAGE_BYTES):
                            raise RuntimeError(f"Ảnh tải về quá nhỏ (<10KB): {raw_png}")

                        if asset_type == "static_image":
                            out_w, out_h = _convert_png_to_jpg(raw_png, dst_jpg, frame_preset=eff_fp.name)
                            _update_scene_state(state, sid, image_status="done", video_status="skipped", error="")
                            print(
                                f"  ✓ Cảnh {sid:03d}: Đã lưu raw_images/{sid:03d}.png + assets/{sid:03d}.jpg ({out_w}x{out_h})",
                                flush=True,
                            )
                        else:
                            _update_scene_state(state, sid, image_status="done", error="")
                            print(f"  ✓ Cảnh {sid:03d}: Đã lưu raw_images/{sid:03d}.png", flush=True)
                        _save_pipeline_state_atomic(state_path, state)
                    except Exception as exc:
                        failed_count += 1
                        shot = _save_debug_screenshot(page, f"error_scene_{sid:03d}_images.png")
                        _update_scene_state(state, sid, image_status="error", error=str(exc))
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: LỖI tải ảnh ({exc}) | Ảnh debug: {shot}", flush=True)

                for sid in unmatched_ids:
                    failed_count += 1
                    is_pol = bool(policy_msg)
                    st_name = "policy_error" if is_pol else "error"
                    msg = policy_msg or "Không khớp được thẻ ảnh mới trên lưới (có thể bị Flow ẩn hoặc timeout)."
                    shot = _save_debug_screenshot(page, f"{'policy' if is_pol else 'error'}_scene_{sid:03d}_images.png")
                    _update_scene_state(state, sid, image_status=st_name, error=msg)
                    _save_pipeline_state_atomic(state_path, state)
                    print(f"  ✗ Cảnh {sid:03d}: {st_name.upper()} ({msg}) | Ảnh debug: {shot}", flush=True)

                if queue:
                    time.sleep(delay_sec)
        finally:
            browser.close()

    return 1 if failed_count > 0 else 0


def run_videos_mode(
    storyboard_path: Path,
    data: Dict[str, Any],
    plan: List[ScenePlanItem],
    cfg: Dict[str, Any],
    cdp_url: str,
    *,
    concurrency: Optional[int] = None,
    frame_preset: Optional[str] = None,
) -> int:
    """Chế độ --mode videos: chạy cảnh flow_video (10 tín dụng/clip) theo đợt song song + xác minh khung đầu + FFmpeg mute."""
    ep_dir = storyboard_path.parent
    raw_dir = ep_dir / "raw_images"
    assets_dir = ep_dir / "assets"
    state_path = ep_dir / "pipeline_state.json"
    state = _load_full_pipeline_state(state_path, data)
    eff_fp = resolve_frame_preset(override=frame_preset, storyboard_preset=data.get("frame_preset"))
    aspect_plan = resolve_flow_aspect_plan(eff_fp, mode="videos")

    # Đồng bộ pipeline_state cho các cảnh flow_video đã có sẵn thành phẩm assets/*.mp4
    state_dirty = False
    for item in plan:
        if item.action == "SKIP_EXISTING_ASSET" and item.asset_type == "flow_video":
            _update_scene_state(state, item.scene_id, image_status="done", video_status="done", error="")
            state_dirty = True
    if state_dirty:
        _save_pipeline_state_atomic(state_path, state)

    # Báo lỗi rõ các cảnh thiếu ảnh gốc
    missing_raw = [it for it in plan if it.action == "ERROR_MISSING_RAW"]
    for it in missing_raw:
        print(f"[Cảnh {it.scene_id:03d}] BỎ QUA: {it.reason}")

    runnable = [it for it in plan if it.action == "RUN"]
    if not runnable:
        print("[VIDEOS] Không có cảnh flow_video nào đủ điều kiện chạy mới.")
        return 1 if missing_raw else 0

    from playwright.sync_api import sync_playwright

    eff_conc = _resolve_concurrency(cfg, "videos", concurrency)
    c_cfg = cfg.get("concurrency", {})
    submit_gap = float(c_cfg.get("submit_gap_sec", 2.5))
    slot_wait_sec = float(c_cfg.get("placeholder_wait_sec", 12.0))
    timeout_sec = int(cfg.get("timeouts", {}).get("video_timeout_sec", 360))
    delay_sec = float(cfg.get("timeouts", {}).get("delay_between_scenes_sec", 4))
    sels = cfg["selectors"]

    print(
        f"[VIDEOS] Chế độ đồng loạt: {eff_conc} video/đợt (tổng {len(runnable)} video, "
        f"{len(runnable) * 10} tín dụng Veo 3.1 - Lite).",
        flush=True,
    )

    failed_count = len(missing_raw)
    queue: List[ScenePlanItem] = []
    for item in runnable:
        sid = item.scene_id
        raw_png = raw_dir / f"{sid:03d}.png"
        if not _file_ok(raw_png, MIN_IMAGE_BYTES):
            failed_count += 1
            msg = f"Thiếu ảnh gốc raw_images/{sid:03d}.png"
            _update_scene_state(state, sid, video_status="error", error=msg)
            _save_pipeline_state_atomic(state_path, state)
            print(f"  ✗ Cảnh {sid:03d}: {msg} → bỏ qua.", flush=True)
        else:
            queue.append(item)

    if not queue:
        return 1 if failed_count > 0 else 0

    wave_idx = 0
    with sync_playwright() as p:
        browser, page = _connect_flow_page(p, cdp_url, _get_flow_url_keywords(cfg))
        try:
            _click_sidebar_tab(page, cfg["video_mode"]["tab_label"], force=True)
            while queue:
                wave_idx += 1
                chunk = queue[:eff_conc]
                queue = queue[eff_conc:]
                chunk_ids = [it.scene_id for it in chunk]
                print(
                    f"\n[VIDEOS | ĐỢT {wave_idx}] Nộp {len(chunk)} cảnh {chunk_ids} ({len(chunk) * 10} tín dụng)...",
                    flush=True,
                )

                try:
                    _configure_video_mode(page, cfg, chunk[0].scene_id, aspect_plan=aspect_plan)
                except SafetyAssertionError:
                    raise

                baseline_text = _get_visible_body_text(page)
                preexisting_sigs = _mark_preexisting_tiles(page)
                known_uids: Set[str] = {
                    str(t.get("uid"))
                    for t in _scan_grid_tiles(page, want_video=None)
                    if t.get("uid")
                }

                submitted: List[Dict[str, Any]] = []
                deferred: List[ScenePlanItem] = []

                # Giai đoạn A: Upload khung Bắt đầu + Nộp nối tiếp nhanh từng cảnh video
                for pos, item in enumerate(chunk):
                    sid = item.scene_id
                    sc = item.scene_data
                    motion_prompt = str(sc.get("motion_prompt", "")).strip()
                    image_prompt = str(sc.get("image_prompt", "")).strip()
                    raw_png = raw_dir / f"{sid:03d}.png"

                    cur_txt = _get_visible_body_text(page)
                    q_lim = _detect_queue_limit(cur_txt, baseline_text)
                    if q_lim and submitted:
                        print(f"  [QUEUE] {q_lim} → chuyển các cảnh còn lại sang đợt sau.", flush=True)
                        deferred.extend(chunk[pos:])
                        break

                    try:
                        _upload_start_frame(page, raw_png)
                        time.sleep(0.8)
                        _fill_prompt(page, sels.get("prompt_input", []), motion_prompt)
                        _assert_settings_pill_summary(
                            page,
                            cfg,
                            (cfg["video_mode"]["tab_label"], cfg["video_mode"]["batch_label"]),
                            scene_id=sid,
                            mode_tag="videos",
                        )
                        gen_btn, _ = _first_visible_locator(page, sels.get("generate_button", []), timeout_ms=4000)
                        if gen_btn is None:
                            raise RuntimeError("Không tìm thấy nút Tạo trên Google Flow. Hãy chạy `--mode probe`.")
                        if gen_btn.is_disabled():
                            time.sleep(2.5)
                        if gen_btn.is_disabled():
                            if submitted:
                                print(f"  [QUEUE] Nút Tạo đang khóa (đầy hàng đợi) → chuyển Cảnh {sid:03d}+ sang đợt sau.", flush=True)
                                deferred.extend(chunk[pos:])
                                break
                            raise RuntimeError("Nút Tạo bị vô hiệu hóa khi nộp cảnh video đầu tiên.")

                        gen_btn.click()
                        slot_uid = _wait_for_new_tile_slot(
                            page, sid, known_uids, preexisting_sigs, timeout_sec=slot_wait_sec
                        )
                        if slot_uid:
                            known_uids.add(slot_uid)
                        submitted.append({
                            "id": sid,
                            "item": item,
                            "image_prompt": image_prompt,
                            "motion_prompt": motion_prompt,
                            "raw_png": raw_png,
                            "tile_uid": slot_uid,
                        })
                        print(f"  -> Đã gửi lệnh video Cảnh {sid:03d} (slot={slot_uid or 'pending'})", flush=True)
                        if pos < len(chunk) - 1:
                            time.sleep(submit_gap)
                    except SafetyAssertionError:
                        raise
                    except Exception as exc:
                        failed_count += 1
                        shot = _save_debug_screenshot(page, f"error_scene_{sid:03d}_videos.png")
                        _update_scene_state(state, sid, video_status="error", error=str(exc))
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: LỖI khi gửi lệnh ({exc}) | Ảnh debug: {shot}", flush=True)

                if deferred:
                    queue = deferred + queue

                if not submitted:
                    continue

                # Giai đoạn B: Chờ toàn bộ đợt video render xong (KHÔNG bấm lại sidebar tab)
                try:
                    final_tiles, policy_msg = _wait_for_batch_tiles(
                        page,
                        submitted,
                        preexisting_sigs,
                        baseline_text,
                        cfg,
                        want_video=True,
                        timeout_sec=timeout_sec,
                    )
                except PolicyBlockedError as pe:
                    failed_count += len(submitted)
                    for sub in submitted:
                        sid = sub["id"]
                        _save_debug_screenshot(page, f"policy_scene_{sid:03d}_videos.png")
                        _update_scene_state(state, sid, video_status="policy_error", error=str(pe))
                    _save_pipeline_state_atomic(state_path, state)
                    print(f"  ✗ Cảnh {[s['id'] for s in submitted]}: BỊ CHẶN POLICY ({pe})", flush=True)
                    continue

                # Giai đoạn C: Khớp thẻ + tải bản gốc + xác minh khung hình đầu + FFmpeg mute
                matched, unmatched_ids = _match_tiles_to_batch_scenes(submitted, final_tiles)
                sub_by_id = {int(s["id"]): s for s in submitted}

                downloaded_tmp: Dict[int, Path] = {}
                scene_raw_pngs: Dict[int, Path] = {}

                for sid, tile in matched.items():
                    if tile.get("is_error"):
                        failed_count += 1
                        err_txt = tile.get("text") or policy_msg or "Google Flow từ chối tạo video (Policy)"
                        shot = _save_debug_screenshot(page, f"policy_scene_{sid:03d}_videos.png")
                        _update_scene_state(state, sid, video_status="policy_error", error=err_txt[:300])
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: BỊ CHẶN POLICY ({err_txt[:120]}) | Ảnh debug: {shot}", flush=True)
                        continue

                    tmp_mp4 = assets_dir / f"{sid:03d}.tmp.mp4"
                    try:
                        _download_tile_by_uid(
                            page,
                            str(tile.get("uid") or ""),
                            tmp_mp4,
                            is_video=True,
                            fallback_aria=str(tile.get("aria_label") or ""),
                        )
                        downloaded_tmp[sid] = tmp_mp4
                        scene_raw_pngs[sid] = sub_by_id[sid]["raw_png"]
                    except Exception as exc:
                        failed_count += 1
                        shot = _save_debug_screenshot(page, f"error_scene_{sid:03d}_videos.png")
                        _update_scene_state(state, sid, video_status="error", error=str(exc))
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: LỖI tải video ({exc}) | Ảnh debug: {shot}", flush=True)

                if len(downloaded_tmp) >= 2:
                    verified_map = _verify_and_assign_video_files_by_first_frame(
                        scene_raw_pngs,
                        list(downloaded_tmp.values()),
                        raw_dir,
                    )
                else:
                    verified_map = dict(downloaded_tmp)

                for sid, src_mp4 in verified_map.items():
                    dst_mp4 = assets_dir / f"{sid:03d}.mp4"
                    if downloaded_tmp.get(sid) != src_mp4:
                        print(
                            f"  [VERIFY] Đã tự hiệu chỉnh khớp khung hình đầu cho Cảnh {sid:03d} ({src_mp4.name})!",
                            flush=True,
                        )
                    try:
                        _strip_audio_ffmpeg(src_mp4, dst_mp4)
                        _update_scene_state(state, sid, video_status="done", error="")
                        _save_pipeline_state_atomic(state_path, state)
                        size_mb = dst_mp4.stat().st_size / (1024 * 1024)
                        print(f"  ✓ Cảnh {sid:03d}: Đã xuất assets/{sid:03d}.mp4 ({size_mb:.2f} MB, đã mute FFmpeg)", flush=True)
                    except Exception as exc:
                        failed_count += 1
                        _update_scene_state(state, sid, video_status="error", error=str(exc))
                        _save_pipeline_state_atomic(state_path, state)
                        print(f"  ✗ Cảnh {sid:03d}: LỖI FFmpeg ({exc})", flush=True)

                for tmp_f in downloaded_tmp.values():
                    tmp_f.unlink(missing_ok=True)

                for sid in unmatched_ids:
                    failed_count += 1
                    is_pol = bool(policy_msg)
                    st_name = "policy_error" if is_pol else "error"
                    msg = policy_msg or "Không khớp được thẻ video mới trên lưới (có thể bị Flow ẩn hoặc timeout)."
                    shot = _save_debug_screenshot(page, f"{'policy' if is_pol else 'error'}_scene_{sid:03d}_videos.png")
                    _update_scene_state(state, sid, video_status=st_name, error=msg)
                    _save_pipeline_state_atomic(state_path, state)
                    print(f"  ✗ Cảnh {sid:03d}: {st_name.upper()} ({msg}) | Ảnh debug: {shot}", flush=True)

                if queue:
                    time.sleep(delay_sec)
        finally:
            browser.close()

    return 1 if failed_count > 0 else 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bot Playwright điều khiển Google Flow qua Chrome CDP cho Pipeline V5."
    )
    parser.add_argument(
        "storyboard",
        type=Path,
        help="Đường dẫn tới file storyboard.json của tập phim.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=("probe", "images", "videos"),
        help="Chế độ chạy: probe (soi DOM), images (gen ảnh 0 tín dụng), videos (gen video 10 tín dụng).",
    )
    sel_group = parser.add_mutually_exclusive_group()
    sel_group.add_argument(
        "--scene",
        type=int,
        default=None,
        help="Chỉ chạy duy nhất 1 cảnh theo ID (vd: --scene 5).",
    )
    sel_group.add_argument(
        "--scenes",
        type=str,
        default=None,
        help="Chỉ chạy danh sách cảnh cách nhau bởi dấu phẩy hoặc dải (vd: --scenes 1,2,5 hoặc 3-6).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Giới hạn số cảnh chạy mới tối đa trong lần này (vd: --limit 2).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Số lệnh gửi song song mỗi đợt (mặc định đọc từ flow_selectors.json: 4 ảnh / 3 video; 1 = tuần tự).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ lập kế hoạch & ước tính tín dụng, KHÔNG mở Chrome, KHÔNG ghi file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bắt buộc tạo lại ngay cả khi file kết quả đã tồn tại.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bỏ qua câu hỏi xác nhận tín dụng khi chạy --mode videos nhiều hơn 1 cảnh.",
    )
    parser.add_argument(
        "--cdp-url",
        type=str,
        default=None,
        help="URL kết nối Chrome CDP (mặc định đọc từ flow_selectors.json: http://127.0.0.1:9222).",
    )
    parser.add_argument(
        "--selectors",
        type=Path,
        default=DEFAULT_SELECTORS_PATH,
        help="Đường dẫn tới file flow_selectors.json.",
    )
    parser.add_argument(
        "--frame-preset",
        type=str,
        choices=VALID_FRAME_PRESETS,
        default=None,
        help="Ghi đè frame_preset từ storyboard.json (portrait_9_16 | framed_4_5 | framed_1_1 | landscape_16_9).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    cfg = _load_selectors_config(args.selectors)
    cdp_url = args.cdp_url or cfg.get("cdp_url", "http://127.0.0.1:9222")

    # Chế độ probe: chỉ soi DOM, không đụng vào file tập phim
    if args.mode == "probe":
        if args.dry_run:
            print(f"[DRY-RUN] Chế độ probe sẽ kết nối tới {cdp_url} và xuất DOM ra {DEBUG_DIR / 'flow_probe_dump.json'}.")
            return 0
        return run_probe_mode(cdp_url, cfg)

    # 1. Tiền kiểm an toàn bằng validate_storyboard.py
    storyboard_path = args.storyboard.resolve()
    reports, data, stats = validate_storyboard(storyboard_path)
    total_errors, _ = print_report(storyboard_path, reports, stats, strict=False)
    if total_errors > 0 or data is None:
        print("\n[ABORT] Storyboard còn lỗi ERROR. Phải sửa PASS 100% trước khi chạy flow_bot.py!")
        return 1

    scenes = data.get("scenes", [])
    selected_ids = _parse_scene_ids(args.scene, args.scenes)
    if selected_ids is not None:
        valid_ids = {int(sc["id"]) for sc in scenes}
        unknown = sorted(selected_ids - valid_ids)
        if unknown:
            print(f"[ABORT] Các cảnh không tồn tại trong storyboard: {unknown}")
            return 1

    eff_fp = resolve_frame_preset(override=args.frame_preset, storyboard_preset=data.get("frame_preset"))

    # 2. Lập kế hoạch thực thi
    ep_dir = storyboard_path.parent
    plan = _build_execution_plan(
        ep_dir,
        data,
        args.mode,
        selected_ids=selected_ids,
        limit=args.limit,
        force=args.force,
    )
    run_count, total_credits = _print_plan_table(
        storyboard_path,
        args.mode,
        plan,
        dry_run=args.dry_run,
        frame_preset=eff_fp.name,
    )

    eff_conc = _resolve_concurrency(cfg, args.mode, args.concurrency)
    if args.dry_run:
        print(f"[DRY-RUN] Chế độ đồng loạt: {eff_conc} {args.mode}/đợt (dùng --concurrency để chỉnh; 1 = tuần tự).")
        return 0

    # 3. Xác nhận tiêu thụ tín dụng trước khi chạy --mode videos nhiều hơn 1 cảnh
    if args.mode == "videos" and run_count > 1 and not args.yes:
        print(
            f"\n⚠️  CẢNH BÁO TÍN DỤNG: Sắp chạy tạo {run_count} clip Veo 3.1 - Lite "
            f"(dự kiến tiêu thụ {total_credits} tín dụng)."
        )
        try:
            ans = input("Nhập 'y' để xác nhận chạy thật (hoặc nhấn Enter để hủy, dùng --yes để bỏ qua): ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("[CANCELLED] Đã hủy theo yêu cầu người dùng. Không tốn tín dụng.")
            return 0

    # 4. Đồng bộ/khởi tạo thư mục & pipeline_state.json (idempotent — giữ nguyên trạng thái done cũ)
    sync_episode_files(storyboard_path, data)

    # 5. Chạy thật trên Chrome CDP
    try:
        if args.mode == "images":
            return run_images_mode(
                storyboard_path,
                data,
                plan,
                cfg,
                cdp_url,
                concurrency=args.concurrency,
                frame_preset=eff_fp.name,
            )
        return run_videos_mode(
            storyboard_path,
            data,
            plan,
            cfg,
            cdp_url,
            concurrency=args.concurrency,
            frame_preset=eff_fp.name,
        )
    except SafetyAssertionError as se:
        print(f"\n🛑 {se}")
        return 2
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
