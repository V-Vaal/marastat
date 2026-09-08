import sqlite3
from types import SimpleNamespace

from marastat.cli import (
    afficher_progression,
    exporter_csv,
    message_fin,
    nettoyer,
    neutraliser_formule,
    publier,
)
from marastat.etl import SCHEMA


def test_barre_de_progression_affiche_compteur_et_pourcentage(capsys) -> None:
    afficher_progression(5, 10)
    afficher_progression(10, 10)
    sortie = capsys.readouterr().out
    assert "50 %" in sortie
    assert "100 %" in sortie
    assert "(10/10 PDF)" in sortie


def test_notification_resume_le_traitement() -> None:
    contexte = SimpleNamespace(factures_integrees=197, lignes=3076)
    message = message_fin(contexte)
    assert "197 factures intégrées" in message
    assert "3076 lignes analysées" in message
    assert "Cliquez sur OK" in message


def test_notification_sans_ouverture_indique_le_dossier() -> None:
    contexte = SimpleNamespace(factures_integrees=1, lignes=2)
    assert "dossier Rapport" in message_fin(contexte, ouverture_prevue=False)


def test_libelle_commencant_par_un_signe_n_est_pas_execute_par_le_tableur() -> None:
    # Le libellé vient d'un PDF tiers : il ne doit jamais devenir une formule.
    assert neutraliser_formule("=1+1") == "'=1+1"
    assert neutraliser_formule("+SUM(A1)") == "'+SUM(A1)"
    assert neutraliser_formule("@import") == "'@import"
    assert neutraliser_formule("-Carotte botte") == "'-Carotte botte"


def test_neutralisation_ne_touche_ni_les_nombres_ni_le_texte_ordinaire() -> None:
    assert neutraliser_formule(-12.5) == -12.5
    assert neutraliser_formule(2024) == 2024
    assert neutraliser_formule(None) is None
    assert neutraliser_formule("Tomate grappe") == "Tomate grappe"


def test_export_csv_neutralise_les_formules(tmp_path) -> None:
    base = tmp_path / "ventes.sqlite"
    with sqlite3.connect(base) as cx:
        cx.executescript(SCHEMA)
        cx.execute(
            "INSERT INTO factures VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("F-1", "f.pdf", "2026-01-31", 2026, 1, "1", "Client", 10, 10, 1),
        )
        cx.execute(
            "INSERT INTO lignes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "F-1", "2026-01-31", 2026, 1, "1", "Client", "",
             "=cmd|'/c calc'!A1", "Tomate", "Tomate", "regle", "kg",
             "facture", 1, 10, 10, None),
        )
    destination = tmp_path / "ventes.csv"
    exporter_csv(base, destination)
    contenu = destination.read_text(encoding="utf-8-sig")
    assert "'=cmd" in contenu
    assert ";=cmd" not in contenu


def test_publication_ne_remplace_rien_tant_que_tout_n_est_pas_produit(
    tmp_path,
) -> None:
    ancien_rapport = tmp_path / "rapport.html"
    ancienne_base = tmp_path / "ventes.sqlite"
    ancien_rapport.write_text("ancien", encoding="utf-8")
    ancienne_base.write_text("ancienne", encoding="utf-8")

    nouveau_rapport = tmp_path / ".rapport.html.tmp"
    nouvelle_base = tmp_path / ".ventes.sqlite.tmp"
    nouveau_rapport.write_text("nouveau", encoding="utf-8")
    nouvelle_base.write_text("nouvelle", encoding="utf-8")

    # Tant que publier() n'a pas ete appele, les anciens fichiers sont intacts.
    assert ancien_rapport.read_text(encoding="utf-8") == "ancien"
    assert ancienne_base.read_text(encoding="utf-8") == "ancienne"

    publier([(nouveau_rapport, ancien_rapport), (nouvelle_base, ancienne_base)])

    assert ancien_rapport.read_text(encoding="utf-8") == "nouveau"
    assert ancienne_base.read_text(encoding="utf-8") == "nouvelle"
    assert not nouveau_rapport.exists() and not nouvelle_base.exists()


def test_nettoyer_supprime_les_temporaires_sans_echouer_sur_un_absent(
    tmp_path,
) -> None:
    present = tmp_path / ".ventes.sqlite.tmp"
    present.write_text("x", encoding="utf-8")
    nettoyer([present, tmp_path / ".jamais-cree.tmp"])
    assert not present.exists()


def _suivre_les_connexions(monkeypatch) -> list:
    """Retient toutes les connexions sqlite ouvertes pendant le test."""
    ouvertes = []
    vrai_connect = sqlite3.connect

    def connect_suivi(*args, **kwargs):
        cx = vrai_connect(*args, **kwargs)
        ouvertes.append(cx)
        return cx

    monkeypatch.setattr(sqlite3, "connect", connect_suivi)
    return ouvertes


def _est_fermee(cx) -> bool:
    try:
        cx.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


def _base_minimale(chemin) -> None:
    with sqlite3.connect(chemin) as cx:
        cx.executescript(SCHEMA)
        cx.execute(
            "INSERT INTO factures VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("F-1", "f.pdf", "2026-01-31", 2026, 1, "1", "Client", 10, 10, 1),
        )
        cx.execute(
            "INSERT INTO lignes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "F-1", "2026-01-31", 2026, 1, "1", "Client", "", "Tomate",
             "Tomate", "Tomate", "regle", "kg", "facture", 1, 10, 10, None),
        )


def test_l_export_ne_laisse_aucune_connexion_ouverte(tmp_path, monkeypatch) -> None:
    """Un sqlite3.Connection utilisé comme gestionnaire de contexte valide la
    transaction mais ne ferme pas la connexion. Le fichier restait donc ouvert,
    et Windows refuse de renommer un fichier ouvert : la publication de la base
    échouait là-bas alors que tout passait sous Linux.
    """
    base = tmp_path / "ventes.sqlite"
    _base_minimale(base)
    ouvertes = _suivre_les_connexions(monkeypatch)

    exporter_csv(base, tmp_path / "ventes.csv", publier=False)

    assert ouvertes, "le suivi n'a capté aucune connexion : test inopérant"
    assert all(_est_fermee(cx) for cx in ouvertes)


def test_la_base_temporaire_est_renommable_apres_export(tmp_path) -> None:
    """Le scénario exact de la publication : lire la base temporaire, puis la
    mettre en place. Sous Windows, cela échoue si une connexion traîne.
    """
    base_temporaire = tmp_path / ".ventes.sqlite.tmp"
    _base_minimale(base_temporaire)
    export_temporaire = exporter_csv(
        base_temporaire, tmp_path / "ventes.csv", publier=False
    )
    publier([
        (export_temporaire, tmp_path / "ventes.csv"),
        (base_temporaire, tmp_path / "ventes.sqlite"),
    ])
    assert (tmp_path / "ventes.sqlite").is_file()
    assert not base_temporaire.exists()
