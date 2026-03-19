"""
Service d'export des variables extraites.
Supporte trois formats (Pattern Strategy) :
  csv      → une ligne par variable (résumé)
  csv_flat → une ligne par field ou par entrée de tableau (exhaustif)
  json     → structure complète
"""

from __future__ import annotations
import csv
import json
import logging
from pathlib import Path

from models.fanuc_models import RobotVariable, RobotVarField, ArrayValue, _serialize_value

logger = logging.getLogger(__name__)


_MAX_ND_DIMS = 7


class ExportError(Exception):
    pass


class VariableExporter:
    """Exporte une liste de RobotVariable vers CSV (résumé ou flat) ou JSON."""

    _SUPPORTED = {"csv", "label"}

    def export(self, variables: list[RobotVariable], path: Path, fmt: str = "csv") -> None:
        """Exporte les variables vers le fichier indiqué dans le format demandé.

        :param variables: liste de variables à exporter.
        :param path: chemin de destination (le dossier parent est créé si absent).
        :param fmt: "csv", "label".
        :raises ExportError: si le format n'est pas supporté.
        """
        fmt = fmt.lower()
        if fmt not in self._SUPPORTED:
            raise ExportError(f"Format non supporté : {fmt!r}. Choix : {self._SUPPORTED}")
        path.parent.mkdir(parents=True, exist_ok=True)
        dispatch = {"csv": self._csv_summary, "csv_flat": self._csv_flat, "json": self._json}
        dispatch[fmt](variables, path)
        logger.info("Export %s → %s (%d vars)", fmt.upper(), path, len(variables))


    @staticmethod
    def _csv_summary(variables: list[RobotVariable], path: Path) -> None:
        """Une ligne par variable — résumé."""
        fieldnames = [
            "namespace", "name", "storage", "access",
            "type_detail", "is_array", "array_size", "value",
            "field_count", "source",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for var in variables:
                w.writerow({
                    "namespace":   var.namespace,
                    "name":        var.name,
                    "storage":     var.storage.value,
                    "access":      var.access.value,
                    "type_detail": var.type_detail,
                    "is_array":    var.is_array,
                    "array_size":  var.array_size,
                    "value":       _serialize_value(var.value),
                    "field_count": len(var.fields),
                    "source":      str(var.source_file) if var.source_file else "",
                })

    @staticmethod
    def _csv_flat(variables: list[RobotVariable], path: Path) -> None:
        """Une ligne par valeur scalaire — export exhaustif.

        Les index multidimensionnels sont répartis sur des colonnes séparées
        index_1, index_2, … jusqu'à _MAX_ND_DIMS dimensions.
        """
        idx_cols   = [f"index_{k}" for k in range(1, _MAX_ND_DIMS + 1)]
        fieldnames = [
            "namespace", "variable", "storage", "access",
            "field", "type",
        ] + idx_cols + ["value"]

        def _idx_cells(nd: tuple[int, ...] | None) -> dict[str, str]:
            """Répartit un index N-D sur les colonnes index_1…index_N."""
            cells: dict[str, str] = {c: "" for c in idx_cols}
            if nd:
                for k, v in enumerate(nd, start=1):
                    if k <= _MAX_ND_DIMS:
                        cells[f"index_{k}"] = str(v)
            return cells

        def _base(var: RobotVariable) -> dict:
            return {
                "namespace": var.namespace,
                "variable":  var.name,
                "storage":   var.storage.value,
                "access":    var.access.value,
            }

        def _write_array(
            w: csv.DictWriter,
            base: dict,
            field_name: str,
            type_detail: str,
            array: ArrayValue,
            nd_prefix: tuple[int, ...] | None = None,
        ) -> None:
            """Écrit une ligne par entrée d'un ArrayValue.

            La clé de chaque item est un tuple d'index (ex: (1,), (2, 3)).
            Si nd_prefix est fourni (index du field parent), il est préfixé aux index.
            """
            for item_key, item_val in array.items.items():
                nd = (*(nd_prefix or ()), *item_key)
                w.writerow({
                    **base,
                    "field": field_name,
                    "type":  type_detail,
                    **_idx_cells(nd),
                    "value": item_val,
                })

        def _write_field(w: csv.DictWriter, base: dict, fld: RobotVarField) -> None:
            """Écrit une ou plusieurs lignes pour un field."""
            val = _serialize_value(fld.value)
            if isinstance(fld.value, ArrayValue):
                _write_array(w, base, fld.field_name, fld.type_detail,
                             fld.value, nd_prefix=fld.parent_index_nd)
            else:
                w.writerow({
                    **base,
                    "field": fld.field_name,
                    "type":  fld.type_detail,
                    **_idx_cells(fld.parent_index_nd),
                    "value": val,
                })

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for var in variables:
                base = _base(var)
                if not var.fields:
                    val = _serialize_value(var.value)
                    if isinstance(var.value, ArrayValue):
                        _write_array(w, base, "", var.type_detail, var.value)
                    else:
                        w.writerow({**base, "field": "", "type": var.type_detail,
                                    **_idx_cells(None), "value": val})
                else:
                    for fld in var.fields:
                        _write_field(w, base, fld)

    @staticmethod
    def _json(variables: list[RobotVariable], path: Path) -> None:
        """Structure complète en JSON indenté."""
        path.write_text(
            json.dumps([v.to_dict() for v in variables], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )