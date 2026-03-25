"""
Configuration centralisée de l'application.

Corrections appliquées
──────────────────────
- ``save()`` utilise désormais ``dataclasses.asdict()`` au lieu de ``self.__dict__``.
  Cela garantit que seuls les champs déclarés dans la dataclass sont sérialisés,
  sans risque d'inclure des attributs privés ajoutés dynamiquement.
"""

from __future__ import annotations
import dataclasses
import logging
from dataclasses import dataclass, field
from pathlib import Path
import json

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
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
                return cls(**valid)
            except Exception as exc:
                logger.warning(
                    "Impossible de charger la configuration (%s) — valeurs par défaut utilisées.",
                    exc,
                )
        return cls()

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(dataclasses.asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )