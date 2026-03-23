"""
Parser DATAID.CSV
Format du fichier
─────────────────
Ligne 0  : ``DATAIDVER,<version>,!!!!,...``  — métadonnée de version
Ligne 1  : en-têtes — ``REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!``
Lignes N : ``DATAID,<nom>,<type>,<valeur>,<access>,<condition>,!!!!``  — variables
Dernière : ``END,...``

Structure d'un nom de variable
───────────────────────────────
Toutes les entrées suivent le schéma :  ``$PARENT.FIELD`` ou ``$PARENT.FIELD[i]``
ou ``$PARENT.FIELD[i,j]``.

  - ``$PARENT``  → équivalent du *namespace* dans les fichiers .VA
                   (ex : ``$ALARM``, ``$AFTER_POWER_CYCLE``)
  - ``FIELD``    → nom du champ (ex : ``AUTO_DISPLAY``, ``ERROR_SEVERITY_TABLE``)
  - ``[i]``      → index 1-D optionnel
  - ``[i,j]``   → index 2-D optionnel

Chaque ligne CSV correspond donc à un ``RobotVarField`` attaché à une
``RobotVariable`` parente synthétique (une par ``$PARENT`` unique).

Particularités
──────────────
- Encodage : UTF-8 avec BOM (``utf-8-sig``) — les autres encodages sont tentés
  en fallback.
- Valeur ``*Uninitialized*`` → normalisée en ``"Uninitialized"`` (cohérence .VA).
- Type ``POSITION`` → valeur inline ``Group:1/Axes:0/.../X:.../Y:...`` parsée
  en ``PositionValue`` (``raw_lines`` = liste des segments ``Clé:Valeur``).
- ``ConditionHandler`` → stocké dans ``RobotVarField.type_detail`` pour ne pas
  perdre l'information (pas de champ dédié dans le modèle actuel).
- Pas de champ ``Storage`` dans DATAID.CSV → ``StorageType.UNKNOWN``.
- ``CW`` (Condition Write) mappé sur ``AccessType.RO`` avec log (valeur inconnue
  des enums existants → traitement conservateur).
"""

from __future__ import annotations
import csv
import logging
import re
from pathlib import Path

from models.fanuc_models import (
    AccessType,
    ArrayValue,
    ExtractionResult,
    PositionValue,
    RobotVarField,
    RobotVariable,
    StorageType,
    VADataType,
)
from services.parser.base_parser import BackupParser, ProgressCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_DATAID_FILENAME = "DATAID.CSV"
_DATAID_VERSION_KEY = "DATAIDVER"
_ROW_TYPE_DATA = "DATAID"
_ROW_TYPE_END = "END"
_UNINITIALIZED_CSV = "*Uninitialized*"
_UNINITIALIZED_NORM = "Uninitialized"

# Regex de décomposition d'un nom DATAID : $PARENT.FIELD[i,j]
_RE_DATAID_NAME = re.compile(
    r"^([\$\w]+)"           # 1 — $PARENT (avec $)
    r"\.([\w]+)"            # 2 — FIELD
    r"(?:\[([0-9,]+)\])?$"  # 3 — index optionnel : [i] ou [i,j]
)

# Mapping Data Type CSV → VADataType
_DATATYPE_MAP: dict[str, VADataType] = {
    "INTEGER":  VADataType.INTEGER,
    "REAL":     VADataType.REAL,
    "BOOLEAN":  VADataType.BOOLEAN,
    "STRING":   VADataType.STRING,
    "POSITION": VADataType.POSITION,
}

# Mapping Access Type CSV → AccessType
_ACCESS_MAP: dict[str, AccessType] = {
    "RW": AccessType.RW,
    "RO": AccessType.RO,
    "CW": AccessType.RO,   # Condition Write → traitement conservateur
    "FP": AccessType.FP,
    "WO": AccessType.WO,
}

