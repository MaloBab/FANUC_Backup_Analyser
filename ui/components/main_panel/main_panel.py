"""
MainPanel — navigateur par arborescence.

Orchestre cinq sous-composants :
  PageNavigator  (_navigator.py) — historique ← / → + activation
  PageRenderer   (_renderer.py)  — rendu Treeview pour chaque type de page
  ResultsTree    (results_tree)  — widget Treeview + scrollbars
  LogTab         (log_tab)       — journal horodaté
  FiltersBar     (filters_bar)   — filtre texte + scope pills

Recherche
─────────
À chaque frappe dans la FiltersBar, ``_on_filter_change`` est appelé.
Si un workspace est chargé, le ViewModel lance une recherche globale en
arrière-plan. Quand les résultats arrivent, ``display_search_results()``
affiche une page dédiée sans modifier l'historique de navigation.

Si le texte est vide, on revient à la page courante du navigateur.

Double-clic sur un résultat : navigue vers la variable source dans son backup.
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
from ui.components.main_panel._navigator import PageNavigator, Page
from ui.components.main_panel._renderer import PageRenderer
from models.fanuc_models import ExtractionResult, RobotBackup, WorkspaceResult
from models.search_models import SearchResults

if TYPE_CHECKING:
    from ui.components.header import HeaderBar


class MainPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg"])
        self._vm                           = vm
        self._header: HeaderBar | None     = None
        self._last_search: SearchResults | None = None
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self._filters = FiltersBar(self, on_filter_change=self._on_filter_change)
        self._filters.grid(row=0, column=0, sticky="ew")

        nb = ttk.Notebook(self)
        nb.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))

        results_tab = tk.Frame(nb, bg=PALETTE["bg_card"])
        results_tab.rowconfigure(0, weight=1)
        results_tab.columnconfigure(0, weight=1)
        nb.add(results_tab, text="  Résultats  ")

        self._tree = ResultsTree(results_tab, on_activate=self._on_activate)
        self._tree.grid(row=0, column=0, sticky="nsew")

        log_tab_frame = tk.Frame(nb, bg=PALETTE["bg_card"])
        log_tab_frame.rowconfigure(0, weight=1)
        log_tab_frame.columnconfigure(0, weight=1)
        nb.add(log_tab_frame, text="  Journal  ")

        self._log = LogTab(log_tab_frame)
        self._log.grid(row=0, column=0, sticky="nsew")

        self._renderer  = PageRenderer(self._tree, self._filters)
        self._navigator = PageNavigator(
            tree          = self._tree,
            render_page   = self._render_page,
            notify_header = self._notify_header,
            clear_filter  = self._filters.clear,
            load_backup   = self._vm.load_backup,
        )

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def set_header(self, header: HeaderBar) -> None:
        self._header = header

    def display_workspace(self, workspace: WorkspaceResult) -> None:
        self._navigator.go_to(workspace, clear_history=True)

    def refresh_screen(self) -> None:
        # Si une recherche est affichée, la relancer pour inclure le backup
        # qui vient d'être chargé
        if self._last_search is not None:
            self._vm.search(self._filters.query, self._filters.scope)
        else:
            self._navigator.refresh()

    def set_scope_filter(self, scope: str) -> None:
        self._filters.set_scope(scope)

    def append_log(self, message: str, level: str = "info") -> None:
        self._log.append(message, level)

    def display_search_results(self, results: SearchResults) -> None:
        """Affiche les résultats de recherche.

        Si les résultats sont vides (texte effacé), revient à la page courante.
        La page de recherche n'entre pas dans l'historique ← / →.
        """
        if not results.query_text:
            self._last_search = None
            self._navigator.refresh()
            self._notify_header()
            return

        self._last_search = results
        self._renderer.render_search_results(results)

        if self._header:
            crumbs = self._navigator.breadcrumb_parts()
            self._header.set_breadcrumbs(
                crumbs + [f'"{results.query_text}"']
            )
            self._header.set_nav_state(
                self._navigator.can_go_back,
                self._navigator.can_go_forward,
            )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate_back(self) -> None:
        self._last_search = None
        self._filters.clear()
        self._navigator.go_back()

    def navigate_forward(self) -> None:
        self._last_search = None
        self._filters.clear()
        self._navigator.go_forward()

    def navigate_to_index(self, index: int) -> None:
        self._last_search = None
        self._filters.clear()
        self._navigator.go_to_index(index)

    # ------------------------------------------------------------------
    # Rendu des pages
    # ------------------------------------------------------------------

    def _render_page(self, page: Page) -> None:
        self._last_search = None
        if isinstance(page, WorkspaceResult):
            self._renderer.render_workspace(page)
        elif isinstance(page, RobotBackup):
            loaded_before = page.loaded
            self._renderer.render_backup(page)
            if not loaded_before:
                self._vm.load_backup(page)
        elif isinstance(page, tuple):
            value = page[1]
            if isinstance(value, list):
                source_all = page[2] if len(page) > 2 else None
                self._renderer.render_subfields(value, source_all)
            elif hasattr(value, "items"):
                self._renderer.render_array(value)
            else:
                self._renderer.render_position(value)
        else:
            self._renderer.render_variable(page)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------

    def _notify_header(self) -> None:
        if self._header is None:
            return
        self._header.set_nav_state(
            self._navigator.can_go_back,
            self._navigator.can_go_forward,
        )
        self._header.set_breadcrumbs(self._navigator.breadcrumb_parts())

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_activate(self, iid: str) -> None:
        """Double-clic ou Entrée sur une ligne."""
        if self._last_search is not None and iid.startswith("hit_"):
            self._activate_search_hit(iid)
            return
        self._navigator.activate(iid)

    def _on_filter_change(self, query: str, scope: str) -> None:
        """Appelé à chaque frappe ou changement de scope."""
        self._vm.search(query, scope)

    # ------------------------------------------------------------------
    # Navigation depuis un hit de recherche
    # ------------------------------------------------------------------

    def _activate_search_hit(self, iid: str) -> None:
        """Navigue vers la variable source d'un résultat de recherche."""
        if self._last_search is None:
            return

        hit_id = int(iid.removeprefix("hit_"))
        hit = next(
            (h for h in self._last_search.hits if id(h) == hit_id), None
        )
        if hit is None:
            return

        ws = self._vm.workspace
        if ws is None:
            return

        backup = next(
            (b for b in ws.backups if b.name == hit.backup_name), None
        )
        if backup is None:
            return

        # Trouver la variable (priorité au fichier source pour les backups
        # contenant plusieurs fichiers .VA avec des noms identiques)
        var = next(
            (v for v in backup.variables
             if v.name == hit.variable_name
             and (not hit.source_file
                  or v.source_file is None
                  or v.source_file.name == hit.source_file)),
            None,
        ) or next(
            (v for v in backup.variables if v.name == hit.variable_name),
            None,
        )

        if var is None:
            self.append_log(
                f"Variable {hit.variable_name!r} introuvable dans {hit.backup_name}",
                "warning",
            )
            return

        # Effacer la recherche et naviguer
        self._last_search = None
        self._filters.clear()
        self._navigator.go_to(ws, clear_history=True)
        self._navigator.go_to(backup)
        self._navigator.go_to(var)