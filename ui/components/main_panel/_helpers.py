"""
Helpers purs pour le navigateur principal.

Fonctions sans dépendance Tkinter — toutes testables unitairement.
"""

from __future__ import annotations

from models.fanuc_models import ArrayValue, PositionValue, RobotVarField, RobotVariable


# ---------------------------------------------------------------------------
# Helpers de valeur / affichage
# ---------------------------------------------------------------------------

def has_children(var: RobotVariable) -> bool:
    """True si la variable a un niveau de détail navigable."""
    return bool(var.fields) or isinstance(var.value, (ArrayValue, PositionValue))


def display_value(var: RobotVariable) -> str:
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


def field_value_preview(
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
            kind = "positions" if any(
                isinstance(v, PositionValue) for v in fld.value.items.values()
            ) else "entrées"
            return f"[{n} {kind}]"
        if all_fields:
            sub = get_descendants(fld, all_fields)
            if sub:
                indices = {f.parent_index_nd for f in sub if f.parent_index_nd}
                n_items = len(indices)
                return f"[{n_items} élément{'s' if n_items > 1 else ''}]"
        return "[0 entrées]"
    if isinstance(fld.value, PositionValue):
        return f"[{len(fld.value.raw_lines)} lignes]"
    return "—"


def index_str(nd: tuple[int, ...] | None) -> str:
    if nd is None:
        return ""
    return "[" + ",".join(str(i) for i in nd) + "]"


def inner_type(type_detail: str) -> str:
    """Extrait le type interne d'un type tableau.

    ``"ARRAY[2] OF DMR_GRP_T"`` → ``"DMR_GRP_T"``,
    ``"INTEGER = 0"``           → ``"INTEGER"``
    """
    raw = type_detail.split("=")[0].strip()
    if raw.upper().startswith("ARRAY") and " OF " in raw.upper():
        return raw.split(" OF ", 1)[-1].strip()
    return raw


# ---------------------------------------------------------------------------
# Helpers de navigation hiérarchique
# ---------------------------------------------------------------------------

def field_path(fld: RobotVarField) -> str:
    """Retourne le préfixe de ``parent_var`` que les fils directs de ce field auront.

    Exemples :
      ``$ADJ_RTRQ`` avec ``parent_index_nd=(3,)`` et ``parent_var="$DIAG_GRP[1].$ADJ_RTRQ"``
      → ``"$DIAG_GRP[1].$ADJ_RTRQ[3].$EFF_AXIS"``
    """
    base = fld.parent_var
    if fld.parent_index_nd:
        idx = "[" + ",".join(str(i) for i in fld.parent_index_nd) + "]"
        return f"{base}{idx}.{fld.field_name}"
    return f"{base}.{fld.field_name}"


def get_descendants(fld: RobotVarField, all_fields: list[RobotVarField]) -> list[RobotVarField]:
    """Retourne tous les descendants (directs et profonds) d'un field.

    Un descendant ``f`` satisfait l'une des conditions :
    - ``f.parent_var == path``              → fils direct indexé via ``parent_index_nd``
    - ``f.parent_var.startswith(path+"[")`` → petit-fils indexé
    - ``f.parent_var.startswith(path+".")`` → petit-fils via struct imbriqué
    """
    p = field_path(fld)
    return [
        f for f in all_fields
        if f is not fld and (
            f.parent_var == p
            or f.parent_var.startswith(p + "[")
            or f.parent_var.startswith(p + ".")
        )
    ]