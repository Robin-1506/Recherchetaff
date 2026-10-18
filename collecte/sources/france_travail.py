"""Source France Travail : API Offres d'emploi v2 (francetravail.io).

Authentification OAuth2 « client credentials ». Les identifiants viennent des
secrets GitHub FT_CLIENT_ID et FT_CLIENT_SECRET.

Conditions de réutilisation : le site affiche « France Travail » comme source,
la date de mise à jour et un lien vers l'offre d'origine.
"""

import logging
import os
import time

import requests

from ..modele import detecter_teletravail, normaliser_texte, tronquer
from .commun import ErreurSource, SourceNonConfiguree

NOM = "France Travail"
CLE = "france_travail"

URL_JETON = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
URL_API = "https://api.francetravail.io/partenaire/offresdemploi/v2"
SCOPE = "api_offresdemploiv2 o2dsoffre"
URL_OFFRE = "https://candidat.francetravail.fr/offres/recherche/detail/{id}"

TAILLE_PAGE = 150  # maximum autorisé par l'API
FIN_MAX = 1149  # l'API ne renvoie pas au-delà de 1150 résultats par recherche
PAUSE = 0.15  # limite : 10 appels par seconde
MAX_VERIFICATIONS = 300  # offres disparues des recherches vérifiées une à une

log = logging.getLogger(__name__)

_CONTRATS = {
    "CDI": "CDI",
    "CDD": "CDD",
    "MIS": "Intérim",
    "SAI": "CDD",
    "LIB": "Freelance",
    "FRA": "Freelance",
    "REP": "Autre",
    "CCE": "Autre",
}


