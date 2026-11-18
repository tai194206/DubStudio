"""Tab Story Studio: Dựng phim ngắn 9:16 tự động từ kịch bản phân cảnh Storyboard (Phase 3)."""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from core.config import OUTPUT_DIR, ROOT_DIR
from core.errors import Cancelled, DubberError
from core.languages import get_language
from core.story_pipeline import StoryPipeline, validate_story_assets
from core.story_schema import load_storyboard
from core.tts import EdgeTTS, ElevenLabsTTS, ToneTTS

from .theme import COLORS
from .widgets import FileRow, GradientProgressBar, LabeledScale, LogBox, row

ENGINES = {
    "edge": "Edge-TTS (miễn phí)",
    "elevenlabs": "ElevenLabs (trả phí, giọng đẹp)",
    "tone": "Giọng thử (tiếng bíp, để test đồng bộ)",
}

CODECS = {
    "H.264": "h264",
    "H.265": "h265",
}


class StoryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=10)
        self.app = app
        s = app.settings

        self.storyboard_path = tk.StringVar()
        self.assets_dir = tk.StringVar()
        self.bgm_path = tk.StringVar()

        self.engine_choice = tk.StringVar(value=ENGINES.get(s.tts_engine, list(ENGINES.values())[0]))
        self.edge_voice = tk.StringVar(value=s.edge_voice or "vi-VN-HoaiMyNeural")
        self.eleven_voice = tk.StringVar(value=s.elevenlabs_voice_id)
        self.speed = tk.DoubleVar(value=1.0)
        self.burn_subs = tk.BooleanVar(value=True)
        self.codec_choice = tk.StringVar(value="H.264")

        self.last_output: Path | None = None

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=480)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

    # ------------------------------------------------------------- Bố cục bên trái: Cấu hình
    def _build_left(self, parent):
        # 1. Đầu vào Storyboard & Tài nguyên
        box_input = ttk.LabelFrame(parent, text="1. Kịch bản Storyboard & Thư mục cảnh", style="Card.TLabelframe", padding=8)
        box_input.pack(fill="x", pady=4)

        FileRow(
            box_input,
            "File Storyboard",
            self.storyboard_path,
            mode="open",
            filetypes=[("Storyboard JSON", "*.json"), ("Tất cả", "*.*")],
            label_width=16,
        ).pack(fill="x", pady=2)

        FileRow(
            box_input,
            "Thư mục cảnh",
            self.assets_dir,
            mode="dir",
            label_width=16,
        ).pack(fill="x", pady=2)

        FileRow(
            box_input,
            "Nhạc nền (BGM)",
            self.bgm_path,
            mode="open",
            filetypes=[("Audio files", "*.mp3 *.wav *.ogg *.m4a *.aac"), ("Tất cả", "*.*")],
            label_width=16,
        ).pack(fill="x", pady=2)

        ttk.Label(
            box_input,
            text="DubStudio tự động tìm clip (001.mp4) hoặc ảnh (001.png) theo số thứ tự cảnh.",
            style="Muted.TLabel",
            wraplength=440,
        ).pack(anchor="w", pady=(4, 0))

        # 2. Giọng đọc & Tùy chọn 9:16
        box_opts = ttk.LabelFrame(parent, text="2. Giọng đọc & Tùy chọn 9:16", style="Card.TLabelframe", padding=8)
        box_opts.pack(fill="x", pady=4)

        row(
            box_opts,
            "Engine TTS",
            lambda p: ttk.Combobox(p, textvariable=self.engine_choice, values=list(ENGINES.values()), state="readonly"),
            label_width=16,
        )

        lang = get_language(self.app.settings.target_lang)
        edge_voices = lang.edge_voices or [self.edge_voice.get()]
        row(
            box_opts,
            "Giọng Edge",
            lambda p: ttk.Combobox(p, textvariable=self.edge_voice, values=edge_voices),
            label_width=16,
        )

        row(
            box_opts,
            "ElevenLabs Voice ID",
            lambda p: ttk.Entry(p, textvariable=self.eleven_voice),
            label_width=16,
        )

        LabeledScale(box_opts, "Tốc độ đọc", self.speed, 0.8, 1.5, "{:.2f}x").pack(fill="x", pady=2)

        row(
            box_opts,
            "Định dạng nén",
            lambda p: ttk.Combobox(p, textvariable=self.codec_choice, values=list(CODECS.keys()), state="readonly"),
            label_width=16,
        )

        ttk.Checkbutton(
            box_opts,
            text="Burn phụ đề Karaoke 9:16 (vùng an toàn TikTok)",
            variable=self.burn_subs,
        ).pack(anchor="w", pady=4)

        # 3. Nút hành động
        bar_act = ttk.Frame(parent)
        bar_act.pack(fill="x", pady=8)

        self.btn_check = ttk.Button(bar_act, text="🔍 Kiểm tra cảnh", style="Neutral.TButton", command=self.check_scenes)
        self.btn_check.pack(side="left", padx=(0, 4))

        self.btn_render = ttk.Button(bar_act, text="🎬 Xuất video 9:16", style="Primary.TButton", command=self.start_render)
        self.btn_render.pack(side="left", padx=4)

        self.btn_cancel = ttk.Button(bar_act, text="⛔ Hủy", style="Danger.TButton", state="disabled", command=self._cancel)
        self.btn_cancel.pack(side="left", padx=4)

        self.btn_open = ttk.Button(bar_act, text="📁 Mở video", style="Neutral.TButton", state="disabled", command=self._open_output)
        self.btn_open.pack(side="right")

    # ------------------------------------------------------------- Bố cục bên phải: Tiến độ & Log
    def _build_right(self, parent):
        ttk.Label(parent, text="Nhật ký dựng phim Story Mode", style="Muted.TLabel").pack(anchor="w", pady=(0, 4))

        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(2, 4))

        self.status = ttk.Label(parent, text="Sẵn sàng. Chọn Storyboard rồi bấm 'Kiểm tra cảnh' hoặc 'Xuất video'.", style="Muted.TLabel")
        self.status.pack(anchor="w", pady=(0, 4))

        self.log = LogBox(parent, height=18)
        self.log.pack(fill="both", expand=True)

        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ------------------------------------------------------------- Logic thao tác
    def _cancel(self):
        self.reporter.cancel()

    def _open_output(self):
        if self.last_output and self.last_output.is_file():
            if sys.platform == "win32":
                os.startfile(str(self.last_output))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.last_output)])
            else:
                subprocess.Popen(["xdg-open", str(self.last_output)])

    def _get_engine(self) -> TTSEngine:
        engine_key = next((k for k, v in ENGINES.items() if v == self.engine_choice.get()), "edge")
        if engine_key == "tone":
            return ToneTTS()
        if engine_key == "elevenlabs":
            return ElevenLabsTTS(
                self.app.keys().elevenlabs,
                self.eleven_voice.get().strip() or self.app.settings.elevenlabs_voice_id,
                self.app.settings.elevenlabs_model,
            )
        # Edge-TTS
        spd = self.speed.get()
        pct = int(round((spd - 1.0) * 100))
        rate_str = f"{pct:+d}%"
        raw_voice = self.edge_voice.get().strip() or "vi-VN-HoaiMyNeural"
        return EdgeTTS(raw_voice, rate=rate_str)

    def check_scenes(self):
        """Kiểm tra sự đầy đủ tài nguyên cho từng cảnh."""
        sb_path = self.storyboard_path.get().strip()
        assets_p = self.assets_dir.get().strip()

        if not sb_path:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng chọn file storyboard.json trước.")
            return
        if not assets_p:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng chọn thư mục chứa tài nguyên ảnh/clip.")
            return

        self.log.clear()
        self.log.write(f"🔍 Đang kiểm tra storyboard: {sb_path}")

        try:
            sb = load_storyboard(sb_path)
            self.log.write(f"✓ Storyboard: '{sb.title}' ({len(sb.scenes)} cảnh, tỉ lệ {sb.aspect_ratio})", "ok")

            found, missing = validate_story_assets(sb, assets_p)
            for sc in sb.scenes:
                asset = found.get(sc.id)
                if asset:
                    kind = "Clip [VIDEO]" if asset.suffix.lower() in (".mp4", ".mov", ".mkv") else "Ảnh [IMAGE]"
                    self.log.write(f"  • Cảnh {sc.id:02d}: {kind} {asset.name} (motion: {sc.motion}, sfx: {sc.sfx})")
                else:
                    self.log.write(f"  ❌ Cảnh {sc.id:02d}: THIẾU TÀI NGUYÊN (cần {sc.id:03d}.mp4 hoặc {sc.id:03d}.png)!", "error")

            if missing:
                self.status.config(text=f"⚠️ Thiếu tài nguyên cho {len(missing)} cảnh: {missing}")
                self.log.write(f"⚠️ Phát hiện {len(missing)} cảnh thiếu ảnh/clip!", "warn")
            else:
                self.status.config(text=f"✓ Đầy đủ tài nguyên cho tất cả {len(sb.scenes)} cảnh.")
                self.log.write("🎉 Tất cả các cảnh đều đã có đầy đủ tài nguyên. Sẵn sàng xuất video 9:16!", "ok")
        except Exception as e:
            self.log.write(f"❌ Lỗi khi kiểm tra: {e}", "error")
            messagebox.showerror("Lỗi kiểm tra", str(e))

    def start_render(self):
        """Bắt đầu quy trình dựng phim ở luồng phụ."""
        sb_path = self.storyboard_path.get().strip()
        assets_p = self.assets_dir.get().strip()
        bgm_p = self.bgm_path.get().strip() or None

        if not sb_path or not Path(sb_path).is_file():
            messagebox.showerror("Lỗi", "File storyboard.json không tồn tại!")
            return
        if not assets_p or not Path(assets_p).is_dir():
            messagebox.showerror("Lỗi", "Thư mục tài nguyên cảnh không tồn tại!")
            return

        try:
            engine = self._get_engine()
        except Exception as e:
            messagebox.showerror("Lỗi cấu hình giọng", str(e))
            return

        codec_code = CODECS.get(self.codec_choice.get(), "h264")
        burn_sub = self.burn_subs.get()

        self.btn_open.config(state="disabled")

        def work():
            pipeline = StoryPipeline(
                settings=self.app.settings,
                keys=self.app.keys(),
                reporter=self.reporter,
                tts_engine=engine,
            )
            out_file = pipeline.render_story(
                storyboard_path_or_obj=sb_path,
                assets_dir=assets_p,
                bgm_path=bgm_p,
                burn_subtitles=burn_sub,
                codec=codec_code,
            )
            self.last_output = out_file
            self.app.post(self.btn_open.config, {"state": "normal"})
            return out_file

        self.app.run_job(
            work,
            buttons=(self.btn_check, self.btn_render),
            cancel_button=self.btn_cancel,
            reporter=self.reporter,
            log=self.log,
        )
