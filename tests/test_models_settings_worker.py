"""
test_models_settings_worker.py — Tests des modèles de données, de la configuration
et du BackgroundWorker.

Couvre :
  - Modèles : RobotVariable, RobotVarField, ArrayValue, PositionValue,
              ExtractionResult, RobotBackup, WorkspaceResult, ConversionResult
  - Settings : load, save, valeurs par défaut, JSON corrompu, clés inconnues
  - BackgroundWorker : run, poll, callbacks done/error/progress, double run,
                       queue draining, thread safety
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import pytest

from config.settings import Settings
from models.fanuc_models import (
    AccessType,
    ArrayValue,
    ConversionResult,
    ConversionStatus,
    ExtractionResult,
    PositionValue,
    RobotBackup,
    RobotVarField,
    RobotVariable,
    StorageType,
    VADataType,
    WorkspaceResult,
    _field_to_dict,
    _serialize_value,
)
from utils.worker import BackgroundWorker, _drain

from test_config import make_backup, make_field, make_var


# ===========================================================================
# 1 — Tests : RobotVariable — propriétés calculées
# ===========================================================================

class TestRobotVariableProperties:

    def test_type_str_avec_valeur_inline(self):
        # type_str extrait le type sans la partie "= valeur"
        v = make_var(type_detail="INTEGER = 100")
        assert v.type_str == "INTEGER"

    def test_type_str_sans_egal(self):
        # Pas de '=' → type_str = type_detail complet
        v = make_var(type_detail="ARRAY[3] OF REAL")
        assert v.type_str == "ARRAY[3] OF REAL"

    def test_type_str_struct(self):
        # Struct → type_str sans le '='
        v = make_var(type_detail="ALMDG_T =")
        assert v.type_str == "ALMDG_T"

    def test_is_system_true(self):
        # namespace *SYSTEM* → is_system True
        v = make_var(namespace="*SYSTEM*")
        assert v.is_system is True

    def test_is_system_false_karel(self):
        # Namespace Karel → is_system False
        v = make_var(namespace="TBSWMD45")
        assert v.is_system is False

    def test_is_system_false_posreg(self):
        # *POSREG* → pas *SYSTEM* → False
        v = make_var(namespace="*POSREG*")
        assert v.is_system is False

    def test_is_struct_true_avec_fields(self):
        # Variable avec fields → is_struct True
        v = make_var(fields=[make_field()])
        assert v.is_struct is True

    def test_is_struct_false_sans_fields(self):
        # Variable sans field → is_struct False
        v = make_var()
        assert v.is_struct is False

    def test_to_dict_contient_cles_requises(self):
        # to_dict retourne toutes les clés attendues
        v = make_var(name="$X", value="1")
        d = v.to_dict()
        required = {"name", "namespace", "storage", "access", "type",
                    "is_array", "array_size", "array_shape", "value", "fields", "source", "line"}
        assert required <= set(d.keys())

    def test_to_dict_array_shape_liste(self):
        # array_shape tuple → liste dans le dict
        v = make_var(is_array=True, array_size=6, array_shape=(2, 3))
        d = v.to_dict()
        assert d["array_shape"] == [2, 3]

    def test_to_dict_array_shape_none(self):
        # Pas de shape → None
        v = make_var()
        d = v.to_dict()
        assert d["array_shape"] is None

    def test_to_dict_source_str_si_present(self):
        # source_file présent → str dans le dict
        v = make_var(source_file=Path("/path/file.va"))
        assert isinstance(v.to_dict()["source"], str)

    def test_to_dict_source_none_si_absent(self):
        # Pas de source_file → None
        v = make_var(source_file=None)
        assert v.to_dict()["source"] is None

    def test_to_dict_storage_valeur_enum(self):
        # storage → valeur de l'enum ("CMOS", etc.)
        v = make_var(storage=StorageType.SHADOW)
        assert v.to_dict()["storage"] == "SHADOW"

    def test_to_dict_access_valeur_enum(self):
        # access → valeur de l'enum
        v = make_var(access=AccessType.RO)
        assert v.to_dict()["access"] == "RO"


# ===========================================================================
# 2 — Tests : RobotVarField — propriétés calculées
# ===========================================================================

class TestRobotVarFieldProperties:

    def test_parent_index_none_quand_pas_de_nd(self):
        # parent_index_nd absent → parent_index None (rétrocompat)
        f = make_field(parent_index_nd=None)
        assert f.parent_index is None

    def test_parent_index_premier_element(self):
        # parent_index_nd (3, 5) → parent_index = 3
        f = make_field(parent_index_nd=(3, 5))
        assert f.parent_index == 3

    def test_parent_index_1d(self):
        # parent_index_nd (7,) → parent_index = 7
        f = make_field(parent_index_nd=(7,))
        assert f.parent_index == 7

    def test_field_to_dict_cles(self):
        # _field_to_dict retourne les clés minimales
        f = make_field(value="42")
        d = _field_to_dict(f)
        assert "full_name" in d
        assert "field_name" in d
        assert "value" in d

    def test_field_to_dict_condition_handler_inclus(self):
        # condition_handler présent dans le dict si non vide
        f = make_field(condition_handler="H1")
        d = _field_to_dict(f)
        assert d.get("condition_handler") == "H1"

    def test_field_to_dict_condition_handler_absent_si_vide(self):
        # condition_handler absent si vide
        f = make_field(condition_handler="")
        d = _field_to_dict(f)
        assert "condition_handler" not in d


# ===========================================================================
# 3 — Tests : ArrayValue
# ===========================================================================

class TestArrayValue:

    def test_repr_items(self):
        # repr mentionne le nombre d'items
        arr = ArrayValue(items={(1,): "a", (2,): "b"})
        assert "2 items" in repr(arr)

    def test_repr_positions(self):
        # repr mentionne "positions" si des PositionValue présentes
        arr = ArrayValue(items={(1,): PositionValue()})
        assert "positions" in repr(arr)

    def test_repr_vide(self):
        # ArrayValue vide → "0 items"
        arr = ArrayValue()
        assert "0 items" in repr(arr)

    def test_items_defaut_vide(self):
        # Constructeur par défaut → items vide
        arr = ArrayValue()
        assert arr.items == {}

    def test_cle_tuple_1d(self):
        # Clé 1D stockée en tuple
        arr = ArrayValue(items={(1,): "x"})
        assert (1,) in arr.items

    def test_cle_tuple_2d(self):
        # Clé 2D stockée en tuple
        arr = ArrayValue(items={(1, 2): "x"})
        assert (1, 2) in arr.items


# ===========================================================================
# 4 — Tests : PositionValue
# ===========================================================================

class TestPositionValue:

    def test_display_label_avec_label(self):
        # display_label retourne le label
        pv = PositionValue(label="MyPos")
        assert pv.display_label == "MyPos"

    def test_display_label_vide(self):
        # Label vide → display_label vide
        pv = PositionValue(label="")
        assert pv.display_label == ""

    def test_repr_joint_lignes(self):
        # repr joint les lignes avec " | "
        pv = PositionValue(raw_lines=["X: 1.0", "Y: 2.0"])
        r = repr(pv)
        assert "X: 1.0" in r
        assert "Y: 2.0" in r
        assert " | " in r

    def test_repr_vide(self):
        # PositionValue sans lignes → repr vide
        pv = PositionValue(raw_lines=[])
        assert repr(pv) == ""

    def test_raw_lines_par_defaut_vide(self):
        # Constructeur par défaut → raw_lines vide
        pv = PositionValue()
        assert pv.raw_lines == []


# ===========================================================================
# 5 — Tests : ExtractionResult
# ===========================================================================

class TestExtractionResult:

    def test_var_count(self):
        # var_count = len(variables)
        r = ExtractionResult(
            input_dir=Path("/x"),
            variables=[make_var(), make_var()],
        )
        assert r.var_count == 2

    def test_var_count_vide(self):
        # var_count = 0 si pas de variables
        r = ExtractionResult(input_dir=Path("/x"))
        assert r.var_count == 0

    def test_field_count(self):
        # field_count = somme des fields de toutes les variables
        f1 = make_field()
        f2 = make_field()
        v = make_var(fields=[f1, f2])
        r = ExtractionResult(input_dir=Path("/x"), variables=[v])
        assert r.field_count == 2

    def test_field_count_sans_fields(self):
        # Pas de fields → field_count = 0
        r = ExtractionResult(input_dir=Path("/x"), variables=[make_var()])
        assert r.field_count == 0

    def test_errors_vide_par_defaut(self):
        # errors est une liste vide par défaut
        r = ExtractionResult(input_dir=Path("/x"))
        assert r.errors == []


# ===========================================================================
# 6 — Tests : RobotBackup
# ===========================================================================

class TestRobotBackup:

    def test_var_count(self):
        # var_count = nombre de variables
        b = make_backup(variables=[make_var(), make_var()])
        assert b.var_count == 2

    def test_field_count(self):
        # field_count = somme des fields
        v = make_var(fields=[make_field()])
        b = make_backup(variables=[v])
        assert b.field_count == 1

    def test_loaded_false_par_defaut_construction(self):
        # RobotBackup non chargé → var_count = 0
        b = RobotBackup(name="R", path=Path("/x"))
        assert not b.loaded
        assert b.var_count == 0

    def test_errors_vide_par_defaut(self):
        # errors est une liste vide par défaut
        b = RobotBackup(name="R", path=Path("/x"))
        assert b.errors == []


# ===========================================================================
# 7 — Tests : WorkspaceResult
# ===========================================================================

class TestWorkspaceResult:

    def test_robot_count(self):
        # robot_count = nombre de backups
        ws = WorkspaceResult(
            root_path=Path("/x"),
            backups=[make_backup(), make_backup()],
        )
        assert ws.robot_count == 2

    def test_robot_count_vide(self):
        # Aucun backup → robot_count = 0
        ws = WorkspaceResult(root_path=Path("/x"))
        assert ws.robot_count == 0

    def test_loaded_count(self):
        # loaded_count = nombre de backups chargés
        b_loaded = make_backup(loaded=True)
        b_unloaded = make_backup(loaded=False)
        ws = WorkspaceResult(root_path=Path("/x"), backups=[b_loaded, b_unloaded])
        assert ws.loaded_count == 1

    def test_loaded_count_tous_charges(self):
        # Tous chargés → loaded_count = robot_count
        ws = WorkspaceResult(
            root_path=Path("/x"),
            backups=[make_backup(loaded=True), make_backup(loaded=True)],
        )
        assert ws.loaded_count == ws.robot_count


# ===========================================================================
# 8 — Tests : ConversionResult
# ===========================================================================

class TestConversionResult:

    def test_statut_pending_par_defaut(self):
        # Statut initial PENDING
        r = ConversionResult(source_path=Path("/x/file.tp"))
        assert r.status == ConversionStatus.PENDING

    def test_output_path_none_par_defaut(self):
        # output_path absent par défaut
        r = ConversionResult(source_path=Path("/x/file.tp"))
        assert r.output_path is None

    def test_error_message_none_par_defaut(self):
        # Pas de message d'erreur par défaut
        r = ConversionResult(source_path=Path("/x/file.tp"))
        assert r.error_message is None


# ===========================================================================
# 9 — Tests : Settings
# ===========================================================================

class TestSettings:

    def test_var_name_filter_vide(self):
        # var_name_filter vide par défaut
        s = Settings()
        assert s.var_name_filter == []

    def test_save_et_load(self, tmp_path, monkeypatch):
        # Cycle save/load round-trip
        config_path = tmp_path / ".fanuc" / "config.json"
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)

        s = Settings(last_input_dir="/some/path", kconvars_timeout=60)
        s.save()
        assert config_path.exists()

        loaded = Settings.load()
        assert loaded.last_input_dir == "/some/path"
        assert loaded.kconvars_timeout == 60

    def test_save_cree_dossier_parent(self, tmp_path, monkeypatch):
        # save() crée le dossier parent s'il n'existe pas
        config_path = tmp_path / "deep" / "nested" / "config.json"
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        Settings().save()
        assert config_path.exists()

    def test_load_retourne_defauts_sans_fichier(self, tmp_path, monkeypatch):
        # Pas de fichier → valeurs par défaut
        config_path = tmp_path / "nonexistent" / "config.json"
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        s = Settings.load()
        assert s.last_input_dir == ""

    def test_load_ignore_cles_inconnues(self, tmp_path, monkeypatch):
        # Clés inconnues dans le JSON ignorées silencieusement
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"last_input_dir": "/x", "UNKNOWN_KEY": "bad"}),
            encoding="utf-8",
        )
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        s = Settings.load()
        assert s.last_input_dir == "/x"
        assert not hasattr(s, "UNKNOWN_KEY")

    def test_load_json_corrompu_retourne_defauts(self, tmp_path, monkeypatch):
        # JSON invalide → pas d'exception, valeurs par défaut
        config_path = tmp_path / "config.json"
        config_path.write_text("NOT VALID JSON {{{", encoding="utf-8")
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        s = Settings.load()
        assert s.last_input_dir == ""

    def test_save_json_contient_timeout(self, tmp_path, monkeypatch):
        # Le JSON sauvegardé contient kconvars_timeout
        config_path = tmp_path / "config.json"
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        Settings(kconvars_timeout=90).save()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["kconvars_timeout"] == 90

    def test_load_partiel_complete_defauts(self, tmp_path, monkeypatch):
        # JSON avec seulement certains champs → autres prennent les valeurs par défaut
        config_path = tmp_path / "config.json"
        config_path.write_text(
            json.dumps({"kconvars_timeout": 45}), encoding="utf-8"
        )
        monkeypatch.setattr("config.settings.CONFIG_FILE", config_path)
        s = Settings.load()
        assert s.kconvars_timeout == 45
        assert s.last_input_dir == ""  # valeur par défaut


# ===========================================================================
# 10 — Tests : BackgroundWorker
# ===========================================================================

class TestBackgroundWorker:

    def test_is_running_false_initialement(self):
        # Avant tout lancement, is_running = False
        w = BackgroundWorker()
        assert w.is_running is False

    def test_callback_done_appele(self):
        # on_done appelé avec le résultat de la fonction
        w = BackgroundWorker()
        results = []
        w.run(lambda: 42, on_done=results.append)
        time.sleep(0.15)
        finished = w.poll_result()
        assert finished is True
        assert results == [42]

    def test_callback_done_avec_args(self):
        # Fonction avec args positionnels
        w = BackgroundWorker()
        results = []
        w.run(lambda a, b: a + b, args=(3, 4), on_done=results.append)
        time.sleep(0.1)
        w.poll_result()
        assert results == [7]

    def test_callback_done_avec_kwargs(self):
        # Fonction avec kwargs
        w = BackgroundWorker()
        results = []
        w.run(lambda x=0: x * 2, kwargs={"x": 5}, on_done=results.append)
        time.sleep(0.1)
        w.poll_result()
        assert results == [10]

    def test_callback_error_appele(self):
        # on_error appelé si la fonction lève une exception
        w = BackgroundWorker()
        errors = []

        def boom():
            raise ValueError("test error")

        w.run(boom, on_error=errors.append)
        time.sleep(0.1)
        finished = w.poll_result()
        assert finished is True
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    def test_error_type_conserve(self):
        # Le type d'exception est conservé
        w = BackgroundWorker()
        errors = []
        w.run(lambda: (_ for _ in ()).throw(RuntimeError("rt")),
              on_error=errors.append)
        time.sleep(0.1)
        w.poll_result()
        assert isinstance(errors[0], RuntimeError)

    def test_double_run_leve_runtime_error(self):
        # Lancer un second worker pendant qu'un est actif → RuntimeError
        w = BackgroundWorker()
        ev = threading.Event()
        w.run(lambda: ev.wait(5))
        time.sleep(0.05)
        with pytest.raises(RuntimeError, match="déjà en cours"):
            w.run(lambda: None)
        ev.set()

    def test_poll_retourne_false_pendant_execution(self):
        # poll_result() = False tant que le thread tourne
        w = BackgroundWorker()
        ev = threading.Event()
        w.run(lambda: ev.wait(5))
        time.sleep(0.05)
        assert w.poll_result() is False
        ev.set()

    def test_poll_queue_vide_retourne_false(self):
        # Aucun thread lancé → poll_result() = False (queue vide)
        w = BackgroundWorker()
        assert w.poll_result() is False

    def test_is_running_true_pendant_execution(self):
        # is_running = True quand le thread tourne
        w = BackgroundWorker()
        ev = threading.Event()
        w.run(lambda: ev.wait(5))
        time.sleep(0.05)
        assert w.is_running is True
        ev.set()

    def test_is_running_false_apres_completion(self):
        # is_running revient à False après la fin
        w = BackgroundWorker()
        done = []
        w.run(lambda: 1, on_done=done.append)
        time.sleep(0.2)
        w.poll_result()
        assert w.is_running is False

    def test_progress_proxy_injecte(self):
        # Si on_progress + "progress_cb" dans kwargs → proxy injecté
        w = BackgroundWorker()
        progress_calls = []

        def work(progress_cb=None):
            if progress_cb:
                progress_cb(1, 3, "étape 1")
                progress_cb(2, 3, "étape 2")
            return "done"

        w.run(
            work,
            kwargs={"progress_cb": None},
            on_done=lambda r: None,
            on_progress=lambda c, t, m: progress_calls.append((c, t, m)),
        )
        time.sleep(0.25)
        while not w.poll_result():
            time.sleep(0.05)

        assert len(progress_calls) >= 2
        assert progress_calls[0] == (1, 3, "étape 1")

    def test_progress_sans_proxy_si_pas_de_cle(self):
        # Pas de clé "progress_cb" dans kwargs → pas d'injection
        w = BackgroundWorker()
        done = []
        w.run(lambda: "ok", kwargs={}, on_done=done.append)
        time.sleep(0.1)
        w.poll_result()
        assert done == ["ok"]

    def test_done_sans_callback_pas_exception(self):
        # on_done=None → pas d'exception même si résultat retourné
        w = BackgroundWorker()
        w.run(lambda: 42)  # pas de on_done
        time.sleep(0.1)
        finished = w.poll_result()
        assert finished is True

    def test_error_sans_callback_pas_exception(self):
        # on_error=None → exception swallée sans crash
        w = BackgroundWorker()
        w.run(lambda: 1 / 0)  # pas de on_error
        time.sleep(0.1)
        finished = w.poll_result()
        assert finished is True

    def test_relance_apres_fin(self):
        # Après fin d'un run, on peut relancer
        w = BackgroundWorker()
        results = []
        w.run(lambda: "first", on_done=results.append)
        time.sleep(0.15)
        w.poll_result()

        w.run(lambda: "second", on_done=results.append)
        time.sleep(0.15)
        w.poll_result()

        assert results == ["first", "second"]

    def test_queue_drainee_au_relancement(self):
        # La queue est vidée avant un nouveau run (pas de résidus)
        w = BackgroundWorker()
        # Forcer un message dans la queue manuellement
        w._queue.put(("done", "stale"))
        results = []
        w.run(lambda: "fresh", on_done=results.append)
        time.sleep(0.15)
        w.poll_result()
        # Seul "fresh" doit être dans results
        assert results == ["fresh"]


# ===========================================================================
# 11 — Tests : _drain (helper module-level)
# ===========================================================================

class TestDrainHelper:

    def test_drain_queue_vide(self):
        # Vider une queue déjà vide → pas d'exception
        q: queue.Queue = queue.Queue()
        _drain(q)
        assert q.empty()

    def test_drain_vide_la_queue(self):
        # _drain vide complètement la queue
        q: queue.Queue = queue.Queue()
        for i in range(10):
            q.put(i)
        _drain(q)
        assert q.empty()

    def test_drain_queue_un_element(self):
        # Drain sur queue avec un seul élément
        q: queue.Queue = queue.Queue()
        q.put("item")
        _drain(q)
        assert q.empty()