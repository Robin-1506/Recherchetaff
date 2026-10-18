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


def integrer(donnees: dict, cle_source: str, nom_source: str, nouvelles: list[dict], maintenant: str,
             complet: bool, encore_en_ligne=None) -> dict:
    """Intègre les offres d'une source dans les données existantes.

    - nouvelle offre : ajoutée avec premiere_vue = maintenant ;
    - offre déjà connue : mise à jour ;
    - offre connue de cette source mais absente aujourd'hui : si la collecte
      est complète, on demande à `encore_en_ligne(ids)` (facultatif) lesquelles
      existent encore ; les autres sont marquées inactives pour cette source.
      Une offre dont toutes les sources sont inactives devient « expirée ».
    Renvoie un petit bilan {"nouvelles": n, "expirees": n}.
    """
    par_id = {o["id"]: o for o in donnees["offres"]}
    vues = set()
    bilan = {"nouvelles": 0, "expirees": 0}

    for offre in nouvelles:
        vues.add(offre["id"])
        existante = par_id.get(offre["id"])
        if existante is None:
            offre = {**offre, "premiere_vue": maintenant, "expiree": False,
                     "date_expiration": None}
            donnees["offres"].append(offre)
            par_id[offre["id"]] = offre
            bilan["nouvelles"] += 1
            continue
        conserves = {k: existante.get(k) for k in ("premiere_vue",)}
        autres_sources = [s for s in existante.get("sources", []) if s["nom"] != nom_source]
        existante.update(offre)
        existante.update(conserves)
        existante["sources"] = autres_sources + offre["sources"]
        existante["expiree"] = False
        existante["date_expiration"] = None

    if complet:
        absentes = [
            o for o in donnees["offres"]
            if o["id"] not in vues and not o.get("expiree")
            and any(s["nom"] == nom_source and s.get("active", True) for s in o.get("sources", []))
        ]
        ids_source = [s["id"] for o in absentes for s in o["sources"] if s["nom"] == nom_source]
        toujours = encore_en_ligne(ids_source) if (encore_en_ligne and ids_source) else set()
        for o in absentes:
            for s in o["sources"]:
                if s["nom"] == nom_source and s["id"] not in toujours:
                    s["active"] = False
            if not any(s.get("active", True) for s in o["sources"]):
                o["expiree"] = True
                o["date_expiration"] = maintenant
                bilan["expirees"] += 1

    return bilan


def alleger(donnees: dict, maintenant: datetime) -> None:
    limite = maintenant - timedelta(days=JOURS_AVANT_ALLEGEMENT)
    for o in donnees["offres"]:
        if o.get("expiree") and o.get("date_expiration") and o.get("description"):
            if datetime.fromisoformat(o["date_expiration"]) < limite:
                o["description"] = ""
