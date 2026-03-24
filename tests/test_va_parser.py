"""
test_va_parser.py — Tests du VAParser et de ses helpers internes.

Couvre :
  - Unitaires : toutes les fonctions helper du module va_parser
  - Fonctionnels : parsing des 9 formats de variable documentés
  - Robustesse : fichiers absents, vides, malformés, encodages dégradés
  - Couverture : cas limites (index vide, type inconnu, lignes parasites…)
"""

from __future__ import annotations

import pytest
from pathlib import Path

from models.fanuc_models import (
    AccessType,
    ArrayValue,
    ExtractionResult,
    PositionValue,
    RobotVariable,
    StorageType,
    VADataType,
)
from services.parser.va_parser import (
    VAParser,
    _is_position_array,
    _parse_access,
    _parse_array_dims,
    _parse_datatype,
    _parse_nd_index,
    _parse_storage,
    _scalar_value,
    _split_field_name,
)

from tests.conftest import (
    VA_ARRAY_OF_POSITION,
    VA_ARRAY_OF_STRUCT,
    VA_ARRAYS,
    VA_FIELD_POSITION,
    VA_HOSTENT,
    VA_KAREL,
    VA_ND_ARRAY,
    VA_POSREG,
    VA_SCALAR_POSITION,
    VA_SIMPLE_SCALARS,
    VA_STRUCT_SIMPLE,
    VA_UNINITIALIZED,
    write_va,
)


# ===========================================================================
# Helpers
# ===========================================================================

def parse_va(parser: VAParser, tmp_path: Path, content: str) -> list[RobotVariable]:
    """Écrit le contenu dans un .VA et parse le fichier."""
    return parser.parse_file(write_va(tmp_path, content))


# ===========================================================================
# 1 — Tests unitaires : _scalar_value
# ===========================================================================

class TestScalarValue:

    def test_chaine_vide_retourne_uninitialized(self):
        # Chaîne vide → valeur manquante normalisée
        assert _scalar_value("") == "Uninitialized"

    def test_keyword_uninitialized_passe_tel_quel(self):
        # Mot-clé FANUC littéral conservé
        assert _scalar_value("Uninitialized") == "Uninitialized"

    def test_whitespace_autour_uninitialized(self):
        # Espaces autour du mot-clé ignorés
        assert _scalar_value("  Uninitialized  ") == "Uninitialized"

    def test_apostrophes_strippees(self):
        # Guillemets FANUC retirés pour les STRING
        assert _scalar_value("'LR HandlingTool'") == "LR HandlingTool"

    def test_apostrophes_vides(self):
        # STRING vide → chaîne vide (pas Uninitialized)
        assert _scalar_value("''") == ""

    def test_entier_inchange(self):
        # Valeur numérique entière laissée telle quelle
        assert _scalar_value("42") == "42"

    def test_flottant_notation_scientifique(self):
        # Notation scientifique FANUC conservée
        assert _scalar_value("1.000000e+00") == "1.000000e+00"

    def test_negatif_inchange(self):
        # Valeur négative conservée
        assert _scalar_value("-1") == "-1"

    def test_boolean_true(self):
        # Booléen TRUE conservé tel quel
        assert _scalar_value("TRUE") == "TRUE"

    def test_boolean_false(self):
        # Booléen FALSE conservé tel quel
        assert _scalar_value("FALSE") == "FALSE"

    def test_whitespace_only_retourne_uninitialized(self):
        # Espace seul traité comme vide
        assert _scalar_value("   ") == "Uninitialized"


# ===========================================================================
# 2 — Tests unitaires : _parse_storage
# ===========================================================================

