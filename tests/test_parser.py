"""Tests unitaires pour VAParser — basés sur le format .VA réel FANUC."""

from __future__ import annotations
import pytest
from pathlib import Path

from services.parser import VAParser
from models.fanuc_models import (
    RobotVariable,
    StorageType, AccessType, VADataType,
    ArrayValue, PositionValue,
    ExtractionResult,
)


# ---------------------------------------------------------------------------
# Échantillons VA représentatifs de tous les cas gérés
# ---------------------------------------------------------------------------

SAMPLE_VA = """\
[*SYSTEM*]$ACC_MAXLMT  Storage: CMOS  Access: RW  : INTEGER = 100

[*SYSTEM*]$AP_AUTOMODE  Storage: CMOS  Access: RW  : BOOLEAN = FALSE

[*SYSTEM*]$ROBOT_NAME  Storage: CMOS  Access: RW  : STRING[37] = Uninitialized

[*SYSTEM*]$APPLICATION  Storage: CMOS  Access: RO  : ARRAY[3] OF STRING[21]
     [1] = 'LR HandlingTool'
     [2] = 'V9.40P/27'
     [3] = 'Uninitialized'

[*SYSTEM*]$ANGTOL  Storage: CMOS  Access: RW  : ARRAY[4] OF REAL
     [1] = 1.000000e+00
     [2] = 2.000000e+00
     [3] = 3.000000e+00
     [4] = 4.000000e+00

[*SYSTEM*]$ALMDG  Storage: SHADOW  Access: FP  : ALMDG_T =
   Field: $ALMDG.$DEBUG1 Access: RW: INTEGER = 0
   Field: $ALMDG.$DEBUG2 Access: RW: INTEGER = 42

[*SYSTEM*]$AIO_CNV  Storage: SHADOW  Access: FP  : ARRAY[2] OF AIO_CNV_T
     Field: $AIO_CNV[1].$RACK Access: RW: INTEGER = 999
     Field: $AIO_CNV[1].$SLOT Access: RW: INTEGER = -1
     Field: $AIO_CNV[1].$DISTORT  ARRAY[2] OF REAL
      [1] = 0.000000e+00
      [2] = 1.000000e+00
     Field: $AIO_CNV[2].$RACK Access: RW: INTEGER = 0
     Field: $AIO_CNV[2].$SLOT Access: RW: INTEGER = 0
     Field: $AIO_CNV[2].$DISTORT  ARRAY[2] OF REAL
      [1] = 0.000000e+00
      [2] = 0.000000e+00

[*SYSTEM*]$PGTRACEDT  Storage: CMOS  Access: RO  : ARRAY[2,3] OF TRACEDT_T
     Field: $PGTRACEDT[1,1].$EPT_INDEX Access: RO: SHORT = 10
     Field: $PGTRACEDT[1,2].$EPT_INDEX Access: RO: SHORT = 20
     Field: $PGTRACEDT[2,1].$EPT_INDEX Access: RO: SHORT = 30
"""

SAMPLE_KAREL_VA = """\
[TBSWMD45]NFPAM  Storage: CMOS  Access: RW  : NFPAM_T =
     Field: NFPAM.TBC.CNT_SCALE  ARRAY[2] OF REAL
      [1] = 1.150000e+00
      [2] = 1.120000e+00
     Field: NFPAM.TBC.MIN_ACC_UCA  ARRAY[2] OF INTEGER
      [1] = 68
      [2] = 72
"""

