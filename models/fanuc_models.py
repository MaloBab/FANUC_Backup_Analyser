"""
Modèles de données — représentations fidèles de la structure des fichiers .VA FANUC.

Convention sur FieldValue :
  - str          : valeur scalaire normalisée (y compris "Uninitialized")
  - ArrayValue   : tableau indexé — items scalaires ou PositionValue (ARRAY OF POSITION)
  - PositionValue: position cartésienne/articulaire multilignes (variable scalaire POSITION)
  - None         : absence de valeur (variable non encore parsée ou sans bloc de valeur)

Modification
────────────
PositionValue reçoit un champ label: str (défaut "") qui stocke le nom
de la position tel qu'il apparaît dans le fichier .VA entre apostrophes, par exemple
'OR_Get_Ref' ou '' pour une position sans nom.
Ce label est utilisé par le renderer pour afficher une preview significative au lieu
de [N lignes].
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class StorageType(Enum):
    CMOS    = "CMOS"
    SHADOW  = "SHADOW"
    DRAM    = "DRAM"
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
    """Tableau indexé de valeurs scalaires ou de positions (index N-D → valeur).

    Les items peuvent être :
      - str           : valeur scalaire (y compris "Uninitialized")
      - None          : absence de valeur
      - PositionValue : position FANUC (cas ARRAY[N] OF POSITION)
    """

    items: dict[tuple[int, ...], str | None | PositionValue] = field(default_factory=dict)

    def __repr__(self) -> str:
        n_pos = sum(1 for v in self.items.values() if isinstance(v, PositionValue))
        if n_pos:
            return f"Array({len(self.items)} positions)"
        return f"Array({len(self.items)} items)"


@dataclass
class PositionValue:
    """Position FANUC multilignes (Group/Config/X/Y/Z/W/P/R ou J1..J9).

    label stocke le nom entre apostrophes tel qu'il apparaît dans le fichier .VA,
    par exemple "OR_Get_Ref" ou "" pour une position sans nom.
    Ce champ est utilisé par le renderer pour afficher une preview significative.
    """

    raw_lines: list[str] = field(default_factory=list)
    label:     str       = ""

    def __repr__(self) -> str:
        return " | ".join(self.raw_lines)

    @property
    def display_label(self) -> str:
        """Label d'affichage : nom de la position si disponible, sinon chaîne vide."""
        return self.label


ScalarValue = str | None
FieldValue  = ScalarValue | ArrayValue | PositionValue


@dataclass
class RobotVarField:
    """Champ d'une variable structurée ou tableau de structs FANUC.

    parent_index_nd encode l'index parent sous forme de tuple :
      - None   : le field n'appartient pas à un élément de tableau (struct simple)
      - (i,)   : tableau 1D, élément i
      - (i, j) : tableau 2D, élément [i, j]
      - etc.

    condition_handler est optionnel et n'est renseigné que par le
    DataIdCsvParser (colonne ConditionHandler du DATAID.CSV).
    Il vaut "" pour toutes les variables issues des fichiers .VA.
    """

    full_name:         str                        # nom complet tel qu'il apparaît dans le .VA
    parent_var:        str                        # variable parente (ex: "$AP_CUREQ", "NFPAM.TBC")
    field_name:        str                        # nom du field seul (ex: "$PANE_EQNO", "CNT_SCALE")
    access:            AccessType
    data_type:         VADataType
    type_detail:       str                        # type brut pur (ex: "SHORT", "ARRAY[9] OF BYTE")
    value:             FieldValue
    parent_index_nd:   tuple[int, ...] | None = None
    condition_handler: str                    = ""

    @property
    def parent_index(self) -> int | None:
        """Premier index du parent (rétrocompat). None si struct simple."""
        return self.parent_index_nd[0] if self.parent_index_nd else None


