"""
Panneau latéral gauche : sélection du dossier, options, lancement.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class SidebarPanel(tk.Frame):
    WIDTH = 280

    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], width=self.WIDTH)
        self.pack_propagate(False)
        self._vm = vm
        self._build()

    # ------------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 16, "pady": 6}

        # ── Section : Dossier source ──────────────────────────────────
        self._section_label("DOSSIER SOURCE")

        self._input_var = tk.StringVar(value=self._vm.settings.last_input_dir)
        self._entry_input = self._path_row(
            self._input_var,
            placeholder="Choisir un dossier…",
            command=self._browse_input,
        )

        # ── Section : Dossier de sortie ───────────────────────────────
        self._section_label("DOSSIER DE SORTIE  (optionnel)")

        self._output_var = tk.StringVar(value=self._vm.settings.last_output_dir)
        self._entry_output = self._path_row(
            self._output_var,
            placeholder="Dossier temporaire auto",
            command=self._browse_output,
        )

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        # ── Section : Filtres ─────────────────────────────────────────
        self._section_label("TYPES DE VARIABLES")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        # ── Boutons d'action ──────────────────────────────────────────
        ttk.Button(
            self, text="▶  Lancer l'extraction",
            style="Accent.TButton",
            command=self._start,
        ).pack(fill="x", padx=16, pady=(0, 8))

        ttk.Button(
            self, text="✕  Annuler",
            style="Danger.TButton",
            command=self._vm.cancel,
        ).pack(fill="x", padx=16)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        # ── Export ────────────────────────────────────────────────────
        self._section_label("EXPORT")

        self._export_fmt = tk.StringVar(value="csv")
        for fmt in ("csv", "json"):
            ttk.Radiobutton(
                self, text=fmt.upper(), variable=self._export_fmt, value=fmt
            ).pack(anchor="w", padx=20, pady=2)

        ttk.Button(
            self, text="💾  Exporter",
            command=self._export,
        ).pack(fill="x", padx=16, pady=8)

    # ------------------------------------------------------------------
    # Helpers de construction
    # ------------------------------------------------------------------

    def _section_label(self, text: str) -> None:
        tk.Label(
            self, text=text,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["tag"], anchor="w",
        ).pack(fill="x", padx=16, pady=(12, 4))

    def _path_row(
        self,
        variable: tk.StringVar,
        placeholder: str,
        command,
    ) -> ttk.Entry:
        frame = tk.Frame(self, bg=PALETTE["bg_panel"])
        frame.pack(fill="x", padx=16, pady=2)

        entry = ttk.Entry(frame, textvariable=variable)
        entry.pack(side="left", fill="x", expand=True)

        # Placeholder
        if not variable.get():
            entry.insert(0, placeholder)
            entry.configure(foreground=PALETTE["text_dim"])

            def _clear(e):
                if entry.get() == placeholder:
                    entry.delete(0, "end")
                    entry.configure(foreground=PALETTE["text"])

            def _restore(e):
                if not entry.get():
                    entry.insert(0, placeholder)
                    entry.configure(foreground=PALETTE["text_dim"])

            entry.bind("<FocusIn>", _clear)
            entry.bind("<FocusOut>", _restore)

        ttk.Button(frame, text="…", width=3, command=command).pack(side="right", padx=(4, 0))
        return entry

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Sélectionner le dossier source")
        if path:
            self._input_var.set(path)
            self._vm.set_input_dir(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Sélectionner le dossier de sortie")
        if path:
            self._output_var.set(path)
            self._vm.set_output_dir(path)

    def _start(self) -> None:
        input_path = self._input_var.get()
        if input_path:
            self._vm.set_input_dir(input_path)
        output_path = self._output_var.get()
        if output_path:
            self._vm.set_output_dir(output_path)
        self._vm.start_extraction()

    def _export(self) -> None:
        fmt = self._export_fmt.get()
        ext = f".{fmt}"
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[(fmt.upper(), f"*{ext}"), ("Tous", "*.*")],
            title="Enregistrer l'export",
        )
        if path:
            from pathlib import Path
            self._vm.export_results(Path(path), fmt)