"""
ViewModel — couche intermédiaire entre l'UI et les services.
Porte l'état de l'application et expose des commandes.
Pattern MVVM : l'UI ne connaît que le ViewModel, jamais les services directement.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import ExtractionResult, RobotBackup, WorkspaceResult
from services.orchestrator import ExtractionOrchestrator
from utils.worker import BackgroundWorker

logger = logging.getLogger(__name__)


class AppViewModel:
    """État global de l'application + commandes déclenchables par l'UI.

    Les callbacks ``on_*`` sont branchés par ``App._bind_viewmodel()`` après
    instanciation. Ils sont tous optionnels (``None`` par défaut) — le ViewModel
    fonctionne sans UI pour les tests.
    """

    # Callbacks — branchés par App._bind_viewmodel
    on_status_change:   Callable[[str], None] | None = None
    on_progress_change: Callable[[int, int], None] | None = None
    on_log_message:     Callable[[str, str], None] | None = None  # (msg, level)
    on_scope_change:    Callable[[str], None] | None = None        # scope filter
    on_workspace_ready: Callable[[WorkspaceResult], None] | None = None
    on_backup_loaded:   Callable[[RobotBackup], None] | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings      = settings
        self._orchestrator = ExtractionOrchestrator(settings)
        self._worker       = BackgroundWorker()

        # État observable
        self.input_dir:      Path | None             = None
        self.output_dir:     Path | None             = None
        self.last_result:    ExtractionResult | None = None
        self.is_busy:        bool                    = False
        # Multi-backups
        self.workspace:      WorkspaceResult | None  = None
        self.active_backup:  RobotBackup | None      = None

        # Référence Tkinter root — injectée par App pour le polling after()
        self._tk_root = None

    # ------------------------------------------------------------------
    # Injection (appelée par App)
    # ------------------------------------------------------------------

    def set_tk_root(self, root) -> None:
        """Injecte la référence à la fenêtre Tkinter racine pour le polling."""
        self._tk_root = root

    # ------------------------------------------------------------------
    # Commandes (appelées par l'UI)
    # ------------------------------------------------------------------

    def set_input_dir(self, path: str) -> None:
        """Met à jour le dossier source et persiste dans les settings.

        Conservé pour compatibilité — préférer ``scan_workspace``.
        """
        self.input_dir             = Path(path)
        self.settings.last_input_dir = path
        self._emit_status(f"Dossier source : {path}")

    def set_output_dir(self, path: str) -> None:
        """Met à jour le dossier de sortie et persiste dans les settings."""
        self.output_dir              = Path(path)
        self.settings.last_output_dir = path

    def set_scope_filter(self, scope: str) -> None:
        """Propage un changement de filtre de scope (all/system/karel) vers l'UI.

        :param scope: ``"all"``, ``"system"`` ou ``"karel"``.
        """
        if self.on_scope_change:
            self.on_scope_change(scope)


    def scan_workspace(self, path: str) -> None:
        """Scanne un dossier racine et charge tous les backups automatiquement.

        :param path: chemin du dossier racine sélectionné.
        """
        root = Path(path)
        if not root.is_dir():
            self._emit_log("Dossier invalide.", "error")
            return
        self.settings.last_input_dir = path
        workspace = self._orchestrator.scan_workspace(root)
        self.workspace = workspace
        n = workspace.robot_count
        self._emit_status(f"Chargement de {n} backup{'s' if n > 1 else ''}…")
        self._emit_log(f"Workspace : {n} robot(s) dans {root.name}", "info")
        # Notifier l'UI de la structure avant de charger
        if self.on_workspace_ready:
            self.on_workspace_ready(workspace)
        # Charger tous les backups séquentiellement en arrière-plan
        if workspace.backups:
            self._load_all_backups(workspace)

    def _load_all_backups(self, workspace: "WorkspaceResult") -> None:
        """Charge tous les backups du workspace séquentiellement.

        Chaque backup est chargé en arrière-plan via le worker.
        On enchaîne les chargements via un callback récursif.
        """
        pending = [b for b in workspace.backups if not b.loaded]
        if not pending:
            total = sum(b.var_count for b in workspace.backups)
            self._emit_status(f"Workspace chargé — {total} variables au total")
            return
        backup = pending[0]

        if self._worker.is_running:
            # Worker occupé : réessayer dans 200ms
            if self._tk_root:
                self._tk_root.after(200, lambda: self._load_all_backups(workspace))
            return

        self.is_busy = True
        self._emit_status(f"Chargement {backup.name}… ({len(pending)} restant(s))")

        def _on_done(b: "RobotBackup") -> None:
            self.is_busy = False
            msg = f"{b.name} — {b.var_count} variable(s)"
            self._emit_log(msg, "success")
            if self.on_backup_loaded:
                self.on_backup_loaded(b)
            # Continuer avec le suivant
            self._load_all_backups(workspace)

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={"progress_cb": self._on_progress},
            on_done=_on_done,
            on_error=self._on_extraction_error,
        )
        self._poll()

    def load_backup(self, backup: RobotBackup) -> None:
        """Parse les fichiers .VA d'un backup robot en arrière-plan.

        :param backup: robot à charger (identifié dans ``workspace.backups``).
        """
        if self._worker.is_running:
            self._emit_log("Un chargement est déjà en cours.", "warning")
            return

        self.active_backup = backup
        self.is_busy = True
        self._emit_status(f"Chargement de {backup.name}…")
        self._emit_log(f"Parsing : {backup.path}", "info")

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={
                "progress_cb":  self._on_progress
            },
            on_done=self._on_backup_loaded,
            on_error=self._on_extraction_error,
        )
        self._poll()

    def start_extraction(self) -> None:
        """Lance l'extraction en arrière-plan (parsing direct des .VA, sans Roboguide)."""
        if self._worker.is_running:
            self._emit_log("Une extraction est déjà en cours.", "warning")
            return
        if not self.input_dir or not self.input_dir.is_dir():
            self._emit_log("Dossier source invalide ou absent.", "error")
            return

        self.is_busy = True
        self._emit_status("Extraction en cours…")
        self._emit_log(f"Démarrage sur : {self.input_dir}", "info")

        self._worker.run(
            self._orchestrator.run,
            args=(self.input_dir,),
            kwargs={
                "output_dir":      self.output_dir,
                "progress_cb":     self._on_progress,
                "skip_conversion": True
            },
            on_done=self._on_extraction_done,
            on_error=self._on_extraction_error,
        )
        self._poll()

    def export_results(self, output_path: Path, fmt: str = "csv") -> None:
        """Exporte le dernier résultat d'extraction.

        :param output_path: chemin de destination.
        :param fmt: ``"csv"``, ``"csv_flat"`` ou ``"json"``.
        """
        if not self.last_result:
            self._emit_log("Aucun résultat à exporter.", "warning")
            return
        try:
            self._orchestrator.export(self.last_result, output_path, fmt)
            self._emit_log(f"Export {fmt.upper()} → {output_path}", "success")
        except Exception as exc:
            self._emit_log(f"Export échoué : {exc}", "error")
            logger.error("Export échoué : %s", exc)

    # ------------------------------------------------------------------
    # Callbacks internes (thread worker → thread Tkinter via queue)
    # ------------------------------------------------------------------

    def _on_backup_loaded(self, backup: RobotBackup) -> None:
        """Appelé quand le parsing d'un backup est terminé."""
        self.is_busy = False
        msg = f"{backup.name} — {backup.var_count} variable(s), {backup.field_count} field(s)"
        self._emit_status(msg)
        self._emit_log(msg, "success")
        for err in backup.errors:
            self._emit_log(f"⚠ {err}", "warning")
        # Mettre à jour last_result pour l'export
        self.last_result = ExtractionResult(
            input_dir=backup.path,
            variables=backup.variables,
            errors=backup.errors,
        )
        if self.on_backup_loaded:
            self.on_backup_loaded(backup)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        """Remonte la progression depuis le thread worker (thread-safe via queue)."""
        if self.on_progress_change:
            self.on_progress_change(current, total)
        if message:
            self._emit_log(message, "info")

    def _on_extraction_done(self, result: ExtractionResult) -> None:
        """Appelé quand l'extraction se termine avec succès."""
        self.last_result = result
        self.is_busy     = False
        msg = (
            f"Extraction terminée — {result.var_count} variable(s), "
            f"{result.field_count} field(s)"
        )
        self._emit_status(msg)
        self._emit_log(msg, "success")

        for err in result.errors:
            self._emit_log(f"⚠ {err}", "warning")


    def _on_extraction_error(self, exc: Exception) -> None:
        """Appelé quand l'extraction lève une exception non rattrapée."""
        self.is_busy = False
        msg = f"Erreur inattendue : {exc}"
        self._emit_status(msg)
        self._emit_log(msg, "error")
        logger.exception("Erreur extraction", exc_info=exc)

    def _poll(self) -> None:
        """Interroge la queue du worker toutes les 100 ms depuis le thread Tkinter."""
        if self._worker.poll_result():
            return  # worker terminé, on arrête le polling
        if self._tk_root:
            self._tk_root.after(100, self._poll)

    # ------------------------------------------------------------------
    # Helpers d'émission
    # ------------------------------------------------------------------

    def _emit_status(self, msg: str) -> None:
        if self.on_status_change:
            self.on_status_change(msg)

    def _emit_log(self, msg: str, level: str = "info") -> None:
        logger.log(
            {"info": 20, "success": 20, "warning": 30, "error": 40}.get(level, 20),
            msg,
        )
        if self.on_log_message:
            self.on_log_message(msg, level)