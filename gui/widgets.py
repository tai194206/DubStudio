"""Các widget dùng lại: thanh tiến độ gradient, khung log, ô chọn file, thanh trượt có nhãn."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

from .theme import COLORS, FONT, MONO


def _lerp(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class GradientProgressBar(tk.Canvas):
    def __init__(self, parent, height: int = 26, c1: str = "#3498db", c2: str = "#2ecc71"):
        super().__init__(parent, height=height, bg=COLORS["input"], highlightthickness=0)
        self.c1, self.c2, self._pct = c1, c2, 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, pct: float) -> None:
        self._pct = max(0.0, min(100.0, pct))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        fw = int(w * self._pct / 100)
        for x in range(0, fw, 3):
            self.create_rectangle(x, 0, min(x + 3, fw), h, fill=_lerp(self.c1, self.c2, x / max(w, 1)), width=0)
        self.create_text(w / 2, h / 2, text=f"{self._pct:.0f}%", fill="white", font=("Segoe UI", 9, "bold"))


class LogBox(ScrolledText):
    COLORS = {"info": COLORS["text"], "ok": "#2ecc71", "warn": COLORS["warn"], "error": COLORS["danger"]}

    def __init__(self, parent, height: int = 8):
        super().__init__(parent, height=height, bg=COLORS["input"], fg=COLORS["text"], font=MONO,
                         relief="flat", state="disabled", wrap="word", insertbackground=COLORS["text"])
        for level, color in self.COLORS.items():
            self.tag_config(level, foreground=color)

    def write(self, msg: str, level: str = "info") -> None:
        self.config(state="normal")
        self.insert("end", msg + "\n", level if level in self.COLORS else "info")
        self.see("end")
        self.config(state="disabled")

    def clear(self) -> None:
        self.config(state="normal")
        self.delete("1.0", "end")
        self.config(state="disabled")


class FileRow(ttk.Frame):
    """Nhãn + ô nhập + nút Chọn. mode: open | save | dir."""

    def __init__(self, parent, label: str, variable: tk.StringVar, mode: str = "open",
                 filetypes=None, label_width: int = 16):
        super().__init__(parent)
        self.var, self.mode, self.filetypes = variable, mode, filetypes or [("Tất cả", "*.*")]
        ttk.Label(self, text=label, width=label_width).pack(side="left")
        ttk.Entry(self, textvariable=variable).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(self, text="Chọn…", style="Neutral.TButton", command=self._pick).pack(side="left")

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
    def __init__(self, parent, label: str, variable: tk.DoubleVar, lo: float, hi: float, fmt: str = "{:.0%}"):
        super().__init__(parent)
        ttk.Label(self, text=label, width=18).pack(side="left")
        self._val = ttk.Label(self, width=6, anchor="e")
        self._val.pack(side="right")
        ttk.Scale(self, from_=lo, to=hi, variable=variable, command=lambda v: self._val.config(
            text=fmt.format(float(v)))).pack(side="left", fill="x", expand=True, padx=4)
        self._val.config(text=fmt.format(variable.get()))


def row(parent, label: str, widget_factory, label_width: int = 16):
    """Một dòng `nhãn + widget`."""
    f = ttk.Frame(parent)
    ttk.Label(f, text=label, width=label_width).pack(side="left")
    w = widget_factory(f)
    w.pack(side="left", fill="x", expand=True)
    f.pack(fill="x", pady=2)
    return w
