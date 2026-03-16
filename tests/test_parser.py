"""Tests unitaires pour VAParser."""

import pytest
from pathlib import Path
from unittest.mock import patch, mock_open

from config.settings import Settings
from services.parser import VAParser
from models.fanuc_models import VariableType


SAMPLE_VA = """
R[1:"Vitesse axe X"]=150.0  ; vitesse en mm/s
PR[2:"Pos depart"]
SR[3:"Nom piece"]="PART_A"
DO[10:"Sortie convoyeur"]=ON
; Ligne commentaire ignorée
"""


@pytest.fixture
def parser() -> VAParser:
    return VAParser(Settings())


class TestVAParser:
    def test_parse_register(self, parser: VAParser, tmp_path: Path) -> None:
        va_file = tmp_path / "test.VA"
        va_file.write_text(SAMPLE_VA, encoding="utf-8")
        variables = parser.parse_file(va_file)
        types = {v.var_type for v in variables}
        assert VariableType.REGISTER in types

    def test_parse_position_register(self, parser: VAParser, tmp_path: Path) -> None:
        va_file = tmp_path / "test.VA"
        va_file.write_text(SAMPLE_VA, encoding="utf-8")
        variables = parser.parse_file(va_file)
        pr_vars = [v for v in variables if v.var_type == VariableType.POSITION_REGISTER]
        assert len(pr_vars) >= 1
        assert pr_vars[0].index == 2

    def test_missing_file_returns_empty(self, parser: VAParser) -> None:
        result = parser.parse_file(Path("/nonexistent/file.VA"))
        assert result == []

    def test_variable_name_extracted(self, parser: VAParser, tmp_path: Path) -> None:
        va_file = tmp_path / "test.VA"
        va_file.write_text(SAMPLE_VA, encoding="utf-8")
        variables = parser.parse_file(va_file)
        names = {v.name for v in variables}
        assert "Vitesse axe X" in names