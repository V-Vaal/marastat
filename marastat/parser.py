"""Extraction des lignes de vente d'une facture PDF.

Le PDF est genere par le logiciel de facturation puis imprime via
"Microsoft Print To PDF". Le texte est reellement present dans le
fichier : aucun OCR n'est necessaire.

Principe : on retrouve la ligne d'en-tete du tableau sur chaque page
(Designation / Unite / Quantite / PU HT / ...), on en deduit les bornes
horizontales de chaque colonne, puis on affecte chaque *caractere* de la
page a une colonne selon sa position. Le travail au caractere (et non au
mot) est indispensable : la colonne "Ref" est tronquee a l'impression et
son dernier fragment se colle a la designation ("SalCantine Salade...").
Decouper sur la frontiere de colonne separe proprement les deux.

Cette approche resiste aussi au changement de gabarit de 2026
(disparition de la colonne "Ref") parce que les bornes sont recalculees
pour chaque page.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from math import isfinite
from pathlib import Path

import pdfplumber

# Colonnes du tableau, dans l'ordre d'apparition. Les libelles sont ceux
# imprimes dans l'en-tete ; certains sont composes de plusieurs mots.
ENTETES = [
    ("ref", ["Réf"]),
    ("libelle", ["Désignation"]),
    ("unite", ["Unité"]),
    ("quantite", ["Quantité"]),
    ("pu_ht", ["PU", "HT"]),
    ("pu_ttc", ["PU", "TTC"]),
    ("remise", ["Remise"]),
    ("total_ht", ["Total", "HT"]),
    ("total_ttc", ["Total", "TTC"]),
    ("taxe", ["Taxe"]),
]

# Colonnes alignees a gauche : la borne se cale sur le debut de l'en-tete.
# Les colonnes numeriques sont alignees a droite : la borne se place a
# mi-chemin entre deux en-tetes.
COLONNES_TEXTE = {"ref", "libelle", "unite"}

TOLERANCE_LIGNE = 3.0  # points PDF : deux caracteres du meme "top" a 3pt pres
ECART_CONTINUATION = 16.0  # au dela, ce n'est plus la suite d'un libelle

# Tout ce qui suit l'un de ces marqueurs sur une page n'appartient plus au
# tableau des lignes de vente (pied de page, cartouche de reglement).
FIN_TABLEAU = re.compile(r"Mode de règlement|Net à payer|Total HT\s*:|Règlements\s*:")

# Lignes intercalaires imprimees au milieu du tableau : elles ne portent
# aucune vente et ne sont pas la suite d'un libelle.
LIGNE_INTERCALAIRE = re.compile(r"Bon de livraison|^\d{4}$")

RE_ENTETE_FACTURE = re.compile(
    r"Facture\s+(?P<numero>[A-Z]-\d{4}-\d+)\s+Code Client\s*:\s*(?P<client>\d+)"
)
RE_DATE = re.compile(r"Le\s+(?P<j>\d{2})-(?P<m>\d{2})-(?P<a>\d{4})")
RE_TOTAL_HT = re.compile(r"Total HT\s*:\s*(?P<montant>[\d\s  ]+,\d{2})\s*€")
PREFIXE_DESTINATAIRE = re.compile(r"^(?:M(?:me|lle)?\.?|EI)\s+", re.IGNORECASE)
RE_NOMBRE = re.compile(
    r"^[+-]?(?:\d+|\d{1,3}(?: \d{3})+)(?:[,.]\d+)?$"
)


class FactureIllisible(Exception):
    """Le fichier ne ressemble pas a une facture exploitable."""


def nettoyer_nombre(texte: str) -> float | None:
    """'1 234,50' (espaces insecables compris) -> 1234.5"""
    if not texte:
        return None
    t = unicodedata.normalize("NFKC", texte).replace("€", "").strip()
    if not RE_NOMBRE.fullmatch(t):
        return None
    try:
        valeur = float(t.replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return valeur if isfinite(valeur) else None


def total_ligne_valide(
    quantite: float, pu_ht: float, remise: float | None, total_ht: float
) -> bool:
    """Verifie le calcul imprime d'une ligne, arrondi au centime commercial."""
    try:
        montant = Decimal(str(quantite)) * Decimal(str(pu_ht))
        if remise is not None:
            montant *= (Decimal(100) - Decimal(str(remise))) / Decimal(100)
        attendu = montant.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        imprime = Decimal(str(total_ht)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        return False
    return attendu == imprime


@dataclass
class Ligne:
    ref: str
    libelle: str
    libelle_propre: str
    unite: str
    quantite: float
    pu_ht: float
    pu_ttc: float | None
    remise: float | None
    total_ht: float
    total_ttc: float | None
    taxe: str
    page: int


@dataclass
class Facture:
    fichier: str
    numero: str
    date: str  # ISO aaaa-mm-jj
    code_client: str
    total_ht_imprime: float | None
    nom_client: str = ""
    lignes: list[Ligne] = field(default_factory=list)
    erreurs_lignes: list[str] = field(default_factory=list)

    @property
    def annee(self) -> int:
        # L'annee vient de la date d'emission, jamais du numero de facture
        # ni du dossier : il existe des factures F-2026-xxxx datees de 2025.
        return int(self.date[:4])

    @property
    def mois(self) -> int:
        return int(self.date[5:7])

    @property
    def total_ht_calcule(self) -> float:
        return round(sum(ligne.total_ht for ligne in self.lignes), 2)

    @property
    def reconcilie(self) -> bool:
        if self.total_ht_imprime is None or self.erreurs_lignes:
            return False
        return abs(self.total_ht_calcule - self.total_ht_imprime) < 0.02

    @property
    def ecart(self) -> float:
        if self.total_ht_imprime is None:
            return float("nan")
        return round(self.total_ht_calcule - self.total_ht_imprime, 2)


def _grouper_par_ligne(elements: list[dict]) -> list[list[dict]]:
    lignes: list[list[dict]] = []
    for el in sorted(elements, key=lambda e: (e["top"], e["x0"])):
        if lignes and abs(el["top"] - lignes[-1][0]["top"]) <= TOLERANCE_LIGNE:
            lignes[-1].append(el)
        else:
            lignes.append([el])
    return [sorted(groupe, key=lambda e: e["x0"]) for groupe in lignes]


def extraire_nom_client(mots: list[dict], largeur_page: float) -> str:
    """Extrait la raison sociale du bloc destinataire de la facture.

    Le vendeur est imprime a gauche et le destinataire a droite, au-dessus
    du titre ``Facture``. La premiere ligne du bloc droit est la raison
    sociale ; les lignes suivantes constituent son adresse.
    """
    limite_basse = next(
        (mot["top"] for mot in mots if mot["text"].strip() == "Facture"),
        230.0,
    )
    for ligne in _grouper_par_ligne(mots):
        top = ligne[0]["top"]
        if not 70.0 <= top < limite_basse:
            continue
        bloc_droit = [mot for mot in ligne if mot["x0"] >= largeur_page / 2]
        if bloc_droit:
            nom = " ".join(mot["text"].strip() for mot in bloc_droit).strip()
            return PREFIXE_DESTINATAIRE.sub("", nom).strip()
    return ""


def _bornes_colonnes(mots_entete: list[dict]) -> dict[str, tuple[float, float]] | None:
    """Deduit les bornes x de chaque colonne depuis la ligne d'en-tete."""
    textes = [m["text"] for m in mots_entete]
    if "Désignation" not in textes or "Quantité" not in textes:
        return None

    ancres: list[tuple[str, float, float]] = []
    i = 0
    for nom, libelle in ENTETES:
        j = i
        while j < len(mots_entete):
            if [m["text"] for m in mots_entete[j : j + len(libelle)]] == libelle:
                bloc = mots_entete[j : j + len(libelle)]
                ancres.append((nom, bloc[0]["x0"], bloc[-1]["x1"]))
                i = j + len(libelle)
                break
            j += 1
        # colonne absente (cas de "Réf" sur le gabarit 2026) : on continue

    if len(ancres) < 5:
        return None

    gauches: list[float] = []
    for k, (nom, x0, _x1) in enumerate(ancres):
        if k == 0:
            gauches.append(0.0)
        elif nom in COLONNES_TEXTE:
            gauches.append(x0 - 2.0)
        else:
            gauches.append((ancres[k - 1][2] + x0) / 2)

    bornes: dict[str, tuple[float, float]] = {}
    for k, (nom, _, _) in enumerate(ancres):
        droite = gauches[k + 1] if k + 1 < len(gauches) else 10_000.0
        bornes[nom] = (gauches[k], droite)
    return bornes


def _cellules(
    ligne: list[dict], bornes: dict[str, tuple[float, float]]
) -> dict[str, str]:
    """Repartit les caracteres d'une ligne dans les colonnes."""
    tampon: dict[str, list[str]] = {nom: [] for nom in bornes}
    for car in ligne:
        centre = (car["x0"] + car["x1"]) / 2
        for nom, (g, d) in bornes.items():
            if g <= centre < d:
                tampon[nom].append(car["text"])
                break
    return {nom: "".join(v).strip() for nom, v in tampon.items()}


def texte_continuation(cellules: dict[str, str]) -> str | None:
    """Retourne une continuation sure, limitee a la seule designation."""
    suite = cellules.get("libelle", "").strip()
    autres_colonnes = any(
        valeur for nom, valeur in cellules.items() if nom != "libelle"
    )
    contient_chiffre = any(c.isdigit() for c in "".join(cellules.values()))
    return suite if suite and not autres_colonnes and not contient_chiffre else None


def lire_facture(chemin: str | Path) -> Facture:
    chemin = Path(chemin)
    facture: Facture | None = None

    with pdfplumber.open(chemin) as pdf:
        for num_page, page in enumerate(pdf.pages, start=1):
            texte = page.extract_text() or ""
            mots_page = page.extract_words()

            if facture is None:
                m = RE_ENTETE_FACTURE.search(texte)
                d = RE_DATE.search(texte)
                if not m or not d:
                    continue
                try:
                    date_facture = date(
                        int(d.group("a")), int(d.group("m")), int(d.group("j"))
                    ).isoformat()
                except ValueError as exc:
                    raise FactureIllisible(
                        f"Date invalide dans {chemin.name} : {d.group(0)}"
                    ) from exc
                facture = Facture(
                    fichier=chemin.name,
                    numero=m.group("numero"),
                    date=date_facture,
                    code_client=m.group("client"),
                    total_ht_imprime=None,
                    nom_client=extraire_nom_client(mots_page, page.width),
                )

            if facture.total_ht_imprime is None:
                t = RE_TOTAL_HT.search(texte)
                if t:
                    facture.total_ht_imprime = nettoyer_nombre(t.group("montant"))

            bornes = None
            debut = 0.0
            for ligne_mots in _grouper_par_ligne(mots_page):
                b = _bornes_colonnes(ligne_mots)
                if b:
                    bornes, debut = b, ligne_mots[0]["top"]
                    break
            if bornes is None:
                continue

            precedent_top = None
            continuations = 0
            for ligne in _grouper_par_ligne(page.chars):
                if ligne[0]["top"] <= debut + TOLERANCE_LIGNE:
                    continue
                cel = _cellules(ligne, bornes)
                brut = "".join(cel.values())
                if FIN_TABLEAU.search(brut):
                    break  # pied de page : le tableau est termine
                if LIGNE_INTERCALAIRE.search(brut.strip()):
                    precedent_top = None  # rien a rattacher a cette ligne
                    continue

                qte = nettoyer_nombre(cel.get("quantite", ""))
                ht = nettoyer_nombre(cel.get("total_ht", ""))

                est_ligne_vente = cel.get("taxe", "").startswith("TVA")
                if est_ligne_vente:
                    pu_ht = nettoyer_nombre(cel.get("pu_ht", ""))
                    remise = nettoyer_nombre(cel.get("remise", ""))
                    if qte is None or pu_ht is None or ht is None:
                        facture.erreurs_lignes.append(
                            f"page {num_page} : quantite, prix ou total HT illisible"
                        )
                        precedent_top = None
                        continuations = 0
                        continue
                    ligne_vente = Ligne(
                        ref=cel.get("ref", ""),
                        libelle=cel.get("libelle", ""),
                        libelle_propre=cel.get("libelle", ""),
                        unite=cel.get("unite", ""),
                        quantite=qte,
                        pu_ht=pu_ht,
                        pu_ttc=nettoyer_nombre(cel.get("pu_ttc", "")),
                        remise=remise,
                        total_ht=ht,
                        total_ttc=nettoyer_nombre(cel.get("total_ttc", "")),
                        taxe=cel.get("taxe", ""),
                        page=num_page,
                    )
                    if not total_ligne_valide(qte, pu_ht, remise, ht):
                        facture.erreurs_lignes.append(
                            f"page {num_page} : {qte:g} x {pu_ht:g} "
                            f"ne donne pas {ht:.2f} HT"
                        )
                    facture.lignes.append(
                        ligne_vente
                    )
                    precedent_top = ligne[0]["top"]
                    continuations = 0
                elif (
                    facture.lignes
                    and facture.lignes[-1].page == num_page
                    and precedent_top is not None
                    and ligne[0]["top"] - precedent_top <= ECART_CONTINUATION
                    and continuations < 2
                ):
                    # suite d'un libelle trop long pour tenir sur une ligne
                    suite = texte_continuation(cel)
                    if suite:
                        derniere = facture.lignes[-1]
                        derniere.libelle = f"{derniere.libelle} {suite}".strip()
                        precedent_top = ligne[0]["top"]
                        continuations += 1

    if facture is None:
        raise FactureIllisible(f"En-tete introuvable dans {chemin.name}")
    return facture


def lire_dossier(
    racine: str | Path,
    progression: Callable[[int, int], None] | None = None,
) -> tuple[list[Facture], list[tuple[str, str]]]:
    """Retourne (factures lues, erreurs) pour tous les PDF sous `racine`."""
    factures, erreurs = [], []
    fichiers = sorted(Path(racine).rglob("*.pdf"))
    total = len(fichiers)
    for position, pdf in enumerate(fichiers, start=1):
        try:
            factures.append(lire_facture(pdf))
        except Exception as exc:
            erreurs.append((pdf.name, f"{type(exc).__name__}: {exc}"))
        finally:
            if progression is not None:
                progression(position, total)
    return factures, erreurs
