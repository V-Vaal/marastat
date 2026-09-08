from __future__ import annotations

import math

import pytest

from marastat import parser
from marastat.parser import (
    extraire_nom_client,
    nettoyer_nombre,
    texte_continuation,
    total_ligne_valide,
)


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("0", 0.0),
        ("2,40", 2.4),
        ("-12,50", -12.5),
        ("1 000,00", 1000.0),
        ("1\u00a0000,00 €", 1000.0),
        ("1\u202f000,00", 1000.0),
        ("12.5", 12.5),
    ],
)
def test_nombres_valides(texte: str, attendu: float) -> None:
    assert nettoyer_nombre(texte) == attendu


@pytest.mark.parametrize(
    "texte",
    ["", "-", "10 2,40", "1 23,50", "1 00,00", "nan", "inf", "-inf", "1e3"],
)
def test_nombres_ambigus_ou_non_finis_sont_refuses(texte: str) -> None:
    valeur = nettoyer_nombre(texte)
    assert valeur is None or math.isfinite(valeur)
    assert valeur is None


def test_controle_du_total_de_ligne() -> None:
    assert total_ligne_valide(10, 2.4, None, 24)
    assert total_ligne_valide(10, 2.4, 10, 21.6)
    assert not total_ligne_valide(10, 2.4, None, 102.4)
    assert not total_ligne_valide(10, 2.4, 10, 24)


def test_continuation_limitee_a_la_designation_sans_nombre() -> None:
    suite = texte_continuation({"libelle": "ancienne", "ref": "", "taxe": ""})
    assert suite == "ancienne"
    assert texte_continuation({"libelle": "ancienne", "ref": "PDT", "taxe": ""}) is None
    assert texte_continuation({"libelle": "colis de 6", "ref": "", "taxe": ""}) is None
    assert texte_continuation({"libelle": "ancienne", "unite": "kg", "taxe": ""}) is None


def test_nom_client_lu_dans_le_bloc_destinataire() -> None:
    mots = [
        {"top": 31.0, "x0": 485.0, "text": "Le"},
        {"top": 87.0, "x0": 18.0, "text": "EARL des Trois Sillons"},
        {"top": 130.0, "x0": 322.0, "text": "Cantine"},
        {"top": 130.0, "x0": 360.0, "text": "Municipale"},
        {"top": 130.0, "x0": 414.0, "text": "Bourgneuf"},
        {"top": 140.0, "x0": 322.0, "text": "15 rue des Jonquilles"},
        {"top": 224.0, "x0": 16.0, "text": "Facture"},
    ]
    assert extraire_nom_client(mots, 595.0) == "Cantine Municipale Bourgneuf"


def test_nom_client_absent_ne_bloque_pas_le_parseur() -> None:
    mots = [
        {"top": 87.0, "x0": 18.0, "text": "EARL des Trois Sillons"},
        {"top": 224.0, "x0": 16.0, "text": "Facture"},
    ]
    assert extraire_nom_client(mots, 595.0) == ""


def test_civilite_du_destinataire_n_encombre_pas_le_rapport() -> None:
    mots = [
        {"top": 130.0, "x0": 322.0, "text": "Mme"},
        {"top": 130.0, "x0": 350.0, "text": "Épicerie"},
        {"top": 130.0, "x0": 380.0, "text": "du Bourg"},
        {"top": 224.0, "x0": 16.0, "text": "Facture"},
    ]
    assert extraire_nom_client(mots, 595.0) == "Épicerie du Bourg"


def test_progression_avance_meme_si_un_pdf_est_illisible(
    tmp_path, monkeypatch,
) -> None:
    (tmp_path / "a.pdf").touch()
    (tmp_path / "b.pdf").touch()

    def lecture(chemin):
        if chemin.name == "b.pdf":
            raise parser.FactureIllisible("test")
        return chemin.name

    monkeypatch.setattr(parser, "lire_facture", lecture)
    appels = []
    factures, erreurs = parser.lire_dossier(
        tmp_path, lambda position, total: appels.append((position, total))
    )

    assert factures == ["a.pdf"]
    assert erreurs == [("b.pdf", "FactureIllisible: test")]
    assert appels == [(1, 2), (2, 2)]
