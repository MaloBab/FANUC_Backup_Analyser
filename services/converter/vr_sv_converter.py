from __future__ import annotations

import logging
from pathlib import Path

from config.settings import Settings
from services.converter.base_converter import FileConverter, ConverterError, ExeNotFoundError

logger = logging.getLogger(__name__)


class VAConverter(FileConverter):

    @classmethod
    def _get_conversion_info(cls, settings: Settings) -> tuple[str, str, int, str]:
        """Définit les paramètres uniques à kconvars."""
        return ("kconvars", ".VA", settings.kconvars_timeout, "fanuc_conv_")

    @classmethod
    def _get_exe_path(cls, settings: Settings) -> Path:
        """Retourne le chemin vers kconvars.exe.

        Raises:
            ExeNotFoundError: si aucun chemin ne donne un exécutable valide.
        """
        # 1. Chemin explicite dans les paramètres
        if settings.kconvars_exe:
            path = Path(settings.kconvars_exe)
            if path.is_file():
                logger.debug("kconvars trouvé (settings) : %s", path)
                return path
        
        # 2. Chemin déduit depuis l'exécutable frère
        if settings.printtp_exe:
            sibling = Path(settings.printtp_exe).parent / "kconvars.exe"
            if sibling.is_file():
                logger.debug("kconvars trouvé (sibling PrintTP) : %s", sibling)
                return sibling

        raise ExeNotFoundError(
            "kconvars.exe introuvable. Renseignez son chemin dans les paramètres "
            "(Settings > Chemin Roboguide)."
        )

    @classmethod
    def _get_source_files(cls, backup_dir: Path) -> list[Path]:
        """Retourne tous les fichiers .SV et .VR à convertir dans le dossier backup,
        triés par nom insensible à la casse.

        Raises:
            ConverterError: si aucun fichier .SV ou .VR n'est trouvé.
        """
        convertible = sorted(
            (f for f in backup_dir.iterdir()
             if f.is_file() and f.suffix.lower() in {".sv", ".vr"}),
            key=lambda f: f.name.lower(),
        )
        if not convertible:
            raise ConverterError(
                f"Aucun fichier .SV ou .VR trouvé dans {backup_dir}."
            )
            
        logger.debug(
            "%d fichier(s) convertible(s) dans %s : %s",
            len(convertible), backup_dir,
            ", ".join(f.name for f in convertible),
        )
        return convertible

    @classmethod
    def _pre_conversion_hook(cls, work_dir: Path, version: str) -> None:
        """Surcharge du hook pour exécuter des actions spécifiques avant la conversion."""
        cls._write_robot_ini(work_dir, version)

    @classmethod
    def _write_robot_ini(cls, work_dir: Path, version: str) -> None:
        """Écrit robot.ini dans le dossier de travail temporaire.
        (Requis spécifiquement par kconvars.exe pour fonctionner)
        """
        content = "[WinOLPC_Util]\nRobot=\\\n" f"Version={version}\n"
        ini_path = work_dir / "robot.ini"
        ini_path.write_text(content, encoding="utf-8")
        logger.debug("robot.ini écrit dans %s :\n%s", work_dir, content.strip())