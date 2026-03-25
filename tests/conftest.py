"""
Contient :
  - Builders de modèles (RobotVariable, RobotVarField, RobotBackup…)
  - Fichiers VA/CSV inline utilisables depuis n'importe quel module de test
  - Fixtures pytest communes (parser, csv_parser, searcher, exporter…)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config.settings import Settings
from models.fanuc_models import (
    AccessType,
    ArrayValue,
    PositionValue,
    RobotBackup,
    RobotVarField,
    RobotVariable,
    StorageType,
    VADataType,
)
from services.converter.vr_sv_converter import VAConverter
from services.exporter import VariableExporter
from services.orchestrator import ExtractionOrchestrator
from services.parser.dataid_csv_parser import DataIdCsvParser
from services.parser.va_parser import VAParser
from services.searcher import Searcher


# ---------------------------------------------------------------------------
# Builders de modèles — fonctions utilitaires, pas des fixtures
# ---------------------------------------------------------------------------

def make_var(
    name: str = "$VAR",
    namespace: str = "*SYSTEM*",
    storage: StorageType = StorageType.CMOS,
    access: AccessType = AccessType.RW,
    data_type: VADataType = VADataType.INTEGER,
    type_detail: str = "INTEGER = 0",
    is_array: bool = False,
    array_size: int | None = None,
    array_shape: tuple[int, ...] | None = None,
    value=None,
    fields: list | None = None,
    source_file: Path | None = None,
    line_number: int | None = None,
) -> RobotVariable:
    """Crée une RobotVariable avec des valeurs par défaut sensées."""
    return RobotVariable(
        name=name,
        namespace=namespace,
        storage=storage,
        access=access,
        data_type=data_type,
        type_detail=type_detail,
        is_array=is_array,
        array_size=array_size,
        array_shape=array_shape,
        value=value,
        fields=fields or [],
        source_file=source_file,
        line_number=line_number,
    )


def make_field(
    full_name: str = "$VAR.$F",
    parent_var: str = "$VAR",
    field_name: str = "$F",
    access: AccessType = AccessType.RW,
    data_type: VADataType = VADataType.INTEGER,
    type_detail: str = "INTEGER",
    value=None,
    parent_index_nd: tuple[int, ...] | None = None,
    condition_handler: str = "",
) -> RobotVarField:
    """Crée un RobotVarField avec des valeurs par défaut sensées."""
    return RobotVarField(
        full_name=full_name,
        parent_var=parent_var,
        field_name=field_name,
        access=access,
        data_type=data_type,
        type_detail=type_detail,
        value=value,
        parent_index_nd=parent_index_nd,
        condition_handler=condition_handler,
    )


def make_backup(
    name: str = "Robot",
    path: Path | None = None,
    variables: list[RobotVariable] | None = None,
    loaded: bool = True,
    errors: list[str] | None = None,
) -> RobotBackup:
    """Crée un RobotBackup déjà chargé par défaut."""
    return RobotBackup(
        name=name,
        path=path or Path("/fake"),
        variables=variables or [],
        loaded=loaded,
        errors=errors or [],
    )


def make_array_var(items: dict, name: str = "$ARR", type_detail: str = "ARRAY[3] OF INTEGER") -> RobotVariable:
    """Crée une variable tableau avec les items fournis."""
    return make_var(
        name=name,
        is_array=True,
        array_size=len(items),
        type_detail=type_detail,
        value=ArrayValue(items=items),
    )


def make_struct_var(fields: list[RobotVarField], name: str = "$STRUCT") -> RobotVariable:
    """Crée une variable struct avec les fields fournis."""
    return make_var(
        name=name,
        data_type=VADataType.STRUCT,
        type_detail="MYSTRUCT_T =",
        fields=fields,
    )


def write_va(tmp_path: Path, content: str, filename: str = "test.VA") -> Path:
    """Écrit du contenu VA dans un fichier temporaire et retourne le chemin."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


def write_dataid(tmp_path: Path, content: str, filename: str = "DATAID.CSV") -> Path:
    """Écrit du contenu DATAID.CSV dans un fichier temporaire."""
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Contenus VA de référence
# ---------------------------------------------------------------------------

