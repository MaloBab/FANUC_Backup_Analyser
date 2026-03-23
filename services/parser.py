"""
Parser de fichiers .VA FANUC.

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

Corrections appliquées
──────────────────────
- _RE_TYPE_ARRAY : (\\S+) → (.+)$ pour capturer les types avec espace (ex: "Position Reg")
- _is_position_array : inner.split()[0] → inner (garde le type complet) + "POSITION REG"
  ajouté à _ARRAY_OF_POSITION_TYPES
- _RE_POSITION_LINE : J\\d+\\s*= ajouté pour les positions articulaires (J1..J9)
- Branche is_pos_context : _RE_POS_LABEL détecte les labels 'texte' sur les lignes
  d'index et les traite comme valeur vide → collecte des lignes multiligne suivantes
- Tous les ``assert`` portant sur des données externes ont été remplacés par des
  levées d'exceptions explicites (``ValueError`` / ``TypeError``).
"""

from __future__ import annotations
import re
import logging
from pathlib import Path

from models.fanuc_models import (
    RobotVariable, RobotVarField,
    StorageType, AccessType, VADataType,
    ArrayValue, PositionValue,
    ExtractionResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------

# En-tête unifié : [NAMESPACE]NOM  Storage: X  Access: Y  : <type_spec>
# Couvre *SYSTEM*, *POSREG* et tout namespace Karel (TBSWMD45, etc.)
_RE_VAR_HEADER = re.compile(
    r"^\[([^\]]+)\]"                 # [namespace]  (tout sauf ])
    r"(\$?[\w.]+)"                   # nom (optionnellement préfixé $, peut contenir des points)
    r"\s+Storage:\s*(\w+)"           # storage
    r"\s+Access:\s*(\w+)"            # access
    r"\s*:\s*(.+)$"                  # type_spec
)

# Nom complet d'un field : ``$AP_CUREQ[1].$PANE_EQNO``, ``NFPAM.TBC.CNT_SCALE``, ``$ALMDG.$X``
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

# Ligne ``[N] = val`` ou ``[N,M] = val`` dans un tableau
_RE_ARRAY_ITEM = re.compile(r"^\s*\[([\d,]+)\]\s*=\s*(.*)$")

# FIX 1 — type_spec tableau N-D : (.+)$ au lieu de (\S+) pour capturer
# les types avec espace comme "Position Reg"
_RE_TYPE_ARRAY  = re.compile(r"^ARRAY\[[\d,]+\]\s+OF\s+(.+)$")

# type_spec : scalaire avec valeur inline optionnelle
_RE_TYPE_SCALAR = re.compile(r"^(\w+(?:\[\d+\])?)(?:\s*=\s*(.*))?$")

# FIX 3 — Lignes de position (Group/Config/coordonnées cartésiennes ET articulaires)
# J\d+\s*= couvre J1 =, J2 =, …, J9 = (positions articulaires FANUC)
_RE_POSITION_LINE = re.compile(r"^\s*(Group:|Config:|X:|Y:|Z:|W:|P:|R:|J\d+\s*=|\[)")

# FIX 2 — Label de position sur une ligne d'index.
#
# Formes rencontrées dans posreg.va :
#   [1,3]  = 'OR_Get_Ref'        → label seul, lignes multiligne suivent
#   [1,1]  = ''   Group: 1       → label vide + contenu inline (ignoré), multilignes suivent
#   [1,2]  = '' Uninitialized    → label vide + Uninitialized → stocker comme scalaire
#
# Le regex capture :  groupe 1 = label (entre apostrophes)
#                     groupe 2 = suffix éventuel après les apostrophes (stripped)
#
# Règle d'interprétation :
#   - suffix == "Uninitialized"  → stocker "Uninitialized" comme scalaire
#   - suffix vide ou autre       → traiter comme valeur vide, lire les multilignes
#     (le suffix inline comme "Group: 1" est redondant avec les lignes suivantes)
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
# Helpers
# ---------------------------------------------------------------------------

def _parse_access(raw: str) -> AccessType:
    """Convertit une chaîne brute en ``AccessType``.

    :param raw: valeur textuelle extraite du fichier .VA (ex: ``"RW"``, ``"FP"``).
    :returns: membre ``AccessType`` correspondant, ou ``AccessType.UNKNOWN`` si non reconnu.
    """
    try:
        return AccessType(raw.strip().upper())
    except ValueError:
        return AccessType.UNKNOWN


def _parse_storage(raw: str) -> StorageType:
    """Convertit une chaîne brute en ``StorageType``.

    :param raw: valeur textuelle extraite du fichier .VA (ex: ``"CMOS"``, ``"SHADOW"``).
    :returns: membre ``StorageType`` correspondant, ou ``StorageType.UNKNOWN`` si non reconnu.
    """
    try:
        return StorageType(raw.strip().upper())
    except ValueError:
        return StorageType.UNKNOWN


def _parse_datatype(raw: str) -> VADataType:
    """Déduit le ``VADataType`` depuis une chaîne de type brute.

    La comparaison ignore la partie dimensionnelle (ex: ``STRING[37]`` → ``STRING``).
    Les types commençant par une majuscule non reconnue sont classés ``STRUCT``.

    :param raw: type brut extrait du fichier .VA (ex: ``"INTEGER"``, ``"ALMDG_T"``,
                ``"STRING[37]"``, ``"Position Reg"``).
    :returns: membre ``VADataType`` correspondant, ``VADataType.STRUCT`` pour les types
              utilisateur inconnus, ou ``VADataType.UNKNOWN`` en dernier recours.
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

    - Chaîne vide ou ``"Uninitialized"`` → retourne ``"Uninitialized"``.
    - Chaîne entre apostrophes (type ``STRING``) → retire les délimiteurs.
    - Autres cas → retourne la chaîne telle quelle.

    :param raw: valeur brute après le ``=``.
    :returns: valeur normalisée, ou ``"Uninitialized"`` si la valeur est absente.
    """
    raw = raw.strip()
    if raw in ("", "Uninitialized"):
        return "Uninitialized"
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def _parse_nd_index(raw: str | None) -> tuple[int, ...] | None:
    """Parse une chaîne d'index brute issue de la regex (ex: ``"[1,2]"``, ``"[3]"``).

    :param raw: chaîne avec crochets capturée par la regex, ou ``None``.
    :returns: tuple des indices ``(i,)`` en 1D, ``(i, j, …)`` en N-D, ou ``None``.
    :raises ValueError: si la chaîne produit un index vide après parsing
                        (données malformées dans le fichier .VA).
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
    """Décompose le nom complet d'un field en ses trois composantes.

    Exemples :
      - ``"$AP_CUREQ[1].$PANE_EQNO"``  → ``("$AP_CUREQ",    (1,),    "$PANE_EQNO")``
      - ``"$PGTRACEDT[1,2].$LINE_NUM"`` → ``("$PGTRACEDT",   (1, 2),  "$LINE_NUM")``
      - ``"NFPAM.TBC.CNT_SCALE"``       → ``("NFPAM.TBC",    None,    "CNT_SCALE")``
      - ``"$ALMDG.$X"``                 → ``("$ALMDG",       None,    "$X")``

    :param raw: nom complet tel que capturé par ``_RE_FIELD_NAME``.
    :returns: triplet ``(parent_var, parent_index_nd, field_name)``.
              Si la décomposition échoue, retourne ``(raw, None, raw)``.
    """
    m = _RE_FIELD_SPLIT.match(raw)
    if not m:
        return raw, None, raw
    parent_var, idx_raw, field_name = m.group(1), m.group(2), m.group(3)
    return parent_var, _parse_nd_index(idx_raw), field_name


def _parse_array_dims(type_spec: str) -> tuple[tuple[int, ...], int, str]:
    """Extrait les dimensions, la taille totale et le type interne d'un type tableau.

    :param type_spec: chaîne de type_spec commençant par ``ARRAY[…]``
                      (ex: ``"ARRAY[4,200] OF TRACEDT_T"``,
                           ``"ARRAY[1,300] OF Position Reg"``).
    :returns: triplet ``(shape, total_size, inner_type)`` où ``shape`` est le
              tuple de dimensions.
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


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

# Types de position FANUC dont les tableaux doivent être traités comme ARRAY OF POSITION
_POSITION_TYPES: frozenset[str] = frozenset({
    "POSITION",
    "XYZWPR",
    "XYZWPREXT",
    "JOINTPOS",
    "JOINTPOS9",
})

# FIX 1 — "POSITION REG" ajouté : type des registres de position posreg.va
# (ARRAY[1,300] OF Position Reg). Les items ont le format multiligne
# Group:/X:/W: (cartésien) ou J1=/J2=… (articulaire).
_ARRAY_OF_POSITION_TYPES: frozenset[str] = frozenset({
    "POSITION",
    "XYZWPR",
    "XYZWPREXT",
    "POSITION REG",
})


def _is_position_array(type_detail: str) -> bool:
    """Retourne True si type_detail est un ARRAY dont les items sont des positions multilignes.

    Gère les types avec espace (ex: ``"ARRAY[1,300] OF Position Reg"``).

    Les types reconnus comme positions multilignes :
      - POSITION, XYZWPR, XYZWPREXT  → format Group:/X:/Y:/Z:/W:/P:/R:
      - POSITION REG                  → format cartésien ou articulaire (J1=/J2=…)

    JOINTPOS et les types struct ont un format différent et ne sont pas inclus ici.
    """
    upper = type_detail.upper()
    if " OF " not in upper:
        return False
    # FIX 1 — on garde le type interne complet (avec espace éventuel)
    # pour matcher "POSITION REG" ; l'ancien .split()[0] tronquait à "POSITION"
    # ce qui donnait un faux positif pour POSITION seul mais ratait "POSITION REG"
    # (les deux donnaient le même résultat avant, mais le frozenset étendu
    # nécessite la chaîne complète pour distinguer les variantes futures)
    inner = upper.split(" OF ", 1)[-1].strip()
    return inner in _ARRAY_OF_POSITION_TYPES


class VAParser:
    """Parse les fichiers .VA FANUC et retourne une liste de ``RobotVariable``.

    Supporte les variables système (``[*SYSTEM*]``), les registres de position
    (``[*POSREG*]``) et les variables Karel (``[NAMESPACE]``) dans un format unifié.
    Implémente un automate ligne par ligne sans regex multilignes.
    """

    def parse_file(self, va_path: Path) -> list[RobotVariable]:
        """Parse un fichier ``.VA`` FANUC et retourne la liste de toutes ses variables.

        Le fichier est lu en UTF-8 avec remplacement des octets invalides.

        :param va_path: chemin vers le fichier ``.VA`` à lire.
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

        lines = text.splitlines()
        variables: list[RobotVariable] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            m = _RE_VAR_HEADER.match(line)
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

        La recherche est insensible à la casse de l'extension (``.VA`` et ``.va``).
        Les erreurs sur un fichier individuel sont consignées dans ``ExtractionResult.errors``
        sans interrompre le traitement des fichiers suivants.

        :param directory: dossier racine à parcourir récursivement.
        :returns: ``ExtractionResult`` agrégeant toutes les variables et les erreurs.
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
    ) -> tuple[RobotVariable | None, int]:
        """Parse une variable complète depuis son en-tête jusqu'au début de la suivante.

        Implémente un automate à états qui consomme les lignes suivant l'en-tête
        pour reconstituer la valeur ou les fields. Gère les 9 cas documentés dans
        le module.

        :param header_match: résultat de ``_RE_VAR_HEADER.match()`` sur la ligne d'en-tête.
        :param lines: liste complète des lignes du fichier.
        :param start: index (0-based) de la ligne d'en-tête.
        :param source: chemin du fichier source.
        :returns: tuple ``(variable, next_index)`` — ``next_index`` est la prochaine
                  ligne à traiter par la boucle principale.
        :raises ValueError: si le fichier contient des données structurellement invalides.
        :raises TypeError: si un invariant interne est violé (ne devrait pas se produire
                           sur un fichier .VA bien formé).
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
        current_array_is_pos : bool              = False  # True si field ARRAY[N] OF POSITION
        root_is_pos          : bool              = (      # True si variable racine ARRAY OF POSITION
            is_array and _is_position_array(type_spec)
        )

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
                # Invariant : _make_field_array initialise toujours value = ArrayValue()
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
                key     = _parse_nd_index(f"[{m.group(1)}]")
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
                    # FIX 2 — Détecter les labels de position sur la ligne d'index.
                    #
                    # Formes rencontrées :
                    #   raw_val = "'OR_Get_Ref'"       → label seul   → multiligne
                    #   raw_val = "''   Group: 1"      → label+inline → multiligne
                    #                                    (inline ignoré, lignes suivantes lues)
                    #   raw_val = "'' Uninitialized"   → label+uninit → scalaire "Uninitialized"
                    #   raw_val = "Uninitialized"      → scalaire pur → scalaire "Uninitialized"
                    #   raw_val = ""                   → vide         → multiligne
                    #
                    # _RE_POS_LABEL matche tout raw_val commençant par '...' et capture
                    # le suffix après les apostrophes (stripped).
                    m_label = _RE_POS_LABEL.match(raw_val) if raw_val else None

                    if m_label:
                        pos_label = m_label.group(1)   # texte entre les apostrophes
                        suffix    = m_label.group(2).strip()
                        if suffix == "Uninitialized":
                            # '' Uninitialized → stocker comme scalaire
                            scalar = "Uninitialized"
                            if current_array is not None:
                                current_array.items[key] = scalar
                            else:
                                if not isinstance(var.value, ArrayValue):
                                    var.value = ArrayValue()
                                var.value.items[key] = scalar
                            i += 1
                        else:
                            # Label seul ('OR_Get_Ref') ou label + contenu inline.
                            # Cas particulier : [1,1] = ''   Group: 1
                            # Le suffix "Group: 1" est une ligne de position à part
                            # entière — on l'injecte en tête de raw_lines pour ne
                            # pas le perdre, avant de lire les lignes suivantes.
                            i += 1
                            arr_pos_lines: list[str] = []
                            if suffix and _RE_POSITION_LINE.match(suffix):
                                arr_pos_lines.append(suffix)
                            while i < len(lines):
                                pl = lines[i].strip()
                                if (not pl
                                        or _RE_VAR_HEADER.match(lines[i])
                                        or pl.startswith("Field:")
                                        or _RE_ARRAY_ITEM.match(pl)):
                                    break
                                if _RE_POSITION_LINE.match(pl):
                                    arr_pos_lines.append(pl)
                                i += 1
                            pos_val = PositionValue(raw_lines=arr_pos_lines, label=pos_label)
                            if current_array is not None:
                                current_array.items[key] = pos_val
                            else:
                                if not isinstance(var.value, ArrayValue):
                                    var.value = ArrayValue()
                                var.value.items[key] = pos_val
                    elif raw_val:
                        # Scalaire pur sans label (ex: "Uninitialized", valeur numérique)
                        scalar = _scalar_value(raw_val)
                        if current_array is not None:
                            current_array.items[key] = scalar
                        else:
                            if not isinstance(var.value, ArrayValue):
                                var.value = ArrayValue()
                            var.value.items[key] = scalar
                        i += 1
                    else:
                        # raw_val vide → collecter lignes multiligne suivantes
                        i += 1
                        arr_pos_lines = []
                        while i < len(lines):
                            pl = lines[i].strip()
                            if (not pl
                                    or _RE_VAR_HEADER.match(lines[i])
                                    or pl.startswith("Field:")
                                    or _RE_ARRAY_ITEM.match(pl)):
                                break
                            if _RE_POSITION_LINE.match(pl):
                                arr_pos_lines.append(pl)
                            i += 1
                        pos_val = PositionValue(raw_lines=arr_pos_lines)
                        if current_array is not None:
                            current_array.items[key] = pos_val
                        else:
                            if not isinstance(var.value, ArrayValue):
                                var.value = ArrayValue()
                            var.value.items[key] = pos_val
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

            # -- Lignes de position racine (variable POSITION scalaire, pas ARRAY OF POSITION) --
            if _RE_POSITION_LINE.match(stripped) and not root_is_pos:
                if not isinstance(var.value, PositionValue):
                    var.value = PositionValue()
                var.value.raw_lines.append(stripped)
                i += 1
                continue

            i += 1

        # Structs avec fields : ne pas garder "Uninitialized" comme valeur —
        # la valeur est portée par les fields, pas par la variable elle-même.
        if var.fields and var.value == "Uninitialized":
            var.value = None

        return var, i

    # ------------------------------------------------------------------
    # Constructeurs de fields
    # ------------------------------------------------------------------

    @staticmethod
    def _make_field_scalar(m: re.Match) -> RobotVarField:
        """Construit un ``RobotVarField`` scalaire depuis un match de ``_RE_FIELD_SCALAR``.

        :param m: groupes attendus : ``(full_name, access, type, valeur_brute)``.
        :returns: ``RobotVarField`` entièrement renseigné.
        """
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
        """Construit un ``RobotVarField`` tableau depuis un match de ``_RE_FIELD_ARRAY``.

        La valeur est initialisée à un ``ArrayValue`` vide peuplé ensuite par la boucle.

        :param m: groupes attendus : ``(full_name, array_spec)``.
        :returns: ``RobotVarField`` avec ``value = ArrayValue()`` vide.
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
        """Construit un ``RobotVarField`` POSITION depuis un match de ``_RE_FIELD_POSITION``.

        Les lignes de coordonnées sont collectées après la construction et affectées à ``value``.

        :param m: groupes attendus : ``(full_name, access, type_position)``.
        :returns: ``RobotVarField`` avec ``value = PositionValue()`` vide.
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