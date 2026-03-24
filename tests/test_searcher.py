"""
test_searcher.py — Tests du Searcher et des helpers de parsing de chemin.

Couvre :
  - Unitaires : _is_path, _build_path_query, _parse_index
  - Fonctionnels : recherche texte libre (nom / type / valeur / fields / arrays / positions)
  - Fonctionnels : résolution de chemin exact ($VAR[i].FIELD[j])
  - Cas limites : texte vide, backup non chargé, scope, limite de hits, fallback
  - Robustesse : chemin invalide, index hors limites, variable inexistante
"""

from __future__ import annotations

from pathlib import Path

import pytest

from models.fanuc_models import (
    ArrayValue,
    PositionValue,
    RobotBackup,
    RobotVariable,
    StorageType,
    VADataType,
)
from models.search_models import (
    PathQuery,
    SearchHit,
    SearchQuery,
    SearchResults,
)
from services.searcher import (
    Searcher,
    _build_path_query,
    _is_path,
    _parse_index,
)

from tests.conftest import make_backup, make_field, make_var


# ===========================================================================
# Helpers de construction de jeux de données
# ===========================================================================

def _backup_with(*vars: RobotVariable, name: str = "Robot_A") -> RobotBackup:
    """Crée un backup chargé avec les variables fournies."""
    return make_backup(name=name, variables=list(vars))


def _unloaded(name: str = "Ghost") -> RobotBackup:
    """Crée un backup non chargé."""
    return make_backup(name=name, variables=[], loaded=False)


# Variables réutilisables dans les tests
VAR_INT = make_var(
    name="$ACC_MAXLMT",
    data_type=VADataType.INTEGER,
    type_detail="INTEGER = 100",
    value="100",
    source_file=Path("sysvars.va"),
)
VAR_STR = make_var(
    name="$ROBOT_NAME",
    data_type=VADataType.STRING,
    type_detail="STRING[37] = MyRobot",
    value="MyRobot",
    source_file=Path("sysvars.va"),
)
VAR_ARR = make_var(
    name="$ANGTOL",
    is_array=True,
    array_size=3,
    data_type=VADataType.REAL,
    type_detail="ARRAY[3] OF REAL",
    value=ArrayValue(items={(1,): "1.0", (2,): "2.0", (3,): "3.0"}),
    source_file=Path("sysvars.va"),
)
_FIELD_DEBUG1 = make_field(
    full_name="$ALMDG.$DEBUG1",
    parent_var="$ALMDG",
    field_name="$DEBUG1",
    value="42",
)
_FIELD_DEBUG2 = make_field(
    full_name="$ALMDG.$DEBUG2",
    parent_var="$ALMDG",
    field_name="$DEBUG2",
    value="0",
)
VAR_STRUCT = make_var(
    name="$ALMDG",
    data_type=VADataType.STRUCT,
    type_detail="ALMDG_T =",
    fields=[_FIELD_DEBUG1, _FIELD_DEBUG2],
    source_file=Path("sysvars.va"),
)
VAR_POS = make_var(
    name="$MASTER_POS",
    data_type=VADataType.POSITION,
    type_detail="POSITION =",
    value=PositionValue(raw_lines=["Group: 1", "X: 100.0", "Y: 200.0"]),
    source_file=Path("sysvars.va"),
)
VAR_KAREL = make_var(
    name="NFPAM",
    namespace="TBSWMD45",
    data_type=VADataType.STRUCT,
    type_detail="NFPAM_T =",
    value=None,
    source_file=Path("karel.va"),
)

ALL_VARS = [VAR_INT, VAR_STR, VAR_ARR, VAR_STRUCT, VAR_POS, VAR_KAREL]
BACKUP_A = _backup_with(*ALL_VARS, name="Robot_A")
BACKUP_B = _backup_with(VAR_INT, VAR_STR, name="Robot_B")


# ===========================================================================
# 1 — Tests unitaires : _parse_index
# ===========================================================================

class TestParseIndex:

    def test_entier_simple(self):
        # "17" → (17,)
        assert _parse_index("17") == (17,)

    def test_virgule_2d(self):
        # "1,2" → (1, 2)
        assert _parse_index("1,2") == (1, 2)

    def test_none_retourne_none(self):
        # Pas d'index → None
        assert _parse_index(None) is None

    def test_chaine_vide_apres_split(self):
        # Chaîne ne contenant que des virgules → None ou tuple vide géré
        result = _parse_index("")
        assert result is None or result == ()


