"""
Configuration centralisée de l'application.

Corrections appliquées
──────────────────────
1. ``save()`` utilise ``dataclasses.asdict()`` au lieu de ``self.__dict__``.
   Cela garantit que seuls les champs déclarés dans la dataclass sont sérialisés,
   sans risque d'inclure des attributs privés ajoutés dynamiquement.

2. ``load()`` distingue désormais les erreurs récupérables des erreurs fatales :
   - ``json.JSONDecodeError`` : fichier de config corrompu → log warning + valeurs
     par défaut (comportement dégradé acceptable, l'utilisateur peut reconfigurer).
   - ``OSError`` : problème de permissions ou de disque → log error + re-raise.
     Une erreur de lecture du fichier de config est considérée fatale car elle
     indique un problème système que l'utilisateur doit résoudre explicitement.
   - Tous les autres types d'exception inattendus se propagent normalement
     (ne plus les masquer silencieusement).
"""

from __future__ import annotations
import dataclasses
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


CONFIG_FILE = Path.home() / ".fanuc_extractor" / "config.json"


@dataclass
class Settings:
    last_input_dir:  str = ""
    last_output_dir: str = ""

    kconvars_exe:     str = "C:/Program Files (x86)/FANUC/WinOLPC/bin/kconvars.exe"
    kconvars_timeout: int = 120

    var_name_filter: list[str] = field(default_factory=list)

    window_title: str = "FANUC Variable Extractor"
    window_size:  str = "1200x750"
    theme:        str = "dark"

    @classmethod
    def load(cls) -> Settings:
        """Charge la configuration depuis le fichier JSON.

        Comportement selon le type d'erreur rencontré :
          - Fichier absent          → valeurs par défaut (cas nominal première utilisation)
          - ``json.JSONDecodeError`` → warning + valeurs par défaut (config corrompue,
            l'utilisateur peut reconfigurer via la UI)
          - ``OSError``              → log error + exception propagée (problème
            système : permissions, disque plein…  l'utilisateur doit intervenir)

        :returns: instance ``Settings`` peuplée ou avec valeurs par défaut.
        :raises OSError: si le fichier existe mais n'est pas lisible (permissions…).
        """
        if not CONFIG_FILE.exists():
            return cls()

        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Config corrompue — non fatale : on repart des valeurs par défaut.
            # L'utilisateur pourra reconfigurer via la UI et un nouveau fichier
            # sera écrit à la prochaine sauvegarde.
            logger.warning(
                "Fichier de configuration corrompu (%s) — valeurs par défaut utilisées. "
                "Le fichier sera écrasé à la prochaine sauvegarde : %s",
                CONFIG_FILE, exc,
            )
            return cls()
        except OSError as exc:
            # Erreur système (permissions, disque inaccessible…) — fatale.
            # On logue au niveau ERROR et on propage : l'appelant (main.py) doit
            # décider si l'application peut démarrer sans configuration.
            logger.error(
                "Impossible de lire le fichier de configuration '%s' : %s",
                CONFIG_FILE, exc,
            )
            raise

        # Filtrer les clés inconnues pour tolérer les configs d'anciennes versions
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    def save(self) -> None:
        """Persiste la configuration courante dans le fichier JSON.

        Crée le répertoire parent si nécessaire.

        :raises OSError: si le fichier ne peut pas être écrit.
        """
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )