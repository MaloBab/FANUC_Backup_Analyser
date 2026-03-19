"""
HeaderBar — barre de titre + navigation arborescente.

Zone gauche  : nom appli
Zone centre  : [←] [→]  |  Backups / back1 / $DMR_GRP  (breadcrumbs cliquables)
Zone droite  : ⚙ Paramètres
Trait ambre 2px en bas de barre.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from ui.theme import PALETTE, FONTS

if TYPE_CHECKING:
    from ui.viewmodel import AppViewModel


class HeaderBar(tk.Frame):

    HEIGHT = 52

    def __init__(
        self,
        parent: tk.Misc,
        on_back: Callable[[], None],
        on_forward: Callable[[], None],
        on_breadcrumb: Callable[[int], None],
        vm: AppViewModel | None = None,
    ) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"], height=self.HEIGHT)
        self.grid_propagate(False)
        self._on_back       = on_back
        self._on_forward    = on_forward
        self._on_breadcrumb = on_breadcrumb
        self._vm            = vm
        self._crumb_widgets: list[tk.Widget] = []
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        # Grille interne : [logo | sep | nav_btns | sep | crumbs | sep | settings]
        self.columnconfigure(4, weight=1)   # crumbs s'étire
        self.rowconfigure(0, weight=1)

        col = 0

        # ── Logo ──────────────────────────────────────────────────────
        logo = tk.Frame(self, bg=PALETTE["bg_panel"])
        logo.grid(row=0, column=col, padx=(16, 0), sticky="ns")

        tk.Label(logo, text="◈",
                 bg=PALETTE["bg_panel"], fg=PALETTE["accent"],
                 font=("Consolas", 17),     # type: ignore[arg-type]
                 ).pack(side="left", pady=0)

        tk.Label(logo, text="  FANUC",
                 bg=PALETTE["bg_panel"], fg=PALETTE["text"],
                 font=("Consolas", 13, "bold"),  # type: ignore[arg-type]
                 ).pack(side="left")

        tk.Label(logo, text="  Backup Analyzer",
                 bg=PALETTE["bg_panel"], fg=PALETTE["text_muted"],
                 font=("Segoe UI", 9),      # type: ignore[arg-type]
                 ).pack(side="left")
        col += 1

        # ── Séparateur ───────────────────────────────────────────────
        tk.Frame(self, bg=PALETTE["border_bright"], width=1).grid(
            row=0, column=col, sticky="ns", padx=12, pady=10,
        )
        col += 1

        # ── Boutons ← → ───────────────────────────────────────────────
        nav = tk.Frame(self, bg=PALETTE["bg_panel"])
        nav.grid(row=0, column=col, sticky="ns", padx=(0, 4))

        self._btn_back = _NavButton(nav, "←", self._on_back)
        self._btn_back.pack(side="left")

        self._btn_fwd = _NavButton(nav, "→", self._on_forward)
        self._btn_fwd.pack(side="left", padx=(2, 0))
        col += 1

        # ── Séparateur ───────────────────────────────────────────────
        tk.Frame(self, bg=PALETTE["border_bright"], width=1).grid(
            row=0, column=col, sticky="ns", padx=(8, 0), pady=10,
        )
        col += 1

        # ── Zone breadcrumbs ──────────────────────────────────────────
        self._crumb_frame = tk.Frame(self, bg=PALETTE["bg_panel"])
        self._crumb_frame.grid(row=0, column=col, sticky="nsew", padx=10)
        col += 1

        # ── Séparateur ───────────────────────────────────────────────
        tk.Frame(self, bg=PALETTE["border_bright"], width=1).grid(
            row=0, column=col, sticky="ns", pady=10,
        )
        col += 1

        # ── Paramètres ────────────────────────────────────────────────
        ttk.Button(self, text="⚙ Settings", style="Ghost.TButton",
                   command=self._open_settings).grid(
            row=0, column=col, padx=14, sticky="ns")

        # ── Trait ambre bas ───────────────────────────────────────────
        tk.Frame(self, bg=PALETTE["accent"], height=2).grid(
            row=1, column=0, columnspan=col + 1, sticky="ew",
        )
        self.rowconfigure(1, minsize=2)

    # ------------------------------------------------------------------
    # Interface publique
    # ------------------------------------------------------------------

    def set_nav_state(self, can_back: bool, can_forward: bool) -> None:
        self._btn_back.set_enabled(can_back)
        self._btn_fwd.set_enabled(can_forward)

    def set_breadcrumbs(self, parts: list[str]) -> None:
        for w in self._crumb_widgets:
            w.destroy()
        self._crumb_widgets.clear()

        last = len(parts) - 1
        for i, label in enumerate(parts):
            is_last = (i == last)

            if i > 0:
                sep = tk.Label(
                    self._crumb_frame, text=" / ",
                    bg=PALETTE["bg_panel"], fg=PALETTE["text_muted"],
                    font=FONTS["small"],    # type: ignore[arg-type]
                )
                sep.pack(side="left")
                self._crumb_widgets.append(sep)

            if is_last:
                lbl = tk.Label(
                    self._crumb_frame, text=label,
                    bg=PALETTE["bg_panel"], fg=PALETTE["text"],
                    font=FONTS["body_med"],  # type: ignore[arg-type]
                )
            else:
                idx = i
                lbl = tk.Label(
                    self._crumb_frame, text=label,
                    bg=PALETTE["bg_panel"], fg=PALETTE["accent"],
                    font=FONTS["body"],      # type: ignore[arg-type]
                    cursor="hand2",
                )
                lbl.bind("<Button-1>",
                         lambda _e, n=idx: self._on_breadcrumb(n))  # type: ignore[misc]
                lbl.bind("<Enter>",
                         lambda _e, w=lbl: w.config(fg=PALETTE["accent_hover"]))  # type: ignore[misc]
                lbl.bind("<Leave>",
                         lambda _e, w=lbl: w.config(fg=PALETTE["accent"]))  # type: ignore[misc]

            lbl.pack(side="left")
            self._crumb_widgets.append(lbl)

    # ------------------------------------------------------------------
    # Interne
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        if self._vm is None:
            return
        from ui.components.settings_dialog import SettingsDialog
        SettingsDialog(self, self._vm)


# ---------------------------------------------------------------------------
# Bouton de navigation interne
# ---------------------------------------------------------------------------

class _NavButton(tk.Frame):
    """Bouton ← ou → avec états actif/inactif visuels."""

    _SIZE   = 28
    _RADIUS = 6   # arrondi simulé par padding

    def __init__(self, parent: tk.Misc, symbol: str, command: Callable[[], None]) -> None:
        super().__init__(parent, bg=PALETTE["bg_panel"])
        self._cmd = command
        self._enabled = True

        self._btn = tk.Label(
            self, text=symbol,
            bg=PALETTE["bg_input"],
            fg=PALETTE["text_dim"],
            font=("Consolas", 12, "bold"),  # type: ignore[arg-type]
            width=2,
            relief="flat",
            cursor="arrow",
            padx=4, pady=3,
        )
        self._btn.pack()
        self._btn.bind("<Button-1>", self._on_click)
        self._btn.bind("<Enter>",    self._on_enter)
        self._btn.bind("<Leave>",    self._on_leave)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self._btn.config(
                fg=PALETTE["text"],
                bg=PALETTE["bg_input"],
                cursor="hand2",
            )
        else:
            self._btn.config(
                fg=PALETTE["text_muted"],
                bg=PALETTE["bg_panel"],
                cursor="arrow",
            )

    def _on_click(self, _: tk.Event) -> None:
        if self._enabled:
            self._cmd()

    def _on_enter(self, _: tk.Event) -> None:
        if self._enabled:
            self._btn.config(bg=PALETTE["bg_hover"], fg=PALETTE["accent"])

    def _on_leave(self, _: tk.Event) -> None:
        if self._enabled:
            self._btn.config(bg=PALETTE["bg_input"], fg=PALETTE["text"])
        else:
            self._btn.config(bg=PALETTE["bg_panel"], fg=PALETTE["text_muted"])