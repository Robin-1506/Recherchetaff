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


# ---------------------------------------------------------------------------
# Adresse des entreprises : API Recherche d'entreprises (annuaire public de
# l'État, gratuit et sans clé). Pour chaque entreprise, on garde ses
# établissements du département ; chaque offre prend le plus proche de sa ville.
# ---------------------------------------------------------------------------

URL_ENTREPRISES = "https://recherche-entreprises.api.gouv.fr/search"
MAX_ENTREPRISES_PAR_COLLECTE = 300
PAUSE_ENTREPRISES = 0.2  # l'API accepte environ 7 appels par seconde
DISTANCE_MAX_KM = 20  # au-delà, l'établissement trouvé n'est sans doute pas celui de l'offre
ETABLISSEMENTS_GARDES = 10
_FORMES_JURIDIQUES = {"sas", "sa", "sarl", "sasu", "eurl", "sci", "sca", "snc", "scop", "groupe", "group", "france",
                      "the", "et", "and", "de", "du", "des", "la", "le", "les", "l", "d"}


def _mots(nom: str | None) -> set[str]:
    return {m for m in normaliser_texte(nom).split() if m not in _FORMES_JURIDIQUES}


def noms_correspondent(nom_offre: str | None, noms_annuaire: list[str | None]) -> bool:
    """Le nom de l'offre et un nom de l'annuaire désignent-ils la même entreprise ?"""
    a = _mots(nom_offre)
    if not a:
        return False
    for nom in noms_annuaire:
        b = _mots(nom)
        if b and (a <= b or b <= a or len(a & b) / len(a | b) >= 0.6):
            return True
    return False


def departement(offre: dict) -> str | None:
    """« 44 - NANTES » (France Travail) → « 44 »."""
    m = re.match(r"\s*(\d{2,3}|2[AB])\s*-", offre.get("lieu") or "")
    return m.group(1) if m else None


def _nombre(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def chercher_etablissements(nom: str, departements: str, session: requests.Session) -> list[dict]:
    """Établissements actifs de l'entreprise dans les départements donnés.
    Lève requests.RequestException si le service ne répond pas."""
    # « ACME SAS » → « ACME » : les formes juridiques gênent la recherche.
    question = re.sub(r"\b(SAS|SASU|SARL|SA|EURL|SCI|SNC|SCOP)\b\.?", " ", nom, flags=re.IGNORECASE)
    question = re.sub(r"\s+", " ", question).strip(" -,") or nom
    r = session.get(URL_ENTREPRISES, params={
        "q": question, "departement": departements, "per_page": 5, "limite_matching_etablissements": 25,
    }, timeout=20)
    if r.status_code == 429:
        raise requests.HTTPError("429 trop de requêtes")
    r.raise_for_status()
    trouves = []
    for resultat in r.json().get("results") or []:
        noms = [resultat.get("nom_complet"), resultat.get("nom_raison_sociale"), resultat.get("sigle")]
        noms += (resultat.get("siege") or {}).get("liste_enseignes") or []
        if not noms_correspondent(nom, noms):
            continue
        etablissements = resultat.get("matching_etablissements") or [resultat.get("siege") or {}]
        for e in etablissements:
            lat, lon = _nombre(e.get("latitude")), _nombre(e.get("longitude"))
            if lat is None or lon is None or e.get("etat_administratif", "A") != "A":
                continue
            trouves.append({"coordonnees": [round(lat, 5), round(lon, 5)], "adresse": e.get("adresse"),
                            "nom": resultat.get("nom_complet"), "siege": bool(e.get("est_siege"))})
    return trouves


def localiser_entreprises(donnees: dict, config: dict, session: requests.Session | None = None) -> str:
    """Place les offres de la zone à l'adresse de l'établissement le plus proche
    de leur ville. Renvoie une ligne de compte rendu."""
    rech = config.get("recherche") or {}
    centre = tuple(rech.get("centre") or NANTES)
    departements_zone = ",".join(str(d) for d in (rech.get("departements") or ["44"]))
    cache = donnees["meta"].setdefault("entreprises", {})
    session = session or requests.Session()
    recherches = 0
    service_ok = True

    # Les offres les mieux notées d'abord (si le plafond de recherches est atteint).
    offres = sorted((o for o in donnees["offres"] if not o.get("expiree") and not o.get("hors_zone")),
                    key=lambda o: -o.get("score_tri", o.get("score") or 0))
    for offre in offres:
        nom = offre.get("entreprise")
        if not _mots(nom):
            continue
        dep = departement(offre) or departements_zone
        cle = f"{normaliser_texte(nom)}|{dep}"
        if cle not in cache:
            if not service_ok or recherches >= MAX_ENTREPRISES_PAR_COLLECTE:
                continue
            try:
                time.sleep(PAUSE_ENTREPRISES)
                trouves = chercher_etablissements(nom, dep, session)
                recherches += 1
            except (requests.RequestException, ValueError, KeyError, TypeError) as e:
                log.warning("Annuaire des entreprises indisponible (%s) : nouvel essai demain.", e.__class__.__name__)
                service_ok = False
                continue
            # On garde les établissements les plus proches du centre (taille du fichier).
            trouves.sort(key=lambda e: distance_km(centre, e["coordonnees"]))
            cache[cle] = trouves[:ETABLISSEMENTS_GARDES]

        candidats = cache.get(cle) or []
        repere = offre.get("coordonnees") or list(centre)
        meilleur = min(candidats, key=lambda e: (distance_km(repere, e["coordonnees"]), not e["siege"]), default=None)
        if meilleur and distance_km(repere, meilleur["coordonnees"]) <= DISTANCE_MAX_KM:
            proches = sum(1 for e in candidats if distance_km(repere, e["coordonnees"]) <= DISTANCE_MAX_KM)
            offre["etablissement"] = {**meilleur, "nb_proches": proches}
            offre["distance_km"] = round(distance_km(centre, meilleur["coordonnees"]))
        else:
            offre.pop("etablissement", None)

    placees = sum(1 for o in offres if o.get("etablissement"))
    ligne = f"Adresses d'entreprises : {placees}/{len(offres)} offres de la zone placées ({recherches} entreprises recherchées)"
    return ligne + ("" if service_ok else " — annuaire indisponible, nouvel essai demain")
