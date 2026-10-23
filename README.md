# Veille emploi Nantes

Application personnelle qui rassemble chaque matin les offres d'emploi de plusieurs
sources, autour de Nantes (et en télétravail complet en France), et les affiche
sur un site web protégé par un mot de passe.

> **Fonctionnalités :** sources France Travail, Adzuna, Jooble et alertes e-mail,
> fusion des doublons, score par poste cible, compétences demandées, carte des
> offres, suivi des candidatures, récap quotidien par e-mail, site web. (L'analyse IA avec le CV, envisagée au départ,
> n'a pas été retenue.)

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
qui indique quelle source n'a pas pu être consultée. Pour Adzuna et Jooble, une recherche
en échec (panne passagère, code 503…) est réessayée une fois à la fin, une minute plus tard.
Les offres déjà connues ne sont jamais retirées après une collecte partielle.

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

## Compétences demandées

Chaque fiche d'offre contient un encadré **« Compétences demandées »** (et la liste montre
les principales, après 🧩) :
- **Selon France Travail** : les compétences exigées et souhaitées, l'expérience, la formation,
  les langues, le permis et les savoir-être, tels que France Travail les fournit ;
- **Repérées dans le texte de l'annonce** (toutes sources) : méthodes, outils, savoir-faire,
  certifications et langues trouvés dans l'annonce, plus l'expérience (« 3 ans d'expérience »,
  « débutant accepté ») et le niveau d'études (« Bac+5 »).

Les mots repérés sont dans la section `competences:` de `config/criteres.yaml` : ajoute un mot
dans un groupe (ou crée un groupe) pour qu'il soit repéré dès la collecte suivante. Ce n'est
pas un résumé rédigé : une compétence absente de la liste n'est pas repérée dans le texte.

---

## Carte et localisation

- L'onglet **Carte** place les offres affichées (mêmes filtres que la liste) sur un plan de
  Nantes et des environs, avec le cercle de la zone de recherche. Les points proches sont
  regroupés ; leur couleur suit le score (vert ≥ 70, orange ≥ 45, gris en dessous). Un clic
  ouvre un résumé puis la fiche.
- La liste et les fiches indiquent la **distance depuis le centre de Nantes**.
- **Adresse des entreprises** : pour les offres de la zone, le programme cherche l'entreprise
  dans l'**annuaire public des entreprises** (API Recherche d'entreprises de l'État, gratuite) et
  garde son établissement le plus proche de la ville de l'offre. Sur la carte, ces offres sont
  des **points pleins** placés à l'adresse ; les autres sont des **points en pointillé** au centre
  de leur ville. L'adresse est à vérifier : pour une ESN ou un cabinet, c'est leur adresse et non
  celle du client, et une grande entreprise peut avoir plusieurs sites (la fiche le signale).
- Dans chaque fiche, le bouton **📍 Google Maps** recherche l'entreprise à cette adresse (ou dans
  sa ville), pour l'itinéraire.

Les coordonnées viennent de France Travail et d'Adzuna quand elles les fournissent ; sinon,
la ville est retrouvée une fois pour toutes grâce au service de géocodage de l'IGN (Base
Adresse Nationale, gratuit). Fond de carte : OpenStreetMap ; bibliothèque : Leaflet (copiée
dans `docs/vendor/`, licences incluses).

---

## Suivi des candidatures (Airtable)

Tes candidatures sont rangées dans **Airtable**. Le site en affiche une copie sous forme de
**kanban**, une colonne par étape, et tu peux t'en servir sans ouvrir Airtable.

- Dans la fiche d'une offre : **☆ Intéressante**, **Ignorer** (l'offre disparaît de la liste ;
  « Afficher les ignorées » dans « Plus de filtres » pour la retrouver) et **Ajouter au suivi**.
- **Ajouter au suivi** : la fiche est créée dans Airtable **une à deux minutes plus tard**
  (étape « À postuler », avec le poste, l'entreprise, le lieu, le score et le lien de l'offre).
