"""Tab tải video hàng loạt bằng yt-dlp (kể cả mở rộng playlist/collection)."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core.config import DOWNLOAD_DIR
from core.downloader import download_video, list_entries, parse_url_list
from core.errors import Cancelled

from .theme import COLORS, MONO
from .widgets import FileRow, GradientProgressBar, LogBox


class DownloaderTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        self.save_dir = tk.StringVar(value=str(DOWNLOAD_DIR))
        self.height = tk.StringVar(value="1080")
        self.expand = tk.BooleanVar(value=False)

        ttk.Label(self, text="⚠️ Chỉ tải video của chính bạn hoặc video bạn được phép sử dụng (giấy phép mở, được chủ sở hữu đồng ý).",
                  foreground=COLORS["warn"]).pack(anchor="w")
        ttk.Label(self, text="Mỗi dòng một link. Chấp nhận cả dạng cũ: 1. Tên video - https://...",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 2))
        self.urls = ScrolledText(self, height=9, bg=COLORS["input"], fg=COLORS["text"], font=MONO,
                                 insertbackground=COLORS["text"], relief="flat")
        self.urls.pack(fill="x")

        FileRow(self, "Thư mục lưu", self.save_dir, mode="dir").pack(fill="x", pady=(8, 2))
        opts = ttk.Frame(self)
        opts.pack(fill="x", pady=4)
        ttk.Label(opts, text="Chất lượng tối đa").pack(side="left")
        ttk.Combobox(opts, textvariable=self.height, values=["480", "720", "1080", "1440", "2160"],
                     width=7, state="readonly").pack(side="left", padx=6)
        ttk.Label(opts, text="p").pack(side="left")
        ttk.Checkbutton(opts, text="Mở rộng playlist/collection thành từng video",
                        variable=self.expand).pack(side="left", padx=20)

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=6)
        self.btn_start = ttk.Button(bar, text="⬇️ Bắt đầu tải", style="Success.TButton", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="⛔ Hủy", style="Danger.TButton", state="disabled", command=self._cancel)
        self.btn_cancel.pack(side="left", padx=6)
        ttk.Button(bar, text="📂 Nạp danh sách từ file", style="Neutral.TButton", command=self.load_list).pack(side="right")

        self.bar = GradientProgressBar(self)
        self.bar.pack(fill="x", pady=(6, 2))
        self.status = ttk.Label(self, text="Sẵn sàng", style="Muted.TLabel")
        self.status.pack(anchor="w")
        self.log = LogBox(self, height=12)
        self.log.pack(fill="both", expand=True, pady=4)
        self.reporter = app.make_reporter(self.log, self.bar, self.status)

    def load_list(self):
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")])
        if p:
            self.urls.delete("1.0", "end")
            self.urls.insert("1.0", Path(p).read_text(encoding="utf-8", errors="replace"))

    def _cancel(self):
        self.reporter.cancel()

    def start(self):
        items = parse_url_list(self.urls.get("1.0", "end"))
        if not items:
            messagebox.showwarning("Thiếu link", "Hãy dán ít nhất một link http(s) vào ô trên.")
            return
        out_dir, height, expand = Path(self.save_dir.get()), int(self.height.get()), self.expand.get()
        self.log.clear()
        rep = self.reporter

        def job():
            queue_items = []
            if expand:
                for it in items:
                    rep.log(f"🔎 Đang liệt kê: {it['url']}")
                    try:
                        queue_items += list_entries(it["url"])
                    except Exception as e:
                        rep.log(f"⚠️ Không liệt kê được {it['url']}: {e}", "warn")
            else:
                queue_items = items
            ok = 0
            for k, it in enumerate(queue_items, 1):
                rep.check_cancel()
                sub = rep.scope((k - 1) * 100 / len(queue_items), k * 100 / len(queue_items))
                rep.log(f"[{k}/{len(queue_items)}] {it['title'] or it['url']}")
                try:
                    download_video(it["url"], out_dir, sub, max_height=height)
                    ok += 1
                except Cancelled:
                    raise
                except Exception as e:
                    rep.log(f"❌ Lỗi, bỏ qua: {e}", "error")
            return ok, len(queue_items)

        def done(res):
            self.log.write(f"🎉 Xong: {res[0]}/{res[1]} video tải thành công.", "ok")
            self.app.open_path(out_dir)

        self.app.run_job(job, buttons=(self.btn_start,), cancel_button=self.btn_cancel,
                         reporter=rep, log=self.log, on_done=done)