class TestParseStorage:

    def test_cmos(self):
        # Type CMOS standard
        assert _parse_storage("CMOS") == StorageType.CMOS

    def test_shadow(self):
        # Type SHADOW (variables fantômes)
        assert _parse_storage("SHADOW") == StorageType.SHADOW

    def test_dram(self):
        # Type DRAM (RAM volatile)
        assert _parse_storage("DRAM") == StorageType.DRAM

    def test_insensible_casse(self):
        # Le parser est insensible à la casse
        assert _parse_storage("cmos") == StorageType.CMOS

    def test_whitespace_strips(self):
        # Espaces autour ignorés
        assert _parse_storage("  SHADOW  ") == StorageType.SHADOW

    def test_inconnu_retourne_unknown(self):
        # Type non répertorié → UNKNOWN (pas d'exception)
        assert _parse_storage("FLASH") == StorageType.UNKNOWN

    def test_vide_retourne_unknown(self):
        # Chaîne vide → UNKNOWN
        assert _parse_storage("") == StorageType.UNKNOWN


# ===========================================================================
# 3 — Tests unitaires : _parse_access
# ===========================================================================

class TestParseAccess:

    def test_rw(self):
        # Accès lecture-écriture
        assert _parse_access("RW") == AccessType.RW

    def test_ro(self):
        # Accès lecture seule
        assert _parse_access("RO") == AccessType.RO

    def test_fp(self):
        # Accès force-protect
        assert _parse_access("FP") == AccessType.FP

    def test_wo(self):
        # Accès écriture seule
        assert _parse_access("WO") == AccessType.WO

    def test_insensible_casse(self):
        # Insensible à la casse
        assert _parse_access("rw") == AccessType.RW

    def test_inconnu_retourne_unknown(self):
        # Valeur inconnue → UNKNOWN sans exception
        assert _parse_access("XX") == AccessType.UNKNOWN

    def test_whitespace_strips(self):
        # Espaces ignorés
        assert _parse_access("  RO  ") == AccessType.RO


# ===========================================================================
# 4 — Tests unitaires : _parse_datatype
# ===========================================================================

class TestParseDatatype:

    def test_integer(self):
        # Type entier basique
        assert _parse_datatype("INTEGER") == VADataType.INTEGER

    def test_real(self):
        # Type réel (flottant)
        assert _parse_datatype("REAL") == VADataType.REAL

    def test_boolean(self):
        # Type booléen
        assert _parse_datatype("BOOLEAN") == VADataType.BOOLEAN

    def test_string_avec_taille(self):
        # STRING[N] → dimension ignorée → STRING
        assert _parse_datatype("STRING[37]") == VADataType.STRING

    def test_position(self):
        # Type position cartésienne
        assert _parse_datatype("POSITION") == VADataType.POSITION

    def test_xyzwpr(self):
        # Alias cartésien XYZWPR
        assert _parse_datatype("XYZWPR") == VADataType.XYZWPR

    def test_type_custom_majuscule_donne_struct(self):
        # Nom commençant par majuscule non répertorié → STRUCT
        assert _parse_datatype("ALMDG_T") == VADataType.STRUCT

    def test_type_dollar_donne_struct(self):
        # Nom commençant par $ → STRUCT (variable pointeur FANUC)
        assert _parse_datatype("$CUSTOM") == VADataType.STRUCT

    def test_vide_donne_unknown(self):
        # Type absent → UNKNOWN
        assert _parse_datatype("") == VADataType.UNKNOWN

    def test_minuscule_inconnu_donne_unknown(self):
        # Minuscule non répertoriée → UNKNOWN
        assert _parse_datatype("custom_type") == VADataType.UNKNOWN

    def test_short_donne_struct(self):
        # SHORT (type FANUC natif) commence par majuscule → STRUCT
        assert _parse_datatype("SHORT") == VADataType.STRUCT


# ===========================================================================
# 5 — Tests unitaires : _parse_nd_index
# ===========================================================================

