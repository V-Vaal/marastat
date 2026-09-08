"""Invariants qui portent sur le dépôt entier, pas sur une fonction.

Ces tests existent parce que certaines fautes ne se voient que sur un système
d'exploitation, et sont donc invisibles pour qui développe sur l'autre. Les
transformer en invariant lisible depuis n'importe quel OS est le seul moyen
fiable de ne pas les réintroduire.
"""

from __future__ import annotations

import ast
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
IGNORES = {".git", ".venv", "venv", "build", "dist", "__pycache__"}


def _sources() -> list[Path]:
    return [
        chemin
        for chemin in RACINE.rglob("*.py")
        if not IGNORES & set(chemin.parts)
    ]


def _est_connexion_nue(expression: ast.expr) -> bool:
    """``sqlite3.connect(...)`` utilisé directement comme gestionnaire.

    L'analyse porte sur l'arbre syntaxique et non sur le texte : une mention
    du motif dans un commentaire ou une docstring, comme celle-ci, ne compte
    pas. ``closing(sqlite3.connect(...))`` non plus, puisque le gestionnaire
    est alors ``closing``, qui ferme bien la connexion.
    """
    return (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "connect"
        and isinstance(expression.func.value, ast.Name)
        and expression.func.value.id == "sqlite3"
    )


def test_le_depot_contient_bien_des_sources_a_inspecter() -> None:
    """Un scan qui ne trouve aucun fichier passerait toujours : on le vérifie."""
    noms = {chemin.name for chemin in _sources()}
    assert {"cli.py", "etl.py", "parser.py", "rapport.py"} <= noms


def test_aucune_connexion_sqlite_n_est_laissee_ouverte() -> None:
    """``with sqlite3.connect(...)`` valide la transaction mais ne ferme rien.

    Le fichier reste alors ouvert. Sous Linux cela ne se voit pas : renommer
    un fichier ouvert est autorisé. Sous Windows, non, et l'outil publie
    justement ses trois livrables par renommage. Le motif est donc proscrit
    partout, tests compris, au profit de ``contextlib.closing`` ou de
    l'assistant ``tests.aide.connexion``.
    """
    fautifs = []
    for chemin in _sources():
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.With | ast.AsyncWith):
                continue
            for element in noeud.items:
                if _est_connexion_nue(element.context_expr):
                    fautifs.append(
                        f"{chemin.relative_to(RACINE)}:{element.context_expr.lineno}"
                    )

    assert not fautifs, (
        "Connexion sqlite laissée ouverte (échouera sous Windows au moment de "
        "publier) :\n  " + "\n  ".join(fautifs)
    )
