"""
Orchestrateur — Façade entre l'UI et les services métier.
Coordonne le parsing (et à terme la conversion) sans que l'UI ne connaisse les détails.
Pattern : Facade + Observer (via callbacks de progression) + Strategy (parsers).

Sélection automatique du parser
────────────────────────────────
``_select_parser()`` parcourt la liste ``_parsers`` dans l'ordre de priorité et
retourne le premier dont ``can_parse()`` retourne ``True``.  Pour ajouter un
nouveau format, il suffit d'instancier le parser correspondant dans ``__init__``
— aucune autre modification n'est nécessaire.

Ordre de priorité actuel :
  1. ``DataIdCsvParser``  — robots nouvelle génération (DATAID.CSV)
  2. ``VAParser``         — robots classiques (fichiers .VA)

``DataIdCsvParser`` est testé en premier pour éviter qu'un dossier contenant
à la fois un DATAID.CSV et des fichiers .VA soit mal classifié (le DATAID.CSV
est le format faisant autorité sur les robots récents).

Note sur le ``progress_cb``
───────────────────────────
Les callbacks de progression sont appelés depuis le thread worker (c'est
intentionnel : l'orchestrateur n'a pas connaissance de Tkinter). La garantie
de thread-safety est assurée par ``BackgroundWorker._progress_proxy`` qui
intercepte le ``progress_cb`` et enfile les notifications dans la queue FIFO
avant qu'elles ne soient délivrées au thread Tkinter via ``poll_result()``.
L'orchestrateur n'a donc rien à changer de ce côté.
"""

from __future__ import annotations
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import (
    ConversionResult, ConversionStatus,
    ExtractionResult,
    RobotBackup, WorkspaceResult,
)
from services.parser.base_parser import BackupParser, ProgressCallback
from services.parser.va_parser import VAParser
from services.parser.dataid_csv_parser import DataIdCsvParser
from services.exporter import VariableExporter

logger = logging.getLogger(__name__)


