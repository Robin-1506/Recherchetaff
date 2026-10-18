"""Source Jooble (jooble.org/api/about), recherche en France.

Secret : JOOBLE_API_KEY. L'API s'appelle en POST sur https://<hôte>/api/<clé>
avec un corps JSON ; on essaie l'hôte français puis l'hôte international.
"""

import hashlib
import logging
import os

import requests

from ..modele import detecter_contrat, detecter_teletravail, nettoyer_html, tronquer
from .commun import ErreurSource, SourceNonConfiguree, appeler, requetes_par_mots_cles

NOM = "Jooble"
CLE = "jooble"
HOTES = ("fr.jooble.org", "jooble.org")
PAGES_MAX = 3
PAUSE = 0.5
DELAI_EXPIRATION_JOURS = 7

log = logging.getLogger(__name__)


class ClientJooble:
    def __init__(self, cle: str, session: requests.Session | None = None, pause: float = PAUSE):
        self.cle = cle
        self.session = session or requests.Session()
        self.pause = pause
        self.hote = None  # déterminé au premier appel réussi

    def _post(self, corps: dict) -> requests.Response:
        hotes = [self.hote] if self.hote else HOTES
        r = None
        for hote in hotes:
            r = appeler(self.session, "POST", f"https://{hote}/api/{self.cle}", NOM, pause=self.pause, json=corps)
            if r.status_code == 200:
                self.hote = hote
                return r
        if r.status_code in (401, 403, 404):
            raise ErreurSource(f"Clé Jooble refusée (code {r.status_code}). Vérifie JOOBLE_API_KEY.")
        raise ErreurSource(f"Recherche Jooble refusée (code {r.status_code}) : {r.text[:200]}")

    def rechercher(self, corps: dict) -> list[dict]:
        resultats = []
        for page in range(1, PAGES_MAX + 1):
            reponse = self._post({**corps, "page": str(page)}).json()
            jobs = reponse.get("jobs") or []
            resultats.extend(jobs)
            if not jobs or len(resultats) >= int(reponse.get("totalCount") or 0):
                break
        return resultats


def _identifiant(brute: dict) -> str:
    if brute.get("id") not in (None, ""):
        return str(brute["id"])
    return hashlib.sha1((brute.get("link") or brute.get("title") or "").encode()).hexdigest()[:16]


def convertir(brute: dict, hors_zone: bool = False) -> dict:
    titre = nettoyer_html(brute.get("title")) or "(sans titre)"
    description = nettoyer_html(brute.get("snippet"))
    type_contrat = nettoyer_html(brute.get("type"))
    id_jooble = _identifiant(brute)
    return {
        "id": f"jbl-{id_jooble}",
        "titre": titre,
        "entreprise": nettoyer_html(brute.get("company")) or None,
        "lieu": nettoyer_html(brute.get("location")) or None,
        "teletravail": detecter_teletravail(titre, description, brute.get("location")),
        "hors_zone": hors_zone,
        "contrat": detecter_contrat(titre, type_contrat) or "Autre",
        "contrat_libelle": type_contrat or None,
        "salaire": nettoyer_html(brute.get("salary")) or None,
        "date_publication": brute.get("updated"),
        "date_maj": brute.get("updated"),
        "description": tronquer(description),
        "sources": [{"nom": NOM, "id": id_jooble, "url": brute.get("link"), "date_maj": brute.get("updated"),
                     "active": True}],
    }


def collecter(config: dict, client: ClientJooble | None = None) -> dict:
    if client is None:
        cle = os.environ.get("JOOBLE_API_KEY")
        if not cle:
            raise SourceNonConfiguree("non configurée (secret JOOBLE_API_KEY absent)")
        client = ClientJooble(cle)

    rech = config["recherche"]
    offres: dict[str, dict] = {}
    avertissements: list[str] = []
    reussites = 0
    requetes = requetes_par_mots_cles(config)
    for mot, national in requetes:
        corps = {"keywords": mot, "location": "France"} if national else {
            "keywords": mot, "location": rech.get("ville", "Nantes"), "radius": str(rech["rayon_km"])}
        libelle = f"France entière : {mot}" if national else mot
        try:
            brutes = client.rechercher(corps)
        except ErreurSource as e:
            if "Clé Jooble refusée" in str(e):
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
        log.info("Jooble « %s » : %d offres reçues", libelle, len(brutes))

    if requetes and reussites == 0:
        raise ErreurSource("Toutes les recherches Jooble ont échoué. " + avertissements[0])
    return {"offres": list(offres.values()), "avertissements": avertissements, "complet": not avertissements,
            "delai_jours": DELAI_EXPIRATION_JOURS}