class TestParseNdIndex:

    def test_none_retourne_none(self):
        # Pas d'index → None
        assert _parse_nd_index(None) is None

    def test_index_simple(self):
        # Index 1D classique
        assert _parse_nd_index("[1]") == (1,)

    def test_index_2d(self):
        # Index 2D (tableau matriciel)
        assert _parse_nd_index("[2,3]") == (2, 3)

    def test_index_3d(self):
        # Index 3D extrême
        assert _parse_nd_index("[1,2,3]") == (1, 2, 3)

    def test_grands_indices(self):
        # Indices à 3 chiffres
        assert _parse_nd_index("[100,200]") == (100, 200)

    def test_index_vide_leve_valueerror(self):
        # [] → malformation → exception explicite
        with pytest.raises(ValueError, match="Index vide"):
            _parse_nd_index("[]")


# ===========================================================================
# 6 — Tests unitaires : _parse_array_dims
# ===========================================================================

class TestParseArrayDims:

    def test_1d_real(self):
        # Tableau 1D de REAL
        shape, size, inner = _parse_array_dims("ARRAY[4] OF REAL")
        assert shape == (4,)
        assert size == 4
        assert inner == "REAL"

    def test_2d_calcule_produit(self):
        # Tableau 2D → taille = produit des dimensions
        shape, size, inner = _parse_array_dims("ARRAY[4,200] OF TRACEDT_T")
        assert shape == (4, 200)
        assert size == 800
        assert inner == "TRACEDT_T"

    def test_type_avec_espace(self):
        # "Position Reg" contient un espace → capturé entièrement
        shape, size, inner = _parse_array_dims("ARRAY[1,300] OF Position Reg")
        assert inner == "Position Reg"

    def test_format_invalide_leve_valueerror(self):
        # Chaîne sans ARRAY → exception
        with pytest.raises(ValueError):
            _parse_array_dims("NOT_AN_ARRAY")

    def test_1d_string(self):
        # Tableau de STRING avec taille
        shape, size, inner = _parse_array_dims("ARRAY[3] OF STRING[21]")
        assert size == 3


# ===========================================================================
# 7 — Tests unitaires : _split_field_name
# ===========================================================================

class TestSplitFieldName:

    def test_systeme_avec_index(self):
        # $VAR[i].$FIELD → décomposition complète
        parent, idx, fname = _split_field_name("$AP_CUREQ[1].$PANE_EQNO")
        assert parent == "$AP_CUREQ"
        assert idx == (1,)
        assert fname == "$PANE_EQNO"

    def test_index_2d(self):
        # Tableau 2D dans le parent
        parent, idx, fname = _split_field_name("$PGTRACEDT[1,2].$LINE_NUM")
        assert parent == "$PGTRACEDT"
        assert idx == (1, 2)
        assert fname == "$LINE_NUM"

    def test_sans_index(self):
        # $VAR.$FIELD → aucun index
        parent, idx, fname = _split_field_name("$ALMDG.$X")
        assert parent == "$ALMDG"
        assert idx is None
        assert fname == "$X"

    def test_karel_pointe(self):
        # Nom Karel avec point dans le parent
        parent, idx, fname = _split_field_name("NFPAM.TBC.CNT_SCALE")
        assert parent == "NFPAM.TBC"
        assert idx is None
        assert fname == "CNT_SCALE"

    def test_fallback_sans_point(self):
        # Nom sans point → fallback (raw, None, raw)
        raw = "NOFIELD"
        parent, idx, fname = _split_field_name(raw)
        assert parent == raw
        assert idx is None
        assert fname == raw


# ===========================================================================
# 8 — Tests unitaires : _is_position_array
# ===========================================================================

class TestIsPositionArray:

    def test_position_reconnu(self):
        # ARRAY OF POSITION classique
        assert _is_position_array("ARRAY[3] OF POSITION") is True

    def test_xyzwpr_reconnu(self):
        # Alias cartésien
        assert _is_position_array("ARRAY[5] OF XYZWPR") is True

    def test_position_reg_reconnu(self):
        # Registres de position (posreg.va)
        assert _is_position_array("ARRAY[1,300] OF Position Reg") is True

    def test_real_non_reconnu(self):
        # Tableau primitif → pas de position
        assert _is_position_array("ARRAY[4] OF REAL") is False

    def test_sans_of_non_reconnu(self):
        # Type scalaire → pas de tableau
        assert _is_position_array("POSITION") is False

    def test_struct_non_reconnu(self):
        # Struct custom → pas de position
        assert _is_position_array("ARRAY[2] OF MY_STRUCT_T") is False