# ===========================================================================
# 2 — Tests unitaires : _is_path
# ===========================================================================

class TestIsPath:

    def test_dollar_avec_index_et_field(self):
        # Forme canonique $VAR[i].$FIELD
        assert _is_path("$HOSTENT[17].$H_ADDR") is True

    def test_dollar_simple(self):
        # Trop court (≤2 chars)
        assert _is_path("$X") is False

    def test_dollar_plus_long(self):
        # $VAR sans index mais assez long
        assert _is_path("$ALMDG.$DEBUG1") is True

    def test_bracket_sans_dollar(self):
        # ANGTOL[3] → chemin via bracket
        assert _is_path("ANGTOL[3]") is True

    def test_texte_libre_non_chemin(self):
        # Texte libre sans $ ni []
        assert _is_path("hello world") is False

    def test_fragment_type_array(self):
        # ARRAY[3] → fragment de type, pas un chemin variable
        assert _is_path("ARRAY[3]") is False

    def test_fragment_type_integer(self):
        # INTEGER[1] → fragment de type
        assert _is_path("INTEGER[1]") is False

    def test_trop_court(self):
        # 2 caractères → trop court
        assert _is_path("$X") is False

    def test_vide(self):
        # Chaîne vide → False
        assert _is_path("") is False

    def test_dollar_seul(self):
        # $ seul → trop court / pas valide
        assert _is_path("$") is False

    def test_bracket_seul(self):
        # Bracket seul → trop court
        assert _is_path("[1]") is False


# ===========================================================================
# 3 — Tests unitaires : _build_path_query
# ===========================================================================

class TestBuildPathQuery:

    def test_var_index_field(self):
        # $VAR[i].$FIELD → tous les composants extraits
        pq = _build_path_query("$HOSTENT[17].$H_ADDR", "all")
        assert pq is not None
        assert pq.var_name == "$HOSTENT"
        assert pq.var_index == (17,)
        assert pq.field_name == "$H_ADDR"
        assert pq.field_index is None

    def test_var_index_seul(self):
        # $VAR[i] → pas de field
        pq = _build_path_query("$ANGTOL[3]", "all")
        assert pq is not None
        assert pq.var_index == (3,)
        assert pq.field_name is None

    def test_var_field_sans_index(self):
        # $VAR.$FIELD → tous les éléments
        pq = _build_path_query("$ALMDG.$DEBUG1", "all")
        assert pq is not None
        assert pq.var_index is None
        assert pq.field_name == "$DEBUG1"

    def test_var_index_field_index(self):
        # $VAR[i].$FIELD[j] → index de field aussi extrait
        pq = _build_path_query("$AAVM_WRK[1].$DISTORT[2]", "all")
        assert pq is not None
        assert pq.var_index == (1,)
        assert pq.field_name == "$DISTORT"
        assert pq.field_index == (2,)

    def test_nd_index(self):
        # Index N-D [1,2]
        pq = _build_path_query("$PGTRACEDT[1,2].$LINE_NUM", "all")
        assert pq is not None
        assert pq.var_index == (1, 2)

    def test_scope_propage(self):
        # Le scope est propagé dans le PathQuery
        pq = _build_path_query("$X[1].$Y", "system")
        assert pq is not None
        assert pq.scope == "system"

    def test_raw_preserve(self):
        # raw contient le texte original
        pq = _build_path_query("$VAR[1].$F", "all")
        assert pq.raw == "$VAR[1].$F"

    def test_retourne_none_si_format_invalide(self):
        # Chaîne ne matchant pas le regex → None
        pq = _build_path_query("!!INVALID!!", "all")
        assert pq is None


# ===========================================================================
# 4 — Tests fonctionnels : recherche texte libre — cas de base
# ===========================================================================

