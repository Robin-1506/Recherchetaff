"""Outils partagés par les sources."""

import logging
import time

import requests

log = logging.getLogger(__name__)


class ErreurSource(Exception):
    """La source a répondu de façon inattendue ou ne répond pas."""


class SourceNonConfiguree(ErreurSource):
    """Un secret nécessaire à la source est absent."""


def appeler(session: requests.Session, methode: str, url: str, nom: str, pause: float = 0.0,
            essais: int = 4, **kwargs) -> requests.Response:
    """Appel HTTP avec nouvel essai en cas de surcharge (429) ou de panne (5xx)."""
    for tentative in range(essais):
        if pause:
            time.sleep(pause)
        try:
            r = session.request(methode, url, timeout=30, **kwargs)
        except requests.RequestException as e:
            if tentative == essais - 1:
                raise ErreurSource(f"{nom} injoignable : {e.__class__.__name__}") from e
            time.sleep(2 ** tentative)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            attente = float(r.headers.get("Retry-After", 2 ** tentative))
            log.warning("%s : code %s, nouvel essai dans %ss", nom, r.status_code, attente)
            time.sleep(attente)
            continue
        return r
    raise ErreurSource(f"{nom} ne répond pas correctement (code {r.status_code})")


def requetes_par_mots_cles(config: dict) -> list[tuple[str, bool]]:
    """Liste des recherches à faire : (mot-clé, est_national)."""
    rech = config["recherche"]
    requetes = [(mot, False) for mot in rech.get("mots_cles", [])]
    tt = rech.get("teletravail_france") or {}
    if tt.get("actif"):
        requetes += [(mot, True) for mot in tt.get("mots_cles", [])]
    return requetes
