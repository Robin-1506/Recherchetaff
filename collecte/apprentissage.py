"""Le score apprend de tes choix, et on mesure sa fiabilité.

Tes choix viennent du suivi chiffré écrit par le site :
  - ★ Intéressante, ou candidature envoyée (Postulé, Entretien, Offre) : positif ;
  - Ignorer : négatif.

Apprentissage : pour chaque mot de l'intitulé et pour l'entreprise, on regarde
s'il apparaît plutôt dans les offres que tu aimes ou dans celles que tu
ignores. Une offre gagne ou perd jusqu'à « points_max » selon ses mots.
Pour qu'une offre ne s'auto-renforce pas, l'ajustement d'une offre que tu as
déjà classée est calculé sans tenir compte de ton propre choix sur elle.

Fiabilité : à partir des mêmes choix, on mesure si le score range bien en
haut ce qui t'intéresse et en bas ce que tu ignores.
"""

from collections import Counter

from .modele import normaliser_texte
from .score import HORS_CIBLE

POINTS_PAR_MOT = 5
OCCURRENCES_MIN = 2  # un mot vu dans une seule offre classée ne compte pas
TOP = 20
COLONNES_POSITIVES = {"postule", "entretien", "offre"}
MOTS_IGNORES = {
    "les", "des", "une", "pour", "avec", "dans", "sur", "aux", "par", "and", "the", "for", "with",
    "homme", "femme", "cdi", "cdd", "nantes", "poste", "offre", "emploi", "h/f", "hfx",
}


def choix(suivi: dict | None) -> dict[str, int]:
    """{id d'offre: +1 (aimée) ou -1 (ignorée)} d'après le suivi."""
    resultat = {}
    for id_offre, etat in ((suivi or {}).get("offres") or {}).items():
        if etat.get("supprime"):
            continue
        if etat.get("statut") == "interessant":
            resultat[id_offre] = 1
        elif etat.get("statut") == "ignore":
            resultat[id_offre] = -1
    for c in ((suivi or {}).get("candidatures") or {}).values():
        if not c.get("supprime") and c.get("offre_id") and c.get("colonne") in COLONNES_POSITIVES:
            resultat[c["offre_id"]] = 1  # une candidature envoyée l'emporte
    return resultat


def caracteristiques(offre: dict) -> set[str]:
    mots = {f"mot:{m}" for m in normaliser_texte(offre.get("titre")).split()
            if len(m) >= 3 and m not in MOTS_IGNORES}
    entreprise = normaliser_texte(offre.get("entreprise"))
    if entreprise:
        mots.add(f"entreprise:{entreprise}")
    return mots


def _libelle(caracteristique: str, offres_par_cle: dict) -> str:
    genre, valeur = caracteristique.split(":", 1)
    return f"entreprise {offres_par_cle.get(caracteristique, valeur)}" if genre == "entreprise" else f"« {valeur} »"


