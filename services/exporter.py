"""
Service d'export des variables extraites.
Supporte trois formats (Pattern Strategy) :
  csv      → une ligne par variable (résumé)
  csv_flat → une ligne par field ou par entrée de tableau (exhaustif)
  json     → structure complète

Corrections appliquées
──────────────────────
- ``_SUPPORTED`` était désynchronisé du dict ``dispatch`` :
  contenait ``{"csv", "label"}`` alors que les handlers réels sont
  ``csv``, ``csv_flat`` et ``json``. Le format ``"label"`` n'avait pas de
  handler et ``"csv_flat"``/``"json"`` étaient rejetés par la garde avant
  d'atteindre le dispatch.
  → ``_SUPPORTED`` est maintenant dérivé directement des clés du dict de
  dispatch, garantissant une cohérence structurelle permanente.
"""

from __future__ import annotations
import csv
import json
import logging
from pathlib import Path
from typing import Callable

from models.fanuc_models import RobotVariable, RobotVarField, ArrayValue, _serialize_value

logger = logging.getLogger(__name__)


_MAX_ND_DIMS = 7

# Type interne d'une stratégie d'export
_ExportFn = Callable[[list[RobotVariable], Path], None]


class ExportError(Exception):
    pass


class VariableExporter:
    """Exporte une liste de ``RobotVariable`` vers CSV (résumé ou flat) ou JSON.

    Les formats supportés sont définis par ``_DISPATCH`` ; la liste ``_SUPPORTED``
    en est dérivée automatiquement — plus aucun risque de désynchronisation.
    """

    # Dispatch table — source de vérité unique des formats supportés.
    # Ajouter un format = ajouter une entrée ici + écrire la méthode statique.
    @classmethod
    def _build_dispatch(cls) -> dict[str, _ExportFn]:
        return {
            "csv":      cls._csv_summary,
            "csv_flat": cls._csv_flat,
            "json":     cls._json,
        }

    def export(self, variables: list[RobotVariable], path: Path, fmt: str = "csv") -> None:
        """Exporte les variables vers le fichier indiqué dans le format demandé.

        :param variables: liste de variables à exporter.
        :param path: chemin de destination (le dossier parent est créé si absent).
        :param fmt: ``"csv"``, ``"csv_flat"`` ou ``"json"``.
        :raises ExportError: si le format n'est pas supporté ou si l'écriture échoue.
        """
        fmt = fmt.lower()
        dispatch = self._build_dispatch()

        if fmt not in dispatch:
            supported = sorted(dispatch.keys())
            raise ExportError(
                f"Format non supporté : {fmt!r}. Formats disponibles : {supported}"
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            dispatch[fmt](variables, path)
        except OSError as exc:
            raise ExportError(f"Écriture impossible vers {path} : {exc}") from exc

        logger.info("Export %s → %s (%d vars)", fmt.upper(), path, len(variables))

    # ------------------------------------------------------------------
    # Stratégies d'export
    # ------------------------------------------------------------------

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
        ``index_1``, ``index_2``, … jusqu'à ``_MAX_ND_DIMS`` dimensions.
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

        def _base(var: RobotVariable) -> dict[str, str]:
            return {
                "namespace": var.namespace,
                "variable":  var.name,
                "storage":   var.storage.value,
                "access":    var.access.value,
            }

        def _write_array(
            w: csv.DictWriter,
            base: dict[str, str],
            field_name: str,
            type_detail: str,
            array: ArrayValue,
            nd_prefix: tuple[int, ...] | None = None,
        ) -> None:
            """Écrit une ligne par entrée d'un ``ArrayValue``."""
            for item_key, item_val in array.items.items():
                nd = (*(nd_prefix or ()), *item_key)
                w.writerow({
                    **base,
                    "field": field_name,
                    "type":  type_detail,
                    **_idx_cells(nd),
                    "value": item_val,
                })

        def _write_field(w: csv.DictWriter, base: dict[str, str], fld: RobotVarField) -> None:
            """Écrit une ou plusieurs lignes pour un field."""
            if isinstance(fld.value, ArrayValue):
                _write_array(w, base, fld.field_name, fld.type_detail,
                             fld.value, nd_prefix=fld.parent_index_nd)
            else:
                w.writerow({
                    **base,
                    "field": fld.field_name,
                    "type":  fld.type_detail,
                    **_idx_cells(fld.parent_index_nd),
                    "value": _serialize_value(fld.value),
                })

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for var in variables:
                base = _base(var)
                if not var.fields:
                    if isinstance(var.value, ArrayValue):
                        _write_array(w, base, "", var.type_detail, var.value)
                    else:
                        w.writerow({
                            **base,
                            "field": "",
                            "type":  var.type_detail,
                            **_idx_cells(None),
                            "value": _serialize_value(var.value),
                        })
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