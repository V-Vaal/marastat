"""Fabrique un jeu de factures PDF de demonstration.

Le depot ne contient aucune facture reelle. Ce script produit, de facon
deterministe, des PDF au meme gabarit que ceux qu'un logiciel de facturation
imprime via "Print To PDF" : meme cartouche, meme tableau, memes colonnes.
Ils servent a la fois de jeu d'essai reproductible et de demonstration :

    python outils/generer_demo.py
    python -m marastat --factures exemples/factures --sortie exemples/rapport \
        --sans-ouverture --sans-notification

Le jeu contient volontairement les cas qui font la valeur de l'outil :

* deux gabarits (avec et sans colonne "Ref", comme le changement de 2026) ;
* une designation trop longue, poursuivie sur la ligne suivante ;
* une copie a l'identique d'une facture, qui ne doit etre comptee qu'une fois ;
* deux factures de meme numero au contenu different, qui doivent etre ecartees ;
* une facture dont le total imprime ne correspond pas a ses lignes ;
* des lignes imprimees sans aucun libelle, qui partent en arbitrage ;
* une designation commencant par "=", qui ne doit jamais devenir une formule.

Dependance : reportlab (uniquement pour ce script, pas pour l'outil).
"""

from __future__ import annotations

import argparse
import calendar
import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

LARGEUR, HAUTEUR = A4

# Positions x des colonnes. Les colonnes de texte sont calees a gauche, les
# colonnes numeriques a droite : c'est la disposition que le parseur deduit de
# la ligne d'en-tete, et elle doit rester lisible meme sans separateurs.
X_TEXTE = {"ref": 30.0, "libelle": 78.0, "unite": 232.0}
X_NOMBRE = {
    "quantite": 300.0,
    "pu_ht": 348.0,
    "pu_ttc": 396.0,
    "remise": 436.0,
    "total_ht": 492.0,
    "total_ttc": 542.0,
}
X_TAXE = 550.0

TOP_DATE = 32.0
TOP_VENDEUR = 88.0
TOP_CLIENT = 132.0
TOP_TITRE = 224.0
TOP_ENTETE = 258.0
INTERLIGNE = 11.0

TVA = Decimal("0.055")

VENDEUR = [
    "EARL des Trois Sillons",
    "12 route du Moulin",
    "35000 Villeneuve",
]

CLIENTS = [
    ("1042", "Cantine Municipale Bourgneuf"),
    ("1108", "Épicerie du Bourg"),
    ("1153", "Restaurant Le Sillon"),
    ("1207", "Collège Jean Rostand"),
    ("1264", "Foyer des Genêts"),
    ("1319", "Marché de la Halle"),
]

# (reference imprimee tronquee, designation, unite, prix HT, saison)
CATALOGUE = [
    ("LegCar", "Carotte de plein champ", "kg", "1.85", (1, 2, 3, 4, 10, 11, 12)),
    ("LegPoi", "Poireau", "kg", "2.40", (1, 2, 3, 9, 10, 11, 12)),
    ("LegTom", "Tomate grappe", "kg", "3.20", (6, 7, 8, 9)),
    ("LegCou", "Courgette", "kg", "2.10", (5, 6, 7, 8, 9)),
    ("LegSal", "Salade batavia", "p", "0.95", (4, 5, 6, 7, 8, 9, 10)),
    ("PdtCha", "Pomme de terre Charlotte", "kg", "1.60", (7, 8, 9, 10, 11, 12)),
    ("LegOig", "Oignon jaune", "kg", "1.95", (1, 2, 8, 9, 10, 11, 12)),
    ("LegChF", "Chou-fleur", "p", "2.30", (3, 4, 10, 11, 12)),
    ("LegEpi", "Épinard", "kg", "4.10", (3, 4, 5, 10, 11)),
    ("LegBet", "Betterave rouge", "kg", "2.05", (6, 7, 8, 9, 10)),
    ("LegRad", "Radis botte", "p", "1.15", (4, 5, 6)),
    ("LegPot", "Potimarron", "kg", "2.25", (9, 10, 11, 12)),
]


def _derniers_jours(annee: int, mois: int) -> list[int]:
    """Les trois derniers jours reellement existants du mois.

    Les factures sont emises en fin de mois. Un 31 juin n'existe pas :
    le parseur le refuserait a juste titre, et le jeu de demonstration ne
    doit pas contenir de PDF impossible.
    """
    dernier = calendar.monthrange(annee, mois)[1]
    return [dernier - 2, dernier - 1, dernier]


