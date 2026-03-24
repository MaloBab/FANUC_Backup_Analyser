"""
test_dataid_csv_parser.py — Tests du DataIdCsvParser et de ses helpers.

Couvre :
  - Unitaires : helpers de normalisation, parsing d'index, détection d'encodage
  - Fonctionnels : lecture CSV valide, reconstruction des RobotVariable
  - Cas limites : BOM UTF-8, *Uninitialized*, accès CW, POSITION inline
  - Robustesse : format invalide, colonnes manquantes, noms malformés, fichier absent
  - Intégration : DataIdCsvParser.parse() bout-en-bout
"""

from __future__ import annotations

from pathlib import Path

import pytest

from models.fanuc_models import (
    AccessType,
    ArrayValue,
    ExtractionResult,
    PositionValue,
    RobotVariable,
    StorageType,
    VADataType,
)
from services.parser.dataid_csv_parser import (
    DataIdCsvParser,
    _build_variables,
    _detect_encoding,
    _normalize_value,
    _parse_access,
    _parse_datatype,
    _parse_index,
    _parse_position_value,
    _read_csv_rows,
    parse_dataid_file,
)

from test_config import (
    DATAID_BAD_FIRST_LINE,
    DATAID_BAD_REM,
    DATAID_CW_ACCESS,
    DATAID_FULL,
    DATAID_MISSING_COLUMNS,
    DATAID_TOO_SHORT,
    DATAID_UNINIT,
    DATAID_WITH_POSITION,
    write_dataid,
)


# ===========================================================================
# 1 — Tests unitaires : _normalize_value
# ===========================================================================

class TestNormalizeValue:

    def test_uninitialized_csv_normalise(self):
        # *Uninitialized* → "Uninitialized" (cohérence avec .VA)
        assert _normalize_value("*Uninitialized*") == "Uninitialized"

    def test_valeur_normale_strippee(self):
        # Espaces autour d'une valeur normale → strippés
        assert _normalize_value("  hello  ") == "hello"

    def test_valeur_vide(self):
        # Valeur vide → chaîne vide
        assert _normalize_value("") == ""

    def test_valeur_numerique_inchangee(self):
        # Valeur numérique conservée telle quelle
        assert _normalize_value("42") == "42"

    def test_valeur_booleenne_inchangee(self):
        # Booléen conservé
        assert _normalize_value("TRUE") == "TRUE"


# ===========================================================================
# 2 — Tests unitaires : _parse_access (CSV)
# ===========================================================================

class TestCsvParseAccess:

    def test_rw(self):
        # Accès standard RW
        assert _parse_access("RW") == AccessType.RW

    def test_ro(self):
        # Accès lecture seule
        assert _parse_access("RO") == AccessType.RO

    def test_fp(self):
        # Force-protect
        assert _parse_access("FP") == AccessType.FP

    def test_wo(self):
        # Écriture seule
        assert _parse_access("WO") == AccessType.WO

    def test_cw_mappe_sur_ro(self):
        # CW (Condition Write) → traitement conservateur → RO
        assert _parse_access("CW") == AccessType.RO

    def test_inconnu_retourne_unknown(self):
        # Valeur non reconnue → UNKNOWN sans exception
        assert _parse_access("ZZ") == AccessType.UNKNOWN

    def test_insensible_casse(self):
        # Insensible à la casse
        assert _parse_access("rw") == AccessType.RW

    def test_whitespace_strips(self):
        # Espaces ignorés
        assert _parse_access("  RO  ") == AccessType.RO


# ===========================================================================
# 3 — Tests unitaires : _parse_datatype (CSV)
# ===========================================================================

class TestCsvParseDatatype:

    def test_boolean(self):
        # Type booléen CSV
        assert _parse_datatype("BOOLEAN") == VADataType.BOOLEAN

    def test_integer(self):
        # Type entier
        assert _parse_datatype("INTEGER") == VADataType.INTEGER

    def test_real(self):
        # Type réel
        assert _parse_datatype("REAL") == VADataType.REAL

    def test_string(self):
        # Type chaîne
        assert _parse_datatype("STRING") == VADataType.STRING

    def test_position(self):
        # Type position cartésienne
        assert _parse_datatype("POSITION") == VADataType.POSITION

    def test_inconnu_retourne_unknown(self):
        # Type personnalisé non répertorié → UNKNOWN
        assert _parse_datatype("CUSTOM_TYPE") == VADataType.UNKNOWN

    def test_insensible_casse(self):
        # Insensible à la casse
        assert _parse_datatype("integer") == VADataType.INTEGER


# ===========================================================================
# 4 — Tests unitaires : _parse_index (CSV)
# ===========================================================================

