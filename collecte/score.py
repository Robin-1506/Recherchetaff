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

from datetime import datetime, timezone

from .modele import normaliser_texte

HORS_CIBLE = "Hors cible"
DIVISEUR_EXCLUSION = 5


def _contient(texte_n: str, expression: str) -> bool:
    """Recherche d'une expression en mots entiers, sans accents ni majuscules."""
    expr = normaliser_texte(expression)
    return bool(expr) and f" {expr} " in f" {texte_n} "


def _premier(texte_n: str, expressions) -> str | None:
    return next((e for e in expressions if _contient(texte_n, e)), None)


def _age_jours(iso: str | None, maintenant: datetime | None) -> int | None:
    if not iso or maintenant is None:
        return None
    try:
        date = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return (maintenant - date).days


def noter(offre: dict, regles: dict, maintenant: datetime | None = None) -> None:
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
    if not offre.get("hors_zone") and offre.get("teletravail") == "partiel" and lieu.get("teletravail_partiel"):
        score += lieu["teletravail_partiel"]
        detail.append(f"Télétravail partiel +{lieu['teletravail_partiel']}")

    # 4. Contrat
    contrat = offre.get("contrat") or "Autre"
    points_contrat = (regles.get("contrats") or {}).get(contrat, 0)
    score += points_contrat
    detail.append(f"Contrat : {contrat} +{points_contrat}")

    # 5. Expérience demandée trop élevée
    exp = regles.get("experience") or {}
    demandees = (offre.get("competences") or {}).get("annees_min")
    if exp and demandees is not None:
        mes, ecart = exp.get("mes_annees", 0), exp.get("ecart_tolere", 3)
        if demandees > mes + 2 * ecart:
            score -= exp.get("malus_fort", 20)
            detail.append(f"Expérience demandée : {demandees} ans, bien au-delà des tiennes −{exp.get('malus_fort', 20)}")
        elif demandees > mes + ecart:
            score -= exp.get("malus", 10)
            detail.append(f"Expérience demandée : {demandees} ans, au-delà des tiennes −{exp.get('malus', 10)}")

    # 6. Poste de direction
    direction = regles.get("postes_direction") or {}
    trouve = _premier(titre_n, direction.get("intitules", []))
    if trouve:
        score -= direction.get("malus", 15)
        detail.append(f"Poste de direction (« {trouve} ») −{direction.get('malus', 15)}")

    # 7. Fraîcheur de l'offre
    fraicheur = regles.get("fraicheur") or {}
    age = _age_jours(offre.get("date_publication"), maintenant)
    if fraicheur and age is not None:
        if age <= fraicheur.get("recente_jours", 7):
            score += fraicheur.get("bonus_recente", 5)
            detail.append(f"Offre récente ({age} j) +{fraicheur.get('bonus_recente', 5)}")
        elif age > fraicheur.get("ancienne_jours", 45):
            score -= fraicheur.get("malus_ancienne", 5)
            detail.append(f"Offre ancienne ({age} j) −{fraicheur.get('malus_ancienne', 5)}")

    # 8. Exclusions (bruit) : déclassement
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
    # Pour classer : note sans plafond (plusieurs offres à « 100 » restent départagées).
    offre["score_tri"] = round(score)
    offre["score_base"] = offre["score_tri"]  # avant l'ajustement selon tes choix (apprentissage.py)
    offre["score_detail"] = detail
    offre["signalements"] = signalements


def noter_tout(donnees: dict, config: dict, maintenant: datetime | None = None) -> dict:
    """Note toutes les offres. Renvoie le nombre d'offres actives par catégorie."""
    regles = config.get("score") or {}
    repartition: dict[str, int] = {}
    for offre in donnees["offres"]:
        noter(offre, regles, maintenant)
        if not offre.get("expiree"):
            repartition[offre["categorie"]] = repartition.get(offre["categorie"], 0) + 1
    return repartition
