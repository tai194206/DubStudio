#!/usr/bin/env python3
"""Trình điều phối 1 chạm cho Pipeline Sản xuất Series Lịch sử V5.

Giao diện CLI:
    python automation/scripts/run_episode.py <episode_dir_or_storyboard_json> \
        --phase <check|images|videos|all> \
        [--batch-size 5] [--scenes 11,12,...] [--dry-run] [--yes] [--cdp-url URL]

Điều phối lại `validate_storyboard.py` và `flow_bot.py` qua subprocess (dùng đúng sys.executable).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Đảm bảo xuất tiếng Việt UTF-8 chuẩn trên Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import DEFAULT_VISUAL_PRESET, VALID_VISUAL_PRESETS  # noqa: E402
from core.frame_presets import (  # noqa: E402
    DEFAULT_FRAME_PRESET,
    VALID_FRAME_PRESETS,
    get_frame_preset,
    resolve_frame_preset,
)

VALIDATE_SCRIPT = SCRIPTS_DIR / "validate_storyboard.py"
FLOW_BOT_SCRIPT = SCRIPTS_DIR / "flow_bot.py"

MIN_IMAGE_BYTES = 10 * 1024      # 10 KB
MIN_JPG_BYTES = 10 * 1024        # 10 KB
MIN_VIDEO_BYTES = 100 * 1024     # 100 KB


def _resolve_storyboard_path(target: Path) -> Tuple[Path, Path]:
    """Chấp nhận cả đường dẫn thư mục tập lẫn đường dẫn trực tiếp tới storyboard.json."""
    resolved = target.resolve()
    if resolved.is_dir():
        sb = resolved / "storyboard.json"
        return resolved, sb
    return resolved.parent, resolved


def _parse_scenes_csv(scenes_arg: Optional[str]) -> Optional[Set[int]]:
    if not scenes_arg:
        return None
    out: Set[int] = set()
    for part in scenes_arg.split(","):
        tok = part.strip()
        if not tok:
            continue
        if "-" in tok:
            a_s, b_s = tok.split("-", 1)
            a, b = int(a_s.strip()), int(b_s.strip())
            if a > b:
                a, b = b, a
            out.update(range(a, b + 1))
        else:
            out.add(int(tok))
    return out if out else None


def _file_ok(path: Path, min_bytes: int) -> bool:
    return path.is_file() and path.stat().st_size > min_bytes


def _load_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _resolve_target_asset_filename(
    data: Dict[str, Any],
    scene_idx: int,
    sc: Dict[str, Any],
) -> str:
    """Trích xuất tên file đích trong assets/ dựa trên beats -> locations."""
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


def _run_subprocess(cmd: List[str]) -> int:
    res = subprocess.run(cmd)
    return res.returncode


def _run_validate(storyboard_path: Path, *, sync_files: bool) -> int:
    cmd = [sys.executable, str(VALIDATE_SCRIPT), str(storyboard_path)]
    if sync_files:
        cmd.append("--sync-files")
    return _run_subprocess(cmd)


def _read_policy_errors(ep_dir: Path, state_path: Optional[Path] = None) -> Tuple[List[int], List[int]]:
    """Trả về (image_policy_error_ids, video_policy_error_ids) từ pipeline_state*.json."""
    st = _load_json_safe(state_path or (ep_dir / "pipeline_state.json"))
    img_err: List[int] = []
    vid_err: List[int] = []
    if not st or not isinstance(st.get("scenes"), list):
        return img_err, vid_err
    for item in st["scenes"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        if item.get("image_status") == "policy_error":
            img_err.append(item["id"])
        if item.get("video_status") == "policy_error":
            vid_err.append(item["id"])
    return sorted(img_err), sorted(vid_err)


def _resolve_episode_io(
    ep_dir: Path,
    data: Dict[str, Any],
    *,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
) -> Tuple[Any, Path, Path, Path]:
    """Xác định frame_preset và thư mục song song (`assets/` vs `assets_16x9/`)."""
    def _sub(cand: Optional[Path], default_name: str) -> Path:
        if cand is None:
            return ep_dir / default_name
        return cand if cand.is_absolute() else (ep_dir / cand)

    if youtube_16x9:
        eff_fp = get_frame_preset("landscape_16_9")
        eff_assets = _sub(assets_dir, "assets_16x9")
        eff_raw = _sub(raw_dir, "raw_images_16x9")
        state_path = ep_dir / "pipeline_state_16x9.json"
        return eff_fp, eff_assets, eff_raw, state_path

    eff_fp = resolve_frame_preset(override=frame_preset, storyboard_preset=data.get("frame_preset"))
    eff_assets = _sub(assets_dir, "assets")
    eff_raw = _sub(raw_dir, "raw_images")
    state_path = ep_dir / "pipeline_state.json"
    return eff_fp, eff_assets, eff_raw, state_path


def _has_video_start_frame(sid: int, raw_dir: Path, assets_dir: Path, *, youtube_16x9: bool) -> bool:
    if youtube_16x9:
        for d, exts in ((assets_dir, (".jpg", ".jpeg", ".png", ".webp")), (raw_dir, (".png", ".jpg", ".jpeg", ".webp"))):
            for ext in exts:
                if _file_ok(d / f"{sid:03d}{ext}", MIN_IMAGE_BYTES):
                    return True
        return False
    return _file_ok(raw_dir / f"{sid:03d}.png", MIN_IMAGE_BYTES)


def inspect_episode_status(
    ep_dir: Path,
    data: Dict[str, Any],
    *,
    selected_ids: Optional[Set[int]] = None,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Quét toàn bộ hiện trạng assets*/raw_images*/pipeline_state*.json của tập phim."""
    eff_fp, eff_assets, eff_raw, state_path = _resolve_episode_io(
        ep_dir,
        data,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )
    scenes: List[Dict[str, Any]] = data.get("scenes", [])

    done_assets: List[int] = []
    missing_raw: List[int] = []
    missing_assets: List[int] = []
    pending_flow_videos: List[int] = []
    pending_static_images: List[int] = []
    ext_notices: List[str] = []

    for idx, sc in enumerate(scenes):
        sid = int(sc["id"])
        if selected_ids is not None and sid not in selected_ids:
            continue

        asset_type = str(sc.get("asset_type", "static_image"))
        target_name = _resolve_target_asset_filename(data, idx, sc)
        target_path = eff_assets / target_name
        raw_png = eff_raw / f"{sid:03d}.png"
        min_bytes = MIN_VIDEO_BYTES if asset_type == "flow_video" else MIN_JPG_BYTES

        has_final = _file_ok(target_path, min_bytes)
        has_raw = _file_ok(raw_png, MIN_IMAGE_BYTES)

        # Cảnh báo lệch đuôi thông minh (vd: JSON khai static_image .jpg nhưng assets/ lại có .mp4)
        if asset_type == "static_image":
            alt_mp4 = eff_assets / f"{sid:03d}.mp4"
            if _file_ok(alt_mp4, MIN_VIDEO_BYTES):
                sz_mb = alt_mp4.stat().st_size / (1024 * 1024)
                ext_notices.append(
                    f"[NOTICE] Cảnh {sid:03d}: storyboard khai báo 'static_image' ({target_name}), "
                    f"nhưng trong {eff_assets.name}/ đang có sẵn file video '{alt_mp4.name}' ({sz_mb:.2f} MB). "
                    f"Hãy kiểm tra lại nếu muốn giữ clip MP4 này hoặc đổi sang ảnh JPG."
                )
        elif asset_type == "flow_video" and not has_final:
            alt_jpg = eff_assets / f"{sid:03d}.jpg"
            if _file_ok(alt_jpg, MIN_JPG_BYTES):
                ext_notices.append(
                    f"[NOTICE] Cảnh {sid:03d}: storyboard khai báo 'flow_video' ({target_name}), "
                    f"hiện chưa có MP4 nhưng trong {eff_assets.name}/ lại có '{alt_jpg.name}'."
                )

        if has_final:
            done_assets.append(sid)
        else:
            missing_assets.append(sid)
            if not has_raw:
                missing_raw.append(sid)
            if asset_type == "flow_video":
                pending_flow_videos.append(sid)
            else:
                pending_static_images.append(sid)

    img_policy_err, vid_policy_err = _read_policy_errors(ep_dir, state_path=state_path)
    # Giọng đọc luôn dùng chung từ assets/full_audio.mp3 (fallback sang eff_assets)
    has_audio = False
    audio_name = ""
    for cand_dir in (ep_dir / "assets", eff_assets):
        for fname in ("full_audio.mp3", "full_audio.wav"):
            p = cand_dir / fname
            if p.is_file():
                has_audio = True
                audio_name = f"{cand_dir.name}/{fname}"
                break
        if has_audio:
            break

    total_checked = len(done_assets) + len(missing_assets)
    rem_credits = len(pending_flow_videos) * 10
    is_ready = (
        len(missing_assets) == 0
        and len(img_policy_err) == 0
        and len(vid_policy_err) == 0
        and has_audio
        and total_checked > 0
    )

    return {
        "frame_preset": eff_fp.name,
        "youtube_16x9": bool(youtube_16x9),
        "assets_dir_name": eff_assets.name,
        "raw_dir_name": eff_raw.name,
        "inner_size": f"{eff_fp.inner_w}x{eff_fp.inner_h}",
        "out_size": f"{eff_fp.out_w}x{eff_fp.out_h}",
        "total_scenes": total_checked,
        "done_assets": done_assets,
        "missing_raw": missing_raw,
        "missing_assets": missing_assets,
        "pending_flow_videos": pending_flow_videos,
        "pending_static_images": pending_static_images,
        "img_policy_errors": img_policy_err,
        "vid_policy_errors": vid_policy_err,
        "ext_notices": ext_notices,
        "has_audio": has_audio,
        "audio_name": audio_name,
        "remaining_credits": rem_credits,
        "status_label": "READY FOR DUBSTUDIO" if is_ready else "PENDING ASSETS",
    }