class TestCsvParseIndex:

    def test_index_simple(self):
        # "3" → (3,)
        assert _parse_index("3") == (3,)

    def test_index_2d(self):
        # "1,2" → (1, 2)
        assert _parse_index("1,2") == (1, 2)

    def test_none_retourne_none(self):
        # None → pas d'index
        assert _parse_index(None) is None

    def test_vide_retourne_none(self):
        # Chaîne vide → None
        assert _parse_index("") is None

    def test_espaces_strips(self):
        # Espaces autour des valeurs ignorés
        assert _parse_index(" 1 , 2 ") == (1, 2)


# ===========================================================================
# 5 — Tests unitaires : _parse_position_value
# ===========================================================================

class TestParsePositionValue:

    def test_segments_slashes(self):
        # Valeur séparée par '/' → liste de segments
        pv = _parse_position_value("Group:1/X:0.0/Y:0.0/Z:0.0")
        assert isinstance(pv, PositionValue)
        assert "Group:1" in pv.raw_lines

    def test_tous_les_axes(self):
        # 7 segments → 7 raw_lines
        pv = _parse_position_value("Group:1/X:0.0/Y:0.0/Z:0.0/W:0.0/P:0.0/R:0.0")
        assert len(pv.raw_lines) == 7

    def test_label_vide(self):
        # PositionValue CSV → label toujours vide
        pv = _parse_position_value("Group:1/X:0.0")
        assert pv.label == ""

    def test_segments_strippes(self):
        # Espaces autour des segments ignorés
        pv = _parse_position_value("  Group:1  /  X:0.0  ")
        assert "Group:1" in pv.raw_lines


# ===========================================================================
# 6 — Tests unitaires : _detect_encoding
# ===========================================================================

class TestDetectEncoding:

    def test_utf8_sans_bom(self, tmp_path):
        # Fichier UTF-8 standard → "utf-8"
        p = tmp_path / "f.csv"
        p.write_bytes(b"hello")
        assert _detect_encoding(p) == "utf-8"

    def test_utf8_avec_bom(self, tmp_path):
        # BOM UTF-8 (0xEF 0xBB 0xBF) → "utf-8-sig"
        p = tmp_path / "f.csv"
        p.write_bytes(b"\xef\xbb\xbfhello")
        assert _detect_encoding(p) == "utf-8-sig"

    def test_fichier_court_pas_bom(self, tmp_path):
        # Fichier de 2 octets → pas de BOM → "utf-8"
        p = tmp_path / "f.csv"
        p.write_bytes(b"\xef\xbb")
        assert _detect_encoding(p) == "utf-8"

    def test_oserror_retourne_utf8(self):
        # Fichier inexistant → OSError absorbée → "utf-8"
        assert _detect_encoding(Path("/nonexistent/file.csv")) == "utf-8"


# ===========================================================================
# 7 — Tests fonctionnels : _read_csv_rows
# ===========================================================================

class TestReadCsvRows:

    def test_fichier_valide_version(self, tmp_path):
        # Version extraite de la ligne DATAIDVER
        p = write_dataid(tmp_path, DATAID_FULL)
        version, _ = _read_csv_rows(p)
        assert version == "V9.40"

    def test_fichier_valide_lignes(self, tmp_path):
        # Nombre de lignes DATAID parsées
        p = write_dataid(tmp_path, DATAID_FULL)
        _, rows = _read_csv_rows(p)
        assert len(rows) == 6

    def test_ligne_end_absente_des_rows(self, tmp_path):
        # La ligne END ne doit pas apparaître dans les rows
        p = write_dataid(tmp_path, DATAID_FULL)
        _, rows = _read_csv_rows(p)
        for row in rows:
            assert row.get("REM", "").strip() != "END"

    def test_mauvaise_premiere_ligne_leve_valueerror(self, tmp_path):
        # Ligne DATAIDVER manquante → ValueError
        p = write_dataid(tmp_path, DATAID_BAD_FIRST_LINE)
        with pytest.raises(ValueError, match="Première ligne"):
            _read_csv_rows(p)

    def test_trop_court_leve_valueerror(self, tmp_path):
        # Fichier avec seulement DATAIDVER → ValueError
        p = write_dataid(tmp_path, DATAID_TOO_SHORT)
        with pytest.raises(ValueError, match="trop court"):
            _read_csv_rows(p)

    def test_colonnes_manquantes_leve_valueerror(self, tmp_path):
        # Colonnes attendues absentes → ValueError
        p = write_dataid(tmp_path, DATAID_MISSING_COLUMNS)
        with pytest.raises(ValueError, match="Colonnes manquantes"):
            _read_csv_rows(p)

    def test_rem_incorrect_leve_valueerror(self, tmp_path):
        # Ligne d'en-tête ne commençant pas par REM → ValueError
        p = write_dataid(tmp_path, DATAID_BAD_REM)
        with pytest.raises(ValueError, match="en-têtes"):
            _read_csv_rows(p)

    def test_fichier_vide_leve_valueerror(self, tmp_path):
        # Fichier complètement vide → ValueError
        p = write_dataid(tmp_path, "")
        with pytest.raises(ValueError):
            _read_csv_rows(p)

    def test_bom_utf8_accepte(self, tmp_path):
        # Fichier avec BOM UTF-8 parsé correctement
        content_bytes = b"\xef\xbb\xbf" + DATAID_FULL.encode("utf-8")
        p = tmp_path / "DATAID.CSV"
        p.write_bytes(content_bytes)
        version, rows = _read_csv_rows(p)
        assert version == "V9.40"
        assert len(rows) == 6


