"""Point d'entree : lit les factures, construit la base, ecrit le rapport.

Usage courant (depuis le dossier qui contient Factures/) :

    python -m marastat

Tout est optionnel : sans argument, l'outil cherche les fichiers a cote de
lui, puis dans le dossier courant.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import sqlite3
import sys
import webbrowser
from contextlib import closing, suppress
from pathlib import Path

from .etl import Rapport, construire
from .rapport import ecrire

LARGEUR_BARRE = 28

# Un tableur interprete comme une formule toute cellule commencant par l'un
# de ces caracteres. Les libelles viennent de PDF tiers : sans neutralisation,
# une designation commencant par "=" ou "+" s'executerait a l'ouverture du
# fichier dans Excel ou LibreOffice.
AMORCES_FORMULE = ("=", "+", "-", "@", "\t", "\r")


def neutraliser_formule(valeur: object) -> object:
    """Prefixe d'une apostrophe toute chaine qu'un tableur executerait.

    Les nombres ne sont jamais touches : ils sont ecrits par sqlite comme des
    int/float, et un signe "-" y est un signe, pas une amorce de formule.
    """
    if isinstance(valeur, str) and valeur.startswith(AMORCES_FORMULE):
        return "'" + valeur
    return valeur


def afficher_progression(position: int, total: int) -> None:
    """Affiche une barre compacte, compatible avec la console Windows."""
    proportion = position / total if total else 1.0
    remplis = round(proportion * LARGEUR_BARRE)
    barre = "#" * remplis + "-" * (LARGEUR_BARRE - remplis)
    pourcentage = round(proportion * 100)
    sys.stdout.write(
        f"\r  [{barre}] {pourcentage:3d} %  ({position}/{total} PDF)"
    )
    sys.stdout.flush()
    if position == total:
        print()


def message_fin(contexte: Rapport, ouverture_prevue: bool = True) -> str:
    conclusion = (
        "Cliquez sur OK pour ouvrir le rapport."
        if ouverture_prevue
        else "Les fichiers sont disponibles dans le dossier Rapport."
    )
    return (
        "Le rapport des ventes est prêt.\n\n"
        f"{contexte.factures_integrees} factures intégrées\n"
        f"{contexte.lignes} lignes analysées\n\n"
        f"{conclusion}"
    )


def sous_windows() -> bool:
    """Isole le test de plateforme derriere une fonction.

    Comparer ``sys.platform`` directement fait deduire au verificateur de types
    que tout le corps de ``notifier_fin`` est mort quand il analyse sous Linux,
    et l'inverse sous Windows : impossible de satisfaire les deux a la fois
    avec une annotation locale. Le passer par un appel supprime la deduction
    sans rien changer au comportement.
    """
    return sys.platform == "win32"


def notifier_fin(contexte: Rapport, ouverture_prevue: bool = True) -> None:
    """Affiche une confirmation Windows sans ajouter de dépendance externe."""
    if not sous_windows():
        return
    try:
        # getattr plutot que ctypes.WinDLL : l'attribut n'existe que sous
        # Windows, et l'ecrire en dur fait echouer le verificateur de types
        # partout ailleurs. Un ignore local, lui, deviendrait inutilise sous
        # Windows, ou la CI passe aussi.
        charger_dll = getattr(ctypes, "WinDLL")  # noqa: B009
        utilisateur = charger_dll("user32", use_last_error=True)
        utilisateur.MessageBoxW(
            None,
            message_fin(contexte, ouverture_prevue),
            "Marastat",
            0x40,
        )
    except OSError:
        # Le rapport est déjà produit : une notification indisponible ne doit
        # jamais transformer une analyse réussie en erreur.
        pass


def _racine_par_defaut() -> Path:
    """Dossier contenant les donnees.

    On cherche un dossier "Factures" a cote de l'executable (ou du dossier
    courant), puis un cran au-dessus : l'outil peut ainsi vivre dans un
    sous-dossier sans que l'utilisateur ait a taper le moindre chemin.
    """
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    candidats = [base, base.parent, Path.cwd(), Path.cwd().parent]
    for candidat in candidats:
        if (candidat / "Factures").is_dir():
            return candidat
    return base


def exporter_csv(base: Path, destination: Path, publier: bool = True) -> Path:
    """Export a plat des lignes, ouvrable directement dans Excel.

    Retourne le fichier reellement ecrit : la destination si ``publier``, le
    fichier temporaire sinon (voir ``publier`` plus bas).
    """
    temporaire = destination.with_name(f".{destination.name}.tmp")
    # closing() et non le seul "with" : un sqlite3.Connection utilise comme
    # gestionnaire de contexte valide la transaction mais NE FERME PAS la
    # connexion. Le fichier restait donc ouvert, et Windows refuse de renommer
    # un fichier ouvert : la publication de la base echouait la-bas.
    with closing(sqlite3.connect(base)) as cx:
        cur = cx.execute("""
            SELECT date, annee, mois, facture, client, famille, legume, classement,
                   unite, quantite, pu_ht, total_ht, ref, libelle
            FROM lignes ORDER BY date, facture, id
        """)
        colonnes = [c[0] for c in cur.description]
        with temporaire.open("w", encoding="utf-8-sig", newline="") as f:
            ecrivain = csv.writer(f, delimiter=";")
            ecrivain.writerow(colonnes)
            for enregistrement in cur:
                ecrivain.writerow([neutraliser_formule(v) for v in enregistrement])
    if not publier:
        return temporaire
    temporaire.replace(destination)
    return destination


def publier(couples: list[tuple[Path, Path]]) -> None:
    """Met en place les fichiers produits, une fois qu'ils existent tous.

    Chaque renommage est atomique sur un meme systeme de fichiers ; les faire
    a la suite, apres que tout a ete calcule, evite le seul etat vraiment
    genant : un rapport a jour a cote d'une base perimee, ou l'inverse.
    """
    for temporaire, definitif in couples:
        temporaire.replace(definitif)


def nettoyer(temporaires: list[Path]) -> None:
    """Retire les fichiers temporaires d'une reconstruction interrompue.

    Le nettoyage est accessoire : il ne doit jamais masquer l'erreur qui l'a
    declenche. Sous Windows, supprimer un fichier encore ouvert echoue, et ce
    second echec ferait disparaitre le premier.
    """
    for chemin in temporaires:
        with suppress(OSError):
            chemin.unlink(missing_ok=True)


def analyser(argv: list[str] | None) -> argparse.Namespace:
    """Options de la ligne de commande, avec des defauts qui evitent d'en taper."""
    racine = _racine_par_defaut()
    analyseur = argparse.ArgumentParser(
        prog="marastat",
        description="Analyse des ventes de legumes a partir des factures PDF.",
    )
    analyseur.add_argument(
        "--factures", type=Path, default=racine / "Factures",
        help="dossier contenant les PDF (defaut : ./Factures)",
    )
    analyseur.add_argument(
        "--clients", type=Path,
        help="ancien catalogue clients facultatif (les noms sont lus dans les PDF)",
    )
    analyseur.add_argument(
        "--produits", type=Path,
        help="catalogue produits facultatif (pistes d'arbitrage uniquement)",
    )
    analyseur.add_argument(
        "--sortie", type=Path, default=racine / "Rapport",
        help="dossier ou ecrire le rapport (defaut : ./Rapport)",
    )
    analyseur.add_argument(
        "--sans-ouverture", action="store_true",
        help="ne pas ouvrir le rapport dans le navigateur",
    )
    analyseur.add_argument(
        "--sans-notification", action="store_true",
        help="ne pas afficher la confirmation Windows de fin",
    )
    return analyseur.parse_args(argv)


def verifier_chemins(args: argparse.Namespace) -> bool:
    """Dit ce qui manque, en clair, avant d'avoir lu le moindre PDF."""
    if not args.factures.exists():
        print(f"Introuvable : dossier des factures ({args.factures})")
        print("Placer l'outil a cote du dossier Factures, ou utiliser --factures.")
        return False
    for chemin, quoi in ((args.clients, "Clients.csv"), (args.produits, "Produits.csv")):
        if chemin is not None and not chemin.is_file():
            print(f"Introuvable : fichier facultatif {quoi} ({chemin})")
            return False
    return True


def raconter(contexte: Rapport, arbitrage: Path) -> None:
    """Restitue ce que la lecture a trouve, et surtout ce qu'elle a ecarte.

    Chaque categorie porte un prefixe fixe pour rester lisible dans une
    console : c'est le seul retour dont dispose un utilisateur qui lance
    l'executable d'un double-clic.
    """
    print(f"  {contexte.fichiers_vus} fichiers vus, "
          f"{contexte.factures_lues} factures lues, "
          f"{contexte.factures_integrees} integrees, {contexte.lignes} lignes.")

    signalements: list[tuple[str, list[tuple[object, str]]]] = [
        ("ILLISIBLE", list(contexte.erreurs_lecture)),
        ("ECARTEE  ", list(contexte.factures_rejetees)),
        ("DOUBLON  ", list(contexte.doublons)),
        ("CONFLIT  ", list(contexte.conflits)),
        ("DATE ?   ", list(contexte.dates_suspectes)),
    ]
    for prefixe, lignes in signalements:
        for quoi, motif in lignes:
            print(f"  {prefixe} : {quoi} ({motif})")
    for numero, motif in contexte.erreurs_arbitrage:
        print(f"  ARBITRAGE : ligne {numero} ignoree ({motif})")

    if contexte.lignes_non_identifiees:
        part = (
            contexte.ca_non_identifie / contexte.ca_total * 100
            if contexte.ca_total
            else 0
        )
        print(f"  {contexte.lignes_non_identifiees} lignes sans libelle "
              f"({contexte.ca_non_identifie:.2f} EUR, {part:.1f} % du CA), "
              f"conservees comme 'Non identifie' (detail facultatif : {arbitrage.name})")


