"""Fenêtre modale de configuration."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent)
        self._vm = vm
        self.title("Paramètres")
        self.geometry("500x320")
        self.resizable(False, False)
        self.configure(bg=PALETTE["bg"])
        self.grab_set()
        self._build()

    def _build(self) -> None:
        settings = self._vm.settings

        tk.Label(self, text="PARAMÈTRES", bg=PALETTE["bg"],
                 fg=PALETTE["accent"], font=FONTS["heading"]).pack(anchor="w", padx=20, pady=6)
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=4)

        tk.Label(self, text="Chemin Kconvars",
                 bg=PALETTE["bg"], fg=PALETTE["text"], font=FONTS["body"]).pack(anchor="w", padx=20, pady=6)

        row = tk.Frame(self, bg=PALETTE["bg"])
        row.pack(fill="x", padx=20, pady=2)
        self._rg_var = tk.StringVar(value=settings.kconvars_exe)
        ttk.Entry(row, textvariable=self._rg_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3,
                   command=self._browse_exe).pack(side="right", padx=(4, 0))

        tk.Label(self, text="Timeout conversion (secondes)",
                 bg=PALETTE["bg"], fg=PALETTE["text"], font=FONTS["body"]).pack(anchor="w", padx=20, pady=6)
        self._timeout_var = tk.IntVar(value=settings.kconvars_timeout)
        ttk.Spinbox(self, from_=10, to=600, increment=10,
                    textvariable=self._timeout_var, width=8).pack(anchor="w", padx=20)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=12)

        btn_frame = tk.Frame(self, bg=PALETTE["bg"])
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)

        ttk.Button(btn_frame, text="Annuler", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Enregistrer", style="Accent.TButton",
                   command=self._save).pack(side="right")

    def _browse_exe(self) -> None:
        path = filedialog.askopenfilename(
            title="Sélectionner l'exécutable Kconvars",
            filetypes=[("Exécutables", "*.exe"), ("Tous", "*.*")],
        )
        if path:
            self._rg_var.set(path)

    def _save(self) -> None:
        s = self._vm.settings
        s.kconvars_exe = self._rg_var.get()
        s.kconvars_timeout = self._timeout_var.get()
        s.save()
        self.destroy()