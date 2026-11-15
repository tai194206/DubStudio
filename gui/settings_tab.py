"""Tab cài đặt: API key, kiểm tra môi trường, danh sách/nhân bản giọng ElevenLabs."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.config import ENV_PATH, save_key
from core.diagnostics import check_environment
from core.tts import clone_elevenlabs_voice, list_elevenlabs_voices

from .theme import COLORS
from .widgets import GradientProgressBar, LogBox


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        k = app.keys()
        self.gemini, self.eleven = tk.StringVar(value=k.gemini), tk.StringVar(value=k.elevenlabs)
        self.show = tk.BooleanVar(value=False)
        self.clone_name = tk.StringVar()
        self.samples: list = []

        box = ttk.LabelFrame(self, text="API key (lưu vào file .env, không bao giờ đưa lên git)", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        self.e1 = self._key_row(box, "GEMINI_API_KEY", self.gemini, "GEMINI_API_KEY")
        self.e2 = self._key_row(box, "ELEVENLABS_API_KEY", self.eleven, "ELEVENLABS_API_KEY")
        ttk.Checkbutton(box, text="Hiện key", variable=self.show, command=self._toggle).pack(anchor="w", pady=2)

        box = ttk.LabelFrame(self, text="ElevenLabs: giọng nói", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        r = ttk.Frame(box)
        r.pack(fill="x")
        self.voices = ttk.Combobox(r, state="readonly")
        self.voices.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Lấy danh sách giọng", style="Neutral.TButton", command=self._load_voices).pack(side="left", padx=4)
        ttk.Button(r, text="Dùng giọng này", style="Primary.TButton", command=self._use_voice).pack(side="left")
        r = ttk.Frame(box)
        r.pack(fill="x", pady=(8, 0))
        ttk.Label(r, text="Nhân bản giọng:").pack(side="left")
        ttk.Entry(r, textvariable=self.clone_name, width=18).pack(side="left", padx=4)
        ttk.Button(r, text="Chọn file mẫu…", style="Neutral.TButton", command=self._pick_samples).pack(side="left")
        ttk.Button(r, text="Nhân bản", style="Success.TButton", command=self._clone).pack(side="left", padx=4)
        ttk.Label(box, text="⚠️ Chỉ nhân bản giọng của chính bạn hoặc người đã đồng ý rõ ràng. Cần gói ElevenLabs hỗ trợ.",
                  foreground=COLORS["warn"]).pack(anchor="w", pady=(4, 0))

        box = ttk.LabelFrame(self, text="Kiểm tra môi trường", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        for name, ok, note in check_environment():
            f = ttk.Frame(box)
            f.pack(fill="x")
            ttk.Label(f, text=("✅ " if ok else "❌ ") + name, width=20,
                      foreground=COLORS["text"] if ok else COLORS["warn"]).pack(side="left")
            ttk.Label(f, text=note, style="Muted.TLabel").pack(side="left")

        self.bar = GradientProgressBar(self, height=8)
        self.log = LogBox(self, height=6)
        self.log.pack(fill="both", expand=True, pady=6)
        self.reporter = app.make_reporter(self.log, self.bar)

    def _key_row(self, parent, label, var, env_name):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        ttk.Label(f, text=label, width=22).pack(side="left")
        e = ttk.Entry(f, textvariable=var, show="•")
        e.pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(f, text="Lưu", style="Primary.TButton",
                   command=lambda: self._save(env_name, var.get().strip())).pack(side="left")
        return e

    def _toggle(self):
        for e in (self.e1, self.e2):
            e.config(show="" if self.show.get() else "•")

    def _save(self, name, value):
        save_key(name, value)
        self.log.write(f"✓ Đã lưu {name} vào {ENV_PATH.name}", "ok")

    def _load_voices(self):
        key = self.app.keys().elevenlabs
        if not key:
            messagebox.showinfo("Thiếu key", "Hãy nhập và lưu ELEVENLABS_API_KEY trước.")
            return

        def done(vs):
            self._voice_map = {f"{v['name']} ({v['id']})": v["id"] for v in vs}
            self.voices.config(values=list(self._voice_map))
            self.log.write(f"✓ Có {len(vs)} giọng.", "ok")

        self.app.run_job(lambda: list_elevenlabs_voices(key), reporter=self.reporter, log=self.log, on_done=done)

    def _use_voice(self):
        vid = getattr(self, "_voice_map", {}).get(self.voices.get())
        if vid:
            self.app.studio.eleven_voice.set(vid)
            self.log.write(f"✓ Đã chọn Voice ID {vid} cho tab Dub Studio.", "ok")

    def _pick_samples(self):
        files = filedialog.askopenfilenames(filetypes=[("Âm thanh", "*.mp3 *.wav *.m4a *.flac")])
        if files:
            self.samples = list(files)
            self.log.write(f"Đã chọn {len(files)} file mẫu.")

    def _clone(self):
        key, name = self.app.keys().elevenlabs, self.clone_name.get().strip()
        if not (key and name and self.samples):
            messagebox.showwarning("Thiếu thông tin", "Cần: ELEVENLABS_API_KEY, tên giọng, và ít nhất một file mẫu.")
            return
        if not messagebox.askyesno("Xác nhận", "Bạn xác nhận đây là giọng của chính bạn hoặc bạn đã được người đó đồng ý?"):
            return

        def done(vid):
            self.app.studio.eleven_voice.set(vid)
            self.log.write(f"🎉 Đã tạo giọng '{name}', Voice ID: {vid} (đã điền sang tab Dub Studio)", "ok")

        self.app.run_job(lambda: clone_elevenlabs_voice(key, name, self.samples),
                         reporter=self.reporter, log=self.log, on_done=done)
