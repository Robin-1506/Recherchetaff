from collecte import apprentissage


def offre(id_, titre, entreprise="X", score=60, categorie="Product Owner"):
    return {"id": id_, "titre": titre, "entreprise": entreprise, "score": score, "score_base": score,
            "score_detail": [], "categorie": categorie}


CONFIG = {"score": {"apprentissage": {"actif": True, "choix_minimum": 4, "points_max": 15}}}


def suivi(aimees=(), ignorees=(), candidatures=()):
    offres = {i: {"statut": "interessant"} for i in aimees}
    offres.update({i: {"statut": "ignore"} for i in ignorees})
    cands = {f"c{n}": {"offre_id": i, "colonne": "postule"} for n, i in enumerate(candidatures)}
    return {"offres": offres, "candidatures": cands}


def test_choix():
    s = suivi(aimees=["a"], ignorees=["b", "c"], candidatures=["c"])
    s["offres"]["d"] = {"statut": "interessant", "supprime": True}
    assert apprentissage.choix(s) == {"a": 1, "b": -1, "c": 1}  # la candidature l'emporte


def test_apprend_des_mots_et_des_entreprises():
    donnees = {"meta": {}, "offres": [
        offre("a1", "Product Owner Data", "Banque A"), offre("a2", "Product Owner Data senior", "Banque A"),
        offre("a3", "Product Owner Data plateforme", "Assur B"),
        offre("i1", "Product Owner e-commerce", "ESN Z"), offre("i2", "Product Owner retail", "ESN Z"),
        offre("n1", "Product Owner Data", "Banque A"),      # nouvelle : mots et entreprise appréciés
        offre("n2", "Product Owner mobile", "ESN Z"),       # nouvelle : entreprise souvent ignorée
        offre("n3", "Product Owner mobile", "Inconnue"),    # rien d'appris : pas d'ajustement
    ]}
    lignes = apprentissage.appliquer(donnees, suivi(aimees=["a1", "a2", "a3"], ignorees=["i1", "i2"]), CONFIG)
    o = {x["id"]: x for x in donnees["offres"]}
    assert o["n1"]["score"] >= 65 and o["n2"]["score"] < 60
    assert abs(o["n3"]["score"] - 60) <= 2  # seuls des mots communs à tout : quasi neutre
    assert any("Tes choix" in d and "« data »" in d for d in o["n1"]["score_detail"])
    assert any("entreprise ESN Z" in d for d in o["n2"]["score_detail"])
    assert any("Apprentissage actif" in l for l in lignes)


def test_pas_d_auto_renforcement():
    # Une seule offre aimée avec le mot « unique » : son propre choix ne doit pas la faire monter.
    donnees = {"meta": {}, "offres": [offre("a", "PO unique", "A"), offre("b", "PO", "B"), offre("c", "PO", "C"),
                                      offre("d", "PO", "D"), offre("e", "PO", "E")]}
    apprentissage.appliquer(donnees, suivi(aimees=["a"], ignorees=["b", "c", "d"]), CONFIG)
    assert donnees["offres"][0]["score"] == 60


def test_plafond():
    donnees = {"meta": {}, "offres": [offre(f"a{i}", "PO alpha beta gamma delta epsilon") for i in range(6)]
               + [offre("n", "PO alpha beta gamma delta epsilon")]}
    apprentissage.appliquer(donnees, suivi(aimees=[f"a{i}" for i in range(6)]), CONFIG)
    assert donnees["offres"][-1]["score"] == 75  # +15 au maximum


def test_pas_assez_de_choix():
    donnees = {"meta": {}, "offres": [offre("a", "PO data"), offre("b", "PO data")]}
    lignes = apprentissage.appliquer(donnees, suivi(aimees=["a"]), CONFIG)
    assert donnees["offres"][1]["score"] == 60
    assert any("en attente" in l for l in lignes)


def test_mesure_de_fiabilite():
    offres = [offre(f"o{i}", f"Poste {i}", score=100 - i) for i in range(40)]
    donnees = {"meta": {}, "offres": offres}
    f = apprentissage.mesurer(donnees, {"o0": 1, "o1": 1, "o39": 1, "o2": -1, "o30": -1})
    assert f["aimees_top"] == 2 and f["ignorees_top"] == 1 and f["top"] == 20
    assert f["rang_aimees"] == round(100 * (0 + 1 + 39) / 3 / 39)
    assert apprentissage.mesurer(donnees, {}) is None
