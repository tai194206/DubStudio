"""Tab cài đặt: API key, giọng nói ElevenLabs (đồng bộ cả 2 Studio) và kiểm tra môi trường."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.config import CACHE_DIR, ENV_PATH, OUTPUT_DIR, ROOT_DIR, save_key
from core.diagnostics import check_environment
from core.tts import clone_elevenlabs_voice, list_elevenlabs_voices

from .theme import COLORS
from .widgets import GradientProgressBar, LogBox, ScrollableFrame, StatusBadge


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        k = app.keys()

        self.gemini = tk.StringVar(value=k.gemini)
        self.eleven = tk.StringVar(value=k.elevenlabs)
        self.show = tk.BooleanVar(value=False)
        self.clone_name = tk.StringVar()
        self.samples: list[str] = []
        self._voice_map: dict[str, str] = {}

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=560)
        right = ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

    # ------------------------------------------------------------- Cột trái: API Key & ElevenLabs
    def _build_left(self, parent):
        sc = ScrollableFrame(parent, padding=4)
        sc.pack(fill="both", expand=True)
        body = sc.body

        # 1. API Keys
        box_key = ttk.LabelFrame(
            body,
            text="1. API Key (lưu cục bộ vào file .env, không đưa lên git)",
            style="Card.TLabelframe",
            padding=10,
        )
        box_key.pack(fill="x", pady=(0, 8))

        self.e1 = self._key_row(box_key, "GEMINI_API_KEY", self.gemini, "GEMINI_API_KEY")
        self.e2 = self._key_row(box_key, "ELEVENLABS_API_KEY", self.eleven, "ELEVENLABS_API_KEY")

        opt_row = ttk.Frame(box_key, style="Surface.TFrame")
        opt_row.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(
            opt_row,
            text="Hiện ký tự API key",
            variable=self.show,
            command=self._toggle,
            style="Card.TCheckbutton",
        ).pack(side="left")
        ttk.Label(
            opt_row,
            text="Bấm 'Lưu' để cập nhật ngay thanh trạng thái trên cùng.",
            style="CardMuted.TLabel",
        ).pack(side="right")

        # 2. Giọng nói ElevenLabs
        box_el = ttk.LabelFrame(
            body,
            text="2. Giọng nói ElevenLabs (đồng bộ Dub Studio & Story Studio)",
            style="Card.TLabelframe",
            padding=10,
        )
        box_el.pack(fill="x", pady=(0, 8))

        r1 = ttk.Frame(box_el, style="Surface.TFrame")
        r1.pack(fill="x", pady=(0, 6))
        ttk.Label(r1, text="Danh sách giọng", width=16, style="Card.TLabel").pack(side="left")
        self.voices = ttk.Combobox(r1, state="readonly")
        self.voices.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.btn_load_voices = ttk.Button(
            r1, text="🔄 Tải giọng", style="Compact.TButton", command=self._load_voices
        )
        self.btn_load_voices.pack(side="left", padx=(0, 4))
        ttk.Button(
            r1, text="✓ Dùng giọng này", style="Primary.TButton", command=self._use_voice
        ).pack(side="left")

        ttk.Separator(box_el, orient="horizontal").pack(fill="x", pady=8)

        r2 = ttk.Frame(box_el, style="Surface.TFrame")
        r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="Nhân bản giọng", width=16, style="Card.TLabel").pack(side="left")
        ttk.Entry(r2, textvariable=self.clone_name).pack(side="left", fill="x", expand=True, padx=(4, 6))
        ttk.Button(
            r2, text="📂 File mẫu…", style="Compact.TButton", command=self._pick_samples
        ).pack(side="left", padx=(0, 4))
        self.btn_clone = ttk.Button(
            r2, text="✨ Nhân bản", style="Success.TButton", command=self._clone
        )
        self.btn_clone.pack(side="left")

        r3 = ttk.Frame(box_el, style="Surface.TFrame")
        r3.pack(fill="x", pady=(6, 0))
        self.bdg_samples = StatusBadge(r3, text="Chưa chọn file mẫu", kind="muted")
        self.bdg_samples.pack(side="left")

        ttk.Label(
            box_el,
            text="⚠️ Chỉ nhân bản giọng của chính bạn hoặc người đã đồng ý rõ ràng. Cần gói ElevenLabs hỗ trợ API.",
            style="CardMuted.TLabel",
            foreground=COLORS["warn"],
            wraplength=500,
        ).pack(anchor="w", pady=(6, 0))

        # 3. Truy cập nhanh thư mục hệ thống
        box_dir = ttk.LabelFrame(body, text="3. Thư mục làm việc", style="Card.TLabelframe", padding=10)
        box_dir.pack(fill="x", pady=(0, 6))

        f_btns = ttk.Frame(box_dir, style="Surface.TFrame")
        f_btns.pack(fill="x")
        ttk.Button(
            f_btns, text="📂 Thư mục gốc", style="Compact.TButton",
            command=lambda: self.app.open_path(ROOT_DIR),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            f_btns, text="🎬 Thư mục Output", style="Compact.TButton",
            command=lambda: self.app.open_path(OUTPUT_DIR),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            f_btns, text="🗄️ Thư mục Cache TTS", style="Compact.TButton",
            command=lambda: self.app.open_path(CACHE_DIR),
        ).pack(side="left")

    # ------------------------------------------------------------- Cột phải: Môi trường & Log
    def _build_right(self, parent):
        box_env = ttk.LabelFrame(
            parent, text="Kiểm tra môi trường hệ thống", style="Card.TLabelframe", padding=10
        )
        box_env.pack(fill="x", pady=(0, 8))

        hdr = ttk.Frame(box_env, style="Surface.TFrame")
        hdr.pack(fill="x", pady=(0, 6))
        ttk.Label(
            hdr,
            text="Trạng thái công cụ lõi (FFmpeg, Python, gói mở rộng):",
            style="CardMuted.TLabel",
        ).pack(side="left")
        ttk.Button(
            hdr, text="🔄 Kiểm tra lại", style="Compact.TButton", command=self._render_env_checks
        ).pack(side="right")

        self.env_container = ttk.Frame(box_env, style="Surface.TFrame")
        self.env_container.pack(fill="x")
        self._render_env_checks()

        ttk.Label(parent, text="Nhật ký cài đặt & kết nối API", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 4)
        )
        self.bar = GradientProgressBar(parent, height=18)
        self.bar.pack(fill="x", pady=(0, 5))

        self.log = LogBox(parent, height=12)
        self.log.pack(fill="both", expand=True)
        self.reporter = self.app.make_reporter(self.log, self.bar)

    # ------------------------------------------------------------- Hành động
    def _render_env_checks(self) -> None:
        for child in self.env_container.winfo_children():
            child.destroy()

        for name, ok, note in check_environment():
            f = ttk.Frame(self.env_container, style="Surface.TFrame")
            f.pack(fill="x", pady=2)
            StatusBadge(f, text="✓ OK" if ok else "✕ Thiếu", kind="ok" if ok else "error").pack(
                side="left", padx=(0, 8)
            )
            ttk.Label(f, text=name, width=18, style="Card.TLabel").pack(side="left")
            ttk.Label(f, text=note, style="CardMuted.TLabel", wraplength=340).pack(
                side="left", fill="x", expand=True
            )

    def _key_row(self, parent, label, var, env_name):
        f = ttk.Frame(parent, style="Surface.TFrame")
        f.pack(fill="x", pady=3)
        ttk.Label(f, text=label, width=20, style="Card.TLabel").pack(side="left")
        e = ttk.Entry(f, textvariable=var, show="•")
        e.pack(side="left", fill="x", expand=True, padx=(4, 6))
        ttk.Button(
            f,
            text="Lưu",
            style="Primary.TButton",
            command=lambda: self._save(env_name, var.get().strip()),
        ).pack(side="left")
        return e

    def _toggle(self):
        mask = "" if self.show.get() else "•"
        for e in (self.e1, self.e2):
            e.config(show=mask)

    def _save(self, name: str, value: str):
        save_key(name, value)
        if hasattr(self.app, "refresh_status_badges"):
            self.app.refresh_status_badges()
        status_note = "đã lưu" if value else "đã xóa trống"
        self.log.write(f"✓ {name} {status_note} vào {ENV_PATH.name}", "ok")

    def _apply_voice_id_everywhere(self, vid: str) -> None:
        self.app.settings.elevenlabs_voice_id = vid
        if hasattr(self.app, "studio"):
            self.app.studio.eleven_voice.set(vid)
        if hasattr(self.app, "story_tab"):
            self.app.story_tab.eleven_voice.set(vid)
            self.app.story_tab.save_settings()
        else:
            self.app.settings.save()

    def _load_voices(self):
        key = self.app.keys().elevenlabs
        if not key:
            messagebox.showinfo("Thiếu key", "Hãy nhập và bấm 'Lưu' ELEVENLABS_API_KEY trước.")
            return

        def done(vs):
            self._voice_map = {f"{v['name']} ({v['id']})": v["id"] for v in vs}
            vals = list(self._voice_map)
            self.voices.config(values=vals)
            if vals and not self.voices.get():
                self.voices.set(vals[0])
            self.log.write(f"✓ Đã tải thành công {len(vs)} giọng từ ElevenLabs.", "ok")

        self.app.run_job(
            lambda: list_elevenlabs_voices(key),
            buttons=(self.btn_load_voices,),
            reporter=self.reporter,
            log=self.log,
            on_done=done,
        )

    def _use_voice(self):
        vid = self._voice_map.get(self.voices.get())
        if not vid:
            messagebox.showinfo("Chưa chọn giọng", "Hãy bấm 'Tải giọng' rồi chọn một giọng trong danh sách.")
            return
        self._apply_voice_id_everywhere(vid)
        self.log.write(f"✓ Đã áp dụng Voice ID {vid} cho cả Dub Studio và Story Studio.", "ok")

    def _pick_samples(self):
        files = filedialog.askopenfilenames(filetypes=[("Âm thanh", "*.mp3 *.wav *.m4a *.flac")])
        if files:
            self.samples = list(files)
            self.bdg_samples.set_status(f"✓ Đã chọn {len(files)} file mẫu", "ok")
            self.log.write(f"📂 Đã chọn {len(files)} file mẫu âm thanh để nhân bản giọng.")

    def _clone(self):
        key, name = self.app.keys().elevenlabs, self.clone_name.get().strip()
        if not (key and name and self.samples):
            messagebox.showwarning(
                "Thiếu thông tin",
                "Cần đầy đủ: ELEVENLABS_API_KEY, tên giọng và ít nhất một file mẫu.",
            )
            return
        if not messagebox.askyesno(
            "Xác nhận",
            "Bạn xác nhận đây là giọng của chính bạn hoặc bạn đã được người đó đồng ý rõ ràng?",
        ):
            return

        def done(vid):
            self._apply_voice_id_everywhere(vid)
            self.log.write(
                f"🎉 Đã tạo giọng '{name}' — Voice ID: {vid} (đã điền sẵn cho cả Dub Studio & Story Studio)",
                "ok",
            )

        self.app.run_job(
            lambda: clone_elevenlabs_voice(key, name, self.samples),
            buttons=(self.btn_clone,),
            reporter=self.reporter,
            log=self.log,
            on_done=done,
        )