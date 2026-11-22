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
    assert p45.preferred == ("4:5", "3:4")
    assert p45.fallback[0] == "9:16"
    assert p45.target_size == (1080, 1350)

    p11 = flow_bot.resolve_flow_aspect_plan("framed_1_1")
    assert p11.preferred[0] == "1:1"
    assert p11.fallback[0] == "16:9"
    assert p11.target_size == (1080, 1080)

    p169 = flow_bot.resolve_flow_aspect_plan("landscape_16_9")
    assert p169.preferred[0] == "16:9"
    assert p169.fallback == ()
    assert p169.target_size == (1920, 1080)


def test_select_aspect_ratio_option_full_and_fallback_ui():
    # UI đầy đủ có 4:5 và 1:1
    page_full_45 = _FakeFlowPage(["9:16", "16:9", "1:1", "4:5"])
    chosen_45 = flow_bot._select_aspect_ratio_option(page_full_45, flow_bot.resolve_flow_aspect_plan("framed_4_5"))
    assert chosen_45 == "4:5"
    assert page_full_45.radios[3].clicked is True
    assert not page_full_45.text_node_clicked

    page_full_11 = _FakeFlowPage(["9:16", "16:9", "1:1", "4:5"])
    chosen_11 = flow_bot._select_aspect_ratio_option(page_full_11, flow_bot.resolve_flow_aspect_plan("framed_1_1"))
    assert chosen_11 == "1:1"
    assert page_full_11.radios[2].clicked is True

    # UI rút gọn chỉ có 9:16 và 16:9 -> 4:5 fallback sang 9:16, 1:1 fallback sang 16:9
    page_min_45 = _FakeFlowPage(["9:16", "16:9"])
    fb_45 = flow_bot._select_aspect_ratio_option(page_min_45, flow_bot.resolve_flow_aspect_plan("framed_4_5"))
    assert fb_45 == "9:16"
    assert page_min_45.radios[0].clicked is True

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
