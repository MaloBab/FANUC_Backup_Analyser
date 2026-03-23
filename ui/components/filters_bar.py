"""
FiltersBar — barre de filtres du panneau principal.

Contient :
  - Filtre texte avec icône loupe
  - Bouton ✕ effacer intégré dans le champ
  - Filtres de scope : Tout / Système / Karel sous forme de pills

Comportement de recherche
─────────────────────────
La recherche est toujours globale (nom + type + valeur, tous backups chargés).
Elle se déclenche à chaque frappe via ``on_filter_change`` — le ViewModel
décide si un workspace est chargé et lance la recherche en conséquence.
Il n'y a pas de distinction local/global du point de vue de la barre.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from ui.theme import PALETTE, FONTS


class FiltersBar(tk.Frame):
    """Barre de filtres placée entre le header et le treeview."""

    HEIGHT = 40

    def __init__(
        self,
        parent: tk.Misc,
        on_filter_change: Callable[[str, str], None],
    ) -> None:
        super().__init__(parent, bg=PALETTE["bg_card"], height=self.HEIGHT)
        self.pack_propagate(False)
        self._callback = on_filter_change
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # ── Icône loupe ───────────────────────────────────────────────
        tk.Label(
            self, text="⌕",
            bg=PALETTE["bg_card"], fg=PALETTE["text_dim"],
            font=("Segoe UI", 13),  # type: ignore[arg-type]
        ).pack(side="left", padx=(12, 2))

        # ── Champ de recherche ────────────────────────────────────────
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._on_text_changed())

        entry_frame = tk.Frame(
            self, bg=PALETTE["bg_input"],
            highlightbackground=PALETTE["border_bright"],
            highlightthickness=1,
        )
        entry_frame.pack(side="left", pady=8)

        self._entry = tk.Entry(
            entry_frame,
            textvariable=self._filter_var,
            bg=PALETTE["bg_input"], fg=PALETTE["text"],
            insertbackground=PALETTE["accent"],
            selectbackground=PALETTE["bg_selected"],
            relief="flat", bd=0,
            font=FONTS["body"],  # type: ignore[arg-type]
            width=24,
        )
        self._entry.pack(side="left", padx=(6, 0), pady=3)

        self._clear_btn = tk.Label(
            entry_frame, text="✕",
            bg=PALETTE["bg_input"], fg=PALETTE["bg_input"],  # invisible par défaut
            font=("Segoe UI", 8),  # type: ignore[arg-type]
            cursor="hand2", padx=5,
        )
        self._clear_btn.pack(side="left", pady=3)
        self._clear_btn.bind("<Button-1>", lambda _: self._filter_var.set(""))

        # ── Séparateur vertical ───────────────────────────────────────
        tk.Frame(self, bg=PALETTE["border_bright"], width=1).pack(
            side="left", fill="y", padx=14, pady=8,
        )

        # ── Pills de scope ────────────────────────────────────────────
        self._scope_var = tk.StringVar(value="all")

        pills_frame = tk.Frame(self, bg=PALETTE["bg_card"])
        pills_frame.pack(side="left")

        self._pill_btns: dict[str, _ScopePill] = {}
        for value, label, accent in (
            ("all",    "All",    False),
            ("system", "System", False),
            ("karel",  "Karel",  True),
        ):
            pill = _ScopePill(
                pills_frame,
                label=label,
                is_accent=accent,
                on_click=lambda v=value: self._select_scope(v),  # type: ignore[misc]
            )
            pill.pack(side="left", padx=2, pady=6)
            self._pill_btns[value] = pill

        self._select_scope("all", emit=False)

        # ── Compteur (côté droit) ─────────────────────────────────────
        self._count_var = tk.StringVar(value="")
        tk.Label(
            self, textvariable=self._count_var,
            bg=PALETTE["bg_card"], fg=PALETTE["text_muted"],
            font=FONTS["small"],  # type: ignore[arg-type]
            anchor="e",
        ).pack(side="right", padx=12)

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    @property
    def query(self) -> str:
        return self._filter_var.get().lower()

    @property
    def scope(self) -> str:
        return self._scope_var.get()

    def set_scope(self, scope: str) -> None:
        self._select_scope(scope)

    def set_count(self, text: str) -> None:
        self._count_var.set(text)

    def clear(self) -> None:
        """Efface le filtre texte sans déclencher le callback."""
        self._filter_var.set("")

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _on_text_changed(self) -> None:
        has_text = bool(self._filter_var.get())
        self._clear_btn.config(
            fg=PALETTE["text_dim"] if has_text else PALETTE["bg_input"],
        )
        self._callback(self.query, self.scope)

    def _select_scope(self, value: str, emit: bool = True) -> None:
        self._scope_var.set(value)
        for v, pill in self._pill_btns.items():
            pill.set_active(v == value)
        if emit:
            self._callback(self.query, self.scope)


# ---------------------------------------------------------------------------
# Pill de scope (identique à l'original)
# ---------------------------------------------------------------------------

class _ScopePill(tk.Frame):
    """Bouton pill Tout / Système / Karel avec état actif/inactif."""

    def __init__(
        self,
        parent: tk.Misc,
        label: str,
        is_accent: bool,
        on_click: Callable[[], None],
    ) -> None:
        super().__init__(parent, bg=PALETTE["bg_card"])
        self._on_click  = on_click
        self._is_accent = is_accent
        self._active    = False

        self._lbl = tk.Label(
            self, text=label,
            bg=PALETTE["bg_card"], fg=PALETTE["text_dim"],
            font=FONTS["small"],  # type: ignore[arg-type]
            padx=10, pady=2,
            cursor="hand2",
            relief="flat",
        )
        self._lbl.pack()
        self._lbl.bind("<Button-1>", lambda _: self._on_click())
        self._lbl.bind("<Enter>",    self._on_enter)
        self._lbl.bind("<Leave>",    self._on_leave)

    def set_active(self, active: bool) -> None:
        self._active = active
        accent_fg = PALETTE["warning"] if self._is_accent else PALETTE["accent"]
        if active:
            self._lbl.config(bg=PALETTE["bg_input"], fg=accent_fg)
            self.config(
                bg=PALETTE["bg_input"],
                highlightbackground=accent_fg,
                highlightthickness=1,
            )
            self._lbl.config(bg=PALETTE["bg_input"])
        else:
            self._lbl.config(bg=PALETTE["bg_card"], fg=PALETTE["text_muted"])
            self.config(
                bg=PALETTE["bg_card"],
                highlightbackground=PALETTE["bg_card"],
                highlightthickness=0,
            )

    def _on_enter(self, _: tk.Event) -> None:
        if not self._active:
            self._lbl.config(fg=PALETTE["text"])

    def _on_leave(self, _: tk.Event) -> None:
        if not self._active:
            self._lbl.config(fg=PALETTE["text_muted"])