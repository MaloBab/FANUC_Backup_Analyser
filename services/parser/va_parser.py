"""
Parser de fichiers .VA FANUC — robots classiques.

Ce module est l'unique responsable du parsing des fichiers ``.VA``.
Il n'a aucune connaissance des autres formats (DATAID.CSV, etc.).

Formats de variables gérés
──────────────────────────
Variables système  : ``[*SYSTEM*]$NOM  Storage: X  Access: Y  : <type_spec>``
Variables Karel    : ``[NAMESPACE]NOM   Storage: X  Access: Y  : <type_spec>``
Variables posreg   : ``[*POSREG*]$NOM  Storage: X  Access: Y  : <type_spec>``

Cas de type_spec gérés
──────────────────────
1. Scalaire simple      : ``INTEGER = 0``
2. Tableau 1-D          : ``ARRAY[9] OF REAL``          + lignes ``[N] = val``
3. Tableau N-D          : ``ARRAY[4,200] OF TRACEDT_T`` + Fields ``[N,M]``
4. Struct simple        : ``ALMDG_T =``                 + Fields
5. Tableau de structs   : ``ARRAY[1] OF AAVM_WRK_T``    + Fields
6. Field scalaire       : ``Field: X.Y Access: RW: INTEGER = val``
7. Field tableau        : ``Field: X.Y  ARRAY[N] OF TYPE`` + lignes ``[N] = val``
8. Field POSITION       : ``Field: X.Y Access: RW: POSITION =`` + lignes Group/X/W…
9. Tableau de positions : ``ARRAY[1,300] OF Position Reg`` + lignes ``[N,M] = 'label'``
                          suivi de Group:/X:/W: ou J1=/J2=… (articulaire)
"""

from __future__ import annotations
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
# Regex
# ---------------------------------------------------------------------------

# En-tête unifié : [NAMESPACE]NOM  Storage: X  Access: Y  : <type_spec>
# Couvre *SYSTEM*, *POSREG* et tout namespace Karel (TBSWMD45, etc.)
_RE_VAR_HEADER = re.compile(
    r"^\[([^\]]+)\]"        # [namespace]  (tout sauf ])
    r"(\$?[\w.]+)"          # nom (optionnellement préfixé $, peut contenir des points)
    r"\s+Storage:\s*(\w+)"  # storage
    r"\s+Access:\s*(\w+)"   # access
    r"\s*:\s*(.+)$"         # type_spec
)

# Nom complet d'un field : ``$AP_CUREQ[1].$PANE_EQNO``, ``NFPAM.TBC.CNT_SCALE``, ``$ALMDG.$X``
# Capturé en un seul groupe puis décomposé par _split_field_name()
_RE_FIELD_NAME = r"[\w.\$\[\],]+"

