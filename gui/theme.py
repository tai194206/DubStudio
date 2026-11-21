"""Màu sắc và style chung cho toàn bộ giao diện DubStudio (Dark Studio Theme)."""
from __future__ import annotations

from tkinter import ttk

COLORS = {
    "bg": "#0f172a",             # Nền chính tối sâu (Slate 900)
    "surface": "#162032",        # Nền thẻ / khung nổi nhẹ
    "card": "#1e293b",           # Nền tab không chọn / viền thẻ (Slate 800)
    "border": "#334155",         # Viền rõ nét (Slate 700)
    "input": "#0b1220",          # Nền ô nhập liệu / log
    "accent": "#0ea5e9",         # Xanh chủ đạo (Sky 500)
    "accent_hover": "#38bdf8",   # Hover nút chính
    "success": "#10b981",        # Xanh thành công (Emerald 500)
    "success_hover": "#34d399",
    "danger": "#ef4444",         # Đỏ hủy / lỗi (Red 500)
    "danger_hover": "#f87171",
    "warn": "#f59e0b",           # Vàng cảnh báo (Amber 500)
    "neutral": "#334155",        # Xám trung tính cho nút phụ
    "neutral_hover": "#475569",
    "text": "#f1f5f9",           # Chữ chính sáng rõ (Slate 100)
    "muted": "#94a3b8",          # Chữ phụ / chú thích (Slate 400)
}

FONT = ("Segoe UI", 10)
FONT_SM = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 15, "bold")
MONO = ("Consolas", 10)


