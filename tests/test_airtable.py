from datetime import datetime, timezone

import pytest

from collecte import airtable
from collecte.sources.commun import ErreurSource

MAINTENANT = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


class Reponse:
    def __init__(self, status_code, corps=None):
        self.status_code = status_code
        self._corps = corps or {}
        self.headers = {}
        self.ok = status_code < 400

    def json(self):
        return self._corps


class FauxAirtable:
    """Imite l'API d'Airtable : tables (méta-données) et fiches."""

    def __init__(self, table_existe=True, code_erreur=None):
        self.tables = [{"id": "tblCAND", "name": "Candidatures",
                        "fields": [{"name": c["name"]} for c in airtable.CHAMPS]}] if table_existe else []
        self.fiches = []
        self.code_erreur = code_erreur
        self.appels = []

    def request(self, methode, url, timeout=None, headers=None, params=None, json=None):
        chemin = url.removeprefix(airtable.URL_API + "/")
        self.appels.append((methode, chemin, json))
        assert headers["Authorization"] == "Bearer jeton"
        if self.code_erreur:
            return Reponse(self.code_erreur, {"error": {"type": "AUTHENTICATION_REQUIRED"}})
        if chemin == "meta/bases/appBASE/tables":
            if methode == "GET":
                return Reponse(200, {"tables": self.tables})
            self.tables.append({"id": "tblNEW", "name": json["name"], "fields": json["fields"]})
            return Reponse(200, {"id": "tblNEW"})
        if chemin.startswith("meta/bases/appBASE/tables/"):
            return Reponse(200, {"id": "fld"})
        if methode == "GET":  # deux pages de résultats si besoin
            debut = int((params or {}).get("offset", 0))
            page = self.fiches[debut:debut + 2]
            corps = {"records": page}
            if debut + 2 < len(self.fiches):
                corps["offset"] = str(debut + 2)
            return Reponse(200, corps)
        if methode == "POST":
            assert len(json["records"]) <= airtable.PAR_LOT
            creees = [{"id": f"rec{len(self.fiches) + i}", "fields": r["fields"]} for i, r in enumerate(json["records"])]
            self.fiches += creees
            return Reponse(200, {"records": creees})
        if methode == "PATCH":
            modifiees = []
            for r in json["records"]:
                fiche = next(f for f in self.fiches if f["id"] == r["id"])
                fiche["fields"].update(r["fields"])
                modifiees.append(fiche)
            return Reponse(200, {"records": modifiees})
        raise AssertionError(chemin)


def client(faux):
    return airtable.ClientAirtable("jeton", "appBASE", faux)


def suivi(*candidatures):
    return {"offres": {}, "candidatures": {c["id"]: c for c in candidatures}}


PO = {"id": "u1", "offre_id": "ft-1", "titre": "Product Owner", "entreprise": "ACME", "lien": "https://ex/1",
      "colonne": "a_postuler", "maj": "2026-10-20T10:00:00Z"}
OFFRES = {"ft-1": {"id": "ft-1", "lieu": "44 - NANTES", "score": 88}}


