"""Kiểm thử Đợt 1: Preset khung hình thích ứng (frame_preset), FFmpeg filter, ASS và Anti-AI Per-Scene."""
from __future__ import annotations

import json
from pathlib import Path

import pysubs2
import pytest
from PIL import Image

from core.anti_ai_filter import build_ai_scene_depolish_vf, probe_video_details
from core.config import DubSettings
from core.continuous_motion import Run, build_run_filter
from core.elevenlabs_alignment import export_karaoke_ass
from core.errors import DubberError
from core.ffmpeg_utils import probe, run_ffmpeg
from core.frame_presets import (
    FRAME_PRESETS,
    VALID_FRAME_PRESETS,
    get_frame_preset,
    resolve_frame_preset,
)
from core.models import Segment, WordTiming
from core.story_pipeline import (
    StoryPipeline,
    _build_video_clip_cmd,
    _build_zoompan_filter,
    generate_story_karaoke_ass,
)
from core.story_schema import StoryScene, Storyboard, load_storyboard
from core.tts import ToneTTS


# ==============================================================================
# 1. Schema, Config & GUI Mapping
# ==============================================================================
def test_frame_preset_schema_config_and_gui_mapping(tmp_path):
    # Tương thích ngược: không có frame_preset mặc định là portrait_9_16
    sb_legacy = load_storyboard({
        "title": "Legacy Story",
        "scenes": [{"id": 1, "speech": "Xin chào."}],
    })
    assert sb_legacy.frame_preset == "portrait_9_16"

    # Roundtrip cả 4 preset
    for preset_name in VALID_FRAME_PRESETS:
        sb = Storyboard(
            title=f"Story_{preset_name}",
            frame_preset=preset_name,
            scenes=[StoryScene(id=1, speech="Nội dung cảnh.")],
        )
        d = sb.to_dict()
        assert d["frame_preset"] == preset_name
        reloaded = Storyboard.from_dict(d)
        assert reloaded.frame_preset == preset_name

    # Báo lỗi rõ ràng khi frame_preset không hợp lệ
    with pytest.raises(DubberError, match="frame_preset"):
        load_storyboard({
            "title": "Invalid Preset",
            "frame_preset": "square_99_1",
            "scenes": [{"id": 1, "speech": "Test"}],
        })

    # resolve_frame_preset: auto/None lấy từ storyboard, override khác auto ghi đè storyboard
    assert resolve_frame_preset("auto", "framed_4_5").name == "framed_4_5"
    assert resolve_frame_preset(None, "framed_1_1").name == "framed_1_1"
    assert resolve_frame_preset("auto", None).name == "portrait_9_16"
    assert resolve_frame_preset("landscape_16_9", "framed_4_5").name == "landscape_16_9"

    # DubSettings tương thích ngược file cũ + sinh kwargs đúng
    old_cfg = tmp_path / "old_settings.json"
    old_cfg.write_text(json.dumps({"story_shot_mode": "scene"}), encoding="utf-8")
    loaded = DubSettings.load(old_cfg)
    assert loaded.story_frame_preset == "auto"
    assert loaded.story_render_kwargs()["frame_preset"] == "auto"

    custom = DubSettings(story_frame_preset="framed_4_5")
    assert custom.story_render_kwargs()["frame_preset"] == "framed_4_5"

    # GUI StoryTab có đủ 5 lựa chọn đúng nhãn yêu cầu
    from gui.story_tab import FRAME_PRESET_CHOICES

    assert list(FRAME_PRESET_CHOICES.keys()) == [
        "auto",
        "portrait_9_16",
        "framed_4_5",
        "framed_1_1",
        "landscape_16_9",
    ]
    assert list(FRAME_PRESET_CHOICES.values()) == [
        "Tự động theo kịch bản",
        "9:16 Tràn viền",
        "4:5 Khung điện ảnh",
        "1:1 Khung tư liệu",
        "16:9 Ngang",
    ]


