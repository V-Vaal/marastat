"""Test de bout en bout : des PDF au rapport, sur le jeu de demonstration.

Ce test fabrique les factures de demonstration, fait tourner la chaine
complete, et verifie sur le resultat chacune des promesses tenues par le
README. C'est le seul test qui traverse tout l'outil ; les autres ciblent une
fonction a la fois.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from aide import connexion

RACINE = Path(__file__).resolve().parents[1]

reportlab = pytest.importorskip(
    "reportlab", reason="reportlab n'est requis que pour fabriquer le jeu de demo"
)


def _generateur():
    chemin = RACINE / "outils" / "generer_demo.py"
    spec = importlib.util.spec_from_file_location("generer_demo", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generer_demo"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def campagne(tmp_path_factory):
    """Genere les factures, lance la chaine, retourne le dossier de sortie."""
    from marastat.cli import main

    base = tmp_path_factory.mktemp("campagne")
    factures = base / "factures"
    sortie = base / "rapport"

    generateur = _generateur()
    generateur.main(["--sortie", str(factures)])

    code = main([
        "--factures", str(factures),
        "--sortie", str(sortie),
        "--sans-ouverture",
        "--sans-notification",
    ])
    assert code == 0
    return sortie


def test_les_trois_livrables_sont_publies(campagne: Path) -> None:
    for nom in ("rapport.html", "ventes.csv", "ventes.sqlite"):
        assert (campagne / nom).is_file(), nom
    # Aucun temporaire ne doit survivre a une execution reussie.
    assert not list(campagne.glob(".*.tmp"))


def test_toute_facture_integree_reconcilie_au_centime(campagne: Path) -> None:
    """L'invariant central : somme des lignes == total HT imprime."""
    with connexion(campagne / "ventes.sqlite") as cx:
        ecarts = cx.execute("""
            SELECT numero, total_ht_imprime, total_ht_calcule
            FROM factures
            WHERE ABS(total_ht_imprime - total_ht_calcule) >= 0.02
        """).fetchall()
    assert ecarts == []


def test_les_totaux_de_la_base_et_de_l_export_concordent(campagne: Path) -> None:
    """Base, export et rapport doivent decrire la meme campagne."""
    with connexion(campagne / "ventes.sqlite") as cx:
        somme_base, nb_base = cx.execute(
            "SELECT ROUND(SUM(total_ht), 2), COUNT(*) FROM lignes"
        ).fetchone()

    lignes_csv = (campagne / "ventes.csv").read_text(
        encoding="utf-8-sig"
    ).strip().splitlines()
    assert len(lignes_csv) - 1 == nb_base

    entete = lignes_csv[0].split(";")
    colonne = entete.index("total_ht")
    somme_csv = round(
        sum(float(ligne.split(";")[colonne]) for ligne in lignes_csv[1:]), 2
    )
    assert somme_csv == pytest.approx(somme_base, abs=0.01)

    page = (campagne / "rapport.html").read_text(encoding="utf-8")
    assert f'"ca_total":{somme_base}' in page


def test_une_facture_au_total_faux_est_ecartee_et_non_corrigee(
    campagne: Path,
) -> None:
    with connexion(campagne / "ventes.sqlite") as cx:
        presente = cx.execute(
            "SELECT COUNT(*) FROM factures WHERE numero = 'F-2025-9003'"
        ).fetchone()[0]
    assert presente == 0


def test_une_copie_a_l_identique_n_est_comptee_qu_une_fois(campagne: Path) -> None:
    with connexion(campagne / "ventes.sqlite") as cx:
        fichiers = cx.execute(
            "SELECT fichier FROM factures WHERE numero = 'F-2024-0004'"
        ).fetchall()
    assert len(fichiers) == 1


def test_deux_factures_contradictoires_sont_toutes_deux_ecartees(
    campagne: Path,
) -> None:
    with connexion(campagne / "ventes.sqlite") as cx:
        presente = cx.execute(
            "SELECT COUNT(*) FROM factures WHERE numero = 'F-2025-9002'"
        ).fetchone()[0]
    assert presente == 0


