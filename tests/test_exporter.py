"""
test_exporter.py — Tests du VariableExporter (CSV résumé, CSV flat, JSON).

Couvre :
  - Unitaires : _serialize_value, _field_to_dict, to_dict
  - Fonctionnels : format csv (colonnes, lignes, valeurs)
  - Fonctionnels : format csv_flat (expansion tableau, index N-D, ConditionHandler)
  - Fonctionnels : format json (structure, valeurs, fields)
  - Robustesse : format inconnu, dossier parent créé, liste vide, casse fmt
  - Couverture : ArrayValue, PositionValue, struct, variable sans fields
"""

from __future__ import annotations

import csv
import json
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
    _field_to_dict,
    _serialize_value,
)
from services.exporter import ExportError, VariableExporter

from tests.conftest import make_field, make_var


# ===========================================================================
# Jeu de variables de test
# ===========================================================================

def _make_test_vars() -> list[RobotVariable]:
    """Ensemble représentatif de variables couvrant tous les cas d'export."""

    # Scalaire entier
    v_int = make_var(
        name="$INT_VAR",
        storage=StorageType.CMOS,
        access=AccessType.RW,
        type_detail="INTEGER = 42",
        value="42",
        source_file=Path("sys.va"),
    )

    # Tableau de chaînes
    v_arr = make_var(
        name="$ARR_VAR",
        is_array=True,
        array_size=3,
        data_type=VADataType.STRING,
        type_detail="ARRAY[3] OF STRING[21]",
        value=ArrayValue(items={(1,): "alpha", (2,): "beta", (3,): "gamma"}),
        source_file=Path("sys.va"),
    )

    # Tableau 2D d'entiers
    v_nd = make_var(
        name="$ND_VAR",
        is_array=True,
        array_size=4,
        array_shape=(2, 2),
        type_detail="ARRAY[2,2] OF INTEGER",
        value=ArrayValue(items={(1, 1): "10", (1, 2): "20", (2, 1): "30", (2, 2): "40"}),
        source_file=Path("sys.va"),
    )

    # Variable POSITION scalaire
    v_pos = make_var(
        name="$POS_VAR",
        data_type=VADataType.POSITION,
        type_detail="POSITION =",
        value=PositionValue(raw_lines=["Group: 1", "X: 100.0", "Y: 200.0"]),
        source_file=Path("sys.va"),
    )

    # Variable struct avec fields scalaires et condition_handler
    f1 = make_field(
        full_name="$STRUCT.$F1",
        parent_var="$STRUCT",
        field_name="$F1",
        value="99",
        condition_handler="MY_HANDLER",
    )
    f2 = make_field(
        full_name="$STRUCT.$F2",
        parent_var="$STRUCT",
        field_name="$F2",
        data_type=VADataType.BOOLEAN,
        type_detail="BOOLEAN",
        value="TRUE",
    )
    v_struct = make_var(
        name="$STRUCT",
        data_type=VADataType.STRUCT,
        type_detail="MYSTRUCT_T =",
        fields=[f1, f2],
        source_file=Path("sys.va"),
    )

    # Variable struct avec field tableau
    f_arr = make_field(
        full_name="$STRARR.$TABLE",
        parent_var="$STRARR",
        field_name="$TABLE",
        data_type=VADataType.REAL,
        type_detail="ARRAY[3] OF REAL",
        value=ArrayValue(items={(1,): "1.0", (2,): "2.0", (3,): "3.0"}),
    )
    v_struct_arr = make_var(
        name="$STRARR",
        data_type=VADataType.STRUCT,
        type_detail="STRARR_T =",
        fields=[f_arr],
        source_file=Path("sys.va"),
    )

    return [v_int, v_arr, v_nd, v_pos, v_struct, v_struct_arr]


# ===========================================================================
# 1 — Tests unitaires : _serialize_value
# ===========================================================================

class TestSerializeValue:

    def test_none_retourne_none(self):
        # None → None sérialisé
        assert _serialize_value(None) is None

    def test_chaine_retournee_telle_quelle(self):
        # Scalaire chaîne → identique
        assert _serialize_value("hello") == "hello"

    def test_array_value_1d_cles_str(self):
        # ArrayValue 1D → dict avec clés "1", "2"
        arr = ArrayValue(items={(1,): "a", (2,): "b"})
        result = _serialize_value(arr)
        assert isinstance(result, dict)
        assert result["1"] == "a"
        assert result["2"] == "b"

    def test_array_value_2d_cles_virgule(self):
        # ArrayValue 2D → clé "1,2"
        arr = ArrayValue(items={(1, 2): "x"})
        result = _serialize_value(arr)
        assert "1,2" in result

    def test_position_value_jointure_pipe(self):
        # PositionValue → lignes jointes par " | "
        pv = PositionValue(raw_lines=["Group: 1", "X: 0.0"])
        result = _serialize_value(pv)
        assert "Group: 1" in result
        assert "X: 0.0" in result
        assert " | " in result

    def test_array_avec_position_value(self):
        # ArrayValue contenant des PositionValue → serialisé en str
        pv = PositionValue(raw_lines=["X: 0.0"])
        arr = ArrayValue(items={(1,): pv})
        result = _serialize_value(arr)
        assert result["1"] == "X: 0.0"

    def test_position_sans_lignes(self):
        # PositionValue vide → " | ".join([]) = ""
        pv = PositionValue(raw_lines=[])
        result = _serialize_value(pv)
        assert result == ""


# ===========================================================================
# 2 — Tests unitaires : _field_to_dict
# ===========================================================================

class TestFieldToDict:

    def test_cles_presentes(self):
        # Clés minimales toujours présentes
        f = make_field(value="42")
        d = _field_to_dict(f)
        required = {"full_name", "field_name", "parent_index_nd", "access", "type", "value"}
        assert required <= set(d.keys())

    def test_condition_handler_inclus_si_set(self):
        # condition_handler présent quand non vide
        f = make_field(value="x", condition_handler="MY_HANDLER")
        d = _field_to_dict(f)
        assert "condition_handler" in d
        assert d["condition_handler"] == "MY_HANDLER"

    def test_condition_handler_exclu_si_vide(self):
        # condition_handler absent quand vide → pas de colonne parasite
        f = make_field(value="x", condition_handler="")
        d = _field_to_dict(f)
        assert "condition_handler" not in d

    def test_parent_index_nd_comme_liste(self):
        # parent_index_nd tuple → converti en liste dans le dict
        f = make_field(parent_index_nd=(1, 2))
        d = _field_to_dict(f)
        assert d["parent_index_nd"] == [1, 2]

    def test_parent_index_nd_none(self):
        # parent_index_nd None → None dans le dict
        f = make_field(parent_index_nd=None)
        d = _field_to_dict(f)
        assert d["parent_index_nd"] is None

    def test_valeur_serialisee(self):
        # La valeur est sérialisée via _serialize_value
        arr = ArrayValue(items={(1,): "v"})
        f = make_field(value=arr)
        d = _field_to_dict(f)
        assert isinstance(d["value"], dict)


# ===========================================================================
# 3 — Tests unitaires : RobotVariable.to_dict
# ===========================================================================

class TestRobotVariableToDict:

    def test_cles_requises(self):
        # Clés minimales du to_dict
        v = make_var(name="$X", value="1")
        d = v.to_dict()
        expected = {"name", "namespace", "storage", "access", "type",
                    "is_array", "array_size", "array_shape", "value", "fields", "source", "line"}
        assert expected <= set(d.keys())

    def test_array_shape_converti_en_liste(self):
        # array_shape tuple → liste JSON-serialisable
        v = make_var(is_array=True, array_size=6, array_shape=(2, 3))
        d = v.to_dict()
        assert d["array_shape"] == [2, 3]

    def test_array_shape_none_si_absent(self):
        # Variable sans shape → None
        v = make_var()
        d = v.to_dict()
        assert d["array_shape"] is None

    def test_source_none_si_absent(self):
        # source_file None → None dans le dict
        v = make_var(source_file=None)
        d = v.to_dict()
        assert d["source"] is None

    def test_source_chemin_str(self):
        # source_file présent → chemin sous forme de str
        v = make_var(source_file=Path("/path/to/sys.va"))
        d = v.to_dict()
        assert isinstance(d["source"], str)
        assert "sys.va" in d["source"]

    def test_fields_serialises(self):
        # Les fields sont inclus dans le dict
        f = make_field(value="99")
        v = make_var(fields=[f])
        d = v.to_dict()
        assert len(d["fields"]) == 1


# ===========================================================================
# 4 — Tests fonctionnels : export CSV résumé
# ===========================================================================

