"""
Panneau principal : onglets Résultats (tableau) et Journal (logs).
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from typing import Literal, cast

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel
from models.fanuc_models import ExtractionResult, SystemVariable


# Type alias pour les ancres Treeview (doit être au niveau module pour cast())
_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

# Mapping niveau → couleur dans le log
_LOG_COLORS = {
    "info":    PALETTE["text"],
    "success": PALETTE["success"],
    "warning": PALETTE["warning"],
    "error":   PALETTE["error"],
}


class MainPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg"])
        self._vm = vm
        self._build()

    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # ── Onglet Résultats ─────────────────────────────────────────
        results_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        results_tab.rowconfigure(1, weight=1)
        results_tab.columnconfigure(0, weight=1)
        nb.add(results_tab, text="  Résultats  ")
        self._build_results_tab(results_tab)

        # ── Onglet Journal ───────────────────────────────────────────
        log_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        log_tab.rowconfigure(0, weight=1)
        log_tab.columnconfigure(0, weight=1)
        nb.add(log_tab, text="  Journal  ")
        self._build_log_tab(log_tab)

    # ------------------------------------------------------------------
    # Onglet Résultats
    # ------------------------------------------------------------------

    def _build_results_tab(self, parent: tk.Frame) -> None:
        # Barre d'outils
        toolbar = tk.Frame(parent, bg=PALETTE["bg_panel"], height=38)
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.pack_propagate(False)
        self._build_toolbar(toolbar)

        # Tableau
        tree_frame = tk.Frame(parent, bg=PALETTE["bg_card"])
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        columns = ("name", "storage", "access", "type", "value", "fields", "source")
        self._tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )

        col_config = {
            "name":    ("Nom",      200, "w"),
            "storage": ("Storage",   70, "center"),
            "access":  ("Access",    60, "center"),
            "type":    ("Type",     180, "w"),
            "value":   ("Valeur",   120, "w"),
            "fields":  ("Fields",    55, "center"),
            "source":  ("Fichier",  140, "w"),
        }
        for col, (heading, width, anchor) in col_config.items():
            a = cast(_AnchorT, anchor)
            self._tree.heading(col, text=heading, anchor=a,
                               command=lambda c=col: self._sort_tree(c))
            self._tree.column(col, width=width, anchor=a, minwidth=40)

        # Alternance de couleurs de lignes
        self._tree.tag_configure("odd",  background=PALETTE["bg_card"])
        self._tree.tag_configure("even", background=PALETTE["bg_panel"])

        # Scrollbars
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # Compteur
        self._count_var = tk.StringVar(value="0 variable(s)")
        tk.Label(
            parent, textvariable=self._count_var,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"], anchor="e",
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=2)

    def _build_toolbar(self, parent: tk.Frame) -> None:
        # Filtre rapide
        tk.Label(
            parent, text="Filtrer :",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"],
        ).pack(side="left", padx=(12, 4), pady=8)

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(parent, textvariable=self._filter_var, width=24).pack(
            side="left", pady=8
        )

        ttk.Button(
            parent, text="✕ Effacer",
            command=lambda: self._filter_var.set(""),
        ).pack(side="left", padx=6, pady=8)

        # Bouton copier sélection
        ttk.Button(
            parent, text="📋 Copier sélection",
            command=self._copy_selection,
        ).pack(side="right", padx=12, pady=8)

    # ------------------------------------------------------------------
    # Onglet Journal
    # ------------------------------------------------------------------

    def _build_log_tab(self, parent: tk.Frame) -> None:
        self._log_text = tk.Text(
            parent,
            bg=PALETTE["bg_card"],
            fg=PALETTE["text"],
            font=FONTS["mono"],
            wrap="none",
            state="disabled",
            relief="flat",
            padx=12,
            pady=8,
        )
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=vsb.set)

        self._log_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        # Tags de couleur
        for level, color in _LOG_COLORS.items():
            self._log_text.tag_configure(level, foreground=color)

        # Bouton effacer
        btn_frame = tk.Frame(parent, bg=PALETTE["bg_panel"])
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(btn_frame, text="🗑 Effacer le journal",
                   command=self._clear_log).pack(side="right", padx=12, pady=4)

    # ------------------------------------------------------------------
    # Interface publique (appelée par App via callbacks)
    # ------------------------------------------------------------------

    def display_results(self, result: ExtractionResult) -> None:
        """Remplit le tableau avec les variables extraites."""
        self._all_variables = result.variables
        self._apply_filter()

    def append_log(self, message: str, level: str = "info") -> None:
        """Ajoute une ligne au journal avec horodatage."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        self._log_text.configure(state="normal")
        self._log_text.insert("end", line, level)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Privé
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        if not hasattr(self, "_all_variables"):
            return

        query = self._filter_var.get().lower()
        filtered = [
            v for v in self._all_variables
            if not query
            or query in v.name.lower()
            or query in v.type_detail.lower()
            or query in v.storage.value.lower()
            or query in v.access.value.lower()
        ]
        self._populate_tree(filtered)

    def _populate_tree(self, variables: list[SystemVariable]) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, var in enumerate(variables):
            tag = "even" if i % 2 == 0 else "odd"
            # Valeur affichée : scalaire brut, "Array(N)" ou "struct"
            if isinstance(var.value, str):
                display_val = var.value or ""
            elif var.value is not None:
                display_val = repr(var.value)
            else:
                display_val = "—" if var.fields else ""
            self._tree.insert(
                "", "end",
                values=(
                    var.name,
                    var.storage.value,
                    var.access.value,
                    var.type_detail[:60],
                    display_val[:60],
                    len(var.fields) if var.fields else "",
                    var.source_file.name if var.source_file else "",
                ),
                tags=(tag,),
            )
        self._count_var.set(f"{len(variables)} variable(s)")

    def _sort_tree(self, col: str) -> None:
        if not hasattr(self, "_all_variables"):
            return
        key_map = {
            "name":    lambda v: v.name.lower(),
            "storage": lambda v: v.storage.value,
            "access":  lambda v: v.access.value,
            "type":    lambda v: v.type_detail.lower(),
            "value":   lambda v: str(v.value or "").lower(),
            "fields":  lambda v: len(v.fields),
            "source":  lambda v: str(v.source_file or "").lower(),
        }
        key_fn = key_map.get(col, lambda v: "")
        rev = getattr(self, f"_sort_rev_{col}", False)
        self._all_variables.sort(key=key_fn, reverse=rev)
        setattr(self, f"_sort_rev_{col}", not rev)
        self._apply_filter()

    def _copy_selection(self) -> None:
        rows = [self._tree.item(i)["values"] for i in self._tree.selection()]
        if not rows:
            return
        text = "\n".join("\t".join(str(c) for c in row) for row in rows)
        self.clipboard_clear()
        self.clipboard_append(text)

    def _clear_log(self) -> None:
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")