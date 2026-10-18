"""Récap quotidien par e-mail, envoyé après la collecte du matin.

Contenu : nombre de nouvelles offres dans les postes cibles, les meilleures
d'entre elles, et les candidatures à relancer (lues dans le suivi chiffré).
Pas d'e-mail s'il n'y a ni nouvelle offre ni relance.

Envoi depuis la boîte Gmail dédiée (mêmes secrets que la lecture des alertes)
vers l'adresse du secret RECAP_DESTINATAIRE.
"""

import html
import logging
import re
import smtplib
from datetime import datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .score import HORS_CIBLE

SERVEUR_SMTP = "smtp.gmail.com"
PARIS = ZoneInfo("Europe/Paris")
COLONNES_A_RELANCER = ("postule", "entretien")
NOMS_COLONNES = {"postule": "Postulé", "entretien": "Entretien"}

log = logging.getLogger(__name__)


def preparer(donnees: dict, suivi: dict | None, horodatage: str, maintenant: datetime, reglages: dict) -> dict | None:
    """Rassemble le contenu du récap, ou None s'il n'y a rien à signaler."""
    suivi = suivi or {}
    ignorees = {k for k, v in (suivi.get("offres") or {}).items() if not v.get("supprime") and v.get("statut") == "ignore"}
    nouvelles = [
        o for o in donnees["offres"]
        if o.get("premiere_vue") == horodatage and not o.get("expiree")
        and o.get("categorie") != HORS_CIBLE and o["id"] not in ignorees
    ]
    nouvelles.sort(key=lambda o: (-(o.get("score") or 0), o.get("titre") or ""))
    aujourdhui = maintenant.astimezone(PARIS).date().isoformat()
    relances = sorted(
        (c for c in (suivi.get("candidatures") or {}).values()
         if not c.get("supprime") and c.get("colonne") in COLONNES_A_RELANCER
         and c.get("relance") and c["relance"] <= aujourdhui),
        key=lambda c: c["relance"],
    )
    if not nouvelles and not relances:
        return None
    return {"nouvelles": nouvelles, "top": nouvelles[: reglages.get("nb_meilleures", 5)], "relances": relances,
            "aujourdhui": aujourdhui}


def _lien(offre: dict) -> str | None:
    return next((s.get("url") for s in offre.get("sources", []) if s.get("url")), None)


def _date_fr(jour: str) -> str:
    try:
        return datetime.fromisoformat(jour).strftime("%d/%m/%Y")
    except ValueError:
        return jour


def _ligne_offre(o: dict) -> str:
    morceaux = [f"{o.get('score', 0)}/100"]
    if o.get("note_ia") is not None:
        morceaux.append(f"IA {o['note_ia']}/10")
    return " · ".join(morceaux)


def sujet(recap: dict) -> str:
    n, r = len(recap["nouvelles"]), len(recap["relances"])
    parties = []
    if n:
        parties.append(f"{n} nouvelle{'s' if n > 1 else ''} offre{'s' if n > 1 else ''}")
    if r:
        parties.append(f"{r} relance{'s' if r > 1 else ''}")
    return "Veille emploi : " + ", ".join(parties)


