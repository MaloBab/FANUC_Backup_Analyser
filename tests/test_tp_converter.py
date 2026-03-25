"""
tests/test_tp_converter.py
──────────────────────────
Tests unitaires pour services/tp_converter.py.
Même structure que test_converter.py.
"""

from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config.settings import Settings
from services.converter.tp_converter import (
    TpConverterError,
    PrintTpNotFoundError,
    _find_printtp,
    _find_tp_files,
    _extract_version,
    convert_tp_files,
    has_tp_files,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.kconvars_exe  = ""
    s.printtp_exe   = ""
    s.printtp_timeout = 30
    return s


@pytest.fixture
def settings_with_printtp(tmp_path) -> Settings:
    exe = tmp_path / "PrintTP.exe"
    exe.write_bytes(b"fake")
    s = Settings()
    s.printtp_exe = str(exe)
    s.printtp_timeout = 30
    return s


# ---------------------------------------------------------------------------
# _find_printtp
# ---------------------------------------------------------------------------

class TestFindPrinttp:

    def test_settings_explicit_path_ok(self, tmp_path):
        exe = tmp_path / "PrintTP.exe"
        exe.write_bytes(b"fake")
        s = Settings()
        s.printtp_exe = str(exe)
        assert _find_printtp(s) == exe

    def test_settings_empty_falls_through(self, tmp_path):
        """Chemin vide dans settings → recherche sibling kconvars."""
        kconvars = tmp_path / "kconvars.exe"
        printtp  = tmp_path / "PrintTP.exe"
        kconvars.write_bytes(b"fake")
        printtp.write_bytes(b"fake")

        s = Settings()
        s.printtp_exe  = ""
        s.kconvars_exe = str(kconvars)
        assert _find_printtp(s) == printtp

    def test_sibling_kconvars_used_when_no_explicit_path(self, tmp_path):
        """PrintTP.exe dans le même dossier que kconvars est détecté automatiquement."""
        kconvars = tmp_path / "bin" / "kconvars.exe"
        printtp  = tmp_path / "bin" / "PrintTP.exe"
        kconvars.parent.mkdir()
        kconvars.write_bytes(b"fake")
        printtp.write_bytes(b"fake")

        s = Settings()
        s.printtp_exe  = ""
        s.kconvars_exe = str(kconvars)
        assert _find_printtp(s) == printtp

    def test_not_found_raises(self, settings):
        with pytest.raises(PrintTpNotFoundError):
            _find_printtp(settings)


# ---------------------------------------------------------------------------
# _find_tp_files
# ---------------------------------------------------------------------------

class TestFindTpFiles:

    def test_returns_tp_files_sorted(self, tmp_path):
        (tmp_path / "B.TP").write_bytes(b"")
        (tmp_path / "A.tp").write_bytes(b"")
        (tmp_path / "readme.txt").write_text("", encoding="utf-8")
        files = _find_tp_files(tmp_path)
        assert [f.name.lower() for f in files] == ["a.tp", "b.tp"]

    def test_no_tp_raises(self, tmp_path):
        (tmp_path / "PROG.SV").write_bytes(b"")
        with pytest.raises(TpConverterError, match="Aucun fichier .TP"):
            _find_tp_files(tmp_path)

    def test_case_insensitive(self, tmp_path):
        (tmp_path / "PROG.TP").write_bytes(b"")
        files = _find_tp_files(tmp_path)
        assert len(files) == 1


# ---------------------------------------------------------------------------
# has_tp_files
# ---------------------------------------------------------------------------

class TestHasTpFiles:

    def test_true_when_tp_present(self, tmp_path):
        (tmp_path / "PROG.TP").write_bytes(b"")
        assert has_tp_files(tmp_path) is True

    def test_false_when_no_tp(self, tmp_path):
        (tmp_path / "PROG.SV").write_bytes(b"")
        assert has_tp_files(tmp_path) is False

    def test_false_on_oserror(self):
        assert has_tp_files(Path("/nonexistent/path")) is False


# ---------------------------------------------------------------------------
# convert_tp_files — intégration avec subprocess mocké
# ---------------------------------------------------------------------------

class TestConvertTpFiles:

    def _make_backup(self, tmp_path, n_tp: int = 1) -> Path:
        for i in range(n_tp):
            (tmp_path / f"PROG{i}.TP").write_bytes(b"fake tp")
        return tmp_path

    @patch("services.tp_converter.subprocess.run")
    def test_produces_ls_files(self, mock_run, tmp_path, settings_with_printtp):
        backup_dir = self._make_backup(tmp_path)

        def _fake_run(cmd, **kwargs):
            # Simule la création du .LS par PrintTP
            out_path = Path(cmd[2])
            out_path.write_text("/PROG\n/END", encoding="utf-8")
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        mock_run.side_effect = _fake_run
        produced = convert_tp_files(backup_dir, settings=settings_with_printtp)
        assert len(produced) == 1
        assert produced[0].suffix.upper() == ".LS"
        assert produced[0].exists()

    @patch("services.tp_converter.subprocess.run")
    def test_progress_callback_called(self, mock_run, tmp_path, settings_with_printtp):
        self._make_backup(tmp_path, n_tp=2)

        def _fake_run(cmd, **kwargs):
            Path(cmd[2]).write_text("", encoding="utf-8")
            r = MagicMock()
            r.returncode = 0
            r.stderr = r.stdout = ""
            return r

        mock_run.side_effect = _fake_run
        calls = []
        convert_tp_files(
            tmp_path,
            settings=settings_with_printtp,
            progress_cb=lambda c, t, m: calls.append((c, t)),
        )
        assert len(calls) == 2
        assert calls[-1] == (2, 2)

    @patch("services.tp_converter.subprocess.run")
    def test_failed_returncode_skips_file(self, mock_run, tmp_path, settings_with_printtp):
        self._make_backup(tmp_path)
        r = MagicMock()
        r.returncode = 1
        r.stderr = "error"
        r.stdout = ""
        mock_run.return_value = r
        # Ne doit pas lever — retourne liste vide
        produced = convert_tp_files(tmp_path, settings=settings_with_printtp)
        assert produced == []

    def test_no_tp_files_raises(self, tmp_path, settings_with_printtp):
        with pytest.raises(TpConverterError, match="Aucun fichier .TP"):
            convert_tp_files(tmp_path, settings=settings_with_printtp)

    def test_invalid_dir_raises(self, settings_with_printtp):
        with pytest.raises(TpConverterError, match="introuvable"):
            convert_tp_files(Path("/nonexistent/backup"), settings=settings_with_printtp)

    def test_printtp_not_found_raises(self, tmp_path, settings):
        (tmp_path / "PROG.TP").write_bytes(b"")
        with pytest.raises(PrintTpNotFoundError):
            convert_tp_files(tmp_path, settings=settings)