"""
test_integration.py — Tests d'intégration : orchestrateur, pipeline complet.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from config.settings import Settings
from models.fanuc_models import (
    ExtractionResult,
    RobotBackup,
    WorkspaceResult,
)
from services.orchestrator import ExtractionOrchestrator
from services.searcher import Searcher

from conftest import (
    DATAID_FULL,
    VA_ARRAYS,
    VA_ARRAY_OF_STRUCT,
    VA_KAREL,
    VA_SIMPLE_SCALARS,
    VA_STRUCT_SIMPLE,
    write_dataid,
    write_va,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def orch(settings: Settings) -> ExtractionOrchestrator:
    return ExtractionOrchestrator(settings)


@pytest.fixture
def va_workspace(tmp_path: Path) -> Path:
    """Workspace VA classique : Robot_A (2 VA), Robot_B (1 VA), EmptyDir.
    Robot_B contient VA_SIMPLE_SCALARS (avec INTEGER) pour que la recherche
    'INTEGER' matche dans les deux robots.
    """
    robot_a = tmp_path / "Robot_A"
    robot_a.mkdir()
    write_va(robot_a, VA_SIMPLE_SCALARS, "sysvars.va")
    write_va(robot_a, VA_STRUCT_SIMPLE, "structs.va")

    robot_b = tmp_path / "Robot_B"
    robot_b.mkdir()
    # FIX : VA_SIMPLE_SCALARS contient des INTEGER → matche dans les deux robots
    write_va(robot_b, VA_SIMPLE_SCALARS, "sysvars.va")

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
# Helper : équivalent de orch.run() via scan_workspace + load_backup
# ===========================================================================

def _run(orch: ExtractionOrchestrator, path: Path, progress_cb=None, skip_conversion: bool = True) -> ExtractionResult:
    """Simule un orch.run() en faisant scan_workspace + load_backup.

    L'orchestrateur n'expose pas de méthode run() directe — on passe par
    scan_workspace() pour détecter le backup, puis load_backup() pour le parser.
    Le résultat est encapsulé dans un ExtractionResult pour la compatibilité
    avec orch.export().
    """
    ws = orch.scan_workspace(path)
    if not ws.backups:
        return ExtractionResult(input_dir=path)

    all_vars = []
    all_errors = []
    for backup in ws.backups:
        orch.load_backup(backup, progress_cb=progress_cb)
        all_vars.extend(backup.variables)
        all_errors.extend(backup.errors)

    return ExtractionResult(
        input_dir=path,
        variables=all_vars,
        errors=all_errors,
    )


# ===========================================================================
# 1 — Tests : scan_workspace
# ===========================================================================

class TestScanWorkspace:

    def test_compte_robots_va(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        assert ws.robot_count == 2

    def test_dossier_vide_exclu(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        names = {b.name for b in ws.backups}
        assert "EmptyDir" not in names

    def test_backups_pas_encore_charges(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        assert all(not b.loaded for b in ws.backups)
        assert all(b.var_count == 0 for b in ws.backups)

    def test_retourne_workspace_result(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        assert isinstance(ws, WorkspaceResult)
        assert all(isinstance(b, RobotBackup) for b in ws.backups)

    def test_format_va_detecte(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        for b in ws.backups:
            assert b.format == "va"

    def test_format_dataid_detecte(self, orch, dataid_workspace):
        ws = orch.scan_workspace(dataid_workspace)
        assert ws.robot_count >= 1
        for b in ws.backups:
            assert b.format == "dataid_csv"

    def test_format_mixte(self, orch, mixed_workspace):
        ws = orch.scan_workspace(mixed_workspace)
        assert ws.robot_count == 2
        formats = {b.format for b in ws.backups}
        assert "va" in formats
        assert "dataid_csv" in formats

    def test_workspace_plat_est_detecte(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "sys.va")
        ws = orch.scan_workspace(tmp_path)
        assert ws.robot_count == 1
        assert ws.backups[0].path == tmp_path

    def test_sous_dossiers_tries_alphabetiquement(self, orch, tmp_path):
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
        ws = orch.scan_workspace(va_workspace)
        backup = next(b for b in ws.backups if b.name == "Robot_A")
        orch.load_backup(backup)
        assert backup.loaded
        assert backup.var_count > 0

    def test_modifie_en_place_et_retourne(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        returned = orch.load_backup(backup)
        assert returned is backup

    def test_loaded_passe_a_true(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        assert not backup.loaded
        orch.load_backup(backup)
        assert backup.loaded

    def test_pas_derreur_sur_va_valide(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        orch.load_backup(ws.backups[0])
        assert ws.backups[0].errors == []

    def test_loaded_count_incremente(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        assert ws.loaded_count == 0
        orch.load_backup(ws.backups[0])
        assert ws.loaded_count == 1
        orch.load_backup(ws.backups[1])
        assert ws.loaded_count == 2

    def test_progress_callback_appele(self, orch, va_workspace):
        ws = orch.scan_workspace(va_workspace)
        backup = ws.backups[0]
        calls = []
        orch.load_backup(backup, progress_cb=lambda c, t, m: calls.append(m))
        assert len(calls) >= 1

    def test_backup_sans_parser_compatible(self, orch, tmp_path):
        (tmp_path / "unknown").mkdir()
        fake_backup = RobotBackup(
            name="unknown",
            path=tmp_path / "unknown",
        )
        result = orch.load_backup(fake_backup)
        assert result.loaded is True
        assert len(result.errors) >= 1

    def test_load_dataid_backup(self, orch, dataid_workspace):
        ws = orch.scan_workspace(dataid_workspace)
        backup = ws.backups[0]
        orch.load_backup(backup)
        assert backup.loaded
        assert backup.var_count >= 2


# ===========================================================================
# 3 — Tests : run (extraction directe via helper _run)
# ===========================================================================

class TestOrchestratorRun:

    def test_run_retourne_extraction_result(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = _run(orch, tmp_path)
        assert isinstance(result, ExtractionResult)

    def test_run_extrait_variables(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = _run(orch, tmp_path)
        assert result.var_count == 4

    def test_run_dossier_sans_fichier_reconnu(self, orch, tmp_path):
        result = _run(orch, tmp_path)
        assert result.var_count == 0

    def test_run_progress_callback(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        calls = []
        _run(orch, tmp_path, progress_cb=lambda c, t, m: calls.append(m))
        assert len(calls) >= 1

    def test_run_dataid(self, orch, tmp_path):
        write_dataid(tmp_path, DATAID_FULL)
        result = _run(orch, tmp_path)
        assert result.var_count >= 2

    def test_run_skip_conversion_true_par_defaut(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = _run(orch, tmp_path, skip_conversion=True)
        assert result.var_count > 0


# ===========================================================================
# 4 — Tests : export via orchestrateur
# ===========================================================================

class TestOrchestratorExport:

    def test_export_csv(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = _run(orch, tmp_path)
        out = tmp_path / "export.csv"
        orch.export(result, out, fmt="csv")
        assert out.exists()
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        assert len(rows) == result.var_count

    def test_export_json(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        result = _run(orch, tmp_path)
        out = tmp_path / "export.json"
        orch.export(result, out, fmt="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data) == result.var_count

    def test_export_csv_flat(self, orch, tmp_path):
        write_va(tmp_path, VA_ARRAYS, "s.va")
        result = _run(orch, tmp_path)
        out = tmp_path / "flat.csv"
        orch.export(result, out, fmt="csv_flat")
        assert out.exists()

    def test_export_resultat_vide(self, orch, tmp_path):
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
        write_dataid(tmp_path, DATAID_FULL)
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        ws = orch.scan_workspace(tmp_path)
        assert ws.backups[0].format == "dataid_csv"

    def test_va_parser_si_pas_de_dataid(self, orch, tmp_path):
        write_va(tmp_path, VA_SIMPLE_SCALARS, "s.va")
        ws = orch.scan_workspace(tmp_path)
        assert ws.backups[0].format == "va"

    def test_unknown_si_aucun_parser(self, orch, tmp_path):
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
        write_va(tmp_path, VA_SIMPLE_SCALARS + VA_STRUCT_SIMPLE, "sys.va")
        result = _run(orch, tmp_path)
        assert result.var_count > 0

        backup = RobotBackup(
            name="TestRobot",
            path=tmp_path,
            variables=result.variables,
            loaded=True,
        )
        searcher = Searcher()
        search_result = searcher.search_from_text("INTEGER", "all", [backup])
        assert search_result.hit_count >= 1

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
        result = _run(orch, tmp_path)
        # 4 scalaires + 1 struct + 1 Karel = 6
        assert result.var_count == 6

    def test_recherche_chemin_apres_parsing(self, orch, tmp_path):
        """Résolution de chemin exact après parsing réel."""
        write_va(tmp_path, VA_ARRAY_OF_STRUCT, "aio.va")
        result = _run(orch, tmp_path)
        backup = RobotBackup(
            name="R",
            path=tmp_path,
            variables=result.variables,
            loaded=True,
        )
        searcher = Searcher()
        results = searcher.search_from_text("$AIO_CNV[1].$RACK", "all", [backup])
        assert results.hit_count >= 1
        assert results.hits[0].match_value == "999"