# Colonnes attendues (ligne REM)
_EXPECTED_COLUMNS = {
    "DataID Name", "Data Type", "Value", "Access Type", "ConditionHandler",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_value(raw: str) -> str:
    """Normalise la valeur brute extraite du CSV.

    ``*Uninitialized*`` → ``"Uninitialized"`` pour cohérence avec le parser .VA.
    """
    stripped = raw.strip()
    return _UNINITIALIZED_NORM if stripped == _UNINITIALIZED_CSV else stripped


def _parse_access(raw: str) -> AccessType:
    access = _ACCESS_MAP.get(raw.strip().upper())
    if access is None:
        logger.debug("AccessType inconnu : %r — UNKNOWN utilisé", raw)
        return AccessType.UNKNOWN
    return access


def _parse_datatype(raw: str) -> VADataType:
    dt = _DATATYPE_MAP.get(raw.strip().upper())
    if dt is None:
        logger.debug("DataType inconnu : %r — UNKNOWN utilisé", raw)
        return VADataType.UNKNOWN
    return dt


def _parse_index(raw: str | None) -> tuple[int, ...] | None:
    """``"1,2"`` → ``(1, 2)``,  ``"3"`` → ``(3,)``,  ``None`` → ``None``."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(int(p) for p in parts) if parts else None


def _parse_position_value(raw: str) -> PositionValue:
    """Parse une valeur POSITION inline DATAID.CSV.

    Format : ``Group:1/Axes:0/Tool:255/Frame:255/Config:.../X:.../Y:...``
    Chaque segment ``Clé:Valeur`` devient une entrée de ``raw_lines``.
    """
    segments = [seg.strip() for seg in raw.split("/") if seg.strip()]
    return PositionValue(raw_lines=segments, label="")


def _detect_encoding(path: Path) -> str:
    """Détecte l'encodage du fichier CSV (BOM UTF-8 en priorité)."""
    try:
        header = path.read_bytes()[:3]
        if header == b"\xef\xbb\xbf":
            return "utf-8-sig"
    except OSError:
        pass
    return "utf-8"


# ---------------------------------------------------------------------------
# Lecture et validation du CSV
# ---------------------------------------------------------------------------

def _read_csv_rows(path: Path) -> tuple[str, list[dict[str, str]]]:
    """Lit le fichier DATAID.CSV et retourne (version, lignes_dataid).

    :returns: tuple ``(version_str, list_of_row_dicts)`` où chaque dict
              a pour clés les noms de colonnes de la ligne REM.
    :raises ValueError: si le format du fichier n'est pas reconnu.
    """
    encoding = _detect_encoding(path)
    try:
        text = path.read_text(encoding=encoding, errors="replace")
    except OSError as exc:
        raise ValueError(f"Lecture impossible : {exc}") from exc

    lines = text.splitlines()
    if not lines:
        raise ValueError("Fichier vide.")

    # --- Ligne 0 : version ---
    version = "unknown"
    first_fields = next(csv.reader([lines[0]]))
    if first_fields and first_fields[0].strip() == _DATAID_VERSION_KEY:
        version = first_fields[1].strip() if len(first_fields) > 1 else "unknown"
    else:
        raise ValueError(
            f"Première ligne inattendue : {lines[0]!r}. "
            f"Attendu : '{_DATAID_VERSION_KEY},…'"
        )

    # --- Ligne 1 : en-têtes (ligne REM) ---
    if len(lines) < 2:
        raise ValueError("Fichier trop court — en-têtes manquants.")

    header_fields = next(csv.reader([lines[1]]))
    if not header_fields or header_fields[0].strip() != "REM":
        raise ValueError(
            f"Ligne d'en-têtes inattendue : {lines[1]!r}. Attendu : 'REM,…'"
        )
    col_names = [f.strip() for f in header_fields]
    missing = _EXPECTED_COLUMNS - set(col_names)
    if missing:
        raise ValueError(f"Colonnes manquantes dans l'en-tête : {missing}")

    # --- Lignes de données ---
    data_rows: list[dict[str, str]] = []
    reader = csv.DictReader(
        lines[2:],
        fieldnames=col_names,
        restkey="_extra",
        restval="",
    )
    for row in reader:
        row_type = (row.get("REM") or "").strip()
        if row_type == _ROW_TYPE_DATA:
            data_rows.append(dict(row))
        elif row_type == _ROW_TYPE_END:
            break
        # Autres types (lignes vides, commentaires) : ignorées silencieusement

    return version, data_rows


# ---------------------------------------------------------------------------
# Reconstruction des RobotVariable depuis les lignes DATAID
# ---------------------------------------------------------------------------

def _build_variables(
    rows: list[dict[str, str]],
    source_file: Path,
) -> tuple[list[RobotVariable], list[str]]:
    """Construit les ``RobotVariable`` depuis les lignes DATAID.

    Chaque ligne DATAID correspond à un ``RobotVarField`` rattaché à une
    ``RobotVariable`` parente synthétique (une par ``$PARENT`` unique).
    Les variables sont retournées dans l'ordre de première apparition du parent.

    :returns: tuple ``(variables, errors)``
    """
    # Index des variables parentes par nom de parent
    parent_map: dict[str, RobotVariable] = {}
    # Ordre d'insertion pour la stabilité
    parent_order: list[str] = []
    errors: list[str] = []

    for row in rows:
        raw_name        = row.get("DataID Name", "").strip()
        raw_type        = row.get("Data Type", "").strip()
        raw_value       = row.get("Value", "").strip()
        raw_access      = row.get("Access Type", "").strip()
        raw_condition   = row.get("ConditionHandler", "").strip()

        m = _RE_DATAID_NAME.match(raw_name)
        if not m:
            errors.append(f"Nom non reconnu (ignoré) : {raw_name!r}")
            logger.warning("Nom DATAID non reconnu : %r", raw_name)
            continue

        parent_name = m.group(1)   # ex: "$ALARM"
        field_name  = m.group(2)   # ex: "ERROR_SEVERITY_TABLE"
        index_raw   = m.group(3)   # ex: "1" ou "1,2" ou None

        data_type   = _parse_datatype(raw_type)
        access      = _parse_access(raw_access)
        index_nd    = _parse_index(index_raw)
        value       = _build_field_value(raw_value, data_type, index_nd)

        # Récupère ou crée la variable parente synthétique
        if parent_name not in parent_map:
            parent_var = _make_parent_variable(parent_name, source_file)
            parent_map[parent_name] = parent_var
            parent_order.append(parent_name)
        else:
            parent_var = parent_map[parent_name]

        # Construit le field et l'attache à la variable parente
        field = RobotVarField(
            full_name       = raw_name,
            parent_var      = parent_name,
            field_name      = field_name,
            access          = access,
            data_type       = data_type,
            # type_detail stocke aussi ConditionHandler pour ne pas perdre l'info
            type_detail     = f"{raw_type} [{raw_condition}]" if raw_condition else raw_type,
            value           = value,
            parent_index_nd = index_nd,
        )
        parent_var.fields.append(field)

        # Met à jour is_array si on voit au moins un index
        if index_nd is not None:
            parent_var.is_array = True

    variables = [parent_map[k] for k in parent_order]
    return variables, errors


def _make_parent_variable(parent_name: str, source_file: Path) -> RobotVariable:
    """Crée une variable parente synthétique pour un ``$PARENT`` DATAID."""
    return RobotVariable(
        name        = parent_name,
        namespace   = "*SYSTEM*",   # toutes les vars DATAID sont système
        storage     = StorageType.UNKNOWN,
        access      = AccessType.UNKNOWN,
        data_type   = VADataType.STRUCT,
        type_detail = "DATAID_STRUCT",
        is_array    = False,
        array_size  = None,
        array_shape = None,
        value       = None,
        fields      = [],
        source_file = source_file,
        line_number = None,
    )


def _build_field_value(
    raw_value: str,
    data_type: VADataType,
    index_nd: tuple[int, ...] | None,
) -> object:
    """Construit la valeur du field selon son type et son index.

    - ``POSITION``              → ``PositionValue``
    - Toute valeur avec index   → intégrée dans ``ArrayValue``
    - Autres                    → scalaire ``str``
    """
    normalized = _normalize_value(raw_value)

    if data_type == VADataType.POSITION and normalized != _UNINITIALIZED_NORM:
        pos_value = _parse_position_value(raw_value)
        if index_nd is not None:
            arr = ArrayValue()
            arr.items[index_nd] = pos_value
            return arr
        return pos_value

    # Scalaire (avec ou sans index)
    return normalized


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class DataIdCsvParser(BackupParser):
    """Parse les fichiers ``DATAID.CSV`` des robots FANUC nouvelle génération.

    Détection : présence d'un fichier ``DATAID.CSV`` (insensible à la casse)
    à la racine du dossier backup.

    Reconstruction : chaque ligne ``DATAID`` devient un ``RobotVarField``
    attaché à une ``RobotVariable`` parente synthétique par ``$PARENT``.
    Cette convention est cohérente avec le modèle produit par ``VAParser``
    pour les structs et tableaux de structs.
    """

    FORMAT_ID = "dataid_csv"

    def can_parse(self, path: Path) -> bool:
        """Retourne ``True`` si le dossier contient un fichier ``DATAID.CSV``."""
        return any(
            f.name.upper() == _DATAID_FILENAME.upper()
            for f in path.iterdir()
            if f.is_file()
        )

    def parse(
        self,
        path: Path,
        progress_cb: ProgressCallback | None = None,
    ) -> list[RobotVariable]:
        """Parse le ``DATAID.CSV`` du dossier et retourne les variables.

        :param path:        dossier contenant ``DATAID.CSV``.
        :param progress_cb: callback de progression ``(current, total, message)``.
        :returns: liste de ``RobotVariable`` (une par ``$PARENT`` unique).
        """
        csv_path = self._find_dataid_file(path)
        if csv_path is None:
            logger.warning("Aucun fichier DATAID.CSV dans : %s", path)
            return []

        if progress_cb:
            progress_cb(0, 1, f"Lecture DATAID.CSV : {csv_path.name}")

        try:
            version, rows = _read_csv_rows(csv_path)
        except ValueError as exc:
            logger.error("DATAID.CSV invalide (%s) : %s", csv_path.name, exc)
            return []

        logger.info(
            "DATAID.CSV v%s — %d lignes DATAID dans %s",
            version, len(rows), csv_path.name,
        )

        if progress_cb:
            progress_cb(0, 1, f"Reconstruction des variables ({len(rows)} entrées)…")

        variables, errors = _build_variables(rows, csv_path)

        for err in errors:
            logger.warning(err)

        if progress_cb:
            progress_cb(1, 1, f"Terminé — {len(variables)} variable(s) parentes")

        logger.debug(
            "%d variable(s) parente(s), %d field(s) depuis %s",
            len(variables),
            sum(len(v.fields) for v in variables),
            csv_path.name,
        )
        return variables

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    @staticmethod
    def _find_dataid_file(path: Path) -> Path | None:
        """Localise ``DATAID.CSV`` de façon insensible à la casse."""
        try:
            return next(
                f for f in path.iterdir()
                if f.is_file() and f.name.upper() == _DATAID_FILENAME.upper()
            )
        except StopIteration:
            return None


# ---------------------------------------------------------------------------
# Parsing autonome (utilisable sans orchestrateur, ex: dev_parse.py)
# ---------------------------------------------------------------------------

def parse_dataid_file(csv_path: Path) -> ExtractionResult:
    """Point d'entrée standalone pour parser un fichier ``DATAID.CSV`` isolé.

    Utile pour les tests et ``dev_parse.py``.

    :param csv_path: chemin direct vers le fichier ``DATAID.CSV``.
    :returns: ``ExtractionResult`` avec les variables et les erreurs éventuelles.
    """
    result = ExtractionResult(input_dir=csv_path.parent)
    try:
        version, rows = _read_csv_rows(csv_path)
    except ValueError as exc:
        result.errors.append(str(exc))
        return result

    variables, errors = _build_variables(rows, csv_path)
    result.variables.extend(variables)
    result.errors.extend(errors)
    return result