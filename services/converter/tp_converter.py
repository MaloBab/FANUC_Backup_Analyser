from __future__ import annotations

import logging
from pathlib import Path

from config.settings import Settings
from services.converter.base_converter import FileConverter, ConverterError, ExeNotFoundError

logger = logging.getLogger(__name__)


class TPConverter(FileConverter):

    @classmethod
    def _get_conversion_info(cls, settings: Settings) -> tuple[str, str, int, str]:
        """Définit les paramètres uniques à PrintTP."""
        return ("printtp", ".LS", settings.printtp_timeout, "fanuc_tp_")

    @classmethod
    def _get_exe_path(cls, settings: Settings) -> Path:
        """Retourne le chemin vers PrintTP.exe.

        Raises:
            ExeNotFoundError: si aucun chemin valide n'est trouvé.
        """
        if settings.printtp_exe:
            path = Path(settings.printtp_exe)
            if path.is_file():
                logger.debug("PrintTP trouvé (settings) : %s", path)
                return path

        if settings.kconvars_exe:
            sibling = Path(settings.kconvars_exe).parent / "printtp.exe"
            if sibling.is_file():
                logger.debug("PrintTP trouvé (sibling kconvars) : %s", sibling)
                return sibling

        raise ExeNotFoundError(
            "PrintTP.exe introuvable. Renseignez son chemin dans les paramètres "
            "(Settings > Chemin PrintTP) ou vérifiez l'installation de WinOLPC."
        )

    @classmethod
    def _get_source_files(cls, backup_dir: Path) -> list[Path]:
        """Retourne tous les fichiers .TP à convertir dans le dossier backup,
        triés par nom insensible à la casse.

        Raises:
            ConverterError: si aucun fichier .TP n'est trouvé.
        """
        tp_files = sorted(
            (f for f in backup_dir.iterdir()
            if f.is_file() and f.suffix.lower() == ".tp"),
            key=lambda f: f.name.lower(),
        )
        if not tp_files:
            raise ConverterError(
                f"Aucun fichier .TP trouvé dans {backup_dir}."
            )
        
        logger.debug(
            "%d fichier(s) .TP dans %s : %s",
            len(tp_files), backup_dir,
            ", ".join(f.name for f in tp_files),
        )
        return tp_files

    @classmethod
    def has_tp_files(cls, backup_dir: Path) -> bool:
        """Retourne True si le dossier contient au moins un fichier .TP.

        Utilisé par l'orchestrateur pour décider si une conversion TP est nécessaire.
        Ne lève pas d'exception (retourne False en cas d'erreur I/O).
        """
        try:
            return any(
                f.is_file() and f.suffix.lower() == ".tp"
                for f in backup_dir.iterdir()
            )
        except OSError:
            return False