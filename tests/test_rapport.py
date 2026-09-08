from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from marastat.etl import SCHEMA, Rapport
from marastat.rapport import GABARIT, agreger


def _bilan() -> Rapport:
    return Rapport(
        fichiers_vus=2, factures_lues=2, factures_integrees=2,
        factures_rejetees=[], doublons=[], conflits=[], erreurs_lecture=[],
        erreurs_arbitrage=[], lignes=2, lignes_arbitrees=0,
        lignes_non_identifiees=0, ca_non_identifie=0, ca_total=20,
    )


def _ajouter(cx, numero: str, annee: int, mois: int, code: str, client: str) -> None:
    jour = f"{annee:04d}-{mois:02d}-01"
    cx.execute(
        "INSERT INTO factures VALUES (?,?,?,?,?,?,?,?,?,?)",
        (numero, numero+".pdf", jour, annee, mois, code, client, 10, 10, 1),
    )
    cx.execute(
        "INSERT INTO lignes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (None, numero, jour, annee, mois, code, client, "", "Tomate", "Tomate",
         "Tomate", "regle", "kg", "facture", 1, 10, 10, None),
    )


def test_clients_de_meme_nom_restent_separes_et_ancienne_annee_non_marquee(
    tmp_path: Path,
) -> None:
    base = tmp_path / "ventes.sqlite"
    with sqlite3.connect(base) as cx:
        cx.executescript(SCHEMA)
        _ajouter(cx, "F-2024-1", 2024, 11, "1", "Même nom")
        _ajouter(cx, "F-2025-1", 2025, 12, "2", "Même nom")
    donnees = agreger(base, _bilan(), date(2026, 9, 4))
    assert {c["code_client"] for c in donnees["clients"]} == {"1", "2"}
    assert donnees["meta"]["incompletes"] == []


def test_gabarit_n_injecte_plus_les_donnees_par_inner_html() -> None:
    assert "innerHTML" not in GABARIT.read_text(encoding="utf-8")


def test_le_gabarit_seul_ne_casse_pas_a_l_ouverture() -> None:
    """Ouvert tel quel, le gabarit doit s'expliquer, pas planter.

    Le marqueur de substitution n'est pas du JSON : sans garde-fou, ouvrir
    gabarit.html dans un navigateur produit une erreur console et une page
    vide. On verifie que le garde-fou est bien la et qu'il precede la lecture
    des donnees.
    """
    source = GABARIT.read_text(encoding="utf-8")
    assert "/*__DONNEES__*/" in source
    assert 'const EST_GABARIT = !SOURCE.trim().startsWith("{");' in source
    assert "if (!EST_GABARIT) init();" in source
    assert source.index("EST_GABARIT") < source.index("const D =")


def test_agreger_ne_laisse_aucune_connexion_ouverte(tmp_path, monkeypatch) -> None:
    """La base lue par agreger() est souvent le fichier temporaire que
    l'appelant s'apprête à renommer : la connexion doit être refermée.
    """
    base = tmp_path / "ventes.sqlite"
    with sqlite3.connect(base) as cx:
        cx.executescript(SCHEMA)
        _ajouter(cx, "F-2026-1", 2026, 1, "1", "Client")

    ouvertes = []
    vrai_connect = sqlite3.connect

    def connect_suivi(*args, **kwargs):
        cx = vrai_connect(*args, **kwargs)
        ouvertes.append(cx)
        return cx

    monkeypatch.setattr(sqlite3, "connect", connect_suivi)
    agreger(base, _bilan(), date(2026, 9, 8))

    assert ouvertes, "le suivi n'a capté aucune connexion : test inopérant"
    for cx in ouvertes:
        with pytest.raises(sqlite3.ProgrammingError):
            cx.execute("SELECT 1")
