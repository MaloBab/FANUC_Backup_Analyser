"""
ViewModel — couche intermédiaire entre l'UI et les services.
Porte l'état de l'application et expose des commandes.
Pattern MVVM : l'UI ne connaît que le ViewModel, jamais les services directement.

Corrections appliquées
──────────────────────
1. Callbacks déclarés comme attributs d'INSTANCE dans ``__init__``.

2. ``scan_workspace`` exécuté dans le ``BackgroundWorker`` (pas dans le thread
   principal) afin de ne pas bloquer l'UI sur des workspaces volumineux.

3. ``_LOG_LEVELS`` défini comme constante de module.

4. ``_tk_root`` correctement typé en ``tk.Tk | None``.

5. Remplacement de la garde booléenne ``_polling`` par un compteur de génération
   ``_poll_generation``.

   Problème de fond : ``_on_done`` (et ``_on_workspace_scanned``) sont appelés
   depuis l'intérieur de ``poll_result()`` → ``_poll()``. Quand ces callbacks
   déclenchent un nouveau ``_worker.run()`` + ``_start_poll()``, la boucle
   ``_poll()`` parente est encore en cours d'exécution. Avec un booléen
   ``_polling``, deux cas défaillants :

   a) ``_polling`` est encore ``True`` → ``_start_poll()`` retourne sans rien
      faire → la boucle after() meurt après le premier backup → les suivants
      ne sont jamais notifiés à l'UI.

   b) On remet ``_polling = False`` dans le callback avant d'appeler
      ``_start_poll()`` → ``_start_poll()`` démarre une nouvelle boucle, PUIS
      ``_poll()`` parente retourne et pose ``_polling = False`` → deux boucles
      after() concurrentes jusqu'à la prochaine fin de run.

   Solution : ``_poll_generation`` est un entier incrémenté à chaque
   ``_start_poll()``. Chaque boucle capture sa génération par closure et
   s'arrête si la génération courante est différente (run plus récent démarré).
   Plusieurs boucles peuvent coexister sans conflit : seule la dernière est
   encore valide une fois les runs précédents terminés.
"""

from __future__ import annotations
import logging
import tkinter as tk
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import ExtractionResult, RobotBackup, WorkspaceResult
from services.orchestrator import ExtractionOrchestrator
from utils.worker import BackgroundWorker

logger = logging.getLogger(__name__)

_LOG_LEVELS: dict[str, int] = {
    "info":    logging.INFO,
    "success": logging.INFO,
    "warning": logging.WARNING,
    "error":   logging.ERROR,
}


