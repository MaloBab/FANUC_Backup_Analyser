"""
Panneau principal : onglets Résultats (tableau) et Journal (logs).
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from typing import Literal, cast, Callable

from ui.theme import PALETTE, FONTS
from ui.viewmodel import AppViewModel
from models.fanuc_models import ExtractionResult, RobotVariable, ArrayValue, PositionValue
from ui.components.detail_dialog import DetailDialog


_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

# Mapping niveau de log → couleur
_LOG_COLORS = {
    "info":    PALETTE["info"],
    "success": PALETTE["success"],
    "warning": PALETTE["warning"],
    "error":   PALETTE["error"],
}

# Colonnes du Treeview : (id, heading, width, anchor)
_COLUMNS = [
    ("namespace", "NS",       70, "center"),
    ("name",      "Nom",     210, "w"),
    ("storage",   "Storage",  70, "center"),
    ("access",    "Access",   60, "center"),
    ("type",      "Type",    180, "w"),
    ("value",     "Valeur",  130, "w"),
    ("fields",    "Fields",   55, "center"),
    ("source",    "Fichier", 140, "w"),
]

_SortKey = Callable[[RobotVariable], str | int]

def _display_value(var: RobotVariable) -> str:
    """Retourne une représentation courte de la valeur d'une variable."""
    if isinstance(var.value, str):
        return var.value
    if isinstance(var.value, ArrayValue):
        return repr(var.value)
    if isinstance(var.value, PositionValue):
        return "POSITION"
    if var.fields:
        return f"struct ({len(var.fields)} fields)"
    return ""


def _has_detail(var: RobotVariable) -> bool:
    """Détermine si une variable mérite d'ouvrir la fenêtre de détail.

    Critère : la valeur ne tient pas entièrement dans la colonne Treeview.
    Cas concernés :
      - Variable avec fields (struct / tableau de structs)
      - Tableau primitif (ArrayValue racine)
      - Variable de type POSITION
    """
    return bool(var.fields) or isinstance(var.value, (ArrayValue, PositionValue))


class MainPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg"])
        self._vm = vm
        self._all_variables:    list[RobotVariable] = []
        self._filtered_variables: list[RobotVariable] = []
        self._build()


    def _build(self) -> None:
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        results_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        results_tab.rowconfigure(1, weight=1)
        results_tab.columnconfigure(0, weight=1)
        nb.add(results_tab, text="  Résultats  ")
        self._build_results_tab(results_tab)

        #Journal
        log_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        log_tab.rowconfigure(0, weight=1)
        log_tab.columnconfigure(0, weight=1)
        nb.add(log_tab, text="  Journal  ")
        self._build_log_tab(log_tab)

    def _build_results_tab(self, parent: tk.Frame) -> None:
        toolbar = tk.Frame(parent, bg=PALETTE["bg_panel"])
        toolbar.grid(row=0, column=0, sticky="ew")
        self._build_toolbar(toolbar)


        tree_frame = tk.Frame(parent, bg=PALETTE["bg_card"])
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        col_ids = tuple(c[0] for c in _COLUMNS)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=col_ids,
            show="headings",
            selectmode="extended",
        )

        for col_id, heading, width, anchor in _COLUMNS:
            a = cast(_AnchorT, anchor)
            self._tree.heading(
                col_id, text=heading, anchor=a,
                command=lambda c=col_id: self._sort_tree(c),
            )
            self._tree.column(col_id, width=width, anchor=a, minwidth=30)

        self._tree.tag_configure("even",  background=PALETTE["bg_card"])
        self._tree.tag_configure("odd",   background=PALETTE["bg_panel"])
        self._tree.tag_configure("karel", foreground=PALETTE["warning"])

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",  command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._count_var = tk.StringVar(value="0 variable(s)")
        tk.Label(
            parent, textvariable=self._count_var,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"], anchor="e",
        ).grid(row=2, column=0, sticky="ew", padx=8, pady=2)

    def _build_toolbar(self, parent: tk.Frame) -> None:
        tk.Label(
            parent, text="Filtrer :",
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"],
        ).pack(side="left", padx=(12, 4), pady=8)

        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(parent, textvariable=self._filter_var, width=24).pack(
            side="left", pady=6,
        )
        ttk.Button(
            parent, text="✕ Effacer",
            command=lambda: self._filter_var.set(""),
        ).pack(side="left", padx=6, pady=8)

        self._scope_var = tk.StringVar(value="all")
        for value, label in (("all", "Tout"), ("system", "Système"), ("karel", "Karel")):
            tk.Radiobutton(
                parent, text=label,
                variable=self._scope_var, value=value,
                command=self._apply_filter,
                bg=PALETTE["bg_panel"], fg=PALETTE["text"],
                selectcolor=PALETTE["bg_input"],
                activebackground=PALETTE["bg_panel"],
                activeforeground=PALETTE["accent"],
                font=FONTS["body"],
                relief="flat", bd=0,
            ).pack(side="left", padx=4, pady=8)

        ttk.Button(
            parent, text="📋 Copier sélection",
            command=self._copy_selection,
        ).pack(side="right", padx=12, pady=8)

    def _build_log_tab(self, parent: tk.Frame) -> None:
        self._log_text = tk.Text(
            parent,
            bg=PALETTE["bg_card"], fg=PALETTE["text"],
            font=FONTS["mono"],
            wrap="none", state="disabled", relief="flat",
            padx=12, pady=8,
        )
        vsb = ttk.Scrollbar(parent, orient="vertical", command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=vsb.set)

        self._log_text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        for level, color in _LOG_COLORS.items():
            self._log_text.tag_configure(level, foreground=color)

        btn_frame = tk.Frame(parent, bg=PALETTE["bg_panel"])
        btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            btn_frame, text="🗑 Effacer le journal",
            command=self._clear_log,
        ).pack(side="right", padx=12, pady=4)