def test_premiere_synchro_cree_la_table_et_envoie_les_candidatures():
    faux = FauxAirtable(table_existe=False)
    ancienne = {**PO, "id": "u2", "offre_id": "adz-2", "titre": "Business Analyst", "colonne": "entretien",
                "notes": "Entretien mardi", "contact": "Mme X", "maj": "2026-10-01T10:00:00Z"}
    supprimee = {"id": "u3", "supprime": True, "maj": "2026-10-02T10:00:00Z"}
    replique, lignes = airtable.synchroniser(client(faux), suivi(PO, ancienne, supprimee), None, OFFRES, MAINTENANT)
    assert faux.tables[0]["name"] == "Candidatures"
    assert replique["table"] == "tblNEW" and replique["base"] == "appBASE"
    champs = {f["fields"]["Réf. veille"]: f["fields"] for f in faux.fiches}
    assert set(champs) == {"ft-1", "adz-2"}
    assert champs["ft-1"]["Étape"] == "À postuler" and champs["ft-1"]["Lieu"] == "44 - NANTES"
    assert champs["ft-1"]["Score"] == 88 and champs["ft-1"]["Ajoutée le"] == "2026-10-20"
    assert champs["adz-2"]["Étape"] == "Entretien" and champs["adz-2"]["Notes"] == "Entretien mardi"
    assert "Date de candidature" not in champs["ft-1"]  # champs vides non envoyés
    assert {c["ref"] for c in replique["candidatures"]} == {"ft-1", "adz-2"}
    notes = {c["ref"]: c["notes"] for c in replique["candidatures"]}
    assert notes == {"ft-1": "", "adz-2": "Entretien mardi"}  # notes recopiées (fichier chiffré)
    assert "2 créées" in lignes[0]


def test_pas_de_doublon_et_pas_d_appel_inutile():
    faux = FauxAirtable()
    replique, _ = airtable.synchroniser(client(faux), suivi(PO), None, OFFRES, MAINTENANT)
    assert len(faux.fiches) == 1
    # Synchro déclenchée par une modification du site sans nouvelle candidature : aucun appel.
    nb = len(faux.appels)
    replique, lignes = airtable.synchroniser(client(faux), suivi(PO), replique, OFFRES, MAINTENANT, complete=False)
    assert len(faux.appels) == nb and lignes == ["Rien à envoyer à Airtable."]
    # Synchro complète : on relit Airtable, sans recréer la fiche.
    replique, _ = airtable.synchroniser(client(faux), suivi(PO), replique, OFFRES, MAINTENANT)
    assert len(faux.fiches) == 1


def test_fiche_supprimee_dans_airtable_puis_ajoutee_de_nouveau():
    faux = FauxAirtable()
    replique, _ = airtable.synchroniser(client(faux), suivi(PO), None, OFFRES, MAINTENANT)
    faux.fiches.clear()  # supprimée dans Airtable
    replique, _ = airtable.synchroniser(client(faux), suivi(PO), replique, OFFRES, MAINTENANT)
    assert faux.fiches == [] and replique["candidatures"] == []  # pas recréée d'office
    ajoutee = {**PO, "maj": "2026-10-21T08:00:00Z"}  # « Ajouter au suivi » de nouveau dans le site
    replique, _ = airtable.synchroniser(client(faux), suivi(ajoutee), replique, OFFRES, MAINTENANT)
    assert len(faux.fiches) == 1


def test_etat_recopie_et_dates_automatiques():
    faux = FauxAirtable()
    faux.fiches = [
        {"id": "recA", "fields": {"Poste": "PO", "Entreprise": "ACME", "Étape": "Postulé", "Réf. veille": "ft-1"}},
        {"id": "recB", "fields": {"Poste": "Chef de projet", "Étape": "Relancé", "Relance": "2026-10-25",
                                  "Date de candidature": "2026-10-10"}},  # étape et fiche créées dans Airtable
        {"id": "recC", "fields": {"Poste": "BA", "Étape": "Refus"}},
    ]
    replique, lignes = airtable.synchroniser(client(faux), suivi(), {"table": "tblCAND"}, {}, MAINTENANT)
    par_id = {c["airtable_id"]: c for c in replique["candidatures"]}
    assert par_id["recA"]["colonne"] == "postule"
    assert par_id["recA"]["date_candidature"] == "2026-10-21" and par_id["recA"]["relance"] == "2026-10-28"
    assert faux.fiches[0]["fields"]["Relance"] == "2026-10-28"  # écrit aussi dans Airtable
    assert par_id["recB"]["etape"] == "Relancé" and par_id["recB"]["colonne"] is None
    assert "1 dates complétées" in lignes[0]
    # Format attendu par le récap et l'apprentissage
    suivi_recap = airtable.candidatures_suivi(replique)
    assert suivi_recap["recA"]["offre_id"] == "ft-1" and suivi_recap["recA"]["colonne"] == "postule"


def test_plus_de_100_fiches_lues_en_plusieurs_pages():
    faux = FauxAirtable()
    faux.fiches = [{"id": f"rec{i}", "fields": {"Poste": f"P{i}"}} for i in range(5)]
    replique, _ = airtable.synchroniser(client(faux), suivi(), {"table": "tblCAND"}, {}, MAINTENANT)
    assert len(replique["candidatures"]) == 5


def test_lots_de_10_fiches_au_plus():
    faux = FauxAirtable()
    candidatures = [{**PO, "id": f"u{i}", "offre_id": f"ft-{i}"} for i in range(23)]
    airtable.synchroniser(client(faux), suivi(*candidatures), {"table": "tblCAND"}, {}, MAINTENANT)
    assert len(faux.fiches) == 23
    assert len([a for a in faux.appels if a[0] == "POST"]) == 3


def test_champ_manquant_ajoute_a_une_table_existante():
    faux = FauxAirtable()
    faux.tables[0]["fields"] = [f for f in faux.tables[0]["fields"] if f["name"] != "Réf. veille"]
    airtable.synchroniser(client(faux), suivi(), None, {}, MAINTENANT)
    ajouts = [a for a in faux.appels if a[1].startswith("meta/bases/appBASE/tables/")]
    assert [a[2]["name"] for a in ajouts] == ["Réf. veille"]


def test_jeton_refuse():
    with pytest.raises(ErreurSource, match="AIRTABLE_TOKEN"):
        airtable.synchroniser(client(FauxAirtable(code_erreur=401)), suivi(PO), None, {}, MAINTENANT)


def test_masquer():
    assert airtable.masquer("jeton patAbc123.def456789xyz pour moi@example.com") == "jeton <jeton> pour <adresse>"


def test_main_sans_secrets(monkeypatch, capsys):
    monkeypatch.setenv("MOT_DE_PASSE_SITE", "x")
    monkeypatch.delenv("AIRTABLE_TOKEN", raising=False)
    assert airtable.main() == 0
    assert "non configuré" in capsys.readouterr().out


def test_main_ecrit_la_replique_chiffree(monkeypatch, tmp_path):
    from collecte.chiffrement import chiffrer, dechiffrer
    import json
    monkeypatch.setattr(airtable, "CHEMIN_SUIVI", tmp_path / "suivi.chiffre.json")
    monkeypatch.setattr(airtable, "CHEMIN_REPLIQUE", tmp_path / "airtable.chiffre.json")
    monkeypatch.setattr(airtable, "CHEMIN_OFFRES", tmp_path / "offres.chiffre.json")
    (tmp_path / "suivi.chiffre.json").write_text(json.dumps(chiffrer(suivi(PO), "mdp")))
    faux = FauxAirtable()
    monkeypatch.setattr(airtable.requests, "Session", lambda: faux)
    monkeypatch.setenv("MOT_DE_PASSE_SITE", "mdp")
    monkeypatch.setenv("AIRTABLE_TOKEN", "jeton")
    monkeypatch.setenv("AIRTABLE_BASE", "appBASE")
    assert airtable.main() == 0
    replique = dechiffrer(json.loads((tmp_path / "airtable.chiffre.json").read_text()), "mdp")
    assert replique["candidatures"][0]["titre"] == "Product Owner" and replique["maj"]
    # Rien de changé : le fichier n'est pas réécrit.
    avant = (tmp_path / "airtable.chiffre.json").read_text()
    assert airtable.main() == 0
    assert (tmp_path / "airtable.chiffre.json").read_text() == avant


