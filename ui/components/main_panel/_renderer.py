"""
PageRenderer — rendu Treeview pour chaque type de page.

Sépare la logique de rendu de la logique de navigation dans MainPanel.
Ne gère pas la navigation, seulement l'affichage.
"""

from __future__ import annotations

from models.fanuc_models import (
    ArrayValue, PositionValue, RobotBackup, RobotVarField,
    RobotVariable, WorkspaceResult,
)
from models.search_models import SearchResults
from ui.components.main_panel._helpers import (
    display_value, field_value_preview, index_str, inner_type,
)
from ui.components.main_panel.results_tree import ResultsTree
from ui.components.filters_bar import FiltersBar


class PageRenderer:
    """Effectue le rendu d'une page dans le ResultsTree."""

    def __init__(self, tree: ResultsTree, filters: FiltersBar) -> None:
        self._tree    = tree
        self._filters = filters

    # ------------------------------------------------------------------
    # Pages principales
    # ------------------------------------------------------------------

    def render_workspace(self, ws: WorkspaceResult) -> None:
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

    def render_backup(self, backup: RobotBackup) -> None:
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
                iid="_loading", tags=("loading",),
            )
            return

        self._tree.configure_columns([
            ("col1", "NameSpace",       70, "center", False),
            ("col2", "Name",     210, "w",      False),
            ("col3", "Storage",  70, "center", False),
            ("col4", "Type",    170, "w",      True),
            ("col5", "Value",  150, "w",      False),
            ("col6", "File", 130, "w",      False),
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
            val_disp = display_value(var)
            if not val_disp or val_disp == "Uninitialized":
                tags.append("uninit")
                val_disp = "Uninitialized"
            ns  = "" if var.is_system else var.namespace
            src = var.source_file.name if var.source_file else ""
            self._tree.insert(
                values=(ns, var.name, var.storage.value,
                        var.type_str[:60], val_disp[:60], src),
                iid=str(id(var)), tags=tuple(tags),
            )
        self._filters.set_count(f"{len(vars_)} variable(s)")

    def render_variable(self, var: RobotVariable) -> None:
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
            items = [f for f in var.fields if f.parent_var == var.name]
            if query:
                items = [f for f in items
                         if query in f.field_name.lower()
                         or query in f.type_detail.lower()]
            for i, fld in enumerate(items):
                self._insert_field_row(i, fld, var.fields)
            self._filters.set_count(f"{len(items)} field(s)")

        elif isinstance(var.value, ArrayValue):
            self.render_array_items(var.value)
        elif isinstance(var.value, PositionValue):
            self.render_position_lines(var.value)

    def render_subfields(
        self,
        fields: list[RobotVarField],
        source_all: list[RobotVarField] | None = None,
    ) -> None:
        self._tree.configure_columns([
            ("col1", "Index",   90, "center", False),
            ("col2", "Field",  220, "w",      False),
            ("col3", "Access",  65, "center", False),
            ("col4", "Type",   170, "w",      False),
            ("col5", "Valeur", 260, "w",      True),
        ])
        query = self._filters.query
        items = fields
        if query:
            items = [f for f in fields
                     if query in f.field_name.lower()
                     or query in f.type_detail.lower()]
        all_fields = source_all if source_all is not None else fields
        self._tree.clear()
        for i, fld in enumerate(items):
            self._insert_field_row(i, fld, all_fields)
        self._filters.set_count(f"{len(items)} field(s)")

    def render_array(self, arr: ArrayValue) -> None:
        self._tree.configure_columns([
            ("col1", "Index",   90, "center", False),
            ("col2", "",         0, "w",      False),
            ("col3", "",         0, "w",      False),
            ("col4", "",         0, "w",      False),
            ("col5", "Valeur",  400, "w",     True),
        ])
        self._tree.clear()
        self.render_array_items(arr)

    def render_position(self, pos: PositionValue) -> None:
        self._tree.configure_columns([
            ("col1", "",  0, "w", False), ("col2", "",  0, "w", False),
            ("col3", "",  0, "w", False), ("col4", "",  0, "w", False),
            ("col5", "Valeur", 600, "w", True),
        ])
        self._tree.clear()
        self.render_position_lines(pos)

    def render_search_results(self, results: SearchResults) -> None:
        """Page de résultats de recherche globale.

        Colonnes : Backup | File | Variable | Path | Value
        Chaque ligne est taguée ``nav`` : double-clic navigue vers la variable.
        """
        self._tree.configure_columns([
            ("col1", "Backup",   140, "w",      False),
            ("col2", "File",  110, "w",      False),
            ("col3", "Variable", 160, "w",      False),
            ("col4", "Path",   210, "w",      True),
            ("col5", "Value",   150, "w",      False),
        ])
        self._tree.clear()

        for i, hit in enumerate(results.hits):
            tags: list[str] = ["even" if i % 2 == 0 else "odd", "nav"]
            val_disp = (hit.match_value or "—")[:80]
            if val_disp == "Uninitialized":
                tags.append("uninit")
            self._tree.insert(
                values=(
                    hit.backup_name,
                    hit.source_file,
                    hit.variable_name,
                    hit.match_path[:80],
                    val_disp,
                ),
                iid=f"hit_{id(hit)}",
                tags=tuple(tags),
            )

        msg = f"{results.hit_count} résultat(s)"
        if results.hit_count >= 2000:
            msg += "  (limite atteinte)"
        self._filters.set_count(msg)

    # ------------------------------------------------------------------
    # Helpers de rendu bas niveau
    # ------------------------------------------------------------------

    def render_array_items(self, arr: ArrayValue) -> None:
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
                iid=f"arr_{i}_{key_str}", tags=tuple(tags_arr),
            )
        self._filters.set_count(f"{len(items)} entrée(s)")

    def render_position_lines(self, pos: PositionValue) -> None:
        for i, line in enumerate(pos.raw_lines):
            self._tree.insert(
                values=("", "", "", "", line),
                iid=f"pos_{i}",
                tags=("even" if i % 2 == 0 else "odd",),
            )
        self._filters.set_count(f"{len(pos.raw_lines)} ligne(s)")

    # ------------------------------------------------------------------
    # Insertion d'une ligne field
    # ------------------------------------------------------------------

    def _insert_field_row(
        self,
        row_index: int,
        fld: RobotVarField,
        all_fields: list[RobotVarField] | None = None,
    ) -> None:
        tags: list[str] = ["even" if row_index % 2 == 0 else "odd"]
        val = field_value_preview(fld, all_fields)
        if not val or val == "Uninitialized":
            tags.append("uninit")
            val = "Uninitialized"
        elif isinstance(fld.value, (ArrayValue, PositionValue)):
            tags.append("nav")
        elif val.startswith("[") and val.endswith("]"):
            tags.append("nav")

        is_scalar_child = (
            fld.parent_index_nd is not None
            and not fld.type_detail.upper().startswith("ARRAY")
        )
        type_disp = inner_type(fld.type_detail) if is_scalar_child else fld.type_detail

        self._tree.insert(
            values=(index_str(fld.parent_index_nd),
                    fld.field_name, fld.access.value,
                    type_disp[:50], val),
            iid=str(id(fld)), tags=tuple(tags),
        )