#------------------------------------------------------------------

    def display_results(self, result: ExtractionResult) -> None:
        """Remplit le tableau avec les variables extraites."""
        self._all_variables = result.variables
        self._apply_filter()

    def set_scope_filter(self, scope: str) -> None:
        """Applique un filtre de scope depuis la sidebar (all / system / karel)."""
        self._scope_var.set(scope)
        self._apply_filter()

    def append_log(self, message: str, level: str = "info") -> None:
        """Ajoute une ligne horodatée au journal."""
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"
        self._log_text.configure(state="normal")
        self._log_text.insert("end", line, level)
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

#------------------------------------------------------------------

    def _apply_filter(self) -> None:
        query = self._filter_var.get().lower()
        scope = self._scope_var.get()

        filtered = self._all_variables

        if scope == "system":
            filtered = [v for v in filtered if v.is_system]
        elif scope == "karel":
            filtered = [v for v in filtered if not v.is_system]

        if query:
            filtered = [
                v for v in filtered
                if query in v.name.lower()
                or query in v.namespace.lower()
                or query in v.type_detail.lower()
                or query in v.storage.value.lower()
                or query in v.access.value.lower()
            ]

        self._populate_tree(filtered)

    def _populate_tree(self, variables: list[RobotVariable]) -> None:
        self._filtered_variables = variables
        self._tree.delete(*self._tree.get_children())
        for i, var in enumerate(variables):
            tags: list[str] = ["even" if i % 2 == 0 else "odd"]
            if not var.is_system:
                tags.append("karel")

            ns_label = "" if var.is_system else var.namespace

            self._tree.insert(
                "", "end",
                values=(
                    ns_label,
                    var.name,
                    var.storage.value,
                    var.access.value,
                    var.type_str[:60],
                    _display_value(var)[:60],
                    len(var.fields) if var.fields else "",
                    var.source_file.name if var.source_file else "",
                ),
                tags=tuple(tags),
            )
        self._count_var.set(f"{len(variables)} variable(s)")

    def _on_double_click(self, event: tk.Event) -> None:
        """Ouvre le détail d'une variable si elle a des fields, un tableau ou une position."""
        item = self._tree.identify_row(event.y)
        if not item:
            return
        idx = self._tree.index(item)
        if idx >= len(self._filtered_variables):
            return
        var = self._filtered_variables[idx]
        if _has_detail(var):
            DetailDialog(self, var)

    def _sort_tree(self, col: str) -> None:
        key_map: dict[str, _SortKey] = {
            "namespace": lambda v: v.namespace.lower(),
            "name":      lambda v: v.name.lower(),
            "storage":   lambda v: v.storage.value,
            "access":    lambda v: v.access.value,
            "type":      lambda v: v.type_detail.lower(),
            "value":     lambda v: _display_value(v).lower(),
            "fields":    lambda v: len(v.fields),
            "source":    lambda v: str(v.source_file or "").lower(),
        }
        default: _SortKey = lambda v: ""
        key_fn = key_map.get(col, default)
        rev    = getattr(self, f"_sort_rev_{col}", False)
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