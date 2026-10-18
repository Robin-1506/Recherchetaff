"""Lecture, mise à jour et écriture du fichier de données (chiffré).

Structure des données :
    {"meta": {"version": 1}, "offres": [offre, ...]}
Les offres expirées sont gardées (champ "expiree") ; leur description est
vidée après JOURS_AVANT_ALLEGEMENT jours pour limiter la taille du fichier.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

from .chiffrement import chiffrer, dechiffrer
from .modele import empreinte

JOURS_AVANT_ALLEGEMENT = 30


def donnees_vides() -> dict:
    return {"meta": {"version": 1}, "offres": []}


def charger(chemin: Path, mot_de_passe: str) -> dict:
    if not chemin.exists():
        return donnees_vides()
    return dechiffrer(json.loads(chemin.read_text(encoding="utf-8")), mot_de_passe)


def enregistrer(chemin: Path, donnees: dict, mot_de_passe: str, precedentes: dict | None) -> bool:
    """Écrit le fichier seulement si le contenu a changé (évite des
    enregistrements inutiles dans l'historique). Renvoie True si écrit."""
    if precedentes is not None and json.dumps(precedentes, sort_keys=True) == json.dumps(donnees, sort_keys=True):
        return False
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(chiffrer(donnees, mot_de_passe)), encoding="utf-8")
    return True


def _cle_source(source: dict) -> tuple[str, str]:
    return (source["nom"], str(source["id"]))


def integrer(donnees: dict, nom_source: str, nouvelles: list[dict], maintenant: str, complet: bool,
             encore_en_ligne=None, delai_jours: int | None = None) -> dict:
    """Intègre les offres d'une source dans les données existantes.

    Pour chaque offre reçue, on cherche si elle est déjà connue :
      1. par son identifiant chez cette source ;
      2. sinon par son « empreinte » (même intitulé, même entreprise) : c'est
         alors un doublon venu d'une autre source, on ajoute simplement le lien.
    Sinon, c'est une nouvelle offre (premiere_vue = maintenant).

    Offres de cette source absentes aujourd'hui (seulement si la collecte est
    complète) : le lien de cette source devient inactif
      - si `encore_en_ligne(ids)` ne les renvoie pas (vérification une à une), ou
      - si elles n'ont pas été revues depuis `delai_jours` jours.
    Une offre dont tous les liens sont inactifs devient « expirée ».
    Renvoie un petit bilan {"nouvelles", "doublons", "expirees"}.
    """
    par_source = {}
    par_empreinte = {}
    for o in donnees["offres"]:
        for s in o.get("sources", []):
            par_source[_cle_source(s)] = o
        e = empreinte(o)
        if e and (e not in par_empreinte or par_empreinte[e].get("expiree")):
            par_empreinte[e] = o

    jour = maintenant[:10]
    vues = set()
    bilan = {"nouvelles": 0, "doublons": 0, "expirees": 0}

    for offre in nouvelles:
        source = {**offre["sources"][0], "active": True}
        if delai_jours is not None:
            source["vue_le"] = jour
        vues.add(_cle_source(source))
        existante = par_source.get(_cle_source(source))
        if existante is None:
            e = empreinte(offre)
            existante = par_empreinte.get(e) if e else None
            if existante is not None:
                bilan["doublons"] += 1
        if existante is None:
            offre = {**offre, "sources": [source], "premiere_vue": maintenant, "expiree": False,
                     "date_expiration": None}
            donnees["offres"].append(offre)
            bilan["nouvelles"] += 1
        else:
            _fusionner(existante, offre, source)
            offre = existante
        par_source[_cle_source(source)] = offre
        e = empreinte(offre)
        if e:
            par_empreinte[e] = offre

    if complet:
        toujours = set()
        if encore_en_ligne:
            a_verifier = [
                s["id"] for o in donnees["offres"] for s in o.get("sources", [])
                if s["nom"] == nom_source and s.get("active", True) and _cle_source(s) not in vues
            ]
            toujours = encore_en_ligne(a_verifier) if a_verifier else set()
        limite = None
        if delai_jours is not None:
            limite = (datetime.fromisoformat(maintenant) - timedelta(days=delai_jours)).date().isoformat()
        for o in donnees["offres"]:
            if o.get("expiree"):
                continue
            for s in o.get("sources", []):
                if s["nom"] != nom_source or not s.get("active", True) or _cle_source(s) in vues:
                    continue
                if s["id"] in toujours:
                    continue
                if limite is not None and s.get("vue_le", "") >= limite:
                    continue  # pas revue aujourd'hui, mais encore dans le délai de grâce
                s["active"] = False
            if not any(s.get("active", True) for s in o.get("sources", [])):
                o["expiree"] = True
                o["date_expiration"] = maintenant
                bilan["expirees"] += 1

    return bilan


# Champs complétés à partir d'un doublon s'ils manquent dans l'offre principale.
_CHAMPS_COMPLEMENTAIRES = ("entreprise", "salaire", "teletravail", "lieu", "date_publication", "description")


def _fusionner(existante: dict, offre: dict, source: dict) -> None:
    sources = existante.setdefault("sources", [])
    principale = sources and _cle_source(sources[0]) == _cle_source(source)
    existante["sources"] = [s for s in sources if _cle_source(s) != _cle_source(source)]
    if principale:
        existante["sources"].insert(0, source)
        for k, v in offre.items():
            if k not in ("id", "sources", "premiere_vue", "expiree", "date_expiration"):
                existante[k] = v
    else:
        existante["sources"].append(source)
        for k in _CHAMPS_COMPLEMENTAIRES:
            if not existante.get(k) and offre.get(k):
                existante[k] = offre[k]
        if existante.get("contrat") in (None, "Autre") and offre.get("contrat") not in (None, "Autre"):
            existante["contrat"] = offre["contrat"]
        if existante.get("hors_zone") and not offre.get("hors_zone"):
            existante["hors_zone"] = False
    existante["expiree"] = False
    existante["date_expiration"] = None


def alleger(donnees: dict, maintenant: datetime) -> None:
    limite = maintenant - timedelta(days=JOURS_AVANT_ALLEGEMENT)
    for o in donnees["offres"]:
        if o.get("expiree") and o.get("date_expiration") and o.get("description"):
            if datetime.fromisoformat(o["date_expiration"]) < limite:
                o["description"] = ""
