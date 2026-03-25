"""
services/ports.py
─────────────────
Ports (interfaces) de la couche service — définitions des contrats via PEP 544
(Protocol + runtime_checkable).

Pourquoi des Protocol et non des ABC ?
  - Les Protocol sont structurellement typés : toute classe possédant les bonnes
    méthodes est automatiquement compatible, sans héritage explicite.
  - Idéal pour les tests : un simple mock ou une dataclass de test satisfait le
    contrat sans hériter de la classe de production.
  - Pas d'import circulaire : les services concrets n'ont pas besoin d'importer
    ce module pour satisfaire le contrat.

Usage (injection dans l'Orchestrateur) :
  orchestrator = ExtractionOrchestrator(
      parser    = VAParser(),
      converter = VAConverter(settings),
      exporter  = VariableExporter(),
  )

Ajout d'un nouveau format :
  1. Implémenter IBackupParser dans un nouveau module parser/
  2. L'injecter dans ExtractionOrchestrator.__init__ via la liste _parsers
  3. Aucune modification de l'orchestrateur requise
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from models.fanuc_models import ExtractionResult, RobotVariable


# ---------------------------------------------------------------------------
# Type alias partagé
# ---------------------------------------------------------------------------

ProgressCallback = Callable[[int, int, str], None]
"""Callback de progression : (current: int, total: int, message: str) -> None"""


# ---------------------------------------------------------------------------
# IBackupParser — contrat des parseurs de sauvegarde
# ---------------------------------------------------------------------------

@runtime_checkable
class IBackupParser(Protocol):
    """Contrat d'un parser de sauvegarde robot.

    Chaque parser est responsable :
      - de détecter s'il peut traiter un dossier donné (``can_parse``)
      - d'extraire les variables depuis ce dossier (``parse``)
      - d'exposer son identifiant de format (``FORMAT_ID``)
    """

    FORMAT_ID: str
    """Identifiant court du format (ex: ``"va"``, ``"dataid_csv"``)."""

    def can_parse(self, path: Path) -> bool:
        """Retourne ``True`` si ce parser est capable de traiter *path*.

        :param path: dossier racine d'un backup robot.
        :raises OSError: si le dossier est inaccessible (géré en amont).
        """
        ...

    def parse(
        self,
        path: Path,
        progress_cb: ProgressCallback | None = None,
    ) -> list[RobotVariable]:
        """Parse le backup et retourne la liste des variables extraites.

        :param path:        dossier racine du backup.
        :param progress_cb: callback de progression optionnel.
        :returns: liste de ``RobotVariable`` (peut être vide).
        :raises OSError:    si un fichier est inaccessible.
        :raises ValueError: si le format du fichier est invalide.
        """
        ...


# ---------------------------------------------------------------------------
# IConverter — contrat des convertisseurs de fichiers binaires
# ---------------------------------------------------------------------------

@runtime_checkable
class IConverter(Protocol):
    """Contrat d'un convertisseur de fichiers binaires en fichiers lisibles.

    Exemple d'implémentation : ``VAConverter`` (kconvars.exe).
    """

    def convert_files(
        self,
        backup_dir: Path,
        settings: object | None = None,
        timeout: int | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> list[Path]:
        """Convertit les fichiers sources d'un dossier et retourne les fichiers produits.

        :param backup_dir:  dossier contenant les fichiers à convertir.
        :param settings:    configuration (chemin exe, timeout…).
        :param timeout:     timeout par fichier en secondes.
        :param progress_cb: callback de progression optionnel.
        :returns: chemins absolus des fichiers produits.
        :raises ConverterError:    si la conversion échoue globalement.
        :raises ExeNotFoundError:  si l'exécutable est introuvable.
        """
        ...


# ---------------------------------------------------------------------------
# IExporter — contrat de l'exporteur de résultats
# ---------------------------------------------------------------------------

@runtime_checkable
class IExporter(Protocol):
    """Contrat d'un exporteur de variables extraites.

    Supporte plusieurs formats identifiés par une chaîne (``fmt``).
    """

    def export(
        self,
        variables: list[RobotVariable],
        output_path: Path,
        fmt: str = "csv",
    ) -> None:
        """Exporte *variables* vers *output_path* dans le format *fmt*.

        :param variables:   variables à exporter.
        :param output_path: chemin de destination (créé si inexistant).
        :param fmt:         identifiant du format (ex: ``"csv"``, ``"json"``).
        :raises ExportError: si le format n'est pas supporté.
        :raises OSError:     si le fichier ne peut pas être écrit.
        """
        ...