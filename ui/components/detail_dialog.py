"""
Fenêtre de détail d'une variable FANUC.

Architecture : Treeview hiérarchique à deux niveaux.
  - Niveau 1 : chaque field de la variable (avec ses métadonnées)
  - Niveau 2 : pour les fields de type ArrayValue, chaque entrée [i] = val
               pour les fields de type PositionValue, chaque ligne brute
Permet d'explorer la structure complète sans fenêtre supplémentaire.
"""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from typing import Literal, cast

from ui.theme import PALETTE, FONTS
from models.fanuc_models import RobotVariable, SystemVarField, ArrayValue, PositionValue

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]

# Colonnes : id, en-tête, largeur minimale, ancre, étirable
_COLUMNS: list[tuple[str, str, int, str, bool]] = [
    ("index",  "Index",  90,  "center", False),
    ("name",   "Field",  220, "w",      False),
    ("access", "Access", 65,  "center", False),
    ("type",   "Type",   170, "w",      False),
    ("value",  "Valeur", 260, "w",      True),
]

_ICON_ARRAY    = "▸"
_ICON_POS      = "⊕"


def _index_str(nd: tuple[int, ...] | None) -> str:
    if nd is None:
        return ""
    return "[" + ",".join(str(i) for i in nd) + "]"




class DetailDialog(tk.Toplevel):
    """Fenêtre modale — vue hiérarchique des fields d'une variable."""

    _MIN_W = 880
    _MIN_H = 560

    def __init__(self, parent: tk.Misc, variable: RobotVariable) -> None:
        super().__init__(parent)
        self._var = variable
        self._count_var = tk.StringVar()
        self._setup_window()
        self._build()
        self.focus_set()


    def _setup_window(self) -> None:
        v = self._var
        prefix = "" if v.is_system else f"[{v.namespace}]  "
        self.title(f"{prefix}{v.name}")
        self.geometry(f"{self._MIN_W}x{self._MIN_H}")
        self.minsize(self._MIN_W, self._MIN_H)
        self.configure(bg=PALETTE["bg"])
        self.resizable(True, True)
        self.grab_set()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)


    def _build(self) -> None:
        self._build_header()
        self._build_tree()
        self._build_footer()

    def _build_header(self) -> None:
        """Bande de titre avec nom, namespace et pills de métadonnées."""
        header = tk.Frame(self, bg=PALETTE["bg_panel"])
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        v = self._var

        name_row = tk.Frame(header, bg=PALETTE["bg_panel"])
        name_row.grid(row=0, column=0, sticky="w", padx=20, pady=(14, 4))

        if not v.is_system:
            tk.Label(
                name_row,
                text=f"[{v.namespace}]",
                bg=PALETTE["bg_panel"], fg=PALETTE["karel_fg"],
                font=FONTS["heading"],
            ).pack(side="left", padx=(0, 8))

        tk.Label(
            name_row,
            text=v.name,
            bg=PALETTE["bg_panel"], fg=PALETTE["text"],
            font=FONTS["title"],
        ).pack(side="left")

        pills_row = tk.Frame(header, bg=PALETTE["bg_panel"])
        pills_row.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 12))

        type_str = v.type_str
        pill_data: list[tuple[str, str]] = [
            (type_str,       PALETTE["text"]),
            (v.storage.value,PALETTE["text_dim"]),
            (v.access.value, PALETTE["text_dim"]),
        ]
        
        if v.array_shape:
            dims = "×".join(str(d) for d in v.array_shape)
            pill_data.append((f"[{dims}]", PALETTE["text_dim"]))
        elif v.array_size and not v.fields:
            pill_data.append((f"{v.array_size} entrées", PALETTE["text_dim"]))
        if v.fields:
            pill_data.append((f"{len(v.fields)} fields", PALETTE["text_dim"]))

        for text, fg in pill_data:
            _Pill(pills_row, text=text, fg=fg).pack(side="left", padx=(0, 6))

        tk.Frame(self, bg=PALETTE["border_bright"], height=1).grid(
            row=0, column=0, sticky="sew",
        )

    def _build_tree(self) -> None:
        """Treeview hiérarchique : fields → valeurs enfants."""
        outer = tk.Frame(self, bg=PALETTE["bg"])
        outer.grid(row=1, column=0, sticky="nsew")
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        col_ids = tuple(c[0] for c in _COLUMNS)
        self._tree = ttk.Treeview(
            outer,
            columns=col_ids,
            show="tree headings",
            selectmode="browse",
        )

        self._tree.column("#0", width=0, minwidth=0, stretch=False)
        self._tree.heading("#0", text="")

        for col_id, heading, width, anchor, stretch in _COLUMNS:
            a = cast(_AnchorT, anchor)
            self._tree.heading(col_id, text=heading, anchor=a)
            self._tree.column(col_id, width=width, anchor=a,
                              minwidth=40, stretch=stretch)

        self._tree.tag_configure("even",     background=PALETTE["bg_card"])
        self._tree.tag_configure("odd",      background=PALETTE["bg_panel"])
        self._tree.tag_configure("arr_field",foreground=PALETTE["text_dim"],
                                             font=FONTS["mono_sm"])
        self._tree.tag_configure("pos_field",foreground=PALETTE["info"],
                                             font=FONTS["mono_sm"])
        self._tree.tag_configure("uninit",   foreground=PALETTE["uninit_fg"])
        self._tree.tag_configure("child",    background=PALETTE["bg"],
                                             foreground=PALETTE["text_dim"],
                                             font=FONTS["mono_sm"])
        self._tree.tag_configure("child_alt",background=PALETTE["bg_input"],
                                             foreground=PALETTE["text_dim"],
                                             font=FONTS["mono_sm"])

        vsb = ttk.Scrollbar(outer, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(outer, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._populate()

    def _build_footer(self) -> None:
        """Barre inférieure : compteur + bouton Fermer."""
        footer = tk.Frame(self, bg=PALETTE["bg_panel"])
        footer.grid(row=2, column=0, sticky="ew")

        tk.Label(
            footer,
            textvariable=self._count_var,
            bg=PALETTE["bg_panel"], fg=PALETTE["text_dim"],
            font=FONTS["small"], anchor="w",
        ).pack(side="left", padx=16, pady=8)


        legend_items = [
            (_ICON_ARRAY + " tableau développable", PALETTE["text_dim"]),
            (_ICON_POS   + " position",              PALETTE["info"]),
        ]
        for txt, fg in legend_items:
            tk.Label(
                footer, text=txt,
                bg=PALETTE["bg_panel"], fg=fg,
                font=FONTS["mono_sm"],
            ).pack(side="left", padx=12, pady=8)

        ttk.Button(
            footer, text="Fermer",
            style="Ghost.TButton",
            command=self.destroy,
        ).pack(side="right", padx=16, pady=6)



    def _populate(self) -> None:
        """Insère les données selon la nature de la variable :
        - fields (struct / tableau de structs) : un nœud par field
        - ArrayValue racine (tableau primitif) : un nœud par entrée
        - PositionValue racine : un nœud par ligne brute
        """
        self._tree.delete(*self._tree.get_children())

        if self._var.fields:
            self._populate_fields()
        elif isinstance(self._var.value, ArrayValue):
            self._populate_root_array()
        elif isinstance(self._var.value, PositionValue):
            self._populate_root_position()

    def _populate_fields(self) -> None:
        """Cas struct / tableau de structs : fields avec enfants dépliables."""
        for i, fld in enumerate(self._var.fields):
            row_tag = "even" if i % 2 == 0 else "odd"
            self._insert_field(fld, row_tag)

        n_fields = len(self._var.fields)
        n_vals   = sum(
            len(f.value.items)
            for f in self._var.fields
            if isinstance(f.value, ArrayValue)
        )
        parts = [f"{n_fields} field{'s' if n_fields > 1 else ''}"]
        if n_vals:
            parts.append(f"{n_vals} valeurs de tableau")
        self._count_var.set("  ·  ".join(parts))

    def _populate_root_array(self) -> None:
        """Cas tableau primitif : une ligne par entrée [i] = val."""
        arr = self._var.value
        assert isinstance(arr, ArrayValue)
        for i, (key, val) in enumerate(arr.items.items()):
            key_str  = "[" + ",".join(str(k) for k in key) + "]"
            row_tag  = "even" if i % 2 == 0 else "odd"
            val_str  = val if val is not None else "Uninitialized"
            tags: list[str] = [row_tag]
            if val_str == "Uninitialized":
                tags.append("uninit")
            self._tree.insert(
                "", "end",
                values=(key_str, "", "", self._var.type_str, val_str),
                tags=tuple(tags),
            )
        n = len(arr.items)
        self._count_var.set(f"{n} entrée{'s' if n > 1 else ''}")

    def _populate_root_position(self) -> None:
        """Cas POSITION racine : une ligne par composante."""
        pos = self._var.value
        assert isinstance(pos, PositionValue)
        for i, line in enumerate(pos.raw_lines):
            row_tag = "even" if i % 2 == 0 else "odd"
            self._tree.insert(
                "", "end",
                values=("", "", "", "", line),
                tags=(row_tag,),
            )
        n = len(pos.raw_lines)
        self._count_var.set(f"{n} ligne{'s' if n > 1 else ''}") 

    def _insert_field(self, fld: SystemVarField, row_tag: str) -> None:
        """Insère un field. Si c'est un tableau ou une position, ajoute des enfants."""
        idx  = _index_str(fld.parent_index_nd)
        tags = [row_tag]

        if isinstance(fld.value, ArrayValue):
            icon = _ICON_ARRAY
            val_preview = f"[{len(fld.value.items)} entrées — cliquer ▶ pour développer]"
            tags.append("arr_field")
        elif isinstance(fld.value, PositionValue):
            icon = _ICON_POS
            n = len(fld.value.raw_lines)
            val_preview = f"[{n} ligne{'s' if n > 1 else ''} — cliquer ▶ pour développer]"
            tags.append("pos_field")
        elif fld.value == "Uninitialized":
            icon = ""
            val_preview = "Uninitialized"
            tags.append("uninit")
        else:
            icon = ""
            val_preview = fld.value or "—"

        name_display = f"{icon}  {fld.field_name}" if icon else fld.field_name

        parent_iid = self._tree.insert(
            "", "end",
            values=(
                idx,
                name_display,
                fld.access.value,
                fld.type_detail[:50],
                val_preview,
            ),
            tags=tuple(tags),
            open=False,
        )

        if isinstance(fld.value, ArrayValue):
            for j, (key, val) in enumerate(fld.value.items.items()):
                key_str  = "[" + ",".join(str(k) for k in key) + "]"
                child_tag = "child" if j % 2 == 0 else "child_alt"
                self._tree.insert(
                    parent_iid, "end",
                    values=(
                        key_str,
                        "",
                        "",
                        "",
                        val if val is not None else "Uninitialized",
                    ),
                    tags=(child_tag,),
                )

        elif isinstance(fld.value, PositionValue):
            for j, line in enumerate(fld.value.raw_lines):
                child_tag = "child" if j % 2 == 0 else "child_alt"
                self._tree.insert(
                    parent_iid, "end",
                    values=("", "", "", "", line),
                    tags=(child_tag,),
                )



class _Pill(tk.Label):
    """Label stylisé en 'pill' pour les métadonnées du header."""

    def __init__(self, parent: tk.Misc, text: str, fg: str) -> None:
        super().__init__(
            parent,
            text=f"  {text}  ",
            bg=PALETTE["bg_input"],
            fg=fg,
            font=FONTS["mono_sm"],
            relief="flat",
        )