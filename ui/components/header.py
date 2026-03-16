"""Barre de titre avec logo et actions globales."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class HeaderBar(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], height=52)
        self._vm = vm
        self.pack_propagate(False)
        self._build()

    def _build(self) -> None:
        # Logo / titre
        tk.Label(
            self, text="⚙  FANUC Variable Extractor",
            bg=PALETTE["bg_panel"], fg=PALETTE["accent"],
            font=FONTS["title"], padx=20,
        ).pack(side="left", pady=10)

        # Séparateur vertical
        ttk.Separator(self, orient="vertical").pack(side="left", fill="y", pady=8)

        # Tag version
        tk.Label(
            self, text="v1.0",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"], padx=10,
        ).pack(side="left")

        # Bouton Paramètres (droite)
        ttk.Button(
            self, text="⚙ Paramètres",
            command=self._open_settings,
        ).pack(side="right", padx=16, pady=10)

    def _open_settings(self) -> None:
        from ui.components.settings_dialog import SettingsDialog
        SettingsDialog(self, self._vm)