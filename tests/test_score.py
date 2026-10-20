from pathlib import Path

import yaml

from collecte import score

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config" / "criteres.yaml").read_text(encoding="utf-8"))
REGLES = CONFIG["score"]


def noter(**champs):
    offre = {"titre": "", "description": "", "contrat": "CDI", "hors_zone": False, "teletravail": None, **champs}
    score.noter(offre, REGLES)
    return offre


def test_product_owner_cdi_nantes_en_tete():
    o = noter(titre="Product Owner H/F", description="Méthode agile, gestion du backlog et recueil des besoins.")
    assert o["categorie"] == "Product Owner"
    assert o["score"] >= 90
    assert any("backlog" in d for d in o["score_detail"])


def test_ordre_de_priorite_des_postes():
    po = noter(titre="Product Owner")["score"]
    cdp = noter(titre="Chef de projet digital")["score"]
    ba = noter(titre="Business Analyst")["score"]
    pm = noter(titre="Product Manager")["score"]
    ux = noter(titre="UX Researcher")["score"]
    assert po > cdp > ba > pm > ux  # même avec le bonus « UX » du titre


def test_accents_et_majuscules_ignores():
    o = noter(titre="CHEF DE PROJET MAÎTRISE D'OUVRAGE")
    assert o["categorie"] == "Chef de projet IT / digital"


def test_intitule_dans_la_description_seulement():
    o = noter(titre="Consultant transformation", description="Vous intervenez comme Product Owner auprès des équipes.")
    assert o["categorie"] == "Product Owner"
    assert o["score"] < noter(titre="Product Owner")["score"]


def test_bruit_declasse():
    for titre in ("Développeur Java", "Stage - Business Analyst", "Ingénieur commercial", "Chef de projet travaux",
                  "Ergonome santé au travail"):
        o = noter(titre=titre)
        assert o["categorie"] == score.HORS_CIBLE, titre
        assert o["score"] < 25, titre


def test_alternance_toujours_hors_cible():
    o = noter(titre="Product Owner", contrat="Alternance")
    assert o["categorie"] == score.HORS_CIBLE


def test_po_secteur_sante_reste_cible():
    o = noter(titre="Product Owner - secteur santé")
    assert o["categorie"] == "Product Owner"


def test_lieu_et_contrat():
    zone_cdi = noter(titre="Product Owner")["score"]
    remote_cdi = noter(titre="Product Owner", hors_zone=True, teletravail="complet")["score"]
    zone_freelance = noter(titre="Product Owner", contrat="Freelance")
    assert zone_cdi > remote_cdi
    assert zone_cdi > zone_freelance["score"]
    assert "Freelance" in zone_freelance["signalements"]


def test_esn_signalee_pas_exclue():
    o = noter(titre="Business Analyst", description="Pour l'un de nos clients, grand compte bancaire…")
    assert o["categorie"] == "Business Analyst / AMOA"
    assert "Mission ESN / conseil" in o["signalements"]


def test_rien_a_voir():
    o = noter(titre="Boulanger", description="Fabrication du pain")
    assert o["categorie"] == score.HORS_CIBLE


def test_noter_tout():
    donnees = {"offres": [{"titre": "Product Owner", "description": "", "contrat": "CDI", "hors_zone": False},
                          {"titre": "Boulanger", "description": "", "contrat": "CDI", "hors_zone": False,
                           "expiree": True}]}
    assert score.noter_tout(donnees, CONFIG) == {"Product Owner": 1}


def test_experience_trop_elevee():
    base = noter(titre="Product Owner")["score"]
    def avec(annees):
        o = {"titre": "Product Owner", "description": "", "contrat": "CDI", "hors_zone": False,
             "competences": {"annees_min": annees}}
        score.noter(o, REGLES)
        return o
    assert avec(5)["score"] == base                 # 5 ans : dans ta portée
    assert avec(7)["score"] == base - 10            # plus de 6 ans
    assert avec(10)["score"] == base - 20           # plus de 9 ans
    assert any("Expérience demandée" in d for d in avec(10)["score_detail"])


def test_poste_de_direction():
    assert noter(titre="Directeur produit - Product Owner")["score"] < noter(titre="Product Owner")["score"]


def test_fraicheur():
    from datetime import datetime, timezone
    maintenant = datetime(2026, 10, 20, tzinfo=timezone.utc)
    def age(date):
        o = {"titre": "Product Owner", "description": "", "contrat": "CDI", "hors_zone": False,
             "date_publication": date}
        score.noter(o, REGLES, maintenant)
        return o["score"]
    recente, moyenne, ancienne = age("2026-10-18T08:00:00Z"), age("2026-10-01T08:00:00Z"), age("2026-08-01T08:00:00Z")
    assert recente == moyenne + 5 and ancienne == moyenne - 5


def test_teletravail_partiel():
    assert noter(titre="Product Owner", teletravail="partiel")["score"] == noter(titre="Product Owner")["score"] + 3


def test_classement_sans_plafond():
    riche = noter(titre="Product Owner", teletravail="partiel",
                  description="agile scrum backlog user stories recueil des besoins UX ergonomie tests utilisateurs")
    simple = noter(titre="Product Owner", description="agile scrum backlog user stories recueil des besoins")
    assert riche["score"] == simple["score"] == 100  # même note affichée…
    assert riche["score_tri"] > simple["score_tri"]  # …mais la plus riche passe devant
