"""
Panneau latéral gauche.

Mode simple  : sélection d'un dossier → extraction directe.
Mode workspace : sélection d'un dossier racine → liste des backups robots
                 → clic sur un robot → chargement de ses variables.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel
from models.fanuc_models import RobotBackup, WorkspaceResult


class SidebarPanel(tk.Frame):
    WIDTH = 290

    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], width=self.WIDTH)
        self.pack_propagate(False)
        self._vm = vm
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._build_workspace_section()
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=8)
        self._build_legend()
        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=8)
        self._build_export_section()

    def _build_workspace_section(self) -> None:
        self._section_label("WORKSPACE")

        self._ws_var = tk.StringVar(value=self._vm.settings.last_input_dir)
        row = tk.Frame(self, bg=PALETTE["bg_panel"])
        row.pack(fill="x", padx=16, pady=(0, 4))

        entry = ttk.Entry(row, textvariable=self._ws_var)
        entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=self._browse_workspace).pack(
            side="right", padx=(4, 0),
        )

        ttk.Button(
            self, text="🔍  Scan",
            command=self._scan,
        ).pack(fill="x", padx=16, pady=(0, 2))

    def _build_legend(self) -> None:
        """Légende des couleurs — Système et Karel."""
        self._section_label("LEGEND")
        frame = tk.Frame(self, bg=PALETTE["bg_panel"])
        frame.pack(fill="x", padx=16)

        items = [
            ("■", PALETTE["text"],    "System variables"),
            ("■", PALETTE["warning"], "Karel variables"),
        ]
        for icon, fg, label in items:
            row = tk.Frame(frame, bg=PALETTE["bg_panel"])
            row.pack(anchor="w", pady=2)
            tk.Label(row, text=icon, bg=PALETTE["bg_panel"], fg=fg,
                     font=FONTS["tag"]).pack(side="left")  # type: ignore[arg-type]
            tk.Label(row, text=f"  {label}", bg=PALETTE["bg_panel"],
                     fg=PALETTE["text_dim"],
                     font=FONTS["small"]).pack(side="left")  # type: ignore[arg-type]


    def _build_export_section(self) -> None:
        self._section_label("EXPORT")
        self._export_fmt = tk.StringVar(value="csv")
        for fmt in ("csv", "csv_flat", "json"):
            ttk.Radiobutton(
                self, text=fmt.upper(),
                variable=self._export_fmt, value=fmt,
            ).pack(anchor="w", padx=20, pady=2)
        ttk.Button(
            self, text="💾  Export",
            command=self._export,
        ).pack(fill="x", padx=16, pady=8)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _section_label(self, text: str) -> None:
        tk.Label(
            self, text=text,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["tag"], anchor="w",  # type: ignore[arg-type]
        ).pack(fill="x", padx=16, pady=(10, 4))

    # ------------------------------------------------------------------
    # Interface publique (appelée par App via callbacks)
    # ------------------------------------------------------------------

    def populate_workspace(self, workspace: WorkspaceResult) -> None:
        """Mémorise le workspace (chargement automatique — pas d'affichage liste)."""
        self._workspace = workspace

    def mark_backup_loaded(self, backup: RobotBackup) -> None:
        """No-op — le chargement est reflété directement dans le panneau principal."""

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------

    def _browse_workspace(self) -> None:
        path = filedialog.askdirectory(title="Sélectionner le dossier workspace")
        if path:
            self._ws_var.set(path)

    def _scan(self) -> None:
        path = self._ws_var.get().strip()
        if not path or not Path(path).is_dir():
            self._vm.emit_log("Veuillez sélectionner un dossier valide.", "error")
            return
        self._vm.scan_workspace(path)


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