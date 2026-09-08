"""Construction de la base SQLite a partir des factures PDF.

Regle de fonctionnement : une facture dont la somme des lignes ne retombe
pas sur le "Total HT" imprime n'est PAS integree. Mieux vaut une facture
manquante et signalee qu'un chiffre faux noye dans un total.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .catalogue import (
    NON_IDENTIFIE,
    Regle,
    candidats_produits,
    charger_clients,
    charger_produits,
    charger_regles,
    classer,
)
from .parser import Facture, Ligne, lire_dossier, nettoyer_nombre

SCHEMA = """
DROP TABLE IF EXISTS lignes;
DROP TABLE IF EXISTS factures;

CREATE TABLE factures (
    numero            TEXT PRIMARY KEY,
    fichier           TEXT NOT NULL,
    date              TEXT NOT NULL,
    annee             INTEGER NOT NULL,
    mois              INTEGER NOT NULL,
    code_client       TEXT NOT NULL,
    client            TEXT NOT NULL,
    total_ht_imprime  REAL,
    total_ht_calcule  REAL NOT NULL,
    nb_lignes         INTEGER NOT NULL
);

CREATE TABLE lignes (
    id            INTEGER PRIMARY KEY,
    facture       TEXT NOT NULL REFERENCES factures(numero),
    date          TEXT NOT NULL,
    annee         INTEGER NOT NULL,
    mois          INTEGER NOT NULL,
    code_client   TEXT NOT NULL,
    client        TEXT NOT NULL,
    ref           TEXT,
    libelle       TEXT,
    legume        TEXT NOT NULL,
    famille       TEXT NOT NULL,
    classement    TEXT NOT NULL,   -- regle | arbitrage | non identifie
    unite         TEXT NOT NULL,   -- kg | piece | inconnu
    unite_source  TEXT NOT NULL,   -- facture | deduite | inconnu
    quantite      REAL NOT NULL,
    pu_ht         REAL NOT NULL,
    total_ht      REAL NOT NULL,
    total_ttc     REAL
);

CREATE INDEX idx_lignes_annee ON lignes(annee);
CREATE INDEX idx_lignes_legume ON lignes(legume);
CREATE INDEX idx_lignes_client ON lignes(code_client);
"""

# Une annee du corpus est tenue pour suspecte si elle est a la fois eloignee
# de toutes les autres et portee par une poignee de factures seulement. Les
# deux conditions ensemble, jamais l'une sans l'autre : voir
# reperer_dates_invraisemblables pour le detail et le contre-exemple.
ECART_ANNEE_ISOLEE = 2
PART_MAX_ANNEE_ISOLEE = 0.05

UNITES = {
    "kg": "kg",
    "k": "kg",
    "p": "piece",
    "u": "piece",
    "piece": "piece",
    "pièce": "piece",
}

COLONNES_ARBITRAGE = [
    "ref", "unite", "pu_ht", "annee", "nb_lignes", "total_ht",
    "pistes_catalogue", "factures", "legume", "famille",
]

COMMENTAIRES_ARBITRAGE = [
    "# Lignes de facture imprimees sans libelle exploitable.",
    "# Renseigner les deux dernieres colonnes (legume;famille) puis relancer :",
    "# ces lignes rejoindront alors les totaux au lieu de rester en 'Non identifie'.",
    "# La colonne pistes_catalogue liste les produits du catalogue qui ont la meme",
    "# unite et le meme prix unitaire. Elle propose, elle ne tranche pas.",
    "# Ne pas modifier les quatre premieres colonnes : elles forment la cle.",
]


@dataclass
class Rapport:
    """Bilan d'une reconstruction, et chemin de la base pas encore publiee.

    ``base_temporaire`` porte la base SQLite ecrite pendant la reconstruction.
    Elle n'est PAS encore a sa place definitive : c'est l'appelant qui publie
    la base, le rapport et l'export en un seul geste, une fois les trois
    produits (voir ``cli.publier``). Tant que ce geste n'a pas eu lieu, les
    fichiers de la campagne precedente restent intacts et coherents entre eux.
    """

    fichiers_vus: int
    factures_lues: int
    factures_integrees: int
    factures_rejetees: list[tuple[str, str]]
    doublons: list[tuple[str, str]]
    conflits: list[tuple[str, str]]
    erreurs_lecture: list[tuple[str, str]]
    erreurs_arbitrage: list[tuple[int, str]]
    lignes: int
    lignes_arbitrees: int
    lignes_non_identifiees: int
    ca_non_identifie: float
    ca_total: float
    # Toujours renseigne par construire() : le type le dit, pour que l'appelant
    # n'ait pas a verifier une absence qui ne se produit jamais.
    base_temporaire: Path
    dates_suspectes: list[tuple[str, str]] = field(default_factory=list)


def _unite_normalisee(brut: str) -> str:
    return UNITES.get(brut.strip().lower(), "inconnu")


def classer_ligne(ligne: Ligne, regles: list[Regle]) -> tuple[str, str]:
    """Classe uniquement le texte porte par la ligne de vente elle-meme."""
    return classer(ligne.ref, ligne.libelle_propre, regles)


def _cle_arbitrage(ref: str, unite: str, pu_ht: float, annee: int) -> str:
    return f"{ref.strip()}|{unite}|{pu_ht:.2f}|{annee}"


def _lignes_arbitrage(chemin: Path) -> list[tuple[int, list[str]]]:
    if not chemin.exists():
        return []
    lignes = []
    with chemin.open("r", encoding="utf-8-sig", newline="") as fichier:
        for numero, champs in enumerate(csv.reader(fichier, delimiter=";"), start=1):
            if not champs or not any(c.strip() for c in champs):
                continue
            premier = champs[0].strip()
            if premier.startswith("#") or premier == "ref":
                continue
            lignes.append((numero, champs))
    return lignes


def charger_arbitrage(
    chemin: Path,
) -> tuple[dict[str, tuple[str, str]], list[tuple[int, str]]]:
    """Lit les decisions manuelles et nomme toute decision inutilisable."""
    decisions = {}
    erreurs = []
    for numero, champs in _lignes_arbitrage(chemin):
        if len(champs) < 9:
            erreurs.append((numero, "moins de 9 colonnes"))
            continue
        ref, unite, pu, annee = champs[0], champs[1], champs[2], champs[3]
        legume = champs[8].strip()
        famille = champs[9].strip() if len(champs) > 9 else ""
        if legume:
            try:
                prix = nettoyer_nombre(pu)
                if prix is None:
                    raise ValueError("prix illisible")
                cle = _cle_arbitrage(ref, unite, prix, int(annee))
            except ValueError as exc:
                erreurs.append((numero, str(exc)))
                continue
            decisions[cle] = (legume, famille or legume)
    return decisions, erreurs


def _empreinte_facture(facture: Facture) -> tuple:
    lignes = tuple(
        (
            ligne.ref, ligne.libelle, ligne.unite, ligne.quantite,
            ligne.pu_ht, ligne.pu_ttc, ligne.remise, ligne.total_ht,
            ligne.total_ttc, ligne.taxe,
        )
        for ligne in facture.lignes
    )
    return (
        facture.date, facture.code_client, facture.nom_client,
        facture.total_ht_imprime, lignes,
    )


def nom_client_facture(facture: Facture, clients: dict[str, str]) -> str:
    """Nom imprime sur la facture, puis ancien catalogue en dernier recours."""
    return (
        facture.nom_client.strip()
        or clients.get(facture.code_client, "").strip()
        or f"Client {facture.code_client}"
    )


def selectionner_factures(
    factures: list[Facture],
) -> tuple[
    list[Facture], list[tuple[str, str]], list[tuple[str, str]],
    list[tuple[str, str]],
]:
    """Rejette les factures invalides et les doublons contradictoires."""
    groupes: dict[str, list[Facture]] = defaultdict(list)
    rejetees: list[tuple[str, str]] = []
    for facture in factures:
        if facture.reconcilie:
            groupes[facture.numero].append(facture)
            continue
        if facture.erreurs_lignes:
            motif = "; ".join(facture.erreurs_lignes[:3])
        elif facture.total_ht_imprime is None:
            motif = "Total HT introuvable"
        else:
            motif = (
                f"somme des lignes {facture.total_ht_calcule:.2f} "
                f"vs total imprime {facture.total_ht_imprime:.2f}"
            )
        rejetees.append((facture.fichier, motif))

    retenues: list[Facture] = []
    doublons: list[tuple[str, str]] = []
    conflits: list[tuple[str, str]] = []
    for numero, groupe in groupes.items():
        premiere = groupe[0]
        if all(_empreinte_facture(f) == _empreinte_facture(premiere) for f in groupe[1:]):
            retenues.append(premiere)
            for copie in groupe[1:]:
                doublons.append(
                    (copie.fichier, f"meme contenu {numero} que {premiere.fichier}")
                )
        else:
            fichiers = ", ".join(f.fichier for f in groupe)
            for facture in groupe:
                conflits.append(
                    (facture.fichier, f"numero {numero} contradictoire ({fichiers})")
                )
    return retenues, rejetees, doublons, conflits


def reperer_dates_invraisemblables(
    factures: list[Facture], aujourd_hui: date | None = None
) -> list[tuple[str, str]]:
    """Signale, sans jamais ecarter, les dates qui ne tiennent pas debout.

    La date est le seul champ portant de la chaine qu'aucun autre champ ne
    contredit : les montants sont recoupes ligne a ligne puis contre le total
    imprime, les doublons sont compares sur leur contenu, mais une date
    simplement fausse reconcilie parfaitement et deplace pourtant la vente
    dans le mauvais mois, voire dans le mauvais exercice.

    Deux signaux, et deux seulement :

    * une date posterieure a aujourd'hui, qui n'existe pas encore ;
    * une annee a la fois **isolee** du reste du corpus (plus de
      ``ECART_ANNEE_ISOLEE`` ans de tout autre millesime) et **marginale**
      (au plus ``PART_MAX_ANNEE_ISOLEE`` des factures).

    La condition de marginalite n'est pas cosmetique : sans elle, une
    exploitation qui a des archives 2020-2022 puis reprend en 2026 verrait
    *toutes* ses factures courantes signalees, l'annee 2026 etant isolee de
    quatre ans. Un millesime qui porte une part significative du corpus decrit
    une interruption d'activite, pas une faute de frappe.

    Ce que le controle ne voit pas, et qu'il faut savoir :

    * une erreur de quelques semaines ou de quelques mois, que rien dans la
      facture ne permet de contredire ;
    * une faute d'annee isolee sur un petit corpus : une seule facture parmi
      dix pese 10 %, donc au-dela du seuil. Il faut une vingtaine de factures
      pour qu'une saisie erronee redescende sous la barre et ressorte.
    """
    aujourd_hui = aujourd_hui or date.today()
    signalements: list[tuple[str, str]] = []
    if not factures:
        return signalements

    comptes = Counter(facture.annee for facture in factures)
    annees = sorted(comptes)

    def isolee(annee: int) -> bool:
        voisines = [abs(annee - autre) for autre in annees if autre != annee]
        return bool(voisines) and min(voisines) > ECART_ANNEE_ISOLEE

    def marginale(annee: int) -> bool:
        return comptes[annee] / len(factures) <= PART_MAX_ANNEE_ISOLEE

    suspectes = {an for an in annees if isolee(an) and marginale(an)}

    for facture in factures:
        if facture.date > aujourd_hui.isoformat():
            signalements.append(
                (facture.fichier, f"date dans le futur ({facture.date})")
            )
        elif facture.annee in suspectes:
            signalements.append(
                (
                    facture.fichier,
                    f"annee {facture.annee} isolee et marginale "
                    f"({comptes[facture.annee]} facture(s) sur {len(factures)}, "
                    f"corpus {annees[0]} a {annees[-1]}) : date a verifier",
                )
            )
    return signalements


def unites_reference(
    factures: list[Facture], regles: list[Regle]
) -> dict[str, str]:
    """Unite unique observee par legume, hors lignes non identifiees."""
    unites_vues: dict[str, Counter] = defaultdict(Counter)
    for facture in factures:
        for ligne in facture.lignes:
            legume, _ = classer_ligne(ligne, regles)
            unite = _unite_normalisee(ligne.unite)
            if legume != NON_IDENTIFIE and unite != "inconnu":
                unites_vues[legume][unite] += 1
    return {
        legume: compte.most_common(1)[0][0]
        for legume, compte in unites_vues.items()
        if len(compte) == 1
    }


def construire(
    dossier_factures: str | Path,
    fichier_clients: str | Path | None,
    fichier_produits: str | Path | None,
    base: str | Path,
    fichier_arbitrage: str | Path,
    progression: Callable[[int, int], None] | None = None,
) -> Rapport:
    factures, erreurs = lire_dossier(dossier_factures, progression)
    clients = charger_clients(fichier_clients) if fichier_clients else {}
    produits = charger_produits(fichier_produits) if fichier_produits else []
    regles = charger_regles()
    decisions, erreurs_arbitrage = charger_arbitrage(Path(fichier_arbitrage))
    retenues, rejetees, doublons, conflits = selectionner_factures(factures)

    # Unite de reference par legume : si toutes les lignes renseignees d'un
    # legume portent la meme unite, on l'applique aux lignes ou l'unite n'a
    # pas ete imprimee. Toute deduction est tracee dans unite_source.
    unite_par_legume = unites_reference(retenues, regles)
    suspectes = reperer_dates_invraisemblables(retenues)

    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    base_temporaire = base.with_name(f".{base.name}.tmp")
    if base_temporaire.exists():
        base_temporaire.unlink()
    cx = sqlite3.connect(base_temporaire)
    cx.executescript(SCHEMA)

    a_arbitrer: dict[str, dict] = {}
    nb_lignes = nb_arbitrees = nb_non_id = 0
    ca_total = ca_non_id = 0.0
    identifiant = 0

    for f in retenues:
        client = nom_client_facture(f, clients)
        cx.execute(
            """INSERT INTO factures
               (numero, fichier, date, annee, mois, code_client, client,
                total_ht_imprime, total_ht_calcule, nb_lignes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                f.numero,
                f.fichier,
                f.date,
                f.annee,
                f.mois,
                f.code_client,
                client,
                f.total_ht_imprime,
                f.total_ht_calcule,
                len(f.lignes),
            ),
        )
        for ligne in f.lignes:
            legume, famille = classer_ligne(ligne, regles)
            classement = "regle"
            unite = _unite_normalisee(ligne.unite)
            unite_source = "facture" if unite != "inconnu" else "inconnu"

            if legume == NON_IDENTIFIE:
                cle = _cle_arbitrage(ligne.ref, unite, ligne.pu_ht, f.annee)
                if cle in decisions:
                    legume, famille = decisions[cle]
                    classement = "arbitrage"
                    nb_arbitrees += 1
                else:
                    classement = "non identifie"
                    nb_non_id += 1
                    ca_non_id += ligne.total_ht
                    entree = a_arbitrer.setdefault(
                        cle,
                        {
                            "ref": ligne.ref,
                            "unite": unite,
                            "pu_ht": ligne.pu_ht,
                            "annee": f.annee,
                            "nb": 0,
                            "ht": 0.0,
                            "factures": [],
                        },
                    )
                    entree["nb"] += 1
                    entree["ht"] += ligne.total_ht
                    if len(entree["factures"]) < 4:
                        entree["factures"].append(f"{f.numero} {client}")

            if (
                unite == "inconnu"
                and legume != NON_IDENTIFIE
                and legume in unite_par_legume
            ):
                unite = unite_par_legume[legume]
                unite_source = "deduite"

            identifiant += 1
            nb_lignes += 1
            ca_total += ligne.total_ht
            cx.execute(
                """INSERT INTO lignes
                   (id, facture, date, annee, mois, code_client, client, ref,
                    libelle, legume, famille, classement, unite, unite_source,
                    quantite, pu_ht, total_ht, total_ttc)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifiant,
                    f.numero,
                    f.date,
                    f.annee,
                    f.mois,
                    f.code_client,
                    client,
                    ligne.ref,
                    ligne.libelle,
                    legume,
                    famille,
                    classement,
                    unite,
                    unite_source,
                    ligne.quantite,
                    ligne.pu_ht,
                    ligne.total_ht,
                    ligne.total_ttc,
                ),
            )

    cx.commit()
    cx.close()
    # La base reste en .tmp : elle ne sera mise en place qu'une fois le
    # rapport et l'export produits, pour que les trois fichiers publies
    # decrivent toujours la meme campagne.

    _ecrire_arbitrage(Path(fichier_arbitrage), a_arbitrer, produits)

    return Rapport(
        fichiers_vus=len(factures) + len(erreurs),
        factures_lues=len(factures),
        factures_integrees=len(retenues),
        factures_rejetees=rejetees,
        doublons=doublons,
        conflits=conflits,
        erreurs_lecture=erreurs,
        erreurs_arbitrage=erreurs_arbitrage,
        lignes=nb_lignes,
        lignes_arbitrees=nb_arbitrees,
        lignes_non_identifiees=nb_non_id,
        ca_non_identifie=round(ca_non_id, 2),
        ca_total=round(ca_total, 2),
        base_temporaire=base_temporaire,
        dates_suspectes=suspectes,
    )


def _ecrire_arbitrage(
    chemin: Path,
    a_arbitrer: dict[str, dict],
    produits: list[dict[str, str]],
) -> None:
    """Reecrit le fichier d'arbitrage en conservant les decisions deja prises."""
    lignes_existantes = [champs for _, champs in _lignes_arbitrage(chemin)]
    deja = set()
    for champs in lignes_existantes:
        if len(champs) < 4:
            continue
        prix = nettoyer_nombre(champs[2])
        try:
            if prix is not None:
                deja.add(_cle_arbitrage(champs[0], champs[1], prix, int(champs[3])))
        except ValueError:
            continue

    nouvelles = []
    for cle, e in sorted(a_arbitrer.items(), key=lambda kv: -kv[1]["ht"]):
        if cle in deja:
            continue
        pistes = candidats_produits(
            produits, e["unite"].replace("piece", "p"), e["pu_ht"]
        )
        nouvelles.append(
            [
                e["ref"], e["unite"], f"{e['pu_ht']:.2f}", str(e["annee"]),
                str(e["nb"]), f"{e['ht']:.2f}",
                " / ".join(pistes[:6]) or "aucune piste",
                " + ".join(e["factures"]), "", "",
            ]
        )

    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(f".{chemin.name}.tmp")
    with temporaire.open("w", encoding="utf-8-sig", newline="") as fichier:
        ecrivain = csv.writer(fichier, delimiter=";", lineterminator="\n")
        for commentaire in COMMENTAIRES_ARBITRAGE:
            ecrivain.writerow([commentaire])
        ecrivain.writerow(COLONNES_ARBITRAGE)
        for champs in lignes_existantes:
            ecrivain.writerow(champs + [""] * max(0, 10 - len(champs)))
        ecrivain.writerows(nouvelles)
    temporaire.replace(chemin)
