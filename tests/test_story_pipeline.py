"""Kiểm thử tự động cho Story Mode 9:16 (Phase 3).

100% offline: Dùng ToneTTS và synthetic FFmpeg fixtures, không gọi mạng hay GPU.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from core.errors import DubberError
from core.elevenlabs_alignment import (
    align_scenes_with_words,
    chars_to_words,
    export_karaoke_ass,
)
from core.ffmpeg_utils import get_ffmpeg, probe, run_ffmpeg, _NO_WINDOW
from core.story_pipeline import (
    StoryPipeline,
    find_scene_asset,
    generate_story_karaoke_ass,
    validate_story_assets,
)
from core.story_schema import StoryScene, Storyboard, load_storyboard
from core.tts import ToneTTS


def _get_video_resolution(path: Path) -> tuple[int, int]:
    """Trích xuất độ phân giải width x height của video từ ffmpeg -i."""
    res = subprocess.run(
        [get_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        creationflags=_NO_WINDOW,
    )
    m = re.search(r"Stream #.*Video:.*,\s*(\d{3,4})x(\d{3,4})", res.stderr)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


# ==============================================================================
# 1. Kiểm thử Schema & Hợp đồng Storyboard
# ==============================================================================
def test_load_storyboard_valid(tmp_path):
    data = {
        "title": "Lịch sử AI",
        "aspect_ratio": "9:16",
        "style_suffix": "2D vector cartoon",
        "bgm": "bgm.mp3",
        "scenes": [
            {
                "id": 1,
                "speech": "Năm 1950, Alan Turing đặt câu hỏi.",
                "image_prompt": "Alan Turing thinking",
                "motion": "zoom_in",
                "sfx": "boom",
                "on_screen_text": "1950",
            },
            {
                "id": 2,
                "speech": "Máy móc có thể suy nghĩ được không?",
                "image_prompt": "Vintage computer glowing",
                "motion": "pan_left",
                "sfx": "whoosh",
            },
        ],
    }
    sb_file = tmp_path / "storyboard.json"
    sb_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    sb = load_storyboard(sb_file)
    assert sb.title == "Lịch sử AI"
    assert sb.aspect_ratio == "9:16"
    assert sb.bgm == "bgm.mp3"
    assert len(sb.scenes) == 2
    assert sb.scenes[0].id == 1
    assert sb.scenes[0].motion == "zoom_in"
    assert sb.scenes[0].sfx == "boom"
    assert sb.scenes[0].on_screen_text == "1950"
    assert sb.scenes[1].id == 2
    assert sb.scenes[1].motion == "pan_left"
    assert sb.scenes[1].sfx == "whoosh"


def test_load_storyboard_missing_scenes_or_empty_speech(tmp_path):
    # Thiếu scenes
    sb_file = tmp_path / "no_scenes.json"
    sb_file.write_text(json.dumps({"title": "No scenes", "scenes": []}), encoding="utf-8")
    with pytest.raises(DubberError, match="scenes"):
        load_storyboard(sb_file)

    # Speech rỗng
    sb_file_empty_speech = tmp_path / "empty_speech.json"
    sb_file_empty_speech.write_text(
        json.dumps({
            "title": "Empty Speech",
            "scenes": [{"id": 1, "speech": "   "}],
        }),
        encoding="utf-8",
    )
    with pytest.raises(DubberError, match="speech.*rỗng"):
        load_storyboard(sb_file_empty_speech)

    # File không tồn tại
    with pytest.raises(DubberError, match="Không tìm thấy file"):
        load_storyboard(tmp_path / "non_existent.json")


def test_load_storyboard_raises_on_invalid_motion_or_sfx(tmp_path):
    # Motion lạ phải báo lỗi rõ ràng, không fallback im lặng
    data_bad_motion = {
        "title": "Invalid Motion Test",
        "scenes": [
            {
                "id": 1,
                "speech": "Thử nghiệm chuyển động lạ",
                "motion": "super_spin_360",
                "sfx": "none",
            }
        ],
    }
    sb_file1 = tmp_path / "bad_motion.json"
    sb_file1.write_text(json.dumps(data_bad_motion), encoding="utf-8")
    with pytest.raises(DubberError, match="motion 'super_spin_360' không hợp lệ"):
        load_storyboard(sb_file1)

    # SFX lạ phải báo lỗi rõ ràng, không fallback im lặng
    data_bad_sfx = {
        "title": "Invalid SFX Test",
        "scenes": [
            {
                "id": 1,
                "speech": "Thử nghiệm âm thanh lạ",
                "motion": "zoom_in",
                "sfx": "alien_laser",
            }
        ],
    }
    sb_file2 = tmp_path / "bad_sfx.json"
    sb_file2.write_text(json.dumps(data_bad_sfx), encoding="utf-8")
    with pytest.raises(DubberError, match="sfx 'alien_laser' không hợp lệ"):
        load_storyboard(sb_file2)


# ==============================================================================
# 2. Kiểm thử Khớp Tài Nguyên (Assets Matching)
# ==============================================================================
def test_validate_assets_matching_priority(tmp_path):
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()

    # Cảnh 1 có cả mp4 và png -> ưu tiên mp4
    f_001_mp4 = scenes_dir / "001.mp4"
    f_001_mp4.write_bytes(b"dummy_video")
    f_001_png = scenes_dir / "001.png"
    f_001_png.write_bytes(b"dummy_image")

    # Cảnh 2 chỉ có png
    f_002_png = scenes_dir / "002.png"
    f_002_png.write_bytes(b"dummy_image_2")

    sb = Storyboard(
        title="Asset Check",
        scenes=[
            StoryScene(id=1, speech="Cảnh một"),
            StoryScene(id=2, speech="Cảnh hai"),
            StoryScene(id=3, speech="Cảnh ba thiếu file"),
        ],
    )

    found, missing = validate_story_assets(sb, scenes_dir)
    assert found[1] == f_001_mp4
    assert found[2] == f_002_png
    assert missing == [3]


# ==============================================================================
# 3. Kiểm thử Phụ Đề Karaoke 9:16 Vùng An Toàn TikTok
# ==============================================================================
def test_generate_story_karaoke_ass_safe_area(tmp_path):
    from core.models import Segment, WordTiming

    segments = [
        Segment(
            start=0.0,
            end=2.0,
            text="Trí tuệ nhân tạo",
            words=[
                WordTiming("Trí", 0.0, 0.6),
                WordTiming("tuệ", 0.6, 1.2),
                WordTiming("nhân", 1.2, 1.6),
                WordTiming("tạo", 1.6, 2.0),
            ],
        )
    ]
    out_ass = tmp_path / "tiktok_test.ass"
    generate_story_karaoke_ass(segments, out_ass)

    assert out_ass.is_file()
    content = out_ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    # Kiểm tra margin TikTok safe area (MarginV=400, MarginL=80, MarginR=160)
    assert "80,160,400" in content or "400" in content
    assert r"\kf" in content or r"\k" in content
    assert "Trí" in content


# ==============================================================================
# 4. Kiểm thử End-to-End StoryPipeline 100% Offline
# ==============================================================================
def test_story_pipeline_e2e_offline_images(tmp_path):
    """End-to-end: 2 cảnh ảnh tĩnh PNG + ToneTTS + Ken Burns -> xuất MP4 1080x1920 có tiếng."""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    # Tạo 2 ảnh PNG mẫu hợp lệ bằng FFmpeg lavfi
    img1 = assets_dir / "001.png"
    img2 = assets_dir / "002.png"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=720x1280:d=1", "-vframes", "1", str(img1)])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=720x1280:d=1", "-vframes", "1", str(img2)])

    sb_data = {
        "title": "Câu chuyện Alan Turing",
        "aspect_ratio": "9:16",
        "scenes": [
            {
                "id": 1,
                "speech": "Năm một chín năm mươi",
                "motion": "zoom_in",
                "sfx": "none",
            },
            {
                "id": 2,
                "speech": "Alan Turing đưa ra ý tưởng",
                "motion": "pan_left",
                "sfx": "none",
            },
        ],
    }
    sb_file = tmp_path / "storyboard.json"
    sb_file.write_text(json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")

    out_mp4 = tmp_path / "final_story_916.mp4"
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")

    result_path = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        burn_subtitles=True,
        codec="h264",
    )

    assert result_path.is_file()
    assert result_path == out_mp4

    # Xác nhận file video hợp lệ và đúng độ phân giải 1080x1920
    info = probe(out_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    assert info.duration > 1.0  # Mỗi cảnh ToneTTS khoảng ~1-2s

    w, h = _get_video_resolution(out_mp4)
    assert w == 1080
    assert h == 1920


    # Kiểm tra file .ass đi kèm được xuất thành công
    ass_file = out_mp4.with_suffix(".ass")
    assert ass_file.is_file()
    assert "PlayResX: 1080" in ass_file.read_text(encoding="utf-8")


def test_story_pipeline_with_clip_and_bgm_and_sfx(tmp_path):
    """End-to-end: 1 clip video + 1 ảnh + BGM + SFX ducking -> xuất MP4 hợp lệ."""
    assets_dir = tmp_path / "assets_mix"
    assets_dir.mkdir()
    sfx_dir = assets_dir / "sfx"
    sfx_dir.mkdir()

    # Cảnh 1: clip video 1 giây
    clip1 = assets_dir / "001.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25",
        "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(clip1),
    ])

    # Cảnh 2: ảnh png
    img2 = assets_dir / "002.png"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=green:s=720x1280:d=1", "-vframes", "1", str(img2)])

    # SFX mẫu
    sfx_boom = sfx_dir / "boom.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=110:duration=0.5", "-c:a", "pcm_s16le", str(sfx_boom)])

    # BGM mẫu
    bgm_file = tmp_path / "sample_bgm.mp3"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:a", "libmp3lame", str(bgm_file)])

    sb_data = {
        "title": "Clip và BGM SFX Test",
        "scenes": [
            {
                "id": 1,
                "speech": "Cảnh một bằng clip",
                "motion": "static",
                "sfx": "boom",
            },
            {
                "id": 2,
                "speech": "Cảnh hai bằng hình ảnh",
                "motion": "zoom_out",
                "sfx": "none",
            },
        ],
    }
    sb_file = tmp_path / "storyboard.json"
    sb_file.write_text(json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")

    out_mp4 = tmp_path / "mix_test.mp4"
    pipe = StoryPipeline(
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work_mix",
        sfx_dir=sfx_dir,
    )

    result_path = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        bgm_path=bgm_file,
        burn_subtitles=True,
        codec="h264",
    )

    assert result_path.is_file()
    info = probe(out_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    w, h = _get_video_resolution(out_mp4)
    assert w == 1080
    assert h == 1920



def test_story_pipeline_raises_on_missing_asset(tmp_path):
    assets_dir = tmp_path / "empty_assets"
    assets_dir.mkdir()

    sb = Storyboard(
        title="Missing Test",
        scenes=[StoryScene(id=1, speech="Xin chào")],
    )
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")

    with pytest.raises(DubberError, match="Thiếu tài nguyên"):
        pipe.render_story(sb, assets_dir=assets_dir, output_path=tmp_path / "out.mp4")


# ==============================================================================
# 5. Kiểm thử Storyboard v2.0 & ElevenLabs Alignment
# ==============================================================================
def test_load_storyboard_v2_schema(tmp_path):
    data = {
        "version": "2.0",
        "title": "Hành trình tìm đường cứu nước - Bến Nhà Rồng 1911",
        "aspect_ratio": "9:16",
        "total_words": 285,
        "style_prefix": "2D flat vector illustration, OverSimplified historical style",
        "character_bible": "Young Vietnamese man in 1911",
        "full_script_for_elevenlabs": "Ngày mồng 5 tháng 6 năm 1911...",
        "scenes": [
            {
                "id": 1,
                "speech": "Ngày mồng 5 tháng 6 năm 1911, tại bến cảng Nhà Rồng.",
                "source_note": "Tài liệu Bảo tàng Hồ Chí Minh.",
                "asset_type": "flow_video",
                "image_prompt": "Wide low-angle shot",
                "motion_prompt": "Slow camera push-in",
                "ken_burns": "zoom_in",
                "sfx": "ship_horn",
                "on_screen_text": "5/6/1911",
            },
            {
                "id": 2,
                "speech": "Người thanh niên yêu nước quyết định ra đi tìm đường cứu nước.",
                "asset_type": "static_image",
                "ken_burns": "pan_left",
                "sfx": "none",
            },
        ],
    }
    sb_file = tmp_path / "storyboard_v2.json"
    sb_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    sb = load_storyboard(sb_file)
    assert sb.version == "2.0"
    assert sb.total_words == 285
    assert "OverSimplified" in sb.style_prefix
    assert "Young Vietnamese" in sb.character_bible
    assert sb.full_script_for_elevenlabs == "Ngày mồng 5 tháng 6 năm 1911..."
    assert sb.get_full_script() == "Ngày mồng 5 tháng 6 năm 1911..."

    sc1 = sb.scenes[0]
    assert sc1.id == 1
    assert sc1.asset_type == "flow_video"
    assert sc1.ken_burns == "zoom_in"
    assert sc1.motion == "zoom_in"
    assert sc1.sfx == "ship_horn"
    assert sc1.on_screen_text == "5/6/1911"

    sc2 = sb.scenes[1]
    assert sc2.id == 2
    assert sc2.asset_type == "static_image"
    assert sc2.ken_burns == "pan_left"
    assert sc2.motion == "pan_left"

    # Thử roundtrip to_dict -> from_dict
    d = sb.to_dict()
    assert d["version"] == "2.0"
    assert d["scenes"][0]["asset_type"] == "flow_video"
    sb_reloaded = Storyboard.from_dict(d)
    assert sb_reloaded.version == "2.0"
    assert len(sb_reloaded.scenes) == 2


def test_elevenlabs_alignment_chars_to_words():
    alignment_sample = {
        "characters": ["N", "g", "à", "y", " ", "5", " ", "t", "h", "á", "n", "g", " ", "6"],
        "character_start_times_seconds": [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7],
        "character_end_times_seconds": [0.05, 0.1, 0.15, 0.2, 0.25, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.85],
    }

    words = chars_to_words(alignment_sample)
    assert len(words) == 4
    assert words[0]["word"] == "Ngày"
    assert words[0]["start"] == 0.0
    assert words[0]["end"] == 0.2
    assert words[1]["word"] == "5"
    assert words[2]["word"] == "tháng"
    assert words[3]["word"] == "6"
    assert words[3]["end"] == 0.85


def test_elevenlabs_alignment_align_scenes_with_words():
    scenes = [
        {"id": 1, "speech": "Ngày 5"},
        {"id": 2, "speech": "tháng 6"},
    ]
    words = [
        {"word": "Ngày", "start": 0.1, "end": 0.4},
        {"word": "5", "start": 0.45, "end": 0.8},
        {"word": "tháng", "start": 1.2, "end": 1.6},
        {"word": "6", "start": 1.65, "end": 2.1},
    ]

    aligned = align_scenes_with_words(scenes, words)
    assert len(aligned) == 2
    # Cảnh 1 bắt đầu từ 0.0s
    assert aligned[0]["start_time"] == 0.0
    # Khoảng nghỉ giữa 0.8 và 1.2 -> end_time = 1.0
    assert aligned[0]["end_time"] == 1.0
    assert aligned[0]["duration_sec"] == 1.0
    assert len(aligned[0]["words"]) == 2

    # Cảnh 2 nối tiếp từ 1.0s
    assert aligned[1]["start_time"] == 1.0
    assert aligned[1]["end_time"] == 2.1
    assert aligned[1]["duration_sec"] == 1.1
    assert len(aligned[1]["words"]) == 2

    # Thử với StoryScene objects
    obj_scenes = [
        StoryScene(id=1, speech="Ngày 5"),
        StoryScene(id=2, speech="tháng 6"),
    ]
    aligned_objs = align_scenes_with_words(obj_scenes, words)
    assert aligned_objs[0].start_time == 0.0
    assert aligned_objs[0].duration_sec == 1.0
    assert aligned_objs[1].start_time == 1.0
    assert aligned_objs[1].duration_sec == 1.1


def test_export_karaoke_ass(tmp_path):
    aligned_scenes = [
        {
            "id": 1,
            "speech": "Ngày 5 tháng 6",
            "words": [
                {"word": "Ngày", "start": 0.0, "end": 0.3},
                {"word": "5", "start": 0.35, "end": 0.7},
                {"word": "tháng", "start": 0.8, "end": 1.1},
                {"word": "6", "start": 1.15, "end": 1.5},
            ],
        }
    ]
    ass_file = tmp_path / "test_karaoke.ass"
    export_karaoke_ass(aligned_scenes, ass_file, max_words_per_line=6)

    assert ass_file.is_file()
    content = ass_file.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Karaoke" in content
    assert r"\kf" in content
    assert "Ngày" in content
    assert "Dialogue:" in content


def test_story_pipeline_v2_hybrid_rendering(tmp_path):
    """Kiểm thử pipeline Hybrid v2: 1 clip flow_video + 1 static_image kết hợp alignment data (100% offline)."""
    assets_dir = tmp_path / "hybrid_assets"
    assets_dir.mkdir()

    # Cảnh 1: clip mp4
    clip1 = assets_dir / "001.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=navy:s=1080x1920:d=2:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(clip1)
    ])

    # Cảnh 2: ảnh png
    img2 = assets_dir / "002.png"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=darkgreen:s=1080x1920:d=1",
        "-frames:v", "1",
        str(img2)
    ])

    # Giả lập alignment data từ ElevenLabs
    alignment_data = {
        "characters": list("Chào bạn đây là thử nghiệm"),
        "character_start_times_seconds": [i * 0.1 for i in range(len("Chào bạn đây là thử nghiệm"))],
        "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len("Chào bạn đây là thử nghiệm"))],
    }

    sb = Storyboard(
        version="2.0",
        title="Hybrid V2 Test",
        scenes=[
            StoryScene(
                id=1,
                speech="Chào bạn",
                asset_type="flow_video",
                ken_burns="zoom_in",
            ),
            StoryScene(
                id=2,
                speech="đây là thử nghiệm",
                asset_type="static_image",
                ken_burns="zoom_out",
            ),
        ],
    )

    out_mp4 = tmp_path / "hybrid_out.mp4"
    pipe = StoryPipeline(
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work_hybrid",
    )

    result_path = pipe.render_story(
        storyboard_path_or_obj=sb,
        assets_dir=assets_dir,
        output_path=out_mp4,
        alignment_data=alignment_data,
        burn_subtitles=True,
    )

    assert result_path.is_file()
    info = probe(out_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    w, h = _get_video_resolution(out_mp4)
    assert w == 1080
    assert h == 1920

    # Phải có file .ass đi kèm
    assert out_mp4.with_suffix(".ass").is_file()


def test_story_pipeline_invalid_shot_mode_raises(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "001.png").write_bytes(b"dummy")
    sb = Storyboard(title="T", scenes=[StoryScene(id=1, speech="A")])
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")
    with pytest.raises(DubberError, match="shot_mode 'invalid' không hợp lệ"):
        pipe.render_story(sb, assets_dir, shot_mode="invalid")


def test_story_pipeline_planned_mode_requires_beats(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "001.png").write_bytes(b"dummy")
    sb = Storyboard(title="T", scenes=[StoryScene(id=1, speech="A")])
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")
    with pytest.raises(DubberError, match="storyboard chưa có beats"):
        pipe.render_story(sb, assets_dir, shot_mode="planned")


def test_story_pipeline_planned_mode_e2e(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    img_path = assets_dir / "001.png"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=720x1280:d=1", "-vframes", "1", str(img_path)])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    sb_dict = {
        "title": "Planned Mode E2E",
        "aspect_ratio": "9:16",
        "scenes": [
            {"id": 1, "speech": "Một hai ba bốn.", "motion": "zoom_in"},
            {"id": 2, "speech": "Năm sáu bảy tám.", "motion": "zoom_out"},
        ],
        "beats": [
            {"id": 1, "text": "Một hai ba bốn.", "location": "nha_rong", "intensity": 1, "intent": "establish"},
            {"id": 2, "text": "Năm sáu bảy tám.", "location": "nha_rong", "intensity": 2, "intent": "explain"},
        ],
        "locations": {
            "nha_rong": "001.png"
        }
    }
    sb = load_storyboard(sb_dict)

    out_mp4 = tmp_path / "planned_out.mp4"
    pipe = StoryPipeline(
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work_planned",
    )

    res = pipe.render_story(
        storyboard_path_or_obj=sb,
        assets_dir=assets_dir,
        output_path=out_mp4,
        shot_mode="planned",
        burn_subtitles=True,
    )
    assert res.is_file()
    info = probe(out_mp4)
    assert info.has_video is True
    assert info.has_audio is True
    w, h = _get_video_resolution(out_mp4)
    assert w == 1080
    assert h == 1920


def test_generate_story_highlight_3words_no_cross_scene_bleed(tmp_path):
    """Kiểm thử kiểu phụ đề 3 chữ sáng từng từ: lẻ 1-2 chữ cuối câu KHÔNG mượn chữ cảnh sau."""
    import pysubs2
    from core.models import Segment, WordTiming

    seg1 = Segment(
        start=0.0,
        end=2.5,
        text="Quốc gia từng rất mạnh.",
        words=[
            WordTiming("Quốc", 0.0, 0.4),
            WordTiming("gia", 0.4, 0.8),
            WordTiming("từng", 0.8, 1.2),
            WordTiming("rất", 1.2, 1.7),
            WordTiming("mạnh.", 1.7, 2.2),
        ],
    )
    seg2 = Segment(
        start=2.8,
        end=4.5,
        text="Nhưng rồi sụp đổ.",
        words=[
            WordTiming("Nhưng", 2.8, 3.1),
            WordTiming("rồi", 3.1, 3.4),
            WordTiming("sụp", 3.4, 3.8),
            WordTiming("đổ.", 3.8, 4.2),
        ],
    )

    ass_out = tmp_path / "hl3.ass"
    generate_story_karaoke_ass(
        [seg1, seg2],
        ass_out,
        subtitle_style="highlight_3words",
        sub_color="#FFFF00",
        sub_margin_v=400,
    )
    subs = pysubs2.load(str(ass_out), encoding="utf-8")
    assert "Highlight3TikTok" in subs.styles
    # 5 từ cảnh 1 + 4 từ cảnh 2 = 9 event
    assert len(subs.events) == 9

    # Cảnh 1 cụm đầu (3 chữ): QUỐC GIA TỪNG — mỗi event chỉ sáng đúng 1 chữ bằng &H00FFFF&
    assert subs.events[0].plaintext == "QUỐC GIA TỪNG"
    assert r"{\1c&H00FFFF&}QUỐC{\1c&HFFFFFF&} GIA TỪNG" in subs.events[0].text
    assert r"QUỐC {\1c&H00FFFF&}GIA{\1c&HFFFFFF&} TỪNG" in subs.events[1].text
    assert r"QUỐC GIA {\1c&H00FFFF&}TỪNG{\1c&HFFFFFF&}" in subs.events[2].text

    # Cảnh 1 cụm cuối (lẻ 2 chữ): RẤT MẠNH — KHÔNG mượn chữ NHƯNG từ cảnh 2!
    assert subs.events[3].plaintext == "RẤT MẠNH"
    assert subs.events[4].plaintext == "RẤT MẠNH"
    assert "NHƯNG" not in subs.events[4].plaintext

    # Cảnh 2 cụm đầu (3 chữ) + cụm cuối (lẻ 1 chữ: ĐỔ)
    assert subs.events[5].plaintext == "NHƯNG RỒI SỤP"
    assert subs.events[8].plaintext == "ĐỔ"


def test_story_pipeline_full_audio_speed_1_2x_and_cache_preserved(tmp_path):
    """Kiểm thử tăng tốc 1.20x trên full_audio:
    - Thời lượng video + giọng nói giảm đúng tỉ lệ / 1.2
    - Mốc từ trong ASS (highlight_3words) khớp đúng mốc chia 1.2
    - Cache Whisper gốc 1.00x (align_*.json) được tái sử dụng và giữ nguyên không bị ghi đè.
    """
    import hashlib
    import pysubs2
    from core.cache import AudioCache
    from core.config import DubSettings
    from core.ffmpeg_utils import to_wav
    from core.story_pipeline import SR

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=navy:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "001.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=maroon:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    # Tạo full_audio.wav dài đúng 6.00s ở 1.00x
    full_audio = assets_dir / "full_audio.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=300:duration=6", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(full_audio)])

    s1_text = "Một hai ba"
    s2_text = "Bốn năm sáu"
    sb_data = {
        "title": "Speed 1.2x Full Audio Test",
        "aspect_ratio": "9:16",
        "scenes": [
            {"id": 1, "speech": s1_text, "motion": "zoom_in", "sfx": "none"},
            {"id": 2, "speech": s2_text, "motion": "pan_left", "sfx": "none"},
        ],
    }
    sb_file = tmp_path / "storyboard.json"
    sb_file.write_text(json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")

    # Tạo sẵn cache Whisper 1.00x khớp đúng khóa SHA-256 của bản wav 1.00x
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    ref_wav = tmp_path / "ref_1x.wav"
    to_wav(full_audio, ref_wav, SR, 1)
    full_prompt = f"{s1_text} {s2_text}"
    h = hashlib.sha256()
    h.update(b"whisper_align_v2|small|")
    h.update(full_prompt.encode("utf-8"))
    h.update(b"|")
    h.update(ref_wav.read_bytes())
    cache_file = cache_dir / f"align_{h.hexdigest()}.json"
    cache_payload = {
        "model": "small",
        "scenes": [
            [
                {"word": "Một", "start": 0.0, "end": 1.0},
                {"word": "hai", "start": 1.0, "end": 2.0},
                {"word": "ba", "start": 2.0, "end": 3.0},
            ],
            [
                {"word": "Bốn", "start": 3.0, "end": 4.0},
                {"word": "năm", "start": 4.0, "end": 5.0},
                {"word": "sáu", "start": 5.0, "end": 6.0},
            ],
        ],
    }
    cache_raw_before = json.dumps(cache_payload, ensure_ascii=False)
    cache_file.write_text(cache_raw_before, encoding="utf-8")

    out_mp4 = tmp_path / "out_1_2x.mp4"
    settings = DubSettings(whisper_model="small", keep_temp=True)
    pipe = StoryPipeline(
        settings=settings,
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work",
        cache=AudioCache(cache_dir),
    )

    res = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        subtitle_style="highlight_3words",
        burn_subtitles=True,
        speed=1.2,
    )
    assert res.is_file()

    # Thời lượng video & giọng nói giảm đúng 6.0 / 1.2 = 5.0s
    out_info = probe(out_mp4)
    assert out_info.has_video is True
    assert out_info.has_audio is True
    assert out_info.duration == pytest.approx(5.0, abs=0.25)

    # Kiểm tra file ASS highlight_3words có 6 event với mốc thời gian chia đúng cho 1.2 (trừ LEAD_MS=70ms của style)
    lead = 0.07
    subs = pysubs2.load(str(out_mp4.with_suffix(".ass")), encoding="utf-8")
    assert len(subs.events) == 6
    # Cảnh 1: 0.0 -> (1.0/1.2 - 0.07) -> (2.0/1.2 - 0.07) -> (3.0/1.2 - 0.07)
    assert subs.events[0].start / 1000.0 == pytest.approx(0.0, abs=0.05)
    assert subs.events[0].end / 1000.0 == pytest.approx(1.0 / 1.2 - lead, abs=0.05)
    # Cảnh 2 từ đầu ("Bốn"): (3.0/1.2 - 0.07) -> (4.0/1.2 - 0.07)
    assert subs.events[3].start / 1000.0 == pytest.approx(3.0 / 1.2 - lead, abs=0.05)
    assert subs.events[3].end / 1000.0 == pytest.approx(4.0 / 1.2 - lead, abs=0.05)
    # Từ cuối ("sáu") kết thúc khớp ~(6.0/1.2 - 0.07)
    assert subs.events[5].end / 1000.0 == pytest.approx(6.0 / 1.2 - lead, abs=0.05)

    # Cache gốc 1.00x còn nguyên vẹn 100%
    assert cache_file.read_text(encoding="utf-8") == cache_raw_before


def test_story_pipeline_bgm_mp4_longer_than_video_keeps_1x_and_trims(tmp_path):
    """Kiểm thử BGM đầu vào là file .mp4 dài 15s (hơn video 5s ở 1.20x):
    - Rút luồng âm thanh thành công (bỏ video)
    - Giữ nguyên tốc độ gốc 1.00x của BGM (tần số sine 440Hz không bị đẩy lên 528Hz)
    - Độ dài video đầu ra khớp đúng ~5s, KHÔNG bị kéo dài 15s theo BGM
    - File .mp4 không có luồng âm thanh báo lỗi DubberError rõ ràng.
    """
    import wave
    import numpy as np
    from core.config import DubSettings
    from core.models import WordTiming
    from core.story_pipeline import SR, _prepare_bgm_audio

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=darkgreen:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "001.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=darkblue:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    # Giọng đọc 6.0s -> khi render ở speed=1.20x sẽ thành đúng 5.0s
    full_audio = assets_dir / "full_audio.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=6", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(full_audio)])

    # Tạo file BGM .mp4 dài 15s gồm video + sóng sine 440Hz
    bgm_mp4 = tmp_path / "bgm_15s.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=15",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", str(bgm_mp4),
    ])
    assert probe(bgm_mp4).duration == pytest.approx(15.0, abs=0.4)

    # Tạo thêm 1 file .mp4 câm (không có luồng audio) để kiểm tra báo lỗi sạch
    silent_mp4 = tmp_path / "bgm_no_audio.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25:d=3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(silent_mp4),
    ])
    with pytest.raises(DubberError, match="không có luồng âm thanh"):
        _prepare_bgm_audio(silent_mp4, tmp_path, 5.0)

    sb = Storyboard(
        title="BGM MP4 1x Trim Test",
        scenes=[
            StoryScene(
                id=1, speech="Cảnh một ngắn gọn", start_time=0.0, end_time=3.0, duration_sec=3.0,
                words=[WordTiming("Cảnh", 0.0, 0.8), WordTiming("một", 0.8, 1.5), WordTiming("ngắn", 1.5, 2.2), WordTiming("gọn", 2.2, 3.0)],
            ),
            StoryScene(
                id=2, speech="Cảnh hai kết thúc", start_time=3.0, end_time=6.0, duration_sec=3.0,
                words=[WordTiming("Cảnh", 3.0, 3.8), WordTiming("hai", 3.8, 4.5), WordTiming("kết", 4.5, 5.2), WordTiming("thúc", 5.2, 6.0)],
            ),
        ],
    )

    work_root = tmp_path / "work_bgm_mp4"
    out_mp4 = tmp_path / "out_bgm_mp4.mp4"
    pipe = StoryPipeline(
        settings=DubSettings(keep_temp=True),
        tts_engine=ToneTTS(),
        work_root=work_root,
    )

    res = pipe.render_story(
        storyboard_path_or_obj=sb,
        assets_dir=assets_dir,
        output_path=out_mp4,
        bgm_path=bgm_mp4,
        bgm_volume=0.30,
        burn_subtitles=True,
        subtitle_style="highlight_3words",
        speed=1.2,
    )
    assert res.is_file()

    # Đầu ra khớp ~5.0s, tuyệt đối không bị kéo dài 15s theo BGM .mp4
    out_info = probe(out_mp4)
    assert out_info.has_video is True
    assert out_info.has_audio is True
    assert out_info.duration == pytest.approx(5.0, abs=0.35)
    assert out_info.duration < 7.0

    # Kiểm chứng file bgm_prepared_1x.wav trong workdir giữ đúng tốc độ 1.00x (tần số chủ đạo 440Hz, KHÔNG phải 528Hz)
    bgm_wavs = list(work_root.glob("**/bgm_prepared_1x.wav"))
    assert len(bgm_wavs) == 1
    assert probe(bgm_wavs[0]).duration == pytest.approx(5.0, abs=0.2)
    with wave.open(str(bgm_wavs[0]), "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        samples = np.frombuffer(wf.readframes(min(sr * 2, wf.getnframes())), dtype=np.int16).astype(np.float32)
        if nch > 1:
            samples = samples[0::nch]
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sr)
    peak_freq = float(freqs[int(np.argmax(np.abs(np.fft.rfft(samples))))])
    assert peak_freq == pytest.approx(440.0, abs=8.0)


def test_story_pipeline_full_audio_forces_whisper_when_duration_sec_present_but_words_empty(tmp_path, monkeypatch):
    """Khóa lỗi cũ: khi full_audio có sẵn và storyboard đã điền ước lượng duration_sec > 0
    nhưng sc.words còn rỗng, pipeline VẪN bắt buộc gọi _align_full_audio_with_whisper.
    """
    import core.story_pipeline as sp
    from core.models import WordTiming
    from core.story_pipeline import SR

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=gray:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "001.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    full_audio = assets_dir / "full_audio.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=250:duration=4", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(full_audio)])

    sb_data = {
        "title": "Force Whisper Align Regression Test",
        "scenes": [
            {"id": 1, "speech": "Cảnh một có duration ước lượng", "start_time": 0.0, "end_time": 3.0, "duration_sec": 3.0},
            {"id": 2, "speech": "Cảnh hai có duration ước lượng", "start_time": 3.0, "end_time": 6.0, "duration_sec": 3.0},
        ],
    }
    sb_file = tmp_path / "sb.json"
    sb_file.write_text(json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")

    called = {"count": 0, "total_dur": 0.0}

    def fake_whisper_align(audio_wav, scenes, total_duration, **kwargs):
        called["count"] += 1
        called["total_dur"] = total_duration
        scenes[0].words = [
            WordTiming("Cảnh", 0.0, 0.4), WordTiming("một", 0.4, 0.8),
            WordTiming("có", 0.8, 1.2), WordTiming("duration", 1.2, 1.6),
            WordTiming("ước", 1.6, 1.8), WordTiming("lượng", 1.8, 2.0),
        ]
        scenes[0].start_time, scenes[0].end_time, scenes[0].duration_sec = 0.0, 2.0, 2.0
        scenes[1].words = [
            WordTiming("Cảnh", 2.0, 2.4), WordTiming("hai", 2.4, 2.8),
            WordTiming("có", 2.8, 3.2), WordTiming("duration", 3.2, 3.6),
            WordTiming("ước", 3.6, 3.8), WordTiming("lượng", 3.8, 4.0),
        ]
        scenes[1].start_time, scenes[1].end_time, scenes[1].duration_sec = 2.0, 4.0, 2.0
        return True

    monkeypatch.setattr(sp, "_align_full_audio_with_whisper", fake_whisper_align)

    out_mp4 = tmp_path / "force_whisper.mp4"
    pipe = StoryPipeline(tts_engine=ToneTTS(), work_root=tmp_path / "work")
    res = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        burn_subtitles=False,
    )
    assert res.is_file()
    assert called["count"] == 1
    assert called["total_dur"] == pytest.approx(4.0, abs=0.2)
    assert probe(out_mp4).duration == pytest.approx(4.0, abs=0.3)


def test_sfx_v52_tags_resolve_and_produce_audio(tmp_path):
    """Mọi tag SFX V5.2 hợp lệ đều được chấp nhận, ưu tiên file thật và tự tổng hợp sạch khi thiếu file."""
    import numpy as np
    from core.audio_track import SR, read_wav, write_wav
    from core.config import DubSettings
    from core.ffmpeg_utils import to_wav
    from core.models import WordTiming
    from core.sfx_engine import SFX_VALID_TAGS, load_or_synthesize_sfx

    v52_tags = ("boom", "cinematic_impact", "artillery_fire", "whoosh", "tension_riser", "battle_charge")
    for tag in ("impact", "cannon", "explosion", "gunshot", "heartbeat", "wind", "dramatic_hit", "transition", "riser", "march"):
        assert tag in SFX_VALID_TAGS

    empty_sfx_dir = tmp_path / "empty_sfx"
    empty_sfx_dir.mkdir()

    for tag in v52_tags:
        samples, src = load_or_synthesize_sfx(tag, [empty_sfx_dir], tmp_path, sample_rate=SR)
        assert samples is not None and samples.size > 0
        assert src.startswith("synth:")
        assert np.all(np.isfinite(samples))
        rms = float(np.sqrt(np.mean(samples ** 2)))
        assert rms > 0.05, f"SFX tổng hợp '{tag}' quá nhỏ: rms={rms}"

    # Ưu tiên file thật trên đĩa nếu có
    file_sfx_dir = tmp_path / "real_sfx"
    file_sfx_dir.mkdir()
    custom_wav = file_sfx_dir / "boom.wav"
    t = np.arange(int(0.4 * SR), dtype=np.float32) / SR
    write_wav(custom_wav, 0.75 * np.sin(2.0 * np.pi * 100.0 * t))
    loaded, src_real = load_or_synthesize_sfx("boom", [file_sfx_dir], tmp_path, sample_rate=SR)
    assert loaded is not None and src_real == "file:boom.wav"

    # End-to-end FFmpeg với storyboard dùng toàn bộ tag V5.2 và thư mục SFX trống
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    scenes = []
    for idx, tag in enumerate(v52_tags, start=1):
        run_ffmpeg([
            "-f", "lavfi", "-i", "color=c=navy:s=540x960:d=1",
            "-vframes", "1", str(assets_dir / f"{idx:03d}.png"),
        ])
        st = float(idx - 1) * 1.2
        en = st + 1.2
        scenes.append(
            StoryScene(
                id=idx,
                speech=f"Cảnh {idx}",
                sfx=tag,
                start_time=st,
                end_time=en,
                duration_sec=1.2,
                words=[WordTiming("Cảnh", st, st + 0.6), WordTiming(str(idx), st + 0.6, en)],
            )
        )

    total_dur = len(v52_tags) * 1.2
    write_wav(assets_dir / "full_audio.wav", np.zeros(int(round(total_dur * SR)), dtype=np.float32))
    sb = Storyboard(title="V52_SFX_All_Tags", scenes=scenes)

    out_mp4 = tmp_path / "v52_sfx.mp4"
    pipe = StoryPipeline(
        settings=DubSettings(keep_temp=False),
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work",
        sfx_dir=empty_sfx_dir,
    )
    res = pipe.render_story(
        sb,
        assets_dir=assets_dir,
        output_path=out_mp4,
        burn_subtitles=False,
        loudness_norm=False,
        anti_ai=False,
        sfx_volume=1.0,
    )
    assert res.is_file()
    decoded = tmp_path / "v52_decoded.wav"
    to_wav(out_mp4, decoded, SR, 1)
    pcm = read_wav(decoded)
    assert float(np.sqrt(np.mean(pcm ** 2))) > 0.05


def test_sfx_volume_scaling_zero_and_no_clipping(tmp_path):
    """Kiểm chứng sfx_volume: 1.20 > 0.30, 0.0 tắt hoàn toàn và alimiter giữ không rè/clip ở 1.50."""
    import numpy as np
    from core.audio_track import SR, read_wav, write_wav
    from core.config import DubSettings
    from core.ffmpeg_utils import to_wav
    from core.models import WordTiming

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    empty_sfx = tmp_path / "empty_sfx"
    empty_sfx.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=maroon:s=540x960:d=1", "-vframes", "1", str(assets_dir / "001.png")])

    dur = 2.4
    write_wav(assets_dir / "full_audio.wav", np.zeros(int(round(dur * SR)), dtype=np.float32))

    def render_at(vol: float, name: str) -> np.ndarray:
        sb = Storyboard(
            title=f"SFX_Vol_{name}",
            scenes=[
                StoryScene(
                    id=1,
                    speech="Thử âm lượng",
                    sfx="cinematic_impact",
                    start_time=0.0,
                    end_time=dur,
                    duration_sec=dur,
                    words=[WordTiming("Thử", 0.0, 0.8), WordTiming("âm", 0.8, 1.6), WordTiming("lượng", 1.6, dur)],
                )
            ],
        )
        out_mp4 = tmp_path / f"out_{name}.mp4"
        pipe = StoryPipeline(
            settings=DubSettings(),
            tts_engine=ToneTTS(),
            work_root=tmp_path / f"w_{name}",
            sfx_dir=empty_sfx,
        )
        pipe.render_story(
            sb,
            assets_dir=assets_dir,
            output_path=out_mp4,
            burn_subtitles=False,
            loudness_norm=False,
            anti_ai=False,
            sfx_volume=vol,
        )
        wav_dec = tmp_path / f"dec_{name}.wav"
        to_wav(out_mp4, wav_dec, SR, 1)
        return read_wav(wav_dec)

    pcm_zero = render_at(0.0, "zero")
    pcm_low = render_at(0.30, "low")
    pcm_high = render_at(1.20, "high")

    win_a, win_b = int(0.02 * SR), int(0.80 * SR)
    rms_zero = float(np.sqrt(np.mean(pcm_zero[win_a:win_b] ** 2)))
    rms_low = float(np.sqrt(np.mean(pcm_low[win_a:win_b] ** 2)))
    rms_high = float(np.sqrt(np.mean(pcm_high[win_a:win_b] ** 2)))

    assert rms_zero < 0.005
    assert rms_low > max(rms_zero * 5.0, 0.02)
    assert rms_high > rms_low * 2.0

    # Stress test: giọng lớn + artillery_fire ở mức 150% + anti_ai vẫn không clipping quá 0 dBFS
    t = np.arange(int(round(dur * SR)), dtype=np.float32) / SR
    write_wav(assets_dir / "full_audio.wav", 0.40 * np.sin(2.0 * np.pi * 180.0 * t))
    sb_hot = Storyboard(
        title="SFX_Hot_Mix_Stress",
        scenes=[
            StoryScene(
                id=1,
                speech="Thử đỉnh",
                sfx="artillery_fire",
                start_time=0.0,
                end_time=dur,
                duration_sec=dur,
                words=[WordTiming("Thử", 0.0, 1.2), WordTiming("đỉnh", 1.2, dur)],
            )
        ],
    )
    out_hot = tmp_path / "out_hot.mp4"
    StoryPipeline(
        settings=DubSettings(),
        tts_engine=ToneTTS(),
        work_root=tmp_path / "w_hot",
        sfx_dir=empty_sfx,
    ).render_story(
        sb_hot,
        assets_dir=assets_dir,
        output_path=out_hot,
        burn_subtitles=False,
        loudness_norm=False,
        anti_ai_level="light",
        sfx_volume=1.50,
    )
    wav_hot = tmp_path / "dec_hot.wav"
    to_wav(out_hot, wav_hot, SR, 1)
    pcm_hot = read_wav(wav_hot)
    assert float(np.max(np.abs(pcm_hot))) <= 0.995


def test_sfx_synced_with_speed_1_20x(tmp_path):
    """Ở tốc độ 1.20x, SFX ở cảnh 2 phải kích đúng mốc 3.0 / 1.2 = 2.50s và không kéo dài MP4."""
    import numpy as np
    from core.audio_track import SR, read_wav, write_wav
    from core.config import DubSettings
    from core.ffmpeg_utils import to_wav
    from core.models import WordTiming

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    empty_sfx = tmp_path / "empty_sfx"
    empty_sfx.mkdir()
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=540x960:d=1", "-vframes", "1", str(assets_dir / "001.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=gray:s=540x960:d=1", "-vframes", "1", str(assets_dir / "002.png")])

    write_wav(assets_dir / "full_audio.wav", np.zeros(int(round(6.0 * SR)), dtype=np.float32))
    sb = Storyboard(
        title="SFX_Speed_120_Sync",
        scenes=[
            StoryScene(
                id=1, speech="Cảnh một", sfx="none",
                start_time=0.0, end_time=3.0, duration_sec=3.0,
                words=[WordTiming("Cảnh", 0.0, 1.5), WordTiming("một", 1.5, 3.0)],
            ),
            StoryScene(
                id=2, speech="Cảnh hai", sfx="artillery_fire",
                start_time=3.0, end_time=6.0, duration_sec=3.0,
                words=[WordTiming("Cảnh", 3.0, 4.5), WordTiming("hai", 4.5, 6.0)],
            ),
        ],
    )

    out_mp4 = tmp_path / "speed_sfx.mp4"
    pipe = StoryPipeline(
        settings=DubSettings(),
        tts_engine=ToneTTS(),
        work_root=tmp_path / "w_spd",
        sfx_dir=empty_sfx,
    )
    pipe.render_story(
        sb,
        assets_dir=assets_dir,
        output_path=out_mp4,
        speed=1.20,
        sfx_volume=1.0,
        burn_subtitles=False,
        loudness_norm=False,
        anti_ai=False,
    )

    info = probe(out_mp4)
    assert info.duration == pytest.approx(5.0, abs=0.30)

    wav_dec = tmp_path / "dec_spd.wav"
    to_wav(out_mp4, wav_dec, SR, 1)
    pcm = read_wav(wav_dec)

    rms_before = float(np.sqrt(np.mean(pcm[int(0.5 * SR) : int(2.2 * SR)] ** 2)))
    rms_scaled_trigger = float(np.sqrt(np.mean(pcm[int(2.52 * SR) : int(2.88 * SR)] ** 2)))

    assert rms_before < 0.005
    assert rms_scaled_trigger > 0.08
    assert rms_scaled_trigger > rms_before * 15.0


def test_settings_sfx_volume_backward_compat(tmp_path):
    """DubSettings nạp settings.json cũ giữ mặc định 0.85 và kẹp âm lượng SFX an toàn [0.0, 1.5]."""
    from core.config import DubSettings

    old_cfg = tmp_path / "old_settings.json"
    old_cfg.write_text(json.dumps({"story_bgm_volume": 0.25}), encoding="utf-8")
    loaded = DubSettings.load(old_cfg)
    assert loaded.story_sfx_volume == pytest.approx(0.85)
    assert loaded.story_render_kwargs()["sfx_volume"] == pytest.approx(0.85)

    low = DubSettings(story_sfx_volume=-0.4)
    assert low.story_render_kwargs()["sfx_volume"] == pytest.approx(0.0)
    high = DubSettings(story_sfx_volume=2.8)
    assert high.story_render_kwargs()["sfx_volume"] == pytest.approx(1.5)


def test_story_pipeline_selective_ai_filter(tmp_path, monkeypatch):
    """Selective Anti-AI: cảnh tư liệu thật trong archive_sources.json giữ nét gốc 100%,
    chỉ cảnh AI áp lọc khử bóng HDR nhẹ; cuối luồng giữ -map_metadata -1 sạch và phụ đề sắc nét.
    """
    import core.story_pipeline as sp
    from core.config import DubSettings
    from core.reporter import Reporter

    ep_dir = tmp_path / "Dien_Bien_Phu_Tap_04"
    assets_dir = ep_dir / "assets"
    assets_dir.mkdir(parents=True)

    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x888888:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "001.jpg")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x446688:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "002.png")])
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x334455:s=720x1280:d=1", "-vframes", "1", str(assets_dir / "003.jpg")])

    # Cảnh 1 là ảnh tư liệu thật từ archive_sources.json; Cảnh 2 là AI cũ; Cảnh 3 là AI đã qua House Look V5.8
    (ep_dir / "archive_sources.json").write_text(
        json.dumps(
            {
                "episode": "Dien_Bien_Phu_Tap_04",
                "assets": {
                    "001": {
                        "scene_id": 1,
                        "asset_file": "assets/001.jpg",
                        "media_type": "image",
                        "provider": "wikimedia_commons",
                        "license": "Public domain",
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (ep_dir / "pipeline_state.json").write_text(
        json.dumps(
            {
                "title": "Selective_AntiAI_Test",
                "scenes": [
                    {"id": 1, "image_status": "done", "house_look_applied": False},
                    {"id": 2, "image_status": "done", "house_look_applied": False},
                    {"id": 3, "image_status": "done", "house_look_applied": True},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sb_file = ep_dir / "storyboard.json"
    sb_file.write_text(
        json.dumps(
            {
                "title": "Selective_AntiAI_Test",
                "scenes": [
                    {"id": 1, "speech": "Ảnh tư liệu lịch sử thật.", "motion": "zoom_in", "sfx": "none"},
                    {"id": 2, "speech": "Cảnh tái hiện bằng AI cũ.", "motion": "pan_left", "sfx": "none"},
                    {"id": 3, "speech": "Cảnh AI đã qua House Look V5.8.", "motion": "zoom_out", "sfx": "none"},
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

    logs: list[str] = []
    out_mp4 = ep_dir / "out_selective.mp4"
    pipe = StoryPipeline(
        settings=DubSettings(keep_temp=False),
        reporter=Reporter(on_log=lambda msg, lv="info": logs.append(msg)),
        tts_engine=ToneTTS(),
        work_root=tmp_path / "work",
    )
    res = pipe.render_story(
        storyboard_path_or_obj=sb_file,
        assets_dir=assets_dir,
        output_path=out_mp4,
        burn_subtitles=True,
        subtitle_style="highlight_3words",
        anti_ai_level="medium",
        cinematic_grade=False,
        loudness_norm=False,
    )
    assert res.is_file()
    out_info = probe(out_mp4)
    assert out_info.has_video is True and out_info.has_audio is True

    cmd_c1 = next(c for c in recorded_cmds if any(a.endswith("clip_001.mp4") for a in c))
    cmd_c2 = next(c for c in recorded_cmds if any(a.endswith("clip_002.mp4") for a in c))
    cmd_c3 = next(c for c in recorded_cmds if any(a.endswith("clip_003.mp4") for a in c))
    cmd_mix = next(c for c in recorded_cmds if any(a.endswith("out_selective.mp4") for a in c))

    vf_c1 = cmd_c1[cmd_c1.index("-vf") + 1]
    vf_c2 = cmd_c2[cmd_c2.index("-vf") + 1]
    vf_c3 = cmd_c3[cmd_c3.index("-vf") + 1]
    fc_mix = cmd_mix[cmd_mix.index("-filter_complex") + 1]

    # Cảnh 1 (tư liệu thật): KHÔNG dùng lọc AI depolish/crop/curves/grain; chỉ hòa tông nhẹ ~15% non-destructive lúc render
    assert "curves=" not in vf_c1
    assert "eq=saturation=" not in vf_c1
    assert "noise=" not in vf_c1
    assert "vignette=" not in vf_c1
    assert "hqdn3d=" not in vf_c1
    assert "unsharp" not in vf_c1
    assert "eq=contrast=1.04:brightness=-0.02" in vf_c1
    assert "colorbalance=rs=-0.018:gs=0.00:bs=0.026" in vf_c1

    # Cảnh 2 (AI cũ chưa qua house_look): CÓ áp House Look V5.8 Bleach-Bypass đầy đủ và KHÔNG dùng unsharp
    assert "hqdn3d=0.8:0.6:1.5:1.2" in vf_c2
    assert "eq=saturation=0.52" in vf_c2
    assert "curves=all='0/0 0.25/0.20 0.5/0.45 0.85/0.82 1/0.95'" in vf_c2
    assert "colorbalance=rs=-0.04:gs=0.00:bs=0.06:rm=-0.03:gm=0.00:bm=0.05:rh=0.02:gh=0.01:bh=-0.01" in vf_c2
    assert "noise=alls=7:allf=t+u" in vf_c2
    assert "vignette=PI/6" in vf_c2
    assert "crop=1080:1920:" in vf_c2
    assert "unsharp" not in vf_c2

    # Cảnh 3 (AI mới đã qua house_look_applied=True): Chế độ pass-through chống Double-Grading
    assert "crop=1080:1920:" in vf_c3
    assert "vignette=PI/6" in vf_c3
    assert "eq=saturation=" not in vf_c3
    assert "curves=" not in vf_c3
    assert "colorbalance=" not in vf_c3
    assert "noise=" not in vf_c3
    assert "hqdn3d=" not in vf_c3
    assert "unsharp" not in vf_c3

    # Mix cuối: KHÔNG làm đục hình toàn cục hay cắt/echo giọng, vẫn đốt ASS & giữ -map_metadata -1
    assert "rotate=" not in fc_mix
    assert "anoisesrc=" not in fc_mix
    assert "ass=subs.ass" in fc_mix
    assert "-map_metadata" in cmd_mix and "-1" in cmd_mix
    assert any("[Anti-AI Selective]" in line for line in logs)