def composer(recap: dict, url_site: str) -> EmailMessage:
    n = len(recap["nouvelles"])
    texte = []
    corps = []
    e = html.escape

    if n:
        titre = f"{n} nouvelle{'s' if n > 1 else ''} offre{'s' if n > 1 else ''} dans tes postes cibles"
        texte += [titre, ""]
        corps.append(f"<h2 style='font-size:17px;margin:0 0 8px'>{e(titre)}</h2>")
        if recap["top"]:
            corps.append("<p style='margin:0 0 6px;color:#5f6878'>Les meilleures :</p><ol style='padding-left:20px;margin:0 0 16px'>")
            for o in recap["top"]:
                lien = _lien(o)
                infos = " · ".join(x for x in (o.get("entreprise"), o.get("lieu"), o.get("contrat")) if x)
                texte += [f"- {o['titre']} ({_ligne_offre(o)})", f"  {infos}", f"  {lien or ''}", ""]
                titre_html = f"<a href='{e(lien)}' style='color:#2456d6'>{e(o['titre'])}</a>" if lien else e(o["titre"])
                corps.append(
                    f"<li style='margin-bottom:10px'><strong>{titre_html}</strong> "
                    f"<span style='color:#0e7a4f;font-weight:600'>{e(_ligne_offre(o))}</span><br>"
                    f"<span style='color:#5f6878'>{e(infos)} — {e(o.get('categorie') or '')}</span></li>"
                )
            corps.append("</ol>")

    if recap["relances"]:
        r = len(recap["relances"])
        titre = f"⏰ {r} candidature{'s' if r > 1 else ''} à relancer"
        texte += [titre, ""]
        corps.append(f"<h2 style='font-size:17px;margin:16px 0 8px'>{e(titre)}</h2><ul style='padding-left:20px;margin:0'>")
        for c in recap["relances"]:
            en_retard = c["relance"] < recap["aujourdhui"]
            quand = f"prévue le {_date_fr(c['relance'])}" if en_retard else "aujourd'hui"
            etape = NOMS_COLONNES.get(c.get("colonne"), "")
            description = " – ".join(x for x in (c.get("entreprise"), c.get("titre")) if x)
            texte.append(f"- {description} ({etape}, relance {quand})" + (f" · contact : {c['contact']}" if c.get("contact") else ""))
            corps.append(
                f"<li style='margin-bottom:6px'><strong>{e(description)}</strong> "
                f"<span style='color:#a33a12'>({e(etape)}, relance {e(quand)})</span>"
                + (f"<br><span style='color:#5f6878'>Contact : {e(c['contact'])}</span>" if c.get("contact") else "")
                + "</li>"
            )
        corps.append("</ul>")
        texte.append("")

    texte += [f"Toutes les offres et ton suivi : {url_site}"]
    corps.append(
        f"<p style='margin:20px 0 0'><a href='{e(url_site)}' style='display:inline-block;padding:10px 16px;"
        "background:#2456d6;color:#fff;border-radius:8px;text-decoration:none;font-weight:600'>Ouvrir ma veille</a></p>"
    )

    message = EmailMessage()
    message["Subject"] = sujet(recap)
    message.set_content("\n".join(texte))
    message.add_alternative(
        "<div style='font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:15px;"
        "line-height:1.5;color:#1c2230;max-width:640px'>" + "".join(corps) + "</div>",
        subtype="html",
    )
    return message


class ErreurEnvoi(Exception):
    """Échec d'envoi, avec un message sans adresse e-mail (affichable publiquement)."""


def masquer(texte: str) -> str:
    return re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<adresse>", texte)


def _ouvrir(mode: str) -> smtplib.SMTP:
    if mode == "SSL 465":
        return smtplib.SMTP_SSL(SERVEUR_SMTP, 465, timeout=30)
    smtp = smtplib.SMTP(SERVEUR_SMTP, 587, timeout=30)
    smtp.ehlo()
    smtp.starttls()
    smtp.ehlo()
    return smtp


def envoyer(message: EmailMessage, expediteur: str, mot_de_passe: str, destinataire: str) -> None:
    """Envoie par Gmail : port 465 (SSL), puis 587 (STARTTLS) en secours."""
    message["From"] = f"Veille emploi <{expediteur}>"
    message["To"] = destinataire
    mot_de_passe = mot_de_passe.replace(" ", "").strip()  # « abcd efgh ijkl mnop » → « abcdefghijklmnop »
    echecs = []
    for mode in ("SSL 465", "STARTTLS 587"):
        etape = "connexion"
        try:
            with _ouvrir(mode) as smtp:
                etape = "identification"
                smtp.login(expediteur, mot_de_passe)
                etape = "envoi"
                smtp.send_message(message)
            return
        except smtplib.SMTPAuthenticationError as e:
            raise ErreurEnvoi(
                "identification refusée par Gmail : vérifie GMAIL_ADRESSE et GMAIL_MOT_DE_PASSE_APPLI "
                f"(code {e.smtp_code})"
            ) from e
        except (smtplib.SMTPException, OSError) as e:
            detail = masquer(str(e))[:150] or e.__class__.__name__
            echecs.append(f"{mode}, étape {etape} : {e.__class__.__name__} {detail}")
            log.warning("Envoi du récap (%s) en échec à l'étape %s : %s", mode, etape, detail)
    raise ErreurEnvoi(" ; ".join(echecs))