# ===========================================================================
# 8 — Tests fonctionnels : _build_variables
# ===========================================================================

class TestBuildVariables:

    @pytest.fixture
    def rows_full(self, tmp_path):
        p = write_dataid(tmp_path, DATAID_FULL)
        _, rows = _read_csv_rows(p)
        return rows, p

    def test_deux_parents(self, rows_full):
        # $ALARM et $OTHER → 2 variables parentes
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        names = [v.name for v in variables]
        assert "$ALARM" in names
        assert "$OTHER" in names

    def test_ordre_preserve(self, rows_full):
        # L'ordre d'apparition des parents est conservé
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        assert variables[0].name == "$ALARM"
        assert variables[1].name == "$OTHER"

    def test_fields_indexes_fusionnes_en_array(self, rows_full):
        # ERROR_SEVERITY_TABLE[1] et [2] fusionnés dans un seul ArrayValue
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        table_field = next(
            f for f in alarm.fields if f.field_name == "ERROR_SEVERITY_TABLE"
        )
        assert isinstance(table_field.value, ArrayValue)
        assert table_field.value.items[(1,)] == "3"
        assert table_field.value.items[(2,)] == "5"

    def test_field_scalaire_valeur_directe(self, rows_full):
        # AUTO_DISPLAY → field scalaire avec valeur directe
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        display = next(f for f in alarm.fields if f.field_name == "AUTO_DISPLAY")
        assert display.value == "TRUE"

    def test_condition_handler_stocke(self, rows_full):
        # ConditionHandler stocké dans le champ dédié
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        table_field = next(
            f for f in alarm.fields if f.field_name == "ERROR_SEVERITY_TABLE"
        )
        assert table_field.condition_handler == "HANDLER_A"

    def test_condition_handler_vide_si_absent(self, rows_full):
        # ConditionHandler absent → chaîne vide
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        display = next(f for f in alarm.fields if f.field_name == "AUTO_DISPLAY")
        assert display.condition_handler == ""

    def test_source_file_assigne(self, rows_full):
        # Chaque variable connaît son fichier source
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        for v in variables:
            assert v.source_file == p

    def test_storage_unknown_pour_dataid(self, rows_full):
        # DATAID.CSV n'a pas de champ Storage → UNKNOWN
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        for v in variables:
            assert v.storage == StorageType.UNKNOWN

    def test_uninitialized_normalise(self, tmp_path):
        # *Uninitialized* → "Uninitialized"
        p = write_dataid(tmp_path, DATAID_UNINIT)
        _, rows = _read_csv_rows(p)
        variables, _ = _build_variables(rows, p)
        for v in variables:
            for f in v.fields:
                assert f.value == "Uninitialized"

    def test_position_value_parsee(self, tmp_path):
        # POSITION inline → PositionValue
        p = write_dataid(tmp_path, DATAID_WITH_POSITION)
        _, rows = _read_csv_rows(p)
        variables, _ = _build_variables(rows, p)
        master = next(v for v in variables if v.name == "$MASTER")
        point = next(f for f in master.fields if f.field_name == "POINT")
        assert isinstance(point.value, PositionValue)

    def test_nom_non_reconnu_genere_erreur(self, tmp_path):
        # Nom DATAID sans '.' → non reconnu → erreur remontée
        bad = (
            "DATAIDVER,V9.40,!!!!\n"
            "REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!\n"
            "DATAID,NOT_A_VALID_NAME,INTEGER,1,RW,,!!!!\n"
            "END,!!!!\n"
        )
        p = write_dataid(tmp_path, bad)
        _, rows = _read_csv_rows(p)
        variables, errors = _build_variables(rows, p)
        assert len(errors) >= 1
        assert len(variables) == 0

    def test_is_array_true_quand_indexes(self, rows_full):
        # Quand un field est indexé → is_array de la variable parente = True
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        assert alarm.is_array is True

    def test_pas_de_conflit_entre_parents(self, rows_full):
        # Les fields de $OTHER n'apparaissent pas dans $ALARM
        rows, p = rows_full
        variables, _ = _build_variables(rows, p)
        alarm = next(v for v in variables if v.name == "$ALARM")
        alarm_field_names = {f.field_name for f in alarm.fields}
        assert "FIELD" not in alarm_field_names


