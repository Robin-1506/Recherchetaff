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

from . import score, stockage
from .sources import adzuna, alertes_email, france_travail, jooble
from .sources.commun import SourceNonConfiguree

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_CONFIG = RACINE / "config" / "criteres.yaml"
CHEMIN_DONNEES = RACINE / "docs" / "data" / "offres.chiffre.json"
# État des sources (non chiffré, sans donnée personnelle) : réécrit à chaque collecte.
CHEMIN_STATUT = RACINE / "docs" / "data" / "statut.json"

# Ordre de passage : la première source qui trouve une offre en garde la
# fiche principale, les suivantes y ajoutent leur lien.
SOURCES = [france_travail, adzuna, jooble, alertes_email]

log = logging.getLogger("collecte")


def lancer(config: dict, donnees: dict, maintenant: datetime, clients: dict | None = None) -> tuple[list[str], dict]:
    """Lance toutes les sources, l'une après l'autre, et met à jour `donnees`.
    Renvoie le compte rendu (Markdown) et le statut des sources."""
    clients = clients or {}
    horodatage = maintenant.isoformat(timespec="seconds")
    compte_rendu = [f"## Collecte du {maintenant:%d/%m/%Y %H:%M} (UTC)", ""]
    statuts: dict[str, dict] = {}

    for source in SOURCES:
        statut = {"nom": source.NOM, "ok": False, "date": horodatage, "nb_offres": 0, "message": ""}
        statuts[source.CLE] = statut
        try:
            res = source.collecter(config, clients.get(source.CLE))
        except SourceNonConfiguree as e:
            statut.update(non_configuree=True, message=str(e))
            compte_rendu.append(f"- ⏸️ **{source.NOM}** {e}")
            continue
        except Exception as e:  # une source en panne ne doit pas bloquer les autres
            log.exception("%s en échec", source.NOM)
            statut["message"] = str(e)
            compte_rendu.append(f"- ❌ **{source.NOM}** en échec : {e}")
            continue
        # Une source peut regrouper plusieurs noms (ex. « LinkedIn (alerte) »,
        # « APEC (alerte) »…) : chacun est intégré et expiré séparément.
        bilan = {"nouvelles": 0, "doublons": 0, "expirees": 0}
        for nom in res.get("noms_sources", [source.NOM]):
            offres_nom = [o for o in res["offres"] if o["sources"][0]["nom"] == nom]
            b = stockage.integrer(
                donnees, nom, offres_nom, horodatage, res["complet"],
                encore_en_ligne=res.get("verifier"), delai_jours=res.get("delai_jours"),
            )
            bilan = {k: bilan[k] + b[k] for k in bilan}
        statut.update(ok=True, nb_offres=len(res["offres"]), message=" ; ".join(res["avertissements"]))
        compte_rendu.append(
            f"- **{source.NOM}** : {len(res['offres'])} offres trouvées, {bilan['nouvelles']} nouvelles, "
            f"{bilan['doublons']} doublons fusionnés, {bilan['expirees']} expirées"
        )
        compte_rendu += [f"  - ⚠️ {a}" for a in res["avertissements"]]
        compte_rendu += res.get("details", [])

    repartition = score.noter_tout(donnees, config)
    categories = [p["nom"] for p in (config.get("score") or {}).get("postes", [])] + [score.HORS_CIBLE]
    donnees["meta"]["categories"] = categories
    compte_rendu += ["", "### Offres en ligne par poste cible", ""]
    compte_rendu += [f"- {c} : {repartition.get(c, 0)}" for c in categories]

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
