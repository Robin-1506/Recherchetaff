import json
from pathlib import Path

import pytest

from collecte.sources import adzuna, jooble
from collecte.sources.commun import ErreurSource, SourceNonConfiguree
from tests.test_france_travail import CONFIG, Reponse

FIXTURES = Path(__file__).parent / "fixtures"
ADZUNA = json.loads((FIXTURES / "adzuna.json").read_text(encoding="utf-8"))
JOOBLE = json.loads((FIXTURES / "jooble.json").read_text(encoding="utf-8"))


class FausseSession:
    def __init__(self, repondre):
        self.repondre = repondre
        self.appels = []

    def request(self, methode, url, timeout=None, **kwargs):
        self.appels.append((methode, url, kwargs))
        return self.repondre(methode, url, kwargs)


def test_adzuna_conversion():
    o = adzuna.convertir(ADZUNA["results"][0])
    assert o["id"] == "adz-4876543210"
    assert o["titre"] == "Product Owner H/F"  # balises HTML retirées
    assert "backlog produit" in o["description"]
    assert o["contrat"] == "CDI"
    assert o["salaire"] == "45 000 € – 52 000 € par an"
    assert o["sources"][0]["url"].startswith("https://www.adzuna.fr/")
    stage = adzuna.convertir(ADZUNA["results"][1])
    assert stage["contrat"] == "Stage"
    assert stage["salaire"] is None  # salaire estimé par Adzuna : ignoré


def test_adzuna_collecte():
    session = FausseSession(lambda m, url, kw: Reponse(200, ADZUNA))
    client = adzuna.ClientAdzuna("id", "cle", session, pause=0)
    res = adzuna.collecter(CONFIG, client)
    assert {o["id"] for o in res["offres"]} == {"adz-4876543210", "adz-4876543211"}
    assert res["delai_jours"] == 7
    zone = [kw["params"] for m, url, kw in session.appels if "where" in kw["params"]]
    assert zone[0]["where"] == "Nantes" and zone[0]["distance"] == 30


def test_adzuna_cle_refusee():
    client = adzuna.ClientAdzuna("id", "cle", FausseSession(lambda m, u, k: Reponse(401, {})), pause=0)
    with pytest.raises(ErreurSource, match="Clé Adzuna refusée"):
        adzuna.collecter(CONFIG, client)


def test_adzuna_non_configuree(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    with pytest.raises(SourceNonConfiguree):
        adzuna.collecter(CONFIG)


def test_jooble_conversion():
    o = jooble.convertir(JOOBLE["jobs"][0])
    assert o["id"] == "jbl--123456"
    assert o["titre"] == "UX Researcher (F/H)"
    assert o["entreprise"] == "Studio Ouest"
    assert o["contrat"] == "CDI"
    assert o["teletravail"] == "complet"
    assert "<b>" not in o["description"]


def test_jooble_essaie_l_hote_international():
    def repondre(m, url, kw):
        return Reponse(404, {}) if url.startswith("https://fr.jooble.org") else Reponse(200, JOOBLE)

    session = FausseSession(repondre)
    client = jooble.ClientJooble("cle", session, pause=0)
    res = jooble.collecter(CONFIG, client)
    assert client.hote == "jooble.org"
    assert len(res["offres"]) == 1
    corps = session.appels[0][2]["json"]
    assert corps["location"] == "Nantes" and corps["radius"] == "30"


def _sans_attente(monkeypatch):
    import collecte.sources.commun as commun
    monkeypatch.setattr(commun.time, "sleep", lambda s: None)
    monkeypatch.setattr(adzuna, "NOUVEL_ESSAI_APRES", 0)
    monkeypatch.setattr(jooble, "NOUVEL_ESSAI_APRES", 0)


def test_adzuna_panne_passagere_rattrapee_au_second_tour(monkeypatch):
    _sans_attente(monkeypatch)
    pannes = {"product owner": 4}  # les 4 essais du premier tour échouent (code 503)

    def repondre(m, url, kw):
        mot = kw["params"]["what_phrase"]
        if pannes.get(mot):
            pannes[mot] -= 1
            return Reponse(503, {})
        return Reponse(200, ADZUNA)

    session = FausseSession(repondre)
    res = adzuna.collecter(CONFIG, adzuna.ClientAdzuna("id", "cle", session, pause=0))
    assert res["avertissements"] == [] and res["complet"]
    essais_po = [kw for m, u, kw in session.appels if kw["params"]["what_phrase"] == "product owner"]
    assert len(essais_po) > 4  # recherche refaite au second tour


def test_jooble_panne_durable_signalee_une_fois(monkeypatch):
    _sans_attente(monkeypatch)
    premier_mot = CONFIG["recherche"]["mots_cles"][0]

    def repondre(m, url, kw):
        return Reponse(503, {}) if kw["json"]["keywords"] == premier_mot else Reponse(200, JOOBLE)

    res = jooble.collecter(CONFIG, jooble.ClientJooble("cle", FausseSession(repondre), pause=0))
    # Un avertissement par recherche concernée (zone de Nantes, et France entière si le mot y est aussi).
    concernees = [r for r in jooble.requetes_par_mots_cles(CONFIG) if r[0] == premier_mot]
    assert len(res["avertissements"]) == len(concernees)
    assert all(premier_mot in a for a in res["avertissements"])
    assert not res["complet"]
