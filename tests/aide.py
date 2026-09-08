"""Petits utilitaires partagés par les tests.

Ce module s'importe sans préfixe, `from aide import connexion`, et non
`from tests.aide import ...` : `tests/` ne contient pas de `__init__.py`, donc
pytest ajoute le dossier du fichier de test à `sys.path` avant de l'importer
(mode d'import « prepend », celui par défaut). C'est un choix, pas un oubli.

Sa contrepartie, à connaître avant de toucher à la configuration : ce mécanisme
disparaît avec `--import-mode=importlib`, où il faudrait alors faire de `tests/`
un paquet. Rien ne l'impose aujourd'hui, et le faire pour la forme casserait un
montage qui fonctionne sur les quatre versions de Python couvertes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def connexion(chemin: str | Path) -> Iterator[sqlite3.Connection]:
    """Ouvre une base, valide à la sortie, et ferme réellement la connexion.

    Le raccourci ``with sqlite3.connect(...) as cx`` ne convient pas : il
    valide la transaction mais laisse la connexion ouverte. Windows refuse
    ensuite de renommer ou de supprimer le fichier, ce qui casse exactement le
    mécanisme de publication de l'outil. Les tests s'en tiennent donc au même
    contrat que le code de production.
    """
    with closing(sqlite3.connect(chemin)) as cx:
        yield cx
        cx.commit()
