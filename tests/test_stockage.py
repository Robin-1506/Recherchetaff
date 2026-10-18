from datetime import datetime, timezone

from collecte import stockage


def offre(id_, source="France Travail"):
    return {"id": id_, "titre": f"Offre {id_}", "description": "texte",
            "sources": [{"nom": source, "id": id_, "url": "u", "active": True}]}


def test_nouvelles_puis_expiration():
    d = stockage.donnees_vides()
    bilan = stockage.integrer(d, "France Travail", [offre("a"), offre("b")], "2026-10-01T05:00:00+00:00", True)
    assert bilan == {"nouvelles": 2, "doublons": 0, "expirees": 0}

    # Le lendemain, « b » a disparu des recherches et n'existe plus.
    bilan = stockage.integrer(d, "France Travail", [offre("a")], "2026-10-02T05:00:00+00:00", True,
                              encore_en_ligne=lambda ids: set())
    assert bilan == {"nouvelles": 0, "doublons": 0, "expirees": 1}
    b = next(o for o in d["offres"] if o["id"] == "b")
    assert b["expiree"] is True and b["premiere_vue"] == "2026-10-01T05:00:00+00:00"
    assert len(d["offres"]) == 2  # conservée, pas supprimée


def test_offre_encore_en_ligne_non_expiree():
    d = stockage.donnees_vides()
    stockage.integrer(d, "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "France Travail", [], "t2", True, encore_en_ligne=lambda ids: {"a"})
    assert d["offres"][0]["expiree"] is False


def test_collecte_incomplete_n_expire_rien():
    d = stockage.donnees_vides()
    stockage.integrer(d, "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "France Travail", [], "t2", False)
    assert d["offres"][0]["expiree"] is False


def test_offre_revenue_n_est_plus_expiree():
    d = stockage.donnees_vides()
    stockage.integrer(d, "France Travail", [offre("a")], "t1", True)
    stockage.integrer(d, "France Travail", [], "t2", True, encore_en_ligne=lambda ids: set())
    stockage.integrer(d, "France Travail", [offre("a")], "t3", True)
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


def test_doublon_entre_sources_fusionne():
    d = stockage.donnees_vides()
    ft = {**offre("ft-1"), "titre": "Product Owner H/F", "entreprise": "ACME SAS", "salaire": None,
          "sources": [{"nom": "France Travail", "id": "1", "url": "https://ft/1", "active": True}]}
    adz = {**offre("adz-9", "Adzuna"), "titre": "Product Owner (H/F)", "entreprise": "Acme",
           "salaire": "45 000 € par an",
           "sources": [{"nom": "Adzuna", "id": "9", "url": "https://adz/9", "active": True}]}
    stockage.integrer(d, "France Travail", [ft], "2026-10-18T05:00:00+00:00", True)
    bilan = stockage.integrer(d, "Adzuna", [adz], "2026-10-18T05:00:00+00:00", True, delai_jours=7)
    assert bilan["doublons"] == 1 and bilan["nouvelles"] == 0
    assert len(d["offres"]) == 1
    o = d["offres"][0]
    assert [s["nom"] for s in o["sources"]] == ["France Travail", "Adzuna"]
    assert o["salaire"] == "45 000 € par an"  # complété par le doublon
    assert o["titre"] == "Product Owner H/F"  # la fiche principale reste celle de France Travail

    # Le lendemain, le lien Adzuna est retrouvé par son identifiant, sans nouveau doublon.
    bilan = stockage.integrer(d, "Adzuna", [adz], "2026-10-19T05:00:00+00:00", True, delai_jours=7)
    assert bilan == {"nouvelles": 0, "doublons": 0, "expirees": 0}
    assert len(d["offres"][0]["sources"]) == 2


def test_sans_entreprise_pas_de_fusion():
    d = stockage.donnees_vides()
    a = {**offre("ft-1"), "titre": "Product Owner", "entreprise": None}
    b = {**offre("adz-1", "Adzuna"), "titre": "Product Owner", "entreprise": None}
    stockage.integrer(d, "France Travail", [a], "2026-10-18T05:00:00+00:00", True)
    stockage.integrer(d, "Adzuna", [b], "2026-10-18T05:00:00+00:00", True)
    assert len(d["offres"]) == 2


def test_delai_de_grace_avant_expiration():
    d = stockage.donnees_vides()
    stockage.integrer(d, "Adzuna", [offre("adz-1", "Adzuna")], "2026-10-01T05:00:00+00:00", True, delai_jours=7)
    stockage.integrer(d, "Adzuna", [], "2026-10-05T05:00:00+00:00", True, delai_jours=7)
    assert d["offres"][0]["expiree"] is False  # absente 4 jours : on attend encore
    bilan = stockage.integrer(d, "Adzuna", [], "2026-10-09T05:00:00+00:00", True, delai_jours=7)
    assert bilan["expirees"] == 1


def test_offre_toujours_active_chez_une_autre_source():
    d = stockage.donnees_vides()
    a = {**offre("ft-1"), "titre": "PO", "entreprise": "ACME"}
    b = {**offre("adz-1", "Adzuna"), "titre": "PO", "entreprise": "ACME"}
    stockage.integrer(d, "France Travail", [a], "2026-10-01T05:00:00+00:00", True)
    stockage.integrer(d, "Adzuna", [b], "2026-10-01T05:00:00+00:00", True, delai_jours=7)
    stockage.integrer(d, "France Travail", [], "2026-10-02T05:00:00+00:00", True, encore_en_ligne=lambda ids: set())
    o = d["offres"][0]
    assert o["expiree"] is False
    assert [s["active"] for s in o["sources"]] == [False, True]
