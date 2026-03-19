"""
MainPanel — navigateur par arborescence.

Orchestre trois sous-composants :
  PageNavigator  (_navigator.py) — historique ← / → + activation
  PageRenderer   (_renderer.py)  — rendu Treeview pour chaque type de page
  ResultsTree    (results_tree)  — widget Treeview + scrollbars
  LogTab         (log_tab)       — journal horodaté
  FiltersBar     (filters_bar)   — filtre texte + scope pills
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
from models.fanuc_models import (
    ExtractionResult, RobotBackup, WorkspaceResult,
)

if TYPE_CHECKING:
    from ui.components.header import HeaderBar


class MainPanel(tk.Frame):
    def __init__(self, parent: tk.Misc, vm: AppViewModel) -> None:
        super().__init__(parent, bg=PALETTE["bg"])
        self._vm     = vm
        self._header: HeaderBar | None = None
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        # FiltersBar (row 0)
        self._filters = FiltersBar(self, on_filter_change=self._on_filter_change)
        self._filters.grid(row=0, column=0, sticky="ew")

        # Notebook (row 1)
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
        log_tab_frame = tk.Frame(nb, bg=PALETTE["bg_card"])
        log_tab_frame.rowconfigure(0, weight=1)
        log_tab_frame.columnconfigure(0, weight=1)
        nb.add(log_tab_frame, text="  Journal  ")

        self._log = LogTab(log_tab_frame)
        self._log.grid(row=0, column=0, sticky="nsew")

        # Renderer + Navigator
        self._renderer = PageRenderer(self._tree, self._filters)
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
        self._navigator.refresh()

    def set_scope_filter(self, scope: str) -> None:
        self._filters.set_scope(scope)

    def append_log(self, message: str, level: str = "info") -> None:
        self._log.append(message, level)

    # ------------------------------------------------------------------
    # Navigation (délégué)
    # ------------------------------------------------------------------

    def navigate_back(self) -> None:
        self._navigator.go_back()

    def navigate_forward(self) -> None:
        self._navigator.go_forward()

    def navigate_to_index(self, index: int) -> None:
        self._navigator.go_to_index(index)

    # ------------------------------------------------------------------
    # Rendu des pages (dispatcher)
    # ------------------------------------------------------------------

    def _render_page(self, page: Page) -> None:
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
                # page[2] = source_all (tous les fields de la variable source)
                source_all = page[2] if len(page) > 2 else None  # type: ignore[misc]
                self._renderer.render_subfields(value, source_all)
            elif hasattr(value, 'items'):  # ArrayValue
                self._renderer.render_array(value)  # type: ignore[arg-type]
            else:
                self._renderer.render_position(value)  # type: ignore[arg-type]
        else:
            self._renderer.render_variable(page)  # type: ignore[arg-type]

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
        self._navigator.activate(iid)

    def _on_filter_change(self, _query: str, _scope: str) -> None:
        self._navigator.refresh()