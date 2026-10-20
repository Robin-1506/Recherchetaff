"""Source « alertes e-mail » : lit la boîte Gmail dédiée (IMAP) et extrait
les offres des e-mails d'alerte (LinkedIn, APEC, Welcome to the Jungle…).

Aucun site n'est consulté : on lit uniquement les e-mails reçus.
Secrets : GMAIL_ADRESSE et GMAIL_MOT_DE_PASSE_APPLI (mot de passe d'application).

Repérage : chaque site a un « motif » d'adresse de lien (config/criteres.yaml).
Un lien qui correspond est une offre ; son texte est l'intitulé, et les
lignes de texte qui suivent donnent l'entreprise et le lieu.

Liens de suivi sans adresse lisible (Indeed) : on demande au service de suivi
où mène le lien et on lit seulement sa réponse (l'adresse), sans ouvrir la
page de destination. Seulement pour les e-mails récents, pour ne pas
redemander chaque jour les mêmes liens (config : alertes_email.redirections).

Le compte rendu (public) ne contient jamais le contenu des e-mails, seulement
des comptages et, si un e-mail n'a rien donné, la forme des liens trouvés
(chiffres masqués, sans paramètres) pour pouvoir ajuster les motifs.
"""

import base64
import binascii
import email
import hashlib
import imaplib
import logging
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.policy import default as politique_defaut
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import requests

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
# Liens à ne jamais suivre, même pour connaître leur destination.
_LIENS_A_EVITER = re.compile(r"desabonn|desinscri|unsubscribe|preference|parametre|modifier|supprimer|settings",
                             re.IGNORECASE)
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


_URL_CACHEE = re.compile(rb"https?://[\x21-\x7e]+")


def _adresses_cachees(href: str) -> list[str]:
    """Adresses de destination codées en base64 dans un lien de suivi.
    Aucun site n'est contacté : on décode seulement le lien lui-même."""
    try:
        p = urlsplit(href)
    except ValueError:
        return []
    segments = [s for s in p.path.split("/") if s]
    # Un bloc base64 peut contenir des « / » : on essaie aussi les fins de chemin.
    morceaux = ["/".join(segments[i:]) for i in range(len(segments))]
    morceaux += [v for _, v in parse_qsl(p.query)]
    trouvees = []
    for m in morceaux:
        m = m.strip("~=")
        if len(m) < 16 or not re.fullmatch(r"[A-Za-z0-9+/_-]+", m):
            continue
        for decodeur in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                brut = decodeur(m + "=" * (-len(m) % 4))
            except (binascii.Error, ValueError):
                continue
            for url in _URL_CACHEE.findall(brut):
                url = url.decode("ascii")
                if url not in trouvees:
                    trouvees.append(url)
    return trouvees


class Redirections:
    """Destination des liens de suivi : on demande au service de suivi où mène
    le lien (réponse « redirection » de HTTP), sans jamais ouvrir la page de
    destination. Chaque lien n'est demandé qu'une fois par collecte."""

    MAX_ETAPES = 3

    def __init__(self, motifs: list[str], session: requests.Session | None = None, maximum: int = 150):
        self.motifs = [re.compile(m, re.IGNORECASE) for m in motifs]
        self.session = session
        self.maximum = maximum
        self.connues: dict[str, str | None] = {}
        self.demandes = 0
        self.echecs = 0
        self.codes: Counter = Counter()  # codes de réponse HTTP, pour le compte rendu

    def concerne(self, href: str) -> bool:
        return any(m.search(href) for m in self.motifs)

    def destination(self, href: str, texte: str = "") -> str | None:
        if not self.concerne(href) or _LIENS_A_EVITER.search(normaliser_texte(texte)):
            return None
        if href not in self.connues:
            if self.demandes >= self.maximum:
                return None
            self.connues[href] = self._suivre(href)
        return self.connues[href]

    def _suivre(self, url: str) -> str | None:
        self.session = self.session or requests.Session()
        for _ in range(self.MAX_ETAPES):
            self.demandes += 1
            try:
                # HEAD, et sans suivre la redirection : seule la réponse du
                # service de suivi est lue, la page de l'offre n'est jamais ouverte.
                r = self.session.head(url, allow_redirects=False, timeout=10)
                if r.status_code in (403, 405):
                    r = self.session.get(url, allow_redirects=False, timeout=10, stream=True)
                    r.close()
            except requests.RequestException:
                self.echecs += 1
                return None
            self.codes[r.status_code] += 1
            suivante = r.headers.get("Location")
            if not (300 <= r.status_code < 400 and suivante):
                self.echecs += 1
                return None
            url = urljoin(url, suivante)
            if not self.concerne(url):
                return url
        return None


