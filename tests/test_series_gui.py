"""Kiểm thử nhanh cho Tab Giao diện `YouTube 16:9` (`gui/series_youtube_tab.py`).

Không chạy `mainloop()` chặn; hủy cửa sổ Tk sạch sẽ sau mỗi test và mock `compile_series_youtube`
để xác nhận mapping dữ liệu & worker thread nền hoàn tất trong vài giây.
"""
from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import pytest
from PIL import Image

from core.config import DubSettings, Keys
from core.reporter import Reporter
from core.series_compiler import (
    ChapterMarker,
    LicenseAuditReport,
    SeriesCompileConfig,
    SeriesCompileResult,
)
from gui.app import App
from gui.series_youtube_tab import (
    ANTI_AI_YT_LEVELS,
    LAYOUT_MODES,
    SUBTITLE_MODES,
    VIDEO_CODECS,
    WATERMARK_POSITIONS,
    XFADE_TRANSITIONS,
    SeriesYouTubeTab,
    parse_skip_scenes_text,
)
from gui.theme import apply_theme


def _make_png(path: Path, size: tuple[int, int] = (640, 360)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (30, 60, 110)).save(str(path), format="PNG")


def _write_storyboard(ep_dir: Path, title: str, speeches: list[str]) -> None:
    ep_dir.mkdir(parents=True, exist_ok=True)
    scenes = [{"id": i, "speech": txt} for i, txt in enumerate(speeches, start=1)]
    (ep_dir / "storyboard.json").write_text(
        json.dumps({"version": "2.0", "title": title, "scenes": scenes}, ensure_ascii=False),
        encoding="utf-8",
    )


def _destroy_tk(win: tk.Misc) -> None:
    """Hủy mọi callback `after` đang chờ rồi đóng cửa sổ Tk sạch sẽ."""
    try:
        for aid in list(win.tk.call("after", "info")):
            try:
                win.after_cancel(aid)
            except Exception:
                pass
    except Exception:
        pass
    try:
        win.destroy()
    except Exception:
        pass


class _StubApp:
    """Stub nhẹ mô phỏng App khi khởi tạo SeriesYouTubeTab độc lập."""

    def __init__(self):
        self.settings = DubSettings()

    def keys(self) -> Keys:
        return Keys()

    def make_reporter(self, log, bar, status=None) -> Reporter:
        return Reporter(
            on_log=lambda m, lv: log.write(m, lv),
            on_progress=lambda pct, msg=None: bar.set(pct),
        )

    def run_job(self, fn, *, buttons=(), cancel_button=None, reporter=None, log=None, on_done=None):
        res = fn()
        if on_done:
            on_done(res)

    def open_path(self, _path) -> None:
        pass


@pytest.fixture(scope="module")
def gui_app():
    """Dùng chung một root `App()` ẩn cho toàn bộ module test (tránh lỗi tái tạo Tk nhiều lần trên Windows)."""
    orig_warn = messagebox.showwarning
    orig_err = messagebox.showerror
    messagebox.showwarning = lambda *a, **kw: None
    messagebox.showerror = lambda *a, **kw: None
    app = App()
    app.withdraw()
    app.update_idletasks()
    try:
        yield app
    finally:
        messagebox.showwarning = orig_warn
        messagebox.showerror = orig_err
        _destroy_tk(app)


