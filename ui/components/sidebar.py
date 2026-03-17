"""
Panneau latéral gauche : sélection du dossier source, filtres, actions.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel


class SidebarPanel(tk.Frame):
    WIDTH = 280

    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], width=self.WIDTH)
        self.pack_propagate(False)
        self._vm = vm
        self._build()

    def _build(self) -> None:

        self._section_label("DOSSIER SOURCE")

        self._input_var = tk.StringVar(value=self._vm.settings.last_input_dir)
        self._entry_input = self._path_row(
            self._input_var,
            placeholder="Choisir un dossier…",
            command=self._browse_input,
        )

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        self._build_type_legend()

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        self._btn_start = ttk.Button(
            self, text="▶  Lancer l'extraction",
            style="Accent.TButton",
            command=self._start,
        )
        self._btn_start.pack(fill="x", padx=16, pady=(0, 8))

        ttk.Button(
            self, text="✕  Annuler",
            style="Danger.TButton",
            command=self._vm.cancel,
        ).pack(fill="x", padx=16)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=12)

        self._section_label("EXPORT")

        self._export_fmt = tk.StringVar(value="csv")
        for fmt in ("csv", "csv_flat", "json"):
            ttk.Radiobutton(
                self, text=fmt.upper(), variable=self._export_fmt, value=fmt,
            ).pack(anchor="w", padx=20, pady=2)

        ttk.Button(
            self, text="💾  Exporter",
            command=self._export,
        ).pack(fill="x", padx=16, pady=8)

    def _build_type_legend(self) -> None:
        """légende des types de variables."""
        frame = tk.Frame(self, bg=PALETTE["bg_panel"])
        frame.pack(fill="x", padx=16)

        legend_frame = tk.Frame(frame, bg=PALETTE["bg_panel"])
        legend_frame.pack(anchor="w", pady=(6, 0), fill="x")
        
        # Légende Système
        system_row = tk.Frame(legend_frame, bg=PALETTE["bg_panel"])
        system_row.pack(anchor="w", pady=2)
        tk.Label(
            system_row, text="■",
            bg=PALETTE["bg_panel"], fg=PALETTE["text"],
            font=FONTS["tag"],
        ).pack(side="left")
        tk.Label(
            system_row, text="Variables Système",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"],
        ).pack(side="left", padx=(4, 0))
        
        
        # Légende Karel
        karel_row = tk.Frame(legend_frame, bg=PALETTE["bg_panel"])
        karel_row.pack(anchor="w", pady=2)
        tk.Label(
            karel_row, text="■",
            bg=PALETTE["bg_panel"], fg=PALETTE["warning"],
            font=FONTS["tag"],
        ).pack(side="left")
        tk.Label(
            karel_row, text="Variables Karel",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"],
        ).pack(side="left", padx=(4, 0))
        


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

        if not variable.get():
            entry.insert(0, placeholder)
            entry.configure(foreground=PALETTE["text_dim"])

            def _clear(e: tk.Event) -> None:
                if entry.get() == placeholder:
                    entry.delete(0, "end")
                    entry.configure(foreground=PALETTE["text"])

            def _restore(e: tk.Event) -> None:
                if not entry.get():
                    entry.insert(0, placeholder)
                    entry.configure(foreground=PALETTE["text_dim"])

            entry.bind("<FocusIn>",  _clear)
            entry.bind("<FocusOut>", _restore)

        ttk.Button(frame, text="…", width=3, command=command).pack(
            side="right", padx=(4, 0),
        )
        return entry


    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Sélectionner le dossier source")
        if path:
            self._input_var.set(path)
            self._vm.set_input_dir(path)

    def _start(self) -> None:
        """Valide le dossier source puis lance l'extraction."""
        raw = self._input_var.get().strip()
        if not raw or not Path(raw).is_dir():
            self._vm._emit_log(
                "Veuillez sélectionner un dossier source valide.", "error",
            )
            return
        self._vm.set_input_dir(raw)
        self._vm.start_extraction()


    def _export(self) -> None:
        fmt = self._export_fmt.get()
        ext = ".csv" if fmt == "csv_flat" else f".{fmt}"
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=[(fmt.upper(), f"*{ext}"), ("Tous", "*.*")],
            title="Enregistrer l'export",
        )
        if path:
            self._vm.export_results(Path(path), fmt)