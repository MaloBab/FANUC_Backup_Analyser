"""Barre de titre avec logo et actions globales."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class HeaderBar(tk.Frame):

    HEIGHT = 50

    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], height=self.HEIGHT)
        self._vm = vm
        self.pack_propagate(False)
        self._build()
        self._accent_line = tk.Frame(self, bg=PALETTE["accent"], height=2)
        self._accent_line.pack(side="bottom", fill="x")

    def _build(self) -> None:
        logo_frame = tk.Frame(self, bg=PALETTE["bg_panel"])
        logo_frame.pack(side="left", padx=(18, 0))

        tk.Label(
            logo_frame, text="◈",
            bg=PALETTE["bg_panel"], fg=PALETTE["accent"],
            font=("Consolas", 16),
        ).pack(side="left", pady=12)

        tk.Label(
            logo_frame, text="  FANUC",
            bg=PALETTE["bg_panel"], fg=PALETTE["text"],
            font=FONTS["title"],
        ).pack(side="left")

        tk.Label(
            logo_frame, text=" Variable Extractor",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=("Segoe UI", 10),
        ).pack(side="left")

        tk.Label(
            self, text="v1.0",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_muted"],
            font=FONTS["tag"],
        ).pack(side="left", padx=12)

        ttk.Button(
            self, text="⚙  Paramètres",
            style="Ghost.TButton",
            command=self._open_settings,
        ).pack(side="right", padx=16, pady=10)

    def _open_settings(self) -> None:
        from ui.components.settings_dialog import SettingsDialog
        SettingsDialog(self, self._vm)