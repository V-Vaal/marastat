"""Rejoue en local ce que fait l'intégration continue.

    python outils/controle.py

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

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]

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
    print(f"\n\033[1m{titre}\033[0m")
    print("  " + " ".join(commande))
    resultat = subprocess.run(commande, cwd=RACINE, check=False)
    reussie = resultat.returncode == 0
    print("  " + ("OK" if reussie else "ÉCHEC"))
    return reussie


def main() -> int:
    absentes = manquantes()
    if absentes:
        expliquer_installation(absentes)
        return 2

    python = sys.executable
    with tempfile.TemporaryDirectory(prefix="marastat-controle-") as brouillon:
        factures = Path(brouillon) / "factures"
        sortie = Path(brouillon) / "rapport"
        etapes = [
            ("Lint", [python, "-m", "ruff", "check", "marastat", "tests", "outils"]),
            ("Tests", [python, "-m", "pytest", "-q"]),
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
        resultats = [(titre, etape(titre, commande)) for titre, commande in etapes]

        if all(reussie for _, reussie in resultats):
            attendus = ("rapport.html", "ventes.csv", "ventes.sqlite")
            manquants = [nom for nom in attendus if not (sortie / nom).is_file()]
            temporaires = list(sortie.glob(".*.tmp"))
            if manquants or temporaires:
                print(f"\n  ÉCHEC livrables manquants={manquants} "
                      f"temporaires restants={[t.name for t in temporaires]}")
                resultats.append(("Livrables", False))
            else:
                resultats.append(("Livrables", True))

    print("\n" + "-" * 46)
    for titre, reussie in resultats:
        print(f"  {'OK   ' if reussie else 'ÉCHEC'}  {titre}")
    rate = [titre for titre, reussie in resultats if not reussie]
    print("-" * 46)
    if rate:
        print(f"\n{len(rate)} étape(s) en échec. Ne pas pousser en l'état.")
        return 1
    print(f"\nTout passe sous Python {sys.version_info.major}."
          f"{sys.version_info.minor}, sur {sys.platform}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
