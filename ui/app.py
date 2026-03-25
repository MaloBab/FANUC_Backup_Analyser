"""
ui/app.py
─────────
Fenêtre racine — point d'entrée de la couche UI.

Correction appliquée
────────────────────
``AppViewModel`` reçoit désormais ``root`` directement dans son constructeur
via le paramètre ``tk_root``. L'ancien appel ``vm.set_tk_root(root)`` dans
``_bind_viewmodel()`` est supprimé — il restait une fenêtre de temps entre
la construction du ViewModel et l'injection de la root, pendant laquelle
tout déclenchement d'un polling aurait causé un ``AttributeError`` (accès à
``self._tk_root`` avant affectation).

La référence ``tk.Misc`` est utilisée comme type (et non ``tk.Tk``) pour
permettre l'injection d'un frame ou d'un toplevel dans les tests.
"""

from __future__ import annotations
import tkinter as tk

from config.settings import Settings
from models.fanuc_models import RobotBackup, WorkspaceResult
from models.search_models import SearchResults
from ui.theme import apply_theme
from ui.components.header import HeaderBar
from ui.components.sidebar import SidebarPanel
from ui.components.main_panel.main_panel import MainPanel
from ui.components.statusbar import StatusBar
from ui.viewmodel import AppViewModel


class App:
    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self._root     = root
        self._settings = settings

        # CORRECTIF : tk_root injecté dès la construction — plus de set_tk_root()
        # post-construction qui laissait une fenêtre de temps sans référence valide.
        self._viewmodel = AppViewModel(settings, tk_root=root)

        self._configure_root()
        apply_theme(self._root)
        self._build_layout()
        self._bind_viewmodel()

    def _configure_root(self) -> None:
        self._root.title(self._settings.window_title)
        self._root.geometry(self._settings.window_size)
        self._root.minsize(900, 600)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

    def _build_layout(self) -> None:
        vm = self._viewmodel
        self._header = HeaderBar(
            self._root,
            on_back=self._on_nav_back,
            on_forward=self._on_nav_forward,
            on_breadcrumb=self._on_breadcrumb_click,
            vm=vm,
        )
        self._header.grid(row=0, column=0, sticky="ew")

        center = tk.Frame(self._root)
        center.grid(row=1, column=0, sticky="nsew")
        center.columnconfigure(1, weight=1)
        center.rowconfigure(0, weight=1)

        self._sidebar = SidebarPanel(center, vm)
        self._sidebar.grid(row=0, column=0, sticky="ns")

        self._main = MainPanel(center, vm)
        self._main.grid(row=0, column=1, sticky="nsew")

        self._statusbar = StatusBar(self._root, vm)
        self._statusbar.grid(row=2, column=0, sticky="ew")

        self._main.set_header(self._header)

    def _on_nav_back(self) -> None:
        self._main.navigate_back()

    def _on_nav_forward(self) -> None:
        self._main.navigate_forward()

    def _on_breadcrumb_click(self, index: int) -> None:
        self._main.navigate_to_index(index)

    def _on_workspace_ready(self, workspace: WorkspaceResult) -> None:
        self._sidebar.populate_workspace(workspace)
        self._main.display_workspace(workspace)

    def _on_backup_loaded(self, backup: RobotBackup) -> None:
        self._sidebar.mark_backup_loaded(backup)
        self._main.refresh_screen()

    def _on_search_results(self, results: SearchResults) -> None:
        self._main.display_search_results(results)

    def _bind_viewmodel(self) -> None:
        # CORRECTIF : set_tk_root() supprimé — la root est déjà injectée dans
        # le constructeur d'AppViewModel. Ce site d'appel était redondant et
        # masquait le vrai moment d'injection.
        self._viewmodel.on_status_change   = self._statusbar.update_status
        self._viewmodel.on_progress_change = self._statusbar.update_progress
        self._viewmodel.on_log_message     = self._main.append_log
        self._viewmodel.on_scope_change    = self._main.set_scope_filter
        self._viewmodel.on_workspace_ready = self._on_workspace_ready
        self._viewmodel.on_backup_loaded   = self._on_backup_loaded
        self._viewmodel.on_search_results  = self._on_search_results