# ===========================================================================
# 9 — Tests fonctionnels : scalaires simples
# ===========================================================================

class TestScalaires:

    @pytest.fixture
    def vars(self, parser, tmp_path):
        return parse_va(parser, tmp_path, VA_SIMPLE_SCALARS)

    def test_compte_variables(self, vars):
        # 4 variables scalaires attendues
        assert len(vars) == 4

    def test_integer_valeur_et_type(self, vars):
        # Variable INTEGER avec valeur inline
        v = next(x for x in vars if x.name == "$ACC_MAXLMT")
        assert v.value == "100"
        assert v.data_type == VADataType.INTEGER
        assert v.storage == StorageType.CMOS
        assert v.access == AccessType.RW
        assert not v.is_array
        assert not v.fields

    def test_boolean_false(self, vars):
        # BOOLEAN FALSE parsé correctement
        v = next(x for x in vars if x.name == "$AP_AUTOMODE")
        assert v.value == "FALSE"
        assert v.data_type == VADataType.BOOLEAN

    def test_string_uninitialized(self, vars):
        # STRING sans valeur → normalisé en Uninitialized
        v = next(x for x in vars if x.name == "$ROBOT_NAME")
        assert v.value == "Uninitialized"
        assert v.data_type == VADataType.STRING

    def test_real_notation_scientifique(self, vars):
        # REAL avec notation scientifique FANUC
        v = next(x for x in vars if x.name == "$PI")
        assert v.value == "3.141593e+00"
        assert v.data_type == VADataType.REAL
        assert v.storage == StorageType.DRAM
        assert v.access == AccessType.RO

    def test_tous_systeme(self, vars):
        # Toutes les variables sont dans le namespace *SYSTEM*
        assert all(v.is_system for v in vars)
        assert all(v.namespace == "*SYSTEM*" for v in vars)


# ===========================================================================
# 10 — Tests fonctionnels : tableaux primitifs
# ===========================================================================

class TestTableauxPrimitifs:

    @pytest.fixture
    def vars(self, parser, tmp_path):
        return parse_va(parser, tmp_path, VA_ARRAYS)

    def test_string_array_taille(self, vars):
        # Tableau STRING[21] de taille 3
        v = next(x for x in vars if x.name == "$APPLICATION")
        assert v.is_array
        assert v.array_size == 3
        assert isinstance(v.value, ArrayValue)

    def test_string_array_valeurs(self, vars):
        # Valeurs extraites correctement, apostrophes supprimées
        v = next(x for x in vars if x.name == "$APPLICATION")
        assert v.value.items[(1,)] == "LR HandlingTool"
        assert v.value.items[(2,)] == "V9.40P/27"
        assert v.value.items[(3,)] == "Uninitialized"

    def test_real_array_valeurs(self, vars):
        # Tableau REAL avec 4 éléments
        v = next(x for x in vars if x.name == "$ANGTOL")
        assert isinstance(v.value, ArrayValue)
        assert len(v.value.items) == 4
        assert v.value.items[(1,)] == "1.000000e+00"
        assert v.value.items[(4,)] == "4.000000e+00"

    def test_cles_sont_tuples(self, vars):
        # Les clés d'ArrayValue sont des tuples 1-élément pour 1D
        v = next(x for x in vars if x.name == "$ANGTOL")
        for key in v.value.items:
            assert isinstance(key, tuple)
            assert len(key) == 1


# ===========================================================================
# 11 — Tests fonctionnels : struct simple
# ===========================================================================

