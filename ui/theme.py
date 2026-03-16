"""
Thème visuel global — palette industrielle précision.
Centralise couleurs, polices et styles ttk.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk


# ---------------------------------------------------------------------------
# Palette  — dark industrial, accents amber
# ---------------------------------------------------------------------------

PALETTE = {
    # Fonds
    "bg":           "#0f1117",   # fond principal très sombre
    "bg_panel":     "#161b24",   # panneaux latéraux
    "bg_card":      "#1c2233",   # cartes / treeview
    "bg_input":     "#232d3f",   # champs de saisie
    "bg_hover":     "#2a3650",   # survol
    "bg_selected":  "#1a3a5c",   # sélection
    # Accents
    "accent":       "#f59e0b",   # ambre — actions principales
    "accent_dim":   "#92650a",   # ambre atténué
    "accent_hover": "#fbbf24",   # ambre survol
    "accent_text":  "#0f1117",   # texte sur fond ambre
    # Sémantique
    "success":      "#10b981",
    "warning":      "#f59e0b",
    "error":        "#ef4444",
    "info":         "#3b82f6",
    # Texte
    "text":         "#e2e8f0",
    "text_dim":     "#64748b",
    "text_muted":   "#334155",
    # Frontières
    "border":       "#1e293b",
    "border_bright":"#334155",
    "separator":    "#1e293b",
    # Tags spéciaux
    "karel_fg":     "#f59e0b",
    "uninit_fg":    "#ef4444",
    "system_fg":    "#3b82f6",
}

FONTS = {
    "title":    ("Consolas", 13, "bold"),
    "heading":  ("Consolas", 10, "bold"),
    "body":     ("Segoe UI",  10),
    "body_med": ("Segoe UI",  10, "bold"),
    "mono":     ("Consolas",  10),
    "mono_sm":  ("Consolas",   9),
    "small":    ("Segoe UI",   9),
    "tag":      ("Consolas",   8, "bold"),
    "detail":   ("Consolas",  11),
}


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=PALETTE["bg"])
    _configure_ttk_styles()


def _configure_ttk_styles() -> None:
    style = ttk.Style()
    style.theme_use("clam")
    P = PALETTE

    # ── Frames ────────────────────────────────────────────────────────
    style.configure("TFrame",       background=P["bg"])
    style.configure("Panel.TFrame", background=P["bg_panel"])
    style.configure("Card.TFrame",  background=P["bg_card"])

    # ── Labels ────────────────────────────────────────────────────────
    style.configure("TLabel",
                    background=P["bg"], foreground=P["text"],
                    font=FONTS["body"])
    style.configure("Title.TLabel",
                    background=P["bg_panel"], foreground=P["accent"],
                    font=FONTS["title"])
    style.configure("Heading.TLabel",
                    background=P["bg_panel"], foreground=P["text"],
                    font=FONTS["heading"])
    style.configure("Dim.TLabel",
                    background=P["bg_panel"], foreground=P["text_dim"],
                    font=FONTS["small"])
    style.configure("Accent.TLabel",
                    background=P["bg"], foreground=P["accent"],
                    font=FONTS["heading"])
    style.configure("Mono.TLabel",
                    background=P["bg_card"], foreground=P["text"],
                    font=FONTS["mono"])
    style.configure("Tag.TLabel",
                    background=P["bg_input"], foreground=P["text_dim"],
                    font=FONTS["tag"], padding=(4, 2))

    # ── Boutons ───────────────────────────────────────────────────────
    style.configure("TButton",
                    background=P["bg_input"], foreground=P["text"],
                    relief="flat", padding=(12, 6),
                    font=FONTS["body"], borderwidth=0)
    style.map("TButton",
              background=[("active", P["bg_hover"]), ("pressed", P["bg_selected"])],
              foreground=[("active", P["text"])])

    style.configure("Accent.TButton",
                    background=P["accent"], foreground=P["accent_text"],
                    relief="flat", padding=(14, 7),
                    font=FONTS["body_med"], borderwidth=0)
    style.map("Accent.TButton",
              background=[("active", P["accent_hover"]), ("pressed", P["accent_dim"])],
              foreground=[("active", P["accent_text"])])

    style.configure("Danger.TButton",
                    background=P["bg_input"], foreground=P["error"],
                    relief="flat", padding=(12, 6),
                    font=FONTS["body"], borderwidth=0)
    style.map("Danger.TButton",
              background=[("active", P["bg_hover"])],
              foreground=[("active", "#ff6b6b")])

    style.configure("Ghost.TButton",
                    background=P["bg_panel"], foreground=P["text_dim"],
                    relief="flat", padding=(8, 4),
                    font=FONTS["small"], borderwidth=0)
    style.map("Ghost.TButton",
              background=[("active", P["bg_input"])],
              foreground=[("active", P["text"])])

    # ── Entry ─────────────────────────────────────────────────────────
    style.configure("TEntry",
                    fieldbackground=P["bg_input"],
                    foreground=P["text"],
                    insertcolor=P["accent"],
                    bordercolor=P["border_bright"],
                    selectbackground=P["bg_selected"],
                    relief="flat", padding=(6, 4))

    # ── Treeview ──────────────────────────────────────────────────────
    style.configure("Treeview",
                    background=P["bg_card"],
                    fieldbackground=P["bg_card"],
                    foreground=P["text"],
                    rowheight=28,
                    font=FONTS["mono"],
                    borderwidth=0,
                    relief="flat")
    style.configure("Treeview.Heading",
                    background=P["bg_input"],
                    foreground=P["text_dim"],
                    relief="flat",
                    font=FONTS["tag"],
                    padding=(6, 5))
    style.map("Treeview",
              background=[("selected", P["bg_selected"])],
              foreground=[("selected", P["accent"])])
    style.map("Treeview.Heading",
              background=[("active", P["bg_hover"])],
              foreground=[("active", P["text"])])

    # ── Scrollbar ─────────────────────────────────────────────────────
    style.configure("TScrollbar",
                    background=P["bg_panel"],
                    troughcolor=P["bg"],
                    arrowcolor=P["text_muted"],
                    bordercolor=P["bg"],
                    relief="flat", arrowsize=12)
    style.map("TScrollbar",
              background=[("active", P["bg_input"])])

    # ── Progressbar ───────────────────────────────────────────────────
    style.configure("TProgressbar",
                    background=P["accent"],
                    troughcolor=P["bg_input"],
                    bordercolor=P["bg_input"],
                    thickness=3)

    # ── Notebook ──────────────────────────────────────────────────────
    style.configure("TNotebook",
                    background=P["bg_panel"],
                    bordercolor=P["border"],
                    tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab",
                    background=P["bg_panel"],
                    foreground=P["text_dim"],
                    padding=(16, 7),
                    font=FONTS["body"])
    style.map("TNotebook.Tab",
              background=[("selected", P["bg_card"])],
              foreground=[("selected", P["text"])],
              expand=[("selected", (0, 0, 0, 0))])

    # ── Separator / Radiobutton ────────────────────────────────────────
    style.configure("TSeparator",   background=P["separator"])
    style.configure("TRadiobutton",
                    background=P["bg_panel"], foreground=P["text"],
                    font=FONTS["body"])
    style.map("TRadiobutton",
              background=[("active", P["bg_panel"])],
              foreground=[("active", P["accent"])])
    style.configure("TCheckbutton",
                    background=P["bg_panel"], foreground=P["text"],
                    font=FONTS["body"])
    style.map("TCheckbutton",
              background=[("active", P["bg_panel"])],
              foreground=[("active", P["accent"])])
    style.configure("TSpinbox",
                    background=P["bg_input"], foreground=P["text"],
                    fieldbackground=P["bg_input"],
                    insertcolor=P["accent"],
                    font=FONTS["body"])