def apply_theme(root) -> None:
    """Áp dụng Dark Studio Theme đồng bộ cho toàn bộ widget ttk & popup Tk."""
    c = COLORS
    root.configure(bg=c["bg"])

    # Đồng bộ màu danh sách xổ xuống (Combobox Listbox) & Menu chuột phải trên Windows
    root.option_add("*TCombobox*Listbox*Background", c["input"])
    root.option_add("*TCombobox*Listbox*Foreground", c["text"])
    root.option_add("*TCombobox*Listbox*selectBackground", c["accent"])
    root.option_add("*TCombobox*Listbox*selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox*Font", FONT)
    root.option_add("*Menu*Background", c["card"])
    root.option_add("*Menu*Foreground", c["text"])
    root.option_add("*Menu*activeBackground", c["accent"])
    root.option_add("*Menu*activeForeground", "#ffffff")

    st = ttk.Style(root)
    st.theme_use("clam")

    st.configure(
        ".",
        background=c["bg"],
        foreground=c["text"],
        fieldbackground=c["input"],
        bordercolor=c["border"],
        lightcolor=c["card"],
        darkcolor=c["bg"],
        font=FONT,
    )

    # Frame & Card
    st.configure("TFrame", background=c["bg"])
    st.configure("Surface.TFrame", background=c["surface"])
    st.configure("Card.TFrame", background=c["surface"], bordercolor=c["border"], relief="solid")

    st.configure(
        "Card.TLabelframe",
        background=c["surface"],
        bordercolor=c["border"],
        borderwidth=1,
        relief="solid",
    )
    st.configure(
        "Card.TLabelframe.Label",
        background=c["surface"],
        foreground=c["accent_hover"],
        font=FONT_BOLD,
    )

    # Label
    st.configure("TLabel", background=c["bg"], foreground=c["text"])
    st.configure("Card.TLabel", background=c["surface"], foreground=c["text"])
    st.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=FONT_SM)
    st.configure("CardMuted.TLabel", background=c["surface"], foreground=c["muted"], font=FONT_SM)
    st.configure("Title.TLabel", background=c["bg"], foreground=c["text"], font=FONT_TITLE)
    st.configure("Accent.TLabel", background=c["bg"], foreground=c["accent_hover"], font=FONT_BOLD)

    # Checkbutton & Radiobutton
    st.configure("TCheckbutton", background=c["bg"], foreground=c["text"], font=FONT)
    st.map(
        "TCheckbutton",
        background=[("active", c["bg"])],
        foreground=[("disabled", c["muted"]), ("active", "#ffffff")],
        indicatorcolor=[("selected", c["accent"]), ("!selected", c["input"])],
    )
    st.configure("Card.TCheckbutton", background=c["surface"], foreground=c["text"], font=FONT)
    st.map(
        "Card.TCheckbutton",
        background=[("active", c["surface"])],
        foreground=[("disabled", c["muted"]), ("active", "#ffffff")],
        indicatorcolor=[("selected", c["accent"]), ("!selected", c["input"])],
    )

    # Entry & Combobox
    st.configure(
        "TEntry",
        fieldbackground=c["input"],
        foreground=c["text"],
        insertcolor=c["accent_hover"],
        bordercolor=c["border"],
        padding=(7, 5),
    )
    st.map(
        "TEntry",
        bordercolor=[("focus", c["accent"]), ("!focus", c["border"])],
        fieldbackground=[("disabled", c["card"])],
        foreground=[("disabled", c["muted"])],
    )

    st.configure(
        "TCombobox",
        fieldbackground=c["input"],
        background=c["card"],
        foreground=c["text"],
        arrowcolor=c["text"],
        bordercolor=c["border"],
        padding=(7, 5),
    )
    st.map(
        "TCombobox",
        fieldbackground=[("readonly", c["input"]), ("disabled", c["card"])],
        foreground=[("readonly", c["text"]), ("disabled", c["muted"])],
        bordercolor=[("focus", c["accent"]), ("!focus", c["border"])],
        arrowcolor=[("disabled", c["muted"]), ("active", c["accent_hover"])],
    )

    # Scale, Separator, Panedwindow, Scrollbar
    st.configure("Horizontal.TScale", background=c["bg"], troughcolor=c["input"], sliderthickness=14)
    st.layout("Horizontal.Card.TScale", st.layout("Horizontal.TScale"))
    st.configure("Horizontal.Card.TScale", background=c["surface"], troughcolor=c["input"], sliderthickness=14)
    st.configure("TSeparator", background=c["border"])
    st.configure("TPanedwindow", background=c["bg"])
    st.configure("Sash", sashthickness=5, gripcount=0)

    st.configure(
        "Vertical.TScrollbar",
        background=c["card"],
        troughcolor=c["bg"],
        bordercolor=c["bg"],
        arrowcolor=c["muted"],
        width=11,
    )
    st.map("Vertical.TScrollbar", background=[("active", c["border"]), ("pressed", c["accent"])])

    # Notebook chính (trên cùng)
    st.configure("TNotebook", background=c["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure(
        "TNotebook.Tab",
        background=c["card"],
        foreground=c["muted"],
        padding=(18, 9),
        font=FONT_BOLD,
        borderwidth=0,
    )
    st.map(
        "TNotebook.Tab",
        background=[("selected", c["accent"]), ("active", c["neutral_hover"])],
        foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
        expand=[("selected", (0, 2, 0, 0))],
    )

    # Notebook con (bên trong các tab như Story Studio / Dub Studio)
    st.configure("Sub.TNotebook", background=c["bg"], borderwidth=0)
    st.configure(
        "Sub.TNotebook.Tab",
        background=c["surface"],
        foreground=c["muted"],
        padding=(12, 6),
        font=FONT_SM,
        borderwidth=0,
    )
    st.map(
        "Sub.TNotebook.Tab",
        background=[("selected", c["card"]), ("active", c["neutral"])],
        foreground=[("selected", c["accent_hover"]), ("active", "#ffffff")],
    )

    # Hệ thống nút bấm có hover rõ ràng
    btn_specs = (
        ("Primary", c["accent"], c["accent_hover"]),
        ("Success", c["success"], c["success_hover"]),
        ("Danger", c["danger"], c["danger_hover"]),
        ("Neutral", c["neutral"], c["neutral_hover"]),
    )
    for name, bg_col, hover_col in btn_specs:
        st.configure(
            f"{name}.TButton",
            background=bg_col,
            foreground="#ffffff",
            padding=(13, 7),
            font=FONT_BOLD,
            borderwidth=0,
            focusthickness=0,
        )
        st.map(
            f"{name}.TButton",
            background=[("disabled", "#1e293b"), ("pressed", bg_col), ("active", hover_col)],
            foreground=[("disabled", "#64748b"), ("active", "#ffffff")],
        )

    # Nút gọn cho các dòng chọn file / làm mới
    st.configure(
        "Compact.TButton",
        background=c["neutral"],
        foreground="#ffffff",
        padding=(10, 5),
        font=FONT_SM,
        borderwidth=0,
    )
    st.map(
        "Compact.TButton",
        background=[("disabled", "#1e293b"), ("active", c["neutral_hover"])],
        foreground=[("disabled", "#64748b")],
    )