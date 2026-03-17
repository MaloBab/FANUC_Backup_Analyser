"""
Orchestrateur — Façade entre l'UI et les services métier.
Coordonne le parsing (et à terme la conversion) sans que l'UI ne connaisse les détails.
Pattern : Facade + Observer (via callbacks de progression).
"""

from __future__ import annotations
import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import ExtractionResult, ConversionResult, ConversionStatus
from services.parser import VAParser
from services.exporter import VariableExporter

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


class ExtractionOrchestrator:
    """Point d'entrée unique pour l'UI.

    En V1 (``skip_conversion=True``), scanne directement les ``.VA`` du dossier
    source sans passer par Roboguide.  Quand la CLI Roboguide sera connue,
    il suffira de passer ``skip_conversion=False`` et de compléter
    ``_build_roboguide_command()``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._parser   = VAParser()
        self._exporter = VariableExporter()

    def run(
        self,
        input_dir: Path,
        output_dir: Path | None = None,
        progress_cb: ProgressCallback | None = None,
        skip_conversion: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> ExtractionResult:
        """Lance le pipeline d'extraction.

        :param input_dir: dossier à analyser.
        :param output_dir: dossier de sortie optionnel (ignoré en V1).
        :param progress_cb: callback ``(current, total, message)`` pour la progression.
        :param skip_conversion: si ``True``, parse directement les ``.VA`` sans Roboguide.
        :returns: ``ExtractionResult`` avec toutes les variables et les erreurs éventuelles.
        """
        if skip_conversion:
            return self._run_direct(input_dir, progress_cb, cancel_event)
        return self._run_with_conversion(input_dir, output_dir, progress_cb)

    def export(self, result: ExtractionResult, output_path: Path, fmt: str = "csv") -> None:
        """Exporte un résultat d'extraction vers un fichier.

        :param result: résultat à exporter.
        :param output_path: chemin de destination.
        :param fmt: ``"csv"``, ``"csv_flat"`` ou ``"json"``.
        """
        self._exporter.export(result.variables, output_path, fmt)



    def _run_direct(
        self,
        input_dir: Path,
        progress_cb: ProgressCallback | None,
        cancel_event: threading.Event | None = None,
    ) -> ExtractionResult:
        """Parse directement tous les fichiers .VA du dossier (V1 — sans Roboguide)."""
        va_files = sorted(p for p in input_dir.rglob("*") if p.suffix.lower() == ".va")
        total    = len(va_files)

        if total == 0:
            _notify(progress_cb, 0, 0, "Aucun fichier .VA trouvé.")
            return ExtractionResult(input_dir=input_dir)

        result = ExtractionResult(input_dir=input_dir)
        for i, va_path in enumerate(va_files, start=1):
            if cancel_event and cancel_event.is_set():
                _notify(progress_cb, i, total, "Extraction annulée.")
                result.errors.append("Annulé par l'utilisateur.")
                break
            _notify(progress_cb, i, total, f"Parsing : {va_path.name}")
            try:
                result.variables.extend(self._parser.parse_file(va_path))
            except Exception as exc:
                result.errors.append(f"{va_path.name}: {exc}")
                logger.error("Erreur parsing %s : %s", va_path.name, exc)

        _notify(
            progress_cb, total, total,
            f"Terminé — {result.var_count} variable(s), {result.field_count} field(s)",
        )
        return result

    def _run_with_conversion(
        self,
        input_dir: Path,
        output_dir: Path | None,
        progress_cb: ProgressCallback | None,
    ) -> ExtractionResult:
        """Pipeline complet avec conversion Roboguide (V2)."""
        result = ExtractionResult(input_dir=input_dir)

        with _ensure_output_dir(output_dir) as work_dir:
            _notify(progress_cb, 0, 1, "Conversion en cours…")
            conversion_results = self._convert_directory(input_dir, work_dir, progress_cb)

            successful = [
                r.output_path for r in conversion_results
                if r.status == ConversionStatus.SUCCESS and r.output_path
            ]
            total = len(successful)
            for i, va_path in enumerate(successful, start=1):
                _notify(progress_cb, i, total, f"Parsing : {va_path.name}")
                try:
                    result.variables.extend(self._parser.parse_file(va_path))
                except Exception as exc:
                    result.errors.append(f"{va_path.name}: {exc}")

        for r in conversion_results:
            if r.status == ConversionStatus.FAILED:
                result.errors.append(
                    f"Conversion échouée : {r.source_path.name} — {r.error_message}"
                )

        _notify(
            progress_cb, 1, 1,
            f"Terminé — {result.var_count} variable(s), {result.field_count} field(s)",
        )
        return result

    # Conversion (V1 — TODO)

    def _convert_directory(
        self,
        input_dir: Path,
        output_dir: Path,
        progress_cb: ProgressCallback | None,
    ) -> list[ConversionResult]:
        """Lance Roboguide sur chaque fichier source trouvé."""
        files   = self._find_convertible_files(input_dir)
        results : list[ConversionResult] = []
        for i, src in enumerate(files, start=1):
            _notify(progress_cb, i, len(files), f"Conversion : {src.name}")
            results.append(self._convert_one(src, output_dir))
        return results

    def _convert_one(self, source: Path, output_dir: Path) -> ConversionResult:
        result = ConversionResult(source_path=source)
        start  = time.monotonic()
        try:
            cmd  = self._build_roboguide_command(source, output_dir)
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self._settings.roboguide_timeout,
            )
            result.duration_s = time.monotonic() - start
            if proc.returncode == 0:
                result.output_path = output_dir / source.with_suffix(".VA").name
                result.status      = ConversionStatus.SUCCESS
            else:
                result.status        = ConversionStatus.FAILED
                result.error_message = (proc.stderr or proc.stdout).strip()
        except subprocess.TimeoutExpired:
            result.status        = ConversionStatus.FAILED
            result.error_message = f"Timeout ({self._settings.roboguide_timeout}s)"
        except FileNotFoundError:
            result.status        = ConversionStatus.FAILED
            result.error_message = f"Exécutable introuvable : {self._settings.roboguide_exe}"
        return result

    def _build_roboguide_command(self, source: Path, output_dir: Path) -> list[str]:
        """TODO : adapter aux arguments CLI réels de Roboguide."""
        return [
            self._settings.roboguide_exe,
            "--input",  str(source),
            "--output", str(output_dir),
            "--format", "VA",
        ]

    @staticmethod
    def _find_convertible_files(directory: Path) -> list[Path]:
        """TODO : ajuster les extensions sources selon le format Roboguide."""
        extensions = {".vr", ".tp", ".sv"}
        return sorted(p for p in directory.rglob("*") if p.suffix.lower() in extensions)



def _notify(cb: ProgressCallback | None, cur: int, tot: int, msg: str) -> None:
    if cb:
        cb(cur, tot, msg)


class _ensure_output_dir:
    """Gestionnaire de contexte : utilise le dossier fourni ou crée un temporaire."""

    def __init__(self, output_dir: Path | None) -> None:
        self._provided = output_dir
        self._tmpdir: tempfile.TemporaryDirectory | None = None

    def __enter__(self) -> Path:
        if self._provided:
            self._provided.mkdir(parents=True, exist_ok=True)
            return self._provided
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fanuc_")
        return Path(self._tmpdir.name)

    def __exit__(self, *_) -> None:
        if self._tmpdir:
            self._tmpdir.cleanup()