"""
Worker thread générique pour exécuter des tâches longues
sans bloquer la boucle Tkinter.
Pattern : Command + Observer
"""

from __future__ import annotations
import threading
import queue
from typing import Callable, Any

CancelEvent = threading.Event


class BackgroundWorker:
    """
    Lance une fonction dans un thread séparé et remonte
    les résultats/erreurs via une queue thread-safe.

    Usage :
        worker = BackgroundWorker()
        worker.run(my_func, args=(a, b), on_done=handle_result, on_error=handle_error)
    """

    def __init__(self) -> None:
        self._queue:    queue.Queue             = queue.Queue()
        self._thread:   threading.Thread | None = None
        """Event partageable avec les tâches longues pour une annulation coopérative."""

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(
        self,
        func: Callable,
        args: tuple = (),
        kwargs: dict | None = None,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if self.is_running:
            raise RuntimeError("Un worker est déjà en cours d'exécution.")

        kwargs = kwargs or {}

        def _target():
            try:
                result = func(*args, **kwargs)
                self._queue.put(("done", result))
            except Exception as exc:
                self._queue.put(("error", exc))

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()

        self._on_done = on_done
        self._on_error = on_error


    def poll_result(self) -> bool:
        """À appeler périodiquement depuis la boucle Tkinter (via after()).

        :returns: ``True`` si le worker a terminé (succès ou erreur).
        """
        try:
            status, payload = self._queue.get_nowait()
            if status == "done" and self._on_done:
                self._on_done(payload)
            elif status == "error" and self._on_error:
                self._on_error(payload)
            return True
        except queue.Empty:
            return False