- **Onglet Suivi (kanban)** : une colonne par étape (À postuler, Postulé, Entretien, Offre,
  Refus, et les étapes que tu ajoutes dans Airtable).
  - **Glisse une carte** d'une colonne à l'autre pour changer d'étape. Sur téléphone,
    ouvre la carte et choisis l'étape dans le menu.
  - **Clique sur une carte** pour voir ses dates, le contact, le lien de l'annonce et
    l'historique des **notes**, et pour **ajouter un commentaire**. Il est ajouté, daté du
    jour, au champ « Notes » de la fiche Airtable.
  - Tant qu'un changement n'est pas arrivé dans Airtable, il est marqué ⏳ (une à deux minutes).
  - Les relances à faire sont signalées par ⏰.
- Quand une fiche passe à **« Postulé »** sans date, la synchro remplit la date de
  candidature (aujourd'hui) et propose une **relance à J+7** (modifiable dans Airtable).
- Dans Airtable, crée une vue **Kanban** groupée par « Étape » pour retrouver le même tableau.

### Comment se fait la synchronisation ?

Par une tâche GitHub, **Synchro Airtable** (`.github/workflows/airtable.yml`,
programme `collecte/airtable.py`). La clé Airtable reste dans les secrets GitHub, jamais sur
tes appareils.
- **Site → Airtable** : après chaque ajout au suivi, changement d'étape ou commentaire
  (une à deux minutes).
- **Airtable → site** : vers 6 h 20 (avant la collecte et le récap), 12 h, 17 h et 21 h. Les
  changements faits dans Airtable apparaissent donc sur le site dans les heures qui suivent.
  Pour aller plus vite : onglet **Actions**, **Synchro Airtable**, **Run workflow**.
- Offre gratuite d'Airtable : **1 000 appels par mois**. Une synchro en utilise 1 ou 2, et
  aucun quand il n'y a rien à envoyer : on reste autour de 200 par mois.
- Si tu supprimes une fiche dans Airtable, elle n'est pas recréée, sauf si tu cliques de
  nouveau sur « Ajouter au suivi ».
- La table « Candidatures » et ses champs sont créés au premier lancement. Ne renomme pas
  les champs (le programme les retrouve par leur nom) ; tu peux en ajouter d'autres.

### Où sont stockées ces informations ?

- **Airtable** : toutes tes candidatures, avec tes **notes**.
- `docs/data/airtable.chiffre.json` : la copie lue par le site (notes comprises), le récap
  (relances) et le score (tes candidatures comptent comme des offres aimées). Elle est
  **chiffrée avec le mot de passe du site**.
- `docs/data/suivi.chiffre.json` : tes choix ☆ / Ignorer, tes ajouts au suivi, tes changements
  d'étape et tes commentaires faits dans le site, **chiffrés**.
  Le site l'enregistre grâce à un **jeton GitHub** saisi une fois par appareil (bouton
  « Synchro »). Sans jeton, un appareil peut consulter le suivi, mais ses choix restent sur
  lui et ses ajouts ne partent pas vers Airtable.

**Le jeton GitHub** ne permet que de modifier ce dépôt. S'il expire ou si un appareil est perdu :
supprime-le sur GitHub (Settings → Developer settings → Personal access tokens) et crée-en un nouveau.

---

## Récap quotidien par e-mail

Après la collecte automatique du matin, un e-mail part de la boîte Gmail dédiée vers
l'adresse du secret `RECAP_DESTINATAIRE`. Il contient :
- le nombre de **nouvelles offres** dans tes postes cibles (hors offres ignorées) ;
- les **5 meilleures** avec leur score et un lien ;
- les **candidatures à relancer** aujourd'hui (ou en retard), avec le contact noté.