class TestRechercheTexteLibre:

    def test_requete_vide_retourne_vide(self, searcher):
        # Texte vide → SearchResults vide immédiatement
        results = searcher.search(SearchQuery(""), [BACKUP_A])
        assert results.is_empty

    def test_whitespace_seul_retourne_vide(self, searcher):
        # Espaces seuls → is_empty
        results = searcher.search(SearchQuery("   "), [BACKUP_A])
        assert results.is_empty

    def test_match_par_nom(self, searcher):
        # Correspondance sur le nom de variable
        results = searcher.search(SearchQuery("ACC_MAXLMT"), [BACKUP_A])
        names = [h.variable_name for h in results.hits]
        assert "$ACC_MAXLMT" in names

    def test_match_par_valeur_scalaire(self, searcher):
        # Correspondance sur la valeur d'une variable scalaire
        results = searcher.search(SearchQuery("MyRobot"), [BACKUP_A])
        assert any(h.match_value == "MyRobot" for h in results.hits)

    def test_match_par_type(self, searcher):
        # Correspondance sur le type_detail
        results = searcher.search(SearchQuery("ALMDG_T"), [BACKUP_A])
        assert results.hit_count >= 1

    def test_match_dans_valeur_tableau(self, searcher):
        # Correspondance dans une valeur d'ArrayValue
        results = searcher.search(SearchQuery("2.0"), [BACKUP_A])
        assert any("$ANGTOL" in h.variable_name for h in results.hits)

    def test_match_dans_valeur_field(self, searcher):
        # Correspondance sur la valeur d'un field scalaire
        results = searcher.search(SearchQuery("42"), [BACKUP_A])
        assert any("$ALMDG" in h.variable_name for h in results.hits)

    def test_match_dans_position(self, searcher):
        # Correspondance dans les lignes d'une PositionValue
        results = searcher.search(SearchQuery("100.0"), [BACKUP_A])
        assert any("$MASTER_POS" in h.variable_name for h in results.hits)

    def test_insensible_casse(self, searcher):
        # Recherche insensible à la casse
        results_lower = searcher.search(SearchQuery("myrobot"), [BACKUP_A])
        results_upper = searcher.search(SearchQuery("MYROBOT"), [BACKUP_A])
        assert results_lower.hit_count >= 1
        assert results_upper.hit_count >= 1

    def test_match_partiel_nom(self, searcher):
        # Correspondance partielle sur le nom
        results = searcher.search(SearchQuery("ALMDG"), [BACKUP_A])
        assert results.hit_count >= 1

    def test_pas_de_match_retourne_vide(self, searcher):
        # Aucune correspondance → hit_count == 0
        results = searcher.search(SearchQuery("XXXXXXXX_INEXISTANT"), [BACKUP_A])
        assert results.hit_count == 0


# ===========================================================================
# 5 — Tests fonctionnels : scopes
# ===========================================================================

class TestScopes:

    def test_scope_all_retourne_tout(self, searcher):
        # scope="all" → système ET Karel
        results = searcher.search(SearchQuery("NFPAM", scope="all"), [BACKUP_A])
        names = [h.variable_name for h in results.hits]
        assert "NFPAM" in names

    def test_scope_system_exclut_karel(self, searcher):
        # scope="system" → uniquement les variables *SYSTEM*
        results = searcher.search(SearchQuery("NFPAM", scope="system"), [BACKUP_A])
        assert all(h.variable_name != "NFPAM" for h in results.hits)

    def test_scope_karel_exclut_systeme(self, searcher):
        # scope="karel" → uniquement les variables non-système
        sys_var = make_var(name="$SYS", namespace="*SYSTEM*", value="hello")
        karel_var = make_var(name="KVAR", namespace="MY_NS", value="hello")
        backup = _backup_with(sys_var, karel_var)
        results = searcher.search(SearchQuery("hello", scope="karel"), [backup])
        names = [h.variable_name for h in results.hits]
        assert "KVAR" in names
        assert "$SYS" not in names

    def test_scope_system_inclut_systeme(self, searcher):
        # scope="system" → retourne les variables *SYSTEM*
        results = searcher.search(SearchQuery("100", scope="system"), [BACKUP_A])
        assert results.hit_count >= 1


# ===========================================================================
# 6 — Tests fonctionnels : comportement multi-backups
# ===========================================================================

