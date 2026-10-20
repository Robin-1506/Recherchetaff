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


ANNUAIRE = {
    "capgemini": [{"nom_complet": "CAPGEMINI TECHNOLOGY SERVICES", "sigle": None, "siege": {},
                   "matching_etablissements": [
                       {"latitude": "47.2063", "longitude": "-1.5640", "adresse": "1 RUE DE NANTES 44000 NANTES",
                        "est_siege": False, "etat_administratif": "A"},
                       {"latitude": "47.2490", "longitude": "-1.4810", "adresse": "5 ROUTE DE CARQUEFOU 44470 CARQUEFOU",
                        "est_siege": False, "etat_administratif": "A"},
                       {"latitude": "47.3", "longitude": "-1.5", "adresse": "FERMÉ", "etat_administratif": "F"}]}],
    "orange": [{"nom_complet": "ORANGE", "siege": {"liste_enseignes": ["ORANGE"]},
                "matching_etablissements": [
                    {"latitude": "47.2120", "longitude": "-1.6500", "adresse": "ZAC ATLANTIS 44800 SAINT-HERBLAIN",
                     "est_siege": False}]}],
    # « ACME » renvoie une autre entreprise : le nom ne correspond pas, on n'invente rien.
    "acme": [{"nom_complet": "BOULANGERIE DUPONT", "matching_etablissements": [
        {"latitude": "47.21", "longitude": "-1.55", "adresse": "PLACE DU MARCHÉ 44000 NANTES"}]}],
}


class FauxAnnuaire:
    def __init__(self, en_panne=False):
        self.questions = []
        self.en_panne = en_panne

    def get(self, url, params=None, timeout=None):
        assert url == geo.URL_ENTREPRISES
        self.questions.append((params["q"], params["departement"]))
        if self.en_panne:
            raise requests.ConnectionError("hors ligne")
        return Reponse({"results": ANNUAIRE.get(normaliser_texte(params["q"]), [])})


def test_noms_correspondent():
    assert geo.noms_correspondent("Capgemini", ["CAPGEMINI TECHNOLOGY SERVICES"])
    assert geo.noms_correspondent("ORANGE SA", ["ORANGE"])
    assert not geo.noms_correspondent("ACME", ["BOULANGERIE DUPONT"])
    assert not geo.noms_correspondent(None, ["X"])


def test_localiser_entreprises():
    nantes, herblain = [47.2184, -1.5536], [47.2122, -1.6497]
    donnees = {"meta": {}, "offres": [
        {"id": "1", "entreprise": "Capgemini", "lieu": "44 - NANTES", "coordonnees": nantes, "score": 90},
        {"id": "2", "entreprise": "CAPGEMINI", "lieu": "44 - CARQUEFOU", "coordonnees": [47.2975, -1.4919], "score": 80},
        {"id": "3", "entreprise": "Orange SA", "lieu": "Saint-Herblain", "coordonnees": herblain, "score": 70},
        {"id": "4", "entreprise": "ACME", "lieu": "44 - NANTES", "coordonnees": nantes, "score": 60},
        {"id": "5", "entreprise": None, "lieu": "Nantes", "coordonnees": nantes},
        {"id": "6", "entreprise": "Capgemini", "lieu": "75 - PARIS", "hors_zone": True},
    ]}
    annuaire = FauxAnnuaire()
    ligne = geo.localiser_entreprises(donnees, {"recherche": {"departements": ["44", "85"]}}, annuaire)
    o = {x["id"]: x for x in donnees["offres"]}
    # Chaque offre prend l'établissement le plus proche de sa ville ; l'établissement fermé est ignoré.
    assert o["1"]["etablissement"]["adresse"].endswith("44000 NANTES")
    assert o["2"]["etablissement"]["adresse"].endswith("CARQUEFOU")
    assert o["1"]["etablissement"]["nb_proches"] == 2
    assert o["3"]["etablissement"]["adresse"].startswith("ZAC ATLANTIS")
    assert o["3"]["distance_km"] == 7
    assert "etablissement" not in o["4"] and "etablissement" not in o["5"] and "etablissement" not in o["6"]
    # Une recherche par entreprise et par département ; « 44 » lu dans le lieu France Travail.
    assert ("Capgemini", "44") in annuaire.questions
    assert ("Orange", "44,85") in annuaire.questions  # « SA » retiré de la recherche
    assert len(annuaire.questions) == 3
    assert ligne.startswith("Adresses d'entreprises : 3/5 offres de la zone placées")

    # Collecte suivante : tout vient du cache.
    annuaire2 = FauxAnnuaire()
    geo.localiser_entreprises(donnees, {"recherche": {"departements": ["44", "85"]}}, annuaire2)
    assert annuaire2.questions == []


def test_annuaire_en_panne():
    donnees = {"meta": {}, "offres": [{"id": "1", "entreprise": "Capgemini", "lieu": "44 - NANTES"},
                                      {"id": "2", "entreprise": "Orange", "lieu": "44 - NANTES"}]}
    annuaire = FauxAnnuaire(en_panne=True)
    ligne = geo.localiser_entreprises(donnees, {}, annuaire)
    assert len(annuaire.questions) == 1 and donnees["meta"]["entreprises"] == {}
    assert "indisponible" in ligne
