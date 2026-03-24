"""
test_integration.py — Tests d'intégration du pipeline complet.

Couvre :
  - ExtractionOrchestrator : scan_workspace, load_backup, run, export
  - Sélection automatique du parser (DataIdCsvParser prioritaire sur VAParser)
  - Workspace multi-robots, format mixte VA + DATAID
  - Pipeline bout-en-bout : parsing → search → export
  - Cas dégénérés : dossier racine lui-même backup, dossier vide
  - Robustesse : parser qui lève une exception, backup non reconnu
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from models.fanuc_models import (
    ExtractionResult,
    RobotBackup,
    WorkspaceResult,
)
from services.exporter import VariableExporter
from services.orchestrator import ExtractionOrchestrator
from services.searcher import Searcher

from test_config import (
    DATAID_FULL,
    VA_ARRAYS,
    VA_ARRAY_OF_STRUCT,
    VA_KAREL,
    VA_SIMPLE_SCALARS,
    VA_STRUCT_SIMPLE,
    write_dataid,
    write_va,
)

import csv
import json


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def orch(settings: Settings) -> ExtractionOrchestrator:
    return ExtractionOrchestrator(settings)


@pytest.fixture
def va_workspace(tmp_path: Path) -> Path:
    """Workspace VA classique : Robot_A (3 VA), Robot_B (1 VA), EmptyDir."""
    robot_a = tmp_path / "Robot_A"
    robot_a.mkdir()
    write_va(robot_a, VA_SIMPLE_SCALARS, "sysvars.va")
    write_va(robot_a, VA_STRUCT_SIMPLE, "structs.va")

    robot_b = tmp_path / "Robot_B"
    robot_b.mkdir()
    write_va(robot_b, VA_ARRAYS, "sysvars.va")

    (tmp_path / "EmptyDir").mkdir()
    return tmp_path


@pytest.fixture
def dataid_workspace(tmp_path: Path) -> Path:
    """Workspace DATAID.CSV : un seul robot."""
    robot = tmp_path / "Robot_CSV"
    robot.mkdir()
    write_dataid(robot, DATAID_FULL)
    return tmp_path


@pytest.fixture
def mixed_workspace(tmp_path: Path) -> Path:
    """Workspace mixte : un robot VA + un robot DATAID."""
    va_robot = tmp_path / "Robot_VA"
    va_robot.mkdir()
    write_va(va_robot, VA_SIMPLE_SCALARS, "sys.va")

    csv_robot = tmp_path / "Robot_CSV"
    csv_robot.mkdir()
    write_dataid(csv_robot, DATAID_FULL)

    return tmp_path


# ===========================================================================
# 1 — Tests : scan_workspace
# ===========================================================================

class TestScanWorkspace:

    def test_compte_robots_va(self, orch, va_workspace):
        # Deux sous-dossiers avec .VA → 2 backups détectés
        ws = orch.scan_workspace(va_workspace)
        assert ws.robot_count == 2

    def test_dossier_vide_exclu(self, orch, va_workspace):
        # Dossier sans fichier reconnu → non inclus
        ws = orch.scan_workspace(va_workspace)
        names = {b.name for b in ws.backups}
        assert "EmptyDir" not in names

    def test_backups_pas_encore_charges(self, orch, va_workspace):
        # scan_workspace ne parse pas → loaded = False
        ws = orch.scan_workspace(va_workspace)
        assert all(not b.loaded for b in ws.backups)
        assert all(b.var_count == 0 for b in ws.backups)

    def test_retourne_workspace_result(self, orch, va_workspace):
        # Retourne un WorkspaceResult avec des RobotBackup
        ws = orch.scan_workspace(va_workspace)
        assert isinstance(ws, WorkspaceResult)
        assert all(isinstance(b, RobotBackup) for b in ws.backups)

    def test_format_va_detecte(self, orch, va_workspace):
        # FORMAT_ID = "va" pour les backups VA
        ws = orch.scan_workspace(va_workspace)
        for b in ws.backups:
            assert b.format == "va"

    def test_format_dataid_detecte(self, orch, dataid_workspace):
        # FORMAT_ID = "dataid_csv" pour les backups DATAID
        ws = orch.scan_workspace(dataid_workspace)
        assert ws.robot_count >= 1
        for b in ws.backups:
            assert b.format == "dataid_csv"

    def test_format_mixte(self, orch, mixed_workspace):
        # Workspace mixte → formats différents détectés
        ws = orch.scan_workspace(mixed_workspace)
        assert ws.robot_count == 2
        formats = {b.format for b in ws.backups}
        assert "va" in formats
        assert "dataid_csv" in formats

    def test_workspace_plat_est_detecte(self, orch, tmp_path):
        # Dossier racine lui-même contient un .VA → backup unique
        write_va(tmp_path, VA_SIMPLE_SCALARS, "sys.va")
        ws = orch.scan_workspace(tmp_path)
        assert ws.robot_count == 1
        assert ws.backups[0].path == tmp_path

    def test_sous_dossiers_tries_alphabetiquement(self, orch, tmp_path):
        # Sous-dossiers triés par ordre alphabétique
        for name in ["ZZ_Robot", "AA_Robot", "MM_Robot"]:
            d = tmp_path / name
            d.mkdir()
            write_va(d, VA_SIMPLE_SCALARS, "s.va")
        ws = orch.scan_workspace(tmp_path)
        names = [b.name for b in ws.backups]
        assert names == sorted(names)


# ===========================================================================
# 2 — Tests : load_backup
# ===========================================================================

class TestLoadBackup:

    def test_charge_les_variables(self, orch, va_workspace):
        # load_backup peuple les variables
        ws = orch.scan_workspace(va_workspace)
        backup = next(b for b in ws.backups if b.name == "Robot_A")
        orch.load_backup(backup)
        assert backup.loaded
        assert backup.var_count > 0

    def test_modifie_en_place_et_retourne(self, orch, va_workspace):
        # load_backup modifie le backup en place et le retourne
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        returned = orch.load_backup(backup)
        assert returned is backup

    def test_loaded_passe_a_true(self, orch, va_workspace):
        # loaded = True après chargement
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        assert not backup.loaded
        orch.load_backup(backup)
        assert backup.loaded

    def test_pas_derreur_sur_va_valide(self, orch, va_workspace):
        # Aucune erreur sur un backup VA bien formé
        ws = orch.scan_workspace(va_workspace)
        orch.load_backup(ws.backups[0])
        assert ws.backups[0].errors == []

    def test_loaded_count_incremente(self, orch, va_workspace):
        # loaded_count de WorkspaceResult incrémenté à chaque load
        ws = orch.scan_workspace(va_workspace)
        assert ws.loaded_count == 0
        orch.load_backup(ws.backups[0])
        assert ws.loaded_count == 1
        orch.load_backup(ws.backups[1])
        assert ws.loaded_count == 2

    def test_progress_callback_appele(self, orch, va_workspace):
        # progress_cb invoqué lors du chargement
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        calls = []
        orch.load_backup(backup, progress_cb=lambda c, t, m: calls.append(m))
        assert len(calls) >= 1

    def test_backup_sans_parser_compatible(self, orch, tmp_path):
        # Backup dans un dossier non reconnu → loaded=True, erreur loguée
        (tmp_path / "unknown").mkdir()
        fake_backup = RobotBackup(
            name="unknown",
            path=tmp_path / "unknown",
        )
        result = orch.load_backup(fake_backup)
        assert result.loaded is True
        assert len(result.errors) >= 1

    def test_load_dataid_backup(self, orch, dataid_workspace):
        # load_backup fonctionne aussi avec DATAID.CSV
        ws = orch.scan_workspace(dataid_workspace)
        backup = ws.backups[0]
        orch.load_backup(backup)
        assert backup.loaded
        assert backup.var_count >= 2


# ===========================================================================
# 3 — Tests : run (extraction directe)
# ===========================================================================

class TestOrchestratorRun:

    def test_run_retourne_extraction_result(self, orch, tmp_path):
        # run() retourne un ExtractionResult
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = orch.run(tmp_path)
        assert isinstance(result, ExtractionResult)

    def test_run_extrait_variables(self, orch, tmp_path):
        # run() trouve les variables du dossier
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = orch.run(tmp_path)
        assert result.var_count == 4

    def test_run_dossier_sans_fichier_reconnu(self, orch, tmp_path):
        # Dossier vide → var_count = 0, pas d'exception
        result = orch.run(tmp_path)
        assert result.var_count == 0

    def test_run_progress_callback(self, orch, tmp_path):
        # progress_cb appelé lors du run
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        calls = []
        orch.run(tmp_path, progress_cb=lambda c, t, m: calls.append(m))
        assert len(calls) >= 1

    def test_run_dataid(self, orch, tmp_path):
        # run() sur dossier DATAID.CSV → variables extraites
        write_dataid(tmp_path, DATAID_FULL)
        result = orch.run(tmp_path)
        assert result.var_count >= 2

    def test_run_skip_conversion_true_par_defaut(self, orch, tmp_path):
        # Par défaut skip_conversion=True
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = orch.run(tmp_path, skip_conversion=True)
        assert result.var_count > 0


# ===========================================================================
# 4 — Tests : export via orchestrateur
# ===========================================================================

class TestOrchestratorExport:

    def test_export_csv(self, orch, tmp_path):
        # Orchestrateur.export() produit un CSV valide
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = orch.run(tmp_path)
        out = tmp_path / "export.csv"
        orch.export(result, out, fmt="csv")
        assert out.exists()
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == result.var_count

    def test_export_json(self, orch, tmp_path):
        # Orchestrateur.export() produit un JSON valide
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = orch.run(tmp_path)
        out = tmp_path / "export.json"
        orch.export(result, out, fmt="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == result.var_count

    def test_export_csv_flat(self, orch, tmp_path):
        # csv_flat fonctionne sans exception
        write_va(tmp_path, VA_ARRAYS, "s.va")
        result = orch.run(tmp_path)
        out = tmp_path / "flat.csv"
        orch.export(result, out, fmt="csv_flat")
        assert out.exists()

    def test_export_resultat_vide(self, orch, tmp_path):
        # Export d'un résultat sans variables → fichier vide (pas d'exception)
        result = ExtractionResult(input_dir=tmp_path)
        out = tmp_path / "empty.json"
        orch.export(result, out, fmt="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data == []


# ===========================================================================
# 5 — Tests : sélection de parser (priorité DataIdCsv > VA)
# ===========================================================================

class TestSelectionParser:

    def test_dataid_prioritaire_sur_va(self, orch, tmp_path):
        # Dossier avec DATAID.CSV ET .VA → DataIdCsvParser sélectionné
        write_dataid(tmp_path, DATAID_FULL)
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        ws = orch.scan_workspace(tmp_path)
        # Le dossier racine est reconnu comme dataid_csv
        assert ws.backups[0].format == "dataid_csv"

    def test_va_parser_si_pas_de_dataid(self, orch, tmp_path):
        # Dossier avec seulement .VA → VAParser
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        ws = orch.scan_workspace(tmp_path)
        assert ws.backups[0].format == "va"

    def test_unknown_si_aucun_parser(self, orch, tmp_path):
        # Dossier sans aucun format reconnu → format "unknown"
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "readme.txt").write_text("nothing", encoding="utf-8")
        ws = orch.scan_workspace(tmp_path)
        assert ws.robot_count == 0


# ===========================================================================
# 6 — Tests d'intégration : pipeline complet parsing → search → export
# ===========================================================================

class TestPipelineComplet:

    def test_parse_search_export(self, orch, tmp_path):
        """Pipeline bout-en-bout : extraction → recherche → export CSV."""
        # Étape 1 : extraction
        write_va(tmp_path, VA_SIMPLE_SCALARS + VA_STRUCT_SIMPLE, "sys.va")
        result = orch.run(tmp_path)
        assert result.var_count > 0

        # Étape 2 : chargement dans un backup pour la recherche
        backup = RobotBackup(
            name="TestRobot",
            path=tmp_path,
            variables=result.variables,
            loaded=True,
        )
        searcher = Searcher()
        search_result = searcher.search_from_text("INTEGER", "all", [backup])
        assert search_result.hit_count >= 1

        # Étape 3 : export
        out = tmp_path / "final.csv"
        orch.export(result, out, fmt="csv")
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == result.var_count

    def test_multi_robot_aggregation(self, orch, va_workspace):
        """Deux robots chargés → recherche dans les deux simultanément."""
        ws = orch.scan_workspace(va_workspace)
        for b in ws.backups:
            orch.load_backup(b)

        searcher = Searcher()
        results = searcher.search_from_text("INTEGER", "all", ws.backups)
        # Les deux robots ont des variables INTEGER
        backup_names = {h.backup_name for h in results.hits}
        assert len(backup_names) >= 2

    def test_dataid_puis_recherche(self, orch, dataid_workspace):
        """Parsing DATAID.CSV puis recherche par chemin."""
        ws = orch.scan_workspace(dataid_workspace)
        backup = ws.backups[0]
        orch.load_backup(backup)

        searcher = Searcher()
        results = searcher.search_from_text("ALARM", "all", [backup])
        assert results.hit_count >= 1

    def test_export_apres_load_backup(self, orch, va_workspace, tmp_path):
        """load_backup puis export du résultat."""
        ws = orch.scan_workspace(va_workspace)
        backup = next(b for b in ws.backups if b.name == "Robot_A")
        orch.load_backup(backup)

        extraction = ExtractionResult(
            input_dir=backup.path,
            variables=backup.variables,
        )
        out = tmp_path / "robot_a.json"
        orch.export(extraction, out, fmt="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == backup.var_count

    def test_parse_multiple_va_files(self, orch, tmp_path):
        """Parsing d'un dossier avec plusieurs fichiers .VA → agrégation."""
        write_va(tmp_path, VA_SIMPLE_SCALARS, "scalars.va")
        write_va(tmp_path, VA_STRUCT_SIMPLE, "structs.va")
        write_va(tmp_path, VA_KAREL, "karel.va")
        result = orch.run(tmp_path)
        # 4 scalaires + 1 struct + 1 Karel = 6
        assert result.var_count == 6

    def test_recherche_chemin_apres_parsing(self, orch, tmp_path):
        """Résolution de chemin exact après parsing réel."""
        write_va(tmp_path, VA_ARRAY_OF_STRUCT, "aio.va")
        result = orch.run(tmp_path)
        backup = RobotBackup(
            name="R",
            path=tmp_path,
            variables=result.variables,
            loaded=True,
        )
        searcher = Searcher()
        # Résolution du champ $AIO_CNV[1].$RACK
        results = searcher.search_from_text("$AIO_CNV[1].$RACK", "all", [backup])
        assert results.hit_count >= 1
        assert results.hits[0].match_value == "999"