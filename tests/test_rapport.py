from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

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
