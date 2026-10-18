"""Format commun des offres, partagé par toutes les sources.

Une offre est un simple dictionnaire :
    id               identifiant interne stable (ex. "ft-123ABCD")
    titre, entreprise, lieu
    teletravail      "complet", "partiel" ou None (inconnu)
    hors_zone        True si l'offre est hors du rayon mais en télétravail complet
    contrat          "CDI", "CDD", "Intérim", "Freelance", "Alternance", "Stage" ou "Autre"
    contrat_libelle  libellé d'origine du contrat
    salaire          texte libre ou None
    date_publication, date_maj   dates ISO 8601
    description      texte brut (tronqué)
    sources          liste de {"nom", "id", "url", "date_maj", "active"}
"""

import re
import unicodedata

LONGUEUR_MAX_DESCRIPTION = 4000

_TELETRAVAIL_COMPLET = re.compile(
    r"full[\s-]?remote|100\s?%\s*(?:en\s+)?(?:t[ée]l[ée]travail|remote|à distance)"
    r"|t[ée]l[ée]travail\s+(?:complet|total|int[ée]gral|à\s*100)"
    r"|totalement\s+(?:en\s+)?t[ée]l[ée]travail|enti[eè]rement\s+(?:en\s+)?t[ée]l[ée]travail"
    r"|poste\s+(?:en\s+)?(?:full\s+)?remote",
    re.IGNORECASE,
)
_TELETRAVAIL_PARTIEL = re.compile(r"t[ée]l[ée]travail|remote|hybride", re.IGNORECASE)


def detecter_teletravail(*textes: str | None) -> str | None:
    texte = " ".join(t for t in textes if t)
    if _TELETRAVAIL_COMPLET.search(texte):
        return "complet"
    if _TELETRAVAIL_PARTIEL.search(texte):
        return "partiel"
    return None


def normaliser_texte(texte: str | None) -> str:
    """Minuscules, sans accents ni ponctuation : sert aux comparaisons."""
    if not texte:
        return ""
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texte.lower()).strip()


def tronquer(texte: str | None, longueur: int = LONGUEUR_MAX_DESCRIPTION) -> str:
    texte = (texte or "").strip()
    if len(texte) <= longueur:
        return texte
    return texte[:longueur].rstrip() + "…"