class TestStructSimple:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_STRUCT_SIMPLE)
        return next(x for x in vars if x.name == "$ALMDG")

    def test_est_struct(self, v):
        # Variable struct possède des fields
        assert v.is_struct
        assert len(v.fields) == 2

    def test_valeurs_fields(self, v):
        # Valeurs numériques des fields scalaires
        debug1 = next(f for f in v.fields if "DEBUG1" in f.field_name)
        debug2 = next(f for f in v.fields if "DEBUG2" in f.field_name)
        assert debug1.value == "0"
        assert debug2.value == "42"

    def test_access_fields(self, v):
        # Tous les fields ont l'accès déclaré
        for f in v.fields:
            assert f.access == AccessType.RW

    def test_pas_de_parent_index(self, v):
        # Struct simple : aucun index parent
        for f in v.fields:
            assert f.parent_index_nd is None

    def test_full_name_fields(self, v):
        # Le full_name contient le nom du parent
        for f in v.fields:
            assert "$ALMDG" in f.full_name


# ===========================================================================
# 12 — Tests fonctionnels : tableau de structs
# ===========================================================================

class TestTableauDeStructs:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_ARRAY_OF_STRUCT)
        return next(x for x in vars if x.name == "$AIO_CNV")

    def test_metadata(self, v):
        # Variable tableau de structs bien identifiée
        assert v.is_array
        assert v.array_size == 2

    def test_compte_fields(self, v):
        # 2 éléments × 3 fields = 6 fields
        assert len(v.fields) == 6

    def test_index_parent_field_scalaire(self, v):
        # Field scalaire [1].$RACK → parent_index_nd = (1,)
        rack_fields = [f for f in v.fields if "RACK" in f.field_name]
        assert len(rack_fields) == 2
        assert rack_fields[0].parent_index_nd == (1,)
        assert rack_fields[0].value == "999"
        assert rack_fields[1].parent_index_nd == (2,)

    def test_field_tableau_imbrique(self, v):
        # Field tableau dans un struct → ArrayValue
        distort = next(
            f for f in v.fields
            if "DISTORT" in f.field_name and f.parent_index_nd == (1,)
        )
        assert isinstance(distort.value, ArrayValue)
        assert distort.value.items[(1,)] == "0.000000e+00"
        assert distort.value.items[(2,)] == "1.000000e+00"

    def test_valeurs_element_2(self, v):
        # Les valeurs du deuxième élément sont bien parsées
        rack2 = next(
            f for f in v.fields
            if "RACK" in f.field_name and f.parent_index_nd == (2,)
        )
        assert rack2.value == "0"


# ===========================================================================
# 13 — Tests fonctionnels : tableau N-D
# ===========================================================================

class TestTableauND:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_ND_ARRAY)
        return next(x for x in vars if x.name == "$PGTRACEDT")

    def test_shape_2d(self, v):
        # Tableau 2×3 → shape et taille correctes
        assert v.is_array
        assert v.array_size == 6
        assert v.array_shape == (2, 3)

    def test_index_nd_fields(self, v):
        # Index ND des fields extraits correctement
        f11 = next(f for f in v.fields if f.parent_index_nd == (1, 1))
        f12 = next(f for f in v.fields if f.parent_index_nd == (1, 2))
        f21 = next(f for f in v.fields if f.parent_index_nd == (2, 1))
        assert f11.value == "10"
        assert f12.value == "20"
        assert f21.value == "30"

    def test_full_name_contient_index_nd(self, v):
        # Le full_name représente fidèlement l'index 2D
        f = next(f for f in v.fields if f.parent_index_nd == (1, 1))
        assert "[1,1]" in f.full_name


# ===========================================================================
# 14 — Tests fonctionnels : Karel
# ===========================================================================