def test_une_ligne_sans_libelle_reste_visible_sans_etre_devinee(
    campagne: Path,
) -> None:
    with connexion(campagne / "ventes.sqlite") as cx:
        nb, montant = cx.execute("""
            SELECT COUNT(*), ROUND(SUM(total_ht), 2) FROM lignes
            WHERE classement = 'non identifie'
        """).fetchone()
    assert nb > 0
    assert montant > 0
    # Elle est bien comptee dans le chiffre d'affaires, pas ecartee.
    with connexion(campagne / "ventes.sqlite") as cx:
        total = cx.execute("SELECT ROUND(SUM(total_ht), 2) FROM lignes").fetchone()[0]
    assert montant < total

    arbitrage = (campagne / "arbitrage.csv").read_text(encoding="utf-8-sig")
    assert "legume;famille" in arbitrage


def test_le_gabarit_sans_colonne_ref_est_lu_comme_les_autres(
    campagne: Path,
) -> None:
    """Le changement de gabarit de 2026 ne doit rien casser."""
    with connexion(campagne / "ventes.sqlite") as cx:
        lignes_2026 = cx.execute(
            "SELECT COUNT(*) FROM lignes WHERE annee = 2026"
        ).fetchone()[0]
        refs_2026 = cx.execute(
            "SELECT COUNT(*) FROM lignes WHERE annee = 2026 AND ref <> ''"
        ).fetchone()[0]
    assert lignes_2026 > 0
    assert refs_2026 == 0  # la colonne n'existe plus, le reste est lu quand meme


def test_une_designation_longue_est_recollee_sur_une_seule_ligne(
    campagne: Path,
) -> None:
    with connexion(campagne / "ventes.sqlite") as cx:
        libelle = cx.execute("""
            SELECT libelle FROM lignes
            WHERE facture = 'F-2025-9001' AND legume LIKE 'Pomme de terre%'
        """).fetchone()[0]
    assert "en sac papier" in libelle


def test_une_designation_piegee_n_est_pas_executable_dans_un_tableur(
    campagne: Path,
) -> None:
    contenu = (campagne / "ventes.csv").read_text(encoding="utf-8-sig")
    assert "'=Carotte" in contenu
    assert ";=Carotte" not in contenu


def test_toutes_les_lignes_portent_une_unite_tracee(campagne: Path) -> None:
    """Une unite deduite doit toujours etre signalee comme telle."""
    with connexion(campagne / "ventes.sqlite") as cx:
        sources = {
            r[0] for r in cx.execute("SELECT DISTINCT unite_source FROM lignes")
        }
    assert sources <= {"facture", "deduite", "inconnu"}


def test_le_jeu_de_demonstration_ne_declenche_aucune_alerte_de_date(
    campagne: Path,
) -> None:
    """Les dates du jeu sont toutes plausibles : aucun faux positif attendu."""
    page = (campagne / "rapport.html").read_text(encoding="utf-8")
    assert '"dates_suspectes":[]' in page


RAPPORT_PUBLIE = RACINE / "exemples" / "rapport-demo.html"

# La date de génération est la seule donnée qui change d'une exécution à
# l'autre. Tout le reste découle du jeu de démonstration, qui est déterministe.
DATE_DE_GENERATION = re.compile(r'"genere_le":"[^"]*"')


def _hors_date(page: str) -> str:
    return DATE_DE_GENERATION.sub('"genere_le":"…"', page)


def test_le_rapport_publie_correspond_bien_au_code(campagne: Path) -> None:
    """`exemples/rapport-demo.html` est un fichier généré, versionné à la main.

    Rien n'empêcherait qu'il dérive du code et finisse par montrer un rapport
    produit par une version disparue : c'est le défaut habituel des artefacts
    committés. Ce test l'interdit en comparant le fichier publié à celui que le
    code produit maintenant, sur le même jeu de démonstration déterministe.
    """
    assert RAPPORT_PUBLIE.is_file(), f"{RAPPORT_PUBLIE} manquant"

    frais = (campagne / "rapport.html").read_text(encoding="utf-8")
    publie = RAPPORT_PUBLIE.read_text(encoding="utf-8")

    assert _hors_date(publie) == _hors_date(frais), (
        "Le rapport publié ne correspond plus à ce que produit le code.\n"
        "Le régénérer :\n"
        "    python outils/controle.py --rafraichir-demo"
    )
