"""Unit test Đợt 2 (V5.3): Đồng bộ frame_preset cho Flow Bot / Validate / Run Episode & lọc ảnh hiện đại."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTOMATION_SCRIPTS = REPO_ROOT / "automation" / "scripts"
if str(AUTOMATION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_SCRIPTS))

import fetch_archive_media  # noqa: E402
import flow_bot  # noqa: E402
import run_episode  # noqa: E402
import validate_storyboard  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Playwright page cho kiểm thử _select_aspect_ratio_option
# ---------------------------------------------------------------------------
class _FakeOptionEl:
    def __init__(self, text: str, *, visible: bool = True, aria_label: str = ""):
        self._text = text
        self._visible = visible
        self._aria = aria_label
        self.clicked = False

    def is_visible(self) -> bool:
        return self._visible

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str:
        return self._aria if name == "aria-label" else ""

    def click(self, timeout: int = 4000) -> None:
        self.clicked = True


class _FakeLocator:
    def __init__(self, items):
        self._items = list(items)

    def count(self) -> int:
        return len(self._items)

    def nth(self, idx: int):
        return self._items[idx]


class _FakeFlowPage:
    def __init__(self, radio_labels, *, prompt_text: str = "4:5 vertical 1:1 square"):
        self.radios = [_FakeOptionEl(lbl) for lbl in radio_labels]
        self.text_node_clicked = False
        self._prompt_text = prompt_text

    def locator(self, selector: str):
        if selector == "button[role='radio']":
            return _FakeLocator(self.radios)
        return _FakeLocator([])

    def get_by_text(self, *_args, **_kwargs):
        self.text_node_clicked = True
        raise AssertionError("Không được gọi get_by_text để tránh click nhầm nội dung prompt")


# ==============================================================================
# 1. Flow Bot: Aspect mapping, UI fallback, prompt cue & convert PNG -> JPG
# ==============================================================================
def test_flow_aspect_plan_all_four_presets():
    p916 = flow_bot.resolve_flow_aspect_plan("portrait_9_16")
    assert p916.preferred[0] == "9:16"
    assert p916.fallback == ()
    assert p916.target_size == (1080, 1920)

    p45 = flow_bot.resolve_flow_aspect_plan("framed_4_5")
    assert p45.preferred == ("3:4", "4:5")
    assert p45.fallback[0] == "9:16"
    assert p45.target_size == (1080, 1350)

    p11 = flow_bot.resolve_flow_aspect_plan("framed_1_1")
    assert p11.preferred[0] == "1:1"
    assert p11.fallback[:2] == ("4:3", "16:9")
    assert p11.target_size == (1080, 1080)

    p169 = flow_bot.resolve_flow_aspect_plan("landscape_16_9")
    assert p169.preferred[0] == "16:9"
    assert p169.fallback == ()
    assert p169.target_size == (1920, 1080)


def test_select_aspect_ratio_option_full_and_fallback_ui():
    # UI thực tế của Google Flow có 5 nút: 16:9, 4:3, 1:1, 3:4, 9:16
    page_full_45 = _FakeFlowPage(["9:16", "16:9", "1:1", "3:4", "4:3"])
    chosen_45 = flow_bot._select_aspect_ratio_option(page_full_45, flow_bot.resolve_flow_aspect_plan("framed_4_5"))
    assert chosen_45 == "3:4"
    assert page_full_45.radios[3].clicked is True
    assert not page_full_45.text_node_clicked

    page_full_11 = _FakeFlowPage(["9:16", "16:9", "1:1", "3:4", "4:3"])
    chosen_11 = flow_bot._select_aspect_ratio_option(page_full_11, flow_bot.resolve_flow_aspect_plan("framed_1_1"))
    assert chosen_11 == "1:1"
    assert page_full_11.radios[2].clicked is True

    # Không có 3:4 -> framed_4_5 fallback sang 9:16; không có 1:1 -> framed_1_1 fallback sang 4:3 rồi 16:9
    page_no_11_34 = _FakeFlowPage(["9:16", "16:9", "4:3"])
    fb_45 = flow_bot._select_aspect_ratio_option(page_no_11_34, flow_bot.resolve_flow_aspect_plan("framed_4_5"))
    assert fb_45 == "9:16"
    fb_11_43 = flow_bot._select_aspect_ratio_option(page_no_11_34, flow_bot.resolve_flow_aspect_plan("framed_1_1"))
    assert fb_11_43 == "4:3"
    assert page_no_11_34.radios[2].clicked is True

    # UI rút gọn chỉ có 9:16 và 16:9 -> framed_1_1 fallback sang 16:9
    page_min_11 = _FakeFlowPage(["9:16", "16:9"])
    fb_11 = flow_bot._select_aspect_ratio_option(page_min_11, flow_bot.resolve_flow_aspect_plan("framed_1_1"))
    assert fb_11 == "16:9"
    assert page_min_11.radios[1].clicked is True


def test_augment_prompt_for_flow_aspect_fallback_cue():
    plan_45 = flow_bot.resolve_flow_aspect_plan("framed_4_5")
    base = "Wide shot of Dien Bien Phu valley, matte archival film grain, 9:16, no text, no watermark"
    # Khi fallback sang 9:16 -> chèn cue 4:5 safe zone ngay trước "no text"
    aug = flow_bot._augment_prompt_for_flow_aspect(base, plan_45, "9:16")
    assert "subject centered within 4:5 safe zone" in aug
    assert aug.index("4:5 safe zone") < aug.index("no text")
    # Khi UI đã chọn được 4:5 trực tiếp -> giữ nguyên prompt
    assert flow_bot._augment_prompt_for_flow_aspect(base, plan_45, "4:5") == base
    # Không lặp nếu prompt đã có cue rồi
    already = "Wide shot, 4:5 vertical framing, subject centered within 4:5 safe zone, no text"
    assert flow_bot._augment_prompt_for_flow_aspect(already, plan_45, "9:16") == already


@pytest.mark.parametrize(
    "preset_name,src_size,exp_size",
    [
        ("portrait_9_16", (1440, 2560), (1080, 1920)),
        ("framed_4_5", (1080, 1920), (1080, 1350)),
        ("framed_1_1", (1920, 1080), (1080, 1080)),
        ("landscape_16_9", (1920, 1080), (1920, 1080)),
    ],
)
def test_convert_png_to_jpg_preserves_frame_preset_dimensions(tmp_path, preset_name, src_size, exp_size):
    raw_png = tmp_path / f"raw_{preset_name}.png"
    dst_jpg = tmp_path / f"out_{preset_name}.jpg"

    # Tạo ảnh nền xám có chủ thể đỏ chiếm vùng trung tâm (35%..65% mỗi chiều)
    img = Image.new("RGB", src_size, (40, 60, 80))
    x0, x1 = int(src_size[0] * 0.35), int(src_size[0] * 0.65)
    y0, y1 = int(src_size[1] * 0.35), int(src_size[1] * 0.65)
    for x in range(x0, x1):
        for y in range(y0, y1):
            img.putpixel((x, y), (220, 20, 20))
    img.save(raw_png, format="PNG")

    out_w, out_h = flow_bot._convert_png_to_jpg(raw_png, dst_jpg, frame_preset=preset_name)
    assert (out_w, out_h) == exp_size
    assert dst_jpg.is_file()
    with Image.open(dst_jpg) as out_img:
        assert out_img.size == exp_size
        # Vùng trung tâm vẫn giữ nguyên chủ thể đỏ (không bị cắt cụt hay kéo méo)
        r, g, _ = out_img.getpixel((exp_size[0] // 2, exp_size[1] // 2))
        assert r > 180 and g < 60
        # Với framed_4_5 từ 1080x1920, phần đầu chủ thể ở ~35-40% chiều cao vẫn còn trọn trong khung
        if preset_name == "framed_4_5":
            hr, hg, _ = out_img.getpixel((exp_size[0] // 2, int(exp_size[1] * 0.38)))
            assert hr > 180 and hg < 60


def test_cli_frame_preset_parsers(tmp_path):
    fb_args = flow_bot.build_arg_parser().parse_args(
        ["dummy.json", "--mode", "images", "--frame-preset", "framed_4_5"]
    )
    assert fb_args.frame_preset == "framed_4_5"

    re_args = run_episode.build_arg_parser().parse_args(
        ["ep_dir", "--phase", "check", "--frame-preset", "framed_1_1"]
    )
    assert re_args.frame_preset == "framed_1_1"

    ep_dir = tmp_path / "ep"
    (ep_dir / "assets").mkdir(parents=True)
    (ep_dir / "raw_images").mkdir()
    info = run_episode.inspect_episode_status(
        ep_dir,
        {"title": "T", "frame_preset": "framed_4_5", "scenes": [{"id": 1, "asset_type": "static_image"}]},
    )
    assert info["frame_preset"] == "framed_4_5"
    assert info["inner_size"] == "1080x1350"
    assert info["out_size"] == "1080x1920"


# ==============================================================================
# 2. Validate Storyboard: hỗ trợ 4 frame_preset & tương thích ngược
# ==============================================================================
def _build_minimal_valid_sb(*, frame_preset=None, aspect_ratio="9:16", prompt_ratio="9:16"):
    speech = "Tại sao pháo binh giữ im lặng suốt đêm nay?"
    sb = {
        "version": "3.0",
        "title": "Test_Storyboard",
        "aspect_ratio": aspect_ratio,
        "asset_ratio_tier": "0_100",
        "full_script_for_elevenlabs": speech,
        "locations": {"loc_01": "001.jpg"},
        "beats": [
            {
                "id": 1,
                "text": speech,
                "location": "loc_01",
                "intensity": 2,
                "intent": "establish",
                "sfx": "boom",
            }
        ],
        "scenes": [
            {
                "id": 1,
                "speech": speech,
                "asset_type": "static_image",
                "image_prompt": f"Wide archival documentary shot of misty mountains, {prompt_ratio}, no text, no watermark",
                "motion_prompt": "",
                "motion": "zoom_in",
                "ken_burns": "zoom_in",
                "sfx": "boom",
                "on_screen_text": "1954",
            }
        ],
    }
    if frame_preset is not None:
        sb["frame_preset"] = frame_preset
    return sb


@pytest.mark.parametrize(
    "frame_preset,aspect_ratio,prompt_ratio,expected_fp",
    [
        (None, "9:16", "9:16", "portrait_9_16"),
        ("portrait_9_16", "9:16", "9:16", "portrait_9_16"),
        ("framed_4_5", "4:5", "4:5 vertical framing", "framed_4_5"),
        ("framed_4_5", "9:16", "9:16", "framed_4_5"),
        ("framed_1_1", "1:1", "1:1 square archival framing", "framed_1_1"),
        ("landscape_16_9", "16:9", "16:9 widescreen framing", "landscape_16_9"),
    ],
)
def test_validate_storyboard_accepts_all_frame_presets_and_legacy(
    tmp_path, frame_preset, aspect_ratio, prompt_ratio, expected_fp
):
    sb_path = tmp_path / "storyboard.json"
    sb_path.write_text(
        json.dumps(
            _build_minimal_valid_sb(
                frame_preset=frame_preset,
                aspect_ratio=aspect_ratio,
                prompt_ratio=prompt_ratio,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reports, data, stats = validate_storyboard.validate_storyboard(sb_path)
    errors = [e for rep in reports for e in rep.errors]
    assert errors == [], f"Unexpected errors: {errors}"
    assert data is not None
    assert stats["frame_preset"] == expected_fp


def test_validate_storyboard_rejects_mismatched_or_invalid_frame_preset(tmp_path):
    # 1) frame_preset không hợp lệ
    p_bad = tmp_path / "bad_fp.json"
    p_bad.write_text(
        json.dumps(_build_minimal_valid_sb(frame_preset="cinema_21_9"), ensure_ascii=False),
        encoding="utf-8",
    )
    reps_bad, _, _ = validate_storyboard.validate_storyboard(p_bad)
    assert any("frame_preset" in e for e in reps_bad[0].errors)

    # 2) aspect_ratio lệch frame_preset
    p_mismatch = tmp_path / "mismatch.json"
    p_mismatch.write_text(
        json.dumps(
            _build_minimal_valid_sb(
                frame_preset="landscape_16_9",
                aspect_ratio="9:16",
                prompt_ratio="16:9",
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reps_mm, _, _ = validate_storyboard.validate_storyboard(p_mismatch)
    assert any("aspect_ratio" in e for e in reps_mm[0].errors)

    # 3) image_prompt thiếu tỉ lệ của frame_preset
    p_prompt = tmp_path / "bad_prompt.json"
    p_prompt.write_text(
        json.dumps(
            _build_minimal_valid_sb(
                frame_preset="framed_1_1",
                aspect_ratio="1:1",
                prompt_ratio="9:16",
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reps_pr, _, _ = validate_storyboard.validate_storyboard(p_prompt)
    assert any("framed_1_1" in e for e in reps_pr[3].errors)


# ==============================================================================
# 3. Fetch Archive Media: công tắc --exclude-modern / --include-sites
# ==============================================================================
def test_fetch_archive_media_modern_filter_switches():
    # Kiểm tra mặc định CLI và cờ chuyển đổi
    args_default = fetch_archive_media.build_parser().parse_args(["--mode", "images", "--query", "test"])
    assert args_default.exclude_modern is True

    args_excl = fetch_archive_media.build_parser().parse_args(
        ["--mode", "images", "--query", "test", "--exclude-modern"]
    )
    assert args_excl.exclude_modern is True

    args_incl = fetch_archive_media.build_parser().parse_args(
        ["--mode", "images", "--query", "test", "--include-sites"]
    )
    assert args_incl.exclude_modern is False

    # Các ảnh du lịch/trưng bày hiện đại phải bị lọc
    blocked_cases = [
        (
            "File:Vietnamese Fabrics on Display - Outside Bunker of Colonel de Castries - Dien Bien Phu - Vietnam.jpg",
            "Vietnamese Fabrics on Display",
            "Taken on 1 July 2019",
            "CC BY-SA 2.0",
        ),
        (
            "File:Panorama of Bunker of Colonel de Castries - Dien Bien Phu - Vietnam.jpg",
            "Panorama of Bunker of Colonel de Castries",
            "Taken on 1 July 2019",
            "CC BY-SA 2.0",
        ),
        (
            "File:Interior of Bunker of Colonel de Castries - Dien Bien Phu - Vietnam - 02.jpg",
            "Interior of Bunker of Colonel de Castries",
            "Taken on 1 July 2019",
            "CC BY-SA 2.0",
        ),
        (
            "File:Diorama of Soldiers with Battle Flag - Dien Bien Phu Victory Museum.jpg",
            "Diorama of Soldiers with Battle Flag",
            "2019-06-30",
            "CC BY-SA 2.0",
        ),
        (
            "File:Dien Bien Phu Victory Monument.jpg",
            "Tourist memorial monument",
            "2022-05-01",
            "CC BY-SA 4.0",
        ),
        (
            "File:Souvenir stalls near memorial.jpg",
            "Wax figure and statue",
            "2020",
            "CC BY-SA 4.0",
        ),
    ]
    for title, desc, yr, lic in blocked_cases:
        blocked, reason = fetch_archive_media.is_modern_tourist_or_site_media(title, desc, yr, lic)
        assert blocked is True, f"Phải chặn '{title}' nhưng lại giữ qua"
        assert reason

    # Ảnh tư liệu gốc chụp lại từ bảo tàng (có chữ Contemporary Photo / 1954 / archival / Public domain) PHẢI giữ qua
    kept_cases = [
        (
            "File:Contemporary Photo of Vietnminh Storming across Muong Thanh Bridge (1954) - Dien Bien Phu Victory Museum - Dien Bien Phu - Vietnam (48159225792).jpg",
            "Contemporary Photo of Vietnminh Storming across Muong Thanh Bridge (May 7, 1954) - Dien Bien Phu Victory Museum",
            "Taken on 30 June 2019, 15:39",
            "CC BY-SA 2.0",
        ),
        (
            "File:Dien Bien Phu Victory Museum archival photograph.jpg",
            "Archival photograph of 1954 trench",
            "2019",
            "CC BY-SA 4.0",
        ),
        (
            "File:Memorial diorama scan.jpg",
            "Original scan",
            "",
            "Public domain",
        ),
    ]
    for title, desc, yr, lic in kept_cases:
        blocked, _ = fetch_archive_media.is_modern_tourist_or_site_media(title, desc, yr, lic)
        assert blocked is False, f"Phải giữ qua ảnh gốc '{title}'"


def test_search_wikimedia_respects_exclude_modern_flag():
    payload = {
        "query": {
            "pages": {
                "1": {
                    "index": 1,
                    "title": "File:Contemporary Photo of Vietnminh Storming across Muong Thanh Bridge (1954) - Dien Bien Phu Victory Museum.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/a.jpg",
                            "mime": "image/jpeg",
                            "width": 1600,
                            "height": 1200,
                            "size": 100000,
                            "sha1": "sha_keep",
                            "extmetadata": {
                                "Artist": {"value": "Adam Jones"},
                                "DateTimeOriginal": {"value": "2019-06-30"},
                                "ImageDescription": {"value": "Contemporary Photo of Vietnminh Storming across Muong Thanh Bridge (May 7, 1954) - Dien Bien Phu Victory Museum"},
                                "LicenseShortName": {"value": "CC BY-SA 2.0"},
                            },
                        }
                    ],
                },
                "2": {
                    "index": 2,
                    "title": "File:Vietnamese Fabrics on Display - Outside Bunker of Colonel de Castries.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/b.jpg",
                            "mime": "image/jpeg",
                            "width": 1600,
                            "height": 1200,
                            "size": 100000,
                            "sha1": "sha_modern",
                            "extmetadata": {
                                "Artist": {"value": "Adam Jones"},
                                "DateTimeOriginal": {"value": "2019-07-01"},
                                "ImageDescription": {"value": "Vietnamese Fabrics on Display"},
                                "LicenseShortName": {"value": "CC BY-SA 2.0"},
                            },
                        }
                    ],
                },
            }
        }
    }

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _FakeSession:
        def get(self, *_args, **_kwargs):
            return _FakeResp()

    filtered = fetch_archive_media._search_wikimedia(
        _FakeSession(), ["Dien Bien Phu"], filetype="bitmap", limit=10, exclude_modern=True
    )
    assert len(filtered) == 1
    assert "Muong Thanh Bridge" in filtered[0]["title"]

    unfiltered = fetch_archive_media._search_wikimedia(
        _FakeSession(), ["Dien Bien Phu"], filetype="bitmap", limit=10, exclude_modern=False
    )
    assert len(unfiltered) == 2


def test_adapt_prompt_for_frame_preset_v54():
    src = (
        "Archival sepia ink and watercolor painting, upright 9:16 portrait, "
        "vertical framing, 4:5 vertical framing, Viet Minh soldiers hauling artillery at Dien Bien Phu 1954."
    )
    same = flow_bot.adapt_prompt_for_frame_preset(src, "portrait_9_16")
    assert same == src

    adapted = flow_bot.adapt_prompt_for_frame_preset(src, "landscape_16_9")
    assert "9:16" not in adapted
    assert "4:5" not in adapted
    assert "16:9 widescreen" in adapted.lower()
    assert "Dien Bien Phu 1954" in adapted


def test_youtube_16x9_io_paths_execution_plan_and_video_start_frame(tmp_path):
    from PIL import Image

    ep_dir = tmp_path / "Tap_01"
    assets_tiktok = ep_dir / "assets"
    raw_tiktok = ep_dir / "raw_images"
    assets_169 = ep_dir / "assets_16x9"
    raw_169 = ep_dir / "raw_images_16x9"
    assets_tiktok.mkdir(parents=True)
    raw_tiktok.mkdir(parents=True)
    assets_169.mkdir(parents=True)
    raw_169.mkdir(parents=True)

    # Tạo sẵn 001.jpg trong assets_16x9 (giả lập ảnh tư liệu bản ngang đã nạp)
    Image.new("RGB", (1920, 1080), (40, 60, 80)).save(assets_169 / "001.jpg")
    # Tạo 003.png CHỈ trong assets/ (bản TikTok), KHÔNG có trong assets_16x9/ hay raw_images_16x9/
    Image.new("RGB", (1080, 1350), (120, 30, 30)).save(assets_tiktok / "003.png")

    sb_data = {
        "episode_name": "Tap_01",
        "frame_preset": "framed_4_5",
        "scenes": [
            {"id": 1, "asset_type": "static_image", "speech": "Cảnh 1", "image_prompt": "9:16 portrait"},
            {"id": 2, "asset_type": "flow_video", "speech": "Cảnh 2", "image_prompt": "4:5 framing", "video_prompt": "pan"},
            {"id": 3, "asset_type": "flow_video", "speech": "Cảnh 3", "image_prompt": "vertical framing", "video_prompt": "zoom"},
        ],
    }

    fp, out_assets, out_raw, out_state = flow_bot.resolve_io_paths(
        ep_dir, youtube_16x9=True, storyboard_preset=sb_data["frame_preset"]
    )
    assert fp.name == "landscape_16_9"
    assert out_assets == assets_169
    assert out_raw == raw_169
    assert out_state == ep_dir / "pipeline_state_16x9.json"

    plan = flow_bot._build_execution_plan(
        ep_dir,
        sb_data,
        "images",
        selected_ids=None,
        limit=None,
        force=False,
        assets_dir=out_assets,
        raw_dir=out_raw,
        state_path=out_state,
        youtube_16x9=True,
    )
    by_id = {item.scene_id: item for item in plan}
    assert by_id[1].action == "SKIP_EXISTING_ASSET"  # 001.jpg đã có trong assets_16x9/ được bảo vệ
    assert by_id[2].action == "RUN"
    assert by_id[3].action == "RUN"   # Không bị đánh lừa bởi assets/003.png của TikTok

    # Tạo frame đầu 16:9 cho cảnh 2 trong raw_images_16x9 -> video mode nhận đúng, không lùi sang assets/003.png
    import os

    Image.frombytes("RGB", (320, 180), os.urandom(320 * 180 * 3)).save(raw_169 / "002.png")
    assert flow_bot._find_video_start_frame(2, out_raw, out_assets, youtube_16x9=True) == raw_169 / "002.png"
    assert flow_bot._find_video_start_frame(3, out_raw, out_assets, youtube_16x9=True) is None

    vplan = flow_bot._build_execution_plan(
        ep_dir,
        sb_data,
        "videos",
        selected_ids=None,
        limit=None,
        force=False,
        assets_dir=out_assets,
        raw_dir=out_raw,
        state_path=out_state,
        youtube_16x9=True,
    )
    vby_id = {item.scene_id: item for item in vplan}
    assert vby_id[2].action == "RUN"
    assert vby_id[3].action == "ERROR_MISSING_RAW"

    parser = flow_bot.build_arg_parser()
    parsed = parser.parse_args([str(ep_dir / "storyboard.json"), "--youtube-16x9", "--mode", "images"])
    assert parsed.youtube_16x9 is True
    assert parsed.mode == "images"


def test_run_episode_youtube_16x9_inspect_and_flags(tmp_path):
    import run_episode
    from PIL import Image

    ep_dir = tmp_path / "Tap_02"
    (ep_dir / "assets").mkdir(parents=True)
    (ep_dir / "assets_16x9").mkdir(parents=True)
    (ep_dir / "assets" / "full_audio.mp3").write_bytes(b"ID3")
    Image.new("RGB", (1080, 1920), (10, 20, 30)).save(ep_dir / "assets" / "001.png")
    Image.new("RGB", (1080, 1920), (20, 30, 40)).save(ep_dir / "assets" / "002.png")
    Image.new("RGB", (1920, 1080), (30, 40, 50)).save(ep_dir / "assets_16x9" / "001.jpg")

    sb_data = {
        "episode_name": "Tap_02",
        "frame_preset": "portrait_9_16",
        "scenes": [
            {"id": 1, "asset_type": "static_image", "speech": "Cảnh 1", "image_prompt": "a"},
            {"id": 2, "asset_type": "flow_video", "speech": "Cảnh 2", "image_prompt": "b", "video_prompt": "v"},
        ],
    }
    status = run_episode.inspect_episode_status(ep_dir, sb_data, youtube_16x9=True)
    assert status["frame_preset"] == "landscape_16_9"
    assert status["youtube_16x9"] is True
    assert status["assets_dir_name"] == "assets_16x9"
    assert status["raw_dir_name"] == "raw_images_16x9"
    assert status["out_size"] == "1920x1080"
    assert status["has_audio"] is True
    assert status["done_assets"] == [1]
    assert status["missing_assets"] == [2]
    assert status["pending_flow_videos"] == [2]

    args = run_episode.build_arg_parser().parse_args(
        [str(ep_dir), "--phase", "all", "--youtube-16x9"]
    )
    assert args.youtube_16x9 is True
    assert args.phase == "all"


def test_fetch_archive_commit_image_youtube_16x9_and_preserve_tiktok(tmp_path):
    from PIL import Image

    ep_dir = tmp_path / "Tap_01"
    (ep_dir / "assets").mkdir(parents=True)
    sb_data = {
        "episode_name": "Tap_01",
        "frame_preset": "framed_4_5",
        "scenes": [{"id": 1, "asset_type": "static_image", "speech": "Tư liệu", "image_prompt": "x"}],
    }
    (ep_dir / "storyboard.json").write_text(json.dumps(sb_data, ensure_ascii=False), encoding="utf-8")

    src_img = tmp_path / "archival_src.jpg"
    Image.new("RGB", (2400, 1600), (110, 95, 80)).save(src_img)

    # 1. Nạp bản TikTok trước (không truyền --target-size -> giữ nguyên kích thước 2400x1600)
    rc_tt = fetch_archive_media.main(
        [
            "--mode",
            "commit-image",
            "--episode-dir",
            str(ep_dir),
            "--scene",
            "1",
            "--input",
            str(src_img),
            "--license",
            "CC BY-SA 2.0",
            "--source-url",
            "https://commons.wikimedia.org/wiki/File:Demo1954.jpg",
        ]
    )
    assert rc_tt == 0
    with Image.open(ep_dir / "assets" / "001.jpg") as im_tt:
        assert im_tt.size == (2400, 1600)

    # 2. Nạp bản YouTube 16:9 song song -> assets_16x9/001.jpg (1920x1080), giữ nguyên assets/001.jpg
    rc_yt = fetch_archive_media.main(
        [
            "--mode",
            "commit-image",
            "--episode-dir",
            str(ep_dir),
            "--scene",
            "1",
            "--input",
            str(src_img),
            "--youtube-16x9",
            "--license",
            "CC BY-SA 2.0",
            "--source-url",
            "https://commons.wikimedia.org/wiki/File:Demo1954.jpg",
        ]
    )
    assert rc_yt == 0
    with Image.open(ep_dir / "assets" / "001.jpg") as im_tt_after:
        assert im_tt_after.size == (2400, 1600)
    with Image.open(ep_dir / "assets_16x9" / "001.jpg") as im_yt:
        assert im_yt.size == (1920, 1080)

    src_json = json.loads((ep_dir / "archive_sources.json").read_text(encoding="utf-8"))
    entry = src_json["assets"]["001"]
    assert entry["asset_file"] == "assets/001.jpg"
    assert entry["asset_file_16x9"] == "assets_16x9/001.jpg"
    assert entry["resolution_16x9"] == "1920x1080"


def test_validate_storyboard_accepts_optional_chapter_title_and_is_cta(tmp_path):
    import validate_storyboard

    ep_dir = tmp_path / "Tap_03"
    ep_dir.mkdir(parents=True)

    speech1 = "Mở đầu chiến dịch lịch sử năm 1954."
    speech2 = "Theo dõi phần tiếp theo của loạt phim."
    sb = {
        "version": "2.0",
        "title": "Tap_03",
        "chapter_title": "Khởi công kéo pháo",
        "frame_preset": "framed_4_5",
        "era_branch": "can_hien_dai",
        "ratio_tier": "0_100",
        "aspect_ratio": "4:5",
        "locations": {"loc_1": "001.jpg", "loc_2": "002.jpg"},
        "full_script_for_elevenlabs": f"{speech1} {speech2}",
        "beats": [
            {
                "id": 1,
                "start_sec": 0.0,
                "end_sec": 3.5,
                "text": speech1,
                "location": "loc_1",
                "sfx": "boom",
                "intent": "establish",
                "intensity": 2,
            },
            {
                "id": 2,
                "start_sec": 3.5,
                "end_sec": 7.0,
                "text": speech2,
                "location": "loc_2",
                "sfx": "none",
                "intent": "explain",
                "intensity": 1,
            },
        ],
        "scenes": [
            {
                "id": 1,
                "speech": speech1,
                "on_screen_text": "KEO PHAO 1954",
                "asset_type": "static_image",
                "motion": "zoom_in",
                "ken_burns": "zoom_in",
                "sfx": "boom",
                "image_prompt": "4:5 vertical framing, archival 1954 Vietnam artillery crew pulling cannons up muddy mountain slope, monochrome documentary film grain, no text",
                "duration_sec": 3.5,
            },
            {
                "id": 2,
                "speech": speech2,
                "on_screen_text": "PHAN TIEP THEO",
                "asset_type": "static_image",
                "motion": "static",
                "ken_burns": "static",
                "sfx": "none",
                "image_prompt": "4:5 vertical framing, 1954 archival map of Muong Thanh basin with ink contours, aged paper texture, no text",
                "duration_sec": 3.5,
                "is_cta": True,
            },
        ],
    }
    sb_path = ep_dir / "storyboard.json"
    sb_path.write_text(json.dumps(sb, ensure_ascii=False, indent=2), encoding="utf-8")

    reports, _data, _stats = validate_storyboard.validate_storyboard(sb_path)
    all_errors = [item for r in reports for item in r.errors]
    assert all_errors == []
    assert validate_storyboard.main([str(sb_path)]) == 0

    # Giá trị sai kiểu cho chapter_title / is_cta phải bị Tầng 1 báo lỗi
    bad_sb = json.loads(json.dumps(sb))
    bad_sb["chapter_title"] = 123
    bad_sb["scenes"][1]["is_cta"] = "yes"
    bad_path = ep_dir / "storyboard_bad.json"
    bad_path.write_text(json.dumps(bad_sb, ensure_ascii=False, indent=2), encoding="utf-8")
    bad_reports, _, _ = validate_storyboard.validate_storyboard(bad_path)
    t1_errors = bad_reports[0].errors
    assert any("chapter_title" in err for err in t1_errors)
    assert any("is_cta" in err for err in t1_errors)


def test_adapt_prompt_for_archival_photo_v54(tmp_path, monkeypatch):
    import run_episode

    day_2d = (
        "Dramatic high-angle view of Muong Thanh valley in 1953, "
        "epic 2D historical animation style, clean delicate lineart, rich cinematic cel-shading, "
        "Kodachrome colorized glow, warm sunlight rays, golden hour, teal-and-orange, cinematic bokeh, "
        "warm dramatic lighting, moody blue and warm sunset contrast, vertical 9:16, no text"
    )
    out_day = flow_bot.adapt_prompt_for_archival_photo(
        flow_bot.adapt_prompt_for_frame_preset(day_2d, "landscape_16_9")
    )
    low_day = out_day.lower()
    cleaned_head = low_day.split("authentic 1954 indochina")[0]
    for banned in (
        "2d historical animation",
        "cel-shading",
        "lineart",
        "painterly brushstrokes",
        "kodachrome",
        "colorized",
        "warm sunlight rays",
        "golden hour",
        "teal-and-orange",
        "cinematic bokeh",
    ):
        assert banned not in cleaned_head
    for banned_global in (
        "kodachrome",
        "colorized",
        "warm sunlight rays",
        "cinematic bokeh",
        "cel-shading",
        "lineart",
    ):
        assert banned_global not in low_day
    assert "no 2d illustration" in low_day
    assert "16:9 widescreen" in low_day
    assert "authentic 1954 indochina archival documentary photograph" in low_day
    assert "cleanly restored archival documentary war-correspondent photography" in low_day
    assert "raw 35mm monochrome / near-monochrome film grain" in low_day
    assert "stark authentic natural lighting" in low_day
    assert "deep gritty texture" in low_day
    assert "no modern cinematic color grading" in low_day
    assert "no golden hour glow" in low_day
    assert "no teal-and-orange" in low_day
    assert "no glossy ai sheen" in low_day
    assert "no fake scan frame" in low_day
    assert "no pillarbox" in low_day
    assert "no letterbox" in low_day
    assert low_day.endswith("no text")

    night_2d = (
        "Porters pushing cargo bicycles uphill with glowing bamboo torches at night, "
        "cinematic historical documentary 2D semi-realistic painted illustration, "
        "fine painterly brushstrokes, dramatic rim lighting, no text, no watermark"
    )
    out_night = flow_bot.adapt_prompt_for_archival_photo(night_2d)
    low_night = out_night.lower()
    assert "painted illustration" not in low_night
    assert "painterly brushstrokes" not in low_night
    assert "dramatic rim lighting" not in low_night
    assert "archival documentary night photograph" in low_night
    assert "high-iso raw 35mm monochrome / near-monochrome film grain" in low_night
    assert "natural dim torchlight contrast" in low_night
    assert "deep gritty texture" in low_night

    # Mặc định CLI phải BẬT archival_photo=True và hỗ trợ tắt bằng --no-archival-photo
    fb_default_args = flow_bot.build_arg_parser().parse_args([str(tmp_path / "storyboard.json"), "--mode", "images"])
    assert fb_default_args.archival_photo is True

    fb_off_args = flow_bot.build_arg_parser().parse_args(
        [str(tmp_path / "storyboard.json"), "--mode", "images", "--no-archival-photo"]
    )
    assert fb_off_args.archival_photo is False

    re_default_args = run_episode.build_arg_parser().parse_args([str(tmp_path), "--phase", "images"])
    assert re_default_args.archival_photo is True

    re_off_args = run_episode.build_arg_parser().parse_args(
        [str(tmp_path), "--phase", "images", "--no-archival-photo"]
    )
    assert re_off_args.archival_photo is False

    # Ghi đè theo từng cảnh: "archival_photo": false bỏ qua bộ lọc V5.4, cảnh thường vẫn áp V5.4
    diagram_scene = {
        "id": 1,
        "archival_photo": False,
        "image_prompt": "Clean geological tunnel cross-section diagram with arrows, vertical 9:16, no text",
    }
    combat_scene = {
        "id": 2,
        "image_prompt": "Viet Minh soldiers advancing through misty trenches at dawn, vertical 9:16, no text",
    }
    assert flow_bot.should_apply_archival_photo(diagram_scene, global_archival_photo=True) is False
    assert flow_bot.should_apply_archival_photo(combat_scene, global_archival_photo=True) is True
    assert flow_bot.should_apply_archival_photo(combat_scene, global_archival_photo=False) is False

    diagram_prompt = flow_bot.prepare_image_prompt_for_scene(diagram_scene, "portrait_9_16", archival_photo=True).lower()
    combat_prompt = flow_bot.prepare_image_prompt_for_scene(combat_scene, "portrait_9_16", archival_photo=True).lower()
    assert "war-correspondent photography" not in diagram_prompt
    assert "geological tunnel cross-section diagram" in diagram_prompt
    assert "war-correspondent photography" in combat_prompt

    # Xác nhận run_phase_images(..., archival_photo=False) truyền đúng cờ --no-archival-photo xuống flow_bot.py
    ep_dir = tmp_path / "Ep_Forward_Test"
    ep_dir.mkdir()
    sb_path = ep_dir / "storyboard.json"
    sb_path.write_text('{"version":"3.0","title":"Test","scenes":[]}', encoding="utf-8")
    captured_cmds = []
    monkeypatch.setattr(run_episode, "_run_validate", lambda *a, **kw: 0)
    monkeypatch.setattr(run_episode, "_load_json_safe", lambda *a, **kw: None)
    monkeypatch.setattr(run_episode, "_run_subprocess", lambda cmd: captured_cmds.append(list(cmd)) or 0)

    run_episode.run_phase_images(
        ep_dir,
        sb_path,
        scenes_arg=None,
        dry_run=True,
        cdp_url=None,
        archival_photo=False,
    )
    assert "--no-archival-photo" in captured_cmds[-1]
    assert "--archival-photo" not in captured_cmds[-1]

    run_episode.run_phase_images(
        ep_dir,
        sb_path,
        scenes_arg=None,
        dry_run=True,
        cdp_url=None,
    )
    assert "--archival-photo" in captured_cmds[-1]
    assert "--no-archival-photo" not in captured_cmds[-1]
