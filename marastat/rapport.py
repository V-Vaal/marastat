"""Generation du rapport HTML a partir de la base SQLite.

Le rapport est un fichier unique, sans dependance externe : il s'ouvre hors
ligne, sur un telephone comme sur un PC, et peut etre envoye tel quel par
mail. Les donnees agregees sont injectees en JSON dans la page ; les
graphiques sont construits en SVG cote navigateur.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .etl import Rapport

GABARIT = Path(__file__).parent / "gabarit.html"

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def _lignes(cx, requete: str) -> list[dict]:
    cur = cx.execute(requete)
    colonnes = [c[0] for c in cur.description]
    return [dict(zip(colonnes, r, strict=True)) for r in cur.fetchall()]


def agreger(
    base: str | Path, controle: Rapport, aujourd_hui: date | None = None
) -> dict:
    aujourd_hui = aujourd_hui or datetime.now().astimezone().date()
    cx = sqlite3.connect(base)

    annees = [
        r[0]
        for r in cx.execute("SELECT DISTINCT annee FROM lignes ORDER BY annee")
    ]

    legumes = _lignes(cx, """
        SELECT annee, mois, famille, legume,
               ROUND(SUM(total_ht), 2) AS ht,
               ROUND(SUM(CASE WHEN unite='kg'    THEN quantite ELSE 0 END), 1) AS kg,
               ROUND(SUM(CASE WHEN unite='piece' THEN quantite ELSE 0 END))     AS p
        FROM lignes GROUP BY annee, mois, famille, legume
    """)

    mensuel = _lignes(cx, """
        SELECT annee, mois, ROUND(SUM(total_ht), 2) AS ht
        FROM lignes GROUP BY annee, mois ORDER BY annee, mois
    """)

    # Le mois est conserve : sans lui, impossible de comparer une annee en
    # cours a la meme periode de l'annee precedente.
    clients = _lignes(cx, """
        SELECT annee, mois, code_client, client,
               ROUND(SUM(total_ht), 2) AS ht,
               COUNT(DISTINCT facture) AS factures
        FROM lignes GROUP BY annee, mois, code_client, client
    """)

    factures = _lignes(cx, """
        SELECT annee, COUNT(*) AS nb FROM factures GROUP BY annee
    """)

    dernieres = {
        annee: derniere
        for annee, derniere in cx.execute(
            "SELECT annee, MAX(date) FROM factures GROUP BY annee"
        )
    }
    derniere = max(dernieres.values(), default="")
    ca_total = cx.execute("SELECT ROUND(SUM(total_ht), 2) FROM lignes").fetchone()[0] or 0
    cx.close()

    # Une annee est dite incomplete si sa derniere facture est anterieure a
    # decembre : ses totaux ne sont alors pas comparables tels quels a une
    # annee pleine, et le rapport compare a periode equivalente.
    dernier_mois = {
        an: max((m["mois"] for m in mensuel if m["annee"] == an), default=0)
        for an in annees
    }
    candidates_incompletes = {aujourd_hui.year}
    if annees:
        candidates_incompletes.add(annees[-1])
    incompletes = [
        an
        for an in annees
        if an in candidates_incompletes
        and (an == aujourd_hui.year or dernier_mois[an] < 12)
    ]

    dernieres_lisibles = {}
    for annee, valeur in dernieres.items():
        a, m, j = valeur.split("-")
        dernieres_lisibles[str(annee)] = f"{int(j)} {MOIS_FR[int(m) - 1]} {a}"

    if derniere:
        a, m, j = derniere.split("-")
        derniere_lisible = f"{int(j)} {MOIS_FR[int(m) - 1]} {a}"
    else:
        derniere_lisible = "inconnue"

    libelle = (
        f"{annees[0]} à {annees[-1]}"
        if len(annees) > 1
        else str(annees[0])
        if annees
        else ""
    )

    return {
        "meta": {
            "annees": annees,
            "incompletes": incompletes,
            "dernier_mois": {str(a): m for a, m in dernier_mois.items()},
            "derniere_facture_par_annee": dernieres_lisibles,
            "mois_courts": ["janv.", "févr.", "mars", "avr.", "mai", "juin",
                            "juil.", "août", "sept.", "oct.", "nov.", "déc."],
            "mois_longs": MOIS_FR,
            "derniere_facture": derniere_lisible,
            "libelle_periode": libelle,
            "ca_total": ca_total,
            "genere_le": aujourd_hui.strftime("%d/%m/%Y"),
        },
        "legumes": legumes,
        "mensuel": mensuel,
        "clients": clients,
        "factures": factures,
        "controle": {
            "fichiers_vus": controle.fichiers_vus,
            "factures_lues": controle.factures_lues,
            "factures_integrees": controle.factures_integrees,
            "factures_rejetees": controle.factures_rejetees,
            "doublons": controle.doublons,
            "conflits": controle.conflits,
            "erreurs_lecture": controle.erreurs_lecture,
            "erreurs_arbitrage": controle.erreurs_arbitrage,
            "dates_suspectes": controle.dates_suspectes,
            "lignes": controle.lignes,
            "lignes_non_identifiees": controle.lignes_non_identifiees,
            "ca_non_identifie": controle.ca_non_identifie,
            "ca_total": controle.ca_total,
        },
    }


def ecrire(
    base: str | Path,
    controle: Rapport,
    destination: str | Path,
    aujourd_hui: date | None = None,
    publier: bool = True,
) -> Path:
    """Ecrit le rapport et retourne le fichier reellement ecrit.

    Avec ``publier=False``, le rapport reste dans son fichier temporaire et
    c'est ce chemin qui est retourne : l'appelant peut alors mettre en place
    le rapport, la base et l'export ensemble, sans jamais laisser sur le
    disque un rapport plus recent que la base dont il est tire.
    """
    donnees = agreger(base, controle, aujourd_hui)
    charge = json.dumps(donnees, ensure_ascii=False, separators=(",", ":"))
    # Empeche une chaine "</script>" presente dans une donnee de fermer le bloc.
    charge = charge.replace("</", "<\\/")

    page = GABARIT.read_text(encoding="utf-8").replace("/*__DONNEES__*/", charge)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporaire = destination.with_name(f".{destination.name}.tmp")
    temporaire.write_text(page, encoding="utf-8")
    if not publier:
        return temporaire
    temporaire.replace(destination)
    return destination
