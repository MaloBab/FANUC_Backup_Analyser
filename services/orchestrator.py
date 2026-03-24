"""
services/orchestrator.py
─────────────────────────
Orchestrateur — Façade entre l'UI et les services métier.
Coordonne la conversion (kconvars) et le parsing sans que l'UI ne connaisse
les détails.
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

Conversion automatique
──────────────────────
Avant le parsing, ``load_backup()`` appelle ``_needs_conversion()`` pour décider
si kconvars doit être invoqué.  La conversion est déclenchée si et seulement si
les trois conditions suivantes sont réunies :
  • pas de DATAID.CSV dans le dossier
  • pas de fichier .VA dans le dossier
  • présence d'au moins un fichier .SV ou .VR dans le dossier

Si la conversion échoue, l'erreur est remontée immédiatement à l'UI
(``ConverterError`` propagée — pas de dégradation silencieuse).

Structure workspace supportée
──────────────────────────────
``scan_workspace()`` détecte automatiquement deux structures :
  • dossier racine contenant des sous-dossiers (un par robot)
  • dossier racine lui-même contenant directement les fichiers backup

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
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import (
    ExtractionResult,
    RobotBackup, WorkspaceResult,
)
from services.converter import convert_backup, ConverterError, KconvarsNotFoundError
from services.parser.base_parser import BackupParser, ProgressCallback
from services.parser.va_parser import VAParser
from services.parser.dataid_csv_parser import DataIdCsvParser
from services.exporter import VariableExporter

logger = logging.getLogger(__name__)

# Extensions qui signalent un backup binaire à convertir
_CONVERTIBLE_EXTENSIONS = {".sv", ".vr"}


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

        Inclut également les dossiers nécessitant une conversion (présence de
        .SV/.VR sans .VA ni DATAID.CSV) — leur format est marqué ``"pending"``.

        Ne parse pas les fichiers immédiatement — retourne uniquement la
        structure pour permettre à l'UI d'afficher la liste des robots avant
        le chargement.

        :param root_path: dossier racine contenant les sous-dossiers robots.
        :returns: ``WorkspaceResult`` avec un ``RobotBackup`` par sous-dossier.
        """
        result = WorkspaceResult(root_path=root_path)

        # Chercher les sous-dossiers directs reconnus par au moins un parser
        # OU nécessitant une conversion
        candidates = sorted(
            p for p in root_path.iterdir()
            if p.is_dir() and (
                self._select_parser(p) is not None
                or _needs_conversion(p)
            )
        )

        # Cas dégénéré : le dossier racine lui-même est un backup
        if not candidates and (
            self._select_parser(root_path) is not None
            or _needs_conversion(root_path)
        ):
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
        step_offset: int = 0,
        total_steps: int = 2,
    ) -> RobotBackup:
        """Parse un backup robot et peuple ``backup.variables``.

        Si le dossier nécessite une conversion (critères ``_needs_conversion``),
        appelle ``convert_backup()`` avant le parsing.  La progression est
        partagée sur ``total_steps`` steps : conversion = step_offset + 1,
        parsing = step_offset + 2.

        En cas d'échec de la conversion, une ``ConverterError`` est propagée
        immédiatement — l'UI est chargée de l'afficher à l'utilisateur.

        :param backup:       ``RobotBackup`` à charger.
        :param progress_cb:  callback ``(current, total, message)`` optionnel.
        :param step_offset:  décalage dans la progression globale (pour les
                             workspaces multi-backups).
        :param total_steps:  nombre total de steps de la progression globale.
        :returns: le même objet ``backup`` mis à jour.
        """
        # ── Étape 1 : conversion si nécessaire ──────────────────────────
        if _needs_conversion(backup.path):
            _notify(
                progress_cb,
                step_offset + 1, total_steps,
                f"Conversion : {backup.name}…",
            )
            logger.info("Conversion nécessaire pour '%s'.", backup.name)

            # ConverterError / KconvarsNotFoundError propagées volontairement :
            # l'UI doit bloquer et informer l'utilisateur.
            va_paths = convert_backup(
                backup_dir=backup.path,
                settings=self._settings,
            )
            logger.info(
                "%d fichier(s) .VA produit(s) pour '%s' : %s",
                len(va_paths), backup.name,
                ", ".join(p.name for p in va_paths),
            )

            # Mise à jour du format maintenant que le .VA existe
            backup.format = self._detect_format(backup.path)

        # ── Étape 2 : parsing ────────────────────────────────────────────
        _notify(
            progress_cb,
            step_offset + 2, total_steps,
            f"Parsing : {backup.name}…",
        )

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
            step_offset + 2, total_steps,
            f"Terminé — {backup.var_count} variable(s), {backup.field_count} field(s)",
        )
        return backup

    def load_workspace(
        self,
        workspace: WorkspaceResult,
        progress_cb: ProgressCallback | None = None,
    ) -> WorkspaceResult:
        """Charge tous les backups d'un workspace avec une progression unifiée.

        Chaque backup compte pour 2 steps (conversion + parsing), qu'une
        conversion soit nécessaire ou non — la progression reste linéaire
        et prévisible.

        En cas d'erreur de conversion sur un backup, la ``ConverterError``
        est propagée immédiatement et stoppe le chargement.

        :param workspace:   ``WorkspaceResult`` retourné par ``scan_workspace()``.
        :param progress_cb: callback ``(current, total, message)`` optionnel.
        :returns: le même ``workspace`` avec tous les backups chargés.
        """
        backups     = workspace.backups
        total_steps = len(backups) * 2  # 2 steps par backup

        for i, backup in enumerate(backups):
            offset = i * 2
            self.load_backup(
                backup,
                progress_cb=progress_cb,
                step_offset=offset,
                total_steps=total_steps,
            )

        return workspace

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
        """Retourne le ``FORMAT_ID`` du parser compatible, ``"pending"`` si
        une conversion est nécessaire, ou ``"unknown"``."""
        parser = self._select_parser(path)
        if parser is not None:
            return parser.FORMAT_ID
        if _needs_conversion(path):
            return "pending"
        return "unknown"


# ---------------------------------------------------------------------------
# Helpers module-level
# ---------------------------------------------------------------------------

def _needs_conversion(path: Path) -> bool:
    """Retourne ``True`` si le dossier *path* doit passer par kconvars.

    Conditions (toutes requises) :
      1. Pas de DATAID.CSV
      2. Pas de fichier .VA
      3. Au moins un fichier .SV ou .VR
    """
    try:
        files = {f.suffix.lower(): f for f in path.iterdir() if f.is_file()}
    except OSError:
        return False

    suffixes = set(files.keys())

    has_dataid = (path / "DATAID.CSV").exists()
    has_va     = any(s == ".va" for s in suffixes)
    has_source = bool(suffixes & _CONVERTIBLE_EXTENSIONS)

    return not has_dataid and not has_va and has_source


def _notify(cb: ProgressCallback | None, cur: int, tot: int, msg: str) -> None:
    if cb:
        cb(cur, tot, msg)