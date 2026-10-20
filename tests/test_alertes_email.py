from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import yaml

from collecte.sources import alertes_email as ae

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "config" / "criteres.yaml").read_text(encoding="utf-8"))
SITES = ae.preparer_sites(CONFIG)


def message(html=None, texte=None, expediteur="jobalerts-noreply@linkedin.com"):
    m = EmailMessage()
    m["From"] = f"LinkedIn <{expediteur}>"
    m["To"] = "veille@example.com"
    m["Subject"] = "Robin, 2 nouvelles offres"
    m["Date"] = "Sat, 17 Oct 2026 08:00:00 +0200"
    m.set_content(texte or "Version texte")
    if html:
        m.add_alternative(html, subtype="html")
    return m.as_bytes()


LINKEDIN = """<html><head><style>.x{color:red}</style></head><body>
<table><tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=abc&refId=xyz">Product Owner H/F</a></td></tr>
<tr><td>ACME Numérique · Nantes, Pays de la Loire, France (Hybride)</td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trk=img"><img src="logo.png"></a></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/view/4099999999/">Business Analyst AMOA</a></td></tr>
<tr><td>Banque Ouest · Saint-Herblain</td></tr>
<tr><td><a href="https://www.linkedin.com/comm/jobs/search?keywords=po">Voir toutes les offres</a></td></tr>
<tr><td><a href="https://www.linkedin.com/comm/psettings/email-unsubscribe?token=SECRET">Se désabonner</a></td></tr>
</table></body></html>"""


def test_linkedin():
    offres, diag = ae.analyser_message(message(LINKEDIN), SITES)
    assert diag["nb"] == 2
    po = offres[0]
    assert po["titre"] == "Product Owner H/F"
    assert po["entreprise"] == "ACME Numérique"
    assert po["lieu"].startswith("Nantes")
    assert po["sources"][0]["nom"] == "LinkedIn (alerte)"
    assert po["sources"][0]["url"] == "https://www.linkedin.com/jobs/view/4012345678"  # lien propre, sans suivi
    assert po["id"] == "mail-linkedin-4012345678"
    assert po["date_publication"].startswith("2026-10-17T06:00")
    assert offres[1]["entreprise"] == "Banque Ouest"


def test_bouton_voir_l_offre_et_lien_de_suivi_encode():
    cible = "https://www.welcometothejungle.com/fr/companies/studio-ouest/jobs/ux-researcher_nantes"
    html = f"""<div><h3>UX Researcher</h3><p>Studio Ouest</p><p>Nantes · CDI</p>
    <a href="https://click.wttj.co/ls/click?upn={quote(cible, safe='')}">Voir l'offre</a></div>"""
    offres, _ = ae.analyser_message(message(html, expediteur="alerts@welcometothejungle.com"), SITES)
    assert len(offres) == 1
    o = offres[0]
    assert o["titre"] == "UX Researcher"
    assert o["sources"][0]["nom"] == "Welcome to the Jungle (alerte)"
    assert o["sources"][0]["url"] == cible
    assert o["contrat"] == "CDI"


def test_apec():
    html = """<a href="https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/176543210W?xtor=EPR">Chef de projet digital (H/F)</a>
    <span>GROUPE OUEST</span><br><span>Nantes - 44</span>"""
    offres, _ = ae.analyser_message(message(html, expediteur="offres@apec.fr"), SITES)
    assert offres[0]["titre"] == "Chef de projet digital (H/F)"
    assert offres[0]["entreprise"] == "GROUPE OUEST"
    assert offres[0]["sources"][0]["url"].endswith("/detail-offre/176543210W")


def test_email_texte_seul():
    texte = "Nouvelles offres :\n\nProduct Owner - ACME\nhttps://www.linkedin.com/jobs/view/42/\n"
    offres, _ = ae.analyser_message(message(texte=texte), SITES)
    assert offres[0]["titre"] == "Product Owner - ACME"


def test_email_sans_offre_reconnue_diagnostic_sans_donnee_personnelle():
    html = """<a href="https://inconnu.example/job/12345/a8f7d6e5c4b3a2918273645546372819?u=moi@example.com">Super job</a>"""
    offres, diag = ae.analyser_message(message(html, expediteur="news@inconnu.example"), SITES)
    assert offres == []
    assert diag["expediteur"] == "inconnu.example"
    assert diag["formes"] == ["inconnu.example/job/99999/…"]  # jeton masqué, pas de paramètres


