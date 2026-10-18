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

import html
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


def nettoyer_html(texte: str | None) -> str:
    """Retire les balises HTML (<b>, <br>…) et décode les entités (&amp;…)."""
    if not texte:
        return ""
    texte = re.sub(r"<br\s*/?>|</p>|</li>", "\n", texte, flags=re.IGNORECASE)
    texte = re.sub(r"<[^>]+>", "", texte)
    texte = html.unescape(texte).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", texte).strip()


_CONTRAT_DANS_TEXTE = [
    ("Stage", re.compile(r"\bstages?\b|\bstagiaire|\binternship", re.IGNORECASE)),
    ("Alternance", re.compile(r"alternan|apprenti|contrat pro", re.IGNORECASE)),
    ("Freelance", re.compile(r"free[\s-]?lance|ind[ée]pendant|portage", re.IGNORECASE)),
    ("Intérim", re.compile(r"int[ée]rim", re.IGNORECASE)),
    ("CDD", re.compile(r"\bCDD\b|dur[ée]e d[ée]termin[ée]e", re.IGNORECASE)),
    ("CDI", re.compile(r"\bCDI\b|dur[ée]e ind[ée]termin[ée]e", re.IGNORECASE)),
]


def detecter_contrat(*textes: str | None) -> str | None:
    """Devine le type de contrat à partir d'un texte (intitulé, type…)."""
    texte = " ".join(t for t in textes if t)
    for contrat, motif in _CONTRAT_DANS_TEXTE:
        if motif.search(texte):
            return contrat
    return None


_MOTS_IGNORES_TITRE = {"h", "f", "x", "m", "hf", "fh", "hfx", "e", "le", "la", "les", "un", "une", "des", "du", "de", "d"}
_MOTS_IGNORES_ENTREPRISE = {"sas", "sa", "sarl", "sasu", "eurl", "sci", "groupe", "group", "france", "the", "et", "and"}


def empreinte(offre: dict) -> str | None:
    """Identifie une même offre publiée sur plusieurs sites : intitulé et
    entreprise simplifiés (sans « H/F », formes juridiques, accents…).
    Renvoie None si l'entreprise est inconnue (fusion trop hasardeuse)."""
    titre = [m for m in normaliser_texte(offre.get("titre")).split() if m not in _MOTS_IGNORES_TITRE]
    entreprise = [m for m in normaliser_texte(offre.get("entreprise")).split() if m not in _MOTS_IGNORES_ENTREPRISE]
    if not titre or not entreprise:
        return None
    return " ".join(titre) + " | " + " ".join(entreprise)
