"""Màu sắc và style chung cho toàn bộ giao diện."""
from tkinter import ttk

COLORS = {
    "bg": "#1e272e", "card": "#2c3e50", "input": "#222f3e", "accent": "#3498db",
    "success": "#27ae60", "danger": "#e74c3c", "warn": "#f39c12",
    "text": "#ecf0f1", "muted": "#95a5a6",
}
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
MONO = ("Consolas", 10)


def apply_theme(root) -> None:
    c = COLORS
    root.configure(bg=c["bg"])
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=c["bg"], foreground=c["text"], fieldbackground=c["input"],
                 bordercolor=c["card"], lightcolor=c["card"], darkcolor=c["card"], font=FONT)
    st.configure("TFrame", background=c["bg"])
    st.configure("Card.TLabelframe", background=c["bg"], bordercolor=c["card"], relief="solid")
    st.configure("Card.TLabelframe.Label", background=c["bg"], foreground=c["accent"], font=FONT_BOLD)
    st.configure("TLabel", background=c["bg"], foreground=c["text"])
    st.configure("Muted.TLabel", foreground=c["muted"])
    st.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
    st.map("TCheckbutton", background=[("active", c["bg"])])
    st.configure("TEntry", fieldbackground=c["input"], foreground=c["text"], insertcolor=c["text"])
    st.configure("TCombobox", fieldbackground=c["input"], foreground=c["text"], arrowcolor=c["text"])
    st.map("TCombobox", fieldbackground=[("readonly", c["input"])], foreground=[("readonly", c["text"])])
    st.configure("TScale", background=c["bg"], troughcolor=c["input"])
    st.configure("TNotebook", background=c["bg"], borderwidth=0)
    st.configure("TNotebook.Tab", background=c["card"], foreground=c["text"], padding=(16, 8), font=FONT_BOLD)
    st.map("TNotebook.Tab", background=[("selected", c["accent"])], foreground=[("selected", "white")])
    for name, color in (("Primary", c["accent"]), ("Success", c["success"]), ("Danger", c["danger"]),
                        ("Neutral", "#566573")):
        st.configure(f"{name}.TButton", background=color, foreground="white", padding=(12, 7),
                     font=FONT_BOLD, borderwidth=0)
        st.map(f"{name}.TButton", background=[("disabled", "#4b5563"), ("active", color)],
               foreground=[("disabled", "#9ca3af")])
