"""Tab Story Studio 9:16: Dựng phim lịch sử tự động (v3.2 - Xfade, Pop 1-2 chữ, Watermark & Anti-AI)."""
from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, ttk

from core.anti_ai_filter import ANTI_AI_LABELS, process_standalone_video
from core.config import OUTPUT_DIR
from core.frame_presets import FRAME_PRESET_GUI_LABELS, resolve_frame_preset
from core.languages import get_language
from core.story_pipeline import StoryPipeline, validate_story_assets
from core.story_schema import load_storyboard
from core.tts import EdgeTTS, ElevenLabsTTS, ToneTTS, TTSEngine

from .theme import COLORS, FONT_BOLD, FONT_SM
from .widgets import (
    FileRow,
    GradientProgressBar,
    LabeledScale,
    LogBox,
    ScrollableFrame,
    StatusBadge,
    row,
)

ENGINES = {
    "edge": "Edge-TTS (miễn phí)",
    "elevenlabs": "ElevenLabs (giọng tự nhiên, API)",
    "tone": "Giọng thử (offline test đồng bộ)",
}

SHOT_MODES = {
    "planned": "Mạch liên tục v3 (Planned - khuyên dùng)",
    "scene": "Từng cảnh độc lập v2 (Scene)",
}

FRAME_PRESET_CHOICES = dict(FRAME_PRESET_GUI_LABELS)

TRANSITIONS = {
    "fade": "Mờ chéo mượt (fade)",
    "dissolve": "Hòa tan điện ảnh (dissolve)",
    "smoothleft": "Lướt trái mượt (smoothleft)",
    "wipeleft": "Gạt cảnh sang trái (wipeleft)",
    "none": "Cắt cứng trực tiếp (none)",
}

SUBTITLE_STYLES = {
    "highlight_3words": "Cụm 3 chữ sáng từng từ (CapCut)",
    "pop_2words": "Nảy từng chữ (1 chữ nổi bật)",
    "karaoke_line": "Karaoke tô màu cả câu",
}

WATERMARK_POS = {
    "top_left": "Góc trên bên trái (khuyên dùng)",
    "top_right": "Góc trên bên phải",
    "bottom_left": "Góc dưới bên trái (né caption)",
}

CODECS = {
    "H.264 (tương thích mọi thiết bị)": "h264",
    "H.265 (nhẹ dung lượng hơn)": "h265",
}

ANTI_AI_LEVELS = dict(ANTI_AI_LABELS)
ANTI_AI_STANDALONE_LEVELS = {k: v for k, v in ANTI_AI_LABELS.items() if k != "off"}
ANTI_AI_SHORT = {
    "light": "Nhẹ",
    "medium": "Vừa",
    "strong": "Mạnh",
}

FONT_CHOICES = ["Arial", "Segoe UI", "Tahoma", "Montserrat", "Roboto", "Impact", "Verdana"]


def _label(mapping: dict, key: str) -> str:
    return mapping.get(key, next(iter(mapping.values())))


def _key(mapping: dict, label: str) -> str:
    return next((k for k, v in mapping.items() if v == label), next(iter(mapping)))


class StoryTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        s = app.settings

        # 1. Đường dẫn đầu vào
        self.storyboard_path = tk.StringVar()
        self.assets_dir = tk.StringVar()
        self.bgm_path = tk.StringVar()
        self.bgm_volume = tk.DoubleVar(value=float(getattr(s, "story_bgm_volume", 0.28)))
        self.sfx_volume = tk.DoubleVar(value=float(getattr(s, "story_sfx_volume", 0.85)))

        # 2. Giọng đọc
        self.engine_choice = tk.StringVar(value=_label(ENGINES, s.tts_engine))
        self.edge_voice = tk.StringVar(value=s.edge_voice or "vi-VN-NamMinhNeural")
        self.eleven_voice = tk.StringVar(value=s.elevenlabs_voice_id)
        self.speed = tk.DoubleVar(value=float(getattr(s, "story_speed", 1.0)))

        # 3. Phụ đề & Kênh
        self.burn_subs = tk.BooleanVar(value=bool(getattr(s, "story_burn_subs", True)))
        self.sub_style_choice = tk.StringVar(
            value=_label(SUBTITLE_STYLES, getattr(s, "story_subtitle_style", "pop_2words"))
        )
        self.sub_font = tk.StringVar(value=getattr(s, "story_sub_font", "Arial"))
        self.sub_color = tk.StringVar(value=getattr(s, "story_sub_color", "#FFE000"))
        self.sub_font_size = tk.DoubleVar(value=float(getattr(s, "story_sub_font_size", 72)))
        self.sub_margin_v = tk.DoubleVar(value=float(getattr(s, "story_sub_margin_v", 390)))
        self.show_scene_titles = tk.BooleanVar(value=bool(getattr(s, "story_show_scene_titles", True)))

        self.wm_enabled = tk.BooleanVar(value=bool(getattr(s, "story_watermark_enabled", True)))
        self.wm_text = tk.StringVar(value=getattr(s, "story_watermark_text", "Dòng Máu Sử Việt"))
        self.wm_pos_choice = tk.StringVar(
            value=_label(WATERMARK_POS, getattr(s, "story_watermark_pos", "top_left"))
        )
        self.wm_opacity = tk.DoubleVar(value=float(getattr(s, "story_watermark_opacity", 65)))

        # 4. Dựng & Hậu kỳ
        self.frame_preset_choice = tk.StringVar(
            value=_label(FRAME_PRESET_CHOICES, getattr(s, "story_frame_preset", "auto"))
        )
        self.shot_mode_choice = tk.StringVar(
            value=_label(SHOT_MODES, getattr(s, "story_shot_mode", "planned"))
        )
        self.transition_choice = tk.StringVar(
            value=_label(TRANSITIONS, getattr(s, "story_transition", "fade"))
        )
        self.transition_dur = tk.DoubleVar(value=float(getattr(s, "story_transition_duration", 0.25)))
        self.smart_clip_fit = tk.BooleanVar(value=bool(getattr(s, "story_smart_clip_fit", True)))
        self.cinematic_grade = tk.BooleanVar(value=bool(getattr(s, "story_cinematic_grade", True)))
        self.loudness_norm = tk.BooleanVar(value=bool(getattr(s, "story_loudness_norm", True)))
        raw_aai_lv = getattr(s, "story_anti_ai_level", None)
        if raw_aai_lv not in ANTI_AI_LEVELS:
            raw_aai_lv = "medium" if bool(getattr(s, "story_anti_ai", True)) else "off"
        self.anti_ai_level_choice = tk.StringVar(value=_label(ANTI_AI_LEVELS, raw_aai_lv))
        self.anti_ai_grain = tk.BooleanVar(value=bool(getattr(s, "story_anti_ai_grain", True)))
        codec_val = getattr(s, "story_codec", "h264")
        self.codec_choice = tk.StringVar(
            value=next((lbl for lbl, code in CODECS.items() if code == codec_val), next(iter(CODECS)))
        )

        # 5. Tab 4: Né quét AI độc lập cho file video có sẵn
        self.aai_input_path = tk.StringVar()
        self.aai_output_path = tk.StringVar()
        self.aai_standalone_level = tk.StringVar(value=_label(ANTI_AI_STANDALONE_LEVELS, "medium"))
        self.aai_standalone_grain = tk.BooleanVar(value=True)
        self.aai_standalone_speed = tk.DoubleVar(value=1.0)
        self.last_aai_output: Path | None = None

        self.last_output: Path | None = None

        self.storyboard_path.trace_add("write", self._on_storyboard_changed)
        self.aai_input_path.trace_add("write", self._on_aai_input_changed)

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=520)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

        for var in (
            self.engine_choice,
            self.edge_voice,
            self.eleven_voice,
            self.frame_preset_choice,
            self.shot_mode_choice,
            self.transition_choice,
            self.sub_style_choice,
            self.burn_subs,
            self.sub_color,
            self.show_scene_titles,
            self.wm_enabled,
            self.wm_text,
            self.wm_pos_choice,
            self.cinematic_grade,
            self.loudness_norm,
            self.anti_ai_level_choice,
            self.anti_ai_grain,
            self.smart_clip_fit,
        ):
            var.trace_add("write", lambda *_: self._refresh_ui_states())

        self._refresh_ui_states()

    # ------------------------------------------------------------- Bố cục cột trái
    def _build_left(self, parent):
        nb = ttk.Notebook(parent, style="Sub.TNotebook")
        nb.pack(fill="both", expand=True)

        # ---- Tab con 1: Dự án & Giọng đọc
        sc1 = ScrollableFrame(nb, padding=6)
        nb.add(sc1, text="  📂 1. Dự án & Giọng  ")
        t1 = sc1.body

        box_in = ttk.LabelFrame(t1, text="Kịch bản Storyboard & Tài nguyên cảnh", style="Card.TLabelframe", padding=10)
        box_in.pack(fill="x", pady=(0, 8))

        FileRow(
            box_in,
            "File Storyboard",
            self.storyboard_path,
            mode="open",
            filetypes=[("Storyboard JSON", "*.json"), ("Tất cả", "*.*")],
            label_width=15,
        ).pack(fill="x", pady=3)

        FileRow(box_in, "Thư mục cảnh", self.assets_dir, mode="dir", label_width=15).pack(fill="x", pady=3)

        FileRow(
            box_in,
            "Nhạc nền (BGM)",
            self.bgm_path,
            mode="open",
            filetypes=[
                ("Âm thanh / Video có nhạc", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.mp4 *.mov *.mkv *.webm"),
                ("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"),
                ("Video (rút nhạc nền)", "*.mp4 *.mov *.mkv *.webm"),
                ("Tất cả", "*.*"),
            ],
            label_width=15,
        ).pack(fill="x", pady=3)

        LabeledScale(box_in, "Âm lượng BGM", self.bgm_volume, 0.05, 0.60, "{:.0%}", label_width=15).pack(
            fill="x", pady=(5, 2)
        )
        LabeledScale(box_in, "Âm lượng SFX", self.sfx_volume, 0.0, 1.50, "{:.0%}", label_width=15).pack(
            fill="x", pady=(3, 2)
        )
        ttk.Label(
            box_in,
            text="💡 Chọn storyboard.json xong app tự điền assets/ bên cạnh (001.mp4 trước 001.png). Kéo Âm lượng SFX về 0% để tắt hiệu ứng âm thanh (mặc định 85%).",
            style="CardMuted.TLabel",
            wraplength=445,
        ).pack(anchor="w", pady=(4, 0))

        box_tts = ttk.LabelFrame(t1, text="Giọng đọc (TTS)", style="Card.TLabelframe", padding=10)
        box_tts.pack(fill="x", pady=(0, 6))

        row(
            box_tts,
            "Engine TTS",
            lambda p: ttk.Combobox(p, textvariable=self.engine_choice, values=list(ENGINES.values()), state="readonly"),
            label_width=15,
        )

        lang = get_language(self.app.settings.target_lang)
        edge_voices = list(lang.edge_voices or [self.edge_voice.get()])
        if "vi-VN-NamMinhNeural" not in edge_voices:
            edge_voices.insert(0, "vi-VN-NamMinhNeural")

        self.cb_edge_voice = row(
            box_tts,
            "Giọng Edge",
            lambda p: ttk.Combobox(p, textvariable=self.edge_voice, values=edge_voices),
            label_width=15,
        )

        self.ent_eleven = row(
            box_tts,
            "ElevenLabs ID",
            lambda p: ttk.Entry(p, textvariable=self.eleven_voice),
            label_width=15,
        )

        self.lbl_tts_hint = ttk.Label(box_tts, text="", style="CardMuted.TLabel", wraplength=445)
        self.lbl_tts_hint.pack(anchor="w", pady=(3, 5))

        LabeledScale(box_tts, "Tốc độ đầu ra", self.speed, 0.80, 1.40, "{:.2f}x", label_width=15).pack(fill="x", pady=2)
        ttk.Label(
            box_tts,
            text="⚡ Đồng bộ tốc độ Video + Giọng đọc + Phụ đề (áp dụng cả full_audio.mp3). Nhạc nền (BGM) luôn giữ nguyên 1.00x và tự cắt khít cuối video.",
            style="CardMuted.TLabel",
            wraplength=445,
        ).pack(anchor="w", pady=(2, 0))

        # ---- Tab con 2: Phụ đề & Thương hiệu kênh
        sc2 = ScrollableFrame(nb, padding=6)
        nb.add(sc2, text="  💬 2. Phụ đề & Kênh  ")
        t2 = sc2.body

        box_sub = ttk.LabelFrame(t2, text="Phụ đề 9:16 (Vùng an toàn TikTok)", style="Card.TLabelframe", padding=10)
        box_sub.pack(fill="x", pady=(0, 8))

        ttk.Checkbutton(
            box_sub,
            text="Burn phụ đề nổi bật vùng dưới video",
            variable=self.burn_subs,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 5))

        self.cb_sub_style = row(
            box_sub,
            "Kiểu hiển thị",
            lambda p: ttk.Combobox(
                p, textvariable=self.sub_style_choice, values=list(SUBTITLE_STYLES.values()), state="readonly"
            ),
            label_width=15,
        )

        self.cb_sub_font = row(
            box_sub,
            "Font chữ",
            lambda p: ttk.Combobox(p, textvariable=self.sub_font, values=FONT_CHOICES),
            label_width=15,
        )

        # Dòng chọn màu chữ trực quan + bảng màu
        f_col = ttk.Frame(box_sub, style="Surface.TFrame")
        f_col.pack(fill="x", pady=3)
        ttk.Label(f_col, text="Màu chữ chính", width=15, style="Card.TLabel").pack(side="left")
        self.lbl_color_swatch = tk.Label(
            f_col,
            width=3,
            bg="#FFE000",
            relief="solid",
            bd=1,
            cursor="hand2",
        )
        self.lbl_color_swatch.pack(side="left", padx=(0, 6), ipady=2)
        self.lbl_color_swatch.bind("<Button-1>", lambda _e: self._pick_sub_color())
        self.ent_sub_color = ttk.Entry(f_col, textvariable=self.sub_color, width=12)
        self.ent_sub_color.pack(side="left", padx=(0, 6))
        self.btn_pick_color = ttk.Button(
            f_col, text="Chọn màu…", style="Compact.TButton", command=self._pick_sub_color
        )
        self.btn_pick_color.pack(side="left")

        LabeledScale(box_sub, "Cỡ chữ phụ đề", self.sub_font_size, 40, 96, "{:.0f}px", label_width=15).pack(
            fill="x", pady=3
        )
        LabeledScale(box_sub, "Độ cao từ đáy", self.sub_margin_v, 200, 650, "{:.0f}px", label_width=15).pack(
            fill="x", pady=3
        )

        ttk.Checkbutton(
            box_sub,
            text="Hiện tiêu đề cảnh phía trên (on_screen_text trong JSON)",
            variable=self.show_scene_titles,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(5, 0))

        box_wm = ttk.LabelFrame(t2, text="Thương hiệu kênh (Watermark chống copy)", style="Card.TLabelframe", padding=10)
        box_wm.pack(fill="x", pady=(0, 6))

        ttk.Checkbutton(
            box_wm,
            text="Bật hiển thị tên kênh trên video",
            variable=self.wm_enabled,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 5))

        self.ent_wm = row(
            box_wm,
            "Tên kênh TikTok",
            lambda p: ttk.Entry(p, textvariable=self.wm_text),
            label_width=15,
        )

        self.cb_wm_pos = row(
            box_wm,
            "Vị trí đặt chữ",
            lambda p: ttk.Combobox(
                p, textvariable=self.wm_pos_choice, values=list(WATERMARK_POS.values()), state="readonly"
            ),
            label_width=15,
        )

        LabeledScale(box_wm, "Độ rõ Watermark", self.wm_opacity, 20, 100, "{:.0f}%", label_width=15).pack(
            fill="x", pady=3
        )
        ttk.Label(
            box_wm,
            text="Mặc định góc trên bên trái giúp tránh bị tên tài khoản và caption của TikTok đè lên.",
            style="CardMuted.TLabel",
            wraplength=445,
        ).pack(anchor="w", pady=(3, 0))

        # ---- Tab con 3: Dựng & Hậu kỳ
        sc3 = ScrollableFrame(nb, padding=6)
        nb.add(sc3, text="  ✨ 3. Dựng & Hậu kỳ  ")
        t3 = sc3.body

        box_edit = ttk.LabelFrame(t3, text="Nhịp dựng & Chuyển cảnh (v3.2)", style="Card.TLabelframe", padding=10)
        box_edit.pack(fill="x", pady=(0, 8))

        self.cb_frame_preset = row(
            box_edit,
            "Khung hình",
            lambda p: ttk.Combobox(
                p, textvariable=self.frame_preset_choice, values=list(FRAME_PRESET_CHOICES.values()), state="readonly"
            ),
            label_width=16,
        )

        row(
            box_edit,
            "Chế độ dựng",
            lambda p: ttk.Combobox(
                p, textvariable=self.shot_mode_choice, values=list(SHOT_MODES.values()), state="readonly"
            ),
            label_width=16,
        )

        row(
            box_edit,
            "Chuyển cảnh xfade",
            lambda p: ttk.Combobox(
                p, textvariable=self.transition_choice, values=list(TRANSITIONS.values()), state="readonly"
            ),
            label_width=16,
        )

        LabeledScale(
            box_edit, "Thời lượng chuyển", self.transition_dur, 0.15, 0.50, "{:.2f}s", label_width=16
        ).pack(fill="x", pady=3)

        ttk.Checkbutton(
            box_edit,
            text="Smart Clip Fit (tự kéo giãn mượt clip 5s cho khít câu, không lặp giật)",
            variable=self.smart_clip_fit,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(5, 0))

        box_post = ttk.LabelFrame(t3, text="Hậu kỳ Điện ảnh & Tối ưu Xuất bản", style="Card.TLabelframe", padding=10)
        box_post.pack(fill="x", pady=(0, 6))

        ttk.Checkbutton(
            box_post,
            text="Phủ màu ấm điện ảnh + hạt phim mỏng + tối góc nhẹ (Cinematic)",
            variable=self.cinematic_grade,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=3)

        ttk.Checkbutton(
            box_post,
            text="Chuẩn hóa âm lượng chuẩn TikTok (-11 LUFS)",
            variable=self.loudness_norm,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=3)

        self.cb_anti_ai_level = row(
            box_post,
            "Né quét AI (TikTok)",
            lambda p: ttk.Combobox(
                p,
                textvariable=self.anti_ai_level_choice,
                values=list(ANTI_AI_LEVELS.values()),
                state="readonly",
            ),
            label_width=16,
            pady=4,
        )

        self.chk_anti_ai_grain = ttk.Checkbutton(
            box_post,
            text="Thêm hạt phim & tối góc nhẹ (Film Grain / Vignette)",
            variable=self.anti_ai_grain,
            style="Card.TCheckbutton",
        )
        self.chk_anti_ai_grain.pack(anchor="w", pady=(2, 4))

        row(
            box_post,
            "Định dạng nén",
            lambda p: ttk.Combobox(
                p, textvariable=self.codec_choice, values=list(CODECS.keys()), state="readonly"
            ),
            label_width=16,
            pady=6,
        )

        # ---- Tab con 4: Né quét AI độc lập cho file video có sẵn
        sc4 = ScrollableFrame(nb, padding=6)
        nb.add(sc4, text="  🛡️ 4. Né quét AI  ")
        t4 = sc4.body

        box_aai_io = ttk.LabelFrame(
            t4, text="Video đầu vào & Đường dẫn lưu (Xử lý độc lập)", style="Card.TLabelframe", padding=10
        )
        box_aai_io.pack(fill="x", pady=(0, 8))

        FileRow(
            box_aai_io,
            "Video đầu vào",
            self.aai_input_path,
            mode="open",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm"), ("Tất cả", "*.*")],
            label_width=14,
        ).pack(fill="x", pady=3)

        FileRow(
            box_aai_io,
            "File đầu ra",
            self.aai_output_path,
            mode="save",
            filetypes=[("MP4 Video", "*.mp4"), ("Tất cả", "*.*")],
            label_width=14,
        ).pack(fill="x", pady=3)

        ttk.Label(
            box_aai_io,
            text="💡 Chọn file video xong app tự điền tên đầu ra <tên_file>_clean.mp4 cùng thư mục.",
            style="CardMuted.TLabel",
            wraplength=445,
        ).pack(anchor="w", pady=(3, 0))

        box_aai_opt = ttk.LabelFrame(
            t4, text="Thiết lập Né quét AI (Xóa Metadata / C2PA + Phá lưới)", style="Card.TLabelframe", padding=10
        )
        box_aai_opt.pack(fill="x", pady=(0, 8))

        row(
            box_aai_opt,
            "Mức xử lý",
            lambda p: ttk.Combobox(
                p,
                textvariable=self.aai_standalone_level,
                values=list(ANTI_AI_STANDALONE_LEVELS.values()),
                state="readonly",
            ),
            label_width=14,
        )

        ttk.Checkbutton(
            box_aai_opt,
            text="Thêm hạt phim & tối góc nhẹ (Film Grain / Vignette)",
            variable=self.aai_standalone_grain,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(5, 4))

        LabeledScale(
            box_aai_opt, "Tốc độ video", self.aai_standalone_speed, 1.00, 1.20, "{:.2f}x", label_width=14
        ).pack(fill="x", pady=3)

        ttk.Label(
            box_aai_opt,
            text="ℹ️ Giữ 1.00x nếu video đã ghép sẵn nhạc nền (BGM). Xuất chuẩn 30fps, H.264 High, 0 góc đen.",
            style="CardMuted.TLabel",
            wraplength=445,
        ).pack(anchor="w", pady=(2, 6))

        f_aai_act = ttk.Frame(box_aai_opt, style="Surface.TFrame")
        f_aai_act.pack(fill="x", pady=(4, 0))

        self.btn_aai_run = ttk.Button(
            f_aai_act,
            text="🛡️ Xử lý Né quét AI",
            style="Primary.TButton",
            command=self.start_standalone_anti_ai,
        )
        self.btn_aai_run.pack(side="left", padx=(0, 6))

        self.btn_aai_open_dir = ttk.Button(
            f_aai_act,
            text="📂 Mở thư mục chứa file",
            style="Success.TButton",
            state="disabled",
            command=self._open_aai_folder,
        )
        self.btn_aai_open_dir.pack(side="left")

        # ---- Thanh nút hành động cố định dưới cùng bên trái
        bar_act = ttk.Frame(parent, padding=(2, 8, 2, 0))
        bar_act.pack(fill="x")

        self.btn_check = ttk.Button(
            bar_act, text="🔍 Kiểm tra cảnh", style="Neutral.TButton", command=self.check_scenes
        )
        self.btn_check.pack(side="left", padx=(0, 5))

        self.btn_render = ttk.Button(
            bar_act, text="🎬 Xuất video 9:16", style="Primary.TButton", command=self.start_render
        )
        self.btn_render.pack(side="left", padx=4)

        self.btn_cancel = ttk.Button(
            bar_act, text="⛔ Hủy", style="Danger.TButton", state="disabled", command=self._cancel
        )
        self.btn_cancel.pack(side="left", padx=4)

        self.btn_open = ttk.Button(
            bar_act, text="▶ Mở video", style="Success.TButton", state="disabled", command=self._open_output
        )
        self.btn_open.pack(side="right")

    # ------------------------------------------------------------- Bố cục cột phải
    def _build_right(self, parent):
        box_sum = ttk.LabelFrame(
            parent, text="Tổng quan cấu hình xuất (Kiểm tra nhanh)", style="Card.TLabelframe", padding=10
        )
        box_sum.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(box_sum, style="Surface.TFrame")
        row1.pack(fill="x", pady=(0, 5))
        self.bdg_frame = StatusBadge(row1, text="Khung hình", kind="info")
        self.bdg_frame.pack(side="left", padx=(0, 6))
        self.bdg_voice = StatusBadge(row1, text="Giọng", kind="info")
        self.bdg_voice.pack(side="left", padx=(0, 6))
        self.bdg_mode = StatusBadge(row1, text="Chế độ", kind="info")
        self.bdg_mode.pack(side="left", padx=(0, 6))
        self.bdg_trans = StatusBadge(row1, text="Chuyển cảnh", kind="muted")
        self.bdg_trans.pack(side="left")

        row2 = ttk.Frame(box_sum, style="Surface.TFrame")
        row2.pack(fill="x")
        self.bdg_sub = StatusBadge(row2, text="Phụ đề", kind="ok")
        self.bdg_sub.pack(side="left", padx=(0, 6))
        self.bdg_wm = StatusBadge(row2, text="Watermark", kind="ok")
        self.bdg_wm.pack(side="left", padx=(0, 6))
        self.bdg_post = StatusBadge(row2, text="Hậu kỳ", kind="ok")
        self.bdg_post.pack(side="left")

        hdr = ttk.Frame(parent)
        hdr.pack(fill="x", pady=(2, 4))
        ttk.Label(hdr, text="Tiến độ & Nhật ký dựng phim Story Mode", style="Muted.TLabel").pack(side="left")
        ttk.Button(
            hdr,
            text="💾 Lưu cấu hình",
            style="Compact.TButton",
            command=self._save_with_feedback,
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            hdr,
            text="📂 Mở output",
            style="Compact.TButton",
            command=lambda: self.app.open_path(OUTPUT_DIR),
        ).pack(side="right")

        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(2, 5))

        self.status = ttk.Label(
            parent,
            text="Sẵn sàng. Chọn file storyboard.json rồi bấm 'Kiểm tra cảnh' hoặc 'Xuất video 9:16'.",
            style="Muted.TLabel",
        )
        self.status.pack(anchor="w", pady=(0, 5))

        self.log = LogBox(parent, height=18)
        self.log.pack(fill="both", expand=True)

        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ------------------------------------------------------------- Tự động hóa UX & Lưu cài đặt
    def _pick_sub_color(self):
        current = self.sub_color.get().strip() or "#FFE000"
        try:
            _, hex_code = colorchooser.askcolor(color=current, title="Chọn màu chữ phụ đề chính")
        except Exception:
            _, hex_code = colorchooser.askcolor(title="Chọn màu chữ phụ đề chính")
        if hex_code:
            self.sub_color.set(hex_code.upper())

    def _on_storyboard_changed(self, *_):
        p = Path(self.storyboard_path.get().strip())
        if not p.is_file():
            return
        cand_assets = p.parent / "assets"
        if cand_assets.is_dir():
            self.assets_dir.set(str(cand_assets))
        # Nếu chưa chọn BGM, tự dò file bgm (audio hoặc mp4) cạnh storyboard hoặc trong assets
        if not self.bgm_path.get().strip():
            for cand in (
                p.parent / "bgm.mp3",
                p.parent / "bgm.wav",
                p.parent / "bgm.m4a",
                p.parent / "bgm.mp4",
                cand_assets / "bgm.mp3",
                cand_assets / "bgm.wav",
                cand_assets / "bgm.m4a",
                cand_assets / "bgm.mp4",
            ):
                if cand.is_file():
                    self.bgm_path.set(str(cand))
                    break

    def _refresh_ui_states(self):
        eng_key = _key(ENGINES, self.engine_choice.get())
        if eng_key == "elevenlabs":
            self.cb_edge_voice.config(state="disabled")
            self.ent_eleven.config(state="normal")
            has_key = bool(self.app.keys().elevenlabs)
            vid = self.eleven_voice.get().strip()
            if not has_key:
                self.lbl_tts_hint.config(
                    text="⚠️ Chưa có ELEVENLABS_API_KEY trong tab Cài đặt!",
                    foreground=COLORS["warn"],
                )
                self.bdg_voice.set_status("🎙️ ElevenLabs (thiếu Key)", "warn")
            elif not vid:
                self.lbl_tts_hint.config(
                    text="⚠️ Hãy dán ElevenLabs Voice ID (hoặc lấy trong tab Cài đặt).",
                    foreground=COLORS["warn"],
                )
                self.bdg_voice.set_status("🎙️ ElevenLabs (thiếu ID)", "warn")
            else:
                self.lbl_tts_hint.config(
                    text=f"✓ Sẵn sàng dùng giọng ElevenLabs (ID: {vid})",
                    foreground="#34d399",
                )
                self.bdg_voice.set_status(f"🎙️ ElevenLabs ({vid[:8]}…)", "ok")
        elif eng_key == "tone":
            self.cb_edge_voice.config(state="disabled")
            self.ent_eleven.config(state="disabled")
            self.lbl_tts_hint.config(
                text="ℹ️ Giọng thử offline tạo âm bíp giúp kiểm tra nhanh nhịp chuyển cảnh.",
                foreground=COLORS["muted"],
            )
            self.bdg_voice.set_status("🎙️ ToneTTS (Test)", "muted")
        else:
            self.cb_edge_voice.config(state="normal")
            self.ent_eleven.config(state="disabled")
            v_name = self.edge_voice.get().strip() or "vi-VN-NamMinhNeural"
            self.lbl_tts_hint.config(
                text="✓ Miễn phí. Khuyên chọn vi-VN-NamMinhNeural cho giọng lịch sử trầm ấm.",
                foreground="#34d399",
            )
            short_v = v_name.replace("vi-VN-", "").replace("Neural", "")
            self.bdg_voice.set_status(f"🎙️ Edge: {short_v}", "info")

        # Phụ đề & màu swatch
        sub_on = self.burn_subs.get()
        self.cb_sub_style.config(state="readonly" if sub_on else "disabled")
        self.cb_sub_font.config(state="normal" if sub_on else "disabled")
        self.ent_sub_color.config(state="normal" if sub_on else "disabled")
        self.btn_pick_color.config(state="normal" if sub_on else "disabled")

        raw_col = self.sub_color.get().strip()
        try:
            self.lbl_color_swatch.config(bg=raw_col if raw_col.startswith("#") and len(raw_col) == 7 else "#FFE000")
        except tk.TclError:
            self.lbl_color_swatch.config(bg="#FFE000")

        # Watermark
        wm_on = self.wm_enabled.get()
        self.ent_wm.config(state="normal" if wm_on else "disabled")
        self.cb_wm_pos.config(state="readonly" if wm_on else "disabled")

        # Cập nhật các badge tóm tắt ở cột phải
        fp_key = _key(FRAME_PRESET_CHOICES, self.frame_preset_choice.get())
        fp_badge_map = {
            "auto": "🖼️ Tự động",
            "portrait_9_16": "🖼️ 9:16",
            "framed_4_5": "🖼️ 4:5",
            "framed_1_1": "🖼️ 1:1",
            "landscape_16_9": "🖼️ 16:9",
        }
        self.bdg_frame.set_status(fp_badge_map.get(fp_key, "🖼️ Tự động"), "info")

        mode_key = _key(SHOT_MODES, self.shot_mode_choice.get())
        self.bdg_mode.set_status(
            "🎞️ v3 Planned" if mode_key == "planned" else "🎞️ v2 Scene",
            "info",
        )

        tr_key = _key(TRANSITIONS, self.transition_choice.get())
        self.bdg_trans.set_status(
            f"🔀 Xfade: {tr_key}" if tr_key != "none" else "✂️ Cắt cứng",
            "ok" if tr_key != "none" else "muted",
        )

        if sub_on:
            st_key = _key(SUBTITLE_STYLES, self.sub_style_choice.get())
            t_flag = "+Tiêu đề" if self.show_scene_titles.get() else ""
            sub_badge_map = {
                "highlight_3words": "3 chữ sáng",
                "pop_2words": "1 chữ",
                "karaoke_line": "Karaoke",
            }
            self.bdg_sub.set_status(
                f"💬 {sub_badge_map.get(st_key, 'Phụ đề')}{t_flag}",
                "ok",
            )
        else:
            self.bdg_sub.set_status("💬 Phụ đề: Tắt", "muted")

        wm_txt = self.wm_text.get().strip()
        if wm_on and wm_txt:
            self.bdg_wm.set_status(f"🏷️ {wm_txt[:22]}", "ok")
        else:
            self.bdg_wm.set_status("🏷️ Watermark: Tắt", "muted")

        aai_lv = _key(ANTI_AI_LEVELS, self.anti_ai_level_choice.get())
        self.chk_anti_ai_grain.config(state="normal" if aai_lv != "off" else "disabled")

        flags = []
        if self.cinematic_grade.get():
            flags.append("Màu phim")
        if self.loudness_norm.get():
            flags.append("-11 LUFS")
        if aai_lv != "off":
            flags.append(f"Né AI ({ANTI_AI_SHORT.get(aai_lv, aai_lv)})")
        if self.smart_clip_fit.get():
            flags.append("Fit clip")
        if flags:
            self.bdg_post.set_status("✨ " + " · ".join(flags), "ok")
        else:
            self.bdg_post.set_status("✨ Hậu kỳ: Tắt", "muted")

    def save_settings(self) -> None:
        aai_lv = _key(ANTI_AI_LEVELS, self.anti_ai_level_choice.get())
        s = dataclasses.replace(
            self.app.settings,
            tts_engine=_key(ENGINES, self.engine_choice.get()),
            edge_voice=self.edge_voice.get().strip() or "vi-VN-NamMinhNeural",
            elevenlabs_voice_id=self.eleven_voice.get().strip(),
            story_frame_preset=_key(FRAME_PRESET_CHOICES, self.frame_preset_choice.get()),
            story_shot_mode=_key(SHOT_MODES, self.shot_mode_choice.get()),
            story_transition=_key(TRANSITIONS, self.transition_choice.get()),
            story_transition_duration=round(self.transition_dur.get(), 2),
            story_smart_clip_fit=self.smart_clip_fit.get(),
            story_subtitle_style=_key(SUBTITLE_STYLES, self.sub_style_choice.get()),
            story_burn_subs=self.burn_subs.get(),
            story_show_scene_titles=self.show_scene_titles.get(),
            story_sub_font=self.sub_font.get().strip() or "Arial",
            story_sub_font_size=int(round(self.sub_font_size.get())),
            story_sub_margin_v=int(round(self.sub_margin_v.get())),
            story_sub_color=self.sub_color.get().strip() or "#FFE000",
            story_watermark_enabled=self.wm_enabled.get(),
            story_watermark_text=self.wm_text.get().strip(),
            story_watermark_pos=_key(WATERMARK_POS, self.wm_pos_choice.get()),
            story_watermark_opacity=int(round(self.wm_opacity.get())),
            story_cinematic_grade=self.cinematic_grade.get(),
            story_loudness_norm=self.loudness_norm.get(),
            story_anti_ai=(aai_lv != "off"),
            story_anti_ai_level=aai_lv,
            story_anti_ai_grain=self.anti_ai_grain.get(),
            story_codec=CODECS.get(self.codec_choice.get(), "h264"),
            story_bgm_volume=round(self.bgm_volume.get(), 2),
            story_sfx_volume=round(self.sfx_volume.get(), 2),
            story_speed=round(self.speed.get(), 2),
        )
        self.app.settings = s
        s.save()

    def _save_with_feedback(self) -> None:
        self.save_settings()
        self.log.write("💾 Đã lưu toàn bộ cấu hình Story Studio vào settings.json.", "ok")

    # ------------------------------------------------------------- Logic thao tác
    def _cancel(self):
        self.reporter.cancel()

    def _open_output(self):
        if self.last_output and self.last_output.is_file():
            self.app.open_path(self.last_output)

    def _get_engine(self) -> TTSEngine:
        engine_key = _key(ENGINES, self.engine_choice.get())
        if engine_key == "tone":
            return ToneTTS()
        if engine_key == "elevenlabs":
            api_key = self.app.keys().elevenlabs
            vid = self.eleven_voice.get().strip() or self.app.settings.elevenlabs_voice_id
            if not api_key:
                raise ValueError("Chưa nhập ELEVENLABS_API_KEY trong tab Cài đặt.")
            if not vid:
                raise ValueError("Vui lòng dán ElevenLabs Voice ID trước khi xuất video.")
            return ElevenLabsTTS(api_key, vid, self.app.settings.elevenlabs_model)
        raw_voice = self.edge_voice.get().strip() or "vi-VN-NamMinhNeural"
        # Giữ Edge-TTS ở 1.00x để tái dùng AudioCache; StoryPipeline sẽ đồng bộ tốc độ bằng FFmpeg atempo (tránh tăng tốc 2 lần)
        return EdgeTTS(raw_voice, rate="+0%")

    def check_scenes(self):
        """Kiểm tra tài nguyên media, thời lượng ước tính và tương thích schema v3."""
        sb_path = self.storyboard_path.get().strip()
        assets_p = self.assets_dir.get().strip()

        if not sb_path:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng chọn file storyboard.json trước.")
            return
        if not assets_p:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng chọn thư mục chứa tài nguyên ảnh/clip.")
            return

        self.save_settings()
        self.log.clear()
        self.log.write(f"🔍 Đang kiểm tra storyboard: {sb_path}", "accent")

        try:
            sb = load_storyboard(sb_path)
            sb.ensure_scenes_from_beats()
            v3_ready = bool(sb.beats)
            est_sec = round(sb.total_words / 2.8) if sb.total_words else 0
            eff_fp = resolve_frame_preset(_key(FRAME_PRESET_CHOICES, self.frame_preset_choice.get()), sb.frame_preset)
            self.log.write(
                f"✓ Kịch bản: '{sb.title}' | {len(sb.scenes)} cảnh | ~{sb.total_words} từ (~{est_sec}s) "
                f"| khung: {eff_fp.name} ({eff_fp.out_w}x{eff_fp.out_h}) | v3 beats: {'Có' if v3_ready else 'Không'}",
                "ok",
            )

            found, missing = validate_story_assets(sb, assets_p)
            mp4_cnt = 0
            img_cnt = 0
            for sc in sb.scenes:
                asset = found.get(sc.id)
                if asset:
                    is_vid = asset.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")
                    mp4_cnt += int(is_vid)
                    img_cnt += int(not is_vid)
                    kind = "Clip [MP4]" if is_vid else "Ảnh [IMG]"
                    title_tag = f" | chữ trên: '{sc.on_screen_text}'" if sc.on_screen_text else ""
                    self.log.write(f"  • Cảnh {sc.id:02d}: {kind} {asset.name} (sfx: {sc.sfx}{title_tag})")
                else:
                    self.log.write(
                        f"  ❌ Cảnh {sc.id:02d}: THIẾU TÀI NGUYÊN (cần {sc.id:03d}.mp4 hoặc {sc.id:03d}.png)!",
                        "error",
                    )

            if _key(SHOT_MODES, self.shot_mode_choice.get()) == "planned" and not v3_ready:
                self.log.write(
                    "⚠️ File JSON này chưa có mảng 'beats' → khi xuất sẽ tự chuyển về chế độ Từng cảnh (Scene).",
                    "warn",
                )

            if missing:
                self.status.config(text=f"⚠️ Thiếu tài nguyên cho {len(missing)} cảnh: {missing}")
                self.log.write(f"⚠️ Phát hiện {len(missing)} cảnh thiếu ảnh/clip!", "warn")
            else:
                self.status.config(
                    text=f"✓ Đủ {len(sb.scenes)} cảnh ({mp4_cnt} MP4, {img_cnt} ảnh). Sẵn sàng xuất video!"
                )
                self.log.write(
                    f"🎉 Sẵn sàng xuất video 9:16 ({mp4_cnt} clip MP4, {img_cnt} ảnh tĩnh)!",
                    "ok",
                )
        except Exception as e:
            self.log.write(f"❌ Lỗi khi kiểm tra: {e}", "error")
            messagebox.showerror("Lỗi kiểm tra", str(e))

    def start_render(self):
        """Khởi chạy quy trình dựng phim Story 9:16 ở luồng nền."""
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

        self.save_settings()
        render_kwargs = self.app.settings.story_render_kwargs()
        self.log.clear()

        # Tự động lùi an toàn nếu chọn v3 Planned nhưng file storyboard chưa có beats
        try:
            sb_check = load_storyboard(sb_path)
            if render_kwargs["shot_mode"] == "planned" and not sb_check.beats:
                self.log.write("ℹ️ Kịch bản chưa có 'beats' → tự dùng chế độ từng cảnh (scene).", "warn")
                render_kwargs["shot_mode"] = "scene"
        except Exception:
            pass

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
                **render_kwargs,
            )
            self.last_output = out_file
            self.app.post(self.btn_open.config, state="normal")
            return out_file

        self.app.run_job(
            work,
            buttons=(self.btn_check, self.btn_render, self.btn_aai_run),
            cancel_button=self.btn_cancel,
            reporter=self.reporter,
            log=self.log,
        )

    # ------------------------------------------------------------- Tab 4: Né quét AI độc lập
    def _on_aai_input_changed(self, *_):
        raw = self.aai_input_path.get().strip()
        if not raw:
            return
        p = Path(raw)
        if p.is_file():
            default_out = p.with_name(f"{p.stem}_clean.mp4")
            self.aai_output_path.set(str(default_out))

    def _open_aai_folder(self):
        if self.last_aai_output and self.last_aai_output.parent.is_dir():
            self.app.open_path(self.last_aai_output.parent)

    def start_standalone_anti_ai(self):
        """Chạy bộ lọc Né quét AI độc lập cho một file video hoàn chỉnh trên background thread."""
        in_raw = self.aai_input_path.get().strip()
        out_raw = self.aai_output_path.get().strip()

        if not in_raw or not Path(in_raw).is_file():
            messagebox.showerror("Lỗi", "Vui lòng chọn file video đầu vào hợp lệ (.mp4 / .mov / .mkv).")
            return

        in_path = Path(in_raw)
        if not out_raw:
            out_path = in_path.with_name(f"{in_path.stem}_clean.mp4")
            self.aai_output_path.set(str(out_path))
        else:
            out_path = Path(out_raw)
            if out_path.suffix.lower() != ".mp4":
                out_path = out_path.with_suffix(".mp4")
                self.aai_output_path.set(str(out_path))

        if out_path.resolve() == in_path.resolve():
            messagebox.showerror("Lỗi", "Đường dẫn file đầu ra không được trùng với file đầu vào.")
            return

        lv = _key(ANTI_AI_STANDALONE_LEVELS, self.aai_standalone_level.get())
        grain = bool(self.aai_standalone_grain.get())
        spd = round(float(self.aai_standalone_speed.get()), 2)

        self.log.clear()
        self.btn_aai_open_dir.config(state="disabled")
        self.btn_open.config(state="disabled")

        def work():
            out_file = process_standalone_video(
                input_path=in_path,
                output_path=out_path,
                level=lv,
                film_grain=grain,
                speed=spd,
                log_cb=self.reporter.log,
                progress_cb=self.reporter.progress,
            )
            self.last_aai_output = out_file
            self.last_output = out_file
            self.app.post(self.btn_aai_open_dir.config, state="normal")
            self.app.post(self.btn_open.config, state="normal")
            return out_file

        self.app.run_job(
            work,
            buttons=(self.btn_check, self.btn_render, self.btn_aai_run),
            cancel_button=self.btn_cancel,
            reporter=self.reporter,
            log=self.log,
        )