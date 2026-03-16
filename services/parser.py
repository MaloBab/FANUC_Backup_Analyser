"""
Parser de fichiers .VA FANUC.

Formats de variables gérés
──────────────────────────
Variables système  : [*SYSTEM*]$NOM  Storage: X  Access: Y  : <type_spec>
Variables Karel    : [NAMESPACE]NOM   Storage: X  Access: Y  : <type_spec>

Cas de type_spec gérés
──────────────────────
1. Scalaire simple      : INTEGER = 0
2. Tableau 1-D          : ARRAY[9] OF REAL          + lignes [N] = val
3. Tableau N-D          : ARRAY[4,200] OF TRACEDT_T + Fields [N,M]
4. Struct simple        : ALMDG_T =                 + Fields
5. Tableau de structs   : ARRAY[1] OF AAVM_WRK_T    + Fields
6. Field scalaire       : Field: X.Y Access: RW: INTEGER = val
7. Field tableau        : Field: X.Y  ARRAY[N] OF TYPE + lignes [N] = val
8. Field POSITION       : Field: X.Y Access: RW: POSITION = + lignes Group/X/W…
"""

from __future__ import annotations
import re
import logging
from pathlib import Path

from models.fanuc_models import (
    SystemVariable, SystemVarField,
    StorageType, AccessType, VADataType,
    ArrayValue, PositionValue,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# En-tête unifié : [NAMESPACE]NOM  Storage: X  Access: Y  : <type_spec>
# Couvre *SYSTEM* et tout namespace Karel (TBSWMD45, etc.)
_RE_VAR_HEADER = re.compile(
    r"^\[([^\]]+)\]"                 # [namespace]  (tout sauf ])
    r"(\$?[\w.]+)"                   # nom (optionnellement préfixé $, peut contenir des points)
    r"\s+Storage:\s*(\w+)"           # storage
    r"\s+Access:\s*(\w+)"            # access
    r"\s*:\s*(.+)$"                  # type_spec
)

# Nom complet d'un field : $AP_CUREQ[1].$PANE_EQNO, NFPAM.TBC.CNT_SCALE, $ALMDG.$X
# Capturé en un seul groupe puis décomposé par _split_field_name()
_RE_FIELD_NAME = r"[\w.\$\[\],]+"

# Field scalaire  (avec Access:)
_RE_FIELD_SCALAR = re.compile(
    rf"Field:\s*({_RE_FIELD_NAME}?)"            # nom complet
    r"\s+Access:\s*(\w+)"
    r":\s*([\w\[\]]+(?:\[\d+\])?)"              # type
    r"\s*=\s*(.*)$"                             # valeur
)

# Field tableau  (sans Access:)
_RE_FIELD_ARRAY = re.compile(
    rf"Field:\s*({_RE_FIELD_NAME}?)"
    r"\s+(ARRAY\[[\d,]+\]\s+OF\s+\S+)\s*$"
)

# Field POSITION  (Access: présent, valeur vide → multilignes)
_RE_FIELD_POSITION = re.compile(
    rf"Field:\s*({_RE_FIELD_NAME}?)"
    r"\s+Access:\s*(\w+)"
    r":\s*(POSITION|XYZWPR\w*)\s*=\s*$"
)

# Ligne [N] = val ou [N,M] = val dans un tableau
_RE_ARRAY_ITEM = re.compile(r"^\s*\[([\d,]+)\]\s*=\s*(.*)$")

# type_spec : tableau N-D
_RE_TYPE_ARRAY  = re.compile(r"^ARRAY\[[\d,]+\]\s+OF\s+(\S+)")
# type_spec : scalaire avec valeur inline optionnelle
_RE_TYPE_SCALAR = re.compile(r"^(\w+(?:\[\d+\])?)(?:\s*=\s*(.*))?$")

# Lignes de position (Group/Config/coordonnées)
_RE_POSITION_LINE = re.compile(r"^\s*(Group:|Config:|X:|Y:|Z:|W:|P:|R:|\[)")

# Décomposition d'un nom de field brut en (parent, [index], field_name)
# Ex: $AP_CUREQ[1].$PANE_EQNO  → $AP_CUREQ, [1],   $PANE_EQNO
#     NFPAM.TBC.CNT_SCALE       → NFPAM.TBC, None,  CNT_SCALE
#     $ALMDG.$X                 → $ALMDG,    None,  $X
_RE_FIELD_SPLIT = re.compile(r"^([\w.\$]+?)(\[[\d,]+\])?\.([\$\w]+)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_access(raw: str) -> AccessType:
    """Convertit une chaîne brute en AccessType.

    :param raw: valeur textuelle extraite du fichier .VA (ex: "RW", "FP").
    :returns: membre AccessType correspondant, ou AccessType.UNKNOWN si non reconnu.
    """
    try:
        return AccessType(raw.strip().upper())
    except ValueError:
        return AccessType.UNKNOWN


def _parse_storage(raw: str) -> StorageType:
    """Convertit une chaîne brute en StorageType.

    :param raw: valeur textuelle extraite du fichier .VA (ex: "CMOS", "SHADOW").
    :returns: membre StorageType correspondant, ou StorageType.UNKNOWN si non reconnu.
    """
    try:
        return StorageType(raw.strip().upper())
    except ValueError:
        return StorageType.UNKNOWN


def _parse_datatype(raw: str) -> VADataType:
    """Déduit le VADataType depuis une chaîne de type brute.

    La comparaison ignore la partie dimensionnelle (ex: STRING[37] → STRING).
    Les types commençant par une majuscule non reconnue sont classés STRUCT.

    :param raw: type brut extrait du fichier .VA (ex: "INTEGER", "ALMDG_T", "STRING[37]").
    :returns: membre VADataType correspondant, VADataType.STRUCT pour les types
              utilisateur inconnus, ou VADataType.UNKNOWN en dernier recours.
    """
    if not raw:
        return VADataType.UNKNOWN
    r = raw.upper().split("[")[0].strip()
    for dt in VADataType:
        if dt.value == r:
            return dt
    return VADataType.STRUCT if raw[0].isupper() or raw[0] == "$" else VADataType.UNKNOWN


def _scalar_value(raw: str) -> str | None:
    """Normalise une valeur scalaire brute extraite du fichier .VA.

    - Chaîne vide ou "Uninitialized" → retourne "Uninitialized".
    - Chaîne entre apostrophes (type STRING) → retire les délimiteurs.
    - Autres cas → retourne la chaîne telle quelle.

    :param raw: valeur brute après le =.
    :returns: valeur normalisée, ou "Uninitialized" si la valeur est absente.
    """
    raw = raw.strip()
    if raw in ("", "Uninitialized"):
        return "Uninitialized"
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def _parse_nd_index(raw: str | None) -> tuple[int, ...] | None:
    """Parse une chaîne d'index brute issue de la regex (ex: "[1,2]", "[3]").

    :param raw: chaîne avec crochets capturée par la regex, ou None.
    :returns: tuple des indices (i,) en 1D, (i, j, …) en N-D, ou None.
    """
    if raw is None:
        return None
    dims: tuple[int, ...] = tuple(int(d) for d in raw.strip("[]").split(",") if d)
    assert len(dims) >= 1, f"Index vide inattendu : {raw!r}"
    return dims


def _split_field_name(raw: str) -> tuple[str, tuple[int, ...] | None, str]:
    """Décompose le nom complet d'un field en ses trois composantes.

    Exemples :
      - "$AP_CUREQ[1].$PANE_EQNO"  → ("$AP_CUREQ",    (1,),    "$PANE_EQNO")
      - "$PGTRACEDT[1,2].$LINE_NUM" → ("$PGTRACEDT",   (1, 2),  "$LINE_NUM")
      - "NFPAM.TBC.CNT_SCALE"       → ("NFPAM.TBC",    None,    "CNT_SCALE")
      - "$ALMDG.$X"                 → ("$ALMDG",       None,    "$X")

    :param raw: nom complet tel que capturé par _RE_FIELD_NAME.
    :returns: triplet (parent_var, parent_index_nd, field_name).
              Si la décomposition échoue, retourne (raw, None, raw).
    """
    m = _RE_FIELD_SPLIT.match(raw)
    if not m:
        return raw, None, raw
    parent_var, idx_raw, field_name = m.group(1), m.group(2), m.group(3)
    return parent_var, _parse_nd_index(idx_raw), field_name


def _parse_array_dims(type_spec: str) -> tuple[tuple[int, ...], int, str]:
    """Extrait les dimensions, la taille totale et le type interne d'un type tableau.

    :param type_spec: chaîne de type_spec commençant par ARRAY[…] (ex: "ARRAY[4,200] OF TRACEDT_T").
    :returns: triplet (shape, total_size, inner_type) où shape est le tuple de dimensions.
    :raises ValueError: si le format n'est pas reconnu.
    """
    bracket_start = type_spec.index("[") + 1
    bracket_end   = type_spec.index("]")
    dims: tuple[int, ...] = tuple(int(d) for d in type_spec[bracket_start:bracket_end].split(",") if d)
    size = 1
    for d in dims:
        size *= d
    m = _RE_TYPE_ARRAY.match(type_spec)
    if not m:
        raise ValueError(f"type_spec tableau invalide : {type_spec!r}")
    return dims, size, m.group(1)


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class VAParser:
    """Parse les fichiers .VA FANUC et retourne une liste de SystemVariable.

    Supporte les variables système ([*SYSTEM*]) et les variables Karel
    ([NAMESPACE]) dans un format unifié. Implémente un automate ligne
    par ligne sans regex multilignes.
    """

    def parse_file(self, va_path: Path) -> list[SystemVariable]:
        """Parse un fichier .VA FANUC et retourne la liste de toutes ses variables.

        Le fichier est lu en UTF-8 avec remplacement des octets invalides.

        :param va_path: chemin vers le fichier .VA à lire.
        :returns: liste de SystemVariable dans l'ordre d'apparition.
                  Retourne une liste vide si le fichier est introuvable ou illisible.
        """
        if not va_path.exists():
            logger.warning("Fichier introuvable : %s", va_path)
            return []
        try:
            text = va_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.error("Lecture impossible %s : %s", va_path, exc)
            return []

        lines = text.splitlines()
        variables: list[SystemVariable] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            m = _RE_VAR_HEADER.match(line)
            if m:
                var, i = self._parse_variable(m, lines, i, va_path)
                if var:
                    variables.append(var)
            else:
                i += 1

        logger.debug("%d variable(s) parsée(s) depuis %s", len(variables), va_path.name)
        return variables

    def parse_directory(self, directory: Path) -> ExtractionResult:
        """Parse récursivement tous les fichiers .VA d'un dossier.

        La recherche est insensible à la casse de l'extension (.VA et .va).
        Les erreurs sur un fichier individuel sont consignées dans ExtractionResult.errors
        sans interrompre le traitement des fichiers suivants.

        :param directory: dossier racine à parcourir récursivement.
        :returns: ExtractionResult agrégeant toutes les variables et les erreurs.
        """
        result = ExtractionResult(input_dir=directory)
        va_files = sorted(
            p for p in directory.rglob("*")
            if p.suffix.lower() == ".va"
        )
        for va_file in va_files:
            try:
                result.variables.extend(self.parse_file(va_file))
            except Exception as exc:
                result.errors.append(f"{va_file.name}: {exc}")
        return result

    # ------------------------------------------------------------------
    # Automate interne
    # ------------------------------------------------------------------

    def _parse_variable(
        self,
        header_match: re.Match,
        lines: list[str],
        start: int,
        source: Path,
    ) -> tuple[SystemVariable | None, int]:
        """Parse une variable complète depuis son en-tête jusqu'au début de la suivante.

        Implémente un automate à états qui consomme les lignes suivant l'en-tête
        pour reconstituer la valeur ou les fields. Gère les 8 cas documentés dans
        le module.

        :param header_match: résultat de _RE_VAR_HEADER.match() sur la ligne d'en-tête.
        :param lines: liste complète des lignes du fichier.
        :param start: index (0-based) de la ligne d'en-tête.
        :param source: chemin du fichier source.
        :returns: tuple (variable, next_index) — next_index est la prochaine
                  ligne à traiter par la boucle principale.
        """
        namespace, name, raw_storage, raw_access, type_spec = header_match.groups()
        type_spec = type_spec.strip()
        storage   = _parse_storage(raw_storage)
        access    = _parse_access(raw_access)

        # Analyse du type_spec
        if _RE_TYPE_ARRAY.match(type_spec):
            shape, array_size, inner_type = _parse_array_dims(type_spec)
            array_shape = shape if len(shape) > 1 else None
            data_type   = _parse_datatype(inner_type)
            is_array    = True
        else:
            array_size  = None
            array_shape = None
            is_array    = False
            m_sc        = _RE_TYPE_SCALAR.match(type_spec)
            inner_type  = m_sc.group(1) if m_sc else type_spec
            data_type   = _parse_datatype(inner_type)

        var = SystemVariable(
            name        = name,
            namespace   = namespace,
            storage     = storage,
            access      = access,
            data_type   = data_type,
            type_detail = type_spec,
            is_array    = is_array,
            array_size  = array_size,
            array_shape = array_shape if is_array else None,
            value       = None,
            source_file = source,
            line_number = start + 1,
        )

        # Valeur scalaire inline sur l'en-tête
        if not is_array and "=" in type_spec:
            raw_val   = type_spec.split("=", 1)[1].strip()
            var.value = _scalar_value(raw_val)

        # Lecture des lignes suivantes
        i             = start + 1
        current_array : ArrayValue | None = None

        while i < len(lines):
            line    = lines[i]
            stripped = line.strip()

            if _RE_VAR_HEADER.match(line):
                break

            if not stripped:
                current_array = None
                i += 1
                continue

            # -- Field POSITION (multilignes) --
            m = _RE_FIELD_POSITION.match(stripped)
            if m:
                f = self._make_field_position(m)
                var.fields.append(f)
                i += 1
                pos_lines: list[str] = []
                while i < len(lines):
                    pl = lines[i].strip()
                    if not pl or _RE_VAR_HEADER.match(lines[i]) or pl.startswith("Field:"):
                        break
                    pos_lines.append(pl)
                    i += 1
                f.value       = PositionValue(raw_lines=pos_lines)
                current_array = None
                continue

            # -- Field tableau (sans Access:) --
            m = _RE_FIELD_ARRAY.match(stripped)
            if m:
                f = self._make_field_array(m)
                var.fields.append(f)
                assert isinstance(f.value, ArrayValue)
                current_array = f.value
                i += 1
                continue

            # -- Field scalaire --
            m = _RE_FIELD_SCALAR.match(stripped)
            if m:
                f = self._make_field_scalar(m)
                var.fields.append(f)
                current_array = None
                i += 1
                continue

            # -- Ligne [N] ou [N,M] = val --
            m = _RE_ARRAY_ITEM.match(stripped)
            if m:
                key = _parse_nd_index(f"[{m.group(1)}]")
                assert key is not None
                val = _scalar_value(m.group(2))
                if current_array is not None:
                    current_array.items[key] = val
                else:
                    if not isinstance(var.value, ArrayValue):
                        var.value = ArrayValue()
                    var.value.items[key] = val
                i += 1
                continue

            # -- Lignes de position racine --
            if _RE_POSITION_LINE.match(stripped):
                if not isinstance(var.value, PositionValue):
                    var.value = PositionValue()
                var.value.raw_lines.append(stripped)
                i += 1
                continue

            i += 1

        return var, i

    # ------------------------------------------------------------------
    # Constructeurs de fields
    # ------------------------------------------------------------------

    @staticmethod
    def _make_field_scalar(m: re.Match) -> SystemVarField:
        """Construit un SystemVarField scalaire depuis un match de _RE_FIELD_SCALAR.

        :param m: groupes attendus : (full_name, access, type, valeur_brute).
        :returns: SystemVarField entièrement renseigné.
        """
        raw_name, raw_access, raw_type, raw_val = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return SystemVarField(
            full_name       = raw_name,
            parent_var      = parent_var,
            field_name      = field_name,
            access          = _parse_access(raw_access),
            data_type       = _parse_datatype(raw_type),
            type_detail     = raw_type.strip(),
            value           = _scalar_value(raw_val),
            parent_index_nd = nd,
        )

    @staticmethod
    def _make_field_array(m: re.Match) -> SystemVarField:
        """Construit un SystemVarField tableau depuis un match de _RE_FIELD_ARRAY.

        La valeur est initialisée à un ArrayValue vide peuplé ensuite par la boucle.

        :param m: groupes attendus : (full_name, array_spec).
        :returns: SystemVarField avec value = ArrayValue() vide.
        """
        raw_name, array_spec = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return SystemVarField(
            full_name       = raw_name,
            parent_var      = parent_var,
            field_name      = field_name,
            access          = AccessType.UNKNOWN,
            data_type       = VADataType.UNKNOWN,
            type_detail     = array_spec.strip(),
            value           = ArrayValue(),
            parent_index_nd = nd,
        )

    @staticmethod
    def _make_field_position(m: re.Match) -> SystemVarField:
        """Construit un SystemVarField POSITION depuis un match de _RE_FIELD_POSITION.

        Les lignes de coordonnées sont collectées après la construction et affectées à value.

        :param m: groupes attendus : (full_name, access, type_position).
        :returns: SystemVarField avec value = PositionValue() vide.
        """
        raw_name, raw_access, raw_type = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return SystemVarField(
            full_name       = raw_name,
            parent_var      = parent_var,
            field_name      = field_name,
            access          = _parse_access(raw_access),
            data_type       = _parse_datatype(raw_type),
            type_detail     = raw_type.strip(),
            value           = PositionValue(),
            parent_index_nd = nd,
        )