class TestExportCSVResume:

    @pytest.fixture
    def out(self, exporter, tmp_path) -> Path:
        p = tmp_path / "out.csv"
        exporter.export(_make_test_vars(), p, "csv")
        return p

    def test_fichier_cree(self, out):
        # Le fichier doit être créé
        assert out.exists()

    def test_nombre_de_lignes(self, out):
        # Une ligne par variable (hors header)
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == len(_make_test_vars())

    def test_colonnes_presentes(self, out):
        # Colonnes attendues dans le CSV résumé
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        expected = {"namespace", "name", "storage", "access", "type_detail",
                    "is_array", "array_size", "value", "field_count", "source"}
        assert expected <= set(rows[0].keys())

    def test_valeur_scalaire_presente(self, out):
        # Valeur scalaire correctement exportée
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        int_row = next(r for r in rows if r["name"] == "$INT_VAR")
        assert int_row["value"] == "42"

    def test_array_value_serialisee(self, out):
        # ArrayValue → valeur non vide dans le résumé
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        arr_row = next(r for r in rows if r["name"] == "$ARR_VAR")
        assert arr_row["value"] != ""

    def test_field_count_correct(self, out):
        # field_count = nombre de fields du struct
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        struct_row = next(r for r in rows if r["name"] == "$STRUCT")
        assert struct_row["field_count"] == "2"

    def test_storage_valeur_enum(self, out):
        # storage = valeur de l'enum (ex: "CMOS")
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        int_row = next(r for r in rows if r["name"] == "$INT_VAR")
        assert int_row["storage"] == "CMOS"

    def test_cree_dossier_parent(self, exporter, tmp_path):
        # Création automatique du dossier parent inexistant
        out = tmp_path / "sub" / "dir" / "out.csv"
        exporter.export([], out, "csv")
        assert out.exists()


# ===========================================================================
# 5 — Tests fonctionnels : export CSV flat
# ===========================================================================

class TestExportCSVFlat:

    @pytest.fixture
    def out(self, exporter, tmp_path) -> Path:
        p = tmp_path / "flat.csv"
        exporter.export(_make_test_vars(), p, "csv_flat")
        return p

    @pytest.fixture
    def rows(self, out):
        return list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))

    def test_fichier_cree(self, out):
        # Le fichier doit être créé
        assert out.exists()

    def test_plus_de_lignes_que_variables(self, rows):
        # csv_flat expandit les tableaux → plus de lignes que de variables
        assert len(rows) > len(_make_test_vars())

    def test_colonnes_index_presentes(self, rows):
        # Colonnes index_1 … index_7 présentes
        assert "index_1" in rows[0]
        assert "index_2" in rows[0]

    def test_tableau_1d_indices_remplis(self, rows):
        # ARR_VAR → index_1 rempli, index_2 vide
        arr_rows = [r for r in rows if r.get("variable") == "$ARR_VAR"]
        assert len(arr_rows) == 3
        idx_vals = {r["index_1"] for r in arr_rows}
        assert "1" in idx_vals
        assert "2" in idx_vals
        assert "3" in idx_vals
        for r in arr_rows:
            assert r["index_2"] == ""

    def test_tableau_2d_index_2_rempli(self, rows):
        # ND_VAR → index_1 et index_2 tous les deux remplis
        nd_rows = [r for r in rows if r.get("variable") == "$ND_VAR"]
        assert len(nd_rows) == 4
        idx2_vals = {r["index_2"] for r in nd_rows}
        assert "1" in idx2_vals
        assert "2" in idx2_vals

    def test_field_scalaire_struct(self, rows):
        # Fields scalaires du struct apparaissent chacun sur une ligne
        struct_rows = [r for r in rows if r.get("variable") == "$STRUCT"]
        field_names = {r["field"] for r in struct_rows}
        assert "$F1" in field_names
        assert "$F2" in field_names

    def test_condition_colonne_presente(self, rows):
        # Colonne condition présente dans csv_flat
        assert "condition" in rows[0]

    def test_condition_handler_exporte(self, rows):
        # condition_handler rempli quand présent
        struct_rows = [r for r in rows if r.get("variable") == "$STRUCT"]
        f1_row = next(r for r in struct_rows if r["field"] == "$F1")
        assert f1_row["condition"] == "MY_HANDLER"

    def test_field_tableau_dans_struct(self, rows):
        # Field tableau d'un struct est expandit
        strarr_rows = [r for r in rows if r.get("variable") == "$STRARR"]
        assert len(strarr_rows) == 3

    def test_position_value_serialisee(self, rows):
        # PositionValue → valeur lisible dans le CSV
        pos_rows = [r for r in rows if r.get("variable") == "$POS_VAR"]
        assert len(pos_rows) == 1
        assert pos_rows[0]["value"] != ""


# ===========================================================================
# 6 — Tests fonctionnels : export JSON
# ===========================================================================

