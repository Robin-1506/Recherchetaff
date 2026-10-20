"""Source « alertes e-mail » : lit la boîte Gmail dédiée (IMAP) et extrait
les offres des e-mails d'alerte (LinkedIn, APEC, Welcome to the Jungle…).

Aucun site n'est consulté : on lit uniquement les e-mails reçus.
Secrets : GMAIL_ADRESSE et GMAIL_MOT_DE_PASSE_APPLI (mot de passe d'application).

Repérage : chaque site a un « motif » d'adresse de lien (config/criteres.yaml).
Un lien qui correspond est une offre ; son texte est l'intitulé, et les
lignes de texte qui suivent donnent l'entreprise et le lieu.

Le compte rendu (public) ne contient jamais le contenu des e-mails, seulement
des comptages et, si un e-mail n'a rien donné, la forme des liens trouvés
(chiffres masqués, sans paramètres) pour pouvoir ajuster les motifs.
"""

import email
import hashlib
import imaplib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.policy import default as politique_defaut
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

from ..modele import detecter_contrat, detecter_teletravail, normaliser_texte, tronquer
from ..recap import ENTETE_RECAP
from .commun import ErreurSource, SourceNonConfiguree

NOM = "Alertes e-mail"
CLE = "alertes_email"
SERVEUR = "imap.gmail.com"
SUFFIXE = " (alerte)"  # ex. « LinkedIn (alerte) » dans la liste des sources

log = logging.getLogger(__name__)

# Textes de lien qui ne sont pas des intitulés de poste.
_TEXTES_GENERIQUES = {
    "voir l offre", "voir l annonce", "voir le poste", "voir", "postuler", "je postule", "candidater",
    "postuler maintenant", "en savoir plus", "decouvrir", "decouvrir l offre", "voir plus", "view job",
    "apply", "apply now", "see job", "see more", "candidature simplifiee", "consulter l offre",
}
_SEPARATEURS = re.compile(r"\s+[·•|–—-]\s+")


class _LecteurHTML(HTMLParser):
    """Transforme le HTML en une suite d'éléments : textes et liens."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.elements: list[dict] = []
        self._lien = None
        self._ignore = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._ignore += 1
        elif tag == "a":
            self._lien = {"type": "lien", "href": dict(attrs).get("href") or "", "texte": ""}
        elif tag in ("br", "p", "div", "td", "tr", "li", "h1", "h2", "h3", "h4"):
            self._couper()

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._ignore = max(0, self._ignore - 1)
        elif tag == "a" and self._lien is not None:
            self._lien["texte"] = _espaces(self._lien["texte"])
            self.elements.append(self._lien)
            self._lien = None

    def handle_data(self, data):
        if self._ignore:
            return
        if self._lien is not None:
            self._lien["texte"] += " " + data
        elif data.strip():
            if self.elements and self.elements[-1]["type"] == "texte" and not self.elements[-1].get("ferme"):
                self.elements[-1]["texte"] = _espaces(self.elements[-1]["texte"] + " " + data)
            else:
                self.elements.append({"type": "texte", "texte": _espaces(data)})

    def _couper(self):
        if self.elements and self.elements[-1]["type"] == "texte":
            self.elements[-1]["ferme"] = True


def _espaces(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("\xa0", " ")).strip()


def _generique(texte: str) -> bool:
    return normaliser_texte(texte) in _TEXTES_GENERIQUES or len(texte) < 3


def _chercher_site(href: str, sites: list[dict]):
    """Renvoie (site, correspondance) si le lien (même déguisé dans un lien de
    suivi, encodé) correspond au motif d'un site."""
    candidats = [href, unquote(href), unquote(unquote(href))]
    for site in sites:
        for c in candidats:
            m = site["_motif"].search(c)
            if m:
                return site, m
    return None, None


def extraire_offres_html(html: str, sites: list[dict]) -> list[dict]:
    lecteur = _LecteurHTML()
    lecteur.feed(html)
    elements = lecteur.elements
    trouvees: dict[tuple, dict] = {}
    ordre = []
    for i, el in enumerate(elements):
        if el["type"] != "lien":
            continue
        site, m = _chercher_site(el["href"], sites)
        if not site:
            continue
        cle = (site["nom"], m.group("id"))
        offre = trouvees.get(cle)
        if offre is None:
            modele_url = site.get("url_offre")
            url = modele_url.format(id=m.group("id"), lien=m.group(0)) if modele_url else el["href"]
            offre = trouvees[cle] = {"site": site["nom"], "id": m.group("id"), "url": url, "titre": "", "infos": []}
            ordre.append(cle)
        if offre["titre"]:
            continue
        texte = el["texte"]
        if texte and not _generique(texte):
            offre["titre"] = texte
            offre["infos"] = _textes_suivants(elements, i, sites, texte)
        else:
            # Bouton « Voir l'offre » : l'intitulé est juste avant.
            precedents = _textes_precedents(elements, i, sites)
            if precedents:
                offre["titre"] = precedents[0]
                offre["infos"] = precedents[1:]
    return [trouvees[c] for c in ordre if trouvees[c]["titre"]]


