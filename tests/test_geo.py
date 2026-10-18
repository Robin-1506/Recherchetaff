import requests

from collecte import geo
from collecte.modele import normaliser_texte
from collecte.sources import adzuna
from collecte.sources import france_travail as ft
from tests.test_adzuna_jooble import ADZUNA
from tests.test_france_travail import OFFRE

COORDS = {"saint herblain": (47.2122, -1.6497), "rennes": (48.1113, -1.6800)}


class Reponse:
    def __init__(self, corps, statut=200):
        self._corps, self.status_code = corps, statut

    def json(self):
        return self._corps

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FauxGeocodage:
    def __init__(self, en_panne=False):
        self.questions = []
        self.en_panne = en_panne

    def get(self, url, params=None, timeout=None):
        assert url == geo.URL_GEOCODAGE and params["type"] == "municipality"
        self.questions.append(params["q"])
        if self.en_panne:
            raise requests.ConnectionError("hors ligne")
        cle = normaliser_texte(params["q"])
        if cle not in COORDS:
            return Reponse({"features": []})
        lat, lon = COORDS[cle]
        return Reponse({"features": [{"geometry": {"coordinates": [lon, lat]}}]})


def test_ville_depuis_lieu():
    assert geo.ville_depuis_lieu("44 - ST HERBLAIN") == "Saint HERBLAIN"
    assert geo.ville_depuis_lieu("Nantes, Pays de la Loire, France (Hybride)") == "Nantes"
    assert geo.ville_depuis_lieu("Rezé (44)") == "Rezé"
    assert geo.ville_depuis_lieu("44400 Rezé") == "Rezé"
    assert geo.ville_depuis_lieu("Télétravail") is None
    assert geo.ville_depuis_lieu(None) is None


def test_coordonnees_des_sources():
    o = ft.convertir({**OFFRE, "lieuTravail": {"libelle": "44 - NANTES", "latitude": 47.2172, "longitude": -1.5534}})
    assert o["coordonnees"] == [47.2172, -1.5534]
    assert "coordonnees" not in ft.convertir(OFFRE)  # pas de clé vide qui écraserait une valeur connue
    a = adzuna.convertir({**ADZUNA["results"][0], "latitude": 47.21, "longitude": -1.55})
    assert a["coordonnees"] == [47.21, -1.55]


def test_localiser_avec_cache_et_distance():
    donnees = {"meta": {}, "offres": [
        {"id": "1", "lieu": "Saint-Herblain, Pays de la Loire"},
        {"id": "2", "lieu": "Saint Herblain (44)"},           # même ville : une seule recherche
        {"id": "3", "lieu": "Ville Inconnue"},
        {"id": "4", "lieu": "44 - NANTES", "coordonnees": [47.2184, -1.5536]},  # déjà situé
        {"id": "5", "lieu": "Rennes", "expiree": True},       # expirée : ignorée
    ]}
    session = FauxGeocodage()
    ligne = geo.localiser(donnees, {"recherche": {"centre": [47.2184, -1.5536]}}, session)
    assert session.questions == ["Saint-Herblain", "Ville Inconnue"]  # une recherche par ville
    o = {x["id"]: x for x in donnees["offres"]}
    assert o["1"]["coordonnees"] == [47.2122, -1.6497] and o["2"]["coordonnees"] == o["1"]["coordonnees"]
    assert 6 <= o["1"]["distance_km"] <= 9
    assert o["4"]["distance_km"] == 0
    assert "coordonnees" not in o["3"] and "coordonnees" not in o["5"]
    assert ligne.startswith("Carte : 3/4 offres situées")

    # Collecte suivante : tout est en cache, aucune nouvelle recherche.
    session2 = FauxGeocodage()
    geo.localiser(donnees, {}, session2)
    assert session2.questions == []


def test_service_en_panne_ne_bloque_rien():
    donnees = {"meta": {}, "offres": [{"id": "1", "lieu": "Rennes"}, {"id": "2", "lieu": "Nantes"}]}
    session = FauxGeocodage(en_panne=True)
    ligne = geo.localiser(donnees, {}, session)
    assert len(session.questions) == 1  # on n'insiste pas
    assert donnees["meta"]["geocache"] == {}  # rien de mis en cache : nouvel essai la prochaine fois
    assert "indisponible" in ligne
