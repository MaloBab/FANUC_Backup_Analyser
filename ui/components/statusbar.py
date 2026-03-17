"""Barre de statut inférieure : message + barre de progression."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class StatusBar(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], height=32)
        self.pack_propagate(False)
        self._vm = vm
        self._build()

    def _build(self) -> None:
        self._status_var = tk.StringVar(value="Prêt.")
        tk.Label(
            self, textvariable=self._status_var,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"], anchor="w",
        ).pack(side="left", padx=12, pady=6)

        self._progress_var = tk.IntVar(value=0)
        self._progress = ttk.Progressbar(
            self,
            variable=self._progress_var,
            maximum=100,
            length=160,
        )
        self._progress.pack(side="right", padx=12, pady=8)


    def update_status(self, message: str) -> None:
        self._status_var.set(message)

    def update_progress(self, current: int, total: int) -> None:
        if total > 0:
            pct = int(current / total * 100)
            self._progress_var.set(pct)
        else:
            self._progress_var.set(0)