def appliquer(donnees: dict, suivi: dict | None, config: dict) -> list[str]:
    """Ajuste les scores d'après tes choix et mesure la fiabilité.
    Renvoie des lignes de compte rendu (comptages seulement)."""
    reglages = (config.get("score") or {}).get("apprentissage") or {}
    par_id = {o["id"]: o for o in donnees["offres"]}
    labels = {k: v for k, v in choix(suivi).items() if k in par_id}
    nb_aimees = sum(1 for v in labels.values() if v > 0)
    nb_ignorees = len(labels) - nb_aimees
    lignes = ["", "### Apprentissage et fiabilité du score", "",
              f"- Tes choix : {nb_aimees} offres aimées, {nb_ignorees} ignorées"]

    actif = reglages.get("actif", True) and len(labels) >= reglages.get("choix_minimum", 5)
    if actif:
        positifs, negatifs = Counter(), Counter()
        noms_entreprises = {}
        for id_offre, valeur in labels.items():
            for c in caracteristiques(par_id[id_offre]):
                (positifs if valeur > 0 else negatifs)[c] += 1
            if par_id[id_offre].get("entreprise"):
                noms_entreprises[f"entreprise:{normaliser_texte(par_id[id_offre]['entreprise'])}"] = par_id[id_offre]["entreprise"]
        points_max = reglages.get("points_max", 15)

        for offre in donnees["offres"]:
            if offre.get("expiree"):
                continue
            propre = labels.get(offre["id"])  # son propre choix ne compte pas pour elle
            contributions = []
            for c in caracteristiques(offre):
                p, n = positifs[c], negatifs[c]
                if propre is not None:
                    p, n = p - (propre > 0), n - (propre < 0)
                if p + n >= OCCURRENCES_MIN:
                    contributions.append((c, POINTS_PAR_MOT * (p - n) / (p + n + 2)))
            ajustement = max(-points_max, min(points_max, round(sum(v for _, v in contributions))))
            if ajustement:
                aimes = [_libelle(c, noms_entreprises) for c, v in sorted(contributions, key=lambda x: -x[1]) if v >= 1][:3]
                evites = [_libelle(c, noms_entreprises) for c, v in sorted(contributions, key=lambda x: x[1]) if v <= -1][:3]
                morceaux = ([f"appréciés : {', '.join(aimes)}"] if aimes else []) + \
                           ([f"évités : {', '.join(evites)}"] if evites else [])
                offre["score_tri"] = offre["score_base"] + ajustement
                offre["score"] = max(0, min(100, offre["score_tri"]))
                offre["score_detail"].append(
                    f"Tes choix : {'+' if ajustement > 0 else '−'}{abs(ajustement)}"
                    + (f" ({' ; '.join(morceaux)})" if morceaux else ""))
        lignes.append(f"- Apprentissage actif ({len(positifs | negatifs)} mots et entreprises appris)")
    else:
        lignes.append(f"- Apprentissage en attente : il faut au moins {reglages.get('choix_minimum', 5)} choix")

    fiabilite = mesurer(donnees, labels)
    donnees["meta"]["fiabilite"] = fiabilite
    if fiabilite:
        lignes.append(f"- Parmi les {TOP} premières offres : {fiabilite['ignorees_top']} ignorées, "
                      f"{fiabilite['aimees_top']} aimées")
        if fiabilite.get("rang_aimees") is not None:
            lignes.append(f"- Tes offres aimées sont en moyenne dans les {fiabilite['rang_aimees']} % premières "
                          f"(sans apprentissage : {fiabilite['rang_aimees_base']} %)")
    return lignes


def _rang_moyen(classement: list[dict], ids: set[str]) -> int | None:
    """Position moyenne (en % du classement) des offres données : 0 = tout en haut."""
    positions = [i for i, o in enumerate(classement) if o["id"] in ids]
    if not positions or len(classement) < 2:
        return None
    return round(100 * sum(positions) / len(positions) / (len(classement) - 1))


def mesurer(donnees: dict, labels: dict[str, int]) -> dict | None:
    """Indicateurs de fiabilité calculés sur les offres en ligne de tes postes cibles."""
    if not labels:
        return None
    offres = [o for o in donnees["offres"] if not o.get("expiree") and o.get("categorie") != HORS_CIBLE]
    classement = sorted(offres, key=lambda o: -o.get("score_tri", o.get("score") or 0))
    classement_base = sorted(offres, key=lambda o: -o.get("score_base", o.get("score") or 0))
    aimees = {k for k, v in labels.items() if v > 0}
    ignorees = {k for k, v in labels.items() if v < 0}
    top = {o["id"] for o in classement[:TOP]}
    return {
        "nb_aimees": len(aimees), "nb_ignorees": len(ignorees),
        "aimees_top": len(top & aimees), "ignorees_top": len(top & ignorees), "top": TOP,
        "rang_aimees": _rang_moyen(classement, aimees), "rang_aimees_base": _rang_moyen(classement_base, aimees),
    }
