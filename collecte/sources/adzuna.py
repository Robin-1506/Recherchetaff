"""Source Adzuna (developer.adzuna.com), recherche en France.

Secrets : ADZUNA_APP_ID et ADZUNA_APP_KEY.
Offre gratuite : 25 appels par minute et 250 par jour, d'où la pause entre
deux appels et le nombre de pages limité.
"""

import logging
import os

import requests

from ..modele import detecter_contrat, detecter_teletravail, nettoyer_html, tronquer
from .commun import ErreurSource, SourceNonConfiguree, appeler, requetes_par_mots_cles

NOM = "Adzuna"
CLE = "adzuna"
URL = "https://api.adzuna.com/v1/api/jobs/fr/search/{page}"
PAR_PAGE = 50
PAGES_MAX = 2
PAUSE = 2.5  # 25 appels par minute au maximum
DELAI_EXPIRATION_JOURS = 7  # pas de vérification une à une : délai de grâce

log = logging.getLogger(__name__)

_CONTRATS = {"permanent": "CDI", "contract": "CDD"}


class ClientAdzuna:
    def __init__(self, app_id: str, app_key: str, session: requests.Session | None = None, pause: float = PAUSE):
        self.app_id = app_id
        self.app_key = app_key
        self.session = session or requests.Session()
        self.pause = pause

    def rechercher(self, params: dict) -> list[dict]:
        resultats = []
        for page in range(1, PAGES_MAX + 1):
            r = appeler(self.session, "GET", URL.format(page=page), NOM, pause=self.pause, params={
                "app_id": self.app_id, "app_key": self.app_key, "results_per_page": PAR_PAGE,
                "content-type": "application/json", **params,
            })
            if r.status_code in (401, 403):
                raise ErreurSource(f"Clé Adzuna refusée (code {r.status_code}). Vérifie ADZUNA_APP_ID / ADZUNA_APP_KEY.")
            if r.status_code != 200:
                raise ErreurSource(f"Recherche Adzuna refusée (code {r.status_code}) : {r.text[:200]}")
            corps = r.json()
            page_resultats = corps.get("results", [])
            resultats.extend(page_resultats)
            if len(page_resultats) < PAR_PAGE or len(resultats) >= corps.get("count", 0):
                break
        return resultats


def _salaire(brute: dict) -> str | None:
    if brute.get("salary_is_predicted") in (1, "1", True):
        return None  # estimation d'Adzuna, pas un salaire annoncé
    mini, maxi = brute.get("salary_min"), brute.get("salary_max")
    fmt = lambda v: f"{round(v):,}".replace(",", " ") + " €"
    if mini and maxi and round(mini) != round(maxi):
        return f"{fmt(mini)} – {fmt(maxi)} par an"
    if mini or maxi:
        return f"{fmt(mini or maxi)} par an"
    return None


def convertir(brute: dict, hors_zone: bool = False) -> dict:
    titre = nettoyer_html(brute.get("title")) or "(sans titre)"
    description = nettoyer_html(brute.get("description"))
    contrat = detecter_contrat(titre) or _CONTRATS.get(brute.get("contract_type") or "", "Autre")
    offre = {
        "id": f"adz-{brute['id']}",
        "titre": titre,
        "entreprise": (brute.get("company") or {}).get("display_name"),
        "lieu": (brute.get("location") or {}).get("display_name"),
        "teletravail": detecter_teletravail(titre, description),
        "hors_zone": hors_zone,
        "contrat": contrat,
        "contrat_libelle": " – ".join(x for x in (brute.get("contract_type"), brute.get("contract_time")) if x),
        "salaire": _salaire(brute),
        "date_publication": brute.get("created"),
        "date_maj": brute.get("created"),
        "description": tronquer(description),
        "sources": [{"nom": NOM, "id": str(brute["id"]), "url": brute.get("redirect_url"),
                     "date_maj": brute.get("created"), "active": True}],
    }
    if brute.get("latitude") is not None and brute.get("longitude") is not None:
        offre["coordonnees"] = [round(float(brute["latitude"]), 5), round(float(brute["longitude"]), 5)]
    return offre


def collecter(config: dict, client: ClientAdzuna | None = None) -> dict:
    if client is None:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if not app_id or not app_key:
            raise SourceNonConfiguree("non configurée (secrets ADZUNA_APP_ID / ADZUNA_APP_KEY absents)")
        client = ClientAdzuna(app_id, app_key)

    rech = config["recherche"]
    jours = (rech.get("teletravail_france") or {}).get("publiees_depuis_jours", 31)
    offres: dict[str, dict] = {}
    avertissements: list[str] = []
    reussites = 0
    requetes = requetes_par_mots_cles(config)
    for mot, national in requetes:
        params = {"what_phrase": mot, "max_days_old": jours} if national else {
            "what_phrase": mot, "where": rech.get("ville", "Nantes"), "distance": rech["rayon_km"]}
        libelle = f"France entière : {mot}" if national else mot
        try:
            brutes = client.rechercher(params)
        except ErreurSource as e:
            if "refusée (code 401" in str(e) or "refusée (code 403" in str(e):
                raise
            avertissements.append(f"Recherche « {libelle} » en échec : {e}")
            if reussites == 0 and len(avertissements) >= 3:
                break  # la source semble en panne : inutile d'insister
            continue
        reussites += 1
        for brute in brutes:
            offre = convertir(brute, hors_zone=national)
            if national and offre["teletravail"] != "complet":
                continue
            if offre["id"] in offres and not offres[offre["id"]]["hors_zone"]:
                continue
            offres[offre["id"]] = offre
        log.info("Adzuna « %s » : %d offres reçues", libelle, len(brutes))

    if requetes and reussites == 0:
        raise ErreurSource("Toutes les recherches Adzuna ont échoué. " + avertissements[0])
    return {"offres": list(offres.values()), "avertissements": avertissements, "complet": not avertissements,
            "delai_jours": DELAI_EXPIRATION_JOURS}
