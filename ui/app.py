"""
App — contrôleur racine Tkinter.
Configure la fenêtre principale et instancie les vues.
"""

from __future__ import annotations
import tkinter as tk

from config.settings import Settings
from ui.theme import apply_theme
from ui.components.header import HeaderBar
from ui.components.sidebar import SidebarPanel
from ui.components.main_panel import MainPanel
from ui.components.statusbar import StatusBar
from ui.viewmodel import AppViewModel


class App:
    """
    Classe racine : crée la fenêtre, injecte le ViewModel dans chaque composant.
    Pattern MVVM léger : ViewModel ↔ Composants UI.
    """

    def __init__(self, root: tk.Tk, settings: Settings) -> None:
        self._root = root
        self._settings = settings
        self._viewmodel = AppViewModel(settings)

        self._configure_root()
        apply_theme(self._root)
        self._build_layout()
        self._bind_viewmodel()

    # ------------------------------------------------------------------

    def _configure_root(self) -> None:
        self._root.title(self._settings.window_title)
        self._root.geometry(self._settings.window_size)
        self._root.minsize(900, 600)
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

    def _build_layout(self) -> None:
        vm = self._viewmodel

        # Barre de titre / actions globales
        self._header = HeaderBar(self._root, vm)
        self._header.grid(row=0, column=0, sticky="ew")

        # Zone centrale : sidebar + panel principal
        center = tk.Frame(self._root)
        center.grid(row=1, column=0, sticky="nsew")
        center.columnconfigure(1, weight=1)
        center.rowconfigure(0, weight=1)

        self._sidebar = SidebarPanel(center, vm)
        self._sidebar.grid(row=0, column=0, sticky="ns")

        self._main = MainPanel(center, vm)
        self._main.grid(row=0, column=1, sticky="nsew")

        # Barre de statut
        self._statusbar = StatusBar(self._root, vm)
        self._statusbar.grid(row=2, column=0, sticky="ew")

    def _bind_viewmodel(self) -> None:
        """Connecte les événements ViewModel → mise à jour UI."""
        self._viewmodel.set_tk_root(self._root)
        self._viewmodel.on_status_change = self._statusbar.update_status
        self._viewmodel.on_progress_change = self._statusbar.update_progress
        self._viewmodel.on_results_ready = self._main.display_results
        self._viewmodel.on_log_message = self._main.append_log
        self._viewmodel.on_scope_change = self._main.set_scope_filter