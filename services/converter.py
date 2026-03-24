"""
services/converter.py
─────────────────────
Conversion d'un fichier de sauvegarde FANUC binaire en fichier .VA
via l'outil externe ``kconvars.exe`` (fourni avec WinOLPC / Roboguide).

Reproduit fidèlement la logique du script batch d'origine, avec :
  - détection automatique de kconvars.exe
  - extraction de la version depuis SUMMARY.DG
  - exécution dans un dossier temporaire isolé (pas de pollution du backup)
  - gestion d'erreurs et logs structurés
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from config.settings import Settings
from services.parser.base_parser import ProgressCallback

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chemins d'installation Roboguide connus
# ---------------------------------------------------------------------------
_KCONVARS_CANDIDATES: list[Path] = [
    Path(r"C:\Program Files (x86)\FANUC\WinOLPC\bin\kconvars.exe"),
    Path(r"C:\Program Files\FANUC\WinOLPC\bin\kconvars.exe"),
    Path(r"C:\Program Files (x86)\FANUC\Roboguide\bin\kconvars.exe"),
    Path(r"C:\Program Files\FANUC\Roboguide\bin\kconvars.exe"),
]

# Ligne 0-indexée lue dans SUMMARY.DG (ligne 22 dans le .bat = index 21)
_SUMMARY_VERSION_LINE = 21
_DEFAULT_VERSION = "V9.10-1"


# ---------------------------------------------------------------------------
# Exceptions publiques
# ---------------------------------------------------------------------------

class ConverterError(Exception):
    """Erreur de conversion non récupérable."""


class KconvarsNotFoundError(ConverterError):
    """kconvars.exe introuvable sur ce poste."""


# ---------------------------------------------------------------------------
# Fonctions internes
# ---------------------------------------------------------------------------

def _find_kconvars(settings: Settings) -> Path:
    """Retourne le chemin vers kconvars.exe.

    Priorité :
    1. ``settings.kconvars_exe`` si renseigné et existant
    2. Chemins d'installation Roboguide connus
    3. PATH système (shutil.which)

    Raises:
        KconvarsNotFoundError: si aucun chemin ne donne un exécutable valide.
    """
    candidates: list[Path] = []

    if settings.kconvars_exe:
        candidates.append(Path(settings.kconvars_exe))

    candidates.extend(_KCONVARS_CANDIDATES)

    for path in candidates:
        if path.is_file():
            logger.debug("kconvars trouvé : %s", path)
            return path

    # Dernière chance : PATH système
    found = shutil.which("kconvars")
    if found:
        logger.debug("kconvars trouvé dans PATH : %s", found)
        return Path(found)

    raise KconvarsNotFoundError(
        "kconvars.exe introuvable. Renseignez son chemin dans les paramètres "
        "(Settings > Chemin Roboguide)."
    )


def _extract_version(backup_dir: Path) -> str:
    """Lit SUMMARY.DG et extrait la version logicielle du backup.

    Exemple de ligne source (index 21) :
        ``Software Edition No.: V9.40P/55``

    Transformation :
        split(':')[1]  → " V9.40P/55"
        strip()        → "V9.40P/55"
        split('P')[0]  → "V9.40"
        + "-1"         → "V9.40-1"  ← correspond aux versions listées par kconvars /ver

    Retourne ``_DEFAULT_VERSION`` si SUMMARY.DG est absent ou illisible.
    """
    summary = backup_dir / "SUMMARY.DG"
    if not summary.exists():
        logger.warning(
            "SUMMARY.DG absent dans %s — version par défaut utilisée (%s).",
            backup_dir,
            _DEFAULT_VERSION,
        )
        return _DEFAULT_VERSION

    try:
        lines = summary.read_text(encoding="utf-8", errors="replace").splitlines()
        raw = lines[_SUMMARY_VERSION_LINE]            # ex: "Software Edition No.: V9.40P/55"
        version = raw.split(":")[1].strip()           # → "V9.40P/55"
        version = version.split("P")[0]               # → "V9.40"
        result = f"{version}-1"                       # → "V9.40-1"
        logger.debug("Version extraite de SUMMARY.DG : %s (ligne brute : %r)", result, raw)
        return result
    except Exception as exc:
        logger.warning(
            "Impossible de lire la version dans SUMMARY.DG (%s) — "
            "version par défaut utilisée (%s).",
            exc,
            _DEFAULT_VERSION,
        )
        return _DEFAULT_VERSION


def _write_robot_ini(work_dir: Path, version: str) -> None:
    """Écrit robot.ini dans le dossier de travail temporaire.

    Requis par kconvars même quand /ver est passé en ligne de commande —
    sans ce fichier kconvars émet un notice et peut retourner un code non nul
    même en cas de succès.
    """
    content = "[WinOLPC_Util]\nRobot=\\\n" f"Version={version}\n"
    ini_path = work_dir / "robot.ini"
    ini_path.write_text(content, encoding="utf-8")
    logger.debug("robot.ini écrit dans %s :\n%s", work_dir, content.strip())


def _find_source_files(backup_dir: Path) -> list[Path]:
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


# ---------------------------------------------------------------------------
# Interface publique
# ---------------------------------------------------------------------------

def convert_backup(
    backup_dir: Path | str,
    settings: Settings | None = None,
    timeout: int | None = None,
    progress_cb: ProgressCallback | None = None,
) -> list[Path]:
    """Convertit tous les fichiers .SV et .VR d'un dossier backup en fichiers ``.VA``.

    Chaque fichier source ``<nom>.<sv|vr>`` produit un ``<nom>.VA`` dans le
    même dossier backup.

    Parameters
    ----------
    backup_dir:
        Dossier contenant les fichiers .SV/.VR (et optionnellement SUMMARY.DG).
    settings:
        Configuration applicative. Si ``None``, charge les valeurs par défaut.
    timeout:
        Timeout en secondes par fichier (par défaut : ``settings.kconvars_timeout``).
    progress_cb:
        Callback ``(current, total, message)`` optionnel, appelé pour chaque
        fichier converti.

    Returns
    -------
    list[Path]
        Chemins absolus des fichiers ``.VA`` produits (un par fichier source).

    Raises
    ------
    KconvarsNotFoundError
        Si kconvars.exe est introuvable.
    ConverterError
        Si la conversion d'un fichier échoue — les fichiers déjà convertis
        avant l'erreur sont conservés sur disque.
    """
    backup_dir = Path(backup_dir).resolve()
    if not backup_dir.is_dir():
        raise ConverterError(f"Dossier backup introuvable : {backup_dir}")

    settings          = settings or Settings.load()
    effective_timeout = timeout if timeout is not None else settings.kconvars_timeout

    kconvars_exe   = _find_kconvars(settings)
    version        = _extract_version(backup_dir)
    source_files   = _find_source_files(backup_dir)
    produced: list[Path] = []

    logger.info(
        "Conversion de %d fichier(s) dans '%s' (version=%s)",
        len(source_files), backup_dir.name, version,
    )

    for i, source_file in enumerate(source_files, start=1):
        output_path = backup_dir / f"{source_file.stem}.VA"

        if progress_cb:
            progress_cb(i, len(source_files), f"Conversion : {source_file.name}…")

        logger.debug("Conversion %d/%d : %s → %s", i, len(source_files),
                     source_file.name, output_path.name)

        with tempfile.TemporaryDirectory(prefix="fanuc_conv_") as tmp_str:
            work_dir = Path(tmp_str)

            # Copie source en minuscules (exigence kconvars)
            tmp_source = work_dir / source_file.name.lower()
            shutil.copy2(source_file, tmp_source)
            tmp_output = work_dir / f"{tmp_source.stem}.VA"

            # robot.ini requis par kconvars même avec /ver
            _write_robot_ini(work_dir, version)

            cmd = [
                str(kconvars_exe),
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
                    f"kconvars a dépassé le timeout ({effective_timeout}s) "
                    f"sur '{source_file.name}'."
                ) from exc
            except FileNotFoundError as exc:
                raise KconvarsNotFoundError(
                    f"Impossible de lancer kconvars : {kconvars_exe}"
                ) from exc

            if result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip()
                logger.warning(
                    "kconvars a échoué (code %d) sur '%s' — fichier ignoré : %s",
                    result.returncode, source_file.name, stderr,
                )
                continue

            if not tmp_output.exists():
                logger.warning(
                    "kconvars s'est terminé sans erreur mais le .VA est absent "
                    "pour '%s' — fichier ignoré.",
                    source_file.name,
                )
                continue

            shutil.move(str(tmp_output), str(output_path))
            produced.append(output_path)
            logger.info("Produit : %s", output_path)

    if not produced:
        raise ConverterError(
            f"Aucun fichier .VA produit dans '{backup_dir.name}' — "
            f"tous les fichiers source ont échoué à la conversion."
        )

    logger.info(
        "%d/%d fichier(s) convertis avec succès dans '%s'.",
        len(produced), len(source_files), backup_dir.name,
    )
    return produced