def _chercher_site(href: str, sites: list[dict], resoudre=None, texte: str = ""):
    """Renvoie (site, correspondance) si le lien (même déguisé dans un lien de
    suivi, encodé) correspond au motif d'un site. « resoudre » donne, si besoin,
    la destination d'un lien de suivi (voir Redirections)."""
    candidats = [href, unquote(href), unquote(unquote(href))]
    for cachee in _adresses_cachees(href):
        candidats += [cachee, unquote(cachee)]
    destination = resoudre(href, texte) if resoudre else None
    if destination:
        candidats += [destination, unquote(destination)]
    for site in sites:
        for c in candidats:
            m = site["_motif"].search(c)
            if m:
                return site, m
    return None, None


def extraire_offres_html(html: str, sites: list[dict], resoudre=None) -> list[dict]:
    lecteur = _LecteurHTML()
    lecteur.feed(html)
    elements = lecteur.elements
    trouvees: dict[tuple, dict] = {}
    ordre = []
    for i, el in enumerate(elements):
        if el["type"] != "lien":
            continue
        site, m = _chercher_site(el["href"], sites, resoudre, el["texte"])
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
            offre["infos"] = _textes_suivants(elements, i, sites, texte, resoudre)
        else:
            # Bouton « Voir l'offre » : l'intitulé est juste avant.
            precedents = _textes_precedents(elements, i, sites, resoudre)
            if precedents:
                offre["titre"] = precedents[0]
                offre["infos"] = precedents[1:]
    return [trouvees[c] for c in ordre if trouvees[c]["titre"]]


def _textes_suivants(elements, i, sites, titre, resoudre=None, nb=4) -> list[str]:
    infos = []
    for el in elements[i + 1:]:
        if el["type"] == "lien" and _chercher_site(el["href"], sites, resoudre, el["texte"])[0]:
            break  # offre suivante
        t = el.get("texte", "")
        if t and t != titre and not _generique(t) and len(t) <= 150:
            infos.append(t)
        if len(infos) >= nb:
            break
    return infos


def _textes_precedents(elements, i, sites, resoudre=None, nb=3) -> list[str]:
    textes = []
    for el in reversed(elements[max(0, i - 8):i]):
        if el["type"] == "lien" and textes and _chercher_site(el["href"], sites, resoudre, el["texte"])[0]:
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


def _forme_lien(href: str, parametres: bool = False) -> str:
    """Forme d'un lien pour le compte rendu public : domaine + chemin, chiffres
    masqués ; avec « parametres », les noms des paramètres (jamais leurs valeurs)."""
    try:
        p = urlsplit(href)
    except ValueError:
        return "?"
    # Les jetons de suivi (longs ou avec « ~ », parfois liés à l'abonné) sont masqués.
    segments = ["…" if len(seg) > 24 or "~" in seg else re.sub(r"\d", "9", seg) for seg in p.path.split("/")]
    forme = f"{p.netloc}{'/'.join(segments)}"[:80]
    if parametres and p.query:
        noms = [re.sub(r"[^\w-]", "", n)[:15] for n, _ in parse_qsl(p.query, keep_blank_values=True)]
        forme += " ?" + "&".join(dict.fromkeys(n for n in noms if n))[:60]
    return forme


