from pathlib import Path

import yaml

from collecte import competences
from collecte.sources import france_travail as ft
from tests.test_france_travail import OFFRE

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config" / "criteres.yaml").read_text(encoding="utf-8"))


def analyser(titre="", description=""):
    o = {"titre": titre, "description": description}
    competences.analyser(o, CONFIG["competences"])
    return o["competences"]


def test_reperage_par_groupe():
    c = analyser("Product Owner", "Méthode Agile / SCRUM, outils JIRA et Confluence. Rédaction des User Stories, "
                                  "animation d'ateliers. Anglais courant. Certification PSPO appréciée.")
    g = c["groupes"]
    assert g["Méthodes"] == ["agile", "scrum"]
    assert g["Outils"] == ["Jira", "Confluence"]
    assert "user stories" in g["Savoir-faire"] and "ateliers" in g["Savoir-faire"]
    assert g["Certifications"] == ["PSPO"]
    assert g["Langues"] == ["anglais"]


def test_mots_entiers_seulement():
    # « api » ne doit pas être repéré dans « rapide », ni « SAP » dans « sapin ».
    g = analyser("", "Un rythme rapide près du sapin.")["groupes"]
    assert "Outils" not in g and "Savoir-faire" not in g


def test_experience_et_etudes():
    assert analyser("", "Vous justifiez de 3 ans d'expérience minimum")["experience"] == "3 ans d'expérience"
    assert analyser("", "5 à 8 ans d’expérience en AMOA")["experience"] == "5 à 8 ans d'expérience"
    assert analyser("", "Débutant accepté")["experience"] == "Débutant / junior accepté"
    assert analyser("", "Rien de précis")["experience"] is None
    assert analyser("", "Formation Bac+5 (école d'ingénieur ou Bac + 3)")["etudes"] == "Bac+3 ou Bac+5"
    assert analyser("", "Titulaire d'un Master")["etudes"] == "Master"


def test_description_videe_garde_l_analyse():
    o = {"titre": "PO", "description": "", "competences": {"groupes": {"Outils": ["Jira"]}}}
    competences.analyser(o, CONFIG["competences"])
    assert o["competences"]["groupes"] == {"Outils": ["Jira"]}


def test_exigences_france_travail():
    brute = {**OFFRE,
             "competences": [{"libelle": "Animer des ateliers", "exigence": "E"},
                             {"libelle": "Rédiger des user stories", "exigence": "S"}],
             "qualitesProfessionnelles": [{"libelle": "Sens de la communication"}],
             "formations": [{"niveauLibelle": "Bac+5 et plus ou équivalents", "domaineLibelle": "informatique",
                             "exigence": "E"}],
             "langues": [{"libelle": "Anglais", "exigence": "S"}],
             "experienceExige": "E", "experienceLibelle": "3 An(s)"}
    e = ft.convertir(brute)["exigences_ft"]
    assert e["exigees"] == ["Animer des ateliers"] and e["souhaitees"] == ["Rédiger des user stories"]
    assert e["savoir_etre"] == ["Sens de la communication"]
    assert e["formations"] == ["Bac+5 et plus ou équivalents informatique (exigé)"]
    assert e["langues"] == ["Anglais (souhaité)"]
    assert e["experience"] == "3 an(s) (exigée)"
    assert "exigences_ft" not in ft.convertir(OFFRE)  # rien de fourni : pas de champ vide
