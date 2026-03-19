"""
Panneau principal — navigateur par arborescence.

Pages :
  0 : WorkspaceResult  — liste des robots
  1 : RobotBackup      — liste des variables
  2 : RobotVariable    — fields / items de tableau
  3 : tuple(label, ArrayValue|PositionValue) — items ou lignes brutes

Sous-composants :
  ResultsTree  — treeview + scrollbars + tags
  LogTab       — journal horodaté
  FiltersBar   — filtre texte + scope pills
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tkinter as tk
from tkinter import ttk

from ui.theme import PALETTE
from ui.viewmodel import AppViewModel
from ui.components.filters_bar import FiltersBar
from ui.components.main_panel.results_tree import ResultsTree
from ui.components.main_panel.log_tab import LogTab
from models.fanuc_models import (
    ExtractionResult, RobotVariable, RobotBackup, WorkspaceResult,
    ArrayValue, PositionValue, RobotVarField,
)

if TYPE_CHECKING:
    from ui.components.header import HeaderBar

# ---------------------------------------------------------------------------
# Type de page de navigation
# ---------------------------------------------------------------------------

# tuple(label, ArrayValue|PositionValue)  : items d'un tableau ou lignes d'une position
# tuple(label, list[RobotVarField], list[RobotVarField])
#   → sous-fields à afficher + tous les fields source (pour navigation profonde)
Page = WorkspaceResult | RobotBackup | RobotVariable | tuple[str, ArrayValue | PositionValue | list[RobotVarField], list[RobotVarField]] | tuple[str, ArrayValue | PositionValue]

# ---------------------------------------------------------------------------
# Helpers d'affichage (fonctions pures, sans dépendance Tkinter)
# ---------------------------------------------------------------------------

def _has_children(var: RobotVariable) -> bool:
    """True si la variable a un niveau de détail navigable."""
    return bool(var.fields) or isinstance(var.value, (ArrayValue, PositionValue))


def _display_value(var: RobotVariable) -> str:
    """Représentation courte de la valeur pour la colonne Valeur."""
    if isinstance(var.value, str):
        return var.value
    if isinstance(var.value, ArrayValue):
        return repr(var.value)
    if isinstance(var.value, PositionValue):
        return "POSITION"
    if var.fields:
        return f"struct ({len(var.fields)} fields)"
    return ""


def _field_value_preview(
    fld: RobotVarField,
    all_fields: list[RobotVarField] | None = None,
) -> str:
    """Représentation courte de la valeur d'un field.

    Pour un field ARRAY OF STRUCT (ArrayValue vide), cherche les sous-fields
    qui lui appartiennent dans ``all_fields`` pour afficher un comptage correct.
    """
    if isinstance(fld.value, str):
        return fld.value if fld.value else "Uninitialized"
    if isinstance(fld.value, ArrayValue):
        n = len(fld.value.items)
        if n > 0:
            kind = "positions" if any(isinstance(v, PositionValue)
                                      for v in fld.value.items.values()) else "entrées"
            return f"[{n} {kind}]"
        # ArrayValue vide → peut être un ARRAY OF STRUCT dont les sous-fields
        # sont stockés dans all_fields avec parent_var pointant vers ce field
        if all_fields:
            sub = [f for f in all_fields
                   if f.parent_var.endswith(fld.field_name)
                   and f is not fld]
            if sub:
                # Compter les indices uniques (éléments du tableau)
                indices = {f.parent_index_nd for f in sub if f.parent_index_nd}
                n_items = len(indices)
                return f"[{n_items} élément{'s' if n_items > 1 else ''}]"
        return "[0 entrées]"
    if isinstance(fld.value, PositionValue):
        return f"[{len(fld.value.raw_lines)} lignes]"
    return "—"


def _index_str(nd: tuple[int, ...] | None) -> str:
    if nd is None:
        return ""
    return "[" + ",".join(str(i) for i in nd) + "]"


def _inner_type(type_detail: str) -> str:
    """Extrait le type interne d'un type tableau.

    "ARRAY[2] OF DMR_GRP_T" → "DMR_GRP_T",  "INTEGER = 0" → "INTEGER"
    """
    raw = type_detail.split("=")[0].strip()
    if raw.upper().startswith("ARRAY") and " OF " in raw.upper():
        return raw.split(" OF ", 1)[-1].strip()
    return raw


# ---------------------------------------------------------------------------
# MainPanel
# ---------------------------------------------------------------------------

class MainPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg"])
        self._vm      = vm
        self._history: list[Page] = []
        self._future:  list[Page] = []
        self._current: Page | None = None
        self._header:  HeaderBar | None = None
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        # Barre de filtres (row 0)
        self._filters = FiltersBar(self, on_filter_change=self._on_filter_change)
        self._filters.grid(row=0, column=0, sticky="ew")

        # Notebook Résultats / Journal (row 1)
        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))

        # — Onglet Résultats —
        results_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        results_tab.rowconfigure(0, weight=1)
        results_tab.columnconfigure(0, weight=1)
        nb.add(results_tab, text="  Résultats  ")

        self._tree = ResultsTree(results_tab, on_activate=self._on_activate)
        self._tree.grid(row=0, column=0, sticky="nsew")

        # — Onglet Journal —
        log_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        log_tab.rowconfigure(0, weight=1)
        log_tab.columnconfigure(0, weight=1)
        nb.add(log_tab, text="  Journal  ")

        self._log = LogTab(log_tab)
        self._log.grid(row=0, column=0, sticky="nsew")

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def set_header(self, header: HeaderBar) -> None:
        """Injecte la référence au HeaderBar (appelé par App après construction)."""
        self._header = header

    def display_workspace(self, workspace: WorkspaceResult) -> None:
        """Navigue vers la racine workspace (efface l'historique)."""
        self._navigate_to(workspace, clear_history=True)

    def refresh_screen(self) -> None:
        """Appelé quand un backup vient d'être chargé. Rafraîchit la page courante si c'est ce backup, sinon navigue vers lui."""

        if not self._current: 
            raise RuntimeError("refresh_screen called but no current page")
        self._render_page(self._current)

    def set_scope_filter(self, scope: str) -> None:
        self._filters.set_scope(scope)

    def append_log(self, message: str, level: str = "info") -> None:
        self._log.append(message, level)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_back(self) -> None:
        if not self._history:
            return
        if self._current is not None:
            self._future.append(self._current)
        self._current = self._history.pop()
        self._filters.clear()
        self._render_page(self._current)
        self._notify_header()

    def navigate_forward(self) -> None:
        if not self._future:
            return
        if self._current is not None:
            self._history.append(self._current)
        self._current = self._future.pop()
        self._filters.clear()
        self._render_page(self._current)
        self._notify_header()

    def navigate_to_index(self, index: int) -> None:
        """Clic sur un segment de breadcrumb → navigue à cet index."""
        full_path = self._history + ([self._current] if self._current else [])
        if index < 0 or index >= len(full_path):
            return
        self._future.clear()
        self._history = full_path[:index]
        self._current = full_path[index]
        self._filters.clear()
        self._render_page(self._current)
        self._notify_header()

    # ------------------------------------------------------------------
    # Navigation interne
    # ------------------------------------------------------------------

    def _navigate_to(self, page: Page, clear_history: bool = False) -> None:
        if clear_history:
            self._history.clear()
            self._future.clear()
        elif self._current is not None:
            self._history.append(self._current)
            self._future.clear()
        self._current = page
        self._filters.clear()
        self._render_page(page)
        self._notify_header()

    def _notify_header(self) -> None:
        if self._header is None:
            return
        self._header.set_nav_state(bool(self._history), bool(self._future))
        self._header.set_breadcrumbs(self._breadcrumb_parts())

    def _breadcrumb_parts(self) -> list[str]:
        parts: list[str] = []
        for p in self._history + ([self._current] if self._current else []):
            if isinstance(p, WorkspaceResult):
                parts.append(p.root_path.name)
            elif isinstance(p, RobotBackup):
                parts.append(p.name)
            elif isinstance(p, RobotVariable):
                parts.append(p.name)
            elif isinstance(p, tuple):
                parts.append(p[0])
        return parts

    # ------------------------------------------------------------------
    # Rendu des pages
    # ------------------------------------------------------------------

    def _render_page(self, page: Page) -> None:
        if isinstance(page, WorkspaceResult):
            self._render_workspace(page)
        elif isinstance(page, RobotBackup):
            self._render_backup(page)
        elif isinstance(page, RobotVariable):
            self._render_variable(page)
        elif isinstance(page, tuple):
            label = page[0]
            value = page[1]
            if isinstance(value, ArrayValue):
                self._render_array(label, value)
            elif isinstance(value, PositionValue):
                self._render_position(label, value)
            elif isinstance(value, list):
                self._render_subfields(label, value)

    def _render_workspace(self, ws: WorkspaceResult) -> None:
        self._tree.configure_columns([
            ("col1", "📁  Robot",    280, "w",      True),
            ("col2", "Variables",     90, "center", False),
            ("col3", "Fichiers .VA",  90, "center", False),
            ("col4", "État",          90, "center", False),
            ("col5", "Chemin",       200, "w",      False),
        ])
        query   = self._filters.query
        backups = [b for b in ws.backups if not query or query in b.name.lower()]
        self._tree.clear()
        for i, b in enumerate(backups):
            state  = "✓ chargé" if b.loaded else "⏳ chargement…"
            va_cnt = sum(1 for p in b.path.rglob("*") if p.suffix.lower() == ".va")
            self._tree.insert(
                values=(f"📁  {b.name}", b.var_count or "—", va_cnt, state, str(b.path)),
                iid=str(id(b)),
                tags=("even" if i % 2 == 0 else "odd", "robot"),
            )
        self._filters.set_count(f"{len(backups)} backup(s)")

    def _render_backup(self, backup: RobotBackup) -> None:
        if not backup.loaded:
            self._tree.configure_columns([
                ("col1", "État", 400, "w", True),
                ("col2", "",   0, "w", False),
                ("col3", "",   0, "w", False),
                ("col4", "",   0, "w", False),
                ("col5", "",   0, "w", False),
            ])
            self._tree.clear()
            self._tree.insert(
                values=("⏳  Chargement en cours…", "", "", "", ""),
                iid="_loading",
                tags=("loading",),
            )
            self._vm.load_backup(backup)
            return

        self._tree.configure_columns([
            ("col1", "NS",       70, "center", False),
            ("col2", "Nom",     210, "w",      False),
            ("col3", "Storage",  70, "center", False),
            ("col4", "Type",    180, "w",      True),
            ("col5", "Valeur",  140, "w",      False),
            ("col6", "Fichier", 130, "w",      False),
        ])
        query = self._filters.query
        scope = self._filters.scope
        vars_ = backup.variables
        if scope == "system":
            vars_ = [v for v in vars_ if v.is_system]
        elif scope == "karel":
            vars_ = [v for v in vars_ if not v.is_system]
        if query:
            vars_ = [v for v in vars_
                     if query in v.name.lower()
                     or query in v.namespace.lower()
                     or query in v.type_str.lower()]

        self._tree.clear()
        for i, var in enumerate(vars_):
            tags: list[str] = ["even" if i % 2 == 0 else "odd"]
            if not var.is_system:
                tags.append("karel")
            val_disp = _display_value(var)
            if not val_disp or val_disp == "Uninitialized":
                tags.append("uninit")
                val_disp = "Uninitialized"
            ns  = "" if var.is_system else var.namespace
            src = var.source_file.name if var.source_file else ""
            self._tree.insert(
                values=(ns, var.name, var.storage.value,
                        var.type_str[:60], val_disp[:60], src),
                iid=str(id(var)),
                tags=tuple(tags),
            )
        self._filters.set_count(f"{len(vars_)} variable(s)")

    def _render_variable(self, var: RobotVariable) -> None:
        self._tree.configure_columns([
            ("col1", "Index",   90, "center", False),
            ("col2", "Field",  220, "w",      False),
            ("col3", "Access",  65, "center", False),
            ("col4", "Type",   170, "w",      False),
            ("col5", "Valeur", 260, "w",      True),
        ])
        query = self._filters.query
        self._tree.clear()

        if var.fields:
            # N'afficher que les fields directs de la variable.
            # Les sous-fields imbriqués (ex: NODEDATA[1].NODE_POS dont
            # parent_var = "VAR.NODEDATA") sont accessibles en cliquant
            # sur leur field parent — ils ne doivent pas apparaître ici.
            items = [f for f in var.fields if f.parent_var == var.name]
            if query:
                items = [f for f in items
                         if query in f.field_name.lower()
                         or query in f.type_detail.lower()]
            for i, fld in enumerate(items):
                tags_fld: list[str] = ["even" if i % 2 == 0 else "odd"]
                val = _field_value_preview(fld, var.fields)
                if not val or val == "Uninitialized":
                    tags_fld.append("uninit")
                    val = "Uninitialized"
                elif isinstance(fld.value, (ArrayValue, PositionValue)):
                    tags_fld.append("nav")
                elif val.startswith("[") and val.endswith("]"):
                    tags_fld.append("nav")  # sous-fields groupés navigables
                # _inner_type uniquement pour les fields scalaires fils d'un
                # tableau de structs. Ne pas l'appliquer si le field est lui-même
                # un tableau (ex: $MRR2_GRP[1].$ARM_PARAM  ARRAY[100] OF REAL).
                is_child_of_struct_array = (
                    fld.parent_index_nd is not None
                    and not fld.type_detail.upper().startswith("ARRAY")
                )
                type_disp = (_inner_type(fld.type_detail)
                             if is_child_of_struct_array
                             else fld.type_detail)
                self._tree.insert(
                    values=(_index_str(fld.parent_index_nd),
                            fld.field_name, fld.access.value,
                            type_disp[:50], val),
                    iid=str(id(fld)),
                    tags=tuple(tags_fld),
                )
            self._filters.set_count(f"{len(items)} field(s)")

        elif isinstance(var.value, ArrayValue):
            self._render_array_items(var.value)
        elif isinstance(var.value, PositionValue):
            self._render_position_lines(var.value)

    def _render_array(self, _label: str, arr: ArrayValue) -> None:
        self._tree.configure_columns([
            ("col1", "Index",   90, "center", False),
            ("col2", "",         0, "w",      False),
            ("col3", "",         0, "w",      False),
            ("col4", "",         0, "w",      False),
            ("col5", "Valeur",  400, "w",     True),
        ])
        self._tree.clear()
        self._render_array_items(arr)

    def _render_position(self, _label: str, pos: PositionValue) -> None:
        self._tree.configure_columns([
            ("col1", "",  0, "w", False),
            ("col2", "",  0, "w", False),
            ("col3", "",  0, "w", False),
            ("col4", "",  0, "w", False),
            ("col5", "Valeur", 600, "w", True),
        ])
        self._tree.clear()
        self._render_position_lines(pos)

    def _render_subfields(self, _label: str, fields: list[RobotVarField]) -> None:
        """Affiche les sous-fields d'un ARRAY OF STRUCT (ex: NODEDATA[N].NODE_POS)."""
        self._tree.configure_columns([
            ("col1", "Index",   90, "center", False),
            ("col2", "Field",  220, "w",      False),
            ("col3", "Access",  65, "center", False),
            ("col4", "Type",   170, "w",      False),
            ("col5", "Valeur", 260, "w",      True),
        ])
        self._tree.clear()
        query = self._filters.query
        items = fields
        if query:
            items = [f for f in fields
                     if query in f.field_name.lower()
                     or query in f.type_detail.lower()]
        for i, fld in enumerate(items):
            tags_fld: list[str] = ["even" if i % 2 == 0 else "odd"]
            val = _field_value_preview(fld)
            if not val or val == "Uninitialized":
                tags_fld.append("uninit")
                val = "Uninitialized"
            elif isinstance(fld.value, (ArrayValue, PositionValue)):
                tags_fld.append("nav")
            self._tree.insert(
                values=(_index_str(fld.parent_index_nd),
                        fld.field_name, fld.access.value,
                        fld.type_detail[:50], val),
                iid=str(id(fld)),
                tags=tuple(tags_fld),
            )
        self._filters.set_count(f"{len(items)} field(s)")

    # ── Helpers de rendu ─────────────────────────────────────────────

    def _render_array_items(self, arr: ArrayValue) -> None:
        items = list(arr.items.items())
        for i, (key, val) in enumerate(items):
            key_str  = "[" + ",".join(str(k) for k in key) + "]"
            tags_arr: list[str] = ["even" if i % 2 == 0 else "odd"]
            if isinstance(val, PositionValue):
                preview = f"[{len(val.raw_lines)} lignes]"
                tags_arr += ["nav", "pos"]
            elif not val or val == "Uninitialized":
                preview = "Uninitialized"
                tags_arr.append("uninit")
            else:
                preview = val
            self._tree.insert(
                values=(key_str, "", "", "", preview),
                iid=f"arr_{i}_{key_str}",
                tags=tuple(tags_arr),
            )
        self._filters.set_count(f"{len(items)} entrée(s)")

    def _render_position_lines(self, pos: PositionValue) -> None:
        for i, line in enumerate(pos.raw_lines):
            self._tree.insert(
                values=("", "", "", "", line),
                iid=f"pos_{i}",
                tags=("even" if i % 2 == 0 else "odd",),
            )
        self._filters.set_count(f"{len(pos.raw_lines)} ligne(s)")

    # ------------------------------------------------------------------
    # Activation (double-clic / Entrée) — délégué par ResultsTree
    # ------------------------------------------------------------------

    def _on_activate(self, iid: str) -> None:
        page = self._current

        if isinstance(page, WorkspaceResult):
            backup = next((b for b in page.backups if str(id(b)) == iid), None)
            if backup:
                self._navigate_to(backup)

        elif isinstance(page, RobotBackup):
            var = next((v for v in page.variables if str(id(v)) == iid), None)
            if var and _has_children(var):
                self._navigate_to(var)

        elif isinstance(page, RobotVariable):
            if page.fields:
                fld = next((f for f in page.fields if str(id(f)) == iid), None)
                if fld is None:
                    pass
                elif isinstance(fld.value, ArrayValue) and not fld.value.items:
                    # ARRAY OF STRUCT : les sous-éléments sont stockés comme fields
                    # avec parent_var pointant vers ce field (ex: COMP_POS1.NODEDATA[N].X)
                    sub = [f for f in page.fields
                           if f.parent_var.endswith(fld.field_name) and f is not fld]
                    if sub:
                        # Passer tous les fields de la variable comme source
                        self._navigate_to((fld.field_name, sub, page.fields))
                    # ArrayValue vraiment vide (aucun sous-field) → pas de navigation
                elif isinstance(fld.value, (ArrayValue, PositionValue)):
                    # Tableau de scalaires / positions ou position simple → navigable
                    self._navigate_to((fld.field_name, fld.value))
            elif isinstance(page.value, ArrayValue):
                row_idx  = self._tree.index_of(iid)
                arr_items = list(page.value.items.items())
                if row_idx < len(arr_items):
                    key, val = arr_items[row_idx]
                    if isinstance(val, PositionValue):
                        self._navigate_to(("[" + ",".join(str(k) for k in key) + "]", val))

        elif isinstance(page, tuple):
            value = page[1]
            if isinstance(value, ArrayValue):
                # Page d'items d'un tableau — naviguer vers une PositionValue
                row_idx   = self._tree.index_of(iid)
                arr_items = list(value.items.items())
                if row_idx < len(arr_items):
                    key, val = arr_items[row_idx]
                    if isinstance(val, PositionValue):
                        self._navigate_to(("[" + ",".join(str(k) for k in key) + "]", val))
            elif isinstance(value, list):
                # source_all = tous les fields de la variable source
                source_all: list[RobotVarField] = (
                    page[2] if len(page) > 2 else value  # type: ignore[misc]
                )
                fld = next((f for f in value if str(id(f)) == iid), None)
                if fld is None:
                    pass
                elif isinstance(fld.value, ArrayValue) and not fld.value.items:
                    # ARRAY OF STRUCT vide → chercher sous-fields dans source_all
                    sub = [f for f in source_all
                           if (f.parent_var.endswith(f'.{fld.field_name}') or
                               f.parent_var.endswith(f'].{fld.field_name}'))
                           and f is not fld]
                    if sub:
                        label = f"{_index_str(fld.parent_index_nd)}.{fld.field_name}"
                        self._navigate_to((label, sub, source_all))
                elif isinstance(fld.value, (ArrayValue, PositionValue)):
                    self._navigate_to((fld.field_name, fld.value))

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_filter_change(self, _query: str, _scope: str) -> None:
        if self._current is not None:
            self._render_page(self._current)