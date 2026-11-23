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

    # Cảnh tư liệu thật phải được miễn lọc khử bóng Anti-AI hoàn toàn (no curves/noise), chỉ hòa tông ~15% non-destructive & nền mờ phủ lạnh V5.8
    vf_port_archival = build_youtube_16x9_scene_vf(
        port_img, duration=1.0, layout_mode="smart", is_archival=True, anti_ai_level="strong"
    )
    assert "split=2[bg][fg]" in vf_port_archival
    assert "gblur=" in vf_port_archival
    assert "eq=brightness=-0.22:saturation=0.75" in vf_port_archival
    assert "colorbalance=rs=-0.05:gs=-0.01:bs=0.07" in vf_port_archival
    assert "eq=contrast=1.04:brightness=-0.02" in vf_port_archival
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


# ==============================================================================
# 7. Đợt 4: subtitle_mode="none", Watermark 4 góc, xfade, Smart Clip Fit & Codec
# ==============================================================================
def test_subtitle_mode_none_watermark_xfade_smart_clip_fit_and_codec(tmp_path, monkeypatch):
    from pysubs2 import Alignment, SSAFile

    from core.series_compiler import (
         VALID_SUBTITLE_MODES,
        VALID_VIDEO_CODECS,
        VALID_WATERMARK_POSITIONS,
        VALID_XFADE_TRANSITIONS,
        _render_scene_video_16x9,
        build_final_video_encode_args,
        normalize_watermark_opacity,
        normalize_watermark_position,
        normalize_xfade_settings,
        resolve_effective_watermark_text,
        write_watermark_ass,
    )

    assert VALID_SUBTITLE_MODES == ("none", "srt_only", "burn_and_srt")
    assert VALID_WATERMARK_POSITIONS == ("top_left", "top_right", "bottom_left", "bottom_right")
    assert VALID_XFADE_TRANSITIONS == ("fade", "fadeblack", "none")
    assert VALID_VIDEO_CODECS == ("h264", "h265")

    # 1. Kiểm tra Watermark 4 góc + độ rõ 0.10–1.00 + bật/tắt
    expected_align = {
        "top_left": Alignment.TOP_LEFT,
        "top_right": Alignment.TOP_RIGHT,
        "bottom_left": Alignment.BOTTOM_LEFT,
        "bottom_right": Alignment.BOTTOM_RIGHT,
    }
    for pos, align in expected_align.items():
        ass_file = tmp_path / f"wm_{pos}.ass"
        write_watermark_ass(ass_file, "Kênh Sử Việt 16:9", position=pos, opacity=0.40, total_duration=12.0)
        subs = SSAFile.load(str(ass_file), encoding="utf-8")
        st = subs.styles["YTSeriesWatermark"]
        assert st.alignment == align
        assert st.primarycolor.a == int(round((1.0 - 0.40) * 255))
        assert subs.info["PlayResX"] == "1920"
        assert subs.info["PlayResY"] == "1080"

    assert normalize_watermark_opacity(0.02) == 0.10
    assert normalize_watermark_opacity(1.50) == 1.00
    assert normalize_watermark_opacity(40) == 0.40
    assert normalize_watermark_position("bottom_right") == "bottom_right"
    assert normalize_watermark_position("invalid_corner") == "top_left"

    cfg_wm_off = SeriesCompileConfig(watermark_text="Kênh A", watermark_enabled=False)
    assert resolve_effective_watermark_text(cfg_wm_off) is None
    cfg_wm_on = SeriesCompileConfig(watermark_text="Kênh A", watermark_enabled=True)
    assert resolve_effective_watermark_text(cfg_wm_on) == "Kênh A"

    # 2. Kiểm tra chuẩn hóa xfade & tham số mã hóa H.264 / H.265
    assert normalize_xfade_settings("fadeblack", 0.45) == ("fadeblack", 0.45)
    assert normalize_xfade_settings("fade", 0.05) == ("fade", 0.15)
    assert normalize_xfade_settings("fade", 1.20) == ("fade", 0.80)
    assert normalize_xfade_settings("none", 0.50) == ("none", 0.0)

    args_264 = build_final_video_encode_args(SeriesCompileConfig(video_codec="h264", crf=21, preset="veryfast"))
    assert "libx264" in args_264
    args_265 = build_final_video_encode_args(SeriesCompileConfig(video_codec="h265", crf=23, preset="fast"))
    assert "libx265" in args_265 and "hvc1" in args_265

    # 3. Kiểm tra Smart Clip Fit (True: dùng setpts kéo giãn mượt; False: dùng -stream_loop -1)
    src_mp4 = tmp_path / "short_clip.mp4"
    _make_solid_mp4(src_mp4, (1280, 720), duration_sec=1.5)
    recorded_cmds: list[list[str]] = []
    real_run_ffmpeg = __import__("core.series_compiler", fromlist=["run_ffmpeg"]).run_ffmpeg

    def spy_run_ffmpeg(cmd, *a, **kw):
        recorded_cmds.append(list(cmd))
        return real_run_ffmpeg(cmd, *a, **kw)

    monkeypatch.setattr("core.series_compiler.run_ffmpeg", spy_run_ffmpeg)
    _render_scene_video_16x9(
        src_mp4,
        1.95,  # 1.95 / 1.50 = 1.30 <= 1.45 -> kích hoạt setpts khi smart_clip_fit=True
        tmp_path / "fit_on.mkv",
        motion="static",
        layout_mode="smart",
        is_video=True,
        is_archival=True,
        anti_ai_level="off",
        crf=24,
        preset="ultrafast",
        smart_clip_fit=True,
    )
    assert any("setpts=" in token for token in recorded_cmds[-1])
    assert "-stream_loop" not in recorded_cmds[-1]

    _render_scene_video_16x9(
        src_mp4,
        1.95,
        tmp_path / "fit_off.mkv",
        motion="static",
        layout_mode="smart",
        is_video=True,
        is_archival=True,
        anti_ai_level="off",
        crf=24,
        preset="ultrafast",
        smart_clip_fit=False,
    )
    assert "-stream_loop" in recorded_cmds[-1]
    assert not any("setpts=" in token for token in recorded_cmds[-1])

    # 4. E2E compile_series_youtube với subtitle_mode="none", watermark bottom_right, xfade="fadeblack"
    series_dir = tmp_path / "NoSub_Series"
    ep1 = series_dir / "Tap_01"
    ep1_assets = ep1 / "assets_16x9"
    _make_solid_mp4(ep1_assets / "001.mp4", (1280, 720), duration_sec=1.6)
    _make_solid_image(ep1_assets / "002.png", (1920, 1080), (40, 90, 130))

    speeches = [
        "Cảnh đầu kiểm tra chuyển cảnh mờ qua đen.",
        "Cảnh hai không đốt phụ đề và không tạo file srt.",
    ]
    _make_storyboard(
        ep1,
        "Tap_01_Khong_Phu_De",
        [
            {"id": 1, "speech": speeches[0], "motion": "static"},
            {"id": 2, "speech": speeches[1], "motion": "zoom_in"},
        ],
    )
    _synthesize_full_audio_and_alignment(
        speeches,
        ep1 / "assets" / "full_audio.wav",
        ep1 / "assets" / "alignment.json",
        tmp_path,
    )

    out_dir = tmp_path / "export_no_sub"
    cfg = SeriesCompileConfig(
        series_name="NoSub_Test",
        series_title="Series Không Phụ Đề",
        output_dir=out_dir,
        subtitle_mode="none",
        watermark_text="Sử Việt 16:9",
        watermark_enabled=True,
        watermark_position="bottom_right",
        watermark_opacity=0.35,
        xfade_transition="fadeblack",
        xfade_duration=0.25,
        smart_clip_fit=True,
        video_codec="h264",
        include_chapter_cards=False,
        include_end_credits=False,
        loudnorm_youtube=False,
        speed_factor=1.0,
        crf=24,
        preset="ultrafast",
        work_root=tmp_path / "work_nosub",
    )

    res = compile_series_youtube(series_dir, cfg)
    assert res.srt_path is None
    assert res.to_dict()["srt_path"] is None
    assert not (out_dir / "NoSub_Test_subtitles.srt").exists()
    assert list(out_dir.glob("*.srt")) == []

    assert res.video_path.is_file() and res.video_path.stat().st_size > 0
    assert res.description_path.is_file()
    assert res.report_path.is_file()

    desc_txt = res.description_path.read_text(encoding="utf-8")
    assert "00:00 Phần 1:" in desc_txt

    comp = json.loads(res.report_path.read_text(encoding="utf-8"))
    assert comp["outputs"]["subtitles_srt"] is None
    assert comp["settings"]["subtitle_mode"] == "none"
    assert comp["settings"]["watermark_enabled"] is True
    assert comp["settings"]["watermark_position"] == "bottom_right"
    assert comp["settings"]["watermark_opacity"] == 0.35
    assert comp["settings"]["xfade_transition"] == "fadeblack"
    assert comp["settings"]["xfade_duration"] == 0.25
    assert comp["settings"]["smart_clip_fit"] is True
    assert comp["settings"]["video_codec"] == "h264"


