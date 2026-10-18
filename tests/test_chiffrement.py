import pytest

from collecte.chiffrement import MotDePasseIncorrect, chiffrer, dechiffrer


def test_aller_retour():
    donnees = {"offres": [{"titre": "Product Owner – Nantes", "description": "é" * 1000}]}
    enveloppe = chiffrer(donnees, "secret")
    assert "Product" not in enveloppe["donnees"]
    assert dechiffrer(enveloppe, "secret") == donnees


def test_mauvais_mot_de_passe():
    with pytest.raises(MotDePasseIncorrect):
        dechiffrer(chiffrer({"a": 1}, "bon"), "mauvais")