**Pas d'e-mail s'il n'y a rien de nouveau.** Une collecte lancée à la main n'envoie pas de
récap, sauf si tu coches « Envoyer aussi le récap par e-mail » au moment de la lancer.
Réglages (activer / désactiver, nombre d'offres détaillées) : section `recap:` de
`config/criteres.yaml`. Si l'envoi échoue, un bandeau ✉️ l'indique sur le site.

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

S'y ajoutent :
- un **malus d'expérience** quand l'offre demande nettement plus d'années que toi (−10 au-delà de
  6 ans, −20 au-delà de 9 ans avec les réglages actuels) et un malus pour les **postes de direction** ;
- un bonus pour les **offres récentes** (moins de 7 jours), un malus pour les **anciennes** (plus de
  45 jours), et un petit bonus pour le **télétravail partiel**.

Dans chaque fiche, « Pourquoi … /100 ? » détaille le calcul. La note affichée est plafonnée à 100,
mais le classement utilise la note complète : deux offres à « 100 » restent départagées.

### Le score apprend de tes choix

Chaque **☆ Intéressante**, chaque candidature envoyée et chaque **Ignorer** sert d'exemple. À la
collecte suivante, les mots des intitulés et les entreprises qui reviennent surtout dans les offres
que tu aimes font gagner des points (et ceux des offres que tu ignores en font perdre), jusqu'à
±15. L'apprentissage démarre à partir de 5 choix ; plus tu tries, plus il est fiable. Une offre
déjà classée n'est jamais ajustée par son propre choix (pas d'auto-renforcement).

**Fiabilité** : sous le compteur d'offres, le site indique où se trouvent en moyenne tes offres
aimées dans le classement, et combien d'offres ignorées restent parmi les 20 premières. Le
compte rendu de chaque collecte donne la même mesure, avec et sans apprentissage.

Tout se règle dans la section `score:` de `config/criteres.yaml` : ajouter un intitulé à
un poste, changer des points, ajouter un mot-clé bonus (`mot: points`) ou une exclusion, ton
nombre d'années d'expérience (`experience: mes_annees`), les seuils de fraîcheur, ou couper
l'apprentissage (`apprentissage: actif: false`).
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
| `RECAP_DESTINATAIRE` | Adresse qui reçoit le récap quotidien | 6 |
| `AIRTABLE_TOKEN` | Jeton personnel Airtable (accès à la seule base des candidatures) | suivi |
| `AIRTABLE_BASE` | Identifiant de la base Airtable (commence par `app`) | suivi |

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
- les fichiers des offres, du suivi et de la copie d'Airtable, **mais chiffrés** : sans le
  mot de passe, ils sont illisibles.

**Ne sont jamais publiés** : tes clés d'API, ton mot de passe, ton CV (étape 7, rangé
dans les secrets) et tes notes de candidature (dans Airtable, qui est privé, et chiffrées
sur le site).

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
- **Liens de suivi** : certains sites cachent l'adresse de l'offre dans un lien de suivi codé.
  Le programme la décode à partir du lien lui-même, sans contacter le site.
- **Indeed et APEC** : leurs liens de suivi (`engage.indeed.com`, `neomarket.diffusion.apec.fr`)
  ne contiennent pas l'adresse de l'offre.
  Le programme demande au service de suivi où mène chaque lien et lit seulement sa réponse :
  la page de l'offre n'est jamais ouverte, et les liens de désabonnement ou de réglages ne sont
  jamais touchés. Le site compte chaque demande comme un clic. Pour ne pas redemander chaque jour
  les mêmes liens, seuls les e-mails de moins de 2 jours sont traités ainsi (réglable dans
  `alertes_email.redirections`).
- Dans le compte rendu, la forme des adresses de destination est affichée avec les noms des
  paramètres seulement, jamais leurs valeurs.
- **Welcome to the Jungle** : leurs alertes sont suspendues pendant une refonte (octobre 2026).
  Il suffira de les créer quand elles reviendront, rien n'est à changer dans le programme.

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
