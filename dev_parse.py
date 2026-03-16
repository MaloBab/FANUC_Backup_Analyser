"""
dev_parse.py — Main de test du parser, indépendant de l'UI.

Usage :
    python dev_parse.py                        # cherche *.VA dans le dossier courant
    python dev_parse.py path/to/file.VA        # parse un fichier précis
    python dev_parse.py path/to/dir            # parse tous les .VA du dossier
    python dev_parse.py path/to/file.VA --export results.csv
    python dev_parse.py path/to/file.VA --export results.json
    python dev_parse.py path/to/file.VA --filter SDTL
"""

from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Résolution du sys.path — doit être fait AVANT tout import du projet.
# On s'assure que la racine (dossier contenant services/, models/, etc.)
# est en tête de sys.path, quelle que soit la façon dont le script est lancé.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# Supprimer d'éventuels chemins relatifs parasites
sys.path = [p for p in sys.path if p != ""]

from services.parser import VAParser          # noqa: E402
from services.exporter import VariableExporter  # noqa: E402
from models.fanuc_models import (              # noqa: E402
    SystemVariable, ArrayValue, PositionValue, ExtractionResult
)


# ---------------------------------------------------------------------------
# Affichage console
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BLUE   = "\033[94m"


def _c(text: str, color: str) -> str:
    """Applique une couleur si le terminal le supporte."""
    return f"{color}{text}{RESET}"


def print_variable(var: SystemVariable, show_fields: bool = True, max_fields: int = 0) -> None:
    """Affiche une variable et ses champs de façon lisible.

    :param max_fields: nombre max de fields à afficher (0 = tous)
    """
    mod_str = "" if var.namespace == "*SYSTEM*" else _c(f"[{var.namespace}] ", DIM)
    print(
        f"  {mod_str}{_c(var.name, BOLD+CYAN)}"
        f"  {_c(var.storage.value, DIM)}  {_c(var.access.value, DIM)}"
        f"  {_c(var.type_detail[:60], BLUE)}"
    )

    # Valeur scalaire
    if isinstance(var.value, str) and var.value is not None:
        print(f"    = {_c(var.value, GREEN)}")
    elif isinstance(var.value, ArrayValue):
        items = var.value.items
        preview = ", ".join(f"[{chr(44).join(str(i) for i in k)}]={v}" for k, v in list(items.items())[:4])
        suffix = f"  … ({len(items)} items)" if len(items) > 4 else ""
        print(f"    [{_c(preview + suffix, GREEN)}]")
    elif isinstance(var.value, PositionValue):
        for line in var.value.raw_lines[:4]:
            print(f"    {_c(line, GREEN)}")

    # Champs
    if show_fields and var.fields:
        limit = max_fields if max_fields > 0 else len(var.fields)
        for fld in var.fields[:limit]:
            idx_str = f"[{fld.parent_index_nd[0]}]" if fld.parent_index_nd else ""
            val_str = ""
            if isinstance(fld.value, str) and fld.value is not None:
                val_str = _c(f"= {fld.value}", GREEN)
            elif isinstance(fld.value, ArrayValue):
                val_str = _c(f"Array({len(fld.value.items)} items)", DIM)
            elif isinstance(fld.value, PositionValue):
                val_str = _c("POSITION(...)", DIM)
            print(
                f"    {DIM}Field{idx_str}{RESET}"
                f" {_c(fld.field_name, YELLOW)}"
                f"  {_c(fld.type_detail[:30], DIM)}"
                f"  {val_str}"
            )
        if len(var.fields) > limit:
            print(_c(f"    … {len(var.fields) - limit} field(s) masqués (--max-fields)", DIM))


def print_summary(result: ExtractionResult, elapsed: float) -> None:
    """Affiche un résumé statistique de l'extraction."""
    vars_ = result.variables

    scalars    = [v for v in vars_ if not v.fields and isinstance(v.value, str)
                  and v.value != "Uninitialized"
                   and v.value != 'Uninitialized']
    prim_arrs  = [v for v in vars_ if not v.fields and isinstance(v.value, ArrayValue)]
    structs    = [v for v in vars_ if v.fields and not v.is_array]
    arr_struct = [v for v in vars_ if v.fields and v.is_array]
    positions  = [v for v in vars_ if not v.fields and isinstance(v.value, PositionValue)]
    unresolved  = [v for v in vars_ if not v.fields and v.value is None]
    uninit      = [v for v in vars_ if not v.fields and v.value == 'Uninitialized']

    total_fields = sum(len(v.fields) for v in vars_)

    print()
    print(_c("=" * 60, DIM))
    print(_c("  RÉSUMÉ D'EXTRACTION", BOLD))
    print(_c("=" * 60, DIM))
    sys_count   = sum(1 for v in vars_ if v.is_system)
    karel_count = len(vars_) - sys_count
    print(f"  Fichier(s)          : {result.input_dir}")
    print(f"  Durée               : {elapsed:.3f}s")
    print()
    print(f"  {_c('Variables totales', BOLD)}   : {_c(str(len(vars_)), CYAN)}")
    if karel_count:
        print(f"    dont système        : {sys_count}")
        print(f"    dont Karel          : {karel_count}")
    print(f"  Scalaires simples   : {len(scalars)}")
    print(f"  Tableaux primitifs  : {len(prim_arrs)}")
    print(f"  Structs simples     : {len(structs)}")
    print(f"  Tableaux de structs : {len(arr_struct)}")
    print(f"  Positions           : {len(positions)}")
    print(f"  Uninitialized       : {_c(str(len(uninit)), YELLOW if uninit else DIM)}")
    print(f"  Non résolus         : {_c(str(len(unresolved)), YELLOW if unresolved else DIM)}")
    print(f"  {_c('Fields totaux', BOLD)}       : {_c(str(total_fields), CYAN)}")

    if result.errors:
        print()
        print(_c(f"  ⚠  {len(result.errors)} erreur(s) :", RED))
        for err in result.errors[:5]:
            print(f"    - {err}")

    print(_c("=" * 60, DIM))
    print()