class TestKarel:

    @pytest.fixture
    def vars(self, parser, tmp_path):
        return parse_va(parser, tmp_path, VA_KAREL)

    def test_une_variable(self, vars):
        # Une seule variable Karel
        assert len(vars) == 1

    def test_pas_systeme(self, vars):
        # Variable Karel ≠ *SYSTEM*
        v = vars[0]
        assert not v.is_system
        assert v.namespace == "TBSWMD45"
        assert v.name == "NFPAM"

    def test_count_fields(self, vars):
        # Deux fields de type ARRAY
        v = vars[0]
        assert len(v.fields) == 2

    def test_array_valeurs_real(self, vars):
        # Valeurs numériques dans le champ CNT_SCALE
        v = vars[0]
        cnt = next(f for f in v.fields if "CNT_SCALE" in f.field_name)
        assert isinstance(cnt.value, ArrayValue)
        assert cnt.value.items[(1,)] == "1.150000e+00"

    def test_field_sans_dollar(self, vars):
        # Les noms de fields Karel n'ont pas de préfixe $
        v = vars[0]
        for f in v.fields:
            assert not f.field_name.startswith("$")

    def test_parent_var_correcte(self, vars):
        # parent_var doit être le nom complet sans le dernier segment
        v = vars[0]
        for f in v.fields:
            assert "NFPAM" in f.parent_var


# ===========================================================================
# 15 — Tests fonctionnels : POSITION scalaire
# ===========================================================================

class TestPositionScalaire:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_SCALAR_POSITION)
        return vars[0]

    def test_type_position(self, v):
        # Variable de type POSITION reconnue
        assert v.data_type == VADataType.POSITION

    def test_valeur_est_position_value(self, v):
        # La valeur est un objet PositionValue
        assert isinstance(v.value, PositionValue)

    def test_lignes_de_position_remplies(self, v):
        # Les coordonnées sont collectées
        assert any("Group" in l for l in v.value.raw_lines)
        assert any("X:" in l for l in v.value.raw_lines)

    def test_repr_contient_coordonnees(self, v):
        # repr affiche les lignes de position
        r = repr(v.value)
        assert "Group" in r or "X:" in r


# ===========================================================================
# 16 — Tests fonctionnels : Field POSITION
# ===========================================================================

class TestFieldPosition:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_FIELD_POSITION)
        return vars[0]

    def test_field_position_detecte(self, v):
        # Field de type POSITION dans une struct
        f = next(f for f in v.fields if "$POS" in f.field_name)
        assert isinstance(f.value, PositionValue)

    def test_field_position_coordonnees(self, v):
        # Les coordonnées sont capturées dans le field
        f = next(f for f in v.fields if "$POS" in f.field_name)
        assert isinstance(f.value, PositionValue)
        assert any("X:" in l for l in f.value.raw_lines)


# ===========================================================================
# 17 — Tests fonctionnels : ARRAY OF POSITION
# ===========================================================================

class TestArrayDePosition:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_ARRAY_OF_POSITION)
        return vars[0]

    def test_field_array_of_position(self, v):
        # Le field de positions est un ArrayValue
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)

    def test_items_sont_position_values(self, v):
        # Chaque item du tableau est un PositionValue
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        for key, item in pos_field.value.items.items():
            assert isinstance(item, PositionValue), f"item {key} devrait être PositionValue"

    def test_trois_positions(self, v):
        # 3 positions dans le tableau
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert len(pos_field.value.items) == 3

    def test_contenu_premiere_position(self, v):
        # La première position contient bien des coordonnées
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        first = pos_field.value.items[(1,)]
        assert isinstance(first, PositionValue)
        assert any("Group" in l for l in first.raw_lines)
        assert any("X:" in l for l in first.raw_lines)

    def test_field_scalaire_apres_positions(self, v):
        # Field scalaire après ARRAY OF POSITION correctement parsé
        count_field = next((f for f in v.fields if "COUNT" in f.field_name), None)
        assert count_field is not None
        assert count_field.value == "3"

    def test_repr_array_positions(self, v):
        # repr d'un ArrayValue de positions mentionne 'positions'
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert "positions" in repr(pos_field.value)


# ===========================================================================
# 18 — Tests fonctionnels : registres POSREG
# ===========================================================================

