"""Cửa sổ chính: gom các tab, thanh trạng thái trực tiếp và chạy tác vụ nền an toàn."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import messagebox, ttk
from typing import Callable, Optional

from core.config import APP_NAME, APP_VERSION, OUTPUT_DIR, DubSettings, Keys, load_env
from core.errors import Cancelled, DubberError
from core.ffmpeg_utils import find_binary
from core.reporter import Reporter

from .downloader_tab import DownloaderTab
from .series_youtube_tab import SeriesYouTubeTab
from .settings_tab import SettingsTab
from .story_tab import StoryTab
from .studio_tab import StudioTab
from .theme import COLORS, apply_theme
from .translator_tab import TranslatorTab
from .widgets import GradientProgressBar, LogBox, StatusBadge


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        load_env()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1280x820")
        self.minsize(1040, 700)
        apply_theme(self)

        self.settings = DubSettings.load()
        self._ui_queue: "queue.Queue[Callable]" = queue.Queue()
        self.after(60, self._pump)

        self._build_header()
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(8, 12))

        self.studio = StudioTab(self.notebook, self)
        self.story_tab = StoryTab(self.notebook, self)
        self.series_youtube_tab = SeriesYouTubeTab(self.notebook, self)
        self.downloader = DownloaderTab(self.notebook, self)
        self.translator = TranslatorTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)

        self.notebook.add(self.studio, text="  🎙️ Dub Studio  ")
        self.notebook.add(self.story_tab, text="  📱 Story Studio 9:16  ")
        self.notebook.add(self.series_youtube_tab, text="  📺 YouTube 16:9  ")
        self.notebook.add(self.downloader, text="  ⬇️ Tải video  ")
        self.notebook.add(self.translator, text="  🌐 Dịch kịch bản  ")
        self.notebook.add(self.settings_tab, text="  ⚙️ Cài đặt  ")

        self.refresh_status_badges()

        if not find_binary("ffmpeg"):
            self.after(
                400,
                lambda: messagebox.showwarning(
                    "Thiếu FFmpeg",
                    "Chưa tìm thấy FFmpeg. Xem tab Cài đặt để biết cách khắc phục.",
                ),
            )

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------- thanh tiêu đề & trạng thái
    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(14, 10, 14, 8))
        bar.pack(fill="x")

        left = ttk.Frame(bar)
        left.pack(side="left", fill="x")

        ttk.Label(left, text="🎬  DUBSTUDIO", style="Title.TLabel").pack(side="left")
        StatusBadge(left, text=f"v{APP_VERSION}", kind="info").pack(side="left", padx=(8, 10))
        ttk.Label(
            left,
            text="AI Video Dubbing & Dựng phim Lịch sử 9:16",
            style="Muted.TLabel",
        ).pack(side="left")

        right = ttk.Frame(bar)
        right.pack(side="right")

        self.badge_ffmpeg = StatusBadge(right, text="FFmpeg", kind="muted")
        self.badge_ffmpeg.pack(side="left", padx=(0, 6))

        self.badge_gemini = StatusBadge(right, text="Gemini", kind="muted")
        self.badge_gemini.pack(side="left", padx=(0, 6))

        self.badge_eleven = StatusBadge(right, text="ElevenLabs", kind="muted")
        self.badge_eleven.pack(side="left", padx=(0, 10))

        ttk.Button(
            right,
            text="📂 Output",
            style="Compact.TButton",
            command=lambda: self.open_path(OUTPUT_DIR),
        ).pack(side="left")

    def refresh_status_badges(self) -> None:
        """Cập nhật trạng thái FFmpeg và API key trên thanh tiêu đề (gọi sau khi lưu key)."""
        has_ff = bool(find_binary("ffmpeg"))
        self.badge_ffmpeg.set_status("✓ FFmpeg" if has_ff else "✕ Thiếu FFmpeg", "ok" if has_ff else "error")

        k = self.keys()
        if k.gemini:
            self.badge_gemini.set_status("✓ Gemini API", "ok")
        else:
            self.badge_gemini.set_status("○ Chưa có Gemini", "muted")

        if k.elevenlabs:
            self.badge_eleven.set_status("✓ ElevenLabs API", "ok")
        else:
            self.badge_eleven.set_status("○ Chưa có ElevenLabs", "muted")

        if hasattr(self, "story_tab") and hasattr(self.story_tab, "_refresh_ui_states"):
            try:
                self.story_tab._refresh_ui_states()
            except Exception:
                pass

    # ----------------------------------------------------- luồng giao diện
    def post(self, fn: Callable, *args, **kwargs) -> None:
        """Gọi từ bất kỳ luồng nào: hàm sẽ chạy an toàn trên luồng giao diện."""
        self._ui_queue.put(lambda: fn(*args, **kwargs))

    def _pump(self) -> None:
        try:
            while True:
                self._ui_queue.get_nowait()()
        except queue.Empty:
            pass
        finally:
            self.after(60, self._pump)

    def keys(self) -> Keys:
        return Keys.from_env()

    def make_reporter(
        self,
        log: LogBox,
        bar: GradientProgressBar,
        status: Optional[ttk.Label] = None,
    ) -> Reporter:
        def on_progress(pct, msg):
            self.post(bar.set, pct)
            if msg and status is not None:
                self.post(lambda m=msg: status.config(text=m))

        return Reporter(
            on_log=lambda m, lv: self.post(log.write, m, lv),
            on_progress=on_progress,
        )

    def run_job(
        self,
        fn: Callable,
        *,
        buttons=(),
        cancel_button=None,
        reporter: Reporter,
        log: LogBox,
        on_done: Optional[Callable] = None,
    ) -> None:
        """Chạy `fn` ở luồng nền, khóa/mở các nút, hiện lỗi dễ hiểu thay vì treo giao diện."""
        for b in buttons:
            b.config(state="disabled")
        if cancel_button is not None:
            cancel_button.config(state="normal")
        reporter.cancel_event.clear()

        def work():
            result, ok = None, False
            try:
                result = fn()
                ok = True
            except Cancelled:
                self.post(log.write, "⛔ Đã hủy tác vụ.", "warn")
            except DubberError as e:
                self.post(log.write, f"❌ {e}", "error")
                self.post(messagebox.showerror, "Lỗi", str(e))
            except Exception as e:
                self.post(
                    log.write,
                    f"❌ Lỗi không mong đợi: {e}\n{traceback.format_exc()[-800:]}",
                    "error",
                )
                self.post(messagebox.showerror, "Lỗi không mong đợi", str(e))
            finally:
                def restore():
                    for b in buttons:
                        b.config(state="normal")
                    if cancel_button is not None:
                        cancel_button.config(state="disabled")
                    if ok and on_done:
                        on_done(result)

                self.post(restore)

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def open_path(path) -> None:
        try:
            target = str(path)
            if not os.path.exists(target):
                os.makedirs(target, exist_ok=True)
            if os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            self.studio.collect()
            if hasattr(self.story_tab, "save_settings"):
                self.story_tab.save_settings()
            else:
                self.settings.save()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    App().mainloop()