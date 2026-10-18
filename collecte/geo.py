"""Localisation des offres pour la carte du site.

- France Travail et Adzuna fournissent souvent des coordonnées : on les garde.
- Sinon, on retrouve la ville à partir du lieu (« 44 - NANTES »,
  « Saint-Herblain, Pays de la Loire »…) grâce au service de géocodage de
  l'IGN (Base Adresse Nationale, gratuit et sans clé). Chaque ville n'est
  cherchée qu'une fois : le résultat est gardé dans les données.

Champs ajoutés à l'offre : "coordonnees" [lat, lon] et "distance_km" depuis
le centre de la recherche (config : recherche.centre).
"""

import logging
import math
import re
import time

import requests

from .modele import normaliser_texte

URL_GEOCODAGE = "https://data.geopf.fr/geocodage/search"
NANTES = (47.2184, -1.5536)
MAX_RECHERCHES_PAR_COLLECTE = 150
PAUSE = 0.1
LIEUX_SANS_VILLE = {"", "france", "teletravail", "remote", "full remote", "a distance", "france entiere"}

log = logging.getLogger(__name__)


def distance_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def ville_depuis_lieu(lieu: str | None) -> str | None:
    """« 44 - ST HERBLAIN » → « Saint Herblain » ; « Nantes, Pays de la Loire » → « Nantes »."""
    if not lieu:
        return None
    texte = re.sub(r"\(.*?\)", " ", lieu)            # « (44) », « (Hybride) »
    texte = re.sub(r"^\s*\d{2,3}\s*-\s*", "", texte)  # « 44 - » de France Travail
    texte = texte.split(",")[0]
    texte = re.sub(r"\b\d{5}\b", " ", texte)          # codes postaux
    texte = re.sub(r"\bSTE?\b", lambda m: "Sainte" if m.group(0).upper() == "STE" else "Saint", texte,
                   flags=re.IGNORECASE)
    texte = re.sub(r"\s+", " ", texte).strip(" -")
    if normaliser_texte(texte) in LIEUX_SANS_VILLE or len(texte) < 2:
        return None
    return texte


def geocoder(ville: str, session: requests.Session, centre=NANTES) -> list[float] | None:
    """Coordonnées [lat, lon] d'une commune, ou None si introuvable.
    Lève requests.RequestException si le service ne répond pas."""
    r = session.get(URL_GEOCODAGE, params={
        "q": ville, "index": "address", "type": "municipality", "limit": 1,
        "lat": centre[0], "lon": centre[1],  # en cas d'homonymes, la plus proche est préférée
    }, timeout=15)
    r.raise_for_status()
    elements = r.json().get("features") or []
    if not elements:
        return None
    lon, lat = elements[0]["geometry"]["coordinates"]
    return [round(lat, 5), round(lon, 5)]


def localiser(donnees: dict, config: dict, session: requests.Session | None = None) -> str:
    """Complète les coordonnées et distances. Renvoie une ligne de compte rendu."""
    centre = tuple((config.get("recherche") or {}).get("centre") or NANTES)
    cache = donnees["meta"].setdefault("geocache", {})
    session = session or requests.Session()
    recherches = 0
    service_ok = True

    for offre in donnees["offres"]:
        if offre.get("expiree"):
            continue
        if not offre.get("coordonnees"):
            ville = ville_depuis_lieu(offre.get("lieu"))
            cle = normaliser_texte(ville) if ville else None
            if cle and cle not in cache and service_ok and recherches < MAX_RECHERCHES_PAR_COLLECTE:
                try:
                    time.sleep(PAUSE)
                    cache[cle] = geocoder(ville, session, centre)
                    recherches += 1
                except (requests.RequestException, ValueError, KeyError, IndexError) as e:
                    log.warning("Géocodage indisponible (%s) : on réessaiera à la prochaine collecte.", e.__class__.__name__)
                    service_ok = False
            if cle and cache.get(cle):
                offre["coordonnees"] = cache[cle]
        if offre.get("coordonnees"):
            offre["distance_km"] = round(distance_km(centre, offre["coordonnees"]))

    actives = [o for o in donnees["offres"] if not o.get("expiree")]
    situees = sum(1 for o in actives if o.get("coordonnees"))
    ligne = f"Carte : {situees}/{len(actives)} offres situées ({recherches} villes recherchées)"
    return ligne + ("" if service_ok else " — service de géocodage indisponible, nouvel essai demain")
