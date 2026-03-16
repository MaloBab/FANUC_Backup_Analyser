"""
Thème visuel global de l'application.
Centralise couleurs, polices et styles ttk.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

PALETTE = {
    "bg":           "#1a1d23",
    "bg_panel":     "#21252d",
    "bg_card":      "#272b35",
    "bg_input":     "#2e3340",
    "accent":       "#00aaff",
    "accent_hover": "#33bbff",
    "success":      "#00cc88",
    "warning":      "#ffaa00",
    "error":        "#ff4466",
    "text":         "#e8ecf0",
    "text_dim":     "#7a8394",
    "border":       "#363c4a",
    "separator":    "#2a2f3a",
}

FONTS = {
    "title":   ("Consolas", 14, "bold"),
    "heading": ("Consolas", 11, "bold"),
    "body":    ("Segoe UI", 10),
    "mono":    ("Consolas", 10),
    "small":   ("Segoe UI", 9),
    "tag":     ("Consolas", 9, "bold"),
}


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=PALETTE["bg"])
    _configure_ttk_styles()


def _configure_ttk_styles() -> None:
    style = ttk.Style()
    style.theme_use("clam")

    P = PALETTE

    # Frame générique
    style.configure("TFrame", background=P["bg"])
    style.configure("Card.TFrame", background=P["bg_card"])
    style.configure("Panel.TFrame", background=P["bg_panel"])

    # Labels
    style.configure("TLabel",
                    background=P["bg"], foreground=P["text"],
                    font=FONTS["body"])
    style.configure("Title.TLabel",
                    background=P["bg"], foreground=P["text"],
                    font=FONTS["title"])
    style.configure("Heading.TLabel",
                    background=P["bg_panel"], foreground=P["text"],
                    font=FONTS["heading"])
    style.configure("Dim.TLabel",
                    background=P["bg"], foreground=P["text_dim"],
                    font=FONTS["small"])
    style.configure("Accent.TLabel",
                    background=P["bg"], foreground=P["accent"],
                    font=FONTS["heading"])

    # Boutons
    style.configure("TButton",
                    background=P["bg_card"], foreground=P["text"],
                    relief="flat", padding=(12, 6),
                    font=FONTS["body"])
    style.map("TButton",
              background=[("active", P["bg_input"]), ("pressed", P["accent"])],
              foreground=[("active", P["text"])])

    style.configure("Accent.TButton",
                    background=P["accent"], foreground="#ffffff",
                    relief="flat", padding=(14, 7),
                    font=FONTS["heading"])
    style.map("Accent.TButton",
              background=[("active", P["accent_hover"]), ("pressed", P["accent"])])

    style.configure("Danger.TButton",
                    background=P["error"], foreground="#ffffff",
                    relief="flat", padding=(12, 6))
    style.map("Danger.TButton",
              background=[("active", "#ff6680")])

    # Entry
    style.configure("TEntry",
                    fieldbackground=P["bg_input"],
                    foreground=P["text"],
                    insertcolor=P["accent"],
                    bordercolor=P["border"],
                    relief="flat")

    # Treeview (tableau de résultats)
    style.configure("Treeview",
                    background=P["bg_card"],
                    fieldbackground=P["bg_card"],
                    foreground=P["text"],
                    rowheight=26,
                    font=FONTS["mono"])
    style.configure("Treeview.Heading",
                    background=P["bg_input"],
                    foreground=P["accent"],
                    relief="flat",
                    font=FONTS["heading"])
    style.map("Treeview",
              background=[("selected", P["accent"])],
              foreground=[("selected", "#ffffff")])

    # Scrollbar
    style.configure("TScrollbar",
                    background=P["bg_panel"],
                    troughcolor=P["bg"],
                    arrowcolor=P["text_dim"],
                    relief="flat")

    # Progressbar
    style.configure("TProgressbar",
                    background=P["accent"],
                    troughcolor=P["bg_input"],
                    thickness=4)

    # Notebook
    style.configure("TNotebook",
                    background=P["bg_panel"],
                    bordercolor=P["border"])
    style.configure("TNotebook.Tab",
                    background=P["bg_panel"],
                    foreground=P["text_dim"],
                    padding=(14, 6),
                    font=FONTS["body"])
    style.map("TNotebook.Tab",
              background=[("selected", P["bg_card"])],
              foreground=[("selected", P["text"])])

    # Séparateur
    style.configure("TSeparator", background=P["separator"])