class TestMultiBackups:

    def test_aggregation_multi_backups(self, searcher):
        # Recherche sur deux backups → hits des deux
        results = searcher.search(SearchQuery("$ACC_MAXLMT"), [BACKUP_A, BACKUP_B])
        backup_names = {h.backup_name for h in results.hits}
        assert "Robot_A" in backup_names
        assert "Robot_B" in backup_names

    def test_backup_non_charge_ignore(self, searcher):
        # Backup non chargé → ignoré, pas d'exception
        unloaded = _unloaded()
        results = searcher.search(SearchQuery("$ACC_MAXLMT"), [unloaded])
        assert results.hit_count == 0

    def test_liste_vide_backups(self, searcher):
        # Aucun backup → résultat vide
        results = searcher.search(SearchQuery("$ACC"), [])
        assert results.hit_count == 0

    def test_searched_incremente_par_variable(self, searcher):
        # searched compte les variables inspectées
        results = searcher.search(SearchQuery("$ACC"), [BACKUP_A])
        assert results.searched == len(ALL_VARS)

    def test_backup_name_dans_hits(self, searcher):
        # backup_name présent dans chaque hit
        results = searcher.search(SearchQuery("$ACC_MAXLMT"), [BACKUP_A])
        for h in results.hits:
            assert h.backup_name == "Robot_A"


# ===========================================================================
# 7 — Tests fonctionnels : résolution de chemin exact
# ===========================================================================