def _forme_diagnostic(href: str, redirections: "Redirections | None" = None) -> str:
    """Forme du lien, suivie de celle de sa destination si elle est connue."""
    destinations = _adresses_cachees(href)
    if redirections and redirections.connues.get(href):
        destinations.insert(0, redirections.connues[href])
    return _forme_lien(href) + (f" → {_forme_lien(destinations[0], parametres=True)}" if destinations else "")


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


def analyser_message(brut: bytes, sites: list[dict], redirections: Redirections | None = None,
                     jours_redirections: int = 2) -> tuple[list[dict], dict]:
    """Extrait les offres d'un e-mail. Renvoie (offres, diagnostic).
    Les liens de suivi ne sont suivis que pour les e-mails de moins de
    « jours_redirections » jours (les plus anciens ont déjà été traités)."""
    message = email.message_from_bytes(brut, policy=politique_defaut)
    try:
        date_iso = parsedate_to_datetime(message["Date"]).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        date_iso = None
    expediteur = (message.get("From") or "").rsplit("@", 1)[-1].strip(" >").lower()
    if message.get(ENTETE_RECAP):  # notre propre récap quotidien
        return [], {"expediteur": expediteur, "nb": 0, "formes": [], "ignore": True}
    html, texte = _corps(message)
    recent = date_iso is not None and \
        datetime.fromisoformat(date_iso) >= datetime.now(timezone.utc) - timedelta(days=jours_redirections)
    resoudre = redirections.destination if redirections and recent else None
    extraites = extraire_offres_html(html, sites, resoudre) if html else extraire_offres_texte(texte, sites)
    diagnostic = {"expediteur": expediteur, "nb": len(extraites), "formes": []}
    if not extraites and html:
        lecteur = _LecteurHTML()
        lecteur.feed(html)
        liens = [e["href"] for e in lecteur.elements if e["type"] == "lien" and e["href"].startswith("http")]
        if redirections and not recent and any(redirections.concerne(h) for h in liens):
            diagnostic["deja_traite"] = True  # liens de suivi déjà suivis lors d'une collecte précédente
            return [], diagnostic
        formes = {_forme_diagnostic(h, redirections) for h in liens}
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


def collecter(config: dict, messages: list[bytes] | None = None, redirections: Redirections | None = None) -> dict:
    reglages = config.get("alertes_email") or {}
    sites = preparer_sites(config)
    reglages_redir = reglages.get("redirections") or {}
    if redirections is None and reglages_redir.get("motifs"):
        redirections = Redirections(reglages_redir["motifs"], maximum=reglages_redir.get("maximum_par_collecte", 150))
    jours_redir = reglages_redir.get("jours", 2)
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
            extraites, diag = analyser_message(brut, sites, redirections, jours_redir)
        except Exception as e:  # un e-mail illisible ne bloque pas les autres
            log.warning("E-mail illisible : %s", e)
            continue
        if diag.get("ignore") or diag["expediteur"] in ignores:
            continue
        for o in extraites:
            offres.setdefault(o["id"], o)
        ligne = f"- e-mail de *{diag['expediteur']}* : {diag['nb']} offres"
        if diag.get("deja_traite"):
            ligne = f"- e-mail de *{diag['expediteur']}* : déjà traité lors d'une collecte précédente"
        elif diag["formes"]:
            ligne += " — aucun lien d'offre reconnu ; liens présents : " + ", ".join(f"`{f}`" for f in diag["formes"])
        details.append(ligne)

    details = ["", f"### Alertes e-mail : {len(details)} e-mails analysés", ""] + details
    if redirections and redirections.demandes:
        details.append(f"- Liens de suivi : {len(redirections.connues)} suivis "
                       f"({redirections.demandes} demandes, {redirections.echecs} sans réponse utile ; codes "
                       + ", ".join(f"{c}×{n}" for c, n in sorted(redirections.codes.items())) + ")")
    return {
        "offres": list(offres.values()),
        "avertissements": [],
        "complet": True,
        "delai_jours": reglages.get("jours_avant_expiration", 21),
        "noms_sources": [s["nom"] + SUFFIXE for s in sites],
        "details": details,
    }