class AppViewModel:
    """État global de l'application + commandes déclenchables par l'UI."""

    def __init__(self, settings: Settings) -> None:
        self.settings      = settings
        self._orchestrator = ExtractionOrchestrator(settings)
        self._worker       = BackgroundWorker()

        # Callbacks — branchés par App._bind_viewmodel (attributs d'instance)
        self.on_status_change:   Callable[[str], None] | None             = None
        self.on_progress_change: Callable[[int, int], None] | None        = None
        self.on_log_message:     Callable[[str, str], None] | None        = None
        self.on_scope_change:    Callable[[str], None] | None             = None
        self.on_workspace_ready: Callable[[WorkspaceResult], None] | None = None
        self.on_backup_loaded:   Callable[[RobotBackup], None] | None     = None

        # État observable
        self.input_dir:     Path | None             = None
        self.output_dir:    Path | None             = None
        self.last_result:   ExtractionResult | None = None
        self.is_busy:       bool                    = False
        self.workspace:     WorkspaceResult | None  = None
        self.active_backup: RobotBackup | None      = None

        # Référence Tkinter root — injectée par App
        self._tk_root: tk.Tk | None = None

        # Compteur de génération de polling (voir docstring module)
        self._poll_generation: int = 0

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def set_tk_root(self, root: tk.Tk) -> None:
        self._tk_root = root

    # ------------------------------------------------------------------
    # Commandes publiques
    # ------------------------------------------------------------------

    def set_input_dir(self, path: str) -> None:
        self.input_dir               = Path(path)
        self.settings.last_input_dir = path
        self._emit_status(f"Dossier source : {path}")

    def set_output_dir(self, path: str) -> None:
        self.output_dir               = Path(path)
        self.settings.last_output_dir = path

    def set_scope_filter(self, scope: str) -> None:
        if self.on_scope_change:
            self.on_scope_change(scope)

    def scan_workspace(self, path: str) -> None:
        """Scanne un dossier racine en arrière-plan puis charge tous les backups."""
        root = Path(path)
        if not root.is_dir():
            self._emit_log("Dossier invalide.", "error")
            return
        if self._worker.is_running:
            self._emit_log("Un chargement est déjà en cours.", "warning")
            return

        self.settings.last_input_dir = path
        self.is_busy = True
        self._emit_status("Scan du workspace…")
        self._emit_log(f"Scan de : {root.name}", "info")

        self._worker.run(
            self._orchestrator.scan_workspace,
            args=(root,),
            on_done=self._on_workspace_scanned,
            on_error=self._on_extraction_error,
        )
        self._start_poll()

    def load_backup(self, backup: RobotBackup) -> None:
        """Parse un backup individuel en arrière-plan (clic manuel)."""
        if self._worker.is_running:
            self._emit_log("Un chargement est déjà en cours.", "warning")
            return

        self.active_backup = backup
        self.is_busy       = True
        self._emit_status(f"Chargement de {backup.name}…")
        self._emit_log(f"Parsing : {backup.path}", "info")

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={"progress_cb": self._on_progress},
            on_done=self._on_backup_loaded,
            on_error=self._on_extraction_error,
            on_progress=self._on_progress,
        )
        self._start_poll()

    def start_extraction(self) -> None:
        """Lance l'extraction directe (sans Roboguide) en arrière-plan."""
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
                "skip_conversion": True,
            },
            on_done=self._on_extraction_done,
            on_error=self._on_extraction_error,
            on_progress=self._on_progress,
        )
        self._start_poll()

    def export_results(self, output_path: Path, fmt: str = "csv") -> None:
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
    # Polling — compteur de génération
    # ------------------------------------------------------------------

    def _start_poll(self) -> None:
        """Démarre une nouvelle boucle de polling pour le run courant.

        Incrémente ``_poll_generation`` ; la closure ``_poll`` capture cette
        valeur et s'interrompt dès qu'elle est périmée (génération différente).
        Plusieurs boucles peuvent coexister sans conflit — les anciennes
        s'arrêtent d'elles-mêmes au prochain tick une fois leur génération
        dépassée.
        """
        self._poll_generation += 1
        gen = self._poll_generation

        if self._tk_root:
            # Différé de 100 ms : laisse le thread worker démarrer
            self._tk_root.after(100, lambda: self._poll(gen))
        else:
            # Mode test (pas de boucle Tkinter) — polling synchrone
            self._poll(gen)

    def _poll(self, generation: int) -> None:
        """Interroge la queue toutes les 100 ms.

        S'arrête si :
        - La génération est périmée (un run plus récent a démarré).
        - Le worker a terminé (``poll_result()`` retourne ``True``).
        """
        if generation != self._poll_generation:
            return  # boucle zombie — un run plus récent a pris le relais

        finished = self._worker.poll_result()
        if finished:
            return  # worker terminé, pas de reschedule

        if self._tk_root:
            self._tk_root.after(100, lambda: self._poll(generation))
        # Pas de tk_root : arrêt (mode test)

    # ------------------------------------------------------------------
    # Callbacks internes — scan workspace
    # ------------------------------------------------------------------

    def _on_workspace_scanned(self, workspace: WorkspaceResult) -> None:
        """Appelé depuis le thread Tkinter quand scan_workspace se termine."""
        self.workspace = workspace
        self.is_busy   = False
        n = workspace.robot_count
        self._emit_status(f"Workspace scanné — {n} backup(s) trouvé(s)")
        self._emit_log(f"Workspace : {n} robot(s) dans {workspace.root_path.name}", "info")

        if self.on_workspace_ready:
            self.on_workspace_ready(workspace)

        if workspace.backups:
            self._load_all_backups(workspace)

    def _load_all_backups(self, workspace: WorkspaceResult) -> None:
        """Charge tous les backups séquentiellement.

        Appelé initialement depuis ``_on_workspace_scanned``, puis de façon
        récursive depuis ``_on_done`` jusqu'à épuisement des backups en attente.
        Chaque appel démarre un nouveau run du worker + une nouvelle génération
        de polling — les boucles précédentes s'arrêtent automatiquement.
        """
        pending = [b for b in workspace.backups if not b.loaded]
        if not pending:
            total = sum(b.var_count for b in workspace.backups)
            self._emit_status(f"Workspace chargé — {total} variable(s) au total")
            return

        backup = pending[0]

        if self._worker.is_running:
            # Worker encore occupé (cas rare) : réessayer dans 200 ms
            if self._tk_root:
                self._tk_root.after(200, lambda: self._load_all_backups(workspace))
            return

        self.is_busy = True
        self._emit_status(f"Chargement {backup.name}… ({len(pending)} restant(s))")

        def _on_done(b: RobotBackup) -> None:
            self.is_busy = False
            msg = f"{b.name} — {b.var_count} variable(s), {b.field_count} field(s)"
            self._emit_status(msg)
            self._emit_log(msg, "success")
            for err in b.errors:
                self._emit_log(f"⚠ {err}", "warning")
            self.last_result = ExtractionResult(
                input_dir=b.path,
                variables=b.variables,
                errors=b.errors,
            )
            if self.on_backup_loaded:
                self.on_backup_loaded(b)
            # Enchaîner : _load_all_backups → _worker.run → _start_poll
            # _start_poll incrémente _poll_generation → la boucle courante
            # (_poll appelée depuis poll_result) s'arrêtera au prochain tick
            # car sa génération sera périmée.
            self._load_all_backups(workspace)

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={"progress_cb": self._on_progress},
            on_done=_on_done,
            on_error=self._on_extraction_error,
            on_progress=self._on_progress,
        )
        self._start_poll()  # incrémente _poll_generation, démarre une nouvelle boucle

    # ------------------------------------------------------------------
    # Callbacks internes — résultats
    # ------------------------------------------------------------------

    def _on_backup_loaded(self, backup: RobotBackup) -> None:
        """Appelé quand le parsing d'un backup individuel (clic manuel) est terminé."""
        self.is_busy = False
        msg = f"{backup.name} — {backup.var_count} variable(s), {backup.field_count} field(s)"
        self._emit_status(msg)
        self._emit_log(msg, "success")
        for err in backup.errors:
            self._emit_log(f"⚠ {err}", "warning")
        self.last_result = ExtractionResult(
            input_dir=backup.path,
            variables=backup.variables,
            errors=backup.errors,
        )
        if self.on_backup_loaded:
            self.on_backup_loaded(backup)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        """Notification de progression — toujours invoquée depuis le thread Tkinter."""
        if self.on_progress_change:
            self.on_progress_change(current, total)
        if message:
            self._emit_log(message, "info")

    def _on_extraction_done(self, result: ExtractionResult) -> None:
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
        self.is_busy = False
        msg = f"Erreur inattendue : {exc}"
        self._emit_status(msg)
        self._emit_log(msg, "error")
        logger.exception("Erreur extraction", exc_info=exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_status(self, msg: str) -> None:
        if self.on_status_change:
            self.on_status_change(msg)

    def _emit_log(self, msg: str, level: str = "info") -> None:
        logger.log(_LOG_LEVELS.get(level, logging.INFO), msg)
        if self.on_log_message:
            self.on_log_message(msg, level)