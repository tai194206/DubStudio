"""Tab chính: tạo kịch bản (tải → nhận diện → dịch), chỉnh sửa, rồi lồng tiếng và xuất video."""
from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from core import subtitles as sub
from core.config import DEFAULT_GEMINI_MODELS, OUTPUT_DIR, DubSettings
from core.downloader import is_url
from core.languages import choices, code_from_choice, get_language
from core.pipeline import DubbingPipeline
from core.separator import demucs_available
from core.style_presets import STYLE_PRESETS, TASK_PROFILES
from core.transcriber import WHISPER_MODELS
from core.translator import list_gemini_models, resolve_model

from .theme import COLORS, MONO
from .widgets import GradientProgressBar, LabeledScale, LogBox, row

ENGINES = {"edge": "Edge-TTS (miễn phí)", "elevenlabs": "ElevenLabs (trả phí, giọng đẹp)",
           "tone": "Giọng thử (tiếng bíp, để test đồng bộ)"}
MIXES = {"duck": "Duck - tự nhỏ nhạc gốc khi AI nói", "simple": "Simple - giảm âm lượng gốc cố định",
         "stems": "Stems - tách nhạc nền (cần Demucs)"}


def _label(mapping: dict, key: str) -> str:
    return mapping.get(key, next(iter(mapping.values())))


def _key(mapping: dict, label: str) -> str:
    return next((k for k, v in mapping.items() if v == label), next(iter(mapping)))


def _lang_choice(code: str, auto: bool = False) -> str:
    for c in choices(include_auto=auto):
        if code_from_choice(c) == code:
            return c
    return choices(include_auto=auto)[0]


class StudioTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        s = app.settings
        self.video_path = None
        self._pipeline = None

        self.source = tk.StringVar()
        self.src_lang = tk.StringVar(value=_lang_choice(s.source_lang, True))
        self.tgt_lang = tk.StringVar(value=_lang_choice(s.target_lang))
        self.whisper = tk.StringVar(value=s.whisper_model)
        self.engine = tk.StringVar(value=_label(ENGINES, s.tts_engine))
        self.voice = tk.StringVar(value=s.edge_voice or get_language(s.target_lang).edge_voice)
        self.eleven_voice = tk.StringVar(value=s.elevenlabs_voice_id)
        self.max_speed = tk.DoubleVar(value=s.max_speed)
        self.mix = tk.StringVar(value=_label(MIXES, s.mix_mode))
        self.vol_orig = tk.DoubleVar(value=s.original_volume)
        self.vol_ai = tk.DoubleVar(value=s.ai_volume)
        self.vol_bg = tk.DoubleVar(value=s.bg_volume)
        self.burn = tk.BooleanVar(value=s.burn_captions)
        self.export_srt = tk.BooleanVar(value=s.export_srt)
        self.profile = tk.StringVar(value=getattr(s, "task_profile", "Cân bằng (hằng ngày)"))
        self.model = tk.StringVar(value=s.gemini_model)
        self.topic = tk.StringVar(value=s.topic)
        self.style_preset = tk.StringVar(value=s.style_preset)
        self.style = tk.StringVar(value=s.style)
        self.pronouns = tk.StringVar(value=s.pronoun_rules)
        self.glossary = tk.StringVar(value=s.glossary)

        self.source.trace_add("write", lambda *_: setattr(self, "video_path", None))
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=6, pady=6)
        left, right = ttk.Frame(panes, width=430), ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)
        self._build_left(left)
        self._build_right(right)
        self.tgt_lang.trace_add("write", self._on_target_change)
        self._on_target_change(keep_current=True)

    # ------------------------------------------------------------------ bố cục
    def _build_left(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        a = ttk.Frame(nb, padding=10)
        nb.add(a, text="Nguồn & ngôn ngữ")
        box = ttk.LabelFrame(a, text="Video nguồn", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        r = ttk.Frame(box)
        r.pack(fill="x")
        ttk.Entry(r, textvariable=self.source).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Chọn file…", style="Neutral.TButton", command=self._browse_source).pack(side="left", padx=4)
        ttk.Label(box, text="Dán link (YouTube…) hoặc chọn file. Chỉ dùng video bạn có quyền sử dụng.",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(4, 0))
        box = ttk.LabelFrame(a, text="Ngôn ngữ", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Ngôn ngữ gốc", lambda p: ttk.Combobox(p, textvariable=self.src_lang, values=choices(True), state="readonly"))
        row(box, "Dịch sang", lambda p: ttk.Combobox(p, textvariable=self.tgt_lang, values=choices(), state="readonly"))
        box = ttk.LabelFrame(a, text="Nhận diện giọng nói (faster-whisper)", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Model", lambda p: ttk.Combobox(p, textvariable=self.whisper, values=WHISPER_MODELS, state="readonly"))
        ttk.Label(box, text="small: cân bằng. large-v3: chính xác hơn nhưng chậm, nên có GPU.",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w")

        b = ttk.Frame(nb, padding=10)
        nb.add(b, text="Giọng & âm thanh")
        box = ttk.LabelFrame(b, text="Giọng đọc", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Engine", lambda p: ttk.Combobox(p, textvariable=self.engine, values=list(ENGINES.values()), state="readonly"))
        self.voice_box = row(box, "Giọng Edge", lambda p: ttk.Combobox(p, textvariable=self.voice))
        row(box, "ElevenLabs Voice ID", lambda p: ttk.Entry(p, textvariable=self.eleven_voice))
        box.winfo_children()[-1].winfo_children()[0].config(width=18)
        LabeledScale(box, "Tốc độ tối đa", self.max_speed, 1.0, 2.0, "{:.2f}x").pack(fill="x", pady=2)
        box = ttk.LabelFrame(b, text="Trộn âm thanh", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Chế độ", lambda p: ttk.Combobox(p, textvariable=self.mix, values=list(MIXES.values()), state="readonly"))
        LabeledScale(box, "Âm lượng video gốc", self.vol_orig, 0.0, 1.5).pack(fill="x", pady=2)
        LabeledScale(box, "Âm lượng giọng AI", self.vol_ai, 0.2, 2.0).pack(fill="x", pady=2)
        LabeledScale(box, "Nhạc nền (Stems)", self.vol_bg, 0.0, 1.5).pack(fill="x", pady=2)
        box = ttk.LabelFrame(b, text="Đầu ra", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        ttk.Checkbutton(box, text="Xuất kèm file phụ đề .srt", variable=self.export_srt).pack(anchor="w")
        ttk.Checkbutton(box, text="Đốt phụ đề vào video (chậm hơn, phải encode lại hình)", variable=self.burn).pack(anchor="w")

        c = ttk.Frame(nb, padding=10)
        nb.add(c, text="Dịch (Gemini)")
        box = ttk.LabelFrame(c, text="Chế độ dịch", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        pick = row(box, "Mức độ việc", lambda p: ttk.Combobox(
            p, textvariable=self.profile, values=list(TASK_PROFILES), state="readonly"), 13)
        pick.bind("<<ComboboxSelected>>", self._apply_profile)

        box = ttk.LabelFrame(c, text="Model", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        r = ttk.Frame(box)
        r.pack(fill="x")
        self.model_box = ttk.Combobox(r, textvariable=self.model, values=DEFAULT_GEMINI_MODELS)
        self.model_box.pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Làm mới", style="Neutral.TButton", command=self._refresh_models).pack(side="left", padx=4)
        ttk.Label(box, text="Tên model Gemini đổi thường xuyên. Bấm 'Làm mới' để lấy danh sách hiện có từ API.",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(4, 0))
        box = ttk.LabelFrame(c, text="Ngữ cảnh giúp dịch hay hơn", style="Card.TLabelframe", padding=8)
        box.pack(fill="x", pady=4)
        row(box, "Chủ đề video", lambda p: ttk.Entry(p, textvariable=self.topic), 18)
        preset = row(box, "Phong cách mẫu", lambda p: ttk.Combobox(
            p, textvariable=self.style_preset, values=list(STYLE_PRESETS), state="readonly"), 18)
        preset.bind("<<ComboboxSelected>>",
                    lambda e: self.style.set(STYLE_PRESETS.get(self.style_preset.get(), "")))
        row(box, "Mô tả phong cách", lambda p: ttk.Entry(p, textvariable=self.style), 18)
        row(box, "Xưng hô", lambda p: ttk.Entry(p, textvariable=self.pronouns), 18)
        row(box, "Thuật ngữ", lambda p: ttk.Entry(p, textvariable=self.glossary), 18)
        ttk.Label(box, text="Xưng hô ví dụ: Narrator xưng 'mình', gọi khán giả là 'các bạn'; "
                            "Tom gọi Jerry là 'mày', Jerry xưng 'tao'.",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(4, 0))

    def _build_right(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 6))
        self.btn_prepare = ttk.Button(bar, text="① Tạo kịch bản", style="Primary.TButton", command=self.start_prepare)
        self.btn_render = ttk.Button(bar, text="② Lồng tiếng & xuất video", style="Success.TButton", command=self.start_render)
        self.btn_auto = ttk.Button(bar, text="⚡ Chạy tự động", style="Neutral.TButton", command=self.start_auto)
        self.btn_cancel = ttk.Button(bar, text="⛔ Hủy", style="Danger.TButton", command=self._cancel, state="disabled")
        for b in (self.btn_prepare, self.btn_render, self.btn_auto, self.btn_cancel):
            b.pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="💾 Lưu", width=8, style="Neutral.TButton", command=self.save_script).pack(side="right")
        ttk.Button(bar, text="📂 Mở...", width=10, style="Neutral.TButton", command=self.load_script).pack(side="right", padx=6)

        ttk.Label(parent, text="Kịch bản lồng tiếng (sửa trực tiếp được). Định dạng: [00:00:01.200 -> 00:00:03.400] Nội dung",
                  style="Muted.TLabel").pack(anchor="w")
        self.editor = ScrolledText(parent, height=16, bg=COLORS["input"], fg=COLORS["text"], font=MONO,
                                   insertbackground=COLORS["text"], relief="flat", wrap="word", undo=True)
        self.editor.pack(fill="both", expand=True, pady=4)

        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(4, 2))
        self.status = ttk.Label(parent, text="Sẵn sàng", style="Muted.TLabel")
        self.status.pack(anchor="w")
        self.log = LogBox(parent, height=7)
        self.log.pack(fill="x", pady=(4, 0))
        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ----------------------------------------------------------------- cài đặt
    def collect(self) -> DubSettings:
        v = self.voice.get().strip()
        s = dataclasses.replace(
            self.app.settings,
            source_lang=code_from_choice(self.src_lang.get()), target_lang=code_from_choice(self.tgt_lang.get()),
            whisper_model=self.whisper.get(), tts_engine=_key(ENGINES, self.engine.get()), edge_voice=v,
            elevenlabs_voice_id=self.eleven_voice.get().strip(), max_speed=round(self.max_speed.get(), 2),
            mix_mode=_key(MIXES, self.mix.get()), original_volume=round(self.vol_orig.get(), 2),
            ai_volume=round(self.vol_ai.get(), 2), bg_volume=round(self.vol_bg.get(), 2),
            burn_captions=self.burn.get(), export_srt=self.export_srt.get(),
            gemini_model=self.model.get().strip() or DEFAULT_GEMINI_MODELS[0],
            task_profile=self.profile.get(),
            topic=self.topic.get().strip(),
            style_preset=self.style_preset.get(),
            style=self.style.get().strip() or STYLE_PRESETS.get(self.style_preset.get(), ""),
            pronoun_rules=self.pronouns.get().strip(),
            glossary=self.glossary.get().strip(),
        )
        self.app.settings = s
        return s

    def _on_target_change(self, *_, keep_current: bool = False):
        lang = get_language(code_from_choice(self.tgt_lang.get()))
        self.voice_box.config(values=list(lang.edge_voices))
        if not keep_current or not self.voice.get():
            self.voice.set(lang.edge_voice)

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

    # ------------------------------------------------------------------ hành động
    def _browse_source(self):
        p = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.webm"), ("Tất cả", "*.*")])
        if p:
            self.source.set(p)

    def _cancel(self):
        self.reporter.cancel()
        self.log.write("⏹ Đang dừng...", "warn")

    def _source(self) -> str:
        src = self.source.get().strip().strip('"')
        if not src:
            messagebox.showwarning("Thiếu video", "Hãy dán link hoặc chọn file video ở tab 'Nguồn & ngôn ngữ'.")
        return src

    def _warn_stems(self, s: DubSettings):
        if s.mix_mode == "stems" and not demucs_available():
            self.log.write("ℹ️ Chưa cài Demucs → sẽ tự dùng chế độ 'duck'. (pip install demucs để tách nhạc nền)", "warn")

    def start_prepare(self):
        src = self._source()
        if not src:
            return
        s = self.collect()
        s.save()
        self.log.clear()
        self._pipeline = DubbingPipeline(s, self.app.keys(), self.reporter)

        def job():
            try:
                return self._pipeline.prepare_script(src)
            finally:
                self._pipeline.cleanup()

        def done(result):
            video, segs = result
            self.video_path = Path(video)
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", sub.format_editor(segs))
            self.status.config(text=f"Đã có {len(segs)} câu. Kiểm tra/sửa kịch bản rồi bấm ② Lồng tiếng.")
            self.log.write(f"✓ Kịch bản gồm {len(segs)} câu. Hãy đọc lại trước khi lồng tiếng.", "ok")

        self.app.run_job(job, buttons=(self.btn_prepare, self.btn_render, self.btn_auto), cancel_button=self.btn_cancel,
                         reporter=self.reporter, log=self.log, on_done=done)

    def _ask_output(self, src: str) -> str:
        stem = Path(self.video_path).stem if self.video_path else (Path(src).stem if not is_url(src) else "dubbed")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        return filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")],
                                            initialdir=str(OUTPUT_DIR), initialfile=f"{stem}_dub.mp4")

    def start_render(self):
        src = self._source()
        if not src:
            return
        segs = sub.parse_script(self.editor.get("1.0", "end"))
        if not segs:
            messagebox.showwarning("Kịch bản trống", "Hãy bấm ① Tạo kịch bản, hoặc mở/dán kịch bản vào ô bên phải.")
            return
        out = self._ask_output(src)
        if not out:
            return
        s = self.collect()
        s.save()
        self._warn_stems(s)
        self.log.clear()
        pipe = DubbingPipeline(s, self.app.keys(), self.reporter)
        known_video = self.video_path

        def job():
            try:
                video = known_video or pipe.acquire_video(src, self.reporter.scope(0, 0))
                return pipe.render(video, segs, out)
            finally:
                pipe.cleanup()

        self.app.run_job(job, buttons=(self.btn_prepare, self.btn_render, self.btn_auto), cancel_button=self.btn_cancel,
                         reporter=self.reporter, log=self.log, on_done=self._finished)

    def start_auto(self):
        src = self._source()
        if not src:
            return
        out = self._ask_output(src)
        if not out:
            return
        s = self.collect()
        s.save()
        self._warn_stems(s)
        self.log.clear()
        pipe = DubbingPipeline(s, self.app.keys(), self.reporter)
        self.app.run_job(lambda: pipe.run_all(src, out), buttons=(self.btn_prepare, self.btn_render, self.btn_auto),
                         cancel_button=self.btn_cancel, reporter=self.reporter, log=self.log, on_done=self._finished)

    def _finished(self, out):
        self.status.config(text=f"Hoàn tất: {out}")
        self.log.write(f"🎉 HOÀN TẤT: {out}", "ok")
        messagebox.showinfo("Thành công", f"Video đã lồng tiếng xong!\n\n{out}")
        self.app.open_path(Path(out).parent)

    def load_script(self):
        p = filedialog.askopenfilename(filetypes=[("Kịch bản", "*.txt *.srt"), ("Tất cả", "*.*")])
        if not p:
            return
        try:
            text = Path(p).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        segs = sub.parse_script(text)
        if not segs:
            messagebox.showwarning("Sai định dạng", "Không đọc được câu thoại nào trong file này.")
            return
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", sub.format_editor(segs))
        self.log.write(f"✓ Đã nạp {len(segs)} câu từ {Path(p).name}", "ok")

    def save_script(self):
        segs = sub.parse_script(self.editor.get("1.0", "end"))
        if not segs:
            return
        p = filedialog.asksaveasfilename(defaultextension=".srt", filetypes=[("SRT", "*.srt"), ("Text", "*.txt")])
        if p:
            Path(p).write_text(sub.format_srt(segs) if p.lower().endswith(".srt") else sub.format_editor(segs),
                               encoding="utf-8")
            self.log.write(f"💾 Đã lưu: {p}", "ok")
