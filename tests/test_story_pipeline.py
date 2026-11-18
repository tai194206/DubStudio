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

