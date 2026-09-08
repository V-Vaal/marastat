"""Rejoue en local ce que fait l'intégration continue.

    python outils/controle.py                  # verifier
    python outils/controle.py --rafraichir-demo  # + republier le rapport de demo

Lint, suite de tests, puis la chaîne complète sur le jeu de démonstration,
dans un dossier temporaire. À lancer avant de pousser : l'essentiel des
échecs de CI se voit ici, en une dizaine de secondes.

Une limite à connaître : ce script ne teste qu'un interpréteur et qu'un
système. La CI, elle, couvre Linux et Windows en Python 3.10 et 3.12, et
c'est là que se révèlent les fautes propres à un système de fichiers, comme
un fichier laissé ouvert qu'on essaie de renommer. Passer ici ne dispense
donc pas de regarder la CI, mais évite d'y aller pour rien.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
RAPPORT_PUBLIE = RACINE / "exemples" / "rapport-demo.html"

# Le module a importer, et ce qu'il sert a faire ici.
DEPENDANCES = {
    "pdfplumber": "lire les factures",
    "pytest": "lancer la suite de tests",
    "ruff": "passer le lint",
    "reportlab": "fabriquer le jeu de demonstration",
}


def manquantes() -> list[str]:
    return [nom for nom in DEPENDANCES if importlib.util.find_spec(nom) is None]


def expliquer_installation(absentes: list[str]) -> None:
    """Une cause, un message. Sans ce controle, chaque etape echouerait
    separement avec sa propre trace, et quatre murs d'erreur pour un seul
    probleme cachent le probleme au lieu de le montrer."""
    dans_un_env = sys.prefix != sys.base_prefix
    print("\nDependances absentes de cet interpreteur :")
    for nom in absentes:
        print(f"  - {nom} ({DEPENDANCES[nom]})")
    print(f"\nInterpreteur utilise : {sys.executable}")
    print("Environnement virtuel  : " + ("oui" if dans_un_env else "non"))
    if dans_un_env:
        print('\nInstaller les dependances :\n')
        print('    python -m pip install -e ".[dev]"')
    else:
        print(
            "\nLa plupart des distributions Linux refusent d'installer dans le\n"
            "Python du systeme. Creer un environnement dedie, une fois :\n"
        )
        print("    python3 -m venv .venv")
        print("    . .venv/bin/activate            # Windows : .venv\\Scripts\\activate")
        print('    python -m pip install -e ".[dev]"')
        print("\npuis relancer :\n")
        print("    python outils/controle.py")


def etape(titre: str, commande: list[str]) -> bool:
    printf_etape(titre)
    print("  " + " ".join(commande))
    resultat = subprocess.run(commande, cwd=RACINE, check=False)
    reussie = resultat.returncode == 0
    print("  " + ("OK" if reussie else "ÉCHEC"))
    return reussie


def printf_etape(titre: str) -> None:
    print(f"\n\033[1m{titre}\033[0m")


def controler_livrables(sortie: Path) -> bool:
    """Les trois fichiers sont la, et aucun temporaire n'a survecu."""
    attendus = ("rapport.html", "ventes.csv", "ventes.sqlite")
    manquants = [nom for nom in attendus if not (sortie / nom).is_file()]
    temporaires = [chemin.name for chemin in sortie.glob(".*.tmp")]
    printf_etape("Livrables")
    if manquants or temporaires:
        print(f"  ÉCHEC manquants={manquants} temporaires restants={temporaires}")
        return False
    print(f"  {', '.join(attendus)} : présents, aucun temporaire")
    print("  OK")
    return True


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        prog="controle",
        description="Rejoue en local ce que fait l'integration continue.",
    )
    analyseur.add_argument(
        "--rafraichir-demo",
        action="store_true",
        help=(
            "recopier le rapport produit dans exemples/rapport-demo.html avant "
            "de verifier. A utiliser apres toute modification du gabarit ou du "
            "jeu de demonstration, sinon le test qui compare les deux echoue."
        ),
    )
    arguments = analyseur.parse_args(argv)

    absentes = manquantes()
    if absentes:
        expliquer_installation(absentes)
        return 2

    python = sys.executable
    resultats: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory(prefix="marastat-controle-") as brouillon:
        factures = Path(brouillon) / "factures"
        sortie = Path(brouillon) / "rapport"

        chaine = [
            (
                "Jeu de démonstration",
                [python, "outils/generer_demo.py", "--sortie", str(factures)],
            ),
            (
                "Chaîne complète",
                [
                    python, "-m", "marastat",
                    "--factures", str(factures),
                    "--sortie", str(sortie),
                    "--sans-ouverture", "--sans-notification",
                ],
            ),
        ]
        for titre, commande in chaine:
            resultats.append((titre, etape(titre, commande)))

        if all(reussie for _, reussie in resultats):
            resultats.append(("Livrables", controler_livrables(sortie)))

            # Republier avant de verifier, et non l'inverse : le test qui
            # compare le rapport publie au rapport produit echouerait sur
            # l'ancienne version alors qu'on vient justement de la remplacer.
            if arguments.rafraichir_demo:
                titre = "Rapport de démonstration republié"
                printf_etape(titre)
                shutil.copyfile(sortie / "rapport.html", RAPPORT_PUBLIE)
                print(f"  écrit : {RAPPORT_PUBLIE.relative_to(RACINE)}")
                print("  OK")
                resultats.append((titre, True))

    for titre, commande in (
        ("Lint", [python, "-m", "ruff", "check", "marastat", "tests", "outils"]),
        ("Tests", [python, "-m", "pytest", "-q"]),
    ):
        resultats.append((titre, etape(titre, commande)))

    print("\n" + "-" * 46)
    for titre, reussie in resultats:
        print(f"  {'OK   ' if reussie else 'ÉCHEC'}  {titre}")
    print("-" * 46)

    rate = [titre for titre, reussie in resultats if not reussie]
    if rate:
        print(f"\n{len(rate)} étape(s) en échec. Ne pas pousser en l'état.")
        return 1
    print(f"\nTout passe sous Python {sys.version_info.major}."
          f"{sys.version_info.minor}, sur {sys.platform}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
