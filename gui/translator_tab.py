"""Tab dịch riêng file kịch bản/SRT (không cần video)."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core import subtitles as sub
from core.config import DEFAULT_GEMINI_MODELS
from core.languages import choices, code_from_choice
from core.style_presets import STYLE_PRESETS, TASK_PROFILES
from core.translator import FreeTranslator, GeminiTranslator, list_gemini_models, resolve_model

from .widgets import FileRow, GradientProgressBar, LogBox, row


class TranslatorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        s = app.settings
        self.inp, self.out = tk.StringVar(), tk.StringVar()
        self.src = tk.StringVar(value=choices(True)[0])
        self.tgt = tk.StringVar(value=next(c for c in choices() if code_from_choice(c) == s.target_lang))
        self.profile = tk.StringVar(value=getattr(s, "task_profile", "Cân bằng (hằng ngày)"))
        self.model = tk.StringVar(value=s.gemini_model)
        self.topic = tk.StringVar(value=s.topic)
        self.style_preset = tk.StringVar(value=getattr(s, "style_preset", "Tự nhiên"))
        self.style = tk.StringVar(value=s.style)
        self.pronouns = tk.StringVar(value=s.pronoun_rules)
        self.glossary = tk.StringVar(value=s.glossary)

        box = ttk.LabelFrame(self, text="1. File kịch bản", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        FileRow(box, "File cần dịch", self.inp, filetypes=[("Kịch bản", "*.txt *.srt")]).pack(fill="x", pady=2)
        FileRow(box, "Lưu kết quả tại", self.out, mode="save", filetypes=[("SRT", "*.srt"), ("Text", "*.txt")]).pack(fill="x", pady=2)
        ttk.Label(box, text="Đọc được: SRT, [00:01 -> 00:03] nội dung, hoặc '1.2, 3.4, nội dung'. Đuôi .srt sẽ xuất SRT.",
                  style="Muted.TLabel").pack(anchor="w")

        box = ttk.LabelFrame(self, text="2. Tùy chọn dịch", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Ngôn ngữ gốc", lambda p: ttk.Combobox(p, textvariable=self.src, values=choices(True), state="readonly"))
        row(box, "Dịch sang", lambda p: ttk.Combobox(p, textvariable=self.tgt, values=choices(), state="readonly"))
        pick_prof = row(box, "Mức độ việc", lambda p: ttk.Combobox(
            p, textvariable=self.profile, values=list(TASK_PROFILES), state="readonly"))
        pick_prof.bind("<<ComboboxSelected>>", self._apply_profile)

        r_model = ttk.Frame(box)
        r_model.pack(fill="x", pady=2)
        ttk.Label(r_model, text="Model Gemini", width=14).pack(side="left")
        self.model_box = ttk.Combobox(r_model, textvariable=self.model, values=DEFAULT_GEMINI_MODELS)
        self.model_box.pack(side="left", fill="x", expand=True)
        ttk.Button(r_model, text="Làm mới", style="Neutral.TButton", command=self._refresh_models).pack(side="left", padx=4)

        row(box, "Chủ đề", lambda p: ttk.Entry(p, textvariable=self.topic))
        preset = row(box, "Phong cách mẫu", lambda p: ttk.Combobox(
            p, textvariable=self.style_preset, values=list(STYLE_PRESETS), state="readonly"))
        preset.bind("<<ComboboxSelected>>",
                    lambda e: self.style.set(STYLE_PRESETS.get(self.style_preset.get(), "")))
        row(box, "Phong cách", lambda p: ttk.Entry(p, textvariable=self.style))
        row(box, "Xưng hô", lambda p: ttk.Entry(p, textvariable=self.pronouns))
        row(box, "Thuật ngữ", lambda p: ttk.Entry(p, textvariable=self.glossary))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=6)
        self.btn = ttk.Button(bar, text="✨ Bắt đầu dịch", style="Success.TButton", command=self.start)
        self.btn.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="⛔ Hủy", style="Danger.TButton", state="disabled", command=lambda: self.reporter.cancel())
        self.btn_cancel.pack(side="left", padx=6)
        self.bar = GradientProgressBar(self)
        self.bar.pack(fill="x", pady=4)
        self.log = LogBox(self, height=12)
        self.log.pack(fill="both", expand=True)
        self.reporter = app.make_reporter(self.log, self.bar)

    def _apply_profile(self, _event=None):
        models = list(self.model_box.cget("values") or [])
        chosen = resolve_model(self.profile.get(), models)
        if chosen:
            self.model.set(chosen)
            self.log.write(f"✓ Chế độ '{self.profile.get()}' → model {chosen}", "ok")
        else:
            self.log.write("ℹ️ Chưa có danh sách model. Bấm 'Làm mới' trước rồi chọn lại chế độ.", "warn")

    def _refresh_models(self):
        key = self.app.keys().gemini
        if not key:
            messagebox.showinfo("Thiếu API key", "Hãy nhập GEMINI_API_KEY trong tab Cài đặt trước.")
            return

        def job():
            return list_gemini_models(key)

        def done(models):
            if models:
                self.model_box.config(values=models)
                self.log.write(f"✓ Có {len(models)} model Gemini khả dụng.", "ok")

        self.app.run_job(job, reporter=self.reporter, log=self.log, on_done=done)

    def start(self):
        src_path, out_path = self.inp.get().strip(), self.out.get().strip()
        if not Path(src_path).is_file():
            messagebox.showwarning("Thiếu file", "Hãy chọn file kịch bản cần dịch.")
            return
        if not out_path:
            messagebox.showwarning("Thiếu nơi lưu", "Hãy chọn nơi lưu kết quả.")
            return

        import dataclasses
        s = dataclasses.replace(
            self.app.settings,
            gemini_model=self.model.get().strip() or DEFAULT_GEMINI_MODELS[0],
            task_profile=self.profile.get(),
            topic=self.topic.get().strip(),
            style_preset=self.style_preset.get(),
            style=self.style.get().strip(),
            pronoun_rules=self.pronouns.get().strip(),
            glossary=self.glossary.get().strip(),
        )
        self.app.settings = s
        s.save()

        keys = self.app.keys()
        src, tgt = code_from_choice(self.src.get()), code_from_choice(self.tgt.get())
        opts = dict(
            topic=self.topic.get(),
            style=self.style.get(),
            pronoun_rules=self.pronouns.get().strip(),
            glossary=self.glossary.get(),
        )
        rep = self.reporter
        self.log.clear()

        def job():
            text = Path(src_path).read_text(encoding="utf-8-sig", errors="replace")
            segs = sub.parse_script(text)
            if not segs:
                raise ValueError("Không đọc được câu thoại nào trong file.")
            rep.log(f"📑 Đọc được {len(segs)} câu")
            if keys.gemini:
                tr = GeminiTranslator(keys.gemini, self.model.get().strip() or DEFAULT_GEMINI_MODELS[0])
            else:
                rep.log("⚠️ Chưa có GEMINI_API_KEY → dùng Google Translate miễn phí", "warn")
                tr = FreeTranslator()
            result = tr.translate(segs, src, tgt, reporter=rep, **opts)
            data = sub.format_srt(result) if out_path.lower().endswith(".srt") else sub.format_editor(result)
            Path(out_path).write_text(data, encoding="utf-8")
            return out_path

        def done(p):
            self.log.write(f"🎉 Đã lưu: {p}", "ok")
            messagebox.showinfo("Thành công", f"Đã dịch xong.\n\n{p}")

        self.app.run_job(job, buttons=(self.btn,), cancel_button=self.btn_cancel, reporter=rep, log=self.log, on_done=done)