# ==============================================================================
# 1. Khởi tạo độc lập & gắn đúng vào cửa sổ chính ngang hàng Studio & Story
# ==============================================================================
def test_series_youtube_tab_standalone_and_mounted_in_app(gui_app):
    # 1a. Khởi tạo độc lập (trong Toplevel riêng với _StubApp)
    win = tk.Toplevel(gui_app)
    win.withdraw()
    try:
        apply_theme(win)
        tab = SeriesYouTubeTab(win, _StubApp())
        tab.pack(fill="both", expand=True)
        win.update_idletasks()

        _, cfg = tab.build_compile_inputs()
        assert cfg.layout_mode == "smart"
        assert cfg.subtitle_mode == "srt_only"
        assert set(SUBTITLE_MODES.keys()) == {"none", "srt_only", "burn_and_srt"}
        assert set(WATERMARK_POSITIONS.keys()) == {"top_left", "top_right", "bottom_left", "bottom_right"}
        assert set(XFADE_TRANSITIONS.keys()) == {"fade", "fadeblack", "none"}
        assert set(VIDEO_CODECS.keys()) == {"h264", "h265"}
        assert cfg.speed_factor == 1.08
        assert cfg.anti_ai_level == "medium"
        assert cfg.watermark_enabled is True
        assert cfg.watermark_position == "top_left"
        assert cfg.watermark_opacity == 0.40
        assert cfg.xfade_transition == "fade"
        assert cfg.xfade_duration == 0.30
        assert cfg.smart_clip_fit is True
        assert cfg.video_codec == "h264"
        assert cfg.show_lower_third is True
        assert cfg.show_chapter_badge is True
        assert cfg.enable_film_grain_vignette is True
        assert cfg.auto_skip_mid_cta is True
        assert cfg.include_chapter_cards is True
        assert cfg.include_end_credits is True
        assert cfg.loudnorm_youtube is True
    finally:
        win.destroy()

    # 1b. Gắn vào cửa sổ chính App() ngang hàng Studio & Story
    assert hasattr(gui_app, "series_youtube_tab")
    assert isinstance(gui_app.series_youtube_tab, SeriesYouTubeTab)

    tab_texts = [gui_app.notebook.tab(tid, "text").strip() for tid in gui_app.notebook.tabs()]
    assert any("Dub Studio" in t for t in tab_texts)
    story_idx = next(i for i, t in enumerate(tab_texts) if "Story Studio" in t)
    yt_idx = next(i for i, t in enumerate(tab_texts) if "YouTube 16:9" in t)
    assert yt_idx == story_idx + 1


