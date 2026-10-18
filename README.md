# Veille emploi Nantes

Application personnelle qui rassemble chaque matin les offres d'emploi de plusieurs
sources, autour de Nantes (et en télétravail complet en France), et les affiche
sur un site web protégé par un mot de passe.

> **État actuel : étape 5 sur 7.** Sources France Travail, Adzuna, Jooble et
> alertes e-mail, fusion des doublons, score par poste cible, suivi des
> candidatures, site web. Les étapes à venir ajouteront le récap quotidien et
> l'analyse IA.

---

## Comment ça marche

1. **Chaque matin vers 7 h** (heure de Paris), GitHub lance automatiquement le
   programme de collecte (dans l'onglet **Actions** du dépôt).
2. Le programme interroge les sources d'offres une par une et met toutes les offres
   au même format. Une même offre trouvée sur plusieurs sites (même intitulé, même
   entreprise) n'apparaît qu'une fois, avec un lien vers chaque site.
3. Les offres sont enregistrées dans `docs/data/offres.chiffre.json`, **chiffré** avec
   ton mot de passe. Les offres qui ont disparu sont marquées « expirées » mais restent
   enregistrées.
4. Le site est republié. Tu l'ouvres, tu saisis ton mot de passe une fois par appareil,
   et tu vois les offres.

Si une source est en panne, les autres continuent. Le site affiche alors un bandeau
qui indique quelle source n'a pas pu être consultée.

### Lancer une collecte à la main

Onglet **Actions**, puis **Collecte des offres** dans la liste à gauche, puis bouton
**Run workflow** et **Run workflow** (vert). Compte environ 2 minutes. Le compte rendu
s'affiche en cliquant sur le lancement, puis sur **collecte**.

---

## Modifier les mots-clés et les réglages

Tous les réglages sont dans **`config/criteres.yaml`**.

Pour le modifier sans rien installer :
1. ouvre le fichier sur GitHub ;
2. clique sur l'icône **crayon** (« Edit this file ») ;
3. fais ta modification ;
4. clique sur **Commit changes…** puis de nouveau sur **Commit changes**.

C'est pris en compte à la collecte suivante. Tu peux aussi en lancer une à la main.

Quelques règles d'écriture :
- une ligne qui commence par `#` est un commentaire, ignoré par le programme ;
- l'alignement (les espaces en début de ligne) compte : garde le même que les lignes voisines ;
- dans une liste, chaque élément est sur sa ligne et commence par `- `.

Exemple : pour ajouter « scrum master » aux recherches autour de Nantes, ajoute une ligne
`    - scrum master` dans la liste `mots_cles` (avec 4 espaces avant le tiret, comme les autres).

---

## Suivi des candidatures

- Dans la fiche d'une offre : **☆ Intéressante** (étoile), **Ignorer** (l'offre disparaît de la
  liste ; « Afficher les ignorées » dans « Plus de filtres » pour la retrouver) et **Ajouter au suivi**.
- L'onglet **Suivi** présente tes candidatures en colonnes : À postuler → Postulé → Entretien →
  Offre / Refus. Les flèches ← → font changer d'étape ; un clic sur une carte ouvre sa fiche
  (date, contact, notes, relance). « + Nouvelle candidature » sert pour une offre trouvée ailleurs.
- En passant à « Postulé », la date de candidature est remplie et une **relance est proposée
  à J+7** (modifiable). Le bouton « Relancé aujourd'hui » note la relance et en propose une
  nouvelle 7 jours plus tard. Les candidatures à relancer sont signalées par ⏰.

### Où sont stockées ces informations ?

