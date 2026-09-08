# Marastat

**Cultivez vos données de vente.**

[![ci](https://github.com/V-Vaal/marastat/actions/workflows/ci.yml/badge.svg)](https://github.com/V-Vaal/marastat/actions/workflows/ci.yml)
[![licence MIT](https://img.shields.io/badge/licence-MIT-informational)](LICENSE)
[![python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)

> **In short (EN).** Marastat turns a folder of printed PDF invoices into a
> single offline HTML sales report, plus a SQLite database and a flat CSV
> export. No OCR, no manual re-entry, no data leaving the machine. It was
> built for a market gardener who invoices a few dozen local customers every
> month and had three years of sales locked inside PDFs. An invoice is only
> counted if its own lines add up, to the cent, to the total printed on it.
> Everything below is in French, which is the language of the invoices, of the
> user, and of the domain vocabulary.

Marastat lit un dossier de factures PDF et en tire un rapport de ventes
consultable hors ligne, une base SQLite et un export CSV. Aucune ressaisie,
aucun OCR, aucune donnée qui sort de la machine.

Il est né d'un besoin réel : une exploitation maraîchère en circuit court
facture chaque mois quelques dizaines de clients (cantines, épiceries,
restaurants) avec un logiciel de facturation qui imprime de très bons PDF et
n'exporte rien d'exploitable. Trois ans de ventes étaient là, illisibles à
l'échelle. La contrainte de départ n'était pas technique : ne rien changer à
la façon dont les factures sont faites.

---

## Essayer en une minute

Aucune facture réelle ne se trouve dans ce dépôt. Le générateur ci-dessous en
fabrique un jeu fictif, au même gabarit que de vraies factures imprimées.

```bash
git clone https://github.com/V-Vaal/marastat && cd marastat
python -m pip install -e ".[dev]"

python outils/generer_demo.py
python -m marastat --factures exemples/factures --sortie exemples/rapport \
    --sans-ouverture --sans-notification
```

Sortie attendue :

```
  108 fichiers vus, 108 factures lues, 104 integrees, 415 lignes.
  ECARTEE   : facture-F-2025-9003.pdf (somme des lignes 39.60 vs total imprime 42.00)
  DOUBLON   : facture-F-2024-0004.pdf (meme contenu F-2024-0004 que copie-F-2024-0004.pdf)
  CONFLIT   : facture-F-2025-9002-bis.pdf (numero F-2025-9002 contradictoire (...))
  CONFLIT   : facture-F-2025-9002.pdf (numero F-2025-9002 contradictoire (...))
  2 lignes sans libelle (24.00 EUR, 0.2 % du CA), conservees comme 'Non identifie'
```

Ces quatre signalements ne sont pas des ratés : ce sont les cas que le jeu de
démonstration contient exprès, et la façon dont l'outil les traite est tout
son intérêt. Ouvrez ensuite `exemples/rapport/rapport.html`, ou regardez
directement [`exemples/rapport-demo.html`](exemples/rapport-demo.html).

## Ce que ça produit

| Fichier | À quoi il sert |
|---|---|
| `rapport.html` | Le rapport. Fichier unique, hors ligne, lisible sur un téléphone. |
| `ventes.csv` | Toutes les lignes à plat, pour ouvrir dans un tableur. |
| `ventes.sqlite` | La base, pour qui veut écrire ses propres requêtes SQL. |
| `arbitrage.csv` | Les rares lignes que la facture ne permet pas d'identifier. |

Le rapport couvre le chiffre d'affaires et les volumes par légume et par
année, la saisonnalité mois par mois, et la répartition par client avec les
évolutions d'une année sur l'autre. Il est autonome : les graphiques sont
construits en SVG dans la page, sans aucune ressource externe.

## Comment ça marche

```
Factures/*.pdf ─► extraction ─► classement ─► ventes.sqlite ─► rapport.html
                    parser        règles                        ventes.csv
```

Trois décisions font l'essentiel du travail. Elles ont toutes été imposées par
les documents réels, pas choisies à l'avance.

**Découper au caractère, pas au mot.** Les PDF sont générés par le logiciel
puis imprimés via « Print To PDF » : le texte est réellement présent, aucun
OCR n'est nécessaire. Sur chaque page, la ligne d'en-tête du tableau donne les
bornes horizontales des colonnes, et chaque *caractère* est ensuite affecté à
une colonne selon sa position. Travailler au mot ne marche pas : la colonne
« Réf » est tronquée à l'impression et son dernier fragment se colle à la
désignation (`SalCantine Salade batavia`). La frontière de colonne sépare
proprement les deux, là où un découpage sur les espaces produit une bouillie.

Cette méthode a un effet de bord qui s'est révélé décisif : quand le gabarit
a changé en 2026 et que la colonne « Réf » a disparu, rien n'a été à
reprendre. Les bornes sont recalculées pour chaque page, donc une colonne en
moins est simplement une ancre en moins. Le jeu de démonstration contient les
deux gabarits pour que ce soit vérifiable.

**Classer par règles, pas par rapprochement de catalogue.** Le classement
d'une ligne en légume et en famille passe par une liste de motifs ordonnée
([`regles_legumes.csv`](marastat/regles_legumes.csv)) : le premier motif qui
correspond gagne, donc les motifs spécifiques passent avant les généraux
(`chou de bruxelles` avant `chou`). Le rapprochement avec le catalogue
produits a été essayé puis abandonné : la référence imprimée est tronquée et
son préfixe est ambigu une fois sur deux (`Pro Oignon` désigne aussi bien le
jaune que le rouge ou le rose). Ajouter un produit, c'est insérer une ligne
dans un CSV. On ne touche pas au code.

**Ne pas deviner.** Environ 3 % des lignes sont imprimées sans aucun libellé :
le légume vendu ne figure nulle part sur la facture. Elles ne sont pas
inférées à partir du prix. Elles restent visibles en « Non identifié », leur
montant reste dans le chiffre d'affaires, et elles sont regroupées par
(référence, unité, prix, année) dans `arbitrage.csv` pour qui veut trancher à
la main. Une vingtaine de cas pour trois ans. Un catalogue produits facultatif
peut proposer des pistes, jamais décider.

## Ce que l'outil garantit

Ces quatre propriétés sont testées, pas seulement affirmées. Le fichier
[`tests/test_bout_en_bout.py`](tests/test_bout_en_bout.py) les vérifie sur la
chaîne complète, des PDF au rapport.

**Une facture n'est comptée que si elle se réconcilie.** La somme de ses
lignes doit retomber au centime sur le « Total HT » imprimé en bas du
document, remises comprises. Sinon elle est écartée et signalée par son nom de
fichier. Mieux vaut une facture manquante et visible qu'un chiffre faux noyé
dans un total. En production, sur 198 factures de 2024 à 2026, les 198
réconcilient.

**Un doublon ne double pas le chiffre d'affaires.** Deux fichiers de même
numéro sont comparés sur le contenu complet de la facture. Contenu identique :
une seule est retenue, l'autre est signalée. Contenus contradictoires : les
deux sont écartées, parce qu'aucune règle automatique ne permet de savoir
laquelle fait foi.

**La publication est atomique.** La base, le rapport et l'export sont écrits
dans des fichiers temporaires, puis mis en place ensemble une fois les trois
produits. Une interruption laisse la campagne précédente entière et cohérente.
Il n'existe pas d'état où un rapport à jour côtoie une base périmée.

**Un libellé de facture n'est jamais exécutable.** Les désignations viennent
de PDF tiers. Toute cellule de l'export qui commence par `=`, `+`, `-` ou `@`
est préfixée d'une apostrophe : ouvrir `ventes.csv` dans un tableur ne peut
pas déclencher de formule. Le jeu de démonstration contient une facture piégée
pour que la protection soit vérifiée à chaque exécution.

## Limites, à connaître avant d'utiliser les chiffres

**Aucune marge.** Les factures ne portent aucun prix d'achat ni coût de
production. L'outil mesure ce qui a été vendu et facturé, pas ce qui a été
gagné.

**Kilos et pièces ne s'additionnent pas.** Les deux volumes restent séparés
partout. Quand l'unité n'est pas imprimée, elle est déduite de celle observée
sur les autres ventes du même légume, et cette déduction est tracée dans la
colonne `unite_source` de la base : une valeur déduite ne peut jamais être
confondue avec une valeur lue.

**Le mois est celui de la facture**, généralement émise en fin de mois de
livraison ou début du mois suivant. La saisonnalité est donc décalée de
quelques semaines par rapport aux récoltes.

**L'année vient de la date d'émission**, jamais du numéro de facture ni du nom
du dossier : il existe des factures numérotées `F-2026-xxxx` datées de
décembre 2025.

**Une année en cours n'est pas comparable à une année pleine.** Le rapport
compare à période équivalente (mêmes mois de part et d'autre) et signale
l'année incomplète en tête.

**Le gabarit est celui d'un logiciel de facturation précis.** Marastat déduit
les colonnes de la ligne d'en-tête et sait absorber la disparition d'une
colonne, mais il attend un tableau à en-tête textuelle, pas n'importe quelle
facture. Les en-têtes reconnues sont listées dans
[`marastat/parser.py`](marastat/parser.py).

## Le dépôt ne contient aucune donnée réelle

Les factures d'une exploitation sont des documents commerciaux nominatifs :
ni les PDF, ni la base, ni les exports, ni les noms de clients ne figurent
ici, et le [`.gitignore`](.gitignore) est écrit pour que ça le reste.

Tout ce qui est visible dans les exemples, les tests et le rapport de
démonstration est fictif et produit par
[`outils/generer_demo.py`](outils/generer_demo.py). Ce générateur est
déterministe : deux exécutions donnent le même jeu, ce qui en fait à la fois
une démonstration et un jeu d'essai reproductible en intégration continue.

## Organisation du code

| Fichier | Rôle |
|---|---|
| `marastat/parser.py` | Lecture des PDF, une facture en objets. |
| `marastat/catalogue.py` | Clients, produits, règles de classement. |
| `marastat/etl.py` | Contrôles, arbitrage, écriture de la base SQLite. |
| `marastat/rapport.py` | Agrégats et injection dans le gabarit. |
| `marastat/gabarit.html` | Le rapport : mise en page, graphiques SVG, tableaux. |
| `marastat/cli.py` | Ligne de commande, export, publication atomique. |
| `marastat/regles_legumes.csv` | Le seul fichier à éditer pour ajouter un produit. |
| `outils/generer_demo.py` | Fabrique le jeu de factures fictives. |
| `outils/verifier.py` | Aide au développement : comparaison avec `pdftotext`. |

## Utilisation

```bash
python -m marastat                     # depuis le dossier qui contient Factures/
python -m marastat --factures ... --sortie ...
```

Options : `--factures`, `--sortie`, `--sans-ouverture`, `--sans-notification`.
Les options `--clients` et `--produits` acceptent d'anciens exports du
logiciel de facturation, mais aucun de ces CSV n'est nécessaire : les noms de
clients sont lus directement dans le bloc destinataire de chaque facture.

Pour un utilisateur non technique sous Windows, le projet se construit en un
exécutable autonome avec PyInstaller : le dossier ne contient plus qu'un
`.exe` et un dossier `Factures`, et un double-clic produit puis ouvre le
rapport.

## Développement

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check marastat tests outils
```

L'intégration continue exécute le lint, la suite de tests et la chaîne
complète sur le jeu de démonstration, sous Linux et Windows, en Python 3.10 et
3.12. Le rapport produit par la CI est publié en artefact de build.

## Licence

MIT. Voir [`LICENSE`](LICENSE).
