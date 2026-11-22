"""Kiểm thử tự động (Unit + E2E offline) cho Lõi Biên Tập Series YouTube 16:9 (`core/series_compiler.py`).

100% offline: sử dụng ảnh tạo bằng Pillow và giọng `ToneTTS` nội bộ của DubStudio,
không gọi mạng hay tải mô hình Whisper.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from core.anti_ai_filter import probe_video_details
from core.audio_track import SR, read_wav, write_wav
from core.cache import AudioCache
from core.ffmpeg_utils import _NO_WINDOW, find_binary, get_ffmpeg, probe, run_ffmpeg
from core.series_compiler import (
    SeriesCompileConfig,
    audit_series_licenses,
    build_youtube_16x9_scene_vf,
    compile_series_youtube,
    format_youtube_timestamp,
    render_chapter_card_image,
    render_end_credits_image,
    resolve_youtube_16x9_layout,
    scan_series_folder,
)
from core.subtitles import parse_script
from core.tts import ToneTTS


def _make_solid_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(str(path), format="PNG")
    return path


def _make_storyboard(ep_dir: Path, title: str, scenes: list[dict]) -> Path:
    ep_dir.mkdir(parents=True, exist_ok=True)
    sb_path = ep_dir / "storyboard.json"
    sb_path.write_text(
        json.dumps({"version": "2.0", "title": title, "scenes": scenes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return sb_path


def _synthesize_full_audio_and_alignment(
    speeches: list[str],
    out_wav: Path,
    out_align_json: Path,
    tmp_dir: Path,
) -> float:
    """Dùng ToneTTS ghép chuỗi lời đọc các cảnh thành full_audio.wav + alignment.json chuẩn ký tự."""
    tts = ToneTTS()
    chunks: list[np.ndarray] = []
    chars: list[str] = []
    c_starts: list[float] = []
    c_ends: list[float] = []

    gap = np.zeros(int(round(0.25 * SR)), dtype=np.float32)
    cursor = 0.0

    for idx, text in enumerate(speeches):
        sc_wav = tmp_dir / f"_tone_{idx:02d}.wav"
        word_timings = tts.synthesize(text, sc_wav)
        samples = read_wav(sc_wav)
        dur = samples.size / float(SR)

        if not word_timings:
            tokens = text.split()
            step = dur / max(1, len(tokens))
            word_timings_pairs = [(tok, i * step, (i + 1) * step) for i, tok in enumerate(tokens)]
        else:
            word_timings_pairs = [(w.word, w.start, w.end) for w in word_timings]

        for w_idx, (word_str, ws, we) in enumerate(word_timings_pairs):
            if w_idx > 0 or idx > 0:
                sp_t = round(cursor + ws, 4)
                chars.append(" ")
                c_starts.append(sp_t)
                c_ends.append(sp_t)
            w_dur = max(0.05, we - ws)
            per_ch = w_dur / max(1, len(word_str))
            for ch_i, ch in enumerate(word_str):
                st = round(cursor + ws + ch_i * per_ch, 4)
                en = round(cursor + ws + (ch_i + 1) * per_ch, 4)
                chars.append(ch)
                c_starts.append(st)
                c_ends.append(en)

        chunks.append(samples)
        cursor += dur
        if idx < len(speeches) - 1:
            chunks.append(gap)
            cursor += gap.size / float(SR)

    full_samples = np.concatenate(chunks) if chunks else np.zeros(SR, dtype=np.float32)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out_wav, full_samples)
    out_align_json.write_text(
        json.dumps(
            {
                "characters": chars,
                "character_start_times_seconds": c_starts,
                "character_end_times_seconds": c_ends,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return full_samples.size / float(SR)


def _inspect_media_tags(path: Path) -> str:
    chunks: list[str] = []
    ffprobe_bin = find_binary("ffprobe")
    if ffprobe_bin:
        res_p = subprocess.run(
            [ffprobe_bin, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
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


# ==============================================================================
# 1. Quét Series đa tập, sắp xếp tự nhiên & phát hiện cảnh CTA
# ==============================================================================
def test_scan_series_folder_natural_sort_and_cta_detection(tmp_path):
    series_dir = tmp_path / "Series_Lich_Su"
    series_dir.mkdir()

    # Đặt tên dễ lệch nếu sort chữ cái (Tap_1, Tap_10, Tap_2)
    specs = [
        ("Tap_10", "Tap_10_Ket_Thuc", "Hãy đăng ký và bình luận suy nghĩ của bạn nhé!"),
        ("Tap_2", "Tap_02_Cao_Trao", "Hãy bình luận dự đoán cho phần tiếp theo."),
        ("Tap_1", "Tap_01_Khoi_Dau", "Đừng bỏ lỡ tập sau, hãy theo dõi kênh ngay!"),
    ]
    for folder_name, title, last_speech in specs:
        ep = series_dir / folder_name
        assets = ep / "assets"
        _make_solid_image(assets / "001.png", (1600, 900), (50, 80, 120))
        _make_solid_image(assets / "002.png", (720, 1280), (90, 60, 40))
        _make_storyboard(
            ep,
            title,
            [
                {"id": 1, "speech": "Mở đầu câu chuyện lịch sử."},
                {"id": 2, "speech": last_speech},
            ],
        )

    # Thêm full_audio giả ở Tap_1 và archive_sources ở Tap_2
    (series_dir / "Tap_1" / "assets" / "full_audio.wav").write_bytes(b"RIFFdummy")
    (series_dir / "Tap_2" / "archive_sources.json").write_text(
        json.dumps({"assets": {"001": {"scene_id": 1, "license": "Public domain"}}}),
        encoding="utf-8",
    )

    entries = scan_series_folder(series_dir)
    assert [e.episode_id for e in entries] == ["Tap_1", "Tap_2", "Tap_10"]

    assert entries[0].chapter_title == "Khoi Dau"
    assert entries[0].has_full_audio is True
    assert entries[0].missing_assets == []
    assert entries[0].suggested_skip_scenes == [2]

    assert entries[1].archival_count == 1
    assert entries[1].suggested_skip_scenes == [2]

    # Tập cuối cùng luôn giữ nguyên cảnh cuối (không tự động gợi ý cắt CTA)
    assert entries[2].suggested_skip_scenes == []


# ==============================================================================
# 2. Kiểm tra bản quyền: khử trùng lặp & cảnh báo rủi ro thương mại
# ==============================================================================
def test_audit_series_licenses_dedup_and_risky_warnings(tmp_path):
    ep1 = tmp_path / "Tap_01"
    ep2 = tmp_path / "Tap_02"
    ep1.mkdir()
    ep2.mkdir()

    # Tập 1: 1 nguồn CC BY-SA (sẽ trùng sha256 với Tập 2), 1 nguồn Public Domain (sẽ trùng URL),
    # 1 nguồn CC BY-NC (rủi ro), 1 nguồn chữ "Canada/French" sạch (không cảnh báo giả)
    (ep1 / "archive_sources.json").write_text(
        json.dumps(
            {
                "assets": {
                    "001": {
                        "scene_id": 1,
                        "title": "File:Bridge_1954.jpg",
                        "author": "Adam Jones from Canada",
                        "license": "CC BY-SA 2.0",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Bridge_1954.jpg",
                        "sha256": "aaa111",
                    },
                    "002": {
                        "scene_id": 2,
                        "title": "File:Bunker_1954.jpg",
                        "author": "French Army Archive",
                        "license": "Public domain",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Bunker_1954.jpg",
                    },
                    "003": {
                        "scene_id": 3,
                        "title": "File:Photo_NC.jpg",
                        "author": "Photographer X",
                        "license": "CC BY-NC 4.0",
                        "source_url": "https://example.org/nc.jpg",
                        "sha256": "nc_hash",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Tập 2: lặp lại sha256 aaa111, lặp lại URL Bunker_1954.jpg, cộng thêm Associated Press + Getty + All rights reserved
    (ep2 / "archive_sources.json").write_text(
        json.dumps(
            {
                "assets": {
                    "001": {
                        "scene_id": 1,
                        "title": "File:Bridge_1954_copy.jpg",
                        "author": "Adam Jones from Canada",
                        "license": "CC BY-SA 2.0",
                        "source_url": "",
                        "sha256": "AAA111",
                    },
                    "002": {
                        "scene_id": 2,
                        "title": "File:Bunker_1954.jpg",
                        "author": "French Army Archive",
                        "license": "Public domain",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Bunker_1954.jpg/",
                    },
                    "003": {
                        "scene_id": 3,
                        "title": "AP Historical Photo",
                        "author": "Associated Press",
                        "license": "All rights reserved",
                        "source_url": "https://news.example.com/ap1",
                        "sha256": "ap_hash",
                    },
                    "004": {
                        "scene_id": 4,
                        "title": "Getty Collection",
                        "provider": "Getty Images",
                        "license": "Editorial",
                        "source_url": "https://news.example.com/getty1",
                        "sha256": "getty_hash",
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = audit_series_licenses([ep1, ep2])
    assert report.total_raw_entries == 7
    assert report.total_unique_sources == 5
    assert len(report.cc_attribution_required) == 1
    assert sorted(report.cc_attribution_required[0].episodes) == ["Tap_01", "Tap_02"]
    assert len(report.public_domain) == 1
    assert sorted(report.public_domain[0].episodes) == ["Tap_01", "Tap_02"]

    assert report.has_risky_warnings is True
    assert len(report.risky_warnings) == 3
    all_reasons = " | ".join(r for item in report.risky_warnings for r in item.risk_reasons)
    assert "NC" in all_reasons
    assert "Associated Press" in all_reasons
    assert "All rights reserved" in all_reasons
    assert "Getty" in all_reasons


# ==============================================================================
# 3. Lên khung 16:9 thông minh (Full-bleed vs Blurred Pillar-box) & Anti-AI chọn lọc
# ==============================================================================
def test_build_youtube_16x9_scene_vf_smart_and_selective_anti_ai(tmp_path):
    land_img = _make_solid_image(tmp_path / "land.png", (1600, 900), (20, 90, 140))
    port_img = _make_solid_image(tmp_path / "port.png", (720, 1280), (180, 70, 50))
    sq_img = _make_solid_image(tmp_path / "square.png", (1080, 1080), (60, 130, 80))

    assert resolve_youtube_16x9_layout(1600, 900, "smart") == "full_bleed"
    assert resolve_youtube_16x9_layout(720, 1280, "smart") == "pillarbox"
    assert resolve_youtube_16x9_layout(1080, 1080, "smart") == "pillarbox"
    assert resolve_youtube_16x9_layout(1200, 900, "pillarbox") == "pillarbox"
    assert resolve_youtube_16x9_layout(720, 1280, "full_bleed") == "full_bleed"

    vf_land = build_youtube_16x9_scene_vf(
        land_img, duration=1.0, layout_mode="smart", is_archival=False, anti_ai_level="light"
    )
    assert "gblur" not in vf_land
    assert "overlay" not in vf_land
    assert "1920:1080" in vf_land
    assert "curves=" in vf_land and "noise=" in vf_land

    # Cảnh tư liệu thật phải được miễn lọc khử bóng Anti-AI hoàn toàn
    vf_port_archival = build_youtube_16x9_scene_vf(
        port_img, duration=1.0, layout_mode="smart", is_archival=True, anti_ai_level="strong"
    )
    assert "split=2[bg][fg]" in vf_port_archival
    assert "gblur=" in vf_port_archival
    assert "eq=brightness=-0.22:saturation=0.75" in vf_port_archival
    assert "overlay=(W-w)/2:(H-h)/2" in vf_port_archival
    assert "curves=" not in vf_port_archival
    assert "noise=" not in vf_port_archival

    # Render thực tế qua FFmpeg xác nhận cả ảnh ngang lẫn ảnh dọc/vuông đều xuất chuẩn 1920x1080
    for idx, (img_p, vf) in enumerate([(land_img, vf_land), (port_img, vf_port_archival), (sq_img, build_youtube_16x9_scene_vf(sq_img, duration=0.8, layout_mode="smart"))]):
        out_mkv = tmp_path / f"scene_{idx}.mkv"
        run_ffmpeg([
            "-framerate", "1", "-loop", "1", "-i", str(img_p),
            "-vf", vf, "-t", "0.6", "-r", "30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an",
            str(out_mkv),
        ])
        info = probe_video_details(out_mkv)
        assert (info.width, info.height) == (1920, 1080)
        assert round(info.fps) == 30


# ==============================================================================
# 4. Vẽ Thẻ chương & End Credits tiếng Việt bằng Pillow (không FFmpeg drawtext)
# ==============================================================================
def test_render_chapter_card_and_end_credits_vietnamese_pillow(tmp_path):
    card_path = tmp_path / "chapter_01.png"
    render_chapter_card_image(
        1,
        "Cái bẫy lòng chảo Điện Biên Phủ và quyết định lịch sử",
        card_path,
        series_title="Chiến dịch Điện Biên Phủ 1954",
        subtitle="Tư liệu lịch sử tổng hợp",
    )
    assert card_path.is_file()
    with Image.open(card_path) as im:
        assert im.size == (1920, 1080)
        assert im.mode == "RGB"
        arr = np.asarray(im)
        assert arr.std() > 5.0  # Có chữ và viền vẽ thực tế, không phải ảnh đen trơn

    # Tạo báo cáo giả >10 mục để kiểm tra rút gọn End Credits
    ep_dir = tmp_path / "Tap_01"
    ep_dir.mkdir()
    assets_dict = {}
    for i in range(1, 14):
        assets_dict[f"{i:03d}"] = {
            "scene_id": i,
            "title": f"File:Tu_lieu_lich_su_so_{i:02d}_Viet_Nam_1954.jpg",
            "author": f"Tác giả số {i}",
            "license": "CC BY-SA 2.0" if i <= 8 else "Public domain",
            "source_url": f"https://commons.wikimedia.org/wiki/File:Pic_{i}.jpg",
            "sha256": f"hash_{i:03d}",
        }
    (ep_dir / "archive_sources.json").write_text(json.dumps({"assets": assets_dict}, ensure_ascii=False), encoding="utf-8")
    report = audit_series_licenses([ep_dir])

    credits_path = tmp_path / "end_credits.png"
    render_end_credits_image(report, credits_path, series_title="Điện Biên Phủ 1954", channel_name="Sử Việt 16:9")
    assert credits_path.is_file()
    with Image.open(credits_path) as im:
        assert im.size == (1920, 1080)
        assert np.asarray(im).std() > 5.0


# ==============================================================================
# 5. E2E Offline: Ghép 2 tập (cắt CTA full_audio + archive + 1.10x + BGM + loudnorm)
# ==============================================================================
def test_e2e_compile_series_youtube_two_episodes(tmp_path):
    series_dir = tmp_path / "Dien_Bien_Test"
    series_dir.mkdir()

    # --- Tập 1: Dùng full_audio.wav + alignment.json, có cảnh 3 CTA phải bị cắt ---
    ep1 = series_dir / "Tap_01"
    ep1_assets = ep1 / "assets"
    _make_solid_image(ep1_assets / "001.png", (720, 1280), (30, 80, 150))   # ảnh dọc -> blurred pillarbox
    _make_solid_image(ep1_assets / "002.png", (1600, 900), (140, 90, 40))   # ảnh ngang -> full-bleed
    _make_solid_image(ep1_assets / "003.png", (1280, 720), (80, 40, 120))   # cảnh CTA sẽ bị cắt

    ep1_speeches = [
        "Mở đầu trận đánh lịch sử năm xưa.",
        "Bộ đội chủ lực vượt núi tiến quân.",
        "Hẹn gặp lại ở tập sau, hãy đăng ký kênh!",
    ]
    _make_storyboard(
        ep1,
        "Tap_01_Mo_Dau",
        [
            {"id": 1, "speech": ep1_speeches[0], "motion": "zoom_in"},
            {"id": 2, "speech": ep1_speeches[1], "motion": "pan_right"},
            {"id": 3, "speech": ep1_speeches[2], "motion": "static"},
        ],
    )
    _synthesize_full_audio_and_alignment(
        ep1_speeches,
        ep1_assets / "full_audio.wav",
        ep1_assets / "alignment.json",
        tmp_path,
    )

    # --- Tập 2: Dùng ToneTTS per-scene, cảnh 1 là tư liệu thật trong archive_sources.json ---
    ep2 = series_dir / "Tap_02"
    ep2_assets = ep2 / "assets"
    _make_solid_image(ep2_assets / "001.png", (900, 1200), (120, 120, 120))  # ảnh dọc tư liệu thật
    _make_solid_image(ep2_assets / "002.png", (1600, 900), (45, 110, 75))    # ảnh ngang AI

    ep2_speeches = [
        "Chiến thắng vang dội khép lại chiến dịch.",
        "Dấu mốc bất tử trong lịch sử dân tộc.",
    ]
    _make_storyboard(
        ep2,
        "Tap_02_Chien_Thang",
        [
            {"id": 1, "speech": ep2_speeches[0], "motion": "zoom_in"},
            {"id": 2, "speech": ep2_speeches[1], "motion": "zoom_out"},
        ],
    )
    (ep2 / "archive_sources.json").write_text(
        json.dumps(
            {
                "assets": {
                    "001": {
                        "scene_id": 1,
                        "title": "File:Victory_1954.jpg",
                        "author": "Bảo tàng Lịch sử",
                        "license": "CC BY-SA 2.0",
                        "source_url": "https://commons.wikimedia.org/wiki/File:Victory_1954.jpg",
                        "sha256": "ep2_archive_sha",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # --- BGM ngắn 1.8s (kiểm tra tự lặp 1.00x xuyên suốt video dài hơn) ---
    bgm_wav = tmp_path / "bgm.wav"
    t = np.linspace(0.0, 1.8, int(round(1.8 * SR)), endpoint=False, dtype=np.float32)
    write_wav(bgm_wav, 0.15 * np.sin(2.0 * np.pi * 220.0 * t).astype(np.float32))

    cfg = SeriesCompileConfig(
        series_name="Dien_Bien_Test",
        series_title="Chiến dịch Điện Biên Phủ",
        layout_mode="smart",
        subtitle_mode="srt_only",
        speed_factor=1.10,
        anti_ai_level="light",
        auto_skip_mid_cta=True,
        include_chapter_cards=True,
        chapter_card_duration=1.2,
        include_end_credits=True,
        end_credits_duration=1.5,
        bgm_path=bgm_wav,
        bgm_volume=0.25,
        sfx_volume=0.0,
        loudnorm_youtube=True,
        use_whisper_align=False,
        crf=24,
        preset="ultrafast",
        work_root=tmp_path / "work",
    )

    result = compile_series_youtube(
        series_dir,
        cfg,
        tts_engine=ToneTTS(),
        cache=AudioCache(tmp_path / "cache"),
    )

    # 1. Đủ 4 file đầu ra
    assert result.video_path.name == "Dien_Bien_Test_Full_16x9.mp4"
    assert result.srt_path.name == "Dien_Bien_Test_subtitles.srt"
    assert result.description_path.name == "youtube_description.txt"
    assert result.report_path.name == "compliance_report.json"
    for p in (result.video_path, result.srt_path, result.description_path, result.report_path):
        assert p.is_file() and p.stat().st_size > 0

    # 2. Thông số kỹ thuật video 1920x1080 @ 30fps, có audio & video, sạch metadata
    details = probe_video_details(result.video_path)
    assert (details.width, details.height) == (1920, 1080)
    assert round(details.fps) == 30
    assert details.has_video and details.has_audio
    assert result.loudnorm_applied is True

    tags_text = _inspect_media_tags(result.video_path).lower()
    assert "c2pa" not in tags_text
    assert '"comment"' not in tags_text
    assert '"artist"' not in tags_text

    # 3. Kiểm tra cắt CTA ở Tập 1 & giữ nguyên cảnh tư liệu ở Tập 2
    assert len(result.chapters) == 2
    ch1, ch2 = result.chapters
    assert ch1.skipped_scenes == [3]
    assert ch1.kept_scenes == [1, 2]
    assert ch1.audio_source == "full_audio"
    assert ch2.skipped_scenes == []
    assert ch2.archival_scenes == [1]
    assert ch2.audio_source == "tts_per_scene"

    # 4. Kiểm tra khớp mốc thời gian .srt và youtube_description.txt với thời lượng thực tế
    srt_raw = result.srt_path.read_text(encoding="utf-8")
    assert "tập sau" not in srt_raw.lower()
    assert "đăng ký" not in srt_raw.lower()
    assert "Mở đầu trận đánh" in srt_raw
    assert "Dấu mốc bất tử" in srt_raw

    segs = parse_script(srt_raw)
    assert len(segs) == 4  # 2 cảnh Tập 1 + 2 cảnh Tập 2
    assert segs[0].start >= cfg.chapter_card_duration - 0.1
    assert segs[-1].end <= details.duration - cfg.end_credits_duration + 0.3

    desc_raw = result.description_path.read_text(encoding="utf-8")
    assert "00:00 Phần 1: Mo Dau" in desc_raw
    expected_ch2_ts = format_youtube_timestamp(ch2.start_sec)
    assert f"{expected_ch2_ts} Phần 2: Chien Thang" in desc_raw
    assert "CC BY-SA 2.0" in desc_raw
    assert "Victory_1954" in desc_raw

    # Kiểm tra chênh lệch tổng thời lượng thực tế so với tổng các thành phần (< 0.35s)
    expected_total = ch1.card_duration_sec + ch1.duration_sec + ch2.card_duration_sec + ch2.duration_sec + cfg.end_credits_duration
    assert abs(details.duration - expected_total) <= 0.35

    # 5. Kiểm tra compliance_report.json
    comp = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert comp["video_specs"]["width"] == 1920
    assert comp["video_specs"]["height"] == 1080
    assert comp["settings"]["loudnorm_youtube"] is True
    assert comp["settings"]["target_lufs"] == -14.0
    assert comp["episodes"][0]["skipped_scenes"] == [3]
    assert comp["license_audit"]["counts"]["cc_attribution_required"] == 1


# ==============================================================================
# 6. Parallel assets_16x9/ Priority, Native .mp4 + Fallback + chapter_title + is_cta
# ==============================================================================
def _make_solid_mp4(path: Path, size: tuple[int, int], duration_sec: float = 1.5, color: str = "0x305080") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    cmd = [
        get_ffmpeg(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={w}x{h}:d={duration_sec}:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, creationflags=_NO_WINDOW)
    assert proc.returncode == 0, f"FFmpeg error creating test mp4: {proc.stderr}"


def test_parallel_assets_16x9_priority_fallback_and_metadata(tmp_path):
    from core.series_compiler import _resolve_episode_scene_assets
    from core.story_schema import load_storyboard

    series_dir = tmp_path / "Dual_Delivery_Series"
    ep1 = series_dir / "Tap_01"
    ep_assets = ep1 / "assets"
    ep_assets_169 = ep1 / "assets_16x9"

    # TikTok gốc: ảnh dọc trong assets/
    _make_solid_image(ep_assets / "001.png", (720, 1280), (40, 80, 120))
    _make_solid_image(ep_assets / "002.png", (720, 1280), (140, 90, 40))
    _make_solid_image(ep_assets / "003.png", (720, 1280), (80, 40, 120))

    # Bản YouTube 16:9 song song: cảnh 1 có .mp4 ngang 1920x1080 (kèm .jpg kiểm chứng ưu tiên .mp4), cảnh 2 thiếu để fallback
    _make_solid_image(ep_assets_169 / "001.jpg", (1920, 1080), (10, 100, 150))
    _make_solid_mp4(ep_assets_169 / "001.mp4", (1920, 1080), duration_sec=1.5)

    speeches = [
        "Cảnh một dùng video mười sáu chín nguyên bản.",
        "Cảnh hai lùi về ảnh dọc nền mờ từ assets.",
        "Cảnh kết có cờ is_cta tự động bỏ.",  # Không chứa từ khóa CTA -> thử nhận diện qua is_cta=True
    ]
    sb_payload = {
        "version": "2.0",
        "title": "Tap_01_Dai_Nghia",
        "chapter_title": "Mở Đầu 16x9",
        "scenes": [
            {"id": 1, "speech": speeches[0], "asset_type": "static_image", "motion": "static"},
            {"id": 2, "speech": speeches[1], "asset_type": "static_image", "motion": "zoom_in"},
            {"id": 3, "speech": speeches[2], "asset_type": "static_image", "motion": "static", "is_cta": True},
        ],
    }
    (ep1 / "storyboard.json").write_text(json.dumps(sb_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _synthesize_full_audio_and_alignment(
        speeches,
        ep_assets / "full_audio.wav",
        ep_assets / "alignment.json",
        tmp_path,
    )

    # Thêm Tập 2 tối giản để Tập 1 là tập giữa (is_last=False) và kích hoạt suggested_skip_scenes qua is_cta
    ep2 = series_dir / "Tap_02"
    _make_solid_image(ep2 / "assets" / "001.png", (1280, 720), (60, 100, 60))
    _make_storyboard(
        ep2,
        "Tap_02_Ket_Thuc",
        [{"id": 1, "speech": "Tập kết thúc của series thử nghiệm.", "motion": "static"}],
    )

    eps = scan_series_folder(series_dir)
    assert len(eps) == 2
    ep = eps[0]
    assert ep.chapter_title == "Mở Đầu 16x9"
    assert ep.suggested_skip_scenes == [3]
    assert ep.assets_16x9_count == 1
    assert ep.assets_16x9_dir == ep_assets_169.resolve()

    # Kiểm tra toggle prefer_assets_16x9=False lùi toàn bộ về assets/
    sb_obj = load_storyboard(ep1 / "storyboard.json")
    resolved_off, missing_off, native_off, fb_off = _resolve_episode_scene_assets(
        sb_obj, ep_assets, ep_assets_169, prefer_assets_16x9=False
    )
    assert missing_off == []
    assert native_off == []
    assert fb_off == [1, 2, 3]
    assert resolved_off[1].parent.resolve() == ep_assets.resolve()

    # Tắt Tap_02 để E2E chạy nhanh trên Tap_01, biên tập với prefer_assets_16x9=True
    eps[1].enabled = False
    cfg = SeriesCompileConfig(
        series_title="Dual Delivery Test",
        prefer_assets_16x9=True,
        include_chapter_cards=False,
        include_end_credits=False,
        speed_factor=1.0,
        loudnorm_youtube=False,
        subtitle_mode="srt_only",
        anti_ai_level="light",
        output_dir=tmp_path / "out_16x9",
    )
    res = compile_series_youtube(series_dir, cfg, episodes=eps)
    assert res.video_path.is_file()
    details = probe_video_details(res.video_path)
    assert (details.width, details.height) == (1920, 1080)

    assert len(res.chapters) == 1
    ch = res.chapters[0]
    assert ch.chapter_title == "Mở Đầu 16x9"
    assert ch.audio_source == "full_audio"
    assert ch.skipped_scenes == [3]
    assert ch.native_16x9_scenes == [1]
    assert ch.fallback_assets_scenes == [2]

    comp = json.loads(res.report_path.read_text(encoding="utf-8"))
    assert comp["settings"]["prefer_assets_16x9"] is True
    assert comp["assets_16x9_summary"]["native_16x9_count"] == 1
    assert comp["assets_16x9_summary"]["fallback_assets_count"] == 1
    assert comp["episodes"][0]["native_16x9_count"] == 1
    assert comp["episodes"][0]["fallback_assets_count"] == 1