class ExtractionOrchestrator:
    """Point d'entrée unique pour l'UI.

    Sélectionne automatiquement le parser adapté à chaque backup via la liste
    ``_parsers``.  Pour ajouter un nouveau format, instancier le parser dans
    ``__init__`` et l'insérer dans ``_parsers`` à la position souhaitée.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._exporter = VariableExporter()

        # Ordre de priorité : DataIdCsvParser avant VAParser.
        # Le premier parser dont can_parse() retourne True est utilisé.
        self._parsers: list[BackupParser] = [
            DataIdCsvParser(),
            VAParser(),
        ]

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def run(
        self,
        input_dir: Path,
        output_dir: Path | None = None,
        progress_cb: ProgressCallback | None = None,
        skip_conversion: bool = True,
    ) -> ExtractionResult:
        """Lance le pipeline d'extraction sur un dossier.

        Sélectionne automatiquement le parser via ``_select_parser()``.

        :param input_dir:       dossier à analyser.
        :param output_dir:      dossier de sortie optionnel (ignoré en V1).
        :param progress_cb:     callback ``(current, total, message)`` pour la progression.
        :param skip_conversion: si ``True``, parse directement sans Roboguide.
        :returns: ``ExtractionResult`` avec toutes les variables et les erreurs éventuelles.
        """
        if skip_conversion:
            return self._run_direct(input_dir, progress_cb)
        return self._run_with_conversion(input_dir, output_dir, progress_cb)

    def export(self, result: ExtractionResult, output_path: Path, fmt: str = "csv") -> None:
        """Exporte un résultat d'extraction vers un fichier.

        :param result:      résultat à exporter.
        :param output_path: chemin de destination.
        :param fmt:         ``"csv"``, ``"csv_flat"`` ou ``"json"``.
        """
        self._exporter.export(result.variables, output_path, fmt)

    def scan_workspace(self, root_path: Path) -> WorkspaceResult:
        """Scanne un dossier racine et détecte les sous-dossiers backups.

        Supporte les formats mixtes : un workspace peut contenir à la fois
        des backups .VA (anciens robots) et des backups DATAID.CSV (nouveaux).
        Chaque sous-dossier est annoté avec le ``format`` détecté.

        Ne parse pas les fichiers immédiatement — retourne uniquement la structure
        pour permettre à l'UI d'afficher la liste des robots avant le chargement.

        :param root_path: dossier racine contenant les sous-dossiers robots.
        :returns: ``WorkspaceResult`` avec un ``RobotBackup`` par sous-dossier trouvé.
        """
        result = WorkspaceResult(root_path=root_path)

        # Chercher les sous-dossiers directs reconnus par au moins un parser
        candidates = sorted(
            p for p in root_path.iterdir()
            if p.is_dir() and self._select_parser(p) is not None
        )

        # Cas dégénéré : le dossier racine lui-même est un backup
        if not candidates and self._select_parser(root_path) is not None:
            fmt = self._detect_format(root_path)
            result.backups.append(
                RobotBackup(name=root_path.name, path=root_path, format=fmt)
            )
        else:
            for sub in candidates:
                fmt = self._detect_format(sub)
                result.backups.append(RobotBackup(name=sub.name, path=sub, format=fmt))

        logger.info(
            "Workspace scanné : %d backup(s) trouvé(s) dans %s",
            len(result.backups), root_path,
        )
        for backup in result.backups:
            logger.debug("  %s — format : %s", backup.name, backup.format)

        return result

    def load_backup(
        self,
        backup: RobotBackup,
        progress_cb: ProgressCallback | None = None,
    ) -> RobotBackup:
        """Parse un backup robot et peuple ``backup.variables``.

        Sélectionne automatiquement le parser via ``_select_parser()``.
        Modifie ``backup`` en place et retourne-le pour faciliter
        l'utilisation avec ``BackgroundWorker``.

        :param backup:      ``RobotBackup`` à charger (``backup.loaded`` passe à ``True``).
        :param progress_cb: callback ``(current, total, message)`` optionnel.
        :returns: le même objet ``backup`` mis à jour.
        """
        parser = self._select_parser(backup.path)

        if parser is None:
            msg = f"Aucun parser compatible pour '{backup.name}' ({backup.path})"
            logger.error(msg)
            backup.errors.append(msg)
            backup.loaded = True
            return backup

        try:
            variables = parser.parse(backup.path, progress_cb)
        except Exception as exc:
            msg = f"Erreur inattendue lors du parsing de '{backup.name}' : {exc}"
            logger.exception(msg)
            backup.errors.append(msg)
            backup.loaded = True
            return backup

        backup.variables = variables
        backup.loaded    = True
        _notify(
            progress_cb,
            1, 1,
            f"Terminé — {backup.var_count} variable(s), {backup.field_count} field(s)",
        )
        return backup

    # ------------------------------------------------------------------
    # Sélection du parser (Strategy)
    # ------------------------------------------------------------------

    def _select_parser(self, path: Path) -> BackupParser | None:
        """Retourne le premier parser compatible avec *path*, ou ``None``.

        Parcourt ``_parsers`` dans l'ordre de priorité.  Le premier dont
        ``can_parse()`` retourne ``True`` est sélectionné.

        :param path: dossier racine d'un backup robot.
        """
        for parser in self._parsers:
            try:
                if parser.can_parse(path):
                    return parser
            except OSError as exc:
                logger.warning(
                    "can_parse() a levé une OSError pour %s (%s) : %s",
                    parser.__class__.__name__, path, exc,
                )
        return None

    def _detect_format(self, path: Path) -> str:
        """Retourne le ``FORMAT_ID`` du parser compatible, ou ``"unknown"``."""
        parser = self._select_parser(path)
        return parser.FORMAT_ID if parser is not None else "unknown"

    # ------------------------------------------------------------------
    # Pipeline direct (sans Roboguide)
    # ------------------------------------------------------------------

    def _run_direct(
        self,
        input_dir: Path,
        progress_cb: ProgressCallback | None,
    ) -> ExtractionResult:
        """Parse directement le dossier en sélectionnant le bon parser."""
        parser = self._select_parser(input_dir)

        if parser is None:
            _notify(progress_cb, 0, 0, "Aucun fichier reconnu dans ce dossier.")
            logger.warning("Aucun parser compatible pour : %s", input_dir)
            return ExtractionResult(input_dir=input_dir)

        result = ExtractionResult(input_dir=input_dir)
        try:
            result.variables.extend(parser.parse(input_dir, progress_cb))
        except Exception as exc:
            result.errors.append(str(exc))
            logger.error("Erreur parsing %s : %s", input_dir, exc)

        _notify(
            progress_cb,
            1, 1,
            f"Terminé — {result.var_count} variable(s), {result.field_count} field(s)",
        )
        return result

    # ------------------------------------------------------------------
    # Pipeline avec conversion Roboguide (V2 — TODO)
    # ------------------------------------------------------------------

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

            # Après conversion, les fichiers produits sont des .VA → VAParser
            va_parser = VAParser()
            successful = [
                r.output_path for r in conversion_results
                if r.status == ConversionStatus.SUCCESS and r.output_path
            ]
            total = len(successful)
            for i, va_path in enumerate(successful, start=1):
                _notify(progress_cb, i, total, f"Parsing : {va_path.name}")
                try:
                    result.variables.extend(va_parser.parse_file(va_path))
                except Exception as exc:
                    result.errors.append(f"{va_path.name}: {exc}")
                    logger.error("Erreur parsing %s : %s", va_path.name, exc)

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

    # ------------------------------------------------------------------
    # Conversion Roboguide (V2 — TODO)
    # ------------------------------------------------------------------

    def _convert_directory(
        self,
        input_dir: Path,
        output_dir: Path,
        progress_cb: ProgressCallback | None,
    ) -> list[ConversionResult]:
        """Lance Roboguide sur chaque fichier source trouvé."""
        files   = self._find_convertible_files(input_dir)
        results: list[ConversionResult] = []
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
        extensions = {".vs", ".tp", ".vr"}
        return sorted(
            p for p in directory.rglob("*") if p.suffix.lower() in extensions
        )


# ---------------------------------------------------------------------------
# Helpers module-level
# ---------------------------------------------------------------------------

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