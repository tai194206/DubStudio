"""Cửa sổ chính: gom các tab, chạy tác vụ nền an toàn và cập nhật giao diện từ luồng phụ."""
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

from core.config import APP_NAME, APP_VERSION, DubSettings, Keys, load_env
from core.errors import Cancelled, DubberError
from core.ffmpeg_utils import find_binary
from core.reporter import Reporter

from .downloader_tab import DownloaderTab
from .settings_tab import SettingsTab
from .story_tab import StoryTab
from .studio_tab import StudioTab
from .theme import apply_theme
from .translator_tab import TranslatorTab
from .widgets import GradientProgressBar, LogBox


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        load_env()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1240x800")
        self.minsize(1000, 680)
        apply_theme(self)

        self.settings = DubSettings.load()
        self._ui_queue: "queue.Queue[Callable]" = queue.Queue()
        self.after(60, self._pump)

        header = ttk.Label(self, text="🎬  AI VIDEO DUBBING STUDIO", font=("Segoe UI", 16, "bold"),
                           foreground="#3498db")
        header.pack(pady=(10, 4))
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.studio = StudioTab(self.notebook, self)
        self.story_tab = StoryTab(self.notebook, self)
        self.downloader = DownloaderTab(self.notebook, self)
        self.translator = TranslatorTab(self.notebook, self)
        self.settings_tab = SettingsTab(self.notebook, self)
        self.notebook.add(self.studio, text="  🎙️ Dub Studio  ")
        self.notebook.add(self.story_tab, text="  📱 Story Studio  ")
        self.notebook.add(self.downloader, text="  ⬇️ Tải video  ")
        self.notebook.add(self.translator, text="  🌐 Dịch kịch bản  ")
        self.notebook.add(self.settings_tab, text="  ⚙️ Cài đặt  ")

        if not find_binary("ffmpeg"):
            self.after(400, lambda: messagebox.showwarning(
                "Thiếu FFmpeg", "Chưa tìm thấy FFmpeg. Xem tab Cài đặt để biết cách khắc phục."))
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----------------------------------------------------- luồng giao diện
    def post(self, fn: Callable, *args) -> None:
        """Gọi từ bất kỳ luồng nào: hàm sẽ chạy trên luồng giao diện."""
        self._ui_queue.put(lambda: fn(*args))

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

    def make_reporter(self, log: LogBox, bar: GradientProgressBar, status: Optional[ttk.Label] = None) -> Reporter:
        def on_progress(pct, msg):
            self.post(bar.set, pct)
            if msg and status is not None:
                self.post(lambda m=msg: status.config(text=m))

        return Reporter(on_log=lambda m, lv: self.post(log.write, m, lv), on_progress=on_progress)

    def run_job(self, fn: Callable, *, buttons=(), cancel_button=None, reporter: Reporter,
                log: LogBox, on_done: Optional[Callable] = None) -> None:
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
                self.post(log.write, "⛔ Đã hủy.", "warn")
            except DubberError as e:
                self.post(log.write, f"❌ {e}", "error")
                self.post(messagebox.showerror, "Lỗi", str(e))
            except Exception as e:  # lỗi không lường trước: ghi chi tiết ra log để dễ báo lỗi
                self.post(log.write, f"❌ Lỗi không mong đợi: {e}\n{traceback.format_exc()[-800:]}", "error")
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
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def _on_close(self) -> None:
        try:
            self.studio.collect().save()
        except Exception:
            pass
        self.destroy()


def main() -> None:
    App().mainloop()