def test_collecte_et_doublons_entre_e_mails():
    res = ae.collecter(CONFIG, messages=[message(LINKEDIN), message(LINKEDIN), b"pas un e-mail"])
    assert len(res["offres"]) == 2
    assert "LinkedIn (alerte)" in res["noms_sources"]
    assert res["delai_jours"] == 21
    assert not any("Robin" in ligne for ligne in res["details"])  # rien du contenu des e-mails


def test_non_configuree(monkeypatch):
    monkeypatch.delenv("GMAIL_ADRESSE", raising=False)
    import pytest
    with pytest.raises(ae.SourceNonConfiguree):
        ae.collecter(CONFIG)


def test_expediteurs_ignores():
    res = ae.collecter(CONFIG, messages=[message(LINKEDIN, expediteur="no-reply@accounts.google.com")])
    assert res["offres"] == []
    assert not any("google" in ligne for ligne in res["details"])


def test_notre_recap_n_est_pas_lu_comme_une_alerte():
    from collecte import recap
    from email import message_from_bytes
    m = message_from_bytes(message(LINKEDIN))
    m[recap.ENTETE_RECAP] = "recap"
    res = ae.collecter(CONFIG, messages=[m.as_bytes()])
    assert res["offres"] == []
    assert not any("e-mail de" in ligne for ligne in res["details"])


def test_motif_du_refus_imap(monkeypatch):
    import imaplib
    import pytest

    class FauxIMAP:
        def __init__(self, *a):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, *a):
            raise imaplib.IMAP4.error(b"[ALERT] Application-specific password required for moi@example.com")

    monkeypatch.setattr(ae.imaplib, "IMAP4_SSL", FauxIMAP)
    with pytest.raises(ae.ErreurSource) as erreur:
        ae.lire_boite("moi@example.com", "abcd", 7)
    assert "Application-specific password required" in str(erreur.value)
    assert "example.com" not in str(erreur.value)


def _lien_engage(destination: str) -> str:
    """Lien de suivi au format des alertes Indeed : adresse codée en base64 après des octets techniques."""
    import base64
    bloc = base64.b64encode(b"F\x04i\x12\x9a\x0bP\x00D" + bytes([len(destination)]) + destination.encode() + b"W\x03spc")
    return f"https://engage.indeed.com/f/a/99iDAoiuuAxIa77XG2U1g~~/AAR4hBA~/{bloc.decode()}"


def test_indeed_adresse_codee_dans_le_lien_de_suivi():
    html = f"""<table><tr><td><a href="{_lien_engage('https://fr.indeed.com/rc/clk/dl?jk=5f9c0a1b2c3d4e5f&from=ja&tk=XYZ')}">Product Owner (H/F)</a></td></tr>
    <tr><td>Banque Ouest - Nantes (44)</td></tr>
    <tr><td><a href="{_lien_engage('https://fr.indeed.com/alert/unsubscribe?id=ABC')}">Se désabonner</a></td></tr></table>"""
    offres, diag = ae.analyser_message(message(html, expediteur="alert@jobalert.indeed.com"), SITES)
    assert diag["nb"] == 1
    o = offres[0]
    assert o["titre"] == "Product Owner (H/F)"
    assert o["entreprise"] == "Banque Ouest"
    assert o["sources"][0]["nom"] == "Indeed (alerte)"
    assert o["sources"][0]["url"] == "https://fr.indeed.com/viewjob?jk=5f9c0a1b2c3d4e5f"


def test_diagnostic_montre_la_forme_de_la_destination_codee():
    html = f"""<a href="{_lien_engage('https://fr.indeed.com/inconnu/12345?xyz=SECRET&u=moi@example.com')}">Offre</a>"""
    _, diag = ae.analyser_message(message(html, expediteur="alert@jobalert.indeed.com"), SITES)
    assert diag["formes"] == ["engage.indeed.com/f/a/…/…/… → fr.indeed.com/inconnu/99999 ?xyz&u"]
    assert "SECRET" not in str(diag) and "moi@" not in str(diag)  # noms des paramètres seulement
