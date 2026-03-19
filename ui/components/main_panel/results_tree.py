"""
ResultsTree — treeview de résultats avec scrollbars.

Composant pur d'affichage : reçoit des données formatées,
ne connaît pas les modèles métier.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Literal, cast

from ui.theme import PALETTE, FONTS

_AnchorT = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]
_ColSpec  = list[tuple[str, str, int, str, bool]]   # (id, heading, width, anchor, stretch)


class ResultsTree(tk.Frame):
    """Treeview à colonnes dynamiques + scrollbars + tags visuels."""

    # Colonnes par défaut (5) — extensible dynamiquement
    DEFAULT_COLUMNS = ("col1", "col2", "col3", "col4", "col5")

    def __init__(
        self,
        parent: tk.Misc,
        on_activate: Callable[[str], None],
    ) -> None:
        """
        :param on_activate: callback(iid) sur double-clic ou Entrée.
        """
        super().__init__(parent, bg=PALETTE["bg_card"])
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self._on_activate_cb = on_activate
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._tree = ttk.Treeview(
            self,
            columns=self.DEFAULT_COLUMNS,
            show="headings",
            selectmode="browse",
        )
        for col in self.DEFAULT_COLUMNS:
            self._tree.column(col, width=100, stretch=False)

        # Tags visuels
        self._tree.tag_configure("even",    background=PALETTE["bg_card"])
        self._tree.tag_configure("odd",     background=PALETTE["bg_panel"])
        self._tree.tag_configure("robot",   foreground=PALETTE["accent"],
                                            font=FONTS["heading"])  # type: ignore[arg-type]
        self._tree.tag_configure("karel",   foreground=PALETTE["warning"])
        self._tree.tag_configure("uninit",  foreground=PALETTE["uninit_fg"])
        self._tree.tag_configure("nav",     foreground=PALETTE["accent"])
        self._tree.tag_configure("pos",     foreground=PALETTE["info"])
        self._tree.tag_configure("loading", foreground=PALETTE["text_muted"])

        vsb = ttk.Scrollbar(self, orient="vertical",   command=self._tree.yview)
        hsb = ttk.Scrollbar(self, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        self._tree.bind("<Double-1>", self._on_event)
        self._tree.bind("<Return>",   self._on_event)

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def configure_columns(self, spec: _ColSpec) -> None:
        """Reconfigure les colonnes (id, heading, width, anchor, stretch).

        Ajuste aussi le nombre de colonnes si nécessaire.
        """
        col_ids = tuple(c[0] for c in spec)
        self._tree["columns"] = col_ids
        for col_id, heading, width, anchor, stretch in spec:
            a = cast(_AnchorT, anchor)
            self._tree.heading(col_id, text=heading, anchor=a)
            self._tree.column(col_id, width=width, anchor=a,
                              minwidth=0, stretch=stretch)

    def clear(self) -> None:
        self._tree.delete(*self._tree.get_children())

    def insert(
        self,
        values: tuple[object, ...],
        iid: str,
        tags: tuple[str, ...] = (),
    ) -> None:
        self._tree.insert("", "end", iid=iid, values=values, tags=tags)

    def focus_iid(self) -> str:
        return self._tree.focus()

    def index_of(self, iid: str) -> int:
        return self._tree.index(iid)

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _on_event(self, _event: tk.Event) -> None:
        iid = self._tree.focus()
        if iid:
            self._on_activate_cb(iid)