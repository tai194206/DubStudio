"""Tab YouTube 16:9: Biên tập series nhiều tập thành video tài liệu YouTube 16:9 hoàn chỉnh."""
from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass, replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple, Union

from core.config import ROOT_DIR
from core.series_compiler import (
    DEFAULT_SPEED_FACTOR,
    MAX_SPEED_FACTOR,
    MIN_SPEED_FACTOR,
    EpisodeEntry,
    LicenseAuditReport,
    SeriesCompileConfig,
    SeriesCompileResult,
    audit_series_licenses,
    compile_series_youtube,
    scan_series_folder,
)

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

LAYOUT_MODES: Dict[str, str] = {
    "smart": "Thông minh: Ngang tràn / Dọc nền mờ",
    "pillarbox": "Luôn nền mờ tài liệu",
    "full_bleed": "Luôn tràn khung 16:9",
}

SUBTITLE_MODES: Dict[str, str] = {
    "srt_only": "Chỉ xuất file .srt rời (Khuyên dùng YouTube)",
    "burn_and_srt": "Đốt phụ đề đáy màn hình + xuất .srt",
}

ANTI_AI_YT_LEVELS: Dict[str, str] = {
    "off": "Tắt",
    "light": "Nhẹ",
    "medium": "Vừa",
    "strong": "Mạnh",
}


def _label(mapping: Dict[str, str], key: str) -> str:
    return mapping.get(key, next(iter(mapping.values())))


def _key(mapping: Dict[str, str], label: str) -> str:
    return next((k for k, v in mapping.items() if v == label), next(iter(mapping)))


def parse_skip_scenes_text(raw: str) -> List[int]:
    """Phân tích chuỗi số cảnh cần bỏ (vd: '21' hoặc '19, 21') thành danh sách số nguyên dương."""
    text = (raw or "").strip()
    if not text:
        return []
    tokens = [tok.strip() for tok in re.split(r"[,;\s]+", text) if tok.strip()]
    ids: List[int] = []
    for tok in tokens:
        if not tok.isdigit() or int(tok) <= 0:
            raise ValueError(f"Số cảnh không hợp lệ: '{tok}' (chỉ nhập số nguyên dương cách nhau bằng dấu phẩy).")
        ids.append(int(tok))
    return sorted(set(ids))


@dataclass
class _EpisodeRowState:
    """Trạng thái chỉnh sửa của từng dòng tập trong bảng quản lý Series."""

    entry: EpisodeEntry
    enabled_var: tk.BooleanVar
    chapter_title_var: tk.StringVar
    skip_var: tk.StringVar
    status_text: str
    status_color: str
    user_edited_skip: bool = False


