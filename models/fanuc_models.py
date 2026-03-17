"""
Modèles de données — représentations fidèles de la structure des fichiers .VA FANUC.

Convention sur ``FieldValue`` :
  - ``str``          : valeur scalaire normalisée (y compris ``"Uninitialized"``)
  - ``ArrayValue``   : tableau indexé de valeurs scalaires
  - ``PositionValue``: position cartésienne/articulaire multilignes
  - ``None``         : absence de valeur (variable non encore parsée ou sans bloc de valeur)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StorageType(Enum):
    CMOS    = "CMOS"
    SHADOW  = "SHADOW"
    UNKNOWN = "?"


class AccessType(Enum):
    RW      = "RW"
    RO      = "RO"
    FP      = "FP"
    WO      = "WO"
    UNKNOWN = "?"


class VADataType(Enum):
    INTEGER   = "INTEGER"
    REAL      = "REAL"
    BOOLEAN   = "BOOLEAN"
    STRING    = "STRING"
    POSITION  = "POSITION"
    XYZWPR    = "XYZWPR"
    XYZWPREXT = "XYZWPREXT"
    STRUCT    = "STRUCT"
    UNKNOWN   = "?"


@dataclass
class ArrayValue:
    """Tableau indexé de valeurs scalaires (index entier → valeur ou None)."""

    items: dict[tuple[int, ...], str | None] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Array({len(self.items)} items)"


@dataclass
class PositionValue:
    """Position FANUC multilignes (Group/Config/X/Y/Z/W/P/R)."""

    raw_lines: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return " | ".join(self.raw_lines)


ScalarValue = str | None
FieldValue  = ScalarValue | ArrayValue | PositionValue


@dataclass
class SystemVarField:
    """Champ d'une variable structurée ou tableau de structs FANUC.

    ``parent_index_nd`` encode l'index parent sous forme de tuple :
      - ``None``   : le field n'appartient pas à un élément de tableau (struct simple)
      - ``(i,)``   : tableau 1D, élément ``i``
      - ``(i, j)`` : tableau 2D, élément ``[i, j]``
      - etc.
    """

    full_name:       str                        # nom complet tel qu'il apparaît dans le .VA
    parent_var:      str                        # variable parente (ex: ``"$AP_CUREQ"``, ``"NFPAM.TBC"``)
    field_name:      str                        # nom du field seul (ex: ``"$PANE_EQNO"``, ``"CNT_SCALE"``)
    access:          AccessType
    data_type:       VADataType
    type_detail:     str                        # type brut (ex: ``"SHORT"``, ``"ARRAY[9] OF BYTE"``)
    value:           FieldValue
    parent_index_nd: tuple[int, ...] | None = None

    @property
    def parent_index(self) -> int | None:
        """Premier index du parent (rétrocompat). ``None`` si struct simple."""
        return self.parent_index_nd[0] if self.parent_index_nd else None


@dataclass
class RobotVariable:
    """Variable système ou Karel extraite d'un fichier .VA FANUC.

    ``array_size``  : produit total des dimensions (ex: 4×200 = 800).
    ``array_shape`` : dimensions exactes sous forme de tuple (ex: ``(4, 200)``).
                      ``None`` pour les tableaux 1D (la taille suffit) et les scalaires.
    """

    name:        str
    namespace:   str                             # contenu entre crochets (ex: ``"*SYSTEM*"``, ``"TBSWMD45"``)
    storage:     StorageType
    access:      AccessType
    data_type:   VADataType
    type_detail: str
    is_array:    bool
    array_size:  int | None
    array_shape: tuple[int, ...] | None = None
    value:       FieldValue = None
    fields:      list[SystemVarField] = field(default_factory=list)
    source_file: Path | None = None
    line_number: int | None = None

    @property
    def type_str(self) -> str:
        """Type pur sans valeur inline (ex: 'INTEGER = 0' → 'INTEGER').

        Utilisé partout où le type_detail doit être affiché sans sa valeur.
        """
        return self.type_detail.split("=")[0].strip()

    @property
    def is_system(self) -> bool:
        """``True`` si la variable appartient au namespace ``*SYSTEM*``."""
        return self.namespace == "*SYSTEM*"

    @property
    def is_struct(self) -> bool:
        """``True`` si la variable possède des fields."""
        return bool(self.fields)

    def to_dict(self) -> dict:
        return {
            "name":        self.name,
            "namespace":   self.namespace,
            "storage":     self.storage.value,
            "access":      self.access.value,
            "type":        self.type_detail,
            "is_array":    self.is_array,
            "array_size":  self.array_size,
            "array_shape": list(self.array_shape) if self.array_shape else None,
            "value":       _serialize_value(self.value),
            "fields":      [_field_to_dict(f) for f in self.fields],
            "source":      str(self.source_file) if self.source_file else None,
            "line":        self.line_number,
        }


@dataclass
class ExtractionResult:
    """Résultat agrégé d'une extraction sur un fichier ou un dossier."""

    input_dir:  Path
    variables:  list[RobotVariable] = field(default_factory=list)
    errors:     list[str] = field(default_factory=list)

    @property
    def var_count(self) -> int:
        return len(self.variables)

    @property
    def field_count(self) -> int:
        return sum(len(v.fields) for v in self.variables)



class ConversionStatus(Enum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED  = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class ConversionResult:
    source_path:   Path
    output_path:   Path | None = None
    status:        ConversionStatus = ConversionStatus.PENDING
    error_message: str | None = None
    duration_s:    float | None = None



def _serialize_value(value: FieldValue) -> object:
    if value is None:
        return None
    if isinstance(value, ArrayValue):
        return {",".join(str(i) for i in k): v for k, v in value.items.items()}
    if isinstance(value, PositionValue):
        return " | ".join(value.raw_lines)
    return value


def _field_to_dict(f: SystemVarField) -> dict:
    return {
        "full_name":       f.full_name,
        "field_name":      f.field_name,
        "parent_index_nd": list(f.parent_index_nd) if f.parent_index_nd is not None else None,
        "access":          f.access.value,
        "type":            f.type_detail,
        "value":           _serialize_value(f.value),
    }