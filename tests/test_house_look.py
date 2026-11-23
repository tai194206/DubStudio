"""Unit tests cho House Look V5.8 (CIE LAB soft color matching, Top ND sky gradient, unified grain & audit)."""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import DEFAULT_HOUSE_LOOK_STRENGTH, DEFAULT_TOP_ND_STRENGTH, DubSettings
from core.house_look import (
    DEFAULT_HOUSE_LOOK_IMAGE,
    DEFAULT_HOUSE_LOOK_JSON,
    HouseLookProfile,
    apply_house_look_to_image,
    audit_image_against_house_look,
    lab_to_rgb,
    load_default_house_look_profile,
    measure_house_look_profile,
    resolve_look_reference,
    rgb_to_lab,
)
from core.story_schema import load_storyboard


def test_rgb_lab_roundtrip_accuracy():
    """Chuyển đổi CIE LAB <-> RGB hai chiều phải chính xác trên toàn dải màu 0..255."""
    rng = np.random.default_rng(42)
    sample = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    corners = np.array(
        [[0, 0, 0], [255, 255, 255], [255, 0, 0], [0, 255, 0], [0, 0, 255], [71, 83, 92]],
        dtype=np.uint8,
    ).reshape(1, 6, 3)

    for arr in (sample, corners):
        lab = rgb_to_lab(arr)
        back = lab_to_rgb(lab)
        max_diff = int(np.abs(arr.astype(np.int16) - back.astype(np.int16)).max())
        assert max_diff <= 2


def test_global_default_house_look_assets_and_two_tier_resolution(tmp_path):
    """Kiểm chứng bộ House Look mặc định toàn hệ thống (008_v57) và cơ chế ghi đè 2 tầng."""
    assert DEFAULT_HOUSE_LOOK_IMAGE.is_file()
    assert DEFAULT_HOUSE_LOOK_JSON.is_file()
    assert DEFAULT_HOUSE_LOOK_STRENGTH == 0.64
    assert DEFAULT_TOP_ND_STRENGTH == 0.20

    default_prof = load_default_house_look_profile()
    assert 25.0 <= default_prof.luma_mean <= 45.0
    assert default_prof.b_mean < -4.0
    assert default_prof.cool_index > 10.0

    # Tầng 1: không khai báo look_reference -> tự lấy Global Default
    prof_tier1 = resolve_look_reference({}, ep_dir=tmp_path)
    assert prof_tier1 is not None
    assert prof_tier1.b_mean == default_prof.b_mean

    # Tắt rõ ràng bằng "none" hoặc false
    assert resolve_look_reference({"look_reference": "none"}, ep_dir=tmp_path) is None
    assert resolve_look_reference({"look_reference": False}, ep_dir=tmp_path) is None

    # Tầng 2: override theo số cảnh trong assets/ của dự án
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    warm_arr = np.full((120, 90, 3), (120, 110, 95), dtype=np.uint8)
    Image.fromarray(warm_arr, mode="RGB").save(assets_dir / "009.jpg", format="JPEG")

    prof_override = resolve_look_reference({"look_reference": "009"}, ep_dir=tmp_path, assets_dir=assets_dir)
    assert prof_override is not None
    assert "override:009.jpg" in prof_override.source
    assert prof_override.b_mean > 0.0

    # DubSettings lưu/nạp và kẹp biên an toàn các tham số V5.8
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(
        json.dumps({"story_house_look_strength": 5.0, "story_top_nd_strength": 2.0}),
        encoding="utf-8",
    )
    loaded = DubSettings.load(cfg_path)
    assert loaded.story_house_look_enabled is True
    assert loaded.story_house_look_strength == 0.85
    assert loaded.story_top_nd_strength == 0.35

    # Storyboard schema giữ nguyên look_reference khi load/save
    sb_obj = load_storyboard({
        "version": "3.0",
        "title": "Test_LookRef",
        "look_reference": "008",
        "scenes": [{"id": 1, "speech": "Kiểm tra tham chiếu màu."}],
    })
    assert sb_obj.look_reference == "008"
    assert sb_obj.to_dict()["look_reference"] == "008"


