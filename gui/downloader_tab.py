"""Tab tải video hàng loạt bằng yt-dlp (kể cả mở rộng playlist/collection) — Dark Studio UI."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core.config import DOWNLOAD_DIR
from core.downloader import download_video, list_entries, parse_url_list
from core.errors import Cancelled

from .theme import COLORS, MONO
from .widgets import FileRow, GradientProgressBar, LogBox, StatusBadge


class DownloaderTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.save_dir = tk.StringVar(value=str(DOWNLOAD_DIR))
        self.height = tk.StringVar(value="1080")
        self.expand = tk.BooleanVar(value=False)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=540)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

    # ------------------------------------------------------------- Cột trái: Link & Tùy chọn
    def _build_left(self, parent):
        # 1. Danh sách link
        box_urls = ttk.LabelFrame(parent, text="1. Danh sách đường dẫn video", style="Card.TLabelframe", padding=10)
        box_urls.pack(fill="both", expand=True, pady=(0, 8))

        ttk.Label(
            box_urls,
            text="⚠️ Chỉ tải video của chính bạn hoặc video bạn có quyền sử dụng (giấy phép mở, được chủ sở hữu đồng ý).",
            style="CardMuted.TLabel",
            foreground=COLORS["warn"],
            wraplength=500,
        ).pack(anchor="w", pady=(0, 6))

        top_bar = ttk.Frame(box_urls, style="Surface.TFrame")
        top_bar.pack(fill="x", pady=(0, 5))
        ttk.Label(
            top_bar,
            text="Mỗi dòng 1 link (hỗ trợ cả dạng: 1. Tên video - https://...)",
            style="CardMuted.TLabel",
        ).pack(side="left")
        self.bdg_count = StatusBadge(top_bar, text="0 link", kind="muted")
        self.bdg_count.pack(side="right")

        self.urls = ScrolledText(
            box_urls,
            height=11,
            bg=COLORS["input"],
            fg=COLORS["text"],
            font=MONO,
            insertbackground=COLORS["accent_hover"],
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            wrap="word",
        )
        self.urls.pack(fill="both", expand=True, pady=(0, 6))
        self.urls.bind("<KeyRelease>", lambda _e: self._update_url_badge())

        url_tools = ttk.Frame(box_urls, style="Surface.TFrame")
        url_tools.pack(fill="x")
        ttk.Button(
            url_tools,
            text="📂 Nạp danh sách từ file…",
            style="Compact.TButton",
            command=self.load_list,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            url_tools,
            text="🧹 Xóa danh sách",
            style="Compact.TButton",
            command=self._clear_urls,
        ).pack(side="left")

        # 2. Tùy chọn tải & nơi lưu
        box_opt = ttk.LabelFrame(parent, text="2. Tùy chọn chất lượng & Thư mục lưu", style="Card.TLabelframe", padding=10)
        box_opt.pack(fill="x", pady=(0, 8))

        FileRow(box_opt, "Thư mục lưu", self.save_dir, mode="dir", label_width=14).pack(fill="x", pady=(0, 6))

        r_opt = ttk.Frame(box_opt, style="Surface.TFrame")
        r_opt.pack(fill="x")
        ttk.Label(r_opt, text="Chất lượng tối đa", width=16, style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            r_opt,
            textvariable=self.height,
            values=["480", "720", "1080", "1440", "2160"],
            width=8,
            state="readonly",
        ).pack(side="left", padx=(0, 4))
        ttk.Label(r_opt, text="p", style="Card.TLabel").pack(side="left", padx=(0, 14))
        ttk.Checkbutton(
            r_opt,
            text="Mở rộng playlist / collection thành từng video",
            variable=self.expand,
            style="Card.TCheckbutton",
        ).pack(side="left")

        # Thanh nút hành động
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        self.btn_start = ttk.Button(bar, text="⬇️ Bắt đầu tải", style="Success.TButton", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_cancel = ttk.Button(
            bar, text="⛔ Hủy", style="Danger.TButton", state="disabled", command=self._cancel
        )
        self.btn_cancel.pack(side="left")
        ttk.Button(
            bar,
            text="📂 Mở thư mục tải",
            style="Neutral.TButton",
            command=self._open_save_dir,
        ).pack(side="right")

    # ------------------------------------------------------------- Cột phải: Tiến độ & Nhật ký
    def _build_right(self, parent):
        ttk.Label(parent, text="Tiến độ & Nhật ký tải video (yt-dlp)", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 4)
        )
        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(0, 5))

        self.status = ttk.Label(parent, text="Sẵn sàng. Dán link rồi bấm 'Bắt đầu tải'.", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(0, 5))

        self.log = LogBox(parent, height=20)
        self.log.pack(fill="both", expand=True)
        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ------------------------------------------------------------- Xử lý sự kiện
    def _update_url_badge(self) -> None:
        items = parse_url_list(self.urls.get("1.0", "end"))
        n = len(items)
        self.bdg_count.set_status(f"{n} link hợp lệ", "ok" if n > 0 else "muted")

    def _clear_urls(self) -> None:
        self.urls.delete("1.0", "end")
        self._update_url_badge()

    def _open_save_dir(self) -> None:
        out_dir = Path(self.save_dir.get().strip() or str(DOWNLOAD_DIR))
        self.app.open_path(out_dir)

    def load_list(self):
        p = filedialog.askopenfilename(filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")])
        if p:
            self.urls.delete("1.0", "end")
            self.urls.insert("1.0", Path(p).read_text(encoding="utf-8", errors="replace"))
            self._update_url_badge()

    def _cancel(self):
        self.reporter.cancel()

    def start(self):
        items = parse_url_list(self.urls.get("1.0", "end"))
        if not items:
            messagebox.showwarning("Thiếu link", "Hãy dán ít nhất một link http(s) vào ô bên trái.")
            return

        out_dir = Path(self.save_dir.get().strip() or str(DOWNLOAD_DIR))
        height = int(self.height.get())
        expand = self.expand.get()
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

            if not queue_items:
                return 0, 0

            ok = 0
            for k, it in enumerate(queue_items, 1):
                rep.check_cancel()
                sub_rep = rep.scope((k - 1) * 100 / len(queue_items), k * 100 / len(queue_items))
                rep.log(f"[{k}/{len(queue_items)}] {it['title'] or it['url']}")
                try:
                    download_video(it["url"], out_dir, sub_rep, max_height=height)
                    ok += 1
                except Cancelled:
                    raise
                except Exception as e:
                    rep.log(f"❌ Lỗi, bỏ qua: {e}", "error")
            return ok, len(queue_items)

        def done(res):
            self.status.config(text=f"Hoàn tất: {res[0]}/{res[1]} video.")
            self.log.write(f"🎉 Xong: {res[0]}/{res[1]} video tải thành công.", "ok")
            if res[0] > 0:
                self.app.open_path(out_dir)

        self.app.run_job(
            job,
            buttons=(self.btn_start,),
            cancel_button=self.btn_cancel,
            reporter=rep,
            log=self.log,
            on_done=done,
        )