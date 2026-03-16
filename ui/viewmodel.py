"""
ViewModel — couche intermédiaire entre l'UI et les services.
Porte l'état de l'application et expose des commandes.
Pattern MVVM : l'UI ne connaît que le ViewModel, jamais les services directement.
"""

from __future__ import annotations
import logging
import threading
from pathlib import Path
from typing import Callable

from config.settings import Settings
from models.fanuc_models import ExtractionResult
from services.orchestrator import ExtractionOrchestrator
from utils.worker import BackgroundWorker

logger = logging.getLogger(__name__)


class AppViewModel:
    """
    État global de l'application + commandes déclenchables par l'UI.
    Les callbacks on_* sont branchés par App après instanciation.
    """

    # Callbacks — branchés par App._bind_viewmodel
    on_status_change:   Callable[[str], None] | None = None
    on_progress_change: Callable[[int, int], None] | None = None
    on_results_ready:   Callable[[ExtractionResult], None] | None = None
    on_log_message:     Callable[[str, str], None] | None = None  # (msg, level)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._orchestrator = ExtractionOrchestrator(settings)
        self._worker = BackgroundWorker()

        # État observable
        self.input_dir: Path | None = None
        self.output_dir: Path | None = None
        self.last_result: ExtractionResult | None = None
        self.is_busy: bool = False

    # ------------------------------------------------------------------
    # Commandes (appelées par l'UI)
    # ------------------------------------------------------------------

    def set_input_dir(self, path: str) -> None:
        self.input_dir = Path(path)
        self.settings.last_input_dir = path
        self._emit_status(f"Dossier source : {path}")

    def set_output_dir(self, path: str) -> None:
        self.output_dir = Path(path)
        self.settings.last_output_dir = path

    def start_extraction(self) -> None:
        if self._worker.is_running:
            self._emit_log("Une extraction est déjà en cours.", "warning")
            return
        if not self.input_dir:
            self._emit_log("Aucun dossier source sélectionné.", "error")
            return

        self.is_busy = True
        self._emit_status("Extraction en cours…")

        self._worker.run(
            self._orchestrator.run,
            args=(self.input_dir,),
            kwargs={"output_dir": self.output_dir, "progress_cb": self._on_progress},
            on_done=self._on_extraction_done,
            on_error=self._on_extraction_error,
        )

        # Poll toutes les 100 ms depuis le thread principal Tkinter
        self._poll()

    def export_results(self, output_path: Path, fmt: str = "csv") -> None:
        if not self.last_result:
            self._emit_log("Aucun résultat à exporter.", "warning")
            return
        try:
            self._orchestrator.export(self.last_result, output_path, fmt)
            self._emit_log(f"Export {fmt.upper()} → {output_path}", "info")
        except Exception as exc:
            self._emit_log(f"Erreur export : {exc}", "error")

    def cancel(self) -> None:
        # TODO: implémenter l'annulation propre du subprocess Roboguide
        self._emit_log("Annulation non encore implémentée.", "warning")

    # ------------------------------------------------------------------
    # Privé
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """Appelé régulièrement via after() pour récupérer le résultat du worker."""
        import tkinter as tk
        finished = self._worker.poll_result()
        if not finished:
            # Reschedule — nécessite un widget Tk ; on utilise un appel global
            tk._default_root.after(100, self._poll)  # type: ignore[attr-defined]

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self._emit_log(message, "info")
        if self.on_progress_change:
            self.on_progress_change(current, total)

    def _on_extraction_done(self, result: ExtractionResult) -> None:
        self.last_result = result
        self.is_busy = False
        msg = (
            f"Extraction terminée — {result.var_count} variable(s), "
            f"{result.field_count} field(s)."
        )
        self._emit_status(msg)
        self._emit_log(msg, "success")
        if self.on_results_ready:
            self.on_results_ready(result)

    def _on_extraction_error(self, exc: Exception) -> None:
        self.is_busy = False
        self._emit_status("Erreur lors de l'extraction.")
        self._emit_log(f"Erreur critique : {exc}", "error")
        logger.exception("Erreur extraction", exc_info=exc)

    def _emit_status(self, msg: str) -> None:
        if self.on_status_change:
            self.on_status_change(msg)

    def _emit_log(self, msg: str, level: str = "info") -> None:
        if self.on_log_message:
            self.on_log_message(msg, level)