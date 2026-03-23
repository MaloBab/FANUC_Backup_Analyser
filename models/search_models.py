"""
Modèles de données pour la recherche globale de variables.

Sans dépendance à Tkinter ou aux services — dataclasses pures.
"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SearchQuery:
    """Paramètres d'une recherche texte libre.

    :param text:  chaîne cherchée (insensible à la casse).
    :param scope: ``"all"``, ``"system"`` ou ``"karel"``.
    """
    text:  str
    scope: str = "all"

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    def matches(self, candidate: str) -> bool:
        """``candidate`` contient-il le texte (insensible à la casse) ?"""
        return self.text.lower() in candidate.lower()


@dataclass(frozen=True)
class PathQuery:
    """Résolution d'un chemin FANUC exact.

    Forme générale : ``VAR[i].FIELD[j]``

    Exemples supportés :
      - ``$HOSTENT[17].$H_ADDR``      → var + index + field
      - ``$HOSTENT[17]``              → var + index (élément d'un array de structs)
      - ``$AAVM_WRK[1].$DISTORT[2]`` → var + index + field + index de field
      - ``$AIO_CNV[1].$RACK``         → var + index + field scalaire
      - ``$HOSTENT.$H_ADDR``          → var + field (sans index → tous les éléments)

    :param raw:         texte original saisi par l'utilisateur.
    :param var_name:    nom de la variable racine (ex: ``"$HOSTENT"``).
    :param var_index:   index de l'élément (ex: ``(17,)``), ou ``None`` → tous.
    :param field_name:  nom du field (ex: ``"$H_ADDR"``), ou ``None``.
    :param field_index: index dans un field tableau (ex: ``(2,)``), ou ``None``.
    :param scope:       ``"all"``, ``"system"`` ou ``"karel"``.
    """
    raw:         str
    var_name:    str
    var_index:   tuple[int, ...] | None
    field_name:  str | None
    field_index: tuple[int, ...] | None
    scope:       str = "all"


@dataclass
class SearchHit:
    """Un résultat atomique : variable + chemin + valeur résolue.

    :param backup_name:   nom du dossier backup (ex: ``"Robot_A"``).
    :param source_file:   nom du fichier .VA (ex: ``"sysvars.va"``).
    :param variable_name: nom de la variable racine.
    :param match_path:    chemin complet jusqu'à la valeur.
    :param match_value:   valeur qui a matché, ou ``None`` si match sur nom/type.
    """
    backup_name:   str
    source_file:   str
    variable_name: str
    match_path:    str
    match_value:   str | None = None

    @property
    def origin(self) -> str:
        return f"{self.backup_name} / {self.source_file}"


@dataclass
class SearchResults:
    """Résultat complet d'une recherche (texte libre ou chemin).

    :param query:    la requête (``SearchQuery`` ou ``PathQuery``).
    :param hits:     liste ordonnée des résultats.
    :param searched: nombre de variables inspectées.
    :param errors:   erreurs non-fatales éventuelles.
    """
    query:    SearchQuery | PathQuery
    hits:     list[SearchHit] = field(default_factory=list)
    searched: int             = 0
    errors:   list[str]       = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def query_text(self) -> str:
        """Texte brut de la requête pour l'affichage."""
        if isinstance(self.query, PathQuery):
            return self.query.raw
        return self.query.text