class TestPosreg:

    @pytest.fixture
    def v(self, parser, tmp_path):
        vars = parse_va(parser, tmp_path, VA_POSREG)
        return vars[0]

    def test_une_variable(self, parser, tmp_path):
        # Un seul POSREG parsé
        vars = parse_va(parser, tmp_path, VA_POSREG)
        assert len(vars) == 1

    def test_shape_nd(self, v):
        # Tableau 1×3 → shape (1,3)
        assert v.is_array
        assert v.array_shape == (1, 3)
        assert v.array_size == 3

    def test_position_nommee_label(self, v):
        # Position avec label 'OR_Get_Ref' → label capturé
        assert isinstance(v.value, ArrayValue)
        pos = v.value.items.get((1, 1))
        assert isinstance(pos, PositionValue)
        assert pos.label == "OR_Get_Ref"

    def test_display_label(self, v):
        # display_label retourne le label
        assert isinstance(v.value, ArrayValue)
        pos = v.value.items[(1, 1)]
        assert pos.display_label == "OR_Get_Ref"

    def test_position_nommee_a_des_coordonnees(self, v):
        # La position nommée a des coordonnées
        pos = v.value.items[(1, 1)]
        assert any("X:" in l for l in pos.raw_lines)

    def test_position_uninitialized(self, v):
        # Position '' Uninitialized → valeur scalaire
        val = v.value.items.get((1, 2))
        assert val == "Uninitialized"

    def test_position_label_vide_a_des_coordonnees(self, v):
        # Position '' (label vide) avec coordonnées
        pos = v.value.items.get((1, 3))
        assert isinstance(pos, PositionValue)
        assert any("Group" in l or "X:" in l for l in pos.raw_lines)


# ===========================================================================
# 19 — Tests fonctionnels : variables non initialisées
# ===========================================================================

class TestUninitialized:

    @pytest.fixture
    def vars(self, parser, tmp_path):
        return parse_va(parser, tmp_path, VA_UNINITIALIZED)

    def test_struct_vide_est_uninitialized(self, vars):
        # Struct sans aucun field → valeur Uninitialized
        v = next(x for x in vars if x.name == "$DCS_NOCODE")
        assert v.value == "Uninitialized"
        assert not v.fields

    def test_string_uninitialized(self, vars):
        # STRING explicitement Uninitialized
        v = next(x for x in vars if x.name == "$PAUSE_PROG")
        assert v.value == "Uninitialized"


# ===========================================================================
# 20 — Tests fonctionnels : métadonnées
# ===========================================================================

class TestMetadonnees:

    def test_source_file_traque(self, parser, tmp_path):
        # Chaque variable connaît son fichier source
        vars = parse_va(parser, tmp_path, VA_SIMPLE_SCALARS)
        for v in vars:
            assert v.source_file is not None
            assert v.source_file.name == "test.VA"

    def test_line_number_premiere_variable(self, parser, tmp_path):
        # Le numéro de ligne de la première variable est 1
        vars = parse_va(parser, tmp_path, VA_SIMPLE_SCALARS)
        v = next(x for x in vars if x.name == "$ACC_MAXLMT")
        assert v.line_number == 1

    def test_type_str_sans_valeur(self, parser, tmp_path):
        # type_str extrait le type sans la valeur inline
        vars = parse_va(parser, tmp_path, VA_SIMPLE_SCALARS)
        v = next(x for x in vars if x.name == "$ACC_MAXLMT")
        assert v.type_str == "INTEGER"

    def test_type_str_array(self, parser, tmp_path):
        # type_str sur un tableau retourne la spec complète (pas de '=')
        vars = parse_va(parser, tmp_path, VA_ARRAYS)
        v = next(x for x in vars if x.name == "$ANGTOL")
        assert v.type_str == "ARRAY[4] OF REAL"


# ===========================================================================
# 21 — Tests de robustesse
# ===========================================================================