class TestExportJSON:

    @pytest.fixture
    def data(self, exporter, tmp_path) -> list:
        out = tmp_path / "out.json"
        exporter.export(_make_test_vars(), out, "json")
        return json.loads(out.read_text(encoding="utf-8"))

    def test_liste_json(self, data):
        # Le JSON racine est une liste
        assert isinstance(data, list)

    def test_nombre_elements(self, data):
        # Un objet par variable
        assert len(data) == len(_make_test_vars())

    def test_champs_requis(self, data):
        # Champs attendus dans chaque objet
        required = {"name", "namespace", "storage", "access", "type",
                    "is_array", "array_size", "value", "fields"}
        for item in data:
            assert required <= set(item.keys())

    def test_valeur_scalaire(self, data):
        # Valeur scalaire correctement incluse
        int_obj = next(d for d in data if d["name"] == "$INT_VAR")
        assert int_obj["value"] == "42"

    def test_array_value_serialisee(self, data):
        # ArrayValue sérialisée (dict ou autre)
        arr_obj = next(d for d in data if d["name"] == "$ARR_VAR")
        assert arr_obj["value"] is not None

    def test_fields_inclus(self, data):
        # Fields d'un struct présents
        struct_obj = next(d for d in data if d["name"] == "$STRUCT")
        assert len(struct_obj["fields"]) == 2

    def test_json_valide_utf8(self, exporter, tmp_path):
        # JSON encodé en UTF-8 sans erreur
        out = tmp_path / "utf8.json"
        v = make_var(name="$ÉÀÜÑ", value="test_unicode")
        exporter.export([v], out, "json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["name"] == "$ÉÀÜÑ"

    def test_json_2d_array(self, data):
        # array_shape 2D sérialisée en liste
        nd_obj = next(d for d in data if d["name"] == "$ND_VAR")
        assert nd_obj["array_shape"] == [2, 2]


# ===========================================================================
# 7 — Tests de robustesse et cas limites
# ===========================================================================

class TestRobustesseExporter:

    def test_format_inconnu_leve_export_error(self, exporter, tmp_path):
        # Format non supporté → ExportError avec message clair
        out = tmp_path / "out.xyz"
        with pytest.raises(ExportError, match="Format non supporté"):
            exporter.export([], out, "xlsx")

    def test_format_casse_insensible(self, exporter, tmp_path):
        # "CSV" en majuscule accepté (fmt.lower() appliqué)
        out = tmp_path / "out.csv"
        exporter.export([], out, "CSV")
        assert out.exists()

    def test_liste_vide_csv(self, exporter, tmp_path):
        # Export CSV vide → fichier avec seulement le header
        out = tmp_path / "empty.csv"
        exporter.export([], out, "csv")
        assert out.exists()
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert rows == []

    def test_liste_vide_json(self, exporter, tmp_path):
        # Export JSON vide → liste vide JSON valide
        out = tmp_path / "empty.json"
        exporter.export([], out, "json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data == []

    def test_liste_vide_csv_flat(self, exporter, tmp_path):
        # Export csv_flat vide → fichier avec header seulement
        out = tmp_path / "empty_flat.csv"
        exporter.export([], out, "csv_flat")
        assert out.exists()

    def test_tous_formats_supportes(self, exporter, tmp_path):
        # Vérification que les 3 formats sont acceptés sans exception
        for fmt in ("csv", "csv_flat", "json"):
            out = tmp_path / f"out.{fmt}"
            exporter.export([], out, fmt)
            assert out.exists()

    def test_message_format_inconnu_liste_formats(self, exporter, tmp_path):
        # Le message d'erreur liste les formats disponibles
        with pytest.raises(ExportError, match="csv"):
            exporter.export([], tmp_path / "f.x", "bad_format")

    def test_variable_sans_source_file(self, exporter, tmp_path):
        # Variable sans source_file → source vide dans le CSV, pas d'exception
        v = make_var(name="$X", source_file=None, value="1")
        out = tmp_path / "no_source.csv"
        exporter.export([v], out, "csv")
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert rows[0]["source"] == ""

    def test_variable_position_value(self, exporter, tmp_path):
        # Variable avec PositionValue exportée sans exception
        v = make_var(
            name="$POS",
            data_type=VADataType.POSITION,
            type_detail="POSITION =",
            value=PositionValue(raw_lines=["Group: 1", "X: 0.0"]),
        )
        out = tmp_path / "pos.json"
        exporter.export([v], out, "json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data[0]["name"] == "$POS"

    def test_variable_uninitialized(self, exporter, tmp_path):
        # Variable Uninitialized exportée correctement
        v = make_var(name="$U", value="Uninitialized")
        out = tmp_path / "uninit.csv"
        exporter.export([v], out, "csv")
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert rows[0]["value"] == "Uninitialized"