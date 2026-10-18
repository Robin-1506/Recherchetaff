"""Score de pertinence et catégorie de chaque offre.

Les règles viennent de la section « score » de config/criteres.yaml.
Le score est recalculé à chaque collecte pour toutes les offres : une
modification des règles s'applique donc aussi aux offres déjà connues.

Champs ajoutés à l'offre :
    categorie      nom du poste cible, ou « Hors cible »
    score          entier de 0 à 100
    score_detail   explications lisibles (affichées dans la fiche)
    signalements   ex. ["Mission ESN / conseil"]
"""

from .modele import normaliser_texte

HORS_CIBLE = "Hors cible"
DIVISEUR_EXCLUSION = 5


def _contient(texte_n: str, expression: str) -> bool:
    """Recherche d'une expression en mots entiers, sans accents ni majuscules."""
    expr = normaliser_texte(expression)
    return bool(expr) and f" {expr} " in f" {texte_n} "


def _premier(texte_n: str, expressions) -> str | None:
    return next((e for e in expressions if _contient(texte_n, e)), None)


def noter(offre: dict, regles: dict) -> None:
    titre_n = normaliser_texte(offre.get("titre"))
    desc_n = normaliser_texte(offre.get("description"))
    tout_n = f"{titre_n} {desc_n}"
    detail = []
    score = 0.0

    # 1. Poste cible (priorité = ordre de la liste)
    categorie = HORS_CIBLE
    for poste in regles.get("postes", []):
        trouve = _premier(titre_n, poste.get("intitules", []))
        if trouve:
            categorie = poste["nom"]
            score += poste["points"]
            detail.append(f"Poste : {poste['nom']} (« {trouve} » dans le titre) +{poste['points']}")
            break
    else:
        part = regles.get("part_si_description", 0.4)
        for poste in regles.get("postes", []):
            trouve = _premier(desc_n, poste.get("intitules", []))
            if trouve:
                categorie = poste["nom"]
                points = round(poste["points"] * part)
                score += points
                detail.append(f"Poste : {poste['nom']} (« {trouve} » dans la description seulement) +{points}")
                break

    # 2. Mots-clés bonus
    trouves = [(mot, pts) for mot, pts in (regles.get("mots_cles_bonus") or {}).items() if _contient(tout_n, mot)]
    if trouves:
        bonus = min(sum(p for _, p in trouves), regles.get("plafond_bonus", 25))
        score += bonus
        detail.append(f"Mots-clés : {', '.join(m for m, _ in trouves)} +{bonus}")

    # 3. Lieu
    lieu = regles.get("lieu") or {}
    if not offre.get("hors_zone"):
        score += lieu.get("zone", 0)
        detail.append(f"Lieu : zone de Nantes +{lieu.get('zone', 0)}")
    elif offre.get("teletravail") == "complet":
        score += lieu.get("teletravail_complet", 0)
        detail.append(f"Lieu : télétravail complet +{lieu.get('teletravail_complet', 0)}")

    # 4. Contrat
    contrat = offre.get("contrat") or "Autre"
    points_contrat = (regles.get("contrats") or {}).get(contrat, 0)
    score += points_contrat
    detail.append(f"Contrat : {contrat} +{points_contrat}")

    # 5. Exclusions (bruit) : déclassement
    exclusion = _premier(titre_n, regles.get("exclusions_titre", []))
    if contrat in (regles.get("contrats_exclus") or []):
        exclusion = exclusion or contrat.lower()
    if exclusion:
        categorie = HORS_CIBLE
        score /= DIVISEUR_EXCLUSION
        detail.append(f"Déclassée : « {exclusion} » (score divisé par {DIVISEUR_EXCLUSION})")

    signalements = []
    if contrat in ("Freelance", "Intérim"):
        signalements.append(contrat)
    indice = _premier(tout_n, regles.get("indices_esn", []))
    if indice:
        signalements.append("Mission ESN / conseil")
        detail.append(f"Signalée : indice d'ESN ou de cabinet (« {indice} »)")

    offre["categorie"] = categorie
    offre["score"] = max(0, min(100, round(score)))
    offre["score_detail"] = detail
    offre["signalements"] = signalements


def noter_tout(donnees: dict, config: dict) -> dict:
    """Note toutes les offres. Renvoie le nombre d'offres actives par catégorie."""
    regles = config.get("score") or {}
    repartition: dict[str, int] = {}
    for offre in donnees["offres"]:
        noter(offre, regles)
        if not offre.get("expiree"):
            repartition[offre["categorie"]] = repartition.get(offre["categorie"], 0) + 1
    return repartition
