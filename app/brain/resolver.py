"""Ground a spoken product phrase to catalog SKUs with a calibrated confidence.

score(product) = 0.65 * alias match + 0.25 * category/descriptor match + 0.10 * popularity
                 (+ size / pack-size bonus, + customer-history prior)
Matching is fuzzy over phonetic keys and consonant skeletons of phrase n-grams, so
"aashirvaad", "ashirwad" and "आशीर्वाद" all hit the same alias.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.brain.catalog import Catalog, Product, load_catalog
from app.brain.text import key, skeleton

STOPWORDS = set(key(
    "ka ki ke kaa kee wala wali wale vala vali waala waali ek aur bhi chahiye chaiye dena de dijiye dijie bhejo "
    "bhej dedo lena lelo hai hain please plz bhaiya bhai ji wo woh vo yeh ye mujhe hume humko mereko mera "
    "meri liye vaala vaali sath saath saathmein mein me bhi kuch thoda zara jara aap aapke paas milega "
    "do de rakh rakhna the tha bas ok okay acha accha haan han"
).split())
STOPWORDS -= {key("do")}  # keep: handled by quantity parser upstream; harmless here

SIZE_WORDS = {**{key(w): "large" for w in ["bada", "badi", "bade", "large", "family", "big", "बड़ा", "बड़ी"]},
              **{key(w): "small" for w in ["chhota", "chhoti", "chhote", "small", "chota", "chhoti", "छोटा", "छोटी"]}}


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class Candidate:
    product: Product
    score: float
    alias: float
    category: float
    desc: float = 0.0

    @property
    def id(self) -> str:
        return self.product.id


@dataclass
class Resolution:
    phrase: str
    candidates: list[Candidate]
    p_correct: float
    features: dict = field(default_factory=dict)

    @property
    def top(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if n <= 0 or not tokens:
        return []
    if len(tokens) <= n:
        return [" ".join(tokens)]
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _floor(a: str, b: str) -> float:
    """Short strings need near-exact matches (ata vs tata must not match)."""
    n = min(len(a.replace(" ", "")), len(b.replace(" ", "")))
    return 90.0 if n <= 4 else 80.0 if n <= 6 else 70.0


def _vclass(w: str) -> str:
    """Romanised Hindi varies between e/i and o/u (jera/jeera, kabooli/kabuli), never between a and i (chana/chini)."""
    return w.replace("e", "i").replace("o", "u")


def _tok_eff(a: str, b: str) -> float:
    r = max(fuzz.ratio(a, b), 0.97 * fuzz.ratio(_vclass(a), _vclass(b)))
    sa, sb = skeleton(a), skeleton(b)
    if min(len(a), len(b)) >= 5 and min(len(sa), len(sb)) >= 3:  # skeletons are too coarse for short words
        r = max(r, 0.95 * fuzz.ratio(sa, sb))
    f = _floor(a, b)
    return max(0.0, (r - f) / (100.0 - f))


def _match(phrase_tokens: list[str], term_key: str) -> float:
    """Match strength (0-1) of a catalog term against the phrase.

    Same-length n-grams are compared token by token (every token must match for full credit);
    different-length n-grams are compared with spaces removed (parle ji ~ parleji)."""
    if not term_key or not phrase_tokens:
        return 0.0
    tt = term_key.split()
    n = len(tt)
    best = 0.0
    for g in _ngrams(phrase_tokens, n):
        gt = g.split()
        if len(gt) == n:
            effs = [_tok_eff(x, y) for x, y in zip(gt, tt)]
            m = sum(effs) / n
            if min(effs) > 0:  # every token must match; a shared generic word ('tel') alone is not a match
                best = max(best, m)
    tc = term_key.replace(" ", "")
    for m_ in {max(1, n - 1), n + 1}:
        if m_ == n:
            continue
        for g in _ngrams(phrase_tokens, m_):
            gc = g.replace(" ", "")
            f = _floor(gc, tc)
            best = max(best, max(0.0, (fuzz.ratio(gc, tc) - f) / (100.0 - f)))
    return min(best, 1.0)


class Resolver:
    def __init__(self, catalog: Catalog | None = None):
        self.cat = catalog or load_catalog()

    def phrase_tokens(self, phrase: str) -> tuple[list[str], list[str], str | None]:
        """(filtered tokens, raw tokens, size word)."""
        raw = key(phrase).split()
        size = None
        out = []
        for t in raw:
            if t in SIZE_WORDS:
                size = SIZE_WORDS[t]
            elif t not in STOPWORDS:
                out.append(t)
        return out, raw, size

    def _score(self, p: Product, toks: tuple[list[str], list[str]], size: str | None, pack_hint: tuple[float, str] | None,
               history: dict[str, int] | None, desc_cats: set[str]) -> Candidate:
        def m(k: str) -> float:
            return max(_match(toks[0], k), _match(toks[1], k))

        a = max((m(k) for k in p.alias_keys if k != p.brand_key), default=0.0)
        if p.brand_key:
            a = max(a, 0.8 * m(p.brand_key))  # brand-only mention: weaker than a product alias
        d = max((m(k) for k in p.desc_keys), default=0.0)
        g = max((m(k) for k in p.generic_keys), default=0.0)
        if p.cat in desc_cats and d < 0.5:
            g *= 0.3  # a sibling's descriptor matched ("sarson ka tel"), this product's did not
        catm = max(d, g)
        s = 0.65 * a + 0.25 * catm + 0.10 * p.pop
        if size and p.size:
            s += 0.08 if p.size == size else -0.08
        if pack_hint and p.pack and p.pack[1] == pack_hint[1]:
            s += 0.08 if abs(p.pack[0] - pack_hint[0]) < 1e-6 else -0.04
        return Candidate(p, s, a, catm, d)

    def resolve(self, phrase: str, *, k: int = 5, pack_hint: tuple[float, str] | None = None,
                history: dict[str, int] | None = None, qty_certainty: float = 1.0,
                restrict: list[Product] | None = None) -> Resolution:
        toks, raw, size = self.phrase_tokens(phrase)
        pool = restrict if restrict is not None else self.cat.products
        desc_cats = {p.cat for p in pool if any(max(_match(toks, t), _match(raw, t)) >= 0.5 for t in p.desc_keys)}
        cands = sorted((self._score(p, (toks, raw), size, pack_hint, history, desc_cats) for p in pool),
                       key=lambda c: -c.score)
        if history and cands:  # history only breaks ties between equally good matches, never overrides words
            best = cands[0]
            for c in cands:
                if c.product.id in history and c.alias >= best.alias - 1e-6 and c.category >= best.category - 1e-6:
                    c.score += min(0.06, 0.02 * history[c.product.id])
            cands.sort(key=lambda c: -c.score)
        cands = [c for c in cands if c.alias > 0 or c.category > 0][:k]
        top1 = cands[0].score if cands else 0.0
        top2 = cands[1].score if len(cands) > 1 else 0.0
        margin = top1 - top2
        feats = {"top1": top1, "margin": margin, "qty_certainty": qty_certainty, "n_tokens": len(toks),
                 "alias_top1": cands[0].alias if cands else 0.0}
        # hand-set calibration until the dev set is fitted (Monday): see eval/calibrate.py
        p = _sigmoid(10.0 * margin + 5.0 * (top1 - 0.6) + 3.0 * (qty_certainty - 0.9)) if cands else 0.0
        return Resolution(phrase, cands, p, feats)

    def resolve_in_cart(self, phrase: str, cart_products: list[Product]) -> Resolution:
        """Cart-first matching for corrections ('milk 2 litre kar do', 'atta hata do')."""
        return self.resolve(phrase, restrict=cart_products, k=3)