# ==============================================================================
# 8. Đợt 5: Lower-Third on_screen_text, Nhãn Chương La Mã & Film Grain / Vignette
# ==============================================================================
def test_documentary_graphics_lower_third_chapter_badge_and_film_grain(tmp_path):
    from pysubs2 import Alignment, SSAFile

    from core.series_compiler import (
        FILM_GRAIN_VIGNETTE_VF,
        LOWER_THIRD_MARGIN_V_DEFAULT,
        LOWER_THIRD_MARGIN_V_RAISED,
        ChapterMarker,
        build_episode_lower_thirds,
        format_chapter_badge_text,
        resolve_chapter_badge_position,
        resolve_lower_third_margin_v,
        to_roman,
        write_chapter_badge_ass,
        write_lower_third_ass,
    )
    from core.story_schema import StoryScene

    # 1. Số La Mã & định dạng Nhãn Chương + tự tránh đụng Watermark góc trên phải
    assert [to_roman(i) for i in (1, 2, 3, 4, 5, 9, 10)] == ["I", "II", "III", "IV", "V", "IX", "X"]
    assert format_chapter_badge_text(1, "Bối cảnh Đông Dương") == "CHƯƠNG I · BỐI CẢNH ĐÔNG DƯƠNG"
    assert format_chapter_badge_text(4, "Đồi A1 rực lửa") == "CHƯƠNG IV · ĐỒI A1 RỰC LỬA"

    cfg_default = SeriesCompileConfig(watermark_enabled=True, watermark_position="top_left")
    assert resolve_chapter_badge_position(cfg_default) == "top_right"

    cfg_wm_top_right = SeriesCompileConfig(watermark_enabled=True, watermark_position="top_right")
    assert resolve_chapter_badge_position(cfg_wm_top_right) == "top_left"

    cfg_wm_disabled_tr = SeriesCompileConfig(watermark_enabled=False, watermark_position="top_right")
    assert resolve_chapter_badge_position(cfg_wm_disabled_tr) == "top_right"

    chapters = [
        ChapterMarker(
            chapter_index=1,
            episode_id="Tap_01",
            chapter_title="Mở màn chiến dịch",
            start_sec=0.0,
            episode_start_sec=2.5,  # Sau Thẻ chương 2.5s
            duration_sec=10.0,
            card_duration_sec=2.5,
        ),
        ChapterMarker(
            chapter_index=2,
            episode_id="Tap_02",
            chapter_title="Quyết định lịch sử",
            start_sec=12.5,
            episode_start_sec=15.0,  # Sau Thẻ chương thứ 2 (12.5..15.0)
            duration_sec=8.0,        # Kết thúc ở 23.0s trước End Credits
            card_duration_sec=2.5,
        ),
    ]
    badge_ass_tr = tmp_path / "badge_default.ass"
    write_chapter_badge_ass(badge_ass_tr, chapters, config=cfg_default)
    subs_tr = SSAFile.load(str(badge_ass_tr), encoding="utf-8")
    assert subs_tr.styles["YTSeriesChapterBadge"].alignment == Alignment.TOP_RIGHT
    assert len(subs_tr.events) == 2
    assert (subs_tr.events[0].start, subs_tr.events[0].end) == (2500, 12500)
    assert "CHƯƠNG I · MỞ MÀN CHIẾN DỊCH" in subs_tr.events[0].text
    assert (subs_tr.events[1].start, subs_tr.events[1].end) == (15000, 23000)
    assert "CHƯƠNG II · QUYẾT ĐỊNH LỊCH SỬ" in subs_tr.events[1].text

    badge_ass_tl = tmp_path / "badge_shifted.ass"
    write_chapter_badge_ass(badge_ass_tl, chapters, config=cfg_wm_top_right)
    subs_tl = SSAFile.load(str(badge_ass_tl), encoding="utf-8")
    assert subs_tl.styles["YTSeriesChapterBadge"].alignment == Alignment.TOP_LEFT

    # 2. Thanh Lower-Third mốc lịch sử: thời lượng hiển thị, fade và tự nâng MarginV
    kept_scenes = [
        StoryScene(id=1, speech="Cảnh 1", on_screen_text="THÁNG 11/1953 · MƯỜNG THANH"),
        StoryScene(id=2, speech="Cảnh 2 không có nhãn", on_screen_text=""),
        StoryScene(id=3, speech="Cảnh 3 ngắn", on_screen_text="ĐỒI HIM LAM · 13/03/1954"),
    ]
    cues = build_episode_lower_thirds(
        kept_scenes,
        timeline_starts=[0.0, 5.0, 8.0],
        scene_durations=[5.0, 3.0, 2.0],
        episode_offset_sec=2.5,
    )
    assert len(cues) == 2
    # Cảnh 1: dur=5.0 -> min(3.5, max(1.2, 4.7)) = 3.5s -> (2.5, 6.0)
    assert cues[0] == (2.5, 6.0, "THÁNG 11/1953 · MƯỜNG THANH")
    # Cảnh 3: dur=2.0 -> min(3.5, max(1.2, 1.7)) = 1.7s -> (10.5, 12.2)
    assert cues[1] == (10.5, 12.2, "ĐỒI HIM LAM · 13/03/1954")

    assert resolve_lower_third_margin_v(SeriesCompileConfig(subtitle_mode="srt_only", watermark_position="top_left")) == LOWER_THIRD_MARGIN_V_DEFAULT
    assert resolve_lower_third_margin_v(SeriesCompileConfig(subtitle_mode="burn_and_srt")) == LOWER_THIRD_MARGIN_V_RAISED
    assert resolve_lower_third_margin_v(SeriesCompileConfig(subtitle_mode="none", watermark_enabled=True, watermark_position="bottom_left")) == LOWER_THIRD_MARGIN_V_RAISED
    assert resolve_lower_third_margin_v(SeriesCompileConfig(subtitle_mode="none", watermark_enabled=False, watermark_position="bottom_left")) == LOWER_THIRD_MARGIN_V_DEFAULT

    lt_ass_norm = tmp_path / "lt_normal.ass"
    write_lower_third_ass(lt_ass_norm, cues, config=SeriesCompileConfig(subtitle_mode="srt_only", watermark_position="top_left"))
    subs_lt_norm = SSAFile.load(str(lt_ass_norm), encoding="utf-8")
    assert subs_lt_norm.styles["YTSeriesLowerThird"].alignment == Alignment.BOTTOM_LEFT
    assert subs_lt_norm.styles["YTSeriesLowerThird"].marginv == LOWER_THIRD_MARGIN_V_DEFAULT
    assert len(subs_lt_norm.events) == 2
    assert r"\fad(350,400)" in subs_lt_norm.events[0].text
    assert "THÁNG 11/1953 · MƯỜNG THANH" in subs_lt_norm.events[0].text

    lt_ass_raised = tmp_path / "lt_raised.ass"
    write_lower_third_ass(lt_ass_raised, cues, config=SeriesCompileConfig(subtitle_mode="burn_and_srt"))
    subs_lt_raised = SSAFile.load(str(lt_ass_raised), encoding="utf-8")
    assert subs_lt_raised.styles["YTSeriesLowerThird"].marginv == LOWER_THIRD_MARGIN_V_RAISED

    # 3. Bộ lọc Hạt phim 35mm sống & Tối góc nhẹ (Film Grain / Vignette)
    land_img = _make_solid_image(tmp_path / "fg_land.png", (1920, 1080), (50, 70, 90))
    port_img = _make_solid_image(tmp_path / "fg_port.png", (720, 1280), (90, 70, 50))

    vf_grain_on = build_youtube_16x9_scene_vf(
        land_img, duration=1.5, layout_mode="smart", is_archival=True, enable_film_grain_vignette=True
    )
    assert FILM_GRAIN_VIGNETTE_VF in vf_grain_on
    assert "noise=alls=4:allf=t+u" in vf_grain_on and "vignette=PI/6.5" in vf_grain_on

    vf_grain_off = build_youtube_16x9_scene_vf(
        land_img, duration=1.5, layout_mode="smart", is_archival=True, enable_film_grain_vignette=False
    )
    assert "noise=alls=4:allf=t+u" not in vf_grain_off and "vignette=PI/6.5" not in vf_grain_off

    vf_port_grain_on = build_youtube_16x9_scene_vf(
        port_img, duration=1.5, layout_mode="smart", is_archival=True, enable_film_grain_vignette=True
    )
    assert FILM_GRAIN_VIGNETTE_VF in vf_port_grain_on

    # 4. E2E compile kiểm tra lọc bỏ on_screen_text của cảnh CTA bị cắt & ghi cấu hình vào compliance_report.json
    series_dir = tmp_path / "DocGraphics_Series"
    ep1 = series_dir / "Tap_01"
    _make_solid_image(ep1 / "assets_16x9" / "001.png", (1920, 1080), (45, 75, 105))
    _make_solid_image(ep1 / "assets_16x9" / "002.png", (1920, 1080), (65, 85, 55))
    ep1_speeches = [
        "Ngày 13 tháng 3 năm 1954 mở màn chiến dịch.",
        "Đón xem tập sau và đăng ký kênh!",
    ]
    (ep1 / "storyboard.json").write_text(
        json.dumps(
            {
                "version": "2.0",
                "title": "Tap_01_Mo_Man",
                "chapter_title": "Trận mở màn Him Lam",
                "scenes": [
                    {
                        "id": 1,
                        "speech": ep1_speeches[0],
                        "motion": "zoom_in",
                        "on_screen_text": "13/03/1954 · CỤM CỨ ĐIỂM HIM LAM",
                    },
                    {
                        "id": 2,
                        "speech": ep1_speeches[1],
                        "motion": "static",
                        "on_screen_text": "CTA PHẢI BỊ BỎ QUA",
                        "is_cta": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _synthesize_full_audio_and_alignment(
        ep1_speeches,
        ep1 / "assets" / "full_audio.wav",
        ep1 / "assets" / "alignment.json",
        tmp_path,
    )

    ep2 = series_dir / "Tap_02"
    _make_solid_image(ep2 / "assets_16x9" / "001.png", (1920, 1080), (80, 60, 40))
    _make_storyboard(ep2, "Tap_02_Ket", [{"id": 1, "speech": "Tập hai kết thúc.", "motion": "static"}])

    eps = scan_series_folder(series_dir)
    eps[1].enabled = False  # Chỉ render Tập 1 (nhưng vẫn giữ suggested_skip_scenes=[2] từ bước quét 2 tập)

    cfg = SeriesCompileConfig(
        series_name="Doc_Test",
        output_dir=tmp_path / "doc_out",
        subtitle_mode="none",
        watermark_text="Sử Việt 16:9",
        watermark_enabled=True,
        watermark_position="top_right",
        show_lower_third=True,
        show_chapter_badge=True,
        enable_film_grain_vignette=True,
        include_chapter_cards=True,
        chapter_card_duration=1.0,
        include_end_credits=False,
        loudnorm_youtube=False,
        crf=24,
        preset="ultrafast",
        work_root=tmp_path / "work_doc",
    )
    res = compile_series_youtube(series_dir, cfg, episodes=eps)
    assert res.video_path.is_file()
    ch1 = res.chapters[0]
    assert ch1.skipped_scenes == [2]
    assert len(ch1.lower_thirds) == 1
    assert ch1.lower_thirds[0][2] == "13/03/1954 · CỤM CỨ ĐIỂM HIM LAM"
    assert ch1.lower_thirds[0][0] >= ch1.episode_start_sec - 0.05

    comp = json.loads(res.report_path.read_text(encoding="utf-8"))
    assert comp["settings"]["show_lower_third"] is True
    assert comp["settings"]["show_chapter_badge"] is True
    assert comp["settings"]["enable_film_grain_vignette"] is True