class TestRobustesse:

    def test_fichier_absent_retourne_vide(self, parser):
        # Fichier inexistant → liste vide sans exception
        result = parser.parse_file(Path("/nonexistent/file.VA"))
        assert result == []

    def test_fichier_vide_retourne_vide(self, parser, tmp_path):
        # Fichier vide → liste vide sans exception
        p = write_va(tmp_path, "", "empty.VA")
        assert parser.parse_file(p) == []

    def test_can_parse_true_avec_va(self, parser, tmp_path):
        # Dossier contenant .VA → can_parse() True
        write_va(tmp_path, "")
        assert parser.can_parse(tmp_path) is True

    def test_can_parse_false_sans_va(self, parser, tmp_path):
        # Dossier sans .VA → can_parse() False
        (tmp_path / "readme.txt").write_text("", encoding="utf-8")
        assert parser.can_parse(tmp_path) is False

    def test_can_parse_oserror_retourne_false(self, parser):
        # Dossier inexistant → OSError absorbée → False
        assert parser.can_parse(Path("/nonexistent/path")) is False

    def test_parse_dossier_sans_va_retourne_vide(self, parser, tmp_path):
        # parse() sur dossier vide → liste vide + appel progress(0,0)
        calls = []
        result = parser.parse(tmp_path, progress_cb=lambda c, t, m: calls.append((c, t)))
        assert result == []
        assert calls[0] == (0, 0)

    def test_parse_progress_callback_appele(self, parser, tmp_path):
        # progress_cb appelé au moins une fois lors du parsing
        write_va(tmp_path, VA_SIMPLE_SCALARS)
        calls = []
        parser.parse(tmp_path, progress_cb=lambda c, t, m: calls.append((c, t)))
        assert len(calls) >= 1

    def test_lignes_parasites_ignorees(self, parser, tmp_path):
        # Lignes non reconnues ignorées sans crasher
        content = (
            "[*SYSTEM*]$SIMPLE  Storage: CMOS  Access: RW  : INTEGER = 7\n"
            "\n"
            "This line is garbage and will be ignored\n"
            "\n"
            "[*SYSTEM*]$SECOND  Storage: CMOS  Access: RW  : INTEGER = 8\n"
        )
        vars = parse_va(parser, tmp_path, content)
        assert len(vars) == 2

    def test_encodage_utf8_invalide_remplace(self, parser, tmp_path):
        # Octets invalides en UTF-8 → remplacés, pas d'exception
        p = tmp_path / "bad.VA"
        raw = b"[*SYSTEM*]$X  Storage: CMOS  Access: RW  : INTEGER = 1\x84\xff\n"
        p.write_bytes(raw)
        result = parser.parse_file(p)
        # Au moins une variable parsée malgré les octets invalides
        assert len(result) >= 1

    def test_extension_va_insensible_casse(self, parser, tmp_path):
        # .va en minuscule aussi reconnu
        p = tmp_path / "test.va"
        p.write_text(VA_SIMPLE_SCALARS, encoding="utf-8")
        result = parser.parse_file(p)
        assert len(result) > 0

    def test_parse_directory_agrege_fichiers(self, parser, tmp_path):
        # parse_directory parcourt récursivement tous les .VA
        write_va(tmp_path, VA_SIMPLE_SCALARS, "a.VA")
        write_va(tmp_path, VA_KAREL, "b.va")
        (tmp_path / "ignore.txt").write_text("not VA", encoding="utf-8")
        result = parser.parse_directory(tmp_path)
        assert isinstance(result, ExtractionResult)
        assert result.var_count == 5   # 4 scalaires + 1 Karel
        assert result.errors == []

    def test_parse_directory_sous_dossier(self, parser, tmp_path):
        # parse_directory est récursif
        sub = tmp_path / "sub"
        sub.mkdir()
        write_va(sub, VA_SIMPLE_SCALARS, "deep.VA")
        result = parser.parse_directory(tmp_path)
        assert result.var_count == 4