# ==============================================================================
# 2. Chuỗi filter FFmpeg cho cả 4 frame_preset
# ==============================================================================
@pytest.mark.parametrize(
    "preset_name,inner_w,inner_h,out_w,out_h,pad_expr,dep_scale",
    [
        ("portrait_9_16", 1080, 1920, 1080, 1920, "", "scale=1114:1978:flags=lanczos"),
        ("framed_4_5", 1080, 1350, 1080, 1920, "pad=1080:1920:0:210:black", "scale=1114:1392:flags=lanczos"),
        ("framed_1_1", 1080, 1080, 1080, 1920, "pad=1080:1920:0:360:black", "scale=1114:1114:flags=lanczos"),
        ("landscape_16_9", 1920, 1080, 1920, 1080, "", "scale=1978:1114:flags=lanczos"),
    ],
)
def test_ffmpeg_filters_all_four_presets(
    tmp_path, preset_name, inner_w, inner_h, out_w, out_h, pad_expr, dep_scale
):
    fp = get_frame_preset(preset_name)
    assert (fp.inner_w, fp.inner_h) == (inner_w, inner_h)
    assert (fp.out_w, fp.out_h) == (out_w, out_h)

    dep_vf = build_ai_scene_depolish_vf("medium", width=fp.inner_w, height=fp.inner_h)
    assert dep_scale in dep_vf
    assert f"crop={inner_w}:{inner_h}:" in dep_vf

    # 1) _build_zoompan_filter (scene ảnh tĩnh)
    zp_vf = _build_zoompan_filter("zoom_in", 3.0, extra_vf=dep_vf, frame_preset=preset_name)
    assert f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase" in zp_vf
    assert f"crop={inner_w}:{inner_h}" in zp_vf
    assert f"s={inner_w}x{inner_h}:fps=30" in zp_vf
    assert "setsar=1" in zp_vf
    assert dep_scale in zp_vf
    if pad_expr:
        assert pad_expr in zp_vf
        assert zp_vf.index(dep_scale) < zp_vf.index(pad_expr)
    else:
        assert "pad=" not in zp_vf

    # 2) build_run_filter (planned continuous motion)
    run = Run("loc", 0.0, 3.0, [(0.0, 3.0, 1.0, 1.1)], [1])
    run_vf = build_run_filter(run, fps=30, frame_preset=preset_name, extra_vf=dep_vf)
    assert f"s={inner_w}x{inner_h}:fps=30" in run_vf
    assert "setsar=1" in run_vf
    assert dep_scale in run_vf
    if pad_expr:
        assert pad_expr in run_vf
        assert run_vf.index(dep_scale) < run_vf.index(pad_expr)
    else:
        assert "pad=" not in run_vf

    # 3) _build_video_clip_cmd (flow_video / clip MP4)
    dummy_clip = tmp_path / f"src_{preset_name}.mp4"
    dummy_clip.write_bytes(b"")
    cmd = _build_video_clip_cmd(
        dummy_clip,
        3.0,
        tmp_path / f"out_{preset_name}.mp4",
        smart_clip_fit=False,
        extra_vf=dep_vf,
        frame_preset=preset_name,
    )
    clip_vf = cmd[cmd.index("-vf") + 1]
    assert f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=increase" in clip_vf
    assert f"crop={inner_w}:{inner_h}" in clip_vf
    assert "setsar=1" in clip_vf
    assert dep_scale in clip_vf
    if pad_expr:
        assert pad_expr in clip_vf
        assert clip_vf.index(dep_scale) < clip_vf.index(pad_expr)
    else:
        assert "pad=" not in clip_vf


