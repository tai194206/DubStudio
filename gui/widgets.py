"""Các widget dùng lại: thanh tiến độ gradient, khung log, ô chọn file, thanh trượt, khung cuộn."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from .theme import COLORS, FONT, FONT_BOLD, FONT_SM, MONO


def _lerp(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class GradientProgressBar(tk.Canvas):
    """Thanh tiến độ chuyển sắc mượt có viền mảnh và chữ phần trăm rõ nét."""

    def __init__(self, parent, height: int = 24, c1: str = "#0ea5e9", c2: str = "#10b981"):
        super().__init__(
            parent,
            height=height,
            bg=COLORS["input"],
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            bd=0,
        )
        self.c1, self.c2, self._pct = c1, c2, 0.0
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, pct: float) -> None:
        self._pct = max(0.0, min(100.0, float(pct)))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2 or h <= 2:
            return
        fw = int(w * self._pct / 100.0)
        if fw > 0:
            step = 4
            for x in range(0, fw, step):
                col = _lerp(self.c1, self.c2, x / max(w, 1))
                self.create_rectangle(x, 0, min(x + step, fw), h, fill=col, width=0)
        self.create_text(
            w / 2,
            h / 2,
            text=f"{self._pct:.0f}%",
            fill="#ffffff",
            font=("Segoe UI", 9, "bold"),
        )


class LogBox(ScrolledText):
    """Khung nhật ký phân màu theo mức độ, hỗ trợ chuột phải sao chép / xóa nhanh."""

    TAG_COLORS = {
        "info": COLORS["text"],
        "ok": "#34d399",
        "warn": COLORS["warn"],
        "error": "#f87171",
        "accent": COLORS["accent_hover"],
        "muted": COLORS["muted"],
    }

    def __init__(self, parent, height: int = 8):
        super().__init__(
            parent,
            height=height,
            bg=COLORS["input"],
            fg=COLORS["text"],
            font=MONO,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"],
            state="disabled",
            wrap="word",
            insertbackground=COLORS["text"],
        )
        for level, color in self.TAG_COLORS.items():
            self.tag_config(level, foreground=color)

        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="📋 Sao chép toàn bộ log", command=self.copy_all)
        self._menu.add_separator()
        self._menu.add_command(label="🗑️ Xóa nhật ký", command=self.clear)
        self.bind("<Button-3>", self._show_menu)

    def _show_menu(self, event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def copy_all(self) -> None:
        text = self.get("1.0", "end-1c")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def write(self, msg: str, level: str = "info") -> None:
        self.config(state="normal")
        self.insert("end", msg + "\n", level if level in self.TAG_COLORS else "info")
        self.see("end")
        self.config(state="disabled")

    def clear(self) -> None:
        self.config(state="normal")
        self.delete("1.0", "end")
        self.config(state="disabled")


class FileRow(ttk.Frame):
    """Nhãn + ô nhập đường dẫn + nút Chọn. mode: open | save | dir."""

    def __init__(
        self,
        parent,
        label: str,
        variable: tk.StringVar,
        mode: str = "open",
        filetypes=None,
        label_width: int = 16,
        in_card: bool = True,
    ):
        super().__init__(parent, style="Surface.TFrame" if in_card else "TFrame")
        self.var, self.mode = variable, mode
        self.filetypes = filetypes or [("Tất cả", "*.*")]
        lbl_style = "Card.TLabel" if in_card else "TLabel"

        ttk.Label(self, text=label, width=label_width, style=lbl_style).pack(side="left")
        ttk.Entry(self, textvariable=variable).pack(side="left", fill="x", expand=True, padx=(4, 6))
        ttk.Button(self, text="Chọn…", style="Compact.TButton", command=self._pick).pack(side="left")

    def _pick(self) -> None:
        if self.mode == "dir":
            p = filedialog.askdirectory()
        elif self.mode == "save":
            p = filedialog.asksaveasfilename(filetypes=self.filetypes)
        else:
            p = filedialog.askopenfilename(filetypes=self.filetypes)
        if p:
            self.var.set(p)


class LabeledScale(ttk.Frame):
    """Thanh trượt kèm nhãn và giá trị số tự cập nhật theo biến."""

    def __init__(
        self,
        parent,
        label: str,
        variable: tk.DoubleVar,
        lo: float,
        hi: float,
        fmt: str = "{:.0%}",
        label_width: int = 18,
        in_card: bool = True,
    ):
        super().__init__(parent, style="Surface.TFrame" if in_card else "TFrame")
        self._var = variable
        self._fmt = fmt
        lbl_style = "Card.TLabel" if in_card else "TLabel"
        scale_style = "Card.TScale" if in_card else "TScale"

        ttk.Label(self, text=label, width=label_width, style=lbl_style).pack(side="left")
        self._val = ttk.Label(self, width=7, anchor="e", style=lbl_style, font=FONT_BOLD)
        self._val.pack(side="right")
        ttk.Scale(
            self,
            from_=lo,
            to=hi,
            variable=variable,
            style=scale_style,
            command=self._on_slide,
        ).pack(side="left", fill="x", expand=True, padx=6)

        self._update_label()
        variable.trace_add("write", lambda *_: self._update_label())

    def _on_slide(self, v: str) -> None:
        try:
            self._val.config(text=self._fmt.format(float(v)))
        except (ValueError, tk.TclError):
            pass

    def _update_label(self) -> None:
        try:
            self._val.config(text=self._fmt.format(float(self._var.get())))
        except (ValueError, tk.TclError):
            pass


class StatusBadge(tk.Label):
    """Thẻ trạng thái nhỏ gọn (ok / warn / error / info) dùng cho thanh tóm tắt."""

    PALETTE = {
        "ok": ("#064e3b", "#6ee7b7"),
        "warn": ("#451a03", "#fcd34d"),
        "error": ("#450a0a", "#fca5a5"),
        "info": ("#0c4a6e", "#7dd3fc"),
        "muted": (COLORS["card"], COLORS["muted"]),
    }

    def __init__(self, parent, text: str = "", kind: str = "info"):
        bg, fg = self.PALETTE.get(kind, self.PALETTE["info"])
        super().__init__(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=FONT_SM,
            padx=9,
            pady=3,
            relief="flat",
            bd=0,
        )

    def set_status(self, text: str, kind: str = "info") -> None:
        bg, fg = self.PALETTE.get(kind, self.PALETTE["info"])
        self.config(text=text, bg=bg, fg=fg)


class ScrollableFrame(ttk.Frame):
    """Khung chứa có thanh cuộn dọc tự thích ứng kích thước (tránh tràn nút trên màn hình nhỏ)."""

    def __init__(self, parent, padding: int = 4):
        super().__init__(parent)
        self._canvas = tk.Canvas(self, bg=COLORS["bg"], highlightthickness=0, bd=0)
        self._vbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self.body = ttk.Frame(self._canvas, padding=padding)

        self._win_id = self._canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas.configure(yscrollcommand=self._vbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        self._vbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _on_body_configure(self, _e) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self._canvas.itemconfigure(self._win_id, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if self._canvas.bbox("all") and self._canvas.bbox("all")[3] > self._canvas.winfo_height():
            delta = int(-1 * (event.delta / 120)) if event.delta else 0
            if delta:
                self._canvas.yview_scroll(delta, "units")

    def _bind_wheel(self, _e) -> None:
        self._canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_wheel(self, _e) -> None:
        self._canvas.unbind_all("<MouseWheel>")


def row(parent, label: str, widget_factory, label_width: int = 16, in_card: bool = True, pady: int = 3):
    """Một dòng `nhãn + widget` chuẩn bố cục phẳng."""
    f = ttk.Frame(parent, style="Surface.TFrame" if in_card else "TFrame")
    lbl_style = "Card.TLabel" if in_card else "TLabel"
    ttk.Label(f, text=label, width=label_width, style=lbl_style).pack(side="left")
    w = widget_factory(f)
    w.pack(side="left", fill="x", expand=True)
    f.pack(fill="x", pady=pady)
    return w