"""
Service de recherche globale dans les variables FANUC.

Sans dépendance à Tkinter — appelable depuis BackgroundWorker ou tests unitaires.

Deux modes de recherche
───────────────────────
1. **Texte libre** (``SearchQuery``) — déclenché quand le texte ne ressemble
   pas à un chemin FANUC. Cherche simultanément dans le nom, le type et la
   valeur de chaque variable et de tous ses fields, à toutes les profondeurs.

2. **Chemin exact** (``PathQuery``) — déclenché quand le texte ressemble à un
   chemin FANUC de la forme ``$VAR[i].FIELD[j]``. Résout le chemin directement
   dans les données parsées et retourne la valeur exacte pour chaque backup.

Détection du mode
─────────────────
``Searcher.search_from_text()`` est le point d'entrée unique :
il détecte automatiquement le mode et délègue à ``search()`` ou ``resolve()``.

Formes de chemin reconnues
──────────────────────────
  ``$HOSTENT[17].$H_ADDR``       var + index + field
  ``$HOSTENT[17]``               var + index seul (tous les fields de cet élément)
  ``$AAVM_WRK[1].$DISTORT[2]``   var + index + field + index de field
  ``$AIO_CNV[1].$RACK``          var + index + field scalaire
  ``$HOSTENT.$H_ADDR``           var + field sans index (→ tous les éléments)
  ``$ANGTOL[3]``                 var tableau primitif + index
"""

from __future__ import annotations
import logging
import re
from typing import Generator

from models.fanuc_models import (
    ArrayValue, PositionValue, RobotBackup, RobotVarField, RobotVariable,
)
from models.search_models import PathQuery, SearchHit, SearchQuery, SearchResults

logger = logging.getLogger(__name__)

_MAX_HITS = 2000

# Regex de détection et décomposition d'un chemin FANUC
# Groupes : (var_name, var_index, field_name, field_index)
_PATH_RE = re.compile(
    r'^(\$?[\w.]+?)'        # 1. nom variable ($ optionnel, points pour Karel)
    r'(?:\[([\d,]+)\])?'    # 2. index variable optionnel : [17] ou [1,2]
    r'(?:\.([\$\w]+))?'     # 3. nom field optionnel : .$H_ADDR
    r'(?:\[([\d,]+)\])?$'   # 4. index field optionnel : [2]
)


def _parse_index(raw: str | None) -> tuple[int, ...] | None:
    """``"17"`` → ``(17,)``,  ``"1,2"`` → ``(1, 2)``,  ``None`` → ``None``."""
    if raw is None:
        return None
    return tuple(int(d) for d in raw.split(",") if d)


# Mots qui ne sont jamais des noms de variable FANUC (fragments de type)
_NON_VAR_PREFIXES: frozenset[str] = frozenset({
    "ARRAY", "INTEGER", "REAL", "BOOLEAN", "STRING", "POSITION",
    "XYZWPR", "JOINTPOS", "SHORT", "BYTE", "OF",
})


def _is_path(text: str) -> bool:
    """Retourne True si le texte ressemble à un chemin FANUC.

    Conditions :
      - Commence par ``$`` ou contient ``[N]``.
      - Le nom de variable ne correspond pas à un fragment de type connu
        (ARRAY, INTEGER, REAL…).
    """
    t = text.strip()
    if len(t) <= 2:
        return False
    if not t.startswith("$") and "[" not in t:
        return False
    m = _PATH_RE.match(t)
    if not m:
        return False
    var_upper = m.group(1).upper()
    if any(var_upper == p or var_upper.startswith(p + "[")
           for p in _NON_VAR_PREFIXES):
        return False
    return True



def _build_path_query(text: str, scope: str) -> PathQuery | None:
    """Parse le texte en ``PathQuery``. Retourne ``None`` si le format est invalide."""
    m = _PATH_RE.match(text.strip())
    if not m:
        return None
    var_name, var_idx_raw, field_name, field_idx_raw = m.groups()
    return PathQuery(
        raw         = text.strip(),
        var_name    = var_name,
        var_index   = _parse_index(var_idx_raw),
        field_name  = field_name,
        field_index = _parse_index(field_idx_raw),
        scope       = scope,
    )


