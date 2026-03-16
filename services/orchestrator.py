"""
Orchestrateur — Façade entre l'UI et les services métier.
Coordonne la conversion et le parsing sans que l'UI ne connaisse les détails.
Pattern : Facade + Observer (via callbacks de progression).
"""

from __future__ import annotations
import logging
import subprocess
import tempfile
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
    """
    Point d'entrée unique pour l'UI.
    L'UI appelle run() et reçoit un ExtractionResult.
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
    ) -> ExtractionResult:
        """
        Pipeline complet :
          1. Conversion des fichiers sources → .VA  (via Roboguide)
          2. Parsing des .VA → SystemVariable
        """
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

        failed = [r for r in conversion_results if r.status == ConversionStatus.FAILED]
        
        for r in failed:
            result.errors.append(f"Conversion échouée : {r.source_path.name} — {r.error_message}")

        _notify(
            progress_cb, 1, 1,
            f"Terminé — {result.var_count} variable(s), {result.field_count} field(s)"
        )
        return result

    def export(self, result: ExtractionResult, output_path: Path, fmt: str = "csv") -> None:
        self._exporter.export(result.variables, output_path, fmt)


    def _convert_directory(
        self,
        input_dir: Path,
        output_dir: Path,
        progress_cb: ProgressCallback | None,
    ) -> list[ConversionResult]:
        """
        Lance Roboguide sur chaque fichier source trouvé.
        TODO : brancher RoboguideConverter quand la CLI est connue.
        """
        files = self._find_convertible_files(input_dir)
        results: list[ConversionResult] = []

        for i, src in enumerate(files, start=1):
            _notify(progress_cb, i, len(files), f"Conversion : {src.name}")
            r = self._convert_one(src, output_dir)
            results.append(r)

        return results

    def _convert_one(self, source: Path, output_dir: Path) -> ConversionResult:
        result = ConversionResult(source_path=source)
        start  = time.monotonic()

        try:
            cmd = self._build_roboguide_command(source, output_dir)
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._settings.roboguide_timeout,
            )
            result.duration_s = time.monotonic() - start

            if proc.returncode == 0:
                result.output_path = output_dir / source.with_suffix(".VA").name
                result.status = ConversionStatus.SUCCESS
            else:
                result.status = ConversionStatus.FAILED
                result.error_message = (proc.stderr or proc.stdout).strip()

        except subprocess.TimeoutExpired:
            result.status = ConversionStatus.FAILED
            result.error_message = f"Timeout ({self._settings.roboguide_timeout}s)"
        except FileNotFoundError:
            result.status = ConversionStatus.FAILED
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
        """TODO : ajuster les extensions sources selon le format attendu par Roboguide."""
        extensions = {".ls", ".tp", ".kl"}
        return sorted(p for p in directory.rglob("*") if p.suffix.lower() in extensions)


def _notify(cb: ProgressCallback | None, cur: int, tot: int, msg: str) -> None:
    if cb:
        cb(cur, tot, msg)


class _ensure_output_dir:
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