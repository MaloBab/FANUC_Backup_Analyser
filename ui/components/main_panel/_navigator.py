"""
PageNavigator — logique de navigation par historique.

Gère l'historique ← / →, le fil d'Ariane, et l'activation des items.
Ne sait pas comment rendre les pages — délègue au PageRenderer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from models.fanuc_models import (
    ArrayValue, PositionValue, RobotBackup, RobotVarField,
    RobotVariable, WorkspaceResult,
)
from ui.components.main_panel._helpers import (
    field_path, get_descendants, has_children, index_str,
)
from ui.components.main_panel.results_tree import ResultsTree

if TYPE_CHECKING:
    pass

# Type de page de navigation
Page = (
    WorkspaceResult
    | RobotBackup
    | RobotVariable
    | tuple[str, ArrayValue | PositionValue | list[RobotVarField], list[RobotVarField]]
    | tuple[str, ArrayValue | PositionValue]
)


class PageNavigator:
    """Gère l'historique de navigation et délègue le rendu via callback."""

    def __init__(
        self,
        tree: ResultsTree,
        render_page: Callable[[Page], None],
        notify_header: Callable[[], None],
        clear_filter: Callable[[], None],
        load_backup: Callable[[RobotBackup], None],
    ) -> None:
        self._tree          = tree
        self._render_page   = render_page
        self._notify_header = notify_header
        self._clear_filter  = clear_filter
        self._load_backup   = load_backup

        self._history: list[Page] = []
        self._future:  list[Page] = []
        self._current: Page | None = None

    # ------------------------------------------------------------------
    # Propriétés
    # ------------------------------------------------------------------

    @property
    def current(self) -> Page | None:
        return self._current

    @property
    def can_go_back(self) -> bool:
        return bool(self._history)

    @property
    def can_go_forward(self) -> bool:
        return bool(self._future)

    # ------------------------------------------------------------------
    # Navigation publique
    # ------------------------------------------------------------------

    def go_to(self, page: Page, clear_history: bool = False) -> None:
        if clear_history:
            self._history.clear()
            self._future.clear()
        elif self._current is not None:
            self._history.append(self._current)
            self._future.clear()
        self._current = page
        self._clear_filter()
        self._render_page(page)
        self._notify_header()

    def go_back(self) -> None:
        if not self._history:
            return
        if self._current is not None:
            self._future.append(self._current)
        self._current = self._history.pop()
        self._clear_filter()
        self._render_page(self._current)
        self._notify_header()

    def go_forward(self) -> None:
        if not self._future:
            return
        if self._current is not None:
            self._history.append(self._current)
        self._current = self._future.pop()
        self._clear_filter()
        self._render_page(self._current)
        self._notify_header()

    def go_to_index(self, index: int) -> None:
        """Clic breadcrumb — revient à la page à cet index dans l'historique."""
        full_path = self._history + ([self._current] if self._current else [])
        if index < 0 or index >= len(full_path):
            return
        self._future.clear()
        self._history = full_path[:index]
        self._current = full_path[index]
        self._clear_filter()
        self._render_page(self._current)
        self._notify_header()

    def refresh(self) -> None:
        """Réaffiche la page courante (après chargement backup ou filtre)."""
        if self._current is not None:
            self._render_page(self._current)

    # ------------------------------------------------------------------
    # Breadcrumbs
    # ------------------------------------------------------------------

    def breadcrumb_parts(self) -> list[str]:
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
    # Activation d'un item (double-clic / Entrée)
    # ------------------------------------------------------------------

    def activate(self, iid: str) -> None:
        page = self._current

        if isinstance(page, WorkspaceResult):
            backup = next((b for b in page.backups if str(id(b)) == iid), None)
            if backup:
                if not backup.loaded:
                    self._load_backup(backup)
                self.go_to(backup)

        elif isinstance(page, RobotBackup):
            var = next((v for v in page.variables if str(id(v)) == iid), None)
            if var and has_children(var):
                self.go_to(var)

        elif isinstance(page, RobotVariable):
            self._activate_variable(page, iid)

        elif isinstance(page, tuple):
            self._activate_tuple(page, iid)

    # ------------------------------------------------------------------
    # Activation interne
    # ------------------------------------------------------------------

    def _activate_variable(self, var: RobotVariable, iid: str) -> None:
        if var.fields:
            fld = next((f for f in var.fields if str(id(f)) == iid), None)
            if fld is None:
                return
            if isinstance(fld.value, ArrayValue) and not fld.value.items:
                all_desc = get_descendants(fld, var.fields)
                if all_desc:
                    p       = field_path(fld)
                    direct  = [f for f in all_desc if f.parent_var == p]
                    display = direct if direct else all_desc
                    self.go_to((fld.field_name, display, var.fields))
            elif isinstance(fld.value, (ArrayValue, PositionValue)):
                self.go_to((fld.field_name, fld.value))

        elif isinstance(var.value, ArrayValue):
            row_idx   = self._tree.index_of(iid)
            arr_items = list(var.value.items.items())
            if row_idx < len(arr_items):
                key, val = arr_items[row_idx]
                if isinstance(val, PositionValue):
                    self.go_to(("[" + ",".join(str(k) for k in key) + "]", val))

    def _activate_tuple(self, page: tuple, iid: str) -> None:  # type: ignore[type-arg]
        value = page[1]

        if isinstance(value, ArrayValue):
            row_idx   = self._tree.index_of(iid)
            arr_items = list(value.items.items())
            if row_idx < len(arr_items):
                key, val = arr_items[row_idx]
                if isinstance(val, PositionValue):
                    self.go_to(("[" + ",".join(str(k) for k in key) + "]", val))

        elif isinstance(value, list):
            source_all: list[RobotVarField] = page[2] if len(page) > 2 else value  # type: ignore[misc]
            fld = next((f for f in value if str(id(f)) == iid), None)
            if fld is None:
                return
            if isinstance(fld.value, ArrayValue) and not fld.value.items:
                all_desc = get_descendants(fld, source_all)
                if all_desc:
                    p       = field_path(fld)
                    direct  = [f for f in all_desc if f.parent_var == p]
                    display = direct if direct else all_desc
                    label   = f"{index_str(fld.parent_index_nd)}.{fld.field_name}"
                    self.go_to((label, display, source_all))
            elif isinstance(fld.value, (ArrayValue, PositionValue)):
                self.go_to((fld.field_name, fld.value))