class Searcher:
    """Point d'entrée unique pour la recherche globale.

    Usage::

        searcher = Searcher()
        results  = searcher.search_from_text(text, scope, backups)
    """

    def search_from_text(
        self,
        text: str,
        scope: str,
        backups: list[RobotBackup],
    ) -> SearchResults:
        """Détecte le mode et lance la recherche appropriée.

        :param text:    texte brut saisi par l'utilisateur.
        :param scope:   ``"all"``, ``"system"`` ou ``"karel"``.
        :param backups: backups à inspecter (seuls les ``loaded`` sont utilisés).
        """
        if not text.strip():
            return SearchResults(query=SearchQuery(text=""))

        if _is_path(text):
            pq = _build_path_query(text, scope)
            if pq is not None:
                results = self.resolve(pq, backups)
                # Si le chemin n'a rien donné (variable inconnue, index hors
                # limites…), on retombe en recherche texte libre pour ne pas
                # laisser l'utilisateur sans résultat.
                if results.hit_count > 0:
                    return results

        return self.search(SearchQuery(text=text, scope=scope), backups)

    # ------------------------------------------------------------------
    # Mode 1 — Texte libre
    # ------------------------------------------------------------------

    def search(
        self,
        query: SearchQuery,
        backups: list[RobotBackup],
    ) -> SearchResults:
        """Recherche texte libre : nom + type + valeur, toutes profondeurs."""
        if query.is_empty:
            return SearchResults(query=query)

        results = SearchResults(query=query)

        for backup in backups:
            if not backup.loaded:
                continue
            for var in backup.variables:
                results.searched += 1
                if not self._scope_ok(var, query.scope):
                    continue
                try:
                    for hit in self._text_hits(var, backup.name, query):
                        results.hits.append(hit)
                        if len(results.hits) >= _MAX_HITS:
                            logger.warning("Limite de %d résultats atteinte.", _MAX_HITS)
                            return results
                except Exception as exc:
                    results.errors.append(f"{backup.name}/{var.name}: {exc}")
                    logger.warning("Erreur recherche %s.%s : %s",
                                   backup.name, var.name, exc)
        return results

    # ------------------------------------------------------------------
    # Mode 2 — Chemin exact
    # ------------------------------------------------------------------

    def resolve(
        self,
        query: PathQuery,
        backups: list[RobotBackup],
    ) -> SearchResults:
        """Résout un chemin FANUC exact dans tous les backups chargés.

        Pour chaque backup, trouve la variable correspondant à ``query.var_name``
        puis résout le chemin dans ses données parsées.
        Un hit est produit pour chaque valeur résolue.
        """
        results = SearchResults(query=query)

        for backup in backups:
            if not backup.loaded:
                continue
            # Trouver toutes les variables portant ce nom (plusieurs fichiers .VA)
            candidates = [
                v for v in backup.variables
                if v.name.upper() == query.var_name.upper()
                and self._scope_ok(v, query.scope)
            ]
            for var in candidates:
                results.searched += 1
                source = var.source_file.name if var.source_file else ""
                try:
                    for hit in self._resolve_var(var, query, backup.name, source):
                        results.hits.append(hit)
                except Exception as exc:
                    results.errors.append(f"{backup.name}/{var.name}: {exc}")
                    logger.warning("Erreur résolution %s.%s : %s",
                                   backup.name, var.name, exc)

        return results

    def _resolve_var(
        self,
        var: RobotVariable,
        query: PathQuery,
        backup_name: str,
        source: str,
    ) -> Generator[SearchHit, None, None]:
        """Résout le chemin dans une variable et génère les hits correspondants."""

        def hit(path: str, value: str | None) -> SearchHit:
            return SearchHit(
                backup_name   = backup_name,
                source_file   = source,
                variable_name = var.name,
                match_path    = path,
                match_value   = value,
            )

        # ── Cas 1 : pas de field → accès direct à la valeur de la variable ──
        if query.field_name is None:
            if query.var_index is None:
                # $VAR — valeur racine (scalaire ou array entier)
                yield hit(var.name, self._value_str(var.value))
                return

            # $VAR[i] — élément d'un array primitif ou tous les fields d'un struct
            idx = query.var_index
            if isinstance(var.value, ArrayValue):
                val = var.value.items.get(idx)
                if val is not None:
                    yield hit(
                        f"{var.name}[{','.join(str(k) for k in idx)}]",
                        self._value_str(val),
                    )
            elif var.fields:
                # Array de structs : retourner tous les fields de cet index
                idx_fields = [f for f in var.fields if f.parent_index_nd == idx]
                for f in idx_fields:
                    yield hit(f.full_name, self._value_str(f.value))
            return

        # ── Cas 2 : field spécifié ─────────────────────────────────────────
        field_name = query.field_name

        # Filtrer les fields qui correspondent
        if query.var_index is not None:
            # $VAR[i].FIELD — index exact
            matching = [
                f for f in var.fields
                if f.parent_index_nd == query.var_index
                and f.field_name.upper() == field_name.upper()
            ]
        else:
            # $VAR.FIELD — tous les index
            matching = [
                f for f in var.fields
                if f.field_name.upper() == field_name.upper()
            ]

        for fld in matching:
            # ── Cas 2a : field scalaire ──────────────────────────────────
            if query.field_index is None:
                yield hit(fld.full_name, self._value_str(fld.value))

            # ── Cas 2b : $VAR[i].FIELD[j] — index dans un field tableau ──
            elif isinstance(fld.value, ArrayValue):
                val = fld.value.items.get(query.field_index)
                if val is not None:
                    fidx_str = "[" + ",".join(str(k) for k in query.field_index) + "]"
                    yield hit(f"{fld.full_name}{fidx_str}", self._value_str(val))

    # ------------------------------------------------------------------
    # Recherche texte libre — générateurs de hits
    # ------------------------------------------------------------------

    def _text_hits(
        self,
        var: RobotVariable,
        backup_name: str,
        query: SearchQuery,
    ) -> Generator[SearchHit, None, None]:
        source = var.source_file.name if var.source_file else ""

        def hit(path: str, value: str | None) -> SearchHit:
            return SearchHit(
                backup_name   = backup_name,
                source_file   = source,
                variable_name = var.name,
                match_path    = path,
                match_value   = value,
            )

        if query.matches(var.name):
            yield hit(var.name, None)

        if query.matches(var.type_detail):
            yield hit(var.name, var.type_detail)

        if isinstance(var.value, str) and var.value:
            if query.matches(var.value):
                yield hit(var.name, var.value)
        elif isinstance(var.value, ArrayValue):
            yield from self._text_hits_in_array(
                var.value, var.name, backup_name, source, var.name, query)
        elif isinstance(var.value, PositionValue):
            for line in var.value.raw_lines:
                if query.matches(line):
                    yield hit(var.name, line)
                    break

        if var.fields:
            yield from self._text_hits_in_fields(
                var.fields, var.name, backup_name, source, query)

    def _text_hits_in_fields(
        self,
        fields: list[RobotVarField],
        var_name: str,
        backup_name: str,
        source: str,
        query: SearchQuery,
    ) -> Generator[SearchHit, None, None]:
        for fld in fields:
            path = fld.full_name

            def hit(value: str | None) -> SearchHit:
                return SearchHit(backup_name=backup_name, source_file=source,
                                 variable_name=var_name, match_path=path,
                                 match_value=value)

            # full_name en priorité : permet la saisie partielle de chemin.
            # "$HOSTENT[17].$H" matche "$HOSTENT[17].$H_ADDR" etc.
            if query.matches(fld.full_name):
                yield hit(fld.value if isinstance(fld.value, str) else None)
                continue  # full_name inclut déjà field_name — pas de doublon
            if query.matches(fld.field_name):
                yield hit(None)
            if query.matches(fld.type_detail):
                yield hit(fld.type_detail)
            if isinstance(fld.value, str) and fld.value:
                if query.matches(fld.value):
                    yield hit(fld.value)
            elif isinstance(fld.value, ArrayValue):
                yield from self._text_hits_in_array(
                    fld.value, path, backup_name, source, var_name, query)
            elif isinstance(fld.value, PositionValue):
                for line in fld.value.raw_lines:
                    if query.matches(line):
                        yield hit(line)
                        break

    def _text_hits_in_array(
        self,
        arr: ArrayValue,
        array_path: str,
        backup_name: str,
        source: str,
        var_name: str,
        query: SearchQuery,
    ) -> Generator[SearchHit, None, None]:
        for key, val in arr.items.items():
            idx   = "[" + ",".join(str(k) for k in key) + "]"
            ipath = f"{array_path}{idx}"

            def hit(value: str | None) -> SearchHit:
                return SearchHit(backup_name=backup_name, source_file=source,
                                 variable_name=var_name, match_path=ipath,
                                 match_value=value)

            if isinstance(val, str) and val:
                if query.matches(val):
                    yield hit(val)
            elif isinstance(val, PositionValue):
                if val.label and query.matches(val.label):
                    yield hit(val.label)
                else:
                    for line in val.raw_lines:
                        if query.matches(line):
                            yield hit(line)
                            break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _scope_ok(var: RobotVariable, scope: str) -> bool:
        if scope == "system":
            return var.is_system
        if scope == "karel":
            return not var.is_system
        return True

    @staticmethod
    def _value_str(value: object) -> str | None:
        """Sérialise une valeur en chaîne affichable."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, PositionValue):
            return " | ".join(value.raw_lines) if value.raw_lines else None
        if isinstance(value, ArrayValue):
            n = len(value.items)
            return f"Array({n} items)" if n else None
        return str(value)