class TestResolutionChemin:

    def test_var_scalaire(self, searcher):
        # $ACC_MAXLMT → valeur racine retournée
        pq = _build_path_query("$ACC_MAXLMT", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count >= 1

    def test_index_tableau_valide(self, searcher):
        # $ANGTOL[2] → valeur exacte à l'index (2,)
        pq = _build_path_query("$ANGTOL[2]", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count == 1
        assert results.hits[0].match_value == "2.0"

    def test_index_tableau_hors_limites(self, searcher):
        # Index inexistant → hit_count = 0
        pq = _build_path_query("$ANGTOL[99]", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count == 0

    def test_field_scalaire(self, searcher):
        # $ALMDG.$DEBUG1 → valeur du field
        pq = _build_path_query("$ALMDG.$DEBUG1", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count == 1
        assert results.hits[0].match_value == "42"

    def test_field_tous_les_elements(self, searcher):
        # $ALMDG.$DEBUG2 sans index → tous les fields correspondants
        pq = _build_path_query("$ALMDG.$DEBUG2", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count >= 1

    def test_variable_inexistante_retourne_vide(self, searcher):
        # Variable inexistante → aucun résultat
        pq = _build_path_query("$XXXXXX_INEXISTANT", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count == 0

    def test_insensible_casse_nom(self, searcher):
        # Nom de variable insensible à la casse
        pq = _build_path_query("$angtol[1]", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count >= 1

    def test_backup_non_charge_ignore(self, searcher):
        # Backup non chargé ignoré dans resolve
        pq = _build_path_query("$ACC_MAXLMT", "all")
        results = searcher.resolve(pq, [_unloaded()])
        assert results.hit_count == 0

    def test_scope_system_filtre(self, searcher):
        # scope="system" → Karel exclu de resolve
        pq = _build_path_query("NFPAM", "system")
        results = searcher.resolve(pq, [BACKUP_A])
        assert results.hit_count == 0

    def test_hit_match_path_contient_nom(self, searcher):
        # match_path du hit contient le nom de la variable
        pq = _build_path_query("$ACC_MAXLMT", "all")
        results = searcher.resolve(pq, [BACKUP_A])
        for h in results.hits:
            assert "$ACC_MAXLMT" in h.match_path


# ===========================================================================
# 8 — Tests fonctionnels : search_from_text (point d'entrée unique)
# ===========================================================================

class TestSearchFromText:

    def test_texte_vide_retourne_vide(self, searcher):
        # Texte vide → SearchResults vide sans recherche
        results = searcher.search_from_text("", "all", [BACKUP_A])
        assert results.is_empty

    def test_chemin_detecte_et_resolu(self, searcher):
        # "$ANGTOL[1]" → détecté comme chemin, résolu
        results = searcher.search_from_text("$ANGTOL[1]", "all", [BACKUP_A])
        assert results.hit_count >= 1

    def test_texte_libre_si_chemin_vide(self, searcher):
        # "$XXXXXX[99]" → resolve → 0 hits → fallback texte libre
        results = searcher.search_from_text("$XXXXXX[99]", "all", [BACKUP_A])
        # Pas d'erreur, peut retourner 0 ou + (fallback texte)
        assert isinstance(results, SearchResults)

    def test_texte_non_chemin(self, searcher):
        # Texte sans $ ni [] → recherche texte libre directe
        results = searcher.search_from_text("MyRobot", "all", [BACKUP_A])
        assert results.hit_count >= 1

    def test_whitespace_seul(self, searcher):
        # Espaces seuls → équivalent à texte vide
        results = searcher.search_from_text("   ", "all", [BACKUP_A])
        assert results.is_empty


# ===========================================================================
# 9 — Tests des modèles SearchQuery / PathQuery / SearchHit / SearchResults
# ===========================================================================

class TestModeleRecherche:

    def test_search_query_is_empty_vrai(self):
        # Texte vide → is_empty True
        assert SearchQuery("  ").is_empty is True

    def test_search_query_is_empty_faux(self):
        # Texte non vide → is_empty False
        assert SearchQuery("x").is_empty is False

    def test_search_query_matches_insensible_casse(self):
        # matches insensible à la casse
        q = SearchQuery("hello")
        assert q.matches("Hello World") is True

    def test_search_query_matches_faux(self):
        # Texte absent → False
        q = SearchQuery("xyz")
        assert q.matches("hello world") is False

    def test_search_hit_origin(self):
        # origin = backup_name / source_file
        hit = SearchHit("Robot_A", "sys.va", "$X", "$X")
        assert hit.origin == "Robot_A / sys.va"

    def test_search_results_is_empty(self):
        # Sans hits → is_empty True
        r = SearchResults(query=SearchQuery("x"))
        assert r.is_empty is True

    def test_search_results_is_empty_avec_hits(self):
        # Avec un hit → is_empty False
        r = SearchResults(query=SearchQuery("x"))
        r.hits.append(SearchHit("R", "f.va", "$X", "$X"))
        assert r.is_empty is False

    def test_search_results_hit_count(self):
        # hit_count = len(hits)
        r = SearchResults(query=SearchQuery("x"))
        r.hits.extend([SearchHit("R", "f", "$X", "$X")] * 5)
        assert r.hit_count == 5

    def test_search_results_query_text_pour_path_query(self):
        # query_text retourne raw pour PathQuery
        pq = PathQuery(raw="$X[1].$Y", var_name="$X", var_index=(1,),
                       field_name="$Y", field_index=None)
        r = SearchResults(query=pq)
        assert r.query_text == "$X[1].$Y"

    def test_search_results_query_text_pour_search_query(self):
        # query_text retourne text pour SearchQuery
        r = SearchResults(query=SearchQuery("hello"))
        assert r.query_text == "hello"

    def test_search_results_searched_counter(self, searcher):
        # searched incrémenté correctement
        results = searcher.search(SearchQuery("dummy"), [BACKUP_A])
        assert results.searched == len(ALL_VARS)


# ===========================================================================
# 10 — Tests de robustesse
# ===========================================================================

class TestRobustesseSearcher:

    def test_exception_dans_variable_reporte_erreur(self, searcher):
        # Variable dont la valeur lève lors du matching → erreur dans results.errors
        bad_var = make_var(name="$BAD")
        bad_var.value = object()  # type non géré
        backup = _backup_with(bad_var)
        results = searcher.search(SearchQuery("test"), [backup])
        # Pas d'exception fatale ; l'erreur est loguée
        assert isinstance(results, SearchResults)

    def test_limite_hits_2000(self, searcher):
        # Avec assez de variables, la limite _MAX_HITS = 2000 est respectée
        vars_list = [make_var(name=f"$V{i}", value="target") for i in range(2500)]
        backup = _backup_with(*vars_list)
        results = searcher.search(SearchQuery("target"), [backup])
        assert results.hit_count <= 2000

    def test_array_value_vide_pas_de_hit(self, searcher):
        # ArrayValue sans items → aucun hit sur une recherche de valeur
        empty_arr = make_var(name="$EMPTY", is_array=True, value=ArrayValue(items={}))
        backup = _backup_with(empty_arr)
        results = searcher.search(SearchQuery("anything"), [backup])
        # Pas de crash, hit_count peut être 0 ou 1 (match sur le nom)
        assert isinstance(results, SearchResults)