# ===========================================================================
# 9 — Tests fonctionnels : DataIdCsvParser.can_parse / parse
# ===========================================================================

class TestDataIdCsvParser:

    def test_can_parse_true(self, csv_parser, tmp_path):
        # Dossier avec DATAID.CSV → True
        write_dataid(tmp_path, DATAID_FULL)
        assert csv_parser.can_parse(tmp_path) is True

    def test_can_parse_false_sans_fichier(self, csv_parser, tmp_path):
        # Dossier vide → False
        assert csv_parser.can_parse(tmp_path) is False

    def test_can_parse_insensible_casse(self, csv_parser, tmp_path):
        # dataid.csv en minuscule reconnu aussi
        write_dataid(tmp_path, DATAID_FULL, filename="dataid.csv")
        assert csv_parser.can_parse(tmp_path) is True

    def test_format_id(self, csv_parser):
        # FORMAT_ID du parser DATAID
        assert csv_parser.FORMAT_ID == "dataid_csv"

    def test_parse_retourne_variables(self, csv_parser, tmp_path):
        # parse() retourne une liste non vide
        write_dataid(tmp_path, DATAID_FULL)
        variables = csv_parser.parse(tmp_path)
        assert len(variables) >= 2

    def test_parse_sans_fichier_retourne_vide(self, csv_parser, tmp_path):
        # Dossier sans DATAID.CSV → liste vide
        result = csv_parser.parse(tmp_path)
        assert result == []

    def test_parse_fichier_invalide_retourne_vide(self, csv_parser, tmp_path):
        # Fichier mal formé → liste vide (erreur loguée, pas d'exception)
        write_dataid(tmp_path, DATAID_BAD_FIRST_LINE)
        result = csv_parser.parse(tmp_path)
        assert result == []

    def test_parse_progress_callback_appele(self, csv_parser, tmp_path):
        # progress_cb invoqué au moins deux fois (début + fin)
        write_dataid(tmp_path, DATAID_FULL)
        calls = []
        csv_parser.parse(tmp_path, progress_cb=lambda c, t, m: calls.append(m))
        assert len(calls) >= 2

    def test_cw_access_traite_comme_ro(self, csv_parser, tmp_path):
        # Access CW → traitement conservateur → RO
        write_dataid(tmp_path, DATAID_CW_ACCESS)
        variables = csv_parser.parse(tmp_path)
        v = variables[0]
        flag = next(f for f in v.fields if f.field_name == "FLAG")
        assert flag.access == AccessType.RO

    def test_fp_access_correct(self, csv_parser, tmp_path):
        # Access FP (force-protect) correctement mappe
        write_dataid(tmp_path, DATAID_CW_ACCESS)
        variables = csv_parser.parse(tmp_path)
        v = variables[0]
        val = next(f for f in v.fields if f.field_name == "VALUE")
        assert val.access == AccessType.FP

    def test_wo_access_correct(self, csv_parser, tmp_path):
        # Access WO (write-only) correctement mappe
        write_dataid(tmp_path, DATAID_CW_ACCESS)
        variables = csv_parser.parse(tmp_path)
        v = variables[0]
        other = next(f for f in v.fields if f.field_name == "OTHER")
        assert other.access == AccessType.WO


# ===========================================================================
# 10 — Tests d'intégration : parse_dataid_file (standalone)
# ===========================================================================

class TestParseDataidFileStandalone:

    def test_retourne_extraction_result(self, tmp_path):
        # Fonction standalone retourne un ExtractionResult
        p = write_dataid(tmp_path, DATAID_FULL)
        result = parse_dataid_file(p)
        assert isinstance(result, ExtractionResult)

    def test_variables_extraites(self, tmp_path):
        # Variables dans le résultat
        p = write_dataid(tmp_path, DATAID_FULL)
        result = parse_dataid_file(p)
        assert result.var_count >= 2

    def test_erreur_sur_fichier_invalide(self, tmp_path):
        # Fichier invalide → erreurs remontées dans ExtractionResult
        p = write_dataid(tmp_path, DATAID_BAD_FIRST_LINE)
        result = parse_dataid_file(p)
        assert len(result.errors) >= 1

    def test_pas_derreur_sur_fichier_valide(self, tmp_path):
        # Fichier valide → aucune erreur
        p = write_dataid(tmp_path, DATAID_FULL)
        result = parse_dataid_file(p)
        assert result.errors == []

    def test_input_dir_est_parent_du_csv(self, tmp_path):
        # input_dir du résultat = dossier parent du CSV
        p = write_dataid(tmp_path, DATAID_FULL)
        result = parse_dataid_file(p)
        assert result.input_dir == tmp_path