# ==============================================================================
# 3. Tọa độ PlayRes / MarginV / Fontsize của phụ đề .ass theo từng preset
# ==============================================================================
@pytest.mark.parametrize("subtitle_style", ["karaoke_line", "pop_2words", "highlight_3words"])
def test_ass_subtitle_layout_per_frame_preset(tmp_path, subtitle_style):
    segs = [
        Segment(
            start=0.0,
            end=1.8,
            text="Lịch sử hào hùng.",
            words=[
                WordTiming("Lịch", 0.0, 0.45),
                WordTiming("sử", 0.45, 0.90),
                WordTiming("hào", 0.90, 1.35),
                WordTiming("hùng.", 1.35, 1.80),
            ],
        )
    ]
    style_key = {
        "karaoke_line": "KaraokeTikTok",
        "pop_2words": "PopWordTikTok",
        "highlight_3words": "Highlight3TikTok",
    }[subtitle_style]

    # 9:16 mặc định
    ass_916 = tmp_path / f"sub_916_{subtitle_style}.ass"
    generate_story_karaoke_ass(
        segs, ass_916, font_size=72, subtitle_style=subtitle_style, sub_margin_v=400, frame_preset="portrait_9_16"
    )
    s_916 = pysubs2.load(str(ass_916), encoding="utf-8")
    assert int(s_916.info["PlayResX"]) == 1080
    assert int(s_916.info["PlayResY"]) == 1920
    assert s_916.styles[style_key].marginv == 400
    fs_916 = s_916.styles[style_key].fontsize

    # 4:5 (MarginV khoảng 390–420 từ đáy 1920px)
    ass_45 = tmp_path / f"sub_45_{subtitle_style}.ass"
    generate_story_karaoke_ass(
        segs, ass_45, font_size=72, subtitle_style=subtitle_style, sub_margin_v=390, frame_preset="framed_4_5"
    )
    s_45 = pysubs2.load(str(ass_45), encoding="utf-8")
    assert int(s_45.info["PlayResX"]) == 1080
    assert int(s_45.info["PlayResY"]) == 1920
    assert 390 <= s_45.styles[style_key].marginv <= 420

    # 1:1 (tự nâng lên 500–520 kể cả khi GUI truyền giá trị mặc định 400)
    ass_11 = tmp_path / f"sub_11_{subtitle_style}.ass"
    generate_story_karaoke_ass(
        segs, ass_11, font_size=72, subtitle_style=subtitle_style, sub_margin_v=400, frame_preset="framed_1_1"
    )
    s_11 = pysubs2.load(str(ass_11), encoding="utf-8")
    assert int(s_11.info["PlayResX"]) == 1080
    assert int(s_11.info["PlayResY"]) == 1920
    assert 500 <= s_11.styles[style_key].marginv <= 520

    # 16:9 Ngang (PlayRes 1920x1080, tự chuyển MarginV & cỡ chữ nhỏ gọn cho màn ngang)
    ass_169 = tmp_path / f"sub_169_{subtitle_style}.ass"
    generate_story_karaoke_ass(
        segs, ass_169, font_size=72, subtitle_style=subtitle_style, sub_margin_v=400, frame_preset="landscape_16_9"
    )
    s_169 = pysubs2.load(str(ass_169), encoding="utf-8")
    assert int(s_169.info["PlayResX"]) == 1920
    assert int(s_169.info["PlayResY"]) == 1080
    assert 40 <= s_169.styles[style_key].marginv <= 100
    assert s_169.styles[style_key].fontsize < fs_916


def test_export_karaoke_ass_frame_presets(tmp_path):
    aligned = [
        {
            "id": 1,
            "speech": "Một hai ba",
            "words": [
                {"word": "Một", "start": 0.0, "end": 0.4},
                {"word": "hai", "start": 0.4, "end": 0.8},
                {"word": "ba", "start": 0.8, "end": 1.2},
            ],
        }
    ]
    for fp_name, exp_res, mv_range in [
        ("portrait_9_16", (1080, 1920), (380, 380)),
        ("framed_4_5", (1080, 1920), (390, 420)),
        ("framed_1_1", (1080, 1920), (500, 520)),
        ("landscape_16_9", (1920, 1080), (60, 90)),
    ]:
        p = tmp_path / f"karaoke_{fp_name}.ass"
        export_karaoke_ass(aligned, p, frame_preset=fp_name)
        subs = pysubs2.load(str(p), encoding="utf-8")
        assert (int(subs.info["PlayResX"]), int(subs.info["PlayResY"])) == exp_res
        assert mv_range[0] <= subs.styles["Karaoke"].marginv <= mv_range[1]
        if fp_name == "landscape_16_9":
            assert subs.styles["Karaoke"].fontsize <= 50