SAMPLE_UNINIT_VA = """\
[*SYSTEM*]$DCS_NOCODE  Storage: CMOS  Access: FP  : DCS_NOCODE_T =

[*SYSTEM*]$PAUSE_PROG  Storage: CMOS  Access: RW  : STRING[37] = Uninitialized
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser() -> VAParser:
    return VAParser()


def _write_and_parse(parser: VAParser, tmp_path: Path, content: str) -> list[RobotVariable]:
    va_file = tmp_path / "test.VA"
    va_file.write_text(content, encoding="utf-8")
    return parser.parse_file(va_file)


@pytest.fixture
def variables(parser: VAParser, tmp_path: Path) -> list[RobotVariable]:
    return _write_and_parse(parser, tmp_path, SAMPLE_VA)


@pytest.fixture
def karel_variables(parser: VAParser, tmp_path: Path) -> list[RobotVariable]:
    return _write_and_parse(parser, tmp_path, SAMPLE_KAREL_VA)


@pytest.fixture
def uninit_variables(parser: VAParser, tmp_path: Path) -> list[RobotVariable]:
    return _write_and_parse(parser, tmp_path, SAMPLE_UNINIT_VA)


# ---------------------------------------------------------------------------
# Tests — comptage et structure générale
# ---------------------------------------------------------------------------

class TestCount:
    def test_total_system(self, variables: list[RobotVariable]) -> None:
        assert len(variables) == 8

    def test_all_system(self, variables: list[RobotVariable]) -> None:
        assert all(v.is_system for v in variables)
        assert all(v.namespace == "*SYSTEM*" for v in variables)

    def test_total_karel(self, karel_variables: list[RobotVariable]) -> None:
        assert len(karel_variables) == 1

    def test_karel_not_system(self, karel_variables: list[RobotVariable]) -> None:
        v = karel_variables[0]
        assert not v.is_system
        assert v.namespace == "TBSWMD45"
        assert v.name == "NFPAM"


# ---------------------------------------------------------------------------
# Tests — scalaires simples
# ---------------------------------------------------------------------------

class TestScalar:
    def test_integer(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ACC_MAXLMT")
        assert v.value == "100"
        assert v.data_type == VADataType.INTEGER
        assert v.storage == StorageType.CMOS
        assert v.access == AccessType.RW
        assert not v.is_array
        assert not v.fields

    def test_boolean(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$AP_AUTOMODE")
        assert v.value == "FALSE"
        assert v.data_type == VADataType.BOOLEAN

    def test_string_uninitialized(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ROBOT_NAME")
        assert v.value == "Uninitialized"
        assert v.data_type == VADataType.STRING


# ---------------------------------------------------------------------------
# Tests — tableaux primitifs
# ---------------------------------------------------------------------------

class TestArrayPrimitive:
    def test_string_array(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$APPLICATION")
        assert v.is_array
        assert v.array_size == 3
        assert isinstance(v.value, ArrayValue)
        assert v.value.items[(1,)] == "LR HandlingTool"
        assert v.value.items[(2,)] == "V9.40P/27"
        assert v.value.items[(3,)] == "Uninitialized"

    def test_real_array_values(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ANGTOL")
        assert isinstance(v.value, ArrayValue)
        assert len(v.value.items) == 4
        assert v.value.items[(1,)] == "1.000000e+00"
        assert v.value.items[(4,)] == "4.000000e+00"

    def test_array_keys_are_tuples(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ANGTOL")
        assert isinstance(v.value, ArrayValue)
        for key in v.value.items:
            assert isinstance(key, tuple)
            assert len(key) == 1


# ---------------------------------------------------------------------------
# Tests — struct simple
# ---------------------------------------------------------------------------

class TestStruct:
    def test_field_count(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ALMDG")
        assert v.is_struct
        assert len(v.fields) == 2

    def test_field_values(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ALMDG")
        debug1 = next(f for f in v.fields if "DEBUG1" in f.field_name)
        debug2 = next(f for f in v.fields if "DEBUG2" in f.field_name)
        assert debug1.value == "0"
        assert debug2.value == "42"

    def test_field_access(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ALMDG")
        for f in v.fields:
            assert f.access == AccessType.RW

    def test_field_no_parent_index(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ALMDG")
        for f in v.fields:
            assert f.parent_index_nd is None


# ---------------------------------------------------------------------------
# Tests — tableau de structs (1D)
# ---------------------------------------------------------------------------

class TestArrayOfStruct:
    def test_metadata(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$AIO_CNV")
        assert v.is_array
        assert v.array_size == 2

    def test_field_count(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$AIO_CNV")
        assert len(v.fields) == 6  # 2 éléments × 3 fields chacun

    def test_scalar_field_index(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$AIO_CNV")
        rack_fields = [f for f in v.fields if "RACK" in f.field_name]
        assert len(rack_fields) == 2
        assert rack_fields[0].parent_index_nd == (1,)
        assert rack_fields[0].value == "999"
        assert rack_fields[1].parent_index_nd == (2,)
        assert rack_fields[1].value == "0"

    def test_nested_array_field(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$AIO_CNV")
        distort = next(f for f in v.fields if "DISTORT" in f.field_name and f.parent_index_nd == (1,))
        assert isinstance(distort.value, ArrayValue)
        assert distort.value.items[(1,)] == "0.000000e+00"
        assert distort.value.items[(2,)] == "1.000000e+00"


# ---------------------------------------------------------------------------
# Tests — tableau N-D
# ---------------------------------------------------------------------------

class TestNDArray:
    def test_array_shape(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$PGTRACEDT")
        assert v.is_array
        assert v.array_size == 6   # 2 × 3
        assert v.array_shape == (2, 3)

    def test_nd_field_index(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$PGTRACEDT")
        f11 = next(f for f in v.fields if f.parent_index_nd == (1, 1))
        f12 = next(f for f in v.fields if f.parent_index_nd == (1, 2))
        f21 = next(f for f in v.fields if f.parent_index_nd == (2, 1))
        assert f11.value == "10"
        assert f12.value == "20"
        assert f21.value == "30"

    def test_nd_full_name(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$PGTRACEDT")
        f = next(f for f in v.fields if f.parent_index_nd == (1, 1))
        assert "[1,1]" in f.full_name


# ---------------------------------------------------------------------------
# Tests — Karel
# ---------------------------------------------------------------------------

class TestKarel:
    def test_field_count(self, karel_variables: list[RobotVariable]) -> None:
        v = karel_variables[0]
        assert len(v.fields) == 2

    def test_nested_array_values(self, karel_variables: list[RobotVariable]) -> None:
        v = karel_variables[0]
        cnt = next(f for f in v.fields if "CNT_SCALE" in f.field_name)
        assert isinstance(cnt.value, ArrayValue)
        assert cnt.value.items[(1,)] == "1.150000e+00"
        assert cnt.value.items[(2,)] == "1.120000e+00"

    def test_field_no_dollar(self, karel_variables: list[RobotVariable]) -> None:
        """Les fields Karel n'ont pas de $ dans le nom."""
        v = karel_variables[0]
        for f in v.fields:
            assert not f.field_name.startswith("$")