# ==============================================================================
# 2. Quản lý tập, mapping cấu hình UI -> SeriesCompileConfig & worker thread nền
# ==============================================================================
def test_series_gui_mapping_and_mocked_compile(gui_app, tmp_path, monkeypatch):
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **kw: None)
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **kw: None)

    # Kiểm tra helper parse_skip_scenes_text
    assert parse_skip_scenes_text("") == []
    assert parse_skip_scenes_text("21, 19, 21") == [19, 21]
    with pytest.raises(ValueError):
        parse_skip_scenes_text("2, abc")

    series_dir = tmp_path / "Series_Test"
    # Tap_01: đủ ảnh + full_audio + cảnh 3 là CTA + có sẵn 2/3 tài nguyên 16:9
    ep1 = series_dir / "Tap_01"
    for i in (1, 2, 3):
        _make_png(ep1 / "assets" / f"{i:03d}.png")
    for i in (1, 2):
        _make_png(ep1 / "assets_16x9" / f"{i:03d}.png", size=(1920, 1080))
    (ep1 / "assets" / "full_audio.wav").write_bytes(b"RIFFdummy")
    _write_storyboard(ep1, "Tap_01_Khoi_Dau", ["Mở đầu", "Diễn biến", "Đón xem tập sau và đăng ký kênh!"])

    # Tap_02: đủ ảnh + archive_sources CC BY-SA 2.0, thiếu full_audio (vẫn bật với cảnh báo TTS)
    ep2 = series_dir / "Tap_02"
    for i in (1, 2):
        _make_png(ep2 / "assets" / f"{i:03d}.png")
    _write_storyboard(ep2, "Tap_02_Cao_Trao", ["Cao trào", "Kết thúc phần 2."])
    (ep2 / "archive_sources.json").write_text(
        json.dumps({"assets": {"001": {"scene_id": 1, "license": "CC BY-SA 2.0", "title": "Pic 1"}}}),
        encoding="utf-8",
    )

    # Tap_03: thiếu ảnh 002.png -> tự bỏ tick và báo thiếu ảnh
    ep3 = series_dir / "Tap_03"
    _make_png(ep3 / "assets" / "001.png")
    _write_storyboard(ep3, "Tap_03_Ket", ["Cảnh 1", "Cảnh cuối series"])

    bgm_file = tmp_path / "bgm.mp3"
    bgm_file.write_bytes(b"ID3")
    custom_out = tmp_path / "custom_export"

    captured: dict = {}

    def fake_compile(s_dir, cfg: SeriesCompileConfig, *, episodes=None, reporter=None, **_kwargs):
        captured["series_dir"] = Path(s_dir)
        captured["config"] = cfg
        captured["episodes"] = list(episodes or [])
        captured["thread_name"] = threading.current_thread().name

        out_dir = Path(cfg.output_dir or (Path(s_dir) / "youtube_export"))
        out_dir.mkdir(parents=True, exist_ok=True)
        vid = out_dir / "Series_Test_Full_16x9.mp4"
        srt = None if cfg.subtitle_mode == "none" else (out_dir / "Series_Test_subtitles.srt")
        desc = out_dir / "youtube_description.txt"
        rep_json = out_dir / "compliance_report.json"
        for f in (vid, desc, rep_json):
            f.write_text("ok", encoding="utf-8")
        if srt is not None:
            srt.write_text("ok", encoding="utf-8")

        return SeriesCompileResult(
            video_path=vid,
            srt_path=srt,
            description_path=desc,
            report_path=rep_json,
            chapters=[
                ChapterMarker(1, "Tap_01", "Mở màn lịch sử", 0.0, 2.5, 10.0, 2.5, [1], [2, 3], []),
                ChapterMarker(2, "Tap_02", "Cao Trao", 12.5, 15.0, 8.0, 2.5, [1, 2], [], [1]),
            ],
            license_report=LicenseAuditReport(),
            total_duration_sec=25.0,
            width=1920,
            height=1080,
            fps=30.0,
            loudnorm_applied=cfg.loudnorm_youtube,
        )

    monkeypatch.setattr("gui.series_youtube_tab.compile_series_youtube", fake_compile)

    tab = gui_app.series_youtube_tab
    tab.load_series(series_dir)
    gui_app.update_idletasks()

    assert len(tab.episode_rows) == 3
    r1, r2, r3 = tab.episode_rows
    assert r1.enabled_var.get() is True and r1.skip_var.get() == "3"
    assert r1.entry.assets_16x9_count == 2
    assert r2.entry.assets_16x9_count == 0
    assert "Sẵn sàng" in r1.status_text
    assert r2.enabled_var.get() is True and "Thiếu audio" in r2.status_text
    assert r3.enabled_var.get() is False and "Thiếu 1 ảnh" in r3.status_text
    assert Path(tab.output_dir.get()) == series_dir.resolve() / "youtube_export"

    def _collect_label_texts(widget: tk.Misc) -> list[str]:
        texts: list[str] = []
        for child in widget.winfo_children():
            try:
                txt = str(child.cget("text"))
                if txt:
                    texts.append(txt)
            except Exception:
                pass
            texts.extend(_collect_label_texts(child))
        return texts

    ui_texts = _collect_label_texts(tab)
    assert any("3 cảnh · 0 thật · 2/3 16:9" in t for t in ui_texts)
    assert any("2 cảnh · 1 thật · 0/2 16:9" in t for t in ui_texts)
    assert tab.prefer_assets_16x9.get() is True

    # Kiểm tra bật/tắt auto_skip_mid_cta cập nhật dòng chưa chỉnh tay, giữ dòng đã chỉnh tay
    tab.auto_skip_mid_cta.set(False)
    assert r1.skip_var.get() == ""
    tab.auto_skip_mid_cta.set(True)
    assert r1.skip_var.get() == "3"

    # Chỉnh sửa trực tiếp tên chương & cảnh bỏ của Tập 1 + toàn bộ cấu hình UI
    r1.chapter_title_var.set("Mở màn lịch sử")
    r1.skip_var.set("2, 3")
    assert r1.user_edited_skip is True

    tab.prefer_assets_16x9.set(False)
    tab.layout_mode_choice.set(LAYOUT_MODES["pillarbox"])
    tab.subtitle_mode_choice.set(SUBTITLE_MODES["none"])
    tab.speed_factor.set(1.15)
    tab.anti_ai_choice.set(ANTI_AI_YT_LEVELS["light"])
    tab.xfade_transition_choice.set(XFADE_TRANSITIONS["fadeblack"])
    tab.xfade_duration.set(0.45)
    tab.smart_clip_fit.set(False)
    tab.video_codec_choice.set(VIDEO_CODECS["h265"])
    tab.watermark_enabled.set(True)
    tab.watermark_position_choice.set(WATERMARK_POSITIONS["bottom_right"])
    tab.watermark_opacity.set(0.55)
    tab.show_lower_third.set(False)
    tab.show_chapter_badge.set(False)
    tab.enable_film_grain_vignette.set(False)
    tab.include_chapter_cards.set(False)
    tab.loudnorm_youtube.set(False)
    tab.bgm_path.set(str(bgm_file))
    tab.bgm_volume.set(0.35)
    tab.sfx_volume.set(0.95)
    tab.channel_name.set("Sử Việt 16:9")
    tab.output_dir.set(str(custom_out))

    tab.start_render()
    deadline = time.monotonic() + 3.0
    while tab.last_result is None and time.monotonic() < deadline:
        gui_app.update()
        time.sleep(0.02)

    assert tab.last_result is not None
    assert tab.last_result.srt_path is None
    assert captured["thread_name"] != threading.main_thread().name
    assert captured["series_dir"] == series_dir.resolve()

    sent_cfg: SeriesCompileConfig = captured["config"]
    sent_eps = captured["episodes"]
    assert sent_cfg.prefer_assets_16x9 is False
    assert sent_cfg.layout_mode == "pillarbox"
    assert sent_cfg.subtitle_mode == "none"
    assert sent_cfg.speed_factor == 1.15
    assert sent_cfg.anti_ai_level == "light"
    assert sent_cfg.xfade_transition == "fadeblack"
    assert sent_cfg.xfade_duration == 0.45
    assert sent_cfg.smart_clip_fit is False
    assert sent_cfg.video_codec == "h265"
    assert sent_cfg.watermark_enabled is True
    assert sent_cfg.watermark_position == "bottom_right"
    assert sent_cfg.watermark_opacity == 0.55
    assert sent_cfg.show_lower_third is False
    assert sent_cfg.show_chapter_badge is False
    assert sent_cfg.enable_film_grain_vignette is False
    assert sent_cfg.include_chapter_cards is False
    assert sent_cfg.include_end_credits is True
    assert sent_cfg.loudnorm_youtube is False
    assert sent_cfg.bgm_path == bgm_file
    assert sent_cfg.bgm_volume == 0.35
    assert sent_cfg.sfx_volume == 0.95
    assert sent_cfg.channel_name == "Sử Việt 16:9"
    assert sent_cfg.watermark_text == "Sử Việt 16:9"
    assert sent_cfg.output_dir == custom_out
    assert sent_cfg.episode_skip_overrides["Tap_01"] == [2, 3]

    # Khi tắt checkbox watermark_enabled thì watermark_text trả về None dù channel_name vẫn giữ cho End Credits
    tab.watermark_enabled.set(False)
    tab.subtitle_mode_choice.set(SUBTITLE_MODES["burn_and_srt"])
    _, cfg_wm_off = tab.build_compile_inputs()
    assert cfg_wm_off.watermark_enabled is False
    assert cfg_wm_off.watermark_text is None
    assert cfg_wm_off.channel_name == "Sử Việt 16:9"
    assert cfg_wm_off.subtitle_mode == "burn_and_srt"

    assert sent_eps[0].chapter_title == "Mở màn lịch sử"
    assert sent_eps[0].skip_scene_ids == [2, 3]
    assert sent_eps[0].enabled is True
    assert sent_eps[2].enabled is False

    assert str(tab.btn_open_export["state"]) == "normal"
    assert str(tab.btn_open_desc["state"]) == "normal"
    assert str(tab.btn_render["state"]) == "normal"
    assert str(tab.btn_cancel["state"]) == "disabled"