def _textes_suivants(elements, i, sites, titre, nb=4) -> list[str]:
    infos = []
    for el in elements[i + 1:]:
        if el["type"] == "lien" and _chercher_site(el["href"], sites)[0]:
            break  # offre suivante
        t = el.get("texte", "")
        if t and t != titre and not _generique(t) and len(t) <= 150:
            infos.append(t)
        if len(infos) >= nb:
            break
    return infos


def _textes_precedents(elements, i, sites, nb=3) -> list[str]:
    textes = []
    for el in reversed(elements[max(0, i - 8):i]):
        if el["type"] == "lien" and _chercher_site(el["href"], sites)[0] and textes:
            break
        t = el.get("texte", "")
        if t and not _generique(t) and len(t) <= 150:
            textes.insert(0, t)
        if len(textes) >= nb:
            break
    return textes


def extraire_offres_texte(texte: str, sites: list[dict]) -> list[dict]:
    """Version texte brut (e-mails sans HTML) : intitulé = ligne avant le lien."""
    lignes = [l.strip() for l in texte.splitlines()]
    offres, vus = [], set()
    for n, ligne in enumerate(lignes):
        for url in re.findall(r"https?://\S+", ligne):
            site, m = _chercher_site(url, sites)
            if not site or (site["nom"], m.group("id")) in vus:
                continue
            avant = [l for l in lignes[max(0, n - 3):n] if l and not _generique(l)]
            if not avant:
                continue
            vus.add((site["nom"], m.group("id")))
            modele_url = site.get("url_offre")
            offres.append({"site": site["nom"], "id": m.group("id"),
                           "url": modele_url.format(id=m.group("id"), lien=m.group(0)) if modele_url else url,
                           "titre": avant[-1], "infos": avant[:-1]})
    return offres


def _entreprise_et_lieu(infos: list[str]) -> tuple[str | None, str | None]:
    if not infos:
        return None, None
    morceaux = _SEPARATEURS.split(infos[0])
    if len(morceaux) >= 2:
        return morceaux[0].strip() or None, morceaux[1].strip() or None
    lieu = infos[1] if len(infos) > 1 and len(infos[1]) <= 60 else None
    return infos[0], lieu


def convertir(extraite: dict, date_iso: str | None) -> dict:
    nom_source = extraite["site"] + SUFFIXE
    entreprise, lieu = _entreprise_et_lieu(extraite["infos"])
    texte_infos = " · ".join(extraite["infos"])
    slug = normaliser_texte(extraite["site"]).replace(" ", "")[:12]
    id_site = str(extraite["id"])
    if len(id_site) > 40:
        id_site = hashlib.sha1(id_site.encode()).hexdigest()[:16]
    return {
        "id": f"mail-{slug}-{id_site}",
        "titre": extraite["titre"],
        "entreprise": entreprise,
        "lieu": lieu,
        "teletravail": detecter_teletravail(extraite["titre"], texte_infos),
        "hors_zone": False,
        "contrat": detecter_contrat(extraite["titre"], texte_infos) or "Autre",
        "contrat_libelle": None,
        "salaire": None,
        "date_publication": date_iso,
        "date_maj": date_iso,
        "description": tronquer(f"Extrait de l'alerte {extraite['site']} : {texte_infos}" if texte_infos else ""),
        "sources": [{"nom": nom_source, "id": id_site, "url": extraite["url"], "date_maj": date_iso,
                     "active": True}],
    }


def _forme_lien(href: str) -> str:
    """Forme d'un lien pour le compte rendu public : domaine + chemin, chiffres masqués."""
    try:
        p = urlsplit(href)
    except ValueError:
        return "?"
    # Les longs segments (jetons de suivi, parfois liés à l'abonné) sont masqués.
    segments = ["…" if len(seg) > 24 else re.sub(r"\d", "9", seg) for seg in p.path.split("/")]
    return f"{p.netloc}{'/'.join(segments)}"[:80]


def _corps(message) -> tuple[str, str]:
    html, texte = "", ""
    for partie in message.walk():
        type_ = partie.get_content_type()
        if partie.get_content_maintype() == "multipart" or partie.get_filename():
            continue
        try:
            contenu = partie.get_content()
        except (LookupError, ValueError):
            continue
        if type_ == "text/html":
            html += contenu
        elif type_ == "text/plain":
            texte += contenu
    return html, texte


