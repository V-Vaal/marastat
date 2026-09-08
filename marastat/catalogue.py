"""Clients, produits et classement des lignes en legumes.

Les deux CSV exportes par le logiciel de facturation (Clients.csv,
Produits.csv) sont encodes en UTF-16 avec un separateur point-virgule.

Le classement d'une ligne de facture en legume ne passe volontairement PAS
par un rapprochement avec le catalogue produits : la colonne "Ref" est
tronquee a l'impression et son prefixe est ambigu une fois sur deux
("Pro Oignon" designe aussi bien l'oignon jaune que rouge ou rose). On
classe donc sur le texte imprime (reference tronquee + designation) via un
fichier de regles ordonne, lisible et modifiable sans toucher au code.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DOSSIER = Path(__file__).parent
FICHIER_REGLES = DOSSIER / "regles_legumes.csv"

NON_IDENTIFIE = "Non identifié"


def _lire_csv_logiciel(
    chemin: Path, colonnes_attendues: set[str]
) -> list[dict[str, str]]:
    """Lit un export et refuse un decodage syntaxiquement trompeur."""
    donnees = chemin.read_bytes()
    if donnees.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodages = ("utf-16",)
    elif donnees.startswith(b"\xef\xbb\xbf"):
        encodages = ("utf-8-sig",)
    else:
        # Un UTF-8 de taille paire peut se decoder en UTF-16 sans erreur mais
        # produire des en-tetes illisibles. Sans BOM, UTF-8 est donc teste en
        # premier, puis l'ancien encodage Windows.
        encodages = ("utf-8", "cp1252")

    for encodage in encodages:
        try:
            texte = donnees.decode(encodage)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Encodage non reconnu pour {chemin.name}")

    lecteur = csv.DictReader(io.StringIO(texte), delimiter=";")
    presentes = {c.strip() for c in (lecteur.fieldnames or []) if c}
    manquantes = colonnes_attendues - presentes
    if manquantes:
        liste = ", ".join(sorted(manquantes))
        raise ValueError(f"{chemin.name} : colonnes manquantes ({liste})")
    return list(lecteur)


def sans_accent(texte: str) -> str:
    decompose = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Regle:
    motif: re.Pattern
    legume: str
    famille: str


def charger_regles(chemin: Path | None = None) -> list[Regle]:
    chemin = chemin or FICHIER_REGLES
    regles: list[Regle] = []
    with chemin.open(encoding="utf-8") as f:
        for numero, brut in enumerate(f, start=1):
            ligne = brut.strip()
            if not ligne or ligne.startswith(("#", "motif;")):
                continue
            try:
                motif, legume, famille = ligne.split(";")
                motif = motif.strip()
                if not motif or not legume.strip() or not famille.strip():
                    raise ValueError("champ vide")
                expression = re.compile(motif, re.IGNORECASE)
            except (ValueError, re.error) as exc:
                raise ValueError(
                    f"{chemin.name}, ligne {numero} : regle invalide ({exc})"
                ) from exc
            regles.append(Regle(expression, legume.strip(), famille.strip()))
    return regles


def classer(ref: str, libelle: str, regles: list[Regle]) -> tuple[str, str]:
    """Retourne (legume, famille) pour une ligne de facture."""
    texte = sans_accent(f"{ref} {libelle}".strip())
    if not texte:
        return NON_IDENTIFIE, NON_IDENTIFIE
    for regle in regles:
        if regle.motif.search(texte):
            return regle.legume, regle.famille
    return NON_IDENTIFIE, NON_IDENTIFIE


def charger_clients(chemin: str | Path) -> dict[str, str]:
    """Code client -> raison sociale."""
    clients = {}
    for ligne in _lire_csv_logiciel(Path(chemin), {"Code", "RaisonSociale"}):
        code = (ligne.get("Code") or "").strip()
        nom = (ligne.get("RaisonSociale") or "").strip()
        if code:
            clients[code] = nom or f"Client {code}"
    return clients


def charger_produits(chemin: str | Path) -> list[dict[str, str]]:
    return _lire_csv_logiciel(
        Path(chemin), {"Reference", "PrixVenteHT", "Unité"}
    )


def candidats_produits(
    produits: list[dict[str, str]], unite: str, pu_ht: float
) -> list[str]:
    """Produits du catalogue compatibles avec une unite et un prix unitaire.

    Sert uniquement a proposer des pistes pour l'arbitrage manuel des lignes
    imprimees sans aucun libelle. On ne tranche jamais automatiquement :
    a 2,40 EUR/kg le catalogue compte onze produits differents.
    """
    pistes = []
    for p in produits:
        prix = (p.get("PrixVenteHT") or "").replace(",", ".")
        try:
            meme_prix = abs(float(prix) - pu_ht) < 0.005
        except ValueError:
            meme_prix = False
        meme_unite = (p.get("Unité") or "").strip().lower() == unite.strip().lower()
        if meme_prix and (meme_unite or not unite):
            pistes.append(p["Reference"].strip())
    return pistes
