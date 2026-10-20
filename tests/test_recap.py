import json
from datetime import datetime, timezone

import pytest

from collecte import __main__ as principal
from collecte import recap
from collecte.chiffrement import chiffrer

MAINTENANT = datetime(2026, 10, 20, 4, 45, tzinfo=timezone.utc)
HORO = MAINTENANT.isoformat(timespec="seconds")


def offre(id_, score, categorie="Product Owner", premiere_vue=HORO, **autres):
    return {"id": id_, "titre": f"Poste {id_}", "entreprise": "ACME", "lieu": "Nantes", "contrat": "CDI",
            "score": score, "categorie": categorie, "premiere_vue": premiere_vue, "expiree": False,
            "sources": [{"nom": "France Travail", "url": f"https://ft/{id_}"}], **autres}


DONNEES = {"meta": {}, "offres": [
    offre("a", 70), offre("b", 95), offre("c", 80, categorie="Hors cible"),
    offre("d", 99, premiere_vue="2026-10-19T04:45:00+00:00"),  # déjà vue hier
    offre("e", 60), offre("f", 50), offre("g", 40), offre("h", 30), offre("i", 90),
]}
SUIVI = {"offres": {"i": {"statut": "ignore", "maj": "x"}}, "candidatures": {
    "1": {"id": "1", "titre": "BA", "entreprise": "Banque", "colonne": "postule", "relance": "2026-10-20", "contact": "Mme Martin"},
    "2": {"id": "2", "titre": "PO", "entreprise": "Studio", "colonne": "entretien", "relance": "2026-10-15"},
    "3": {"id": "3", "titre": "PM", "colonne": "postule", "relance": "2026-10-27"},  # plus tard
    "4": {"id": "4", "titre": "UX", "colonne": "refus", "relance": "2026-10-01"},  # refusée : pas de relance
    "5": {"id": "5", "titre": "X", "colonne": "postule", "relance": "2026-10-01", "supprime": True},
}}


def test_preparer():
    r = recap.preparer(DONNEES, SUIVI, HORO, MAINTENANT, {"nb_meilleures": 5})
    assert [o["id"] for o in r["top"]] == ["b", "a", "e", "f", "g"]  # sans hors cible, ancienne, ignorée
    assert len(r["nouvelles"]) == 6
    assert [c["id"] for c in r["relances"]] == ["2", "1"]  # la plus en retard d'abord


def test_rien_de_nouveau_pas_d_email():
    donnees = {"meta": {}, "offres": [offre("d", 99, premiere_vue="2026-10-19T04:45:00+00:00")]}
    assert recap.preparer(donnees, {"candidatures": {}}, HORO, MAINTENANT, {}) is None


def test_relances_seules_suffisent():
    r = recap.preparer({"meta": {}, "offres": []}, SUIVI, HORO, MAINTENANT, {})
    assert r and not r["nouvelles"] and len(r["relances"]) == 2


def test_composer():
    r = recap.preparer(DONNEES, SUIVI, HORO, MAINTENANT, {"nb_meilleures": 5})
    m = recap.composer(r, "https://robin-1506.github.io/Recherchetaff/")
    assert m["Subject"] == "Veille emploi : 6 nouvelles offres, 2 relances"
    texte = m.get_body(("plain",)).get_content()
    page = m.get_body(("html",)).get_content()
    assert "Poste b (95/100)" in texte and "https://ft/b" in texte
    assert "Mme Martin" in texte and "relance aujourd'hui" in texte and "prévue le 15/10/2026" in texte
    assert "Poste c" not in texte
    assert "<a href='https://ft/b'" in page and "Ouvrir ma veille" in page


def test_echappement_html():
    donnees = {"meta": {}, "offres": [offre("x", 90, titre="<script>alert(1)</script>")]}
    m = recap.composer(recap.preparer(donnees, None, HORO, MAINTENANT, {}), "https://site/")
    assert "<script>" not in m.get_body(("html",)).get_content()


