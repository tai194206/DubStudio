"""Tab dịch riêng file kịch bản/SRT (không cần video)."""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core import subtitles as sub
from core.config import DEFAULT_GEMINI_MODELS
from core.languages import choices, code_from_choice
from core.translator import FreeTranslator, GeminiTranslator

from .widgets import FileRow, GradientProgressBar, LogBox, row


class TranslatorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        s = app.settings
        self.inp, self.out = tk.StringVar(), tk.StringVar()
        self.src = tk.StringVar(value=choices(True)[0])
        self.tgt = tk.StringVar(value=next(c for c in choices() if code_from_choice(c) == s.target_lang))
        self.model = tk.StringVar(value=s.gemini_model)
        self.topic, self.style, self.glossary = tk.StringVar(value=s.topic), tk.StringVar(value=s.style), tk.StringVar(value=s.glossary)
        self.pronouns = tk.StringVar(value=s.pronoun_rules)

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
        row(box, "Model Gemini", lambda p: ttk.Combobox(p, textvariable=self.model, values=DEFAULT_GEMINI_MODELS))
        row(box, "Chủ đề", lambda p: ttk.Entry(p, textvariable=self.topic))
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

    def start(self):
        src_path, out_path = self.inp.get().strip(), self.out.get().strip()
        if not Path(src_path).is_file():
            messagebox.showwarning("Thiếu file", "Hãy chọn file kịch bản cần dịch.")
            return
        if not out_path:
            messagebox.showwarning("Thiếu nơi lưu", "Hãy chọn nơi lưu kết quả.")
            return
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
