"""Synchronisation avec Airtable, où tu gères tes candidatures.

  - Site → Airtable : une offre ajoutée au suivi dans le site (fichier
    docs/data/suivi.chiffre.json) devient une fiche de la table « Candidatures ».
  - Airtable → site : l'état de toutes les fiches est recopié, chiffré, dans
    docs/data/airtable.chiffre.json. Le site l'affiche (vue Suivi, en lecture
    seule), le récap y lit les relances et le score y lit tes candidatures.

Secrets : AIRTABLE_TOKEN (jeton personnel limité à la base) et AIRTABLE_BASE
(identifiant de la base, « app… »). La table et ses champs sont créés au
premier lancement s'ils n'existent pas.

Offre gratuite d'Airtable : 1 000 appels par mois. Une synchronisation complète
en coûte 1 ou 2. Lancée après une modification du suivi dans le site, elle
n'appelle Airtable que s'il y a une fiche à créer.

Lancement : python -m collecte.airtable (voir .github/workflows/airtable.yml).
"""

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from .chiffrement import chiffrer, dechiffrer
from .sources.commun import ErreurSource, appeler

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_SUIVI = RACINE / "docs" / "data" / "suivi.chiffre.json"
CHEMIN_REPLIQUE = RACINE / "docs" / "data" / "airtable.chiffre.json"
CHEMIN_OFFRES = RACINE / "docs" / "data" / "offres.chiffre.json"

URL_API = "https://api.airtable.com/v0"
TABLE = "Candidatures"
PAR_LOT = 10  # Airtable crée ou modifie 10 fiches au plus par appel
DELAI_RELANCE_JOURS = 7

# Étapes : code utilisé par le programme et le site, nom affiché dans Airtable.
ETAPES = [
    ("a_postuler", "À postuler"),
    ("postule", "Postulé"),
    ("entretien", "Entretien"),
    ("offre", "Offre"),
    ("refus", "Refus"),
]
CODE_ETAPE = {nom: code for code, nom in ETAPES}
NOM_ETAPE = dict(ETAPES)

_DATE = {"dateFormat": {"name": "european"}}
CHAMPS = [  # le premier est le champ principal de la table
    {"name": "Poste", "type": "singleLineText"},
    {"name": "Entreprise", "type": "singleLineText"},
    {"name": "Étape", "type": "singleSelect", "options": {"choices": [{"name": nom} for _, nom in ETAPES]}},
    {"name": "Date de candidature", "type": "date", "options": _DATE},
    {"name": "Relance", "type": "date", "options": _DATE},
    {"name": "Contact", "type": "singleLineText"},
    {"name": "Notes", "type": "multilineText"},
    {"name": "Lien de l'offre", "type": "url"},
    {"name": "Lieu", "type": "singleLineText"},
    {"name": "Score", "type": "number", "options": {"precision": 0}},
    {"name": "Ajoutée le", "type": "date", "options": _DATE},
    {"name": "Réf. veille", "type": "singleLineText"},
]

log = logging.getLogger(__name__)


def masquer(texte: str) -> str:
    """Retire adresses e-mail et jetons d'un message d'erreur (journal public)."""
    texte = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<adresse>", texte)
    return re.sub(r"\bpat[\w.]{10,}", "<jeton>", texte)


class ClientAirtable:
    def __init__(self, jeton: str, base: str, session: requests.Session | None = None):
        self.jeton = jeton
        self.base = base
        self.session = session or requests.Session()
        self.appels = 0

    def _appel(self, methode: str, chemin: str, **kwargs) -> dict:
        self.appels += 1
        r = appeler(self.session, methode, f"{URL_API}/{chemin}", "Airtable",
                    headers={"Authorization": f"Bearer {self.jeton}"}, **kwargs)
        if r.status_code in (401, 403):
            raise ErreurSource(f"Airtable refuse l'accès (code {r.status_code}) : vérifie le jeton AIRTABLE_TOKEN "
                               "(droits data.records:read/write et schema.bases:read/write, accès à la base) "
                               "et l'identifiant AIRTABLE_BASE.")
        if r.status_code == 404:
            raise ErreurSource("Base ou table Airtable introuvable (code 404) : vérifie AIRTABLE_BASE.")
        if not r.ok:
            try:
                erreur = r.json().get("error") or {}
                detail = erreur.get("message") or erreur.get("type") or str(erreur) if isinstance(erreur, dict) else str(erreur)
            except ValueError:
                detail = ""
            raise ErreurSource(f"Airtable a refusé la demande (code {r.status_code}) : {masquer(detail)[:200]}")
        return r.json()

    def tables(self) -> list[dict]:
        return self._appel("GET", f"meta/bases/{self.base}/tables").get("tables", [])

    def creer_table(self) -> dict:
        return self._appel("POST", f"meta/bases/{self.base}/tables", json={"name": TABLE, "fields": CHAMPS})

    def creer_champ(self, table: str, champ: dict) -> None:
        self._appel("POST", f"meta/bases/{self.base}/tables/{table}/fields", json=champ)

    def lister(self, table: str) -> list[dict]:
        fiches, decalage = [], None
        while True:
            params = {"pageSize": 100, **({"offset": decalage} if decalage else {})}
            page = self._appel("GET", f"{self.base}/{table}", params=params)
            fiches += page.get("records", [])
            decalage = page.get("offset")
            if not decalage:
                return fiches

    def creer(self, table: str, champs: list[dict]) -> list[dict]:
        creees = []
        for i in range(0, len(champs), PAR_LOT):
            lot = [{"fields": c} for c in champs[i:i + PAR_LOT]]
            creees += self._appel("POST", f"{self.base}/{table}", json={"records": lot, "typecast": True})["records"]
        return creees

    def modifier(self, table: str, modifications: list[dict]) -> list[dict]:
        modifiees = []
        for i in range(0, len(modifications), PAR_LOT):
            lot = modifications[i:i + PAR_LOT]
            modifiees += self._appel("PATCH", f"{self.base}/{table}", json={"records": lot, "typecast": True})["records"]
        return modifiees