def centimes(valeur: Decimal) -> Decimal:
    return valeur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fr(valeur: Decimal | float, decimales: int = 2) -> str:
    """Formate un nombre a la francaise, avec espace de milliers."""
    texte = f"{Decimal(str(valeur)):,.{decimales}f}"
    return texte.replace(",", " ").replace(".", ",")


@dataclass
class LigneVente:
    ref: str
    libelle: str
    unite: str
    quantite: Decimal
    pu_ht: Decimal
    remise: Decimal | None = None
    suite_libelle: str = ""

    @property
    def total_ht(self) -> Decimal:
        montant = self.quantite * self.pu_ht
        if self.remise is not None:
            montant *= (Decimal(100) - self.remise) / Decimal(100)
        return centimes(montant)

    @property
    def pu_ttc(self) -> Decimal:
        return centimes(self.pu_ht * (1 + TVA))

    @property
    def total_ttc(self) -> Decimal:
        return centimes(self.total_ht * (1 + TVA))


@dataclass
class FactureDemo:
    numero: str
    jour: int
    mois: int
    annee: int
    code_client: str
    nom_client: str
    lignes: list[LigneVente]
    avec_colonne_ref: bool = True
    total_imprime: Decimal | None = None

    @property
    def total_ht(self) -> Decimal:
        if self.total_imprime is not None:
            return self.total_imprime
        return centimes(sum((ligne.total_ht for ligne in self.lignes), Decimal(0)))


def _texte(c: canvas.Canvas, x: float, top: float, contenu: str) -> None:
    c.drawString(x, HAUTEUR - top, contenu)


def _texte_droite(c: canvas.Canvas, x: float, top: float, contenu: str) -> None:
    c.drawRightString(x, HAUTEUR - top, contenu)


def _entete_tableau(c: canvas.Canvas, avec_ref: bool) -> None:
    c.setFont("Helvetica-Bold", 6.5)
    if avec_ref:
        _texte(c, X_TEXTE["ref"], TOP_ENTETE, "Réf")
    _texte(c, X_TEXTE["libelle"], TOP_ENTETE, "Désignation")
    _texte(c, X_TEXTE["unite"], TOP_ENTETE, "Unité")
    _texte_droite(c, X_NOMBRE["quantite"], TOP_ENTETE, "Quantité")
    # "PU" et "HT" sont deux mots distincts a l'impression : le parseur
    # reconstitue l'ancre de colonne a partir de la paire.
    _texte_droite(c, X_NOMBRE["pu_ht"] - 14, TOP_ENTETE, "PU")
    _texte_droite(c, X_NOMBRE["pu_ht"], TOP_ENTETE, "HT")
    _texte_droite(c, X_NOMBRE["pu_ttc"] - 19, TOP_ENTETE, "PU")
    _texte_droite(c, X_NOMBRE["pu_ttc"], TOP_ENTETE, "TTC")
    _texte_droite(c, X_NOMBRE["remise"], TOP_ENTETE, "Remise")
    _texte_droite(c, X_NOMBRE["total_ht"] - 15, TOP_ENTETE, "Total")
    _texte_droite(c, X_NOMBRE["total_ht"], TOP_ENTETE, "HT")
    _texte_droite(c, X_NOMBRE["total_ttc"] - 19, TOP_ENTETE, "Total")
    _texte_droite(c, X_NOMBRE["total_ttc"], TOP_ENTETE, "TTC")
    _texte(c, X_TAXE, TOP_ENTETE, "Taxe")
    c.setLineWidth(0.3)
    c.line(28, HAUTEUR - TOP_ENTETE - 3, 580, HAUTEUR - TOP_ENTETE - 3)