VA_SIMPLE_SCALARS = """\
[*SYSTEM*]$ACC_MAXLMT  Storage: CMOS  Access: RW  : INTEGER = 100
[*SYSTEM*]$AP_AUTOMODE  Storage: CMOS  Access: RW  : BOOLEAN = FALSE
[*SYSTEM*]$ROBOT_NAME  Storage: CMOS  Access: RW  : STRING[37] = Uninitialized
[*SYSTEM*]$PI  Storage: DRAM  Access: RO  : REAL = 3.141593e+00
"""

VA_ARRAYS = """\
[*SYSTEM*]$APPLICATION  Storage: CMOS  Access: RO  : ARRAY[3] OF STRING[21]
     [1] = 'LR HandlingTool'
     [2] = 'V9.40P/27'
     [3] = 'Uninitialized'

[*SYSTEM*]$ANGTOL  Storage: CMOS  Access: RW  : ARRAY[4] OF REAL
     [1] = 1.000000e+00
     [2] = 2.000000e+00
     [3] = 3.000000e+00
     [4] = 4.000000e+00
"""

VA_STRUCT_SIMPLE = """\
[*SYSTEM*]$ALMDG  Storage: SHADOW  Access: FP  : ALMDG_T =
   Field: $ALMDG.$DEBUG1 Access: RW: INTEGER = 0
   Field: $ALMDG.$DEBUG2 Access: RW: INTEGER = 42
"""

VA_ARRAY_OF_STRUCT = """\
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
"""

VA_ND_ARRAY = """\
[*SYSTEM*]$PGTRACEDT  Storage: CMOS  Access: RO  : ARRAY[2,3] OF TRACEDT_T
     Field: $PGTRACEDT[1,1].$EPT_INDEX Access: RO: SHORT = 10
     Field: $PGTRACEDT[1,2].$EPT_INDEX Access: RO: SHORT = 20
     Field: $PGTRACEDT[2,1].$EPT_INDEX Access: RO: SHORT = 30
"""

VA_KAREL = """\
[TBSWMD45]NFPAM  Storage: CMOS  Access: RW  : NFPAM_T =
     Field: NFPAM.TBC.CNT_SCALE  ARRAY[2] OF REAL
      [1] = 1.150000e+00
      [2] = 1.120000e+00
     Field: NFPAM.TBC.MIN_ACC_UCA  ARRAY[2] OF INTEGER
      [1] = 68
      [2] = 72
"""

VA_POSREG = """\
[*POSREG*]$POSREG  Storage: CMOS  Access: RW  : ARRAY[1,3] OF Position Reg
     [1,1] = 'OR_Get_Ref'
  Group: 1   Config: N U T, 0, 0, 0
  X:   100.000   Y:   200.000   Z:   300.000
  W:     0.000   P:     0.000   R:     0.000
     [1,2] = '' Uninitialized
     [1,3] = ''
  Group: 1   Config: N U T, 0, 0, 0
  X:     0.000   Y:     0.000   Z:     0.000
  W:     0.000   P:     0.000   R:     0.000
"""

VA_SCALAR_POSITION = """\
[*SYSTEM*]$MASTER_POS  Storage: CMOS  Access: RW  : POSITION =
  Group: 1
  X:     0.000   Y:     0.000   Z:     0.000
  W:     0.000   P:     0.000   R:     0.000
"""

VA_FIELD_POSITION = """\
[*SYSTEM*]$MASTP  Storage: SHADOW  Access: FP  : MASTP_T =
   Field: $MASTP.$POS Access: RW: POSITION =
  Group: 1
  X:     1.000   Y:     2.000   Z:     3.000
  W:     4.000   P:     5.000   R:     6.000
"""

VA_ARRAY_OF_POSITION = """\
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

VA_UNINITIALIZED = """\
[*SYSTEM*]$DCS_NOCODE  Storage: CMOS  Access: FP  : DCS_NOCODE_T =

[*SYSTEM*]$PAUSE_PROG  Storage: CMOS  Access: RW  : STRING[37] = Uninitialized
"""

VA_HOSTENT = """\
[*SYSTEM*]$HOSTENT  Storage: CMOS  Access: RW  : ARRAY[3] OF HOSTENT_T
     Field: $HOSTENT[1].$H_ADDR Access: RW: STRING[16] = '192.168.1.1'
     Field: $HOSTENT[2].$H_ADDR Access: RW: STRING[16] = '192.168.1.2'
     Field: $HOSTENT[3].$H_ADDR Access: RW: STRING[16] = Uninitialized
