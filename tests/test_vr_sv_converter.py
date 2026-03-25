"""
tests/test_converter.py
────────────────────────
Tests unitaires pour services/converter.py.

Stratégie : on ne teste PAS l'appel réel à kconvars.exe (dépendance externe,
absente en CI). On teste toute la logique Python autour :
  - détection des fichiers convertibles
  - extraction de version depuis SUMMARY.DG
  - écriture de robot.ini
  - recherche de kconvars.exe
  - logique best-effort (fichier ignoré si échec, erreur si aucun produit)
  - construction de la commande subprocess

L'appel subprocess est mocké via pytest-mock (``mocker.patch``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from config.settings import Settings
from services.converter.vr_sv_converter import (
    VAConverter,
    ConverterError,
    ExeNotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backup(tmp_path: Path, files: dict[str, str | bytes]) -> Path:
    """Crée un dossier backup avec les fichiers spécifiés."""
    backup = tmp_path / "backup"
    backup.mkdir()
    for name, content in files.items():
        p = backup / name
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return backup


def _make_summary(version_str: str = "V9.40P/55") -> str:
    """Génère un SUMMARY.DG minimal avec la version à la ligne 22 (index 21)."""
    lines = ["ligne_vide"] * 21
    lines.append(f"Software Edition No.: {version_str}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# _find_source_files
# ---------------------------------------------------------------------------

class TestFindSourceFiles:

    def test_retourne_fichier_sv(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SYSVAR.SV": "data", "SUMMARY.DG": "x"})
        files = VAConverter._get_source_files(backup)
        assert len(files) == 1
        assert files[0].suffix.lower() == ".sv"

    def test_retourne_fichier_vr(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"NUMREG.VR": "data"})
        files = VAConverter._get_source_files(backup)
        assert len(files) == 1
        assert files[0].suffix.lower() == ".vr"

    def test_ignore_dg_et_autres(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {
            "SUMMARY.DG": "x",
            "SYSVAR.SV": "data",
            "readme.txt": "doc",
        })
        files = VAConverter._get_source_files(backup)
        assert all(f.suffix.lower() in {".sv", ".vr"} for f in files)

    def test_retourne_plusieurs_fichiers(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "a",
            "NUMREG.VR": "b",
            "POSREG.VR": "c",
        })
        files = VAConverter._get_source_files(backup)
        assert len(files) == 3

    def test_tri_alphabetique_insensible_casse(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {
            "ZEBRA.SV": "z",
            "alpha.vr": "a",
            "MANGO.VR": "m",
        })
        files = VAConverter._get_source_files(backup)
        noms = [f.name.lower() for f in files]
        assert noms == sorted(noms)

    def test_leve_erreur_si_aucun_fichier(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SUMMARY.DG": "x", "readme.txt": "doc"})
        with pytest.raises(ConverterError, match=r"\.SV ou \.VR"):
            VAConverter._get_source_files(backup)

    def test_leve_erreur_dossier_vide(self, tmp_path: Path) -> None:
        backup = tmp_path / "empty"
        backup.mkdir()
        with pytest.raises(ConverterError):
            VAConverter._get_source_files(backup)


# ---------------------------------------------------------------------------
# _extract_version
# ---------------------------------------------------------------------------

class TestExtractVersion:

    def test_version_standard(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SUMMARY.DG": _make_summary("V9.40P/55")})
        assert VAConverter.extract_version(backup) == "V9.40-1"

    def test_version_v7(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SUMMARY.DG": _make_summary("V7.20P/08")})
        assert VAConverter.extract_version(backup) == "V7.20-1"

    def test_version_v10(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SUMMARY.DG": _make_summary("V10.10P/01")})
        assert VAConverter.extract_version(backup) == "V10.10-1"

    def test_sans_summary_retourne_defaut(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SYSVAR.SV": "data"})
        version = VAConverter.extract_version(backup)
        assert version.startswith("V")
        assert version.endswith("-1")

    def test_summary_trop_court_retourne_defaut(self, tmp_path: Path) -> None:
        backup = _make_backup(tmp_path, {"SUMMARY.DG": "ligne_courte\n"})
        version = VAConverter.extract_version(backup)
        assert version.endswith("-1")


# ---------------------------------------------------------------------------
# _write_robot_ini
# ---------------------------------------------------------------------------

class TestWriteRobotIni:

    def test_fichier_cree(self, tmp_path: Path) -> None:
        VAConverter._write_robot_ini(tmp_path, "V9.40-1")
        assert (tmp_path / "robot.ini").exists()

    def test_contenu_section(self, tmp_path: Path) -> None:
        VAConverter._write_robot_ini(tmp_path, "V9.40-1")
        content = (tmp_path / "robot.ini").read_text(encoding="utf-8")
        assert "[WinOLPC_Util]" in content

    def test_contenu_version(self, tmp_path: Path) -> None:
        VAConverter._write_robot_ini(tmp_path, "V9.40-1")
        content = (tmp_path / "robot.ini").read_text(encoding="utf-8")
        assert "Version=V9.40-1" in content

    def test_contenu_robot(self, tmp_path: Path) -> None:
        VAConverter._write_robot_ini(tmp_path, "V9.40-1")
        content = (tmp_path / "robot.ini").read_text(encoding="utf-8")
        assert "Robot=\\" in content


# ---------------------------------------------------------------------------
# _get_exe_path
# ---------------------------------------------------------------------------

class TestFindKconvars:

    def test_utilise_settings_kconvars_exe(self, tmp_path: Path) -> None:
        exe = tmp_path / "kconvars.exe"
        exe.write_bytes(b"")
        s = Settings()
        s.kconvars_exe = str(exe)
        result = VAConverter._get_exe_path(s)
        assert result == exe

    def test_leve_erreur_si_introuvable(self, mocker) -> None:
        mocker.patch("shutil.which", return_value=None)
        s = Settings()
        s.kconvars_exe = "" 
        with pytest.raises(ExeNotFoundError):
            VAConverter._get_exe_path(s)


# ---------------------------------------------------------------------------
# VAConverter.convert_files — tests avec subprocess mocké
# ---------------------------------------------------------------------------

class TestConvertBackup:

    def _make_kconvars_mock(self, mocker, tmp_path: Path, returncode: int = 0):
        """Mock subprocess.run qui crée le .VA attendu si returncode == 0."""
        def fake_run(cmd, **kwargs):
            if returncode == 0:
                out = Path(cmd[2])
                out.write_text("$FAKE_VAR= 1\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=fake_run)
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)

    def test_retourne_liste_de_paths(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        result = VAConverter.convert_files(backup, settings=Settings())
        assert isinstance(result, list)
        assert all(isinstance(p, Path) for p in result)

    def test_produit_un_va_par_fichier_source(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "a",
            "NUMREG.VR": "b",
            "POSREG.VR": "c",
            "SUMMARY.DG": _make_summary(),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        result = VAConverter.convert_files(backup, settings=Settings())
        assert len(result) == 3

    def test_nom_va_correspond_au_source(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        result = VAConverter.convert_files(backup, settings=Settings())
        assert result[0].stem.upper() == "SYSVAR"
        assert result[0].suffix.upper() == ".VA"

    def test_va_cree_dans_dossier_backup(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        result = VAConverter.convert_files(backup, settings=Settings())
        assert result[0].parent == backup

    def test_best_effort_ignore_fichier_echoue(self, tmp_path: Path, mocker) -> None:
        """Un fichier qui échoue est ignoré, les autres sont produits."""
        backup = _make_backup(tmp_path, {
            "GOOD.SV": "ok",
            "BAD.VR": "bad",
            "SUMMARY.DG": _make_summary(),
        })
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)

        call_count = 0

        def selective_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            cwd = Path(kwargs.get("cwd", "."))
            src = Path(cmd[1])
            if "bad" in src.name.lower():
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Error")
            # Succès : crée le .VA
            out = Path(cmd[2])
            out.write_text("ok\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=selective_run)
        result = VAConverter.convert_files(backup, settings=Settings())
        assert len(result) == 1
        assert result[0].stem.upper() == "GOOD"

    def test_leve_erreur_si_tous_echouent(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)
        mocker.patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="fail"),
        )
        with pytest.raises(ConverterError, match="Aucun fichier .VA produit"):
            VAConverter.convert_files(backup, settings=Settings())

    def test_leve_erreur_si_dossier_inexistant(self) -> None:
        with pytest.raises(ConverterError, match="introuvable"):
            VAConverter.convert_files(Path("/inexistant/chemin"), settings=Settings())

    def test_timeout_leve_converter_error(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="kconvars", timeout=30),
        )
        with pytest.raises(ConverterError, match="timeout"):
            VAConverter.convert_files(backup, settings=Settings(), timeout=30)

    def test_progress_callback_appele(self, tmp_path: Path, mocker) -> None:
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "a",
            "NUMREG.VR": "b",
            "SUMMARY.DG": _make_summary(),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        calls = []
        VAConverter.convert_files(backup, settings=Settings(), progress_cb=lambda c, t, m: calls.append((c, t)))
        assert len(calls) == 2
        assert calls[0] == (1, 2)
        assert calls[1] == (2, 2)

    def test_commande_contient_ver(self, tmp_path: Path, mocker) -> None:
        """/ver doit être passé à kconvars."""
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary("V9.40P/55"),
        })
        self._make_kconvars_mock(mocker, tmp_path)
        captured_cmds = []

        original_run = subprocess.run

        def capturing_run(cmd, **kwargs):
            captured_cmds.append(cmd)
            cwd = Path(kwargs.get("cwd", "."))
            out = Path(cmd[2])
            out.write_text("ok\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=capturing_run)
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)

        VAConverter.convert_files(backup, settings=Settings())
        assert "/ver" in captured_cmds[0]
        assert "V9.40-1" in captured_cmds[0]

    def test_robot_ini_ecrit_avant_appel(self, tmp_path: Path, mocker) -> None:
        """robot.ini doit exister dans le cwd passé à subprocess."""
        backup = _make_backup(tmp_path, {
            "SYSVAR.SV": "data",
            "SUMMARY.DG": _make_summary(),
        })
        fake_exe = tmp_path / "kconvars.exe"
        fake_exe.write_bytes(b"")
        mocker.patch.object(VAConverter, "_get_exe_path", return_value=fake_exe)

        ini_found = []

        def check_ini(cmd, **kwargs):
            cwd = Path(kwargs.get("cwd", "."))
            ini_found.append((cwd / "robot.ini").exists())
            out = Path(cmd[2])
            out.write_text("ok\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        mocker.patch("subprocess.run", side_effect=check_ini)
        VAConverter.convert_files(backup, settings=Settings())
        assert all(ini_found), "robot.ini absent du cwd lors de l'appel kconvars"


# ---------------------------------------------------------------------------
# _needs_conversion (importé depuis orchestrator)
# ---------------------------------------------------------------------------

class TestNeedsConversion:

    def test_sv_sans_va_ni_dataid(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        (d / "SYSVAR.SV").write_text("x")
        assert _needs_conversion(d) is True

    def test_vr_sans_va_ni_dataid(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        (d / "NUMREG.VR").write_text("x")
        assert _needs_conversion(d) is True

    def test_va_present_pas_de_conversion(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        (d / "SYSVAR.SV").write_text("x")
        (d / "SYSVAR.VA").write_text("x")
        assert _needs_conversion(d) is False

    def test_dataid_present_pas_de_conversion(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        (d / "SYSVAR.SV").write_text("x")
        (d / "DATAID.CSV").write_text("x")
        assert _needs_conversion(d) is False

    def test_sans_sv_ni_vr_pas_de_conversion(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        (d / "SUMMARY.DG").write_text("x")
        assert _needs_conversion(d) is False

    def test_dossier_vide_pas_de_conversion(self, tmp_path: Path) -> None:
        from services.orchestrator import _needs_conversion
        d = tmp_path / "r"
        d.mkdir()
        assert _needs_conversion(d) is False

    def test_oserror_retourne_false(self, mocker) -> None:
        from services.orchestrator import _needs_conversion
        mocker.patch("pathlib.Path.iterdir", side_effect=OSError("perm"))
        assert _needs_conversion(Path("/fake")) is False