# ==============================================================================
# 3. Compliance Panel hiển thị chính xác khi sạch và khi có cảnh báo rủi ro
# ==============================================================================
def test_series_gui_compliance_panel_safe_and_risky(gui_app, tmp_path):
    win = tk.Toplevel(gui_app)
    win.withdraw()
    try:
        apply_theme(win)
        tab = SeriesYouTubeTab(win, _StubApp())
        tab.pack(fill="both", expand=True)

        # Series sạch: CC BY-SA 2.0 + Public Domain
        safe_dir = tmp_path / "Safe_Series"
        ep_safe = safe_dir / "Tap_01"
        _make_png(ep_safe / "assets" / "001.png")
        _write_storyboard(ep_safe, "Tap_01", ["Cảnh 1"])
        (ep_safe / "archive_sources.json").write_text(
            json.dumps(
                {
                    "assets": {
                        "001": {
                            "scene_id": 1,
                            "title": "Archival_CC",
                            "author": "Museum",
                            "license": "CC BY-SA 2.0",
                            "sha256": "s1",
                        },
                        "002": {
                            "scene_id": 2,
                            "title": "Archival_PD",
                            "author": "Archive",
                            "license": "Public domain",
                            "sha256": "s2",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        tab.load_series(safe_dir)
        win.update_idletasks()
        assert tab.compliance_has_warnings is False
        assert tab._risk_panel_visible is False
        assert "Tổng nguồn: 2" in tab.bdg_lic_total.cget("text")
        assert "CC BY / SA: 1" in tab.bdg_lic_cc.cget("text")
        assert "Public Domain: 1" in tab.bdg_lic_pd.cget("text")
        assert "Sạch" in tab.bdg_lic_risk.cget("text")

        # Series rủi ro: CC BY-NC + Associated Press + Getty Images
        risky_dir = tmp_path / "Risky_Series"
        ep_risky = risky_dir / "Tap_01"
        _make_png(ep_risky / "assets" / "001.png")
        _write_storyboard(ep_risky, "Tap_01", ["Cảnh 1"])
        (ep_risky / "archive_sources.json").write_text(
            json.dumps(
                {
                    "assets": {
                        "001": {
                            "scene_id": 1,
                            "title": "Risk_NC_Photo",
                            "author": "User A",
                            "license": "CC BY-NC 4.0",
                            "sha256": "r1",
                        },
                        "002": {
                            "scene_id": 2,
                            "title": "News_AP_Photo",
                            "author": "Associated Press",
                            "provider": "Getty Images",
                            "license": "All rights reserved",
                            "sha256": "r2",
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        tab.load_series(risky_dir)
        win.update_idletasks()
        assert tab.compliance_has_warnings is True
        assert tab._risk_panel_visible is True
        assert "2 nguồn rủi ro" in tab.bdg_lic_risk.cget("text")

        risk_body = tab.risk_text.get("1.0", "end-1c")
        assert "Tap_01" in risk_body
        assert "Risk_NC_Photo" in risk_body
        assert "Associated Press" in risk_body
        assert "Getty" in risk_body
    finally:
        win.destroy()
