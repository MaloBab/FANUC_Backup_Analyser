"""
main.py
───────
Point d'entrée de l'application.

Le wiring des dépendances concrètes (parsers, converter, exporter) est effectué
ici, en dehors du ViewModel et de l'Orchestrateur, conformément au principe
de Dependency Inversion (SOLID « D »).

Si ``settings.load()`` lève une ``OSError`` (fichier de config illisible —
permissions, disque inaccessible…), l'application ne peut pas démarrer
proprement. L'erreur est loguée avant propagation pour conserver la trace.
"""

import logging
import tkinter as tk

from config.settings import Settings
from ui.app import App
from utils.logger import setup_logger


def main() -> None:
    setup_logger()
    logger = logging.getLogger(__name__)

    try:
        settings = Settings.load()
    except OSError as exc:
        logger.critical(
            "Impossible de charger la configuration — démarrage annulé : %s", exc
        )
        raise SystemExit(1) from exc

    root = tk.Tk()
    App(root, settings)
    root.mainloop()


if __name__ == "__main__":
    main()