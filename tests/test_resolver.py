"""Resolver smoke set (the full ~300-mention set lives in data/resolver_testset.jsonl, built Fri)."""
import pytest

from app.brain.resolver import Resolver

R = Resolver()

CONFIDENT = [  # phrase -> sku that must be top-1 with high confidence
    ("aashirvaad atta", "aashirvaad-atta"),
    ("आशीर्वाद आता", "aashirvaad-atta"),
    ("ashirwad", "aashirvaad-atta"),
    ("pillsberry", "pillsbury-atta"),            # mined STT alias
    ("सर F Excel", "surf-excel-1kg"),            # garbled STT seen on Day 1
    ("fortune sarson ka tel", "fortune-mustard-oil"),
    ("parle ji", "parle-g"),
    ("पार्लेजी", "parle-g"),
    ("good day", "good-day"),
    ("lifeboy", "lifebuoy"),
    ("toor dal", "toor-dal-loose"),
    ("arhar", "toor-dal-loose"),
    ("cheeni", "cheeni-loose"),
    ("जीरा", "jeera"),
    ("amul taaza", "amul-taaza"),
    ("tata chai bada packet", "tata-tea-1kg"),
    ("chana dal", "chana-dal"),
]
AMBIGUOUS = ["atta", "doodh", "fortune", "bread", "surf", "maggi", "sarson ka tel", "chawal"]


@pytest.mark.parametrize("phrase,sku", CONFIDENT)
def test_confident(phrase, sku):
    res = R.resolve(phrase)
    assert res.top.id == sku, [(c.id, round(c.score, 2)) for c in res.candidates[:3]]
    assert res.p_correct >= 0.85


@pytest.mark.parametrize("phrase", AMBIGUOUS)
def test_ambiguous_asks(phrase):
    assert R.resolve(phrase).p_correct < 0.85


def test_garbage_has_no_candidates():
    assert R.resolve("टेलीकलिफ़्रेट").candidates == []
    assert R.resolve("xyzabc").candidates == []


def test_atta_is_not_tata():
    assert all(c.product.cat == "atta" for c in R.resolve("atta").candidates[:3])
