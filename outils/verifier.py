"""Controle croise independant de la base produite.

Ce script ne reutilise PAS le parser : il relit les PDF avec un autre outil
(pdftotext) et une autre methode (expression reguliere sur le pied de page),
puis compare le total obtenu a celui de la base. Deux chemins independants qui
tombent sur le meme chiffre, c'est une verification ; deux fois le meme code,
non.

    python outils/verifier.py <dossier factures> <ventes.sqlite>

Necessite poppler-utils (pdftotext). Sert au developpement, pas a l'utilisateur final.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import unicodedata
from math import isfinite
from pathlib import Path

RE_TOTAL = re.compile(r"Total HT\s*:\s*([\d\s  ]+,\d{2})\s*€")
RE_NUM = re.compile(r"Facture\s+([A-Z]-\d{4}-\d+)")


def montant(t: str) -> float:
    t = unicodedata.normalize("NFKC", t)
    t = "".join(c for c in t if not c.isspace())
    valeur = float(t.replace(",", "."))
    if not isfinite(valeur):
        raise ValueError(f"Montant non fini : {t}")
    return valeur


def main(dossier: str, base: str) -> int:
    totaux: dict[str, float] = {}
    conflits: dict[str, tuple[float, float]] = {}
    doublons = 0
    for pdf in sorted(Path(dossier).rglob("*.pdf")):
        texte = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True, text=True, check=True
        ).stdout
        n, t = RE_NUM.search(texte), RE_TOTAL.search(texte)
        if not n or not t:
            print(f"  non lu : {pdf.name}")
            continue
        numero, total = n.group(1), montant(t.group(1))
        if numero in conflits:
            continue
        if numero in totaux:
            if abs(totaux[numero] - total) < 0.02:
                doublons += 1
            else:
                conflits[numero] = (totaux.pop(numero), total)
            continue
        totaux[numero] = total

    attendu = round(sum(totaux.values()), 2)

    cx = sqlite3.connect(base)
    obtenu = round(cx.execute("SELECT SUM(total_ht) FROM lignes").fetchone()[0], 2)
    nb_factures = cx.execute("SELECT COUNT(*) FROM factures").fetchone()[0]

    # Chaque facture prise une par une, en plus du total general.
    ecarts = []
    for numero, total in totaux.items():
        ligne = cx.execute(
            "SELECT ROUND(SUM(total_ht), 2) FROM lignes WHERE facture = ?", (numero,)
        ).fetchone()[0]
        if ligne is None:
            ecarts.append((numero, total, None))
        elif abs(ligne - total) >= 0.02:
            ecarts.append((numero, total, ligne))
    cx.close()

    print(f"PDF relus            : {len(totaux)} factures ({doublons} doublons ignores)")
    print(f"Base                 : {nb_factures} factures")
    print(f"Total HT imprime     : {attendu:,.2f}".replace(",", " "))
    print(f"Total HT en base     : {obtenu:,.2f}".replace(",", " "))
    print(f"Ecart                : {abs(attendu - obtenu):.2f}")
    print(f"Factures en ecart    : {len(ecarts)}")
    print(f"Numeros conflictuels : {len(conflits)}")
    for numero, imprime, calcule in ecarts[:20]:
        print(f"   {numero} imprime={imprime} base={calcule}")

    return 0 if not ecarts and not conflits and abs(attendu - obtenu) < 0.02 else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
