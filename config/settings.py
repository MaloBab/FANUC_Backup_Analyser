"""
Configuration centralisée de l'application.
Toutes les constantes et paramètres modifiables sont ici.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import json


CONFIG_FILE = Path.home() / ".fanuc_extractor" / "config.json"


@dataclass
class Settings:
    last_input_dir: str = ""
    last_output_dir: str = ""

    roboguide_exe: str = ""
    roboguide_timeout: int = 120


    window_title: str = "FANUC Variable Extractor"
    window_size: str = "1100x700"
    theme: str = "dark"

    @classmethod
    def load(cls) -> Settings:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(self.__dict__, indent=2),
            encoding="utf-8"
        )