def assurer_table(client: ClientAirtable) -> str:
    """Identifiant de la table « Candidatures », créée (ou complétée) si besoin."""
    table = next((t for t in client.tables() if t.get("name") == TABLE), None)
    if table is None:
        log.info("Création de la table « %s » dans Airtable.", TABLE)
        return client.creer_table()["id"]
    presents = {c.get("name") for c in table.get("fields", [])}
    for champ in CHAMPS:
        if champ["name"] not in presents:
            log.info("Ajout du champ « %s » dans Airtable.", champ["name"])
            client.creer_champ(table["id"], champ)
    return table["id"]


# --- Conversions ---------------------------------------------------------------

def reference(candidature: dict) -> str:
    """Lien entre la fiche Airtable et le site : l'identifiant de l'offre."""
    return candidature.get("offre_id") or candidature["id"]


def champs_airtable(c: dict, offre: dict | None, aujourdhui: str) -> dict:
    """Fiche Airtable à créer pour une candidature ajoutée dans le site."""
    offre = offre or {}
    champs = {
        "Poste": c.get("titre") or offre.get("titre") or "(sans titre)",
        "Entreprise": c.get("entreprise") or offre.get("entreprise"),
        "Étape": NOM_ETAPE.get(c.get("colonne"), NOM_ETAPE["a_postuler"]),
        "Date de candidature": c.get("date_candidature"),
        "Relance": c.get("relance"),
        "Contact": c.get("contact"),
        "Notes": c.get("notes"),
        "Lien de l'offre": c.get("lien"),
        "Lieu": offre.get("lieu"),
        "Score": offre.get("score"),
        "Ajoutée le": (c.get("maj") or aujourdhui)[:10],
        "Réf. veille": reference(c),
    }
    return {k: v for k, v in champs.items() if v not in (None, "")}


def depuis_airtable(fiche: dict) -> dict:
    """Fiche Airtable → candidature de la réplique lue par le site (sans les notes)."""
    f = fiche.get("fields") or {}
    etape = f.get("Étape") or NOM_ETAPE["a_postuler"]
    return {
        "airtable_id": fiche["id"],
        "ref": f.get("Réf. veille"),
        "titre": f.get("Poste") or "(sans titre)",
        "entreprise": f.get("Entreprise") or "",
        "etape": etape,
        "colonne": CODE_ETAPE.get(etape),  # None pour une étape ajoutée par toi dans Airtable
        "date_candidature": f.get("Date de candidature") or "",
        "relance": f.get("Relance") or "",
        "contact": f.get("Contact") or "",
        "lien": f.get("Lien de l'offre") or "",
    }


def dates_automatiques(fiche: dict, aujourdhui: str) -> dict | None:
    """Passée à « Postulé » sans date : date de candidature = aujourd'hui,
    relance une semaine plus tard (comme le faisait le tableau du site)."""
    f = fiche.get("fields") or {}
    if CODE_ETAPE.get(f.get("Étape")) != "postule" or f.get("Date de candidature"):
        return None
    champs = {"Date de candidature": aujourdhui}
    if not f.get("Relance"):
        champs["Relance"] = (date.fromisoformat(aujourdhui) + timedelta(days=DELAI_RELANCE_JOURS)).isoformat()
    return {"id": fiche["id"], "fields": champs}


def candidatures_suivi(replique: dict | None) -> dict:
    """Candidatures de la réplique au format du suivi du site (récap, apprentissage)."""
    return {c["airtable_id"]: {**c, "offre_id": c.get("ref")} for c in (replique or {}).get("candidatures", [])}


# --- Synchronisation -------------------------------------------------------------

