import json
from pathlib import Path

from collecte.sources import france_travail as ft

OFFRE = json.loads((Path(__file__).parent / "fixtures" / "ft_offre.json").read_text(encoding="utf-8"))


class Reponse:
    def __init__(self, status_code, corps=None, entetes=None):
        self.status_code = status_code
        self._corps = corps
        self.headers = entetes or {}
        self.text = json.dumps(corps)

    def json(self):
        return self._corps


class FausseSession:
    """Imite l'API France Travail : renvoie les réponses prévues par recherche."""

    def __init__(self, reponses):
        self.reponses = reponses  # fonction(params) -> Reponse
        self.appels = []

    def post(self, url, data=None, timeout=None):
        assert data["scope"] == ft.SCOPE
        return Reponse(200, {"access_token": "jeton", "expires_in": 1499})

    def get(self, url, params=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer jeton"
        self.appels.append((url, params))
        return self.reponses(url, params or {})


def client(reponses):
    ft.PAUSE = 0
    return ft.ClientFranceTravail("id", "secret", FausseSession(reponses))


CONFIG = {
    "recherche": {
        "commune_insee": "44109",
        "rayon_km": 30,
        "mots_cles": ["product owner", "business analyst"],
        "codes_rome": [],
        "teletravail_france": {"actif": True, "mots_cles": ["product owner"], "publiees_depuis_jours": 31},
    }
}


def test_conversion_format_commun():
    o = ft.convertir(OFFRE)
    assert o["id"] == "ft-123ABCD"
    assert o["titre"] == "Product Owner H/F"
    assert o["entreprise"] == "ACME Numérique"
    assert o["contrat"] == "CDI"
    assert o["teletravail"] == "partiel"
    assert o["salaire"].startswith("Annuel")
    assert o["sources"][0]["nom"] == "France Travail"
    assert o["sources"][0]["url"].endswith("/123ABCD")
    assert o["rome"]["code"] == "M1806"


def test_alternance_detectee():
    o = ft.convertir({**OFFRE, "typeContrat": "CDD", "natureContrat": "Contrat apprentissage"})
    assert o["contrat"] == "Alternance"


def test_pagination():
    pages = {
        "0-149": Reponse(206, {"resultats": [{**OFFRE, "id": f"A{i}"} for i in range(150)]},
                         {"Content-Range": "offres 0-149/200"}),
        "150-299": Reponse(206, {"resultats": [{**OFFRE, "id": f"B{i}"} for i in range(50)]},
                           {"Content-Range": "offres 150-199/200"}),
    }
    c = client(lambda url, p: pages[p["range"]])
    assert len(c.rechercher({"motsCles": "x"})) == 200


def test_collecte_zone_et_teletravail_national():
    distant = {**OFFRE, "id": "REMOTE1", "lieuTravail": {"libelle": "75 - PARIS"},
               "description": "Poste en full remote, 100% télétravail."}
    hybride_paris = {**OFFRE, "id": "PARIS1", "lieuTravail": {"libelle": "75 - PARIS"}}

    def reponses(url, p):
        if "commune" in p:
            return Reponse(200, {"resultats": [OFFRE]}) if p["motsCles"] == "product owner" else Reponse(204)
        return Reponse(200, {"resultats": [distant, hybride_paris, OFFRE]})

    res = ft.collecter(CONFIG, client(reponses))
    ids = {o["id"]: o for o in res["offres"]}
    assert set(ids) == {"ft-123ABCD", "ft-REMOTE1"}  # l'offre hybride à Paris est écartée
    assert ids["ft-REMOTE1"]["hors_zone"] is True
    assert ids["ft-REMOTE1"]["teletravail"] == "complet"
    assert ids["ft-123ABCD"]["hors_zone"] is False  # trouvée dans la zone : pas « hors zone »
    assert res["complet"] is True


def test_une_recherche_en_echec_n_arrete_pas_les_autres():
    def reponses(url, p):
        if p.get("motsCles") == "business analyst":
            return Reponse(400, {"message": "erreur"})
        return Reponse(200, {"resultats": [OFFRE]})

    res = ft.collecter(CONFIG, client(reponses))
    assert len(res["offres"]) == 1
    assert res["complet"] is False
    assert "business analyst" in res["avertissements"][0]


def test_rapport_rome():
    offres = [ft.convertir(OFFRE), ft.convertir({**OFFRE, "id": "X"}),
              ft.convertir({**OFFRE, "id": "Y", "romeCode": "M1805", "romeLibelle": "Études et développement"})]
    rapport = ft.rapport_rome(offres)
    assert rapport[0]["code"] == "M1806" and rapport[0]["nb"] == 2