def dessiner(facture: FactureDemo, destination: Path) -> None:
    c = canvas.Canvas(str(destination), pagesize=A4)
    c.setTitle(f"Facture {facture.numero}")

    c.setFont("Helvetica", 7.5)
    _texte_droite(
        c, 560, TOP_DATE, f"Le {facture.jour:02d}-{facture.mois:02d}-{facture.annee}"
    )

    # Bloc vendeur, a gauche. Il n'a aucun mot dans la moitie droite de la
    # page : c'est ce qui permet au parseur de ne pas le confondre avec le
    # destinataire.
    for rang, ligne in enumerate(VENDEUR):
        c.setFont("Helvetica-Bold" if rang == 0 else "Helvetica", 8)
        _texte(c, 30, TOP_VENDEUR + rang * 10, ligne)

    # Bloc destinataire, a droite. Sa premiere ligne est la raison sociale.
    c.setFont("Helvetica-Bold", 8)
    _texte(c, 330, TOP_CLIENT, facture.nom_client)
    c.setFont("Helvetica", 8)
    _texte(c, 330, TOP_CLIENT + 11, "3 place de l'Église")
    _texte(c, 330, TOP_CLIENT + 22, "35000 Villeneuve")

    c.setFont("Helvetica-Bold", 9)
    _texte(
        c,
        30,
        TOP_TITRE,
        f"Facture {facture.numero}    Code Client : {facture.code_client}",
    )

    _entete_tableau(c, facture.avec_colonne_ref)

    c.setFont("Helvetica", 6.5)
    top = TOP_ENTETE + 14
    for ligne in facture.lignes:
        if facture.avec_colonne_ref:
            _texte(c, X_TEXTE["ref"], top, ligne.ref)
        _texte(c, X_TEXTE["libelle"], top, ligne.libelle)
        _texte(c, X_TEXTE["unite"], top, ligne.unite)
        _texte_droite(c, X_NOMBRE["quantite"], top, fr(ligne.quantite))
        _texte_droite(c, X_NOMBRE["pu_ht"], top, fr(ligne.pu_ht))
        _texte_droite(c, X_NOMBRE["pu_ttc"], top, fr(ligne.pu_ttc))
        if ligne.remise is not None:
            _texte_droite(c, X_NOMBRE["remise"], top, fr(ligne.remise))
        _texte_droite(c, X_NOMBRE["total_ht"], top, fr(ligne.total_ht))
        _texte_droite(c, X_NOMBRE["total_ttc"], top, fr(ligne.total_ttc))
        _texte(c, X_TAXE, top, "TVA 5,5")
        top += INTERLIGNE
        if ligne.suite_libelle:
            # Continuation : uniquement de la designation, aucun chiffre.
            _texte(c, X_TEXTE["libelle"], top, ligne.suite_libelle)
            top += INTERLIGNE

    top += 8
    c.setFont("Helvetica-Bold", 8)
    _texte(c, 380, top, f"Total HT : {fr(facture.total_ht)} €")
    c.setFont("Helvetica", 7.5)
    _texte(c, 380, top + 12, f"TVA 5,5 % : {fr(centimes(facture.total_ht * TVA))} €")
    _texte(
        c,
        380,
        top + 24,
        f"Net à payer : {fr(centimes(facture.total_ht * (1 + TVA)))} €",
    )
    _texte(c, 30, top + 24, "Mode de règlement : virement à 30 jours")
    c.showPage()
    c.save()


def _lignes_du_mois(alea: random.Random, mois: int, annee: int) -> list[LigneVente]:
    disponibles = [p for p in CATALOGUE if mois in p[4]]
    alea.shuffle(disponibles)
    lignes = []
    for ref, libelle, unite, prix, _ in disponibles[: alea.randint(3, 6)]:
        quantite = Decimal(alea.choice(["4", "6.5", "8", "12", "15", "20", "25.5"]))
        # Le prix derive doucement d'une annee sur l'autre.
        derive = Decimal(1) + Decimal(annee - 2024) * Decimal("0.03")
        pu = centimes(Decimal(prix) * derive)
        remise = Decimal(5) if alea.random() < 0.12 else None
        lignes.append(LigneVente(ref, libelle, unite, quantite, pu, remise))
    return lignes