def print_check_summary(ep_dir: Path, info: Dict[str, Any]) -> None:
    assets_lbl = info.get("assets_dir_name", "assets")
    raw_lbl = info.get("raw_dir_name", "raw_images")
    print("\n" + "=" * 84)
    print(f"BÁO CÁO ĐỐI CHIẾU TÀI NGUYÊN TẬP: {ep_dir.name}")
    print(f"Thư mục: {ep_dir}")
    print("=" * 84)
    print(
        f"  Khung hình (frame_preset)   : {info.get('frame_preset', DEFAULT_FRAME_PRESET)} "
        f"(assets {info.get('inner_size', '1080x1920')} → xuất {info.get('out_size', '1080x1920')})"
    )
    print(f"  Tổng số cảnh kiểm tra        : {info['total_scenes']}")
    print(f"  Đã có thành phẩm ({assets_lbl}/): {len(info['done_assets'])}/{info['total_scenes']}  {info['done_assets'] if info['done_assets'] else '[]'}")
    print(f"  Còn thiếu thành phẩm cuối    : {len(info['missing_assets'])}  {info['missing_assets'] if info['missing_assets'] else '[]'}")
    print(f"    - flow_video chưa có .mp4  : {len(info['pending_flow_videos'])}  {info['pending_flow_videos'] if info['pending_flow_videos'] else '[]'}")
    print(f"    - static_image chưa có .jpg: {len(info['pending_static_images'])}  {info['pending_static_images'] if info['pending_static_images'] else '[]'}")
    print(f"  Còn thiếu ảnh gốc ({raw_lbl}): {len(info['missing_raw'])}  {info['missing_raw'] if info['missing_raw'] else '[]'}")
    print(f"  Lỗi Policy (ảnh / video)     : {info['img_policy_errors']} / {info['vid_policy_errors']}")
    if info["has_audio"]:
        print(f"  Âm thanh full_audio          : ✓ Đã có {info['audio_name']}")
    else:
        print("  Âm thanh full_audio          : ✗ CHƯA CÓ assets/full_audio.mp3 → hãy lấy elevenlabs.txt đi tạo voice!")
    print(f"  Tín dụng Veo 3.1 còn cần     : {info['remaining_credits']} tín dụng ({len(info['pending_flow_videos'])} clip x 10)")

    if info["ext_notices"]:
        print("\n" + "-" * 84)
        print(f"CẢNH BÁO LỆCH ĐUÔI FILE TRONG {assets_lbl}/:")
        for n in info["ext_notices"]:
            print(f"  {n}")

    print("\n" + "=" * 84)
    print(f"TRẠNG THÁI TẬP PHIM: {info['status_label']}")
    print("=" * 84)