def produire_et_publier(contexte: Rapport, sortie: Path) -> tuple[Path, Path, Path]:
    """Calcule le rapport et l'export, puis met les trois fichiers en place.

    Tout est ecrit en temporaire d'abord. Une interruption laisse donc la
    campagne precedente entiere et coherente, plutot qu'un rapport a jour a
    cote d'une base perimee.
    """
    base = sortie / "ventes.sqlite"
    page = sortie / "rapport.html"
    export = sortie / "ventes.csv"
    base_temporaire = contexte.base_temporaire

    try:
        page_temporaire = ecrire(base_temporaire, contexte, page, publier=False)
        export_temporaire = exporter_csv(base_temporaire, export, publier=False)
    except Exception:
        nettoyer([base_temporaire])
        raise

    publier(
        [
            (page_temporaire, page),
            (export_temporaire, export),
            (base_temporaire, base),
        ]
    )
    return page, base, export


def main(argv: list[str] | None = None) -> int:
    args = analyser(argv)
    if not verifier_chemins(args):
        return 2

    args.sortie.mkdir(parents=True, exist_ok=True)
    arbitrage = args.sortie / "arbitrage.csv"

    print("Lecture des factures...")
    contexte = construire(
        args.factures,
        args.clients,
        args.produits,
        args.sortie / "ventes.sqlite",
        arbitrage,
        progression=afficher_progression,
    )
    raconter(contexte, arbitrage)

    page, base, export = produire_et_publier(contexte, args.sortie)
    print(f"\nRapport   : {page}")
    print(f"Base      : {base}")
    print(f"Export    : {export}")

    if not args.sans_notification:
        notifier_fin(contexte, ouverture_prevue=not args.sans_ouverture)
    if not args.sans_ouverture:
        webbrowser.open(page.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