def a_transmettre(suivi: dict | None, replique: dict) -> list[dict]:
    """Candidatures du site pas encore envoyées à Airtable (ou ajoutées de
    nouveau depuis leur dernier envoi, si tu avais supprimé la fiche)."""
    transmises = replique.get("transmises") or {}
    return [c for c in ((suivi or {}).get("candidatures") or {}).values()
            if not c.get("supprime") and (c.get("maj") or "") > transmises.get(reference(c), "")]


def synchroniser(client: ClientAirtable, suivi: dict | None, replique: dict | None, offres: dict,
                 maintenant: datetime, complete: bool = True) -> tuple[dict, list[str]]:
    """Envoie les nouvelles candidatures et recopie l'état d'Airtable.
    Renvoie (nouvelle réplique, lignes de compte rendu sans donnée personnelle)."""
    replique = dict(replique or {})
    envoi = a_transmettre(suivi, replique)
    if not envoi and not complete and replique.get("table"):
        return replique, ["Rien à envoyer à Airtable."]

    aujourdhui = maintenant.astimezone(timezone.utc).date().isoformat()
    table = replique.get("table") or assurer_table(client)
    try:
        fiches = client.lister(table)
    except ErreurSource:
        if not replique.get("table"):
            raise
        table = assurer_table(client)  # table supprimée ou recréée dans Airtable
        fiches = client.lister(table)

    deja = {(f.get("fields") or {}).get("Réf. veille") for f in fiches}
    nouvelles = [champs_airtable(c, offres.get(c.get("offre_id")), aujourdhui) for c in envoi if reference(c) not in deja]
    if nouvelles:
        fiches += client.creer(table, nouvelles)
    transmises = dict(replique.get("transmises") or {})
    for c in envoi:
        transmises[reference(c)] = c.get("maj") or maintenant.isoformat(timespec="seconds")

    modifications = [m for m in (dates_automatiques(f, aujourdhui) for f in fiches) if m]
    if modifications:
        modifiees = {f["id"]: f for f in client.modifier(table, modifications)}
        fiches = [modifiees.get(f["id"], f) for f in fiches]

    candidatures = sorted((depuis_airtable(f) for f in fiches), key=lambda c: (c["titre"], c["airtable_id"]))
    replique.update(version=1, base=client.base, table=table, transmises=transmises, candidatures=candidatures)
    replique.pop("erreur", None)
    lignes = [f"{len(candidatures)} candidatures dans Airtable, {len(nouvelles)} créées, "
              f"{len(modifications)} dates complétées ({client.appels} appels à Airtable)."]
    return replique, lignes


# --- Lancement ------------------------------------------------------------------------

def _lire(chemin: Path, mot_de_passe: str) -> dict | None:
    if not chemin.exists():
        return None
    try:
        return dechiffrer(json.loads(chemin.read_text(encoding="utf-8")), mot_de_passe)
    except Exception:
        log.warning("Fichier %s illisible : ignoré.", chemin.name)
        return None


def _sans_horodatage(replique: dict | None) -> dict:
    return {k: v for k, v in (replique or {}).items() if k != "maj"}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mot_de_passe = os.environ.get("MOT_DE_PASSE_SITE")
    jeton, base = os.environ.get("AIRTABLE_TOKEN"), os.environ.get("AIRTABLE_BASE")
    if not mot_de_passe:
        log.error("Le secret MOT_DE_PASSE_SITE est absent.")
        return 1
    if not (jeton and base):
        print("Airtable non configuré (secrets AIRTABLE_TOKEN / AIRTABLE_BASE absents) : rien à faire.")
        return 0

    maintenant = datetime.now(timezone.utc)
    suivi = _lire(CHEMIN_SUIVI, mot_de_passe)
    avant = _lire(CHEMIN_REPLIQUE, mot_de_passe) or {}
    offres = {o["id"]: o for o in (_lire(CHEMIN_OFFRES, mot_de_passe) or {}).get("offres", [])}
    complete = os.environ.get("SYNCHRO_COMPLETE", "oui") == "oui"
    client = ClientAirtable(jeton, base.strip())
    try:
        replique, lignes = synchroniser(client, suivi, avant, offres, maintenant, complete)
        code = 0
    except ErreurSource as e:
        message = masquer(str(e))
        log.error("Synchronisation Airtable impossible : %s", message)
        replique, lignes, code = {**avant, "erreur": message}, [f"Échec : {message}"], 1

    if _sans_horodatage(replique) != _sans_horodatage(avant):
        replique["maj"] = maintenant.isoformat(timespec="seconds")
        CHEMIN_REPLIQUE.write_text(json.dumps(chiffrer(replique, mot_de_passe), indent=1), encoding="utf-8")
        lignes.append("Réplique mise à jour.")
    texte = "\n".join(["## Synchro Airtable", ""] + [f"- {l}" for l in lignes])
    print(texte)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(texte + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
