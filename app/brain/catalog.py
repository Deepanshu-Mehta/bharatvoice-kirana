"""Catalog loading. Prices and stock are synthetic."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.brain.text import key

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "catalog.yaml"


@dataclass
class Product:
    id: str
    name: str
    brand: str
    cat: str
    unit: str  # kg | l | pkt | pc
    price: float  # per base unit
    stock: float
    pop: float
    aliases: list[str] = field(default_factory=list)  # product-specific names (roman + deva + stt)
    desc: list[str] = field(default_factory=list)  # descriptors shared by a subset (sarson ka tel)
    generic: list[str] = field(default_factory=list)  # category-level terms (atta, tel)
    size: str | None = None  # small | large
    pack: tuple[float, str] | None = None  # one packet = (qty, unit)

    # precomputed keys
    alias_keys: list[str] = field(default_factory=list)
    desc_keys: list[str] = field(default_factory=list)
    generic_keys: list[str] = field(default_factory=list)
    brand_key: str = ""

    def in_stock(self, qty: float) -> bool:
        return self.stock >= qty


@dataclass
class Catalog:
    products: list[Product]
    by_id: dict[str, Product]
    by_cat: dict[str, list[Product]]

    def get(self, pid: str) -> Product:
        return self.by_id[pid]

    def top_terms(self, category: str | None = None, limit: int = 200, brands_only: bool = True) -> list[str]:
        """Terms for Prisma keyterms biasing, most popular first.

        brands_only: generic Hindi words (moong dal, cheeni) are already known to the ASR, and biasing toward them
        swallowed adjacent numbers on Day 1 ("moong do kilo" -> "Moong Dal kilo"). Brands are what it misses."""
        prods = self.by_cat.get(category, []) if category else self.products
        out: list[str] = []
        for p in sorted(prods, key=lambda p: -p.pop):
            terms = ([p.brand] if p.brand else []) + ([] if brands_only else p.aliases[:1])
            if brands_only and p.brand:
                terms += [a for a in p.aliases[:2] if a.isascii() and p.brand.lower() in a.lower()]
            for t in terms:  # no digits: sizes like "1 kg" in keyterms would bias how quantities are heard
                if t and t not in out and t.isascii() and not any(ch.isdigit() for ch in t):
                    out.append(t)
        return out[:limit]


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


MINED_PATH = CATALOG_PATH.parent / "stt_aliases_mined.yaml"


@lru_cache(maxsize=4)
def load_catalog(path: str | None = None) -> Catalog:
    import os
    d = yaml.safe_load(Path(path or CATALOG_PATH).read_text())
    cats = d["categories"]
    mined: dict = {}
    if os.environ.get("MINED_ALIASES", "1") == "1" and MINED_PATH.exists():  # ablation switch
        mined = (yaml.safe_load(MINED_PATH.read_text()) or {}).get("aliases") or {}
    prods = []
    for r in d["products"]:
        cat = cats.get(r["cat"], {}) or {}
        aliases = _uniq([r["name"], *(r.get("aliases") or []), *(r.get("deva") or []), *(r.get("stt_aliases") or []),
                         *(r.get("aliases_haryanvi") or []), *(mined.get(r["id"]) or [])])
        pack = r.get("pack")
        p = Product(
            id=r["id"], name=r["name"], brand=r.get("brand") or "", cat=r["cat"], unit=r["unit"],
            price=float(r["price"]), stock=float(r["stock"]), pop=float(r.get("pop", 0.5)),
            aliases=aliases, desc=list(r.get("desc") or []),
            generic=_uniq([*(cat.get("generic") or []), *(cat.get("stt") or [])]),
            size=r.get("size"), pack=(float(pack["qty"]), pack["unit"]) if pack else None,
        )
        p.alias_keys = _uniq([key(a) for a in p.aliases + ([p.brand] if p.brand else [])])
        p.desc_keys = _uniq([key(a) for a in p.desc])
        p.generic_keys = _uniq([key(a) for a in p.generic])
        p.brand_key = key(p.brand) if p.brand else ""
        prods.append(p)
    by_cat: dict[str, list[Product]] = {}
    for p in prods:
        by_cat.setdefault(p.cat, []).append(p)
    return Catalog(prods, {p.id: p for p in prods}, by_cat)