@dataclass
class RobotVariable:
    """Variable système ou Karel extraite d'un fichier .VA FANUC.

    array_size  : produit total des dimensions (ex: 4×200 = 800).
    array_shape : dimensions exactes sous forme de tuple (ex: (4, 200)).
                      None pour les tableaux 1D (la taille suffit) et les scalaires.
    """

    name:        str
    namespace:   str                             # contenu entre crochets (ex: "*SYSTEM*", "TBSWMD45")
    storage:     StorageType
    access:      AccessType
    data_type:   VADataType
    type_detail: str
    is_array:    bool
    array_size:  int | None
    array_shape: tuple[int, ...] | None = None
    value:       FieldValue = None
    fields:      list[RobotVarField] = field(default_factory=list)
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
        """True si la variable appartient au namespace *SYSTEM*."""
        return self.namespace == "*SYSTEM*"

    @property
    def is_struct(self) -> bool:
        """True si la variable possède des fields."""
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


# ---------------------------------------------------------------------------
# Navigation multi-backups
# ---------------------------------------------------------------------------

@dataclass
class RobotBackup:
 
    name:      str
    path:      Path
    format:    str                 = "unknown"
    variables: list               = field(default_factory=list)
    errors:    list[str]          = field(default_factory=list)
    loaded:    bool               = False
 
    tp_programs: list[TpProgram] = field(default_factory=list)
 
    @property
    def var_count(self) -> int:
        return len(self.variables)
 
    @property
    def field_count(self) -> int:
        return sum(len(v.fields) for v in self.variables)
 
    @property
    def tp_count(self) -> int:
        """Nombre de programmes .TP convertis en .LS."""
        return len(self.tp_programs)


@dataclass
class WorkspaceResult:
    """Résultat d'un scan de dossier racine multi-robots."""
    root_path: Path
    backups:   list[RobotBackup] = field(default_factory=list)

    @property
    def robot_count(self) -> int:
        return len(self.backups)

    @property
    def loaded_count(self) -> int:
        return sum(1 for b in self.backups if b.loaded)


# ---------------------------------------------------------------------------
# Programme TP
# ---------------------------------------------------------------------------

@dataclass
class TpProgram:
    """Programme robot FANUC converti depuis .TP vers .LS.
 
    ``name``      : nom du programme sans extension (ex: ``"MAIN"``)
    ``ls_path``   : chemin absolu du fichier ``.LS`` produit par PrintTP
    ``content``   : contenu brut du fichier .LS (``None`` avant chargement)
    ``load_error``: message d'erreur si la lecture a échoué (``None`` si OK)
    """
 
    name:       str
    ls_path:    Path
    content:    str | None = None
    load_error: str | None = None
    
    def load(self) -> None:
        """Charge le contenu du .LS en mémoire. Idempotent, sans exception."""
        if self.content is not None:
            return
        try:
            self.content = self.ls_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self.load_error = str(exc)
            
 
    @property
    def is_loaded(self) -> bool:
        return self.content is not None
 
    @property
    def line_count(self) -> int:
        if self.content is None:
            return 0
        return self.content.count("\n") + 1
 


# ---------------------------------------------------------------------------
# Modèles de conversion
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sérialiseurs (utilisés par to_dict et l'exporter)
# ---------------------------------------------------------------------------

def _serialize_value(value: FieldValue) -> object:
    if value is None:
        return None
    if isinstance(value, ArrayValue):
        return {
            ",".join(str(i) for i in k): (
                " | ".join(v.raw_lines) if isinstance(v, PositionValue) else v
            )
            for k, v in value.items.items()
        }
    if isinstance(value, PositionValue):
        return " | ".join(value.raw_lines)
    return value


def _field_to_dict(f: RobotVarField) -> dict:
    d = {
        "full_name":       f.full_name,
        "field_name":      f.field_name,
        "parent_index_nd": list(f.parent_index_nd) if f.parent_index_nd is not None else None,
        "access":          f.access.value,
        "type":            f.type_detail,
        "value":           _serialize_value(f.value),
    }
    if f.condition_handler:
        d["condition_handler"] = f.condition_handler
    return d