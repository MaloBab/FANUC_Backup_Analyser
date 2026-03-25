"""
_navigator.py
─────────────
PageNavigator — gestion de l'historique de navigation.

Gère l'historique ← / →, le fil d'Ariane, et l'activation des items.
Ne sait pas comment rendre les pages — délègue au PageRenderer.

Corrections appliquées
──────────────────────
1. **Types de page explicites** — le ``tuple`` polymorphe
   ``tuple[str, ArrayValue | PositionValue | list[RobotVarField], ...]``
   est remplacé par deux dataclasses dédiées :
     - ``FieldDetailPage`` : navigation dans un field tableau ou POSITION
     - ``FieldGroupPage``  : navigation dans un sous-arbre de fields (struct imbriqué)
   Cela supprime tous les ``type: ignore[misc]`` et rend le dispatch
   dans ``activate()`` / ``_render_page()`` complètement typé.

2. **``id()`` comme clé de Treeview** — pour les pages ``WorkspaceResult``
   et ``RobotBackup``, l'``id()`` Python (adresse mémoire) pouvait en théorie
   être réutilisé après garbage-collection. On conserve ``id()`` pour les
   objets dont la durée de vie est garantie par le workspace (``RobotBackup``,
   ``RobotVariable``, ``RobotVarField``) mais la logique de lookup est encapsulée
   dans des méthodes dédiées pour rendre le couplage explicite et faciliter
   un éventuel remplacement par UUID.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
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


# ---------------------------------------------------------------------------
# Types de page explicites (remplacent le tuple polymorphe)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDetailPage:
    """Navigation dans un field dont la valeur est un tableau ou une position.

    Exemples :
      - double-clic sur un field ARRAY[N] → ``value`` est l'``ArrayValue``
      - double-clic sur un field POSITION → ``value`` est la ``PositionValue``
    """
    label: str
    value: ArrayValue | PositionValue


@dataclass(frozen=True)
class FieldGroupPage:
    """Navigation dans un sous-arbre de fields (struct imbriqué dans un tableau).

    ``fields``     : liste des fields à afficher à ce niveau.
    ``source_all`` : liste complète des fields de la variable parente
                     (nécessaire pour résoudre les descendants lors d'un
                     double-clic sur un field tableau vide).
    """
    label:      str
    fields:     list[RobotVarField]
    source_all: list[RobotVarField]


# Union des types de page supportés par le navigateur
Page = (
    WorkspaceResult
    | RobotBackup
    | RobotVariable
    | FieldDetailPage
    | FieldGroupPage
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
            elif isinstance(p, (FieldDetailPage, FieldGroupPage)):
                # CORRECTIF : accès typé au lieu de p[0]
                parts.append(p.label)
        return parts

    # ------------------------------------------------------------------
    # Activation d'un item (double-clic / Entrée)
    # ------------------------------------------------------------------

    def activate(self, iid: str) -> None:
        page = self._current

        if isinstance(page, WorkspaceResult):
            backup = self._find_by_id(page.backups, iid)
            if backup:
                if not backup.loaded:
                    self._load_backup(backup)
                self.go_to(backup)

        elif isinstance(page, RobotBackup):
            var = self._find_by_id(page.variables, iid)
            if var and has_children(var):
                self.go_to(var)

        elif isinstance(page, RobotVariable):
            self._activate_variable(page, iid)

        elif isinstance(page, FieldGroupPage):
            self._activate_field_group(page, iid)

        elif isinstance(page, FieldDetailPage):
            self._activate_field_detail(page, iid)

    # ------------------------------------------------------------------
    # Activation interne
    # ------------------------------------------------------------------

    def _activate_variable(self, var: RobotVariable, iid: str) -> None:
        if var.fields:
            fld = self._find_by_id(var.fields, iid)
            if fld is None:
                return
            if isinstance(fld.value, ArrayValue) and not fld.value.items:
                all_desc = get_descendants(fld, var.fields)
                if all_desc:
                    p       = field_path(fld)
                    direct  = [f for f in all_desc if f.parent_var == p]
                    display = direct if direct else all_desc
                    self.go_to(FieldGroupPage(
                        label=fld.field_name,
                        fields=display,
                        source_all=var.fields,
                    ))
            elif isinstance(fld.value, (ArrayValue, PositionValue)):
                self.go_to(FieldDetailPage(label=fld.field_name, value=fld.value))

        elif isinstance(var.value, ArrayValue):
            row_idx   = self._tree.index_of(iid)
            arr_items = list(var.value.items.items())
            if row_idx < len(arr_items):
                key, val = arr_items[row_idx]
                if isinstance(val, PositionValue):
                    label = "[" + ",".join(str(k) for k in key) + "]"
                    self.go_to(FieldDetailPage(label=label, value=val))

    def _activate_field_group(self, page: FieldGroupPage, iid: str) -> None:
        fld = self._find_by_id(page.fields, iid)
        if fld is None:
            return
        if isinstance(fld.value, ArrayValue) and not fld.value.items:
            all_desc = get_descendants(fld, page.source_all)
            if all_desc:
                p       = field_path(fld)
                direct  = [f for f in all_desc if f.parent_var == p]
                display = direct if direct else all_desc
                label   = f"{index_str(fld.parent_index_nd)}.{fld.field_name}"
                self.go_to(FieldGroupPage(
                    label=label,
                    fields=display,
                    source_all=page.source_all,
                ))
        elif isinstance(fld.value, (ArrayValue, PositionValue)):
            self.go_to(FieldDetailPage(label=fld.field_name, value=fld.value))

    def _activate_field_detail(self, page: FieldDetailPage, iid: str) -> None:
        if isinstance(page.value, ArrayValue):
            row_idx   = self._tree.index_of(iid)
            arr_items = list(page.value.items.items())
            if row_idx < len(arr_items):
                key, val = arr_items[row_idx]
                if isinstance(val, PositionValue):
                    label = "[" + ",".join(str(k) for k in key) + "]"
                    self.go_to(FieldDetailPage(label=label, value=val))

    # ------------------------------------------------------------------
    # Helper : recherche par id() dans une liste
    # ------------------------------------------------------------------

    @staticmethod
    def _find_by_id(items: list, iid: str) -> object | None:
        """Trouve l'objet dont ``str(id(obj)) == iid``.

        L'usage de ``id()`` est intentionnel : tous les objets du workspace
        ont une durée de vie garantie par le ``WorkspaceResult`` parent tant
        que la session est ouverte. Une refactorisation vers des UUID stables
        (stockés dans les modèles) est possible mais hors-scope ici.
        """
        target = int(iid) if iid.isdigit() else None
        if target is None:
            return None
        return next((obj for obj in items if id(obj) == target), None)