def test_apply_house_look_soft_lab_top_nd_and_gritty_texture():
    """Kiểm chứng 3 ràng buộc V5.8: khớp LAB mềm (0.55–0.70), dìm nhẹ trời mép trên 15–25%, giữ kết cấu gai góc."""
    rng = np.random.default_rng(2026)
    h, w = 320, 180
    # Ảnh giả lập: vùng trời sáng (85% độ sáng) ở 30% trên, vùng đất/vải nhiễu xám trung tính hơi ấm ở 70% dưới
    base = np.full((h, w, 3), (112, 110, 104), dtype=np.float32)
    base[: int(h * 0.32), :, :] = np.array([218.0, 216.0, 212.0], dtype=np.float32)
    texture = rng.normal(0.0, 12.0, size=(h, w, 1)).astype(np.float32)
    textured_u8 = np.clip(np.rint(base + texture), 0, 255).astype(np.uint8)
    src_img = Image.fromarray(textured_u8, mode="RGB")

    # 1. Đo riêng tác động Top ND trên vùng trời trên cùng khi tắt LAB match & grain
    same_prof = measure_house_look_profile(src_img)
    nd_only = apply_house_look_to_image(
        src_img,
        profile=same_prof,
        strength=0.0,
        top_nd_strength=0.20,
        unify_grain=False,
    )
    lab_before = rgb_to_lab(textured_u8)
    lab_nd = rgb_to_lab(np.asarray(nd_only, dtype=np.uint8))
    top_before = float(lab_before[: int(h * 0.12), :, 0].mean())
    top_after = float(lab_nd[: int(h * 0.12), :, 0].mean())
    top_drop = (top_before - top_after) / top_before
    assert 0.10 <= top_drop <= 0.25
    # Nửa dưới (bottom 60%) không bị ND gradient chạm vào
    bot_diff = float(np.abs(lab_nd[int(h * 0.40) :, :, 0] - lab_before[int(h * 0.40) :, :, 0]).max())
    assert bot_diff < 1.0

    # 2. Chạy đầy đủ House Look V5.8 (soft LAB strength=0.64 + Top ND 0.20 + unified grain)
    out_img = apply_house_look_to_image(src_img, strength=0.64, top_nd_strength=0.20, unify_grain=True, seed="test_s5")
    out_prof = measure_house_look_profile(out_img)
    # Tông chuyển sang nghiêng lạnh slate-blue (b_mean âm, B > R) nhưng không bết đen
    assert out_prof.b_mean < -2.5
    assert out_prof.cool_index > 6.0
    assert out_prof.top_sky_luma < 72.0

    # Kiểm tra kết cấu gai góc được bảo toàn (hi-freq variance không bị làm phẳng sang nhựa)
    out_arr = np.asarray(out_img, dtype=np.float32)
    bot_std_orig = float(textured_u8[int(h * 0.45) :, :, :].std())
    bot_std_out = float(out_arr[int(h * 0.45) :, :, :].std())
    assert bot_std_out >= bot_std_orig * 0.70

    # Đốm lửa thực sáng ấm (L=82, b=+28) vẫn giữ độ ấm tương đối hơn nền xám lạnh
    ember_arr = textured_u8.copy()
    ember_arr[200:230, 70:110] = (245, 165, 65)
    ember_out = apply_house_look_to_image(Image.fromarray(ember_arr, mode="RGB"), seed="ember")
    ember_lab = rgb_to_lab(np.asarray(ember_out, dtype=np.uint8))
    assert float(ember_lab[205:225, 75:105, 2].mean()) > float(ember_lab[150:180, 10:50, 2].mean()) + 6.0


def test_audit_image_against_house_look_passes_default_and_flags_outlier():
    """Audit phải đạt 95–100 trên ảnh chuẩn và tự gắn cờ với ảnh sáng vàng/trời trắng gắt."""
    res_ref = audit_image_against_house_look(DEFAULT_HOUSE_LOOK_IMAGE)
    assert res_ref.passed is True
    assert res_ref.consistency_score >= 95.0
    assert res_ref.flags == []

    # Ảnh ngoại lai sáng rực màu xanh/vàng & trời trắng -> phải bị FAIL + đủ cờ cảnh báo
    bad_arr = np.full((240, 140, 3), (95, 185, 70), dtype=np.uint8)
    bad_arr[:80, :, :] = (250, 250, 248)
    res_bad = audit_image_against_house_look(Image.fromarray(bad_arr, mode="RGB"))
    assert res_bad.passed is False
    assert res_bad.consistency_score < 70.0
    assert len(res_bad.flags) >= 2


