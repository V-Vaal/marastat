from __future__ import annotations

import csv
from pathlib import Path

from marastat.catalogue import NON_IDENTIFIE, charger_regles
from marastat.etl import (
    charger_arbitrage,
    classer_ligne,
    nom_client_facture,
    selectionner_factures,
    unites_reference,
)
from marastat.parser import Facture, Ligne


def _ligne(libelle: str = "Tomate", total: float = 24.0, unite: str = "kg") -> Ligne:
    return Ligne(
        ref="", libelle=libelle, libelle_propre=libelle, unite=unite,
        quantite=10.0, pu_ht=2.4, pu_ttc=None, remise=None,
        total_ht=total, total_ttc=None, taxe="TVA 5,5", page=1,
    )


def _facture(fichier: str, ligne: Ligne, numero: str = "F-2026-0001") -> Facture:
    return Facture(
        fichier=fichier, numero=numero, date="2026-01-31", code_client="1042",
        total_ht_imprime=ligne.total_ht, lignes=[ligne],
    )


def test_doublons_identiques_comptes_une_fois() -> None:
    a, b = _facture("a.pdf", _ligne()), _facture("b.pdf", _ligne())
    retenues, rejetees, doublons, conflits = selectionner_factures([a, b])
    assert retenues == [a]
    assert not rejetees and not conflits
    assert len(doublons) == 1


def test_meme_numero_et_total_mais_lignes_differentes_est_un_conflit() -> None:
    a = _facture("a.pdf", _ligne("Tomate"))
    b = _facture("b.pdf", _ligne("Carotte"))
    retenues, rejetees, doublons, conflits = selectionner_factures([a, b])
    assert not retenues and not rejetees and not doublons
    assert {fichier for fichier, _ in conflits} == {"a.pdf", "b.pdf"}


def test_une_erreur_de_ligne_invalide_la_facture() -> None:
    facture = _facture("a.pdf", _ligne())
    facture.erreurs_lignes.append("calcul incoherent")
    retenues, rejetees, _, _ = selectionner_factures([facture])
    assert not retenues
    assert rejetees == [("a.pdf", "calcul incoherent")]


def test_arbitrage_accepte_excel_famille_absente_et_reference_vide(
    tmp_path: Path,
) -> None:
    chemin = tmp_path / "arbitrage.csv"
    with chemin.open("w", encoding="utf-8", newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=";")
        ecrivain.writerow(["ref", "unite", "pu_ht", "annee", "nb_lignes", "total_ht",
                           "pistes_catalogue", "factures", "legume", "famille"])
        ecrivain.writerow(["", "kg", "2,40", "2025", "1", "24,00",
                           "une piste;avec séparateur", "F-1", "Tomate"])
    decisions, erreurs = charger_arbitrage(chemin)
    assert erreurs == []
    assert decisions == {"|kg|2.40|2025": ("Tomate", "Tomate")}


def test_arbitrage_invalide_est_signale(tmp_path: Path) -> None:
    chemin = tmp_path / "arbitrage.csv"
    chemin.write_text("X;kg;prix;2025;1;24;piste;F-1;Tomate;Famille\n", encoding="utf-8")
    decisions, erreurs = charger_arbitrage(chemin)
    assert decisions == {}
    assert erreurs == [(1, "prix illisible")]


def test_non_identifie_ne_fournit_jamais_d_unite_de_reference() -> None:
    inconnue = _facture("a.pdf", _ligne("", unite="kg"))
    assert unites_reference([inconnue], charger_regles()).get(NON_IDENTIFIE) is None


def test_classement_ignore_le_texte_ajoute_apres_la_ligne() -> None:
    ligne = _ligne("Tomate")
    ligne.libelle = "Tomate colis de 6 mini pots"
    assert classer_ligne(ligne, charger_regles())[0] == "Tomate"


def test_nom_client_vient_du_pdf_sans_catalogue() -> None:
    facture = _facture("a.pdf", _ligne())
    facture.nom_client = "Client imprimé"
    assert nom_client_facture(facture, {}) == "Client imprimé"
    assert nom_client_facture(facture, {"1042": "Ancien nom"}) == "Client imprimé"


def test_nom_client_a_des_replis_sans_bloquer() -> None:
    facture = _facture("a.pdf", _ligne())
    assert nom_client_facture(facture, {"1042": "Catalogue"}) == "Catalogue"
    assert nom_client_facture(facture, {}) == "Client 1042"