# ---------------------------------------------------------------------------
# Tests — Uninitialized
# ---------------------------------------------------------------------------

class TestUninitialized:
    def test_struct_empty_is_uninitialized(self, uninit_variables: list[RobotVariable]) -> None:
        v = next(x for x in uninit_variables if x.name == "$DCS_NOCODE")
        assert v.value == "Uninitialized"
        assert not v.fields

    def test_string_uninitialized(self, uninit_variables: list[RobotVariable]) -> None:
        v = next(x for x in uninit_variables if x.name == "$PAUSE_PROG")
        assert v.value == "Uninitialized"


# ---------------------------------------------------------------------------
# Tests — métadonnées et robustesse
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_source_file_tracked(self, variables: list[RobotVariable], tmp_path: Path) -> None:
        for v in variables:
            assert v.source_file is not None
            assert v.source_file.name == "test.VA"

    def test_line_number_tracked(self, variables: list[RobotVariable]) -> None:
        v = next(x for x in variables if x.name == "$ACC_MAXLMT")
        assert v.line_number == 1

    def test_missing_file_returns_empty(self, parser: VAParser) -> None:
        assert parser.parse_file(Path("/nonexistent/file.VA")) == []

    def test_empty_file_returns_empty(self, parser: VAParser, tmp_path: Path) -> None:
        va_file = tmp_path / "empty.VA"
        va_file.write_text("", encoding="utf-8")
        assert parser.parse_file(va_file) == []

    def test_parse_directory(self, parser: VAParser, tmp_path: Path) -> None:
        (tmp_path / "a.VA").write_text(SAMPLE_VA, encoding="utf-8")
        (tmp_path / "b.va").write_text(SAMPLE_KAREL_VA, encoding="utf-8")
        (tmp_path / "ignore.txt").write_text("not a VA file", encoding="utf-8")
        result = parser.parse_directory(tmp_path)
        assert isinstance(result, ExtractionResult)
        assert result.var_count == 9   # 8 système + 1 Karel
        assert result.errors == []

    def test_directory_case_insensitive(self, parser: VAParser, tmp_path: Path) -> None:
        """parse_directory doit trouver .va et .VA."""
        (tmp_path / "upper.VA").write_text(SAMPLE_VA, encoding="utf-8")
        (tmp_path / "lower.va").write_text(SAMPLE_KAREL_VA, encoding="utf-8")
        result = parser.parse_directory(tmp_path)
        assert result.var_count == 9


# ---------------------------------------------------------------------------
# Tests — ARRAY OF POSITION
# ---------------------------------------------------------------------------

SAMPLE_ARRAY_POS_VA = """\
[*SYSTEM*]$PLID  Storage: SHADOW  Access: FP  : PLID_T =
   Field: $PLID[1].$POS  ARRAY[3] OF POSITION
    [1] = 
  Group: 1   Config: N R D B, 0, 0, 0
  X:     1.000   Y:     2.000   Z:     3.000
  W:     4.000   P:     5.000   R:     6.000
    [2] = 
  Group: 2   Config: N R D B, 0, 0, 0
  X:    10.000   Y:    20.000   Z:    30.000
  W:    40.000   P:    50.000   R:    60.000
    [3] = 
  Group: 1   Config: N R D B, 0, 0, 0
  X:     0.000   Y:     0.000   Z:     0.000
  W:     0.000   P:     0.000   R:     0.000
   Field: $PLID[1].$COUNT Access: RW: INTEGER = 3
"""