def construire_jeu() -> list[tuple[str, FactureDemo]]:
    """Retourne (nom de fichier, facture), de facon deterministe."""
    alea = random.Random(20260908)
    jeu: list[tuple[str, FactureDemo]] = []
    compteur = 0

    couverture = ((2024, range(1, 13)), (2025, range(1, 13)), (2026, range(1, 7)))
    for annee, mois_couverts in couverture:
        for mois in mois_couverts:
            for code, nom in CLIENTS:
                if alea.random() > 0.55:
                    continue
                compteur += 1
                numero = f"F-{annee}-{compteur:04d}"
                facture = FactureDemo(
                    numero=numero,
                    jour=alea.choice(_derniers_jours(annee, mois)),
                    mois=mois,
                    annee=annee,
                    code_client=code,
                    nom_client=nom,
                    lignes=_lignes_du_mois(alea, mois, annee),
                    # Le gabarit change en 2026 : la colonne "Ref" disparait.
                    avec_colonne_ref=annee < 2026,
                )
                if not facture.lignes:
                    continue
                jeu.append((f"{annee}/facture-{numero}.pdf", facture))

    # --- Cas particuliers, ajoutes apres coup pour rester reperables ---

    base = jeu[3][1]

    # 1. Designation trop longue, poursuivie sur la ligne suivante.
    longue = FactureDemo(
        numero="F-2025-9001",
        jour=30, mois=6, annee=2025,
        code_client="1153", nom_client="Restaurant Le Sillon",
        lignes=[
            LigneVente(
                "PdtCha", "Pomme de terre Charlotte de conservation,", "kg",
                Decimal(18), Decimal("1.70"),
                suite_libelle="calibre moyen, en sac papier",
            ),
            LigneVente("LegCou", "Courgette", "kg", Decimal(9), Decimal("2.16")),
        ],
    )
    jeu.append(("2025/facture-F-2025-9001.pdf", longue))

    # 2. Copie a l'identique : le meme contenu ne doit compter qu'une fois.
    jeu.append((f"2024/copie-{base.numero}.pdf", base))

    # 3. Meme numero, contenu different : conflit, les deux sont ecartees.
    conflit = FactureDemo(
        numero="F-2025-9002",
        jour=31, mois=3, annee=2025,
        code_client="1108", nom_client="Épicerie du Bourg",
        lignes=[LigneVente("LegPoi", "Poireau", "kg", Decimal(10), Decimal("2.47"))],
    )
    conflit_bis = FactureDemo(
        numero="F-2025-9002",
        jour=31, mois=3, annee=2025,
        code_client="1108", nom_client="Épicerie du Bourg",
        lignes=[LigneVente("LegCar", "Carotte de plein champ", "kg",
                           Decimal(10), Decimal("1.91"))],
    )
    jeu.append(("2025/facture-F-2025-9002.pdf", conflit))
    jeu.append(("2025/facture-F-2025-9002-bis.pdf", conflit_bis))

    # 4. Total imprime faux : la facture entiere est ecartee et signalee.
    fausse = FactureDemo(
        numero="F-2025-9003",
        jour=30, mois=9, annee=2025,
        code_client="1207", nom_client="Collège Jean Rostand",
        lignes=[LigneVente("LegTom", "Tomate grappe", "kg",
                           Decimal(12), Decimal("3.30"))],
        total_imprime=Decimal("42.00"),
    )
    jeu.append(("2025/facture-F-2025-9003.pdf", fausse))

    # 5. Lignes sans aucun libelle : elles partent en arbitrage et restent
    #    visibles en "Non identifie" plutot que d'etre devinees.
    muette = FactureDemo(
        numero="F-2026-9004",
        jour=30, mois=4, annee=2026,
        code_client="1264", nom_client="Foyer des Genêts",
        lignes=[
            LigneVente("", "", "kg", Decimal(6), Decimal("2.40")),
            LigneVente("", "", "kg", Decimal(4), Decimal("2.40")),
            LigneVente("LegSal", "Salade batavia", "p", Decimal(24), Decimal("1.01")),
        ],
        avec_colonne_ref=False,
    )
    jeu.append(("2026/facture-F-2026-9004.pdf", muette))

    # 6. Designation commencant par "=" : elle ne doit jamais devenir une
    #    formule dans l'export ouvert avec un tableur.
    piegee = FactureDemo(
        numero="F-2026-9005",
        jour=29, mois=5, annee=2026,
        code_client="1319", nom_client="Marché de la Halle",
        lignes=[
            LigneVente("", "=Carotte de plein champ", "kg",
                       Decimal(11), Decimal("1.96")),
        ],
        avec_colonne_ref=False,
    )
    jeu.append(("2026/facture-F-2026-9005.pdf", piegee))

    return jeu


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="generer_demo",
        description="Fabrique les factures PDF de demonstration.",
    )
    p.add_argument(
        "--sortie", type=Path, default=Path("exemples/factures"),
        help="dossier ou ecrire les PDF (defaut : exemples/factures)",
    )
    args = p.parse_args(argv)

    jeu = construire_jeu()
    for chemin_relatif, facture in jeu:
        destination = args.sortie / chemin_relatif
        destination.parent.mkdir(parents=True, exist_ok=True)
        dessiner(facture, destination)

    print(f"{len(jeu)} factures écrites dans {args.sortie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
