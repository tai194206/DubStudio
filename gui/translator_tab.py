"""Tab dịch riêng file kịch bản/SRT (không cần video) — Dark Studio UI."""
from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core import subtitles as sub
from core.config import DEFAULT_GEMINI_MODELS
from core.languages import choices, code_from_choice
from core.style_presets import STYLE_PRESETS, TASK_PROFILES
from core.translator import FreeTranslator, GeminiTranslator, list_gemini_models, resolve_model

from .widgets import FileRow, GradientProgressBar, LogBox, ScrollableFrame, StatusBadge, row


class TranslatorTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        s = app.settings

        self.inp = tk.StringVar()
        self.out = tk.StringVar()
        self.src = tk.StringVar(value=choices(True)[0])
        tgt_default = next((c for c in choices() if code_from_choice(c) == s.target_lang), choices()[0])
        self.tgt = tk.StringVar(value=tgt_default)
        self.profile = tk.StringVar(value=getattr(s, "task_profile", "Cân bằng (hằng ngày)"))
        self.model = tk.StringVar(value=s.gemini_model)
        self.topic = tk.StringVar(value=s.topic)
        self.style_preset = tk.StringVar(value=getattr(s, "style_preset", "Tự nhiên"))
        self.style = tk.StringVar(value=s.style)
        self.pronouns = tk.StringVar(value=s.pronoun_rules)
        self.glossary = tk.StringVar(value=s.glossary)
        self.last_output: Path | None = None

        self.inp.trace_add("write", self._auto_fill_output)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=520)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)
        self._refresh_engine_badge()

    # ------------------------------------------------------------- Cột trái: File & Tùy chọn dịch
    def _build_left(self, parent):
        sc = ScrollableFrame(parent, padding=4)
        sc.pack(fill="both", expand=True)
        body = sc.body

        # 1. File kịch bản
        box_file = ttk.LabelFrame(body, text="1. File kịch bản nguồn & đầu ra", style="Card.TLabelframe", padding=10)
        box_file.pack(fill="x", pady=(0, 8))

        FileRow(
            box_file,
            "File cần dịch",
            self.inp,
            mode="open",
            filetypes=[("Kịch bản", "*.txt *.srt"), ("Tất cả", "*.*")],
            label_width=15,
        ).pack(fill="x", pady=3)

        FileRow(
            box_file,
            "Lưu kết quả tại",
            self.out,
            mode="save",
            filetypes=[("SRT", "*.srt"), ("Text", "*.txt"), ("Tất cả", "*.*")],
            label_width=15,
        ).pack(fill="x", pady=3)

        ttk.Label(
            box_file,
            text="Đọc được: SRT, [00:01 -> 00:03] nội dung, hoặc '1.2, 3.4, nội dung'. Đuôi .srt sẽ xuất chuẩn SRT.",
            style="CardMuted.TLabel",
            wraplength=455,
        ).pack(anchor="w", pady=(4, 0))

        # 2. Ngôn ngữ & Model Gemini
        box_model = ttk.LabelFrame(body, text="2. Ngôn ngữ & Model dịch", style="Card.TLabelframe", padding=10)
        box_model.pack(fill="x", pady=(0, 8))

        row(
            box_model,
            "Ngôn ngữ gốc",
            lambda p: ttk.Combobox(p, textvariable=self.src, values=choices(True), state="readonly"),
            label_width=15,
        )
        row(
            box_model,
            "Dịch sang",
            lambda p: ttk.Combobox(p, textvariable=self.tgt, values=choices(), state="readonly"),
            label_width=15,
        )
        pick_prof = row(
            box_model,
            "Mức độ việc",
            lambda p: ttk.Combobox(p, textvariable=self.profile, values=list(TASK_PROFILES), state="readonly"),
            label_width=15,
        )
        pick_prof.bind("<<ComboboxSelected>>", self._apply_profile)

        r_model = ttk.Frame(box_model, style="Surface.TFrame")
        r_model.pack(fill="x", pady=3)
        ttk.Label(r_model, text="Model Gemini", width=15, style="Card.TLabel").pack(side="left")
        self.model_box = ttk.Combobox(r_model, textvariable=self.model, values=DEFAULT_GEMINI_MODELS)
        self.model_box.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(r_model, text="Làm mới", style="Compact.TButton", command=self._refresh_models).pack(side="left")

        # 3. Ngữ cảnh dịch
        box_ctx = ttk.LabelFrame(body, text="3. Ngữ cảnh giúp bản dịch tự nhiên hơn", style="Card.TLabelframe", padding=10)
        box_ctx.pack(fill="x", pady=(0, 6))

        row(box_ctx, "Chủ đề", lambda p: ttk.Entry(p, textvariable=self.topic), label_width=15)
        preset = row(
            box_ctx,
            "Phong cách mẫu",
            lambda p: ttk.Combobox(p, textvariable=self.style_preset, values=list(STYLE_PRESETS), state="readonly"),
            label_width=15,
        )
        preset.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.style.set(STYLE_PRESETS.get(self.style_preset.get(), "")),
        )
        row(box_ctx, "Phong cách", lambda p: ttk.Entry(p, textvariable=self.style), label_width=15)
        row(box_ctx, "Xưng hô", lambda p: ttk.Entry(p, textvariable=self.pronouns), label_width=15)
        row(box_ctx, "Thuật ngữ", lambda p: ttk.Entry(p, textvariable=self.glossary), label_width=15)

        # Thanh nút hành động ghim đáy cột trái
        bar = ttk.Frame(parent, padding=(2, 8, 2, 0))
        bar.pack(fill="x")

        self.btn = ttk.Button(bar, text="✨ Bắt đầu dịch", style="Success.TButton", command=self.start)
        self.btn.pack(side="left", padx=(0, 6))

        self.btn_cancel = ttk.Button(
            bar,
            text="⛔ Hủy",
            style="Danger.TButton",
            state="disabled",
            command=lambda: self.reporter.cancel(),
        )
        self.btn_cancel.pack(side="left", padx=(0, 6))

        self.btn_open = ttk.Button(
            bar,
            text="📂 Mở kết quả",
            style="Neutral.TButton",
            state="disabled",
            command=self._open_output,
        )
        self.btn_open.pack(side="right")

    # ------------------------------------------------------------- Cột phải: Tiến độ & Nhật ký
    def _build_right(self, parent):
        box_info = ttk.LabelFrame(parent, text="Trạng thái trình dịch", style="Card.TLabelframe", padding=10)
        box_info.pack(fill="x", pady=(0, 8))

        r_info = ttk.Frame(box_info, style="Surface.TFrame")
        r_info.pack(fill="x")
        self.bdg_engine = StatusBadge(r_info, text="Đang kiểm tra…", kind="muted")
        self.bdg_engine.pack(side="left", padx=(0, 8))
        ttk.Button(
            r_info,
            text="🔄 Cập nhật trạng thái API",
            style="Compact.TButton",
            command=self._refresh_engine_badge,
        ).pack(side="right")

        ttk.Label(parent, text="Tiến độ & Nhật ký dịch kịch bản", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 4)
        )
        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(0, 5))

        self.status = ttk.Label(parent, text="Sẵn sàng. Chọn file kịch bản rồi bấm 'Bắt đầu dịch'.", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(0, 5))

        self.log = LogBox(parent, height=18)
        self.log.pack(fill="both", expand=True)
        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ------------------------------------------------------------- Xử lý sự kiện
    def _refresh_engine_badge(self) -> None:
        if self.app.keys().gemini:
            m = self.model.get().strip() or DEFAULT_GEMINI_MODELS[0]
            self.bdg_engine.set_status(f"✓ Gemini API ({m})", "ok")
        else:
            self.bdg_engine.set_status("⚠️ Chưa có GEMINI_API_KEY → dùng Google Translate miễn phí", "warn")

    def _auto_fill_output(self, *_) -> None:
        raw_in = self.inp.get().strip()
        if not raw_in or self.out.get().strip():
            return
        p = Path(raw_in)
        if p.is_file():
            tgt_code = code_from_choice(self.tgt.get()) or "vi"
            ext = p.suffix if p.suffix.lower() in (".srt", ".txt") else ".srt"
            self.out.set(str(p.with_name(f"{p.stem}_{tgt_code}{ext}")))

    def _open_output(self) -> None:
        if self.last_output and self.last_output.is_file():
            self.app.open_path(self.last_output.parent)

    def _apply_profile(self, _event=None):
        models = list(self.model_box.cget("values") or [])
        chosen = resolve_model(self.profile.get(), models)
        if chosen:
            self.model.set(chosen)
            self._refresh_engine_badge()
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
                self._refresh_engine_badge()
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
        self._refresh_engine_badge()

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
        self.btn_open.config(state="disabled")

        def job():
            text = Path(src_path).read_text(encoding="utf-8-sig", errors="replace")
            segs = sub.parse_script(text)
            if not segs:
                raise ValueError("Không đọc được câu thoại nào trong file.")
            rep.log(f"📑 Đọc được {len(segs)} câu thoại từ {Path(src_path).name}")
            if keys.gemini:
                tr = GeminiTranslator(keys.gemini, self.model.get().strip() or DEFAULT_GEMINI_MODELS[0])
            else:
                rep.log("⚠️ Chưa có GEMINI_API_KEY → dùng Google Translate miễn phí", "warn")
                tr = FreeTranslator()
            result = tr.translate(segs, src, tgt, reporter=rep, **opts)
            data = sub.format_srt(result) if out_path.lower().endswith(".srt") else sub.format_editor(result)
            out_file = Path(out_path)
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(data, encoding="utf-8")
            return out_file

        def done(p: Path):
            self.last_output = p
            self.btn_open.config(state="normal")
            self.status.config(text=f"Hoàn tất dịch: {p.name}")
            self.log.write(f"🎉 Đã lưu bản dịch: {p}", "ok")
            messagebox.showinfo("Thành công", f"Đã dịch xong.\n\n{p}")

        self.app.run_job(
            job,
            buttons=(self.btn,),
            cancel_button=self.btn_cancel,
            reporter=rep,
            log=self.log,
            on_done=done,
        )