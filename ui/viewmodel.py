"""
ViewModel — couche intermédiaire entre l'UI et les services.
Porte l'état de l'application et expose des commandes.
Pattern MVVM : l'UI ne connaît que le ViewModel, jamais les services directement.

Recherche globale
─────────────────
``search(text, scope)`` est appelé à chaque frappe dans la FiltersBar.
Si un workspace est chargé, la recherche est lancée en arrière-plan via un
``BackgroundWorker`` dédié (distinct du worker d'extraction).
Quand les résultats arrivent, ``on_search_results(SearchResults)`` est déclenché
dans le thread Tkinter.

Si le texte est vide, ``on_search_results`` est appelé avec un résultat vide
afin que le MainPanel revienne à la vue précédente.
"""

from __future__ import annotations
import logging
import tkinter as tk
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import ExtractionResult, RobotBackup, WorkspaceResult
from models.search_models import SearchQuery, SearchResults
from services.orchestrator import ExtractionOrchestrator
from services.searcher import VariableSearcher
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
        self.settings       = settings
        self._orchestrator  = ExtractionOrchestrator(settings)
        self._searcher      = VariableSearcher()
        self._worker        = BackgroundWorker()
        self._search_worker = BackgroundWorker()

        # Callbacks — branchés par App._bind_viewmodel
        self.on_status_change:   Callable[[str], None] | None              = None
        self.on_progress_change: Callable[[int, int], None] | None         = None
        self.on_log_message:     Callable[[str, str], None] | None         = None
        self.on_scope_change:    Callable[[str], None] | None              = None
        self.on_workspace_ready: Callable[[WorkspaceResult], None] | None  = None
        self.on_backup_loaded:   Callable[[RobotBackup], None] | None      = None
        self.on_search_results:  Callable[[SearchResults], None] | None    = None

        # État observable
        self.input_dir:     Path | None             = None
        self.output_dir:    Path | None             = None
        self.last_result:   ExtractionResult | None = None
        self.is_busy:       bool                    = False
        self.workspace:     WorkspaceResult | None  = None
        self.active_backup: RobotBackup | None      = None

        self._tk_root:              tk.Tk | None = None
        self._poll_generation:        int = 0
        self._search_poll_generation: int = 0

    # ------------------------------------------------------------------
    # Injection
    # ------------------------------------------------------------------

    def set_tk_root(self, root: tk.Tk) -> None:
        self._tk_root = root

    # ------------------------------------------------------------------
    # Commandes — extraction
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
            kwargs={"output_dir": self.output_dir,
                    "progress_cb": self._on_progress,
                    "skip_conversion": True},
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
    # Commande — recherche globale
    # ------------------------------------------------------------------

    def search(self, text: str, scope: str) -> None:
        """Lance une recherche sur tous les backups chargés.

        Appelé à chaque frappe dans la FiltersBar.
        Si le texte est vide, notifie ``on_search_results`` avec un résultat
        vide pour que le MainPanel revienne à la vue normale.
        """
        # Texte vide → signaler résultat vide (retour à la vue normale)
        if not text.strip():
            if self.on_search_results:
                self.on_search_results(SearchResults(query=SearchQuery(text="")))
            return

        loaded = [b for b in self.workspace.backups if b.loaded] \
            if self.workspace else []
        if not loaded:
            return

        # Si une recherche est en cours, on la laisse finir — le polling
        # redémarrera avec la prochaine frappe.
        if self._search_worker.is_running:
            return

        self._search_worker.run(
            self._searcher.search_from_text,
            args=(text, scope, loaded),
            on_done=self._on_search_done,
            on_error=self._on_search_error,
        )
        self._start_search_poll()

    # ------------------------------------------------------------------
    # Polling — worker d'extraction
    # ------------------------------------------------------------------

    def _start_poll(self) -> None:
        self._poll_generation += 1
        gen = self._poll_generation
        if self._tk_root:
            self._tk_root.after(100, lambda: self._poll(gen))
        else:
            self._poll(gen)

    def _poll(self, generation: int) -> None:
        if generation != self._poll_generation:
            return
        if self._worker.poll_result():
            return
        if self._tk_root:
            self._tk_root.after(100, lambda: self._poll(generation))

    # ------------------------------------------------------------------
    # Polling — worker de recherche (intervalle court pour la réactivité)
    # ------------------------------------------------------------------

    def _start_search_poll(self) -> None:
        self._search_poll_generation += 1
        gen = self._search_poll_generation
        if self._tk_root:
            self._tk_root.after(50, lambda: self._poll_search(gen))
        else:
            self._poll_search(gen)

    def _poll_search(self, generation: int) -> None:
        if generation != self._search_poll_generation:
            return
        if self._search_worker.poll_result():
            return
        if self._tk_root:
            self._tk_root.after(50, lambda: self._poll_search(generation))

    # ------------------------------------------------------------------
    # Callbacks — scan workspace
    # ------------------------------------------------------------------

    def _on_workspace_scanned(self, workspace: WorkspaceResult) -> None:
        self.workspace = workspace
        self.is_busy   = False
        n = workspace.robot_count
        self._emit_status(f"Workspace analysed — {n} backup(s) founded")
        self._emit_log(f"Workspace : {n} robot(s) in {workspace.root_path.name}", "info")
        if self.on_workspace_ready:
            self.on_workspace_ready(workspace)
        if workspace.backups:
            self._load_all_backups(workspace)

    def _load_all_backups(self, workspace: WorkspaceResult) -> None:
        pending = [b for b in workspace.backups if not b.loaded]
        if not pending:
            total = sum(b.var_count for b in workspace.backups)
            self._emit_status(f"Workspace loaded — {total} variable(s)")
            return
        backup = pending[0]
        if self._worker.is_running:
            if self._tk_root:
                self._tk_root.after(200, lambda: self._load_all_backups(workspace))
            return
        self.is_busy = True
        self._emit_status(f"Loading {backup.name}… ({len(pending)} remaining")

        def _on_done(b: RobotBackup) -> None:
            self.is_busy = False
            msg = f"{b.name} — {b.var_count} variable(s), {b.field_count} field(s)"
            self._emit_status(msg)
            self._emit_log(msg, "success")
            for err in b.errors:
                self._emit_log(f"⚠ {err}", "warning")
            self.last_result = ExtractionResult(
                input_dir=b.path, variables=b.variables, errors=b.errors)
            if self.on_backup_loaded:
                self.on_backup_loaded(b)
            self._load_all_backups(workspace)

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={"progress_cb": self._on_progress},
            on_done=_on_done,
            on_error=self._on_extraction_error,
            on_progress=self._on_progress,
        )
        self._start_poll()

    # ------------------------------------------------------------------
    # Callbacks — résultats extraction
    # ------------------------------------------------------------------

    def _on_backup_loaded(self, backup: RobotBackup) -> None:
        self.is_busy = False
        msg = f"{backup.name} — {backup.var_count} variable(s), {backup.field_count} field(s)"
        self._emit_status(msg)
        self._emit_log(msg, "success")
        for err in backup.errors:
            self._emit_log(f"⚠ {err}", "warning")
        self.last_result = ExtractionResult(
            input_dir=backup.path, variables=backup.variables, errors=backup.errors)
        if self.on_backup_loaded:
            self.on_backup_loaded(backup)

    def _on_progress(self, current: int, total: int, message: str) -> None:
        if self.on_progress_change:
            self.on_progress_change(current, total)
        if message:
            self._emit_log(message, "info")

    def _on_extraction_done(self, result: ExtractionResult) -> None:
        self.last_result = result
        self.is_busy     = False
        msg = (f"Extraction terminée — {result.var_count} variable(s), "
               f"{result.field_count} field(s)")
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
    # Callbacks — résultats recherche
    # ------------------------------------------------------------------

    def _on_search_done(self, results: SearchResults) -> None:
        if results.query_text:
            msg = (f"Recherche '{results.query_text}' — "
                   f"{results.hit_count} résultat(s) sur {results.searched} variable(s)")
            self._emit_status(msg)
        if self.on_search_results:
            self.on_search_results(results)

    def _on_search_error(self, exc: Exception) -> None:
        self._emit_log(f"Erreur de recherche : {exc}", "error")
        logger.exception("Erreur recherche", exc_info=exc)

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