def test_double_grading_guard_passthrough_and_state_persistence(tmp_path):
    """Kiểm chứng Double-Grading Guard:
    - Cảnh AI đã qua House Look V5.8 (`house_look_applied=True`) chạy pass-through nhẹ (không giảm saturation,
      không dìm curves, không thêm colorbalance hay noise lần 2).
    - Cảnh AI cũ (`house_look_applied=False`) giữ nguyên `DEPOLISH_SPECS`.
    - `load_house_look_scene_ids` đọc đúng `pipeline_state.json` / `pipeline_state_16x9.json` và
      `validate_storyboard.sync_episode_files` bảo toàn cờ `house_look_applied`.
    """
    from core.anti_ai_filter import build_ai_scene_depolish_vf, load_house_look_scene_ids
    from core.series_compiler import build_youtube_16x9_scene_vf
    sys.path.insert(0, str(ROOT_DIR / "automation" / "scripts"))
    import validate_storyboard

    # 1. So sánh trực tiếp chuỗi VF của cảnh AI cũ vs cảnh AI đã qua House Look V5.8
    vf_legacy = build_ai_scene_depolish_vf("medium", width=1080, height=1920, house_look_applied=False)
    vf_hl = build_ai_scene_depolish_vf("medium", width=1080, height=1920, house_look_applied=True)

    assert "eq=saturation=0.52" in vf_legacy
    assert "curves=" in vf_legacy
    assert "colorbalance=" in vf_legacy
    assert "noise=alls=7:allf=t+u" in vf_legacy
    assert "hqdn3d=" in vf_legacy

    assert "scale=1114:1978:flags=lanczos" in vf_hl
    assert "crop=1080:1920:" in vf_hl
    assert "vignette=PI/6" in vf_hl
    assert "eq=saturation=" not in vf_hl
    assert "curves=" not in vf_hl
    assert "colorbalance=" not in vf_hl
    assert "noise=" not in vf_hl
    assert "hqdn3d=" not in vf_hl

    # 2. Kiểm tra build_youtube_16x9_scene_vf với house_look_applied=True
    vf_yt_hl = build_youtube_16x9_scene_vf(
        src_size=(1920, 1080),
        duration=1.5,
        layout_mode="smart",
        is_video=False,
        is_archival=False,
        house_look_applied=True,
        anti_ai_level="medium",
    )
    assert "vignette=PI/6" in vf_yt_hl
    assert "eq=saturation=" not in vf_yt_hl
    assert "curves=" not in vf_yt_hl
    assert "noise=" not in vf_yt_hl

    # 3. Kiểm tra load_house_look_scene_ids và sync_episode_files bảo toàn house_look_applied
    ep_dir = tmp_path / "Ep_DoubleGrade"
    assets_dir = ep_dir / "assets"
    assets_16x9 = ep_dir / "assets_16x9"
    assets_dir.mkdir(parents=True)
    assets_16x9.mkdir(parents=True)

    state_9x16 = {
        "title": "Ep_DoubleGrade",
        "scenes": [
            {"id": 1, "asset_type": "static_image", "image_status": "done", "house_look_applied": False},
            {"id": 2, "asset_type": "static_image", "image_status": "done", "house_look_applied": True},
            {"id": 3, "asset_type": "flow_video", "image_status": "done", "house_look_applied": True},
        ],
    }
    state_16x9 = {
        "title": "Ep_DoubleGrade",
        "scenes": [
            {"id": 2, "asset_type": "static_image", "image_status": "done", "house_look_applied": False},
            {"id": 4, "asset_type": "static_image", "image_status": "done", "house_look_applied": True},
        ],
    }
    (ep_dir / "pipeline_state.json").write_text(json.dumps(state_9x16), encoding="utf-8")
    (ep_dir / "pipeline_state_16x9.json").write_text(json.dumps(state_16x9), encoding="utf-8")

    assert load_house_look_scene_ids(ep_dir, prefer_16x9=False) == {2, 3}
    assert load_house_look_scene_ids(assets_dir, prefer_16x9=False) == {2, 3}
    assert load_house_look_scene_ids(ep_dir, prefer_16x9=True) == {4}
    assert load_house_look_scene_ids(assets_16x9) == {4}

    sb_data = {
        "version": "3.0",
        "title": "Ep_DoubleGrade",
        "scenes": [
            {"id": 1, "speech": "Cảnh một.", "asset_type": "static_image"},
            {"id": 2, "speech": "Cảnh hai.", "asset_type": "static_image"},
            {"id": 3, "speech": "Cảnh ba.", "asset_type": "flow_video"},
        ],
    }
    validate_storyboard.sync_episode_files(ep_dir / "storyboard.json", sb_data)
    assert load_house_look_scene_ids(ep_dir, prefer_16x9=False) == {2, 3}