"""

DATAID_FULL = """\
DATAIDVER,V9.40,!!!!
REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!
DATAID,$ALARM.AUTO_DISPLAY,BOOLEAN,TRUE,RW,,!!!!
DATAID,$ALARM.ERROR_SEVERITY_TABLE[1],INTEGER,3,RO,HANDLER_A,!!!!
DATAID,$ALARM.ERROR_SEVERITY_TABLE[2],INTEGER,5,RO,HANDLER_A,!!!!
DATAID,$ALARM.MAX_COUNT,INTEGER,100,RW,,!!!!
DATAID,$OTHER.FIELD,REAL,1.5,RW,,!!!!
DATAID,$OTHER.ACTIVE,BOOLEAN,FALSE,RO,,!!!!
END,!!!!
"""

DATAID_WITH_POSITION = """\
DATAIDVER,V9.40,!!!!
REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!
DATAID,$MASTER.POINT,POSITION,Group:1/X:0.0/Y:0.0/Z:0.0/W:0.0/P:0.0/R:0.0,RW,,!!!!
DATAID,$MASTER.NAME,STRING,MyPoint,RW,,!!!!
END,!!!!
"""

DATAID_UNINIT = """\
DATAIDVER,V9.40,!!!!
REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!
DATAID,$ZONE.ACTIVE,BOOLEAN,*Uninitialized*,RW,,!!!!
DATAID,$ZONE.COUNT,INTEGER,*Uninitialized*,RW,,!!!!
END,!!!!
"""

DATAID_CW_ACCESS = """\
DATAIDVER,V9.40,!!!!
REM,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!
DATAID,$COND.FLAG,BOOLEAN,TRUE,CW,,!!!!
DATAID,$COND.VALUE,INTEGER,0,FP,,!!!!
DATAID,$COND.OTHER,INTEGER,0,WO,,!!!!
END,!!!!
"""

DATAID_BAD_FIRST_LINE = "NOT_A_DATAID_FILE\nREM,...\n"
DATAID_TOO_SHORT = "DATAIDVER,V9.40,!!!!\n"
DATAID_MISSING_COLUMNS = (
    "DATAIDVER,V9.40,!!!!\n"
    "REM,DataID Name,Data Type,Value,!!!!\n"
    "END,!!!!\n"
)
DATAID_BAD_REM = (
    "DATAIDVER,V9.40,!!!!\n"
    "HEADER,DataID Name,Data Type,Value,Access Type,ConditionHandler,!!!!\n"
    "END,!!!!\n"
)


# ---------------------------------------------------------------------------
# Fixtures globales
# ---------------------------------------------------------------------------

@pytest.fixture
def parser() -> VAParser:
    return VAParser()


@pytest.fixture
def csv_parser() -> DataIdCsvParser:
    return DataIdCsvParser()


@pytest.fixture
def searcher() -> Searcher:
    return Searcher()


@pytest.fixture
def exporter() -> VariableExporter:
    return VariableExporter()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def orchestrator(settings: Settings) -> ExtractionOrchestrator:
    return ExtractionOrchestrator(
                parsers   = [DataIdCsvParser(), VAParser()],
                converter = VAConverter,
                exporter  = VariableExporter(),
                settings  = settings,
            )


@pytest.fixture
def va_workspace(tmp_path: Path) -> Path:
    """Workspace avec deux robots VA et un dossier vide."""
    robot_a = tmp_path / "Robot_A"
    robot_a.mkdir()
    (robot_a / "sysvars.va").write_text(VA_SIMPLE_SCALARS + VA_ARRAYS, encoding="utf-8")
    (robot_a / "structs.va").write_text(VA_STRUCT_SIMPLE, encoding="utf-8")

    robot_b = tmp_path / "Robot_B"
    robot_b.mkdir()
    (robot_b / "sysvars.va").write_text(VA_SIMPLE_SCALARS, encoding="utf-8")

    (tmp_path / "EmptyDir").mkdir()
    return tmp_path