# Field scalaire  (avec Access:)
_RE_FIELD_SCALAR = re.compile(
    rf"Field:\s*({_RE_FIELD_NAME}?)"        # nom complet
    r"\s+Access:\s*(\w+)"
    r":\s*([\w\[\]]+(?:\[\d+\])?)"          # type
    r"\s*=\s*(.*)$"                         # valeur
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

# Ligne ``[N] = val`` ou ``[N,M] = val`` dans un tableau
_RE_ARRAY_ITEM = re.compile(r"^\s*\[([\d,]+)\]\s*=\s*(.*)$")

# type_spec tableau N-D : (.+)$ capture les types avec espace (ex: "Position Reg")
_RE_TYPE_ARRAY = re.compile(r"^ARRAY\[[\d,]+\]\s+OF\s+(.+)$")

# type_spec scalaire avec valeur inline optionnelle
_RE_TYPE_SCALAR = re.compile(r"^(\w+(?:\[\d+\])?)(?:\s*=\s*(.*))?$")

# Lignes de position : cartésiennes (Group/Config/X…R) et articulaires (J1…J9)
_RE_POSITION_LINE = re.compile(
    r"^\s*(Group:|Config:|X:|Y:|Z:|W:|P:|R:|J\d+\s*=|\[)"
)

# Label de position sur une ligne d'index.
#
# Formes rencontrées dans posreg.va :
#   [1,3]  = 'OR_Get_Ref'        → label seul, lignes multiligne suivent
#   [1,1]  = ''   Group: 1       → label vide + contenu inline (ignoré), multilignes suivent
#   [1,2]  = '' Uninitialized    → label vide + Uninitialized → stocker comme scalaire
#
# groupe 1 = label (entre apostrophes)
# groupe 2 = suffix éventuel après les apostrophes (stripped)
_RE_POS_LABEL = re.compile(r"^'([^']*)'\s*(.*)$")

# Décomposition d'un nom de field brut en (parent, [index], field_name)
# Ex: ``$AP_CUREQ[1].$PANE_EQNO``  → ``$AP_CUREQ``, ``[1]``,   ``$PANE_EQNO``
#     ``NFPAM.TBC.CNT_SCALE``       → ``NFPAM.TBC``, ``None``,  ``CNT_SCALE``
#     ``$ALMDG.$X``                 → ``$ALMDG``,    ``None``,  ``$X``
_RE_FIELD_SPLIT = re.compile(
    r"^([\w.\$][\w.\$\[\],]*?)"
    r"(?:\[([\d,]+)\])?"
    r"\.([\$\w]+)$"
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

# Types de position FANUC dont les tableaux sont traités comme ARRAY OF POSITION
_ARRAY_OF_POSITION_TYPES: frozenset[str] = frozenset({
    "POSITION",
    "XYZWPR",
    "XYZWPREXT",
    "POSITION REG",   # registres de position posreg.va (ARRAY[1,300] OF Position Reg)
})


# ---------------------------------------------------------------------------
# Helpers module-level (privés)
# ---------------------------------------------------------------------------

def _parse_access(raw: str) -> AccessType:
    """Convertit une chaîne brute en ``AccessType`` (``UNKNOWN`` si non reconnu)."""
    try:
        return AccessType(raw.strip().upper())
    except ValueError:
        return AccessType.UNKNOWN


def _parse_storage(raw: str) -> StorageType:
    """Convertit une chaîne brute en ``StorageType`` (``UNKNOWN`` si non reconnu)."""
    try:
        return StorageType(raw.strip().upper())
    except ValueError:
        return StorageType.UNKNOWN


def _parse_datatype(raw: str) -> VADataType:
    """Déduit le ``VADataType`` depuis une chaîne de type brute.

    La comparaison ignore la partie dimensionnelle (ex: ``STRING[37]`` → ``STRING``).
    Les types commençant par une majuscule non reconnue sont classés ``STRUCT``.
    """
    if not raw:
        return VADataType.UNKNOWN
    r = raw.upper().split("[")[0].strip()
    for dt in VADataType:
        if dt.value == r:
            return dt
    return VADataType.STRUCT if raw[0].isupper() or raw[0] == "$" else VADataType.UNKNOWN


def _scalar_value(raw: str) -> str:
    """Normalise une valeur scalaire brute.

    - Vide ou ``"Uninitialized"``          → ``"Uninitialized"``
    - Entre apostrophes (type ``STRING``)  → retire les délimiteurs
    - Autres                               → retourne tel quel
    """
    raw = raw.strip()
    if raw in ("", "Uninitialized"):
        return "Uninitialized"
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def _parse_nd_index(raw: str | None) -> tuple[int, ...] | None:
    """Parse une chaîne d'index brute (ex: ``"[1,2]"`` → ``(1, 2)``).

    :raises ValueError: si la chaîne produit un index vide (donnée malformée).
    """
    if raw is None:
        return None
    parts = [d for d in raw.strip("[]").split(",") if d]
    if not parts:
        raise ValueError(
            f"Index vide inattendu dans le fichier .VA : {raw!r}. "
            "La ligne est peut-être malformée."
        )
    return tuple(int(d) for d in parts)


def _split_field_name(raw: str) -> tuple[str, tuple[int, ...] | None, str]:
    """Décompose le nom complet d'un field en ``(parent_var, index_nd, field_name)``.

    Exemples :
      ``"$AP_CUREQ[1].$PANE_EQNO"``  → ``("$AP_CUREQ",  (1,),   "$PANE_EQNO")``
      ``"$PGTRACEDT[1,2].$LINE_NUM"`` → ``("$PGTRACEDT", (1, 2), "$LINE_NUM")``
      ``"NFPAM.TBC.CNT_SCALE"``       → ``("NFPAM.TBC",  None,   "CNT_SCALE")``
      ``"$ALMDG.$X"``                 → ``("$ALMDG",     None,   "$X")``

    Si la décomposition échoue, retourne ``(raw, None, raw)``.
    """
    m = _RE_FIELD_SPLIT.match(raw)
    if not m:
        return raw, None, raw
    parent_var, idx_raw, field_name = m.group(1), m.group(2), m.group(3)
    return parent_var, _parse_nd_index(idx_raw), field_name


def _parse_array_dims(type_spec: str) -> tuple[tuple[int, ...], int, str]:
    """Extrait ``(shape, total_size, inner_type)`` depuis un type_spec tableau.

    Ex: ``"ARRAY[4,200] OF TRACEDT_T"`` → ``((4, 200), 800, "TRACEDT_T")``

    :raises ValueError: si le format n'est pas reconnu.
    """
    bracket_start = type_spec.index("[") + 1
    bracket_end   = type_spec.index("]")
    dims: tuple[int, ...] = tuple(
        int(d) for d in type_spec[bracket_start:bracket_end].split(",") if d
    )
    size = 1
    for d in dims:
        size *= d
    m = _RE_TYPE_ARRAY.match(type_spec)
    if not m:
        raise ValueError(f"type_spec tableau invalide : {type_spec!r}")
    return dims, size, m.group(1)


def _is_position_array(type_detail: str) -> bool:
    """Retourne ``True`` si ``type_detail`` est un ``ARRAY OF <type_position>``.

    Les types reconnus sont listés dans ``_ARRAY_OF_POSITION_TYPES``.
    Gère les types avec espace (ex: ``"ARRAY[1,300] OF Position Reg"``).
    """
    upper = type_detail.upper()
    if " OF " not in upper:
        return False
    inner = upper.split(" OF ", 1)[-1].strip()
    return inner in _ARRAY_OF_POSITION_TYPES


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

class VAParser(BackupParser):
    """Parse les fichiers ``.VA`` FANUC et retourne une liste de ``RobotVariable``.

    Supporte les variables système (``[*SYSTEM*]``), les registres de position
    (``[*POSREG*]``) et les variables Karel (``[NAMESPACE]``) dans un format unifié.
    Implémente un automate ligne par ligne sans regex multilignes.

    Implémente le protocole ``BackupParser`` — l'orchestrateur le sélectionne
    automatiquement pour tout dossier contenant des fichiers ``.VA``.
    """

    FORMAT_ID = "va"

    # ------------------------------------------------------------------
    # Protocole BackupParser
    # ------------------------------------------------------------------

    def can_parse(self, path: Path) -> bool:
        """Retourne ``True`` si le dossier contient au moins un fichier ``.VA``."""
        try:
            return any(
                f.suffix.lower() == ".va"
                for f in path.rglob("*")
                if f.is_file()
            )
        except OSError:
            return False

    def parse(
        self,
        path: Path,
        progress_cb: ProgressCallback | None = None,
    ) -> list[RobotVariable]:
        """Parse tous les fichiers ``.VA`` du dossier et retourne les variables agrégées.

        :param path:        dossier racine du backup.
        :param progress_cb: callback ``(current, total, message)`` optionnel.
        :returns: liste de ``RobotVariable`` dans l'ordre d'extraction fichier par fichier.
        """
        va_files = sorted(
            p for p in path.rglob("*") if p.suffix.lower() == ".va"
        )
        total = len(va_files)
        if total == 0:
            if progress_cb:
                progress_cb(0, 0, "Aucun fichier .VA trouvé.")
            return []

        variables: list[RobotVariable] = []
        for i, va_path in enumerate(va_files, start=1):
            if progress_cb:
                progress_cb(i, total, f"Parsing : {va_path.name}")
            variables.extend(self.parse_file(va_path))

        return variables

    # ------------------------------------------------------------------
    # API publique complémentaire (utilisée par dev_parse.py et les tests)
    # ------------------------------------------------------------------

    def parse_file(self, va_path: Path) -> list[RobotVariable]:
        """Parse un fichier ``.VA`` isolé et retourne ses variables.

        Le fichier est lu en UTF-8 avec remplacement des octets invalides.

        :param va_path: chemin vers le fichier ``.VA``.
        :returns: liste de ``RobotVariable`` dans l'ordre d'apparition.
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

        lines     = text.splitlines()
        variables : list[RobotVariable] = []
        i         = 0
        n         = len(lines)

        while i < n:
            line = lines[i]
            m    = _RE_VAR_HEADER.match(line)
            if m:
                try:
                    var, i = self._parse_variable(m, lines, i, va_path)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Ligne %d ignorée dans %s : %s", i + 1, va_path.name, exc
                    )
                    i += 1
                    continue
                if var:
                    variables.append(var)
            else:
                i += 1

        logger.debug("%d variable(s) parsée(s) depuis %s", len(variables), va_path.name)
        return variables

    def parse_directory(self, directory: Path) -> ExtractionResult:
        """Parse récursivement tous les fichiers ``.VA`` d'un dossier.

        Méthode utilitaire standalone — retourne un ``ExtractionResult`` complet
        avec les erreurs par fichier.  Utilisée par ``dev_parse.py``.

        :param directory: dossier racine à parcourir.
        :returns: ``ExtractionResult`` agrégeant toutes les variables et les erreurs.
        """
        result   = ExtractionResult(input_dir=directory)
        va_files = sorted(
            p for p in directory.rglob("*") if p.suffix.lower() == ".va"
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
    ) -> tuple[RobotVariable | None, int]:
        """Parse une variable complète depuis son en-tête jusqu'au début de la suivante.

        Implémente un automate à états qui consomme les lignes suivant l'en-tête
        pour reconstituer la valeur ou les fields. Gère les 9 cas documentés dans
        le module.

        :param header_match: résultat de ``_RE_VAR_HEADER.match()`` sur la ligne d'en-tête.
        :param lines:        liste complète des lignes du fichier.
        :param start:        index (0-based) de la ligne d'en-tête.
        :param source:       chemin du fichier source.
        :returns: tuple ``(variable, next_index)``.
        :raises ValueError: données structurellement invalides dans le fichier .VA.
        :raises TypeError:  invariant interne violé (ne devrait pas se produire sur
                            un fichier .VA bien formé).
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

        var = RobotVariable(
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
        i                    = start + 1
        current_array        : ArrayValue | None = None
        current_array_is_pos : bool              = False
        root_is_pos          : bool              = is_array and _is_position_array(type_spec)

        while i < len(lines):
            line     = lines[i]
            stripped = line.strip()

            if _RE_VAR_HEADER.match(line):
                break

            if not stripped:
                current_array        = None
                current_array_is_pos = False
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
                f.value              = PositionValue(raw_lines=pos_lines)
                current_array        = None
                current_array_is_pos = False
                continue

            # -- Field tableau (sans Access:) --
            m = _RE_FIELD_ARRAY.match(stripped)
            if m:
                f = self._make_field_array(m)
                var.fields.append(f)
                if not isinstance(f.value, ArrayValue):
                    raise TypeError(
                        f"Invariant violé : ArrayValue attendu pour le field "
                        f"'{f.full_name}' (ligne {i + 1} de {source.name}), "
                        f"obtenu {type(f.value).__name__!r}."
                    )
                current_array        = f.value
                current_array_is_pos = _is_position_array(f.type_detail)
                i += 1
                continue

            # -- Field scalaire --
            m = _RE_FIELD_SCALAR.match(stripped)
            if m:
                f = self._make_field_scalar(m)
                var.fields.append(f)
                current_array        = None
                current_array_is_pos = False
                i += 1
                continue

            # -- Ligne [N] ou [N,M] = val --
            m = _RE_ARRAY_ITEM.match(stripped)
            if m:
                key = _parse_nd_index(f"[{m.group(1)}]")
                if key is None:
                    raise ValueError(
                        f"Index N-D non résolu pour '{m.group(1)}' "
                        f"(ligne {i + 1} de {source.name})."
                    )
                raw_val = m.group(2).strip()

                is_pos_context = (
                    (current_array is not None and current_array_is_pos)
                    or (current_array is None and root_is_pos)
                )

                if is_pos_context:
                    self._collect_position_item(
                        lines, i, key, raw_val, var, current_array, source
                    )
                    # _collect_position_item avance i en interne via return
                    # → on doit récupérer le nouvel i
                    i = self._collect_position_item(
                        lines, i, key, raw_val, var, current_array, source
                    )
                    continue
                else:
                    val = _scalar_value(raw_val)
                    if current_array is not None:
                        current_array.items[key] = val
                    else:
                        if not isinstance(var.value, ArrayValue):
                            var.value = ArrayValue()
                        var.value.items[key] = val
                    i += 1
                    continue

            # -- Lignes de position racine (POSITION scalaire, pas ARRAY OF POSITION) --
            if _RE_POSITION_LINE.match(stripped) and not root_is_pos:
                if not isinstance(var.value, PositionValue):
                    var.value = PositionValue()
                var.value.raw_lines.append(stripped)
                i += 1
                continue

            i += 1

        # Structs avec fields : la valeur est portée par les fields, pas la variable.
        if var.fields and var.value == "Uninitialized":
            var.value = None

        return var, i

    def _collect_position_item(
        self,
        lines: list[str],
        i: int,
        key: tuple[int, ...],
        raw_val: str,
        var: RobotVariable,
        current_array: ArrayValue | None,
        source: Path,
    ) -> int:
        """Collecte un item de position (scalaire ou multiligne) dans un tableau.

        Gère les trois formes rencontrées dans ``posreg.va`` :
          - ``'OR_Get_Ref'``      → label seul   → lire les lignes multiligne suivantes
          - ``'' Group: 1``       → label + inline → multiligne (inline ignoré, redondant)
          - ``'' Uninitialized``  → label + uninit → stocker ``"Uninitialized"``
          - ``Uninitialized``     → scalaire pur  → stocker ``"Uninitialized"``
          - ``""``                → vide          → lire les lignes multiligne suivantes

        :returns: prochain index ``i`` à traiter par la boucle principale.
        """
        def _store(value: object) -> None:
            if current_array is not None:
                current_array.items[key] = value
            else:
                if not isinstance(var.value, ArrayValue):
                    var.value = ArrayValue()
                var.value.items[key] = value

        m_label = _RE_POS_LABEL.match(raw_val) if raw_val else None

        if m_label:
            pos_label = m_label.group(1)
            suffix    = m_label.group(2).strip()

            if suffix == "Uninitialized":
                _store("Uninitialized")
                return i + 1

            # Label seul ou label + contenu inline (le suffix inline est redondant
            # avec les lignes multilignes suivantes — on l'injecte en tête si c'est
            # une ligne de position valide pour ne pas perdre l'information)
            i += 1
            pos_lines: list[str] = []
            if suffix and _RE_POSITION_LINE.match(suffix):
                pos_lines.append(suffix)
            while i < len(lines):
                pl = lines[i].strip()
                if (not pl
                        or _RE_VAR_HEADER.match(lines[i])
                        or pl.startswith("Field:")
                        or _RE_ARRAY_ITEM.match(pl)):
                    break
                if _RE_POSITION_LINE.match(pl):
                    pos_lines.append(pl)
                i += 1
            _store(PositionValue(raw_lines=pos_lines, label=pos_label))
            return i

        if raw_val:
            # Scalaire pur (ex: "Uninitialized", valeur numérique)
            _store(_scalar_value(raw_val))
            return i + 1

        # raw_val vide → lire les lignes multiligne suivantes
        i += 1
        pos_lines = []
        while i < len(lines):
            pl = lines[i].strip()
            if (not pl
                    or _RE_VAR_HEADER.match(lines[i])
                    or pl.startswith("Field:")
                    or _RE_ARRAY_ITEM.match(pl)):
                break
            if _RE_POSITION_LINE.match(pl):
                pos_lines.append(pl)
            i += 1
        _store(PositionValue(raw_lines=pos_lines))
        return i

    # ------------------------------------------------------------------
    # Constructeurs de fields
    # ------------------------------------------------------------------

    @staticmethod
    def _make_field_scalar(m: re.Match) -> RobotVarField:
        """Construit un ``RobotVarField`` scalaire depuis un match ``_RE_FIELD_SCALAR``."""
        raw_name, raw_access, raw_type, raw_val = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return RobotVarField(
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
    def _make_field_array(m: re.Match) -> RobotVarField:
        """Construit un ``RobotVarField`` tableau depuis un match ``_RE_FIELD_ARRAY``.

        La valeur est initialisée à un ``ArrayValue`` vide, peuplé ensuite par la boucle.
        """
        raw_name, array_spec = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return RobotVarField(
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
    def _make_field_position(m: re.Match) -> RobotVarField:
        """Construit un ``RobotVarField`` POSITION depuis un match ``_RE_FIELD_POSITION``.

        Les lignes de coordonnées sont collectées après la construction.
        """
        raw_name, raw_access, raw_type = m.groups()
        parent_var, nd, field_name = _split_field_name(raw_name)
        return RobotVarField(
            full_name       = raw_name,
            parent_var      = parent_var,
            field_name      = field_name,
            access          = _parse_access(raw_access),
            data_type       = _parse_datatype(raw_type),
            type_detail     = raw_type.strip(),
            value           = PositionValue(),
            parent_index_nd = nd,
        )