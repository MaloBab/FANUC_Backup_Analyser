"""
ViewModel — couche intermédiaire entre l'UI et les services.
Porte l'état de l'application et expose des commandes.
Pattern MVVM : l'UI ne connaît que le ViewModel, jamais les services directement.

Recherche globale
─────────────────
``search(text, scope)`` est appelé à chaque frappe dans la FiltersBar.
Si un workspace est chargé, la recherche est lancée en arrière-plan via un
BackgroundWorker dédié (distinct du worker d'extraction).
Quand les résultats arrivent, ``on_search_results(SearchResults)`` est déclenché
dans le thread Tkinter.

Si le texte est vide, ``on_search_results`` est appelé avec un résultat vide
afin que le MainPanel revienne à la vue précédente.

Corrections appliquées
──────────────────────
1. **Injection de ``tk_root`` dans le constructeur** — l'ancien ``set_tk_root()``
   post-construction accédait à ``tk._default_root`` comme fallback (attribut
   privé CPython, non documenté, susceptible de changer ou d'être absent).
   ``tk_root`` est désormais un paramètre optionnel du constructeur avec valeur
   par défaut ``None``.

   Migration :
     Avant : vm = AppViewModel(settings)
             vm.set_tk_root(root)
     Après : vm = AppViewModel(settings, tk_root=root)

   Rétrocompat : ``set_tk_root()`` est conservé pour permettre une migration
   progressive si d'autres sites d'appel existent.

2. **Debounce de la recherche** (150 ms) — chaque frappe annule le timer
   précédent et planifie un nouveau déclenchement après 150 ms.

3. **Gestion de ``is_busy`` dans ``_load_all_backups``** — en cas d'erreur de
   conversion, ``is_busy`` restait ``True`` indéfiniment. La closure
   ``_on_error_partial`` remet ``is_busy`` à ``False`` sans bloquer les suivants.

4. **Uniformisation de la langue** — tous les messages utilisateur sont en français.
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
from services.searcher import Searcher
from utils.worker import BackgroundWorker

logger = logging.getLogger(__name__)

_LOG_LEVELS: dict[str, int] = {
    "info":    logging.INFO,
    "success": logging.INFO,
    "warning": logging.WARNING,
    "error":   logging.ERROR,
}

# Délai de debounce (ms) avant de déclencher la recherche après la dernière frappe.
_SEARCH_DEBOUNCE_MS = 150


class AppViewModel:
    """État global de l'application + commandes déclenchables par l'UI.

    :param settings: configuration applicative.
    :param tk_root:  fenêtre racine Tkinter, requise pour ``after()`` et les timers.
                     Peut être ``None`` uniquement dans les tests unitaires qui
                     n'impliquent pas de polling asynchrone.
    :param orchestrator: orchestrateur injecté (optionnel — construit par défaut).
                         Permettre l'injection facilite les tests d'intégration.
    """

    def __init__(
        self,
        settings: Settings,
        tk_root: tk.Misc | None = None,
        orchestrator: ExtractionOrchestrator | None = None,
    ) -> None:
        self.settings       = settings
        self._tk_root       = tk_root          # CORRECTIF : injecté dès la construction
        self._orchestrator  = orchestrator or _build_default_orchestrator(settings)
        self._searcher      = Searcher()
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

        self._poll_generation:        int = 0
        self._search_poll_generation: int = 0

        # CORRECTIF debounce : état interne de la recherche différée
        self._pending_search:   tuple[str, str] | None = None  # (text, scope)
        self._search_timer_id:  str | None             = None  # after() id

    # ------------------------------------------------------------------
    # Injection post-construction (rétrocompatibilité)
    # ------------------------------------------------------------------

    def set_tk_root(self, root: tk.Misc) -> None:
        """Injecte la référence à la fenêtre Tkinter.

        .. deprecated::
            Préférer l'injection via le constructeur :
            ``AppViewModel(settings, tk_root=root)``.
            Cette méthode est conservée pour la compatibilité avec les sites
            d'appel existants mais sera supprimée dans une version future.
        """
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
            self._emit_log("Dossier invalide ou introuvable.", "error")
            return
        if self._worker.is_running:
            self._emit_log("Un chargement est déjà en cours.", "warning")
            return
        self.settings.last_input_dir = path
        self.is_busy = True
        self._emit_status("Analyse du workspace…")
        self._emit_log(f"Scan : {root.name}", "info")
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
    # Commande — recherche globale (avec debounce)
    # ------------------------------------------------------------------

    def search(self, text: str, scope: str) -> None:
        """Planifie une recherche sur tous les backups chargés avec debounce.

        Appelé à chaque frappe dans la FiltersBar.

        Chaque appel annule le timer précédent et replanifie. La recherche
        n'est effectivement lancée qu'après ``_SEARCH_DEBOUNCE_MS`` ms
        d'inactivité. Le texte utilisé est toujours le texte courant au moment
        du déclenchement (pas celui de la frappe qui a armé le timer).

        Si le texte est vide, notifie immédiatement ``on_search_results``
        avec un résultat vide pour que le MainPanel revienne à la vue normale.
        """
        if self._search_timer_id is not None and self._tk_root is not None:
            self._tk_root.after_cancel(self._search_timer_id)
            self._search_timer_id = None

        if not text.strip():
            self._pending_search = None
            if self.on_search_results:
                self.on_search_results(SearchResults(query=SearchQuery(text="")))
            return

        self._pending_search = (text.strip(), scope)
        if self._tk_root is not None:
            self._search_timer_id = self._tk_root.after(
                _SEARCH_DEBOUNCE_MS,
                self._fire_search,
            )
        else:
            # Pas de root Tkinter (tests unitaires) → déclenchement immédiat
            self._fire_search()

    def _fire_search(self) -> None:
        """Déclenche effectivement la recherche avec le texte courant.

        Appelé par ``after()`` après le délai de debounce.
        Si le worker est encore occupé, replanifie automatiquement.
        """
        self._search_timer_id = None

        if self._pending_search is None:
            return

        text, scope = self._pending_search

        loaded = [b for b in self.workspace.backups if b.loaded] if self.workspace else []
        if not loaded or not text:
            self._pending_search = None
            return

        if self._search_worker.is_running:
            if self._tk_root is not None:
                self._search_timer_id = self._tk_root.after(
                    100,
                    self._fire_search,
                )
            return

        self._pending_search = None
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
            # CORRECTIF : utilise self._tk_root (injecté) — plus de tk._default_root
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
    # Polling — worker de recherche
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
        self._emit_status(f"Workspace analysé — {n} backup(s) trouvé(s)")
        self._emit_log(f"Workspace : {n} robot(s) dans {workspace.root_path.name}", "info")
        if self.on_workspace_ready:
            self.on_workspace_ready(workspace)
        if workspace.backups:
            self._load_all_backups(workspace)

    def _load_all_backups(self, workspace: WorkspaceResult) -> None:
        """Charge les backups du workspace séquentiellement.

        En cas d'erreur sur un backup, ``_on_error_partial`` remet ``is_busy``
        à ``False``, logue l'erreur et continue avec le backup suivant.
        """
        pending = [b for b in workspace.backups if not b.loaded]
        if not pending:
            total = sum(b.var_count for b in workspace.backups)
            self._emit_status(f"Workspace chargé — {total} variable(s)")
            return

        backup = pending[0]

        if self._worker.is_running:
            if self._tk_root:
                self._tk_root.after(200, lambda: self._load_all_backups(workspace))
            return

        self.is_busy = True
        self._emit_status(f"Chargement de {backup.name}… ({len(pending)} restant(s))")

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

        def _on_error_partial(exc: Exception) -> None:
            # CORRECTIF is_busy : on remet is_busy à False et on continue —
            # l'erreur sur ce backup ne doit pas geler le chargement des suivants.
            self.is_busy = False
            msg = f"Erreur lors du chargement de '{backup.name}' : {exc}"
            self._emit_status(msg)
            self._emit_log(msg, "error")
            logger.exception("Erreur chargement backup", exc_info=exc)
            backup.loaded = True
            backup.errors.append(str(exc))
            if self.on_backup_loaded:
                self.on_backup_loaded(backup)
            self._load_all_backups(workspace)

        self._worker.run(
            self._orchestrator.load_backup,
            args=(backup,),
            kwargs={"progress_cb": self._on_progress},
            on_done=_on_done,
            on_error=_on_error_partial,
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


# ---------------------------------------------------------------------------
# Factory par défaut (wiring de production)
# ---------------------------------------------------------------------------

def _build_default_orchestrator(settings: Settings) -> ExtractionOrchestrator:
    """Construit l'orchestrateur avec les dépendances concrètes de production.

    Ce wiring est centralisé ici pour que le ViewModel reste indépendant des
    classes concrètes. Les tests peuvent injecter leurs propres mocks via le
    paramètre ``orchestrator`` du constructeur de ``AppViewModel``.
    """
    # Import local pour éviter les imports circulaires et garder la couche
    # ViewModel indépendante des implémentations concrètes.
    from services.converter.vr_sv_converter import VAConverter
    from services.exporter import VariableExporter
    from services.parser.dataid_csv_parser import DataIdCsvParser
    from services.parser.va_parser import VAParser

    return ExtractionOrchestrator(
        parsers   = [DataIdCsvParser(), VAParser()],
        converter = VAConverter,
        exporter  = VariableExporter(),
        settings  = settings,
    )