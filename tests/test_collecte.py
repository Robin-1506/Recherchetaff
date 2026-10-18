from datetime import datetime, timezone

from collecte import stockage
from collecte.__main__ import lancer
from collecte.sources import france_travail as ft
from tests.test_france_travail import CONFIG, OFFRE, Reponse, client

MAINTENANT = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)


def test_collecte_complete():
    d = stockage.donnees_vides()
    c = client(lambda url, p: Reponse(200, {"resultats": [OFFRE]}))
    compte_rendu, statut = lancer(CONFIG, d, MAINTENANT, {ft.CLE: c})
    assert statut["sources"]["france_travail"]["ok"] is True
    assert len(d["offres"]) == 1
    assert any("M1806" in ligne for ligne in compte_rendu)  # rapport des codes ROME


def test_source_en_panne_signalee_sans_planter(monkeypatch):
    monkeypatch.setattr(ft.time, "sleep", lambda s: None)  # pas d'attente entre les essais
    d = stockage.donnees_vides()
    c = client(lambda url, p: Reponse(503, {}))
    compte_rendu, statut = lancer(CONFIG, d, MAINTENANT, {ft.CLE: c})
    assert statut["sources"]["france_travail"]["ok"] is False
    assert "échoué" in statut["sources"]["france_travail"]["message"]
    assert d["offres"] == []


def test_alertes_email_integrees_par_site():
    import yaml
    from pathlib import Path

    from collecte.sources import alertes_email
    from tests.test_alertes_email import LINKEDIN, message

    config = {**yaml.safe_load((Path(__file__).parent.parent / "config" / "criteres.yaml").read_text(encoding="utf-8")),
              "recherche": CONFIG["recherche"]}
    d = stockage.donnees_vides()
    c = client(lambda url, p: Reponse(204))
    compte_rendu, statut = lancer(config, d, MAINTENANT, {ft.CLE: c, alertes_email.CLE: [message(LINKEDIN)]})
    assert statut["sources"]["alertes_email"]["ok"] is True
    assert {o["sources"][0]["nom"] for o in d["offres"]} == {"LinkedIn (alerte)"}
    assert all("categorie" in o for o in d["offres"])  # notées comme les autres
