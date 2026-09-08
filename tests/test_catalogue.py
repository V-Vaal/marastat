from __future__ import annotations

from pathlib import Path

import pytest

from marastat.catalogue import charger_clients, charger_regles, classer


def _csv_utf8_de_parite(parite: int) -> tuple[bytes, str]:
    nom = "Cantine"
    while True:
        contenu = f"Code;RaisonSociale\n1042;{nom}\n".encode()
        if len(contenu) % 2 == parite:
            return contenu, nom
        nom += "x"


@pytest.mark.parametrize("parite", [0, 1])
def test_lit_utf8_independamment_de_la_taille(tmp_path: Path, parite: int) -> None:
    contenu, nom = _csv_utf8_de_parite(parite)
    chemin = tmp_path / "Clients.csv"
    chemin.write_bytes(contenu)
    assert charger_clients(chemin) == {"1042": nom}


def test_refuse_les_colonnes_manquantes(tmp_path: Path) -> None:
    chemin = tmp_path / "Clients.csv"
    chemin.write_text("Code;Nom\n1042;Cantine\n", encoding="utf-8")
    with pytest.raises(ValueError, match="RaisonSociale"):
        charger_clients(chemin)


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("PDT Charlotte", "Pomme de terre (non précisée)"),
        ("celeri branche", "Céleri (non précisé)"),
        ("salade delicate", "Salade"),
        ("mini potiron", "Non identifié"),
        ("PDT Terroir", "Pomme de terre (non précisée)"),
        ("Botte p Panais", "Non identifié"),
        ("PDTerre By", "Pomme de terre (non précisée)"),
    ],
)
def test_les_motifs_generaux_ne_capturent_pas_un_autre_produit(
    texte: str, attendu: str
) -> None:
    assert classer("", texte, charger_regles())[0] == attendu


def test_chaque_regle_classe_son_propre_libelle() -> None:
    regles = charger_regles()
    for regle in regles:
        assert classer("", regle.legume, regles)[0] == regle.legume
