"""Petits utilitaires partagés par les tests."""

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