def test_la_collecte_lit_les_candidatures_d_airtable(monkeypatch, tmp_path):
    import json
    import collecte.__main__ as principal
    from collecte.chiffrement import chiffrer
    monkeypatch.setattr(principal, "CHEMIN_SUIVI", tmp_path / "suivi.chiffre.json")
    monkeypatch.setattr(principal, "CHEMIN_REPLIQUE", tmp_path / "airtable.chiffre.json")
    (tmp_path / "suivi.chiffre.json").write_text(json.dumps(chiffrer(
        {"offres": {"ft-9": {"statut": "ignore"}}, "candidatures": {"u1": PO}}, "mdp")))
    assert principal.charger_suivi("mdp")["candidatures"] == {"u1": PO}  # Airtable pas encore branché
    replique = {"table": "tblCAND", "candidatures": [airtable.depuis_airtable(
        {"id": "recA", "fields": {"Poste": "PO", "Étape": "Postulé", "Relance": "2026-10-20", "Réf. veille": "ft-1"}})]}
    (tmp_path / "airtable.chiffre.json").write_text(json.dumps(chiffrer(replique, "mdp")))
    lu = principal.charger_suivi("mdp")
    assert lu["offres"] == {"ft-9": {"statut": "ignore"}}
    assert lu["candidatures"]["recA"]["offre_id"] == "ft-1" and lu["candidatures"]["recA"]["relance"] == "2026-10-20"



def test_etape_et_commentaires_faits_dans_le_site():
    faux = FauxAirtable()
    faux.fiches = [{"id": "recA", "fields": {"Poste": "PO", "Étape": "À postuler", "Notes": "Vu sur LinkedIn",
                                             "Réf. veille": "ft-1"}},
                   {"id": "recB", "fields": {"Poste": "BA", "Étape": "Entretien"}}]
    demandes = {
        "m1": {"type": "etape", "airtable_id": "recA", "etape": "Postulé", "date": "2026-10-21T08:00:00Z", "maj": "a"},
        "m2": {"type": "commentaire", "airtable_id": "recA", "texte": " CV envoyé à Mme X ", "date": "2026-10-21T08:01:00Z", "maj": "b"},
        "m3": {"type": "commentaire", "airtable_id": "recA", "texte": "Relance prévue", "date": "2026-10-22T08:00:00Z", "maj": "c"},
        "m4": {"type": "commentaire", "airtable_id": "recSUPPRIMEE", "texte": "perdu", "date": "2026-10-21T08:00:00Z", "maj": "d"},
        "m5": {"supprime": True, "maj": "e"},
    }
    s = {"offres": {}, "candidatures": {}, "modifications": demandes}
    replique, lignes = airtable.synchroniser(client(faux), s, {"table": "tblCAND"}, {}, MAINTENANT, complete=False)
    champs = faux.fiches[0]["fields"]
    assert champs["Étape"] == "Postulé"
    assert champs["Notes"] == "Vu sur LinkedIn\n21/10/2026 : CV envoyé à Mme X\n22/10/2026 : Relance prévue"
    assert champs["Date de candidature"] == "2026-10-21"  # dates automatiques après le passage à « Postulé »
    assert len([a for a in faux.appels if a[0] == "PATCH"]) == 2
    assert set(replique["appliquees"]) == {"m1", "m2", "m3", "m4"}
    assert "1 modifiées depuis le site (4 demandes)" in lignes[0]
    carte = next(c for c in replique["candidatures"] if c["airtable_id"] == "recA")
    assert carte["etape"] == "Postulé" and carte["notes"].endswith("Relance prévue")
    # Déjà appliquées : rien n'est renvoyé, et sans autre demande, aucun appel.
    nb = len(faux.appels)
    replique, lignes = airtable.synchroniser(client(faux), s, replique, {}, MAINTENANT, complete=False)
    assert len(faux.appels) == nb and lignes == ["Rien à envoyer à Airtable."]
    # Une demande retirée du suivi est oubliée de la liste des demandes appliquées.
    del s["modifications"]["m4"]
    replique, _ = airtable.synchroniser(client(faux), s, replique, {}, MAINTENANT)
    assert "m4" not in replique["appliquees"]
