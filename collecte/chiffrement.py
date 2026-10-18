"""Chiffrement du fichier de données publié sur le site.

Le fichier est compressé (gzip) puis chiffré en AES-GCM avec une clé
dérivée du mot de passe (PBKDF2-SHA256). Le site fait l'opération inverse
dans le navigateur (Web Crypto), voir docs/app.js.
"""

import base64
import gzip
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

FORMAT = "veille-emploi-v1"
ITERATIONS = 250_000


class MotDePasseIncorrect(Exception):
    pass


def _cle(mot_de_passe: str, sel: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sel, iterations=iterations)
    return kdf.derive(mot_de_passe.encode("utf-8"))


def chiffrer(donnees: dict, mot_de_passe: str) -> dict:
    sel = os.urandom(16)
    iv = os.urandom(12)
    clair = gzip.compress(json.dumps(donnees, ensure_ascii=False).encode("utf-8"), mtime=0)
    chiffre = AESGCM(_cle(mot_de_passe, sel, ITERATIONS)).encrypt(iv, clair, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {
        "format": FORMAT,
        "kdf": "PBKDF2-SHA256",
        "iterations": ITERATIONS,
        "sel": b64(sel),
        "iv": b64(iv),
        "donnees": b64(chiffre),
    }


def dechiffrer(enveloppe: dict, mot_de_passe: str) -> dict:
    if enveloppe.get("format") != FORMAT:
        raise ValueError("Format de fichier inconnu")
    sel = base64.b64decode(enveloppe["sel"])
    iv = base64.b64decode(enveloppe["iv"])
    chiffre = base64.b64decode(enveloppe["donnees"])
    try:
        clair = AESGCM(_cle(mot_de_passe, sel, enveloppe["iterations"])).decrypt(iv, chiffre, None)
    except InvalidTag as e:
        raise MotDePasseIncorrect("Mot de passe incorrect") from e
    return json.loads(gzip.decompress(clair).decode("utf-8"))
