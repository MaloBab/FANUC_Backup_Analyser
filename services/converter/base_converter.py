import logging
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from config.settings import Settings
from services.parser.base_parser import ProgressCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions Personnalisées
# ---------------------------------------------------------------------------

class ConverterError(Exception):
    """Erreur de conversion non récupérable."""

class ExeNotFoundError(ConverterError):
    """Executable de conversion introuvable au chemin spécifié."""


# ---------------------------------------------------------------------------
# Classe de Base Abstraite
# ---------------------------------------------------------------------------

class FileConverter(ABC):
    SUMMARY_VERSION_LINE = 21
    DEFAULT_VERSION      = "V9.40-1"
    
    @classmethod
    def extract_version(cls, backup_dir: Path) -> str:
        summary = backup_dir / "SUMMARY.DG"
        if not summary.exists():
            return cls.DEFAULT_VERSION
        try:
            lines = summary.read_text(encoding="utf-8", errors="replace").splitlines()
            line = lines[cls.SUMMARY_VERSION_LINE]
            parts = line.split(":", 1)
            if len(parts) < 2:
                logger.debug("SUMMARY.DG ligne %d sans ':' : %r", cls.SUMMARY_VERSION_LINE, line)
                return cls.DEFAULT_VERSION
            raw = parts[1].strip()              
            p_idx = raw.find("P")
            if p_idx == -1:
                logger.debug("Version sans 'P' dans SUMMARY.DG : %r", raw)
                return cls.DEFAULT_VERSION
            version = raw[:p_idx]              
            return f"{version}-1"               
        except (IndexError, OSError) as exc:
            logger.debug("SUMMARY.DG illisible : %s", exc)
            return cls.DEFAULT_VERSION

    # --- CONTRATS (À implémenter par les enfants) ---

    @classmethod
    @abstractmethod
    def _get_conversion_info(cls, settings: Settings) -> tuple[str, str, int, str]:
        """Retourne les métadonnées spécifiques au convertisseur:
        (nom_executable, extension_cible, timeout_effectif, prefixe_dossier_temp)
        """
        pass

    @classmethod
    @abstractmethod
    def _get_exe_path(cls, settings: Settings) -> Path:
        """Retourne le chemin vers l'executable de conversion.
        
        Raises:
            ExeNotFoundError: si aucun chemin valide n'est trouvé.
        """
        pass

    @classmethod
    @abstractmethod
    def _get_source_files(cls, backup_dir: Path) -> list[Path]:
        """Retourne tous les fichiers à convertir dans le dossier backup,
        triés par nom insensible à la casse.

        Raises:
            ConverterError: si aucun fichier source n'est trouvé.
        """
        pass

    # --- HOOK OPTIONNEL ---

    @classmethod
    def _pre_conversion_hook(cls, work_dir: Path, version: str) -> None:
        """Hook optionnel exécuté dans le dossier temporaire juste avant la conversion."""
        pass

    # --- TEMPLATE METHOD (Logique métier commune) ---

    @classmethod
    def convert_files(    
        cls,
        backup_dir: Path | str,
        settings: Settings | None = None,
        timeout: int | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> list[Path]:
        """
        Convertit tous les fichiers sources d'un dossier backup en fichiers lisibles.
        Chaque fichier source produit un fichier converti dans le même dossier backup.

        Parameters
        backup_dir: Dossier contenant les fichiers d'extension sources.
        settings: Configuration de l'application.
        timeout: Timeout en secondes par fichier.
        progress_cb: Callback (current, total, message) optionnel, appelé pour chaque fichier converti.

        Returns
        list[Path] : Chemins absolus des fichiers convertis produits.

        Raises
        ------
        ExeNotFoundError
            Si l'executable de conversion est introuvable.
        ConverterError
            Si la conversion d'un fichier échoue — les fichiers déjà convertis
            avant l'erreur sont conservés sur disque.
        """
        backup_dir = Path(backup_dir).resolve()
        if not backup_dir.is_dir():
            raise ConverterError(f"Dossier backup introuvable : {backup_dir}")

        settings = settings or Settings.load()
        
        # Récupération dynamique des spécificités du convertisseur
        exe_name, target_ext, default_timeout, tmp_prefix = cls._get_conversion_info(settings)
        effective_timeout = timeout if timeout is not None else default_timeout

        exe_path     = cls._get_exe_path(settings)
        version      = cls.extract_version(backup_dir)
        source_files = cls._get_source_files(backup_dir)
        produced: list[Path] = []

        logger.info(
            "Conversion de %d fichier(s) dans '%s' (version=%s)",
            len(source_files), backup_dir.name, version,
        )

        for i, source_file in enumerate(source_files, start=1):
            output_path = backup_dir / f"{source_file.stem}{target_ext}"

            if progress_cb:
                progress_cb(i, len(source_files), f"Conversion : {source_file.name}…")

            logger.debug(
                "Conversion %d/%d : %s → %s",
                i, len(source_files), source_file.name, output_path.name,
            )

            with tempfile.TemporaryDirectory(prefix=tmp_prefix) as tmp_str:
                work_dir = Path(tmp_str)

                tmp_source = work_dir / source_file.name.lower()
                shutil.copy2(source_file, tmp_source)
                tmp_output = work_dir / f"{tmp_source.stem}{target_ext}"

                cls._pre_conversion_hook(work_dir, version)

                cmd = [
                    str(exe_path),
                    str(tmp_source),
                    str(tmp_output),
                    "/ver", version,
                ]
                logger.debug("Commande : %s", " ".join(cmd))

                try:
                    result = subprocess.run(
                        cmd,
                        cwd=str(work_dir),
                        capture_output=True,
                        text=True,
                        timeout=effective_timeout,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise ConverterError(
                        f"{exe_name} a dépassé le timeout ({effective_timeout}s) "
                        f"sur '{source_file.name}'."
                    ) from exc
                except FileNotFoundError as exc:
                    raise ExeNotFoundError(
                        f"Impossible de lancer {exe_name} : {exe_path}"
                    ) from exc

                if result.returncode != 0:
                    stderr = result.stderr.strip() or result.stdout.strip()
                    logger.warning(
                        "%s a échoué (code %d) sur '%s' — fichier ignoré : %s",
                        exe_name, result.returncode, source_file.name, stderr,
                    )
                    continue

                if not tmp_output.exists():
                    logger.warning(
                        "%s s'est terminé sans erreur mais le %s est absent "
                        "pour '%s' — fichier ignoré.",
                        exe_name, target_ext, source_file.name,
                    )
                    continue

                shutil.move(str(tmp_output), str(output_path))
                produced.append(output_path)
                logger.info("Produit : %s", output_path)

        if not produced:
            raise ConverterError(
                f"Aucun fichier {target_ext} produit dans '{backup_dir.name}' — "
                f"tous les fichiers source ont échoué à la conversion."
            )

        logger.info(
            "%d/%d fichier(s) convertis avec succès dans '%s'.",
            len(produced), len(source_files), backup_dir.name,
        )
        return produced