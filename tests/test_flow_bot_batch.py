"""Unit test cho nâng cấp Batch Pipelining & chống treo Google Flow (automation/scripts/flow_bot.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_SCRIPTS = REPO_ROOT / "automation" / "scripts"
if str(AUTOMATION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_SCRIPTS))

import flow_bot  # noqa: E402
from core.ffmpeg_utils import run_ffmpeg  # noqa: E402


def test_text_overlap_score_matches_flow_aria_labels():
    """Bộ 8 aria-label thật từ Google Flow phải khớp đúng 8/8 prompt tương ứng của chính nó."""
    samples = [
        (
            1,
            "Artillery crew holding still",
            "Close-up of stunned Viet Minh artillery soldiers beside a camouflaged 105mm howitzer. "
            "Slow dramatic push-in toward the shocked artillery crew holding completely still in heavy dawn mist.",
        ),
        (
            6,
            "Soldiers pulling ropes together",
            "Mud-stained soldiers gripping thick hemp ropes guiding a heavy 105mm artillery piece. "
            "Steady lateral tracking pan alongside determined soldiers gripping hemp ropes together.",
        ),
        (
            10,
            "Paper sheets fluttering over mou…",
            "Hundreds of blank white paper sheets fluttering slowly down above misty green Northwest mountain jungle. "
            "Slow gentle upward zoom-out as blank white paper sheets flutter softly across misty mountain ridges.",
        ),
        (
            14,
            "Signal flag sweeps downward",
            "Resolute officer holding a crimson signal flag beside a steel cannon breech. "
            "Crisp suspenseful push-in as the crimson signal flag sweeps decisively downward.",
        ),
        (
            15,
            "Smoke rising from mountain slopes",
            "Steep dark ridges lit by bright amber light flashes, plumes of white smoke drifting. "
            "Dramatic slow zoom-out revealing flashes across the dark mountain slopes as rising plumes of white smoke drift.",
        ),
        (
            20,
            "Figures climbing uphill through …",
            "Silhouetted figures advancing steadily through misty smoke climbing out of forward trenches. "
            "Dynamic forward tracking movement climbing uphill alongside silhouetted figures advancing through misty smoke.",
        ),
        (
            25,
            "Flag waving over misty valley",
            "Dawn vista atop ridge while a red flag waves proudly over misty valley mist. "
            "Slow majestic zoom-out from the red-and-gold flag waving strongly over the misty valley horizon.",
        ),
        (
            26,
            "Dark silhouette of fortified hill",
            "Ominous landscape looking toward a steep fortified red-clay hill silhouetted against crimson dusk clouds. "
            "Slow dramatic push-in toward the ominous dark silhouette of the fortified hill.",
        ),
    ]

    for target_sid, aria_lbl, _ in samples:
        scores = {
            sid: flow_bot._text_overlap_score(aria_lbl, prompt)
            for sid, _, prompt in samples
        }
        best_sid = max(scores, key=lambda k: scores[k])
        assert best_sid == target_sid, f"aria_label {aria_lbl!r} khớp nhầm vào {best_sid}: {scores}"
        assert scores[target_sid] >= 0.70


def test_match_tiles_by_dom_tag_exact():
    """Tầng 1: Khi thẻ có data-flowbot-scene-id (scene_tag), khớp 100% bất kể thứ tự lưới."""
    submitted = [
        {"id": 10, "prompt": "Paper sheets fluttering over mountains"},
        {"id": 14, "prompt": "Crimson signal flag sweeps downward"},
        {"id": 15, "prompt": "Smoke rising from mountain slopes"},
    ]
    # Lưới cố tình đảo thứ tự & không có aria-label rõ ràng
    tiles = [
        {"uid": "fb_tile_3", "scene_tag": 14, "aria_label": "", "x": 248, "y": 76},
        {"uid": "fb_tile_1", "scene_tag": 10, "aria_label": "", "x": 414, "y": 76},
        {"uid": "fb_tile_2", "scene_tag": 15, "aria_label": "", "x": 581, "y": 76},
    ]

    matched, unmatched = flow_bot._match_tiles_to_batch_scenes(submitted, tiles)
    assert unmatched == []
    assert matched[10]["uid"] == "fb_tile_1"
    assert matched[14]["uid"] == "fb_tile_3"
    assert matched[15]["uid"] == "fb_tile_2"


def test_match_tiles_fallback_order_and_shuffled_aria():
    """Tầng 2a: Mất DOM tag -> mặc định thứ tự ngược chuẩn Flow; nếu bị xáo trộn thì tự sửa theo aria-label."""
    # Trường hợp A: Không có aria_label & mất scene_tag -> map đúng thứ tự ngược [newest..oldest]
    submitted_plain = [
        {"id": 1, "prompt": "Prompt A"},
        {"id": 2, "prompt": "Prompt B"},
        {"id": 3, "prompt": "Prompt C"},
    ]
    tiles_plain = [
        {"uid": "t_newest", "scene_tag": None, "aria_label": "", "x": 248, "y": 76},
        {"uid": "t_mid", "scene_tag": None, "aria_label": "", "x": 414, "y": 76},
        {"uid": "t_oldest", "scene_tag": None, "aria_label": "", "x": 581, "y": 76},
    ]
    matched_a, unmatched_a = flow_bot._match_tiles_to_batch_scenes(submitted_plain, tiles_plain)
    assert unmatched_a == []
    assert matched_a[3]["uid"] == "t_newest"
    assert matched_a[2]["uid"] == "t_mid"
    assert matched_a[1]["uid"] == "t_oldest"

    # Trường hợp B: Angular re-mount làm mất scene_tag + thứ tự render xong bị tráo ngược
    submitted_sem = [
        {"id": 10, "prompt": "White paper sheets fluttering down across misty mountain ridges"},
        {"id": 14, "prompt": "Crimson signal flag sweeps downward beside artillery breech"},
        {"id": 15, "prompt": "Amber flashes and rising white smoke across steep mountain slopes"},
    ]
    tiles_shuffled = [
        {"uid": "u10", "scene_tag": None, "aria_label": "Paper sheets fluttering over mou…", "x": 248, "y": 76},
        {"uid": "u15", "scene_tag": None, "aria_label": "Smoke rising from mountain slopes", "x": 414, "y": 76},
        {"uid": "u14", "scene_tag": None, "aria_label": "Signal flag sweeps downward", "x": 581, "y": 76},
    ]
    matched_b, unmatched_b = flow_bot._match_tiles_to_batch_scenes(submitted_sem, tiles_shuffled)
    assert unmatched_b == []
    assert matched_b[10]["uid"] == "u10"
    assert matched_b[14]["uid"] == "u14"
    assert matched_b[15]["uid"] == "u15"


def test_match_tiles_partial_when_policy_drops_tile():
    """Tầng 2b: Khi Flow xóa mất 1 thẻ do Policy (nộp 3 nhưng chỉ còn 2), không gán nhầm thẻ của cảnh khác."""
    submitted = [
        {"id": 10, "prompt": "White paper sheets fluttering down across misty mountain ridges"},
        {"id": 14, "prompt": "Crimson signal flag sweeps downward beside artillery breech"},
        {"id": 15, "prompt": "Amber flashes and rising white smoke across steep mountain slopes"},
    ]
    # Giả sử Cảnh 14 bị xóa hẳn khỏi DOM, chỉ còn thẻ của 15 và 10
    tiles = [
        {"uid": "t15", "scene_tag": None, "aria_label": "Smoke rising from mountain slopes", "x": 248, "y": 76},
        {"uid": "t10", "scene_tag": None, "aria_label": "Paper sheets fluttering over mou…", "x": 414, "y": 76},
    ]

    matched, unmatched = flow_bot._match_tiles_to_batch_scenes(submitted, tiles)
    assert matched[10]["uid"] == "t10"
    assert matched[15]["uid"] == "t15"
    assert 14 not in matched
    assert unmatched == [14]


def test_verify_videos_by_first_frame_swapped_order(tmp_path):
    """So khớp vật lý khung hình đầu video với raw_images/*.png phải sửa đúng 100% kể cả khi bị tráo tên."""
    raw_dir = tmp_path / "raw_images"
    dl_dir = tmp_path / "dl"
    raw_dir.mkdir()
    dl_dir.mkdir()

    # 3 ảnh gốc màu rất khác biệt: 1=Đỏ, 2=Xanh lá, 3=Xanh dương
    colors = {1: "0xFF1010", 2: "0x10FF10", 3: "0x1010FF"}
    scene_raws = {}
    for sid, col in colors.items():
        png = raw_dir / f"{sid:03d}.png"
        run_ffmpeg(["-f", "lavfi", "-i", f"color=c={col}:s=180x320:d=0.5", "-vframes", "1", str(png)])
        scene_raws[sid] = png

    # Cố tình đặt tên file tải về bị đảo vòng (clip Đỏ mang tên swap_a, Xanh lá -> swap_b, Xanh dương -> swap_c,
    # nhưng map vào video_paths với thứ tự sai so với ID)
    vid_blue = dl_dir / "vid_x.tmp.mp4"
    vid_red = dl_dir / "vid_y.tmp.mp4"
    vid_green = dl_dir / "vid_z.tmp.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c={colors[3]}:s=180x320:r=15:d=0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(vid_blue)])
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c={colors[1]}:s=180x320:r=15:d=0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(vid_red)])
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c={colors[2]}:s=180x320:r=15:d=0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(vid_green)])

    assigned = flow_bot._verify_and_assign_video_files_by_first_frame(
        scene_raws,
        [vid_blue, vid_red, vid_green],
        tmp_path,
    )
    assert assigned[1] == vid_red
    assert assigned[2] == vid_green
    assert assigned[3] == vid_blue


def test_cli_dry_run_with_concurrency():
    """CLI --dry-run --concurrency trên Tập 3 hoàn thành gọn, giữ nguyên SKIP_EXISTING_ASSET (0 tín dụng)."""
    tap3_dir = REPO_ROOT / "story_mode" / "projects" / "Dien_Bien_Phu_1954" / "Dien_Bien_Phu_Tap_03"
    tap3_sb = tap3_dir / "storyboard.json"
    if not tap3_sb.is_file():
        pytest.skip("Chưa có Dien_Bien_Phu_Tap_03/storyboard.json")

    res_bot = subprocess.run(
        [sys.executable, str(AUTOMATION_SCRIPTS / "flow_bot.py"), str(tap3_sb), "--mode", "images", "--dry-run", "--concurrency", "4"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res_bot.returncode == 0, res_bot.stderr
    assert "SKIP_EXISTING_ASSET" in res_bot.stdout
    assert "4 images/đợt" in res_bot.stdout

    res_ep = subprocess.run(
        [sys.executable, str(AUTOMATION_SCRIPTS / "run_episode.py"), str(tap3_dir), "--phase", "all", "--dry-run", "--concurrency", "3"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert res_ep.returncode == 0, res_ep.stderr
    assert "TỔNG TÍN DỤNG DỰ KIẾN TIÊU THỤ: 0 tín dụng" in res_ep.stdout
    assert "3 videos/đợt" in res_ep.stdout


def test_archival_photo_default_and_scene_override():
    """Kiểm tra mặc định bật V5.6 restored_doc_still toàn cục (era-agnostic) và ghi đè `archival_photo: False`."""
    diagram_sc = {
        "id": 1,
        "archival_photo": False,
        "image_prompt": "Clean geological hillside cross-section diagram showing tunnel beneath ridge, vertical 9:16, no text",
    }
    battlefield_sc = {
        "id": 2,
        "image_prompt": "Infantry soldiers advancing along a misty 1979 mountain outpost at dawn, vertical 9:16, no text",
    }
    daylight_sc = {
        "id": 3,
        "image_prompt": "Field artillery crew inside an earthen bunker checking brass watch light, vertical 9:16, no text",
    }

    assert flow_bot.should_apply_archival_photo(diagram_sc, global_archival_photo=True) is False
    assert flow_bot.should_apply_archival_photo(battlefield_sc, global_archival_photo=True) is True
    assert flow_bot.should_apply_archival_photo(diagram_sc, global_archival_photo=False) is False
    assert flow_bot.should_apply_archival_photo(battlefield_sc, global_archival_photo=False) is False

    # Cùng 1 lượt chạy mặc định restored_doc_still (V5.8 Unified House Look): sơ đồ giữ nguyên, chiến trường/ban ngày cùng tông cold slate-charcoal, không dính 1954 hay torchlight
    p_diag = flow_bot.prepare_image_prompt_for_scene(diagram_sc, "landscape_16_9").lower()
    p_bat = flow_bot.prepare_image_prompt_for_scene(battlefield_sc, "landscape_16_9").lower()
    p_day = flow_bot.prepare_image_prompt_for_scene(daylight_sc, "landscape_16_9").lower()
    assert "16:9 widescreen" in p_diag
    assert "low-key dark archival" not in p_diag
    assert "low-key dark archival war-correspondent documentary still with heavy bleach-bypass look" in p_bat
    assert "unified deep dark slate-charcoal near-monochrome shadows" in p_bat
    assert "heavy smoke-darkened overcast sky with desaturated cold slate-blue pre-dawn mist in the background, no bright white sky" in p_bat
    assert "no planted torches unless explicitly requested" in p_bat
    assert "torchlight" not in p_bat
    assert "low-key dark archival documentary still with heavy silver-halide bleach-bypass look" in p_day
    assert "unified cold slate-charcoal near-monochrome shadows across uniforms, stone, foliage, skin and mud" in p_day
    assert "heavy smoke-darkened overcast sky with cool desaturated slate-blue atmospheric mist, no bright white sky" in p_day
    assert "never selective yellow tint painted onto props or gear" in p_day
    assert "1954" not in p_bat and "1954" not in p_day

    # Tắt toàn cục (--no-archival-photo hoặc visual_preset="raw"): không cảnh nào bị tiêm hậu tố V5.8
    p_bat_off = flow_bot.prepare_image_prompt_for_scene(battlefield_sc, "landscape_16_9", archival_photo=False).lower()
    p_bat_raw = flow_bot.prepare_image_prompt_for_scene(battlefield_sc, "landscape_16_9", visual_preset="raw").lower()
    assert "low-key dark archival" not in p_bat_off
    assert "low-key dark archival" not in p_bat_raw


def test_process_flow_downloaded_image_auto_crop_and_archival_guard(tmp_path):
    """Auto-crop 1.04x & House Look V5.8 chỉ chạy cho ảnh AI; ảnh thuộc archive_sources.json giữ nguyên từng pixel."""
    from PIL import Image
    import numpy as np
    from core.house_look import measure_house_look_profile

    dl_file = tmp_path / "dl.png"
    rng = np.random.default_rng(12345)
    arr = rng.integers(85, 135, size=(600, 1000, 3), dtype=np.uint8)
    Image.fromarray(arr, mode="RGB").save(dl_file, format="PNG")
    assert dl_file.stat().st_size > 10 * 1024

    # Cảnh AI (id=2 không thuộc archival_ids) -> crop 1.04x (1000x600 -> 962x577) + House Look V5.8 LAB match
    ai_png = tmp_path / "raw_images" / "002.png"
    ai_jpg = tmp_path / "assets" / "002.jpg"
    out_w, out_h = flow_bot.process_flow_downloaded_image(
        dl_file,
        ai_png,
        dst_jpg=ai_jpg,
        frame_preset="landscape_16_9",
        scene_id=2,
        archival_scene_ids={1},
        strip_flow_watermark=True,
        zoom_factor=1.04,
        apply_house_look=True,
    )
    assert (out_w, out_h) == (1920, 1080)
    with Image.open(ai_png) as im_ai:
        assert im_ai.size == (962, 577)
        prof_ai = measure_house_look_profile(im_ai)
        assert prof_ai.b_mean < -2.0
        assert prof_ai.cool_index > 4.0

    # Cảnh tư liệu thật (id=1 thuộc archival_ids) -> miễn crop & miễn House Look trên đĩa, giữ nguyên 1000x600 từng pixel
    arch_png = tmp_path / "raw_images" / "001.png"
    flow_bot.process_flow_downloaded_image(
        dl_file,
        arch_png,
        dst_jpg=None,
        frame_preset="landscape_16_9",
        scene_id=1,
        archival_scene_ids={1},
        strip_flow_watermark=True,
        zoom_factor=1.04,
        apply_house_look=True,
    )
    with Image.open(arch_png) as im_arch:
        assert im_arch.size == (1000, 600)
        assert np.array_equal(np.asarray(im_arch, dtype=np.uint8), arr)
