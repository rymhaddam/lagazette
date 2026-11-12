# Qualité des données — La gazette (TD 4)

Entre le brut et la gazette, il y a le tri. Ce document décrit ce qui pollue nos flux,
les règles qui l'écartent, les contrôles qui vérifient le résultat chaque matin, la
politique de rétention, et le journal des ajustements.

- Règles et seuils : [`config/regles.toml`](../config/regles.toml) (aucune règle n'est codée en dur).
- Code : `src/gazette/clean/` (normalisation, règles, doublons) et `src/gazette/quality/`
  (rapport, contrôles, alerte).
- Rapport du jour : `data/quality/<AAAA-MM-JJ>/rapport.md` (et `.json`, et la table
  `rapports_qualite`).

```
data/raw/<jour>/  ──lecture seule──►  clean  ──►  articles_propres   (PostgreSQL, base gazette)
                                        │    └──►  articles_rejetes   (règle + raison, 30 j)
                                        └────────►  data/quality/<jour>/rapport.{json,md}
                                 quality_checks ──►  metriques_qualite + alerte Discord
```

Le brut n'est jamais modifié : on peut rejouer le nettoyage d'un jour après avoir changé une
règle (`python -m gazette clean --date AAAA-MM-JJ`). Le rejeu remplace le propre et les rejets
de ce jour, il ne crée pas de doublon.

---

## 1. Reprise : la collecte a-t-elle tourné ?

Collecte du **2026-11-12** (11 h 48, relancée après la perte du `.env`) : 7 sources en
succès sur 8, **465 items**.

| Source | Type | Items | Remarque |
|---|---|---:|---|
| Ouest-France | PQR | 0 | **échec** : `robots.txt` interdit le flux à notre robot. On respecte : la source reste en échec et visible dans le manifeste. |
| Sud Ouest | PQR | 20 | 1 extrait vide |
| La Dépêche du Midi | PQR | 100 | des articles anciens restés dans le flux (jusqu'à oct. 2025) |
| Le Dauphiné Libéré | PQR | 20 | préfixes de rubrique dans les titres (« Météo. », « Société. ») |
| Le Monde | PQN | 16 | — |
| Le Figaro | PQN | 20 | 11 « bons plans » sur 20 |
| Libération | PQN | 50 | — |
| L'Obs | PQN | 239 | flux d'archives : 158 items de plus de 2 jours, quiz, critiques télé, vidéos |

## 2. Nos dix pollueurs

Relevés sur le brut réel du 2026-11-12 (exemples tirés des flux) :

| # | Pollueur | Où | Exemple réel | Traitement |
|---|---|---|---|---|
| 1 | **Bons plans / affiliation** (publi déguisé) | Le Figaro, rubrique `bons-plans` (11/20) | « Amazon vide ses stocks Nintendo Switch 2 : des consoles, manettes et jeux à prix cassés » | `R01_publi` |
| 2 | **Publicité locale** | La Dépêche | « Nailloux Outlet Village : Black Friday dès le 16 novembre avec des remises jusqu'à 70 % » | `R01_publi` (motif) |
| 3 | **Quiz et jeux** | L'Obs, rubrique `les-jeux-de-l-obs` (11) ; La Dépêche | « Quiz. COP31 : 12 questions sur le climat… », « La Dépêche du Midi lance son espace jeux » | `R04_jeux_concours` |
| 4 | **Programmes / critiques TV** | L'Obs, rubrique `teleobs` (17) | « Les Pleins Pouvoirs : Clint Eastwood et Gene Hackman dans un duel sans merci » | `R03_programme_tv` |
| 5 | **Articles anciens** recyclés dans le flux | L'Obs (158 items > 2 j, le plus vieux de mai 2026), La Dépêche (oct. 2025) | « Immobilier en Corse : les prix s'envolent » (8 octobre) | `R11_trop_ancien` |
| 6 | **Dépêche d'agence reprise** à l'identique | La Dépêche / Sud Ouest | « Guerre en Ukraine : un militaire russe tué dans l'explosion d'une voiture à Saint-Pétersbourg » (« DIRECT. » en plus chez La Dépêche) | `R10_depeche_reprise` |
| 7 | **Doublon approximatif** (« à deux mots près ») | Le Monde / Sud Ouest | « A Toulouse, la liquidation judiciaire de l'usine de pâte à papier Fibre Excellence définitivement entérinée… » vs « À Toulouse, la liquidation judiciaire de Fibre Excellence définitivement entérinée… » | `R09_doublon_approx` (Jaccard 0,80) |
| 8 | **Liens cassés** (hôte doublé) | L'Obs, 5 vidéos | `https://www.nouvelobs.comhttps://www.nouvelobs.com/videos/…` | réparé par `repair_link`, sinon `R13_lien_invalide` |
| 9 | **Préfixes éditoriaux** dans les titres | La Dépêche, Le Dauphiné, Le Monde | « DIRECT. … », « REPLAY. … », « Météo. … », « EN DIRECT, … \| … » | retirés **pour comparer** (`strip_title_prefix`), le titre affiché reste intact |
| 10 | **Mise en forme** : espaces insécables partout, extraits trop longs, extrait vide | toutes ; L'Obs (extraits jusqu'à 902 caractères) ; Sud Ouest (1 vide) | « «\xa0Avoir un CDI ne suffit plus…\xa0» » | normalisation (espaces), extrait borné à 300 caractères |

Pas vus le 12/11 mais attendus et couverts par la fixture de test : HTML et entités dans
les extraits, encodage cassé (`Ã©`), titre vide, date absente ou future, horoscope.

## 3. Normalisation

Une fonction pure par transformation (`src/gazette/clean/normalize.py`), un test par
fonction (`tests/test_normalize.py`). Ordre appliqué au titre et à l'extrait :

1. `fix_encoding` : répare un texte UTF-8 relu en cp1252/latin-1 (`Ã‰tÃ©` → `Été`) ; s'il
   reste du mojibake, `R07_encodage_casse` écartera l'item.
2. `strip_html` : retire les balises et le contenu de `<script>`/`<style>`.
3. `unescape_entities` : `&eacute;`, `&nbsp;`, y compris doublement encodées (`&amp;eacute;`).
4. `normalize_whitespace` : espaces insécables, caractères invisibles, blancs multiples.
5. `truncate` : extrait borné à `extrait_max` = 300 caractères, coupé sur un mot.

Et aussi : `to_utc` (dates RFC 822 ou ISO → UTC ; sans fuseau = heure de Paris),
`repair_link`, `normalize_title` (minuscules, sans accents ni ponctuation, apostrophes
typographiques traitées comme séparateurs), `strip_title_prefix`, `url_sections`
(rubriques lues dans l'URL), `jaccard`.

## 4. Règles d'exclusion

Chaque règle a un identifiant stable, une description dans `regles.toml`, et laisse une
**raison** lisible sur chaque rejet (table `articles_rejetes`). Un item est écarté par la
**première** règle qui le vise, dans cet ordre : structure, puis contenu, puis doublons
(un item à la fois vieux et doublon compte en `R11`, pas en `R09`).

| Id | Ce qu'elle écarte | Pourquoi | Exemple |
|---|---|---|---|
| `R13_lien_invalide` | lien absent, non HTTP(S), ou hôte doublé irréparable | pas de lien = pas d'article à citer | `javascript:void(0)` |
| `R05_titre_vide` | titre vide ou < 15 caractères | rien à afficher, et impossible à regrouper | `"   "` |
| `R07_encodage_casse` | mojibake irréparable (`Ã©`, `â€™`, `�`) | illisible dans la gazette | « Pr�visions de trafic » |
| `R12_date_absente` | date absente ou illisible | on ne sait pas si c'est de l'actualité | item sans `pubDate` |
| `R06_date_future` | date > passage + 60 min | erreur de flux ; fausse le tri | « 21 Nov 2026 » collecté le 21 octobre |
| `R11_trop_ancien` | publié > 72 h avant le passage | une gazette du matin, pas une archive | L'Obs, « Immobilier en Corse », 8 octobre |
| `R01_publi` | rubriques `bons-plans`, `partenaires`… ; motifs « contenu sponsorisé », « bon plan », « code promo », « remises jusqu'à », « jusqu'à -55 % », « en partenariat avec » | contenu commercial, pas de l'information | Le Figaro `bons-plans` |
| `R02_horoscope` | rubriques `horoscope`, `astro` ; motifs horoscope / signe + amour, travail… | hors sujet | « Horoscope du mardi 21 octobre » |
| `R03_programme_tv` | rubriques `teleobs`, `programme-tv` ; motifs « programme télé », « ce soir à la télé » | grille de programmes, pas de l'actualité | L'Obs `teleobs` |
| `R04_jeux_concours` | rubriques `jeux`, `les-jeux-de-l-obs`, `quiz` ; motifs « Quiz », « jeu-concours », « mots croisés », « sudoku » | divertissement | « Quiz. COP31 : 12 questions… » |
| `R08_doublon_exact` | même lien canonique, ou même titre normalisé dans la même source | le flux republie une mise à jour | « Budget 2027 … finances » puis « … finances ! » |
| `R10_depeche_reprise` | même titre normalisé, ou même extrait (≥ 60 caractères), dans une **autre** source | dépêche AFP reprise par plusieurs titres : on la garde une fois | La Dépêche (« DIRECT. ») / Sud Ouest, militaire russe |
| `R09_doublon_approx` | Jaccard des mots du titre ≥ 0,80 avec un article gardé (titres d'au moins 4 mots) | « même titre à deux mots près » | Le Monde / Sud Ouest, Fibre Excellence |

**Doublons : on garde la première publication.** Les articles sont parcourus du plus ancien au
plus récent. La raison du rejet cite l'article gardé (`reprise de Sud Ouest (c9c3aca0dc)`).

**Ce que le nettoyage ne fait pas** : regrouper deux articles *différents* sur le même sujet
(Libération et La Dépêche sur Fibre Excellence, Jaccard 0,75 : deux articles gardés). C'est le
regroupement sémantique du TD 5.

Toutes les règles sont testées sur une fixture de brut pollué (`tests/fixtures/raw/`) :
chaque item y porte son verdict attendu (`_attendu`), et un test vérifie que **chaque règle**
de `regles.toml` est couverte.

## 5. Rapport de qualité

Écrit à chaque passage dans `data/quality/<jour>/rapport.md` et `rapport.json`, et dans la table
`rapports_qualite` (JSONB). Il contient :

- le nombre d'items entrés, gardés et rejetés ;
- les rejets **par règle** et **par source**, avec le taux de rejet de chaque source ;
- **les cinq rejets les plus fréquents** (couples règle × source, avec un exemple) ;
- le résultat des contrôles.

**Repérer une règle trop gourmande** : une règle qui monte d'un coup dans « par règle », ou
qui touche une source qu'elle ne devrait pas toucher, se voit dans le top 5 avec un exemple.
On vérifie alors l'exemple, puis on cherche les autres cas en base :

```sql
SELECT source_slug, titre, raison FROM articles_rejetes
WHERE jour_collecte = '2026-11-12' AND regle_id = 'R01_publi';
```

Premier rapport (2026-11-12) : 465 entrés, **281 gardés**, 184 rejetés (40 %). Par règle :
`R11_trop_ancien` 160, `R01_publi` 12, `R03_programme_tv` 9, puis `R04`, `R09` et `R10`
avec 1 chacune. L'Obs est rejeté à 70 %, Le Figaro à 55 %, les autres sources à moins de 5 %.

## 6. Contrôles qualité

Les contrôles ne testent pas du code : ils vérifient des **faits sur les données réelles**
(lignes de `articles_propres` du jour), après chaque passage. La tâche Airflow s'appelle
`quality_checks`, et la commande équivalente est `python -m gazette check`.

| Contrôle | Fait vérifié | Seuil (`[controles]`) |
|---|---|---|
| `C01_aucune_date_future` | aucune date de publication après le passage | tolérance 60 min |
| `C02_aucun_titre_vide` | aucun titre vide | 0 |
| `C03_aucun_doublon_lien` | aucun lien en double | 0 |
| `C04_extrait_borne` | extrait le plus long ≤ borne | 300 |
| `C05_encodage_sain` | aucun mojibake | 0 |
| `C06_taux_rejet_par_source` | le taux de rejet d'aucune source ne dépasse 80 % | 0,80 |
| `C07_volume_minimum` | au moins 30 articles gardés | 30 |
| `C08_sources_representees` | au moins 4 sources dans le propre | 4 |

**Pourquoi des assertions maison** plutôt que Great Expectations ou Soda :
- huit faits simples, chacun lisible en quelques lignes (`src/gazette/quality/checks.py`) ;
- pas de dépendance lourde à faire cohabiter avec les contraintes de version d'Airflow dans
  l'image ;
- les résultats sont écrits directement au format qu'on choisit, dans une table que le TD 8
  pourra collecter.

Un outil dédié deviendrait intéressant avec des dizaines de contrôles ou plusieurs tables.

**Où vont les chiffres** :
- la table `metriques_qualite` : une ligne par contrôle et par passage (valeur, seuil, ok,
  détail). C'est la source que le TD 8 viendra collecter ;
- les XCom de la tâche `quality_checks`, visibles dans l'interface Airflow ;
- les journaux de la tâche.

**Alerte** : quand au moins un fait est faux, un message part sur le salon **Discord** de
l'équipe (webhook `GAZETTE_ALERT_WEBHOOK`). Il liste les contrôles en échec, avec leur valeur
et leur seuil. Sans webhook, l'alerte est seulement journalisée (niveau ERROR). La tâche reste
« success » : l'alerte est le signal, et un seuil ne bloque pas la publication du jour.

**Déclencher l'alerte pour de vrai** : un seuil remplacé le temps d'un passage, sur les vraies
données du jour :

```bash
python -m gazette check --date 2026-11-12 --seuil taux_rejet_max_par_source=0.5
```

L'Obs (70 %) et Le Figaro (55 %) dépassent alors 50 %, le fait est faux et l'alerte part.

> Alerte déclenchée le : _à compléter (date, capture du message Discord)_.

## 7. Conformité et rétention

**Ce qu'on stocke** dans `articles_propres` se limite à ce qu'on a le droit de republier :
le titre, un extrait court (300 caractères au plus), le lien, la date et la source. On ne
stocke jamais le texte intégral, ni aucune donnée personnelle : pas d'auteur, pas de
commentaire, pas de profil (déjà vrai dans le brut, voir le README). Le User-Agent de collecte
identifie le projet et donne un e-mail de contact ; les flux interdits par `robots.txt`
(Ouest-France) ne sont pas collectés.

**Durées de rétention**, appliquées par la tâche `retention` du DAG ou par
`python -m gazette retention --apply` (sans `--apply`, la commande ne fait que simuler) :

| Donnée | Chaud | Froid (`data/archive/`) | Suppression |
|---|---|---|---|
| Brut `data/raw/<jour>/` | 30 jours | `data/archive/raw/<jour>.tar.gz` | à 90 jours |
| Rapports `data/quality/<jour>/` | 30 jours | `data/archive/quality/<jour>.tar.gz` | conservés (quelques Ko par jour) |
| État `data/state/runs/<jour>/` | 30 jours | — | à 30 jours |
| Table `articles_rejetes` | 30 jours | — | à 30 jours (sert à régler les règles, pas à publier) |
| Table `articles_propres` | conservée | — | à décider avec les éditions (TD suivants) |
| Éditions publiées (TD suivants) | 1 mois | stockage froid | — |

## 8. Journal des ajustements

Chaque changement de règle ou de seuil est noté ici : la date, ce qui a changé, pourquoi
(avec un exemple), et l'effet mesuré dans le rapport.

| Date | Changement | Pourquoi | Effet |
|---|---|---|---|
| 2026-11-12 | `R09` : `seuil_jaccard` passe de 0,75 à **0,80** | à 0,75, deux articles différents sur Fibre Excellence (Libération : « … entérinée par le tribunal de commerce de Toulouse » / La Dépêche : « l'usine de Saint-Gaudens placée en liquidation… ») étaient fusionnés | `R09` passe de 2 rejets à 1 : seule la dépêche reprise par Le Monde et Sud Ouest est écartée |
| 2026-11-12 | `normalize_title` : l'apostrophe typographique `’` devient un séparateur | « l’usine » donnait « lusine » mais « l'usine » donnait « l usine » : la même dépêche AFP (La Dépêche / Sud Ouest) n'était pas reconnue | la reprise « militaire russe tué… » est détectée (`R10`) |
| _jour 1_ | | | |
| _jour 2_ | | | |
| _jour 3_ | | | |

**Pour la prochaine séance** : lire le rapport trois matins de suite, ajuster au moins une
règle, et la noter dans les lignes ci-dessus.