def analyser_message(brut: bytes, sites: list[dict]) -> tuple[list[dict], dict]:
    """Extrait les offres d'un e-mail. Renvoie (offres, diagnostic)."""
    message = email.message_from_bytes(brut, policy=politique_defaut)
    try:
        date_iso = parsedate_to_datetime(message["Date"]).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        date_iso = None
    expediteur = (message.get("From") or "").rsplit("@", 1)[-1].strip(" >").lower()
    if message.get(ENTETE_RECAP):  # notre propre récap quotidien
        return [], {"expediteur": expediteur, "nb": 0, "formes": [], "ignore": True}
    html, texte = _corps(message)
    extraites = extraire_offres_html(html, sites) if html else extraire_offres_texte(texte, sites)
    diagnostic = {"expediteur": expediteur, "nb": len(extraites), "formes": []}
    if not extraites and html:
        lecteur = _LecteurHTML()
        lecteur.feed(html)
        formes = {_forme_lien(e["href"]) for e in lecteur.elements if e["type"] == "lien" and e["href"].startswith("http")}
        diagnostic["formes"] = sorted(formes)[:15]
    return [convertir(e, date_iso) for e in extraites], diagnostic


def preparer_sites(config: dict) -> list[dict]:
    sites = []
    for site in (config.get("alertes_email") or {}).get("sites", []):
        sites.append({**site, "_motif": re.compile(site["motif_lien"], re.IGNORECASE)})
    return sites


def lire_boite(adresse: str, mot_de_passe: str, jours: int) -> list[bytes]:
    """Récupère les e-mails des derniers jours sans les marquer comme lus."""
    depuis = (datetime.now(timezone.utc) - timedelta(days=jours)).strftime("%d-%b-%Y")
    try:
        with imaplib.IMAP4_SSL(SERVEUR) as imap:
            imap.login(adresse, mot_de_passe.replace(" ", "").strip())
            imap.select("INBOX", readonly=True)
            statut, donnees = imap.search(None, "SINCE", depuis)
            if statut != "OK":
                raise ErreurSource("Recherche dans la boîte Gmail impossible.")
            messages = []
            for num in donnees[0].split():
                statut, morceaux = imap.fetch(num, "(BODY.PEEK[])")
                if statut == "OK" and morceaux and isinstance(morceaux[0], tuple):
                    messages.append(morceaux[0][1])
            return messages
    except imaplib.IMAP4.error as e:
        raison = e.args[0] if e.args else ""
        raison = raison.decode(errors="replace") if isinstance(raison, bytes) else str(raison)
        raison = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<adresse>", raison)[:150]
        raise ErreurSource(
            f"Connexion à Gmail refusée (réponse de Google : {raison or 'aucune'}). Vérifie GMAIL_ADRESSE et "
            "GMAIL_MOT_DE_PASSE_APPLI (mot de passe d'application, pas le mot de passe du compte)."
        ) from e
    except OSError as e:
        raise ErreurSource(f"Gmail injoignable : {e.__class__.__name__}") from e


def collecter(config: dict, messages: list[bytes] | None = None) -> dict:
    reglages = config.get("alertes_email") or {}
    sites = preparer_sites(config)
    if messages is None:
        adresse = os.environ.get("GMAIL_ADRESSE")
        mot_de_passe = os.environ.get("GMAIL_MOT_DE_PASSE_APPLI")
        if not adresse or not mot_de_passe:
            raise SourceNonConfiguree("non configurée (secrets GMAIL_ADRESSE / GMAIL_MOT_DE_PASSE_APPLI absents)")
        messages = lire_boite(adresse, mot_de_passe, reglages.get("jours_relus", 7))

    ignores = {d.lower() for d in reglages.get("expediteurs_ignores", [])}
    offres: dict[str, dict] = {}
    details = []
    for brut in messages:
        try:
            extraites, diag = analyser_message(brut, sites)
        except Exception as e:  # un e-mail illisible ne bloque pas les autres
            log.warning("E-mail illisible : %s", e)
            continue
        if diag.get("ignore") or diag["expediteur"] in ignores:
            continue
        for o in extraites:
            offres.setdefault(o["id"], o)
        ligne = f"- e-mail de *{diag['expediteur']}* : {diag['nb']} offres"
        if diag["formes"]:
            ligne += " — aucun lien d'offre reconnu ; liens présents : " + ", ".join(f"`{f}`" for f in diag["formes"])
        details.append(ligne)

    details = ["", f"### Alertes e-mail : {len(details)} e-mails analysés", ""] + details
    return {
        "offres": list(offres.values()),
        "avertissements": [],
        "complet": True,
        "delai_jours": reglages.get("jours_avant_expiration", 21),
        "noms_sources": [s["nom"] + SUFFIXE for s in sites],
        "details": details,
    }
