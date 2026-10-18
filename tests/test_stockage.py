from datetime import datetime, timezone

from collecte import stockage


def offre(id_, source="France Travail"):
    return {"id": id_, "titre": f"Offre {id_}", "description": "texte",
            "sources": [{"nom": source, "id": id_, "url": "u", "active": True}]}


def test_nouvelles_puis_expiration():
    d = stockage.donnees_vides()
    bilan = stockage.integrer(d, "ft", "France Travail", [offre("a"), offre("b")], "2026-10-01T05:00:00+00:00", True)
    assert bilan == {"nouvelles": 2, "expirees": 0}

    # Le lendemain, « b » a disparu des recherches et n'existe plus.
    bilan = stockage.integrer(d, "ft", "France Travail", [offre("a")], "2026-10-02T05:00:00+00:00", True,
                              encore_en_ligne=lambda ids: set())
    assert bilan == {"nouvelles": 0, "expirees": 1}
    b = next(o for o in d["offres"] if o["id"] == "b")
    assert b["expiree"] is True and b["premiere_vue"] == "2026-10-01T05:00:00+00:00"
    assert len(d["offres"]) == 2  # conservée, pas supprimée


def test_offre_encore_en_ligne_non_expiree():
    d = stockage.donnees_vides()
    stockage.integrer(d, "ft", "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "ft", "France Travail", [], "t2", True, encore_en_ligne=lambda ids: {"a"})
    assert d["offres"][0]["expiree"] is False


def test_collecte_incomplete_n_expire_rien():
    d = stockage.donnees_vides()
    stockage.integrer(d, "ft", "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "ft", "France Travail", [], "t2", False)
    assert d["offres"][0]["expiree"] is False


def test_offre_revenue_n_est_plus_expiree():
    d = stockage.donnees_vides()
    stockage.integrer(d, "ft", "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "ft", "France Travail", [], "t2", True, encore_en_ligne=lambda ids: set())
    stockage.integrer(d, "ft", "France Travail", [offre("a")], "t3", True)
    assert d["offres"][0]["expiree"] is False
    assert d["offres"][0]["premiere_vue"] == "t1"


def test_allegement_des_vieilles_offres_expirees():
    d = {"meta": {}, "offres": [{"id": "a", "expiree": True, "date_expiration": "2026-08-01T00:00:00+00:00",
                                "description": "long"}]}
    stockage.alleger(d, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert d["offres"][0]["description"] == ""


def test_pas_de_reecriture_si_rien_ne_change(tmp_path):
    chemin = tmp_path / "d.json"
    d = stockage.donnees_vides()
    assert stockage.enregistrer(chemin, d, "mdp", None) is True
    assert stockage.enregistrer(chemin, d, "mdp", stockage.charger(chemin, "mdp")) is False