Dans `docs/data/suivi.chiffre.json`, **chiffré avec le mot de passe du site** : le fichier est
public mais illisible sans le mot de passe. Le site l'enregistre directement dans le dépôt
grâce à un **jeton GitHub** saisi une fois par appareil (bouton « Synchro »). Ainsi :
- téléphone et ordinateur partagent le même suivi (mis à jour à l'ouverture de la page) ;
- le récap du matin pourra te rappeler les relances du jour ;
- sans jeton, un appareil peut consulter le suivi mais ses modifications restent sur lui.

Une copie reste aussi dans chaque navigateur, et le bouton **Exporter** télécharge une
sauvegarde (à réimporter avec **Importer** si besoin).

**Le jeton** ne permet que de modifier ce dépôt. S'il expire ou si un appareil est perdu :
supprime-le sur GitHub (Settings → Developer settings → Personal access tokens) et crée-en un nouveau.

---

## Comprendre et régler le score

Chaque offre reçoit une **catégorie** (l'un de tes 5 postes cibles, ou « Hors cible ») et
un **score sur 100**, calculé ainsi :

| Élément | Points (réglages actuels) |
|---|---|
| Poste cible trouvé dans le titre | Product Owner 50, Chef de projet 42, Business Analyst / AMOA 38, Product Manager 34, UX Researcher / Ergonome 28 |
| …trouvé seulement dans la description | 40 % des points ci-dessus |
| Mots-clés bonus (agile, backlog, UX, recueil des besoins…) | de 2 à 6 points chacun, 25 au maximum |
| Lieu | zone de Nantes 15, télétravail complet ailleurs 10 |
| Contrat | CDI 15, CDD 10, non précisé 6, freelance ou intérim 3 |

Les offres dont le **titre** contient une expression d'exclusion (stage, alternance,
développeur, commercial, santé au travail…) sont classées « Hors cible » et leur score
est divisé par 5. Sur le site, elles sont masquées par défaut : choisis « Toutes les
offres » ou « Hors cible » dans la liste des postes pour les voir. Les missions en ESN
ou en cabinet sont **signalées** par un badge, mais pas exclues.

Dans chaque fiche, « Pourquoi … /100 ? » détaille le calcul.

Tout se règle dans la section `score:` de `config/criteres.yaml` : ajouter un intitulé à
un poste, changer des points, ajouter un mot-clé bonus (`mot: points`) ou une exclusion.
Le nouveau calcul s'applique à toutes les offres, même anciennes, dès la collecte suivante.

---

## Ajouter ou changer une clé d'API (les « secrets »)

Les clés ne sont **jamais** écrites dans le code. Elles sont rangées dans le coffre-fort
de GitHub :

1. dans le dépôt, onglet **Settings** (Paramètres) ;
2. menu de gauche : **Secrets and variables**, puis **Actions** ;
3. bouton vert **New repository secret** ;
4. **Name** : le nom exact du secret (voir le tableau ci-dessous). **Secret** : la valeur ;
5. **Add secret**.

Pour changer une valeur : clique sur le crayon à côté du secret existant.

| Nom du secret | À quoi il sert | Depuis l'étape |
|---|---|---|
| `FT_CLIENT_ID` | Identifiant de l'application France Travail | 1 |
| `FT_CLIENT_SECRET` | Clé secrète de l'application France Travail | 1 |
| `MOT_DE_PASSE_SITE` | Mot de passe du site (chiffre les données) | 1 |
| `ADZUNA_APP_ID` | Identifiant Adzuna (Application ID) | 2 |
| `ADZUNA_APP_KEY` | Clé Adzuna (Application Key) | 2 |
| `JOOBLE_API_KEY` | Clé Jooble | 2 |
| `GMAIL_ADRESSE` | Adresse de la boîte Gmail dédiée aux alertes | 4 |
| `GMAIL_MOT_DE_PASSE_APPLI` | Mot de passe d'application de cette boîte (16 lettres) | 4 |

Si un secret manque, la source correspondante est simplement indiquée « pas encore
configurée » sur le site, et les autres continuent de fonctionner.

⚠️ Si tu changes `MOT_DE_PASSE_SITE`, les données déjà enregistrées ne pourront plus
être lues. Il faudra supprimer `docs/data/offres.chiffre.json` pour repartir de zéro.

---

## Ce qui est public et ce qui ne l'est pas

Le dépôt est public, ce qui est nécessaire pour GitHub Pages gratuit. **Tout le monde
peut voir** :
- le code du programme et du site ;
- le fichier de réglages `config/criteres.yaml` (donc tes postes cibles et tes mots-clés) ;
- `docs/data/statut.json` : la date de la dernière collecte, et pour chaque source si elle a
  fonctionné et combien d'offres elle a trouvées ;
- le fichier des offres, **mais chiffré** : sans le mot de passe, il est illisible.

**Ne sont jamais publiés** : tes clés d'API, ton mot de passe, ton CV (étape 7, rangé
dans les secrets) et tes notes de candidature.

---

## Sources

| Source | Méthode | Statut |
|---|---|---|
| France Travail | API officielle « Offres d'emploi v2 » | ✅ étape 1 |
| Adzuna | API (offre gratuite : 250 appels par jour) | ✅ étape 2 |
| Jooble | API | ✅ étape 2 |
| APEC, LinkedIn, Welcome to the Jungle, HelloWork, Indeed | Alertes e-mail lues dans une boîte Gmail dédiée | ✅ étape 4 |

Les offres France Travail affichent leur source, leur date de mise à jour et un lien
vers l'offre d'origine, comme le demandent les conditions de réutilisation de France Travail.

**Quand une offre est-elle « expirée » ?** France Travail permet de vérifier offre par
offre : dès qu'une offre n'y existe plus, elle est marquée expirée. Adzuna et Jooble ne
le permettent pas : une offre est considérée comme retirée si elle n'a plus été vue
pendant 7 jours. Une offre reste affichée tant qu'au moins un de ses liens est actif.

### Alertes e-mail

Le programme lit (sans rien modifier ni marquer comme lu) les e-mails reçus ces 7 derniers
jours dans la boîte Gmail dédiée. Il y repère les liens d'offres de chaque site, et en
déduit l'intitulé, l'entreprise et le lieu. Aucun site n'est consulté directement.

- **Créer une alerte** : sur le site (APEC, Welcome to the Jungle…), crée une alerte avec
  tes critères et l'adresse de la boîte dédiée.
- **Alertes envoyées à ta boîte perso** (LinkedIn, par exemple) : crée dans ta boîte perso
  un transfert automatique vers la boîte dédiée. Les e-mails transférés sont lus de la même façon.
- **Un site n'est pas reconnu ?** Le compte rendu de la collecte indique, pour chaque e-mail,
  combien d'offres ont été trouvées. Si un e-mail n'a rien donné, il affiche la forme de ses
  liens (sans données personnelles). Il suffit alors d'ajouter ou d'ajuster un « motif » dans
  la section `alertes_email:` de `config/criteres.yaml`.

Une offre vue seulement dans les alertes est considérée comme retirée au bout de 21 jours
sans nouvelle mention.

### Codes ROME

Les codes métiers ROME de `config/criteres.yaml` ont été relevés dans les offres réelles
de France Travail (et non devinés). Pour revoir la liste, vide-la (`codes_rome: []`) :
la collecte suivante affichera dans son compte rendu les codes trouvés par mots-clés.

---

## Organisation du dépôt

```
.github/workflows/   les automatismes GitHub (collecte, publication, tests)
collecte/            le programme Python de collecte
config/              tes réglages (criteres.yaml)
docs/                le site web (index.html, style.css, app.js)
docs/data/           les données publiées (chiffrées) et le statut des sources
tests/               les vérifications automatiques du programme
```
