"""
Worker thread générique pour exécuter des tâches longues
sans bloquer la boucle Tkinter.
Pattern : Command + Observer

Corrections appliquées
──────────────────────
1. Race condition callbacks : ``_on_done``/``_on_error`` sont assignés AVANT
   ``thread.start()`` pour éviter qu'un thread instantané enfile son résultat
   avant que les callbacks ne soient enregistrés.

2. Thread-safety du ``progress_cb`` : les notifications de progression transitent
   par la même queue FIFO que ``done``/``error``. ``on_progress`` est toujours
   invoqué depuis le thread Tkinter via ``poll_result()``.

3. ``poll_result`` gère les trois types de messages (``done``, ``error``,
   ``progress``) et ne termine le polling que sur ``done``/``error``.

4. Purge de la queue au début de ``run()`` : élimine tout résidu d'un run
   précédent (typiquement un ``done`` arrivé entre la fin du thread et le
   prochain appel à ``run()``). Sans cette purge, le ``_on_done`` du nouveau
   run consomme le payload de l'ancien run, produisant des erreurs de type
   (ex: ``RobotBackup`` reçu à la place d'un ``WorkspaceResult``).
"""

from __future__ import annotations
import threading
import queue
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

# Type d'un message dans la queue interne
_QueueMsg = tuple[str, Any]   # ("done"|"error"|"progress", payload)


class BackgroundWorker:
    """Lance une fonction dans un thread séparé et remonte les résultats,
    erreurs et notifications de progression via une queue FIFO thread-safe.

    Tous les callbacks (``on_done``, ``on_error``, ``on_progress``) sont
    invoqués **depuis le thread Tkinter** à chaque appel de ``poll_result()``.

    Usage ::

        worker = BackgroundWorker()
        worker.run(
            my_func,
            args=(a, b),
            on_done=handle_result,
            on_error=handle_error,
            on_progress=handle_progress,   # (current, total, message)
        )
        # Dans la boucle Tkinter — appelé via after() :
        finished = worker.poll_result()
    """

    def __init__(self) -> None:
        self._queue:       queue.Queue[_QueueMsg]             = queue.Queue()
        self._thread:      threading.Thread | None            = None
        self._on_done:     Callable[[Any], None] | None       = None
        self._on_error:    Callable[[Exception], None] | None = None
        self._on_progress: Callable[[int, int, str], None] | None = None

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """``True`` si un thread est actuellement en cours d'exécution."""
        return self._thread is not None and self._thread.is_alive()

    def run(
        self,
        func: Callable[..., _T],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        on_done: Callable[[_T], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Lance ``func(*args, **kwargs)`` dans un thread daemon.

        :param func:        fonction à exécuter en arrière-plan.
        :param args:        arguments positionnels.
        :param kwargs:      arguments nommés. Si la clé ``"progress_cb"`` est
                            présente **et** que ``on_progress`` est fourni, le
                            worker substitue automatiquement un proxy thread-safe
                            afin que les notifications transitent par la queue.
        :param on_done:     appelé avec le résultat quand ``func`` se termine
                            (depuis le thread Tkinter).
        :param on_error:    appelé avec l'exception si ``func`` lève
                            (depuis le thread Tkinter).
        :param on_progress: appelé avec ``(current, total, message)`` à chaque
                            notification de progression (thread Tkinter uniquement).
        :raises RuntimeError: si un worker est déjà en cours.
        """
        if self.is_running:
            raise RuntimeError("Un worker est déjà en cours d'exécution.")

        kwargs = dict(kwargs) if kwargs else {}

        _drain(self._queue)

        self._on_done     = on_done
        self._on_error    = on_error
        self._on_progress = on_progress

        if on_progress is not None and "progress_cb" in kwargs:
            def _progress_proxy(cur: int, tot: int, msg: str) -> None:
                self._queue.put(("progress", (cur, tot, msg)))
            kwargs["progress_cb"] = _progress_proxy

        def _target() -> None:
            try:
                result = func(*args, **kwargs)
                self._queue.put(("done", result))
            except Exception as exc:
                self._queue.put(("error", exc))

        self._thread = threading.Thread(target=_target, daemon=True)
        self._thread.start()

    def poll_result(self) -> bool:
        """Interroge la queue depuis le thread Tkinter (via ``after()``).

        Dépile **tous** les messages disponibles à cet instant en une seule
        passe pour éviter l'accumulation de notifications de progression.
        S'arrête dès qu'un message ``done`` ou ``error`` est rencontré.

        :returns: ``True`` si le worker a terminé (``done`` ou ``error``),
                  ``False`` s'il est encore en cours (ou si la queue est vide).
        """
        finished = False
        while True:
            try:
                status, payload = self._queue.get_nowait()
            except queue.Empty:
                break

            if status == "done":
                if self._on_done:
                    self._on_done(payload)
                finished = True
                break   # plus rien à traiter après une fin normale

            elif status == "error":
                if self._on_error:
                    self._on_error(payload)
                finished = True
                break

            elif status == "progress":
                if self._on_progress:
                    cur, tot, msg = payload
                    self._on_progress(cur, tot, msg)
                # continuer à dépiler — il peut rester d'autres messages

        return finished


# ---------------------------------------------------------------------------
# Helper module-level
# ---------------------------------------------------------------------------

def _drain(q: "queue.Queue[Any]") -> None:
    """Vide une queue sans bloquer."""
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            break