# ==============================================================================
# 4. E2E Render + Anti-AI Per-Scene (0 ảnh tư liệu thật & có archive_sources.json)
# ==============================================================================
def test_e2e_framed_4_5_zero_archival_per_scene_anti_ai_and_pure_black_bars(tmp_path, monkeypatch):
    """Tập 0 ảnh tư liệu thật + framed_4_5 + anti_ai_level='medium' + cinematic_grade=True:
    - Chạy depolish 1080x1350 TRƯỚC pad=1080:1920:0:210:black
    - Không rotate/anoisesrc ở mix cuối, giữ -map_metadata -1
    - Dải đen trên (y<210) và dưới (y>1560) giữ đen tuyền, vùng ảnh giữa giữ màu
    - File .ass có MarginV trong [390, 420].
    """
    import core.story_pipeline as sp

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x2266CC:s=1080x1350:d=1", "-vframes", "1", str(assets_dir / "001.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0xCC5522:s=1080x1350:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    sb_file = tmp_path / "storyboard.json"
    sb_file.write_text(
        json.dumps(
            {
                "title": "Framed45_ZeroArchival",
                "frame_preset": "framed_4_5",
                "scenes": [
                    {"id": 1, "speech": "Thời đại Hùng Vương dựng nước.", "motion": "zoom_in", "sfx": "none"},
                    {"id": 2, "speech": "Những trang sử hào hùng.", "motion": "zoom_out", "sfx": "none"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recorded_cmds: list[list[str]] = []
    real_run_ffmpeg = sp.run_ffmpeg

    def spy_run_ffmpeg(args, **kwargs):
        args_list = [str(a) for a in args]
        recorded_cmds.append(args_list)
        return real_run_ffmpeg(args, **kwargs)

    monkeypatch.setattr(sp, "run_ffmpeg", spy_run_ffmpeg)

    out_mp4 = tmp_path / "out_45.mp4"
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")
    res = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        burn_subtitles=True,
        subtitle_style="highlight_3words",
        sub_margin_v=400,
        anti_ai_level="medium",
        cinematic_grade=True,
        loudness_norm=False,
    )
    assert res.is_file()
    details = probe_video_details(out_mp4)
    assert (details.width, details.height) == (1080, 1920)

    # Kiểm tra lệnh FFmpeg của cả 2 cảnh AI & mix cuối
    for cname in ("clip_001.mp4", "clip_002.mp4"):
        cmd_c = next(c for c in recorded_cmds if any(a.endswith(cname) for a in c))
        vf_c = cmd_c[cmd_c.index("-vf") + 1]
        assert "s=1080x1350:fps=30" in vf_c
        assert "scale=1114:1392:flags=lanczos" in vf_c
        assert "pad=1080:1920:0:210:black" in vf_c
        assert vf_c.index("scale=1114:1392:flags=lanczos") < vf_c.index("pad=1080:1920:0:210:black")

    cmd_mix = next(c for c in recorded_cmds if any(a.endswith("out_45.mp4") for a in c))
    fc_mix = cmd_mix[cmd_mix.index("-filter_complex") + 1]
    assert "rotate=" not in fc_mix
    assert "anoisesrc=" not in fc_mix
    assert "ass=subs.ass" in fc_mix
    assert "-map_metadata" in cmd_mix and "-1" in cmd_mix

    # Kiểm tra frame thực tế: dải đen trên/dưới đen tuyền, vùng ảnh giữa sáng màu
    frame_png = tmp_path / "check_45.png"
    run_ffmpeg(["-ss", "0.2", "-i", str(out_mp4), "-vframes", "1", str(frame_png)])
    img = Image.open(frame_png).convert("RGB")
    assert img.size == (1080, 1920)
    top_luma = sum(img.getpixel((540, 20))) / 3.0
    bot_luma = sum(img.getpixel((540, 1880))) / 3.0
    mid_rgb = img.getpixel((540, 800))
    assert top_luma <= 8.0, f"Dải đen trên bị lệch màu/sáng: {img.getpixel((540, 20))}"
    assert bot_luma <= 8.0, f"Dải đen dưới bị lệch màu/sáng: {img.getpixel((540, 1880))}"
    assert max(mid_rgb) >= 100, f"Vùng ảnh giữa bất thường: {mid_rgb}"

    subs = pysubs2.load(str(out_mp4.with_suffix(".ass")), encoding="utf-8")
    assert 390 <= subs.styles["Highlight3TikTok"].marginv <= 420


def test_e2e_framed_1_1_with_archival_exemption_and_override_to_16_9(tmp_path, monkeypatch):
    """Kiểm thử framed_1_1 có archive_sources.json (cảnh 1 miễn lọc, cảnh 2 depolish 1080x1080)
    và kiểm thử tham số GUI frame_preset='landscape_16_9' ghi đè storyboard.
    """
    import core.story_pipeline as sp

    ep_dir = tmp_path / "ep_11"
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(parents=True)
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x808080:s=1080x1080:d=1", "-vframes", "1", str(assets_dir / "001.jpg")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x3377AA:s=1080x1080:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    (ep_dir / "archive_sources.json").write_text(
        json.dumps({"assets": {"001": {"scene_id": 1, "provider": "wikimedia_commons"}}}),
        encoding="utf-8",
    )
    sb_file = ep_dir / "storyboard.json"
    sb_file.write_text(
        json.dumps(
            {
                "title": "Framed11_Archival",
                "frame_preset": "framed_1_1",
                "scenes": [
                    {"id": 1, "speech": "Ảnh tư liệu lịch sử.", "motion": "zoom_in", "sfx": "none"},
                    {"id": 2, "speech": "Cảnh minh họa AI.", "motion": "pan_right", "sfx": "none"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    recorded_cmds: list[list[str]] = []
    real_run_ffmpeg = sp.run_ffmpeg

    def spy_run_ffmpeg(args, **kwargs):
        args_list = [str(a) for a in args]
        recorded_cmds.append(args_list)
        return real_run_ffmpeg(args, **kwargs)

    monkeypatch.setattr(sp, "run_ffmpeg", spy_run_ffmpeg)

    # 1) Chạy framed_1_1 từ storyboard (frame_preset="auto")
    out_11 = ep_dir / "out_11.mp4"
    StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "w11").render_story(
        sb_file,
        assets_dir=assets_dir,
        output_path=out_11,
        frame_preset="auto",
        burn_subtitles=True,
        subtitle_style="pop_2words",
        sub_margin_v=390,
        anti_ai_level="medium",
    )
    assert out_11.is_file()
    det_11 = probe_video_details(out_11)
    assert (det_11.width, det_11.height) == (1080, 1920)

    vf_c1 = next(c for c in recorded_cmds if any(a.endswith("clip_001.mp4") for a in c))
    vf_c1_str = vf_c1[vf_c1.index("-vf") + 1]
    assert "curves=" not in vf_c1_str
    assert "noise=" not in vf_c1_str
    assert "pad=1080:1920:0:360:black" in vf_c1_str

    vf_c2 = next(c for c in recorded_cmds if any(a.endswith("clip_002.mp4") for a in c))
    vf_c2_str = vf_c2[vf_c2.index("-vf") + 1]
    assert "scale=1114:1114:flags=lanczos" in vf_c2_str
    assert "pad=1080:1920:0:360:black" in vf_c2_str
    assert vf_c2_str.index("scale=1114:1114:flags=lanczos") < vf_c2_str.index("pad=1080:1920:0:360:black")

    subs_11 = pysubs2.load(str(out_11.with_suffix(".ass")), encoding="utf-8")
    assert 500 <= subs_11.styles["PopWordTikTok"].marginv <= 520

    # 2) Ghi đè từ GUI: frame_preset="landscape_16_9"
    recorded_cmds.clear()
    out_169 = ep_dir / "out_169.mp4"
    StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "w169").render_story(
        sb_file,
        assets_dir=assets_dir,
        output_path=out_169,
        frame_preset="landscape_16_9",
        burn_subtitles=True,
        subtitle_style="highlight_3words",
        sub_margin_v=400,
        anti_ai_level="medium",
    )
    assert out_169.is_file()
    det_169 = probe_video_details(out_169)
    assert (det_169.width, det_169.height) == (1920, 1080)

    vf_169_c2 = next(c for c in recorded_cmds if any(a.endswith("clip_002.mp4") for a in c))
    vf_169_c2_str = vf_169_c2[vf_169_c2.index("-vf") + 1]
    assert "scale=1978:1114:flags=lanczos" in vf_169_c2_str
    assert "pad=" not in vf_169_c2_str

    subs_169 = pysubs2.load(str(out_169.with_suffix(".ass")), encoding="utf-8")
    assert (int(subs_169.info["PlayResX"]), int(subs_169.info["PlayResY"])) == (1920, 1080)
    assert subs_169.styles["Highlight3TikTok"].marginv == 72