# ---------------------------------------------------------------------------
# Entrée principale
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test du parser .VA FANUC (sans UI)"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Fichier .VA ou dossier (défaut : dossier courant)",
    )
    parser.add_argument(
        "--filter", "-f",
        default="",
        metavar="TEXTE",
        help="Affiche uniquement les variables dont le nom contient TEXTE",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        metavar="N",
        help="Nombre max de variables à afficher (défaut : 20, 0 = tout)",
    )
    parser.add_argument(
        "--no-fields",
        action="store_true",
        help="Masque les champs des variables structurées",
    )
    parser.add_argument(
        "--max-fields", "-mf",
        type=int,
        default=0,
        metavar="N",
        help="Nombre max de fields par variable (défaut : 0 = tous)",
    )
    parser.add_argument(
        "--unresolved", "-u",
        action="store_true",
        help="Affiche uniquement les variables non résolues (value=None, pas de fields)",
    )
    parser.add_argument(
        "--system",
        action="store_true",
        help="Affiche uniquement les variables système [*SYSTEM*]",
    )
    parser.add_argument(
        "--karel",
        action="store_true",
        help="Affiche uniquement les variables Karel (non-système)",
    )
    parser.add_argument(
        "--export", "-e",
        metavar="FICHIER",
        help="Exporte les résultats vers un fichier .csv, .csv_flat ou .json",
    )
    args = parser.parse_args()

    target = Path(args.target)
    va_parser = VAParser()

    # --- Parsing ---
    print(_c("\n  Parsing en cours…", DIM))
    t0 = time.monotonic()

    if target.is_file():
        variables = va_parser.parse_file(target)
        result = ExtractionResult(input_dir=target, variables=variables)
    elif target.is_dir():
        result = va_parser.parse_directory(target)
    else:
        print(_c(f"  ✗ Cible introuvable : {target}", RED))
        sys.exit(1)

    elapsed = time.monotonic() - t0

    # --- Filtrage ---
    query = args.filter.lower()
    to_display = result.variables

    if args.unresolved:
        to_display = [v for v in to_display if not v.fields and v.value is None]
        print(f"  --unresolved → {len(to_display)} variable(s) non résolue(s)\n")
    elif args.system:
        to_display = [v for v in to_display if v.is_system]
        print(f"  --system → {len(to_display)} variable(s) système\n")
    elif args.karel:
        to_display = [v for v in to_display if not v.is_system]
        print(f"  --karel → {len(to_display)} variable(s) Karel\n")
    elif query:
        to_display = [
            v for v in to_display
            if query in v.name.lower() or query in v.type_detail.lower()
        ]
        print(f"  Filtre '{args.filter}' → {len(to_display)} résultat(s)\n")

    # --- Affichage ---
    limit = args.limit if args.limit > 0 else len(to_display)
    for var in to_display[:limit]:
        print_variable(var, show_fields=not args.no_fields, max_fields=args.max_fields)

    if len(to_display) > limit:
        print(_c(f"\n  … {len(to_display) - limit} variable(s) supplémentaire(s) masquées (--limit)", DIM))

    # --- Résumé ---
    print_summary(result, elapsed)

    # --- Export ---
    if args.export:
        export_path = Path(args.export)
        fmt = export_path.suffix.lstrip(".") or "csv"
        # csv_flat si le nom contient "flat"
        if "flat" in export_path.stem:
            fmt = "csv_flat"
        try:
            VariableExporter().export(result.variables, export_path, fmt)
            print(_c(f"  ✓ Export {fmt.upper()} → {export_path}", GREEN))
        except Exception as exc:
            print(_c(f"  ✗ Export échoué : {exc}", RED))
    print()


if __name__ == "__main__":
    main()