@pytest.fixture
def array_pos_variables(parser: VAParser, tmp_path: Path) -> list[RobotVariable]:
    va_file = tmp_path / "arrpos.VA"
    va_file.write_text(SAMPLE_ARRAY_POS_VA, encoding="utf-8")
    return parser.parse_file(va_file)


class TestArrayOfPosition:
    def test_field_detected(self, array_pos_variables: list[RobotVariable]) -> None:
        v = array_pos_variables[0]
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)

    def test_items_are_position_values(self, array_pos_variables: list[RobotVariable]) -> None:
        v = array_pos_variables[0]
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)
        for key, item in pos_field.value.items.items():
            assert isinstance(item, PositionValue), f"item {key} should be PositionValue"

    def test_position_lines_content(self, array_pos_variables: list[RobotVariable]) -> None:
        v = array_pos_variables[0]
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)
        first = pos_field.value.items[(1,)]
        assert isinstance(first, PositionValue)
        assert any("Group" in l for l in first.raw_lines)
        assert any("X:" in l for l in first.raw_lines)
        assert any("W:" in l for l in first.raw_lines)

    def test_three_positions(self, array_pos_variables: list[RobotVariable]) -> None:
        v = array_pos_variables[0]
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)
        assert len(pos_field.value.items) == 3

    def test_subsequent_field_still_parsed(self, array_pos_variables: list[RobotVariable]) -> None:
        """Le field scalaire après ARRAY OF POSITION doit être parsé correctement."""
        v = array_pos_variables[0]
        count_field = next((f for f in v.fields if "COUNT" in f.field_name), None)
        assert count_field is not None
        assert count_field.value == "3"

    def test_array_value_repr(self, array_pos_variables: list[RobotVariable]) -> None:
        v = array_pos_variables[0]
        pos_field = next(f for f in v.fields if "POS" in f.field_name)
        assert isinstance(pos_field.value, ArrayValue)
        assert "positions" in repr(pos_field.value)


# ---------------------------------------------------------------------------
# Tests — Workspace multi-backups
# ---------------------------------------------------------------------------

from services.orchestrator import ExtractionOrchestrator
from config.settings import Settings
from models.fanuc_models import RobotBackup, WorkspaceResult


_SIMPLE_VA = "[*SYSTEM*]$ZZZ  Storage: CMOS  Access: RW  : INTEGER = 0\n"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """Crée un workspace avec deux robots."""
    (tmp_path / "Robot_A").mkdir()
    (tmp_path / "Robot_A" / "sysvars.va").write_text(_SIMPLE_VA * 3)
    (tmp_path / "Robot_B").mkdir()
    (tmp_path / "Robot_B" / "sysvars.va").write_text(_SIMPLE_VA * 2)
    (tmp_path / "EmptyDir").mkdir()
    return tmp_path


@pytest.fixture
def orch() -> ExtractionOrchestrator:
    return ExtractionOrchestrator(Settings())


class TestWorkspaceScan:
    def test_robot_count(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        assert ws.robot_count == 2

    def test_empty_dir_excluded(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        names = {b.name for b in ws.backups}
        assert "EmptyDir" not in names

    def test_robots_not_loaded(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        assert all(not b.loaded for b in ws.backups)
        assert all(b.var_count == 0 for b in ws.backups)

    def test_result_type(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        assert isinstance(ws, WorkspaceResult)
        assert all(isinstance(b, RobotBackup) for b in ws.backups)

    def test_flat_workspace(self, orch: ExtractionOrchestrator, tmp_path: Path) -> None:
        """Dossier racine contenant des .VA directement → 1 backup."""
        (tmp_path / "sysvars.va").write_text(_SIMPLE_VA)
        ws = orch.scan_workspace(tmp_path)
        assert ws.robot_count == 1
        assert ws.backups[0].path == tmp_path


class TestLoadBackup:
    def test_loads_variables(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        backup = next(b for b in ws.backups if b.name == "Robot_A")
        orch.load_backup(backup)
        assert backup.loaded
        assert backup.var_count == 3

    def test_modifies_in_place(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        backup = ws.backups[0]
        returned = orch.load_backup(backup)
        assert returned is backup

    def test_no_errors(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        orch.load_backup(ws.backups[0])
        assert ws.backups[0].errors == []

    def test_properties(self, orch: ExtractionOrchestrator, workspace_dir: Path) -> None:
        ws = orch.scan_workspace(workspace_dir)
        orch.load_backup(ws.backups[0])
        assert ws.loaded_count == 1
        orch.load_backup(ws.backups[1])
        assert ws.loaded_count == 2