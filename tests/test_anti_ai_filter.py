"""Unit test cho module lõi Né quét AI (core/anti_ai_filter.py) và tích hợp StoryPipeline."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from core.anti_ai_filter import (
    DEFAULT_FLOW_WATERMARK_ZOOM,
    build_ai_scene_depolish_vf,
    build_anti_ai_audio_filters,
    build_anti_ai_video_filters,
    crop_flow_watermark,
    get_clean_metadata_flags,
    is_archival_scene,
    load_archival_scene_ids,
    normalize_anti_ai_level,
    probe_video_details,
    process_standalone_video,
)
from core.config import DubSettings
from core.ffmpeg_utils import _NO_WINDOW, find_binary, get_ffmpeg, probe, run_ffmpeg
from core.story_pipeline import StoryPipeline
from core.story_schema import StoryScene, Storyboard
from core.tts import ToneTTS


def _inspect_media_metadata(path: Path) -> str:
    """Đọc toàn bộ metadata text bằng ffprobe (nếu có) và ffmpeg -i."""
    chunks = []
    ffprobe_bin = find_binary("ffprobe")
    if ffprobe_bin:
        res_p = subprocess.run(
            [ffprobe_bin, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
        )
        chunks.append(res_p.stdout)
    res_f = subprocess.run(
        [get_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
    )
    chunks.append(res_f.stderr)
    return "\n".join(chunks)


def test_normalize_anti_ai_level_and_config_backward_compat(tmp_path):
    """Kiểm tra chuẩn hóa mức Anti-AI và tương thích ngược settings.json cũ."""
    assert normalize_anti_ai_level(False) == "off"
    assert normalize_anti_ai_level(None) == "off"
    assert normalize_anti_ai_level("off") == "off"
    assert normalize_anti_ai_level(True) == "medium"
    assert normalize_anti_ai_level("light") == "light"
    assert normalize_anti_ai_level("medium") == "medium"
    assert normalize_anti_ai_level("strong") == "strong"

    with pytest.raises(ValueError):
        normalize_anti_ai_level("invalid_tier")

    # Tương thích ngược settings.json cũ chỉ lưu story_anti_ai (bool)
    old_off = tmp_path / "settings_old_off.json"
    old_off.write_text(json.dumps({"story_anti_ai": False}), encoding="utf-8")
    s_off = DubSettings.load(old_off)
    assert s_off.story_anti_ai is False
    assert s_off.story_anti_ai_level == "off"
    kw_off = s_off.story_render_kwargs()
    assert kw_off["anti_ai"] is False
    assert kw_off["anti_ai_level"] == "off"

    old_on = tmp_path / "settings_old_on.json"
    old_on.write_text(json.dumps({"story_anti_ai": True}), encoding="utf-8")
    s_on = DubSettings.load(old_on)
    assert s_on.story_anti_ai is True
    assert s_on.story_anti_ai_level == "medium"
    kw_on = s_on.story_render_kwargs()
    assert kw_on["anti_ai"] is True
    assert kw_on["anti_ai_level"] == "medium"

    assert build_anti_ai_video_filters("off") == []
    assert build_anti_ai_audio_filters("off") == ""
    flags = get_clean_metadata_flags()
    assert "-map_metadata" in flags and "-map_chapters" in flags


@pytest.mark.parametrize("level,film_grain,min_corner_luma", [
    ("light", False, 180),
    ("medium", False, 180),
    ("strong", False, 180),
    ("strong", True, 55),   # Có vignette=PI/5 vẫn sáng xám rõ, tuyệt đối KHÔNG bị góc đen 0
])
def test_zero_black_corners_across_all_levels(tmp_path, level, film_grain, min_corner_luma):
    """Không hở góc đen: video sáng 1080x1920 qua cả 3 mức light/medium/strong giữ 4 góc sáng."""
    src_mp4 = tmp_path / f"bright_{level}_{int(film_grain)}.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0xE8E8E8:s=1080x1920:r=30:d=0.6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src_mp4),
    ])

    out_mp4 = tmp_path / f"clean_{level}_{int(film_grain)}.mp4"
    process_standalone_video(
        src_mp4,
        out_mp4,
        level=level,
        film_grain=film_grain,
        speed=1.0,
        seed=42,
    )
    assert out_mp4.is_file()
    details = probe_video_details(out_mp4)
    assert details.width == 1080
    assert details.height == 1920

    # Trích 1 khung hình giữa video và kiểm tra 4 góc
    frame_png = tmp_path / f"frame_{level}_{int(film_grain)}.png"
    run_ffmpeg(["-ss", "0.2", "-i", str(out_mp4), "-vframes", "1", str(frame_png)])
    img = Image.open(frame_png).convert("RGB")
    assert img.size == (1080, 1920)

    corners = [
        (0, 0),
        (1079, 0),
        (0, 1919),
        (1079, 1919),
        (4, 4),
        (1075, 4),
        (4, 1915),
        (1075, 1915),
    ]
    for x, y in corners:
        r, g, b = img.getpixel((x, y))
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        assert luma >= min_corner_luma, f"Góc ({x},{y}) bị tối/đen bất thường: RGB=({r},{g},{b}) ở mức {level}"


def test_strip_metadata_c2pa_and_standard_30fps_1080x1920(tmp_path):
    """Xóa sạch metadata/C2PA (title=AI_Generated, comment=C2PA_SynthID_Test), giữ 1080x1920 @ 30fps."""
    src_mp4 = tmp_path / "ai_tagged.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x446688:s=1080x1920:r=25:d=1.2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=1.2",
        "-metadata", "title=AI_Generated",
        "-metadata", "comment=C2PA_SynthID_Test",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(src_mp4),
    ])

    before_meta = _inspect_media_metadata(src_mp4)
    assert "AI_Generated" in before_meta
    assert "C2PA_SynthID_Test" in before_meta

    out_mp4 = tmp_path / "ai_tagged_clean.mp4"
    logs = []
    progresses = []
    process_standalone_video(
        src_mp4,
        out_mp4,
        level="medium",
        film_grain=True,
        speed=1.0,
        log_cb=lambda m, lv="info": logs.append((m, lv)),
        progress_cb=lambda p, m="": progresses.append((p, m)),
        seed="meta_test",
    )
    assert out_mp4.is_file()
    assert len(logs) >= 2
    assert progresses[-1][0] == 100

    after_meta = _inspect_media_metadata(out_mp4)
    assert "AI_Generated" not in after_meta
    assert "C2PA_SynthID_Test" not in after_meta

    details = probe_video_details(out_mp4)
    assert details.width == 1080
    assert details.height == 1920
    assert details.fps == pytest.approx(30.0, abs=0.5)
    assert details.has_audio is True


def test_audio_duration_preserved_with_pitch_shift_and_silent_video_no_crash(tmp_path):
    """Bảo toàn thời lượng âm thanh (sai số <= 0.15s) ở mức medium/strong và hỗ trợ video không có audio."""
    src_av = tmp_path / "av_3s.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=44100:duration=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(src_av),
    ])
    src_dur = probe(src_av).duration

    for lv in ("medium", "strong"):
        out_p = tmp_path / f"av_{lv}.mp4"
        process_standalone_video(src_av, out_p, level=lv, film_grain=True, speed=1.0, seed=lv)
        info = probe(out_p)
        assert info.has_video is True
        assert info.has_audio is True
        assert abs(info.duration - src_dur) <= 0.15

    # Kiểm thử tăng tốc đồng bộ 1.20x trong chế độ standalone
    out_fast = tmp_path / "av_fast_1_2x.mp4"
    process_standalone_video(src_av, out_fast, level="light", film_grain=False, speed=1.2, seed="fast")
    fast_info = probe(out_fast)
    assert fast_info.has_video is True and fast_info.has_audio is True
    assert fast_info.duration == pytest.approx(src_dur / 1.2, abs=0.2)

    # Kiểm thử video KHÔNG có luồng audio vẫn chạy mượt không crash
    src_silent = tmp_path / "silent_2s.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=0x808080:s=540x960:r=30:d=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(src_silent),
    ])
    out_silent = tmp_path / "silent_strong.mp4"
    process_standalone_video(src_silent, out_silent, level="strong", film_grain=True, speed=1.0, seed="silent")
    silent_info = probe(out_silent)
    assert silent_info.has_video is True
    assert silent_info.has_audio is False
    assert abs(silent_info.duration - 2.0) <= 0.15


def test_story_pipeline_anti_ai_before_ass_and_bool_backward_compat(tmp_path, monkeypatch):
    """Kiểm chứng StoryPipeline Anti-AI Per-Scene khi 0 ảnh tư liệu thật:
    - anti_ai_level='medium' áp khử bóng nhẹ (depolish) ở từng cảnh TRƯỚC pad/ASS,
      giữ phụ đề ASS sắc nét, giữ sạch giọng đọc (không rotate/anoisesrc toàn cục) và xóa metadata.
    - Cờ anti_ai=True/False cũ tương thích ngược 100%.
    """
    import core.story_pipeline as sp

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0xD0D0D0:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "001.png")])

    sb = Storyboard(
        title="AntiAI_1Pass_Test",
        scenes=[StoryScene(id=1, speech="Xin chào Việt Nam.", motion="zoom_in")],
    )

    recorded_cmds: list[list[str]] = []
    real_run_ffmpeg = sp.run_ffmpeg

    def spy_run_ffmpeg(args, **kwargs):
        args_list = [str(a) for a in args]
        recorded_cmds.append(args_list)
        return real_run_ffmpeg(args, **kwargs)

    monkeypatch.setattr(sp, "run_ffmpeg", spy_run_ffmpeg)

    def _find_vf(suffix: str) -> str:
        cmd = next(c for c in recorded_cmds if any(a.endswith(suffix) for a in c))
        return cmd[cmd.index("-vf") + 1]

    def _find_mix(suffix: str) -> tuple[list[str], str]:
        cmd = next(c for c in recorded_cmds if any(a.endswith(suffix) for a in c))
        return cmd, cmd[cmd.index("-filter_complex") + 1]

    # 1. anti_ai_level="medium" (0 ảnh tư liệu thật) -> depolish từng cảnh, giữ sạch giọng & phụ đề
    out_med = tmp_path / "story_med.mp4"
    pipe1 = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "w1")
    pipe1.render_story(
        sb,
        assets_dir=assets_dir,
        output_path=out_med,
        burn_subtitles=True,
        subtitle_style="highlight_3words",
        anti_ai_level="medium",
        anti_ai_grain=True,
    )
    assert out_med.is_file()
    vf_med = _find_vf("clip_001.mp4")
    assert "hqdn3d=0.8:0.6:1.5:1.2" in vf_med
    assert "eq=saturation=0.52" in vf_med
    assert "curves=all='0/0 0.25/0.20 0.5/0.45 0.85/0.82 1/0.95'" in vf_med
    assert "colorbalance=rs=-0.04:gs=0.00:bs=0.06:rm=-0.03:gm=0.00:bm=0.05:rh=0.02:gh=0.01:bh=-0.01" in vf_med
    assert "noise=alls=7:allf=t+u" in vf_med
    assert "vignette=PI/6" in vf_med
    assert "crop=1080:1920:" in vf_med
    assert "unsharp" not in vf_med

    cmd_med, final_fc = _find_mix("story_med.mp4")
    assert "ass=subs.ass" in final_fc
    assert "rotate=" not in final_fc
    assert "anoisesrc=" not in final_fc
    assert "-map_metadata" in cmd_med and "-1" in cmd_med

    # 2. Tương thích ngược cờ cũ anti_ai=True (không truyền anti_ai_level)
    recorded_cmds.clear()
    out_bool_true = tmp_path / "story_bool_true.mp4"
    pipe2 = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "w2")
    pipe2.render_story(sb, assets_dir=assets_dir, output_path=out_bool_true, burn_subtitles=True, anti_ai=True)
    assert out_bool_true.is_file()
    vf_true = _find_vf("clip_001.mp4")
    assert "hqdn3d=0.8:0.6:1.5:1.2" in vf_true
    assert "eq=saturation=0.52" in vf_true
    assert "colorbalance=rs=-0.04:gs=0.00:bs=0.06" in vf_true
    assert "curves=all='0/0 0.25/0.20 0.5/0.45 0.85/0.82 1/0.95'" in vf_true
    assert "vignette=PI/6" in vf_true
    cmd_true, fc_true = _find_mix("story_bool_true.mp4")
    assert "ass=subs.ass" in fc_true
    assert "rotate=" not in fc_true
    assert "anoisesrc=" not in fc_true
    assert "-map_metadata" in cmd_true and "-1" in cmd_true

    # 3. Tương thích ngược cờ cũ anti_ai=False
    recorded_cmds.clear()
    out_bool_false = tmp_path / "story_bool_false.mp4"
    pipe3 = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "w3")
    pipe3.render_story(sb, assets_dir=assets_dir, output_path=out_bool_false, burn_subtitles=True, anti_ai=False)
    assert out_bool_false.is_file()
    vf_false = _find_vf("clip_001.mp4")
    assert "curves=" not in vf_false
    assert "eq=saturation=" not in vf_false
    assert "noise=" not in vf_false
    assert "vignette=" not in vf_false
    assert "hqdn3d=" not in vf_false
    cmd_false, fc_false = _find_mix("story_bool_false.mp4")
    assert "rotate=" not in fc_false
    assert "anoisesrc=" not in fc_false
    assert "-map_metadata" not in cmd_false


def test_load_archival_scene_ids(tmp_path):
    """Đọc chính xác tập scene_id từ định dạng archive_sources.json và trả set() khi thiếu/rỗng."""
    assert load_archival_scene_ids(tmp_path) == set()
    assert load_archival_scene_ids(None) == set()

    # 1. Định dạng chuẩn fetch_archive_media.py (dict 'assets' theo khóa 001/005)
    ep_std = tmp_path / "ep_std"
    ep_std.mkdir()
    (ep_std / "archive_sources.json").write_text(
        json.dumps(
            {
                "episode": "Tap_04",
                "assets": {
                    "001": {"scene_id": 1, "provider": "wikimedia_commons"},
                    "005": {"scene_id": 5, "provider": "internet_archive"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert load_archival_scene_ids(ep_std) == {1, 5}

    # 2. Định dạng list các bản ghi có 'scene' hoặc 'scene_id'
    ep_list = tmp_path / "ep_list"
    ep_list.mkdir()
    (ep_list / "archive_sources.json").write_text(
        json.dumps([{"scene": 2}, {"scene_id": 7}, {"id": "11"}]),
        encoding="utf-8",
    )
    assert load_archival_scene_ids(ep_list) == {2, 7, 11}

    # 3. File rỗng hoặc JSON rỗng assets trả set() an toàn
    ep_empty = tmp_path / "ep_empty"
    ep_empty.mkdir()
    (ep_empty / "archive_sources.json").write_text("", encoding="utf-8")
    assert load_archival_scene_ids(ep_empty) == set()
    (ep_empty / "archive_sources.json").write_text(json.dumps({"episode": "Empty", "assets": {}}), encoding="utf-8")
    assert load_archival_scene_ids(ep_empty) == set()


def test_ai_scene_depolish_vf_spec():
    """Kiểm tra thông số House Look V5.8 cho cảnh AI (hqdn3d nhẹ, slate colorbalance, curves, vignette, KHÔNG unsharp) & hòa tông ~15% cho ảnh tư liệu thật."""
    from core.anti_ai_filter import ARCHIVAL_HARMONIZE_VF, build_archival_harmonize_vf

    assert build_ai_scene_depolish_vf("off") == ""
    assert build_archival_harmonize_vf(False) == ""
    harm = build_archival_harmonize_vf(True)
    assert harm == ARCHIVAL_HARMONIZE_VF
    assert "eq=contrast=1.04:brightness=-0.02" in harm
    assert "colorbalance=rs=-0.018:gs=0.00:bs=0.026" in harm
    assert "curves=" not in harm and "noise=" not in harm and "crop=" not in harm

    vf_light = build_ai_scene_depolish_vf("light", width=1080, height=1920)
    assert "hqdn3d=0.6:0.5:1.2:1.0" in vf_light
    assert "eq=saturation=0.62" in vf_light
    assert "curves=all='0/0 0.25/0.22 0.5/0.47 0.85/0.84 1/0.96'" in vf_light
    assert "colorbalance=rs=-0.03:gs=0.00:bs=0.045:rm=-0.025:gm=0.00:bm=0.04:rh=0.015:gh=0.005:bh=-0.01" in vf_light
    assert "noise=alls=5:allf=t+u" in vf_light
    assert "vignette=PI/7" in vf_light
    assert "scale=1114:1978:flags=lanczos" in vf_light
    assert "crop=1080:1920:" in vf_light
    assert "unsharp" not in vf_light

    vf_med = build_ai_scene_depolish_vf("medium", width=1080, height=1920)
    assert "hqdn3d=0.8:0.6:1.5:1.2" in vf_med
    assert "eq=saturation=0.52" in vf_med
    assert "curves=all='0/0 0.25/0.20 0.5/0.45 0.85/0.82 1/0.95'" in vf_med
    assert "colorbalance=rs=-0.04:gs=0.00:bs=0.06:rm=-0.03:gm=0.00:bm=0.05:rh=0.02:gh=0.01:bh=-0.01" in vf_med
    assert "noise=alls=7:allf=t+u" in vf_med
    assert "vignette=PI/6" in vf_med
    assert "scale=1114:1978:flags=lanczos" in vf_med
    assert "crop=1080:1920:" in vf_med
    assert "unsharp" not in vf_med

    vf_strong = build_ai_scene_depolish_vf("strong", width=1080, height=1920)
    assert "hqdn3d=1.0:0.8:1.8:1.4" in vf_strong
    assert "eq=saturation=0.44" in vf_strong
    assert "curves=all='0/0 0.25/0.18 0.5/0.42 0.85/0.79 1/0.93'" in vf_strong
    assert "colorbalance=rs=-0.05:gs=0.00:bs=0.075:rm=-0.035:gm=0.00:bm=0.06:rh=0.02:gh=0.01:bh=-0.01" in vf_strong
    assert "noise=alls=9:allf=t+u" in vf_strong
    assert "vignette=PI/5" in vf_strong
    assert "scale=1124:1998:flags=lanczos" in vf_strong
    assert "crop=1080:1920:" in vf_strong
    assert "unsharp" not in vf_strong


def test_crop_flow_watermark_and_v56_settings_defaults(tmp_path):
    """Kiểm chứng auto-crop 1.04x + xóa thoi góc phải dưới Flow và cấu hình mặc định V5.6 trong DubSettings."""
    import numpy as np

    assert DEFAULT_FLOW_WATERMARK_ZOOM == pytest.approx(1.04)
    assert is_archival_scene(3, {1, 3, 5}) is True
    assert is_archival_scene(4, {1, 3, 5}) is False
    assert is_archival_scene(4, None) is False

    # 1. Tạo ảnh mẫu 1024x576 tối sẫm có ngôi sao 4 cánh sáng ở góc phải dưới (x~93%, y~88%)
    w, h = 1024, 576
    arr = np.full((h, w, 3), 18, dtype=np.uint8)
    cx, cy, r_star = int(w * 0.93), int(h * 0.88), 24
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.power(np.abs(xx - cx) / r_star, 0.54) + np.power(np.abs(yy - cy) / r_star, 0.54)
    arr[d <= 1.0] = (145, 145, 145)
    src_img = Image.fromarray(arr, mode="RGB")

    cropped = crop_flow_watermark(src_img, zoom_factor=1.04, remove_corner_sparkle=True)
    assert cropped.size == (985, 554)
    # Điểm tâm ngôi sao cũ phải được lấp về gần nền tối (~18), tuyệt đối không còn sáng > 50
    clean_arr = np.asarray(cropped, dtype=np.uint8)
    assert int(clean_arr[cy - 4 : cy + 5, cx - 4 : cx + 5].max()) < 50

    # Tắt crop/clean trả nguyên kích thước & pixel gốc
    untouched = crop_flow_watermark(src_img, zoom_factor=1.0, remove_corner_sparkle=False)
    assert untouched.size == (1024, 576)
    assert int(np.asarray(untouched)[cy, cx, 0]) == 145

    # 2. DubSettings mặc định V5.6 + tương thích ngược file cũ
    legacy_path = tmp_path / "legacy_settings.json"
    legacy_path.write_text(json.dumps({"story_anti_ai": True}), encoding="utf-8")
    loaded = DubSettings.load(legacy_path)
    assert loaded.story_visual_preset == "restored_doc_still"
    assert loaded.story_flow_watermark_crop is True
    assert loaded.story_flow_watermark_zoom == pytest.approx(1.04)