@pytest.fixture
def environnement(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVOYER_RECAP", "oui")
    monkeypatch.setenv("GMAIL_ADRESSE", "veille@example.com")
    monkeypatch.setenv("GMAIL_MOT_DE_PASSE_APPLI", "abcd")
    monkeypatch.setenv("RECAP_DESTINATAIRE", "moi@example.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Robin-1506/Recherchetaff")
    chemin = tmp_path / "suivi.chiffre.json"
    chemin.write_text(json.dumps(chiffrer(SUIVI, "mdp")))
    monkeypatch.setattr(principal, "CHEMIN_SUIVI", chemin)
    envois = []
    monkeypatch.setattr(recap, "envoyer", lambda m, exp, mdp, dest: envois.append((m, exp, dest)))
    return envois


def test_envoi(environnement):
    statut = principal.gerer_recap({}, DONNEES, MAINTENANT, "mdp")
    assert statut["envoye"] is True
    message, expediteur, destinataire = environnement[0]
    assert destinataire == "moi@example.com"
    assert "https://robin-1506.github.io/Recherchetaff/" in message.get_body(("plain",)).get_content()
    assert "moi@example.com" not in statut["message"]


def test_pas_de_recap_pour_une_collecte_manuelle(environnement, monkeypatch):
    monkeypatch.setenv("ENVOYER_RECAP", "non")
    assert principal.gerer_recap({}, DONNEES, MAINTENANT, "mdp")["envoye"] is False
    assert environnement == []


def test_recap_non_configure(environnement, monkeypatch):
    monkeypatch.delenv("RECAP_DESTINATAIRE")
    statut = principal.gerer_recap({}, DONNEES, MAINTENANT, "mdp")
    assert statut["ok"] is False and statut["non_configure"] is True


def test_erreur_d_envoi_signalee_sans_adresse(environnement, monkeypatch):
    import smtplib

    def echec(*a):
        raise smtplib.SMTPAuthenticationError(535, b"Bad credentials for veille@example.com")
    monkeypatch.setattr(recap, "envoyer", echec)
    statut = principal.gerer_recap({}, DONNEES, MAINTENANT, "mdp")
    assert statut["ok"] is False
    assert "example.com" not in statut["message"]


def test_envoyer_via_smtp_gmail(monkeypatch):
    appels = {}

    class FauxSMTP:
        def __init__(self, hote, port, timeout=None):
            appels["serveur"] = (hote, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, utilisateur, mdp):
            appels["login"] = utilisateur

        def send_message(self, message):
            appels["message"] = message

        def ehlo_or_helo_if_needed(self):
            pass

        def auth_plain(self):
            pass

        def auth(self, mecanisme, objet):
            appels["login"] = self.user
            appels["mdp"] = self.password

    monkeypatch.setattr(recap.smtplib, "SMTP_SSL", FauxSMTP)
    message = recap.composer(recap.preparer(DONNEES, None, HORO, MAINTENANT, {}), "https://site/")
    recap.envoyer(message, "veille@example.com", "abcd efgh ijkl mnop", "moi@example.com")
    assert appels["serveur"] == ("smtp.gmail.com", 465)
    assert appels["mdp"] == "abcdefghijklmnop"
    assert appels["login"] == "veille@example.com"
    assert appels["message"]["To"] == "moi@example.com"
    assert "Veille emploi" in appels["message"]["From"]
    assert appels["message"][recap.ENTETE_RECAP] == "recap"


def test_secours_587_et_diagnostic_sans_adresse(monkeypatch):
    import smtplib

    class SSLCoupe:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def ehlo_or_helo_if_needed(self):
            pass

        def auth_plain(self):
            pass

        def auth(self, *a):
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed for veille@example.com")

    envoye = []

    class Starttls(SSLCoupe):
        def auth(self, *a):
            pass

        def send_message(self, m):
            envoye.append(m)

    monkeypatch.setattr(recap.smtplib, "SMTP_SSL", SSLCoupe)
    monkeypatch.setattr(recap.smtplib, "SMTP", Starttls)
    message = recap.composer(recap.preparer(DONNEES, None, HORO, MAINTENANT, {}), "https://site/")
    recap.envoyer(message, "veille@example.com", "abcd", "moi@example.com")
    assert envoye  # le port 587 a pris le relais

    monkeypatch.setattr(recap.smtplib, "SMTP", SSLCoupe)
    message = recap.composer(recap.preparer(DONNEES, None, HORO, MAINTENANT, {}), "https://site/")
    with pytest.raises(recap.ErreurEnvoi) as erreur:
        recap.envoyer(message, "veille@example.com", "abcd", "moi@example.com")
    assert "étape identification" in str(erreur.value)
    assert "example.com" not in str(erreur.value)


def test_motif_du_refus_de_gmail_affiche_sans_adresse(monkeypatch):
    import smtplib

    class Refus:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo_or_helo_if_needed(self):
            pass

        def auth_plain(self):
            pass

        def auth(self, *a):
            raise smtplib.SMTPAuthenticationError(
                534, b"5.7.9 Please log in with your web browser for veille@example.com")

    monkeypatch.setattr(recap.smtplib, "SMTP_SSL", Refus)
    message = recap.composer(recap.preparer(DONNEES, None, HORO, MAINTENANT, {}), "https://site/")
    with pytest.raises(recap.ErreurEnvoi) as erreur:
        recap.envoyer(message, "veille@example.com", "abcd", "moi@example.com")
    assert "code 534" in str(erreur.value) and "log in with your web browser" in str(erreur.value)
    assert "example.com" not in str(erreur.value)
