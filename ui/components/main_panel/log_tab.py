"""
LogTab — onglet journal de l'application.

Affiche les messages horodatés avec coloration par niveau.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime

from ui.theme import PALETTE, FONTS

_LOG_COLORS: dict[str, str] = {
    "info":    PALETTE["info"],
    "success": PALETTE["success"],
    "warning": PALETTE["warning"],
    "error":   PALETTE["error"],
}


class LogTab(tk.Frame):
    """Zone de journal avec messages horodatés et bouton Effacer."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, bg=PALETTE["bg_card"])
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._text = tk.Text(
            self,
            bg=PALETTE["bg_card"], fg=PALETTE["text"],
            font=FONTS["mono"],       # type: ignore[arg-type]
            wrap="none", state="disabled", relief="flat",
            padx=12, pady=8,
        )
        vsb = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=vsb.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        for level, color in _LOG_COLORS.items():
            self._text.tag_configure(level, foreground=color)

        footer = tk.Frame(self, bg=PALETTE["bg_panel"])
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(footer, text="🗑 Effacer", command=self.clear).pack(
            side="right", padx=12, pady=4,
        )

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def append(self, message: str, level: str = "info") -> None:
        """Ajoute une ligne horodatée au journal."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._text.configure(state="normal")
        self._text.insert("end", f"[{ts}] {message}\n", level)
        self._text.see("end")
        self._text.configure(state="disabled")

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")