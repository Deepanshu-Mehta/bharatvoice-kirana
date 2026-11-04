"""Clean raw mined confusions (data/stt_aliases_mined_raw.yaml) into safe aliases and substitution rules.

Keeps a mined phrase only if
  * it is not a size-variant confusion (the wrong product has the same brand and category), and
  * after stripping trailing fillers it still sounds like the product (fuzzy >= 55 against a product name), and
  * it does not already resolve confidently to a DIFFERENT product with the clean catalog, and
  * it is not a plain English/Hindi word list entry that would be dangerous as a find-and-replace pattern.

  uv run python -m eval.clean_mined
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "stt_aliases_mined_raw.yaml"
FILLER_TAIL = {"dena", "de", "dee", "den", "denge", "dinner", "diyaa", "diya", "do", "there", "here", "jaana", "jaanaa",
               "dear", "packet", "kilo", "litre", "ek", "the", "naa", "enough", "powder"}
UNSAFE = {"saffron", "of tea", "in the", "take", "ring", "tight", "type"}  # real words: alias ok, never a replace pattern


def main() -> None:
    os.environ["MINED_ALIASES"] = "0"  # judge against the clean catalog
    from app.brain.catalog import load_catalog
    from app.brain.resolver import Resolver
    from app.brain.text import key
    cat, res = load_catalog(), Resolver()
    raw = (yaml.safe_load(RAW.read_text()) or {}).get("aliases", {})
    aliases: dict[str, list[str]] = {}
    for sku, phrases in raw.items():
        p = cat.get(sku)
        names = [key(a) for a in p.aliases if a.isascii()] + ([key(p.brand)] if p.brand else [])
        for ph in phrases:
            toks = key(ph).split()
            while toks and toks[-1] in FILLER_TAIL:
                toks.pop()
            while toks and toks[0] in FILLER_TAIL:
                toks.pop(0)
            if not toks:
                continue
            clean = " ".join(toks)
            if max(fuzz.ratio(clean, n) for n in names) < 55 and max(fuzz.partial_ratio(clean, n) for n in names) < 70:
                continue
            r = res.resolve(clean)
            if r.top and r.top.id != sku:
                o = r.top.product
                if o.brand == p.brand and o.cat == p.cat:  # size variant (tata tea 250 g vs 1 kg): not an STT error
                    continue
                if r.p_correct >= 0.85:  # would steal a word that already means something else
                    continue
            if r.top and r.top.id == sku and r.p_correct >= 0.85:
                continue  # already handled without the alias
            aliases.setdefault(sku, [])
            if clean not in aliases[sku]:
                aliases[sku].append(clean)
    (ROOT / "data" / "stt_aliases_mined.yaml").write_text(yaml.safe_dump(
        {"_note": "Cleaned by eval/clean_mined.py from stt_aliases_mined_raw.yaml", "aliases": aliases},
        allow_unicode=True, sort_keys=True))
    rules = []
    for sku in sorted(aliases, key=lambda s: -len(aliases[s]))[:10]:
        pats = [a for a in aliases[sku] if a not in UNSAFE and len(a) >= 4]
        if pats:
            p = cat.get(sku)
            rules.append({"patterns": pats[:10], "replacement": p.brand or p.name})
    (ROOT / "data" / "substitution_rules_mined.json").write_text(json.dumps(rules, ensure_ascii=False, indent=1))
    kept = sum(len(v) for v in aliases.values())
    print(f"kept {kept} aliases for {len(aliases)} products (from {sum(len(v) for v in raw.values())} raw); "
          f"{len(rules)} substitution rules")
    for sku, v in sorted(aliases.items()):
        print(f"  {sku:22s} {v}")


if __name__ == "__main__":
    main()
