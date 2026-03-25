"""Fenêtre modale de configuration."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog
from typing import Callable

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent)
        self._vm = vm
        self.title("Paramètres")
        self.geometry("520x480")
        self.resizable(False, False)
        self.configure(bg=PALETTE["bg"])
        self.grab_set()
        self._build()

    def _build(self) -> None:
        settings = self._vm.settings

        tk.Label(
            self, text="PARAMÈTRES",
            bg=PALETTE["bg"], fg=PALETTE["accent"], font=FONTS["heading"],
        ).pack(anchor="w", padx=20, pady=6)
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=4)

        # ── Kconvars ────────────────────────────────────────────────────
        self._section_label("KCONVARS  (.SV / .VR → .VA)")

        self._kconvars_var = tk.StringVar(value=settings.kconvars_exe)
        self._build_exe_row(
            label="Chemin kconvars.exe",
            var=self._kconvars_var,
            browse_cmd=self._browse_kconvars,
        )

        tk.Label(
            self, text="Timeout conversion VA (secondes)",
            bg=PALETTE["bg"], fg=PALETTE["text"], font=FONTS["body"],
        ).pack(anchor="w", padx=20, pady=(6, 0))
        self._kconvars_timeout_var = tk.IntVar(value=settings.kconvars_timeout)
        ttk.Spinbox(
            self, from_=10, to=600, increment=10,
            textvariable=self._kconvars_timeout_var, width=8,
        ).pack(anchor="w", padx=20, pady=(0, 4))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=8)

        # ── PrintTP ─────────────────────────────────────────────────────
        self._section_label("PRINTTP  (.TP → .LS)")

        tk.Label(
            self,
            text="Chemin PrintTP.exe  (laisser vide = détection automatique)",
            bg=PALETTE["bg"], fg=PALETTE["text_dim"], font=FONTS["small"],
        ).pack(anchor="w", padx=20, pady=(0, 2))

        self._printtp_var = tk.StringVar(value=settings.printtp_exe)
        self._build_exe_row(
            label="Chemin PrintTP.exe",
            var=self._printtp_var,
            browse_cmd=self._browse_printtp,
            show_label=False,
        )

        tk.Label(
            self, text="Timeout conversion TP (secondes)",
            bg=PALETTE["bg"], fg=PALETTE["text"], font=FONTS["body"],
        ).pack(anchor="w", padx=20, pady=(6, 0))
        self._printtp_timeout_var = tk.IntVar(value=settings.printtp_timeout)
        ttk.Spinbox(
            self, from_=10, to=300, increment=10,
            textvariable=self._printtp_timeout_var, width=8,
        ).pack(anchor="w", padx=20, pady=(0, 4))

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=16, pady=8)

        # ── Boutons ─────────────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg=PALETTE["bg"])
        btn_frame.pack(side="bottom", fill="x", padx=16, pady=12)
        ttk.Button(btn_frame, text="Annuler", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(
            btn_frame, text="Enregistrer", style="Accent.TButton",
            command=self._save,
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Builders internes
    # ------------------------------------------------------------------

    def _section_label(self, text: str) -> None:
        tk.Label(
            self, text=text,
            bg=PALETTE["bg"], fg=PALETTE["accent"], font=FONTS["body_med"],
        ).pack(anchor="w", padx=20, pady=(8, 2))

    def _build_exe_row(
        self,
        label: str,
        var: tk.StringVar,
        browse_cmd: "Callable[[], None]",
        show_label: bool = True,
    ) -> None:
        if show_label:
            tk.Label(
                self, text=label,
                bg=PALETTE["bg"], fg=PALETTE["text"], font=FONTS["body"],
            ).pack(anchor="w", padx=20, pady=(6, 0))
        row = tk.Frame(self, bg=PALETTE["bg"])
        row.pack(fill="x", padx=20, pady=2)
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=browse_cmd).pack(
            side="right", padx=(4, 0)
        )

    # ------------------------------------------------------------------
    # Callbacks browse
    # ------------------------------------------------------------------

    def _browse_kconvars(self) -> None:
        path = filedialog.askopenfilename(
            title="Sélectionner kconvars.exe",
            filetypes=[("Exécutables", "*.exe"), ("Tous", "*.*")],
        )
        if path:
            self._kconvars_var.set(path)

    def _browse_printtp(self) -> None:
        path = filedialog.askopenfilename(
            title="Sélectionner PrintTP.exe",
            filetypes=[("Exécutables", "*.exe"), ("Tous", "*.*")],
        )
        if path:
            self._printtp_var.set(path)

    # ------------------------------------------------------------------
    # Sauvegarde
    # ------------------------------------------------------------------

    def _save(self) -> None:
        s = self._vm.settings
        s.kconvars_exe        = self._kconvars_var.get()
        s.kconvars_timeout    = self._kconvars_timeout_var.get()
        s.printtp_exe         = self._printtp_var.get()
        s.printtp_timeout     = self._printtp_timeout_var.get()
        s.save()
        self.destroy()