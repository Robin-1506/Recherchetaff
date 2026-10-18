"""Point d'entrée de la collecte : `python -m collecte`.

Chaque source est lancée séparément : si l'une échoue, les autres
continuent et l'échec est noté dans les données (affiché sur le site).
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import stockage
from .sources import france_travail

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_CONFIG = RACINE / "config" / "criteres.yaml"
CHEMIN_DONNEES = RACINE / "docs" / "data" / "offres.chiffre.json"
# État des sources (non chiffré, sans donnée personnelle) : réécrit à chaque collecte.
CHEMIN_STATUT = RACINE / "docs" / "data" / "statut.json"

log = logging.getLogger("collecte")


def lancer(config: dict, donnees: dict, maintenant: datetime, clients: dict | None = None) -> tuple[list[str], dict]:
    """Lance toutes les sources et met à jour `donnees`.
    Renvoie le compte rendu (Markdown) et le statut des sources."""
    clients = clients or {}
    horodatage = maintenant.isoformat(timespec="seconds")
    compte_rendu = [f"## Collecte du {maintenant:%d/%m/%Y %H:%M} (UTC)", ""]
    statuts: dict[str, dict] = {}

    # --- France Travail ---------------------------------------------------
    try:
        res = france_travail.collecter(config, clients.get(france_travail.CLE))
        client = res["client"]
        bilan = stockage.integrer(
            donnees, france_travail.CLE, france_travail.NOM, res["offres"], horodatage, res["complet"],
            encore_en_ligne=lambda ids: france_travail.offres_encore_en_ligne(client, ids),
        )
        statuts[france_travail.CLE] = {
            "nom": france_travail.NOM, "ok": True, "date": horodatage, "nb_offres": len(res["offres"]),
            "message": " ; ".join(res["avertissements"]),
        }
        compte_rendu.append(
            f"- **{france_travail.NOM}** : {len(res['offres'])} offres trouvées, "
            f"{bilan['nouvelles']} nouvelles, {bilan['expirees']} expirées"
        )
        compte_rendu += [f"  - ⚠️ {a}" for a in res["avertissements"]]
        if not config["recherche"].get("codes_rome"):
            compte_rendu += ["", "### Codes ROME présents dans les offres trouvées", "",
                             "| Code | Métier | Offres | Exemples |", "|---|---|---|---|"]
            for e in france_travail.rapport_rome(res["offres"]):
                compte_rendu.append(f"| {e['code']} | {e['libelle']} | {e['nb']} | {' / '.join(e['exemples'])} |")
    except Exception as e:  # une source en panne ne doit pas bloquer les autres
        log.exception("France Travail en échec")
        statuts[france_travail.CLE] = {
            "nom": france_travail.NOM, "ok": False, "date": horodatage, "nb_offres": 0, "message": str(e),
        }
        compte_rendu.append(f"- ❌ **{france_travail.NOM}** en échec : {e}")

    stockage.alleger(donnees, maintenant)
    actives = sum(1 for o in donnees["offres"] if not o.get("expiree"))
    compte_rendu += ["", f"Total : {actives} offres en ligne, {len(donnees['offres']) - actives} expirées conservées."]
    return compte_rendu, {"derniere_collecte": horodatage, "sources": statuts}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mot_de_passe = os.environ.get("MOT_DE_PASSE_SITE")
    if not mot_de_passe:
        log.error("Le secret MOT_DE_PASSE_SITE est absent : impossible de chiffrer les données.")
        return 1

    config = yaml.safe_load(CHEMIN_CONFIG.read_text(encoding="utf-8"))
    precedentes = stockage.charger(CHEMIN_DONNEES, mot_de_passe)
    donnees = stockage.charger(CHEMIN_DONNEES, mot_de_passe)
    maintenant = datetime.now(timezone.utc)

    compte_rendu, statut = lancer(config, donnees, maintenant)
    ecrit = stockage.enregistrer(CHEMIN_DONNEES, donnees, mot_de_passe,
                                 precedentes if CHEMIN_DONNEES.exists() else None)
    CHEMIN_STATUT.write_text(json.dumps(statut, ensure_ascii=False, indent=2), encoding="utf-8")
    compte_rendu.append("Données mises à jour." if ecrit else "Aucun changement dans les données.")

    texte = "\n".join(compte_rendu)
    print(texte)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as f:
            f.write(texte + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
