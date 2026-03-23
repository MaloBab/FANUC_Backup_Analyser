"""
Interface commune à tous les parsers de backup robot.

Chaque format de backup (fichiers .VA, DATAID.CSV, …) implémente ce protocole.
L'orchestrateur utilise ``can_parse()`` pour sélectionner automatiquement
le parser approprié à un dossier donné, sans connaissance des formats concrets.

Pattern : Strategy
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

from models.fanuc_models import RobotVariable

ProgressCallback = Callable[[int, int, str], None]


class BackupParser(ABC):
    """Protocole de parsing d'un backup robot.

    Toute implémentation concrète doit :
      1. déclarer le(s) format(s) qu'elle reconnaît via ``FORMAT_ID`` (str court,
         ex : ``"va"``, ``"dataid_csv"``).
      2. implémenter ``can_parse()`` pour détecter la présence de ses fichiers
         sources dans un dossier.
      3. implémenter ``parse()`` pour retourner la liste des variables extraites.
    """

    #: Identifiant court du format — utilisé dans ``RobotBackup.format``.
    FORMAT_ID: str = "unknown"

    @abstractmethod
    def can_parse(self, path: Path) -> bool:
        """Retourne ``True`` si ce parser sait traiter le dossier *path*.

        Doit être rapide (pas de lecture de contenu) — l'orchestrateur l'appelle
        sur chaque dossier au moment du scan.

        :param path: dossier racine d'un backup robot.
        """
        ...

    @abstractmethod
    def parse(
        self,
        path: Path,
        progress_cb: ProgressCallback | None = None,
    ) -> list[RobotVariable]:
        """Parse le backup et retourne toutes les variables extraites.

        :param path:        dossier racine du backup.
        :param progress_cb: callback ``(current, total, message)`` optionnel,
                            appelé depuis le thread worker (pas le thread Tkinter).
        :returns: liste de ``RobotVariable`` dans l'ordre d'extraction.
                  En cas d'erreur sur un fichier individuel, les erreurs sont
                  loguées et le parsing continue sur les fichiers restants.
        """
        ...