class ClientFranceTravail:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = session or requests.Session()
        self._jeton = None
        self._expiration = 0.0

    def _entetes(self) -> dict:
        if not self._jeton or time.time() > self._expiration - 60:
            r = self.session.post(
                URL_JETON,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": SCOPE,
                },
                timeout=30,
            )
            if r.status_code != 200:
                raise ErreurSource(
                    f"Connexion refusée par France Travail (code {r.status_code}). "
                    "Vérifie FT_CLIENT_ID / FT_CLIENT_SECRET et l'abonnement à l'API Offres d'emploi v2."
                )
            corps = r.json()
            self._jeton = corps["access_token"]
            self._expiration = time.time() + int(corps.get("expires_in", 1499))
        return {"Authorization": f"Bearer {self._jeton}", "Accept": "application/json"}

    def get(self, chemin: str, params: dict | None = None) -> requests.Response:
        for tentative in range(4):
            time.sleep(PAUSE)
            r = self.session.get(f"{URL_API}{chemin}", params=params, headers=self._entetes(), timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                attente = float(r.headers.get("Retry-After", 2 ** tentative))
                log.warning("France Travail %s : code %s, nouvel essai dans %ss", chemin, r.status_code, attente)
                time.sleep(attente)
                continue
            return r
        raise ErreurSource(f"France Travail ne répond pas correctement ({chemin}, code {r.status_code})")

    def rechercher(self, params: dict) -> list[dict]:
        """Renvoie toutes les offres d'une recherche (pagination incluse)."""
        resultats = []
        debut = 0
        while debut <= FIN_MAX:
            fin = min(debut + TAILLE_PAGE - 1, FIN_MAX)
            r = self.get("/offres/search", {**params, "range": f"{debut}-{fin}"})
            if r.status_code == 204:
                break
            if r.status_code not in (200, 206):
                raise ErreurSource(f"Recherche refusée (code {r.status_code}) : {r.text[:200]}")
            page = r.json().get("resultats", [])
            resultats.extend(page)
            total = _total(r.headers.get("Content-Range", ""))
            if r.status_code == 200 or not page or (total is not None and fin + 1 >= total):
                break
            debut = fin + 1
        return resultats

    def offre_existe(self, id_offre: str) -> bool:
        r = self.get(f"/offres/{id_offre}")
        if r.status_code in (204, 404):
            return False
        if r.status_code == 200:
            return True
        raise ErreurSource(f"Vérification de l'offre {id_offre} impossible (code {r.status_code})")


def _total(content_range: str) -> int | None:
    # Format : "offres 0-149/1234"
    try:
        return int(content_range.rsplit("/", 1)[1])
    except (IndexError, ValueError):
        return None


def convertir(brute: dict, hors_zone: bool = False) -> dict:
    """Transforme une offre France Travail au format commun."""
    id_ft = brute["id"]
    nature = brute.get("natureContrat") or ""
    contrat = _CONTRATS.get(brute.get("typeContrat", ""), "Autre")
    nature_n = normaliser_texte(nature)
    if "apprentissage" in nature_n or "professionnalisation" in nature_n:
        contrat = "Alternance"
    salaire = brute.get("salaire") or {}
    lieu = brute.get("lieuTravail") or {}
    description = brute.get("description") or ""
    date_maj = brute.get("dateActualisation") or brute.get("dateCreation")
    offre = {
        "id": f"ft-{id_ft}",
        "titre": brute.get("intitule") or "(sans titre)",
        "entreprise": (brute.get("entreprise") or {}).get("nom"),
        "lieu": lieu.get("libelle"),
        "teletravail": detecter_teletravail(brute.get("intitule"), description),
        "hors_zone": hors_zone,
        "contrat": contrat,
        "contrat_libelle": " – ".join(x for x in (brute.get("typeContratLibelle"), nature) if x),
        "salaire": salaire.get("libelle") or salaire.get("commentaire"),
        "date_publication": brute.get("dateCreation"),
        "date_maj": date_maj,
        "description": tronquer(description),
        "rome": {"code": brute.get("romeCode"), "libelle": brute.get("romeLibelle")},
        "sources": [
            {
                "nom": NOM,
                "id": id_ft,
                "url": URL_OFFRE.format(id=id_ft),
                "url_origine": (brute.get("origineOffre") or {}).get("urlOrigine"),
                "date_maj": date_maj,
                "active": True,
            }
        ],
    }
    if lieu.get("latitude") is not None and lieu.get("longitude") is not None:
        offre["coordonnees"] = [round(float(lieu["latitude"]), 5), round(float(lieu["longitude"]), 5)]
    return offre


def collecter(config: dict, client: ClientFranceTravail | None = None) -> dict:
    """Lance toutes les recherches France Travail.

    Renvoie {"offres": [...], "avertissements": [...], "complet": bool,
    "verifier": fonction qui dit quelles offres disparues existent encore}.
    « complet » est faux si une recherche a échoué : on ne marquera alors
    aucune offre comme expirée, pour ne pas se tromper.
    """
    if client is None:
        client_id = os.environ.get("FT_CLIENT_ID")
        client_secret = os.environ.get("FT_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise SourceNonConfiguree("non configurée (secrets FT_CLIENT_ID / FT_CLIENT_SECRET absents)")
        client = ClientFranceTravail(client_id, client_secret)

    rech = config["recherche"]
    zone = {"commune": rech["commune_insee"], "distance": rech["rayon_km"]}
    offres: dict[str, dict] = {}
    avertissements: list[str] = []
    reussites = 0

    requetes = [({**zone, "motsCles": mot}, False, mot) for mot in rech.get("mots_cles", [])]
    if rech.get("codes_rome"):
        codes = ",".join(rech["codes_rome"])
        requetes.append(({**zone, "codeROME": codes}, False, f"ROME {codes}"))
    tt = rech.get("teletravail_france") or {}
    if tt.get("actif"):
        for mot in tt.get("mots_cles", []):
            requetes.append(
                ({"motsCles": mot, "publieeDepuis": tt.get("publiees_depuis_jours", 31)}, True, f"France entière : {mot}")
            )

    for params, national, libelle in requetes:
        try:
            brutes = client.rechercher(params)
        except (ErreurSource, requests.RequestException) as e:
            avertissements.append(f"Recherche « {libelle} » en échec : {e}")
            log.warning("Recherche « %s » en échec : %s", libelle, e)
            if reussites == 0 and len(avertissements) >= 3:
                break  # la source semble en panne : inutile d'insister
            continue
        reussites += 1
        gardees = 0
        mention_teletravail = 0
        for brute in brutes:
            offre = convertir(brute, hors_zone=national)
            mention_teletravail += offre["teletravail"] is not None
            if national and offre["teletravail"] != "complet":
                continue
            if offre["id"] in offres and not offres[offre["id"]]["hors_zone"]:
                continue  # déjà trouvée dans la zone : on garde cette version
            offres[offre["id"]] = offre
            gardees += 1
        log.info("Recherche « %s » : %d offres reçues (%d parlent de télétravail), %d nouvelles gardées",
                 libelle, len(brutes), mention_teletravail, gardees)

    if requetes and reussites == 0:
        raise ErreurSource("Toutes les recherches France Travail ont échoué. " + avertissements[0])

    details = []
    if not rech.get("codes_rome"):
        details += ["", "### Codes ROME présents dans les offres trouvées", "",
                    "| Code | Métier | Offres | Exemples |", "|---|---|---|---|"]
        details += [f"| {e['code']} | {e['libelle']} | {e['nb']} | {' / '.join(e['exemples'])} |"
                    for e in rapport_rome(list(offres.values()))]

    return {
        "offres": list(offres.values()),
        "details": details,
        "avertissements": avertissements,
        "complet": not avertissements,
        "verifier": lambda ids: offres_encore_en_ligne(client, ids),
    }


def offres_encore_en_ligne(client: ClientFranceTravail, ids: list[str]) -> set[str]:
    """Parmi des offres absentes des recherches du jour, renvoie celles qui
    existent encore chez France Travail (elles ne sont pas expirées)."""
    encore = set()
    for i, id_ft in enumerate(ids):
        if i >= MAX_VERIFICATIONS:
            encore.update(ids[i:])  # pas vérifiées aujourd'hui : on ne conclut rien
            break
        try:
            if client.offre_existe(id_ft):
                encore.add(id_ft)
        except (ErreurSource, requests.RequestException):
            encore.add(id_ft)
    return encore


def rapport_rome(offres: list[dict]) -> list[dict]:
    """Compte les codes ROME officiels présents dans les offres trouvées par
    mots-clés : sert à choisir les codes à mettre dans config/criteres.yaml."""
    codes: dict[str, dict] = {}
    for offre in offres:
        rome = offre.get("rome") or {}
        if not rome.get("code") or offre.get("hors_zone"):
            continue
        entree = codes.setdefault(rome["code"], {"code": rome["code"], "libelle": rome.get("libelle"), "nb": 0, "exemples": []})
        entree["nb"] += 1
        if len(entree["exemples"]) < 3 and offre["titre"] not in entree["exemples"]:
            entree["exemples"].append(offre["titre"])
    return sorted(codes.values(), key=lambda e: -e["nb"])