class SeriesYouTubeTab(ttk.Frame):
    """Tab độc lập biên tập Series YouTube 16:9 ngang hàng Studio & Story."""

    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app

        # --- Biến cấu hình Series & đường dẫn ---
        self.series_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.bgm_path = tk.StringVar()
        self.channel_name = tk.StringVar()

        # --- Biến cấu hình Xuất YouTube 16:9 ---
        self.layout_mode_choice = tk.StringVar(value=_label(LAYOUT_MODES, "smart"))
        self.subtitle_mode_choice = tk.StringVar(value=_label(SUBTITLE_MODES, "srt_only"))
        self.speed_factor = tk.DoubleVar(value=DEFAULT_SPEED_FACTOR)
        self.anti_ai_choice = tk.StringVar(value=_label(ANTI_AI_YT_LEVELS, "medium"))

        self.prefer_assets_16x9 = tk.BooleanVar(value=True)
        self.auto_skip_mid_cta = tk.BooleanVar(value=True)
        self.include_chapter_cards = tk.BooleanVar(value=True)
        self.include_end_credits = tk.BooleanVar(value=True)
        self.loudnorm_youtube = tk.BooleanVar(value=True)

        self.bgm_volume = tk.DoubleVar(value=0.28)
        self.sfx_volume = tk.DoubleVar(value=0.80)

        # --- Trạng thái nội bộ ---
        self.episode_rows: List[_EpisodeRowState] = []
        self.last_license_report: Optional[LicenseAuditReport] = None
        self.last_result: Optional[SeriesCompileResult] = None
        self.compliance_has_warnings: bool = False
        self._risk_panel_visible: bool = False
        self._syncing_rows: bool = False
        self._last_auto_export_dir: str = ""

        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, width=450)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)

        self._build_left(left)
        self._build_right(right)

        self.auto_skip_mid_cta.trace_add("write", lambda *_: self._on_auto_skip_toggled())

    # ------------------------------------------------------------------ Cột trái: Cấu hình & Nút xuất
    def _build_left(self, parent: ttk.Frame) -> None:
        scroller = ScrollableFrame(parent, padding=4)
        scroller.pack(fill="both", expand=True)
        body = scroller.body

        # 1. Bố cục, Phụ đề & Anti-AI
        box_fmt = ttk.LabelFrame(body, text="Bố cục, Phụ đề & Anti-AI", style="Card.TLabelframe", padding=10)
        box_fmt.pack(fill="x", pady=(0, 8))

        self.cb_layout = row(
            box_fmt,
            "Bố cục 16:9",
            lambda p: ttk.Combobox(
                p, textvariable=self.layout_mode_choice, values=list(LAYOUT_MODES.values()), state="readonly"
            ),
            label_width=14,
        )
        self.cb_subtitle = row(
            box_fmt,
            "Phụ đề",
            lambda p: ttk.Combobox(
                p, textvariable=self.subtitle_mode_choice, values=list(SUBTITLE_MODES.values()), state="readonly"
            ),
            label_width=14,
        )
        LabeledScale(
            box_fmt,
            "Tốc độ đọc",
            self.speed_factor,
            MIN_SPEED_FACTOR,
            MAX_SPEED_FACTOR,
            "{:.2f}x",
            label_width=14,
        ).pack(fill="x", pady=3)
        self.cb_anti_ai = row(
            box_fmt,
            "Lọc Anti-AI",
            lambda p: ttk.Combobox(
                p, textvariable=self.anti_ai_choice, values=list(ANTI_AI_YT_LEVELS.values()), state="readonly"
            ),
            label_width=14,
        )
        ttk.Label(
            box_fmt,
            text="🛡️ Chỉ áp nhẹ trên cảnh AI; luôn miễn lọc 100% ảnh tư liệu lịch sử thật trong archive_sources.json.",
            style="CardMuted.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(4, 0))

        # 2. Tùy chọn biên tập & Chính sách YouTube
        box_opt = ttk.LabelFrame(body, text="Tùy chọn biên tập & Chính sách", style="Card.TLabelframe", padding=10)
        box_opt.pack(fill="x", pady=(0, 8))

        for var, text in (
            (self.prefer_assets_16x9, "Ưu tiên tài nguyên assets_16x9/ (tự lùi về assets/ nếu thiếu)"),
            (self.auto_skip_mid_cta, "Tự bỏ cảnh CTA cuối ở các tập giữa"),
            (self.include_chapter_cards, "Chèn Thẻ tiêu đề chương (2.5s)"),
            (self.include_end_credits, "Chèn End Credits ghi nguồn tư liệu (4.5s)"),
            (self.loudnorm_youtube, "Chuẩn hóa âm lượng YouTube (-14 LUFS)"),
        ):
            ttk.Checkbutton(
                box_opt,
                text=text,
                variable=var,
                style="Card.TCheckbutton",
            ).pack(anchor="w", pady=2)

        # 3. Âm thanh & Đầu ra
        box_aud = ttk.LabelFrame(body, text="Âm thanh & Đầu ra", style="Card.TLabelframe", padding=10)
        box_aud.pack(fill="x", pady=(0, 6))

        FileRow(
            box_aud,
            "BGM dài (1.00x)",
            self.bgm_path,
            mode="open",
            filetypes=[
                ("Âm thanh / Video có nhạc", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.mp4 *.mov *.mkv *.webm"),
                ("Tất cả", "*.*"),
            ],
            label_width=15,
        ).pack(fill="x", pady=3)

        LabeledScale(box_aud, "Âm lượng BGM", self.bgm_volume, 0.0, 0.80, "{:.0%}", label_width=15).pack(
            fill="x", pady=2
        )
        LabeledScale(box_aud, "Âm lượng SFX", self.sfx_volume, 0.0, 1.50, "{:.0%}", label_width=15).pack(
            fill="x", pady=2
        )
        self.ent_channel = row(
            box_aud,
            "Kênh / Watermark",
            lambda p: ttk.Entry(p, textvariable=self.channel_name),
            label_width=15,
        )
        FileRow(box_aud, "Thư mục xuất", self.output_dir, mode="dir", label_width=15).pack(fill="x", pady=(4, 2))
        ttk.Label(
            box_aud,
            text="💡 Mặc định xuất 4 file vào <series>/youtube_export/ (MP4 16:9, SRT, Mô tả Chapters, Compliance JSON).",
            style="CardMuted.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(2, 0))

        # --- Thanh nút hành động cố định dưới đáy cột trái ---
        bar_main = ttk.Frame(parent, padding=(4, 8, 4, 2))
        bar_main.pack(fill="x")

        self.btn_render = ttk.Button(
            bar_main,
            text="🎬 Xuất Series 16:9",
            style="Primary.TButton",
            command=self.start_render,
        )
        self.btn_render.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_cancel = ttk.Button(
            bar_main,
            text="⛔ Hủy",
            style="Danger.TButton",
            state="disabled",
            command=self._cancel,
        )
        self.btn_cancel.pack(side="left")

        bar_out = ttk.Frame(parent, padding=(4, 4, 4, 0))
        bar_out.pack(fill="x")

        self.btn_open_export = ttk.Button(
            bar_out,
            text="📂 Mở thư mục xuất",
            style="Success.TButton",
            state="disabled",
            command=self._open_export_dir,
        )
        self.btn_open_export.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.btn_open_desc = ttk.Button(
            bar_out,
            text="📄 Mở Mô tả & Chapters",
            style="Neutral.TButton",
            state="disabled",
            command=self._open_description_file,
        )
        self.btn_open_desc.pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------------ Cột phải: Series, Tập, Compliance & Log
    def _build_right(self, parent: ttk.Frame) -> None:
        # 1. Chọn thư mục Series
        box_src = ttk.LabelFrame(parent, text="Chọn thư mục Series", style="Card.TLabelframe", padding=10)
        box_src.pack(fill="x", pady=(0, 8))

        src_row = ttk.Frame(box_src, style="Surface.TFrame")
        src_row.pack(fill="x")
        ttk.Label(src_row, text="Thư mục Series", width=14, style="Card.TLabel").pack(side="left")
        self.ent_series_dir = ttk.Entry(src_row, textvariable=self.series_dir)
        self.ent_series_dir.pack(side="left", fill="x", expand=True, padx=(4, 6))
        self.ent_series_dir.bind("<Return>", lambda _e: self.rescan_series())

        self.btn_browse_series = ttk.Button(
            src_row,
            text="Chọn thư mục…",
            style="Compact.TButton",
            command=self._browse_series_dir,
        )
        self.btn_browse_series.pack(side="left", padx=(0, 6))

        self.btn_rescan = ttk.Button(
            src_row,
            text="🔄 Quét lại",
            style="Compact.TButton",
            command=self.rescan_series,
        )
        self.btn_rescan.pack(side="left")

        # 2. Bảng Quản lý Tập
        box_eps = ttk.LabelFrame(parent, text="Danh sách Tập & Chỉnh sửa Chương", style="Card.TLabelframe", padding=8)
        box_eps.pack(fill="x", pady=(0, 8))

        hdr = ttk.Frame(box_eps, style="Surface.TFrame")
        hdr.pack(fill="x", pady=(0, 4))
        for col_txt, w_chars in (
            ("Bật", 4),
            ("Tập", 20),
            ("Tiêu đề chương (in Thẻ & Chapters)", 28),
            ("Cảnh / Tư liệu / 16:9", 26),
            ("Cảnh bỏ (CTA)", 13),
            ("Trạng thái", 18),
        ):
            ttk.Label(hdr, text=col_txt, width=w_chars, style="CardMuted.TLabel", font=FONT_BOLD).pack(
                side="left", padx=2
            )

        tbl_wrap = ttk.Frame(box_eps, style="Surface.TFrame")
        tbl_wrap.pack(fill="x")

        self._table_canvas = tk.Canvas(
            tbl_wrap,
            height=175,
            bg=COLORS["input"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            bd=0,
        )
        self._table_vbar = ttk.Scrollbar(tbl_wrap, orient="vertical", command=self._table_canvas.yview)
        self._table_body = ttk.Frame(self._table_canvas, style="Surface.TFrame", padding=4)
        self._table_win_id = self._table_canvas.create_window((0, 0), window=self._table_body, anchor="nw")
        self._table_canvas.configure(yscrollcommand=self._table_vbar.set)

        self._table_canvas.pack(side="left", fill="both", expand=True)
        self._table_vbar.pack(side="right", fill="y")

        self._table_body.bind("<Configure>", self._on_table_body_configure)
        self._table_canvas.bind("<Configure>", self._on_table_canvas_configure)
        self._table_canvas.bind("<Enter>", lambda _e: self._bind_table_wheel(True))
        self._table_canvas.bind("<Leave>", lambda _e: self._bind_table_wheel(False))
        self.bind("<Destroy>", lambda _e: self._bind_table_wheel(False), add="+")

        self._empty_table_lbl = ttk.Label(
            self._table_body,
            text="Chưa quét series. Nhấn 'Chọn thư mục…' để nạp danh sách tập.",
            style="CardMuted.TLabel",
        )
        self._empty_table_lbl.pack(anchor="w", padx=6, pady=12)

        # 3. Khung Kiểm tra Bản quyền & Chính sách (Compliance Panel)
        self.box_compliance = ttk.LabelFrame(
            parent,
            text="Kiểm tra Bản quyền & Chính sách (Compliance Panel)",
            style="Card.TLabelframe",
            padding=8,
        )
        self.box_compliance.pack(fill="x", pady=(0, 8))

        badge_row = ttk.Frame(self.box_compliance, style="Surface.TFrame")
        badge_row.pack(fill="x", pady=(0, 4))

        self.bdg_lic_total = StatusBadge(badge_row, text="Tổng nguồn: 0", kind="muted")
        self.bdg_lic_total.pack(side="left", padx=(0, 6))
        self.bdg_lic_cc = StatusBadge(badge_row, text="CC BY / SA: 0", kind="info")
        self.bdg_lic_cc.pack(side="left", padx=(0, 6))
        self.bdg_lic_pd = StatusBadge(badge_row, text="Public Domain: 0", kind="ok")
        self.bdg_lic_pd.pack(side="left", padx=(0, 6))
        self.bdg_lic_risk = StatusBadge(badge_row, text="Bản quyền: Chưa kiểm tra", kind="muted")
        self.bdg_lic_risk.pack(side="left")

        self.lbl_lic_note = ttk.Label(
            self.box_compliance,
            text="ℹ️ Nguồn CC BY / CC BY-SA & Public Domain sẽ tự động xuất vào End Credits và youtube_description.txt.",
            style="CardMuted.TLabel",
            wraplength=680,
        )
        self.lbl_lic_note.pack(anchor="w")

        self.risk_panel = tk.Frame(
            self.box_compliance,
            bg="#3b0d14",
            highlightthickness=1,
            highlightbackground=COLORS["danger"],
            padx=8,
            pady=6,
        )
        self.lbl_risk_title = tk.Label(
            self.risk_panel,
            text="⚠️ CẢNH BÁO BẢN QUYỀN RỦI RO — Vui lòng rà soát trước khi đăng tải thương mại:",
            bg="#3b0d14",
            fg="#fca5a5",
            font=FONT_BOLD,
            anchor="w",
        )
        self.lbl_risk_title.pack(fill="x", pady=(0, 4))

        self.risk_text = tk.Text(
            self.risk_panel,
            height=3,
            bg="#26080d",
            fg="#fecaca",
            font=FONT_SM,
            relief="flat",
            bd=0,
            wrap="word",
            state="disabled",
        )
        self.risk_text.pack(fill="x")

        # 4. Tiến độ & Nhật ký
        ttk.Label(parent, text="Tiến độ & Nhật ký biên tập Series 16:9", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 3)
        )
        self.bar = GradientProgressBar(parent)
        self.bar.pack(fill="x", pady=(0, 4))

        self.status = ttk.Label(
            parent,
            text="Sẵn sàng. Chọn thư mục series (vd: story_mode/projects/Dien_Bien_Phu_1954) để quét tập.",
            style="Muted.TLabel",
        )
        self.status.pack(anchor="w", pady=(0, 4))

        self.log = LogBox(parent, height=9)
        self.log.pack(fill="both", expand=True)

        self.reporter = self.app.make_reporter(self.log, self.bar, self.status)

    # ------------------------------------------------------------------ Scroll bảng tập
    def _on_table_body_configure(self, _e) -> None:
        if self._table_canvas.winfo_exists():
            self._table_canvas.configure(scrollregion=self._table_canvas.bbox("all"))

    def _on_table_canvas_configure(self, event) -> None:
        if self._table_canvas.winfo_exists():
            self._table_canvas.itemconfigure(self._table_win_id, width=event.width)

    def _on_table_wheel(self, event) -> None:
        try:
            if not self._table_canvas.winfo_exists():
                return
            bbox = self._table_canvas.bbox("all")
            if bbox and bbox[3] > self._table_canvas.winfo_height():
                delta = int(-1 * (event.delta / 120)) if event.delta else 0
                if delta:
                    self._table_canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _bind_table_wheel(self, bind: bool) -> None:
        try:
            if not self.winfo_exists():
                return
            if bind:
                self._table_canvas.bind_all("<MouseWheel>", self._on_table_wheel)
            else:
                self._table_canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ Quét series & Bảng tập
    def _default_projects_dir(self) -> Path:
        cand = ROOT_DIR / "story_mode" / "projects"
        return cand if cand.is_dir() else ROOT_DIR

    def _browse_series_dir(self) -> None:
        cur = self.series_dir.get().strip()
        init_dir = Path(cur) if cur and Path(cur).is_dir() else self._default_projects_dir()
        picked = filedialog.askdirectory(initialdir=str(init_dir), title="Chọn thư mục Series")
        if picked:
            self.load_series(picked)

    def rescan_series(self) -> None:
        raw = self.series_dir.get().strip()
        if not raw:
            messagebox.showwarning("Thiếu thư mục", "Vui lòng chọn thư mục Series trước khi quét lại.")
            return
        try:
            self.load_series(raw)
        except Exception as e:
            self.log.write(f"❌ Không thể quét series: {e}", "error")
            messagebox.showerror("Lỗi quét Series", str(e))

    def load_series(self, series_dir: Union[Path, str]) -> Tuple[List[EpisodeEntry], LicenseAuditReport]:
        """Quét thư mục series, nạp bảng quản lý tập và kiểm tra bản quyền ngay lập tức."""
        series_p = Path(series_dir).resolve()
        episodes = scan_series_folder(series_p)
        report = audit_series_licenses(episodes)

        self.series_dir.set(str(series_p))
        cur_out = self.output_dir.get().strip()
        default_export = str(series_p / "youtube_export")
        if not cur_out or cur_out == self._last_auto_export_dir:
            self.output_dir.set(default_export)
        self._last_auto_export_dir = default_export

        self._populate_episode_table(episodes)
        self.update_compliance_panel(report)

        ready_cnt = sum(1 for r in self.episode_rows if r.enabled_var.get())
        self.status.config(
            text=f"Đã quét {len(episodes)} tập ({ready_cnt} tập bật xuất) · {report.total_unique_sources} nguồn tư liệu."
        )
        self.log.write(
            f"📺 Đã nạp Series '{series_p.name}': {len(episodes)} tập | {report.total_unique_sources} nguồn tư liệu duy nhất.",
            "accent",
        )
        if report.has_risky_warnings:
            self.log.write(
                f"⚠️ Phát hiện {len(report.risky_warnings)} nguồn có cờ rủi ro bản quyền — xem khung cảnh báo bên trên!",
                "warn",
            )
        return episodes, report

    def _populate_episode_table(self, episodes: Sequence[EpisodeEntry]) -> None:
        for child in self._table_body.winfo_children():
            child.destroy()
        self.episode_rows.clear()

        if not episodes:
            self._empty_table_lbl = ttk.Label(
                self._table_body,
                text="Không tìm thấy tập hợp lệ trong thư mục đã chọn.",
                style="CardMuted.TLabel",
            )
            self._empty_table_lbl.pack(anchor="w", padx=6, pady=12)
            return

        self._syncing_rows = True
        try:
            auto_skip = bool(self.auto_skip_mid_cta.get())
            for ep in episodes:
                has_missing = len(ep.missing_assets) > 0
                if has_missing:
                    default_on = False
                    status_txt = f"❌ Thiếu {len(ep.missing_assets)} ảnh"
                    status_fg = COLORS["danger"]
                elif not ep.has_full_audio:
                    default_on = bool(ep.enabled)
                    status_txt = "⚠️ Thiếu audio (TTS)"
                    status_fg = COLORS["warn"]
                else:
                    default_on = bool(ep.enabled)
                    status_txt = "✓ Sẵn sàng"
                    status_fg = COLORS["success"]

                init_skips = (
                    ep.skip_scene_ids
                    if ep.skip_scene_ids is not None
                    else (ep.suggested_skip_scenes if auto_skip else [])
                )
                skip_str = ", ".join(str(s) for s in init_skips)

                en_var = tk.BooleanVar(value=default_on)
                ch_var = tk.StringVar(value=ep.chapter_title)
                sk_var = tk.StringVar(value=skip_str)

                state = _EpisodeRowState(
                    entry=ep,
                    enabled_var=en_var,
                    chapter_title_var=ch_var,
                    skip_var=sk_var,
                    status_text=status_txt,
                    status_color=status_fg,
                    user_edited_skip=(ep.skip_scene_ids is not None),
                )
                sk_var.trace_add("write", lambda *_args, st=state: self._mark_skip_edited(st))
                self.episode_rows.append(state)

                row_f = ttk.Frame(self._table_body, style="Surface.TFrame")
                row_f.pack(fill="x", pady=2)

                ttk.Checkbutton(row_f, variable=en_var, width=3, style="Card.TCheckbutton").pack(side="left", padx=2)
                ttk.Label(row_f, text=ep.episode_id, width=20, style="Card.TLabel").pack(side="left", padx=2)
                ttk.Entry(row_f, textvariable=ch_var, width=28).pack(side="left", padx=2)
                ttk.Label(
                    row_f,
                    text=f"{ep.scene_count} cảnh · {ep.archival_count} thật · {ep.assets_16x9_count}/{ep.scene_count} 16:9",
                    width=26,
                    style="CardMuted.TLabel",
                ).pack(side="left", padx=2)
                ttk.Entry(row_f, textvariable=sk_var, width=13).pack(side="left", padx=2)
                tk.Label(
                    row_f,
                    text=status_txt,
                    width=18,
                    anchor="w",
                    bg=COLORS["surface"],
                    fg=status_fg,
                    font=FONT_SM,
                ).pack(side="left", padx=2)
        finally:
            self._syncing_rows = False

    def _mark_skip_edited(self, state: _EpisodeRowState) -> None:
        if not self._syncing_rows:
            state.user_edited_skip = True

    def _on_auto_skip_toggled(self) -> None:
        auto_on = bool(self.auto_skip_mid_cta.get())
        self._syncing_rows = True
        try:
            for st in self.episode_rows:
                if not st.user_edited_skip:
                    skips = st.entry.suggested_skip_scenes if auto_on else []
                    st.skip_var.set(", ".join(str(s) for s in skips))
        finally:
            self._syncing_rows = False

    # ------------------------------------------------------------------ Compliance Panel
    def update_compliance_panel(self, report: Optional[LicenseAuditReport]) -> None:
        """Cập nhật badge bản quyền & khung cảnh báo đỏ/cam khi có nguồn rủi ro."""
        self.last_license_report = report
        if report is None:
            self.bdg_lic_total.set_status("Tổng nguồn: 0", "muted")
            self.bdg_lic_cc.set_status("CC BY / SA: 0", "info")
            self.bdg_lic_pd.set_status("Public Domain: 0", "ok")
            self.bdg_lic_risk.set_status("Bản quyền: Chưa kiểm tra", "muted")
            self.compliance_has_warnings = False
            self._set_risk_panel_visible(False, "")
            return

        cc_cnt = len(report.cc_attribution_required)
        pd_cnt = len(report.public_domain)
        risk_cnt = len(report.risky_warnings)

        self.bdg_lic_total.set_status(f"Tổng nguồn: {report.total_unique_sources}", "info")
        self.bdg_lic_cc.set_status(f"CC BY / SA: {cc_cnt}", "info" if cc_cnt else "muted")
        self.bdg_lic_pd.set_status(f"Public Domain: {pd_cnt}", "ok" if pd_cnt else "muted")

        if report.has_risky_warnings:
            self.compliance_has_warnings = True
            self.bdg_lic_risk.set_status(f"⚠️ {risk_cnt} nguồn rủi ro", "error")
            lines = []
            for item in report.risky_warnings:
                eps = ", ".join(item.episodes) if item.episodes else "?"
                reasons = ", ".join(item.risk_reasons)
                lines.append(f"• [{eps}] {item.title} — Rủi ro: {reasons} (Giấy phép: {item.license or 'không rõ'})")
            self._set_risk_panel_visible(True, "\n".join(lines))
        else:
            self.compliance_has_warnings = False
            self.bdg_lic_risk.set_status("✓ Sạch rủi ro thương mại", "ok")
            self._set_risk_panel_visible(False, "")

    def _set_risk_panel_visible(self, visible: bool, body_text: str) -> None:
        self.risk_text.config(state="normal")
        self.risk_text.delete("1.0", "end")
        if body_text:
            self.risk_text.insert("1.0", body_text)
        self.risk_text.config(state="disabled")

        if visible and not self._risk_panel_visible:
            self.risk_panel.pack(fill="x", pady=(6, 0))
        elif not visible and self._risk_panel_visible:
            self.risk_panel.pack_forget()
        self._risk_panel_visible = visible

    # ------------------------------------------------------------------ Mapping sang core/series_compiler
    def build_compile_inputs(self) -> Tuple[List[EpisodeEntry], SeriesCompileConfig]:
        """Chuyển trạng thái UI hiện tại thành `(episodes, SeriesCompileConfig)` chuẩn."""
        episodes: List[EpisodeEntry] = []
        skip_overrides: Dict[str, List[int]] = {}

        for st in self.episode_rows:
            parsed_skips = parse_skip_scenes_text(st.skip_var.get())
            ch_title = st.chapter_title_var.get().strip() or st.entry.chapter_title
            cloned = replace(
                st.entry,
                enabled=bool(st.enabled_var.get()),
                chapter_title=ch_title,
                skip_scene_ids=list(parsed_skips),
            )
            episodes.append(cloned)
            skip_overrides[cloned.episode_id] = list(parsed_skips)

        spd = round(max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, float(self.speed_factor.get()))), 2)
        bgm_vol = round(max(0.0, min(1.0, float(self.bgm_volume.get()))), 2)
        sfx_vol = round(max(0.0, min(1.5, float(self.sfx_volume.get()))), 2)
        ch_name = self.channel_name.get().strip()
        out_raw = self.output_dir.get().strip()
        bgm_raw = self.bgm_path.get().strip()

        cfg = SeriesCompileConfig(
            output_dir=Path(out_raw) if out_raw else None,
            layout_mode=_key(LAYOUT_MODES, self.layout_mode_choice.get()),
            subtitle_mode=_key(SUBTITLE_MODES, self.subtitle_mode_choice.get()),
            speed_factor=spd,
            anti_ai_level=_key(ANTI_AI_YT_LEVELS, self.anti_ai_choice.get()),
            prefer_assets_16x9=bool(self.prefer_assets_16x9.get()),
            auto_skip_mid_cta=bool(self.auto_skip_mid_cta.get()),
            episode_skip_overrides=skip_overrides,
            include_chapter_cards=bool(self.include_chapter_cards.get()),
            include_end_credits=bool(self.include_end_credits.get()),
            bgm_path=Path(bgm_raw) if bgm_raw else None,
            bgm_volume=bgm_vol,
            sfx_volume=sfx_vol,
            loudnorm_youtube=bool(self.loudnorm_youtube.get()),
            channel_name=ch_name,
            watermark_text=ch_name or None,
        )
        return episodes, cfg

    # ------------------------------------------------------------------ Chạy nền & Mở thành phẩm
    def start_render(self) -> None:
        """Kiểm tra hợp lệ và khởi chạy `compile_series_youtube` trên worker thread nền."""
        series_raw = self.series_dir.get().strip()
        if not series_raw or not Path(series_raw).is_dir():
            messagebox.showerror("Thiếu thư mục", "Vui lòng chọn thư mục Series hợp lệ trước khi xuất.")
            return

        if not self.episode_rows:
            try:
                self.load_series(series_raw)
            except Exception as e:
                messagebox.showerror("Lỗi quét Series", str(e))
                return

        try:
            episodes, cfg = self.build_compile_inputs()
        except ValueError as e:
            messagebox.showerror("Lỗi dữ liệu cảnh bỏ", str(e))
            return

        enabled_eps = [ep for ep in episodes if ep.enabled]
        if not enabled_eps:
            messagebox.showwarning("Chưa chọn tập", "Vui lòng bật ít nhất 1 tập hợp lệ để xuất Series 16:9.")
            return

        broken_eps = [f"{ep.episode_id} ({ep.missing_assets})" for ep in enabled_eps if ep.missing_assets]
        if broken_eps:
            messagebox.showerror(
                "Tập thiếu tài nguyên",
                f"Các tập đang bật bị thiếu ảnh/clip: {', '.join(broken_eps)}.\nHãy bổ sung hoặc bỏ tick các tập này.",
            )
            return

        self.log.clear()
        self.last_result = None
        self.btn_open_export.config(state="disabled")
        self.btn_open_desc.config(state="disabled")

        def work() -> SeriesCompileResult:
            return compile_series_youtube(
                series_raw,
                cfg,
                episodes=episodes,
                reporter=self.reporter,
                settings=self.app.settings,
                keys=self.app.keys(),
            )

        self.app.run_job(
            work,
            buttons=(self.btn_render, self.btn_browse_series, self.btn_rescan),
            cancel_button=self.btn_cancel,
            reporter=self.reporter,
            log=self.log,
            on_done=self._on_render_done,
        )

    def _on_render_done(self, result: Optional[SeriesCompileResult]) -> None:
        if result is None:
            return
        self.last_result = result
        self.btn_open_export.config(state="normal")
        self.btn_open_desc.config(state="normal")
        self.status.config(
            text=f"✓ Hoàn tất Series 16:9 ({result.total_duration_sec:.1f}s · {len(result.chapters)} chương)."
        )
        self.log.write(f"📦 Thư mục xuất: {result.video_path.parent}", "ok")
        self.log.write(f"   • Video 16:9: {result.video_path.name}", "info")
        self.log.write(f"   • Phụ đề SRT: {result.srt_path.name}", "info")
        self.log.write(f"   • Mô tả Chapters: {result.description_path.name}", "info")
        self.log.write(f"   • Báo cáo bản quyền: {result.report_path.name}", "info")

    def _cancel(self) -> None:
        self.reporter.cancel()
        self.log.write("⏳ Đang yêu cầu dừng sau phân đoạn hiện tại...", "warn")

    def _open_export_dir(self) -> None:
        if self.last_result and self.last_result.video_path.parent.is_dir():
            self.app.open_path(self.last_result.video_path.parent)

    def _open_description_file(self) -> None:
        if self.last_result and self.last_result.description_path.is_file():
            self.app.open_path(self.last_result.description_path)
