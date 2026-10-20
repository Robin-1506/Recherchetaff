"""Récap des compétences demandées par chaque offre.

Repère dans l'intitulé et la description les mots de la section
« competences » de config/criteres.yaml (méthodes, outils, savoir-faire…),
ainsi que l'expérience et le niveau d'études demandés. Les compétences
structurées de France Travail (champ "exigences_ft") sont gardées à part.

Champ ajouté à l'offre : "competences" = {"groupes": {nom: [mots]},
"experience": texte ou None, "etudes": texte ou None}.
"""

import re

from .score import _contient
from .modele import normaliser_texte

_EXPERIENCE = re.compile(
    r"(?:(?:au moins|minimum|min\.?)\s*)?(\d{1,2})\s*(?:(?:à|a|-)\s*(\d{1,2})\s*)?(?:\+\s*)?(?:ans|années|years)"
    r"(?:\s+minimum)?\s+(?:d['’]\s*|of\s+)?(?:exp[ée]rience|experience)",
    re.IGNORECASE,
)
_DEBUTANT = re.compile(r"d[ée]butant(?:e|\(e\))?s?\s+accept[ée]|premi[èe]re exp[ée]rience|junior", re.IGNORECASE)
_ETUDES = re.compile(r"\bbac\s*\+\s*(\d)\b", re.IGNORECASE)
_DIPLOMES = [("Master", re.compile(r"\bmaster\b", re.IGNORECASE)),
             ("École d'ingénieur", re.compile(r"[ée]cole d['’]ing[ée]nieur", re.IGNORECASE))]


def experience(texte: str) -> str | None:
    m = _EXPERIENCE.search(texte)
    if m:
        mini, maxi = m.group(1), m.group(2)
        return f"{mini} à {maxi} ans d'expérience" if maxi else f"{mini} ans d'expérience"
    if _DEBUTANT.search(texte):
        return "Débutant / junior accepté"
    return None


def etudes(texte: str) -> str | None:
    niveaux = sorted({int(n) for n in _ETUDES.findall(texte)})
    if niveaux:
        return " ou ".join(f"Bac+{n}" for n in niveaux)
    for nom, motif in _DIPLOMES:
        if motif.search(texte):
            return nom
    return None


def analyser(offre: dict, groupes: dict) -> None:
    texte = " ".join(t for t in (offre.get("titre"), offre.get("description")) if t)
    if not offre.get("description") and offre.get("competences"):
        return  # description vidée (offre expirée ancienne) : on garde l'analyse précédente
    texte_n = normaliser_texte(texte)
    trouves = {}
    for nom, mots in (groupes or {}).items():
        presents = [m for m in mots or [] if _contient(texte_n, m)]
        if presents:
            trouves[nom] = presents
    offre["competences"] = {"groupes": trouves, "experience": experience(texte), "etudes": etudes(texte)}


def analyser_tout(donnees: dict, config: dict) -> None:
    groupes = config.get("competences") or {}
    for offre in donnees["offres"]:
        analyser(offre, groupes)