def _append_io_flags(
    cmd: List[str],
    *,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
) -> None:
    if frame_preset:
        cmd.extend(["--frame-preset", frame_preset])
    if youtube_16x9:
        cmd.append("--youtube-16x9")
    if assets_dir is not None:
        cmd.extend(["--assets-dir", str(assets_dir)])
    if raw_dir is not None:
        cmd.extend(["--raw-dir", str(raw_dir)])


def run_phase_check(
    ep_dir: Path,
    storyboard_path: Path,
    *,
    selected_ids: Optional[Set[int]],
    dry_run: bool,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    rc = _run_validate(storyboard_path, sync_files=False)
    if rc != 0:
        print("\n[ABORT] validate_storyboard.py phát hiện ERROR. Dừng kiểm tra.")
        return rc, None

    data = _load_json_safe(storyboard_path)
    if data is None:
        print(f"\n[ABORT] Không đọc được JSON: {storyboard_path}")
        return 1, None

    info = inspect_episode_status(
        ep_dir,
        data,
        selected_ids=selected_ids,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )
    print_check_summary(ep_dir, info)
    return 0, info


def run_phase_images(
    ep_dir: Path,
    storyboard_path: Path,
    *,
    scenes_arg: Optional[str],
    dry_run: bool,
    cdp_url: Optional[str],
    concurrency: Optional[int] = None,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    archival_photo: bool = True,
    visual_preset: Optional[str] = None,
) -> int:
    # Khi chạy --youtube-16x9: chỉ validate read-only để không chạm assets/ hay pipeline_state.json của TikTok
    use_sync = (not dry_run) and (not youtube_16x9) and (assets_dir is None) and (raw_dir is None)
    rc_val = _run_validate(storyboard_path, sync_files=use_sync)
    if rc_val != 0:
        print("\n[ABORT] Storyboard chưa PASS kiểm định. Không chạy phase images.")
        return rc_val

    eff_archival = bool(archival_photo) and (str(visual_preset or "").strip().lower() != "raw")
    cmd = [sys.executable, str(FLOW_BOT_SCRIPT), str(storyboard_path), "--mode", "images"]
    if scenes_arg:
        cmd.extend(["--scenes", scenes_arg])
    if dry_run:
        cmd.append("--dry-run")
    if eff_archival:
        cmd.append("--archival-photo")
    else:
        cmd.append("--no-archival-photo")
    if visual_preset:
        cmd.extend(["--visual-preset", str(visual_preset)])
    if cdp_url:
        cmd.extend(["--cdp-url", cdp_url])
    if concurrency is not None:
        cmd.extend(["--concurrency", str(concurrency)])
    _append_io_flags(
        cmd,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )

    rc_bot = _run_subprocess(cmd)

    data = _load_json_safe(storyboard_path)
    if data is not None:
        info = inspect_episode_status(
            ep_dir,
            data,
            selected_ids=_parse_scenes_csv(scenes_arg),
            frame_preset=frame_preset,
            youtube_16x9=youtube_16x9,
            assets_dir=assets_dir,
            raw_dir=raw_dir,
        )
        done_img = info["total_scenes"] - len(info["missing_raw"])
        raw_lbl = info.get("raw_dir_name", "raw_images")
        print("\n" + "-" * 84)
        print(
            f"[TÓM TẮT PHASE IMAGES] Đã sẵn sàng ảnh/thành phẩm: {done_img}/{info['total_scenes']} | "
            f"Còn thiếu ảnh gốc ({raw_lbl}/): {len(info['missing_raw'])} | "
            f"Policy error (ảnh): {info['img_policy_errors']}"
        )
        if not dry_run:
            print(f"Gợi ý: Hãy duyệt nhanh thư mục {raw_lbl}/ (xóa ảnh xấu nếu có rồi chạy lại --phase images) trước khi sang --phase videos.")
        print("-" * 84)
    return rc_bot


def _chunk_list(items: List[int], batch_size: int) -> List[List[int]]:
    size = max(1, batch_size)
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_phase_videos(
    ep_dir: Path,
    storyboard_path: Path,
    *,
    batch_size: int,
    scenes_arg: Optional[str],
    dry_run: bool,
    yes: bool,
    cdp_url: Optional[str],
    concurrency: Optional[int] = None,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
) -> int:
    rc_val = _run_validate(storyboard_path, sync_files=False)
    if rc_val != 0:
        print("\n[ABORT] Storyboard chưa PASS kiểm định. Không chạy phase videos.")
        return rc_val

    data = _load_json_safe(storyboard_path)
    if data is None:
        return 1

    selected_ids = _parse_scenes_csv(scenes_arg)
    info = inspect_episode_status(
        ep_dir,
        data,
        selected_ids=selected_ids,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )
    _, eff_assets, eff_raw, _ = _resolve_episode_io(
        ep_dir,
        data,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )
    pending_vids: List[int] = info["pending_flow_videos"]

    if dry_run:
        cmd = [sys.executable, str(FLOW_BOT_SCRIPT), str(storyboard_path), "--mode", "videos", "--dry-run"]
        if scenes_arg:
            cmd.extend(["--scenes", scenes_arg])
        if cdp_url:
            cmd.extend(["--cdp-url", cdp_url])
        if concurrency is not None:
            cmd.extend(["--concurrency", str(concurrency)])
        _append_io_flags(
            cmd,
            frame_preset=frame_preset,
            youtube_16x9=youtube_16x9,
            assets_dir=assets_dir,
            raw_dir=raw_dir,
        )
        _run_subprocess(cmd)

        ready_for_video = [
            sid for sid in pending_vids if _has_video_start_frame(sid, eff_raw, eff_assets, youtube_16x9=youtube_16x9)
        ]
        missing_raw_for_video = [sid for sid in pending_vids if sid not in ready_for_video]
        batches_planned = _chunk_list(pending_vids, batch_size)

        print("\n" + "=" * 84)
        print(f"KẾ HOẠCH CHIA MẺ PHASE VIDEOS [DRY-RUN] (batch_size={batch_size})")
        print("=" * 84)
        print(f"  Cảnh flow_video đã có {eff_assets.name}/*.mp4 (bảo vệ): {len(info['done_assets'])} cảnh xong toàn tập")
        print(f"  Cảnh flow_video chưa có .mp4                : {len(pending_vids)} cảnh {pending_vids}")
        print(f"    - Đã đủ ảnh khung đầu (chạy được ngay)    : {len(ready_for_video)} cảnh {ready_for_video}")
        print(f"    - Thiếu ảnh khung đầu (cần gen ảnh)       : {len(missing_raw_for_video)} cảnh {missing_raw_for_video}")
        print(f"  Dự kiến chia {len(batches_planned)} mẻ (sau khi đủ ảnh gốc):")
        for b_idx, batch in enumerate(batches_planned, start=1):
            csv_str = ",".join(str(x) for x in batch)
            print(f"    Mẻ {b_idx}: --scenes {csv_str}  ({len(batch)} clip = {len(batch) * 10} tín dụng)")
        print(f"  TỔNG TÍN DỤNG VEO 3.1 DỰ KIẾN: {len(pending_vids) * 10} tín dụng")
        print("=" * 84)
        return 0

    if not pending_vids:
        print(f"\n[VIDEOS] Tất cả các cảnh flow_video được chọn đã có thành phẩm trong {eff_assets.name}/. Không cần chạy thêm!")
        print_check_summary(ep_dir, info)
        return 0

    missing_raw_vids = [
        sid for sid in pending_vids if not _has_video_start_frame(sid, eff_raw, eff_assets, youtube_16x9=youtube_16x9)
    ]
    if missing_raw_vids:
        print(f"\n[ABORT] Thiếu ảnh khung đầu trong {eff_raw.name}/ (hoặc {eff_assets.name}/) cho các cảnh flow_video sau — DỪNG SỚM:")
        print(f"  Các cảnh thiếu ảnh khung đầu: {missing_raw_vids}")
        print("  Hãy chạy `--phase images` trước để tạo đủ ảnh gốc (0 tín dụng).")
        return 1

    batches = _chunk_list(pending_vids, batch_size)
    total_credits = len(pending_vids) * 10

    print("\n" + "=" * 84)
    print(f"CHUẨN BỊ CHẠY PHASE VIDEOS: {len(pending_vids)} clip ({total_credits} tín dụng) | Chia {len(batches)} mẻ")
    for b_idx, batch in enumerate(batches, start=1):
        print(f"  Mẻ {b_idx}/{len(batches)}: cảnh {batch} ({len(batch) * 10} tín dụng)")
    print("=" * 84)

    if not yes:
        try:
            ans = input(
                f"Xác nhận tiêu thụ tối đa {total_credits} tín dụng cho {len(pending_vids)} clip? "
                f"Nhập 'y' để chạy (Enter để hủy): "
            ).strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("[CANCELLED] Đã hủy chạy phase videos. Không tốn tín dụng.")
            return 0

    had_error = False
    for b_idx, batch in enumerate(batches, start=1):
        csv_str = ",".join(str(x) for x in batch)
        print(f"\n>>> [MẺ {b_idx}/{len(batches)}] Đang chạy flow_bot.py --mode videos --scenes {csv_str} ...")
        cmd = [
            sys.executable,
            str(FLOW_BOT_SCRIPT),
            str(storyboard_path),
            "--mode", "videos",
            "--scenes", csv_str,
            "--yes",
        ]
        if cdp_url:
            cmd.extend(["--cdp-url", cdp_url])
        if concurrency is not None:
            cmd.extend(["--concurrency", str(concurrency)])
        _append_io_flags(
            cmd,
            frame_preset=frame_preset,
            youtube_16x9=youtube_16x9,
            assets_dir=assets_dir,
            raw_dir=raw_dir,
        )
        rc = _run_subprocess(cmd)
        if rc == 2:
            print("\n🛑 [DỪNG KHẨN CẤP] flow_bot.py phát hiện sai cấu hình tín dụng (SafetyAssertionError)!")
            return 2
        if rc != 0:
            had_error = True
            print(f"[WARN] Mẻ {b_idx} có cảnh lỗi / policy_error (exit code {rc}).")

    data_after = _load_json_safe(storyboard_path) or data
    info_after = inspect_episode_status(
        ep_dir,
        data_after,
        selected_ids=selected_ids,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )
    print_check_summary(ep_dir, info_after)
    if info_after["vid_policy_errors"]:
        print(
            f"[POLICY] Phát hiện cảnh video bị chặn Policy: {info_after['vid_policy_errors']}. "
            f"Hãy chạy workflow `.agent/workflows/v5_heal_policy.md` để viết mềm motion_prompt rồi chạy bù."
        )
    return 1 if had_error else 0


def run_phase_all(
    ep_dir: Path,
    storyboard_path: Path,
    *,
    batch_size: int,
    scenes_arg: Optional[str],
    dry_run: bool,
    yes: bool,
    cdp_url: Optional[str],
    concurrency: Optional[int] = None,
    frame_preset: Optional[str] = None,
    youtube_16x9: bool = False,
    assets_dir: Optional[Path] = None,
    raw_dir: Optional[Path] = None,
    archival_photo: bool = True,
    visual_preset: Optional[str] = None,
) -> int:
    rc_img = run_phase_images(
        ep_dir,
        storyboard_path,
        scenes_arg=scenes_arg,
        dry_run=dry_run,
        cdp_url=cdp_url,
        concurrency=concurrency,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
        archival_photo=archival_photo,
        visual_preset=visual_preset,
    )
    if rc_img == 2:
        return 2

    if dry_run:
        return run_phase_videos(
            ep_dir,
            storyboard_path,
            batch_size=batch_size,
            scenes_arg=scenes_arg,
            dry_run=True,
            yes=yes,
            cdp_url=cdp_url,
            concurrency=concurrency,
            frame_preset=frame_preset,
            youtube_16x9=youtube_16x9,
            assets_dir=assets_dir,
            raw_dir=raw_dir,
        )

    state_file = ep_dir / ("pipeline_state_16x9.json" if youtube_16x9 else "pipeline_state.json")
    img_policy_err, _ = _read_policy_errors(ep_dir, state_path=state_file)
    if img_policy_err:
        print("\n" + "=" * 84)
        print(f"[DỪNG GIỮA PHASE ALL] Phát hiện {len(img_policy_err)} cảnh ảnh bị lỗi Policy: {img_policy_err}")
        print("Hãy chạy workflow `.agent/workflows/v5_heal_policy.md` để sửa prompt ảnh trước khi tốn tín dụng video!")
        print("=" * 84)
        return 1

    if rc_img != 0:
        print("\n[DỪNG GIỮA PHASE ALL] Phase images kết thúc có lỗi kỹ thuật. Hãy xử lý xong ảnh rồi chạy tiếp.")
        return rc_img

    raw_lbl = raw_dir.name if raw_dir else ("raw_images_16x9" if youtube_16x9 else "raw_images")
    if not yes:
        try:
            ans = input(
                f"\nĐã xong toàn bộ ảnh gốc (0 tín dụng). Hãy duyệt nhanh thư mục `{raw_lbl}/` "
                "(xóa ảnh lỗi nếu có rồi chạy lại `--phase images`). "
                "Nhấn Enter để tiếp tục chạy Video, hoặc gõ `q` để dừng: "
            ).strip().lower()
        except EOFError:
            ans = "q"
        if ans == "q":
            print("[STOPPED] Đã dừng sau phase images theo yêu cầu người dùng.")
            return 0

    return run_phase_videos(
        ep_dir,
        storyboard_path,
        batch_size=batch_size,
        scenes_arg=scenes_arg,
        dry_run=False,
        yes=yes,
        cdp_url=cdp_url,
        concurrency=concurrency,
        frame_preset=frame_preset,
        youtube_16x9=youtube_16x9,
        assets_dir=assets_dir,
        raw_dir=raw_dir,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Trình điều phối 1 chạm Pipeline Sản xuất Series Lịch sử V5."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Đường dẫn thư mục tập phim HOẶC file storyboard.json.",
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=("check", "images", "videos", "all"),
        help="Giai đoạn thực thi: check | images | videos | all.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Số cảnh video tối đa mỗi mẻ trong --phase videos (mặc định: 5).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Số lệnh gửi song song mỗi đợt (mặc định lấy từ flow_selectors.json: 4 ảnh / 3 video; 1 = tuần tự).",
    )
    parser.add_argument(
        "--scenes",
        type=str,
        default=None,
        help="Chỉ định danh sách cảnh (vd: --scenes 11,12,15 hoặc 11-26).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chế độ chạy thử chỉ đọc: không mở Chrome, không ghi file, 0 tín dụng.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Bỏ qua bước hỏi xác nhận tương tác.",
    )
    parser.add_argument(
        "--cdp-url",
        type=str,
        default=None,
        help="URL kết nối Chrome CDP (mặc định dùng flow_selectors.json).",
    )
    parser.add_argument(
        "--frame-preset",
        type=str,
        choices=VALID_FRAME_PRESETS,
        default=None,
        help="Ghi đè frame_preset từ storyboard.json (portrait_9_16 | framed_4_5 | framed_1_1 | landscape_16_9).",
    )
    parser.add_argument(
        "--youtube-16x9",
        action="store_true",
        help="Sản xuất song song bản YouTube 16:9 vào raw_images_16x9/ & assets_16x9/ (không đụng assets/ TikTok).",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=None,
        help="Thư mục đầu ra tùy chỉnh cho ảnh/video thành phẩm (vd: assets_16x9).",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=None,
        help="Thư mục ảnh gốc tùy chỉnh (vd: raw_images_16x9).",
    )
    parser.add_argument(
        "--archival-photo",
        dest="archival_photo",
        action="store_true",
        default=True,
        help="Mặc định BẬT (V5.6 restored_doc_still): áp chuẩn ảnh tư liệu phục dựng sắc nét có màu trầm & ánh sáng chọn lọc (dùng --no-archival-photo hoặc --visual-preset raw để tắt).",
    )
    parser.add_argument(
        "--no-archival-photo",
        dest="archival_photo",
        action="store_false",
        help="Tắt bộ lọc ảnh tư liệu phục dựng V5.6 toàn cục (giữ nguyên prompt gốc).",
    )
    parser.add_argument(
        "--visual-preset",
        choices=VALID_VISUAL_PRESETS,
        default=None,
        help=f"Chọn preset hình ảnh toàn cục (mặc định: {DEFAULT_VISUAL_PRESET}; chọn 'raw' để tắt tiêm hậu tố V5.6).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    ep_dir, storyboard_path = _resolve_storyboard_path(args.target)
    if not storyboard_path.is_file():
        print(f"[ERROR] Không tìm thấy file storyboard.json tại: {storyboard_path}")
        return 1

    selected_ids = _parse_scenes_csv(args.scenes)
    eff_frame_preset = "landscape_16_9" if args.youtube_16x9 else args.frame_preset

    if args.phase == "check":
        rc, _ = run_phase_check(
            ep_dir,
            storyboard_path,
            selected_ids=selected_ids,
            dry_run=args.dry_run,
            frame_preset=eff_frame_preset,
            youtube_16x9=bool(args.youtube_16x9),
            assets_dir=args.assets_dir,
            raw_dir=args.raw_dir,
        )
        return rc

    if args.phase == "images":
        return run_phase_images(
            ep_dir,
            storyboard_path,
            scenes_arg=args.scenes,
            dry_run=args.dry_run,
            cdp_url=args.cdp_url,
            concurrency=args.concurrency,
            frame_preset=eff_frame_preset,
            youtube_16x9=bool(args.youtube_16x9),
            assets_dir=args.assets_dir,
            raw_dir=args.raw_dir,
            archival_photo=bool(args.archival_photo) and (args.visual_preset != "raw"),
            visual_preset=args.visual_preset,
        )

    if args.phase == "videos":
        return run_phase_videos(
            ep_dir,
            storyboard_path,
            batch_size=args.batch_size,
            scenes_arg=args.scenes,
            dry_run=args.dry_run,
            yes=args.yes,
            cdp_url=args.cdp_url,
            concurrency=args.concurrency,
            frame_preset=eff_frame_preset,
            youtube_16x9=bool(args.youtube_16x9),
            assets_dir=args.assets_dir,
            raw_dir=args.raw_dir,
        )

    return run_phase_all(
        ep_dir,
        storyboard_path,
        batch_size=args.batch_size,
        scenes_arg=args.scenes,
        dry_run=args.dry_run,
        yes=args.yes,
        cdp_url=args.cdp_url,
        concurrency=args.concurrency,
        frame_preset=eff_frame_preset,
        youtube_16x9=bool(args.youtube_16x9),
        assets_dir=args.assets_dir,
        raw_dir=args.raw_dir,
        archival_photo=bool(args.archival_photo) and (args.visual_preset != "raw"),
        visual_preset=args.visual_preset,
    )


if __name__ == "__main__":
    raise SystemExit(main())
